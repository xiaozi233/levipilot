#include "mod/Internal.h"

#include "ll/api/command/CommandHandle.h"
#include "ll/api/command/CommandRegistrar.h"
#include "ll/api/command/Overload.h"
#include "ll/api/event/command/ServerCommandRegisterEvent.h"
#include "mc/server/commands/CommandOrigin.h"
#include "mc/server/commands/CommandOutput.h"
#include "mc/world/level/GameType.h"

#include <dbghelp.h>

using namespace probe::detail;

namespace probe {


ProbeMod& ProbeMod::getInstance() {
    static ProbeMod instance;
    return instance;
}

bool ProbeMod::load() {
    getSelf().getLogger().info("harness: loading");
    bootLog("load");
    return true;
}

bool ProbeMod::enable() {
    if (mRunning.exchange(true)) {
        return true;
    }
    bootLog("enable: begin");
    mChatCapture.reset();
    resetQueries();
    hookResetCalls();
    mPoseEpoch.fetch_add(1);

    mMouseListener =
        ll::event::EventBus::getInstance().emplaceListener<ll::event::input::MouseInputEvent>([this](auto& event) {
            // MouseAction: ActionMove=0, ActionLeft=1, ActionRight=2, DataDown=1.
            int const action = static_cast<int>(event.actionButtonId());
            int const data   = static_cast<int>(event.buttonData());
            mMouseEventCount.fetch_add(1, std::memory_order_relaxed);
            mMouseLastAction = action;
            mMouseLastData   = data;
            if (action >= 0 && action < static_cast<int>(mMouseActionCounts.size())) {
                mMouseActionCounts[static_cast<size_t>(action)].fetch_add(1, std::memory_order_relaxed);
            }

            if (action == 1) {
                mMouseLeft = data != 0;
                if (data != 0) {
                    mMouseLeftLatch = true;
                }
            } else if (action == 2) {
                mMouseRight = data != 0;
                if (data != 0) {
                    mMouseRightLatch = true;
                }
            }
        });

    mKeyListener =
        ll::event::EventBus::getInstance().emplaceListener<ll::event::input::KeyInputEvent>([this](auto& event) {
            mKeyEventCount.fetch_add(1, std::memory_order_relaxed);
            mKeyLastCode.store(event.keyCode(), std::memory_order_relaxed);
            mKeyLastAction.store(static_cast<std::int32_t>(event.action()), std::memory_order_relaxed);
            if (mInjectingInput.load(std::memory_order_relaxed)) {
                mKeyInjectedSeen.fetch_add(1, std::memory_order_relaxed);
            }
        });

    std::error_code ec;
    std::filesystem::create_directories(macroDir(), ec);

    // T1: everything printed to the pipe is also spooled to the mod directory.
    // Resolve an absolute path once so the crash handler works regardless of
    // the process CWD and never allocates.
    std::error_code absoluteEc;
    auto            modDirPath = std::filesystem::absolute(getSelf().getModDir(), absoluteEc);
    if (absoluteEc) {
        modDirPath = getSelf().getModDir();
    }
    auto const modDir = modDirPath.wstring();
    if (!modDir.empty() && modDir.size() < std::size(mModDirW)) {
        std::copy(modDir.begin(), modDir.end(), mModDirW);
        mModDirW[modDir.size()] = L'\0';
    }
    mSpoolPath = modDirPath
               / ("spool-" + std::to_string(GetCurrentProcessId()) + ".jsonl");
    // F6: a reused PID must not append this session to an old spool file.
    {
        std::error_code ec;
        if (std::filesystem::exists(mSpoolPath, ec)
            && std::filesystem::file_size(mSpoolPath, ec) > 0) {
            auto const pid = std::to_string(GetCurrentProcessId());
            for (int n = 1; n < 1000; ++n) {
                auto candidate = modDirPath / ("spool-" + pid + "-" + std::to_string(n) + ".jsonl");
                if (!std::filesystem::exists(candidate, ec)) {
                    std::filesystem::rename(mSpoolPath, candidate, ec);
                    break;
                }
            }
        }
    }
    installCrashHandler();
    resolveInit(getSelf().getModDir());
    watchInit();
    overheadInit();

    mWorld.start();
    loadAutoConfig();
    // On a client the mod is enabled at the main window, long before any server
    // (and its command registry) exists -- registering in enable() crashes.
    // LeviLamina fires this event while the local server builds its command
    // tree, which is the documented place for client mods to add server
    // commands. The registry is rebuilt per world, hence the pointer guard.
    mServerCommandListener =
        ll::event::EventBus::getInstance().emplaceListener<ll::event::command::ServerCommandRegisterEvent>(
            [this](auto& event) { registerCommands(event.commandRegistry()); }
        );
    loadCheckpoints();

    mSpoolThread = std::thread([this] { spoolThreadLoop(); });

    mServerThread = std::thread([this] { serverLoop(); });
    getSelf().getLogger().info("harness: enabled, pipe=\\\\.\\pipe\\mcbe-harness");
    bootLog("enable: done");
    return true;
}

bool ProbeMod::disable() {
    if (!mRunning.exchange(false)) {
        return true;
    }

    stopLegacy(StreamEnd::Disabled, false);
    cancelTasks("disabled");
    mChatCapture.cancel("disabled");
    cancelQueries("disabled");
    hookCancelCalls("disabled");
    recordCancel(RecordEnd::Disabled);
    mPoseEpoch.fetch_add(1);
    {
        std::lock_guard lock(mMacroSessionMutex);
        mMacroPlayback.stop(MacroEnd::Disabled);
        mMacroRecording.stop(MacroRecordEnd::Disabled);
        drainMacroRecordingLocked();
    }
    // Input release must happen on the game tick. If a final tick is still
    // delivered during shutdown, this request clears event-level keys before
    // the mod leaves; it also makes the intent explicit for a later unload.

    packetCaptureStop();
    // Never leave the game with real input dropped after an unload.
    mHardwareInputLocked = false;

    if (mMouseListener) {
        ll::event::EventBus::getInstance().removeListener<ll::event::input::MouseInputEvent>(mMouseListener);
        mMouseListener.reset();
    }
    if (mKeyListener) {
        ll::event::EventBus::getInstance().removeListener<ll::event::input::KeyInputEvent>(mKeyListener);
        mKeyListener.reset();
    }
    if (mServerCommandListener) {
        ll::event::EventBus::getInstance().removeListener<ll::event::command::ServerCommandRegisterEvent>(
            mServerCommandListener
        );
        mServerCommandListener.reset();
    }
    mWorld.stop();

    // The channel observes mRunning while waiting for connect/read/write and
    // cancels/drains its own operation before leaving the worker.
    if (mServerThread.joinable()) {
        mServerThread.join();
    }
    recordStop();
    if (mSpoolThread.joinable()) {
        mSpoolThread.join();
    }
    // The spool worker can finish before an admitted capture releases its
    // lease. stop() has now joined it, so persist any remaining final failure.
    if (auto failure = recordTakeFailure()) enqueue(failure->dump());
    drainMacroPlayback();
    drainClicks();
    flushSpool(true);
    {
        std::lock_guard lock(mSpoolFileMutex);
        if (mSpoolFile.is_open()) {
            mSpoolFile.close();
        }
    }
    watchShutdown();

    {
        std::lock_guard lock(mQueueMutex);
        mQueue.clear();
    }
    getSelf().getLogger().info("harness: disabled");
    return true;
}

void ProbeMod::loadAutoConfig() {
    auto          path = getSelf().getModDir() / "harness.json";
    std::ifstream in(path);
    if (!in) {
        return;
    }
    auto config = nlohmann::json::parse(in, nullptr, false);
    if (config.is_discarded()) {
        bootLog("config: invalid json");
        return;
    }
    auto const autoWorld = config.value("auto_enter", std::string{});
    auto const autoDelayMs = std::clamp(config.value("delay_ms", 2000), 0, 60000);
    std::string autoOptions;
    mBackgroundGuard  = config.value("background_guard", true);
    mAllowMemoryWrite = config.value("allow_write", false);
    mAllowCrashTest   = config.value("allow_crash_test", false);
    mAllowHookVa      = config.value("allow_hook_va", false);
    mAllowSelftestStress = config.value("allow_selftest_stress", false);
    mAllowWatch      = config.value("allow_watch", true);
    mAllowPacketCapture = config.value("allow_packet_capture", false);
    mAllowPacketInject  = config.value("allow_packet_inject", false);
    mAllowInputInject = config.value("allow_input_inject", false);
    // Armed by serverLoop() when a client connects, not here: a lock held from
    // startup (previous script run, stale harness.json) makes the main menu
    // unclickable for the person at the keyboard.
    mLockInputOnConnect  = config.value("lock_input", false);
    mHardwareInputLocked = false;
    mAllowVersionMismatch = config.value("allow_version_mismatch", false);
    setAllowVersionMismatch(mAllowVersionMismatch.load());
    mWorld.setSignInWaitMs(std::clamp(config.value("signin_wait_ms", 15000), 0, 120000));
    if (config.contains("options") && config["options"].is_object()) {
        for (auto it = config["options"].begin(); it != config["options"].end(); ++it) {
            std::string value  = it.value().is_string() ? it.value().get<std::string>() : it.value().dump();
            autoOptions += it.key() + ":" + value + "\n";
        }
    }
    if (autoWorld.empty() && autoOptions.empty()) {
        return;
    }
    bootLog("config: auto_enter=" + autoWorld + " delay=" + std::to_string(autoDelayMs));
    mWorld.autoEnter(autoWorld, autoOptions, autoDelayMs);
}

void ProbeMod::onPlayerTick(LocalPlayer& player) {
    mTickStartQpc = qpcNow();
    // A bug in our own code must not take the game down with it.
    try {
        safeTickPre(player);
        if (!mTickRecovery.active()) injectTickError(TickPhase::Pre);
    } catch (std::exception const& e) {
        reportInternalError(TickPhase::Pre, e.what());
    } catch (...) {
        reportInternalError(TickPhase::Pre, "unknown exception");
    }
    mPreUs = qpcElapsedUs(mTickStartQpc, qpcNow());
}

void ProbeMod::safeTickPre(LocalPlayer& player) {
    if (!mRunning.load(std::memory_order_relaxed) || !hooksAllowed()) {
        return;
    }
    mJumpEdgeThisTick = false;
    auto client       = ll::service::getClientInstance();
    if (!client || client->getLocalPlayer() != &player) {
        releaseRealInput();
        mWorld.observePlayer(false);
        return;
    }
    mWorld.observePlayer(true);
    mLastTickMs = GetTickCount64();
    if (recoverTickControls(player)) return;

    capturePlayerIdentity(player);

    latchActionClicks(player);
    // G1/G2: drain queued event-level input into the game's own queues before
    // anything else in this tick touches movement.
    tickInputInjection();
    tickPacketInject(player);

    // T9: one lock-free snapshot of movement/look requests per tick.
    RequestBlock const requests = readRequests();

    if (mResetInputButtons.exchange(false, std::memory_order_acq_rel)) {
        mButtonStates.fill(false);
        mHandlerStates.fill(false);
        mPrevJump = false;
    }

    tickFlightAssertion(player);

    if (int click = mClickRequest.exchange(-1); click >= 0) {
        performClick(player, click);
    }

    releaseMacroIfRequested();

    // Closed-loop tasks own the tick while they run.
    if (tickTask(*client, player)) {
        return;
    }

    if (mMacroPlayback.active()) {
        tickMacroPlayback(*client, player);
        return;
    }

    consumeMovementRequests(*client, player, requests);
    consumeLookRequests(player, requests);
}

void ProbeMod::registerCommands(CommandRegistry& registry) {
    // A real server command players can type in chat: /togglefly grants or
    // removes the MayFly ability (the double-space flight gate) in any mode
    // except spectator. NotCheat keeps it usable without enabling cheats.
    // Server-side only: the ability is server-authoritative, and a client-side
    // command would flip the client copy that the next sync overwrites.
    if (mCommandRegistry == &registry) {
        return;
    }
    try {
        auto& handle = ll::command::CommandRegistrar::getServerInstance().getOrCreateCommand(
            "togglefly",
            "grant/remove double-space flight (non-spectator)",
            CommandPermissionLevel::Any,
            CommandFlagValue::NotCheat
        );
        handle.overload().execute(
            [](CommandOrigin const& origin, CommandOutput& output) {
                auto* actor = origin.getEntity();
                if (actor == nullptr || !actor->isPlayer()) {
                    output.error("togglefly: player origin required");
                    return;
                }
                auto& player = *static_cast<Player*>(actor);
                if (player.getPlayerGameType() == GameType::Spectator) {
                    output.error("togglefly: 旁观模式不支持");
                    return;
                }
                bool const current = player.getAbilities().getBool(AbilitiesIndex::MayFly);
                bool const target  = !current;
                player.setAbility(AbilitiesIndex::MayFly, target);
                if (!target) {
                    player.setAbility(AbilitiesIndex::Flying, false);
                }
                output.success(
                    target ? "双击空格切换飞行：已开启" : "双击空格切换飞行：已关闭"
                );
            }
        );
        mCommandRegistry = &registry;
        bootLog("command: registered togglefly");
    } catch (...) {
        bootLog("command: togglefly registration failed");
    }
}

void ProbeMod::consumeMovementRequests(ClientInstance& client, LocalPlayer& player, RequestBlock const& requests) {
    ControlState state;
    bool         apply = false;
    if (requests.inputSeq != mSeenInputSeq) {
        mSeenInputSeq     = requests.inputSeq;
        mActiveInput      = requests.input;
        mActiveInputTicks = requests.inputTicks;
    }
    if (requests.jumpSeq != mSeenJumpSeq) {
        mSeenJumpSeq        = requests.jumpSeq;
        mJumpRequestPending = !requests.jumpCancel;
    }
    if (mActiveInputTicks != 0) {
        state = mActiveInput;
        apply = state.active || state.jump;
        if (mActiveInputTicks > 0) {
            --mActiveInputTicks;
            if (mActiveInputTicks == 0) {
                mActiveInput = ControlState{};
            }
        }
    }
    if (mJumpRequestPending) {
        mJumpRequestPending = false;
        state.jump          = true;
        state.active        = true;
        apply               = true;
    }
    if (!applyControls(client, player, apply ? state : ControlState{})) {
        // This sequence is consumed. Do not silently replay an unbounded
        // desired state after an uncertain edge; a new request may restart it.
        mActiveInput = ControlState{};
        mActiveInputTicks = 0;
        mJumpRequestPending = false;
    }
}

void ProbeMod::consumeLookRequests(LocalPlayer& player, RequestBlock const& requests) {
    // Absolute look targets are applied after movement so the rotation matches the current tick.
    if (requests.lookSeq != mSeenLookSeq) {
        mSeenLookSeq        = requests.lookSeq;
        mActiveLookRelative = requests.lookRelative;
        mActiveLookYaw      = requests.lookYaw;
        mActiveLookPitch    = requests.lookPitch;
        mActiveLookTicks    = requests.lookTicks;
    }
    bool const lookActive = mActiveLookTicks != 0;
    if (lookActive) {
        Vec2 rot;
        if (!guardedControl(ControlOp::LookRead, [&] { rot = player.getRotation(); return true; })) {
            mActiveLookTicks = 0;
            return;
        }
        // _applyTurnDelta uses the opposite pitch sign from ActorRotation.x.
        float dy = mActiveLookRelative ? mActiveLookYaw : wrapDegrees(mActiveLookYaw - rot.y);
        float dp = mActiveLookRelative ? -mActiveLookPitch : (rot.x - mActiveLookPitch);
        if (mActiveLookTicks > 0) {
            --mActiveLookTicks;
        }
        if (std::fabs(dy) > 1.0e-5f || std::fabs(dp) > 1.0e-5f) {
            if (!applyTurnDelta(player, Vec2{dp, dy})) mActiveLookTicks = 0;
        }
    }
}

void ProbeMod::afterPlayerTick(LocalPlayer& player) {
    std::uint64_t const postStart = qpcNow();
    try {
        safeTickPost(player);
        if (!mTickRecovery.active()) injectTickError(TickPhase::Post);
    } catch (std::exception const& e) {
        reportInternalError(TickPhase::Post, e.what());
    } catch (...) {
        reportInternalError(TickPhase::Post, "unknown exception");
    }
    std::uint64_t const postEnd = qpcNow();
    std::uint64_t const hookUs  = mPreUs + qpcElapsedUs(postStart, postEnd);
    std::uint64_t const tickUs =
        mPrevTickStartQpc != 0 ? qpcElapsedUs(mPrevTickStartQpc, mTickStartQpc) : 0;
    overheadRecord(
        static_cast<std::uint32_t>(std::min<std::uint64_t>(tickUs, 0xFFFFFFFFull)),
        static_cast<std::uint32_t>(std::min<std::uint64_t>(hookUs, 0xFFFFFFFFull))
    );
    mPrevTickStartQpc = mTickStartQpc;
}

void ProbeMod::safeTickPost(LocalPlayer& player) {
    if (!mRunning.load(std::memory_order_relaxed) || !hooksAllowed()) {
        return;
    }
    auto client = ll::service::getClientInstance();
    if (!client || client->getLocalPlayer() != &player) {
        return;
    }
    updateFocusState();
    overheadBusyTick();

    // T1/T3: the tick thread is the default target for hardware watchpoints,
    // and the pose below is what the crash report captures.
    mTickThreadId.store(GetCurrentThreadId(), std::memory_order_relaxed);
    gTickThreadId.store(GetCurrentThreadId(), std::memory_order_relaxed);
    mTickProbe.fetch_add(1, std::memory_order_relaxed);
    auto tickIndex = mTick.fetch_add(1, std::memory_order_relaxed) + 1;
    gWatchTick.store(tickIndex, std::memory_order_relaxed);
    recordTick(player, *client, tickIndex, mJumpEdgeThisTick);
    recordTickPose(player);

    latchActionClicks(player);

    // Slot changes are applied after the original tick: the normal tick re-reads
    // the hotbar from input state, so a pre-tick change would be overwritten.
    if (int slot = mSlotRequest.exchange(-1); slot >= 0 && !mTickRecovery.active()) {
        // Internal task selection is not a user select_slot request.
        SlotSnapshot ignored;
        applySlotRequest(player, SlotQuery{slot}, ignored);
    }
    if (!mTickRecovery.active()) {
        mSlotCapture.advance([&](auto const& request, auto& out) {
            if (request.test == CaptureTest::Deferred) return false;
            applySlotRequest(player, request, out);
            return true;
        });
    }

    runCaptureRequests(*client, player);

    detail::hookTick(player);

    if (mMacroRecording.active()) {
        recordMacroTick(*client, player);
    }

    // Parkour events: landing (with fall distance), damage and death. These
    // run on every tick, independent of JSON/binary recording.
    tickParkourEvents(player, tickIndex);

    captureLegacyTick(*client, player);
}

void ProbeMod::recordTickPose(LocalPlayer& player) {
    auto const epoch = mPoseEpoch.load();
    auto const test  = mPoseTest.load();
    // Fault modes stay armed until explicitly cleared, allowing a cold reader
    // to verify that a failed sample never reuses the preceding valid pose.
    mPose.capture(epoch, mTick.load(), [&](PoseSnapshot& out) {
        return guardedGameRead([&] {
            injectCaptureFault(test);
            auto const pos  = player.getPosition();
            auto const feet = player.getFeetPos();
            auto const rot  = player.getRotation();
            out.x           = pos.x;
            out.y           = pos.y;
            out.z           = pos.z;
            out.feetX       = feet.x;
            out.feetY       = feet.y;
            out.feetZ       = feet.z;
            out.yaw         = rot.y;
            out.pitch       = rot.x;
        });
    });
}

void ProbeMod::runCaptureRequests(ClientInstance& client, LocalPlayer& player) {
    mStateCapture.advance([&](auto const& request, auto& out) {
        if (request.test == CaptureTest::Deferred) return false;
        captureState(client, player, request, out);
        return true;
    });
    mButtonCapture.advance([&](auto const& request, auto& out) {
        if (request.test == CaptureTest::Deferred) return false;
        captureButtons(client, request, out);
        return true;
    });
    mInventoryCapture.advance([&](auto const& request, auto& out) {
        if (request.test == CaptureTest::Deferred) return false;
        captureInventory(player, request, out);
        return true;
    });
    mBlockCapture.advance([&](auto const& request, auto& out) {
        if (request.test == CaptureTest::Deferred) return false;
        captureBlocks(player, request, out);
        return true;
    });
    mScanCapture.advance([&](auto const& request, auto& out) {
        return request.test != CaptureTest::Deferred && captureScan(player, request, out);
    });
}

bool ProbeMod::allowTurnDelta() const {
    if (!mRunning.load(std::memory_order_relaxed) || !mBackgroundGuard || !hooksAllowed()) {
        return true;
    }
    if (mInjectingLook.load(std::memory_order_relaxed)) {
        return true;
    }
    std::uint64_t const until = mInjectTurnUntilTick.load(std::memory_order_relaxed);
    if (until != 0 && mTick.load(std::memory_order_relaxed) <= until) {
        return true;
    }
    // Scripts own the camera while the hardware input lock is on.
    if (mHardwareInputLocked.load(std::memory_order_relaxed)) {
        return false;
    }
    return detail::isGameForeground();
}

bool ProbeMod::applyTurnDelta(LocalPlayer& player, Vec2 const& delta) {
    InjectionCountScope injecting(mInjectingLook);
    return guardedControl(ControlOp::Turn, [&] { player._applyTurnDelta(delta); return true; });
}

void ProbeMod::updateFocusState() {
    bool const foreground = detail::isGameForeground();
    if (foreground == mForeground) {
        if (!foreground) {
            // The game keeps calling ClipCursor even in the background.
            ClipCursor(nullptr);
        }
        return;
    }

    if (!foreground) {
        ClipCursor(nullptr);
        if (mBackgroundGuard) {
            int calls = 0;
            while (calls < 8) {
                ++calls;
                if (ShowCursor(TRUE) >= 0) {
                    break;
                }
            }
            mCursorShowCalls += calls;
        }
        bootLog("focus: lost");
    } else {
        if (mCursorShowCalls > 0) {
            for (int i = 0; i < mCursorShowCalls; ++i) {
                ShowCursor(FALSE);
            }
            mCursorShowCalls = 0;
        }
        bootLog("focus: gained");
    }
    mForeground = foreground;
}


void ProbeMod::installCrashHandler() {
    // LeviLamina or the game may replace the top-level filter after mod load,
    // so the authoritative hook is the vectored handler registered by
    // watchInit(). This filter stays as a second chance.
    SetUnhandledExceptionFilter(&ProbeMod::crashFilter);
}

void ProbeMod::handleFatalException(EXCEPTION_POINTERS* info) {
    if (mCrashDumpInProgress.exchange(true, std::memory_order_acq_rel)) {
        return;
    }
    writeCrashArtifacts(info);
}

LONG WINAPI ProbeMod::crashFilter(EXCEPTION_POINTERS* info) {
    getInstance().writeCrashArtifacts(info);
    // Let Windows Error Reporting / the previous filter continue as usual.
    return EXCEPTION_CONTINUE_SEARCH;
}

void ProbeMod::writeCrashArtifacts(EXCEPTION_POINTERS* info) noexcept {
    // Crash context: no allocation, no mutex, no C++ streams. Values are copied
    // from atomics and the fixed last-command buffer.
    DWORD const       pid  = GetCurrentProcessId();
    DWORD const       tid  = GetCurrentThreadId();
    std::uint64_t     tick = mTick.load(std::memory_order_relaxed);

    wchar_t dumpPath[1200]{};
    std::swprintf(
        dumpPath,
        sizeof(dumpPath) / sizeof(dumpPath[0]),
        L"%s\\crash-%lu-%llu.dmp",
        mModDirW,
        static_cast<unsigned long>(pid),
        static_cast<unsigned long long>(tick)
    );
    HANDLE file = CreateFileW(dumpPath, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file != INVALID_HANDLE_VALUE) {
        MINIDUMP_EXCEPTION_INFORMATION mei{};
        mei.ThreadId          = tid;
        mei.ExceptionPointers = info;
        mei.ClientPointers    = FALSE;
        MiniDumpWriteDump(
            GetCurrentProcess(),
            pid,
            file,
            static_cast<MINIDUMP_TYPE>(MiniDumpNormal | MiniDumpWithThreadInfo),
            info != nullptr ? &mei : nullptr,
            nullptr,
            nullptr
        );
        CloseHandle(file);
    }

    char command[512]{};
    for (std::size_t i = 0; i < sizeof(command) - 1 && mLastCommand[i] != '\0'; ++i) {
        command[i] = mLastCommand[i];
    }

    wchar_t textPath[1200]{};
    std::swprintf(
        textPath,
        sizeof(textPath) / sizeof(textPath[0]),
        L"%s\\crash-%lu.txt",
        mModDirW,
        static_cast<unsigned long>(pid)
    );
    PoseSnapshot pose;
    bool const   havePose = mPose.latest(pose);
    char text[2048]{};
    std::snprintf(
        text,
        sizeof(text),
        "pid=%lu\n"
        "tick=%llu\n"
        "exception=0x%08lX\n"
        "address=%p\n"
        "thread=%lu\n"
        "last_command=%s\n"
        "state=(%.3f, %.3f, %.3f) yaw=%.2f\n"
        "pose_valid=%d pose_tick=%llu pose_epoch=%llu\n"
        "queue_dropped=%llu\n"
        "spool=%ls\n"
        "dump=%ls\n",
        static_cast<unsigned long>(pid),
        static_cast<unsigned long long>(tick),
        info != nullptr && info->ExceptionRecord != nullptr ? info->ExceptionRecord->ExceptionCode : 0UL,
        info != nullptr && info->ExceptionRecord != nullptr ? info->ExceptionRecord->ExceptionAddress : nullptr,
        static_cast<unsigned long>(tid),
        command,
        static_cast<double>(pose.x),
        static_cast<double>(pose.y),
        static_cast<double>(pose.z),
        static_cast<double>(pose.yaw),
        havePose ? 1 : 0,
        static_cast<unsigned long long>(pose.tick),
        static_cast<unsigned long long>(pose.epoch),
        static_cast<unsigned long long>(mQueueDropped.load(std::memory_order_relaxed)),
        mSpoolPath.c_str(),
        dumpPath
    );

    HANDLE report = CreateFileW(textPath, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (report != INVALID_HANDLE_VALUE) {
        DWORD written = 0;
        WriteFile(report, text, static_cast<DWORD>(std::strlen(text)), &written, nullptr);
        CloseHandle(report);
    }
}

} // namespace probe

LL_TYPE_INSTANCE_HOOK(
    ProbeLocalPlayerTickHook,
    ll::memory::HookPriority::Normal,
    LocalPlayer,
    &LocalPlayer::$normalTick,
    void
) {
    ::probe::ProbeMod::getInstance().onPlayerTick(*this);
    origin();
    ::probe::ProbeMod::getInstance().afterPlayerTick(*this);
};

static ll::memory::HookRegistrar<ProbeLocalPlayerTickHook> gPlayerTickHook;

// Background raw mouse input still reaches the game; without this guard the
// camera keeps turning while another window is focused. Our own look/task calls
// set mInjectingLook and pass through.
LL_TYPE_INSTANCE_HOOK(
    ProbeTurnDeltaHook,
    ll::memory::HookPriority::Normal,
    LocalPlayer,
    &LocalPlayer::_applyTurnDelta,
    void,
    Vec2 const& delta
) {
    auto& mod = ::probe::ProbeMod::getInstance();
    if (!mod.allowTurnDelta()) {
        return;
    }
    origin(delta);
};

static ll::memory::HookRegistrar<ProbeTurnDeltaHook> gTurnDeltaHook;


LL_REGISTER_MOD(probe::ProbeMod, probe::ProbeMod::getInstance());
