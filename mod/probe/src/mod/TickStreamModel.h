#pragma once

#include "mod/TaskModel.h"

#include <atomic>
#include <optional>
#include <thread>

namespace probe::detail {

// One producer and one consumer. Cold lifecycle reset requires both to be
// quiescent. Release of the read cursor prevents reuse while a copy is live.
template <class Entry, std::size_t Capacity>
class TickQueue {
    static_assert(Capacity > 0 && std::is_trivially_copyable_v<Entry>);

public:
    bool push(Entry const& entry, std::size_t limit = Capacity) {
        auto const head = mHead.load(std::memory_order_relaxed);
        if (head - mTail.load(std::memory_order_acquire) >= std::clamp<std::size_t>(limit, 1, Capacity)) return false;
        mRows[head % Capacity] = entry;
        mHead.store(head + 1, std::memory_order_release);
        return true;
    }
    bool pop(Entry& entry) {
        auto const tail = mTail.load(std::memory_order_relaxed);
        if (tail == mHead.load(std::memory_order_acquire)) return false;
        entry = mRows[tail % Capacity];
        mTail.store(tail + 1, std::memory_order_release);
        return true;
    }
    bool empty() const { return mTail.load(std::memory_order_acquire) == mHead.load(std::memory_order_acquire); }
    void reset() {
        mHead.store(0);
        mTail.store(0);
    }

private:
    std::array<Entry, Capacity> mRows{};
    std::atomic<std::uint64_t>  mHead{0}, mTail{0};
};

enum class StreamTest { None, Fault, Cpp };
enum class StreamEnd { None, Client, MaxTicks, CaptureFault, CaptureFailed, BufferFull, WorldLeft, Disabled };
inline char const* streamEndName(StreamEnd reason) {
    switch (reason) {
    case StreamEnd::None:
        return "none";
    case StreamEnd::Client:
        return "client";
    case StreamEnd::MaxTicks:
        return "max_ticks";
    case StreamEnd::CaptureFault:
        return "capture_fault";
    case StreamEnd::CaptureFailed:
        return "capture_failed";
    case StreamEnd::BufferFull:
        return "buffer_full";
    case StreamEnd::WorldLeft:
        return "world_left";
    case StreamEnd::Disabled:
        return "disabled";
    }
    return "unknown";
}

struct LegacyTick {
    std::uint64_t tick = 0, recordId = 0;
    float         x = 0, y = 0, z = 0, vx = 0, vy = 0, vz = 0, yaw = 0, pitch = 0, health = 0;
    bool          onGround = false, jumpEdge = false, sprint = false, sneak = false;
    bool          jump = false, w = false, a = false, s = false, d = false, actionSprint = false, inputSprint = false;
    bool          truncated = false;
    std::array<FixedText<256>, 3> blocks{};
};

// Commands and the spool consumer share one cold lock externally. The tick
// uses bounded CAS admission only. Completion is out-of-band and is readable
// only after the admitted capture and every queued sample have finished.
template <std::size_t Capacity = 256>
class LegacyStream {
    static constexpr unsigned Active = 1, Capturing = 2;

public:
    struct Completion {
        std::uint64_t recordId = 0, rows = 0;
        StreamEnd     reason = StreamEnd::None;
        bool          ok() const { return reason == StreamEnd::Client || reason == StreamEnd::MaxTicks; }
    };
    bool begin(std::uint64_t maxTicks, std::size_t capacity = Capacity) {
        if (mOpen) return false;
        mQueue.reset();
        mRows     = 0;
        mMaxTicks = maxTicks;
        mCapacity = std::clamp<std::size_t>(capacity, 1, Capacity);
        ++mRecordId;
        mEnd.store(StreamEnd::None, std::memory_order_relaxed);
        mOpen = true;
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
        LegacyTick row;
        row.tick     = mRows + 1;
        row.recordId = mRecordId;
        try {
            if (!read(row)) {
                finish(StreamEnd::CaptureFault);
                return;
            }
        } catch (...) {
            finish(StreamEnd::CaptureFailed);
            return;
        }
        if (!mQueue.push(row, mCapacity)) {
            finish(StreamEnd::BufferFull);
            return;
        }
        ++mRows;
        if (mMaxTicks && mRows >= mMaxTicks) finish(StreamEnd::MaxTicks);
    }
    void stop(StreamEnd reason) {
        if (!mOpen) return;
        if (mState.load(std::memory_order_acquire)) finish(reason);
        while (mState.load(std::memory_order_acquire) & Capturing) std::this_thread::yield(); // Cold side only.
    }
    bool                      pop(LegacyTick& row) { return mQueue.pop(row); }
    std::optional<Completion> takeCompletion() {
        if (!mOpen || mState.load(std::memory_order_acquire) != 0 || !mQueue.empty()) return {};
        mOpen = false;
        return Completion{mRecordId, mRows, mEnd.load(std::memory_order_acquire)};
    }
    bool          active() const { return (mState.load(std::memory_order_acquire) & Active) != 0; }
    bool          ready() const { return !mOpen; }       // Cold caller only.
    std::uint64_t recordId() const { return mRecordId; } // Cold caller only.
private:
    void finish(StreamEnd reason) {
        auto       expected = StreamEnd::None;
        bool const failure  = reason != StreamEnd::Client && reason != StreamEnd::MaxTicks;
        // A concurrent client stop cannot disguise failure of an admitted read.
        if (!mEnd.compare_exchange_strong(expected, reason, std::memory_order_release, std::memory_order_relaxed)
            && failure && (expected == StreamEnd::Client || expected == StreamEnd::MaxTicks)) {
            mEnd.compare_exchange_strong(expected, reason, std::memory_order_release, std::memory_order_relaxed);
        }
        mState.fetch_and(~Active, std::memory_order_release);
    }
    TickQueue<LegacyTick, Capacity> mQueue;
    std::atomic<unsigned>           mState{0};
    std::atomic<StreamEnd>          mEnd{StreamEnd::None};
    std::uint64_t                   mRecordId = 0, mMaxTicks = 0, mRows = 0;
    std::size_t                     mCapacity = Capacity;
    bool                            mOpen     = false;
};

struct ParkourPose {
    float x = 0, y = 0, z = 0, health = 0;
    bool  onGround = false;
};
struct ParkourEvent {
    enum Flag : unsigned { Landed = 1, Hurt = 2, Died = 4 };
    ParkourPose   pose;
    float         fall = 0, damage = 0;
    unsigned      flags      = 0;
    std::uint64_t tick       = 0;
    std::uint64_t generation = 0;
};

class ParkourTracker {
public:
    void reset() {
        mInitialized = false;
        mAirborne    = false;
    }
    ParkourEvent observe(ParkourPose pose, std::uint64_t tick) {
        ParkourEvent event{pose};
        event.tick = tick;
        if (!mInitialized) {
            mHealth      = pose.health;
            mInitialized = true;
        }
        if (!pose.onGround) {
            if (!mAirborne) {
                mAirborne = true;
                mAirY     = pose.y;
            }
        } else if (mAirborne) {
            mAirborne  = false;
            event.fall = mAirY - pose.y;
            if (event.fall >= 0.75f) event.flags |= ParkourEvent::Landed;
        }
        event.damage = mHealth - pose.health;
        if (event.damage > 0.01f) event.flags |= ParkourEvent::Hurt;
        if (pose.health <= 0 && mHealth > 0) event.flags |= ParkourEvent::Died;
        mHealth = pose.health;
        return event;
    }

private:
    bool  mInitialized = false, mAirborne = false;
    float mHealth = 0, mAirY = 0;
};

} // namespace probe::detail
