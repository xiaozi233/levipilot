#pragma once

#include "mod/AtomicRing.h"

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string_view>

namespace probe::detail {

// Bound on the copied violation context. The callback's std::string is live for
// the duration of the call, so only these bytes are kept; nothing from the
// callback is retained past it. BinaryStream's overflow message carries the
// numbers that matter (`checkedNumber`, `mReadPointer`, `buffer Length`), and
// the old 47-byte cap cut them off, so the bound is wide enough for the whole
// sentence.
constexpr std::size_t kViolationContext = 192;

// The disconnect page's error-details block is one long string: the header
// lines plus one line per session id. It is captured whole so the page can be
// reconstructed without a screenshot, which is why this bound is separate (and
// wider) than the violation context.
constexpr std::size_t kViolationDetail = 640;

// One protocol-violation notification, copied out of the callback verbatim.
// Everything is fixed size: no owning string, no Packet or callback pointer.
struct ViolationRecord {
    std::uint64_t seq                        = 0;
    std::uint64_t tick                       = 0;
    std::uint32_t tid                        = 0;
    std::int32_t  response                   = 0; // PacketViolationResponse (3 == TerminateConnection)
    std::int32_t  packetId                   = 0; // MinecraftPacketIds of the offending packet
    std::int32_t  errorValue                 = 0; // std::error_code::value()
    std::uint32_t packetSize                 = 0;
    std::uint32_t pad0                       = 0; // copies into the packet buffer since the last parse
    std::uint32_t assignSize                 = 0; // size the string assign wrote for this buffer
    std::uint64_t category                   = 0; // std::error_category const*, session-local
    std::uint8_t  contextSize                = 0;
    std::uint8_t  contextTruncated           = 0;
    std::uint8_t  synthetic                  = 0; // 1: self-test record, never a real event
    std::uint8_t  source                     = 0; // ViolationSource
    std::uint16_t detailSize                 = 0;
    std::uint8_t  detailTruncated            = 0;
    std::uint8_t  pad1[2]                    = {};
    std::uint8_t  beforeSize                 = 0;
    std::uint8_t  pad2[7]                    = {};
    char          context[kViolationContext] = {}; // violation context / codeword
    char          detail[kViolationDetail]   = {}; // disconnect-page details text
    // Raw bytes before the copied slice. On this local deep-copy path they are
    // unrelated allocation neighbours, not a frame length prefix.
    char before[16] = {};
    // Parent stream candidates are diagnostic addresses only. Static analysis
    // showed that this parse path uses an independent deep copy, not a subspan.
    std::uint64_t parentView    = 0;
    std::uint64_t parentSize    = 0;
    std::uint64_t parentReadPtr = 0;
};

// Full-length text sink. The strings the game hands us are std::string values
// with no fixed bound, and the record rows above only keep a bounded prefix for
// correlation. This captures a whole string without allocating in the callback:
// eight preallocated slots, published with a memcpy and drained off-thread.
constexpr std::size_t kViolationText = 4096;
constexpr std::size_t kTextCapacity  = 512;

struct TextRecord {
    std::uint64_t seq                  = 0;
    std::uint64_t tick                 = 0;
    std::uint32_t len                  = 0;
    std::uint32_t truncated            = 0;
    std::uint8_t  kind                 = 0; // ViolationSource the text came from
    std::uint8_t  pad[7]               = {};
    char          text[kViolationText] = {};
};

static_assert(sizeof(TextRecord) % sizeof(std::uint64_t) == 0);
static_assert(std::is_trivially_copyable_v<TextRecord>);

using TextRing = AtomicRing<TextRecord, kTextCapacity>;

// Where a row came from. Both are read-only observations of the client.
enum class ViolationSource : std::uint8_t {
    Callback = 1, // ClientNetworkHandler::handlePacketViolation
    Screen   = 2, // OreUI::EntryPoints::DisconnectScreen::composeRoute
    Inbound  = 3, // Packet::readNoHeader entry (parse boundary)
};

// Only the calling thread's explicit route probe is synthetic. A nested probe
// must not clear its caller's marker, nor mark a real screen on another thread.
class SyntheticViolationScope {
public:
    SyntheticViolationScope() noexcept { ++sDepth; }
    ~SyntheticViolationScope() { --sDepth; }
    SyntheticViolationScope(SyntheticViolationScope const&)            = delete;
    SyntheticViolationScope& operator=(SyntheticViolationScope const&) = delete;
    static bool              active() noexcept { return sDepth != 0; }

private:
    inline static thread_local unsigned sDepth = 0;
};

static_assert(sizeof(ViolationRecord) % sizeof(std::uint64_t) == 0);
static_assert(std::is_trivially_copyable_v<ViolationRecord>);

// Copy at most kViolationContext-1 bytes (the last byte stays NUL so a dump is
// always printable) and mark truncation instead of growing anything.
inline void copyViolationContext(ViolationRecord& record, std::string_view value) {
    std::size_t const limit = kViolationContext - 1;
    std::size_t const size  = value.size() < limit ? value.size() : limit;
    for (std::size_t index = 0; index < size; ++index) record.context[index] = value[index];
    record.contextSize      = static_cast<std::uint8_t>(size);
    record.contextTruncated = value.size() > limit ? 1 : 0;
}

constexpr std::size_t kViolationCapacity = 256;

inline void copyViolationDetail(ViolationRecord& record, std::string_view value) {
    std::size_t const limit = kViolationDetail - 1;
    std::size_t const size  = value.size() < limit ? value.size() : limit;
    for (std::size_t index = 0; index < size; ++index) record.detail[index] = value[index];
    record.detailSize      = static_cast<std::uint16_t>(size);
    record.detailTruncated = value.size() > limit ? 1 : 0;
}

using ViolationRing = AtomicRing<ViolationRecord, kViolationCapacity>;

// Inbound sizes are recorded far more often than violations, so they get their
// own ring and cursor: a busy second must not push the violation rows out.
constexpr std::size_t kInboundCapacity = 1024;
using InboundRing                      = AtomicRing<ViolationRecord, kInboundCapacity>;

} // namespace probe::detail
