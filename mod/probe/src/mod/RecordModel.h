#pragma once

#include <algorithm>
#include <array>
#include <atomic>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <new>
#include <thread>
#include <type_traits>
#include <utility>

namespace probe::detail {

// One tick producer; lifecycle and dump callers are serialized externally.
// Commands wait for an admitted capture before resetting. The producer never
// waits, allocates or takes a mutex, including during concurrent start/stop.
template <class Entry, std::size_t Capacity> class RecordBuffer {
    static_assert(Capacity > 0 && std::is_trivially_copyable_v<Entry>);
    static_assert(sizeof(Entry) % sizeof(std::uint64_t) == 0);
    static_assert(std::atomic<std::uint64_t>::is_always_lock_free);
    using Words = std::array<std::uint64_t, sizeof(Entry) / sizeof(std::uint64_t)>;
    struct Slot {
        std::atomic<std::uint64_t> sequence{0};
        std::array<std::atomic<std::uint64_t>, std::tuple_size_v<Words>> words{};
    };
    static constexpr unsigned Active = 1, Capturing = 2;

  public:
    class Capture {
      public:
        Capture(Capture const&) = delete;
        Capture& operator=(Capture const&) = delete;
        Capture(Capture&& other) noexcept : mOwner(std::exchange(other.mOwner, nullptr)) {}
        ~Capture() {
            if (mOwner) {
                mOwner->mState.fetch_and(~Capturing, std::memory_order_release);
            }
        }
        explicit operator bool() const { return mOwner != nullptr; }
        bool sampleDue() {
            auto const counter = mOwner->mTickCounter++;
            auto const period = mOwner->period();
            return period == 1 || counter % period == 0;
        }
        void publish(Entry const& entry) {
            auto const index = mOwner->mHead.load(std::memory_order_relaxed);
            auto& slot = mOwner->mSlots[index % Capacity];
            auto const sequence = (index + 1) * 2;
            slot.sequence.store(sequence | 1, std::memory_order_relaxed);
            std::atomic_thread_fence(std::memory_order_release);
            auto const words = std::bit_cast<Words>(entry);
            for (std::size_t i = 0; i < words.size(); ++i) {
                slot.words[i].store(words[i], std::memory_order_relaxed);
            }
            slot.sequence.store(sequence, std::memory_order_release);
            mOwner->mHead.store(index + 1, std::memory_order_release);
        }

      private:
        friend class RecordBuffer;
        explicit Capture(RecordBuffer* owner) : mOwner(owner) {}
        RecordBuffer* mOwner;
    };

    template <class Initialize> bool start(std::uint32_t period, Initialize&& initialize) {
        stop();
        if (!mSlots) {
            mSlots.reset(new (std::nothrow) Slot[Capacity]());
        }
        if (!mSlots) {
            return false;
        }
        mHead.store(0, std::memory_order_relaxed);
        mTickCounter = 0;
        mPeriod.store(std::clamp<std::uint32_t>(period, 1, 200), std::memory_order_relaxed);
        initialize();
        mState.store(Active, std::memory_order_release);
        return true;
    }

    void stop() {
        mState.fetch_and(~Active, std::memory_order_acq_rel);
        while ((mState.load(std::memory_order_acquire) & Capturing) != 0) {
            std::this_thread::yield(); // Command thread only, never the tick.
        }
    }

    Capture capture() {
        // Dormant recording retains the original one-load fast path. Only an
        // active capture needs the read/modify/write admission operation.
        if (mState.load(std::memory_order_relaxed) != Active) {
            return Capture(nullptr);
        }
        unsigned expected = Active;
        if (!mState.compare_exchange_strong(expected, Active | Capturing, std::memory_order_acquire,
                                            std::memory_order_relaxed)) {
            return Capture(nullptr);
        }
        return Capture(this);
    }

    bool active() const { return (mState.load(std::memory_order_acquire) & Active) != 0; }
    std::uint64_t head() const { return mHead.load(std::memory_order_acquire); }
    std::uint32_t period() const { return mPeriod.load(std::memory_order_relaxed); }

    bool read(std::uint64_t index, Entry& entry) const {
        if (!mSlots || index >= head()) {
            return false;
        }
        auto const& slot = mSlots[index % Capacity];
        auto const expected = (index + 1) * 2;
        if (slot.sequence.load(std::memory_order_acquire) != expected) {
            return false;
        }
        Words words;
        for (std::size_t i = 0; i < words.size(); ++i) {
            words[i] = slot.words[i].load(std::memory_order_relaxed);
        }
        // Atomic payload words make wraparound reads data-race-free. The
        // second sequence check rejects a slot overwritten during this copy.
        std::atomic_thread_fence(std::memory_order_acquire);
        if (slot.sequence.load(std::memory_order_relaxed) != expected) {
            return false;
        }
        entry = std::bit_cast<Entry>(words);
        return true;
    }

  private:
    std::unique_ptr<Slot[]> mSlots;
    std::atomic<unsigned> mState{0};
    std::atomic<std::uint64_t> mHead{0};
    std::atomic<std::uint32_t> mPeriod{1};
    std::uint64_t mTickCounter = 0;
};

} // namespace probe::detail
