#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

namespace {

Actor* findActorByRuntimeId(std::uint64_t runtimeId) {
    auto level = ll::service::bedrock::getLevel();
    if (!level) {
        return nullptr;
    }
    for (auto* actor : level->getRuntimeActorList()) {
        if (actor != nullptr
            && static_cast<std::uint64_t>(actor->getRuntimeID()) == runtimeId) {
            return actor;
        }
    }
    return nullptr;
}

std::uint32_t storageStride(entt::basic_registry<EntityId>::common_type const* storage) {
    if (storage == nullptr || storage->size() < 2) {
        return 0;
    }
    void const* first  = storage->value((*storage)[0]);
    void const* second = storage->value((*storage)[1]);
    if (first == nullptr || second == nullptr) {
        return 0;
    }
    auto const a = reinterpret_cast<std::uintptr_t>(first);
    auto const b = reinterpret_cast<std::uintptr_t>(second);
    return static_cast<std::uint32_t>(a > b ? a - b : b - a);
}

// One bad storage must not blank the whole listing: value()/index() assume the
// storage really holds this component type, and the outer SEH backstop loses
// every row after the faulting one (a registry with 142 storages returned
// nothing at all because a single entry faulted). Probe each storage in its own
// guard so the reply can report the offender instead.
struct StorageRow {
    std::uint32_t id      = 0;
    std::uint64_t index   = 0;
    std::uint64_t address = 0;
    std::uint64_t count   = 0;
    std::uint32_t stride  = 0;
    bool          ok      = false; // false: the probe faulted, skip the row
};

bool probeStorageRow(
    entt::basic_registry<EntityId>::common_type* storage,
    EntityId                                     entity,
    StorageRow&                                  out
) {
    __try {
        out.count   = static_cast<std::uint64_t>(storage->size());
        out.index   = static_cast<std::uint64_t>(storage->index(entity));
        out.address = reinterpret_cast<std::uint64_t>(storage->value(entity));
        out.stride  = storageStride(storage);
        out.ok      = true;
    } __except (
        isFatalSehCode(GetExceptionCode()) ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH
    ) {
        out.ok = false;
    }
    return true;
}

// The EntityContext* comes from the pipe (a hook view), so the whole access
// chain must stay under SEH: applyEcs runs on the game tick thread where a
// plain C++ catch cannot intercept an access violation. Keep this function
// free of objects that need unwinding so it can host __try.
struct ContextProbe {
    std::uint64_t registry = 0;
    std::uint64_t entity   = 0;
    std::uint64_t value    = 0;
    std::uint64_t index    = 0;
    std::uint64_t count    = 0;
    bool          found    = false;
};

bool probeContext(std::uint64_t address, std::uint32_t componentId, ContextProbe& out) {
    __try {
        auto& context  = *reinterpret_cast<EntityContext*>(address);
        auto& registry = context.getRegistry();
        out.registry   = reinterpret_cast<std::uint64_t>(&registry);
        out.entity     = static_cast<std::uint64_t>(context.mEntity.mRawId);
        auto* storage  = registry.storage(static_cast<entt::id_type>(componentId));
        if (storage == nullptr || !storage->contains(context.mEntity)) {
            return true;
        }
        out.value = reinterpret_cast<std::uint64_t>(storage->value(context.mEntity));
        out.index = static_cast<std::uint64_t>(storage->index(context.mEntity));
        out.count = static_cast<std::uint64_t>(storage->size());
        out.found = true;
        return true;
    } __except (isFatalSehCode(GetExceptionCode()) ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH) {
        return false;
    }
}

} // namespace

void ProbeMod::applyEcs(LocalPlayer& player, EcsRequest const& request) {
    if (request.kind == EcsRequest::Kind::Registries) {
        auto&      context  = player.getEntityContext();
        auto&      registry = context.getRegistry();
        std::size_t components = 0;
        for (auto&& [id, storage] : registry.storage()) {
            (void)id;
            (void)storage;
            ++components;
        }
        enqueue(
            nlohmann::json{
                {"event",      "ecs_registries"                              },
                {"registries", nlohmann::json::array({
                    {
                        {"scope",    "client"                                },
                        {"registry", reinterpret_cast<std::uint64_t>(&registry)},
                        {"components", components                            },
                        {"entity",   static_cast<std::uint64_t>(context.mEntity.mRawId)},
                    },
                })},
        }
                .dump()
        );
        return;
    }

    if (request.kind == EcsRequest::Kind::Context) {
        // H0.3: resolve a component from an EntityContext* captured by a hook
        // argument, so the hook view and the runtime_id view can be compared.
        if (!readableRange(request.contextAddress, sizeof(EntityContext))) {
            enqueue(R"({"event":"error","reason":"bad_addr","phase":"ecs_context"})");
            return;
        }
        ContextProbe probe;
        if (!probeContext(request.contextAddress, request.componentId, probe)) {
            enqueue(
                R"({"event":"error","reason":"internal_error","phase":"ecs_context","message":"structured exception"})"
            );
            return;
        }
        if (!probe.found) {
            enqueue(
                nlohmann::json{
                    {"event",        "error"             },
                    {"reason",       "component_not_found"},
                    {"component_id", request.componentId },
                }
                    .dump()
            );
            return;
        }
        std::uint32_t size = 0;
        bool const    sized = componentSize(request.componentId, size);
        enqueue(
            nlohmann::json{
                {"event",        "ecs_context"                              },
                {"context",      request.contextAddress                     },
                {"entity",       probe.entity                               },
                {"component_id", request.componentId                        },
                {"address",      probe.value                                },
                {"index",        probe.index                                },
                {"count",        probe.count                                },
                {"size",         sized ? nlohmann::json(size) : nlohmann::json(nullptr)},
            }
                .dump()
        );
        return;
    }

    // Runtime-id kinds resolve the actor through the level's actor list,
    // which is the SERVER side in single-player; iterating its registry from
    // the client tick races the server thread (observed AV inside storage()).
    // Run it on the server thread and keep a structured-exception backstop.
    queueServerTask([this, request] {
        if (!runEcsGuarded(request)) {
            enqueue(
                R"({"event":"error","reason":"internal_error","phase":"ecs","message":"structured exception"})"
            );
        }
    });
}

bool ProbeMod::runEcsGuarded(EcsRequest const& request) {
    __try {
        applyRuntimeEcsRequest(request);
        return true;
    } __except (
        isFatalSehCode(GetExceptionCode()) ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH
    ) {
        return false;
    }
}

void ProbeMod::applyRuntimeEcsRequest(EcsRequest const& request) {
    Actor* actor = findActorByRuntimeId(request.runtimeId);
    if (actor == nullptr) {
        enqueue(R"({"event":"error","reason":"entity_not_found"})");
        return;
    }
    auto& context  = actor->getEntityContext();
    auto& registry = context.getRegistry();

    if (request.kind == EcsRequest::Kind::Entity) {
        std::size_t components = 0;
        for (auto&& [id, storage] : registry.storage()) {
            if (storage.contains(context.mEntity)) {
                ++components;
            }
        }
        enqueue(
            nlohmann::json{
                {"event",      "ecs_entity"                                  },
                {"runtime_id", request.runtimeId                             },
                {"type",       actor->getTypeName()                          },
                {"actor",      reinterpret_cast<std::uint64_t>(actor)        },
                {"context",    reinterpret_cast<std::uint64_t>(&context)     },
                {"registry",   reinterpret_cast<std::uint64_t>(&registry)    },
                {"entity",     static_cast<std::uint64_t>(context.mEntity.mRawId)},
                {"components", components                                    },
        }
                .dump()
        );
        return;
    }

    if (request.kind == EcsRequest::Kind::Components) {
        nlohmann::json components = nlohmann::json::array();
        nlohmann::json skipped    = nlohmann::json::array();
        for (auto&& [id, storage] : registry.storage()) {
            if (!storage.contains(context.mEntity)) {
                continue;
            }
            StorageRow row{};
            row.id = static_cast<std::uint32_t>(id);
            probeStorageRow(&storage, context.mEntity, row);
            if (!row.ok) {
                skipped.push_back(row.id);
                continue;
            }
            std::uint32_t tableSize = 0;
            bool const    sized     = componentSize(row.id, tableSize);
            nlohmann::json entry{
                {"id",      row.id    },
                {"index",   row.index },
                {"address", row.address},
                {"stride",  row.stride },
                {"count",   row.count  },
            };
            if (sized) {
                entry["size"] = tableSize;
            }
            auto name = componentName(row.id);
            if (!name.empty()) {
                entry["name"] = std::move(name);
            }
            components.push_back(std::move(entry));
        }
        enqueue(
            nlohmann::json{
                {"event",      "ecs_components"                              },
                {"runtime_id", request.runtimeId                             },
                {"registry",   reinterpret_cast<std::uint64_t>(&registry)    },
                {"entity",     static_cast<std::uint64_t>(context.mEntity.mRawId)},
                {"components", std::move(components)                         },
                {"skipped",    std::move(skipped)                            },
        }
                .dump()
        );
        return;
    }

    // Kind::Component
    auto* storage = registry.storage(static_cast<entt::id_type>(request.componentId));
    if (storage == nullptr || !storage->contains(context.mEntity)) {
        enqueue(
            nlohmann::json{
                {"event",        "error"          },
                {"reason",       "component_not_found"},
                {"component_id", request.componentId},
        }
                .dump()
        );
        return;
    }
    void const* value = storage->value(context.mEntity);
    if (value == nullptr) {
        enqueue(R"({"event":"error","reason":"bad_component"})");
        return;
    }
    std::uint32_t length = request.length;
    if (length == 0) {
        // Prefer the offset table (F5); fall back to inferred stride.
        if (!componentSize(request.componentId, length)) {
            length = storageStride(storage);
            if (length == 0) {
                length = 64;
            }
        }
    }
    length = std::clamp<std::uint32_t>(length, 1, 512);
    std::vector<std::uint8_t> bytes(length);
    nlohmann::json out{
        {"event",        "ecs_component"                                   },
        {"runtime_id",   request.runtimeId                                 },
        {"component_id", request.componentId                               },
        {"address",      reinterpret_cast<std::uint64_t>(value)            },
        {"stride",       storageStride(storage)                            },
        {"len",          length                                            },
    };
    auto name = componentName(request.componentId);
    if (!name.empty()) {
        out["name"] = std::move(name);
    }
    if (!readMemory(reinterpret_cast<std::uint64_t>(value), bytes.data(), bytes.size())) {
        out["ok"]     = false;
        out["reason"] = "bad_addr";
    } else {
        out["ok"]  = true;
        out["hex"] = hexEncode(bytes.data(), bytes.size());
    }
    enqueue(out.dump());
}

} // namespace probe
