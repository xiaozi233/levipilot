#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

bool ProbeMod::queueFlight(FlightQuery query, std::uint64_t timeout) {
    std::lock_guard lock(mFlight.mutex);
    if (!hooksAllowed() || !mWorld.inWorld()) {
        enqueue(nlohmann::json{{"event", "error"}, {"reason", hooksAllowed() ? "not_in_world" : "version_mismatch"}}.dump());
        return false;
    }
    query.id = mFlight.nextId + 1;
    query.generation = mFlight.generation.load();
    if (!mFlight.capture.begin(query, GetTickCount64(), timeout, activeReqId())) {
        enqueue(R"({"event":"error","reason":"flight_busy"})");
        return false;
    }
    mFlight.nextId = query.id;
    mFlight.assertion.prepare(query.id);
    enqueue(nlohmann::json{{"event", "flight_queued"}, {"flight_id", query.id}}.dump());
    return true;
}

void ProbeMod::cancelFlight(char const* reason, bool discard) {
    std::lock_guard lock(mFlight.mutex);
    mFlight.generation.fetch_add(1);
    mFlight.assertion.cancel();
    if (discard) {
        mFlight.capture.reset();
        mFlight.replyAfter = mFlight.nextId;
    } else mFlight.capture.cancel(reason);
}

FlightStage ProbeMod::captureFlightStage(FlightStage stage, FlightQuery const& query, FlightSnapshot& out) {
    FlightStage next = FlightStage::Complete;
    if (!guardedGameRead([&] {
        if (query.generation != mFlight.generation.load() || !mWorld.inWorld()) { out.error = "world_left"; return; }
        if (stage == query.testStage) injectCaptureFault(query.test);
        if (stage == FlightStage::Client) {
            auto client = ll::service::getClientInstance();
            auto* player = client ? client->getLocalPlayer() : nullptr;
            if (!player) { out.error = "no_player"; return; }
            out.identity = localPlayerIdentity();
            if (!out.identity.valid) { out.error = "flight_player_unknown"; return; }
            out.gamemode = static_cast<int>(player->getPlayerGameType());
            if (query.operation == FlightOperation::Toggle && player->getPlayerGameType() == GameType::Spectator) {
                out.error = "spectator_not_supported"; return;
            }
            auto const current = player->getAbilities().getBool(AbilitiesIndex::MayFly);
            out.target = query.target == FlightTarget::Invert ? !current : query.target == FlightTarget::On;
            next = FlightStage::Server;
            return;
        }
        if (stage == FlightStage::Server) {
            auto context = serverPlayerContext(out.identity, mPoseEpoch.load(), mWorld.inWorld());
            if (!context.level) { out.error = "flight_no_server_level"; return; }
            auto* player = context.player;
            if (!player) { out.error = "flight_player_not_found"; return; }
            if (query.operation == FlightOperation::Toggle && player->getPlayerGameType() == GameType::Spectator) {
                out.error = "spectator_not_supported"; return;
            }
            player->setAbility(AbilitiesIndex::MayFly, out.target);
            if (query.operation == FlightOperation::Fly || !out.target)
                player->setAbility(AbilitiesIndex::Flying, out.target);
            out.serverApplied = true;
            next = FlightStage::Verify;
            return;
        }
        auto client = ll::service::getClientInstance();
        auto* player = client ? client->getLocalPlayer() : nullptr;
        if (!player) { out.error = "no_player"; return; }
        out.gamemode = static_cast<int>(player->getPlayerGameType());
        auto const mayfly = player->getAbilities().getBool(AbilitiesIndex::MayFly);
        out.flying = player->isFlying();
        bool const flyingMatches = (query.operation == FlightOperation::Toggle && out.target)
            || out.flying == out.target;
        if (mayfly != out.target || !flyingMatches) {
            next = FlightStage::Verify;
            return;
        }
        out.clientApplied = true;
        if (query.operation == FlightOperation::Toggle) {
            auto const test = query.testStage == FlightStage::Assert ? query.test : CaptureTest::None;
            mFlight.assertion.start(query.id, out.target, test);
        }
    })) out.error = "flight_fault";
    return next;
}

void ProbeMod::tickFlightAssertion(LocalPlayer& player) {
    mFlight.assertion.tick([&](bool target, CaptureTest test) -> char const* {
        bool matches = false;
        if (!guardedGameRead([&] {
            injectCaptureFault(test);
            matches = player.getAbilities().getBool(AbilitiesIndex::MayFly) == target
                && (target || !player.isFlying());
        })) return "flight_assert_fault";
        return matches ? nullptr : "flight_assert_mismatch";
    });
}

void ProbeMod::drainFlight() {
    std::lock_guard lock(mFlight.mutex);
    FlightAssertion::Failure failure;
    while (mFlight.assertion.pop(failure)) {
        if (failure.id <= mFlight.replyAfter) continue;
        auto request = mFlight.capture.current();
        if (request && request->query.id == failure.id) request->cancel(failure.error);
        else enqueue(nlohmann::json{{"event", "flight_assertion_failed"}, {"flight_id", failure.id},
                                   {"ok", false}, {"reason", failure.error}, {"partial", true}}.dump());
    }
    if (auto lost = mFlight.assertion.takeLost())
        enqueue(nlohmann::json{{"event", "flight_assertion_failed"}, {"reason", "buffer_full"}, {"lost", lost}}.dump());
    if (auto request = mFlight.capture.current()) {
        if (auto stage = request->dispatch(); stage && !(request->query.test == CaptureTest::Deferred && *stage == request->query.testStage)) {
            auto weak = std::weak_ptr{request};
            auto run = [this, weak, stage = *stage] {
                if (auto active = weak.lock()) {
                    active->run(stage, GetTickCount64(), [&](FlightSnapshot& out) {
                        return captureFlightStage(stage, active->query, out);
                    });
                    wakeSpool();
                }
            };
            auto failed = [weak](char const* error) { if (auto active = weak.lock()) active->cancel(error); };
            try {
                if (*stage == FlightStage::Server) queueServerTask(std::move(run), failed);
                else queueClientTask(std::move(run), failed);
            } catch (...) { request->cancel("flight_queue_failed"); }
        }
    }
    auto done = mFlight.capture.take(GetTickCount64());
    if (!done.request) return;
    auto error = done.error ? done.error : done.request->snapshot.error;
    auto const& query = done.request->query;
    nlohmann::json result;
    if (error) {
        mFlight.assertion.cancel();
        result = {{"event", "error"}, {"phase", "flight"}, {"flight_id", query.id}, {"reason", error}, {"partial", true}};
    } else {
        auto const& out = done.request->snapshot;
        result = {{"event", query.operation == FlightOperation::Fly ? "fly_ok" : "togglefly_ok"},
                  {"flight_id", query.id}, {"mayfly", out.target}, {"flying", out.flying},
                  {"gamemode", out.gamemode}, {"client_applied", out.clientApplied},
                  {"client_observed", out.clientApplied}, {"server_applied", out.serverApplied},
                  {"application", "server_with_client_readback"}, {"assertion", "readback"}};
    }
    attachReqId(result, done.request->reqId);
    auto line = result.dump();
    if (done.release()) enqueue(std::move(line));
}

} // namespace probe
