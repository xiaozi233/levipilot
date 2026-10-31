#pragma once

#include "mod/ChatModel.h"
#include "mod/EcsModel.h"
#include "mod/EntityModel.h"
#include "mod/FlightModel.h"
#include "mod/InputEventModel.h"
#include "mod/MacroPlaybackModel.h"
#include "mod/MovementModel.h"
#include "mod/PacketTap.h"
#include "mod/PacketInjectionModel.h"
#include "mod/PlayerIdentityModel.h"
#include "mod/PoseModel.h"
#include "mod/QueryModel.h"
#include "mod/RequestModel.h"
#include "mod/TaskModel.h"
#include "mod/TeleportModel.h"
#include "mod/TickStreamModel.h"
#include "mod/TickRecoveryModel.h"
#include "mod/WorldSession.h"

#include "ll/api/event/Listener.h"
#include "ll/api/mod/NativeMod.h"

#include <array>
#include <atomic>
#include <cstdint>
#include <deque>
#include <filesystem>
#include <fstream>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

#include <nlohmann/json.hpp>

#include <windows.h>

class ClientInstance;
class CommandRegistry;
class Packet;
class LocalPlayer;
class Level;
class Vec2;
class Vec3;

namespace probe {

struct ControlState {
    float forward    = 0.0f;
    float strafe     = 0.0f;
    bool  sprint     = false;
    bool  sneak      = false;
    bool  jump       = false;
    bool  rmb        = false;
    bool  lmb        = false;
    bool  active     = false;
    float yawDelta   = 0.0f;
    float pitchDelta = 0.0f;
};

struct Checkpoint {
    float x     = 0.0f;
    float y     = 0.0f;
    float z     = 0.0f;
    float yaw   = 0.0f;
    float pitch = 0.0f;
};

class ProbeMod {
public:
    static ProbeMod& getInstance();

    ProbeMod() : mSelf(*ll::mod::NativeMod::current()) {}

    [[nodiscard]] ll::mod::NativeMod& getSelf() const { return mSelf; }

    bool load();
    bool enable();
    bool disable();

    // Small append-only diagnostics file in the mod directory. LeviLamina's own
    // log location depends on how the game was launched, so this is the one
    // place a harness bug can always be inspected.
    void bootLog(std::string const& text);

    // G1/G2: event-level input injection. The pipe thread queues; the client
    // tick drains into the game's own Keyboard/MouseDevice queues.
    using InputInjection = detail::InputInjection;
    std::mutex mInputEventMutex; // Cold admission, completion and cancellation only.
    detail::InjectedEventQueue<> mInputEvents;
    std::uint64_t mInputReplyGeneration = 1; // Cold; reconnect discards older results.
    detail::UiCapture mUiCapture;
    detail::TextCapture mTextCapture;
    std::atomic<std::uint64_t> mInputTickSerial{0};
    std::array<std::optional<InputInjection>, 264> mHeldEvents{}; // Tick-owned.
    std::uint64_t mInputEventGeneration = 0; // Tick-owned release generation.
    std::uint64_t queueInputInjection(InputInjection const& item);
    bool queueTextInput(std::string text, detail::CaptureTest test = detail::CaptureTest::None);
    bool queueUiQuery(detail::UiQuery query);
    void drainInputEvents();
    void resetInputEvents();
    void cancelInputEvents(char const* reason);
    char const* applyInputEvent(InputInjection const& item);
    void                                         tickInputInjection();
    bool                                         releaseInputEvents();
    char const*                                  hidKeyEdge(int code, bool down, bool hardware);

    // H1 packet tap (identity ring, byte ring, send/recv hooks) lives in its
    // own owner; these forwarders keep the file-scope LL hooks unchanged.
    PacketTap&       packets() { return mPackets; }
    PacketTap const& packets() const { return mPackets; }
    void             onPacketSent(Packet& packet);
    void             onPacketReceived(Packet const& packet, std::size_t anchor);
    void             packetPeerTick();
    void             packetCaptureStop();

    std::atomic_bool                           mAllowPacketCapture{false};
    std::atomic_bool                           mAllowPacketInject{false};
    std::atomic<std::uint64_t>                 mChatSent{0};
    detail::CommandTemplateStore mCommandTemplates;
    detail::PacketInjectionCapture mPacketInjection;
    std::mutex mPacketInjectionMutex; // Cold admission, scheduling and completion only.
    std::atomic<std::uint64_t> mPacketGeneration{0}, mPacketTemplateFaults{0};
    std::uint64_t mNextPacketId = 0; // Cold-owned.
    std::atomic<detail::CaptureTest> mPacketTemplateTest{detail::CaptureTest::None};
    void queuePacketInjection(detail::PacketInjectionQuery query, std::uint64_t timeout = 2000);
    void drainPacketInjection();
    void cancelPacketInjection(char const* reason, bool discard = false);
    void sendCommandPacket(detail::PacketInjectionQuery const& query, detail::PacketInjectionSnapshot& out);
    bool             hidTextInput(std::string const& text, std::string& error);
    void             captureCommandTemplate(Packet& packet);
    std::atomic_bool mAllowInputInject{false};
    std::atomic_bool mInjectingInput{false};
    // While scripts own the session, real hardware keys/mouse are dropped at
    // the HID/mouse entry points; our own injections still pass.
    std::atomic_bool mHardwareInputLocked{false};
    // config lock_input: arm the lock only while a harness client is attached.
    // Locking at startup made a manually launched game unclickable at the menu.
    std::atomic_bool mLockInputOnConnect{false};
    // Tick until which a game-side turn is allowed because we injected a mouse
    // move (the game applies it later in the frame, outside our call).
    std::atomic<std::uint64_t> mInjectTurnUntilTick{0};
    std::atomic<std::uint64_t> mKeyEventCount{0};
    std::atomic<std::uint64_t> mKeyInjectedSeen{0};
    std::atomic<std::int32_t>  mKeyLastCode{-1};
    std::atomic<std::int32_t>  mKeyLastAction{-1};
    std::atomic<std::uint64_t> mMouseInjected{0};
    ll::event::ListenerPtr     mKeyListener;

    // Called by the _applyTurnDelta hook: background mouse look must not move
    // the camera, but our own look/task injections must still work.
    [[nodiscard]] bool allowTurnDelta() const;
    [[nodiscard]] bool hardwareInputLocked() const
    {
        return mHardwareInputLocked.load(std::memory_order_relaxed);
    }
    [[nodiscard]] bool injectingInput() const
    {
        return mInjectingInput.load(std::memory_order_relaxed);
    }

    // Called from the vectored exception handler for genuinely fatal faults
    // (an access violation that is not part of a dispatched request).
    void handleFatalException(EXCEPTION_POINTERS* info);

    // Called by the LocalPlayer::normalTick hook: before and after the original tick.
    void onPlayerTick(LocalPlayer& player);
    void afterPlayerTick(LocalPlayer& player);

private:
    friend class WorldSession;
    void serverLoop();
    void handleLine(std::string const& line);
    bool dispatchWithSeh(std::string const& line);
    // Not inlined: keeps the SEH (__try) frame out of the command dispatcher
    // and avoids C2712 with LTCG.
    __declspec(noinline) void dispatchLine(std::string const& line);
    // Pipe-thread handlers: true means consumed, including validation failures.
    bool dispatchDiagnosticsCommand(std::string const& cmd, nlohmann::json& j);
    bool dispatchInspectionCommand(std::string const& cmd, nlohmann::json& j);
    bool dispatchInputCommand(std::string const& cmd, nlohmann::json& j);
    bool dispatchPacketsCommand(std::string const& cmd, nlohmann::json& j);
    bool dispatchRecordingCommand(std::string const& cmd, nlohmann::json& j);
    bool dispatchQueryCommand(std::string const& cmd, nlohmann::json& j);
    bool dispatchWorldCommand(std::string const& cmd, nlohmann::json& j);
    bool dispatchTasksCommand(std::string const& cmd, nlohmann::json& j);
    void enqueue(std::string line);

    // T1: durable record spool and crash forensics. Records are appended to
    // <modDir>/spool-<pid>.jsonl so a killed/crashed game still leaves data.
    void spoolThreadLoop();
    // Returns the number of rows written; 0 when nothing was due/queued.
    std::uint64_t      flushSpool(bool force);
    // Re-append swapped-out rows after a failed spool write (bounded).
    void               requeueSpool(std::string& data);
    void               installCrashHandler();
    static LONG WINAPI crashFilter(EXCEPTION_POINTERS* info);
    void               writeCrashArtifacts(EXCEPTION_POINTERS* info) noexcept;
    void               recordCommand(std::string const& line);
    // Runs a callback on the integrated server thread with exception isolation.
    void queueServerTask(
        std::function<void()>            fn,
        std::function<void(char const*)> onFailure              = {},
        bool                             rejectQueueForSelftest = false
    );

    // Tick isolation: a bug in one of our hooks must not take the game down.
    void safeTickPre(LocalPlayer& player);
    void safeTickPost(LocalPlayer& player);
    void reportInternalError(detail::TickPhase phase, char const* message) noexcept;
    void drainInternalErrors();
    bool recoverTickControls(LocalPlayer& player);
    void injectTickError(detail::TickPhase phase);

    // Closed-loop tasks.
    bool          tickTask(ClientInstance& client, LocalPlayer& player);
    bool          tickNavigate(ClientInstance& client, LocalPlayer& player, NavigateTask& task, detail::TaskStep& step);
    bool          tickJump(ClientInstance& client, LocalPlayer& player, JumpTask& task, detail::TaskStep& step);
    bool          tickRoute(ClientInstance& client, LocalPlayer& player, RouteTask& task, detail::TaskStep& step);
    bool          tickMine(LocalPlayer& player, MineTask& task, detail::TaskStep& step);
    bool          tickPlace(LocalPlayer& player, PlaceTask& task, detail::TaskStep& step);
    bool          tickUse(LocalPlayer& player, UseTask& task, detail::TaskStep& step);
    void          cancelTasks(char const* reason = "cancelled");
    std::uint64_t startTask(TaskState state);
    void          drainTaskEvents();
    void          requestChatHistory(int limit, bool deferForSelftest = false);
    static void   captureChatHistory(detail::ChatCapture::Snapshot& snapshot, int limit);
    void          drainChatHistory();
    void          dispatchUseTask(std::uint64_t id);
    void          beginTask();
    nlohmann::json taskStatusJson();
    [[nodiscard]] bool aimAt(LocalPlayer& player, Vec3 const& point);

    // World flow: start/leave an existing world without going through the UI.
    void enterWorld(std::string const& name, std::string const& id, int intent, bool force);
    void leaveWorld();
    void listWorlds();
    void reportSignInState();
    void setOptions(std::string const& text);
    void saveOptions();
    std::uint64_t resetPlayer(
        float x,
        float y,
        float z,
        bool  hasYaw,
        float yaw,
        bool  hasPitch,
        float pitch,
        bool  clearEffects
    );
    void loadAutoConfig();
    void registerCommands(CommandRegistry& registry);
    [[nodiscard]] bool applyTurnDelta(LocalPlayer& player, Vec2 const& delta);
    void updateFocusState();

    [[nodiscard]] bool applyControls(ClientInstance& client, LocalPlayer& player, ControlState const& state);
    bool applyLegacyControls(ClientInstance& client, LocalPlayer& player, ControlState const& state);
    template <class Apply> bool guardedControl(detail::ControlOp op, Apply&& apply);
    void drainControlDiagnostics();
    detail::ControlDiagnostics mControlDiagnostics;
    std::array<std::atomic<detail::ControlTest>, detail::ControlDiagnostics::Operations> mControlTests{};
    std::atomic_bool mControlCleanupPending{false};
    std::atomic<std::uint32_t> mControlHeldMask{0};
    void publishHeldInput();
    // Real-input path: translate the desired digital key state into Hardware
    // HID key edges. Legacy flag/queue/handler/impulse paths are gated behind
    // input_cfg force.
    bool applyRealInput(LocalPlayer& player, ControlState const& state);
    bool releaseRealInput();
    // Reads the guest's current keyboard remapping (IOptionRegistry →
    // KeyboardRemappingLayout → key.* actions). Returns true when valid.
    bool refreshBindings(bool releaseOnChange);
    // Shared walk-to-target driver for tickNavigate/tickRoute; defined in
    // Tasks.cpp. Reads/writes the shared lastX/lastZ/haveLast/stuckTicks/
    // jumpCooldown fields directly on the task.
    template <typename TaskT, bool CountJumps = false>
    [[nodiscard]] bool walkToward(
        ClientInstance& client,
        LocalPlayer&    player,
        TaskT&          task,
        float           targetX,
        float           targetZ,
        float           distance,
        float           tolerance,
        JumpMode        jumpMode,
        float           jumpDistance,
        float           edgeLookahead
    );
    bool applyMacroTick(ClientInstance& client, LocalPlayer& player, MacroTick const& tick, bool eventLevel);
    std::uint64_t queueTeleport(detail::TeleportQuery query, bool resetControls = false, std::uint64_t timeout = 2000);
    void drainTeleport();
    void cancelTeleport(char const* reason, bool discard = false);
    void captureTeleport(detail::TeleportQuery const& query, detail::TeleportSnapshot& out);
    void applySlotRequest(LocalPlayer& player, detail::SlotQuery const& request, detail::SlotSnapshot& out);
    void captureState(
        ClientInstance&               client,
        LocalPlayer&                  player,
        detail::CaptureRequest const& request,
        detail::StateSnapshot&        out
    );
    void captureInventory(LocalPlayer& player, detail::CaptureRequest const& request, detail::InventorySnapshot& out);
    void drainEntities();
    detail::EntityStage captureEntityStage(detail::EntityStage stage, detail::EntityQuery const& query,
                                           detail::EntitySnapshot& out);
    detail::EcsStage captureEcsStage(detail::EcsStage stage, detail::EcsQuery const& query,
                                   detail::EcsSnapshot& out);
    void drainEcs();
    void queueInventoryWrite(std::string const& container, int slot, std::string const& item, int count, int aux);
    // Moves an existing inventory stack into a worn slot (armor/offhand) so
    // enchantments applied while held (e.g. /enchant) survive the transfer.
    void queueEquip(std::string const& target, int slot);
    void queueGive(std::string const& item, int count, int aux);
    void queueClearInventory();
    void captureBlocks(LocalPlayer& player, detail::BlockRequest const& request, detail::BlockSnapshot& out);
    bool captureScan(LocalPlayer& player, detail::ScanQuery const& request, detail::ScanSnapshot& out);
    void drainQueries();
    void resetQueries();
    void cancelQueries(char const* reason);

    // Named checkpoints persisted in <modDir>/checkpoints.json.
    std::filesystem::path checkpointsPath() const;
    void                  loadCheckpoints();
    bool                  saveCheckpoints(std::string& error);
    void                  checkpointSave(std::string const& name);
    void                  checkpointLoad(std::string const& name);
    void                  checkpointList();
    void                  checkpointDelete(std::string const& name);
    void captureButtons(ClientInstance& client, detail::CaptureRequest const& request, detail::ButtonSnapshot& out);
    bool performClick(LocalPlayer& player, int button);
    void drainClicks();
    void recordMacroTick(ClientInstance& client, LocalPlayer& player);
    void latchActionClicks(LocalPlayer& player);
    void drainMacroPlaybackLocked(bool wait = false);
    void drainMacroPlayback();

    std::filesystem::path macroDir() const;
    std::filesystem::path macroPath(std::string const& name) const;
    bool loadMacroFrom(std::string const& path, std::string const& name, std::string& error, bool& stopped);

    static bool parseMacroJson(nlohmann::json const& value, Macro& out, std::string& error);
    static bool macroToJson(Macro const& macro, nlohmann::json& out, std::string& error);
    static bool parseMacroFile(std::filesystem::path const& path, Macro& out, std::string& error);
    static bool saveMacroFile(std::filesystem::path const& path, Macro const& macro, std::string& error);

    ll::mod::NativeMod& mSelf;
    WorldSession mWorld{*this};
    PacketTap    mPackets;
    bool queueClientTask(std::function<void()> task, std::function<void(char const*)> onFailure = {});

    ll::event::ListenerPtr mMouseListener;
    ll::event::ListenerPtr mServerCommandListener;
    // Registry the /togglefly overload was added to. Compared by identity so a
    // rebuilt registry (new world) gets the command again without duplicating
    // the execute handler on the same one.
    void const* mCommandRegistry = nullptr;
    std::thread            mServerThread;

    std::atomic_bool mRunning{false};
    std::atomic<std::uint64_t> mLastTickMs{0};
    // QPC timestamps for the T9 overhead metric (tick thread only).
    std::uint64_t              mTickStartQpc     = 0;
    std::uint64_t              mPrevTickStartQpc = 0;
    std::uint64_t              mPreUs            = 0;
    std::atomic<std::uint64_t> mTick{0};
    // Tick writes fixed samples; commands/spool serialize consumption and
    // lifecycle with this cold-only lock. No transport or JSON on the tick.
    detail::LegacyStream<> mLegacyStream;
    std::mutex mLegacyMutex;
    detail::LegacyStream<>::Completion mLegacyLastCompletion;
    std::atomic<detail::StreamTest> mLegacyTest{detail::StreamTest::None};
    std::atomic_bool mLegacyWorldLeft{false};
    std::uint64_t mLegacyDrainAfterMs = 0; // Cold lock; gated overflow selftest.
    bool startLegacy(std::uint64_t maxTicks, std::size_t capacity = 256,
                     detail::StreamTest test = detail::StreamTest::None, std::uint64_t drainDelayMs = 0);
    void stopLegacy(detail::StreamEnd reason, bool reply);
    bool drainLegacyLocked();
    void drainTickStreams();

    // Movement and look requests share one transactional mailbox so
    // the client tick can take a single lock-free snapshot. Atomic payload
    // words make concurrent reads legal. Command sequence numbers make each
    // command visible exactly once, which
    // keeps the tick-local countdowns (ticks < 0 means "until stopped").
    struct RequestBlock {
        ControlState  input;
        int           inputTicks = 0;
        std::uint32_t inputSeq   = 0;
        // A new jumpSeq either requests a jump or cancels a pending one
        // (beginTask / internal-error reset); jumpCancel disambiguates.
        bool            jumpCancel   = false;
        std::uint32_t   jumpSeq      = 0;
        bool            lookRelative = true;
        float           lookYaw      = 0.0f;
        float           lookPitch    = 0.0f;
        int             lookTicks    = 0;
        std::uint32_t   lookSeq      = 0;
    };

    template <typename Fn>
    void updateRequests(Fn&& fn) {
        mRequests.update(std::forward<Fn>(fn));
    }

    [[nodiscard]] RequestBlock readRequests() {
        mRequests.read(mLastRequests);
        return mLastRequests;
    }

    detail::RequestMailbox<RequestBlock> mRequests;
    RequestBlock mLastRequests{}; // Game-thread-owned fallback, never a partial copy.

    // safeTickPre/Post stage helpers; each keeps the exact ordering the
    // monolithic functions had (ProbeMod.cpp).
    bool queueFlight(detail::FlightQuery query, std::uint64_t timeout = 2000);
    void drainFlight();
    void tickFlightAssertion(LocalPlayer& player);
    void cancelFlight(char const* reason, bool discard = false);
    detail::FlightStage captureFlightStage(detail::FlightStage stage, detail::FlightQuery const& query,
                                           detail::FlightSnapshot& out);
    void releaseMacroIfRequested();
    void tickMacroPlayback(ClientInstance& client, LocalPlayer& player);
    void consumeMovementRequests(ClientInstance& client, LocalPlayer& player, RequestBlock const& requests);
    void consumeLookRequests(LocalPlayer& player, RequestBlock const& requests);
    void recordTickPose(LocalPlayer& player);
    void runCaptureRequests(ClientInstance& client, LocalPlayer& player);
    void tickParkourEvents(LocalPlayer& player, std::uint64_t tick);
    void captureLegacyTick(ClientInstance& client, LocalPlayer& player);
    // Tick-thread consumption state for the block above.
    std::uint32_t mSeenInputSeq = 0;
    ControlState  mActiveInput;
    int           mActiveInputTicks   = 0;
    std::uint32_t mSeenJumpSeq        = 0;
    bool          mJumpRequestPending = false;
    std::uint32_t mSeenLookSeq        = 0;
    bool          mActiveLookRelative = true;
    float         mActiveLookYaw      = 0.0f;
    float         mActiveLookPitch    = 0.0f;
    int           mActiveLookTicks    = 0;

    bool mPrevSprint  = false;
    bool mPrevSneak   = false;
    bool mInjected    = false;
    bool mInputActive = false;
    // Physical-input path: keys are held through HIDController with the
    // Hardware origin, exactly like the guest's keyboard. The legacy
    // flag/queue/handler/impulse paths are explicit diagnostics only.
    struct KeyBindings {
        int forward = 87;  // W fallback
        int back    = 83;  // S
        int left    = 65;  // A
        int right   = 68;  // D
        int sneak   = 16;  // Lshift
        int jump    = 32;  // Space
        int sprint  = 0;   // 0 = unassigned; use double-tap forward
    };
    KeyBindings mBindings;
    bool        mBindingsValid = false;
    std::uint64_t mBindingsTick = 0;
    std::array<bool, 7> mHeldKeys{};
    bool                mHeldLmb = false;
    bool                mHeldRmb = false;
    // 0 idle, 1 first W tap down, 2 W released, 3 sprint latched. Sprint is
    // produced by the real double-tap-W gesture; Ctrl is not bound in this build.
    int mSprintTapPhase = 0;
    // Set by input_cfg on the pipe thread, read/written on the tick thread.
    std::atomic_bool mPrevJump{false};
    // Set by applyControls when a jump press edge is injected; cleared at the
    // start of each tick so the tick event can report it.
    bool             mJumpEdgeThisTick = false;
    bool             mPrevMacroRmb     = false;
    bool             mPrevMacroLmb     = false;
    std::atomic_bool mMouseLeft{false};
    std::atomic_bool mMouseRight{false};
    // Latched on the press edge so a click shorter than one tick is still recorded.
    std::atomic_bool mMouseLeftLatch{false};
    std::atomic_bool mMouseRightLatch{false};
    // Diagnostics: which MouseInputEvent ids actually reach the game.
    std::atomic<std::uint64_t>      mMouseEventCount{0};
    std::atomic<int>                mMouseLastAction{-1};
    std::atomic<int>                mMouseLastData{-1};
    std::array<std::atomic<int>, 8> mMouseActionCounts{};
    // Set while the mod itself calls GameMode, so playback is not re-recorded.
    std::atomic_bool mInjectingClick{false};
    // Set while the mod itself applies a turn (look/task), so the background
    // input guard lets it through.
    std::atomic<std::uint32_t> mInjectingLook{0}; // Overlapping client/tick turns.
    // Written by enable()/config and read on the tick thread.
    std::atomic_bool mBackgroundGuard{true};
    bool             mForeground     = true;
    detail::ParkourTracker mParkourTracker; // Tick-owned; reset on generation changes.
    detail::TickQueue<detail::ParkourEvent, 256> mParkourEvents;
    std::atomic<std::uint64_t> mParkourGeneration{0};
    std::uint64_t mParkourSeenGeneration = 0;
    std::atomic<detail::StreamTest> mParkourTest{detail::StreamTest::None};
    std::atomic<std::uint64_t> mParkourLost{0}, mParkourFaults{0}, mParkourFailures{0};
    int                 mCursorShowCalls = 0;
    // Reset from the pipe thread (mouse_clear/macro_rec_start), read/written
    // on the tick thread.
    std::atomic_bool   mPrevActionLmb{false};
    std::atomic_bool   mPrevActionRmb{false};
    std::array<bool, 9> mButtonStates{};
    std::array<bool, 9> mHandlerStates{};
    std::atomic_bool mResetInputButtons{false};

    // Look requests live in RequestBlock above (mLookMutex removed in T9).

    detail::StateCapture  mStateCapture;
    detail::ButtonCapture mButtonCapture;
    detail::BlockCapture  mBlockCapture;
    detail::ScanCapture   mScanCapture;
    detail::InventoryCapture mInventoryCapture;
    detail::SlotCapture mSlotCapture;
    // UI work owns a bounded snapshot; the spool consumer owns timeout and JSON.
    detail::ChatCapture mChatCapture;
    detail::EntityCapture mEntityCapture;
    detail::EcsCapture mEcsCapture;
    std::atomic<int>            mInputSource{-1};
    std::atomic<int>            mInputController{0};
    std::atomic<int>            mInputFocus{0};
    detail::FlightCapture mFlightCapture;
    detail::TeleportCapture mTeleportCapture{detail::TeleportStage::Server, "teleport_stage_invalid"};
    std::mutex mTeleportMutex; // Cold admission, scheduling and lifecycle only.
    std::atomic<std::uint64_t> mTeleportGeneration{0};
    std::uint64_t mNextTeleportId = 0; // Cold-owned.
    detail::FlightAssertion mFlightAssertion;
    std::mutex mFlightMutex; // Cold admission, completion and lifecycle only.
    std::atomic<std::uint64_t> mFlightGeneration{0};
    std::uint64_t mNextFlightId = 0, mFlightReplyAfter = 0; // Cold-owned.
    struct ClickResult {
        int         button = 0, hitType = 0, x = 0, y = 0, z = 0, face = 0;
        float       hitX = 0, hitY = 0, hitZ = 0;
        bool        gameMode = false, hitValid = false;
        char const* error = nullptr;
    };
    detail::TickQueue<ClickResult, 128> mClickResults;
    std::atomic<std::uint64_t>          mClickResultsLost{0};
    std::atomic<detail::CaptureTest>    mClickCaptureTest{detail::CaptureTest::None};
    std::atomic<int>                    mClickRequest{-1};
    std::atomic<float>          mClickReach{5.5f};
    std::atomic<int>            mSlotRequest{-1};
    // 0 = real physical input (default). Legacy diagnostics:
    // bit 1 direct flags, bit 2 queue, bit 4 handler callbacks, bit 8 toggle fallback.
    // Non-zero is accepted by input_cfg only with force=true.
    std::atomic<int> mButtonMode{0};
    // 0 = real Space key (default). Legacy diagnostics:
    // bit 1 = LocalPlayer::jumpFromGround, bit 2 = MobJump::setJumpPendingScale.
    // Non-zero is accepted by input_cfg only with force=true.
    std::atomic<int> mJumpImpulseMode{0};


    std::mutex                        mCheckpointMutex;
    std::map<std::string, Checkpoint> mCheckpoints;
    // Tick writer; coherent checkpoint/crash snapshots. World exit advances
    // the epoch so the preceding world's pose cannot become a new checkpoint.
    detail::PoseStore                mPose;
    std::atomic<std::uint64_t>       mPoseEpoch{1};
    std::atomic<detail::CaptureTest> mPoseTest{detail::CaptureTest::None};

    detail::TaskController     mTasks;
    std::uint64_t              mSeenTaskId = 0; // Game-thread input ownership generation.
    std::atomic<std::uint64_t> mTaskFaultId{0}; // Explicitly gated fault injection.

    // Tick publishes UUID values only; cold callbacks validate the world epoch
    // again before resolving the matching server-side player.
    detail::PlayerIdentityStore mPlayerIdentity;
    std::atomic<detail::CaptureTest> mPlayerIdentityTest{detail::CaptureTest::None};
    detail::PlayerIdentity mPlayerIdentityRemembered; // Pipe-only selftest data.
    detail::PlayerIdentity localPlayerIdentity() const;
    void capturePlayerIdentity(LocalPlayer& player);

    // Playback owns immutable frames; only the cold session lock mutates them.
    detail::MacroPlayback            mMacroPlayback;
    std::atomic<detail::CaptureTest> mMacroPlaybackTest{detail::CaptureTest::None};
    std::atomic<detail::CaptureTest> mMacroReleaseTest{detail::CaptureTest::None};
    bool                             applyMacroTickEvents(LocalPlayer& player, MacroTick const& tick);
    bool                             releaseMacroInputs();

    // Macro recorder.
    detail::MacroRecordSession       mMacroRecording;
    std::mutex                 mMacroSessionMutex;
    std::atomic<detail::CaptureTest> mMacroRecordTest{detail::CaptureTest::None};
    std::atomic_bool                 mMacroRecordWorldLeft{false};
    void                             drainMacroRecording();
    void                             drainMacroRecordingLocked();

    std::mutex              mQueueMutex;
    std::deque<std::string> mQueue;
    std::atomic<std::uint64_t> mQueueDropped{0};
    std::atomic<std::uint64_t> mPipeWriteTimeouts{0};
    detail::TickRecovery mTickRecovery;
    // Cold only; handleLine owns this outside its raw SEH frame, so dispatch
    // faults still release the lock. The spool recovery serializes admission.
    std::mutex mTickRecoveryMutex;
    std::uint64_t mLastInternalErrorMs = 0; // Spool-owned logger rate limit.
    std::array<std::atomic<detail::TickErrorTest>, 2> mTickErrorTests{};
    std::atomic<std::uint64_t> mTickRecoveryHoldUntilMs{0}; // Gated selftest only.

    // T1 spool. mSpoolBuffer/mSpoolDroppedMarked/mLastFlushMs are protected by
    // mQueueMutex (the tick path already takes it in enqueue); the file stream is
    // only touched by the spool thread or an explicit flush.
    std::thread   mSpoolThread;
    std::string   mSpoolBuffer;
    std::uint64_t mSpoolDroppedMarked = 0;
    std::uint64_t mLastFlushMs        = 0;
    std::mutex    mSpoolFileMutex;
    // F6: serializes swap+write so two flush callers cannot interleave rows.
    std::mutex                 mSpoolWriteMutex;
    std::ofstream              mSpoolFile;
    std::uint64_t              mSpoolFileBytes = 0;
    int                        mSpoolRotation  = 0;
    std::filesystem::path      mSpoolPath;
    wchar_t                    mModDirW[1024]{};
    std::atomic<std::uint64_t> mSpoolRows{0};
    std::atomic<std::uint64_t> mSpoolFlushes{0};
    // F4: set by non-spool threads that need the next flush to be immediate
    // (world_left, panic_done) without doing file IO on the tick thread.
    std::atomic_bool           mSpoolForce{false};
    std::atomic<std::uint64_t> mSpoolErrors{0};

    // Crash report context. The pipe thread is the only writer of
    // mLastCommand; the tick thread is the only writer of mPose.
    char                       mLastCommand[512]{};
    std::atomic<std::uint32_t> mTickThreadId{0};

    // T2/T3 configuration and diagnostics.
    std::atomic_bool           mAllowMemoryWrite{false};
    std::atomic_bool           mAllowCrashTest{false};
    std::atomic_bool           mAllowHookVa{false};
    std::atomic_bool           mAllowSelftestStress{false};
    std::atomic_bool           mAllowWatch{true};
    std::atomic_bool           mAllowVersionMismatch{false};
    std::atomic_bool           mCrashDumpInProgress{false};
    std::atomic<std::uint64_t> mTickProbe{0};
};

} // namespace probe
