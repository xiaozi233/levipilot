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
        auto const reqId    = activeReqId();
        bool       accepted = false;
        if (target == "state") accepted = mStateCapture.begin({test}, now, timeout, reqId);
        else if (target == "buttons") accepted = mButtonCapture.begin({test}, now, timeout, reqId);
        else if (target == "inventory") accepted = mInventoryCapture.begin({test}, now, timeout, reqId);
        else if (target == "select_slot") accepted = mSlotCapture.begin({0, test}, now, timeout, reqId);
        else if (target == "ecs") {
            EcsQuery query;
            query.test       = test;
            auto const stage = j.value("stage", std::string{"client"});
            if (stage == "client") query.testStage = EcsStage::Client;
            else if (stage == "server") query.testStage = EcsStage::Server;
            else {
                enqueue(R"({"event":"error","reason":"bad_test_stage"})");
                return true;
            }
            query.runtimeId = j.value("runtime_id", std::uint64_t{0});
            query.operation = query.testStage == EcsStage::Client ? EcsOperation::Registries : EcsOperation::Components;
            accepted        = bool(mEcsCapture.begin(query, now, timeout, reqId));
        } else if (target == "entities") {
            EntityQuery query;
            query.test       = test;
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
            accepted = bool(mEntityCapture.begin(query, now, timeout, reqId));
        } else if (target == "blocks") {
            BlockRequest request;
            request.test = test;
            accepted     = mBlockCapture.begin(request, now, timeout, reqId);
        } else if (target == "scan") {
            ScanQuery request;
            request.test = test;
            accepted     = mScanCapture.begin(request, now, timeout, reqId);
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
            if (!mStateCapture.begin({}, now, 2000, activeReqId()))
                enqueue(R"({"event":"error","reason":"state_busy"})");
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
        request.limit  = std::clamp(j.value("limit", 64), 1, 512);
        if (!std::isfinite(request.radius) || request.radius < 0) {
            enqueue(R"({"event":"error","reason":"bad_radius"})");
            return true;
        }
        if (!mEntityCapture.begin(request, GetTickCount64(), 2000, activeReqId()))
            enqueue(R"({"event":"error","reason":"entities_busy"})");
        return true;
    }
    if (cmd == "blocks") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        BlockRequest request;
        if (j.contains("positions")) {
            if (!j["positions"].is_array()) {
                enqueue(R"({"event":"error","reason":"bad_coord","param":"positions"})");
                return true;
            }
            for (auto const& pos : j["positions"]) {
                if (request.count >= request.positions.size()) {
                    break;
                }
                if (!pos.is_array() || pos.size() != 3) {
                    enqueue(R"({"event":"error","reason":"bad_coord","param":"positions"})");
                    return true;
                }
                double x = 0.0, y = 0.0, z = 0.0;
                // A raw static_cast<int> on an out-of-range double is UB
                // (MSVC yields INT_MIN), so coordinates are checked first.
                if (!numberValue(pos[0], -CoordHorizonLimit, CoordHorizonLimit, x)
                    || !numberValue(pos[1], CoordHeightLow, CoordHeightHigh, y)
                    || !numberValue(pos[2], -CoordHorizonLimit, CoordHorizonLimit, z)) {
                    enqueue(R"({"event":"error","reason":"bad_coord","param":"positions"})");
                    return true;
                }
                request.positions[request.count++] = BlockPoint{
                    static_cast<int>(std::floor(x)),
                    static_cast<int>(std::floor(y)),
                    static_cast<int>(std::floor(z)),
                };
            }
        }
        if (!mBlockCapture.begin(request, GetTickCount64(), 2000, activeReqId()))
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
        if (!scanVolumeWithinLimit(request.from, request.to)) {
            enqueue(R"({"event":"error","reason":"region_too_large","limit":32768})");
            return true;
        }
        request.includeAir = j.value("include_air", false);
        request.limit      = std::clamp(j.value("limit", 4096), 1, 16384);
        if (!mScanCapture.begin(request, GetTickCount64(), 10000, activeReqId()))
            enqueue(R"({"event":"error","reason":"scan_busy"})");
        return true;
    }
    if (cmd == "select_slot") {
        if (!mWorld.inWorld()) enqueue(R"({"event":"error","reason":"not_in_world"})");
        else if (!mSlotCapture.begin({std::clamp(j.value("slot", 0), 0, 8)}, GetTickCount64(), 2000, activeReqId()))
            enqueue(R"({"event":"error","reason":"select_slot_busy"})");
        else enqueue(R"({"event":"select_slot_ok"})");
        return true;
    }
    if (cmd == "inventory") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
        } else {
            if (!mInventoryCapture.begin({}, GetTickCount64(), 2000, activeReqId()))
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
            clear ? 0 : j.value("aux", 0),
            clear ? -1 : j.value("damage", -1),
            clear ? -1 : j.value("flight", -1)
        );
        return true;
    }
    if (cmd == "give") {
        queueGive(
            j.value("item", std::string{}),
            j.value("count", 1),
            j.value("aux", 0),
            j.value("damage", -1),
            j.value("flight", -1)
        );
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
    if (cmd == "ecs_registries" || cmd == "ecs_entity" || cmd == "ecs_components" || cmd == "ecs_component"
        || cmd == "ecs_context") {
        auto const epoch = mPoseEpoch.load();
        if (!mWorld.inWorld() || !hooksAllowed()) {
            enqueue(
                nlohmann::json{
                    {"event",  "error"                                               },
                    {"reason", mWorld.inWorld() ? "version_mismatch" : "not_in_world"}
            }
                    .dump()
            );
            return true;
        }
        EcsQuery query;
        query.runtimeId   = j.value("runtime_id", std::uint64_t{0});
        query.componentId = j.value("component_id", std::uint32_t{0});
        if (cmd == "ecs_registries") {
            query.operation  = EcsOperation::Registries;
            auto const scope = j.value("scope", std::string{"client"});
            if (scope == "client") query.scope = EcsScope::Client;
            else if (scope == "server") query.scope = EcsScope::Server;
            else if (scope == "all") query.scope = EcsScope::All;
            else {
                enqueue(R"({"event":"error","reason":"bad_scope"})");
                return true;
            }
        } else if (cmd == "ecs_entity") query.operation = EcsOperation::Entity;
        else if (cmd == "ecs_components") query.operation = EcsOperation::Components;
        else if (cmd == "ecs_context") {
            query.operation = EcsOperation::Context;
            if (!parseAddress(j.value("context", nlohmann::json{}), query.context)) {
                enqueue(R"({"event":"error","reason":"bad_addr"})");
                return true;
            }
        } else {
            query.operation = EcsOperation::Component;
            query.length    = j.value("len", std::uint32_t{0});
        }
        if (auto request = mEcsCapture.begin(query, GetTickCount64(), 2000, activeReqId())) {
            if (!mWorld.inWorld() || mPoseEpoch.load() != epoch) request->cancel("world_left");
        } else enqueue(R"({"event":"error","reason":"ecs_busy","phase":"ecs"})");
        return true;
    }
    if (cmd == "ecs_read_batch") {
        auto const epoch = mPoseEpoch.load();
        if (!mWorld.inWorld() || !hooksAllowed()) {
            enqueue(
                nlohmann::json{
                    {"event",  "error"                                               },
                    {"reason", mWorld.inWorld() ? "version_mismatch" : "not_in_world"}
            }
                    .dump()
            );
            return true;
        }
        if (!j.contains("items") || !j["items"].is_array() || j["items"].empty()
            || j["items"].size() > EcsQuery::MaxBatchItems) {
            enqueue(R"({"event":"error","reason":"bad_param","param":"items"})");
            return true;
        }
        EcsQuery query;
        query.operation = EcsOperation::ContextBatch;
        if (j.contains("context") && !j["context"].is_null()) {
            if (!parseAddress(j["context"], query.context)) {
                enqueue(R"({"event":"error","reason":"bad_addr"})");
                return true;
            }
        }
        bool needMemRead = false;
        for (auto const& item : j["items"]) {
            if (!item.is_object() || !item.contains("component_id") || !item["component_id"].is_number_integer()) {
                enqueue(R"({"event":"error","reason":"bad_param","param":"items"})");
                return true;
            }
            auto const rawId = item["component_id"].get<std::int64_t>();
            if (rawId < 0 || rawId > static_cast<std::int64_t>(UINT32_MAX)) {
                enqueue(R"({"event":"error","reason":"bad_param","param":"component_id"})");
                return true;
            }
            std::int64_t const offset = item.value("offset", std::int64_t{0});
            std::int64_t const len    = item.value("len", std::int64_t{0});
            if (offset < 0 || offset > 65536 || len < 0 || len > static_cast<std::int64_t>(EcsQuery::MaxBatchBytes)) {
                enqueue(R"({"event":"error","reason":"bad_param","param":"items"})");
                return true;
            }
            if (len > 0) needMemRead = true;
            query.batchItems[query.batchCount++] = {
                static_cast<std::uint32_t>(rawId),
                static_cast<std::uint32_t>(offset),
                static_cast<std::uint32_t>(len),
            };
        }
        if (needMemRead && !mAllowMemoryRead.load(std::memory_order_relaxed)) {
            enqueue(R"({"event":"error","reason":"read_disabled","hint":"set allow_mem_read=true in harness.json"})");
            return true;
        }
        if (auto request = mEcsCapture.begin(query, GetTickCount64(), 2000, activeReqId())) {
            if (!mWorld.inWorld() || mPoseEpoch.load() != epoch) request->cancel("world_left");
        } else enqueue(R"({"event":"error","reason":"ecs_busy","phase":"ecs"})");
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
        if (!mEntityCapture.begin(action, GetTickCount64(), 2000, activeReqId()))
            enqueue(R"({"event":"error","reason":"entities_busy"})");
        return true;
    }
    if (cmd == "block_shapes") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        auto const identity = localPlayerIdentity();
        if (!identity.valid) {
            enqueue(R"({"event":"error","reason":"player_unknown"})");
            return true;
        }
        bool        invalidMode = false;
        std::string entityMode  = safeJsonString(j, "entity_mode", "none", &invalidMode);
        if (invalidMode
            || (entityMode != "none" && entityMode != "player" && entityMode != "leather_boots"
                && entityMode != "sneak_above")) {
            enqueue(R"({"event":"error","reason":"bad_param","param":"entity_mode"})");
            return true;
        }
        if (!j.contains("positions") || !j["positions"].is_array() || j["positions"].empty()
            || j["positions"].size() > 64) {
            enqueue(R"({"event":"error","reason":"bad_coord","param":"positions"})");
            return true;
        }
        std::vector<BlockPoint> positions;
        positions.reserve(j["positions"].size());
        for (auto const& pos : j["positions"]) {
            if (!pos.is_array() || pos.size() != 3) {
                enqueue(R"({"event":"error","reason":"bad_coord","param":"positions"})");
                return true;
            }
            double x = 0.0, y = 0.0, z = 0.0;
            if (!numberValue(pos[0], -CoordHorizonLimit, CoordHorizonLimit, x)
                || !numberValue(pos[1], CoordHeightLow, CoordHeightHigh, y)
                || !numberValue(pos[2], -CoordHorizonLimit, CoordHorizonLimit, z)) {
                enqueue(R"({"event":"error","reason":"bad_coord","param":"positions"})");
                return true;
            }
            positions.push_back(
                BlockPoint{
                    static_cast<int>(std::floor(x)),
                    static_cast<int>(std::floor(y)),
                    static_cast<int>(std::floor(z)),
                }
            );
        }
        runBlockShapesQuery(identity, std::move(positions), std::move(entityMode));
        return true;
    }
    if (cmd == "dump_block_collisions") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        auto const identity = localPlayerIdentity();
        if (!identity.valid) {
            enqueue(R"({"event":"error","reason":"player_unknown"})");
            return true;
        }
        bool        invalidFilename = false;
        std::string filename        = safeJsonString(j, "filename", "block-collisions.jsonl", &invalidFilename);
        auto        validFilename   = [](std::string const& s) {
            if (s.empty() || s.size() > 128 || s.front() == '.') return false;
            return std::all_of(s.begin(), s.end(), [](unsigned char c) {
                return std::isalnum(c) || c == '.' || c == '_' || c == '-';
            });
        };
        if (invalidFilename || !validFilename(filename)) {
            enqueue(R"({"event":"error","reason":"bad_param","param":"filename"})");
            return true;
        }
        bool        invalidMode = false;
        std::string entityMode  = safeJsonString(j, "entity_mode", "none", &invalidMode);
        if (invalidMode
            || (entityMode != "none" && entityMode != "player" && entityMode != "leather_boots"
                && entityMode != "sneak_above")) {
            enqueue(R"({"event":"error","reason":"bad_param","param":"entity_mode"})");
            return true;
        }
        bool invalidAir = false;
        bool includeAir = safeJsonBool(j, "include_air", false, &invalidAir);
        if (invalidAir) {
            enqueue(R"({"event":"error","reason":"bad_param","param":"include_air"})");
            return true;
        }
        bool       hasPos = false;
        BlockPoint pos{0, 0, 0};
        if (j.contains("pos") && !j["pos"].is_null()) {
            if (!j["pos"].is_array() || j["pos"].size() != 3) {
                enqueue(R"({"event":"error","reason":"bad_coord","param":"pos"})");
                return true;
            }
            double x = 0.0, y = 0.0, z = 0.0;
            if (!numberValue(j["pos"][0], -CoordHorizonLimit, CoordHorizonLimit, x)
                || !numberValue(j["pos"][1], CoordHeightLow, CoordHeightHigh, y)
                || !numberValue(j["pos"][2], -CoordHorizonLimit, CoordHorizonLimit, z)) {
                enqueue(R"({"event":"error","reason":"bad_coord","param":"pos"})");
                return true;
            }
            pos = BlockPoint{
                static_cast<int>(std::floor(x)),
                static_cast<int>(std::floor(y)),
                static_cast<int>(std::floor(z)),
            };
            hasPos = true;
        }
        runDumpBlockCollisions(identity, std::move(filename), hasPos, pos, includeAir, std::move(entityMode));
        return true;
    }
    return false;
}

} // namespace probe
