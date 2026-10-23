#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

namespace {

template <typename Advance>
TaskStep taskTickWithSeh(Advance&& advance) {
    TaskStep step;
    __try {
        step = advance();
    } __except (
        GetExceptionCode() == EXCEPTION_ACCESS_VIOLATION ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH
    ) {
        // Catch inside the controller's lock scope, so an AV cannot strand
        // the task mutex under /EHsc. The next idle tick releases inputs.
        step.finished       = true;
        step.outcome.reason = "task_fault";
    }
    return step;
}

bool isAirName(std::string_view name) {
    return name == "minecraft:air" || name == "minecraft:cave_air" || name == "minecraft:void_air";
}

// True when the block ahead at foot level is open and the block below that
// position is missing. This is the "about to run off a ledge" condition.
bool atEdge(LocalPlayer& player, float lookAhead) {
    auto const  feet     = player.getFeetPos();
    auto const  rot      = player.getRotation();
    float const rad      = rot.y * 0.01745329252f;
    float const fx       = -std::sin(rad);
    float const fz       = std::cos(rad);
    int const   x        = static_cast<int>(std::floor(feet.x + fx * lookAhead));
    int const   z        = static_cast<int>(std::floor(feet.z + fz * lookAhead));
    int const   y        = static_cast<int>(std::floor(feet.y));
    auto const& source   = player.getDimensionBlockSourceConst();
    bool const  support  = !isAirName(source.getBlock(BlockPos{x, y - 1, z}).getTypeName());
    bool const  passable = isAirName(source.getBlock(BlockPos{x, y, z}).getTypeName())
                        && isAirName(source.getBlock(BlockPos{x, y + 1, z}).getTypeName());
    return !support && passable;
}

float horizontalDistance(Vec3 const& pos, float x, float z) {
    float const dx = x - static_cast<float>(pos.x);
    float const dz = z - static_cast<float>(pos.z);
    return std::sqrt(dx * dx + dz * dz);
}

// Do not drive at full speed while turning around a nearby destination: the
// finite turn rate otherwise produces an orbit outside a small tolerance.
struct Steering {
    float turn;
    float forward;
};

Steering steerToward(LocalPlayer& player, float x, float z, float distance, float tolerance) {
    auto const  pos   = player.getFeetPos();
    float const yaw   = static_cast<float>(std::atan2(-(x - pos.x), z - pos.z) * 57.29577951308232);
    float const error = wrapDegrees(yaw - player.getRotation().y);
    float const delta = std::clamp(error, -35.0f, 35.0f);
    float const forward =
        std::fabs(error - delta) > 10.0f ? 0.0f : std::clamp(distance / std::max(0.6f, tolerance * 4.0f), 0.1f, 1.0f);
    return {delta, forward};
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

void ProbeMod::cancelTasks(char const* reason) { mTasks.cancel(reason); }

std::uint64_t ProbeMod::startTask(TaskState state) {
    return mTasks.start(state, [this] { beginTask(); });
}

void ProbeMod::beginTask() {
    // Only publish requests here. Macro key release belongs to the game tick.
    updateRequests([](RequestBlock& request) {
        request.input      = ControlState{};
        request.inputTicks = 0;
        ++request.inputSeq;
        request.jumpCancel = true;
        ++request.jumpSeq;
        request.lookTicks = 0;
        ++request.lookSeq;
    });
    mMacroStartRequested = false;
    mMacroPlaying        = false;
    mMacroStopRequested  = true;
}

bool ProbeMod::tickTask(ClientInstance& client, LocalPlayer& player) {
    bool       advanced = false;
    bool const active   = mTasks.tick([&](TaskState& state, std::uint64_t id) {
        advanced = true;
        return taskTickWithSeh([&] {
            auto faultId = id;
            if (mTaskFaultId.compare_exchange_strong(faultId, 0)) {
                RaiseException(EXCEPTION_ACCESS_VIOLATION, 0, 0, nullptr);
            }
            if (mMacroStopRequested.exchange(false)) releaseMacroInputs();
            if (mSeenTaskId != id) {
                mSeenTaskId = id;
                applyControls(client, player, ControlState{});
            }
            TaskStep step;
            step.finished = std::visit(
                [&](auto& task) -> bool {
                    using T = std::decay_t<decltype(task)>;
                    if constexpr (std::is_same_v<T, NavigateTask>) return tickNavigate(client, player, task, step);
                    else if constexpr (std::is_same_v<T, JumpTask>) return tickJump(client, player, task, step);
                    else if constexpr (std::is_same_v<T, RouteTask>) return tickRoute(client, player, task, step);
                    else if constexpr (std::is_same_v<T, MineTask>) return tickMine(player, task, step);
                    else if constexpr (std::is_same_v<T, PlaceTask>) return tickPlace(player, task, step);
                    else if constexpr (std::is_same_v<T, UseTask>) return tickUse(player, task, step);
                    else return false;
                },
                state
            );
            if (step.finished) {
                // Keep a successful jump pressed until origin consumes this tick.
                bool const keepJumpPress = std::holds_alternative<JumpTask>(state) && step.outcome.ok;
                if (!keepJumpPress) applyControls(client, player, ControlState{});
            }
            return step;
        });
    });
    if (active && !advanced) {
        auto const stopped = taskTickWithSeh([&] {
            applyControls(client, player, ControlState{});
            return TaskStep{};
        });
        if (stopped.finished) cancelTasks("task_fault");
    }
    return active;
}

bool ProbeMod::tickNavigate(ClientInstance& client, LocalPlayer& player, NavigateTask& task, TaskStep& step) {
    Vec3 const  pos      = player.getFeetPos();
    float const dx       = task.x - static_cast<float>(pos.x);
    float const dz       = task.z - static_cast<float>(pos.z);
    float const distance = std::sqrt(dx * dx + dz * dz);
    ++task.elapsed;

    auto finish = [&](char const* reason, bool ok) {
        step.outcome.ok       = ok;
        step.outcome.reason   = reason;
        step.outcome.x        = finiteOrZero(static_cast<float>(pos.x));
        step.outcome.z        = finiteOrZero(static_cast<float>(pos.z));
        step.outcome.distance = finiteOrZero(distance);
        return true;
    };

    if (distance <= task.tolerance) {
        return finish("arrived", true);
    }
    if (task.elapsed >= task.maxTicks) {
        return finish("timeout", false);
    }

    auto const steering = steerToward(player, task.x, task.z, distance, task.tolerance);
    if (std::fabs(steering.turn) > 0.01f) {
        applyTurnDelta(player, Vec2{0.0f, steering.turn});
    }

    if (task.haveLast) {
        float const mx    = static_cast<float>(pos.x) - task.lastX;
        float const mz    = static_cast<float>(pos.z) - task.lastZ;
        bool const  moved = std::sqrt(mx * mx + mz * mz) >= 0.02f;
        if (!moved && player.isOnGround() && steering.forward > 0.0f) {
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
    if (player.isOnGround() && task.jumpCooldown == 0 && steering.forward > 0.0f) {
        if (task.jumpMode == JumpMode::Distance) {
            // The target is on the far side of the gap: take off just before
            // the edge instead of when the gap is already underfoot.
            wantJump = distance <= task.jumpDistance;
        } else if (task.jumpMode == JumpMode::Edge) {
            wantJump = atEdge(player, task.edgeLookahead);
        } else if (task.jumpMode == JumpMode::Auto && task.stuckTicks >= 5) {
            wantJump        = true;
            task.stuckTicks = 0;
        }
    }
    if (wantJump) {
        task.jumpCooldown = 5;
        ++task.jumps;
    }

    ControlState state;
    state.forward = steering.forward;
    state.sprint  = task.sprint && steering.forward == 1.0f;
    state.active  = true;
    state.jump    = wantJump;
    applyControls(client, player, state);
    return false;
}

bool ProbeMod::tickJump(ClientInstance& client, LocalPlayer& player, JumpTask& task, TaskStep& step) {
    Vec3 const  pos      = player.getFeetPos();
    auto const  rot      = player.getRotation();
    float const distance = horizontalDistance(pos, task.x, task.z);
    ++task.elapsed;

    auto finish = [&](bool ok, char const* reason) {
        step.outcome.ok       = ok;
        step.outcome.reason   = reason;
        step.outcome.x        = finiteOrZero(static_cast<float>(pos.x));
        step.outcome.z        = finiteOrZero(static_cast<float>(pos.z));
        step.outcome.distance = finiteOrZero(distance);
        step.outcome.onGround = player.isOnGround();
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

bool ProbeMod::tickRoute(ClientInstance& client, LocalPlayer& player, RouteTask& task, TaskStep& step) {
    ++task.elapsed;
    if (task.index >= static_cast<int>(task.count)) {
        step.outcome.ok = true;
        return true;
    }

    RouteWaypoint& point = task.points[static_cast<std::size_t>(task.index)];
    ++task.segmentElapsed;
    Vec3 const  pos      = player.getFeetPos();
    float const distance = horizontalDistance(pos, point.x, point.z);

    if (distance <= point.tolerance) {
        step.waypoint              = true;
        step.outcome.x             = finiteOrZero(static_cast<float>(pos.x));
        step.outcome.z             = finiteOrZero(static_cast<float>(pos.z));
        step.outcome.waypoint      = task.index;
        step.outcome.waypointTicks = task.segmentElapsed;
        ++task.index;
        task.segmentElapsed = 0;
        task.stuckTicks     = 0;
        task.haveLast       = false;
        if (task.index >= static_cast<int>(task.count)) {
            step.outcome.ok = true;
            return true;
        }
        return false;
    }

    if (task.segmentElapsed >= point.maxTicks) {
        step.outcome.reason   = "timeout";
        step.outcome.x        = finiteOrZero(static_cast<float>(pos.x));
        step.outcome.z        = finiteOrZero(static_cast<float>(pos.z));
        step.outcome.distance = finiteOrZero(distance);
        return true;
    }

    auto const steering = steerToward(player, point.x, point.z, distance, point.tolerance);
    if (std::fabs(steering.turn) > 0.01f) {
        applyTurnDelta(player, Vec2{0.0f, steering.turn});
    }

    if (task.haveLast) {
        float const mx    = static_cast<float>(pos.x) - task.lastX;
        float const mz    = static_cast<float>(pos.z) - task.lastZ;
        bool const  moved = std::sqrt(mx * mx + mz * mz) >= 0.02f;
        if (!moved && player.isOnGround() && steering.forward > 0.0f) {
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
    if (player.isOnGround() && task.jumpCooldown == 0 && steering.forward > 0.0f) {
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
    state.forward = steering.forward;
    state.sprint  = task.sprint && steering.forward == 1.0f;
    state.active  = true;
    state.jump    = wantJump;
    applyControls(client, player, state);
    return false;
}

bool ProbeMod::tickMine(LocalPlayer& player, MineTask& task, TaskStep& step) {
    auto const&    source = player.getDimensionBlockSourceConst();
    BlockPos const pos{task.x, task.y, task.z};
    ++task.elapsed;

    auto finish = [&](char const* reason, bool ok, std::string_view name) {
        step.outcome.ok     = ok;
        step.outcome.reason = reason;
        step.outcome.block.assign(name);
        return true;
    };

    if (!player.mGameMode) {
        return finish("no_gamemode", false, "");
    }

    auto const& name = source.getBlock(pos).getTypeName();
    if (name == "minecraft:air") {
        return finish(task.started ? "mined" : "already_air", true, task.block.view());
    }
    if (task.block.empty()) {
        task.block.assign(name);
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

bool ProbeMod::tickPlace(LocalPlayer& player, PlaceTask& task, TaskStep& step) {
    auto const&    source = player.getDimensionBlockSourceConst();
    BlockPos const target{task.x, task.y, task.z};
    ++task.elapsed;

    auto finish = [&](char const* reason, bool ok, std::string_view name) {
        step.outcome.ok     = ok;
        step.outcome.reason = reason;
        step.outcome.block.assign(name);
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
        auto const& before = source.getBlock(target).getTypeName();
        if (before != "minecraft:air") {
            bool const matches = task.expected.empty() || before.find(task.expected.view()) != std::string::npos;
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
    auto const& now     = source.getBlock(target).getTypeName();
    bool const  changed = now != "minecraft:air";
    bool const  matches = task.expected.empty() || now.find(task.expected.view()) != std::string::npos;
    if (changed && matches) {
        return finish("placed", true, now);
    }
    if (task.elapsed >= task.verifyAt) {
        return finish(changed ? "unexpected_block" : "not_placed", false, now);
    }
    return false;
}

bool ProbeMod::tickUse(LocalPlayer&, UseTask& task, TaskStep& step) {
    if (!task.dispatched && ++task.elapsed >= task.ticks) {
        task.dispatched = true;
        step.useReady   = true;
    }
    // Stay active until the guarded server callback publishes the result.
    return false;
}

} // namespace probe
