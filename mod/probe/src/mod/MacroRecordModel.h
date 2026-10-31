#pragma once

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <optional>
#include <thread>
#include <vector>

namespace probe {

struct MacroTick {
    bool                w = false, a = false, s = false, d = false;
    bool                sprint = false, sneak = false, jump = false, rmb = false, lmb = false;
    float               yaw = 0, pitch = 0;
    [[nodiscard]] float forward() const { return (w ? 1.0f : 0.0f) - (s ? 1.0f : 0.0f); }
    [[nodiscard]] float strafe() const { return (d ? 1.0f : 0.0f) - (a ? 1.0f : 0.0f); }
};
using Macro = std::vector<MacroTick>;

namespace detail {

inline constexpr std::uint64_t MaxMacroRecordedTicks = 200000;

constexpr std::uint64_t macroRecordingLimit(std::uint64_t requested) {
    return requested == 0 ? MaxMacroRecordedTicks : std::min(requested, MaxMacroRecordedTicks);
}

inline bool consumeMacroClick(std::atomic_bool const& held, std::atomic_bool& latch) {
    bool const edge = latch.exchange(false);
    return held.load() || edge;
}

enum class MacroRecordEnd { None, Client, Limit, CaptureFault, CaptureFailed, WorldLeft, Disabled };
inline char const* macroRecordEndName(MacroRecordEnd reason) {
    switch (reason) {
    case MacroRecordEnd::None:
        return "none";
    case MacroRecordEnd::Client:
        return "client";
    case MacroRecordEnd::Limit:
        return "max_ticks";
    case MacroRecordEnd::CaptureFault:
        return "capture_fault";
    case MacroRecordEnd::CaptureFailed:
        return "capture_failed";
    case MacroRecordEnd::WorldLeft:
        return "world_left";
    case MacroRecordEnd::Disabled:
        return "disabled";
    }
    return "unknown";
}
inline bool macroRecordSucceeded(MacroRecordEnd reason) {
    return reason == MacroRecordEnd::Client || reason == MacroRecordEnd::Limit;
}

// One game-thread producer. Cold commands/consumer serialize externally.
// Begin allocates and initializes all frames before publication; the producer
// never grows a vector, acquires a lock, formats text or waits for a consumer.
class MacroRecordSession {
    static constexpr unsigned Active = 1, Capturing = 2;

public:
    struct Sample {
        MacroTick buttons;
        float     yaw = 0, pitch = 0;
    };
    struct Completion {
        std::uint64_t  recordId = 0, rows = 0;
        MacroRecordEnd reason = MacroRecordEnd::None;
        bool           ok() const { return macroRecordSucceeded(reason); }
    };
    struct Snapshot {
        Completion completion;
        Macro      rows;
    };

    template <class Prepare>
    bool begin(std::uint64_t requested, Prepare&& prepare) {
        if (active()) return false;
        waitForCapture();
        // A failed allocation/prepare preserves the previous recording.
        auto const limit = macroRecordingLimit(requested);
        Macro      fresh(static_cast<std::size_t>(limit));
        prepare();
        mFrames.swap(fresh);
        mRows.store(0, std::memory_order_relaxed);
        mLimit.store(limit, std::memory_order_relaxed);
        ++mRecordId;
        mHaveRotation = false;
        mEnd.store(MacroRecordEnd::None, std::memory_order_relaxed);
        mState.store(Active, std::memory_order_release);
        return true;
    }
    template <class Read>
    void tick(Read&& read) {
        unsigned expected = Active;
        if (!mState.compare_exchange_strong(
                expected,
                Active | Capturing,
                std::memory_order_acquire,
                std::memory_order_relaxed
            ))
            return;
        struct Release {
            std::atomic<unsigned>& state;
            ~Release() { state.fetch_and(~Capturing, std::memory_order_release); }
        } release{mState};
        Sample sample;
        try {
            if (!read(sample)) {
                finish(MacroRecordEnd::CaptureFault);
                return;
            }
        } catch (...) {
            finish(MacroRecordEnd::CaptureFailed);
            return;
        }
        if (!std::isfinite(sample.yaw) || !std::isfinite(sample.pitch)) {
            finish(MacroRecordEnd::CaptureFailed);
            return;
        }
        auto& row = sample.buttons;
        if (mHaveRotation) {
            // Bounded wrap, retaining the old +/-180 convention.
            float yaw = std::fmod(sample.yaw - mPrevYaw, 360.0f);
            if (yaw > 180) yaw -= 360;
            if (yaw < -180) yaw += 360;
            float const pitch = -(sample.pitch - mPrevPitch);
            if (!std::isfinite(yaw) || !std::isfinite(pitch)) {
                finish(MacroRecordEnd::CaptureFailed);
                return;
            }
            row.yaw   = std::fabs(yaw) < 0.001f ? 0 : yaw;
            row.pitch = std::fabs(pitch) < 0.001f ? 0 : pitch;
        } else {
            row.yaw   = 0;
            row.pitch = 0;
        }
        mPrevYaw                                 = sample.yaw;
        mPrevPitch                               = sample.pitch;
        mHaveRotation                            = true;
        auto const index                         = mRows.load(std::memory_order_relaxed);
        mFrames[static_cast<std::size_t>(index)] = row;
        mRows.store(index + 1, std::memory_order_release);
        if (index + 1 == mLimit.load(std::memory_order_relaxed)) finish(MacroRecordEnd::Limit);
    }
    void stop(MacroRecordEnd reason = MacroRecordEnd::Client) {
        if (mState.load(std::memory_order_acquire)) finish(reason);
        waitForCapture();
    }
    Snapshot snapshot() const { // Cold; caller must stop before copying ordinary payloads.
        return {
            {mRecordId, rows(), mEnd.load(std::memory_order_acquire)},
            Macro(mFrames.begin(), mFrames.begin() + static_cast<std::size_t>(rows()))
        };
    }
    bool clear(std::uint64_t recordId) {
        if (recordId != mRecordId || mState.load(std::memory_order_acquire)) return false;
        mFrames.clear();
        mRows.store(0, std::memory_order_release);
        return true;
    }
    std::optional<Completion> takeCompletion() { // Cold; retained frames remain available for saving.
        if (!mRecordId || mReportedId == mRecordId || mState.load(std::memory_order_acquire)) return {};
        mReportedId = mRecordId;
        return Completion{mRecordId, rows(), mEnd.load(std::memory_order_acquire)};
    }
    bool          active() const { return (mState.load(std::memory_order_acquire) & Active) != 0; }
    std::uint64_t rows() const { return mRows.load(std::memory_order_acquire); }
    std::uint64_t limit() const { return mLimit.load(std::memory_order_acquire); }
    std::uint64_t recordId() const { return mRecordId; } // Cold only.
private:
    void waitForCapture() const {
        while (mState.load(std::memory_order_acquire) & Capturing) std::this_thread::yield(); // Cold only.
    }
    void finish(MacroRecordEnd reason) {
        auto expected = MacroRecordEnd::None;
        if (!mEnd.compare_exchange_strong(expected, reason, std::memory_order_release, std::memory_order_relaxed)
            && !macroRecordSucceeded(reason) && macroRecordSucceeded(expected)) {
            mEnd.compare_exchange_strong(expected, reason, std::memory_order_release, std::memory_order_relaxed);
        }
        mState.fetch_and(~Active, std::memory_order_release);
    }
    Macro                       mFrames;
    std::atomic<unsigned>       mState{0};
    std::atomic<std::uint64_t>  mRows{0}, mLimit{MaxMacroRecordedTicks};
    std::atomic<MacroRecordEnd> mEnd{MacroRecordEnd::None};
    std::uint64_t               mRecordId = 0, mReportedId = 0; // Cold ownership.
    bool                        mHaveRotation = false;          // Producer, reset only while quiescent.
    float                       mPrevYaw = 0, mPrevPitch = 0;
};

} // namespace detail
} // namespace probe
