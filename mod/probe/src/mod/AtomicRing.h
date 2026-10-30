#pragma once

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

// A bounded, multi-producer ring for fixed-size records. Reservation order is
// the public sequence order; publication can complete out of order. Payload
// words are atomic so wrapping writers and a concurrent pipe-thread reader do
// not race on ordinary C++ objects.
template <class Entry, std::size_t Capacity> class AtomicRing {
    static_assert(Capacity > 0);
    static_assert(std::is_trivially_copyable_v<Entry>);
    static_assert(sizeof(Entry) % sizeof(std::uint64_t) == 0);
    static_assert(std::atomic<std::uint64_t>::is_always_lock_free);

    static constexpr std::size_t kWordCount = sizeof(Entry) / sizeof(std::uint64_t);
    using Words = std::array<std::uint64_t, kWordCount>;

    struct Slot {
        // 4 * sequence is committed, +1 is an active writer, +2 is a tombstone.
        std::atomic<std::uint64_t> state{0};
        std::array<std::atomic<std::uint64_t>, kWordCount> words{};
    };

public:
    enum class ReadResult { ready, pending, dropped, overwritten };

    class Reservation {
    public:
        Reservation(Reservation const&) = delete;
        Reservation& operator=(Reservation const&) = delete;
        Reservation(Reservation&& other) noexcept
            : mOwner(std::exchange(other.mOwner, nullptr)), mIndex(other.mIndex) {}

        ~Reservation() {
            if (mOwner != nullptr) {
                // A producer that exits early must not leave a permanent hole
                // in reservation order. A tombstone lets consumers advance
                // and count the missing row.
                AtomicRing* owner = std::exchange(mOwner, nullptr);
                owner->abandon(mIndex);
            }
        }

        [[nodiscard]] std::uint64_t index() const { return mIndex; }

        // Returns false if this reservation was already overtaken by a later
        // generation of the same slot. It never publishes stale bytes over a
        // newer record.
        bool publish(Entry const& entry) {
            if (mOwner == nullptr) {
                return false;
            }
            AtomicRing* owner = std::exchange(mOwner, nullptr);
            return owner->publish(mIndex, entry);
        }

    private:
        friend class AtomicRing;
        Reservation(AtomicRing* owner, std::uint64_t index) : mOwner(owner), mIndex(index) {}
        AtomicRing* mOwner;
        std::uint64_t mIndex;
    };

    bool initialize() {
        if (mSlots) {
            return true;
        }
        mSlots.reset(new (std::nothrow) Slot[Capacity]());
        return mSlots != nullptr;
    }

    // Reset is a lifecycle operation: callers must first disable producers
    // and ensure no prior callback can still hold a reservation.
    void reset() {
        if (mSlots) {
            for (std::size_t i = 0; i < Capacity; ++i) {
                mSlots[i].state.store(0, std::memory_order_relaxed);
            }
        }
        mHead.store(0, std::memory_order_relaxed);
    }

    [[nodiscard]] explicit operator bool() const { return mSlots != nullptr; }
    [[nodiscard]] std::uint64_t head() const { return mHead.load(std::memory_order_acquire); }

    Reservation reserve() {
        if (!mSlots) {
            return Reservation(nullptr, 0);
        }
        return Reservation(this, mHead.fetch_add(1, std::memory_order_relaxed));
    }

    ReadResult read(std::uint64_t index, Entry& entry) const {
        if (!mSlots) {
            return ReadResult::pending;
        }
        Slot const& slot = mSlots[index % Capacity];
        std::uint64_t const wanted = index + 1;
        std::uint64_t before = slot.state.load(std::memory_order_acquire);
        ReadResult result = classify(before, wanted);
        if (result != ReadResult::ready) {
            return result;
        }

        Words words{};
        for (std::size_t i = 0; i < kWordCount; ++i) {
            words[i] = slot.words[i].load(std::memory_order_relaxed);
        }
        std::atomic_thread_fence(std::memory_order_acquire);
        std::uint64_t const after = slot.state.load(std::memory_order_acquire);
        if (after != before) {
            return classify(after, wanted);
        }
        entry = std::bit_cast<Entry>(words);
        return ReadResult::ready;
    }

private:
    static ReadResult classify(std::uint64_t state, std::uint64_t wanted) {
        std::uint64_t const sequence = state >> 2;
        std::uint64_t const status   = state & 3u;
        if (status == 1 || sequence < wanted) {
            return ReadResult::pending;
        }
        if (sequence > wanted) {
            return ReadResult::overwritten;
        }
        return status == 2 ? ReadResult::dropped : ReadResult::ready;
    }

    bool publish(std::uint64_t index, Entry const& entry) {
        Slot& slot = mSlots[index % Capacity];
        std::uint64_t const sequence = index + 1;
        std::uint64_t const writing = sequence * 4 + 1;
        std::uint64_t state = slot.state.load(std::memory_order_acquire);
        for (;;) {
            if ((state & 3u) == 1) {
                // Only same-slot writers contend here. Let the current short
                // fixed-record copy finish, then claim the next generation.
                std::this_thread::yield();
                state = slot.state.load(std::memory_order_acquire);
                continue;
            }
            if ((state >> 2) > sequence) {
                return false;
            }
            if (slot.state.compare_exchange_weak(
                    state,
                    writing,
                    std::memory_order_acq_rel,
                    std::memory_order_acquire
                )) {
                break;
            }
        }

        Words const words = std::bit_cast<Words>(entry);
        for (std::size_t i = 0; i < kWordCount; ++i) {
            slot.words[i].store(words[i], std::memory_order_relaxed);
        }
        slot.state.store(sequence * 4, std::memory_order_release);
        return true;
    }

    void abandon(std::uint64_t index) {
        Slot& slot = mSlots[index % Capacity];
        std::uint64_t const sequence  = index + 1;
        std::uint64_t const tombstone = sequence * 4 + 2;
        std::uint64_t state = slot.state.load(std::memory_order_acquire);
        for (;;) {
            if ((state >> 2) > sequence) {
                return;
            }
            if ((state & 3u) == 1) {
                std::this_thread::yield();
                state = slot.state.load(std::memory_order_acquire);
                continue;
            }
            if (slot.state.compare_exchange_weak(
                    state,
                    tombstone,
                    std::memory_order_acq_rel,
                    std::memory_order_acquire
                )) {
                return;
            }
        }
    }

    std::unique_ptr<Slot[]> mSlots;
    std::atomic<std::uint64_t> mHead{0};
};

} // namespace probe::detail
