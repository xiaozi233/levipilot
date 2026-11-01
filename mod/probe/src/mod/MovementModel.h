#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <utility>

namespace probe::detail {

// Values belong to the existing input_cfg protocol. Convert integers only at
// that boundary; non-zero combinations remain gated legacy diagnostics.
enum class ControlInputMode : int {
    Hardware       = 0,
    DirectFlags    = 1,
    Queue          = 2,
    Handler        = 4,
    ToggleFallback = 8,
};

enum class JumpImpulse : int { None = 0, FromGround = 1, PendingScale = 2 };

constexpr bool hasInputMode(ControlInputMode mode, ControlInputMode path) {
    return (static_cast<int>(mode) & static_cast<int>(path)) != 0;
}

constexpr bool hasJumpImpulse(JumpImpulse mode, JumpImpulse path) {
    return (static_cast<int>(mode) & static_cast<int>(path)) != 0;
}

enum class ControlOp : unsigned {
    Bindings,
    KeyDown,
    KeyUp,
    MouseDown,
    MouseUp,
    Turn,
    LookRead,
    Actions,
    Legacy,
    Count
};
enum class ControlTest { None, SehBefore, CppBefore, SehAfter, CppAfter, Unavailable };
enum class ControlIssue : unsigned { Fault, Failed, Unavailable, Count };
inline char const* controlOpName(ControlOp op) {
    constexpr char const* names[] =
        {"bindings", "key_down", "key_up", "mouse_down", "mouse_up", "turn", "look_read", "actions", "legacy"};
    return names[static_cast<unsigned>(op)];
}

// Keep this scope outside the local raw SEH frame. Nested injection restores
// its caller's flag instead of accidentally dropping the enclosing guard.
class InjectionScope {
public:
    explicit InjectionScope(std::atomic_bool& flag) : mFlag(flag), mPrevious(flag.exchange(true)) {}
    ~InjectionScope() { mFlag.store(mPrevious, std::memory_order_release); }
    InjectionScope(InjectionScope const&)            = delete;
    InjectionScope& operator=(InjectionScope const&) = delete;

private:
    std::atomic_bool& mFlag;
    bool              mPrevious;
};

// Turns may come from the platform client queue as well as the player tick.
// A counter prevents overlapping scopes from restoring a stale true flag.
class InjectionCountScope {
public:
    explicit InjectionCountScope(std::atomic<std::uint32_t>& depth) : mDepth(depth) { mDepth.fetch_add(1); }
    ~InjectionCountScope() { mDepth.fetch_sub(1); }
    InjectionCountScope(InjectionCountScope const&)            = delete;
    InjectionCountScope& operator=(InjectionCountScope const&) = delete;

private:
    std::atomic<std::uint32_t>& mDepth;
};

// A failed SDK call may already have delivered its edge. Record presses before
// invoking it and retire releases only after success; retrying key-up is safe.
template <class Apply>
bool trackedInputEdge(bool& held, bool down, Apply&& apply) {
    if (held == down) return true;
    if (down) held = true;
    if (!apply()) return false;
    held = down;
    return true;
}

// Fixed counters support both client-queue turns and tick input. Formatting is
// cold; even a stalled consumer cannot make a hot producer allocate or block.
class ControlDiagnostics {
public:
    static constexpr unsigned Operations = static_cast<unsigned>(ControlOp::Count);
    using Counts                         = std::array<std::uint64_t, 3>;
    void record(ControlOp op, ControlIssue issue) {
        mCounts[static_cast<unsigned>(op)][static_cast<unsigned>(issue)].fetch_add(1, std::memory_order_relaxed);
    }
    Counts take(ControlOp op) {
        Counts result{};
        auto&  counters = mCounts[static_cast<unsigned>(op)];
        for (unsigned i = 0; i < result.size(); ++i) result[i] = counters[i].exchange(0);
        return result;
    }

private:
    std::array<std::array<std::atomic<std::uint64_t>, 3>, Operations> mCounts{};
};

} // namespace probe::detail
