#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

bool ProbeMod::dispatchQueryCommand(std::string const& cmd, nlohmann::json& j) {
    if (cmd == "selftest_query_failure") {
        if (!mAllowSelftestStress.load()) {
            enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
            return true;
        }
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        auto const  mode = j.value("mode", std::string{});
        CaptureTest test;
        if (mode == "seh") test = CaptureTest::Fault;
        else if (mode == "cpp") test = CaptureTest::Cpp;
        else if (mode == "partial") test = CaptureTest::Partial;
        else if (mode == "timeout") test = CaptureTest::Deferred;
        else {
            enqueue(R"({"event":"error","reason":"bad_test_mode"})");
            return true;
        }
        auto const target   = j.value("target", std::string{"state"});
        auto const now      = GetTickCount64();
        auto const timeout  = static_cast<std::uint64_t>(std::clamp(j.value("timeout_ms", 250), 1, 10000));
        bool       accepted = false;
        if (target == "state") accepted = mStateCapture.begin({test}, now, timeout);
        else if (target == "buttons") accepted = mButtonCapture.begin({test}, now, timeout);
        else if (target == "inventory") accepted = mInventoryCapture.begin({test}, now, timeout);
        else if (target == "select_slot") accepted = mSlotCapture.begin({0, test}, now, timeout);
        else if (target == "entities") {
            EntityQuery query;
            query.test = test;
            auto const stage = j.value("stage", std::string{"client"});
            if (stage == "client") query.testStage = EntityStage::Client;
            else if (stage == "server") query.testStage = EntityStage::Server;
            else if (stage == "aim") {
                query.testStage = EntityStage::Aim;
                query.operation = EntityOperation::LookAt;
                query.runtimeId = j.value("runtime_id", std::uint64_t{0});
            } else {
                enqueue(R"({"event":"error","reason":"bad_test_stage"})");
                return true;
            }
            accepted = bool(mEntityCapture.begin(query, now, timeout));
        }
        else if (target == "blocks") {
            BlockRequest request;
            request.test = test;
            accepted     = mBlockCapture.begin(request, now, timeout);
        } else if (target == "scan") {
            ScanQuery request;
            request.test = test;
            accepted     = mScanCapture.begin(request, now, timeout);
        } else {
            enqueue(R"({"event":"error","reason":"bad_test_target"})");
            return true;
        }
        if (!accepted) enqueue(R"({"event":"error","reason":"capture_busy"})");
        return true;
    }
    if (cmd == "chat_history") {
        requestChatHistory(std::clamp(j.value("limit", 32), 1, 64));
        return true;
    }
    if (cmd == "state") {
        auto const now = GetTickCount64();
        if (!mWorld.inWorld() || (mLastTickMs.load() != 0 && now - mLastTickMs.load() > 1000)) {
            enqueue(R"({"event":"state","in_world":false})");
        } else {
            if (!mStateCapture.begin({}, now, 2000)) enqueue(R"({"event":"error","reason":"state_busy"})");
        }
        return true;
    }
    if (cmd == "entities") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        EntityQuery request;
        request.radius = j.value("radius", 16.0f);
        request.limit = std::clamp(j.value("limit", 64), 1, 512);
        if (!std::isfinite(request.radius) || request.radius < 0) {
            enqueue(R"({"event":"error","reason":"bad_radius"})");
            return true;
        }
        if (!mEntityCapture.begin(request, GetTickCount64()))
            enqueue(R"({"event":"error","reason":"entities_busy"})");
        return true;
    }
    if (cmd == "blocks") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        BlockRequest request;
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
                request.positions[request.count++] = BlockPoint{
                    static_cast<int>(std::floor(x)),
                    static_cast<int>(std::floor(y)),
                    static_cast<int>(std::floor(z)),
                };
                if (request.count >= request.positions.size()) {
                    break;
                }
            }
        }
        if (!mBlockCapture.begin(request, GetTickCount64(), 2000))
            enqueue(R"({"event":"error","reason":"blocks_busy"})");
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
        ScanQuery request;
        if (!readCorner("from", request.from.x, request.from.y, request.from.z)
            || !readCorner("to", request.to.x, request.to.y, request.to.z)) {
            enqueue(R"({"event":"error","reason":"bad_region"})");
            return true;
        }
        std::int64_t const dx = std::llabs(static_cast<std::int64_t>(request.to.x) - request.from.x) + 1;
        std::int64_t const dy = std::llabs(static_cast<std::int64_t>(request.to.y) - request.from.y) + 1;
        std::int64_t const dz = std::llabs(static_cast<std::int64_t>(request.to.z) - request.from.z) + 1;
        if (dx * dy * dz > 32768) {
            enqueue(R"({"event":"error","reason":"region_too_large","limit":32768})");
            return true;
        }
        request.includeAir = j.value("include_air", false);
        request.limit      = std::clamp(j.value("limit", 4096), 1, 16384);
        if (!mScanCapture.begin(request, GetTickCount64(), 10000)) enqueue(R"({"event":"error","reason":"scan_busy"})");
        return true;
    }
    if (cmd == "select_slot") {
        if (!mWorld.inWorld()) enqueue(R"({"event":"error","reason":"not_in_world"})");
        else if (!mSlotCapture.begin({std::clamp(j.value("slot", 0), 0, 8)}, GetTickCount64(), 2000))
            enqueue(R"({"event":"error","reason":"select_slot_busy"})");
        else enqueue(R"({"event":"select_slot_ok"})");
        return true;
    }
    if (cmd == "inventory") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
        } else {
            if (!mInventoryCapture.begin({}, GetTickCount64(), 2000))
                enqueue(R"({"event":"error","reason":"inventory_busy"})");
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
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        EntityQuery action;
        if (cmd == "entity") {
            action.operation = EntityOperation::Info;
        } else if (cmd == "attack_entity") {
            action.operation = EntityOperation::Attack;
        } else if (cmd == "interact_entity") {
            action.operation = EntityOperation::Interact;
        } else {
            action.operation = EntityOperation::LookAt;
        }
        action.runtimeId = j.value("runtime_id", std::uint64_t{0});
        if (!mEntityCapture.begin(action, GetTickCount64()))
            enqueue(R"({"event":"error","reason":"entities_busy"})");
        return true;
    }
    return false;
}

} // namespace probe
