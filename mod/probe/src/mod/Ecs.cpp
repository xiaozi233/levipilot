#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {
namespace {
using Registry = entt::basic_registry<EntityId>;

std::uint32_t storageStride(Registry::common_type const* storage) {
    if (!storage || storage->size() < 2) return 0;
    auto first = reinterpret_cast<std::uintptr_t>(storage->value((*storage)[0]));
    auto second = reinterpret_cast<std::uintptr_t>(storage->value((*storage)[1]));
    if (!first || !second) return 0;
    auto distance = first > second ? first - second : second - first;
    return distance <= UINT32_MAX ? static_cast<std::uint32_t>(distance) : 0;
}

bool probeRow(Registry::common_type& storage, EntityId entity, bool details,
              EcsSnapshot::Row& row, bool& present, bool inject, bool& injected) {
    // Contains/index/value/stride all belong to this storage's local boundary.
    // A bad entry cannot discard already captured rows or strand a lock.
    try {
        return guardedGameRead([&] {
            present = storage.contains(entity);
            if (!present) return;
            if (inject && !injected) {
                injected = true;
                injectCaptureFault(CaptureTest::Fault);
            }
            if (!details) return;
            row.count = storage.size();
            row.index = storage.index(entity);
            row.address = reinterpret_cast<std::uint64_t>(storage.value(entity));
            row.stride = storageStride(&storage);
        });
    } catch (...) { return false; }
}

void captureRegistry(Registry& registry, EntityId entity, EcsQuery const& query, EcsSnapshot& out) {
    out.registry = reinterpret_cast<std::uint64_t>(&registry);
    out.entity = static_cast<std::uint64_t>(entity.mRawId);
    bool injected = false;
    collectEcsRows(query.operation, out, registry.storage(), [&](auto& storage, auto& row, bool& present) {
        return probeRow(storage, entity, query.operation == EcsOperation::Components,
                        row, present, query.test == CaptureTest::Partial, injected);
    });
}

void captureComponent(Registry& registry, EntityId entity, EcsQuery const& query, EcsSnapshot& out) {
    auto* storage = registry.storage(static_cast<entt::id_type>(query.componentId));
    if (!storage || !storage->contains(entity)) { out.error = "component_not_found"; return; }
    auto& row = out.component;
    row.id = query.componentId;
    row.address = reinterpret_cast<std::uint64_t>(storage->value(entity));
    row.index = static_cast<std::uint64_t>(storage->index(entity));
    row.count = storage->size();
    if (query.operation == EcsOperation::Context) return;
    if (!row.address) { out.error = "bad_component"; return; }
    row.stride = storageStride(storage);
    auto length = query.length;
    if (!length && !componentSize(query.componentId, length)) length = row.stride ? row.stride : 64;
    out.length = std::clamp<std::uint32_t>(length, 1, out.bytes.size());
    out.ok = readMemory(row.address, out.bytes.data(), out.length);
}

nlohmann::json rowJson(EcsSnapshot::Row const& row) {
    nlohmann::json result{{"id", row.id}, {"index", row.index}, {"address", row.address},
                          {"stride", row.stride}, {"count", row.count}};
    std::uint32_t size = 0;
    if (componentSize(row.id, size)) result["size"] = size;
    auto name = componentName(row.id);
    if (!name.empty()) result["name"] = std::move(name);
    return result;
}

nlohmann::json ecsJson(EcsQuery const& query, EcsSnapshot const& out) {
    if (out.error) return {{"event", "error"}, {"reason", out.error}, {"phase", "ecs"},
                           {"component_id", query.componentId}};
    nlohmann::json result;
    switch (query.operation) {
    case EcsOperation::Registries:
        result = {{"event", "ecs_registries"}, {"registries", nlohmann::json::array({
            {{"scope", "client"}, {"registry", out.registry}, {"components", out.count}, {"entity", out.entity}}})}};
        break;
    case EcsOperation::Entity:
        result = {{"event", "ecs_entity"}, {"runtime_id", query.runtimeId}, {"type", out.type.c_str()},
                  {"actor", out.actor}, {"context", out.context}, {"registry", out.registry},
                  {"entity", out.entity}, {"components", out.count}};
        break;
    case EcsOperation::Components:
        result = {{"event", "ecs_components"}, {"runtime_id", query.runtimeId},
                  {"registry", out.registry}, {"entity", out.entity}, {"components", nlohmann::json::array()}};
        for (std::size_t i = 0; i < out.count; ++i) result["components"].push_back(rowJson(out.rows[i]));
        break;
    case EcsOperation::Context: {
        std::uint32_t size = 0;
        bool sized = componentSize(query.componentId, size);
        auto const& row = out.component;
        result = {{"event", "ecs_context"}, {"context", query.context}, {"registry", out.registry}, {"entity", out.entity},
                  {"component_id", query.componentId}, {"address", row.address}, {"index", row.index},
                  {"count", row.count}, {"size", sized ? nlohmann::json(size) : nlohmann::json(nullptr)}};
        break;
    }
    case EcsOperation::Component:
        result = {{"event", "ecs_component"}, {"runtime_id", query.runtimeId}, {"component_id", query.componentId},
                  {"address", out.component.address}, {"stride", out.component.stride}, {"len", out.length}, {"ok", out.ok}};
        if (auto name = componentName(query.componentId); !name.empty()) result["name"] = std::move(name);
        if (out.ok) result["hex"] = hexEncode(out.bytes.data(), out.length);
        else result["reason"] = "bad_addr";
        break;
    }
    result["scope"] = out.server ? "server" : "client";
    result["truncated"] = out.truncated;
    result["partial"] = out.skippedCount != 0;
    result["visited"] = out.visited;
    result["skipped"] = nlohmann::json::array();
    for (std::size_t i = 0; i < out.skippedCount; ++i) result["skipped"].push_back(out.skipped[i]);
    return result;
}
} // namespace

EcsStage ProbeMod::captureEcsStage(EcsStage stage, EcsQuery const& query, EcsSnapshot& out) {
    EcsStage next = EcsStage::Complete;
    // Owning SDK values stay outside the raw SEH frame. Queues, never tick,
    // perform this work; only scalar identities and bytes cross stages.
    std::vector<Actor*> actors;
    std::string type;
    if (!guardedGameRead([&] {
        if (stage == query.testStage) injectCaptureFault(query.test);
        if (stage == EcsStage::Client) {
            auto client = ll::service::getClientInstance();
            auto* player = client ? client->getLocalPlayer() : nullptr;
            if (!player) { out.error = "no_player"; return; }
            if (query.operation == EcsOperation::Registries) {
                auto& context = player->getEntityContext();
                captureRegistry(context.getRegistry(), context.mEntity, query, out);
                return;
            }
            if (query.operation == EcsOperation::Context) {
                if (!readableRange(query.context, sizeof(EntityContext))) { out.error = "bad_addr"; return; }
                auto& context = *reinterpret_cast<EntityContext*>(query.context);
                // Read only the handle's registry identity and entity id here.
                // Never enumerate an unverified or foreign-thread registry.
                out.registry = reinterpret_cast<std::uint64_t>(&context.getRegistry());
                out.entity = static_cast<std::uint64_t>(context.mEntity.mRawId);
                auto& registry = player->getEntityContext().getRegistry();
                if (out.registry == reinterpret_cast<std::uint64_t>(&registry)) {
                    captureComponent(registry, EntityId(static_cast<EntityId::entity_type>(out.entity)), query, out);
                    return;
                }
            }
            next = EcsStage::Server;
            return;
        }
        out.server = true;
        auto level = ll::service::bedrock::getLevel();
        if (!level) { out.error = "level_unavailable"; return; }
        actors = level->getRuntimeActorList();
        for (auto* actor : actors) {
            if (!actor) continue;
            auto& context = actor->getEntityContext();
            auto& registry = context.getRegistry();
            if (query.operation == EcsOperation::Context) {
                if (out.registry != reinterpret_cast<std::uint64_t>(&registry)) continue;
                captureComponent(registry, EntityId(static_cast<EntityId::entity_type>(out.entity)), query, out);
                return;
            }
            if (static_cast<std::uint64_t>(actor->getRuntimeID()) != query.runtimeId) continue;
            out.actor = reinterpret_cast<std::uint64_t>(actor);
            out.context = reinterpret_cast<std::uint64_t>(&context);
            out.registry = reinterpret_cast<std::uint64_t>(&registry);
            out.entity = static_cast<std::uint64_t>(context.mEntity.mRawId);
            if (query.operation == EcsOperation::Entity) {
                type = actor->getTypeName();
                out.type.assign(type);
                out.truncated = out.type.view().size() != type.size();
            }
            if (query.operation == EcsOperation::Component) captureComponent(registry, context.mEntity, query, out);
            else captureRegistry(registry, context.mEntity, query, out);
            return;
        }
        out.error = query.operation == EcsOperation::Context ? "context_not_found" : "entity_not_found";
    })) out.error = "ecs_fault";
    return next;
}

void ProbeMod::drainEcs() {
    if (auto request = mEcsCapture.current()) {
        if (auto stage = request->dispatch()) {
            if (request->query.test != CaptureTest::Deferred || *stage != request->query.testStage) {
                auto weak = std::weak_ptr{request};
                auto run = [this, weak, stage = *stage] {
                    if (auto active = weak.lock()) active->run(stage, GetTickCount64(), [&](EcsSnapshot& out) {
                        return captureEcsStage(stage, active->query, out);
                    });
                };
                auto failed = [weak](char const* error) { if (auto active = weak.lock()) active->cancel(error); };
                try {
                    if (*stage == EcsStage::Server) queueServerTask(std::move(run), failed);
                    else queueClientTask(std::move(run), failed);
                } catch (...) { request->cancel("ecs_queue_failed"); }
            }
        }
    }
    auto done = mEcsCapture.take(GetTickCount64());
    if (!done.request) return;
    auto result = done.error
        ? nlohmann::json{{"event", "error"}, {"reason", done.error}, {"phase", "ecs"}}
        : ecsJson(done.request->query, done.request->snapshot);
    auto line = result.dump();
    if (done.release()) enqueue(std::move(line));
}
} // namespace probe
