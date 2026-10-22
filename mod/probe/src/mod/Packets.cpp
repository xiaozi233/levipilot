#include "mod/Internal.h"

using namespace probe::detail;


LL_TYPE_INSTANCE_HOOK(
    ProbePacketSendHook,
    ll::memory::HookPriority::Normal,
    LoopbackPacketSender,
    &LoopbackPacketSender::$sendToServer,
    void,
    Packet& packet
) {
    ::probe::ProbeMod::getInstance().onPacketSent(packet);
    origin(packet);
};


namespace probe {

namespace {

// Session-level / login / handshake / resource-pack traffic is never captured,
// even when the caller explicitly asks for it.
constexpr std::int32_t kDeniedIds[] = {
    0,   // KeepAlive
    1,   // Login
    2,   // PlayStatus
    3,   // ServerToClientHandshake
    4,   // ClientToServerHandshake
    5,   // Disconnect
    6,   // ResourcePacksInfo
    7,   // ResourcePackStack
    8,   // ResourcePackClientResponse
    82,  // ResourcePackDataInfo
    83,  // ResourcePackChunkData
    84,  // ResourcePackChunkRequest
    85,  // Transfer
    94,  // SubclientLogin
    143, // NetworkSettings
    193, // RequestNetworkSettings
    340, // ResourcePacksReadyForValidation
};

bool isDeniedPacketId(std::int32_t id) {
    for (std::int32_t denied : kDeniedIds) {
        if (denied == id) {
            return true;
        }
    }
    return false;
}

} // namespace

void ProbeMod::packetPeerTick() {
    mPacketPeerCount.fetch_add(1, std::memory_order_relaxed);
}

void ProbeMod::onPacketSent(Packet& packet) {
    if (!mPacketCapture.load(std::memory_order_relaxed)) {
        return;
    }
    std::int32_t const id = static_cast<std::int32_t>(packet.getId());
    if (isDeniedPacketId(id)) {
        mPacketDenied.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    if (mPacketIdCount > 0) {
        bool allowed = false;
        for (std::size_t index = 0; index < mPacketIdCount; ++index) {
            if (mPacketIds[index] == id) {
                allowed = true;
                break;
            }
        }
        if (!allowed) {
            return;
        }
    }
    // The order counter advances for every allowed packet so sampling is stable.
    std::uint64_t const order  = mPacketSeq.fetch_add(1, std::memory_order_relaxed);
    std::uint32_t const sample = mPacketSample.load(std::memory_order_relaxed);
    if (sample > 1 && (order % sample) != 0) {
        return;
    }
    auto const* ring = mPacketRing.get();
    if (ring == nullptr) {
        return;
    }
    std::uint64_t const index = mPacketHead.fetch_add(1, std::memory_order_acq_rel);
    PacketRecord&       record = mPacketRing[index % kPacketRing];
    record.order    = order;
    record.tick     = mTick.load(std::memory_order_relaxed);
    record.id       = id;
    record.tid      = GetCurrentThreadId();
    record.injected = 0;
    std::atomic_thread_fence(std::memory_order_release);
    record.seq.store(index + 1, std::memory_order_release);

    // B3: serialize only the explicitly whitelisted ids. This is the one place
    // the hook allocates (the first call grows a thread-local buffer), which is
    // why the whitelist is tiny and capture is opt-in.
    bool serialize = false;
    for (std::size_t slot = 0; slot < mPacketSerializeCount; ++slot) {
        if (mPacketSerializeIds[slot] == id) {
            serialize = true;
            break;
        }
    }
    if (!serialize || !mPacketByteRing) {
        return;
    }
    static thread_local std::string buffer;
    buffer.clear();
    BinaryStream stream{buffer};
    packet.write(stream);
    std::uint64_t const byteIndex = mPacketByteHead.fetch_add(1, std::memory_order_acq_rel);
    PacketBytes&        bytes     = mPacketByteRing[byteIndex % kPacketByteRing];
    bytes.order = order;
    bytes.id    = static_cast<std::uint32_t>(id);
    bytes.size  = static_cast<std::uint32_t>(buffer.size());
    bytes.tid   = record.tid;
    std::size_t const copy = std::min<std::size_t>(buffer.size(), sizeof(bytes.data));
    if (copy > 0) {
        std::memcpy(bytes.data, buffer.data(), copy);
    }
    std::atomic_thread_fence(std::memory_order_release);
    bytes.seq.store(byteIndex + 1, std::memory_order_release);
}

nlohmann::json ProbeMod::packetCaptureStart(bool enable, nlohmann::json const& args) {
    if (!enable) {
        packetCaptureStop();
        return nlohmann::json{
            {"event",  "packet_capture"},
            {"active", false           },
        };
    }
    if (!mAllowPacketCapture.load(std::memory_order_relaxed)) {
        return nlohmann::json{
            {"event",  "error"                 },
            {"reason", "packet_capture_disabled"},
            {"hint",   "set allow_packet_capture=true"},
        };
    }

    std::array<std::int32_t, 64> ids{};
    std::size_t                  idCount = 0;
    if (args.contains("ids") && args["ids"].is_array()) {
        for (auto const& item : args["ids"]) {
            if (!item.is_number_integer()) {
                return nlohmann::json{{"event", "error"}, {"reason", "bad_id"}};
            }
            std::int32_t const id = item.get<std::int32_t>();
            if (id < 0 || id > 2000) {
                return nlohmann::json{{"event", "error"}, {"reason", "bad_id"}};
            }
            if (isDeniedPacketId(id)) {
                return nlohmann::json{
                    {"event",  "error"   },
                    {"reason", "denied_id"},
                    {"id",     id        },
                };
            }
            if (idCount >= ids.size()) {
                return nlohmann::json{{"event", "error"}, {"reason", "too_many_ids"}};
            }
            ids[idCount++] = id;
        }
    }
    std::uint32_t const sample =
        std::clamp<std::uint32_t>(args.value("sample", 1u), 1, 10000);

    std::array<std::int32_t, 4> serializeIds{};
    std::size_t                 serializeCount = 0;
    if (args.contains("serialize_ids") && args["serialize_ids"].is_array()) {
        for (auto const& item : args["serialize_ids"]) {
            if (!item.is_number_integer()) {
                return nlohmann::json{{"event", "error"}, {"reason", "bad_id"}};
            }
            std::int32_t const id = item.get<std::int32_t>();
            if (id < 0 || id > 2000 || isDeniedPacketId(id) || serializeCount >= serializeIds.size()) {
                return nlohmann::json{
                    {"event",  "error"    },
                    {"reason", isDeniedPacketId(id) ? "denied_id" : "bad_id"},
                    {"id",     id          },
                };
            }
            serializeIds[serializeCount++] = id;
        }
    }

    packetCaptureStop();
    if (!mPacketRing) {
        mPacketRing = std::unique_ptr<PacketRecord[]>(new (std::nothrow) PacketRecord[kPacketRing]());
        if (!mPacketRing) {
            return nlohmann::json{{"event", "error"}, {"reason", "no_memory"}};
        }
    }
    if (serializeCount > 0 && !mPacketByteRing) {
        mPacketByteRing =
            std::unique_ptr<PacketBytes[]>(new (std::nothrow) PacketBytes[kPacketByteRing]());
        if (!mPacketByteRing) {
            return nlohmann::json{{"event", "error"}, {"reason", "no_memory"}};
        }
    }
    mPacketHead.store(0, std::memory_order_relaxed);
    mPacketSeq.store(0, std::memory_order_relaxed);
    mPacketPeerCount.store(0, std::memory_order_relaxed);
    mPacketDenied.store(0, std::memory_order_relaxed);
    mPacketIds      = ids;
    mPacketIdCount  = idCount;
    mPacketSerializeIds   = serializeIds;
    mPacketSerializeCount = serializeCount;
    mPacketByteHead.store(0, std::memory_order_relaxed);
    mPacketSample.store(sample, std::memory_order_relaxed);

    int const  sendStatus = ProbePacketSendHook::hook();
    bool const sendHooked = sendStatus == 0;
    mPacketSendHooked.store(sendHooked, std::memory_order_release);
    if (!sendHooked) {
        return nlohmann::json{
            {"event",  "error"       },
            {"reason", "hook_failed" },
            {"code",   sendStatus    },
        };
    }
    // Stage A measurement: LocalNetworkPeer::$sendPacket is dllimport-only in
    // this build, so the peer-level byte tap is not reachable via symbols.
    // That is itself the answer: local client->server traffic does not expose a
    // serialized byte buffer through this layer.
    // Measured: a hook_va probe on BatchedNetworkPeer::sendPacket sees 0 calls
    // during local play (loopback bypasses the serialized peer layer).
    bool const peerHooked = false;
    mPacketPeerHooked.store(false, std::memory_order_release);
    mPacketCapture.store(true, std::memory_order_release);

    return nlohmann::json{
        {"event",       "packet_capture"        },
        {"active",      true                    },
        {"send_hooked", sendHooked              },
        {"peer_hooked", peerHooked              },
        {"peer_reason", "peer_send_packet_zero_traffic"},
        {"sample",      sample                  },
        {"ids",         packetCaptureStatus()["ids"]},
        {"serialize_ids", [&] {
            nlohmann::json list = nlohmann::json::array();
            for (std::size_t slot = 0; slot < serializeCount; ++slot) {
                list.push_back(serializeIds[slot]);
            }
            return serializeCount == 0 ? nlohmann::json(nullptr) : list;
        }()},
    };
}

void ProbeMod::packetCaptureStop() {
    mPacketCapture.store(false, std::memory_order_release);
    if (mPacketSendHooked.exchange(false, std::memory_order_acq_rel)) {
        ProbePacketSendHook::unhook();
    }
    mPacketPeerHooked.store(false, std::memory_order_relaxed);
}

nlohmann::json ProbeMod::packetCaptureStatus() {
    std::uint64_t const head = mPacketHead.load(std::memory_order_acquire);
    nlohmann::json      ids  = nlohmann::json::array();
    for (std::size_t index = 0; index < mPacketIdCount; ++index) {
        ids.push_back(mPacketIds[index]);
    }
    return nlohmann::json{
        {"event",        "packet_stats"                                        },
        {"active",       mPacketCapture.load(std::memory_order_relaxed)        },
        {"records",      head                                                  },
        {"dropped",      head > kPacketRing ? head - kPacketRing : 0           },
        {"peer_sends",   mPacketPeerCount.load(std::memory_order_relaxed)      },
        {"denied",       mPacketDenied.load(std::memory_order_relaxed)         },
        {"sample",       mPacketSample.load(std::memory_order_relaxed)         },
        {"send_hooked",  mPacketSendHooked.load(std::memory_order_relaxed)     },
        {"peer_hooked",  mPacketPeerHooked.load(std::memory_order_relaxed)     },
        {"ids",          std::move(ids)                                        },
    };
}

nlohmann::json ProbeMod::packetDrain(std::uint64_t since, std::size_t limit) {
    std::uint64_t const head   = mPacketHead.load(std::memory_order_acquire);
    std::uint64_t const oldest = head > kPacketRing ? head - kPacketRing : 0;
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
    nlohmann::json      rows  = nlohmann::json::array();
    std::size_t         count = 0;
    if (!mPacketRing) {
        return nlohmann::json{{"event", "packet_drain"}, {"hits", nlohmann::json::array()}, {"count", 0}};
    }
    for (std::uint64_t index = start; index < end; ++index) {
        PacketRecord const& record = mPacketRing[index % kPacketRing];
        if (record.seq.load(std::memory_order_acquire) != index + 1) {
            continue; // reserved by another writer, not published yet
        }
        rows.push_back({
            {"index",    index + 1        },
            {"seq",      record.order     },
            {"tick",     record.tick      },
            {"id",       record.id        },
            {"tid",      record.tid       },
            {"injected", record.injected != 0},
        });
        ++count;
    }
    return nlohmann::json{
        {"event", "packet_drain"               },
        {"since", since                        },
        {"next",  end                          },
        {"head",  head                         },
        {"lost",  lost                         },
        {"hits",  std::move(rows)              },
        {"count", count                        },
    };
}

nlohmann::json ProbeMod::packetBytesDrain(std::uint64_t since, std::size_t limit) {
    std::uint64_t const head   = mPacketByteHead.load(std::memory_order_acquire);
    std::uint64_t const oldest = head > kPacketByteRing ? head - kPacketByteRing : 0;
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
        limit = 1024;
    }
    std::uint64_t const end   = std::min(head, start + limit);
    nlohmann::json      rows  = nlohmann::json::array();
    std::size_t         count = 0;
    if (!mPacketByteRing) {
        return nlohmann::json{{"event", "packet_bytes"}, {"hits", std::move(rows)}, {"count", 0}};
    }
    for (std::uint64_t index = start; index < end; ++index) {
        PacketBytes const& record = mPacketByteRing[index % kPacketByteRing];
        if (record.seq.load(std::memory_order_acquire) != index + 1) {
            continue;
        }
        std::size_t const shown = std::min<std::size_t>(record.size, sizeof(record.data));
        rows.push_back({
            {"index",      index + 1     },
            {"seq",        record.order  },
            {"id",         record.id     },
            {"size",       record.size   },
            {"tid",        record.tid    },
            {"hex",        hexEncode(record.data, shown)},
            {"serialized", true          },
        });
        ++count;
    }
    return nlohmann::json{
        {"event", "packet_bytes"},
        {"since", since         },
        {"next",  end           },
        {"head",  head          },
        {"lost",  lost          },
        {"count", count         },
        {"hits",  std::move(rows)},
    };
}

} // namespace probe
