#include "mod/Internal.h"

using namespace probe::detail;


namespace probe {

void ProbeMod::onPacketSent(Packet& packet) {
    if (mPackets.sendActive() && packet.getId() == MinecraftPacketIds::CommandRequest) {
        captureCommandTemplate(packet);
    }
    mPackets.onSent(packet, mTick.load(std::memory_order_relaxed));
}

void ProbeMod::onPacketReceived(Packet const& packet, std::size_t anchor) {
    mPackets.onReceived(packet, anchor, mTick.load(std::memory_order_relaxed));
}

void ProbeMod::packetPeerTick() { mPackets.peerTick(); }

void ProbeMod::packetCaptureStop() { mPackets.stop(); }

void ProbeMod::tickPacketInject(LocalPlayer& player) {
    std::string text;
    {
        std::lock_guard lock(mChatMutex);
        if (mChatPending.empty()) {
            return;
        }
        text.swap(mChatPending);
    }
    std::string error;
    bool const  ok = sendCommandPacket(player, text, error);
    mChatLastResult.store(ok ? 0 : 1, std::memory_order_relaxed);
    if (ok) {
        mChatSent.fetch_add(1, std::memory_order_relaxed);
    }
    nlohmann::json event{
        {"event", "chat_sent"},
        {"ok",    ok         },
        {"text",  text       },
    };
    if (!ok) {
        event["reason"] = error;
    }
    enqueue(event.dump());
}

void ProbeMod::captureCommandTemplate(Packet& packet) {
    if (!mAllowPacketInject.load(std::memory_order_relaxed)) {
        return;
    }
    try {
        // CommandRequestPacket publicly inherits its payload, so a copy of the
        // payload (including the player origin) is a valid template.
        CommandRequestPacketPayload copy =
            static_cast<CommandRequestPacketPayload const&>(static_cast<CommandRequestPacket&>(packet));
        std::lock_guard lock(mCommandTemplateMutex);
        mCommandTemplate = std::move(copy);
    } catch (...) {
        // Template capture is best effort; chat_send will report no template.
    }
}

bool ProbeMod::sendCommandPacket(LocalPlayer& player, std::string const& text, std::string& error) {
    (void)player;
    try {
        std::optional<CommandRequestPacketPayload> payload;
        {
            std::lock_guard lock(mCommandTemplateMutex);
            if (!mCommandTemplate) {
                error = "no_command_template";
                return false;
            }
            payload = *mCommandTemplate;
        }
        payload->mCommand        = text;
        payload->mVersion        = CurrentCmdVersion::Latest;
        payload->mInternalSource = false;
        CommandRequestPacket packet{std::move(*payload)};
        packet.sendToServer();
        return true;
    } catch (std::exception const& e) {
        error = e.what();
    } catch (...) {
        error = "unknown exception";
    }
    return false;
}

} // namespace probe
