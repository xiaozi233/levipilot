#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {
namespace {
EntitySnapshot::Vec snapshotVec(Vec3 const& value) { return {value.x, value.y, value.z}; }

void captureActor(
    Actor&                     actor,
    EntitySnapshot::Vec const& origin,
    EntitySnapshot::Row&       row,
    bool&                      truncated,
    bool                       detailed
) {
    row.runtimeId    = static_cast<std::uint64_t>(actor.getRuntimeID());
    auto const& type = actor.getTypeName();
    auto const& name = actor.getNameTag();
    row.type.assign(type);
    row.name.assign(name);
    truncated    = truncated || row.type.view().size() != type.size() || row.name.view().size() != name.size();
    row.pos      = snapshotVec(actor.getPosition());
    row.isPlayer = actor.isPlayer();
    row.health   = actor.getHealth();
    float dx = row.pos.x - origin.x, dy = row.pos.y - origin.y, dz = row.pos.z - origin.z;
    row.distance = std::sqrt(dx * dx + dy * dy + dz * dz);
    if (detailed) {
        row.velocity   = snapshotVec(actor.getVelocity());
        auto const rot = actor.getRotation();
        row.yaw        = rot.y;
        row.pitch      = rot.x;
        row.alive      = actor.isAlive();
        row.onGround   = actor.isOnGround();
    }
}

nlohmann::json entityRowJson(EntitySnapshot::Row const& row) {
    return {
        {"runtime_id", row.runtimeId             },
        {"type",       row.type.c_str()          },
        {"name",       row.name.c_str()          },
        {"is_player",  row.isPlayer              },
        {"health",     finiteOrZero(row.health)  },
        {"x",          finiteOrZero(row.pos.x)   },
        {"y",          finiteOrZero(row.pos.y)   },
        {"z",          finiteOrZero(row.pos.z)   },
        {"distance",   finiteOrZero(row.distance)}
    };
}

nlohmann::json aimPositionJson(EntitySnapshot::Vec const& value) {
    return {
        {"x", finiteOrZero(value.x)},
        {"y", finiteOrZero(value.y)},
        {"z", finiteOrZero(value.z)}
    };
}

nlohmann::json entityJson(EntityQuery const& query, EntitySnapshot const& out) {
    if (out.error)
        return {
            {"event",  "error"   },
            {"reason", out.error },
            {"phase",  "entities"}
        };
    auto const&    row = out.rows.front();
    nlohmann::json result;
    switch (query.operation) {
    case EntityOperation::List:
        result = {
            {"event",    "entities"             },
            {"radius",   query.radius           },
            {"count",    out.count              },
            {"entities", nlohmann::json::array()}
        };
        for (std::size_t i = 0; i < out.count; ++i) result["entities"].push_back(entityRowJson(out.rows[i]));
        break;
    case EntityOperation::Info:
        result = entityRowJson(row);
        result.update({
            {"event",     "entity_info"               },
            {"is_alive",  row.alive                   },
            {"on_ground", row.onGround                },
            {"vx",        finiteOrZero(row.velocity.x)},
            {"vy",        finiteOrZero(row.velocity.y)},
            {"vz",        finiteOrZero(row.velocity.z)},
            {"yaw",       finiteOrZero(row.yaw)       },
            {"pitch",     finiteOrZero(row.pitch)     }
        });
        break;
    case EntityOperation::Attack:
        result = {
            {"event",         "entity_attacked"       },
            {"runtime_id",    query.runtimeId         },
            {"ok",            out.ok                  },
            {"type",          row.type.c_str()        },
            {"health_before", finiteOrZero(row.health)}
        };
        break;
    case EntityOperation::Interact:
        result = {
            {"event",      "entity_interacted"},
            {"runtime_id", query.runtimeId    },
            {"ok",         out.ok             },
            {"type",       row.type.c_str()   },
            {"method",     "input"            }
        };
        break;
    case EntityOperation::LookAt:
        result = {
            {"event",      "look_at_done"          },
            {"runtime_id", query.runtimeId         },
            {"yaw",        finiteOrZero(out.yaw)   },
            {"pitch",      finiteOrZero(out.pitch) },
            {"eye",        aimPositionJson(out.eye)},
            {"target",     aimPositionJson(out.aim)}
        };
        break;
    }
    result["truncated"] = out.truncated;
    return result;
}
} // namespace

EntityStage ProbeMod::captureEntityStage(EntityStage stage, EntityQuery const& query, EntitySnapshot& out) {
    // SDK owning values live outside the raw SEH frame; no actor pointer or
    // SDK reference escapes this callback. These callbacks never run in tick.
    std::vector<Actor*> actors;
    EntityStage         next = EntityStage::Complete;
    if (!guardedGameRead([&] {
            if (stage == query.testStage) injectCaptureFault(query.test);
            if (stage == EntityStage::Client) {
                auto  client = ll::service::getClientInstance();
                auto* player = client ? client->getLocalPlayer() : nullptr;
                if (!player) {
                    out.error = "no_player";
                    return;
                }
                out.origin   = snapshotVec(player->getPosition());
                out.eye      = snapshotVec(player->getHeadPos());
                out.selfId   = static_cast<std::uint64_t>(player->getRuntimeID());
                out.identity = localPlayerIdentity();
                if (query.operation == EntityOperation::Attack && !out.identity.valid) {
                    out.error = "player_not_found";
                    return;
                }
                next = EntityStage::Server;
                return;
            }
            if (stage == EntityStage::Server) {
                ServerPlayerContext playerContext;
                if (query.operation == EntityOperation::Attack)
                    playerContext = serverPlayerContext(out.identity, mPoseEpoch.load(), mWorld.inWorld());
                auto level = ll::service::bedrock::getLevel();
                if (query.operation == EntityOperation::Attack) level = playerContext.level;
                if (!level) {
                    out.error = "level_unavailable";
                    return;
                }
                actors = level->getRuntimeActorList();
                for (auto* actor : actors) {
                    if (!actor) continue;
                    if (query.operation == EntityOperation::List) {
                        if (!actor->isAlive()
                            || (actor->isPlayer() && static_cast<std::uint64_t>(actor->getRuntimeID()) == out.selfId))
                            continue;
                        auto const pos = snapshotVec(actor->getPosition());
                        float      dx = pos.x - out.origin.x, dy = pos.y - out.origin.y, dz = pos.z - out.origin.z;
                        if (std::sqrt(dx * dx + dy * dy + dz * dz) > query.radius) continue;
                        captureActor(*actor, out.origin, out.rows[out.count++], out.truncated, false);
                        if (out.count >= static_cast<std::size_t>(std::clamp(query.limit, 1, 512))) break;
                        continue;
                    }
                    if (static_cast<std::uint64_t>(actor->getRuntimeID()) != query.runtimeId) continue;
                    captureActor(
                        *actor,
                        out.origin,
                        out.rows.front(),
                        out.truncated,
                        query.operation == EntityOperation::Info
                    );
                    out.count = 1;
                    if (query.operation == EntityOperation::Info) return;
                    auto const aim = actor->getHeadPos();
                    out.aim        = snapshotVec(aim);
                    if (query.operation != EntityOperation::Attack) {
                        next = EntityStage::Aim;
                        return;
                    }
                    if (!playerContext.player) {
                        out.error = "player_not_found";
                        return;
                    }
                    out.ok = playerContext.player->mGameMode && playerContext.player->mGameMode->attack(*actor, aim);
                    return;
                }
                if (query.operation != EntityOperation::List) out.error = "entity_not_found";
                return;
            }
            auto  client = ll::service::getClientInstance();
            auto* player = client ? client->getLocalPlayer() : nullptr;
            if (!player) {
                out.error = "no_player";
                return;
            }
            // The original request eye is preserved for compatibility.
            double dx = out.aim.x - out.eye.x, dy = out.aim.y - out.eye.y, dz = out.aim.z - out.eye.z;
            out.yaw         = yawTo(dx, dz);
            out.pitch       = pitchTo(dy, std::sqrt(dx * dx + dz * dz));
            auto const rot  = player->getRotation();
            float      dyaw = wrapDegrees(out.yaw - rot.y), dpitch = rot.x - out.pitch;
            if ((std::fabs(dyaw) > 1.0e-5f || std::fabs(dpitch) > 1.0e-5f)
                && !applyTurnDelta(*player, Vec2{dpitch, dyaw}))
                out.error = "look_failed";
        }))
        out.error = "entities_fault";
    // RequestMailbox owns a lock: keep it outside the raw SEH frame so an AV
    // cannot bypass lock destruction. A failed game read never injects input.
    if (!out.error && stage == EntityStage::Aim && query.operation == EntityOperation::Interact) {
        updateRequests([](RequestBlock& request) {
            request.input        = ControlState{};
            request.input.rmb    = true;
            request.input.active = true;
            request.inputTicks   = 3;
            ++request.inputSeq;
        });
        out.ok = true;
    }
    return next;
}

void ProbeMod::drainEntities() {
    if (auto request = mEntityCapture.current()) {
        if (auto stage = request->dispatch()) {
            if (request->query.test == CaptureTest::Deferred && *stage == request->query.testStage) {
                // Deliberately leave this stage queued; the wall-clock owner
                // must still terminate it without another game tick.
            } else {
                auto weak = std::weak_ptr{request};
                auto run  = [this, weak, stage = *stage] {
                    if (auto active = weak.lock()) {
                        active->run(stage, GetTickCount64(), [&](EntitySnapshot& out) {
                            return captureEntityStage(stage, active->query, out);
                        });
                        wakeSpool();
                    }
                };
                try {
                    if (*stage == EntityStage::Server) {
                        queueServerTask(std::move(run), [weak](char const* error) {
                            if (auto active = weak.lock()) active->cancel(error);
                        });
                    } else
                        queueClientTask(std::move(run), [weak](char const* error) {
                            if (auto active = weak.lock()) active->cancel(error);
                        });
                } catch (...) {
                    request->cancel("entity_queue_failed");
                }
            }
        }
    }
    auto done = mEntityCapture.take(GetTickCount64());
    if (!done.request) return;
    auto result = done.error
        ? nlohmann::json{{"event", "error"}, {"reason", done.error}, {"phase", "entities"}}
        : entityJson(done.request->query, done.request->snapshot);
    attachReqId(result, done.request->reqId);
    auto line = result.dump();
    if (done.release()) enqueue(std::move(line));
}

} // namespace probe
