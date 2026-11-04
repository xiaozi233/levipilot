#include "mod/Internal.h"
#include "mod/ViolationWatchShared.h"

using namespace probe::detail;

// Send and serialize suspect probes own their persistent origins and bounded
// forensic files. Healthy traffic performs only fixed loads and comparisons.
namespace probe::detail {
namespace {

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
using SendProbeFn                       = void (*)(void*, void*, void*, void*);
ll::memory::FuncPtr        gSendOrigin  = nullptr;
std::uint64_t              gSendAddress = 0;
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

// Allocated before either hook is installed; callbacks only publish fixed rows.
SendProbeHistory gSendRing;

void pushSendRec(std::uint64_t net, std::uint32_t before, std::uint32_t after) {
    auto slot = gSendRing.reserve();
    slot.publish(SendProbeRecord{gWatchTick.load(std::memory_order_relaxed), net, GetCurrentThreadId(), before, after});
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
    auto const head = gSendRing.head();
    auto const begin = head > 64 ? head - 64 : 0;
    char                line[256];
    for (auto index = begin; index < head; ++index) {
        SendProbeRecord rec;
        if (gSendRing.read(index, rec) != SendProbeHistory::ReadResult::ready) continue;
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
    static char const*  digits = "0123456789abcdef";
    char                head[41]{};
    std::uint32_t const shown = size < 20 ? size : 20;
    for (std::uint32_t index = 0; index < shown; ++index) {
        head[index * 2]     = digits[data[index] >> 4];
        head[index * 2 + 1] = digits[data[index] & 0xF];
    }
    void*     frames[8]{};
    USHORT    count = RtlCaptureStackBackTrace(1, 8, frames, nullptr);
    char      line[768];
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
    CallbackScope callback(gCallbacks, CallbackDomain::Diagnostic);
    gSendHits.fetch_add(1, std::memory_order_relaxed);
    if (string != nullptr && plausiblePointer(reinterpret_cast<std::uint64_t>(string))) {
        auto const size   = *reinterpret_cast<std::uint32_t*>(reinterpret_cast<char*>(string) + 0x10);
        auto const data   = *reinterpret_cast<std::uint8_t const**>(string);
        bool const uplink = plausiblePointer(reinterpret_cast<std::uint64_t>(data))
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
using SerializeFn                     = std::uint64_t (*)(void*, void*, void*, void*);
ll::memory::FuncPtr gSerializeOrigin  = nullptr;
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
    void*     frames[6]{};
    USHORT    count = RtlCaptureStackBackTrace(1, 6, frames, nullptr);
    char      line[640];
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
    CallbackScope callback(gCallbacks, CallbackDomain::Diagnostic);
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

nlohmann::json sendProbeStart(std::uint64_t address, std::uint64_t serializeAddress, bool allowVa, bool inWorld) {
    if (!allowVa) return errorJson("hook_va_disabled");
    if (!inWorld) return errorJson("not_in_world");
    if (!hookAddressExecutable(address)) return errorJson("not_executable");
    if (gSendInstalled.load(std::memory_order_acquire)) {
        return nlohmann::json{
            {"event",   "send_probe"},
            {"active",  true        },
            {"already", true        },
            {"history_records", gSendRing.head()}
        };
    }
    if (!gSendRing.initialize()) return errorJson("send_probe_allocation_failed");
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
        )
        != 0) {
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
            )
            == 0) {
            gSerializeInstalled.store(true, std::memory_order_release);
            serializeHooked = true;
        }
    }
    return nlohmann::json{
        {"event",            "send_probe"   },
        {"active",           true           },
        {"path",             gSuspectPath   },
        {"hits",             0              },
        {"serialize_hooked", serializeHooked},
        {"history_records", gSendRing.head()},
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
        {"event",  "send_probe"                                  },
        {"active", gSendInstalled.load(std::memory_order_acquire)},
        {"hits",   gSendHits.load(std::memory_order_relaxed)     },
        {"dumps",  gSendDumps.load(std::memory_order_relaxed)    },
        {"history_records", gSendRing.head()}
    };
}

} // namespace

bool sendCallbacksInstalled() { return gSendInstalled.load() || gSerializeInstalled.load(); }

nlohmann::json
sendProbeJson(bool on, bool allowVa, bool inWorld, std::uint64_t address, std::uint64_t serializeAddress) {
    return on ? sendProbeStart(address, serializeAddress, allowVa, inWorld) : sendProbeStop();
}

} // namespace probe::detail
