#pragma once

#include <array>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <type_traits>

namespace probe::detail {

struct PoseSnapshot {
    float         x = 0, y = 0, z = 0, feetX = 0, feetY = 0, feetZ = 0, yaw = 0, pitch = 0;
    std::uint64_t epoch = 0, tick = 0;
    std::uint64_t valid = 0; // Full word: no indeterminate padding in atomic payloads.
};

// One game-thread writer; checkpoint and crash readers make bounded attempts.
// Atomic payloads make a concurrent overwrite legal, and the sequence checks
// prevent coordinates and angles from different ticks forming a checkpoint.
class PoseStore {
    static constexpr std::size_t WordCount = (sizeof(PoseSnapshot) + 7) / 8;
    using Words                            = std::array<std::uint64_t, WordCount>;
    static_assert(std::is_trivially_copyable_v<PoseSnapshot>);
    static_assert(std::atomic<std::uint64_t>::is_always_lock_free);

public:
    struct Failures {
        std::uint64_t fault = 0, cpp = 0, nonFinite = 0;
    };

    template <class Read>
    void capture(std::uint64_t epoch, std::uint64_t tick, Read&& read) {
        PoseSnapshot next;
        try {
            next.valid = read(next);
            if (!next.valid) ++mFaults;
            else if (!finite(next)) {
                next.valid = false;
                ++mNonFinite;
            }
        } catch (...) {
            next.valid = false;
            ++mCpp;
        }
        next.epoch = epoch;
        next.tick  = tick;
        publish(next); // A failed sample invalidates the preceding good pose.
    }

    bool read(PoseSnapshot& out, std::uint64_t epoch) const {
        PoseSnapshot next;
        if (!latest(next) || next.epoch != epoch) return false;
        out = next;
        return true;
    }

    bool latest(PoseSnapshot& out) const {
        for (int attempt = 0; attempt < 8; ++attempt) {
            auto const before = mSequence.load();
            if (before & 1u) continue;
            Words words{};
            for (std::size_t i = 0; i < WordCount; ++i) words[i] = mWords[i].load();
            if (mSequence.load() != before) continue;
            PoseSnapshot next;
            std::memcpy(&next, words.data(), sizeof(next));
            if (!next.valid) return false;
            out = next;
            return true;
        }
        return false; // Never wait for a writer, including in a crash handler.
    }

    Failures takeFailures() { return {mFaults.exchange(0), mCpp.exchange(0), mNonFinite.exchange(0)}; }

private:
    static bool finite(PoseSnapshot const& p) {
        return std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z) && std::isfinite(p.feetX)
            && std::isfinite(p.feetY) && std::isfinite(p.feetZ) && std::isfinite(p.yaw) && std::isfinite(p.pitch);
    }
    void publish(PoseSnapshot const& pose) {
        Words words{};
        std::memcpy(words.data(), &pose, sizeof(pose));
        auto const sequence = mSequence.load();
        mSequence.store(sequence + 1);
        for (std::size_t i = 0; i < WordCount; ++i) mWords[i].store(words[i]);
        mSequence.store(sequence + 2);
    }

    std::atomic<std::uint64_t>                        mSequence{0};
    std::array<std::atomic<std::uint64_t>, WordCount> mWords{};
    std::atomic<std::uint64_t>                        mFaults{0}, mCpp{0}, mNonFinite{0};
};

} // namespace probe::detail
