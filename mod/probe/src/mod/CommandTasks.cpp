#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

bool ProbeMod::dispatchTasksCommand(std::string const& cmd, nlohmann::json& j) {
    if (cmd == "selftest_use_failure") {
        if (!mAllowSelftestStress.load()) {
            enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
            return true;
        }
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        auto const mode = j.value("mode", std::string{});
        UseTask    task;
        if (mode == "timeout") task.testMode = UseTask::TestMode::DropCallback;
        else if (mode == "queue") task.testMode = UseTask::TestMode::QueueFailure;
        else if (mode == "seh") task.testMode = UseTask::TestMode::CallbackSeh;
        else if (mode == "cpp") task.testMode = UseTask::TestMode::CallbackCpp;
        else if (mode == "effect_seh") task.testMode = UseTask::TestMode::EffectSeh;
        else {
            enqueue(R"({"event":"error","reason":"bad_test_mode"})");
            return true;
        }
        // Shorter than production, so deterministic live checks stay bounded.
        task.serverTimeoutMs = 250;
        auto const id        = startTask(task);
        if (!id) {
            enqueue(R"({"event":"error","reason":"task_backpressure"})");
            return true;
        }
        enqueue(
            nlohmann::json{
                {"event",   "use_started"},
                {"task_id", id           },
                {"ticks",   task.ticks   }
        }.dump()
        );
        return true;
    }
    if (cmd == "selftest_task_fault") {
        if (!mAllowSelftestStress.load()) {
            enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
            return true;
        }
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        UseTask task;
        task.ticks    = 2000;
        auto const id = startTask(task);
        if (id == 0) {
            enqueue(R"({"event":"error","reason":"task_backpressure"})");
            return true;
        }
        mTaskFaultId.store(id);
        enqueue(
            nlohmann::json{
                {"event",   "task_fault_started"},
                {"task_id", id                  }
        }.dump()
        );
        return true;
    }
    if (cmd == "navigate") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        double x = 0.0, z = 0.0;
        if (!numberJson(j, "x", -CoordHorizonLimit, CoordHorizonLimit, 0.0, x)
            || !numberJson(j, "z", -CoordHorizonLimit, CoordHorizonLimit, 0.0, z)) {
            enqueue(R"({"event":"error","reason":"bad_coord"})");
            return true;
        }
        double tolerance = 0.35, maxTicksVal = 600.0, jumpDist = 1.0, edgeLookahead = 0.55;
        if (!numberJson(j, "tolerance", 0.0, 1e6, 0.35, tolerance)
            || !numberJson(j, "max_ticks", -1e9, 1e9, 600.0, maxTicksVal)
            || !numberJson(j, "jump_distance", 0.0, 1e6, 1.0, jumpDist)
            || !numberJson(j, "edge_lookahead", 0.0, 1e6, 0.55, edgeLookahead)) {
            enqueue(R"({"event":"error","reason":"bad_param"})");
            return true;
        }
        NavigateTask task;
        task.x                 = static_cast<float>(x);
        task.z                 = static_cast<float>(z);
        task.tolerance         = std::max(0.05f, static_cast<float>(tolerance));
        task.sprint            = j.value("sprint", false);
        task.maxTicks          = std::clamp(static_cast<int>(maxTicksVal), 1, 200000);
        std::string const jump = j.value("jump", std::string{"auto"});
        if (jump != "auto" && jump != "distance" && jump != "edge" && jump != "never") {
            enqueue(R"({"event":"error","reason":"invalid_jump_mode"})");
            return true;
        }
        if (jump == "distance") {
            task.jumpMode = JumpMode::Distance;
        } else if (jump == "edge") {
            task.jumpMode = JumpMode::Edge;
        } else if (jump == "never") {
            task.jumpMode = JumpMode::Never;
        }
        task.jumpDistance  = std::clamp(static_cast<float>(jumpDist), 0.1f, 8.0f);
        task.edgeLookahead = std::clamp(static_cast<float>(edgeLookahead), 0.2f, 1.5f);
        auto const taskId  = startTask(task);
        if (taskId == 0) {
            enqueue(R"({"event":"error","reason":"task_backpressure"})");
            return true;
        }
        enqueue(
            nlohmann::json{
                {"event",         "navigate_started"},
                {"task_id",       taskId            },
                {"x",             task.x            },
                {"z",             task.z            },
                {"tolerance",     task.tolerance    },
                {"max_ticks",     task.maxTicks     },
                {"jump",          jump              },
                {"jump_distance", task.jumpDistance },
        }
                .dump()
        );
        return true;
    }
    if (cmd == "jump_when") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        JumpTask          task;
        std::string const mode = j.value("mode", std::string{"now"});
        if (mode != "now" && mode != "tick" && mode != "distance" && mode != "edge") {
            enqueue(R"({"event":"error","reason":"invalid_jump_mode"})");
            return true;
        }
        if (mode == "distance") {
            task.mode = JumpMode::Distance;
        } else if (mode == "edge") {
            task.mode = JumpMode::Edge;
        } else if (mode == "tick") {
            task.mode = JumpMode::Tick;
        } else {
            task.mode = JumpMode::Now;
        }
        double x = 0.0, z = 0.0;
        if (!numberJson(j, "x", -CoordHorizonLimit, CoordHorizonLimit, 0.0, x)
            || !numberJson(j, "z", -CoordHorizonLimit, CoordHorizonLimit, 0.0, z)) {
            enqueue(R"({"event":"error","reason":"bad_coord"})");
            return true;
        }
        double dist = 0.6, fwd = 0.0, yawVal = 0.0, afterVal = 1.0, maxTicksVal = 60.0, edgeVal = 0.55;
        if (!numberJson(j, "distance", 0.0, 1e6, 0.6, dist) || !numberJson(j, "forward", -1e6, 1e6, 0.0, fwd)
            || !numberJson(j, "yaw", -1e9, 1e9, 0.0, yawVal) || !numberJson(j, "after_ticks", -1e9, 1e9, 1.0, afterVal)
            || !numberJson(j, "max_ticks", -1e9, 1e9, 60.0, maxTicksVal)
            || !numberJson(j, "edge_lookahead", 0.0, 1e6, 0.55, edgeVal)) {
            enqueue(R"({"event":"error","reason":"bad_param"})");
            return true;
        }
        task.x             = static_cast<float>(x);
        task.z             = static_cast<float>(z);
        task.distance      = std::clamp(static_cast<float>(dist), 0.05f, 8.0f);
        task.forward       = std::clamp(static_cast<float>(fwd), -1.0f, 1.0f);
        task.sprint        = j.value("sprint", false);
        task.hasYaw        = j.contains("yaw");
        task.yaw           = static_cast<float>(yawVal);
        task.afterTicks    = std::clamp(static_cast<int>(afterVal), 1, 2000);
        task.maxTicks      = std::clamp(static_cast<int>(maxTicksVal), 1, 5000);
        task.edgeLookahead = std::clamp(static_cast<float>(edgeVal), 0.2f, 1.5f);
        auto const taskId  = startTask(task);
        if (taskId == 0) {
            enqueue(R"({"event":"error","reason":"task_backpressure"})");
            return true;
        }
        enqueue(
            nlohmann::json{
                {"event",       "jump_when_started"},
                {"task_id",     taskId             },
                {"mode",        mode               },
                {"x",           task.x             },
                {"z",           task.z             },
                {"distance",    task.distance      },
                {"forward",     task.forward       },
                {"sprint",      task.sprint        },
                {"after_ticks", task.afterTicks    },
                {"max_ticks",   task.maxTicks      },
        }
                .dump()
        );
        return true;
    }
    if (cmd == "route") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        if (!j.contains("waypoints") || !j["waypoints"].is_array() || j["waypoints"].empty()) {
            enqueue(R"({"event":"error","reason":"missing_waypoints"})");
            return true;
        }
        if (j["waypoints"].size() > RouteTask::MaxWaypoints) {
            enqueue(R"({"event":"error","reason":"too_many_waypoints"})");
            return true;
        }
        RouteTask task;
        task.sprint = j.value("sprint", false);
        for (auto const& row : j["waypoints"]) {
            if (!row.is_object()) {
                enqueue(R"({"event":"error","reason":"invalid_waypoint"})");
                return true;
            }
            double wx = 0.0, wz = 0.0;
            if (!numberJson(row, "x", -CoordHorizonLimit, CoordHorizonLimit, 0.0, wx)
                || !numberJson(row, "z", -CoordHorizonLimit, CoordHorizonLimit, 0.0, wz)) {
                enqueue(R"({"event":"error","reason":"bad_coord"})");
                return true;
            }
            double tol = 0.35, maxTicksVal = 600.0, jumpDist = 1.0, edgeVal = 0.55;
            if (!numberJson(row, "tolerance", 0.0, 1e6, 0.35, tol)
                || !numberJson(row, "max_ticks", -1e9, 1e9, 600.0, maxTicksVal)
                || !numberJson(row, "jump_distance", 0.0, 1e6, 1.0, jumpDist)
                || !numberJson(row, "edge_lookahead", 0.0, 1e6, 0.55, edgeVal)) {
                enqueue(R"({"event":"error","reason":"bad_param"})");
                return true;
            }
            RouteWaypoint point;
            point.x                = static_cast<float>(wx);
            point.z                = static_cast<float>(wz);
            point.tolerance        = std::clamp(static_cast<float>(tol), 0.05f, 2.0f);
            point.maxTicks         = std::clamp(static_cast<int>(maxTicksVal), 1, 200000);
            std::string const jump = row.value("jump", std::string{"auto"});
            if (jump != "auto" && jump != "distance" && jump != "edge" && jump != "never") {
                enqueue(R"({"event":"error","reason":"invalid_jump_mode"})");
                return true;
            }
            if (jump == "distance") {
                point.jumpMode = JumpMode::Distance;
            } else if (jump == "edge") {
                point.jumpMode = JumpMode::Edge;
            } else if (jump == "never") {
                point.jumpMode = JumpMode::Never;
            }
            point.jumpDistance        = std::clamp(static_cast<float>(jumpDist), 0.1f, 8.0f);
            point.edgeLookahead       = std::clamp(static_cast<float>(edgeVal), 0.2f, 1.5f);
            task.points[task.count++] = point;
        }
        if (task.count == 0) {
            enqueue(R"({"event":"error","reason":"missing_waypoints"})");
            return true;
        }
        auto const taskId = startTask(task);
        if (taskId == 0) {
            enqueue(R"({"event":"error","reason":"task_backpressure"})");
            return true;
        }
        enqueue(
            nlohmann::json{
                {"event",     "route_started"},
                {"task_id",   taskId         },
                {"waypoints", task.count     },
                {"sprint",    task.sprint    },
        }
                .dump()
        );
        return true;
    }
    if (cmd == "mine") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        double mx = 0.0, my = 0.0, mz = 0.0, maxTicksVal = 400.0;
        if (!numberJson(j, "x", -CoordHorizonLimit, CoordHorizonLimit, 0.0, mx)
            || !numberJson(j, "y", CoordHeightLow, CoordHeightHigh, 0.0, my)
            || !numberJson(j, "z", -CoordHorizonLimit, CoordHorizonLimit, 0.0, mz)) {
            enqueue(R"({"event":"error","reason":"bad_coord"})");
            return true;
        }
        if (!numberJson(j, "max_ticks", -1e9, 1e9, 400.0, maxTicksVal)) {
            enqueue(R"({"event":"error","reason":"bad_param"})");
            return true;
        }
        MineTask task;
        task.x            = static_cast<int>(std::floor(mx));
        task.y            = static_cast<int>(std::floor(my));
        task.z            = static_cast<int>(std::floor(mz));
        task.maxTicks     = std::clamp(static_cast<int>(maxTicksVal), 1, 200000);
        auto const taskId = startTask(task);
        if (taskId == 0) {
            enqueue(R"({"event":"error","reason":"task_backpressure"})");
            return true;
        }
        enqueue(
            nlohmann::json{
                {"event",     "mine_started"},
                {"task_id",   taskId        },
                {"x",         task.x        },
                {"y",         task.y        },
                {"z",         task.z        },
                {"max_ticks", task.maxTicks },
        }
                .dump()
        );
        return true;
    }
    if (cmd == "place") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        double px = 0.0, py = 0.0, pz = 0.0, maxTicksVal = 30.0;
        if (!numberJson(j, "x", -CoordHorizonLimit, CoordHorizonLimit, 0.0, px)
            || !numberJson(j, "y", CoordHeightLow, CoordHeightHigh, 0.0, py)
            || !numberJson(j, "z", -CoordHorizonLimit, CoordHorizonLimit, 0.0, pz)) {
            enqueue(R"({"event":"error","reason":"bad_coord"})");
            return true;
        }
        if (!numberJson(j, "max_ticks", -1e9, 1e9, 30.0, maxTicksVal)) {
            enqueue(R"({"event":"error","reason":"bad_param"})");
            return true;
        }
        PlaceTask task;
        task.x              = static_cast<int>(std::floor(px));
        task.y              = static_cast<int>(std::floor(py));
        task.z              = static_cast<int>(std::floor(pz));
        task.maxTicks       = std::clamp(static_cast<int>(maxTicksVal), 2, 200);
        auto const expected = j.value("expected", std::string{});
        if (expected.size() >= task.expected.bytes.size()) {
            enqueue(R"({"event":"error","reason":"expected_too_long"})");
            return true;
        }
        task.expected.assign(expected);
        if (j.contains("slot")) {
            double slotVal = -1.0;
            if (!numberValue(j["slot"], -1.0, 8.0, slotVal) || std::trunc(slotVal) != slotVal) {
                enqueue(R"({"event":"error","reason":"bad_param","param":"slot"})");
                return true;
            }
            task.slot = static_cast<int>(slotVal);
        }
        if (j.contains("face")) {
            double faceVal = -1.0;
            if (!numberValue(j["face"], -1e9, 1e9, faceVal)) {
                enqueue(R"({"event":"error","reason":"bad_param","param":"face"})");
                return true;
            }
            task.face = std::clamp(static_cast<int>(faceVal), -1, 5);
        }
        if (j.contains("against") && j["against"].is_array() && j["against"].size() == 3) {
            double ax = 0.0, ay = 0.0, az = 0.0;
            if (!numberValue(j["against"][0], -CoordHorizonLimit, CoordHorizonLimit, ax)
                || !numberValue(j["against"][1], CoordHeightLow, CoordHeightHigh, ay)
                || !numberValue(j["against"][2], -CoordHorizonLimit, CoordHorizonLimit, az)) {
                enqueue(R"({"event":"error","reason":"bad_coord","param":"against"})");
                return true;
            }
            task.againstX    = static_cast<int>(std::floor(ax));
            task.againstY    = static_cast<int>(std::floor(ay));
            task.againstZ    = static_cast<int>(std::floor(az));
            task.autoAgainst = false;
            if (task.face < 0) {
                task.face = faceBetween(
                    BlockPos{task.againstX, task.againstY, task.againstZ},
                    BlockPos{task.x, task.y, task.z}
                );
            }
        }
        auto const taskId = startTask(task);
        if (taskId == 0) {
            enqueue(R"({"event":"error","reason":"task_backpressure"})");
            return true;
        }
        enqueue(
            nlohmann::json{
                {"event",     "place_started"},
                {"task_id",   taskId         },
                {"x",         task.x         },
                {"y",         task.y         },
                {"z",         task.z         },
                {"face",      task.face      },
                {"max_ticks", task.maxTicks  },
        }
                .dump()
        );
        return true;
    }
    if (cmd == "use") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        double ticksVal = 32.0;
        if (!numberJson(j, "ticks", -1e9, 1e9, 32.0, ticksVal)) {
            enqueue(R"({"event":"error","reason":"bad_param"})");
            return true;
        }
        UseTask task;
        task.ticks        = std::clamp(static_cast<int>(ticksVal), 1, 2000);
        auto const taskId = startTask(task);
        if (taskId == 0) {
            enqueue(R"({"event":"error","reason":"task_backpressure"})");
            return true;
        }
        enqueue(
            nlohmann::json{
                {"event",   "use_started"},
                {"task_id", taskId       },
                {"ticks",   task.ticks   }
        }.dump()
        );
        return true;
    }
    if (cmd == "task_status") {
        enqueue(taskStatusJson().dump());
        return true;
    }
    if (cmd == "task_stop") {
        cancelTasks("stopped");
        beginTask();
        enqueue(R"({"event":"task_stopped"})");
        return true;
    }
    return false;
}

} // namespace probe
