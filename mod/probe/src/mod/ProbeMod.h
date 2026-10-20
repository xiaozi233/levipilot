#pragma once

#include "ll/api/event/Listener.h"
#include "ll/api/mod/NativeMod.h"

#include <array>
#include <atomic>
#include <cstdint>
#include <deque>
#include <filesystem>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

#include <nlohmann/json.hpp>

class ClientInstance;
class LocalPlayer;
class Level;

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

struct TeleportRequest {
    float x           = 0.0f;
    float y           = 0.0f;
    float z           = 0.0f;
    bool  hasRotation = false;
    float yaw         = 0.0f;
    float pitch       = 0.0f;
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

    // Called by the LocalPlayer::normalTick hook: before and after the original tick.
    void onPlayerTick(LocalPlayer& player);
    void afterPlayerTick(LocalPlayer& player);

private:
    void serverLoop();
    void handleLine(std::string const& line);
    void enqueue(std::string line);

    void applyControls(ClientInstance& client, LocalPlayer& player, ControlState const& state);
    void applyMacroTick(ClientInstance& client, LocalPlayer& player, MacroTick const& tick);
    void requestServerTeleport(LocalPlayer& player, TeleportRequest const& request);
    void applySlotRequest(LocalPlayer& player, int slot);
    void captureState(ClientInstance& client, LocalPlayer& player);
    void captureInventory(LocalPlayer& player);
    void applyEntityAction(LocalPlayer& player, EntityAction const& action);
    void queueInventoryWrite(
        std::string const& container,
        int                slot,
        std::string const& item,
        int                count,
        int                aux
    );
    void queueGive(std::string const& item, int count, int aux);
    void queueClearInventory();
    void captureEntities(LocalPlayer& player);
    void captureBlocks(LocalPlayer& player);
    void captureButtons(ClientInstance& client);
    void performClick(LocalPlayer& player, int button);
    void recordMacroTick(ClientInstance& client, LocalPlayer& player);
    void latchActionClicks(LocalPlayer& player);
    void finishMacro(std::string reason);

    std::filesystem::path macroDir() const;
    std::filesystem::path macroPath(std::string const& name) const;
    bool loadMacroFrom(std::string const& path, std::string const& name, std::string& error);

    static bool parseMacroJson(nlohmann::json const& value, Macro& out, std::string& error);
    static bool macroToJson(Macro const& macro, nlohmann::json& out, std::string& error);
    static bool parseMacroFile(std::filesystem::path const& path, Macro& out, std::string& error);
    static bool saveMacroFile(std::filesystem::path const& path, Macro const& macro, std::string& error);

    ll::mod::NativeMod& mSelf;

    ll::event::ListenerPtr mMouseListener;
    std::thread mServerThread;

    std::atomic_bool           mRunning{false};
    std::atomic_bool           mInWorld{false};
    std::atomic<std::uint64_t> mLastTickMs{0};
    std::atomic_bool           mRecording{false};
    std::atomic<std::uint64_t> mTick{0};
    std::atomic<std::uint64_t> mMaxTicks{0};

    // Continuous movement input. ticks < 0 means "until stopped".
    std::mutex   mInputMutex;
    ControlState mDesired;
    int          mDesiredTicks  = 0;
    bool         mJumpRequested = false;
    bool         mPrevSprint    = false;
    bool         mPrevSneak     = false;
    bool         mInjected      = false;
    bool         mInputActive   = false;
    bool         mPrevJump      = false;
    bool         mPrevMacroRmb  = false;
    bool         mPrevMacroLmb  = false;
    std::atomic_bool mMouseLeft{false};
    std::atomic_bool mMouseRight{false};
    // Latched on the press edge so a click shorter than one tick is still recorded.
    std::atomic_bool mMouseLeftLatch{false};
    std::atomic_bool mMouseRightLatch{false};
    // Diagnostics: which MouseInputEvent ids actually reach the game.
    std::atomic<std::uint64_t> mMouseEventCount{0};
    std::atomic<int>           mMouseLastAction{-1};
    std::atomic<int>           mMouseLastData{-1};
    std::array<std::atomic<int>, 8> mMouseActionCounts{};
    // Set while the mod itself calls GameMode, so playback is not re-recorded.
    std::atomic_bool mInjectingClick{false};
    bool             mPrevActionLmb = false;
    bool             mPrevActionRmb = false;
    std::array<bool, 9> mButtonStates{};
    std::array<bool, 9> mHandlerStates{};

    // Look is separate so an absolute target can be held across ticks.
    std::mutex mLookMutex;
    int        mLookTicks    = 0;
    bool       mLookRelative = true;
    float      mLookYaw      = 0.0f;
    float      mLookPitch    = 0.0f;

    std::atomic_bool mStateRequested{false};
    std::atomic_bool mInventoryRequested{false};
    std::mutex       mEntityActionMutex;
    std::optional<EntityAction> mEntityAction;
    std::atomic<int> mInputSource{-1};
    std::atomic<int> mInputController{0};
    std::atomic<int> mInputFocus{0};
    std::atomic<int> mFlyRequest{-1};
    std::atomic<int> mClickRequest{-1};
    std::atomic<float> mClickReach{5.5f};
    std::atomic<int> mSlotRequest{-1};
    // bit 1 direct flags, bit 2 queue, bit 4 handler callbacks, bit 8 toggle fallback
    std::atomic<int> mButtonMode{1 | 2 | 4};
    std::atomic_bool mButtonsRequested{false};

    std::mutex              mEntityMutex;
    bool                    mEntitiesRequested = false;
    float                   mEntityRadius      = 16.0f;
    int                     mEntityLimit       = 64;

    std::mutex              mBlockMutex;
    bool                    mBlocksRequested = false;
    std::vector<BlockQuery> mBlockQueries;

    std::mutex      mTeleportMutex;
    bool            mTeleportRequested = false;
    TeleportRequest mTeleportRequest;

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
    std::atomic<int>       mMacroIndex{0};
    std::atomic<int>       mMacroStart{0};
    std::atomic<int>       mMacroEnd{0};
    std::atomic_bool       mMacroStopRequested{false};

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
};

} // namespace probe