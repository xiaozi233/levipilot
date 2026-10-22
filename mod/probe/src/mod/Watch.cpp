#include "mod/Internal.h"

#include <tlhelp32.h>

#include <new>

using namespace probe::detail;

namespace probe::detail {

std::atomic<std::uint64_t> gWatchTick{0};
std::atomic<std::uint32_t> gTickThreadId{0};
std::atomic<std::uint32_t> gServerThreadId{0};

namespace {

constexpr std::size_t kSlotCount    = 4; // DR0-DR3
constexpr std::size_t kRingCapacity = 1u << 16;
constexpr std::size_t kMaxFrames    = 8;

struct Watch {
    std::atomic_bool           used{false};
    std::atomic<std::uint32_t> id{0};
    std::atomic<std::uint64_t> hits{0};
    std::uint64_t              addr      = 0;
    std::uint32_t              size      = 1;
    bool                       readWrite = false;
    int                        slot      = -1;
    std::string                tag;
    std::vector<DWORD>         threads;
};

struct HitRecord {
    std::atomic<std::uint64_t> seq{0}; // index + 1 once the record is complete
    std::uint32_t              watchId    = 0;
    std::uint32_t              tid        = 0;
    std::uint64_t              tick       = 0;
    std::uint64_t              rip        = 0;
    std::uint32_t              frameCount = 0;
    std::uint32_t              pad        = 0;
    std::uint64_t              frames[kMaxFrames]{};
};

std::unique_ptr<Watch>              gWatches[kSlotCount];
std::unique_ptr<HitRecord[]>        gRing;
std::atomic<std::uint64_t>          gHead{0};
std::atomic<std::uint64_t>          gTotalHits{0};
std::atomic<std::uint32_t>          gNextId{1};
std::atomic_bool                    gInitialized{false};
PVOID                               gVehHandle = nullptr;

std::uint64_t lengthBits(std::uint32_t size) {
    switch (size) {
    case 1:
        return 0b00;
    case 2:
        return 0b01;
    case 8:
        return 0b10;
    case 4:
    default:
        return 0b11;
    }
}

std::uint64_t* debugRegister(CONTEXT& context, int slot) {
    switch (slot) {
    case 1:
        return &context.Dr1;
    case 2:
        return &context.Dr2;
    case 3:
        return &context.Dr3;
    default:
        return &context.Dr0;
    }
}

void applySlot(CONTEXT& context, int slot, std::uint64_t addr, std::uint32_t size, bool readWrite, bool enable) {
    *debugRegister(context, slot) = enable ? addr : 0;

    std::uint64_t const shift = 16 + static_cast<std::uint64_t>(slot) * 4;
    context.Dr7 &= ~(0xFull << shift);
    context.Dr7 &= ~(0x3ull << (static_cast<std::uint64_t>(slot) * 2));
    if (enable) {
        context.Dr7 |= 1ull << (static_cast<std::uint64_t>(slot) * 2); // local enable
        context.Dr7 |= (readWrite ? 0b11ull : 0b01ull) << shift;       // RW: rw or write
        context.Dr7 |= lengthBits(size) << (shift + 2);                // LEN
    }
}

std::vector<DWORD> processThreads() {
    std::vector<DWORD> threads;
    HANDLE const       snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (snapshot == INVALID_HANDLE_VALUE) {
        return threads;
    }
    THREADENTRY32 entry{};
    entry.dwSize = sizeof(entry);
    if (Thread32First(snapshot, &entry)) {
        do {
            if (entry.th32OwnerProcessID == GetCurrentProcessId()) {
                threads.push_back(entry.th32ThreadID);
            }
        } while (Thread32Next(snapshot, &entry));
    }
    CloseHandle(snapshot);
    return threads;
}

bool installOnThread(
    DWORD         tid,
    int           slot,
    std::uint64_t addr,
    std::uint32_t size,
    bool          readWrite,
    std::string&  error
) {
    HANDLE const thread = OpenThread(
        THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_SET_CONTEXT | THREAD_QUERY_INFORMATION,
        FALSE,
        tid
    );
    if (thread == nullptr) {
        DWORD const code = GetLastError();
        ProbeMod::getInstance().bootLog(
            "watch: open thread failed tid=" + std::to_string(tid) + " err=" + std::to_string(code)
        );
        error = code == ERROR_INVALID_PARAMETER ? "thread_gone" : "open_thread_failed";
        return false;
    }
    bool const self = tid == GetCurrentThreadId();
    if (!self && SuspendThread(thread) == static_cast<DWORD>(-1)) {
        CloseHandle(thread);
        error = "suspend_failed";
        return false;
    }

    CONTEXT context{};
    context.ContextFlags = CONTEXT_DEBUG_REGISTERS | CONTEXT_CONTROL;
    bool ok              = GetThreadContext(thread, &context) != FALSE;
    if (ok) {
        // F7: only this slot matters. A debugger occupying another DR slot
        // must not block every watch install.
        std::uint64_t const slotBits =
            (context.Dr7 >> (static_cast<std::uint64_t>(slot) * 2)) & 0x3ull;
        if (slotBits != 0) {
            error = "dr_busy";
            ok    = false;
        }
    }
    if (ok) {
        applySlot(context, slot, addr, size, readWrite, true);
        ok = SetThreadContext(thread, &context) != FALSE;
        if (!ok) {
            error = "set_context_failed";
        }
    }
    if (!self) {
        ResumeThread(thread);
    }
    CloseHandle(thread);
    return ok;
}

bool clearOnThread(DWORD tid, int slot) {
    HANDLE const thread = OpenThread(
        THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_SET_CONTEXT | THREAD_QUERY_INFORMATION,
        FALSE,
        tid
    );
    if (thread == nullptr) {
        DWORD const code = GetLastError();
        // A thread that already exited takes its debug registers with it.
        if (code == ERROR_INVALID_PARAMETER) {
            return true;
        }
        ProbeMod::getInstance().bootLog(
            "watch: clear open failed tid=" + std::to_string(tid) + " err=" + std::to_string(code)
        );
        return false;
    }
    bool const self = tid == GetCurrentThreadId();
    if (!self && SuspendThread(thread) == static_cast<DWORD>(-1)) {
        ProbeMod::getInstance().bootLog(
            "watch: clear suspend failed tid=" + std::to_string(tid) + " err="
            + std::to_string(GetLastError())
        );
        CloseHandle(thread);
        return false;
    }
    CONTEXT context{};
    context.ContextFlags = CONTEXT_DEBUG_REGISTERS | CONTEXT_CONTROL;
    bool ok              = GetThreadContext(thread, &context) != FALSE;
    if (!ok) {
        ProbeMod::getInstance().bootLog(
            "watch: clear get context failed tid=" + std::to_string(tid) + " err="
            + std::to_string(GetLastError())
        );
    }
    if (ok) {
        applySlot(context, slot, 0, 1, false, false);
        ok = SetThreadContext(thread, &context) != FALSE;
        if (!ok) {
            ProbeMod::getInstance().bootLog(
                "watch: clear set context failed tid=" + std::to_string(tid) + " slot="
                + std::to_string(slot) + " err=" + std::to_string(GetLastError())
            );
        }
    }
    if (!self) {
        ResumeThread(thread);
    }
    CloseHandle(thread);
    return ok;
}

void storeHit(int slot, CONTEXT& context) {
    if (!gRing) {
        return;
    }
    pathCount(PathCounter::Watch);
    std::uint64_t const index = gHead.fetch_add(1, std::memory_order_acq_rel);
    HitRecord&          record = gRing[index % kRingCapacity];
    record.watchId             = gWatches[slot] ? gWatches[slot]->id.load(std::memory_order_relaxed) : 0;
    record.tid                 = GetCurrentThreadId();
    record.tick                = gWatchTick.load(std::memory_order_relaxed);
    record.rip                 = context.Rip;
    record.frameCount          = static_cast<std::uint32_t>(
        RtlCaptureStackBackTrace(0, kMaxFrames, reinterpret_cast<PVOID*>(record.frames), nullptr)
    );
    record.seq.store(index + 1, std::memory_order_release);
    if (gWatches[slot]) {
        gWatches[slot]->hits.fetch_add(1, std::memory_order_relaxed);
    }
    gTotalHits.fetch_add(1, std::memory_order_relaxed);
}

LONG CALLBACK watchVeh(EXCEPTION_POINTERS* info) {
    if (info == nullptr || info->ExceptionRecord == nullptr || info->ContextRecord == nullptr) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    DWORD const code = info->ExceptionRecord->ExceptionCode;
    if (isFatalSehCode(code)) {
        // A fault raised while a pipe command is being dispatched is converted
        // to internal_error by dispatchWithSeh; only real crashes get a dump.
        if (!probe::gDispatchSehActive) {
            probe::ProbeMod::getInstance().handleFatalException(info);
        }
        return EXCEPTION_CONTINUE_SEARCH;
    }
    if (code != EXCEPTION_SINGLE_STEP) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    CONTEXT&   context = *info->ContextRecord;
    bool       matched = false;
    for (int slot = 0; slot < static_cast<int>(kSlotCount); ++slot) {
        std::uint64_t const bit = 1ull << slot;
        if ((context.Dr6 & bit) == 0) {
            continue;
        }
        // Clear the status bit so the kernel does not report the same hit again.
        context.Dr6 &= ~bit;
        matched      = true;
        if (gWatches[slot] && gWatches[slot]->used.load(std::memory_order_acquire)) {
            storeHit(slot, context);
        }
    }
    if (!matched) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    return EXCEPTION_CONTINUE_EXECUTION;
}

nlohmann::json errorJson(char const* reason) {
    return nlohmann::json{
        {"event", "error" },
        {"reason", reason },
    };
}

} // namespace

void watchInit() {
    if (gInitialized.exchange(true, std::memory_order_acq_rel)) {
        return;
    }
    if (!gRing) {
        gRing = std::unique_ptr<HitRecord[]>(new (std::nothrow) HitRecord[kRingCapacity]());
    }
    gVehHandle = AddVectoredExceptionHandler(1, &watchVeh);
}

void watchShutdown() {
    watchRemoveAll();
    if (gVehHandle != nullptr) {
        RemoveVectoredExceptionHandler(gVehHandle);
        gVehHandle = nullptr;
    }
    gRing.reset();
    gHead.store(0, std::memory_order_relaxed);
    gTotalHits.store(0, std::memory_order_relaxed);
    gInitialized.store(false, std::memory_order_release);
}

nlohmann::json watchInstall(
    std::uint64_t      addr,
    std::uint32_t      size,
    bool               readWrite,
    std::string const& thread,
    std::string const& tag
) {
    watchInit();
    if (size != 1 && size != 2 && size != 4 && size != 8) {
        return errorJson("bad_size");
    }
    if (!writableRange(addr, size)) {
        return errorJson("bad_addr");
    }
    if (size > 1 && (addr % size) != 0) {
        return errorJson("bad_align");
    }

    std::vector<DWORD> targets;
    if (thread == "tick") {
        DWORD const tid = gTickThreadId.load(std::memory_order_relaxed);
        if (tid == 0) {
            return errorJson("no_tick_thread");
        }
        targets.push_back(tid);
    } else if (thread == "all") {
        targets = processThreads();
        if (targets.empty()) {
            return errorJson("no_threads");
        }
    } else if (thread == "server") {
        DWORD const tid = gServerThreadId.load(std::memory_order_relaxed);
        if (tid == 0) {
            return errorJson("no_server_thread");
        }
        targets.push_back(tid);
    } else if (thread == "both") {
        DWORD const tick   = gTickThreadId.load(std::memory_order_relaxed);
        DWORD const server = gServerThreadId.load(std::memory_order_relaxed);
        if (tick != 0) {
            targets.push_back(tick);
        }
        if (server != 0 && server != tick) {
            targets.push_back(server);
        }
        if (targets.empty()) {
            return errorJson("no_threads");
        }
    } else {
        char*                   end    = nullptr;
        unsigned long const     parsed = std::strtoul(thread.c_str(), &end, 0);
        if (end == thread.c_str() || end == nullptr || *end != '\0' || parsed == 0) {
            return errorJson("bad_thread");
        }
        targets.push_back(static_cast<DWORD>(parsed));
    }

    int slot = -1;
    for (int candidate = 0; candidate < static_cast<int>(kSlotCount); ++candidate) {
        if (!gWatches[candidate]) {
            gWatches[candidate] = std::unique_ptr<Watch>(new (std::nothrow) Watch());
        }
        if (gWatches[candidate] && !gWatches[candidate]->used.load(std::memory_order_acquire)) {
            slot = candidate;
            break;
        }
    }
    if (slot < 0) {
        return errorJson("no_free_slot");
    }

    Watch& watch   = *gWatches[slot];
    watch.addr     = addr;
    watch.size     = size;
    watch.readWrite = readWrite;
    watch.slot     = slot;
    watch.tag      = tag;
    watch.hits.store(0, std::memory_order_relaxed);
    watch.id.store(gNextId.fetch_add(1, std::memory_order_relaxed), std::memory_order_relaxed);
    watch.threads.clear();
    // Keep `used` false until installation succeeds: a pre-existing enable bit
    // on this slot must be reported as dr_busy, not treated as ours.
    watch.used.store(false, std::memory_order_release);

    std::string error;
    for (DWORD tid : targets) {
        if (installOnThread(tid, slot, addr, size, readWrite, error)) {
            watch.threads.push_back(tid);
        } else if (thread == "tick") {
            // A watch requested for the tick thread must not silently apply to
            // zero threads.
            for (DWORD installed : watch.threads) {
                clearOnThread(installed, slot);
            }
            watch.threads.clear();
            watch.tag.clear();
            return errorJson(error.empty() ? "install_failed" : error.c_str());
        }
    }
    if (watch.threads.empty()) {
        watch.tag.clear();
        return errorJson(error.empty() ? "install_failed" : error.c_str());
    }

    watch.used.store(true, std::memory_order_release);

    nlohmann::json out;
    out["event"]   = "watch_installed";
    out["id"]      = watch.id.load(std::memory_order_relaxed);
    out["slot"]    = slot;
    out["addr"]    = addr;
    out["size"]    = size;
    out["mode"]    = readWrite ? "rw" : "w";
    out["tag"]     = tag;
    out["threads"] = nlohmann::json::array();
    for (DWORD tid : watch.threads) {
        out["threads"].push_back(tid);
    }
    out["suspends_tick_thread"] = true;
    return out;
}

nlohmann::json watchRemove(int id) {
    for (std::size_t slot = 0; slot < kSlotCount; ++slot) {
        auto& watch = gWatches[slot];
        if (!watch || !watch->used.load(std::memory_order_acquire)) {
            continue;
        }
        if (static_cast<int>(watch->id.load(std::memory_order_relaxed)) != id) {
            continue;
        }
        bool cleared = true;
        for (DWORD tid : watch->threads) {
            cleared = clearOnThread(tid, static_cast<int>(slot)) && cleared;
        }
        if (!cleared) {
            // Keep the slot owned: the DR7 bit is still set, and treating it as
            // free would make the next install fail with dr_busy forever.
            return errorJson("clear_failed");
        }
        watch->threads.clear();
        watch->tag.clear();
        watch->used.store(false, std::memory_order_release);
        return nlohmann::json{
            {"event", "watch_cleared"},
            {"id", id               },
            {"slot", slot           },
        };
    }
    return errorJson("not_found");
}

bool watchRemoveAll() {
    bool allCleared = true;
    for (std::size_t slot = 0; slot < kSlotCount; ++slot) {
        auto& watch = gWatches[slot];
        if (!watch || !watch->used.load(std::memory_order_acquire)) {
            continue;
        }
        bool cleared = true;
        for (DWORD tid : watch->threads) {
            cleared = clearOnThread(tid, static_cast<int>(slot)) && cleared;
        }
        if (!cleared) {
            allCleared = false;
            continue; // keep ownership so a later clear can retry
        }
        watch->threads.clear();
        watch->tag.clear();
        watch->used.store(false, std::memory_order_release);
    }
    return allCleared;
}

nlohmann::json watchListJson() {
    nlohmann::json out;
    out["event"]   = "watch_list";
    out["watches"] = nlohmann::json::array();
    for (std::size_t slot = 0; slot < kSlotCount; ++slot) {
        auto& watch = gWatches[slot];
        if (!watch || !watch->used.load(std::memory_order_acquire)) {
            continue;
        }
        nlohmann::json row;
        row["id"]   = watch->id.load(std::memory_order_relaxed);
        row["slot"] = slot;
        row["addr"] = watch->addr;
        row["size"] = watch->size;
        row["mode"] = watch->readWrite ? "rw" : "w";
        row["tag"]  = watch->tag;
        row["hits"] = watch->hits.load(std::memory_order_relaxed);
        row["threads"] = nlohmann::json::array();
        for (DWORD tid : watch->threads) {
            row["threads"].push_back(tid);
        }
        out["watches"].push_back(std::move(row));
    }
    out["hits"]    = gTotalHits.load(std::memory_order_relaxed);
    out["dropped"] = watchDroppedHits();
    out["head"]    = gHead.load(std::memory_order_acquire);
    return out;
}

nlohmann::json watchDrainJson(std::uint64_t since, std::size_t limit) {
    std::uint64_t const head   = gHead.load(std::memory_order_acquire);
    std::uint64_t const oldest = head > kRingCapacity ? head - kRingCapacity : 0;
    std::uint64_t       start  = since;
    std::uint64_t       lost   = 0;
    if (start < oldest) {
        lost  = oldest - start;
        start = oldest;
    }
    if (start > head) {
        start = head;
    }
    if (limit == 0) {
        limit = 4096;
    }

    nlohmann::json hits = nlohmann::json::array();
    std::size_t    count = 0;
    for (std::uint64_t index = start; index < head && count < limit; ++index) {
        HitRecord const& record = gRing[index % kRingCapacity];
        if (record.seq.load(std::memory_order_acquire) != index + 1) {
            break; // writer reserved this slot but has not published it yet
        }
        std::string tag;
        for (std::size_t slot = 0; slot < kSlotCount; ++slot) {
            if (gWatches[slot]
                && gWatches[slot]->id.load(std::memory_order_relaxed) == record.watchId) {
                tag = gWatches[slot]->tag;
                break;
            }
        }
        nlohmann::json row{
            {"seq",   index + 1                        },
            {"watch", record.watchId                   },
            {"tag",   tag                              },
            {"tid",   record.tid                       },
            {"tick",  record.tick                      },
            {"rip",   record.rip                       },
            {"stack", nlohmann::json::array()          },
        };
        // F7: data breakpoints trap after the store. Give the caller the bytes
        // right before rip so the writing instruction can be decoded offline.
        if (record.rip >= 16) {
            std::array<std::uint8_t, 16> code{};
            if (readMemory(record.rip - 16, code.data(), code.size())) {
                row["code_hex"] = hexEncode(code.data(), code.size());
            }
        }
        for (std::uint32_t frame = 0; frame < record.frameCount && frame < kMaxFrames; ++frame) {
            row["stack"].push_back(record.frames[frame]);
        }
        hits.push_back(std::move(row));
        ++count;
    }

    nlohmann::json out;
    out["event"] = "watch_hits";
    out["since"] = since;
    out["next"]  = start + count;
    out["head"]  = head;
    out["lost"]  = lost;
    out["rip_semantics"] = "next_instruction";
    out["hits"]  = std::move(hits);
    return out;
}

std::size_t watchCount() {
    std::size_t count = 0;
    for (std::size_t slot = 0; slot < kSlotCount; ++slot) {
        if (gWatches[slot] && gWatches[slot]->used.load(std::memory_order_acquire)) {
            ++count;
        }
    }
    return count;
}

std::uint64_t watchTotalHits() { return gTotalHits.load(std::memory_order_relaxed); }

std::uint64_t watchDroppedHits() {
    std::uint64_t const head = gHead.load(std::memory_order_acquire);
    return head > kRingCapacity ? head - kRingCapacity : 0;
}

} // namespace probe::detail
