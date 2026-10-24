#include "mod/Internal.h"

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

    cancelTasks("disabled");
    mMacroRecording = false;
    mMacroPlaying   = false;
    packetCaptureStop();

    if (mMouseListener) {
        ll::event::EventBus::getInstance().removeListener<ll::event::input::MouseInputEvent>(mMouseListener);
        mMouseListener.reset();
    }
    if (mKeyListener) {
        ll::event::EventBus::getInstance().removeListener<ll::event::input::KeyInputEvent>(mKeyListener);
        mKeyListener.reset();
    }
    mWorld.stop();

    // Wake a server thread blocked in ConnectNamedPipe, then let it exit.
    HANDLE wake = CreateFileW(PipeName, GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, 0, nullptr);
    if (wake != INVALID_HANDLE_VALUE) {
        CloseHandle(wake);
    }
    // F10: a client that connected but stopped reading would otherwise leave
    // the pipe thread blocked in WriteFile and hang unload.
    if (HANDLE thread = mServerThreadHandle.load(std::memory_order_acquire)) {
        CancelSynchronousIo(thread);
    }
    if (mServerThread.joinable()) {
        mServerThread.join();
    }
    if (HANDLE thread = mServerThreadHandle.exchange(nullptr, std::memory_order_acq_rel)) {
        CloseHandle(thread);
    }
    if (mSpoolThread.joinable()) {
        mSpoolThread.join();
    }
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
    mRecording = false;
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
    } catch (std::exception const& e) {
        reportInternalError("tick_pre", e.what());
    } catch (...) {
        reportInternalError("tick_pre", "unknown exception");
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
        mWorld.observePlayer(false);
        return;
    }
    mWorld.observePlayer(true);
    mLastTickMs = GetTickCount64();

    {
        auto const&     name = player.getRealName();
        std::lock_guard lock(mPlayerNameMutex);
        if (mLocalPlayerName != name) {
            mLocalPlayerName = name;
        }
    }

    latchActionClicks(player);
    // G1/G2: drain queued event-level input into the game's own queues before
    // anything else in this tick touches movement.
    tickInputInjection();
    tickPacketInject(player);

    // T9: one lock-free snapshot of movement/look/teleport requests per tick.
    RequestBlock const requests = readRequests();

    // Queued teleport goes through the integrated server so the position is
    // authoritative; a client-only _setPos is corrected back on the next tick.
    if (requests.teleportSeq != mSeenTeleportSeq) {
        mSeenTeleportSeq = requests.teleportSeq;
        requestServerTeleport(player, requests.teleport);
    }

    if (int fly = mFlyRequest.exchange(-1); fly >= 0) {
        bool const enabled = fly != 0;
        player.setAbility(AbilitiesIndex::MayFly, enabled);
        player.setAbility(AbilitiesIndex::Flying, enabled);
        // The client ability write alone is overwritten the next time the
        // server syncs abilities (for example after a /tp). Mirror it on the
        // server-side player so "fly off" actually sticks.
        if (auto server = ll::service::bedrock::getServerInstance()) {
            std::string name = player.getRealName();
            queueServerTask([name, enabled] {
                auto    minecraft    = ll::service::bedrock::getMinecraft();
                Level*  level        = minecraft ? minecraft->getLevel() : nullptr;
                Player* serverPlayer = (level != nullptr && !name.empty()) ? level->getPlayer(name) : nullptr;
                if (serverPlayer != nullptr) {
                    serverPlayer->setAbility(AbilitiesIndex::MayFly, enabled);
                    serverPlayer->setAbility(AbilitiesIndex::Flying, enabled);
                }
            });
        }
    }
    if (int click = mClickRequest.exchange(-1); click >= 0) {
        performClick(player, click);
    }

    if (mMacroStopRequested.exchange(false)) releaseMacroInputs();

    // Closed-loop tasks own the tick while they run.
    if (tickTask(*client, player)) {
        return;
    }

    if (mMacroStartRequested.exchange(false)) {
        {
            std::lock_guard lock(mMacroMutex);
            if (mMacro) {
                mMacroPlaying = true;
            }
        }
        if (mMacroPlaying) {
            mPrevSprint   = false;
            mPrevSneak    = false;
            mPrevMacroRmb = false;
            mPrevMacroLmb = false;
            mInjected     = false;
            getSelf().getLogger().info(
                "harness: macro started at tick {}, range [{}, {})",
                mMacroIndex.load(),
                mMacroStart.load(),
                mMacroEnd.load()
            );
        } else {
            enqueue(R"({"event":"error","reason":"macro_not_loaded"})");
        }
    }

    if (mMacroPlaying.load(std::memory_order_relaxed)) {
        std::shared_ptr<Macro> macro;
        {
            std::lock_guard lock(mMacroMutex);
            macro = mMacro;
        }
        int index = mMacroIndex.load(std::memory_order_relaxed);
        int end   = mMacroEnd.load(std::memory_order_relaxed);
        if (!macro || index < 0 || index >= static_cast<int>(macro->size()) || index >= end) {
            // macro_load can swap in a shorter macro while looping, so the
            // restart point must be re-validated against the *current* size
            // before the row access below; otherwise it reads out of bounds.
            int const restart = mMacroStart.load(std::memory_order_relaxed);
            if (macro && mMacroLoop.load() && !macro->empty() && restart >= 0
                && restart < static_cast<int>(macro->size()) && restart < end) {
                index = restart;
                mMacroIndex.store(index, std::memory_order_relaxed);
            } else {
                finishMacro("end");
            }
        }
        if (mMacroPlaying.load(std::memory_order_relaxed)) {
            auto const& row = (*macro)[static_cast<size_t>(index)];
            applyMacroTick(*client, player, row);
            mMacroIndex.store(index + 1);
        }
        return;
    }

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
    if (apply) {
        applyControls(*client, player, state);
    } else {
        applyControls(*client, player, ControlState{});
    }

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
        auto const rot = player.getRotation();
        // _applyTurnDelta uses the opposite pitch sign from ActorRotation.x.
        float dy = mActiveLookRelative ? mActiveLookYaw : wrapDegrees(mActiveLookYaw - rot.y);
        float dp = mActiveLookRelative ? -mActiveLookPitch : (rot.x - mActiveLookPitch);
        if (mActiveLookTicks > 0) {
            --mActiveLookTicks;
        }
        if (std::fabs(dy) > 1.0e-5f || std::fabs(dp) > 1.0e-5f) {
            applyTurnDelta(player, Vec2{dp, dy});
        }
    }
}

void ProbeMod::afterPlayerTick(LocalPlayer& player) {
    std::uint64_t const postStart = qpcNow();
    try {
        safeTickPost(player);
    } catch (std::exception const& e) {
        reportInternalError("tick_post", e.what());
    } catch (...) {
        reportInternalError("tick_post", "unknown exception");
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
    {
        auto const& pos  = player.getPosition();
        auto const  feet = player.getFeetPos();
        auto const  rot  = player.getRotation();
        mLastX.store(finiteOrZero(static_cast<float>(pos.x)), std::memory_order_relaxed);
        mLastY.store(finiteOrZero(static_cast<float>(pos.y)), std::memory_order_relaxed);
        mLastZ.store(finiteOrZero(static_cast<float>(pos.z)), std::memory_order_relaxed);
        mLastYaw.store(finiteOrZero(rot.y), std::memory_order_relaxed);
        mLastPitch.store(finiteOrZero(rot.x), std::memory_order_relaxed);
        mLastFeetX.store(finiteOrZero(static_cast<float>(feet.x)), std::memory_order_relaxed);
        mLastFeetY.store(finiteOrZero(static_cast<float>(feet.y)), std::memory_order_relaxed);
        mLastFeetZ.store(finiteOrZero(static_cast<float>(feet.z)), std::memory_order_relaxed);
        mPoseValid.store(true, std::memory_order_relaxed);
    }

    latchActionClicks(player);

    // Slot changes are applied after the original tick: the normal tick re-reads
    // the hotbar from input state, so a pre-tick change would be overwritten.
    if (int slot = mSlotRequest.exchange(-1); slot >= 0) {
        applySlotRequest(player, slot);
    }

    if (mStateRequested.exchange(false)) {
        captureState(*client, player);
    }
    if (mButtonsRequested.exchange(false)) {
        captureButtons(*client);
    }
    if (mInventoryRequested.exchange(false)) {
        captureInventory(player);
    }
    {
        std::optional<EntityAction> action;
        {
            std::lock_guard lock(mEntityActionMutex);
            action.swap(mEntityAction);
        }
        if (action) {
            applyEntityAction(player, *action);
        }
    }
    {
        std::optional<EcsRequest> request;
        {
            std::lock_guard lock(mEcsMutex);
            request.swap(mEcsRequest);
        }
        if (request) {
            applyEcs(player, *request);
        }
    }

    {
        bool requested = false;
        {
            std::lock_guard lock(mEntityMutex);
            requested          = mEntitiesRequested;
            mEntitiesRequested = false;
        }
        if (requested) {
            captureEntities(player);
        }
    }

    {
        bool requested = false;
        {
            std::lock_guard lock(mBlockMutex);
            requested        = mBlocksRequested;
            mBlocksRequested = false;
        }
        if (requested) {
            captureBlocks(player);
        }
    }

    {
        bool        requested = false;
        ScanRequest request;
        {
            std::lock_guard lock(mScanMutex);
            requested      = mScanRequested;
            mScanRequested = false;
            if (requested) {
                request = mScanRequest;
            }
        }
        if (requested) {
            captureScan(player, request);
        }
    }

    {
        nlohmann::json hookResult;
        if (detail::hookTick(player, hookResult)) {
            enqueue(hookResult.dump());
        }
    }

    if (mMacroRecording.load(std::memory_order_relaxed)) {
        recordMacroTick(*client, player);
    }

    // Parkour events: landing (with fall distance), damage and death. These
    // run on every tick, independent of JSON/binary recording.
    {
        float const health   = player.getHealth();
        auto const  pose     = player.getFeetPos();
        bool const  onGround = player.isOnGround();
        if (!onGround) {
            if (!mWasAirborne) {
                mWasAirborne = true;
                mAirStartY   = static_cast<float>(pose.y);
            }
        } else if (mWasAirborne) {
            mWasAirborne = false;
            float const fall = mAirStartY - static_cast<float>(pose.y);
            if (fall >= 0.75f) {
                enqueue(
                    nlohmann::json{
                        {"event",         "player_landed"                       },
                        {"fall_distance", finiteOrZero(fall)                    },
                        {"x",             finiteOrZero(static_cast<float>(pose.x))},
                        {"y",             finiteOrZero(static_cast<float>(pose.y))},
                        {"z",             finiteOrZero(static_cast<float>(pose.z))},
                        {"health",        finiteOrZero(health)                  },
                }
                        .dump()
                );
            }
        }
        if (health < mLastHealth - 0.01f) {
            enqueue(
                nlohmann::json{
                    {"event",  "player_hurt"                       },
                    {"health", finiteOrZero(health)                },
                    {"damage", finiteOrZero(mLastHealth - health)  },
            }
                    .dump()
            );
        }
        if (health <= 0.0f && mLastHealth > 0.0f) {
            enqueue(
                nlohmann::json{
                    {"event", "player_died"                                  },
                    {"x",     finiteOrZero(static_cast<float>(pose.x))       },
                    {"y",     finiteOrZero(static_cast<float>(pose.y))       },
                    {"z",     finiteOrZero(static_cast<float>(pose.z))       },
            }
                    .dump()
            );
        }
        mLastHealth = health;
    }

    if (!mRecording.load(std::memory_order_relaxed)) {
        return;
    }

    auto const& pos = player.getPosition();
    auto        vel = player.getVelocity();
    auto const  rot = player.getRotation();
    auto        in  = readInput(*client);

    bool actionSprint = false;
    if (auto action = player.getEntityContext().tryGetComponent<PlayerActionComponent>()) {
        actionSprint = action->mPlayerActions->test(PlayerActionStartSprinting);
    }

    auto const  feet     = player.getFeetPos();
    bool const  onGround = player.isOnGround();
    int const   fx       = static_cast<int>(std::floor(feet.x));
    int const   fy       = static_cast<int>(std::floor(feet.y));
    int const   fz       = static_cast<int>(std::floor(feet.z));
    int const   hy       = static_cast<int>(std::floor(feet.y + 1.8f));
    auto const& source   = player.getDimensionBlockSourceConst();

    nlohmann::json j;
    j["event"]             = "tick";
    j["tick"]              = tickIndex;
    j["x"]                 = finiteOrZero(static_cast<float>(pos.x));
    j["y"]                 = finiteOrZero(static_cast<float>(pos.y));
    j["z"]                 = finiteOrZero(static_cast<float>(pos.z));
    j["vx"]                = finiteOrZero(static_cast<float>(vel.x));
    j["vy"]                = finiteOrZero(static_cast<float>(vel.y));
    j["vz"]                = finiteOrZero(static_cast<float>(vel.z));
    j["yaw"]               = finiteOrZero(rot.y);
    j["pitch"]             = finiteOrZero(rot.x);
    j["on_ground"]         = onGround;
    j["in_air"]            = !onGround;
    j["jump_edge"]         = mJumpEdgeThisTick;
    j["block_below"]       = blockName(source, fx, fy - 1, fz);
    j["block_feet"]        = blockName(source, fx, fy, fz);
    j["block_head"]        = blockName(source, fx, hy, fz);
    j["sprint"]            = player.isSprinting();
    j["sneak"]             = player.isSneaking();
    j["jump"]              = in.jump;
    j["w"]                 = in.w;
    j["a"]                 = in.a;
    j["s"]                 = in.s;
    j["d"]                 = in.d;
    j["action_sprint"]     = actionSprint;
    j["input_sprint"]      = in.sprint;
    j["input_sprint_down"] = in.sprint;
    j["health"]            = player.getHealth();
    enqueue(j.dump());

    auto max = mMaxTicks.load(std::memory_order_relaxed);
    if (max != 0 && tickIndex >= max) {
        mRecording = false;
        enqueue(R"({"event":"stopped","reason":"max_ticks"})");
    }
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
    return detail::isGameForeground();
}

void ProbeMod::applyTurnDelta(LocalPlayer& player, Vec2 const& delta) {
    struct Guard {
        std::atomic_bool& flag;
        explicit Guard(std::atomic_bool& value) : flag(value) { flag = true; }
        ~Guard() { flag = false; }
    } guard{mInjectingLook};
    player._applyTurnDelta(delta);
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
        "queue_dropped=%llu\n"
        "spool=%ls\n"
        "dump=%ls\n",
        static_cast<unsigned long>(pid),
        static_cast<unsigned long long>(tick),
        info != nullptr && info->ExceptionRecord != nullptr ? info->ExceptionRecord->ExceptionCode : 0UL,
        info != nullptr && info->ExceptionRecord != nullptr
            ? info->ExceptionRecord->ExceptionAddress
            : nullptr,
        static_cast<unsigned long>(tid),
        command,
        static_cast<double>(mLastX.load(std::memory_order_relaxed)),
        static_cast<double>(mLastY.load(std::memory_order_relaxed)),
        static_cast<double>(mLastZ.load(std::memory_order_relaxed)),
        static_cast<double>(mLastYaw.load(std::memory_order_relaxed)),
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

void ProbeMod::reportInternalError(char const* phase, std::string const& message) {
    updateRequests([](RequestBlock& request) {
        request.input      = ControlState{};
        request.inputTicks = 0;
        ++request.inputSeq;
        request.jumpCancel = true;
        ++request.jumpSeq;
    });
    cancelTasks("internal_error");

    std::uint64_t const now  = GetTickCount64();
    std::uint64_t const last = mLastInternalErrorMs.load(std::memory_order_relaxed);
    if (now - last < 1000) {
        return;
    }
    mLastInternalErrorMs.store(now, std::memory_order_relaxed);
    getSelf().getLogger().error("harness: {} failed: {}", phase, message);
    enqueue(
        nlohmann::json{
            {"event",   "error"         },
            {"reason",  "internal_error"},
            {"phase",   phase           },
            {"message", message         },
    }
            .dump()
    );
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
