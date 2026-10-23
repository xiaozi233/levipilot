#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

bool ProbeMod::dispatchTasksCommand(std::string const& cmd, nlohmann::json& j) {
    if (cmd == "navigate") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        NavigateTask task;
        task.x         = j.value("x", 0.0f);
        task.z         = j.value("z", 0.0f);
        task.tolerance = std::max(0.05f, j.value("tolerance", 0.35f));
        task.sprint    = j.value("sprint", false);
        task.maxTicks  = std::clamp(j.value("max_ticks", 600), 1, 200000);
        std::string const jump = j.value("jump", std::string{"auto"});
        if (jump == "distance") {
            task.jumpMode = JumpMode::Distance;
        } else if (jump == "edge") {
            task.jumpMode = JumpMode::Edge;
        } else if (jump == "never") {
            task.jumpMode = JumpMode::Never;
        }
        task.jumpDistance  = std::clamp(j.value("jump_distance", 1.0f), 0.1f, 8.0f);
        task.edgeLookahead = std::clamp(j.value("edge_lookahead", 0.55f), 0.2f, 1.5f);
        {
            std::lock_guard lock(mTaskMutex);
            ++mTaskEpoch;
            mNavigateTask = task;
            mMineTask.reset();
            mPlaceTask.reset();
            mUseTask.reset();
            mJumpTask.reset();
            mRouteTask.reset();
        }
        beginTask();
        enqueue(
            nlohmann::json{
                {"event",     "navigate_started"},
                {"x",         task.x            },
                {"z",         task.z            },
                {"tolerance", task.tolerance    },
                {"max_ticks", task.maxTicks     },
                {"jump",      jump              },
                {"jump_distance", task.jumpDistance},
        }
                .dump()
        );
        return true;
    }
    if (cmd == "jump_when") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        JumpTask task;
        std::string const mode = j.value("mode", std::string{"now"});
        if (mode == "distance") {
            task.mode = JumpMode::Distance;
        } else if (mode == "edge") {
            task.mode = JumpMode::Edge;
        } else if (mode == "tick") {
            task.mode = JumpMode::Tick;
        } else {
            task.mode = JumpMode::Now;
        }
        task.x             = j.value("x", 0.0f);
        task.z             = j.value("z", 0.0f);
        task.distance      = std::clamp(j.value("distance", 0.6f), 0.05f, 8.0f);
        task.forward       = std::clamp(j.value("forward", 0.0f), -1.0f, 1.0f);
        task.sprint        = j.value("sprint", false);
        task.hasYaw        = j.contains("yaw");
        task.yaw           = j.value("yaw", 0.0f);
        task.afterTicks    = std::clamp(j.value("after_ticks", 1), 1, 2000);
        task.maxTicks      = std::clamp(j.value("max_ticks", 60), 1, 5000);
        task.edgeLookahead = std::clamp(j.value("edge_lookahead", 0.55f), 0.2f, 1.5f);
        {
            std::lock_guard lock(mTaskMutex);
            ++mTaskEpoch;
            mJumpTask = task;
            mNavigateTask.reset();
            mRouteTask.reset();
            mMineTask.reset();
            mPlaceTask.reset();
            mUseTask.reset();
        }
        beginTask();
        enqueue(
            nlohmann::json{
                {"event",       "jump_when_started"},
                {"mode",        mode              },
                {"x",           task.x            },
                {"z",           task.z            },
                {"distance",    task.distance     },
                {"forward",     task.forward      },
                {"sprint",      task.sprint       },
                {"after_ticks", task.afterTicks   },
                {"max_ticks",   task.maxTicks     },
        }
                .dump()
        );
        return true;
    }
    if (cmd == "route") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        if (!j.contains("waypoints") || !j["waypoints"].is_array() || j["waypoints"].empty()) {
            enqueue(R"({"event":"error","reason":"missing_waypoints"})");
            return true;
        }
        RouteTask task;
        task.sprint = j.value("sprint", false);
        for (auto const& row : j["waypoints"]) {
            if (!row.is_object()) {
                continue;
            }
            RouteWaypoint point;
            point.x         = row.value("x", 0.0f);
            point.z         = row.value("z", 0.0f);
            point.tolerance = std::clamp(row.value("tolerance", 0.35f), 0.05f, 2.0f);
            point.maxTicks  = std::clamp(row.value("max_ticks", 600), 1, 200000);
            std::string const jump = row.value("jump", std::string{"auto"});
            if (jump == "distance") {
                point.jumpMode = JumpMode::Distance;
            } else if (jump == "edge") {
                point.jumpMode = JumpMode::Edge;
            } else if (jump == "never") {
                point.jumpMode = JumpMode::Never;
            }
            point.jumpDistance = std::clamp(row.value("jump_distance", 1.0f), 0.1f, 8.0f);
            task.points[task.count++] = point;
            if (task.count >= RouteTask::MaxWaypoints) {
                break;
            }
        }
        if (task.count == 0) {
            enqueue(R"({"event":"error","reason":"missing_waypoints"})");
            return true;
        }
        {
            std::lock_guard lock(mTaskMutex);
            ++mTaskEpoch;
            mRouteTask = task;
            mNavigateTask.reset();
            mJumpTask.reset();
            mMineTask.reset();
            mPlaceTask.reset();
            mUseTask.reset();
        }
        beginTask();
        enqueue(
            nlohmann::json{
                {"event",     "route_started"    },
                {"waypoints", task.count         },
                {"sprint",    task.sprint        },
        }
                .dump()
        );
        return true;
    }
    if (cmd == "mine") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        MineTask task;
        task.x        = j.value("x", 0);
        task.y        = j.value("y", 0);
        task.z        = j.value("z", 0);
        task.maxTicks = std::clamp(j.value("max_ticks", 400), 1, 200000);
        {
            std::lock_guard lock(mTaskMutex);
            ++mTaskEpoch;
            mMineTask = task;
            mNavigateTask.reset();
            mPlaceTask.reset();
            mUseTask.reset();
            mJumpTask.reset();
            mRouteTask.reset();
        }
        beginTask();
        enqueue(
            nlohmann::json{
                {"event",     "mine_started"},
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
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        PlaceTask task;
        task.x        = j.value("x", 0);
        task.y        = j.value("y", 0);
        task.z        = j.value("z", 0);
        task.maxTicks = std::clamp(j.value("max_ticks", 30), 2, 200);
        task.expected = j.value("expected", std::string{});
        task.slot     = j.value("slot", -1);
        if (task.slot < -1 || task.slot > 8) {
            task.slot = -1;
        }
        if (j.contains("face")) {
            task.face = std::clamp(j.value("face", -1), -1, 5);
        }
        if (j.contains("against") && j["against"].is_array() && j["against"].size() == 3) {
            task.againstX    = static_cast<int>(std::floor(j["against"][0].get<double>()));
            task.againstY    = static_cast<int>(std::floor(j["against"][1].get<double>()));
            task.againstZ    = static_cast<int>(std::floor(j["against"][2].get<double>()));
            task.autoAgainst = false;
            if (task.face < 0) {
                task.face = faceBetween(
                    BlockPos{task.againstX, task.againstY, task.againstZ},
                    BlockPos{task.x, task.y, task.z}
                );
            }
        }
        {
            std::lock_guard lock(mTaskMutex);
            ++mTaskEpoch;
            mPlaceTask = task;
            mNavigateTask.reset();
            mMineTask.reset();
            mUseTask.reset();
        }
        beginTask();
        enqueue(
            nlohmann::json{
                {"event",     "place_started"},
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
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        UseTask task;
        task.ticks = std::clamp(j.value("ticks", 32), 1, 2000);
        {
            std::lock_guard lock(mTaskMutex);
            ++mTaskEpoch;
            mUseTask = task;
            mNavigateTask.reset();
            mMineTask.reset();
            mPlaceTask.reset();
        }
        beginTask();
        enqueue(
            nlohmann::json{
                {"event", "use_started"},
                {"ticks", task.ticks   }
        }.dump()
        );
        return true;
    }
    if (cmd == "task_status") {
        enqueue(taskStatusJson().dump());
        return true;
    }
    if (cmd == "task_stop") {
        cancelTasks();
        beginTask();
        enqueue(R"({"event":"task_stopped"})");
        return true;
    }
    return false;
}

} // namespace probe
