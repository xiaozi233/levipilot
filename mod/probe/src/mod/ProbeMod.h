#pragma once

#include "mod/TaskModel.h"
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

#include "mc/network/packet/CommandRequestPacketPayload.h"

#include <windows.h>

class ClientInstance;
class Packet;
class LocalPlayer;
class Level;
class Vec2;
class Vec3;

namespace probe {

struct MacroTick {
    bool  w      = false;
    bool  a      = false;
    bool  s      = false;
    bool  d      = false;
    bool  sprint = false;
    bool  sneak  = false;
    bool  jump   = false;
    bool  rmb    = false;
    bool  lmb    = false;
    float yaw    = 0.0f;
    float pitch  = 0.0f;

    [[nodiscard]] float forward() const { return (w ? 1.0f : 0.0f) - (s ? 1.0f : 0.0f); }
    [[nodiscard]] float strafe() const { return (d ? 1.0f : 0.0f) - (a ? 1.0f : 0.0f); }
};

using Macro = std::vector<MacroTick>;

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

struct BlockQuery {
    int x = 0;
    int y = 0;
    int z = 0;
};

struct ScanRequest {
    int  x1         = 0;
    int  y1         = 0;
    int  z1         = 0;
    int  x2         = 0;
    int  y2         = 0;
    int  z2         = 0;
    bool includeAir = false;
    int  limit      = 4096;
};

struct Checkpoint {
    float x     = 0.0f;
    float y     = 0.0f;
    float z     = 0.0f;
    float yaw   = 0.0f;
    float pitch = 0.0f;
};

struct EcsRequest {
    enum class Kind { Entity, Components, Component, Registries, Context };

    Kind          kind      = Kind::Entity;
    std::uint64_t runtimeId = 0;
    // Kind::Context: raw EntityContext* captured by a hook argument.
    std::uint64_t contextAddress = 0;
    std::uint32_t componentId    = 0;
    std::uint32_t length         = 64;
};

struct TeleportRequest {
    float x            = 0.0f;
    float y            = 0.0f;
    float z            = 0.0f;
    bool  hasRotation  = false;
    float yaw          = 0.0f;
    float pitch        = 0.0f;
    bool  clearEffects = false;
};

struct EntityAction {
    enum class Kind { Info, Attack, Interact, LookAt };

    Kind          kind      = Kind::Info;
    std::uint64_t runtimeId = 0;
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
    struct InputInjection {
        std::uint8_t kind   = 0; // 0=key queue, 1=mouse, 2=HID virtual, 3=HID hardware
        std::uint8_t code   = 0; // key code / MouseAction
        std::int8_t  data   = 0; // key down or MouseAction data
        std::int16_t dx     = 0;
        std::int16_t dy     = 0;
        std::int16_t x      = 0;
        std::int16_t y      = 0;
        bool         hasPos = false;
        bool         repeat = false;
    };
    static constexpr std::size_t                 kInputInjectRing = 128;
    std::array<InputInjection, kInputInjectRing> mInjectRing{};
    std::atomic<std::uint32_t>                   mInjectHead{0};
    std::atomic<std::uint32_t>                   mInjectTail{0};
    bool                                         queueInputInjection(InputInjection const& item);
    void                                         tickInputInjection();
    bool                                         hidKeyEvent(int code, bool down, bool hardware, std::string& error);

    // H1 stage A: packet identity tap. Only id/tick/seq/tid, no bytes.
    // dir: 0 = client -> server, 1 = server -> client (in-process loopback).
    static constexpr std::uint8_t kPacketSend = 0;
    static constexpr std::uint8_t kPacketRecv = 1;
    // Recv-side anchors, in installation order. The first block is the
    // server-side send helper family; the last two are the loopback delivery
    // thunks. Both layers can fire for one packet, so counts are per anchor.
    enum RecvAnchor : std::size_t {
        kRecvToPlayer = 0,
        kRecvToActor,
        kRecvToBlockPos,
        kRecvToClientId,
        kRecvToClientWithSubId,
        kRecvToClients,
        kRecvToClientsExcept,
        kRecvLoopbackClient,
        kRecvLoopbackClientUser,
        kRecvAnchorCount,
    };
    struct PacketRecord {
        std::atomic<std::uint64_t> seq{0};
        std::uint64_t              order    = 0;
        std::uint64_t              tick     = 0;
        std::int32_t               id       = 0;
        std::uint32_t              tid      = 0;
        std::uint8_t               dir      = kPacketSend;
        std::uint8_t               injected = 0;
        std::uint8_t               pad[2]{};
    };
    struct PacketBytes {
        std::atomic<std::uint64_t> seq{0};
        std::uint64_t              order = 0;
        std::uint32_t              id    = 0;
        std::uint32_t              size  = 0;
        std::uint32_t              tid   = 0;
        std::uint8_t               dir   = kPacketSend;
        std::uint8_t               data[128]{};
    };
    static constexpr std::size_t                             kPacketRing     = 1u << 14;
    static constexpr std::size_t                             kPacketByteRing = 1u << 12;
    std::unique_ptr<PacketBytes[]>                           mPacketByteRing;
    std::atomic<std::uint64_t>                               mPacketByteHead{0};
    std::array<std::int32_t, 4>                              mPacketSerializeIds{};
    std::size_t                                              mPacketSerializeCount = 0;
    std::unique_ptr<PacketRecord[]>                          mPacketRing;
    std::atomic<std::uint64_t>                               mPacketHead{0};
    std::atomic<std::uint64_t>                               mPacketSeq{0};
    std::atomic<std::uint64_t>                               mPacketPeerCount{0};
    std::atomic<std::uint64_t>                               mPacketDenied{0};
    std::atomic_bool                                         mPacketCapture{false};
    std::atomic_bool                                         mPacketSendHooked{false};
    std::atomic_bool                                         mPacketPeerHooked{false};
    std::atomic_bool                                         mPacketSendEnabled{false};
    std::atomic_bool                                         mPacketRecvEnabled{false};
    std::array<std::atomic_bool, kRecvAnchorCount>           mPacketRecvHooked{};
    std::array<std::atomic<std::uint64_t>, kRecvAnchorCount> mPacketRecvHits{};
    std::atomic<std::uint64_t>                               mPacketRecvRecords{0};
    std::array<std::int32_t, 64>                             mPacketIds{};
    std::size_t                                              mPacketIdCount = 0;
    std::atomic<std::uint32_t>                               mPacketSample{1};
    void                                                     onPacketSent(Packet& packet);
    void                                                     onPacketReceived(Packet const& packet, std::size_t anchor);
    void                                                     recordPacket(Packet const& packet, std::uint8_t dir);
    void                                                     packetPeerTick();
    nlohmann::json packetCaptureStart(bool enable, nlohmann::json const& args);
    void           packetCaptureStop();
    nlohmann::json packetCaptureStatus();
    nlohmann::json packetDrain(std::uint64_t since, std::size_t limit);
    nlohmann::json packetBytesDrain(std::uint64_t since, std::size_t limit);

    std::atomic_bool                           mAllowPacketCapture{false};
    std::atomic_bool                           mAllowPacketInject{false};
    std::mutex                                 mChatMutex;
    std::string                                mChatPending;
    std::mutex                                 mTextMutex;
    std::string                                mTextPending;
    std::mutex                                 mCommandTemplateMutex;
    std::optional<CommandRequestPacketPayload> mCommandTemplate;
    std::atomic<std::uint64_t>                 mChatSent{0};
    std::atomic<std::int32_t>                  mChatLastResult{0};
    void                                       tickPacketInject(LocalPlayer& player);
    bool             sendCommandPacket(LocalPlayer& player, std::string const& text, std::string& error);
    bool             hidTextInput(std::string const& text, std::string& error);
    void             captureCommandTemplate(Packet& packet);
    std::atomic_bool mAllowInputInject{false};
    std::atomic_bool mInjectingInput{false};
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
    void               installCrashHandler();
    static LONG WINAPI crashFilter(EXCEPTION_POINTERS* info);
    void               writeCrashArtifacts(EXCEPTION_POINTERS* info) noexcept;
    void               recordCommand(std::string const& line);
    // Runs a callback on the integrated server thread with exception isolation.
    void queueServerTask(std::function<void()> fn);

    // Tick isolation: a bug in one of our hooks must not take the game down.
    void safeTickPre(LocalPlayer& player);
    void safeTickPost(LocalPlayer& player);
    void reportInternalError(char const* phase, std::string const& message);

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
    void          requestChatHistory(int limit);
    void          captureChatHistory(int limit);
    void          drainChatHistory();
    void          dispatchUseTask(std::uint64_t id);
    void          beginTask();
    nlohmann::json taskStatusJson();
    void           aimAt(LocalPlayer& player, Vec3 const& point);

    // World flow: start/leave an existing world without going through the UI.
    void enterWorld(std::string const& name, std::string const& id, int intent, bool force);
    void leaveWorld();
    void listWorlds();
    void reportSignInState();
    void setOptions(std::string const& text);
    void saveOptions();
    void resetPlayer(float x, float y, float z, bool hasYaw, float yaw, bool hasPitch, float pitch);
    void loadAutoConfig();
    void applyTurnDelta(LocalPlayer& player, Vec2 const& delta);
    void updateFocusState();

    void applyControls(ClientInstance& client, LocalPlayer& player, ControlState const& state);
    void applyMacroTick(ClientInstance& client, LocalPlayer& player, MacroTick const& tick);
    void requestServerTeleport(LocalPlayer& player, TeleportRequest const& request);
    void applySlotRequest(LocalPlayer& player, int slot);
    void captureState(ClientInstance& client, LocalPlayer& player);
    void captureInventory(LocalPlayer& player);
    void applyEntityAction(LocalPlayer& player, EntityAction const& action);
    void applyEcs(LocalPlayer& player, EcsRequest const& request);
    void queueInventoryWrite(std::string const& container, int slot, std::string const& item, int count, int aux);
    void queueGive(std::string const& item, int count, int aux);
    void queueClearInventory();
    void captureEntities(LocalPlayer& player);
    void captureBlocks(LocalPlayer& player);
    void captureScan(LocalPlayer& player, ScanRequest const& request);

    // Named checkpoints persisted in <modDir>/checkpoints.json.
    std::filesystem::path checkpointsPath() const;
    void                  loadCheckpoints();
    bool                  saveCheckpoints(std::string& error);
    void                  checkpointSave(std::string const& name);
    void                  checkpointLoad(std::string const& name);
    void                  checkpointList();
    void                  checkpointDelete(std::string const& name);
    void                  captureButtons(ClientInstance& client);
    void                  performClick(LocalPlayer& player, int button);
    void                  recordMacroTick(ClientInstance& client, LocalPlayer& player);
    void                  latchActionClicks(LocalPlayer& player);
    void                  finishMacro(std::string reason);

    std::filesystem::path macroDir() const;
    std::filesystem::path macroPath(std::string const& name) const;
    bool                  loadMacroFrom(std::string const& path, std::string const& name, std::string& error);

    static bool parseMacroJson(nlohmann::json const& value, Macro& out, std::string& error);
    static bool macroToJson(Macro const& macro, nlohmann::json& out, std::string& error);
    static bool parseMacroFile(std::filesystem::path const& path, Macro& out, std::string& error);
    static bool saveMacroFile(std::filesystem::path const& path, Macro const& macro, std::string& error);

    ll::mod::NativeMod& mSelf;
    WorldSession mWorld{*this};
    void queueClientTask(std::function<void()> task);

    ll::event::ListenerPtr mMouseListener;
    std::thread            mServerThread;
    // F10: duplicated handle for CancelSynchronousIo so disable() is not stuck
    // behind a WriteFile to a client that stopped reading.
    std::atomic<HANDLE> mServerThreadHandle{nullptr};

    std::atomic_bool mRunning{false};
    std::atomic<std::uint64_t> mLastTickMs{0};
    // QPC timestamps for the T9 overhead metric (tick thread only).
    std::uint64_t              mTickStartQpc     = 0;
    std::uint64_t              mPrevTickStartQpc = 0;
    std::uint64_t              mPreUs            = 0;
    std::atomic_bool           mRecording{false};
    std::atomic<std::uint64_t> mTick{0};
    std::atomic<std::uint64_t> mMaxTicks{0};

    // T9: movement, look and teleport requests share one seqlock'd block so the
    // client tick can take a single lock-free snapshot. Pipe-thread writers
    // publish under mRequestWriteMutex; the tick thread never takes it on the
    // read path. Sequence numbers make each command visible exactly once, which
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
        TeleportRequest teleport;
        std::uint32_t   teleportSeq = 0;
    };

    template <typename Fn>
    void updateRequests(Fn&& fn) {
        std::lock_guard     writeLock(mRequestWriteMutex);
        std::uint32_t const seq = mRequestSeq.load(std::memory_order_relaxed);
        mRequestSeq.store(seq + 1, std::memory_order_relaxed); // odd: write in progress
        std::atomic_thread_fence(std::memory_order_release);
        fn(mRequestBlock);
        mRequestSeq.store(seq + 2, std::memory_order_release); // even: stable
    }

    [[nodiscard]] RequestBlock readRequests() const {
        for (;;) {
            std::uint32_t const before = mRequestSeq.load(std::memory_order_acquire);
            if ((before & 1u) == 0) {
                RequestBlock copy = mRequestBlock;
                std::atomic_thread_fence(std::memory_order_acquire);
                if (mRequestSeq.load(std::memory_order_relaxed) == before) {
                    return copy;
                }
            }
            std::this_thread::yield();
        }
    }

    alignas(64) std::atomic<std::uint32_t> mRequestSeq{0};
    RequestBlock mRequestBlock;
    std::mutex   mRequestWriteMutex;
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
    std::uint32_t mSeenTeleportSeq    = 0;

    bool mPrevSprint  = false;
    bool mPrevSneak   = false;
    bool mInjected    = false;
    bool mInputActive = false;
    bool mPrevJump    = false;
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
    std::atomic_bool mInjectingLook{false};
    bool             mBackgroundGuard = true;
    bool             mForeground      = true;
    // Parkour event tracking (tick thread only).
    float               mLastHealth      = 20.0f;
    bool                mWasAirborne     = false;
    float               mAirStartY       = 0.0f;
    int                 mCursorShowCalls = 0;
    bool                mPrevActionLmb   = false;
    bool                mPrevActionRmb   = false;
    std::array<bool, 9> mButtonStates{};
    std::array<bool, 9> mHandlerStates{};

    // Look requests live in RequestBlock above (mLookMutex removed in T9).

    std::atomic_bool mStateRequested{false};
    struct ChatLine {
        detail::FixedText<2048> text;
        int                     type      = 0;
        bool                    truncated = false;
    };
    // One outstanding request. Client executor owns Requested -> Ready;
    // spool owns Ready -> Idle. Neither game objects nor strings cross threads.
    enum class ChatPhase { Idle, Requested, Ready };
    std::atomic<ChatPhase>      mChatPhase{ChatPhase::Idle};
    std::array<ChatLine, 64>    mChatLines{};
    std::size_t                 mChatCount = 0, mChatTotal = 0;
    char const*                 mChatError = nullptr;
    std::atomic_bool            mInventoryRequested{false};
    std::mutex                  mEntityActionMutex;
    std::optional<EntityAction> mEntityAction;
    std::mutex                  mEcsMutex;
    std::optional<EcsRequest>   mEcsRequest;
    std::atomic<int>            mInputSource{-1};
    std::atomic<int>            mInputController{0};
    std::atomic<int>            mInputFocus{0};
    std::atomic<int>            mFlyRequest{-1};
    std::atomic<int>            mClickRequest{-1};
    std::atomic<float>          mClickReach{5.5f};
    std::atomic<int>            mSlotRequest{-1};
    // bit 1 direct flags, bit 2 queue, bit 4 handler callbacks, bit 8 toggle fallback
    std::atomic<int> mButtonMode{1 | 2 | 4};
    // Local jump impulse: bit 1 = LocalPlayer::jumpFromGround,
    // bit 2 = MobJump::setJumpPendingScale. Measured on 1.26.51.01:
    // jumpFromGround gives the exact 0.42/0.3332/... arc immediately, while
    // setJumpPendingScale alone still waits for the server (3-tick snap).
    std::atomic<int> mJumpImpulseMode{1};
    std::atomic_bool mButtonsRequested{false};

    std::mutex mEntityMutex;
    bool       mEntitiesRequested = false;
    float      mEntityRadius      = 16.0f;
    int        mEntityLimit       = 64;

    std::mutex              mBlockMutex;
    bool                    mBlocksRequested = false;
    std::vector<BlockQuery> mBlockQueries;

    std::mutex  mScanMutex;
    bool        mScanRequested = false;
    ScanRequest mScanRequest;

    std::mutex                        mCheckpointMutex;
    std::map<std::string, Checkpoint> mCheckpoints;
    // Last known feet pose, updated by the tick thread for checkpoint_save.
    std::atomic<float> mLastFeetX{0.0f};
    std::atomic<float> mLastFeetY{0.0f};
    std::atomic<float> mLastFeetZ{0.0f};
    std::atomic<float> mLastPitch{0.0f};
    std::atomic_bool   mPoseValid{false};

    // Teleport requests live in RequestBlock above (mTeleportMutex removed in T9).

    detail::TaskController     mTasks;
    std::uint64_t              mSeenTaskId = 0; // Game-thread input ownership generation.
    std::atomic<std::uint64_t> mTaskFaultId{0}; // Explicitly gated fault injection.

    // Updated on the client tick; the pipe thread uses it to resolve the
    // matching server-side player for commands and teleports.
    std::mutex  mPlayerNameMutex;
    std::string mLocalPlayerName;

    // Macro manager.
    std::mutex             mMacroMutex;
    std::shared_ptr<Macro> mMacro;
    std::string            mMacroName;
    std::atomic_bool       mMacroPlaying{false};
    std::atomic_bool       mMacroStartRequested{false};
    std::atomic_bool       mMacroLoop{false};
    // G4: replay through the event layer; off keeps the legacy GameMode path.
    bool             mMacroEventLevel = false;
    MacroTick        mPrevMacroTick{};
    bool             mHavePrevMacroTick = false;
    void             applyMacroTickEvents(LocalPlayer& player, MacroTick const& tick);
    void             releaseMacroInputs();
    std::atomic<int> mMacroIndex{0};
    std::atomic<int> mMacroStart{0};
    std::atomic<int> mMacroEnd{0};
    std::atomic_bool mMacroStopRequested{false};

    // Macro recorder.
    std::atomic_bool           mMacroRecording{false};
    std::atomic<std::uint64_t> mMacroRecordMax{0};
    std::mutex                 mRecordMutex;
    Macro                      mRecordedMacro;
    bool                       mHavePrevRotation = false;
    float                      mPrevYaw          = 0.0f;
    float                      mPrevPitch        = 0.0f;

    std::mutex              mQueueMutex;
    std::deque<std::string> mQueue;
    std::string             mCommandBuffer;
    // Pipe thread only: true after a line_too_long until the next newline.
    bool                       mDiscardPartial = false;
    std::atomic<std::uint64_t> mQueueDropped{0};
    std::atomic<std::uint64_t> mLastInternalErrorMs{0};

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
    // mLastCommand; the tick thread is the only writer of the pose fields.
    char                       mLastCommand[512]{};
    std::atomic<float>         mLastX{0.0f};
    std::atomic<float>         mLastY{0.0f};
    std::atomic<float>         mLastZ{0.0f};
    std::atomic<float>         mLastYaw{0.0f};
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
