#pragma once

#include <array>
#include <atomic>
#include <bit>
#include <cstdint>

namespace probe::detail {

struct InputLifecycleSnapshot {
    std::uint64_t epoch = 0, inputSeq = 0, jumpSeq = 0, lookSeq = 0;
    std::int64_t inputTicks = 0, lookTicks = 0;
    std::uint64_t jumpPending = 0, heldEvents = 0, valid = 0;

    bool pending(std::uint32_t input, std::uint32_t jump, std::uint32_t look) const {
        return inputSeq != input || jumpSeq != jump || lookSeq != look;
    }
    bool active() const {
        return inputTicks != 0 || lookTicks != 0 || jumpPending != 0 || heldEvents != 0;
    }
};

// One game-thread writer; pipe readers never touch the tick-owned countdowns
// or its request-mailbox fallback. Bounded reads fail closed during a write.
class InputLifecycleStore {
    using Words = std::array<std::uint64_t, 9>;
    static_assert(sizeof(InputLifecycleSnapshot) == sizeof(Words));
    static_assert(std::atomic<std::uint64_t>::is_always_lock_free);
public:
    void publish(InputLifecycleSnapshot const& snapshot) noexcept {
        auto const words = std::bit_cast<Words>(snapshot);
        auto const sequence = mSequence.load();
        mSequence.store(sequence + 1);
        for (std::size_t i = 0; i < words.size(); ++i) mWords[i].store(words[i]);
        mSequence.store(sequence + 2);
    }

    bool read(InputLifecycleSnapshot& out, std::uint64_t epoch) const noexcept {
        for (int attempt = 0; attempt < 8; ++attempt) {
            auto const before = mSequence.load();
            if (before & 1u) continue;
            Words words{};
            for (std::size_t i = 0; i < words.size(); ++i) words[i] = mWords[i].load();
            if (mSequence.load() != before) continue;
            auto const next = std::bit_cast<InputLifecycleSnapshot>(words);
            if (!next.valid || next.epoch != epoch) return false;
            out = next;
            return true;
        }
        return false;
    }
private:
    std::atomic<std::uint64_t> mSequence{0};
    std::array<std::atomic<std::uint64_t>, 9> mWords{};
};

} // namespace probe::detail
