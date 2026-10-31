#pragma once

#include <array>
#include <atomic>
#include <bit>
#include <cstdint>

namespace probe::detail {

// Values only: never retain a client or server Player across threads/worlds.
struct PlayerIdentity {
    std::uint64_t uuidA = 0, uuidB = 0, epoch = 0, tick = 0, valid = 0;
    bool matches(std::uint64_t currentEpoch, bool inWorld) const {
        return valid && inWorld && epoch == currentEpoch && (uuidA || uuidB);
    }
};

// One tick writer and any number of cold readers. Atomic payloads legalize
// concurrent overwrite; bounded sequence checks reject torn/obsolete values.
class PlayerIdentityStore {
    using Words = std::array<std::uint64_t, 5>;
    static_assert(sizeof(PlayerIdentity) == sizeof(Words));
    static_assert(std::atomic<std::uint64_t>::is_always_lock_free);
public:
    template <class Read>
    void capture(std::uint64_t epoch, std::uint64_t tick, Read&& read) {
        PlayerIdentity next;
        try {
            next.valid = read(next);
            if (!next.valid) ++mFaults;
            else if (!(next.uuidA || next.uuidB)) { next.valid = 0; ++mEmpty; }
        } catch (...) { next.valid = 0; ++mCpp; }
        next.epoch = epoch;
        next.tick = tick;
        auto const words = std::bit_cast<Words>(next);
        auto const sequence = mSequence.load();
        mSequence.store(sequence + 1);
        for (std::size_t i = 0; i < words.size(); ++i) mWords[i].store(words[i]);
        mSequence.store(sequence + 2);
    }
    bool read(PlayerIdentity& out, std::uint64_t epoch) const {
        for (int attempt = 0; attempt < 8; ++attempt) {
            auto const before = mSequence.load();
            if (before & 1u) continue;
            Words words{};
            for (std::size_t i = 0; i < words.size(); ++i) words[i] = mWords[i].load();
            if (mSequence.load() != before) continue;
            auto const next = std::bit_cast<PlayerIdentity>(words);
            if (!next.matches(epoch, true)) return false;
            out = next;
            return true;
        }
        return false;
    }
    struct Failures { std::uint64_t fault = 0, cpp = 0, empty = 0; };
    Failures failures() const { return {mFaults.load(), mCpp.load(), mEmpty.load()}; }
private:
    std::atomic<std::uint64_t> mSequence{0}, mFaults{0}, mCpp{0}, mEmpty{0};
    std::array<std::atomic<std::uint64_t>, 5> mWords{};
};

} // namespace probe::detail
