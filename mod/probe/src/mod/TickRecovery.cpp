#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

void ProbeMod::reportInternalError(TickPhase phase, char const* message) noexcept {
    mTickRecovery.fail(phase, message, mTick.load(std::memory_order_relaxed));
    mInputSession.failStop();
    mClickRequest = -1;
    mSlotRequest = -1;
    mMacroSession.cancelPlayback(MacroEnd::InternalError);
    // These helpers have their own per-call SEH/C++ boundaries. A failed
    // release retains its held edge for the recovery ticks to retry.
    releaseRealInput();
    releaseInputEvents();
}

bool ProbeMod::recoverTickControls(LocalPlayer& player) {
    auto const state = mTickRecovery.snapshot();
    if (state.phase == TickRecovery::Phase::Running) return false;

    bool released = releaseRealInput();
    if (!releaseInputEvents()) released = false;
    if (mInputSession.inputMode.load() != ControlInputMode::Hardware
        || mInputSession.jumpImpulseMode.load() != JumpImpulse::None) {
        if (!guardedControl(ControlOp::Legacy, [&] {
                auto client = ll::service::getClientInstance();
                return client && client->getLocalPlayer() == &player
                    && applyLegacyControls(*client, player, ControlState{});
            })) released = false;
    }
    releaseMacroIfRequested();
    if (mMacroSession.playback.releasePending() || mInputSession.controlCleanupPending.load()) released = false;
    if (state.phase != TickRecovery::Phase::Prepared || !released) return true;

    // Recovery must never use readRequests()'s old fallback. Cold cleanup
    // publishes cleared sequence numbers before Prepared; consume a freshly
    // validated mailbox before admitting commands again.
    RequestBlock requests;
    if (!mRequests.read(requests)) return true;
    mLastRequests = requests;
    mInputSession.seenInputSeq = requests.inputSeq;
    mInputSession.seenJumpSeq = requests.jumpSeq;
    mInputSession.seenLookSeq = requests.lookSeq;
    mInputSession.resetButtons();
    mTickRecovery.finish(state.id, released, true);
    return true; // Resume on the following tick, with a new command only.
}

void ProbeMod::drainInternalErrors() {
    if (mTickRecovery.snapshot().phase == TickRecovery::Phase::Pending
        && GetTickCount64() >= mTickRecoveryHoldUntilMs.load()) {
        std::lock_guard admission(mTickRecoveryMutex);
        auto const state = mTickRecovery.snapshot();
        if (state.phase == TickRecovery::Phase::Pending) {
            updateRequests([](RequestBlock& request) {
                request.input = ControlState{};
                request.inputTicks = request.lookTicks = 0;
                ++request.inputSeq;
                request.jumpCancel = true;
                ++request.jumpSeq;
                ++request.lookSeq;
            });
            cancelTasks("internal_error");
            mMacroSession.cancelPlayback(MacroEnd::InternalError);
            cancelInputEvents("internal_error");
            cancelFlight("internal_error");
            cancelTeleport("internal_error");
            cancelPacketInjection("internal_error");
            mEntityCapture.cancel("internal_error");
            mSlotCapture.cancel("internal_error");
            mClickRequest = mSlotRequest = -1;
            // A newer tick failure invalidates this preparation ticket.
            mTickRecovery.prepare(state.id);
        }
    }

    TickFailure row;
    while (mTickRecovery.pop(row)) {
        enqueue(nlohmann::json{
            {"event", "error"}, {"reason", "internal_error"}, {"phase", tickPhaseName(row.phase)},
            {"message", row.message.c_str()}, {"truncated", row.truncated},
            {"recovery_id", row.id}, {"tick", row.tick}
        }.dump(-1, ' ', false, nlohmann::json::error_handler_t::replace));
        auto const now = GetTickCount64();
        if (mLastInternalErrorMs == 0 || now - mLastInternalErrorMs >= 1000) {
            mLastInternalErrorMs = now;
            getSelf().getLogger().error("harness: {} failed: {}", tickPhaseName(row.phase), row.message.c_str());
        }
    }
    if (auto lost = mTickRecovery.takeLost())
        enqueue(nlohmann::json{{"event", "tick_error_diagnostic"}, {"reason", "buffer_full"}, {"lost", lost}}.dump());
}

void ProbeMod::injectTickError(TickPhase phase, bool clientRead) {
    if (!mRunning.load() || !mWorld.inWorld() || !hooksAllowed()) return;
    auto& pending = mTickErrorTests[static_cast<unsigned>(phase)];
    if ((pending.load() == TickErrorTest::ClientRead) != clientRead) return;
    auto const test = pending.exchange(TickErrorTest::None);
    if (test == TickErrorTest::ClientRead) RaiseException(EXCEPTION_ACCESS_VIOLATION, 0, 0, nullptr);
    if (test == TickErrorTest::Standard) throw std::runtime_error("selftest tick recovery");
    if (test == TickErrorTest::Unknown) throw 1;
    if (test == TickErrorTest::InvalidMessage) {
        struct InvalidError : std::exception {
            char const* what() const noexcept override { return "selftest invalid UTF-8: \xFF"; }
        };
        throw InvalidError{};
    }
    if (test == TickErrorTest::LongMessage) {
        struct LongError : std::exception {
            char const* what() const noexcept override {
                return "selftest tick recovery "
                    "01234567890123456789012345678901234567890123456789"
                    "01234567890123456789012345678901234567890123456789"
                    "01234567890123456789012345678901234567890123456789"
                    "01234567890123456789012345678901234567890123456789"
                    "01234567890123456789012345678901234567890123456789"
                    "01234567890123456789012345678901234567890123456789";
            }
        };
        throw LongError{};
    }
}

} // namespace probe
