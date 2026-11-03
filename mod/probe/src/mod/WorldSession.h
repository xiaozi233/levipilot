#pragma once

#include "mod/WorldEntryModel.h"

#include "ll/api/data/CancellableCallback.h"
#include "ll/api/event/Listener.h"

#include <atomic>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <nlohmann/json_fwd.hpp>

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
    void enter(std::string name, std::string id, int intent, bool force,
               detail::EntryTest test = detail::EntryTest::None);
    void pollEntry(); // Spool wall-clock deadline; game inspection is queued.
    nlohmann::json entryStatus() const;
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
        std::uint64_t      deadline,
        detail::EntryTest   test = detail::EntryTest::None
    );
    void leaveOnClient();
    void listOnClient();
    void signInOnClient();
    void autoEnterOnClient(std::string const& name, std::string const& options, int delayMs, std::uint64_t deadline);
    void post(std::function<void()> task);
    std::shared_ptr<ll::data::CancellableCallback> after(std::function<void()> task, int milliseconds);
    void settleEntry(bool ok, char const* reason);
    void emitEntryCompletion(std::optional<detail::WorldEntryModel::Completion> const& completion);
    bool replyEntryBusy();
    void probeEntryOnClient(std::uint64_t id);
    bool menuReadyOnClient() const;
    nlohmann::json menuDiagnosticsOnClient() const;
    [[nodiscard]] char const* phaseName() const;

    ProbeMod&                                                   mOwner;
    std::atomic_bool                                            mInWorld{false};
    std::atomic<Phase>                                          mPhase{Phase::Menu};
    // One local-server start at a time. Both the explicit enter command and
    // auto_enter go through it; a second start while the first is still
    // loading corrupts the UI transition and crashes the client.
    detail::WorldEntryModel                                     mEntry;
    std::atomic<std::uint64_t>                                  mNextEntryProbeMs{0};
    std::atomic<std::uint64_t>                                  mEntryProbeQueued{0};
    std::atomic<std::uint32_t>                                  mEntryProbeErrors{0};
    std::atomic<char const*>                                    mEntryProbeError{nullptr};
    std::atomic<detail::EntryTest>                               mEntryTest{detail::EntryTest::None};
    std::atomic_bool                                            mEntryProbeFault{false};
    std::atomic<std::uint64_t>                                  mEntryUnsafeUntil{0};
    std::atomic<int>                                            mSignInWaitMs{15000};
    std::atomic<std::uint64_t>                                  mEpoch{0};
    std::atomic<std::uint64_t>                                  mLeaveSerial{0};
    std::atomic<std::uint64_t>                                  mLeaveRequest{0};
    std::atomic<std::uint64_t>                                  mLeaveCompleted{0};
    std::atomic<std::shared_ptr<ll::data::CancellableCallback>> mSignInRetry;
    std::atomic<std::shared_ptr<ll::data::CancellableCallback>> mAutoRetry;
    ll::event::ListenerPtr mStartJoinListener, mJoinListener, mExitListener, mCancelListener;
};
} // namespace probe
