#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <memory>

#include <nlohmann/json.hpp>

#include "mod/AtomicRing.h"
#include "mod/CallbackGate.h"
#include "mod/PacketTapModel.h"

class Packet;

namespace probe {

// H1 packet tap: identity ring + optional serialized bytes, send and recv.
// Hooks run on the client/server threads, so every field written from a hook is
// a relaxed atomic or a slot published with a release sequence; start/stop and
// the drains run on the pipe thread.
class PacketTap {
public:
    enum class TestFault { None, IdSeh, IdCpp, BytesSeh, BytesCpp };
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
    // Cold-side, explicitly gated by allow_selftest_stress + packet capture.
    bool armTest(TestFault fault);

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
        return mRing ? mRing->head() : 0;
    }
    [[nodiscard]] std::uint64_t recvRecords() const {
        return mRecvRecords.load(std::memory_order_relaxed);
    }
    [[nodiscard]] std::uint64_t peerSends() const {
        return mPeerCount.load(std::memory_order_relaxed);
    }

private:
    using ByteError = detail::PacketByteError;
    using PacketBytes = detail::PacketByteRecord;
    struct PacketRecord {
        std::uint64_t              order    = 0;
        std::uint64_t              tick     = 0;
        std::int32_t               id       = 0;
        std::uint32_t              tid      = 0;
        std::uint8_t               dir      = kSend;
        std::uint8_t               injected = 0;
        std::uint8_t               pad[2]{};
    };

    bool readPacketId(Packet const& packet, std::int32_t& id) noexcept;
    void recordPacket(Packet const& packet, std::int32_t id, std::uint8_t dir, std::uint64_t tick);
    void serializePacket(Packet const& packet, PacketRecord const& record) noexcept;
    void injectTestFault(bool bytes);

    static constexpr std::size_t kRing     = 1u << 14;
    static constexpr std::size_t kByteRing = 1u << 12;
    using RecordRing = detail::AtomicRing<PacketRecord, kRing>;
    using ByteRing   = detail::AtomicRing<PacketBytes, kByteRing>;
    std::unique_ptr<RecordRing>      mRing;
    std::unique_ptr<ByteRing>        mByteRing;
    std::atomic<std::uint64_t>      mSeq{0};
    std::atomic<std::uint64_t>      mPeerCount{0};
    std::atomic<std::uint64_t>      mDenied{0};
    std::atomic<std::uint64_t>      mRecvRecords{0};
    std::atomic<std::uint64_t>      mIdFaults{0}, mByteFaults{0};
    std::atomic<TestFault>          mTestFault{TestFault::None};
    // Stop closes callback entry before waiting, so it cannot miss a callback
    // paused between entry and an ordinary in-flight counter increment.
    detail::CallbackGate            mWriters;
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
