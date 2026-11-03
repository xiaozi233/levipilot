#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {
namespace {
thread_local bool injectingCommandPacket = false;
struct CommandInjectionScope {
    bool previous = injectingCommandPacket;
    CommandInjectionScope() { injectingCommandPacket = true; }
    ~CommandInjectionScope() { injectingCommandPacket = previous; }
};
} // namespace

void ProbeMod::onPacketSent(Packet& packet) {
    if (!injectingCommandPacket && mPackets.sendActive() && mAllowPacketInject.load()) {
        try {
            if (!guardedGameRead([&] {
                if (packet.getId() == MinecraftPacketIds::CommandRequest) captureCommandTemplate(packet);
            })) throw 0;
        } catch (...) {
            mPacketRequests.templateFaults.fetch_add(1);
            mPacketRequests.templates.capture(localPlayerIdentity(), mPacketRequests.generation.load(), [](auto&) { return false; });
        }
    }
    mPackets.onSent(packet, mTick.load(std::memory_order_relaxed));
}

void ProbeMod::onPacketReceived(Packet const& packet, std::size_t anchor) {
    mPackets.onReceived(packet, anchor, mTick.load(std::memory_order_relaxed));
}

void ProbeMod::packetPeerTick() { mPackets.peerTick(); }
void ProbeMod::packetCaptureStop() { mPackets.stop(); }

void ProbeMod::captureCommandTemplate(Packet& packet) {
    mPacketRequests.templates.capture(localPlayerIdentity(), mPacketRequests.generation.load(), [&](CommandTemplate& out) {
        bool ok = false;
        try { ok = guardedGameRead([&] {
            auto const test = mPacketRequests.templateTest.exchange(CaptureTest::None);
            injectCaptureFault(test);
            auto const& payload = static_cast<CommandRequestPacket const&>(packet);
            auto const& origin = *payload.mOrigin;
            out.type = static_cast<std::uint64_t>(origin.mType);
            out.uuidA = origin.mUUID->a;
            out.uuidB = origin.mUUID->b;
            out.playerId = origin.mPlayerId;
            out.setRequestId(*origin.mRequestId);
            if (test == CaptureTest::Partial) out.error = CommandTemplateError::TooLong;
        }); } catch (...) { mPacketRequests.templateFaults.fetch_add(1); throw; }
        if (!ok) mPacketRequests.templateFaults.fetch_add(1);
        return ok;
    });
}

void ProbeMod::queuePacketInjection(PacketInjectionQuery query, std::uint64_t timeout) {
    std::lock_guard lock(mPacketRequests.mutex);
    query.owner = localPlayerIdentity();
    if (!query.owner.valid) {
        enqueue(R"({"event":"error","reason":"packet_player_unknown"})");
        return;
    }
    auto const now = GetTickCount64();
    query.id = mPacketRequests.nextId + 1;
    query.generation = mPacketRequests.generation.load();
    query.deadline = now + timeout;
    query.templateError = mPacketRequests.templates.latest(query.origin, query.owner, query.generation);
    if (query.test == PacketInjectionTest::Obsolete) ++query.owner.epoch;
    if (query.test == PacketInjectionTest::WrongOwner) ++query.origin.owner.uuidA;
    auto request = mPacketRequests.injection.begin(std::move(query), now, timeout);
    if (!request) {
        enqueue(R"({"event":"error","reason":"packet_inject_busy"})");
        return;
    }
    mPacketRequests.nextId = request->query.id;
    bootLog("input: chat_send queued id=" + std::to_string(mPacketRequests.nextId));
    enqueue(nlohmann::json{{"event", "chat_send_queued"}, {"packet_id", mPacketRequests.nextId}}.dump());
}

void ProbeMod::cancelPacketInjection(char const* reason, bool discard) {
    std::lock_guard lock(mPacketRequests.mutex);
    mPacketRequests.generation.fetch_add(1);
    mPacketRequests.templateTest = CaptureTest::None;
    if (discard) mPacketRequests.injection.reset();
    else mPacketRequests.injection.cancel(reason);
}

void ProbeMod::sendCommandPacket(PacketInjectionQuery const& query, PacketInjectionSnapshot& out) {
    if (query.templateError) { out.error = query.templateError; return; }
    // SDK owning objects live outside the raw SEH frame. Completed objects
    // unwind normally after a handled fault; partial SDK construction cannot
    // be rolled back. No SDK object is retained by the template/send hook.
    std::unique_ptr<CommandOrigin> contextOrigin;
    std::optional<CommandContext> context;
    std::optional<CommandRequestPacketPayload> payload;
    std::optional<CommandRequestPacket> packet;
    std::string requestId(query.origin.requestIdView());
    CommandInjectionScope injected; // Restores even after SEH/C++; never recapture our own send.
    if (!guardedGameRead([&] {
        applyPacketInjection(out,
            [&]() -> char const* {
                if (auto error = packetInjectionAdmission(query, mPacketRequests.generation.load(), mPoseEpoch.load(),
                                                          mWorld.inWorld(), GetTickCount64())) return error;
                if (!mAllowPacketInject.load()) return "packet_inject_disabled";
                auto client = ll::service::getClientInstance();
                auto* player = client ? client->getLocalPlayer() : nullptr;
                if (!player) return "packet_player_not_found";
                auto const uuid = player->getUuid();
                if (uuid.a != query.owner.uuidA || uuid.b != query.owner.uuidB) return "packet_player_changed";
                return nullptr;
            },
            [&]() -> char const* {
                // Use real SDK constructors, then restore EVERY captured
                // origin field. The temporary automation origin never sends.
                contextOrigin = std::make_unique<ClientAutomationCommandOrigin>(requestId);
                context.emplace(query.text, std::move(contextOrigin), static_cast<int>(CurrentCmdVersion::Latest));
                payload.emplace(*context, false);
                auto& origin = *payload->mOrigin;
                origin.mType = static_cast<CommandOriginType>(query.origin.type);
                origin.mUUID = mce::UUID{query.origin.uuidA, query.origin.uuidB};
                origin.mRequestId = requestId;
                origin.mPlayerId = query.origin.playerId;
                payload->mCommand = query.text;
                payload->mVersion = CurrentCmdVersion::Latest;
                payload->mInternalSource = false;
                packet.emplace(std::move(*payload));
                return nullptr;
            },
            [&] { packet->sendToServer(); },
            [&](PacketInjectionTest point) {
                if (point == PacketInjectionTest::Seh && query.test == PacketInjectionTest::Cpp) throw 42;
                if (query.test == point) RaiseException(EXCEPTION_ACCESS_VIOLATION, 0, 0, nullptr);
            });
    })) out.error = "packet_inject_fault";
}

void ProbeMod::drainPacketInjection() {
    std::lock_guard lock(mPacketRequests.mutex);
    if (auto request = mPacketRequests.injection.current(); request && request->query.test != PacketInjectionTest::Deferred) {
        if (auto stage = request->dispatch()) {
            auto weak = std::weak_ptr{request};
            auto failed = [weak](char const* error) { if (auto active = weak.lock()) active->cancel(error); };
            try {
                if (request->query.test == PacketInjectionTest::Queue) failed("packet_inject_queue_failed");
                else queueClientTask([this, weak, stage = *stage] {
                    if (auto active = weak.lock()) active->run(stage, GetTickCount64(), [&](PacketInjectionSnapshot& out) {
                        sendCommandPacket(active->query, out);
                        return PacketInjectionStage::Complete;
                    });
                }, failed);
            } catch (...) { failed("packet_inject_queue_failed"); }
        }
    }
    auto done = mPacketRequests.injection.take(GetTickCount64());
    if (!done.request) return;
    // Cancellation may finish while a callback still owns snapshot. Never
    // inspect that snapshot or claim rollback; report conservative partial.
    auto error = done.error ? done.error : done.request->snapshot.error;
    if (error && std::string_view(error) == "capture_timeout") error = "packet_inject_timeout";
    if (error && std::string_view(error) == "capture_failed") error = "packet_inject_failed";
    auto const& query = done.request->query;
    nlohmann::json result{{"event", "chat_sent"}, {"ok", error == nullptr}, {"text", query.text},
                           {"packet_id", query.id}, {"partial", error != nullptr}};
    if (error) result["reason"] = error;
    auto line = result.dump();
    if (done.release()) {
        if (!error) mPacketRequests.chatSent.fetch_add(1);
        enqueue(std::move(line));
    }
}

} // namespace probe
