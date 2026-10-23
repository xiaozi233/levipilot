#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <memory>

#include <nlohmann/json.hpp>

class Packet;

namespace probe {

// H1 packet tap: identity ring + optional serialized bytes, send and recv.
// Hooks run on the client/server threads, so every field written from a hook is
// a relaxed atomic or a slot published with a release sequence; start/stop and
// the drains run on the pipe thread.
class PacketTap {
public:
    static constexpr std::uint8_t kSend = 0;
    static constexpr std::uint8_t kRecv = 1;

    // Recv-side anchors, in installation order. The first block is the
    // server-side send helper family; the last two are the loopback delivery
    // thunks. Both layers can fire for one packet, so counts are per anchor.
    enum RecvAnchor : std::size_t {
        kRecvToPlayer = 0,
        kRecvToActor,
        kRecvToBlockPos,
        kRecvToClientId,
        kRecvToClientWithSubId,
        kRecvToClients,
        kRecvToClientsExcept,
        kRecvLoopbackClient,
        kRecvLoopbackClientUser,
        kRecvAnchorCount,
    };

    nlohmann::json start(bool enable, nlohmann::json const& args);
    void           stop();
    nlohmann::json status();
    nlohmann::json drain(std::uint64_t since, std::size_t limit);
    nlohmann::json bytesDrain(std::uint64_t since, std::size_t limit);

    void onSent(Packet& packet, std::uint64_t tick);
    void onReceived(Packet const& packet, std::size_t anchor, std::uint64_t tick);
    void peerTick();

    [[nodiscard]] bool capturing() const {
        return mCapture.load(std::memory_order_relaxed);
    }
    [[nodiscard]] bool sendActive() const {
        return capturing() && mSendEnabled.load(std::memory_order_relaxed);
    }
    [[nodiscard]] bool recvEnabled() const {
        return mRecvEnabled.load(std::memory_order_relaxed);
    }
    [[nodiscard]] std::uint64_t records() const {
        return mHead.load(std::memory_order_relaxed);
    }
    [[nodiscard]] std::uint64_t recvRecords() const {
        return mRecvRecords.load(std::memory_order_relaxed);
    }
    [[nodiscard]] std::uint64_t peerSends() const {
        return mPeerCount.load(std::memory_order_relaxed);
    }

private:
    struct PacketRecord {
        std::atomic<std::uint64_t> seq{0};
        std::uint64_t              order    = 0;
        std::uint64_t              tick     = 0;
        std::int32_t               id       = 0;
        std::uint32_t              tid      = 0;
        std::uint8_t               dir      = kSend;
        std::uint8_t               injected = 0;
        std::uint8_t               pad[2]{};
    };
    struct PacketBytes {
        std::atomic<std::uint64_t> seq{0};
        std::uint64_t              order = 0;
        std::uint32_t              id    = 0;
        std::uint32_t              size  = 0;
        std::uint32_t              tid   = 0;
        std::uint8_t               dir   = kSend;
        std::uint8_t               data[128]{};
    };

    void recordPacket(Packet const& packet, std::uint8_t dir, std::uint64_t tick);

    static constexpr std::size_t kRing     = 1u << 14;
    static constexpr std::size_t kByteRing = 1u << 12;
    std::unique_ptr<PacketRecord[]> mRing;
    std::unique_ptr<PacketBytes[]>  mByteRing;
    std::atomic<std::uint64_t>      mHead{0};
    std::atomic<std::uint64_t>      mByteHead{0};
    std::atomic<std::uint64_t>      mSeq{0};
    std::atomic<std::uint64_t>      mPeerCount{0};
    std::atomic<std::uint64_t>      mDenied{0};
    std::atomic<std::uint64_t>      mRecvRecords{0};
    std::atomic_bool                mCapture{false};
    std::atomic_bool                mSendHooked{false};
    std::atomic_bool                mPeerHooked{false};
    std::atomic_bool                mSendEnabled{false};
    std::atomic_bool                mRecvEnabled{false};
    std::array<std::atomic_bool, kRecvAnchorCount>           mRecvHooked{};
    std::array<std::atomic<std::uint64_t>, kRecvAnchorCount> mRecvHits{};
    std::array<std::int32_t, 4>     mSerializeIds{};
    std::size_t                     mSerializeCount = 0;
    std::array<std::int32_t, 64>    mIds{};
    std::size_t                     mIdCount = 0;
    std::atomic<std::uint32_t>      mSample{1};
};

} // namespace probe
