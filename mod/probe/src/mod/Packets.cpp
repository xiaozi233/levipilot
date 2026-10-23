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

// Recv-side (server -> client) anchors. In single-player the integrated server
// runs in-process, so these hooks observe what the client is about to receive.
// Layer 1: the Packet send helpers used by server code.
LL_TYPE_INSTANCE_HOOK(
    ProbePacketRecvToPlayerHook,
    ll::memory::HookPriority::Normal,
    Packet,
    static_cast<void (Packet::*)(Player const&) const>(&Packet::sendTo),
    void,
    Player const& player
) {
    ::probe::ProbeMod::getInstance().onPacketReceived(*this, ::probe::ProbeMod::kRecvToPlayer);
    origin(player);
};

LL_TYPE_INSTANCE_HOOK(
    ProbePacketRecvToActorHook,
    ll::memory::HookPriority::Normal,
    Packet,
    static_cast<void (Packet::*)(Actor const&, optional_ref<Player const>) const>(&Packet::sendTo),
    void,
    Actor const&               actor,
    optional_ref<Player const> except
) {
    ::probe::ProbeMod::getInstance().onPacketReceived(*this, ::probe::ProbeMod::kRecvToActor);
    origin(actor, except);
};

LL_TYPE_INSTANCE_HOOK(
    ProbePacketRecvToBlockPosHook,
    ll::memory::HookPriority::Normal,
    Packet,
    static_cast<void (Packet::*)(BlockPos const&, DimensionType, optional_ref<Player const>) const>(&Packet::sendTo),
    void,
    BlockPos const&            pos,
    DimensionType              dimId,
    optional_ref<Player const> except
) {
    ::probe::ProbeMod::getInstance().onPacketReceived(*this, ::probe::ProbeMod::kRecvToBlockPos);
    origin(pos, dimId, except);
};

LL_TYPE_INSTANCE_HOOK(
    ProbePacketRecvToClientHook,
    ll::memory::HookPriority::Normal,
    Packet,
    static_cast<void (Packet::*)(NetworkIdentifier const&, SubClientId) const>(&Packet::sendToClient),
    void,
    NetworkIdentifier const& id,
    SubClientId              subId
) {
    ::probe::ProbeMod::getInstance().onPacketReceived(*this, ::probe::ProbeMod::kRecvToClientId);
    origin(id, subId);
};

LL_TYPE_INSTANCE_HOOK(
    ProbePacketRecvToClientSubHook,
    ll::memory::HookPriority::Normal,
    Packet,
    static_cast<void (Packet::*)(NetworkIdentifierWithSubId const&) const>(&Packet::sendToClient),
    void,
    NetworkIdentifierWithSubId const& id
) {
    ::probe::ProbeMod::getInstance().onPacketReceived(*this, ::probe::ProbeMod::kRecvToClientWithSubId);
    origin(id);
};

LL_TYPE_INSTANCE_HOOK(
    ProbePacketRecvToClientsHook,
    ll::memory::HookPriority::Normal,
    Packet,
    static_cast<void (Packet::*)() const>(&Packet::sendToClients),
    void
) {
    ::probe::ProbeMod::getInstance().onPacketReceived(*this, ::probe::ProbeMod::kRecvToClients);
    origin();
};

LL_TYPE_INSTANCE_HOOK(
    ProbePacketRecvToClientsExceptHook,
    ll::memory::HookPriority::Normal,
    Packet,
    static_cast<void (Packet::*)(NetworkIdentifier const&, SubClientId) const>(&Packet::sendToClients),
    void,
    NetworkIdentifier const& exceptId,
    SubClientId              exceptSubId
) {
    ::probe::ProbeMod::getInstance().onPacketReceived(*this, ::probe::ProbeMod::kRecvToClientsExcept);
    origin(exceptId, exceptSubId);
};

// Layer 2: the loopback delivery thunks. These fire once per actual client
// delivery, so they double-count anything that also went through layer 1.
LL_TYPE_INSTANCE_HOOK(
    ProbePacketRecvLoopbackHook,
    ll::memory::HookPriority::Normal,
    LoopbackPacketSender,
    static_cast<void (LoopbackPacketSender::*)(NetworkIdentifier const&, Packet const&, SubClientId)>(
        &LoopbackPacketSender::$sendToClient
    ),
    void,
    NetworkIdentifier const& id,
    Packet const&            packet,
    SubClientId              recipientSubId
) {
    ::probe::ProbeMod::getInstance().onPacketReceived(packet, ::probe::ProbeMod::kRecvLoopbackClient);
    origin(id, packet, recipientSubId);
};

LL_TYPE_INSTANCE_HOOK(
    ProbePacketRecvLoopbackUserHook,
    ll::memory::HookPriority::Normal,
    LoopbackPacketSender,
    static_cast<void (LoopbackPacketSender::*)(UserEntityIdentifierComponent const*, Packet const&)>(
        &LoopbackPacketSender::$sendToClient
    ),
    void,
    UserEntityIdentifierComponent const* user,
    Packet const&                        packet
) {
    ::probe::ProbeMod::getInstance().onPacketReceived(packet, ::probe::ProbeMod::kRecvLoopbackClientUser);
    origin(user, packet);
};

namespace probe {

const char* recvAnchorName(std::size_t anchor) {
    static constexpr char const* kNames[ProbeMod::kRecvAnchorCount] = {
        "sendTo_player",
        "sendTo_actor",
        "sendTo_blockpos",
        "sendToClient_id",
        "sendToClient_subid",
        "sendToClients",
        "sendToClients_except",
        "loopback_sendToClient",
        "loopback_sendToClient_user",
    };
    return anchor < ProbeMod::kRecvAnchorCount ? kNames[anchor] : "unknown";
}

// Session-level / login / handshake / resource-pack traffic is never captured,
// even when the caller explicitly asks for it.
namespace {

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
    if (!mPacketCapture.load(std::memory_order_relaxed) ||
        !mPacketSendEnabled.load(std::memory_order_relaxed)) {
        return;
    }
    if (packet.getId() == MinecraftPacketIds::CommandRequest) {
        captureCommandTemplate(packet);
    }
    recordPacket(packet, kPacketSend);
}

void ProbeMod::onPacketReceived(Packet const& packet, std::size_t anchor) {
    if (anchor < kRecvAnchorCount) {
        mPacketRecvHits[anchor].fetch_add(1, std::memory_order_relaxed);
    }
    if (!mPacketCapture.load(std::memory_order_relaxed) ||
        !mPacketRecvEnabled.load(std::memory_order_relaxed)) {
        return;
    }
    // Both recv layers can fire for one delivery (a helper calling the loopback
    // sender). Collapse repeats of the same object inside one tick; broadcasts
    // to several clients then read as one packet, which is what the identity
    // tap is for. Per-anchor counters still see every raw call.
    std::int32_t const                id         = static_cast<std::int32_t>(packet.getId());
    std::uint64_t const               tick       = mTick.load(std::memory_order_relaxed);
    static thread_local Packet const* lastPacket = nullptr;
    static thread_local std::uint64_t lastTick   = 0;
    static thread_local std::int32_t  lastId     = -1;
    if (&packet == lastPacket && tick == lastTick && id == lastId) {
        return;
    }
    lastPacket = &packet;
    lastTick   = tick;
    lastId     = id;
    recordPacket(packet, kPacketRecv);
    mPacketRecvRecords.fetch_add(1, std::memory_order_relaxed);
}

void ProbeMod::recordPacket(Packet const& packet, std::uint8_t dir) {
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
    record.dir                 = dir;
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
    bytes.dir                     = dir;
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
                return nlohmann::json{
                    {"event",  "error" },
                    {"reason", "bad_id"}
                };
            }
            std::int32_t const id = item.get<std::int32_t>();
            if (id < 0 || id > 2000) {
                return nlohmann::json{
                    {"event",  "error" },
                    {"reason", "bad_id"}
                };
            }
            if (isDeniedPacketId(id)) {
                return nlohmann::json{
                    {"event",  "error"   },
                    {"reason", "denied_id"},
                    {"id",     id        },
                };
            }
            if (idCount >= ids.size()) {
                return nlohmann::json{
                    {"event",  "error"       },
                    {"reason", "too_many_ids"}
                };
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
                return nlohmann::json{
                    {"event",  "error" },
                    {"reason", "bad_id"}
                };
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

    bool sendEnabled = true;
    bool recvEnabled = false;
    if (args.contains("directions")) {
        if (!args["directions"].is_array()) {
            return nlohmann::json{
                {"event",  "error"         },
                {"reason", "bad_directions"}
            };
        }
        sendEnabled = false;
        recvEnabled = false;
        for (auto const& item : args["directions"]) {
            if (!item.is_string()) {
                return nlohmann::json{
                    {"event",  "error"         },
                    {"reason", "bad_directions"}
                };
            }
            std::string const direction = item.get<std::string>();
            if (direction == "send") {
                sendEnabled = true;
            } else if (direction == "recv") {
                recvEnabled = true;
            } else {
                return nlohmann::json{
                    {"event",     "error"         },
                    {"reason",    "bad_directions"},
                    {"direction", direction       },
                };
            }
        }
        if (!sendEnabled && !recvEnabled) {
            return nlohmann::json{
                {"event",  "error"         },
                {"reason", "bad_directions"}
            };
        }
    }

    packetCaptureStop();
    if (!mPacketRing) {
        mPacketRing = std::unique_ptr<PacketRecord[]>(new (std::nothrow) PacketRecord[kPacketRing]());
        if (!mPacketRing) {
            return nlohmann::json{
                {"event",  "error"    },
                {"reason", "no_memory"}
            };
        }
    }
    if (serializeCount > 0 && !mPacketByteRing) {
        mPacketByteRing = std::unique_ptr<PacketBytes[]>(new (std::nothrow) PacketBytes[kPacketByteRing]());
        if (!mPacketByteRing) {
            return nlohmann::json{
                {"event",  "error"    },
                {"reason", "no_memory"}
            };
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
    mPacketRecvRecords.store(0, std::memory_order_relaxed);
    for (auto& hits : mPacketRecvHits) {
        hits.store(0, std::memory_order_relaxed);
    }

    int const  sendStatus = sendEnabled ? ProbePacketSendHook::hook() : -1;
    bool const sendHooked = sendEnabled && sendStatus == 0;
    mPacketSendHooked.store(sendHooked, std::memory_order_release);
    mPacketSendEnabled.store(sendEnabled, std::memory_order_release);
    if (sendEnabled && !sendHooked) {
        return nlohmann::json{
            {"event",  "error"       },
            {"reason", "hook_failed" },
            {"code",   sendStatus    },
        };
    }

    std::array<int, kRecvAnchorCount> recvCodes{};
    recvCodes.fill(-2); // -2 = not requested; 0 = hook installed
    std::size_t recvHookedCount = 0;
    if (recvEnabled) {
        auto install = [&](std::size_t anchor, int code) {
            bool const ok     = code == 0;
            recvCodes[anchor] = code;
            mPacketRecvHooked[anchor].store(ok, std::memory_order_relaxed);
            if (ok) {
                ++recvHookedCount;
            }
        };
        install(kRecvToPlayer, ProbePacketRecvToPlayerHook::hook());
        install(kRecvToActor, ProbePacketRecvToActorHook::hook());
        install(kRecvToBlockPos, ProbePacketRecvToBlockPosHook::hook());
        install(kRecvToClientId, ProbePacketRecvToClientHook::hook());
        install(kRecvToClientWithSubId, ProbePacketRecvToClientSubHook::hook());
        install(kRecvToClients, ProbePacketRecvToClientsHook::hook());
        install(kRecvToClientsExcept, ProbePacketRecvToClientsExceptHook::hook());
        install(kRecvLoopbackClient, ProbePacketRecvLoopbackHook::hook());
        install(kRecvLoopbackClientUser, ProbePacketRecvLoopbackUserHook::hook());
        if (recvHookedCount == 0) {
            mPacketSendEnabled.store(false, std::memory_order_release);
            packetCaptureStop();
            nlohmann::json codes = nlohmann::json::object();
            for (std::size_t anchor = 0; anchor < kRecvAnchorCount; ++anchor) {
                codes[recvAnchorName(anchor)] = recvCodes[anchor];
            }
            return nlohmann::json{
                {"event",  "error"           },
                {"reason", "recv_hook_failed"},
                {"codes",  std::move(codes)  },
            };
        }
    }
    mPacketRecvEnabled.store(recvEnabled, std::memory_order_release);

    // Stage A measurement: LocalNetworkPeer::$sendPacket is dllimport-only in
    // this build, so the peer-level byte tap is not reachable via symbols.
    // That is itself the answer: local client->server traffic does not expose a
    // serialized byte buffer through this layer.
    // Measured: a hook_va probe on BatchedNetworkPeer::sendPacket sees 0 calls
    // during local play (loopback bypasses the serialized peer layer).
    bool const peerHooked = false;
    mPacketPeerHooked.store(false, std::memory_order_release);
    mPacketCapture.store(true, std::memory_order_release);

    nlohmann::json recvAnchors = nlohmann::json::object();
    for (std::size_t anchor = 0; anchor < kRecvAnchorCount; ++anchor) {
        recvAnchors[recvAnchorName(anchor)] = recvCodes[anchor];
    }

    return nlohmann::json{
        {"event",       "packet_capture"        },
        {"active",      true                    },
        {"send_hooked", sendHooked              },
        {"send_enabled",  sendEnabled                    },
        {"recv_enabled",  recvEnabled                    },
        {"recv_hooked",   recvHookedCount > 0            },
        {"recv_anchors",  std::move(recvAnchors)         },
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
    mPacketSendEnabled.store(false, std::memory_order_release);
    mPacketRecvEnabled.store(false, std::memory_order_release);
    if (mPacketSendHooked.exchange(false, std::memory_order_acq_rel)) {
        ProbePacketSendHook::unhook();
    }
    auto release = [&](std::size_t anchor, auto const& unhookFn) {
        if (mPacketRecvHooked[anchor].exchange(false, std::memory_order_acq_rel)) {
            unhookFn();
        }
    };
    release(kRecvToPlayer, [] { ProbePacketRecvToPlayerHook::unhook(); });
    release(kRecvToActor, [] { ProbePacketRecvToActorHook::unhook(); });
    release(kRecvToBlockPos, [] { ProbePacketRecvToBlockPosHook::unhook(); });
    release(kRecvToClientId, [] { ProbePacketRecvToClientHook::unhook(); });
    release(kRecvToClientWithSubId, [] { ProbePacketRecvToClientSubHook::unhook(); });
    release(kRecvToClients, [] { ProbePacketRecvToClientsHook::unhook(); });
    release(kRecvToClientsExcept, [] { ProbePacketRecvToClientsExceptHook::unhook(); });
    release(kRecvLoopbackClient, [] { ProbePacketRecvLoopbackHook::unhook(); });
    release(kRecvLoopbackClientUser, [] { ProbePacketRecvLoopbackUserHook::unhook(); });
    mPacketPeerHooked.store(false, std::memory_order_relaxed);
}

nlohmann::json ProbeMod::packetCaptureStatus() {
    std::uint64_t const head = mPacketHead.load(std::memory_order_acquire);
    nlohmann::json      ids  = nlohmann::json::array();
    for (std::size_t index = 0; index < mPacketIdCount; ++index) {
        ids.push_back(mPacketIds[index]);
    }
    nlohmann::json anchors   = nlohmann::json::object();
    std::size_t    recvHooks = 0;
    for (std::size_t anchor = 0; anchor < kRecvAnchorCount; ++anchor) {
        bool const hooked = mPacketRecvHooked[anchor].load(std::memory_order_relaxed);
        if (hooked) {
            ++recvHooks;
        }
        anchors[recvAnchorName(anchor)] = nlohmann::json{
            {"hooked", hooked                                                 },
            {"hits",   mPacketRecvHits[anchor].load(std::memory_order_relaxed)},
        };
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
        {"send_enabled", mPacketSendEnabled.load(std::memory_order_relaxed)},
        {"recv_enabled", mPacketRecvEnabled.load(std::memory_order_relaxed)},
        {"recv_hooked",  recvHooks > 0                                     },
        {"recv_hooks",   recvHooks                                         },
        {"recv_records", mPacketRecvRecords.load(std::memory_order_relaxed)},
        {"recv_anchors", std::move(anchors)                                },
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
            {"dir",      record.dir == kPacketRecv ? "recv" : "send"},
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
            {"dir", record.dir == kPacketRecv ? "recv" : "send"},
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

void ProbeMod::tickPacketInject(LocalPlayer& player) {
    std::string text;
    {
        std::lock_guard lock(mChatMutex);
        if (mChatPending.empty()) {
            return;
        }
        text.swap(mChatPending);
    }
    std::string error;
    bool const  ok = sendCommandPacket(player, text, error);
    mChatLastResult.store(ok ? 0 : 1, std::memory_order_relaxed);
    if (ok) {
        mChatSent.fetch_add(1, std::memory_order_relaxed);
    }
    nlohmann::json event{
        {"event", "chat_sent"},
        {"ok",    ok         },
        {"text",  text       },
    };
    if (!ok) {
        event["reason"] = error;
    }
    enqueue(event.dump());
}

void ProbeMod::captureCommandTemplate(Packet& packet) {
    if (!mAllowPacketInject.load(std::memory_order_relaxed)) {
        return;
    }
    try {
        // CommandRequestPacket publicly inherits its payload, so a copy of the
        // payload (including the player origin) is a valid template.
        CommandRequestPacketPayload copy =
            static_cast<CommandRequestPacketPayload const&>(static_cast<CommandRequestPacket&>(packet));
        std::lock_guard lock(mCommandTemplateMutex);
        mCommandTemplate = std::move(copy);
    } catch (...) {
        // Template capture is best effort; chat_send will report no template.
    }
}

bool ProbeMod::sendCommandPacket(LocalPlayer& player, std::string const& text, std::string& error) {
    (void)player;
    try {
        std::optional<CommandRequestPacketPayload> payload;
        {
            std::lock_guard lock(mCommandTemplateMutex);
            if (!mCommandTemplate) {
                error = "no_command_template";
                return false;
            }
            payload = *mCommandTemplate;
        }
        payload->mCommand        = text;
        payload->mVersion        = CurrentCmdVersion::Latest;
        payload->mInternalSource = false;
        CommandRequestPacket packet{std::move(*payload)};
        packet.sendToServer();
        return true;
    } catch (std::exception const& e) {
        error = e.what();
    } catch (...) {
        error = "unknown exception";
    }
    return false;
}

} // namespace probe
