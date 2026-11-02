#include "mod/Internal.h"

#include "mc/client/gui/oreui/DisconnectScreen.h"
#include "mc/deps/core/utility/ReadOnlyBinaryStream.h"

#include <intrin.h>


using namespace probe::detail;

// Read-only watch on the client's protocol-violation boundary.
//
// The wrapped function (role @client_handle_packet_violation in docs/roles) is
// ClientNetworkHandler::handlePacketViolation:
//
//   void(shared_ptr<IPacketSecurityController> const&, std::error_code const&,
//        PacketViolationResponse, MinecraftPacketIds, std::string&&,
//        NetworkIdentifier const&, SubClientId, SubClientId, uint)
//
// `this` plus nine arguments: the first four arrive in registers and the rest on
// the stack, so only a wrapper with the real signature keeps them intact. The
// wrapper copies a fixed-size row and then forwards every argument unchanged.
namespace probe::detail {
namespace {

// Exact ABI. PacketViolationResponse / MinecraftPacketIds are int-backed enums
// and SubClientId is a one-byte enum, so they are spelled as their underlying
// types; std::string&& is a pointer to the caller's string.
using HandleViolationFn = void (*)(
    void*                  self,
    void*                  controller,
    std::error_code const* errorCode,
    int                    response,
    int                    packetId,
    std::string*           context,
    void*                  netId,
    unsigned char          clientSubId,
    unsigned char          senderSubId,
    unsigned int           packetSize
);

// Built once per disconnect to render the error page; the reason, the codeword
// and the detail text all become its arguments, so this is where the client
// states *why* it left -- without reading its UI.
using ComposeRouteFn = std::string (*)(
    int,
    std::string const&,
    std::string const&,
    std::string const&,
    std::string const&,
    std::string const&,
    std::string const&
);

// Preloader retains these output slots and updates them when other hooks in
// the chain change (pl/Hook.h). They must outlive the installation. Use the
// same persistent-slot contract as LL's typed hook macros, never a stack local
// copied into a separate atomic after hook() has resumed the other threads.
ll::memory::FuncPtr             gRouteOrigin = nullptr;
ll::memory::FuncPtr             gOrigin = nullptr;
std::atomic_bool               gInstalled{false};
std::atomic_bool               gScreenInstalled{false};
std::mutex                     gInstallMutex; // cold start/stop only
ViolationRing                  gRing;
std::atomic_bool               gRingReady{false};
std::atomic<std::uint64_t>     gHits{0};
std::atomic<std::uint64_t>     gSynthetic{0};
TextRing                       gTextRing;
std::atomic_bool              gTextReady{false};
std::atomic<std::uint64_t>     gObservationFaults{0};
std::atomic<std::uint64_t>     gTarget{0};
// The parse loop's entry gives the object whose member string carries the
// packet bytes (its owner is *a1). Exporting that pointer lets a debug-register
// watchpoint be placed on the member's size field to catch its writer.
using LoopEntryFn = void (*)(void*, void*);
ll::memory::FuncPtr        gLoopOrigin = nullptr;
std::atomic_bool           gLoopInstalled{false};
std::atomic<std::uint64_t> gNetContext{0};
// The assign that fills the packet string also writes its size. Capturing the
// size right after the call, filtered on the packet string's address, gives the
// length that the writer intended for this packet.
using AssignFn = std::int64_t (*)(void*, void*, void*, void*);
using DispatchFn = std::int64_t (*)(void*, void*);
ll::memory::FuncPtr        gDispatchOrigin = nullptr;
std::atomic_bool           gDispatchInstalled{false};
constexpr std::uint8_t kDispatchKind = 10;
constexpr std::uint8_t kAssignCallerKind = 11;
ll::memory::FuncPtr        gAssignOrigin = nullptr;
std::atomic_bool           gAssignInstalled{false};
std::atomic<std::uint64_t> gPacketString{0};
std::atomic<std::uint32_t> gAssignSize{0};
std::atomic<std::uint64_t> gAssignCount{0};
std::atomic<std::uint64_t> gAssignCalls{0};

void detourLoopEntry(void* a1, void* a2) {
    for (int spin = 0; spin < 200000; ++spin) {
        if (gLoopOrigin != nullptr) break;
        YieldProcessor();
    }
    // *a1 is the object for whichever direction reached the loop first, and the
    // directions do not share it. Classify by the bytes already buffered: an
    // id-144 head (`90 01`) is uplink, everything else is downlink, and only
    // the downlink object is what the packet-violation investigation needs.
    std::uint64_t owner = 0;
    // Once the downlink context is known this hook does no memory access at all:
    // every read here is on the parse path and one of them triggered the hang.
    if (gPacketString.load(std::memory_order_relaxed) == 0 && a1 != nullptr
        && readMemory(reinterpret_cast<std::uint64_t>(a1), &owner, sizeof(owner)) && owner != 0) {
        std::uint64_t const stringAddr = owner + 0x128;
        std::uint64_t       dataPtr    = stringAddr;
        std::uint64_t       field      = 0;
        if (readMemory(stringAddr, &field, sizeof(field)) && field >= 0x10000
            && field < 0x00007FFFFFFFFFFFull) {
            dataPtr = field;
        }
        std::uint8_t head[2]{};
        if (readMemory(dataPtr, head, sizeof(head))) {
            bool const uplink = head[0] == 0x90 && head[1] == 0x01;
            if (!uplink) {
                gNetContext.store(owner, std::memory_order_relaxed);
                gPacketString.store(stringAddr, std::memory_order_relaxed);
            }
        }
    }
    if (gLoopOrigin == nullptr) return;
    reinterpret_cast<LoopEntryFn>(gLoopOrigin)(a1, a2);
}

// Inbound parse boundary. The machine-level shape of Packet::readNoHeader in
// this build is five pointer-sized arguments with no register return
// (rcx = packet, rdx = stream, r8/r9, stack = subid); the typed wrapper that
// assumed a hidden return pointer crashed twice. This wrapper forwards all five
// verbatim and only *reads* a candidate stream through readMemory behind a
// validation guard, so a wrong guess cannot fault.
using ReadBoundary = void (*)(void*, void*, void*, void*, void*);
ll::memory::FuncPtr        gInboundOrigin = nullptr;
std::atomic_bool           gInboundInstalled{false};
InboundRing                gInboundRing;
std::atomic_bool           gInboundReady{false};
std::atomic<std::uint64_t> gInboundHits{0};
std::atomic<std::uint64_t> gInboundSampled{0};
// Sampling stride for the parse boundary. 1:4 keeps a long history under
// normal play; 1:1 is for a reproduction run, where the packet before the
// disconnect matters more than the history behind it.
std::atomic<std::uint32_t> gInboundMask{3};
// First-error freeze: once the violation callback fires, stop publishing to the
// inbound ring. The failing slice was published at readNoHeader entry, i.e.
// before the callback; without the freeze the teardown traffic (DisconnectPacket
// and the error strings) keeps writing and pushes that row out of the ring.
std::atomic_bool gInboundFrozen{false};
constexpr std::uint8_t kMemmoveKind = 9;
using MemmoveFn = void* (*)(void*, void const*, std::size_t);
ll::memory::FuncPtr        gMemcpyOrigin = nullptr;
std::atomic_bool           gMemcpyInstalled{false};
ll::memory::FuncPtr        gMemmoveOrigin = nullptr;
std::atomic_bool           gMemmoveInstalled{false};
std::atomic_bool           gMemmoveEnabled{false};
std::atomic<std::uint64_t> gPacketBegin{0};


std::atomic<std::uint32_t> gMemmoveFilter{0}; // 0 = record every copy
// Write counter keyed by buffer address: the downlink and uplink buffers
// interleave, so a single counter would be cleared by the other direction.
struct CopySlot {
    std::atomic<std::uint64_t> addr{0};
    std::atomic<std::uint32_t> count{0};
    std::atomic<std::uint32_t> assignSize{0};
};
constexpr std::size_t kCopySlots = 4;
CopySlot gCopySlots[kCopySlots];

void noteCopy(std::uint64_t dst) {
    for (std::size_t index = 0; index < kCopySlots; ++index) {
        std::uint64_t expected = gCopySlots[index].addr.load(std::memory_order_relaxed);
        if (expected == dst) {
            gCopySlots[index].count.fetch_add(1, std::memory_order_relaxed);
            return;
        }
        if (expected == 0) {
            std::uint64_t empty = 0;
            if (gCopySlots[index].addr.compare_exchange_strong(empty, dst,
                                                               std::memory_order_relaxed)) {
                gCopySlots[index].count.fetch_add(1, std::memory_order_relaxed);
                return;
            }
            if (gCopySlots[index].addr.load(std::memory_order_relaxed) == dst) {
                gCopySlots[index].count.fetch_add(1, std::memory_order_relaxed);
                return;
            }
        }
    }
}

void noteAssign(std::uint64_t buffer, std::uint32_t size) {
    for (std::size_t index = 0; index < kCopySlots; ++index) {
        std::uint64_t expected = gCopySlots[index].addr.load(std::memory_order_relaxed);
        if (expected == buffer) {
            gCopySlots[index].assignSize.store(size, std::memory_order_relaxed);
            return;
        }
        if (expected == 0) {
            std::uint64_t empty = 0;
            if (gCopySlots[index].addr.compare_exchange_strong(empty, buffer,
                                                               std::memory_order_relaxed)) {
                gCopySlots[index].assignSize.store(size, std::memory_order_relaxed);
                return;
            }
        }
    }
}

std::uint32_t takeAssignSize(std::uint64_t buffer) {
    for (std::size_t index = 0; index < kCopySlots; ++index) {
        if (gCopySlots[index].addr.load(std::memory_order_relaxed) == buffer) {
            return gCopySlots[index].assignSize.load(std::memory_order_relaxed);
        }
    }
    return 0;
}

std::uint32_t takeCopies(std::uint64_t buffer) {
    for (std::size_t index = 0; index < kCopySlots; ++index) {
        if (gCopySlots[index].addr.load(std::memory_order_relaxed) == buffer) {
            return gCopySlots[index].count.exchange(0, std::memory_order_relaxed);
        }
    }
    return 0;
}
std::atomic<std::uint64_t> gInboundBytes{0};

bool initializeInboundRing() {
    if (!gInboundReady.load(std::memory_order_acquire)) {
        if (!gInboundRing.initialize()) return false;
        gInboundReady.store(true, std::memory_order_release);
    }
    return true;
}

// ReadOnlyBinaryStream holds its view after the vptr. The pair at +0x28/+0x30
// is the slice extent; the parser reads forward from +0x38 and latches an
// overflow flag at +0x40. Both extents are read as raw qwords and interpreted
// as (pointer, size) first and (begin, end) second: only readMemory is used, so
// a wrong interpretation costs a failed read, never a fault.
struct StreamProbe {
    std::uint64_t object   = 0;
    std::uint64_t begin    = 0;
    std::uint64_t size     = 0;
    std::uint64_t readPtr  = 0;
    std::uint8_t  overflow = 0;
};

bool plausiblePointer(std::uint64_t value) {
    return value >= 0x10000 && value < 0x00007FFFFFFFFFFFull;
}

bool probeStream(std::uint64_t candidate, StreamProbe& out) {
    if (!plausiblePointer(candidate)) return false;
    std::uint64_t first = 0;
    std::uint64_t second = 0;
    out.object = candidate;
    if (!readMemory(candidate + 0x28, &first, sizeof(first))) return false;
    if (!readMemory(candidate + 0x30, &second, sizeof(second))) return false;
    if (!readMemory(candidate + 0x38, &out.readPtr, sizeof(out.readPtr))) return false;
    if (!readMemory(candidate + 0x40, &out.overflow, sizeof(out.overflow))) return false;
    if (plausiblePointer(first) && second >= 1 && second <= (1ull << 20)) {
        out.begin = first;
        out.size  = second;
    } else if (plausiblePointer(first) && plausiblePointer(second) && second > first
               && (second - first) <= (1ull << 20)) {
        out.begin = first;
        out.size  = second - first;
    } else {
        return false;
    }
    return out.readPtr <= out.size;
}

// Copy a bounded prefix of the packet slice verbatim. The parse stream starts
// at the packet id; this local deep-copy path has no frame length prefix.
std::uint8_t copySlice(std::uint64_t address, std::uint64_t limit, char* into,
                       std::size_t cap = 24) {
    if (!plausiblePointer(address) || limit == 0) return 0;
    std::size_t const want = limit < cap ? static_cast<std::size_t>(limit) : cap;
    return readMemory(address, into, want) ? static_cast<std::uint8_t>(want) : 0;
}

void publishInbound(int argIndex, StreamProbe const& probe, StreamProbe const* parent,
                    std::uint64_t auxPointer) {
    if (!gInboundReady.load(std::memory_order_acquire)) return;
    auto reservation  = gInboundRing.reserve();
    ViolationRecord record{};
    record.seq        = reservation.index() + 1;
    record.tick       = gWatchTick.load(std::memory_order_relaxed);
    record.tid        = GetCurrentThreadId();
    record.source     = static_cast<std::uint8_t>(ViolationSource::Inbound);
    record.packetId   = argIndex; // which argument carried the stream
    record.response   = probe.overflow ? 1 : 0;
    record.packetSize = static_cast<std::uint32_t>(probe.size);
    record.errorValue = static_cast<std::int32_t>(probe.readPtr);
    record.category   = probe.begin;
    record.pad0       = takeCopies(probe.begin);
    record.assignSize = takeAssignSize(probe.begin);
    gPacketBegin.store(probe.begin, std::memory_order_relaxed);
    record.contextSize = copySlice(probe.begin, probe.size, record.context,
                                   kViolationContext - 1);
    record.contextTruncated = probe.size > kViolationContext - 1 ? 1 : 0;
    // Bytes at the fourth boundary argument ([rsi+0x208] at the call site): if
    // that is the batch buffer or a container over it, these bytes show it.
    record.detailSize = copySlice(auxPointer, 24, record.detail);
    // Preserve allocation neighbours as raw diagnostic bytes only. Static
    // analysis showed a deep copy here, so these bytes are not framing data.
    if (probe.begin >= sizeof(record.before)) {
        record.beforeSize = copySlice(probe.begin - sizeof(record.before),
                                      sizeof(record.before), record.before);
    }
    if (parent != nullptr) {
        record.parentView    = parent->begin;
        record.parentSize    = parent->size;
        record.parentReadPtr = parent->readPtr;
    }
    gInboundBytes.fetch_add(record.contextSize, std::memory_order_relaxed);
    (void)reservation.publish(record);
}

void detourReadBoundary(void* a, void* b, void* c, void* d, void* e) {
    for (int spin = 0; spin < 200000; ++spin) {
        if (gInboundOrigin != nullptr) break;
        YieldProcessor();
    }
    // Frozen after the first violation: keep the ring exactly as it was when the
    // callback fired, so the failing slice cannot be overwritten by teardown.
    if (gInboundFrozen.load(std::memory_order_acquire)) {
        if (gInboundOrigin == nullptr) return;
        reinterpret_cast<ReadBoundary>(gInboundOrigin)(a, b, c, d, e);
        return;
    }
    auto const hit = gInboundHits.fetch_add(1, std::memory_order_relaxed) + 1;
    if ((hit & gInboundMask.load(std::memory_order_relaxed)) == 0) {
        void* candidates[4] = {a, b, c, d};
        StreamProbe probes[4];
        bool        valid[4] = {false, false, false, false};
        bool        used[4]  = {false, false, false, false};
        for (int index = 0; index < 4; ++index) {
            valid[index] = probeStream(reinterpret_cast<std::uint64_t>(candidates[index]),
                                       probes[index]);
        }
        for (int index = 0; index < 4; ++index) {
            if (!valid[index] || used[index]) continue;
            // A parent is a stream whose view plus read pointer is exactly where
            // this slice begins: the slice is a subspan of it.
            int         parentIndex = -1;
            for (int other = 0; other < 4; ++other) {
                if (other == index || !valid[other] || used[other]) continue;
                std::uint64_t const at = probes[other].begin + probes[other].readPtr;
                std::uint64_t const start = probes[index].begin;
                if (at <= start && start - at <= 8
                    && probes[other].size > probes[index].size) {
                    parentIndex = other;
                    break;
                }
            }
            gInboundSampled.fetch_add(1, std::memory_order_relaxed);
            publishInbound(index, probes[index],
                           parentIndex >= 0 ? &probes[parentIndex] : nullptr,
                           reinterpret_cast<std::uint64_t>(candidates[3]));
            used[index] = true;
            if (parentIndex >= 0) used[parentIndex] = true;
            break;
        }
    }
    if (gInboundOrigin == nullptr) return;
    reinterpret_cast<ReadBoundary>(gInboundOrigin)(a, b, c, d, e);
}

// Copy a whole game string into a preallocated slot. No allocation, no JSON and
// no lock: the callback only memcpys, and the drain formats off-thread.
void publishText(std::uint8_t kind, std::string_view value) {
    if (!gTextReady.load(std::memory_order_acquire)) return;
    auto reservation = gTextRing.reserve();
    TextRecord record{};
    record.tick   = gWatchTick.load(std::memory_order_relaxed);
    record.kind   = kind;
    std::size_t const limit = kViolationText - 1;
    std::size_t const size  = value.size() < limit ? value.size() : limit;
    for (std::size_t index = 0; index < size; ++index) record.text[index] = value[index];
    record.len       = static_cast<std::uint32_t>(size);
    record.truncated = value.size() > limit ? 1 : 0;
    (void)reservation.publish(record);
}

bool initializeTextRing() {
    if (!gTextReady.load(std::memory_order_acquire)) {
        if (!gTextRing.initialize()) return false;
        gTextReady.store(true, std::memory_order_release);
    }
    return true;
}

// The packet bytes reach the reused string through one memmove whose target is
// that string's buffer. Recording (source, length, source neighbourhood) for
// exactly those copies is what separates "the length came from a prefix" from
// "the length is the buffer's". The probe only compares an address per call and
// forwards everything untouched.
struct MemmoveBlob {
    std::uint64_t src    = 0;
    std::uint64_t len    = 0;
    char          before[16] = {};
    char          after[16]  = {};
};

std::int64_t detourAssign(void* target, void* a2, void* a3, void* a4) {
    std::int64_t result = 0;
    if (gAssignOrigin != nullptr) {
        result = reinterpret_cast<AssignFn>(gAssignOrigin)(target, a2, a3, a4);
    }
    gAssignCalls.fetch_add(1, std::memory_order_relaxed);
    // The prologue is `mov rdi, rdx`, so the string is the *second* argument
    // (the first one is copied into rbx and used elsewhere).
    std::uint64_t const wanted = gPacketString.load(std::memory_order_relaxed);
    if (wanted != 0 && reinterpret_cast<std::uint64_t>(a2) == wanted) {
        // No readMemory here: this runs on the parse path and the guarded read
        // can deadlock against a lock the caller already holds. `wanted` was
        // matched against the known packet string, so a plain load is safe.
        std::uint32_t size = *reinterpret_cast<std::uint32_t*>(wanted + 0x10);
        gAssignSize.store(size, std::memory_order_relaxed);
        std::uint64_t field = *reinterpret_cast<std::uint64_t*>(wanted);
        std::uint64_t dataPtr =
            (field >= 0x10000 && field < 0x00007FFFFFFFFFFFull) ? field : wanted;
        noteAssign(dataPtr, size);
        // Who asked for this write: the return address is the code right after
        // the call, i.e. the function that computed the length. Zero memory
        // access, so it stays safe on this path.
        MemmoveBlob blob{};
        blob.src = reinterpret_cast<std::uint64_t>(_ReturnAddress());
        blob.len = size;
        // Keep both raw arguments so the caller's own decoding can be checked
        // against the size that was actually written.
        std::uint64_t const arg1 = reinterpret_cast<std::uint64_t>(a4); // r9
        std::uint64_t const arg2 = reinterpret_cast<std::uint64_t>(a3);
        std::memcpy(blob.before, &arg1, sizeof(arg1));
        std::memcpy(blob.after, &arg2, sizeof(arg2));
        publishText(kAssignCallerKind, std::string_view(reinterpret_cast<char const*>(&blob),
                                                        sizeof(blob)));
        gAssignCount.fetch_add(1, std::memory_order_relaxed);
    }
    return result;
}

std::int64_t detourDispatch(void* a1, void* a2) {
    // The layer above the string assign: record both arguments and, later on the
    // pipe thread, the first words of the second one -- if it carries a
    // {pointer, length} pair, that is where the packet length is decided.
    MemmoveBlob blob{};
    blob.src = reinterpret_cast<std::uint64_t>(a1);
    blob.len = reinterpret_cast<std::uint64_t>(a2);
    publishText(kDispatchKind, std::string_view(reinterpret_cast<char const*>(&blob),
                                                sizeof(blob)));
    if (gDispatchOrigin == nullptr) return 0;
    return reinterpret_cast<DispatchFn>(gDispatchOrigin)(a1, a2);
}

void recordCopy(void* dst, void const* src, std::size_t length) {
    static thread_local bool busy = false;
    if (busy) return;
    busy = true;
    auto const begin = gPacketBegin.load(std::memory_order_relaxed);
    if (gMemmoveEnabled.load(std::memory_order_relaxed) && begin != 0
        && reinterpret_cast<std::uint64_t>(dst) == begin && length != 0 && length < 4096) {
        noteCopy(begin);
        auto const filter = gMemmoveFilter.load(std::memory_order_relaxed);
        // No readMemory here either: the filter is 0 for this probe (record
        // every copy) and the source bytes are read at drain time instead.
        (void)filter;
        MemmoveBlob blob{};
        blob.src = reinterpret_cast<std::uint64_t>(src);
        blob.len = length;
        publishText(kMemmoveKind, std::string_view(reinterpret_cast<char const*>(&blob),
                                                   sizeof(blob)));
    }
    busy = false;
}

void* detourMemmove(void* dst, void const* src, std::size_t length) {
    recordCopy(dst, src, length);
    if (gMemmoveOrigin == nullptr) return dst;
    return reinterpret_cast<MemmoveFn>(gMemmoveOrigin)(dst, src, length);
}

void* detourMemcpy(void* dst, void const* src, std::size_t length) {
    recordCopy(dst, src, length);
    if (gMemcpyOrigin == nullptr) return dst;
    return reinterpret_cast<MemmoveFn>(gMemcpyOrigin)(dst, src, length);
}

bool initializeRing() {
    if (!gRingReady.load(std::memory_order_acquire)) {
        if (!gRing.initialize()) return false;
        gRingReady.store(true, std::memory_order_release);
    }
    return true;
}

char const* violationLabel(int response) {
    switch (response) {
    case 0:
        return "none";
    case 1:
        return "warning";
    case 2:
        return "final_warning";
    case 3:
        return "terminate_connection";
    default:
        return "unknown";
    }
}

// Hot path: fixed writes into the published ring slot. No allocation, no JSON,
// no locking, and nothing from the callback outlives the call.
void publishViolation(int response, int packetId, std::error_code const* errorCode,
                      std::string const* context, unsigned int packetSize, bool synthetic) {
    if (!gRingReady.load(std::memory_order_acquire)) {
        return;
    }
    ViolationRecord record{};
    record.tick       = gWatchTick.load(std::memory_order_relaxed);
    record.tid        = GetCurrentThreadId();
    record.response   = response;
    record.packetId   = packetId;
    record.packetSize = packetSize;
    try {
        if (!guardedGameRead([&] {
            if (errorCode != nullptr) {
                record.errorValue = errorCode->value();
                record.category = reinterpret_cast<std::uint64_t>(&errorCode->category());
            }
            if (context != nullptr) copyViolationContext(record, *context);
        })) {
            gObservationFaults.fetch_add(1, std::memory_order_relaxed);
            return;
        }
    } catch (...) {
        gObservationFaults.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    if (context != nullptr) publishText(static_cast<std::uint8_t>(ViolationSource::Callback), *context);
    record.synthetic = synthetic ? 1 : 0;
    record.source    = static_cast<std::uint8_t>(ViolationSource::Callback);
    gHits.fetch_add(1, std::memory_order_relaxed);
    if (synthetic) gSynthetic.fetch_add(1, std::memory_order_relaxed);
    auto reservation = gRing.reserve();
    record.seq = reservation.index() + 1;
    reservation.publish(record);
}

void publishDisconnectScreen(int reason, std::string const& codeword,
                             std::string const& details) {
    if (!gRingReady.load(std::memory_order_acquire)) return;
    ViolationRecord record{};
    record.tick       = gWatchTick.load(std::memory_order_relaxed);
    record.tid        = GetCurrentThreadId();
    record.response   = reason;
    record.packetId   = -1;
    record.source     = static_cast<std::uint8_t>(ViolationSource::Screen);
    record.synthetic = SyntheticViolationScope::active() ? 1 : 0;
    try {
        if (!guardedGameRead([&] {
            copyViolationContext(record, codeword);
            copyViolationDetail(record, details);
        })) {
            gObservationFaults.fetch_add(1, std::memory_order_relaxed);
            return;
        }
    } catch (...) {
        gObservationFaults.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    publishText(static_cast<std::uint8_t>(ViolationSource::Screen), details);
    gHits.fetch_add(1, std::memory_order_relaxed);
    if (record.synthetic) gSynthetic.fetch_add(1, std::memory_order_relaxed);
    auto reservation = gRing.reserve();
    record.seq = reservation.index() + 1;
    reservation.publish(record);
}

std::string detourComposeRoute(int reason, std::string const& title,
                               std::string const& body, std::string const& codeword,
                               std::string const& details, std::string const& serverMessage,
                               std::string const& serverName) {
    publishDisconnectScreen(reason, codeword, details);
    return reinterpret_cast<ComposeRouteFn>(gRouteOrigin)(
        reason, title, body, codeword, details, serverMessage, serverName
    );
}

void detourHandleViolation(void* self, void* controller, std::error_code const* errorCode,
                           int response, int packetId, std::string* context, void* netId,
                           unsigned char clientSubId, unsigned char senderSubId,
                           unsigned int packetSize) {
    if (response != 0) {
        gInboundFrozen.store(true, std::memory_order_release);
    }
    publishViolation(response, packetId, errorCode, context, packetSize, false);
    reinterpret_cast<HandleViolationFn>(gOrigin)(
        self, controller, errorCode, response, packetId, context, netId, clientSubId,
        senderSubId, packetSize
    );
}

nlohmann::json errorJson(char const* reason) {
    return nlohmann::json{{"event", "error"}, {"reason", reason}};
}

// Cold path: hex for the inbound slice bytes (binary, not printable).
std::string hexText(char const* data, std::size_t size) {
    static char const* digits = "0123456789abcdef";
    std::string out;
    out.reserve(size * 2);
    for (std::size_t index = 0; index < size && index < kViolationDetail; ++index) {
        auto const byte = static_cast<unsigned char>(data[index]);
        out.push_back(digits[byte >> 4]);
        out.push_back(digits[byte & 0xF]);
    }
    return out;
}

// Cold path only: this runs in the drain, never inside the callback.
std::string printableText(char const* data, std::size_t size) {
    std::string out;
    out.reserve(size);
    for (std::size_t index = 0; index < size && index < kViolationDetail; ++index) {
        auto const uch = static_cast<unsigned char>(data[index]);
        out.push_back(uch >= 0x20 && uch < 0x7F ? data[index] : '.');
    }
    return out;
}

// Cold path: mirror PacketTap::drain so callers can reuse the same cursor idiom.
template <class Ring>
nlohmann::json drainRing(Ring& ring, std::size_t capacity, std::uint64_t since,
                         std::size_t limit, char const* event) {
    std::uint64_t const head   = ring.head();
    std::uint64_t const oldest = head > capacity ? head - capacity : 0;
    std::uint64_t       start  = since;
    std::uint64_t       lost   = 0;
    if (start < oldest) {
        lost  = oldest - start;
        start = oldest;
    }
    if (start > head) start = head;
    if (limit == 0) limit = 256;
    nlohmann::json rows  = nlohmann::json::array();
    std::size_t    count = 0;
    std::uint64_t  cursor = start;
    for (std::uint64_t index = start; index < head && count < limit; ++index) {
        ViolationRecord record{};
        auto const      read = ring.read(index, record);
        if (read == Ring::ReadResult::pending) {
            cursor = index;
            break;
        }
        if (read != Ring::ReadResult::ready) {
            ++lost;
            cursor = index + 1;
            continue;
        }
        rows.push_back(nlohmann::json{
            {"index",      index + 1                                          },
            {"seq",        record.seq                                         },
            {"tick",       record.tick                                        },
            {"tid",        record.tid                                         },
            {"response",   record.response                                    },
            {"verdict",    record.source == static_cast<std::uint8_t>(ViolationSource::Screen)
                               ? "disconnect" : violationLabel(record.response)},
            {"disconnect_reason", record.source == static_cast<std::uint8_t>(ViolationSource::Screen)
                                      ? nlohmann::json(record.response) : nlohmann::json(nullptr)},
            {"packet_id",  record.packetId                                    },
            {"packet_size", record.packetSize                                 },
            {"error_value", record.errorValue                                 },
            {"category",   record.category == 0 ? nlohmann::json(nullptr)
                                                : nlohmann::json(record.category)},
            {"copies",     record.pad0                                      },
            {"assign_size", record.assignSize                               },
            {"source",     record.source == 3 ? "inbound"
                           : record.source == 2 ? "screen" : "callback"          },
            {"context_hex", hexText(record.context, record.contextSize)       },
            {"detail_hex",  hexText(record.detail, record.detailSize)         },
            {"before_hex",  hexText(record.before, record.beforeSize)         },
            {"parent_view",    record.parentView                              },
            {"parent_size",    record.parentSize                              },
            {"parent_read_ptr", record.parentReadPtr                          },
            {"context",    printableText(record.context, record.contextSize)  },
            {"detail",     printableText(record.detail, record.detailSize)    },
            {"detail_truncated", record.detailTruncated != 0                  },
            {"context_size", record.contextSize                               },
            {"context_truncated", record.contextTruncated != 0                },
            {"synthetic",  record.synthetic != 0                              },
        });
        ++count;
        cursor = index + 1;
    }
    return nlohmann::json{
        {"event", event},
        {"since", since},
        {"next",  cursor},
        {"head",  head},
        {"lost",  lost},
        {"count", count},
        {"hits",  std::move(rows)},
    };
}

// ---------------------------------------------------------------------------
// Send-path suspect probe. `0x140D7CFC0(connection, std::string const&)` is the
// enqueue; healthy traffic only carries 3/14/15/17/18/19/21/22/23/25/27/95-byte
// frames, while every failing capture has carried something else (55 bytes: a
// 6-byte foreign prefix glued in front of a whole 187 frame). The game sometimes
// dies right after the violation and the in-memory rings die with it, so this
// writes the hit to `<modDir>/suspect-<pid>.jsonl` at the moment it happens.
// The dump only runs for lengths outside the healthy set, so the hot path stays
// a couple of loads and a compare in the normal case.
// ---------------------------------------------------------------------------
using SendProbeFn = void (*)(void*, void*, void*, void*);
ll::memory::FuncPtr        gSendOrigin    = nullptr;
std::uint64_t              gSendAddress   = 0;
std::atomic_bool           gSendInstalled{false};
std::atomic<std::uint64_t> gSendHits{0};
std::atomic<std::uint64_t> gSendDumps{0};
char                       gSuspectPath[512]{};

bool healthySendSize(std::uint32_t size) {
    switch (size) {
    case 3:
    case 14:
    case 15:
    case 17:
    case 18:
    case 19:
    case 21:
    case 22:
    case 23:
    case 25:
    case 27:
    case 35: // 187 with a single layer
    case 94:
    case 95:
    case 96:
        return true;
    default:
        return false;
    }
}

// Fixed ring of the last serialize calls: no allocation, no locks, one writer.
struct SendRec {
    std::uint64_t tick;
    std::uint64_t net;
    std::uint32_t tid;
    std::uint32_t before;
    std::uint32_t after;
};
constexpr std::uint32_t kSendRingSize = 64;
SendRec                 gSendRing[kSendRingSize]{};
std::atomic<std::uint32_t> gSendRingHead{0};

void pushSendRec(std::uint64_t net, std::uint32_t before, std::uint32_t after) {
    std::uint32_t const slot = gSendRingHead.fetch_add(1, std::memory_order_relaxed)
                               % kSendRingSize;
    gSendRing[slot] = SendRec{
        gWatchTick.load(std::memory_order_relaxed), net, GetCurrentThreadId(), before, after
    };
}

void dumpSendRing() {
    if (gSuspectPath[0] == 0) {
        return;
    }
    HANDLE handle = CreateFileA(
        gSuspectPath,
        FILE_APPEND_DATA,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        nullptr,
        OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        nullptr
    );
    if (handle == INVALID_HANDLE_VALUE) {
        return;
    }
    std::uint32_t const head = gSendRingHead.load(std::memory_order_relaxed);
    std::uint32_t const count = head < kSendRingSize ? head : kSendRingSize;
    char line[256];
    for (std::uint32_t index = 0; index < count; ++index) {
        SendRec const& rec = gSendRing[(head - count + index) % kSendRingSize];
        int const      length = std::snprintf(
            line,
            sizeof line,
            "{\"kind\":\"ring\",\"tick\":%llu,\"tid\":%lu,\"net\":\"%p\","
            "\"before\":%u,\"after\":%u}\n",
            static_cast<unsigned long long>(rec.tick),
            static_cast<unsigned long>(rec.tid),
            reinterpret_cast<void*>(rec.net),
            rec.before,
            rec.after
        );
        DWORD written = 0;
        if (length > 0) {
            WriteFile(handle, line, static_cast<DWORD>(length), &written, nullptr);
        }
    }
    CloseHandle(handle);
}

void writeSuspectFrame(void* str, std::uint32_t size) {
    if (gSuspectPath[0] == 0) {
        return;
    }
    auto const* data = *reinterpret_cast<std::uint8_t const**>(str);
    if (!plausiblePointer(reinterpret_cast<std::uint64_t>(data))) {
        return;
    }
    HANDLE handle = CreateFileA(
        gSuspectPath,
        FILE_APPEND_DATA,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        nullptr,
        OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        nullptr
    );
    if (handle == INVALID_HANDLE_VALUE) {
        return;
    }
    static char const* digits = "0123456789abcdef";
    char               head[41]{};
    std::uint32_t const shown = size < 20 ? size : 20;
    for (std::uint32_t index = 0; index < shown; ++index) {
        head[index * 2]     = digits[data[index] >> 4];
        head[index * 2 + 1] = digits[data[index] & 0xF];
    }
    void*  frames[8]{};
    USHORT count = RtlCaptureStackBackTrace(1, 8, frames, nullptr);
    char   line[768];
    int const length = std::snprintf(
        line,
        sizeof line,
        "{\"tick\":%llu,\"tid\":%lu,\"size\":%u,\"count\":%u,\"head\":\"%s\","
        "\"stack\":[\"%p\",\"%p\",\"%p\",\"%p\",\"%p\"]}\n",
        static_cast<unsigned long long>(gWatchTick.load(std::memory_order_relaxed)),
        static_cast<unsigned long>(GetCurrentThreadId()),
        size,
        static_cast<unsigned>(count),
        head,
        count > 0 ? frames[0] : nullptr,
        count > 1 ? frames[1] : nullptr,
        count > 2 ? frames[2] : nullptr,
        count > 3 ? frames[3] : nullptr,
        count > 4 ? frames[4] : nullptr
    );
    DWORD written = 0;
    if (length > 0) {
        WriteFile(handle, line, static_cast<DWORD>(length), &written, nullptr);
    }
    CloseHandle(handle);
}

void detourSend(void* net, void* string, void* peer, void* flags) {
    gSendHits.fetch_add(1, std::memory_order_relaxed);
    if (string != nullptr && plausiblePointer(reinterpret_cast<std::uint64_t>(string))) {
        auto const size =
            *reinterpret_cast<std::uint32_t*>(reinterpret_cast<char*>(string) + 0x10);
        auto const  data = *reinterpret_cast<std::uint8_t const**>(string);
        bool const  uplink = plausiblePointer(reinterpret_cast<std::uint64_t>(data))
                           && *data == 0x90; // 0x90 0x01 frames vary 94..96 by content
        if (size >= 24 && size <= 120 && !uplink && !healthySendSize(size)
            && gSendDumps.load(std::memory_order_relaxed) < 2000) {
            gSendDumps.fetch_add(1, std::memory_order_relaxed);
            writeSuspectFrame(string, size);
            dumpSendRing();
        }
    }
    if (gSendOrigin != nullptr) {
        reinterpret_cast<SendProbeFn>(gSendOrigin)(net, string, peer, flags);
    }
}

// Second half of the probe: `0x1416B0490(netSystem, packet, peer, mode)` clears
// `netSystem+0x1B0`, writes "Header Data" and the body, then hands that string to
// the enqueue. Reading the string length on both sides of the call answers the
// open question directly: entering with a non-empty buffer and leaving with a
// glued length means two writers share the buffer.
using SerializeFn = std::uint64_t (*)(void*, void*, void*, void*);
ll::memory::FuncPtr gSerializeOrigin = nullptr;
std::uint64_t       gSerializeAddress = 0;
std::atomic_bool    gSerializeInstalled{false};

std::uint32_t frameLength(void* netSystem) {
    if (netSystem == nullptr || !plausiblePointer(reinterpret_cast<std::uint64_t>(netSystem))) {
        return 0;
    }
    auto* string = *reinterpret_cast<void**>(reinterpret_cast<char*>(netSystem) + 0x1B0);
    if (string == nullptr || !plausiblePointer(reinterpret_cast<std::uint64_t>(string))) {
        return 0;
    }
    return *reinterpret_cast<std::uint32_t*>(reinterpret_cast<char*>(string) + 0x10);
}

void writeSerializeHit(void* netSystem, std::uint32_t before, std::uint32_t after) {
    if (gSuspectPath[0] == 0) {
        return;
    }
    HANDLE handle = CreateFileA(
        gSuspectPath,
        FILE_APPEND_DATA,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        nullptr,
        OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        nullptr
    );
    if (handle == INVALID_HANDLE_VALUE) {
        return;
    }
    void*  frames[6]{};
    USHORT count = RtlCaptureStackBackTrace(1, 6, frames, nullptr);
    char   line[640];
    int const length = std::snprintf(
        line,
        sizeof line,
        "{\"kind\":\"serialize\",\"tick\":%llu,\"tid\":%lu,\"net\":\"%p\","
        "\"before\":%u,\"after\":%u,\"stack\":[\"%p\",\"%p\",\"%p\",\"%p\"]}\n",
        static_cast<unsigned long long>(gWatchTick.load(std::memory_order_relaxed)),
        static_cast<unsigned long>(GetCurrentThreadId()),
        netSystem,
        before,
        after,
        count > 0 ? frames[0] : nullptr,
        count > 1 ? frames[1] : nullptr,
        count > 2 ? frames[2] : nullptr,
        count > 3 ? frames[3] : nullptr
    );
    DWORD written = 0;
    if (length > 0) {
        WriteFile(handle, line, static_cast<DWORD>(length), &written, nullptr);
    }
    CloseHandle(handle);
}

std::uint64_t detourSerialize(void* netSystem, void* packet, void* peer, void* mode) {
    std::uint32_t const before = frameLength(netSystem);
    std::uint64_t       result = 0;
    if (gSerializeOrigin != nullptr) {
        result = reinterpret_cast<SerializeFn>(gSerializeOrigin)(netSystem, packet, peer, mode);
    }
    std::uint32_t const after = frameLength(netSystem);
    pushSendRec(reinterpret_cast<std::uint64_t>(netSystem), before, after);
    if (after >= 24 && after <= 120 && !healthySendSize(after)) {
        writeSerializeHit(netSystem, before, after);
    }
    return result;
}

nlohmann::json sendProbeStart(std::uint64_t address, std::uint64_t serializeAddress,
                              bool allowVa, bool inWorld) {
    if (!allowVa) return errorJson("hook_va_disabled");
    if (!inWorld) return errorJson("not_in_world");
    if (!hookAddressExecutable(address)) return errorJson("not_executable");
    if (gSendInstalled.load(std::memory_order_acquire)) {
        return nlohmann::json{{"event", "send_probe"}, {"active", true}, {"already", true}};
    }
    if (gSuspectPath[0] == 0) {
        auto const path = ProbeMod::getInstance().getSelf().getModDir()
                          / ("suspect-" + std::to_string(GetCurrentProcessId()) + ".jsonl");
        std::snprintf(gSuspectPath, sizeof gSuspectPath, "%s", path.string().c_str());
    }
    gSendAddress = address;
    if (ll::memory::hook(
            reinterpret_cast<ll::memory::FuncPtr>(address),
            ll::memory::toFuncPtr(&detourSend),
            &gSendOrigin,
            ll::memory::HookPriority::Normal,
            true
        ) != 0) {
        return errorJson("send_probe_hook_failed");
    }
    gSendInstalled.store(true, std::memory_order_release);
    bool serializeHooked = false;
    if (serializeAddress != 0 && hookAddressExecutable(serializeAddress)) {
        gSerializeAddress = serializeAddress;
        if (ll::memory::hook(
                reinterpret_cast<ll::memory::FuncPtr>(serializeAddress),
                ll::memory::toFuncPtr(&detourSerialize),
                &gSerializeOrigin,
                ll::memory::HookPriority::Normal,
                true
            ) == 0) {
            gSerializeInstalled.store(true, std::memory_order_release);
            serializeHooked = true;
        }
    }
    return nlohmann::json{
        {"event", "send_probe"}, {"active", true}, {"path", gSuspectPath},
        {"hits", 0}, {"serialize_hooked", serializeHooked},
    };
}

nlohmann::json sendProbeStop() {
    if (gSerializeInstalled.load(std::memory_order_acquire) && gSerializeAddress != 0) {
        if (ll::memory::unhook(
                reinterpret_cast<ll::memory::FuncPtr>(gSerializeAddress),
                ll::memory::toFuncPtr(&detourSerialize),
                true
            )) {
            gSerializeInstalled.store(false, std::memory_order_release);
        }
    }
    if (gSendInstalled.load(std::memory_order_acquire) && gSendAddress != 0) {
        if (ll::memory::unhook(
                reinterpret_cast<ll::memory::FuncPtr>(gSendAddress),
                ll::memory::toFuncPtr(&detourSend),
                true
            )) {
            gSendInstalled.store(false, std::memory_order_release);
        }
    }
    return nlohmann::json{
        {"event", "send_probe"}, {"active", gSendInstalled.load(std::memory_order_acquire)},
        {"hits", gSendHits.load(std::memory_order_relaxed)},
        {"dumps", gSendDumps.load(std::memory_order_relaxed)}
    };
}

} // namespace

std::uint64_t gameModuleBase() {
    return reinterpret_cast<std::uint64_t>(GetModuleHandleW(nullptr));
}

nlohmann::json sendProbeJson(bool on, bool allowVa, bool inWorld, std::uint64_t address,
                             std::uint64_t serializeAddress) {
    return on ? sendProbeStart(address, serializeAddress, allowVa, inWorld) : sendProbeStop();
}

nlohmann::json violationWatchStart(std::uint64_t address, bool allowVa, bool inWorld,
                                   std::uint64_t loopAddress, std::uint64_t assignAddress,
                                   std::uint64_t dispatchAddress) {
    std::lock_guard lock(gInstallMutex);
    if (!allowVa) return errorJson("violation_watch_disabled");
    if (!inWorld) return errorJson("not_in_world");
    // The caller passes a runtime address: roles are static VAs under the
    // documented image base, and this packed exe's PE ImageBase was rewritten,
    // so only `module_base + (role_va - documented_base)` maps correctly.
    if (!hookAddressExecutable(address)) return errorJson("not_executable");
    if (!initializeRing()) return errorJson("allocation_failed");
    gInboundFrozen.store(false, std::memory_order_release);
    bool const already = gInstalled.load(std::memory_order_acquire);
    if (already && gTarget.load() != address) return errorJson("violation_watch_busy");
    if (!already) {
        int const status = ll::memory::hook(
            reinterpret_cast<ll::memory::FuncPtr>(address),
            ll::memory::toFuncPtr(&detourHandleViolation),
            &gOrigin,
            ll::memory::HookPriority::Normal,
            true
        );
        if (status != 0) {
            return nlohmann::json{{"event", "error"}, {"reason", "hook_failed"}, {"code", status}};
        }
        gTarget.store(address);
        gInstalled.store(true, std::memory_order_release);
        ProbeMod::getInstance().bootLog("violation watch: installed");
    }

    // The screen side uses an exported symbol. A failed side can be retried
    // without registering the already-installed callback a second time.
    if (!gScreenInstalled.load(std::memory_order_acquire)) {
        if (ll::memory::hook(
                ll::memory::toFuncPtr(&OreUI::EntryPoints::DisconnectScreen::composeRoute),
                ll::memory::toFuncPtr(&detourComposeRoute),
                &gRouteOrigin,
                ll::memory::HookPriority::Normal,
                true
            ) == 0) {
            gScreenInstalled.store(true, std::memory_order_release);
        } else {
            ProbeMod::getInstance().bootLog("violation watch: screen hook unavailable");
        }
    }
    if (loopAddress != 0 && !gLoopInstalled.load(std::memory_order_acquire)
        && hookAddressExecutable(loopAddress)) {
        if (ll::memory::hook(
                reinterpret_cast<ll::memory::FuncPtr>(loopAddress),
                ll::memory::toFuncPtr(&detourLoopEntry),
                &gLoopOrigin,
                ll::memory::HookPriority::Normal,
                true
            ) == 0) {
            gLoopInstalled.store(true, std::memory_order_release);
        } else {
            ProbeMod::getInstance().bootLog("violation watch: loop hook unavailable");
        }
    }
    if (dispatchAddress != 0 && !gDispatchInstalled.load(std::memory_order_acquire)
        && hookAddressExecutable(dispatchAddress)) {
        if (ll::memory::hook(
                reinterpret_cast<ll::memory::FuncPtr>(dispatchAddress),
                ll::memory::toFuncPtr(&detourDispatch),
                &gDispatchOrigin,
                ll::memory::HookPriority::Normal,
                true
            ) == 0) {
            gDispatchInstalled.store(true, std::memory_order_release);
        }
    }
    if (assignAddress != 0 && !gAssignInstalled.load(std::memory_order_acquire)
        && hookAddressExecutable(assignAddress)) {
        if (ll::memory::hook(
                reinterpret_cast<ll::memory::FuncPtr>(assignAddress),
                ll::memory::toFuncPtr(&detourAssign),
                &gAssignOrigin,
                ll::memory::HookPriority::Normal,
                true
            ) == 0) {
            gAssignInstalled.store(true, std::memory_order_release);
        } else {
            ProbeMod::getInstance().bootLog("violation watch: assign hook unavailable");
        }
    }
    initializeTextRing();
    if (!gInboundInstalled.load(std::memory_order_acquire) && initializeInboundRing()) {
        if (ll::memory::hook(
                ll::memory::toFuncPtr(&Packet::readNoHeader),
                ll::memory::toFuncPtr(&detourReadBoundary),
                &gInboundOrigin,
                ll::memory::HookPriority::Normal,
                true
            ) == 0) {
            gInboundInstalled.store(true, std::memory_order_release);
        } else {
            ProbeMod::getInstance().bootLog("violation watch: inbound hook unavailable");
        }
    }
    bool const screenHooked = gScreenInstalled.load(std::memory_order_acquire);
    return nlohmann::json{
        {"event",         "violation_watch"},
        {"active",        true             },
        {"already",       already          },
        {"callback_hooked", true           },
        {"address",       address          },
        {"module_base",   gameModuleBase() },
        {"screen_hooked", screenHooked     },
        {"inbound_hooked", gInboundInstalled.load(std::memory_order_acquire)},
        {"loop_hooked", gLoopInstalled.load(std::memory_order_acquire)},
        {"assign_hooked", gAssignInstalled.load(std::memory_order_acquire)},
        {"dispatch_hooked", gDispatchInstalled.load(std::memory_order_acquire)},
        {"screen_error",  screenHooked ? nlohmann::json(nullptr)
                                       : nlohmann::json("screen_hook_failed")},
    };
}

nlohmann::json violationWatchStop() {
    std::lock_guard lock(gInstallMutex);
    gInboundFrozen.store(false, std::memory_order_release);
    bool const callbackBefore = gInstalled.load(std::memory_order_acquire);
    bool const screenBefore = gScreenInstalled.load(std::memory_order_acquire);
    bool const inboundBefore = gInboundInstalled.load(std::memory_order_acquire);
    if (inboundBefore && ll::memory::unhook(
            ll::memory::toFuncPtr(&Packet::readNoHeader),
            ll::memory::toFuncPtr(&detourReadBoundary),
            true
        )) {
        gInboundInstalled.store(false, std::memory_order_release);
    }
    if (screenBefore && ll::memory::unhook(
            ll::memory::toFuncPtr(&OreUI::EntryPoints::DisconnectScreen::composeRoute),
            ll::memory::toFuncPtr(&detourComposeRoute),
            true
        )) {
        gScreenInstalled.store(false, std::memory_order_release);
    }
    if (callbackBefore && ll::memory::unhook(
                reinterpret_cast<ll::memory::FuncPtr>(gTarget.load()),
                ll::memory::toFuncPtr(&detourHandleViolation),
                true
            )) {
        gInstalled.store(false, std::memory_order_release);
    }
    // Keep both origin slots alive for SDK chain maintenance and in-flight
    // calls. Do not claim removal when either SDK unhook failed.
    bool const callback = gInstalled.load(std::memory_order_acquire);
    bool const screen = gScreenInstalled.load(std::memory_order_acquire);
    bool const inbound = gInboundInstalled.load(std::memory_order_acquire);
    ProbeMod::getInstance().bootLog(callback || screen || inbound
        ? "violation watch: stop incomplete" : "violation watch: stopped");
    return nlohmann::json{
        {"event", "violation_watch"}, {"active", callback || screen || inbound},
        {"callback_hooked", callback}, {"screen_hooked", screen},
        {"inbound_hooked", inbound},
        {"already", !callbackBefore && !screenBefore && !inboundBefore},
        {"unhooked", !callback && !screen && !inbound}
    };
}

nlohmann::json violationWatchStats() {
    return nlohmann::json{
        {"event",     "violation_stats"                     },
        {"active",    gInstalled.load(std::memory_order_acquire) || gScreenInstalled.load(std::memory_order_acquire)},
        {"callback_hooked", gInstalled.load(std::memory_order_acquire)},
        {"address",   gTarget.load()                        },
        {"screen_hooked", gScreenInstalled.load(std::memory_order_acquire)},
        {"inbound_hooked", gInboundInstalled.load(std::memory_order_acquire)},
        {"inbound_hits", gInboundHits.load(std::memory_order_relaxed)},
        {"inbound_sampled", gInboundSampled.load(std::memory_order_relaxed)},
        {"inbound_sample", gInboundMask.load(std::memory_order_relaxed) + 1},
        {"inbound_bytes", gInboundBytes.load(std::memory_order_relaxed)},
        {"inbound_ready", gInboundReady.load(std::memory_order_acquire)},
        {"inbound_frozen", gInboundFrozen.load(std::memory_order_acquire)},
        {"loop_hooked", gLoopInstalled.load(std::memory_order_acquire)},
        {"net_ctx", gNetContext.load(std::memory_order_relaxed)},
        {"assign_hooked", gAssignInstalled.load(std::memory_order_acquire)},
        {"assign_count", gAssignCount.load(std::memory_order_relaxed)},
        {"assign_calls", gAssignCalls.load(std::memory_order_relaxed)},
        {"text_ready", gTextReady.load(std::memory_order_acquire)},
        {"text_head", gTextRing.head()},
        {"inbound_head", gInboundRing.head()},
        {"hits",      gHits.load(std::memory_order_relaxed) },
        {"synthetic", gSynthetic.load(std::memory_order_relaxed)},
        {"observation_faults", gObservationFaults.load(std::memory_order_relaxed)},
        {"capacity",  kViolationCapacity                      },
    };
}

nlohmann::json memmoveProbeJson(bool on, int packetId) {
    std::lock_guard lock(gInstallMutex);
    gMemmoveFilter.store(packetId > 0 ? static_cast<std::uint32_t>(packetId) : 0u,
                         std::memory_order_relaxed);
    if (on && !gMemmoveInstalled.load(std::memory_order_acquire)) {
        if (ll::memory::hook(
                reinterpret_cast<ll::memory::FuncPtr>(&memmove),
                ll::memory::toFuncPtr(&detourMemmove),
                &gMemmoveOrigin,
                ll::memory::HookPriority::Normal,
                true
            ) == 0) {
            gMemmoveInstalled.store(true, std::memory_order_release);
        }
    }
    if (on && !gMemcpyInstalled.load(std::memory_order_acquire)) {
        if (ll::memory::hook(
                reinterpret_cast<ll::memory::FuncPtr>(&memcpy),
                ll::memory::toFuncPtr(&detourMemcpy),
                &gMemcpyOrigin,
                ll::memory::HookPriority::Normal,
                true
            ) == 0) {
            gMemcpyInstalled.store(true, std::memory_order_release);
        }
    }
    if (!on && gMemcpyInstalled.load(std::memory_order_acquire)) {
        if (ll::memory::unhook(reinterpret_cast<ll::memory::FuncPtr>(&memcpy),
                               ll::memory::toFuncPtr(&detourMemcpy), true)) {
            gMemcpyInstalled.store(false, std::memory_order_release);
        }
    }
    if (!on && gMemmoveInstalled.load(std::memory_order_acquire)) {
        if (ll::memory::unhook(reinterpret_cast<ll::memory::FuncPtr>(&memmove),
                               ll::memory::toFuncPtr(&detourMemmove), true)) {
            gMemmoveInstalled.store(false, std::memory_order_release);
        }
    }
    gMemmoveEnabled.store(on && gMemmoveInstalled.load(std::memory_order_acquire),
                          std::memory_order_release);
    return nlohmann::json{{"event", "memmove_probe"},
                          {"on", gMemmoveEnabled.load(std::memory_order_acquire)},
                          {"filter_id", gMemmoveFilter.load(std::memory_order_relaxed)},
                          {"hooked", gMemmoveInstalled.load(std::memory_order_acquire)}};
}

nlohmann::json violationTextJson(std::uint64_t since, std::size_t limit) {
    nlohmann::json rows = nlohmann::json::array();
    std::uint64_t head = gTextRing.head();
    std::uint64_t cursor = since;
    if (!gTextReady.load(std::memory_order_acquire)) {
        return nlohmann::json{{"event", "violation_text"}, {"supported", false},
                              {"hits", rows}, {"count", 0}, {"head", 0}, {"next", 0}};
    }
    std::uint64_t const oldest = head > kTextCapacity ? head - kTextCapacity : 0;
    std::uint64_t lost = 0;
    if (cursor < oldest) {
        lost   = oldest - cursor;
        cursor = oldest;
    }
    if (cursor > head) cursor = head;
    if (limit == 0) limit = kTextCapacity;
    std::size_t count = 0;
    for (std::uint64_t index = cursor; index < head && count < limit; ++index) {
        TextRecord record{};
        auto const read = gTextRing.read(index, record);
        if (read == TextRing::ReadResult::pending) break;
        if (read != TextRing::ReadResult::ready) {
            ++lost;
            cursor = index + 1;
            continue;
        }
        nlohmann::json row{
            {"index", index + 1},
            {"tick", record.tick},
            {"kind", record.kind == 2 ? "screen"
                     : record.kind == kMemmoveKind ? "memmove" : "callback"},
            {"len", record.len},
            {"truncated", record.truncated != 0},
        };
        if (record.kind == kAssignCallerKind && record.len == sizeof(MemmoveBlob)) {
            MemmoveBlob blob{};
            std::memcpy(&blob, record.text, sizeof(blob));
            row["caller"] = blob.src;
            row["assigned"] = blob.len;
            std::uint64_t arg1 = 0;
            std::uint64_t arg2 = 0;
            std::memcpy(&arg1, blob.before, sizeof(arg1));
            std::memcpy(&arg2, blob.after, sizeof(arg2));
            row["arg1"] = arg1;
            row["arg2"] = arg2;
            if (arg1 > 0x10000) {
                std::uint64_t srcSize = 0;
                if (readMemory(arg1 + 0x10, &srcSize, sizeof(srcSize))) row["arg1_size"] = srcSize;
            }
        } else if (record.kind == kDispatchKind && record.len == sizeof(MemmoveBlob)) {
            MemmoveBlob blob{};
            std::memcpy(&blob, record.text, sizeof(blob));
            std::uint8_t words[32]{};
            if (blob.len != 0) readMemory(blob.len, words, sizeof(words));
            row["arg1"] = blob.src;
            row["arg2"] = blob.len;
            row["arg2_words"] = hexText(reinterpret_cast<char const*>(words), sizeof(words));
        } else if (record.kind == kMemmoveKind && record.len == sizeof(MemmoveBlob)) {
            // Binary: emit the fields so the reply stays valid UTF-8. The source
            // bytes are read here, on the pipe thread, not in the copy hook.
            MemmoveBlob blob{};
            std::memcpy(&blob, record.text, sizeof(blob));
            if (blob.src > sizeof(blob.before)) {
                readMemory(blob.src - sizeof(blob.before), blob.before, sizeof(blob.before));
            }
            readMemory(blob.src, blob.after, sizeof(blob.after));
            row["src"] = hexText(blob.before[0] == 0 ? "" : "", 0); // placeholder overwritten below
            row["src"] = nlohmann::json::array();
            row.erase("src");
            row["src_addr"] = blob.src;
            row["copy_len"] = blob.len;
            row["before_hex"] = hexText(blob.before, sizeof(blob.before));
            row["after_hex"] = hexText(blob.after, sizeof(blob.after));
        } else {
            row["text"] = std::string(record.text, record.len);
        }
        rows.push_back(std::move(row));
        ++count;
        cursor = index + 1;
    }
    return nlohmann::json{{"event", "violation_text"}, {"supported", true},
                          {"since", since}, {"next", cursor}, {"head", head},
                          {"lost", lost}, {"count", count}, {"hits", rows}};
}

nlohmann::json inboundSampleJson(int sample) {
    int stride = 4;
    for (int candidate : {1, 2, 4, 8, 16, 32}) {
        if (sample <= candidate) {
            stride = candidate;
            break;
        }
    }
    gInboundMask.store(static_cast<std::uint32_t>(stride - 1), std::memory_order_relaxed);
    return nlohmann::json{
        {"event", "inbound_sample"},
        {"sample", stride},
        {"hooked", gInboundInstalled.load(std::memory_order_acquire)},
    };
}

nlohmann::json inboundDrainJson(std::uint64_t since, std::size_t limit) {
    if (!gInboundReady.load(std::memory_order_acquire)) {
        return nlohmann::json{{"event", "inbound_drain"}, {"supported", false},
                              {"reason", "ring_not_initialized"},
                              {"hits", nlohmann::json::array()},
                              {"count", 0}, {"head", 0}, {"next", 0}, {"lost", 0}};
    }
    nlohmann::json out = drainRing(gInboundRing, kInboundCapacity, since, limit, "inbound_drain");
    out["supported"]   = gInboundInstalled.load(std::memory_order_acquire);
    return out;
}

nlohmann::json violationDrainJson(std::uint64_t since, std::size_t limit) {
    if (!gRingReady.load(std::memory_order_acquire)) {
        return nlohmann::json{{"event", "violation_drain"}, {"hits", nlohmann::json::array()},
                              {"count", 0}, {"head", 0}, {"next", 0}, {"lost", 0}};
    }
    return drainRing(gRing, kViolationCapacity, since, limit, "violation_drain");
}

// Builds the same route the error page uses, through the (optionally hooked)
// symbol. Verifies the screen-side record without waiting for a real disconnect.
nlohmann::json disconnectRouteProbe(int reason, std::string const& codeword,
                                    std::string const& details) {
    SyntheticViolationScope synthetic;
    std::string route;
    if (!guardedGameRead([&] {
        route = OreUI::EntryPoints::DisconnectScreen::composeRoute(
            reason, "", "", codeword, details, "", ""
        );
    })) return errorJson("disconnect_route_fault");
    std::string clean;
    clean.reserve(route.size() < 160 ? route.size() : 160);
    for (std::size_t index = 0; index < route.size() && clean.size() < 160; ++index) {
        auto const uch = static_cast<unsigned char>(route[index]);
        clean.push_back(uch >= 0x20 && uch < 0x7F ? route[index] : '.');
    }
    return nlohmann::json{
        {"event",  "disconnect_route"},
        {"reason", reason            },
        {"route",  clean             },
        {"length", route.size()      },
    };
}

nlohmann::json violationWatchSynthetic() {
    std::lock_guard lock(gInstallMutex);
    if (!initializeRing()) return errorJson("allocation_failed");
    std::string const sample = "synthetic violation record";
    publishViolation(3, -1, nullptr, &sample, 0, true);
    return nlohmann::json{{"event", "violation_synthetic"}, {"ok", true}};
}

} // namespace probe::detail
