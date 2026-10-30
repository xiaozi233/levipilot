#pragma once

#include <atomic>
#include <cstdint>
#include <thread>

namespace probe::detail {

// Stops new callback entries before waiting for active callbacks to leave.
// The closed bit and active count share one atomic so stop cannot miss a
// callback paused between its entry check and count increment.
class CallbackGate {
    static constexpr std::uint32_t kClosed    = 1u << 31;
    static constexpr std::uint32_t kCountMask = kClosed - 1;

public:
    bool enter() noexcept {
        std::uint32_t state = mState.load(std::memory_order_acquire);
        for (;;) {
            if ((state & kClosed) != 0 || (state & kCountMask) == kCountMask) {
                return false;
            }
            if (mState.compare_exchange_weak(
                    state,
                    state + 1,
                    std::memory_order_acq_rel,
                    std::memory_order_acquire
                )) {
                return true;
            }
        }
    }

    void leave() noexcept { mState.fetch_sub(1, std::memory_order_release); }

    void closeAndWait() noexcept {
        mState.fetch_or(kClosed, std::memory_order_acq_rel);
        while ((mState.load(std::memory_order_acquire) & kCountMask) != 0) {
            std::this_thread::yield();
        }
    }

    // Call only after closeAndWait() has returned.
    void open() noexcept { mState.store(0, std::memory_order_release); }

    class Guard {
    public:
        explicit Guard(CallbackGate& gate) noexcept : mGate(gate.enter() ? &gate : nullptr) {}
        Guard(Guard const&) = delete;
        Guard& operator=(Guard const&) = delete;
        ~Guard() {
            if (mGate != nullptr) {
                mGate->leave();
            }
        }

        explicit operator bool() const noexcept { return mGate != nullptr; }

    private:
        CallbackGate* mGate;
    };

private:
    std::atomic<std::uint32_t> mState{kClosed};
};

} // namespace probe::detail
