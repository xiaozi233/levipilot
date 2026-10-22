#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

namespace {

bool isAirName(std::string const& name) {
    return name == "minecraft:air" || name == "minecraft:cave_air" || name == "minecraft:void_air";
}

// True when the block ahead at foot level is open and the block below that
// position is missing. This is the "about to run off a ledge" condition.
bool atEdge(LocalPlayer& player, float lookAhead) {
    auto const  feet = player.getFeetPos();
    auto const  rot  = player.getRotation();
    float const rad  = rot.y * 0.01745329252f;
    float const fx   = -std::sin(rad);
    float const fz   = std::cos(rad);
    int const   x    = static_cast<int>(std::floor(feet.x + fx * lookAhead));
    int const   z    = static_cast<int>(std::floor(feet.z + fz * lookAhead));
    int const   y    = static_cast<int>(std::floor(feet.y));
    auto const& source = player.getDimensionBlockSourceConst();
    bool const  support = !isAirName(blockName(source, x, y - 1, z));
    bool const  passable =
        isAirName(blockName(source, x, y, z)) && isAirName(blockName(source, x, y + 1, z));
    return !support && passable;
}

float horizontalDistance(Vec3 const& pos, float x, float z) {
    float const dx = x - static_cast<float>(pos.x);
    float const dz = z - static_cast<float>(pos.z);
    return std::sqrt(dx * dx + dz * dz);
}

char const* jumpModeName(JumpMode mode) {
    switch (mode) {
    case JumpMode::Distance:
        return "distance";
    case JumpMode::Edge:
        return "edge";
    case JumpMode::Never:
        return "never";
    case JumpMode::Now:
        return "now";
    case JumpMode::Tick:
        return "tick";
    case JumpMode::Auto:
    default:
        return "auto";
    }
}

} // namespace

void ProbeMod::aimAt(LocalPlayer& player, Vec3 const& point) {
    Vec3 const   eye   = player.getHeadPos();
    double const dx    = point.x - eye.x;
    double const dy    = point.y - eye.y;
    double const dz    = point.z - eye.z;
    double const horiz = std::sqrt(dx * dx + dz * dz);
    if (horiz < 1.0e-6 && std::fabs(dy) < 1.0e-6) {
        return;
    }
    float const yaw   = static_cast<float>(std::atan2(-dx, dz) * 57.29577951308232);
    float const pitch = static_cast<float>(-std::atan2(dy, horiz) * 57.29577951308232);
    auto const& rot   = player.getRotation();
    applyTurnDelta(player, Vec2{rot.x - pitch, wrapDegrees(yaw - rot.y)});
}

void ProbeMod::cancelTasks() {
    std::lock_guard lock(mTaskMutex);
    ++mTaskEpoch;
    mNavigateTask.reset();
    mJumpTask.reset();
    mRouteTask.reset();
    mMineTask.reset();
    mPlaceTask.reset();
    mUseTask.reset();
}

void ProbeMod::beginTask() {
    {
        std::lock_guard lock(mInputMutex);
        mDesired       = ControlState{};
        mDesiredTicks  = 0;
        mJumpRequested = false;
    }
    {
        std::lock_guard lock(mLookMutex);
        mLookTicks = 0;
    }
    // A task takes over movement; a running macro would fight it for the input.
    mMacroPlaying       = false;
    mMacroStopRequested = true;
}

nlohmann::json ProbeMod::taskStatusJson() {
    nlohmann::json  out;
    std::lock_guard lock(mTaskMutex);
    out["event"] = "task_status";
    if (mNavigateTask) {
        out["active"]    = "navigate";
        out["elapsed"]   = mNavigateTask->elapsed;
        out["max_ticks"] = mNavigateTask->maxTicks;
        out["target"]    = {mNavigateTask->x, mNavigateTask->z};
        out["jump_mode"] = static_cast<int>(mNavigateTask->jumpMode);
    } else if (mJumpTask) {
        out["active"]     = "jump_when";
        out["elapsed"]    = mJumpTask->elapsed;
        out["max_ticks"]  = mJumpTask->maxTicks;
        out["target"]     = {mJumpTask->x, mJumpTask->z};
        out["jump_mode"]  = static_cast<int>(mJumpTask->mode);
    } else if (mRouteTask) {
        out["active"]    = "route";
        out["elapsed"]   = mRouteTask->elapsed;
        out["waypoint"]  = mRouteTask->index;
        out["waypoints"] = mRouteTask->count;
        if (mRouteTask->index < static_cast<int>(mRouteTask->count)) {
            auto const& point = mRouteTask->points[static_cast<std::size_t>(mRouteTask->index)];
            out["target"]     = {point.x, point.z};
        }
    } else if (mMineTask) {
        out["active"]    = "mine";
        out["elapsed"]   = mMineTask->elapsed;
        out["max_ticks"] = mMineTask->maxTicks;
        out["target"]    = {mMineTask->x, mMineTask->y, mMineTask->z};
        out["block"]     = mMineTask->block;
    } else if (mPlaceTask) {
        out["active"]    = "place";
        out["elapsed"]   = mPlaceTask->elapsed;
        out["max_ticks"] = mPlaceTask->maxTicks;
        out["target"]    = {mPlaceTask->x, mPlaceTask->y, mPlaceTask->z};
        out["phase"]     = mPlaceTask->phase;
    } else if (mUseTask) {
        out["active"]  = "use";
        out["elapsed"] = mUseTask->elapsed;
        out["ticks"]   = mUseTask->ticks;
    } else {
        out["active"] = nullptr;
    }
    return out;
}

bool ProbeMod::tickTask(ClientInstance& client, LocalPlayer& player) {
    std::optional<NavigateTask> navigate;
    std::optional<JumpTask>     jump;
    std::optional<RouteTask>    route;
    std::optional<MineTask>     mine;
    std::optional<PlaceTask>    place;
    std::optional<UseTask>      use;
    std::uint64_t               epoch = 0;
    {
        std::lock_guard lock(mTaskMutex);
        epoch    = mTaskEpoch;
        navigate = mNavigateTask;
        jump     = mJumpTask;
        route    = mRouteTask;
        mine     = mMineTask;
        place    = mPlaceTask;
        use      = mUseTask;
    }
    if (!navigate && !jump && !route && !mine && !place && !use) {
        return false;
    }

    nlohmann::json result;
    bool           finished = false;
    if (navigate) {
        finished = tickNavigate(client, player, *navigate, result);
    } else if (jump) {
        finished = tickJump(client, player, *jump, result);
    } else if (route) {
        finished = tickRoute(client, player, *route, result);
    } else if (mine) {
        finished = tickMine(player, *mine, result);
    } else if (place) {
        finished = tickPlace(player, *place, result);
    } else if (use) {
        finished = tickUse(player, *use, result);
    }

    {
        std::lock_guard lock(mTaskMutex);
        // A command may have replaced or cancelled the task while it ran.
        if (mTaskEpoch == epoch) {
            if (navigate) {
                if (finished) {
                    mNavigateTask.reset();
                } else {
                    mNavigateTask = navigate;
                }
            }
            if (jump) {
                if (finished) {
                    mJumpTask.reset();
                } else {
                    mJumpTask = jump;
                }
            }
            if (route) {
                if (finished) {
                    mRouteTask.reset();
                } else {
                    mRouteTask = route;
                }
            }
            if (mine) {
                if (finished) {
                    mMineTask.reset();
                } else {
                    mMineTask = mine;
                }
            }
            if (place) {
                if (finished) {
                    mPlaceTask.reset();
                } else {
                    mPlaceTask = place;
                }
            }
            if (use) {
                if (finished) {
                    mUseTask.reset();
                } else {
                    mUseTask = use;
                }
            }
        }
    }

    if (finished) {
        // A one-shot jump must keep its injected press for the rest of this
        // tick; clearing it here cancels the jump before origin() reads it.
        // The idle applyControls on the next tick releases it.
        bool const keepJumpPress =
            !result.is_null() && result.value("event", std::string{}) == "jumped"
            && result.value("ok", false);
        if (!keepJumpPress) {
            applyControls(client, player, ControlState{});
        }
        if (!result.is_null() && !result.empty()) {
            enqueue(result.dump());
        }
    }
    return true;
}

bool ProbeMod::tickNavigate(ClientInstance& client, LocalPlayer& player, NavigateTask& task, nlohmann::json& result) {
    Vec3 const  pos      = player.getFeetPos();
    float const dx       = task.x - static_cast<float>(pos.x);
    float const dz       = task.z - static_cast<float>(pos.z);
    float const distance = std::sqrt(dx * dx + dz * dz);
    ++task.elapsed;

    auto finish = [&](char const* reason, bool ok) {
        result = {
            {"event",    "navigated"                            },
            {"ok",       ok                                     },
            {"reason",   reason                                 },
            {"target",   {task.x, task.z}                       },
            {"x",        finiteOrZero(static_cast<float>(pos.x))},
            {"z",        finiteOrZero(static_cast<float>(pos.z))},
            {"distance", finiteOrZero(distance)                 },
            {"ticks",    task.elapsed                           },
            {"jumps",    task.jumps                             },
            {"jump_mode", jumpModeName(task.jumpMode)           },
        };
        return true;
    };

    if (distance <= task.tolerance) {
        return finish("arrived", true);
    }
    if (task.elapsed >= task.maxTicks) {
        return finish("timeout", false);
    }

    float const yaw   = static_cast<float>(std::atan2(-dx, dz) * 57.29577951308232);
    auto const& rot   = player.getRotation();
    float const delta = std::clamp(wrapDegrees(yaw - rot.y), -35.0f, 35.0f);
    if (std::fabs(delta) > 0.01f) {
        applyTurnDelta(player, Vec2{0.0f, delta});
    }

    if (task.haveLast) {
        float const mx    = static_cast<float>(pos.x) - task.lastX;
        float const mz    = static_cast<float>(pos.z) - task.lastZ;
        bool const  moved = std::sqrt(mx * mx + mz * mz) >= 0.02f;
        if (!moved && player.isOnGround()) {
            ++task.stuckTicks;
        } else {
            task.stuckTicks = 0;
        }
    }
    task.lastX    = static_cast<float>(pos.x);
    task.lastZ    = static_cast<float>(pos.z);
    task.haveLast = true;

    bool wantJump = false;
    if (task.jumpCooldown > 0) {
        --task.jumpCooldown;
    }
    if (player.isOnGround() && task.jumpCooldown == 0) {
        if (task.jumpMode == JumpMode::Distance) {
            // The target is on the far side of the gap: take off just before
            // the edge instead of when the gap is already underfoot.
            wantJump = distance <= task.jumpDistance;
        } else if (task.jumpMode == JumpMode::Edge) {
            wantJump = atEdge(player, task.edgeLookahead);
        } else if (task.jumpMode == JumpMode::Auto && task.stuckTicks >= 5) {
            wantJump      = true;
            task.stuckTicks = 0;
        }
    }
    if (wantJump) {
        task.jumpCooldown = 5;
        ++task.jumps;
    }

    ControlState state;
    state.forward = 1.0f;
    state.sprint  = task.sprint;
    state.active  = true;
    state.jump    = wantJump;
    applyControls(client, player, state);
    return false;
}

bool ProbeMod::tickJump(
    ClientInstance& client,
    LocalPlayer&    player,
    JumpTask&       task,
    nlohmann::json& result
) {
    Vec3 const  pos      = player.getFeetPos();
    auto const  rot      = player.getRotation();
    float const distance = horizontalDistance(pos, task.x, task.z);
    ++task.elapsed;

    auto finish = [&](bool ok, char const* reason) {
        result = {
            {"event",     "jumped"                              },
            {"ok",        ok                                    },
            {"reason",    reason                                },
            {"mode",      jumpModeName(task.mode)               },
            {"x",         finiteOrZero(static_cast<float>(pos.x))},
            {"z",         finiteOrZero(static_cast<float>(pos.z))},
            {"distance",  finiteOrZero(distance)                },
            {"ticks",     task.elapsed                          },
            {"on_ground", player.isOnGround()                   },
        };
        return true;
    };

    bool trigger = false;
    switch (task.mode) {
    case JumpMode::Now:
        trigger = true;
        break;
    case JumpMode::Tick:
        trigger = task.elapsed >= task.afterTicks;
        break;
    case JumpMode::Distance:
        trigger = player.isOnGround() && distance <= task.distance;
        break;
    case JumpMode::Edge:
        trigger = player.isOnGround() && atEdge(player, task.edgeLookahead);
        break;
    default:
        break;
    }

    if (!trigger && task.elapsed >= task.maxTicks) {
        return finish(false, "timeout");
    }

    // Keep facing the target while waiting so the jump goes where the caller
    // expects; an explicit yaw wins over distance-derived aiming.
    if (task.hasYaw) {
        float const delta = std::clamp(wrapDegrees(task.yaw - rot.y), -35.0f, 35.0f);
        if (std::fabs(delta) > 0.01f) {
            applyTurnDelta(player, Vec2{0.0f, delta});
        }
    } else if (task.mode == JumpMode::Distance && (task.forward != 0.0f || task.x != 0.0f || task.z != 0.0f)) {
        float const dx = task.x - static_cast<float>(pos.x);
        float const dz = task.z - static_cast<float>(pos.z);
        if (std::fabs(dx) + std::fabs(dz) > 1.0e-4f) {
            float const yaw   = static_cast<float>(std::atan2(-dx, dz) * 57.29577951308232);
            float const delta = std::clamp(wrapDegrees(yaw - rot.y), -35.0f, 35.0f);
            if (std::fabs(delta) > 0.01f) {
                applyTurnDelta(player, Vec2{0.0f, delta});
            }
        }
    }

    ControlState state;
    state.forward = task.forward;
    state.sprint  = task.sprint;
    state.active  = task.forward != 0.0f || task.sprint || trigger;
    state.jump    = trigger;
    applyControls(client, player, state);
    if (trigger) {
        return finish(true, "triggered");
    }
    return false;
}

bool ProbeMod::tickRoute(
    ClientInstance& client,
    LocalPlayer&    player,
    RouteTask&      task,
    nlohmann::json& result
) {
    ++task.elapsed;
    if (task.index >= static_cast<int>(task.count)) {
        result = {
            {"event",     "route_done"           },
            {"ok",        true                   },
            {"waypoints", task.count     },
            {"ticks",     task.elapsed           },
        };
        return true;
    }

    RouteWaypoint& point = task.points[static_cast<std::size_t>(task.index)];
    ++task.segmentElapsed;
    Vec3 const  pos      = player.getFeetPos();
    auto const  rot      = player.getRotation();
    float const distance = horizontalDistance(pos, point.x, point.z);

    if (distance <= point.tolerance) {
        enqueue(
            nlohmann::json{
                {"event",    "route_waypoint"                           },
                {"index",    task.index                                 },
                {"x",        finiteOrZero(static_cast<float>(pos.x))     },
                {"z",        finiteOrZero(static_cast<float>(pos.z))     },
                {"ticks",    task.segmentElapsed                        },
                {"waypoints", task.count                        },
        }
                .dump()
        );
        ++task.index;
        task.segmentElapsed = 0;
        task.stuckTicks     = 0;
        task.haveLast       = false;
        if (task.index >= static_cast<int>(task.count)) {
            result = {
                {"event",     "route_done"       },
                {"ok",        true               },
                {"waypoints", task.count },
                {"ticks",     task.elapsed       },
            };
            return true;
        }
        return false;
    }

    if (task.segmentElapsed >= point.maxTicks) {
        result = {
            {"event",    "route_failed"                             },
            {"ok",       false                                      },
            {"reason",   "timeout"                                  },
            {"index",    task.index                                 },
            {"target",   {point.x, point.z}                         },
            {"x",        finiteOrZero(static_cast<float>(pos.x))     },
            {"z",        finiteOrZero(static_cast<float>(pos.z))     },
            {"distance", finiteOrZero(distance)                     },
            {"ticks",    task.elapsed                               },
        };
        return true;
    }

    float const dx    = point.x - static_cast<float>(pos.x);
    float const dz    = point.z - static_cast<float>(pos.z);
    float const yaw   = static_cast<float>(std::atan2(-dx, dz) * 57.29577951308232);
    float const delta = std::clamp(wrapDegrees(yaw - rot.y), -35.0f, 35.0f);
    if (std::fabs(delta) > 0.01f) {
        applyTurnDelta(player, Vec2{0.0f, delta});
    }

    if (task.haveLast) {
        float const mx    = static_cast<float>(pos.x) - task.lastX;
        float const mz    = static_cast<float>(pos.z) - task.lastZ;
        bool const  moved = std::sqrt(mx * mx + mz * mz) >= 0.02f;
        if (!moved && player.isOnGround()) {
            ++task.stuckTicks;
        } else {
            task.stuckTicks = 0;
        }
    }
    task.lastX    = static_cast<float>(pos.x);
    task.lastZ    = static_cast<float>(pos.z);
    task.haveLast = true;

    bool wantJump = false;
    if (task.jumpCooldown > 0) {
        --task.jumpCooldown;
    }
    if (player.isOnGround() && task.jumpCooldown == 0) {
        if (point.jumpMode == JumpMode::Distance) {
            wantJump = distance <= point.jumpDistance;
        } else if (point.jumpMode == JumpMode::Edge) {
            wantJump = atEdge(player, 0.55f);
        } else if (point.jumpMode == JumpMode::Auto && task.stuckTicks >= 5) {
            wantJump        = true;
            task.stuckTicks = 0;
        }
    }
    if (wantJump) {
        task.jumpCooldown = 5;
    }

    ControlState state;
    state.forward = 1.0f;
    state.sprint  = task.sprint;
    state.active  = true;
    state.jump    = wantJump;
    applyControls(client, player, state);
    return false;
}

bool ProbeMod::tickMine(LocalPlayer& player, MineTask& task, nlohmann::json& result) {
    auto const&    source = player.getDimensionBlockSourceConst();
    BlockPos const pos{task.x, task.y, task.z};
    ++task.elapsed;

    auto finish = [&](char const* reason, bool ok, std::string const& name) {
        result = {
            {"event",  "block_mined"},
            {"ok",     ok           },
            {"reason", reason       },
            {"x",      task.x       },
            {"y",      task.y       },
            {"z",      task.z       },
            {"block",  name         },
            {"ticks",  task.elapsed },
        };
        return true;
    };

    if (!player.mGameMode) {
        return finish("no_gamemode", false, "");
    }

    std::string const name = source.getBlock(pos).getTypeName();
    if (name == "minecraft:air") {
        return finish(task.started ? "mined" : "already_air", true, task.block);
    }
    if (task.block.empty()) {
        task.block = name;
    }

    Vec3 const   center = blockCenter(pos);
    Vec3 const   eye    = player.getHeadPos();
    double const dx     = center.x - eye.x;
    double const dy     = center.y - eye.y;
    double const dz     = center.z - eye.z;
    double const dist   = std::sqrt(dx * dx + dy * dy + dz * dz);
    if (dist > 6.0) {
        return finish("out_of_reach", false, name);
    }
    if (task.elapsed >= task.maxTicks) {
        return finish("timeout", false, name);
    }

    aimAt(player, center);

    if (!task.started) {
        int       face = -1;
        HitResult hit  = player.traceRay(6.0f, true, true);
        if (hit.mType == HitResultType::Tile && hit.mBlock.x == pos.x && hit.mBlock.y == pos.y
            && hit.mBlock.z == pos.z) {
            face = static_cast<int>(hit.mFacing);
        }
        if (face < 0) {
            // The ray missed (for example the player is inside the block):
            // fall back to the axis that dominates the block-to-eye vector.
            double const ax = std::fabs(dx);
            double const ay = std::fabs(dy);
            double const az = std::fabs(dz);
            if (ay >= ax && ay >= az) {
                face = dy > 0 ? 0 : 1;
            } else if (ax >= az) {
                face = dx > 0 ? 4 : 5;
            } else {
                face = dz > 0 ? 2 : 3;
            }
        }

        bool destroyed = false;
        bool ok        = player.mGameMode->startDestroyBlock(pos, static_cast<uchar>(face), destroyed);
        task.started   = true;
        task.face      = static_cast<std::uint8_t>(face);
        if (destroyed || source.getBlock(pos).getTypeName() == "minecraft:air") {
            return finish("mined", true, name);
        }
        if (!ok) {
            return finish("cannot_destroy", false, name);
        }
        return false;
    }

    bool destroyed = false;
    bool ok =
        player.mGameMode->continueDestroyBlock(pos, static_cast<uchar>(task.face), player.getFeetPos(), destroyed);
    if (destroyed || source.getBlock(pos).getTypeName() == "minecraft:air") {
        return finish("mined", true, name);
    }
    if (!ok) {
        ++task.misses;
        // Some blocks only report progress every few ticks; give up only after
        // a sustained run of failures.
        if (task.misses >= 40) {
            return finish("cannot_continue", false, name);
        }
    } else {
        task.misses = 0;
    }
    return false;
}

bool ProbeMod::tickPlace(LocalPlayer& player, PlaceTask& task, nlohmann::json& result) {
    auto const&    source = player.getDimensionBlockSourceConst();
    BlockPos const target{task.x, task.y, task.z};
    ++task.elapsed;

    auto finish = [&](char const* reason, bool ok, std::string const& name) {
        result = {
            {"event",  "block_placed"},
            {"ok",     ok            },
            {"reason", reason        },
            {"x",      task.x        },
            {"y",      task.y        },
            {"z",      task.z        },
            {"block",  name          },
            {"ticks",  task.elapsed  },
        };
        return true;
    };

    if (!player.mGameMode) {
        return finish("no_gamemode", false, "");
    }

    if (task.phase == 0) {
        if (source.getBlock(target).getTypeName() != "minecraft:air") {
            return finish("occupied", false, source.getBlock(target).getTypeName());
        }

        // Resolve the clicked block and the face.
        if (task.face >= 0) {
            FaceOffset const off = faceOffset(task.face);
            task.againstX        = task.x - off.x;
            task.againstY        = task.y - off.y;
            task.againstZ        = task.z - off.z;
            task.autoAgainst     = false;
        }

        int face = task.face;
        if (task.autoAgainst) {
            // Prefer standing on the block below the target, then the sides,
            // then the block above. Only visible faces can be clicked.
            static int const order[] = {1, 3, 2, 5, 4, 0};
            Vec3 const       eye     = player.getHeadPos();
            bool             found   = false;
            for (int candidateFace : order) {
                FaceOffset const off = faceOffset(candidateFace);
                BlockPos const   cand{task.x - off.x, task.y - off.y, task.z - off.z};
                if (source.getBlock(cand).getTypeName() == "minecraft:air") {
                    continue;
                }
                double const vx = eye.x - (cand.x + 0.5);
                double const vy = eye.y - (cand.y + 0.5);
                double const vz = eye.z - (cand.z + 0.5);
                if (off.x * vx + off.y * vy + off.z * vz <= 0.0) {
                    continue;
                }
                task.againstX = cand.x;
                task.againstY = cand.y;
                task.againstZ = cand.z;
                face          = candidateFace;
                found         = true;
                break;
            }
            if (!found) {
                return finish("no_support", false, "");
            }
            task.autoAgainst = false;
        }

        BlockPos const against{task.againstX, task.againstY, task.againstZ};
        if (face < 0) {
            face = faceBetween(against, target);
        }
        if (face < 0) {
            return finish("bad_face", false, "");
        }

        FaceOffset const off = faceOffset(face);
        Vec3 const       eye = player.getHeadPos();
        double const     nx  = off.x;
        double const     ny  = off.y;
        double const     nz  = off.z;
        double const     vx  = eye.x - (against.x + 0.5);
        double const     vy  = eye.y - (against.y + 0.5);
        double const     vz  = eye.z - (against.z + 0.5);
        if (nx * vx + ny * vy + nz * vz <= 0.0) {
            return finish("face_not_visible", false, "");
        }

        Vec3 const clickPoint{
            against.x + 0.5f + static_cast<float>(off.x) * 0.5f,
            against.y + 0.5f + static_cast<float>(off.y) * 0.5f,
            against.z + 0.5f + static_cast<float>(off.z) * 0.5f,
        };
        aimAt(player, clickPoint);
        if (task.slot >= 0) {
            // Re-applied in afterPlayerTick, where the hotbar write sticks.
            mSlotRequest.store(task.slot);
        }
        task.face  = face;
        task.phase = 1;
        return false;
    }

    if (task.phase == 1) {
        if (task.elapsed >= task.maxTicks) {
            return finish("timeout", false, source.getBlock(target).getTypeName());
        }
        std::string const before = source.getBlock(target).getTypeName();
        if (before != "minecraft:air") {
            bool const matches = task.expected.empty() || before.find(task.expected) != std::string::npos;
            return finish(matches ? "placed" : "occupied", matches, before);
        }

        HitResult      hit = player.traceRay(6.0f, true, true);
        BlockPos const against{task.againstX, task.againstY, task.againstZ};
        bool const     hitTarget = hit.mType == HitResultType::Tile && hit.mBlock.x == against.x
                                && hit.mBlock.y == against.y && hit.mBlock.z == against.z;
        if (!hitTarget) {
            return finish("not_targeted", false, before);
        }
        if (task.face >= 0 && static_cast<int>(hit.mFacing) != task.face) {
            return finish("wrong_face", false, before);
        }

        auto& item = const_cast<ItemStack&>(player.getSelectedItem());
        if (item.isNull()) {
            return finish("empty_hand", false, before);
        }
        auto const&       clicked = source.getBlock(hit.mBlock);
        InteractionResult used =
            player.mGameMode->useItemOn(item, hit.mBlock, hit.mFacing, hit.mPos, HandSlot::Mainhand, &clicked, true);
        if (!used.mSuccess) {
            return finish("use_failed", false, before);
        }
        task.phase    = 2;
        task.verifyAt = task.elapsed + 2;
        return false;
    }

    // Verification phase: the client places blocks predictively, so read the
    // block back instead of trusting useItemOn's return value.
    std::string const now     = source.getBlock(target).getTypeName();
    bool const        changed = now != "minecraft:air";
    bool const        matches = task.expected.empty() || now.find(task.expected) != std::string::npos;
    if (changed && matches) {
        return finish("placed", true, now);
    }
    if (task.elapsed >= task.verifyAt) {
        return finish(changed ? "unexpected_block" : "not_placed", false, now);
    }
    return false;
}

bool ProbeMod::tickUse(LocalPlayer& player, UseTask& task, nlohmann::json& result) {
    ++task.elapsed;
    if (task.elapsed < task.ticks) {
        return false;
    }
    std::string name = player.getRealName();
    auto const  uuid = player.getUuid();
    // Consuming on the client only predicts the result; the inventory and
    // hunger changes that stick are the server player's. Run the completion
    // path there and let it emit the single terminal event.
    queueServerTask([this, name, uuid, ticks = task.ticks] {
        auto    minecraft    = ll::service::bedrock::getMinecraft();
        Level*  level        = minecraft ? minecraft->getLevel() : nullptr;
        Player* serverPlayer = nullptr;
        if (level != nullptr) {
            serverPlayer = level->getPlayer(uuid);
            if (serverPlayer == nullptr && !name.empty()) {
                serverPlayer = level->getPlayer(name);
            }
        }
        if (serverPlayer == nullptr) {
            enqueue(R"({"event":"item_used","ok":false,"reason":"player_not_found"})");
            return;
        }

        auto& inventory = serverPlayer->getInventory();
        int   slot      = serverPlayer->getSelectedItemSlot();
        auto  stack     = inventory.getItemNonConst(slot);
        if (!stack || stack->isNull()) {
            enqueue(R"({"event":"item_used","ok":false,"reason":"empty_hand"})");
            return;
        }

        std::string type = stack->getTypeName();
        Item*       item = stack->mItem.get();
        if (!item) {
            enqueue(R"({"event":"item_used","ok":false,"reason":"no_item"})");
            return;
        }

        // Item::use performs the instant action (throw, drink, place). Food
        // needs the completion call as well: useTimeDepleted applies the food
        // effect and returns the ItemUseMethod, which Player::useItem then
        // consumes the stack for.
        item->use(*stack, *serverPlayer, HandSlot::Mainhand);
        if (item->isFood()) {
            ItemUseMethod const method = item->useTimeDepleted(*stack, level, serverPlayer);
            serverPlayer->useItem(*stack, method, true);
        }
        // The completion happened on the server stack; push it to the client so
        // its inventory snapshot (and the next use) sees the new count.
        serverPlayer->sendInventory(true);
        enqueue(
            nlohmann::json{
                {"event",  "item_used"                    },
                {"ok",     true                           },
                {"reason", "server"                       },
                {"item",   type                           },
                {"ticks",  ticks                          },
                {"count",  static_cast<int>(stack->mCount)},
        }
                .dump()
        );
    });

    // The terminal event comes from the server thread.
    result = nlohmann::json{};
    return true;
}


} // namespace probe
