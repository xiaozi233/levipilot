#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

bool ProbeMod::dispatchQueryCommand(std::string const& cmd, nlohmann::json& j) {
    if (cmd == "chat_history") {
        requestChatHistory(std::clamp(j.value("limit", 32), 1, 64));
        return true;
    }
    if (cmd == "state") {
        auto const now = GetTickCount64();
        if (!mWorld.inWorld() || (mLastTickMs.load() != 0 && now - mLastTickMs.load() > 1000)) {
            enqueue(R"({"event":"state","in_world":false})");
        } else {
            mStateRequested = true;
        }
        return true;
    }
    if (cmd == "entities") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        {
            std::lock_guard lock(mEntityMutex);
            mEntityRadius      = j.value("radius", 16.0f);
            mEntityLimit       = std::clamp(j.value("limit", 64), 1, 512);
            mEntitiesRequested = true;
        }
        return true;
    }
    if (cmd == "blocks") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        std::vector<BlockQuery> queries;
        if (j.contains("positions") && j["positions"].is_array()) {
            for (auto const& pos : j["positions"]) {
                if (!pos.is_array() || pos.size() != 3) {
                    continue;
                }
                double x = 0.0, y = 0.0, z = 0.0;
                // A raw static_cast<int> on an out-of-range double is UB
                // (MSVC yields INT_MIN), so coordinates are checked first.
                if (!numberValue(pos[0], -CoordHorizonLimit, CoordHorizonLimit, x)
                    || !numberValue(pos[1], CoordHeightLow, CoordHeightHigh, y)
                    || !numberValue(pos[2], -CoordHorizonLimit, CoordHorizonLimit, z)) {
                    continue;
                }
                queries.push_back(
                    BlockQuery{
                        static_cast<int>(std::floor(x)),
                        static_cast<int>(std::floor(y)),
                        static_cast<int>(std::floor(z)),
                    }
                );
                if (queries.size() >= 256) {
                    break;
                }
            }
        }
        {
            std::lock_guard lock(mBlockMutex);
            mBlockQueries    = std::move(queries);
            mBlocksRequested = true;
        }
        return true;
    }
    if (cmd == "scan") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        auto readCorner = [&](char const* key, int& x, int& y, int& z) -> bool {
            if (!j.contains(key) || !j[key].is_array() || j[key].size() != 3) {
                return false;
            }
            double cx = 0.0, cy = 0.0, cz = 0.0;
            if (!numberValue(j[key][0], -CoordHorizonLimit, CoordHorizonLimit, cx)
                || !numberValue(j[key][1], CoordHeightLow, CoordHeightHigh, cy)
                || !numberValue(j[key][2], -CoordHorizonLimit, CoordHorizonLimit, cz)) {
                return false;
            }
            x = static_cast<int>(std::floor(cx));
            y = static_cast<int>(std::floor(cy));
            z = static_cast<int>(std::floor(cz));
            return true;
        };
        ScanRequest request;
        if (!readCorner("from", request.x1, request.y1, request.z1)
            || !readCorner("to", request.x2, request.y2, request.z2)) {
            enqueue(R"({"event":"error","reason":"bad_region"})");
            return true;
        }
        std::int64_t const dx = std::llabs(static_cast<std::int64_t>(request.x2) - request.x1) + 1;
        std::int64_t const dy = std::llabs(static_cast<std::int64_t>(request.y2) - request.y1) + 1;
        std::int64_t const dz = std::llabs(static_cast<std::int64_t>(request.z2) - request.z1) + 1;
        if (dx * dy * dz > 32768) {
            enqueue(R"({"event":"error","reason":"region_too_large","limit":32768})");
            return true;
        }
        request.includeAir = j.value("include_air", false);
        request.limit      = std::clamp(j.value("limit", 4096), 1, 16384);
        {
            std::lock_guard lock(mScanMutex);
            mScanRequest   = request;
            mScanRequested = true;
        }
        return true;
    }
    if (cmd == "select_slot") {
        mSlotRequest.store(std::clamp(j.value("slot", 0), 0, 8));
        enqueue(R"({"event":"select_slot_ok"})");
        return true;
    }
    if (cmd == "inventory") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
        } else {
            mInventoryRequested = true;
        }
        return true;
    }
    if (cmd == "set_item" || cmd == "clear_item") {
        auto const container = j.value("container", std::string{"inventory"});
        if (container != "inventory" && container != "armor" && container != "offhand") {
            enqueue(R"({"event":"error","reason":"bad_container"})");
            return true;
        }
        bool const clear = cmd == "clear_item";
        queueInventoryWrite(
            container,
            j.value("slot", 0),
            clear ? std::string{} : j.value("item", std::string{}),
            clear ? 0 : j.value("count", 1),
            clear ? 0 : j.value("aux", 0)
        );
        return true;
    }
    if (cmd == "give") {
        queueGive(j.value("item", std::string{}), j.value("count", 1), j.value("aux", 0));
        return true;
    }
    if (cmd == "equip") {
        queueEquip(j.value("target", std::string{}), j.value("slot", -1));
        return true;
    }
    if (cmd == "clear_inventory") {
        queueClearInventory();
        return true;
    }
    if (cmd == "ecs_registries") {
        EcsRequest request;
        request.kind = EcsRequest::Kind::Registries;
        {
            std::lock_guard lock(mEcsMutex);
            mEcsRequest = request;
        }
        return true;
    }
    if (cmd == "ecs_entity" || cmd == "ecs_components" || cmd == "ecs_component" || cmd == "ecs_context") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        EcsRequest request;
        request.runtimeId = j.value("runtime_id", std::uint64_t{0});
        if (cmd == "ecs_entity") {
            request.kind = EcsRequest::Kind::Entity;
        } else if (cmd == "ecs_components") {
            request.kind = EcsRequest::Kind::Components;
        } else if (cmd == "ecs_context") {
            std::uint64_t contextAddr = 0;
            if (!parseAddress(j.value("context", nlohmann::json{}), contextAddr)) {
                enqueue(R"({"event":"error","reason":"bad_addr"})");
                return true;
            }
            request.kind           = EcsRequest::Kind::Context;
            request.contextAddress = contextAddr;
            request.componentId    = j.value("component_id", std::uint32_t{0});
        } else {
            request.kind        = EcsRequest::Kind::Component;
            request.componentId = j.value("component_id", std::uint32_t{0});
            request.length      = j.value("len", std::uint32_t{0});
        }
        {
            std::lock_guard lock(mEcsMutex);
            mEcsRequest = request;
        }
        return true;
    }
    if (cmd == "entity" || cmd == "attack_entity" || cmd == "interact_entity" || cmd == "look_at_entity") {
        EntityAction action;
        if (cmd == "entity") {
            action.kind = EntityAction::Kind::Info;
        } else if (cmd == "attack_entity") {
            action.kind = EntityAction::Kind::Attack;
        } else if (cmd == "interact_entity") {
            action.kind = EntityAction::Kind::Interact;
        } else {
            action.kind = EntityAction::Kind::LookAt;
        }
        action.runtimeId = j.value("runtime_id", std::uint64_t{0});
        {
            std::lock_guard lock(mEntityActionMutex);
            mEntityAction = action;
        }
        return true;
    }
    return false;
}

} // namespace probe
