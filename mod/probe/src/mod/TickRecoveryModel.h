#pragma once

#include "mod/TickStreamModel.h"

#include <atomic>
#include <cstdint>
#include <string_view>

namespace probe::detail {

enum class TickPhase : unsigned { Pre, Post };
inline char const* tickPhaseName(TickPhase phase) { return phase == TickPhase::Pre ? "tick_pre" : "tick_post"; }
enum class TickErrorTest { None, Standard, Unknown, LongMessage, InvalidMessage, ClientRead };

struct TickFailure {
    std::uint64_t id = 0, tick = 0;
    TickPhase phase = TickPhase::Pre;
    bool truncated = false;
    FixedText<256> message;
};

// One tick producer; one spool consumer. Fault publication must work even if
// the diagnostic queue is full. Recovery admission is independent of that
// queue, so losing diagnostics can never resume an uncleaned control session.
class TickRecovery {
public:
    enum class Phase : unsigned { Running, Pending, Prepared };
    struct Snapshot {
        std::uint64_t id;
        Phase phase;
    };
    Snapshot snapshot() const {
        auto const state = mState.load(std::memory_order_acquire);
        return {state >> 2, static_cast<Phase>(state & 3)};
    }
    bool active() const { return snapshot().phase != Phase::Running; }
    void fail(TickPhase phase, char const* message, std::uint64_t tick) noexcept {
        TickFailure row;
        row.id = (mState.load(std::memory_order_relaxed) >> 2) + 1;
        row.tick = tick;
        row.phase = phase;
        if (!message) message = "unknown exception";
        std::size_t length = 0;
        while (length < row.message.bytes.size() && message[length]) ++length;
        row.truncated = length >= row.message.bytes.size();
        row.message.assign(std::string_view(message, length));
        mState.store((row.id << 2) | static_cast<unsigned>(Phase::Pending), std::memory_order_release);
        if (!mFailures.push(row)) mLost.fetch_add(1, std::memory_order_relaxed);
    }
    bool prepare(std::uint64_t id) { return transition(id, Phase::Pending, Phase::Prepared); }
    bool finish(std::uint64_t id, bool released, bool freshRequests) {
        return released && freshRequests && transition(id, Phase::Prepared, Phase::Running);
    }
    bool pop(TickFailure& row) { return mFailures.pop(row); }
    std::uint64_t takeLost() { return mLost.exchange(0); }

private:
    bool transition(std::uint64_t id, Phase from, Phase to) {
        auto expected = (id << 2) | static_cast<unsigned>(from);
        return mState.compare_exchange_strong(expected, (id << 2) | static_cast<unsigned>(to),
                                             std::memory_order_acq_rel, std::memory_order_acquire);
    }
    std::atomic<std::uint64_t> mState{0}, mLost{0};
    TickQueue<TickFailure, 32> mFailures;
};

// Cold dispatch only. Queries, stop/unlock, world lifecycle and unknown
// commands keep their existing handlers. These commands could restart one of
// the control sessions being cancelled by recovery.
inline bool blockedDuringTickRecovery(std::string_view command) {
    constexpr std::string_view commands[] = {
        "move", "set", "jump", "look", "click", "input_cfg", "key_event", "mouse_event", "text_input",
        "macro_run", "navigate", "jump_when", "route", "mine", "place", "use", "fly", "togglefly",
        "select_slot", "chat_send", "teleport", "reset", "checkpoint_load",
        "look_at_entity", "attack_entity", "interact_entity",
        "selftest_use_failure", "selftest_task_fault", "selftest_flight", "selftest_teleport",
        "selftest_input_events", "selftest_controls", "selftest_packet_injection", "selftest_packet_template"
    };
    for (auto known : commands) if (command == known) return true;
    return false;
}

// Quiesced for unload: everything is refused except the switch that lifts the
// quiesce state itself, liveness probes, the emergency stop and the one command
// packet that triggers the unload. Keeping these reachable means a failed or
// aborted unload can always be rolled back, and the trigger itself can be sent.
inline bool quiesceExempt(std::string_view command) {
    constexpr std::string_view commands[] = {
        "lifecycle_prepare", "ping", "status", "panic", "chat_send"
    };
    for (auto known : commands) if (command == known) return true;
    return false;
}

} // namespace probe::detail
