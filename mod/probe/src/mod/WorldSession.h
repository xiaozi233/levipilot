#pragma once

#include "ll/api/data/CancellableCallback.h"
#include "ll/api/event/Listener.h"

#include <atomic>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>

namespace probe {
class ProbeMod;

// Owns world-flow state and subscriptions. All game/UI work runs on the client
// executor; the pipe only submits requests containing copied values.
class WorldSession {
public:
    explicit WorldSession(ProbeMod& owner) : mOwner(owner) {}
    void start();
    void stop();
    bool inWorld() const { return mInWorld.load(std::memory_order_relaxed) && mPhase.load() == Phase::Playing; }
    void observePlayer(bool present) {
        mInWorld.store(present, std::memory_order_relaxed);
        if (present) {
            auto expected = Phase::Menu;
            mPhase.compare_exchange_strong(expected, Phase::Playing);
        }
    }
    void setSignInWaitMs(int milliseconds) { mSignInWaitMs.store(milliseconds); }
    void enter(std::string name, std::string id, int intent, bool force);
    void leave();
    void list();
    void signIn();
    void autoEnter(std::string name, std::string options, int delayMs);

private:
    enum class Phase { Menu, Entering, Playing, Leaving };
    void enterOnClient(
        std::string const& name,
        std::string const& id,
        int                intent,
        bool               force,
        bool               automatic,
        std::uint64_t      deadline
    );
    void leaveOnClient();
    void listOnClient();
    void signInOnClient();
    void autoEnterOnClient(std::string const& name, std::string const& options, int delayMs, std::uint64_t deadline);
    void post(std::function<void()> task);
    std::shared_ptr<ll::data::CancellableCallback> after(std::function<void()> task, int milliseconds);
    void finishEntry();
    [[nodiscard]] char const* phaseName() const;

    ProbeMod&                                                   mOwner;
    std::atomic_bool                                            mInWorld{false};
    std::atomic<Phase>                                          mPhase{Phase::Menu};
    // One local-server start at a time. Both the explicit enter command and
    // auto_enter go through it; a second start while the first is still
    // loading corrupts the UI transition and crashes the client.
    std::atomic_bool                                            mEntryInFlight{false};
    std::atomic<int>                                            mSignInWaitMs{15000};
    std::atomic<std::uint64_t>                                  mEpoch{0};
    std::atomic<std::shared_ptr<ll::data::CancellableCallback>> mSignInRetry;
    std::atomic<std::shared_ptr<ll::data::CancellableCallback>> mAutoRetry;
    ll::event::ListenerPtr mStartJoinListener, mJoinListener, mExitListener, mCancelListener;
};
} // namespace probe
