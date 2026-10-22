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
        for (auto&& [id, storage] : registry.storage()) {
            if (!storage.contains(context.mEntity)) {
                continue;
            }
            void const* value = storage.value(context.mEntity);
            components.push_back({
                {"id",      static_cast<std::uint32_t>(id)                   },
                {"index",   static_cast<std::uint64_t>(storage.index(context.mEntity))},
                {"address", reinterpret_cast<std::uint64_t>(value)           },
                {"stride",  storageStride(&storage)                          },
                {"count",   static_cast<std::uint64_t>(storage.size())       },
            });
        }
        enqueue(
            nlohmann::json{
                {"event",      "ecs_components"                              },
                {"runtime_id", request.runtimeId                             },
                {"registry",   reinterpret_cast<std::uint64_t>(&registry)    },
                {"entity",     static_cast<std::uint64_t>(context.mEntity.mRawId)},
                {"components", std::move(components)                         },
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
        length = storageStride(storage);
        if (length == 0) {
            length = 64;
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
