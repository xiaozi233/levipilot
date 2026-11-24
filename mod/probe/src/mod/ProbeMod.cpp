#include "mod/Internal.h"

#include "ll/api/command/CommandHandle.h"
#include "ll/api/command/CommandRegistrar.h"
#include "ll/api/command/Overload.h"
#include "ll/api/event/command/ServerCommandRegisterEvent.h"
#include "mc/client/game/MinecraftGame.h"
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
    // Deliberately the first thing load() does: on the hot-reload path the
    // absence of this line separates "the loader never called ll_mod_load" from
    // "our load path started and then blocked".
    bootLog("load: enter");
    getSelf().getLogger().info("harness: loading");
    bootLog("load: done");
    return true;
}

bool ProbeMod::enable() {
    bootLog("enable: enter tid=" + std::to_string(GetCurrentThreadId()));
    gCallbacks.reopen();
    mQuiescing.store(false, std::memory_order_release);
    if (mRunning.exchange(true)) {
        bootLog("enable: already running");
        return true;
    }
    auto const previous   = readLifecycleCounters(getSelf().getModDir() / "harness-boot.log");
    auto const generation = previous.generation + 1;
    mLifecycleGeneration.store(generation);
    mDisableSerial.store(previous.disable);
    bootLog("enable: begin lifecycle=" + std::to_string(generation));
    mForeground.store(detail::isGameForeground(), std::memory_order_relaxed);

    auto cleanupOnError = [&](char const* message) {
        bootLog(message);
        mRunning.store(false, std::memory_order_release);
        wakeSpool();
        if (mServerThread.joinable()) mServerThread.join();
        if (mSpoolThread.joinable()) mSpoolThread.join();
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
        mCommandRegistry = nullptr;
        mWorld.stop();
        watchShutdown();
        stopLifecycleHooks();
        if (mSpoolWakeEvent) {
            CloseHandle(mSpoolWakeEvent);
            mSpoolWakeEvent = nullptr;
        }
    };

    try {
        if (!mPacketRequests.templates.initialize()) bootLog("packet templates: allocation failed");
        mChatCapture.reset();
        resetQueries();
        hookResetCalls();
        bool const keyOk    = setLifecycleKeyHook(true);
        bool const mouseOk  = setLifecycleMouseHook(true);
        bool const playerOk = setLifecyclePlayerHook(true);
        bool const turnOk   = setLifecycleTurnHook(true);
        bool const cursorOk = setLifecycleCursorHook(true);
        bool const focusOk  = setLifecycleFocusHook(true);
        if (!(keyOk && mouseOk && playerOk && turnOk && cursorOk && focusOk)) {
            cleanupOnError("enable: hook installation failed");
            return false;
        }
        bootLog("enable: hooks installed");
        mPoseEpoch.fetch_add(1);

        mMouseListener =
            ll::event::EventBus::getInstance().emplaceListener<ll::event::input::MouseInputEvent>([this](auto& event) {
                CallbackScope callback(gCallbacks, CallbackDomain::Event);
                // MouseAction: ActionMove=0, ActionLeft=1, ActionRight=2, DataDown=1.
                int const action = static_cast<int>(event.actionButtonId());
                int const data   = static_cast<int>(event.buttonData());
                mInputSession.mouseEventCount.fetch_add(1, std::memory_order_relaxed);
                mInputSession.mouseLastAction = action;
                mInputSession.mouseLastData   = data;
                if (action >= 0 && action < static_cast<int>(mInputSession.mouseActionCounts.size())) {
                    mInputSession.mouseActionCounts[static_cast<size_t>(action)].fetch_add(
                        1,
                        std::memory_order_relaxed
                    );
                }

                if (action == 1) {
                    mInputSession.mouseLeft = data != 0;
                    if (data != 0) {
                        mInputSession.mouseLeftLatch = true;
                    }
                } else if (action == 2) {
                    mInputSession.mouseRight = data != 0;
                    if (data != 0) {
                        mInputSession.mouseRightLatch = true;
                    }
                }
            });

        mKeyListener =
            ll::event::EventBus::getInstance().emplaceListener<ll::event::input::KeyInputEvent>([this](auto& event) {
                CallbackScope callback(gCallbacks, CallbackDomain::Event);
                mInputSession.keyEventCount.fetch_add(1, std::memory_order_relaxed);
                mInputSession.keyLastCode.store(event.keyCode(), std::memory_order_relaxed);
                mInputSession.keyLastAction.store(static_cast<std::int32_t>(event.action()), std::memory_order_relaxed);
                if (mInputSession.injecting.load(std::memory_order_relaxed)) {
                    mInputSession.keyInjectedSeen.fetch_add(1, std::memory_order_relaxed);
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
        mSpoolPath = modDirPath / ("spool-" + std::to_string(GetCurrentProcessId()) + ".jsonl");
        // F6: a reused PID must not append this session to an old spool file.
        {
            std::error_code spoolEc;
            if (std::filesystem::exists(mSpoolPath, spoolEc) && std::filesystem::file_size(mSpoolPath, spoolEc) > 0) {
                auto const pid = std::to_string(GetCurrentProcessId());
                for (int n = 1; n < 1000; ++n) {
                    auto candidate = modDirPath / ("spool-" + pid + "-" + std::to_string(n) + ".jsonl");
                    if (!std::filesystem::exists(candidate, spoolEc)) {
                        std::filesystem::rename(mSpoolPath, candidate, spoolEc);
                        break;
                    }
                }
            }
        }
        // VEH owns crash diagnostics and is explicitly removed by watchShutdown().
        // Do not install a process-global filter that could outlive this DLL.
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
                [this](auto& event) {
                    CallbackScope callback(gCallbacks, CallbackDomain::Event);
                    if (!callback.admitted()) return;
                    if (!guardedSdkCall([&] { registerCommands(event.commandRegistry()); }))
                        bootLog("command: registration event failed");
                }
            );
        // A reload inside an existing world does not fire the registration event
        // again. Rebind our overload on the server thread after LL disabled the old
        // DLL's factory. At the main menu there is no server; the event handles the
        // first world normally.
        queueServerTask(
            [this] {
                if (!mRunning.load(std::memory_order_acquire)) return;
                if (auto registry = ll::service::getCommandRegistry(false)) registerCommands(*registry);
            },
            [](char const*) {}
        );
        loadCheckpoints();

        if (!mSpoolWakeEvent) mSpoolWakeEvent = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        mSpoolThread = std::thread([this] { spoolThreadLoop(); });

        mServerThread = std::thread([this] { serverLoop(); });
        getSelf().getLogger().info("harness: enabled, pipe=\\\\.\\pipe\\mcbe-harness");
        bootLog("enable: done lifecycle=" + std::to_string(generation));
        return true;
    } catch (std::exception const& e) {
        cleanupOnError((std::string("enable: exception: ") + e.what()).c_str());
        return false;
    } catch (...) {
        cleanupOnError("enable: unknown exception");
        return false;
    }
}

bool ProbeMod::stopLifecycleHooks(bool suspendThreads) {
    // No short-circuit: one failure must not leave the other hooks installed.
    bool const keyOk    = setLifecycleKeyHook(false, suspendThreads);
    bool const mouseOk  = setLifecycleMouseHook(false, suspendThreads);
    bool const playerOk = setLifecyclePlayerHook(false, suspendThreads);
    bool const turnOk   = setLifecycleTurnHook(false, suspendThreads);
    bool const cursorOk = setLifecycleCursorHook(false, suspendThreads);
    bool const focusOk  = setLifecycleFocusHook(false, suspendThreads);
    return keyOk && mouseOk && playerOk && turnOk && cursorOk && focusOk;
}

bool ProbeMod::disable() {
    std::unique_lock lifecycle(mDisableMutex, std::try_to_lock);
    if (!lifecycle.owns_lock()) return false;
    if (!mRunning.load()) return gCallbacks.closed() && gCallbacks.idle();
    // No state is destroyed until every admitted callback has retired. A false
    // return leaves LL's mod enabled; drainCallbacks restores all source state.
    if (!drainCallbacks()) return false;
    if (mCallbackTestThread.joinable()) mCallbackTestThread.join();
    mRunning.store(false);
    wakeSpool();

    auto const generation = mLifecycleGeneration.load();
    auto const disableId  = mDisableSerial.fetch_add(1) + 1;
    bootLog("disable: begin lifecycle=" + std::to_string(generation) + " disable_id=" + std::to_string(disableId));
    stopLegacy(StreamEnd::Disabled, false);
    bootLog("disable: streams stopped");
    cancelTasks("disabled");
    mChatCapture.cancel("disabled");
    cancelQueries("disabled");
    hookCancelCalls("disabled");
    recordCancel(RecordEnd::Disabled);
    mPoseEpoch.fetch_add(1);
    mMacroSession.stopAll(MacroEnd::Disabled, MacroRecordEnd::Disabled);
    {
        std::lock_guard lock(mMacroSession.mutex);
        drainMacroRecordingLocked();
    }
    bootLog("disable: work cancelled");
    // Real input is unlocked below. Held injected edges still require the game
    // tick for release; stopping the session does not prove they were released.
    packetCaptureStop();
    // Never leave the game with real input dropped after an unload.
    mInputSession.hardwareLocked = false;

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
    mCommandRegistry = nullptr;
    mWorld.stop();

    // The channel observes mRunning while waiting for connect/read/write and
    // cancels/drains its own operation before leaving the worker.
    if (mServerThread.joinable()) {
        mServerThread.join();
    }
    bootLog("disable: server thread joined");
    bool const violationStopped = !violationWatchStop().value("active", true);
    recordStop();
    if (mSpoolThread.joinable()) {
        mSpoolThread.join();
    }
    bootLog("disable: spool thread joined");
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
    bool const hooksStopped = stopLifecycleHooks();
    if (mSpoolWakeEvent) {
        CloseHandle(mSpoolWakeEvent);
        mSpoolWakeEvent = nullptr;
    }
    bootLog("disable: hooks stopped ok=" + std::to_string(hooksStopped ? 1 : 0));

    {
        std::lock_guard lock(mQueueMutex);
        mQueue.clear();
        mQueueBytes = 0;
    }
    getSelf().getLogger().info("harness: disabled");
    bootLog(
        "disable: done lifecycle=" + std::to_string(generation) + " disable_id=" + std::to_string(disableId)
        + " ok=" + (violationStopped && hooksStopped ? "1" : "0")
    );
    return violationStopped && hooksStopped;
}

bool ProbeMod::unload() {
    // Breadcrumbs for the hot-reload acceptance: the process hung once after
    // "disable: done" with probe.dll still mapped. "returning" is written on the
    // last statement, so a missing line means the block is still inside.
    bootLog("unload: begin tid=" + std::to_string(GetCurrentThreadId()));
    bool const stopped = disable();
    bootLog("unload: disable returned " + std::to_string(stopped ? 1 : 0));
    bootLog("unload: returning " + std::to_string(stopped ? 1 : 0));
    return stopped;
}

void ProbeMod::loadAutoConfig() {
    try {
        auto          path = getSelf().getModDir() / "harness.json";
        std::ifstream in(path);
        if (!in) {
            return;
        }
        auto config = nlohmann::json::parse(in, nullptr, false);
        if (config.is_discarded() || !config.is_object()) {
            bootLog("config: invalid json");
            return;
        }
        auto readBool = [&](char const* key, bool fallback) {
            bool       invalid = false;
            bool const val     = safeJsonBool(config, key, fallback, &invalid);
            if (invalid) bootLog(std::string("config: bad field=") + key);
            return val;
        };
        auto readInt = [&](char const* key, int fallback) {
            bool      invalid = false;
            int const val     = safeJsonInt(config, key, fallback, &invalid);
            if (invalid) bootLog(std::string("config: bad field=") + key);
            return val;
        };
        auto readString = [&](char const* key, std::string const& fallback) {
            bool        invalid = false;
            std::string val     = safeJsonString(config, key, fallback, &invalid);
            if (invalid) bootLog(std::string("config: bad field=") + key);
            return val;
        };
        auto const  autoWorld   = readString("auto_enter", "");
        auto const  autoDelayMs = std::clamp(readInt("delay_ms", 2000), 0, 60000);
        std::string autoOptions;
        mBackgroundGuard          = readBool("background_guard", true);
        mSuppressCursorGrab       = readBool("suppress_cursor_grab", false);
        mSuppressFocusPause       = readBool("suppress_focus_pause", false);
        mAllowMemoryRead          = readBool("allow_mem_read", true);
        mAllowMemoryWrite         = readBool("allow_write", false);
        mAllowToggleFly           = readBool("allow_togglefly", true);
        mAllowCrashTest           = readBool("allow_crash_test", false);
        mAllowHookVa              = readBool("allow_hook_va", false);
        mAllowSelftestStress      = readBool("allow_selftest_stress", false);
        mAllowWatch               = readBool("allow_watch", true);
        mAllowPacketCapture       = readBool("allow_packet_capture", false);
        mAllowPacketInject        = readBool("allow_packet_inject", false);
        mInputSession.allowInject = readBool("allow_input_inject", false);
        // Armed by serverLoop() when a client connects, not here: a lock held from
        // startup (previous script run, stale harness.json) makes the main menu
        // unclickable for the person at the keyboard.
        mInputSession.lockOnConnect  = readBool("lock_input", false);
        mInputSession.hardwareLocked = false;
        mAllowVersionMismatch        = readBool("allow_version_mismatch", false);
        setAllowVersionMismatch(mAllowVersionMismatch.load());
        mPipeToken = readString("pipe_token", "");
        mPipeAuthed.store(mPipeToken.empty(), std::memory_order_release);
        mWorld.setSignInWaitMs(std::clamp(readInt("signin_wait_ms", 15000), 0, 120000));
        if (config.contains("options")) {
            if (config["options"].is_object()) {
                for (auto it = config["options"].begin(); it != config["options"].end(); ++it) {
                    std::string value  = it.value().is_string() ? it.value().get<std::string>() : it.value().dump();
                    autoOptions       += it.key() + ":" + value + "\n";
                }
            } else {
                bootLog("config: bad field=options");
            }
        }
        if (autoWorld.empty() && autoOptions.empty()) {
            return;
        }
        bootLog("config: auto_enter=" + autoWorld + " delay=" + std::to_string(autoDelayMs));
        mWorld.autoEnter(autoWorld, autoOptions, autoDelayMs);
    } catch (std::exception const& e) {
        bootLog(std::string("config: exception: ") + e.what());
    } catch (...) {
        bootLog("config: unknown exception");
    }
}

void ProbeMod::onPlayerTick(LocalPlayer& player) {
    mTickStartQpc                  = qpcNow();
    mInputSession.lifecycleCurrent = false;
    // A bug in our own code must not take the game down with it.
    try {
        safeTickPre(player);
        if (!mTickRecovery.active()) injectTickError(TickPhase::Pre, false);
    } catch (std::exception const& e) {
        reportInternalError(TickPhase::Pre, e.what());
    } catch (...) {
        reportInternalError(TickPhase::Pre, "unknown exception");
    }
    mInputSession.publishLifecycle(mPoseEpoch.load());
    mPreUs = qpcElapsedUs(mTickStartQpc, qpcNow());
}

void ProbeMod::safeTickPre(LocalPlayer& player) {
    if (!mRunning.load(std::memory_order_relaxed) || !hooksAllowed()) {
        return;
    }
    mInputSession.jumpEdgeThisTick = false;
    ClientInstance* client         = nullptr;
    bool            current        = false;
    if (!guardedGameRead([&] {
            injectTickError(TickPhase::Pre, true);
            client  = ll::service::getClientInstance();
            current = client && client->getLocalPlayer() == &player;
        })) {
        reportInternalError(TickPhase::Pre, "client_player_read_fault");
        return;
    }
    if (!current) {
        releaseRealInput();
        mWorld.observePlayer(false);
        return;
    }
    mWorld.observePlayer(true);
    mInputSession.lifecycleCurrent = true;
    mLastTickMs                    = GetTickCount64();
    if (recoverTickControls(player)) return;

    capturePlayerIdentity(player);

    latchActionClicks(player);
    // G1/G2: drain queued event-level input into the game's own queues before
    // anything else in this tick touches movement.
    tickInputInjection();

    // T9: one lock-free snapshot of movement/look requests per tick.
    RequestBlock const requests = readRequests();

    if (mInputSession.resetInputButtons.exchange(false, std::memory_order_acq_rel)) {
        mInputSession.resetButtons();
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

    if (mMacroSession.playback.active()) {
        tickMacroPlayback(*client, player);
        return;
    }

    consumeMovementRequests(*client, player, requests);
    consumeLookRequests(player, requests);
}

bool ProbeMod::registerCommands(CommandRegistry& registry, CaptureTest test) {
    // A real server command players can type in chat: /togglefly grants or
    // removes the MayFly ability (the double-space flight gate) in any mode
    // except spectator. NotCheat keeps it usable without enabling cheats.
    // Server-side only: the ability is server-authoritative, and a client-side
    // command would flip the client copy that the next sync overwrites.
    if (test == CaptureTest::None && mCommandRegistry == &registry) return true;
    bool const registered = guardedSdkCall(
        [&] {
            auto& handle = ll::command::CommandRegistrar::getServerInstance().getOrCreateCommand(
                "togglefly",
                "grant/remove double-space flight (non-spectator)",
                CommandPermissionLevel::Any,
                CommandFlagValue::NotCheat
            );
            handle.overload().execute([this](CommandOrigin const& origin, CommandOutput& output) {
                CallbackScope callback(gCallbacks, CallbackDomain::Command);
                bool const    ok = guardedSdkCall([&] {
                    if (!callback.admitted()) {
                        output.error("probe is quiescing");
                        return;
                    }
                    if (!mAllowToggleFly.load(std::memory_order_relaxed)) {
                        output.error("togglefly: disabled by allow_togglefly=false in harness.json");
                        return;
                    }
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
                    output.success(target ? "双击空格切换飞行：已开启" : "双击空格切换飞行：已关闭");
                });
                if (!ok) {
                    bootLog("command: togglefly execution failed");
                    // Output is another SDK call, with its own bounded guard.
                    guardedSdkCall([&] { output.error("togglefly: internal error"); });
                }
            });
            mCommandRegistry = &registry;
            bootLog("command: registered togglefly");
        },
        test
    );
    if (!registered) bootLog("command: togglefly registration failed");
    return registered;
}

void ProbeMod::consumeMovementRequests(ClientInstance& client, LocalPlayer& player, RequestBlock const& requests) {
    ControlState state;
    bool         apply = false;
    if (requests.inputSeq != mInputSession.seenInputSeq) {
        mInputSession.seenInputSeq     = requests.inputSeq;
        mInputSession.activeInput      = requests.input;
        mInputSession.activeInputTicks = requests.inputTicks;
    }
    if (requests.jumpSeq != mInputSession.seenJumpSeq) {
        mInputSession.seenJumpSeq        = requests.jumpSeq;
        mInputSession.jumpRequestPending = !requests.jumpCancel;
    }
    if (mInputSession.activeInputTicks != 0) {
        state = mInputSession.activeInput;
        apply = state.active || state.jump;
        if (mInputSession.activeInputTicks > 0) {
            --mInputSession.activeInputTicks;
            if (mInputSession.activeInputTicks == 0) {
                mInputSession.activeInput = ControlState{};
            }
        }
    }
    if (mInputSession.jumpRequestPending) {
        mInputSession.jumpRequestPending = false;
        state.jump                       = true;
        state.active                     = true;
        apply                            = true;
    }
    if (!applyControls(client, player, apply ? state : ControlState{})) {
        // This sequence is consumed. Do not silently replay an unbounded
        // desired state after an uncertain edge; a new request may restart it.
        mInputSession.activeInput        = ControlState{};
        mInputSession.activeInputTicks   = 0;
        mInputSession.jumpRequestPending = false;
    }
}

void ProbeMod::consumeLookRequests(LocalPlayer& player, RequestBlock const& requests) {
    // Absolute look targets are applied after movement so the rotation matches the current tick.
    if (requests.lookSeq != mInputSession.seenLookSeq) {
        mInputSession.seenLookSeq        = requests.lookSeq;
        mInputSession.activeLookRelative = requests.lookRelative;
        mInputSession.activeLookYaw      = requests.lookYaw;
        mInputSession.activeLookPitch    = requests.lookPitch;
        mInputSession.activeLookTicks    = requests.lookTicks;
    }
    bool const lookActive = mInputSession.activeLookTicks != 0;
    if (lookActive) {
        Vec2 rot;
        if (!guardedControl(ControlOp::LookRead, [&] {
                rot = player.getRotation();
                return true;
            })) {
            mInputSession.activeLookTicks = 0;
            return;
        }
        // _applyTurnDelta uses the opposite pitch sign from ActorRotation.x.
        float dy = mInputSession.activeLookRelative ? mInputSession.activeLookYaw
                                                    : wrapDegrees(mInputSession.activeLookYaw - rot.y);
        float dp =
            mInputSession.activeLookRelative ? -mInputSession.activeLookPitch : (rot.x - mInputSession.activeLookPitch);
        if (mInputSession.activeLookTicks > 0) {
            --mInputSession.activeLookTicks;
        }
        if (std::fabs(dy) > 1.0e-5f || std::fabs(dp) > 1.0e-5f) {
            if (!applyTurnDelta(player, Vec2{dp, dy})) mInputSession.activeLookTicks = 0;
        }
    }
}

void ProbeMod::afterPlayerTick(LocalPlayer& player) {
    std::uint64_t const postStart  = qpcNow();
    mInputSession.lifecycleCurrent = false;
    try {
        safeTickPost(player);
        if (!mTickRecovery.active()) injectTickError(TickPhase::Post, false);
    } catch (std::exception const& e) {
        reportInternalError(TickPhase::Post, e.what());
    } catch (...) {
        reportInternalError(TickPhase::Post, "unknown exception");
    }
    mInputSession.publishLifecycle(mPoseEpoch.load());
    std::uint64_t const postEnd = qpcNow();
    std::uint64_t const hookUs  = mPreUs + qpcElapsedUs(postStart, postEnd);
    std::uint64_t const tickUs  = mPrevTickStartQpc != 0 ? qpcElapsedUs(mPrevTickStartQpc, mTickStartQpc) : 0;
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
    ClientInstance* client  = nullptr;
    bool            current = false;
    if (!guardedGameRead([&] {
            injectTickError(TickPhase::Post, true);
            client  = ll::service::getClientInstance();
            current = client && client->getLocalPlayer() == &player;
        })) {
        reportInternalError(TickPhase::Post, "client_player_read_fault");
        return;
    }
    if (!current) return;
    mInputSession.lifecycleCurrent = true;
    updateFocusState();
    overheadBusyTick();

    // T1/T3: the tick thread is the default target for hardware watchpoints,
    // and the pose below is what the crash report captures.
    mTickThreadId.store(GetCurrentThreadId(), std::memory_order_relaxed);
    gTickThreadId.store(GetCurrentThreadId(), std::memory_order_relaxed);
    mTickProbe.fetch_add(1, std::memory_order_relaxed);
    auto tickIndex = mTick.fetch_add(1, std::memory_order_relaxed) + 1;
    gWatchTick.store(tickIndex, std::memory_order_relaxed);
    recordTick(player, *client, tickIndex, mInputSession.jumpEdgeThisTick);
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
        if (mSlotCapture.advance([&](auto const& request, auto& out) {
                if (request.test == CaptureTest::Deferred) return false;
                applySlotRequest(player, request, out);
                return true;
            })) {
            wakeSpool();
        }
    }

    runCaptureRequests(*client, player);

    if (detail::hookTick(player)) wakeSpool();

    if (mMacroSession.recording.active()) {
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
    bool notify  = false;
    notify      |= mStateCapture.advance([&](auto const& request, auto& out) {
        if (request.test == CaptureTest::Deferred) return false;
        captureState(client, player, request, out);
        return true;
    });
    notify      |= mButtonCapture.advance([&](auto const& request, auto& out) {
        if (request.test == CaptureTest::Deferred) return false;
        captureButtons(client, request, out);
        return true;
    });
    notify      |= mInventoryCapture.advance([&](auto const& request, auto& out) {
        if (request.test == CaptureTest::Deferred) return false;
        captureInventory(player, request, out);
        return true;
    });
    notify      |= mBlockCapture.advance([&](auto const& request, auto& out) {
        if (request.test == CaptureTest::Deferred) return false;
        captureBlocks(player, request, out);
        return true;
    });
    notify      |= mScanCapture.advance([&](auto const& request, auto& out) {
        return request.test != CaptureTest::Deferred && captureScan(player, request, out);
    });
    if (notify) wakeSpool();
}

bool ProbeMod::allowTurnDelta() const {
    if (!mRunning.load(std::memory_order_relaxed) || !mBackgroundGuard || !hooksAllowed()) {
        return true;
    }
    if (mInputSession.injectingLook.load(std::memory_order_relaxed)) {
        return true;
    }
    std::uint64_t const until = mInputSession.injectTurnUntilTick.load(std::memory_order_relaxed);
    if (until != 0 && mTick.load(std::memory_order_relaxed) <= until) {
        return true;
    }
    // Scripts own the camera while the hardware input lock is on.
    if (mInputSession.hardwareLocked.load(std::memory_order_relaxed)) {
        return false;
    }
    return mForeground.load(std::memory_order_relaxed);
}

bool ProbeMod::allowCursorClip() const {
    if (!mRunning.load(std::memory_order_relaxed) || !hooksAllowed()) {
        return true;
    }
    if (mSuppressCursorGrab.load(std::memory_order_relaxed)
        || mInputSession.hardwareLocked.load(std::memory_order_relaxed)) {
        return false;
    }
    if (mBackgroundGuard.load(std::memory_order_relaxed) && !detail::isGameForeground()) {
        return false;
    }
    return true;
}

bool ProbeMod::allowFocusLost() const {
    if (!mRunning.load(std::memory_order_relaxed) || !hooksAllowed()) {
        return true;
    }
    return !mSuppressFocusPause.load(std::memory_order_relaxed);
}

bool ProbeMod::applyTurnDelta(LocalPlayer& player, Vec2 const& delta) {
    InjectionCountScope injecting(mInputSession.injectingLook);
    return guardedControl(ControlOp::Turn, [&] {
        player._applyTurnDelta(delta);
        return true;
    });
}

void ProbeMod::updateFocusState() {
    bool const foreground   = detail::isGameForeground();
    bool const suppressGrab = mSuppressCursorGrab.load(std::memory_order_relaxed);
    if (!foreground || suppressGrab) {
        ClipCursor(nullptr);
    }
    bool const prevForeground = mForeground.load(std::memory_order_relaxed);
    if (foreground == prevForeground) {
        if (suppressGrab && mCursorShowCalls == 0) {
            int calls = 0;
            while (calls < 8) {
                ++calls;
                if (ShowCursor(TRUE) >= 0) {
                    break;
                }
            }
            mCursorShowCalls += calls;
        }
        return;
    }

    if (!foreground) {
        ClipCursor(nullptr);
        if (mBackgroundGuard.load(std::memory_order_relaxed) || suppressGrab) {
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
        if (!suppressGrab && mCursorShowCalls > 0) {
            for (int i = 0; i < mCursorShowCalls; ++i) {
                ShowCursor(FALSE);
            }
            mCursorShowCalls = 0;
        }
        bootLog("focus: gained");
    }
    mForeground.store(foreground, std::memory_order_relaxed);
}


void ProbeMod::handleFatalException(EXCEPTION_POINTERS* info) {
    if (mCrashDumpInProgress.exchange(true, std::memory_order_acq_rel)) {
        return;
    }
    writeCrashArtifacts(info);
}

void ProbeMod::writeCrashArtifacts(EXCEPTION_POINTERS* info) noexcept {
    // Crash context: no allocation, no mutex, no C++ streams. Values are copied
    // from atomics and the fixed last-command buffer.
    DWORD const   pid  = GetCurrentProcessId();
    DWORD const   tid  = GetCurrentThreadId();
    std::uint64_t tick = mTick.load(std::memory_order_relaxed);

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

    unsigned const slot = mLastCommandSlot.load(std::memory_order_acquire) & 1u;
    char           command[512]{};
    for (std::size_t i = 0; i < sizeof(command) - 1 && mLastCommand[slot][i] != '\0'; ++i) {
        command[i] = mLastCommand[slot][i];
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
    char         text[2048]{};
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
    ::probe::detail::CallbackScope callback(::probe::detail::gCallbacks, ::probe::detail::CallbackDomain::Tick);
    if (!callback.admitted()) {
        origin();
        return;
    }
    ::probe::ProbeMod::getInstance().callbackTestDelay(::probe::detail::CallbackDomain::Tick);
    ::probe::ProbeMod::getInstance().onPlayerTick(*this);
    origin();
    ::probe::ProbeMod::getInstance().afterPlayerTick(*this);
};

namespace probe {

bool ProbeMod::setLifecyclePlayerHook(bool on, bool suspendThreads) {
    if (on) {
        if (mLifecyclePlayerHooked.load(std::memory_order_acquire)) return true;
        int const status = ProbeLocalPlayerTickHook::hook(suspendThreads);
        if (status != 0) {
            bootLog("lifecycle: player hook restore failed status=" + std::to_string(status));
            return false;
        }
        mLifecyclePlayerHooked.store(true, std::memory_order_release);
        return true;
    }
    if (!mLifecyclePlayerHooked.exchange(false, std::memory_order_acq_rel)) return true;
    if (!ProbeLocalPlayerTickHook::unhook(suspendThreads)) {
        mLifecyclePlayerHooked.store(true, std::memory_order_release);
        bootLog("lifecycle: player hook unhook failed");
        return false;
    }
    return true;
}

} // namespace probe

// Background raw mouse input still reaches the game; without this guard the
// camera keeps turning while another window is focused. Our own look/task calls
// set mInputSession.injectingLook and pass through.
LL_TYPE_INSTANCE_HOOK(
    ProbeTurnDeltaHook,
    ll::memory::HookPriority::Normal,
    LocalPlayer,
    &LocalPlayer::_applyTurnDelta,
    void,
    Vec2 const& delta
) {
    ::probe::detail::CallbackScope callback(::probe::detail::gCallbacks, ::probe::detail::CallbackDomain::Turn);
    auto&                          mod = ::probe::ProbeMod::getInstance();
    if (!mod.allowTurnDelta()) {
        return;
    }
    origin(delta);
};

namespace probe {

bool ProbeMod::setLifecycleTurnHook(bool on, bool suspendThreads) {
    if (on) {
        if (mLifecycleTurnHooked.load(std::memory_order_acquire)) return true;
        int const status = ProbeTurnDeltaHook::hook(suspendThreads);
        if (status != 0) {
            bootLog("lifecycle: turn hook restore failed status=" + std::to_string(status));
            return false;
        }
        mLifecycleTurnHooked.store(true, std::memory_order_release);
        return true;
    }
    if (!mLifecycleTurnHooked.exchange(false, std::memory_order_acq_rel)) return true;
    if (!ProbeTurnDeltaHook::unhook(suspendThreads)) {
        mLifecycleTurnHooked.store(true, std::memory_order_release);
        bootLog("lifecycle: turn hook unhook failed");
        return false;
    }
    return true;
}

} // namespace probe

namespace {

ll::memory::FuncPtr gClipCursorTarget = nullptr;
ll::memory::FuncPtr gOrigClipCursor   = nullptr;
bool                gClipCursorHooked = false;
bool                gGrabMouseHooked  = false;

BOOL WINAPI detourClipCursor(RECT const* lpRect) {
    ::probe::detail::CallbackScope callback(::probe::detail::gCallbacks, ::probe::detail::CallbackDomain::Cursor);
    auto                           orig = reinterpret_cast<BOOL(WINAPI*)(RECT const*)>(gOrigClipCursor);
    if (lpRect != nullptr && callback.admitted() && !::probe::ProbeMod::getInstance().allowCursorClip()) {
        return TRUE;
    }
    return orig ? orig(lpRect) : TRUE;
}

} // namespace

LL_TYPE_INSTANCE_HOOK(
    ProbeGrabMouseHook,
    ll::memory::HookPriority::Normal,
    ClientInstance,
    &ClientInstance::$grabMouse,
    void
) {
    ::probe::detail::CallbackScope callback(::probe::detail::gCallbacks, ::probe::detail::CallbackDomain::Cursor);
    auto&                          mod = ::probe::ProbeMod::getInstance();
    if (callback.admitted() && !mod.allowCursorClip()) {
        return;
    }
    origin();
};

LL_TYPE_INSTANCE_HOOK(
    ProbeFocusLostHook,
    ll::memory::HookPriority::Normal,
    MinecraftGame,
    &MinecraftGame::$onAppFocusLost,
    void
) {
    ::probe::detail::CallbackScope callback(::probe::detail::gCallbacks, ::probe::detail::CallbackDomain::Focus);
    auto&                          mod = ::probe::ProbeMod::getInstance();
    if (callback.admitted() && !mod.allowFocusLost()) {
        return;
    }
    origin();
};

namespace probe {

bool ProbeMod::setLifecycleCursorHook(bool on, bool suspendThreads) {
    if (on) {
        if (mLifecycleCursorHooked.load(std::memory_order_acquire)) return true;
        if (!gClipCursorHooked) {
            if (!gClipCursorTarget) {
                if (HMODULE user32 = GetModuleHandleW(L"user32.dll")) {
                    gClipCursorTarget = reinterpret_cast<ll::memory::FuncPtr>(GetProcAddress(user32, "ClipCursor"));
                }
            }
            if (!gClipCursorTarget) {
                bootLog("lifecycle: cursor ClipCursor symbol unresolved");
                return false;
            }
            int const status = ll::memory::hook(
                gClipCursorTarget,
                reinterpret_cast<ll::memory::FuncPtr>(&detourClipCursor),
                &gOrigClipCursor,
                ll::memory::HookPriority::Normal,
                suspendThreads
            );
            if (status != 0) {
                bootLog("lifecycle: clip cursor hook failed status=" + std::to_string(status));
                return false;
            }
            gClipCursorHooked = true;
        }
        if (!gGrabMouseHooked) {
            int const status = ProbeGrabMouseHook::hook(suspendThreads);
            if (status != 0) {
                bootLog("lifecycle: grabMouse hook failed status=" + std::to_string(status));
                if (gClipCursorHooked) {
                    if (ll::memory::unhook(
                            gClipCursorTarget,
                            reinterpret_cast<ll::memory::FuncPtr>(&detourClipCursor),
                            suspendThreads
                        )) {
                        gClipCursorHooked = false;
                        gOrigClipCursor   = nullptr;
                    }
                }
                return false;
            }
            gGrabMouseHooked = true;
        }
        mLifecycleCursorHooked.store(true, std::memory_order_release);
        return true;
    }
    if (!mLifecycleCursorHooked.exchange(false, std::memory_order_acq_rel)) return true;
    bool ok = true;
    if (gGrabMouseHooked) {
        if (ProbeGrabMouseHook::unhook(suspendThreads)) {
            gGrabMouseHooked = false;
        } else {
            ok = false;
            bootLog("lifecycle: grabMouse hook unhook failed");
        }
    }
    if (gClipCursorHooked) {
        if (ll::memory::unhook(
                gClipCursorTarget,
                reinterpret_cast<ll::memory::FuncPtr>(&detourClipCursor),
                suspendThreads
            )) {
            gClipCursorHooked = false;
            gOrigClipCursor   = nullptr;
        } else {
            ok = false;
            bootLog("lifecycle: clip cursor hook unhook failed");
        }
    }
    if (!ok) {
        mLifecycleCursorHooked.store(true, std::memory_order_release);
        return false;
    }
    return true;
}

bool ProbeMod::setLifecycleFocusHook(bool on, bool suspendThreads) {
    if (on) {
        if (mLifecycleFocusHooked.load(std::memory_order_acquire)) return true;
        int const status = ProbeFocusLostHook::hook(suspendThreads);
        if (status != 0) {
            bootLog("lifecycle: focus hook restore failed status=" + std::to_string(status));
            return false;
        }
        mLifecycleFocusHooked.store(true, std::memory_order_release);
        return true;
    }
    if (!mLifecycleFocusHooked.exchange(false, std::memory_order_acq_rel)) return true;
    if (!ProbeFocusLostHook::unhook(suspendThreads)) {
        mLifecycleFocusHooked.store(true, std::memory_order_release);
        bootLog("lifecycle: focus hook unhook failed");
        return false;
    }
    return true;
}

} // namespace probe

LL_REGISTER_MOD(probe::ProbeMod, probe::ProbeMod::getInstance());
