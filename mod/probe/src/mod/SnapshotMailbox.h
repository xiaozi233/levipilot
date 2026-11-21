#pragma once

#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <type_traits>
#include <utility>

namespace probe::detail {

// One preallocated request/result slot. The cold producer/consumer serialize
// lifecycle operations; the game thread claims exclusive ownership with CAS,
// never takes the control mutex and never allocates. A running bounded batch
// cannot be recycled by cancellation, timeout or disconnect.
template <class Request, class Snapshot>
class SnapshotMailbox {
    static_assert(std::is_trivially_copyable_v<Request>);
    static_assert(std::is_trivially_copyable_v<Snapshot>);
    enum class Phase { Idle, Pending, Capturing, Ready, Consuming };

public:
    class Completion {
    public:
        Completion()                             = default;
        Completion(Completion const&)            = delete;
        Completion& operator=(Completion const&) = delete;
        Completion(Completion&& other) noexcept
        : error(other.error),
          reqId(other.reqId),
          mOwner(std::exchange(other.mOwner, nullptr)) {}
        ~Completion() { release(); }
        bool release() {
            if (auto owner = std::exchange(mOwner, nullptr)) {
                std::lock_guard lock(owner->mControl);
                bool const      deliver = !owner->mDiscard;
                owner->mPhase.store(Phase::Idle, std::memory_order_release);
                return deliver;
            }
            return false;
        }
        explicit        operator bool() const { return mOwner != nullptr; }
        Snapshot const& snapshot() const { return mOwner->mSnapshot; }
        char const*     error = nullptr;
        std::uint64_t   reqId = 0;

    private:
        friend class SnapshotMailbox;
        Completion(SnapshotMailbox* owner, char const* failure, std::uint64_t id)
        : error(failure),
          reqId(id),
          mOwner(owner) {}
        SnapshotMailbox* mOwner = nullptr;
    };

    bool begin(Request const& request, std::uint64_t now, std::uint64_t timeoutMs, std::uint64_t reqId = 0) {
        std::lock_guard lock(mControl);
        reapDiscarded();
        if (mPhase.load(std::memory_order_acquire) != Phase::Idle) return false;
        mRequest = request;
        mReqId   = reqId;
        // In-place value initialization avoids a multi-MiB temporary for scan.
        std::destroy_at(&mSnapshot);
        std::construct_at(&mSnapshot);
        mDeadline = now + timeoutMs;
        mFailure.store(nullptr, std::memory_order_relaxed);
        mDiscard = false;
        mPhase.store(Phase::Pending, std::memory_order_release);
        return true;
    }

    template <class Advance>
    bool advance(Advance&& advance) {
        auto expected = Phase::Pending;
        if (!mPhase.compare_exchange_strong(expected, Phase::Capturing, std::memory_order_acquire)) return false;
        bool done = true;
        if (!mFailure.load(std::memory_order_acquire)) {
            try {
                done = advance(mRequest, mSnapshot);
            } catch (...) {
                fail("capture_failed");
            }
        }
        bool const failed = mFailure.load(std::memory_order_acquire) != nullptr;
        mPhase.store(done ? Phase::Ready : Phase::Pending, std::memory_order_release);
        return done || failed;
    }

    Completion take(std::uint64_t now) {
        std::lock_guard lock(mControl);
        reapDiscarded();
        auto phase = mPhase.load(std::memory_order_acquire);
        if (phase == Phase::Idle || phase == Phase::Consuming) return {};
        if (now >= mDeadline && phase != Phase::Ready) fail("capture_timeout");
        auto error = mFailure.load(std::memory_order_acquire);
        if (phase == Phase::Capturing || (phase == Phase::Pending && !error)) return {};
        if (!mPhase.compare_exchange_strong(phase, Phase::Consuming, std::memory_order_acquire)) return {};
        // Cancellation can win during a batch; never expose its partial data.
        if (mDiscard) {
            mPhase.store(Phase::Idle, std::memory_order_release);
            return {};
        }
        return {this, error, mReqId};
    }

    void cancel(char const* reason) {
        std::lock_guard lock(mControl);
        auto const      phase = mPhase.load(std::memory_order_acquire);
        if (phase != Phase::Idle && phase != Phase::Consuming) fail(reason);
    }

    void reset() {
        std::lock_guard lock(mControl);
        auto const      phase = mPhase.load(std::memory_order_acquire);
        if (phase == Phase::Idle) return;
        mDiscard = true;
        if (phase == Phase::Consuming) return;
        fail("capture_cancelled");
        reapDiscarded();
    }

private:
    void fail(char const* reason) {
        char const* expected = nullptr;
        mFailure.compare_exchange_strong(expected, reason, std::memory_order_release);
    }
    void reapDiscarded() {
        if (!mDiscard) return;
        auto phase = mPhase.load(std::memory_order_acquire);
        if (phase == Phase::Pending || phase == Phase::Ready) {
            if (mPhase.compare_exchange_strong(phase, Phase::Consuming, std::memory_order_acquire))
                mPhase.store(Phase::Idle, std::memory_order_release);
        }
    }

    std::mutex               mControl; // Never acquired by advance or its callback.
    std::atomic<Phase>       mPhase{Phase::Idle};
    std::atomic<char const*> mFailure{nullptr};
    Request                  mRequest{};
    Snapshot                 mSnapshot{};
    std::uint64_t            mReqId    = 0;
    std::uint64_t            mDeadline = 0;
    bool                     mDiscard  = false; // Cold control side only.
};

} // namespace probe::detail
