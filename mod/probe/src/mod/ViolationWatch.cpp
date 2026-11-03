#include "mod/Internal.h"
#include "mod/ViolationWatchShared.h"

#include "mc/client/gui/oreui/DisconnectScreen.h"

using namespace probe::detail;

// Client protocol-violation and disconnect-screen observation owns these rings.
// Inbound parsing and send-path suspects live in their respective translation
// units; only fixed records cross the hook boundaries.
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
ll::memory::FuncPtr        gRouteOrigin = nullptr;
ll::memory::FuncPtr        gOrigin      = nullptr;
std::atomic_bool           gInstalled{false};
std::atomic_bool           gScreenInstalled{false};
ViolationRing              gRing;
std::atomic_bool           gRingReady{false};
std::atomic<std::uint64_t> gHits{0};
std::atomic<std::uint64_t> gSynthetic{0};
TextRing                   gTextRing;
std::atomic_bool           gTextReady{false};
std::atomic<std::uint64_t> gObservationFaults{0};
std::atomic<std::uint64_t> gTarget{0};

bool initializeRing() {
    if (!gRingReady.load(std::memory_order_acquire)) {
        if (!gRing.initialize()) return false;
        gRingReady.store(true, std::memory_order_release);
    }
    return true;
}

// Hot path: fixed writes into the published ring slot. No allocation, no JSON,
// no locking, and nothing from the callback outlives the call.
void publishViolation(
    int                    response,
    int                    packetId,
    std::error_code const* errorCode,
    std::string const*     context,
    unsigned int           packetSize,
    bool                   synthetic
) {
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
                    record.category   = reinterpret_cast<std::uint64_t>(&errorCode->category());
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
    record.seq       = reservation.index() + 1;
    reservation.publish(record);
}

void publishDisconnectScreen(int reason, std::string const& codeword, std::string const& details) {
    if (!gRingReady.load(std::memory_order_acquire)) return;
    ViolationRecord record{};
    record.tick      = gWatchTick.load(std::memory_order_relaxed);
    record.tid       = GetCurrentThreadId();
    record.response  = reason;
    record.packetId  = -1;
    record.source    = static_cast<std::uint8_t>(ViolationSource::Screen);
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
    record.seq       = reservation.index() + 1;
    reservation.publish(record);
}

std::string detourComposeRoute(
    int                reason,
    std::string const& title,
    std::string const& body,
    std::string const& codeword,
    std::string const& details,
    std::string const& serverMessage,
    std::string const& serverName
) {
    publishDisconnectScreen(reason, codeword, details);
    return reinterpret_cast<
        ComposeRouteFn>(gRouteOrigin)(reason, title, body, codeword, details, serverMessage, serverName);
}

void detourHandleViolation(
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
) {
    if (response != 0) {
        inboundWatchFreeze();
    }
    publishViolation(response, packetId, errorCode, context, packetSize, false);
    reinterpret_cast<HandleViolationFn>(gOrigin)(
        self,
        controller,
        errorCode,
        response,
        packetId,
        context,
        netId,
        clientSubId,
        senderSubId,
        packetSize
    );
}

} // namespace

std::mutex gViolationInstallMutex;

// Copy a whole game string into a preallocated slot. No allocation, no JSON and
// no lock: the callback only memcpys, and the drain formats off-thread.
void publishText(std::uint8_t kind, std::string_view value) {
    if (!gTextReady.load(std::memory_order_acquire)) return;
    auto       reservation = gTextRing.reserve();
    TextRecord record{};
    record.tick             = gWatchTick.load(std::memory_order_relaxed);
    record.kind             = kind;
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

nlohmann::json errorJson(char const* reason) {
    return nlohmann::json{
        {"event",  "error"},
        {"reason", reason }
    };
}

// Cold path: hex for the inbound slice bytes (binary, not printable).
std::string hexText(char const* data, std::size_t size) {
    static char const* digits = "0123456789abcdef";
    std::string        out;
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

std::uint64_t gameModuleBase() { return reinterpret_cast<std::uint64_t>(GetModuleHandleW(nullptr)); }

nlohmann::json violationWatchStart(
    std::uint64_t address,
    bool          allowVa,
    bool          inWorld,
    std::uint64_t loopAddress,
    std::uint64_t assignAddress,
    std::uint64_t dispatchAddress
) {
    std::lock_guard lock(gViolationInstallMutex);
    if (!allowVa) return errorJson("violation_watch_disabled");
    if (!inWorld) return errorJson("not_in_world");
    // The caller passes a runtime address: roles are static VAs under the
    // documented image base, and this packed exe's PE ImageBase was rewritten,
    // so only `module_base + (role_va - documented_base)` maps correctly.
    if (!hookAddressExecutable(address)) return errorJson("not_executable");
    if (!initializeRing()) return errorJson("allocation_failed");
    inboundWatchReset();
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
            return nlohmann::json{
                {"event",  "error"      },
                {"reason", "hook_failed"},
                {"code",   status       }
            };
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
            )
            == 0) {
            gScreenInstalled.store(true, std::memory_order_release);
        } else {
            ProbeMod::getInstance().bootLog("violation watch: screen hook unavailable");
        }
    }
    auto           inbound      = inboundWatchStart(loopAddress, assignAddress, dispatchAddress);
    bool const     screenHooked = gScreenInstalled.load(std::memory_order_acquire);
    nlohmann::json out          = {
        {"event",           "violation_watch"                                                            },
        {"active",          true                                                                         },
        {"already",         already                                                                      },
        {"callback_hooked", true                                                                         },
        {"address",         address                                                                      },
        {"module_base",     gameModuleBase()                                                             },
        {"screen_hooked",   screenHooked                                                                 },
        {"screen_error",    screenHooked ? nlohmann::json(nullptr) : nlohmann::json("screen_hook_failed")},
    };
    out.update(inbound);
    return out;
}

nlohmann::json violationWatchStop() {
    std::lock_guard lock(gViolationInstallMutex);

    bool const callbackBefore = gInstalled.load(std::memory_order_acquire);
    bool const screenBefore   = gScreenInstalled.load(std::memory_order_acquire);
    auto const inboundResult  = inboundWatchStop();
    bool const inboundBefore  = inboundResult.value("inbound_before", false);
    bool const inbound        = inboundResult.value("inbound_hooked", true);
    if (screenBefore
        && ll::memory::unhook(
            ll::memory::toFuncPtr(&OreUI::EntryPoints::DisconnectScreen::composeRoute),
            ll::memory::toFuncPtr(&detourComposeRoute),
            true
        )) {
        gScreenInstalled.store(false, std::memory_order_release);
    }
    if (callbackBefore
        && ll::memory::unhook(
            reinterpret_cast<ll::memory::FuncPtr>(gTarget.load()),
            ll::memory::toFuncPtr(&detourHandleViolation),
            true
        )) {
        gInstalled.store(false, std::memory_order_release);
    }
    // Keep both origin slots alive for SDK chain maintenance and in-flight
    // calls. Do not claim removal when either SDK unhook failed.
    bool const callback = gInstalled.load(std::memory_order_acquire);
    bool const screen   = gScreenInstalled.load(std::memory_order_acquire);
    ProbeMod::getInstance().bootLog(
        callback || screen || inbound ? "violation watch: stop incomplete" : "violation watch: stopped"
    );
    return nlohmann::json{
        {"event",           "violation_watch"                                 },
        {"active",          callback || screen || inbound                     },
        {"callback_hooked", callback                                          },
        {"screen_hooked",   screen                                            },
        {"inbound_hooked",  inbound                                           },
        {"already",         !callbackBefore && !screenBefore && !inboundBefore},
        {"unhooked",        !callback && !screen && !inbound                  }
    };
}

nlohmann::json violationWatchStats() {
    nlohmann::json out = {
        {"event",              "violation_stats"                                                                             },
        {"active",             gInstalled.load(std::memory_order_acquire) || gScreenInstalled.load(std::memory_order_acquire)},
        {"callback_hooked",    gInstalled.load(std::memory_order_acquire)                                                    },
        {"address",            gTarget.load()                                                                                },
        {"screen_hooked",      gScreenInstalled.load(std::memory_order_acquire)                                              },
        {"text_ready",         gTextReady.load(std::memory_order_acquire)                                                    },
        {"text_head",          gTextRing.head()                                                                              },
        {"hits",               gHits.load(std::memory_order_relaxed)                                                         },
        {"synthetic",          gSynthetic.load(std::memory_order_relaxed)                                                    },
        {"observation_faults", gObservationFaults.load(std::memory_order_relaxed)                                            },
        {"capacity",           kViolationCapacity                                                                            },
    };
    inboundWatchStats(out);
    return out;
}

nlohmann::json violationTextJson(std::uint64_t since, std::size_t limit) {
    nlohmann::json rows   = nlohmann::json::array();
    std::uint64_t  head   = gTextRing.head();
    std::uint64_t  cursor = since;
    if (!gTextReady.load(std::memory_order_acquire)) {
        return nlohmann::json{
            {"event",     "violation_text"},
            {"supported", false           },
            {"hits",      rows            },
            {"count",     0               },
            {"head",      0               },
            {"next",      0               }
        };
    }
    std::uint64_t const oldest = head > kTextCapacity ? head - kTextCapacity : 0;
    std::uint64_t       lost   = 0;
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
            {"index",     index + 1                    },
            {"tick",      record.tick                  },
            {"kind",
             record.kind == 2              ? "screen"
             : record.kind == kMemmoveKind ? "memmove"
                                           : "callback"},
            {"len",       record.len                   },
            {"truncated", record.truncated != 0        },
        };
        if (record.kind == kAssignCallerKind && record.len == sizeof(MemmoveBlob)) {
            MemmoveBlob blob{};
            std::memcpy(&blob, record.text, sizeof(blob));
            row["caller"]      = blob.src;
            row["assigned"]    = blob.len;
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
            row["arg1"]       = blob.src;
            row["arg2"]       = blob.len;
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
            row["src_addr"]   = blob.src;
            row["copy_len"]   = blob.len;
            row["before_hex"] = hexText(blob.before, sizeof(blob.before));
            row["after_hex"]  = hexText(blob.after, sizeof(blob.after));
        } else {
            row["text"] = std::string(record.text, record.len);
        }
        rows.push_back(std::move(row));
        ++count;
        cursor = index + 1;
    }
    return nlohmann::json{
        {"event",     "violation_text"},
        {"supported", true            },
        {"since",     since           },
        {"next",      cursor          },
        {"head",      head            },
        {"lost",      lost            },
        {"count",     count           },
        {"hits",      rows            }
    };
}

nlohmann::json violationDrainJson(std::uint64_t since, std::size_t limit) {
    if (!gRingReady.load(std::memory_order_acquire)) {
        return nlohmann::json{
            {"event", "violation_drain"      },
            {"hits",  nlohmann::json::array()},
            {"count", 0                      },
            {"head",  0                      },
            {"next",  0                      },
            {"lost",  0                      }
        };
    }
    return drainRing(gRing, kViolationCapacity, since, limit, "violation_drain");
}

// Builds the same route the error page uses, through the (optionally hooked)
// symbol. Verifies the screen-side record without waiting for a real disconnect.
nlohmann::json disconnectRouteProbe(int reason, std::string const& codeword, std::string const& details) {
    SyntheticViolationScope synthetic;
    std::string             route;
    if (!guardedGameRead([&] {
            route = OreUI::EntryPoints::DisconnectScreen::composeRoute(reason, "", "", codeword, details, "", "");
        }))
        return errorJson("disconnect_route_fault");
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
    std::lock_guard lock(gViolationInstallMutex);
    if (!initializeRing()) return errorJson("allocation_failed");
    std::string const sample = "synthetic violation record";
    publishViolation(3, -1, nullptr, &sample, 0, true);
    return nlohmann::json{
        {"event", "violation_synthetic"},
        {"ok",    true                 }
    };
}

} // namespace probe::detail
