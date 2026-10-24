#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

namespace {
TaskOutcome useOnServerImpl(std::string const& name) {
    TaskOutcome result;
    result.reason    = "player_not_found";
    auto   minecraft = ll::service::bedrock::getMinecraft();
    Level* level     = minecraft ? minecraft->getLevel() : nullptr;
    auto*  player    = level && !name.empty() ? level->getPlayer(name) : nullptr;
    if (!player) return result;
    auto& inventory = player->getInventory();
    auto  stack     = inventory.getItemNonConst(player->getSelectedItemSlot());
    result.reason   = "empty_hand";
    if (!stack || stack->isNull()) return result;
    Item* item    = stack->mItem.get();
    result.reason = "no_item";
    if (!item) return result;
    result.block.assign(stack->getTypeName());
    item->use(*stack, *player, HandSlot::Mainhand);
    if (item->isFood()) {
        auto const method = item->useTimeDepleted(*stack, level, player);
        player->useItem(*stack, method, true);
    }
    player->sendInventory(true);
    result.ok     = true;
    result.reason = "server";
    result.count  = static_cast<int>(stack->mCount);
    return result;
}

TaskOutcome useOnServer(std::string const& name) {
    TaskOutcome result;
    __try {
        result = useOnServerImpl(name);
    } __except (
        GetExceptionCode() == EXCEPTION_ACCESS_VIOLATION ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH
    ) {
        result.reason = "server_task_fault";
    }
    return result;
}
} // namespace


nlohmann::json ProbeMod::taskStatusJson() {
    auto const     snapshot = mTasks.snapshot();
    nlohmann::json out      = {
        {"event",   "task_status"},
        {"task_id", snapshot.id  },
        {"active",  nullptr      }
    };
    std::visit(
        [&](auto const& task) {
            using T = std::decay_t<decltype(task)>;
            if constexpr (!std::is_same_v<T, std::monostate>) {
                out["active"]  = taskName(snapshot.state);
                out["elapsed"] = task.elapsed;
                if constexpr (std::is_same_v<T, UseTask>) {
                    out["ticks"] = task.ticks;
                    out["phase"] = task.dispatched ? "server_pending" : "waiting";
                } else if constexpr (std::is_same_v<T, RouteTask>) {
                    out["waypoint"]  = task.index;
                    out["waypoints"] = task.count;
                    if (task.index < static_cast<int>(task.count)) {
                        auto const& point = task.points[static_cast<std::size_t>(task.index)];
                        out["target"]     = {point.x, point.z};
                    }
                } else {
                    out["max_ticks"] = task.maxTicks;
                    if constexpr (std::is_same_v<T, NavigateTask> || std::is_same_v<T, JumpTask>) {
                        out["target"] = {task.x, task.z};
                        if constexpr (std::is_same_v<T, NavigateTask>)
                            out["jump_mode"] = static_cast<int>(task.jumpMode);
                        else out["jump_mode"] = static_cast<int>(task.mode);
                    } else {
                        out["target"] = {task.x, task.y, task.z};
                        if constexpr (std::is_same_v<T, MineTask>) out["block"] = task.block.c_str();
                        else out["phase"] = task.phase;
                    }
                }
            }
        },
        snapshot.state
    );
    return out;
}

void ProbeMod::drainTaskEvents() {
    TaskEvent event;
    // Bound each pass so sustained command traffic cannot starve spool flushes.
    for (std::size_t n = 0; n < TaskController::EventCapacity && mTasks.pop(event); ++n) {
        if (event.kind == TaskEvent::Kind::UseReady) {
            dispatchUseTask(event.id);
            continue;
        }
        auto const&    result = event.outcome;
        nlohmann::json out    = {
            {"task_id", event.id}
        };
        if (event.kind == TaskEvent::Kind::Cancelled) {
            out.update({
                {"event",  "task_cancelled"     },
                {"task",   taskName(event.state)},
                {"ok",     false                },
                {"reason", result.reason        }
            });
        } else if (event.kind == TaskEvent::Kind::Waypoint) {
            // The spool thread must not die on a mismatched variant; guard
            // explicitly instead of trusting the producer.
            if (!std::holds_alternative<RouteTask>(event.state)) {
                out.update({
                    {"event",  "error"                    },
                    {"reason", "waypoint_variant_mismatch"}
                });
                enqueue(out.dump());
                continue;
            }
            auto const& task = std::get<RouteTask>(event.state);
            out.update({
                {"event",     "route_waypoint"    },
                {"index",     result.waypoint     },
                {"x",         result.x            },
                {"z",         result.z            },
                {"ticks",     result.waypointTicks},
                {"waypoints", task.count          }
            });
        } else {
            out.update({
                {"ok",     result.ok    },
                {"reason", result.reason}
            });
            std::visit(
                [&](auto const& task) {
                    using T = std::decay_t<decltype(task)>;
                    if constexpr (!std::is_same_v<T, std::monostate>) {
                        out["ticks"] = task.elapsed;
                        if constexpr (std::is_same_v<T, NavigateTask>) {
                            out.update({
                                {"event",     "navigated"                },
                                {"target",    {task.x, task.z}           },
                                {"x",         result.x                   },
                                {"z",         result.z                   },
                                {"distance",  result.distance            },
                                {"jumps",     task.jumps                 },
                                {"jump_mode", jumpModeName(task.jumpMode)}
                            });
                        } else if constexpr (std::is_same_v<T, JumpTask>) {
                            out.update({
                                {"event",     "jumped"               },
                                {"mode",      jumpModeName(task.mode)},
                                {"x",         result.x               },
                                {"z",         result.z               },
                                {"distance",  result.distance        },
                                {"on_ground", result.onGround        }
                            });
                        } else if constexpr (std::is_same_v<T, RouteTask>) {
                            out["event"] = result.ok ? "route_done" : "route_failed";
                            if (result.ok) out["waypoints"] = task.count;
                            else {
                                auto const& point = task.points[static_cast<std::size_t>(task.index)];
                                out.update({
                                    {"index",    task.index        },
                                    {"target",   {point.x, point.z}},
                                    {"x",        result.x          },
                                    {"z",        result.z          },
                                    {"distance", result.distance   }
                                });
                            }
                        } else if constexpr (std::is_same_v<T, MineTask> || std::is_same_v<T, PlaceTask>) {
                            out.update({
                                {"event", std::is_same_v<T, MineTask> ? "block_mined" : "block_placed"},
                                {"x", task.x},
                                {"y", task.y},
                                {"z", task.z},
                                {"block", result.block.c_str()}
                            });
                        } else if constexpr (std::is_same_v<T, UseTask>) {
                            out["event"] = "item_used";
                            if (result.ok)
                                out.update({
                                    {"item",  result.block.c_str()},
                                    {"count", result.count        }
                                });
                        }
                    }
                },
                event.state
            );
        }
        enqueue(out.dump());
    }
}

void ProbeMod::dispatchUseTask(std::uint64_t id) {
    // Scheduling/formatting happens on the event consumer, never in tickUse.
    std::string name;
    {
        std::lock_guard lock(mPlayerNameMutex);
        name = mLocalPlayerName;
    }
    if (!ll::service::bedrock::getServerInstance()) {
        mTasks.completeUse(id, [] {
            TaskOutcome result;
            result.reason = "no_server_instance";
            return result;
        });
        return;
    }
    queueServerTask([this, id, name] { mTasks.completeUse(id, [&] { return useOnServer(name); }); });
}

} // namespace probe
