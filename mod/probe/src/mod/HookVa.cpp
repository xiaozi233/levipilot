#include "mod/Internal.h"

#include "ll/api/memory/Hook.h"

#include <intrin.h>
#include <new>

using namespace probe::detail;

namespace probe::detail {

namespace {

constexpr std::size_t kHookSlots        = 4;
constexpr std::size_t kHookRingCapacity = 1u << 12;
constexpr std::size_t kHookMaxFrames    = 4;
constexpr char const* kJumpPendingSymbol = "MobJump::setJumpPendingScale";

using SetJumpPendingScaleFn = void (*)(EntityContext&, float);
using GenericFn             = std::uint64_t (*)(std::uint64_t, std::uint64_t, std::uint64_t, std::uint64_t);

struct HookHit {
    std::atomic<std::uint64_t> seq{0};
    std::uint32_t              hookId     = 0;
    std::uint32_t              tid        = 0;
    std::uint64_t              tick       = 0;
    std::uint64_t              a          = 0;
    std::uint64_t              b          = 0;
    std::uint64_t              c          = 0;
    std::uint64_t              d          = 0;
    std::uint64_t              ret        = 0;
    std::uint32_t              frameCount = 0;
    std::uint32_t              pad        = 0;
    std::uint64_t              frames[kHookMaxFrames]{};
};

struct HookSlot {
    std::atomic_bool           used{false};
    std::atomic<std::uint32_t> id{0};
    std::atomic<std::uint64_t> hits{0};
    std::atomic<std::uint64_t> captured{0};
    // F2: set once the origin (and everything captureHit reads) is published.
    // A detour that runs between the patch and this flag spins briefly instead
    // of silently returning 0.
    std::atomic_bool           ready{false};
    bool                       symbol = false;
    std::string                tag;
    std::string                name;
    std::uint64_t              address = 0;
    std::atomic<std::uint32_t> sample{1};
    ll::memory::FuncPtr        target = nullptr;
    ll::memory::FuncPtr        detour = nullptr;
    std::atomic<SetJumpPendingScaleFn> originTyped{nullptr};
};

std::unique_ptr<HookHit[]>          gRing;
std::atomic<std::uint64_t>          gRingHead{0};
std::atomic<std::uint32_t>          gNextId{1};
HookSlot                            gSlots[kHookSlots];
std::atomic<GenericFn>              gGenericOrigins[kHookSlots]{};
std::mutex                          gHookMutex;

// Bounded wait for the install window. 200k pauses is a few milliseconds on
// current CPUs and far longer than publishing an origin takes.
constexpr int kReadySpinLimit = 200000;

void waitReady(std::size_t slot) {
    for (int spin = 0; spin < kReadySpinLimit; ++spin) {
        if (gSlots[slot].ready.load(std::memory_order_acquire)) {
            return;
        }
        YieldProcessor();
    }
}

std::atomic_bool  gCallPending{false};
std::atomic<float> gCallScale{1.0f};

std::uint32_t floatBits(float value) {
    std::uint32_t out = 0;
    std::memcpy(&out, &value, sizeof(out));
    return out;
}

float bitsFloat(std::uint64_t value) {
    std::uint32_t raw  = static_cast<std::uint32_t>(value);
    float         out  = 0.0f;
    std::memcpy(&out, &raw, sizeof(out));
    return out;
}

nlohmann::json errorJson(char const* reason) {
    return nlohmann::json{
        {"event", "error" },
        {"reason", reason },
    };
}

void ensureRing() {
    if (!gRing) {
        gRing = std::unique_ptr<HookHit[]>(new (std::nothrow) HookHit[kHookRingCapacity]());
    }
}

void captureHit(std::size_t slot, std::uint64_t a, std::uint64_t b, std::uint64_t c, std::uint64_t d) {
    if (!gRing || slot >= kHookSlots) {
        return;
    }
    HookSlot&                  watch = gSlots[slot];
    pathCount(PathCounter::Hook);
    std::uint64_t const        hit   = watch.hits.fetch_add(1, std::memory_order_relaxed) + 1;
    std::uint32_t const        sample = watch.sample.load(std::memory_order_relaxed);
    if (sample > 1 && (hit % sample) != 0) {
        return;
    }
    std::uint64_t const index = gRingHead.fetch_add(1, std::memory_order_acq_rel);
    HookHit&            record = gRing[index % kHookRingCapacity];
    record.hookId              = watch.id.load(std::memory_order_relaxed);
    record.tid                 = GetCurrentThreadId();
    record.tick                = gWatchTick.load(std::memory_order_relaxed);
    record.a                   = a;
    record.b                   = b;
    record.c                   = c;
    record.d                   = d;
    record.ret                 = reinterpret_cast<std::uint64_t>(_ReturnAddress());
    record.frameCount          = static_cast<std::uint32_t>(
        RtlCaptureStackBackTrace(0, kHookMaxFrames, reinterpret_cast<PVOID*>(record.frames), nullptr)
    );
    record.seq.store(index + 1, std::memory_order_release);
    watch.captured.fetch_add(1, std::memory_order_relaxed);
}

template <int Slot>
void detourSetJumpPendingScale(EntityContext& context, float scale) {
    // F2: the patch is live before the origin is published; wait it out rather
    // than returning a fake result. The spin is bounded and install failure
    // sets ready before removing the patch.
    waitReady(static_cast<std::size_t>(Slot));
    captureHit(
        static_cast<std::size_t>(Slot),
        reinterpret_cast<std::uint64_t>(&context),
        floatBits(scale),
        0,
        0
    );
    if (auto origin = gSlots[Slot].originTyped.load(std::memory_order_acquire)) {
        origin(context, scale);
    }
}

ll::memory::FuncPtr typedDetour(std::size_t slot) {
    switch (slot) {
    case 0:
        return ll::memory::toFuncPtr(&detourSetJumpPendingScale<0>);
    case 1:
        return ll::memory::toFuncPtr(&detourSetJumpPendingScale<1>);
    case 2:
        return ll::memory::toFuncPtr(&detourSetJumpPendingScale<2>);
    default:
        return ll::memory::toFuncPtr(&detourSetJumpPendingScale<3>);
    }
}

template <int Slot>
std::uint64_t detourGeneric(std::uint64_t a, std::uint64_t b, std::uint64_t c, std::uint64_t d) {
    waitReady(static_cast<std::size_t>(Slot));
    captureHit(static_cast<std::size_t>(Slot), a, b, c, d);
    GenericFn origin = gGenericOrigins[Slot].load(std::memory_order_acquire);
    return origin ? origin(a, b, c, d) : 0;
}

ll::memory::FuncPtr genericDetour(std::size_t slot) {
    switch (slot) {
    case 0:
        return ll::memory::toFuncPtr(&detourGeneric<0>);
    case 1:
        return ll::memory::toFuncPtr(&detourGeneric<1>);
    case 2:
        return ll::memory::toFuncPtr(&detourGeneric<2>);
    default:
        return ll::memory::toFuncPtr(&detourGeneric<3>);
    }
}

int findFreeSlot() {
    for (int index = 0; index < static_cast<int>(kHookSlots); ++index) {
        if (!gSlots[index].used.load(std::memory_order_acquire)) {
            return index;
        }
    }
    return -1;
}

} // namespace

bool hookAddressExecutable(std::uint64_t addr) {
    if (addr == 0) {
        return false;
    }
    MEMORY_BASIC_INFORMATION info{};
    if (VirtualQuery(reinterpret_cast<LPCVOID>(addr), &info, sizeof(info)) == 0) {
        return false;
    }
    if (info.State != MEM_COMMIT || (info.Protect & PAGE_GUARD) != 0) {
        return false;
    }
    switch (info.Protect & 0xFF) {
    case PAGE_EXECUTE:
    case PAGE_EXECUTE_READ:
    case PAGE_EXECUTE_READWRITE:
    case PAGE_EXECUTE_WRITECOPY:
        return true;
    default:
        return false;
    }
}

nlohmann::json hookInstall(
    std::string const& symbol,
    std::uint64_t      va,
    bool               useVa,
    std::string const& tag,
    std::uint32_t      sample,
    bool               dryRun,
    bool               allowVa,
    bool               inWorld,
    bool               injectFail
) {
    std::uint64_t targetAddress = 0;
    std::string   name;
    if (useVa) {
        targetAddress = va;
        name          = "va";
    } else {
        if (symbol != kJumpPendingSymbol) {
            return errorJson("unknown_symbol");
        }
        targetAddress = reinterpret_cast<std::uint64_t>(&MobJump::setJumpPendingScale);
        name          = symbol;
    }

    bool const executable = hookAddressExecutable(targetAddress);
    if (dryRun) {
        nlohmann::json out{
            {"event",      "hook_dry_run"                       },
            {"address",    targetAddress                        },
            {"executable", executable                           },
            {"abi",        useVa ? "generic4-int" : "typed"     },
            {"instruction_boundary", useVa ? "unverified" : "symbol"},
        };
        out["symbol"] = useVa ? nlohmann::json(nullptr) : nlohmann::json(symbol);
        out["va"]     = useVa ? nlohmann::json(targetAddress) : nlohmann::json(nullptr);
        return out;
    }
    if (!executable) {
        return errorJson("not_executable");
    }
    if (useVa && !allowVa) {
        return errorJson("hook_va_disabled");
    }
    if (!inWorld) {
        // Patching code that the main menu is actively running is unsafe: LL
        // does not suspend threads in that state.
        return errorJson("not_in_world");
    }

    int                slot   = -1;
    ll::memory::FuncPtr target = nullptr;
    ll::memory::FuncPtr detour = nullptr;
    {
        std::lock_guard lock(gHookMutex);
        // F3: one hook per address; a second install on the same VA would
        // trample the first trampoline.
        for (auto const& existing : gSlots) {
            if (existing.used.load(std::memory_order_acquire) && existing.address == targetAddress) {
                return errorJson("hook_duplicate");
            }
        }
        slot = findFreeSlot();
        if (slot < 0) {
            return errorJson("no_free_slot");
        }
        ensureRing();

        HookSlot& watch = gSlots[static_cast<std::size_t>(slot)];
        watch.symbol    = !useVa;
        watch.tag       = tag;
        watch.name      = name;
        watch.address   = targetAddress;
        watch.sample.store(std::max<std::uint32_t>(1, sample), std::memory_order_relaxed);
        watch.target = reinterpret_cast<ll::memory::FuncPtr>(targetAddress);
        watch.detour = useVa ? genericDetour(static_cast<std::size_t>(slot))
                             : typedDetour(static_cast<std::size_t>(slot));
        watch.id.store(gNextId.fetch_add(1, std::memory_order_relaxed), std::memory_order_relaxed);
        watch.hits.store(0, std::memory_order_relaxed);
        watch.captured.store(0, std::memory_order_relaxed);
        watch.originTyped.store(nullptr, std::memory_order_relaxed);
        gGenericOrigins[slot].store(nullptr, std::memory_order_relaxed);
        watch.ready.store(false, std::memory_order_release);
        // Reserve the slot before dropping the lock so a concurrent install
        // cannot pick it.
        watch.used.store(true, std::memory_order_release);
        target = watch.target;
        detour = watch.detour;
    }

    // F2: do not hold gHookMutex across hook(). It suspends the other threads
    // and may allocate the trampoline while a suspended thread owns the heap.
    ll::memory::FuncPtr originRaw = nullptr;
    int const           status    = ll::memory::hook(
        target,
        detour,
        &originRaw,
        ll::memory::HookPriority::Normal,
        true
    );

    HookSlot& watch = gSlots[static_cast<std::size_t>(slot)];
    if (status == 0 && originRaw != nullptr && injectFail) {
        // Test-only: hook() succeeded for real, but pretend it published no
        // origin so the F3 cleanup path (unhook + release) is exercised.
        originRaw = nullptr;
    }
    if (status != 0) {
        // The patch was never applied, so nothing can be executing the detour.
        watch.ready.store(true, std::memory_order_release);
        watch.used.store(false, std::memory_order_release);
        watch.target = nullptr;
        watch.detour = nullptr;
        watch.address = 0;
        watch.tag.clear();
        watch.name.clear();
        return nlohmann::json{
            {"event",  "error"       },
            {"reason", "hook_failed" },
            {"code",   status        },
        };
    }
    if (originRaw == nullptr) {
        // F3: hook() reported success but produced no origin. The patch is in
        // the code, so remove it before releasing the slot. If unhook fails,
        // keep the slot owned so hook_rm/panic can retry.
        watch.ready.store(true, std::memory_order_release);
        if (!ll::memory::unhook(target, detour, true)) {
            return errorJson("unhook_failed");
        }
        watch.used.store(false, std::memory_order_release);
        watch.target = nullptr;
        watch.detour = nullptr;
        watch.address = 0;
        watch.tag.clear();
        watch.name.clear();
        return nlohmann::json{
            {"event",  "error"       },
            {"reason", "hook_failed" },
            {"code",   status        },
        };
    }
    if (useVa) {
        gGenericOrigins[slot].store(reinterpret_cast<GenericFn>(originRaw), std::memory_order_release);
    } else {
        watch.originTyped.store(
            reinterpret_cast<SetJumpPendingScaleFn>(originRaw),
            std::memory_order_release
        );
    }
    // Publish last: every field captureHit reads is visible before a detour
    // stops spinning.
    watch.ready.store(true, std::memory_order_release);

    nlohmann::json out{
        {"event",   "hook_installed"},
        {"id",      watch.id.load() },
        {"slot",    slot            },
        {"address", targetAddress   },
        {"tag",     tag             },
        {"sample",  watch.sample.load() },
        {"abi",     useVa ? "generic4-int" : "typed"},
    };
    out["symbol"] = useVa ? nlohmann::json(nullptr) : nlohmann::json(symbol);
    out["va"]     = useVa ? nlohmann::json(targetAddress) : nlohmann::json(nullptr);
    return out;
}

nlohmann::json hookRemove(int id) {
    std::lock_guard lock(gHookMutex);
    for (std::size_t slot = 0; slot < kHookSlots; ++slot) {
        HookSlot& watch = gSlots[slot];
        if (!watch.used.load(std::memory_order_acquire)
            || static_cast<int>(watch.id.load(std::memory_order_relaxed)) != id) {
            continue;
        }
        bool const removed = ll::memory::unhook(watch.target, watch.detour, true);
        if (!removed) {
            return errorJson("unhook_failed");
        }
        watch.used.store(false, std::memory_order_release);
        watch.target = nullptr;
        watch.detour = nullptr;
        watch.originTyped.store(nullptr, std::memory_order_relaxed);
        watch.address = 0;
        watch.tag.clear();
        watch.name.clear();
        gGenericOrigins[slot].store(nullptr, std::memory_order_relaxed);
        return nlohmann::json{
            {"event", "hook_removed"},
            {"id",    id           },
            {"ok",    true         },
        };
    }
    return errorJson("not_found");
}

int hookRemoveAll() {
    std::lock_guard lock(gHookMutex);
    int             removed = 0;
    for (std::size_t slot = 0; slot < kHookSlots; ++slot) {
        HookSlot& watch = gSlots[slot];
        if (!watch.used.load(std::memory_order_acquire)) {
            continue;
        }
        if (!ll::memory::unhook(watch.target, watch.detour, true)) {
            continue; // keep it owned so the caller can retry
        }
        watch.used.store(false, std::memory_order_release);
        watch.target = nullptr;
        watch.detour = nullptr;
        watch.originTyped.store(nullptr, std::memory_order_relaxed);
        watch.address = 0;
        watch.tag.clear();
        watch.name.clear();
        gGenericOrigins[slot].store(nullptr, std::memory_order_relaxed);
        ++removed;
    }
    return removed;
}

nlohmann::json hookListJson() {
    std::lock_guard lock(gHookMutex);
    nlohmann::json out;
    out["event"]  = "hook_list";
    out["hooks"]  = nlohmann::json::array();
    out["head"]   = gRingHead.load(std::memory_order_acquire);
    std::uint64_t const head = out["head"].get<std::uint64_t>();
    out["dropped"] = head > kHookRingCapacity ? head - kHookRingCapacity : 0;
    for (std::size_t slot = 0; slot < kHookSlots; ++slot) {
        HookSlot& watch = gSlots[slot];
        if (!watch.used.load(std::memory_order_acquire)) {
            continue;
        }
        out["hooks"].push_back({
            {"id",       watch.id.load()              },
            {"slot",     slot                         },
            {"symbol",   watch.symbol ? nlohmann::json(watch.name) : nlohmann::json(nullptr)},
            {"va",       watch.symbol ? nlohmann::json(nullptr) : nlohmann::json(watch.address)},
            {"address",  watch.address                },
            {"tag",      watch.tag                    },
            {"sample",   watch.sample.load()          },
            {"hits",     watch.hits.load()            },
            {"captured", watch.captured.load()        },
        });
    }
    return out;
}

nlohmann::json hookDrainJson(std::uint64_t since, std::size_t limit) {
    std::lock_guard     lock(gHookMutex);
    std::uint64_t const head   = gRingHead.load(std::memory_order_acquire);
    std::uint64_t const oldest = head > kHookRingCapacity ? head - kHookRingCapacity : 0;
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
    std::uint64_t const end   = std::min(head, start + limit);
    nlohmann::json      hits  = nlohmann::json::array();
    for (std::uint64_t index = start; index < end; ++index) {
        HookHit const& record = gRing[index % kHookRingCapacity];
        if (record.seq.load(std::memory_order_acquire) != index + 1) {
            break;
        }
        std::string tag;
        for (std::size_t slot = 0; slot < kHookSlots; ++slot) {
            if (gSlots[slot].id.load(std::memory_order_relaxed) == record.hookId) {
                tag = gSlots[slot].tag;
                break;
            }
        }
        nlohmann::json row{
            {"seq",     index + 1                       },
            {"hook",    record.hookId                   },
            {"tag",     tag                             },
            {"tid",     record.tid                      },
            {"tick",    record.tick                     },
            {"args",    {record.a, record.b, record.c, record.d}},
            {"b_float", bitsFloat(record.b)             },
            {"ret",     record.ret                      },
            {"stack",   nlohmann::json::array()         },
        };
        for (std::uint32_t frame = 0; frame < record.frameCount && frame < kHookMaxFrames; ++frame) {
            row["stack"].push_back(record.frames[frame]);
        }
        hits.push_back(std::move(row));
    }

    nlohmann::json out;
    out["event"] = "hook_hits";
    out["since"] = since;
    out["next"]  = start + static_cast<std::uint64_t>(hits.size());
    out["head"]  = head;
    out["lost"]  = lost;
    out["hits"]  = std::move(hits);
    return out;
}

bool hookRequestCall(std::string const& symbol, float scale, std::string& error) {
    if (symbol != kJumpPendingSymbol) {
        error = "unknown_symbol";
        return false;
    }
    gCallScale.store(scale, std::memory_order_relaxed);
    gCallPending.store(true, std::memory_order_release);
    return true;
}

bool hookTick(LocalPlayer& player, nlohmann::json& result) {
    if (!gCallPending.exchange(false, std::memory_order_acq_rel)) {
        return false;
    }
    float const scale = gCallScale.load(std::memory_order_relaxed);
    MobJump::setJumpPendingScale(player.getEntityContext(), scale);
    result = {
        {"event",  "hook_call_done"      },
        {"symbol", kJumpPendingSymbol    },
        {"scale",  scale                 },
    };
    return true;
}

} // namespace probe::detail
