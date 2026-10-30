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
    ::probe::ProbeMod::getInstance().onPacketReceived(*this, ::probe::PacketTap::kRecvToPlayer);
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
    ::probe::ProbeMod::getInstance().onPacketReceived(*this, ::probe::PacketTap::kRecvToActor);
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
    ::probe::ProbeMod::getInstance().onPacketReceived(*this, ::probe::PacketTap::kRecvToBlockPos);
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
    ::probe::ProbeMod::getInstance().onPacketReceived(*this, ::probe::PacketTap::kRecvToClientId);
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
    ::probe::ProbeMod::getInstance().onPacketReceived(*this, ::probe::PacketTap::kRecvToClientWithSubId);
    origin(id);
};

LL_TYPE_INSTANCE_HOOK(
    ProbePacketRecvToClientsHook,
    ll::memory::HookPriority::Normal,
    Packet,
    static_cast<void (Packet::*)() const>(&Packet::sendToClients),
    void
) {
    ::probe::ProbeMod::getInstance().onPacketReceived(*this, ::probe::PacketTap::kRecvToClients);
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
    ::probe::ProbeMod::getInstance().onPacketReceived(*this, ::probe::PacketTap::kRecvToClientsExcept);
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
    ::probe::ProbeMod::getInstance().onPacketReceived(packet, ::probe::PacketTap::kRecvLoopbackClient);
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
    ::probe::ProbeMod::getInstance().onPacketReceived(packet, ::probe::PacketTap::kRecvLoopbackClientUser);
    origin(user, packet);
};

namespace probe {


const char* recvAnchorName(std::size_t anchor) {
    static constexpr char const* kNames[PacketTap::kRecvAnchorCount] = {
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
    return anchor < PacketTap::kRecvAnchorCount ? kNames[anchor] : "unknown";
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

void PacketTap::peerTick() {
    mPeerCount.fetch_add(1, std::memory_order_relaxed);
}

void PacketTap::onSent(Packet& packet, std::uint64_t tick) {
    detail::CallbackGate::Guard scope{mWriters};
    if (!scope) {
        return;
    }
    if (!mCapture.load(std::memory_order_relaxed) ||
        !mSendEnabled.load(std::memory_order_relaxed)) {
        return;
    }
    recordPacket(packet, kSend, tick);
}

void PacketTap::onReceived(Packet const& packet, std::size_t anchor, std::uint64_t tick) {
    detail::CallbackGate::Guard scope{mWriters};
    if (!scope) {
        return;
    }
    if (anchor < kRecvAnchorCount) {
        mRecvHits[anchor].fetch_add(1, std::memory_order_relaxed);
    }
    if (!mCapture.load(std::memory_order_relaxed) ||
        !mRecvEnabled.load(std::memory_order_relaxed)) {
        return;
    }
    // Both recv layers can fire for one delivery (a helper calling the loopback
    // sender). Collapse repeats of the same object inside one tick; broadcasts
    // to several clients then read as one packet, which is what the identity
    // tap is for. Per-anchor counters still see every raw call.
    std::int32_t const                id         = static_cast<std::int32_t>(packet.getId());
    static thread_local Packet const* lastPacket = nullptr;
    static thread_local std::uint64_t lastTick   = 0;
    static thread_local std::int32_t  lastId     = -1;
    if (&packet == lastPacket && tick == lastTick && id == lastId) {
        return;
    }
    lastPacket = &packet;
    lastTick   = tick;
    lastId     = id;
    recordPacket(packet, kRecv, tick);
    mRecvRecords.fetch_add(1, std::memory_order_relaxed);
}

void PacketTap::recordPacket(Packet const& packet, std::uint8_t dir, std::uint64_t tick) {
    std::int32_t const id = static_cast<std::int32_t>(packet.getId());
    if (isDeniedPacketId(id)) {
        mDenied.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    if (mIdCount > 0) {
        bool allowed = false;
        for (std::size_t index = 0; index < mIdCount; ++index) {
            if (mIds[index] == id) {
                allowed = true;
                break;
            }
        }
        if (!allowed) {
            return;
        }
    }
    // The order counter advances for every allowed packet so sampling is stable.
    std::uint64_t const order  = mSeq.fetch_add(1, std::memory_order_relaxed);
    std::uint32_t const sample = mSample.load(std::memory_order_relaxed);
    if (sample > 1 && (order % sample) != 0) {
        return;
    }
    if (!mRing) {
        return;
    }
    auto reservation = mRing->reserve();
    PacketRecord record{};
    record.order    = order;
    record.tick     = tick;
    record.id       = id;
    record.tid      = GetCurrentThreadId();
    record.dir      = dir;
    record.injected = 0;
    reservation.publish(record);

    // B3: serialize only the explicitly whitelisted ids. This is the one place
    // the hook allocates (the first call grows a thread-local buffer), which is
    // why the whitelist is tiny and capture is opt-in.
    bool serialize = false;
    for (std::size_t slot = 0; slot < mSerializeCount; ++slot) {
        if (mSerializeIds[slot] == id) {
            serialize = true;
            break;
        }
    }
    if (!serialize || !mByteRing) {
        return;
    }
    static thread_local std::string buffer;
    buffer.clear();
    BinaryStream stream{buffer};
    packet.write(stream);
    auto byteReservation = mByteRing->reserve();
    PacketBytes bytes{};
    bytes.order = order;
    bytes.id    = static_cast<std::uint32_t>(id);
    bytes.size  = static_cast<std::uint32_t>(buffer.size());
    bytes.tid   = record.tid;
    bytes.dir   = dir;
    std::size_t const copy = std::min<std::size_t>(buffer.size(), sizeof(bytes.data));
    if (copy > 0) {
        std::memcpy(bytes.data, buffer.data(), copy);
    }
    byteReservation.publish(bytes);
}

nlohmann::json PacketTap::start(bool enable, nlohmann::json const& args) {
    if (!enable) {
        stop();
        return nlohmann::json{
            {"event",  "packet_capture"},
            {"active", false           },
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

    stop();
    if (!mRing) {
        mRing = std::unique_ptr<RecordRing>(new (std::nothrow) RecordRing());
        if (!mRing || !mRing->initialize()) {
            return nlohmann::json{
                {"event",  "error"    },
                {"reason", "no_memory"}
            };
        }
    }
    if (serializeCount > 0 && !mByteRing) {
        mByteRing = std::unique_ptr<ByteRing>(new (std::nothrow) ByteRing());
        if (!mByteRing || !mByteRing->initialize()) {
            return nlohmann::json{
                {"event",  "error"    },
                {"reason", "no_memory"}
            };
        }
    }
    mRing->reset();
    mSeq.store(0, std::memory_order_relaxed);
    mPeerCount.store(0, std::memory_order_relaxed);
    mDenied.store(0, std::memory_order_relaxed);
    mIds      = ids;
    mIdCount  = idCount;
    mSerializeIds   = serializeIds;
    mSerializeCount = serializeCount;
    if (mByteRing) {
        mByteRing->reset();
    }
    mSample.store(sample, std::memory_order_relaxed);
    mRecvRecords.store(0, std::memory_order_relaxed);
    for (auto& hits : mRecvHits) {
        hits.store(0, std::memory_order_relaxed);
    }

    int const  sendStatus = sendEnabled ? ProbePacketSendHook::hook() : -1;
    bool const sendHooked = sendEnabled && sendStatus == 0;
    mSendHooked.store(sendHooked, std::memory_order_release);
    mSendEnabled.store(sendEnabled, std::memory_order_release);
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
            mRecvHooked[anchor].store(ok, std::memory_order_relaxed);
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
            mSendEnabled.store(false, std::memory_order_release);
            stop();
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
    mRecvEnabled.store(recvEnabled, std::memory_order_release);

    // Stage A measurement: LocalNetworkPeer::$sendPacket is dllimport-only in
    // this build, so the peer-level byte tap is not reachable via symbols.
    // That is itself the answer: local client->server traffic does not expose a
    // serialized byte buffer through this layer.
    // Measured: a hook_va probe on BatchedNetworkPeer::sendPacket sees 0 calls
    // during local play (loopback bypasses the serialized peer layer).
    bool const peerHooked = false;
    mPeerHooked.store(false, std::memory_order_release);
    mWriters.open();
    mCapture.store(true, std::memory_order_release);

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
        {"ids",         status()["ids"]},
        {"serialize_ids", [&] {
            nlohmann::json list = nlohmann::json::array();
            for (std::size_t slot = 0; slot < serializeCount; ++slot) {
                list.push_back(serializeIds[slot]);
            }
            return serializeCount == 0 ? nlohmann::json(nullptr) : list;
        }()},
    };
}

void PacketTap::stop() {
    mCapture.store(false, std::memory_order_release);
    mSendEnabled.store(false, std::memory_order_release);
    mRecvEnabled.store(false, std::memory_order_release);
    mWriters.closeAndWait();
    if (mSendHooked.exchange(false, std::memory_order_acq_rel)) {
        ProbePacketSendHook::unhook();
    }
    auto release = [&](std::size_t anchor, auto const& unhookFn) {
        if (mRecvHooked[anchor].exchange(false, std::memory_order_acq_rel)) {
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
    mPeerHooked.store(false, std::memory_order_relaxed);
}

nlohmann::json PacketTap::status() {
    std::uint64_t const head = mRing ? mRing->head() : 0;
    nlohmann::json      ids  = nlohmann::json::array();
    for (std::size_t index = 0; index < mIdCount; ++index) {
        ids.push_back(mIds[index]);
    }
    nlohmann::json anchors   = nlohmann::json::object();
    std::size_t    recvHooks = 0;
    for (std::size_t anchor = 0; anchor < kRecvAnchorCount; ++anchor) {
        bool const hooked = mRecvHooked[anchor].load(std::memory_order_relaxed);
        if (hooked) {
            ++recvHooks;
        }
        anchors[recvAnchorName(anchor)] = nlohmann::json{
            {"hooked", hooked                                                 },
            {"hits",   mRecvHits[anchor].load(std::memory_order_relaxed)},
        };
    }
    return nlohmann::json{
        {"event",        "packet_stats"                                        },
        {"active",       mCapture.load(std::memory_order_relaxed)        },
        {"records",      head                                                  },
        {"dropped",      head > kRing ? head - kRing : 0           },
        {"peer_sends",   mPeerCount.load(std::memory_order_relaxed)      },
        {"denied",       mDenied.load(std::memory_order_relaxed)         },
        {"sample",       mSample.load(std::memory_order_relaxed)         },
        {"send_hooked",  mSendHooked.load(std::memory_order_relaxed)     },
        {"send_enabled", mSendEnabled.load(std::memory_order_relaxed)},
        {"recv_enabled", mRecvEnabled.load(std::memory_order_relaxed)},
        {"recv_hooked",  recvHooks > 0                                     },
        {"recv_hooks",   recvHooks                                         },
        {"recv_records", mRecvRecords.load(std::memory_order_relaxed)},
        {"recv_anchors", std::move(anchors)                                },
        {"peer_hooked",  mPeerHooked.load(std::memory_order_relaxed)     },
        {"ids",          std::move(ids)                                        },
    };
}

nlohmann::json PacketTap::drain(std::uint64_t since, std::size_t limit) {
    std::uint64_t const head   = mRing ? mRing->head() : 0;
    std::uint64_t const oldest = head > kRing ? head - kRing : 0;
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
    nlohmann::json      rows  = nlohmann::json::array();
    std::size_t         count = 0;
    if (!mRing) {
        return nlohmann::json{{"event", "packet_drain"}, {"hits", nlohmann::json::array()}, {"count", 0}};
    }
    std::uint64_t cursor = start;
    for (std::uint64_t index = start; index < head && count < limit; ++index) {
        PacketRecord record{};
        auto const read = mRing->read(index, record);
        if (read == RecordRing::ReadResult::pending) {
            cursor = index;
            break;
        }
        if (read == RecordRing::ReadResult::overwritten) {
            ++lost;
            cursor = index + 1;
            continue;
        }
        if (read == RecordRing::ReadResult::dropped) {
            ++lost;
            cursor = index + 1;
            continue;
        }
        rows.push_back({
            {"index",    index + 1        },
            {"seq",      record.order     },
            {"tick",     record.tick      },
            {"id",       record.id        },
            {"tid",      record.tid       },
            {"dir",      record.dir == kRecv ? "recv" : "send"},
            {"injected", record.injected != 0},
        });
        ++count;
        cursor = index + 1;
    }
    return nlohmann::json{
        {"event", "packet_drain"               },
        {"since", since                        },
        {"next",  cursor                       },
        {"head",  head                         },
        {"lost",  lost                         },
        {"hits",  std::move(rows)              },
        {"count", count                        },
    };
}

nlohmann::json PacketTap::bytesDrain(std::uint64_t since, std::size_t limit) {
    std::uint64_t const head   = mByteRing ? mByteRing->head() : 0;
    std::uint64_t const oldest = head > kByteRing ? head - kByteRing : 0;
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
    nlohmann::json      rows  = nlohmann::json::array();
    std::size_t         count = 0;
    if (!mByteRing) {
        return nlohmann::json{{"event", "packet_bytes"}, {"hits", std::move(rows)}, {"count", 0}};
    }
    std::uint64_t cursor = start;
    for (std::uint64_t index = start; index < head && count < limit; ++index) {
        PacketBytes record{};
        auto const read = mByteRing->read(index, record);
        if (read == ByteRing::ReadResult::pending) {
            cursor = index;
            break;
        }
        if (read == ByteRing::ReadResult::overwritten) {
            ++lost;
            cursor = index + 1;
            continue;
        }
        if (read == ByteRing::ReadResult::dropped) {
            ++lost;
            cursor = index + 1;
            continue;
        }
        std::size_t const shown = std::min<std::size_t>(record.size, sizeof(record.data));
        rows.push_back({
            {"index",      index + 1     },
            {"seq",        record.order  },
            {"id",         record.id     },
            {"size",       record.size   },
            {"tid",        record.tid    },
            {"dir", record.dir == kRecv ? "recv" : "send"},
            {"hex",        hexEncode(record.data, shown)},
            {"serialized", true          },
        });
        ++count;
        cursor = index + 1;
    }
    return nlohmann::json{
        {"event", "packet_bytes"},
        {"since", since         },
        {"next",  cursor        },
        {"head",  head          },
        {"lost",  lost          },
        {"count", count         },
        {"hits",  std::move(rows)},
    };
}

} // namespace probe
