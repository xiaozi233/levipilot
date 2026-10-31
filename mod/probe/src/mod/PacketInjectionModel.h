#pragma once

#include "mod/AtomicRing.h"
#include "mod/PlayerIdentityModel.h"
#include "mod/StagedCapture.h"

#include <algorithm>
#include <array>
#include <string>
#include <string_view>

namespace probe::detail {

enum class CommandTemplateError : std::uint64_t { None, Fault, TooLong, NoOwner };

// Only value fields cross the hook boundary. Never copy the SDK payload or its
// owning command/requestId strings in a send hook. The origin UUID is opaque;
// owner is a separate snapshot of the local player's identity.
struct CommandTemplate {
    PlayerIdentity owner;
    std::uint64_t generation = 0, uuidA = 0, uuidB = 0;
    std::int64_t playerId = 0;
    std::uint64_t type = 0, requestIdSize = 0;
    CommandTemplateError error = CommandTemplateError::None;
    std::array<char, 512> requestId{};

    void setRequestId(std::string_view value) {
        if (value.size() > requestId.size()) { error = CommandTemplateError::TooLong; return; }
        requestIdSize = value.size();
        std::copy(value.begin(), value.end(), requestId.begin());
    }
    std::string_view requestIdView() const { return {requestId.data(), requestIdSize}; }
};

class CommandTemplateStore {
    using Ring = AtomicRing<CommandTemplate, 64>;
public:
    bool initialize() { return mRing.initialize(); } // Cold, before installing hooks.
    template <class Read>
    void capture(PlayerIdentity owner, std::uint64_t generation, Read&& read) {
        auto slot = mRing.reserve(); // An in-progress attempt supersedes the old template.
        CommandTemplate value;
        value.owner = owner;
        value.generation = generation;
        try {
            if (!owner.valid) value.error = CommandTemplateError::NoOwner;
            else if (!read(value)) value.error = CommandTemplateError::Fault;
        } catch (...) { value.error = CommandTemplateError::Fault; }
        slot.publish(value);
    }
    char const* latest(CommandTemplate& out, PlayerIdentity owner, std::uint64_t generation) const {
        auto const head = mRing.head();
        if (!head) return "no_command_template";
        CommandTemplate value;
        if (mRing.read(head - 1, value) != Ring::ReadResult::ready || head != mRing.head())
            return "command_template_busy";
        if (value.generation != generation || value.owner.epoch != owner.epoch)
            return "command_template_stale";
        if (value.error == CommandTemplateError::Fault) return "command_template_fault";
        if (value.error == CommandTemplateError::TooLong) return "command_template_too_long";
        if (value.error == CommandTemplateError::NoOwner || !owner.valid || !value.owner.valid
            || value.owner.uuidA != owner.uuidA || value.owner.uuidB != owner.uuidB)
            return "command_template_owner_mismatch";
        out = value;
        return nullptr;
    }
    std::uint64_t attempts() const { return mRing.head(); }
private:
    Ring mRing; // Never reset while hooks can be running; epochs invalidate old rows.
};

enum class PacketInjectionStage { Client, Complete };
enum class PacketInjectionTest { None, Seh, Cpp, Deferred, Queue, AfterSend, Obsolete, WrongOwner };
struct PacketInjectionQuery {
    PlayerIdentity owner;
    CommandTemplate origin;
    std::string text; // Cold request ownership; only the platform callback uses it.
    std::uint64_t id = 0, generation = 0, deadline = 0;
    PacketInjectionTest test = PacketInjectionTest::None;
    char const* templateError = nullptr;
};
struct PacketInjectionSnapshot {
    bool sendStarted = false, sent = false;
    char const* error = nullptr;
};
using PacketInjectionCapture = StagedCapture<PacketInjectionQuery, PacketInjectionSnapshot, PacketInjectionStage>;

inline char const* packetInjectionAdmission(PacketInjectionQuery const& query, std::uint64_t generation,
                                            std::uint64_t epoch, bool inWorld, std::uint64_t now) {
    if (query.generation != generation || !query.owner.matches(epoch, inWorld)) return "world_left";
    if (now >= query.deadline) return "packet_inject_timeout";
    auto const& source = query.origin;
    if (source.generation != generation || !source.owner.matches(epoch, inWorld)) return "command_template_stale";
    if (source.owner.uuidA != query.owner.uuidA || source.owner.uuidB != query.owner.uuidB)
        return "command_template_owner_mismatch";
    return nullptr;
}

// Recheck immediately before sending. Already-started SDK calls cannot be
// cancelled or rolled back; an uncertain send must never be retried.
template <class Admit, class Prepare, class Send, class Fault>
void applyPacketInjection(PacketInjectionSnapshot& out, Admit&& admit, Prepare&& prepare, Send&& send, Fault&& fault) {
    if ((out.error = admit())) return;
    fault(PacketInjectionTest::Seh);
    if ((out.error = prepare())) return;
    if ((out.error = admit())) return;
    out.sendStarted = true;
    send();
    out.sent = true;
    fault(PacketInjectionTest::AfterSend);
}

} // namespace probe::detail
