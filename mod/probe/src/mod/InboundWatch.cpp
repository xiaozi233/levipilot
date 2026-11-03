#include "mod/Internal.h"
#include "mod/ViolationWatchShared.h"

#include <intrin.h>

using namespace probe::detail;

// Inbound parse, assign and copy probes own their hooks, cursors and first-error
// freeze. No callback pointer or SDK string is retained beyond a fixed record.
namespace probe::detail {
namespace {

// The parse loop's entry gives the object whose member string carries the
// packet bytes (its owner is *a1). Exporting that pointer lets a debug-register
// watchpoint be placed on the member's size field to catch its writer.
using LoopEntryFn                      = void (*)(void*, void*);
ll::memory::FuncPtr        gLoopOrigin = nullptr;
std::atomic_bool           gLoopInstalled{false};
std::atomic<std::uint64_t> gNetContext{0};
// The assign that fills the packet string also writes its size. Capturing the
// size right after the call, filtered on the packet string's address, gives the
// length that the writer intended for this packet.
using AssignFn                             = std::int64_t (*)(void*, void*, void*, void*);
using DispatchFn                           = std::int64_t (*)(void*, void*);
ll::memory::FuncPtr        gDispatchOrigin = nullptr;
std::atomic_bool           gDispatchInstalled{false};
constexpr std::uint8_t     kDispatchKind     = 10;
constexpr std::uint8_t     kAssignCallerKind = 11;
ll::memory::FuncPtr        gAssignOrigin     = nullptr;
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
        if (readMemory(stringAddr, &field, sizeof(field)) && field >= 0x10000 && field < 0x00007FFFFFFFFFFFull) {
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
using ReadBoundary                        = void (*)(void*, void*, void*, void*, void*);
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
std::atomic_bool       gInboundFrozen{false};
constexpr std::uint8_t kMemmoveKind      = 9;
using MemmoveFn                          = void* (*)(void*, void const*, std::size_t);
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
CopySlot              gCopySlots[kCopySlots];

void noteCopy(std::uint64_t dst) {
    for (std::size_t index = 0; index < kCopySlots; ++index) {
        std::uint64_t expected = gCopySlots[index].addr.load(std::memory_order_relaxed);
        if (expected == dst) {
            gCopySlots[index].count.fetch_add(1, std::memory_order_relaxed);
            return;
        }
        if (expected == 0) {
            std::uint64_t empty = 0;
            if (gCopySlots[index].addr.compare_exchange_strong(empty, dst, std::memory_order_relaxed)) {
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
            if (gCopySlots[index].addr.compare_exchange_strong(empty, buffer, std::memory_order_relaxed)) {
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

bool probeStream(std::uint64_t candidate, StreamProbe& out) {
    if (!plausiblePointer(candidate)) return false;
    std::uint64_t first  = 0;
    std::uint64_t second = 0;
    out.object           = candidate;
    if (!readMemory(candidate + 0x28, &first, sizeof(first))) return false;
    if (!readMemory(candidate + 0x30, &second, sizeof(second))) return false;
    if (!readMemory(candidate + 0x38, &out.readPtr, sizeof(out.readPtr))) return false;
    if (!readMemory(candidate + 0x40, &out.overflow, sizeof(out.overflow))) return false;
    if (plausiblePointer(first) && second >= 1 && second <= (1ull << 20)) {
        out.begin = first;
        out.size  = second;
    } else if (
        plausiblePointer(first) && plausiblePointer(second) && second > first && (second - first) <= (1ull << 20)
    ) {
        out.begin = first;
        out.size  = second - first;
    } else {
        return false;
    }
    return out.readPtr <= out.size;
}

// Copy a bounded prefix of the packet slice verbatim. The parse stream starts
// at the packet id; this local deep-copy path has no frame length prefix.
std::uint8_t copySlice(std::uint64_t address, std::uint64_t limit, char* into, std::size_t cap = 24) {
    if (!plausiblePointer(address) || limit == 0) return 0;
    std::size_t const want = limit < cap ? static_cast<std::size_t>(limit) : cap;
    return readMemory(address, into, want) ? static_cast<std::uint8_t>(want) : 0;
}

void publishInbound(int argIndex, StreamProbe const& probe, StreamProbe const* parent, std::uint64_t auxPointer) {
    if (!gInboundReady.load(std::memory_order_acquire)) return;
    auto            reservation = gInboundRing.reserve();
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
    record.contextSize      = copySlice(probe.begin, probe.size, record.context, kViolationContext - 1);
    record.contextTruncated = probe.size > kViolationContext - 1 ? 1 : 0;
    // Bytes at the fourth boundary argument ([rsi+0x208] at the call site): if
    // that is the batch buffer or a container over it, these bytes show it.
    record.detailSize = copySlice(auxPointer, 24, record.detail);
    // Preserve allocation neighbours as raw diagnostic bytes only. Static
    // analysis showed a deep copy here, so these bytes are not framing data.
    if (probe.begin >= sizeof(record.before)) {
        record.beforeSize = copySlice(probe.begin - sizeof(record.before), sizeof(record.before), record.before);
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
        void*       candidates[4] = {a, b, c, d};
        StreamProbe probes[4];
        bool        valid[4] = {false, false, false, false};
        bool        used[4]  = {false, false, false, false};
        for (int index = 0; index < 4; ++index) {
            valid[index] = probeStream(reinterpret_cast<std::uint64_t>(candidates[index]), probes[index]);
        }
        for (int index = 0; index < 4; ++index) {
            if (!valid[index] || used[index]) continue;
            // A parent is a stream whose view plus read pointer is exactly where
            // this slice begins: the slice is a subspan of it.
            int parentIndex = -1;
            for (int other = 0; other < 4; ++other) {
                if (other == index || !valid[other] || used[other]) continue;
                std::uint64_t const at    = probes[other].begin + probes[other].readPtr;
                std::uint64_t const start = probes[index].begin;
                if (at <= start && start - at <= 8 && probes[other].size > probes[index].size) {
                    parentIndex = other;
                    break;
                }
            }
            gInboundSampled.fetch_add(1, std::memory_order_relaxed);
            publishInbound(
                index,
                probes[index],
                parentIndex >= 0 ? &probes[parentIndex] : nullptr,
                reinterpret_cast<std::uint64_t>(candidates[3])
            );
            used[index] = true;
            if (parentIndex >= 0) used[parentIndex] = true;
            break;
        }
    }
    if (gInboundOrigin == nullptr) return;
    reinterpret_cast<ReadBoundary>(gInboundOrigin)(a, b, c, d, e);
}

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
        std::uint64_t field   = *reinterpret_cast<std::uint64_t*>(wanted);
        std::uint64_t dataPtr = (field >= 0x10000 && field < 0x00007FFFFFFFFFFFull) ? field : wanted;
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
        publishText(kAssignCallerKind, std::string_view(reinterpret_cast<char const*>(&blob), sizeof(blob)));
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
    publishText(kDispatchKind, std::string_view(reinterpret_cast<char const*>(&blob), sizeof(blob)));
    if (gDispatchOrigin == nullptr) return 0;
    return reinterpret_cast<DispatchFn>(gDispatchOrigin)(a1, a2);
}

void recordCopy(void* dst, void const* src, std::size_t length) {
    static thread_local bool busy = false;
    if (busy) return;
    busy             = true;
    auto const begin = gPacketBegin.load(std::memory_order_relaxed);
    if (gMemmoveEnabled.load(std::memory_order_relaxed) && begin != 0 && reinterpret_cast<std::uint64_t>(dst) == begin
        && length != 0 && length < 4096) {
        noteCopy(begin);
        auto const filter = gMemmoveFilter.load(std::memory_order_relaxed);
        // No readMemory here either: the filter is 0 for this probe (record
        // every copy) and the source bytes are read at drain time instead.
        (void)filter;
        MemmoveBlob blob{};
        blob.src = reinterpret_cast<std::uint64_t>(src);
        blob.len = length;
        publishText(kMemmoveKind, std::string_view(reinterpret_cast<char const*>(&blob), sizeof(blob)));
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

} // namespace

void inboundWatchFreeze() { gInboundFrozen.store(true, std::memory_order_release); }

void inboundWatchReset() { gInboundFrozen.store(false, std::memory_order_release); }

nlohmann::json
inboundWatchStart(std::uint64_t loopAddress, std::uint64_t assignAddress, std::uint64_t dispatchAddress) {
    if (loopAddress != 0 && !gLoopInstalled.load(std::memory_order_acquire) && hookAddressExecutable(loopAddress)) {
        if (ll::memory::hook(
                reinterpret_cast<ll::memory::FuncPtr>(loopAddress),
                ll::memory::toFuncPtr(&detourLoopEntry),
                &gLoopOrigin,
                ll::memory::HookPriority::Normal,
                true
            )
            == 0) {
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
            )
            == 0) {
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
            )
            == 0) {
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
            )
            == 0) {
            gInboundInstalled.store(true, std::memory_order_release);
        } else {
            ProbeMod::getInstance().bootLog("violation watch: inbound hook unavailable");
        }
    }
    return nlohmann::json{
        {"inbound_hooked",  gInboundInstalled.load(std::memory_order_acquire) },
        {"loop_hooked",     gLoopInstalled.load(std::memory_order_acquire)    },
        {"assign_hooked",   gAssignInstalled.load(std::memory_order_acquire)  },
        {"dispatch_hooked", gDispatchInstalled.load(std::memory_order_acquire)},
    };
}

nlohmann::json inboundWatchStop() {
    inboundWatchReset();
    bool const before = gInboundInstalled.load(std::memory_order_acquire);
    if (before
        && ll::memory::unhook(
            ll::memory::toFuncPtr(&Packet::readNoHeader),
            ll::memory::toFuncPtr(&detourReadBoundary),
            true
        )) {
        gInboundInstalled.store(false, std::memory_order_release);
    }
    return nlohmann::json{
        {"inbound_before", before                                           },
        {"inbound_hooked", gInboundInstalled.load(std::memory_order_acquire)},
    };
}

void inboundWatchStats(nlohmann::json& out) {
    out.update(
        nlohmann::json{
            {"inbound_hooked",  gInboundInstalled.load(std::memory_order_acquire)},
            {"inbound_hits",    gInboundHits.load(std::memory_order_relaxed)     },
            {"inbound_sampled", gInboundSampled.load(std::memory_order_relaxed)  },
            {"inbound_sample",  gInboundMask.load(std::memory_order_relaxed) + 1 },
            {"inbound_bytes",   gInboundBytes.load(std::memory_order_relaxed)    },
            {"inbound_ready",   gInboundReady.load(std::memory_order_acquire)    },
            {"inbound_frozen",  gInboundFrozen.load(std::memory_order_acquire)   },
            {"loop_hooked",     gLoopInstalled.load(std::memory_order_acquire)   },
            {"net_ctx",         gNetContext.load(std::memory_order_relaxed)      },
            {"assign_hooked",   gAssignInstalled.load(std::memory_order_acquire) },
            {"assign_count",    gAssignCount.load(std::memory_order_relaxed)     },
            {"assign_calls",    gAssignCalls.load(std::memory_order_relaxed)     },
            {"inbound_head",    gInboundRing.head()                              },
    }
    );
}

nlohmann::json memmoveProbeJson(bool on, int packetId) {
    std::lock_guard lock(gViolationInstallMutex);
    gMemmoveFilter.store(packetId > 0 ? static_cast<std::uint32_t>(packetId) : 0u, std::memory_order_relaxed);
    if (on && !gMemmoveInstalled.load(std::memory_order_acquire)) {
        if (ll::memory::hook(
                reinterpret_cast<ll::memory::FuncPtr>(&memmove),
                ll::memory::toFuncPtr(&detourMemmove),
                &gMemmoveOrigin,
                ll::memory::HookPriority::Normal,
                true
            )
            == 0) {
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
            )
            == 0) {
            gMemcpyInstalled.store(true, std::memory_order_release);
        }
    }
    if (!on && gMemcpyInstalled.load(std::memory_order_acquire)) {
        if (ll::memory::unhook(
                reinterpret_cast<ll::memory::FuncPtr>(&memcpy),
                ll::memory::toFuncPtr(&detourMemcpy),
                true
            )) {
            gMemcpyInstalled.store(false, std::memory_order_release);
        }
    }
    if (!on && gMemmoveInstalled.load(std::memory_order_acquire)) {
        if (ll::memory::unhook(
                reinterpret_cast<ll::memory::FuncPtr>(&memmove),
                ll::memory::toFuncPtr(&detourMemmove),
                true
            )) {
            gMemmoveInstalled.store(false, std::memory_order_release);
        }
    }
    gMemmoveEnabled.store(on && gMemmoveInstalled.load(std::memory_order_acquire), std::memory_order_release);
    return nlohmann::json{
        {"event",     "memmove_probe"                                  },
        {"on",        gMemmoveEnabled.load(std::memory_order_acquire)  },
        {"filter_id", gMemmoveFilter.load(std::memory_order_relaxed)   },
        {"hooked",    gMemmoveInstalled.load(std::memory_order_acquire)}
    };
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
        {"event",  "inbound_sample"                                 },
        {"sample", stride                                           },
        {"hooked", gInboundInstalled.load(std::memory_order_acquire)},
    };
}

nlohmann::json inboundDrainJson(std::uint64_t since, std::size_t limit) {
    if (!gInboundReady.load(std::memory_order_acquire)) {
        return nlohmann::json{
            {"event",     "inbound_drain"        },
            {"supported", false                  },
            {"reason",    "ring_not_initialized" },
            {"hits",      nlohmann::json::array()},
            {"count",     0                      },
            {"head",      0                      },
            {"next",      0                      },
            {"lost",      0                      }
        };
    }
    nlohmann::json out = drainRing(gInboundRing, kInboundCapacity, since, limit, "inbound_drain");
    out["supported"]   = gInboundInstalled.load(std::memory_order_acquire);
    return out;
}

} // namespace probe::detail
