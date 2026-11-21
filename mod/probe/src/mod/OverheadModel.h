#pragma once

#include "mod/AtomicRing.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>

namespace probe::detail {

struct OverheadSample {
    std::uint32_t tickUs = 0, hookUs = 0;
};

// Initialization is cold and completes before sampling is admitted. Producers
// only publish a fixed atomic record; readers retry a bounded number of times
// and never calculate percentiles from an unverified window.
template <std::size_t Capacity>
class OverheadWindow {
public:
    struct Snapshot {
        std::array<OverheadSample, Capacity> rows{};
        std::uint64_t                        head       = 0;
        std::size_t                          count      = 0;
        bool                                 consistent = false;
    };
    bool initialize() {
        if (!mRing.initialize()) return false;
        mAvailable.store(true, std::memory_order_release);
        return true;
    }
    bool available() const { return mAvailable.load(std::memory_order_acquire); }
    void record(OverheadSample const& sample) {
        if (!available()) return;
        auto slot = mRing.reserve();
        slot.publish(sample);
    }
    Snapshot snapshot(unsigned attempts = 64) const {
        Snapshot result;
        if (!available()) return result;
        for (unsigned attempt = 0; attempt < attempts; ++attempt) {
            result.head         = mRing.head();
            auto const count    = static_cast<std::size_t>(std::min<std::uint64_t>(result.head, Capacity));
            bool       complete = true;
            for (std::size_t i = 0; i < count; ++i) {
                if (mRing.read(result.head - count + i, result.rows[i]) != Ring::ReadResult::ready) {
                    complete = false;
                    break;
                }
            }
            if (complete && mRing.head() == result.head) {
                result.count      = count;
                result.consistent = true;
                return result;
            }
        }
        return result; // count=0: failed candidates are never exported.
    }

private:
    using Ring = AtomicRing<OverheadSample, Capacity>;
    Ring             mRing;
    std::atomic_bool mAvailable{false};
};

class PathRateCounter {
public:
    struct Snapshot {
        std::uint64_t total, startedMs;
    };
    void record(std::uint64_t now) {
        std::uint64_t empty = 0;
        mStartedMs.compare_exchange_strong(empty, now, std::memory_order_relaxed);
        mTotal.fetch_add(1, std::memory_order_relaxed);
    }
    Snapshot snapshot() const {
        return {mTotal.load(std::memory_order_relaxed), mStartedMs.load(std::memory_order_relaxed)};
    }

private:
    std::atomic<std::uint64_t> mTotal{0}, mStartedMs{0};
};

} // namespace probe::detail
