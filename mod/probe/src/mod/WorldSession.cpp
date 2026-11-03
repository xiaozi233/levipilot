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

void WorldSession::enter(std::string name, std::string id, int intent, bool force, EntryTest test) {
    post([this, name = std::move(name), id = std::move(id), intent, force, test] {
        // An explicit enter always wins over a pending auto_enter.
        if (auto old = mAutoRetry.exchange({})) old->cancel();
        if (replyEntryBusy()) return;
        enterOnClient(name, id, intent, force, false, GetTickCount64() + mSignInWaitMs.load(), test);
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
    if (mEntry.snapshot().phase != WorldEntryModel::Phase::Idle) {
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
        mOwner.mParkourGeneration.fetch_add(1, std::memory_order_release);
        // Manual UI starts share the same observation and terminal contract.
        if (mEntry.snapshot().phase == WorldEntryModel::Phase::Idle) mEntry.begin(GetTickCount64());
        mPhase = Phase::Entering;
        mOwner.enqueue(
            nlohmann::json{
                {"event", "world_joining"  },
                {"world", event.worldName()}
        }.dump()
        );
    });
    mJoinListener      = bus.emplaceListener<ll::event::client::ClientJoinLevelEvent>([this](auto& event) {
        if (mEntryTest.load() == EntryTest::MissingJoin
            && mEntry.snapshot().phase == WorldEntryModel::Phase::Starting) return;
        mInWorld = true;
        mPhase   = Phase::Playing;
        mOwner.enqueue(
            nlohmann::json{
                {"event",  "world_entered"             },
                {"player", event.player().getRealName()},
        }
                .dump()
        );
        settleEntry(true, "joined");
    });
    mExitListener      = bus.emplaceListener<ll::event::client::ClientExitLevelEvent>([this](auto&) {
        mInWorld = false;
        mOwner.mPoseEpoch.fetch_add(1);
        mOwner.mPoseTest = CaptureTest::None;
        mOwner.mPlayerIdentityTest = CaptureTest::None;
        for (auto& test : mOwner.mInputSession.controlTests) test = ControlTest::None;
        for (auto& test : mOwner.mTickErrorTests) test = TickErrorTest::None;
        mOwner.mTickRecoveryHoldUntilMs = 0;
        hookCancelCalls("world_left");
        recordCancel(RecordEnd::WorldLeft);
        mOwner.mParkourGeneration.fetch_add(1, std::memory_order_release);
        mOwner.mLegacyWorldLeft.store(true, std::memory_order_release);
        mOwner.mMacroSession.worldLeft();
        mOwner.mChatCapture.cancel("world_left");
        mOwner.cancelQueries("world_left");
        mOwner.cancelTasks("world_left");
        mOwner.beginTask();
        mPhase = Phase::Leaving;
        auto const leaveId = mLeaveRequest.exchange(0);
        mLeaveCompleted.store(leaveId);
        nlohmann::json left{{"event", "world_left"}, {"leave_id", leaveId}};
        left["menu"] = menuDiagnosticsOnClient();
        left["menu_error"] = left["menu"].is_null();
        mOwner.enqueue(left.dump());
        settleEntry(false, "left_while_joining");
        // F4: this listener may run on the client thread; ask the spool thread
        // to flush immediately instead of doing file IO here.
        mOwner.mSpoolForce.store(true, std::memory_order_release);
    });
    mCancelListener    = bus.emplaceListener<ll::event::client::ClientCancelJoinLevelEvent>([this](auto&) {
        mPhase = Phase::Leaving;
        mOwner.enqueue(R"({"event":"world_join_cancelled"})");
        settleEntry(false, "cancelled");
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
    mEntry.reset();
    mEntryProbeQueued = 0;
}

void WorldSession::emitEntryCompletion(std::optional<WorldEntryModel::Completion> const& completion) {
    if (!completion) return;
    mOwner.enqueue(nlohmann::json{
        {"event", "enter_world_settled"}, {"entry_id", completion->id},
        {"ok", completion->ok}, {"reason", completion->reason},
        {"entry", entryStatus()}
    }.dump());
}

void WorldSession::settleEntry(bool ok, char const* reason) {
    emitEntryCompletion(mEntry.settle(mEntry.snapshot().id, ok, reason));
    if (ok) mEntryTest = EntryTest::None;
}

bool WorldSession::replyEntryBusy() {
    auto const state = mEntry.snapshot();
    if (state.phase == WorldEntryModel::Phase::Idle) return false;
    bool const pending = state.phase == WorldEntryModel::Phase::Starting;
    mOwner.enqueue(nlohmann::json{
        {"event", "enter_world_result"}, {"ok", pending}, {"entry_id", state.id},
        {"reason", pending ? "already_starting" : "entry_recovering"}
    }.dump());
    return true;
}

nlohmann::json WorldSession::entryStatus() const {
    auto const state = mEntry.snapshot();
    auto const now = GetTickCount64();
    auto const error = mEntryProbeError.load();
    return {{"entry_id", state.id},
            {"phase", state.phase == WorldEntryModel::Phase::Idle ? "idle"
                    : state.phase == WorldEntryModel::Phase::Starting ? "starting" : "quarantined"},
            {"remaining_ms", state.phase == WorldEntryModel::Phase::Starting && state.deadline > now
                            ? state.deadline - now : 0},
            {"probe_errors", mEntryProbeErrors.load()},
            {"last_probe_error", error ? error : ""}};
}

void WorldSession::pollEntry() {
    auto const now = GetTickCount64();
    // This deadline runs independently of both world ticks and the UI queue.
    emitEntryCompletion(mEntry.expire(now));
    auto const state = mEntry.snapshot();
    if (state.phase == WorldEntryModel::Phase::Idle || now < mNextEntryProbeMs.load()) return;
    std::uint64_t empty = 0;
    if (!mEntryProbeQueued.compare_exchange_strong(empty, state.id)) return;
    mNextEntryProbeMs = now + 100;
    auto const epoch = mEpoch.load();
    try {
        if (mOwner.queueClientTask([this, epoch, id = state.id] {
            try {
                if (mEpoch.load() == epoch) probeEntryOnClient(id);
            } catch (...) {
                ++mEntryProbeErrors;
                mEntryProbeError = "probe_callback_failed";
            }
            auto expected = id;
            mEntryProbeQueued.compare_exchange_strong(expected, 0);
        })) return;
        mEntryProbeError = "queue_unavailable";
    } catch (...) {
        mEntryProbeError = "queue_failed";
    }
    ++mEntryProbeErrors;
    auto expected = state.id;
    mEntryProbeQueued.compare_exchange_strong(expected, 0);
}

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
