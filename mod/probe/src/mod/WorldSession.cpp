#include "ll/api/thread/ThreadPoolExecutor.h"
#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

void ProbeMod::enterWorld(std::string const& name, std::string const& id, int intent, bool force) {
    mWorld.enter(name, id, intent, force);
}
void ProbeMod::leaveWorld() { mWorld.leave(); }
void ProbeMod::listWorlds() { mWorld.list(); }
void ProbeMod::reportSignInState() { mWorld.signIn(); }

void WorldSession::post(std::function<void()> task) {
    auto const epoch = mEpoch.load(std::memory_order_acquire);
    mOwner.queueClientTask([this, epoch, task = std::move(task)] {
        if (mEpoch.load(std::memory_order_acquire) == epoch) task();
    });
}

std::shared_ptr<ll::data::CancellableCallback> WorldSession::after(std::function<void()> task, int milliseconds) {
    auto const epoch = mEpoch.load(std::memory_order_acquire);
    // Delay in wall-clock time, then enqueue on the main thread. World-tick
    // delays cannot make progress while waiting for sign-in at the main menu.
    return ll::thread::ThreadPoolExecutor::getDefault().executeAfter(
        [this, epoch, task = std::move(task)] {
            mOwner.queueClientTask([this, epoch, task] {
                if (mEpoch.load(std::memory_order_acquire) == epoch) task();
            });
        },
        std::chrono::milliseconds(milliseconds)
    );
}

void WorldSession::enter(std::string name, std::string id, int intent, bool force) {
    post([this, name = std::move(name), id = std::move(id), intent, force] {
        // An explicit enter always wins over a pending auto_enter.
        if (auto old = mAutoRetry.exchange({})) old->cancel();
        if (mEntryInFlight.load(std::memory_order_acquire)) {
            // The load already in flight will emit enter_world_settled; replying
            // ok keeps the caller waiting for that event instead of retrying.
            mOwner.enqueue(
                R"({"event":"enter_world_result","ok":true,"reason":"already_starting"})"
            );
            return;
        }
        enterOnClient(name, id, intent, force, false, GetTickCount64() + mSignInWaitMs.load());
    });
}

void WorldSession::leave() {
    post([this] { leaveOnClient(); });
}
void WorldSession::list() {
    post([this] { listOnClient(); });
}
void WorldSession::signIn() {
    post([this] { signInOnClient(); });
}

void WorldSession::autoEnter(std::string name, std::string options, int delayMs) {
    post([this, name = std::move(name), options = std::move(options), delayMs] {
        autoEnterOnClient(name, options, delayMs, 0);
    });
}

void WorldSession::autoEnterOnClient(
    std::string const& name,
    std::string const& options,
    int                delayMs,
    std::uint64_t      deadline
) {
    // A scripted enter is authoritative; stop the auto chain instead of racing
    // it with a second local-server start.
    if (mEntryInFlight.load(std::memory_order_acquire)) {
        if (auto old = mAutoRetry.exchange({})) old->cancel();
        return;
    }
    auto                      client = ll::service::getClientInstance();
    std::vector<LevelSummary> worlds;
    if (client) client->getMinecraftGame().getLevelListCache().getLevelList(worlds, true, false, false, false);
    auto const now   = GetTickCount64();
    bool const ready = !worlds.empty() && detail::isGameForeground();
    if (ready && deadline == 0) deadline = now + delayMs;
    if (!ready || now < deadline) {
        auto pending = after(
            [this, name, options, delayMs, deadline] { autoEnterOnClient(name, options, delayMs, deadline); },
            100
        );
        if (auto old = mAutoRetry.exchange(std::move(pending))) old->cancel();
        return;
    }
    if (auto old = mAutoRetry.exchange({})) old->cancel();
    if (!options.empty()) mOwner.setOptions(options);
    if (!name.empty() && !inWorld()) {
        mOwner.bootLog("auto: entering " + name);
        enterOnClient(name, "", 0, false, true, now + mSignInWaitMs.load());
    }
}

void WorldSession::start() {
    mEpoch.fetch_add(1);
    auto& bus          = ll::event::EventBus::getInstance();
    mStartJoinListener = bus.emplaceListener<ll::event::client::ClientStartJoinLevelEvent>([this](auto& event) {
        mPhase = Phase::Entering;
        mOwner.enqueue(
            nlohmann::json{
                {"event", "world_joining"  },
                {"world", event.worldName()}
        }.dump()
        );
    });
    mJoinListener      = bus.emplaceListener<ll::event::client::ClientJoinLevelEvent>([this](auto& event) {
        mInWorld = true;
        mPhase   = Phase::Playing;
        finishEntry();
        mOwner.enqueue(
            nlohmann::json{
                {"event",  "world_entered"             },
                {"player", event.player().getRealName()},
        }
                .dump()
        );
        mOwner.enqueue(R"({"event":"enter_world_settled","ok":true,"reason":"joined"})");
    });
    mExitListener      = bus.emplaceListener<ll::event::client::ClientExitLevelEvent>([this](auto&) {
        mInWorld = false;
        finishEntry();
        mOwner.cancelTasks("world_left");
        mOwner.beginTask();
        bool const wasJoining = mPhase.exchange(Phase::Leaving) == Phase::Entering;
        mOwner.enqueue(R"({"event":"world_left"})");
        if (wasJoining) {
            mOwner.enqueue(R"({"event":"enter_world_settled","ok":false,"reason":"left_while_joining"})");
        }
        // F4: this listener may run on the client thread; ask the spool thread
        // to flush immediately instead of doing file IO here.
        mOwner.mSpoolForce.store(true, std::memory_order_release);
    });
    mCancelListener    = bus.emplaceListener<ll::event::client::ClientCancelJoinLevelEvent>([this](auto&) {
        mPhase = Phase::Leaving;
        finishEntry();
        mOwner.enqueue(R"({"event":"world_join_cancelled"})");
        mOwner.enqueue(R"({"event":"enter_world_settled","ok":false,"reason":"cancelled"})");
    });
}

void WorldSession::stop() {
    mEpoch.fetch_add(1);
    if (auto callback = mSignInRetry.exchange({})) callback->cancel();
    if (auto callback = mAutoRetry.exchange({})) callback->cancel();
    if (mStartJoinListener) {
        ll::event::EventBus::getInstance().removeListener<ll::event::client::ClientStartJoinLevelEvent>(
            mStartJoinListener
        );
        mStartJoinListener.reset();
    }
    if (mJoinListener) {
        ll::event::EventBus::getInstance().removeListener<ll::event::client::ClientJoinLevelEvent>(mJoinListener);
        mJoinListener.reset();
    }
    if (mExitListener) {
        ll::event::EventBus::getInstance().removeListener<ll::event::client::ClientExitLevelEvent>(mExitListener);
        mExitListener.reset();
    }
    if (mCancelListener) {
        ll::event::EventBus::getInstance().removeListener<ll::event::client::ClientCancelJoinLevelEvent>(
            mCancelListener
        );
        mCancelListener.reset();
    }

    mInWorld = false;
    mPhase   = Phase::Menu;
    finishEntry();
}

void WorldSession::finishEntry() { mEntryInFlight.store(false, std::memory_order_release); }

char const* WorldSession::phaseName() const {
    switch (mPhase.load(std::memory_order_relaxed)) {
    case Phase::Entering:
        return "entering";
    case Phase::Playing:
        return "playing";
    case Phase::Leaving:
        return "leaving";
    default:
        return "menu";
    }
}

} // namespace probe
