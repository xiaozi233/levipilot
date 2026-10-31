#pragma once

#include "mod/MacroRecordModel.h"

#include <atomic>
#include <cstdint>
#include <optional>
#include <string>
#include <thread>
#include <utility>

namespace probe::detail {

enum class MacroEnd {
    None,
    End,
    Client,
    Loaded,
    Task,
    WorldLeft,
    Disabled,
    ApplyFault,
    ApplyFailed,
    ReleaseFault,
    ReleaseFailed,
    InternalError
};
inline bool macroSucceeded(MacroEnd reason) {
    return reason == MacroEnd::None || reason == MacroEnd::End || reason == MacroEnd::Client
        || reason == MacroEnd::Loaded || reason == MacroEnd::Task;
}
inline char const* macroEndName(MacroEnd reason) {
    switch (reason) {
    case MacroEnd::None:
        return "none";
    case MacroEnd::End:
        return "end";
    case MacroEnd::Client:
        return "client";
    case MacroEnd::Loaded:
        return "loaded";
    case MacroEnd::Task:
        return "task";
    case MacroEnd::WorldLeft:
        return "world_left";
    case MacroEnd::Disabled:
        return "disabled";
    case MacroEnd::ApplyFault:
        return "playback_fault";
    case MacroEnd::ApplyFailed:
        return "playback_failed";
    case MacroEnd::ReleaseFault:
        return "release_fault";
    case MacroEnd::ReleaseFailed:
        return "release_failed";
    case MacroEnd::InternalError:
        return "internal_error";
    }
    return "unknown";
}

// Cold load/begin/stop/completion callers serialize externally. Tick holds a
// single bounded CAS lease; frames/configuration cannot change until it exits.
// Only cold callers allocate, destroy frame storage, copy names or wait.
class MacroPlayback {
    static constexpr unsigned Active = 1, Busy = 2, Writing = 4;

public:
    struct Completion {
        std::uint64_t runId  = 0;
        int           index  = 0;
        MacroEnd      reason = MacroEnd::None;
        bool          ok() const { return macroSucceeded(reason); }
    };
    void load(Macro frames, std::string name) { // Cold; stop and drain first.
        mState.fetch_or(Writing, std::memory_order_acq_rel);
        join();
        mFrames = std::move(frames);
        mName   = std::move(name);
        mState.fetch_and(~Writing, std::memory_order_release);
    }
    bool begin(int start, int end, bool loop, bool eventLevel) {
        if (active() || mFrames.empty() || start < 0 || end <= start || static_cast<std::size_t>(end) > mFrames.size())
            return false;
        mState.fetch_or(Writing, std::memory_order_acq_rel);
        join();
        mStart      = start;
        mEnd        = end;
        mLoop       = loop;
        mEventLevel = eventLevel;
        mFirst      = true;
        mIndex.store(start, std::memory_order_relaxed);
        ++mRunId;
        mReason.store(MacroEnd::None, std::memory_order_relaxed);
        mState.store(Active, std::memory_order_release);
        return true;
    }
    void cancel(MacroEnd reason) { // Lifecycle/task thread; no wait or game access.
        if (mState.load(std::memory_order_acquire)) {
            mReleaseRequested.store(true, std::memory_order_release);
            finish(reason);
        }
    }
    void stop(MacroEnd reason) { // Cold only.
        cancel(reason);
        join();
    }
    template <class Release>
    bool releaseRequested(Release&& release) {
        if (!mReleaseRequested.load(std::memory_order_acquire)) return false;
        auto lease = acquire();
        if (!lease) return false;
        releaseNow(release);
        return true;
    }
    template <class Apply, class Release>
    bool tick(Apply&& apply, Release&& release) {
        if (!active()) return false;
        auto lease = acquire();
        if (!lease) return false;
        if (mReleaseRequested.load(std::memory_order_acquire) && !releaseNow(release)) return true;
        if (!active()) return true;
        int index = mIndex.load(std::memory_order_relaxed);
        if (index >= mEnd) {
            if (mLoop) index = mStart;
            else {
                finish(MacroEnd::End);
                releaseNow(release);
                return true;
            }
        }
        bool const first = std::exchange(mFirst, false);
        mDirty           = true; // Partial injection must still be released on a fault.
        try {
            if (!apply(mFrames[static_cast<std::size_t>(index)], mEventLevel, first)) finish(MacroEnd::ApplyFault);
            else mIndex.store(index + 1, std::memory_order_release);
        } catch (...) {
            finish(MacroEnd::ApplyFailed);
        }
        if (!active()) releaseNow(release);
        return true;
    }
    std::optional<Completion> takeCompletion(bool wait = false) { // Cold; no frame/name access in tick.
        if (!mRunId || mReportedId == mRunId) return {};
        if (wait) {
            mState.fetch_or(Writing, std::memory_order_acq_rel);
            join();
        } else {
            unsigned expected = 0;
            if (!mState
                     .compare_exchange_strong(expected, Writing, std::memory_order_acquire, std::memory_order_relaxed))
                return {};
        }
        if (active()) {
            mState.fetch_and(~Writing, std::memory_order_release);
            return {};
        }
        mReportedId = mRunId;
        Completion result{mRunId, index(), mReason.load(std::memory_order_acquire)};
        mState.fetch_and(~Writing, std::memory_order_release);
        return result;
    }
    bool active() const { return (mState.load(std::memory_order_acquire) & Active) != 0; }
    int  index() const { return mIndex.load(std::memory_order_acquire); }
    bool releasePending() const { return mReleaseRequested.load(std::memory_order_acquire); }
    struct ReleaseErrors {
        std::uint64_t fault = 0, cpp = 0;
    };
    ReleaseErrors      takeReleaseErrors() { return {mReleaseFaults.exchange(0), mReleaseExceptions.exchange(0)}; }
    std::size_t        size() const { return mFrames.size(); } // Cold, externally serialized.
    std::string const& name() const { return mName; }          // Cold, externally serialized.
    std::uint64_t      runId() const { return mRunId; }        // Cold, externally serialized.
private:
    struct Lease {
        MacroPlayback* owner = nullptr;
        Lease(Lease const&)  = delete;
        explicit Lease(MacroPlayback* value) : owner(value) {}
        ~Lease() {
            if (owner) owner->mState.fetch_and(~Busy, std::memory_order_release);
        }
        explicit operator bool() const { return owner; }
    };
    Lease acquire() {
        auto state = mState.load(std::memory_order_relaxed);
        if (state & (Busy | Writing)
            || !mState
                    .compare_exchange_strong(state, state | Busy, std::memory_order_acquire, std::memory_order_relaxed))
            return Lease(nullptr);
        return Lease(this);
    }
    template <class Release>
    bool releaseNow(Release& release) {
        mReleaseRequested.exchange(false, std::memory_order_acq_rel);
        if (!mDirty) return true;
        try {
            if (!release()) {
                mReleaseFaults.fetch_add(1, std::memory_order_relaxed);
                finish(MacroEnd::ReleaseFault);
                mReleaseRequested.store(true, std::memory_order_release);
                return false;
            }
        } catch (...) {
            mReleaseExceptions.fetch_add(1, std::memory_order_relaxed);
            finish(MacroEnd::ReleaseFailed);
            mReleaseRequested.store(true, std::memory_order_release);
            return false;
        }
        mDirty = false;
        return true;
    }
    void finish(MacroEnd reason) {
        auto expected = MacroEnd::None;
        if (!mReason.compare_exchange_strong(expected, reason, std::memory_order_release, std::memory_order_relaxed)
            && !macroSucceeded(reason) && macroSucceeded(expected))
            mReason.compare_exchange_strong(expected, reason, std::memory_order_release, std::memory_order_relaxed);
        mState.fetch_and(~Active, std::memory_order_release);
    }
    void join() const {
        while (mState.load(std::memory_order_acquire) & Busy) std::this_thread::yield();
    }
    Macro                      mFrames;
    std::string                mName;
    std::atomic<unsigned>      mState{0};
    std::atomic<int>           mIndex{0};
    std::atomic<MacroEnd>      mReason{MacroEnd::None};
    std::atomic_bool           mReleaseRequested{false};
    std::atomic<std::uint64_t> mReleaseFaults{0}, mReleaseExceptions{0};
    std::uint64_t              mRunId = 0, mReportedId = 0;
    int                        mStart = 0, mEnd = 0;
    bool                       mLoop = false, mEventLevel = false, mFirst = true;
    bool                       mDirty = false; // Tick-owned; preserved across cold restarts until released.
};

} // namespace probe::detail
