#pragma once

// Game-independent task ownership. Production translation units include this
// through Internal.h -> ProbeMod.h; standalone tests need no Bedrock SDK.
#include <algorithm>
#include <array>
#include <cstdint>
#include <mutex>
#include <string_view>
#include <type_traits>
#include <variant>

namespace probe {

namespace detail {

template <std::size_t Capacity = 256>
struct FixedText {
    static_assert(Capacity > 0);
    std::array<char, Capacity> bytes{};
    void                       assign(std::string_view text) {
        auto length = std::min(text.size(), Capacity - 1);
        // Game names are UTF-8: never leave an incomplete final code point.
        while (length > 0 && length < text.size() && (static_cast<unsigned char>(text[length]) & 0xC0) == 0x80) {
            --length;
        }
        std::copy_n(text.data(), length, bytes.data());
        bytes[length] = '\0';
    }
    bool             empty() const { return bytes[0] == '\0'; }
    char const*      c_str() const { return bytes.data(); }
    std::string_view view() const { return c_str(); }
};

} // namespace detail

enum class JumpMode : int {
    Auto     = 0,
    Distance = 1,
    Edge     = 2,
    Never    = 3,
    Now      = 4,
    Tick     = 5,
};

struct NavigateTask {
    float    x = 0, z = 0, tolerance = 0.35f;
    bool     sprint   = false;
    int      maxTicks = 600, elapsed = 0, stuckTicks = 0;
    float    lastX = 0, lastZ = 0;
    bool     haveLast     = false;
    JumpMode jumpMode     = JumpMode::Auto;
    float    jumpDistance = 1, edgeLookahead = 0.55f;
    int      jumpCooldown = 0, jumps = 0;
};

struct JumpTask {
    JumpMode mode = JumpMode::Distance;
    float    x = 0, z = 0, distance = 0.6f, forward = 0;
    bool     sprint = false, hasYaw = false;
    float    yaw        = 0;
    int      afterTicks = 1, maxTicks = 60, elapsed = 0;
    float    edgeLookahead = 0.55f;
};

struct RouteWaypoint {
    float    x = 0, z = 0, tolerance = 0.35f;
    int      maxTicks     = 600;
    JumpMode jumpMode     = JumpMode::Auto;
    float    jumpDistance = 1;
    float    edgeLookahead = 0.55f;
};

struct RouteTask {
    static constexpr std::size_t            MaxWaypoints = 64;
    std::array<RouteWaypoint, MaxWaypoints> points{};
    std::size_t                             count = 0;
    int                                     index = 0, elapsed = 0, segmentElapsed = 0, stuckTicks = 0;
    float                                   lastX = 0, lastZ = 0;
    bool                                    haveLast = false, sprint = false;
    int                                     jumpCooldown = 0;
};

struct MineTask {
    int                 x = 0, y = 0, z = 0, maxTicks = 400, elapsed = 0, misses = 0;
    bool                started = false;
    std::uint8_t        face    = 0;
    detail::FixedText<> block;
};

struct PlaceTask {
    int                 x = 0, y = 0, z = 0, againstX = 0, againstY = 0, againstZ = 0;
    int                 face = -1, slot = -1;
    detail::FixedText<> expected;
    int                 maxTicks = 30, elapsed = 0, phase = 0, verifyAt = 0;
    bool                autoAgainst = true;
};

struct UseTask {
    int  ticks = 1, elapsed = 0;
    bool dispatched = false;
};

using TaskState = std::variant<std::monostate, NavigateTask, JumpTask, RouteTask, MineTask, PlaceTask, UseTask>;
static_assert(std::is_trivially_copyable_v<TaskState>);

namespace detail {

inline char const* jumpModeName(JumpMode mode) {
    switch (mode) {
    case JumpMode::Distance:
        return "distance";
    case JumpMode::Edge:
        return "edge";
    case JumpMode::Never:
        return "never";
    case JumpMode::Now:
        return "now";
    case JumpMode::Tick:
        return "tick";
    case JumpMode::Auto:
    default:
        return "auto";
    }
}

inline char const* taskName(TaskState const& state) {
    constexpr char const* names[] = {"", "navigate", "jump_when", "route", "mine", "place", "use"};
    return names[state.index()];
}

struct TaskOutcome {
    bool        ok     = false;
    char const* reason = ""; // Static literals only; never points into a game object.
    float       x = 0, z = 0, distance = 0;
    bool        onGround = false;
    FixedText<> block;
    int         count    = 0;
    int         waypoint = 0, waypointTicks = 0;
};

struct TaskStep {
    bool        finished = false;
    bool        waypoint = false;
    bool        useReady = false;
    TaskOutcome outcome;
};

struct TaskEvent {
    enum class Kind { Completed, Cancelled, Waypoint, UseReady };
    Kind          kind = Kind::Completed;
    std::uint64_t id   = 0;
    TaskState     state;
    TaskOutcome   outcome;
};

struct TaskSnapshot {
    std::uint64_t id = 0;
    TaskState     state;
};

// One lock replaces the old task mutex. A tick and cancellation are atomic
// relative to each other: no stale writes, effects or terminal publication.
// Callbacks must not re-enter this controller. Only the event consumer performs
// formatting, pipe I/O or server-thread scheduling.
class TaskController {
public:
    static constexpr std::size_t EventCapacity = 128;

    std::uint64_t start(TaskState state) {
        return start(state, [] {});
    }

    template <typename Prepare>
    std::uint64_t start(TaskState state, Prepare&& prepare) {
        std::lock_guard lock(mMutex);
        if (state.index() == 0 || freeEvents() < 3) {
            return 0; // Backpressure: leave the current task untouched.
        }
        cancelLocked("replaced");
        prepare();
        mActive = {++mNextId, state};
        return mActive.id;
    }

    void cancel(char const* reason) {
        std::lock_guard lock(mMutex);
        cancelLocked(reason);
    }

    TaskSnapshot snapshot() const {
        std::lock_guard lock(mMutex);
        return mActive;
    }

    template <typename Advance>
    bool tick(Advance&& advance) {
        std::lock_guard lock(mMutex);
        if (mActive.state.index() == 0) {
            return false;
        }
        // Reserve room for a waypoint + completion and a cancellation. Slow
        // consumers pause task progress; they never lose a terminal event.
        if (freeEvents() < 3) {
            return true;
        }
        TaskStep const step = advance(mActive.state, mActive.id);
        if (step.waypoint) {
            push(TaskEvent::Kind::Waypoint, step.outcome);
        }
        if (step.useReady) {
            push(TaskEvent::Kind::UseReady, step.outcome);
        }
        if (step.finished) {
            push(TaskEvent::Kind::Completed, step.outcome);
            mActive = {};
        }
        return true;
    }

    template <typename Complete>
    bool completeUse(std::uint64_t id, Complete&& complete) {
        std::lock_guard lock(mMutex);
        auto const*     use = std::get_if<UseTask>(&mActive.state);
        if (mActive.id != id || !use || !use->dispatched) {
            return false; // Cancelled/replaced queued server work has no effect.
        }
        TaskOutcome outcome;
        try {
            outcome = complete();
        } catch (...) {
            outcome.reason = "server_task_failed";
        }
        push(TaskEvent::Kind::Completed, outcome);
        mActive = {};
        return true;
    }

    bool pop(TaskEvent& event) {
        std::lock_guard lock(mMutex);
        if (mRead == mWrite) {
            return false;
        }
        event = mEvents[mRead++ % EventCapacity];
        return true;
    }

private:
    std::size_t freeEvents() const { return EventCapacity - (mWrite - mRead); }
    void        push(TaskEvent::Kind kind, TaskOutcome const& outcome) {
        mEvents[mWrite++ % EventCapacity] = {kind, mActive.id, mActive.state, outcome};
    }
    void cancelLocked(char const* reason) {
        if (mActive.state.index() != 0) {
            TaskOutcome outcome;
            outcome.reason = reason;
            push(TaskEvent::Kind::Cancelled, outcome);
            mActive = {};
        }
    }

    mutable std::mutex                   mMutex;
    TaskSnapshot                         mActive;
    std::uint64_t                        mNextId = 0;
    std::array<TaskEvent, EventCapacity> mEvents{};
    std::size_t                          mRead = 0, mWrite = 0;
};

} // namespace detail
} // namespace probe
