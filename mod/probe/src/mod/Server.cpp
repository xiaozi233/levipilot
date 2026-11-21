#include "mod/Internal.h"

using namespace probe::detail;

namespace probe::detail {

ServerPlayerContext serverPlayerContext(
    PlayerIdentity const& identity, std::uint64_t currentEpoch, bool inWorld,
    std::string const* explicitName
) {
    if (!identity.matches(currentEpoch, inWorld)) return {};
    auto minecraft = ll::service::bedrock::getMinecraft();
    if (!minecraft) return {};
    ServerPlayerContext context{&*minecraft, minecraft->getLevel()};
    if (context.level) context.player = explicitName && !explicitName->empty()
        ? context.level->getPlayer(*explicitName)
        : context.level->getPlayer(mce::UUID{identity.uuidA, identity.uuidB});
    return context;
}

} // namespace probe::detail

namespace probe {

std::uint64_t ProbeMod::queueTeleport(TeleportQuery query, bool resetControls, std::uint64_t timeout) {
    std::lock_guard lock(mTeleport.mutex);
    if (!hooksAllowed() || !mWorld.inWorld()) {
        enqueue(nlohmann::json{{"event", "error"}, {"reason", hooksAllowed() ? "not_in_world" : "version_mismatch"}}.dump());
        return 0;
    }
    query.identity = localPlayerIdentity();
    if (!query.identity.valid) {
        enqueue(R"({"event":"error","reason":"teleport_player_unknown"})");
        return 0;
    }
    auto const now = GetTickCount64();
    query.id = mTeleport.nextId + 1;
    query.generation = mTeleport.generation.load();
    query.deadline = now + timeout;
    if (query.test == TeleportTest::Obsolete) ++query.identity.epoch;
    auto request = mTeleport.capture.begin(query, now, timeout, activeReqId());
    if (!request) {
        enqueue(R"({"event":"error","reason":"teleport_busy"})");
        return 0;
    }
    mTeleport.nextId = query.id;
    // Admission precedes reset side effects: a rejected reset/checkpoint must
    // not cancel the active task, replace a teleport, or strip effects.
    try {
        if (resetControls) {
            cancelTasks("reset");
            beginTask();
            mClickRequest.store(-1);
            mSlotRequest.store(-1);
        }
    } catch (...) {
        request->cancel("teleport_prepare_failed");
        return 0;
    }
    nlohmann::json reply{{"event", resetControls ? "reset_done" : "teleport_queued"},
                         {"teleport_id", query.id}, {"ok", true},
                         {"x", query.x}, {"y", query.y}, {"z", query.z}};
    if (resetControls) {
        reply["yaw"] = query.hasRotation ? nlohmann::json(query.yaw) : nlohmann::json(nullptr);
        reply["pitch"] = query.hasRotation ? nlohmann::json(query.pitch) : nlohmann::json(nullptr);
    }
    enqueue(reply.dump());
    return query.id;
}

void ProbeMod::cancelTeleport(char const* reason, bool discard) {
    std::lock_guard lock(mTeleport.mutex);
    mTeleport.generation.fetch_add(1);
    if (discard) mTeleport.capture.reset();
    else mTeleport.capture.cancel(reason);
}

void ProbeMod::captureTeleport(TeleportQuery const& query, TeleportSnapshot& out) {
    Player* player = nullptr;
    Vec2 rotation;
    if (!guardedGameRead([&] {
        applyTeleport(query, out,
            [&] { return teleportAdmission(query, mTeleport.generation.load(), mPoseEpoch.load(), mWorld.inWorld(), GetTickCount64()); },
            [&]() -> char const* {
                auto context = serverPlayerContext(query.identity, mPoseEpoch.load(), mWorld.inWorld());
                if (!context.level) return "teleport_no_server_level";
                if (!context.player) return "teleport_player_not_found";
                player = context.player;
                // Preserve the old partial-angle rule: when either angle is
                // supplied, the omitted angle is zero; neither keeps rotation.
                rotation = query.hasRotation ? Vec2{query.pitch, query.yaw} : player->getRotation();
                return nullptr;
            },
            [&] { player->removeAllEffects(); },
            [&] { player->teleport(Vec3{query.x, query.y, query.z}, player->getDimensionId(), rotation); },
            [&](TeleportTest point) {
                if (point == TeleportTest::Seh && query.test == TeleportTest::Cpp) throw 42;
                if (query.test != point) return;
                RaiseException(EXCEPTION_ACCESS_VIOLATION, 0, 0, nullptr);
            });
    })) out.error = "teleport_fault";
}

void ProbeMod::drainTeleport() {
    std::lock_guard lock(mTeleport.mutex);
    if (auto request = mTeleport.capture.current(); request && request->query.test != TeleportTest::Deferred) {
        if (auto stage = request->dispatch()) {
            auto weak = std::weak_ptr{request};
            auto failed = [weak](char const* error) { if (auto active = weak.lock()) active->cancel(error); };
            try {
                queueServerTask([this, weak, stage = *stage] {
                    if (auto active = weak.lock()) {
                        active->run(stage, GetTickCount64(), [&](TeleportSnapshot& out) {
                            captureTeleport(active->query, out);
                            return TeleportStage::Complete;
                        });
                        wakeSpool();
                    }
                }, failed, request->query.test == TeleportTest::Queue);
            } catch (...) { request->cancel("teleport_queue_failed"); }
        }
    }
    auto done = mTeleport.capture.take(GetTickCount64());
    if (!done.request) return;
    // A timeout/cancelled callback may still own snapshot; never read it until
    // successful Ready publication. Failure is conservatively partial.
    auto error = done.error ? done.error : done.request->snapshot.error;
    if (error && std::string_view(error) == "capture_timeout") error = "teleport_timeout";
    if (error && std::string_view(error) == "capture_failed") error = "teleport_failed";
    if (error && std::string_view(error) == "no_server_instance") error = "teleport_no_server_instance";
    auto const& query = done.request->query;
    nlohmann::json result;
    if (error) result = {{"event", "error"}, {"phase", "teleport"}, {"teleport_id", query.id},
                         {"reason", error}, {"partial", true}};
    else result = {{"event", "teleported"}, {"source", "server"}, {"teleport_id", query.id}, {"ok", true},
                   {"x", query.x}, {"y", query.y}, {"z", query.z}, {"has_rotation", query.hasRotation},
                   {"effects_cleared", done.request->snapshot.effectsCleared}};
    attachReqId(result, done.request->reqId);
    auto line = result.dump();
    if (done.release()) enqueue(std::move(line));
}

std::uint64_t ProbeMod::resetPlayer(
    float x,
    float y,
    float z,
    bool  hasYaw,
    float yaw,
    bool  hasPitch,
    float pitch,
    bool  clearEffects
) {
    // The pipe thread must not read LocalPlayer (its rotation is written every
    // frame by the tick thread). When no rotation was supplied the server task
    // keeps the current one, so nothing is lost.
    TeleportQuery request;
    request.x            = x;
    request.y            = y;
    request.z            = z;
    request.hasRotation  = hasYaw || hasPitch;
    request.clearEffects = clearEffects;
    request.yaw          = yaw;
    request.pitch        = pitch;
    return queueTeleport(request, true);
}


} // namespace probe
