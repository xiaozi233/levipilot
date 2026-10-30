#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <type_traits>
#include <utility>

namespace probe::detail {

// Cold writers build a private transaction before publishing. The game-thread
// reader never waits for a writer and retains its own last validated snapshot.
// Atomic payload words are essential: a sequence counter alone does not make
// concurrent access to an ordinary C++ object legal.
template <class T> class RequestMailbox {
    static_assert(std::is_trivially_copyable_v<T>);
    static_assert(std::atomic<std::uint64_t>::is_always_lock_free);
    static constexpr std::size_t WordCount = (sizeof(T) + sizeof(std::uint64_t) - 1) / sizeof(std::uint64_t);
    using Words = std::array<std::uint64_t, WordCount>;

  public:
    RequestMailbox() { publish(mCurrent); }

    template <class Update> void update(Update&& update) {
        std::lock_guard lock(mWriterMutex);
        T next = mCurrent;
        std::forward<Update>(update)(next); // Failure leaves the published value intact.
        mCurrent = next;
        publish(next);
    }

    // Only commit the output after both sequence checks. A busy writer leaves
    // the caller's previous snapshot untouched; no spinning/yielding in tick.
    bool read(T& last) const noexcept {
        for (int attempt = 0; attempt < 8; ++attempt) {
            auto const before = mSequence.load();
            if (before & 1u) continue;
            Words words{};
            for (std::size_t i = 0; i < WordCount; ++i) words[i] = mWords[i].load();
            if (mSequence.load() == before) {
                std::memcpy(&last, words.data(), sizeof(T));
                return true;
            }
        }
        return false;
    }

  private:
    void publish(T const& value) noexcept {
        Words words{};
        std::memcpy(words.data(), &value, sizeof(T));
        auto const sequence = mSequence.load();
        mSequence.store(sequence + 1);
        // Sequential consistency provides one order for sequence and payload,
        // including concurrent overwrite. Publication is a cold command path.
        for (std::size_t i = 0; i < WordCount; ++i) mWords[i].store(words[i]);
        mSequence.store(sequence + 2);
    }

    T mCurrent{}; // Writer-owned, accessed only under mWriterMutex.
    std::mutex mWriterMutex;
    alignas(64) std::atomic<std::uint64_t> mSequence{0};
    std::array<std::atomic<std::uint64_t>, WordCount> mWords{};
};

} // namespace probe::detail
