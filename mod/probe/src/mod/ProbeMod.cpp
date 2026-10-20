#include "mod/ProbeMod.h"

#include "ll/api/event/EventBus.h"
#include "ll/api/event/input/MouseInputEvent.h"
#include "ll/api/memory/Hook.h"
#include "ll/api/mod/RegisterHelper.h"
#include "ll/api/service/Bedrock.h"
#include "ll/api/service/TargetedBedrock.h"

#include "mc/client/game/ClientInstance.h"
#include "mc/client/input/ClientMoveInputHandler.h"
#include "mc/client/input/MinecraftInputHandler.h"
#include "mc/client/player/LocalPlayer.h"
#include "mc/deps/core/math/Vec2.h"
#include "mc/deps/core/math/Vec3.h"
#include "mc/deps/input/InputEventQueue.h"
#include "mc/deps/input/InputHandler.h"
#include "mc/deps/input/enums/ButtonState.h"
#include "mc/deps/input/enums/FocusImpact.h"
#include "mc/deps/core/utility/MCRESULT.h"
#include "mc/deps/shared_types/legacy/actor/ArmorSlot.h"
#include "mc/entity/components/MoveInputComponent.h"
#include "mc/entity/components/PlayerActionComponent.h"
#include "mc/server/ServerInstance.h"
#include "mc/server/ServerLevel.h"
#include "mc/server/commands/CommandContext.h"
#include "mc/server/commands/CommandPermissionLevel.h"
#include "mc/server/commands/CurrentCmdVersion.h"
#include "mc/server/commands/MinecraftCommands.h"
#include "mc/server/commands/PlayerCommandOrigin.h"
#include "mc/server/commands/ServerCommandOrigin.h"
#include "mc/world/ContainerID.h"
#include "mc/world/Minecraft.h"
#include "mc/world/actor/player/Inventory.h"
#include "mc/world/actor/player/PlayerInventory.h"
#include "mc/world/item/registry/ItemRegistryRef.h"
#include "mc/world/level/dimension/DimensionType.h"
#include "mc/input/MoveInputState.h"
#include "mc/platform/UUID.h"
#include "mc/platform/brstd/bitset.h"
#include "mc/world/actor/Actor.h"
#include "mc/legacy/ActorRuntimeID.h"
#include "mc/world/actor/player/AbilitiesIndex.h"
#include "mc/world/actor/player/Player.h"
#include "mc/world/actor/provider/MobJump.h"
#include "mc/world/gamemode/GameMode.h"
#include "mc/world/gamemode/InteractionResult.h"
#include "mc/world/phys/HitResult.h"
#include "mc/world/phys/HitResultType.h"
#include "mc/world/attribute/AttributeInstance.h"
#include "mc/world/attribute/AttributeInstanceConstRef.h"
#include "mc/world/item/ItemStack.h"
#include "mc/world/level/BlockPos.h"
#include "mc/world/level/BlockSource.h"
#include "mc/world/level/Level.h"
#include "mc/world/level/block/Block.h"

#include <nlohmann/json.hpp>

#include <windows.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <limits>
#include <sstream>
#include <string>
#include <string_view>
#include <utility>


namespace probe {

namespace {

constexpr wchar_t PipeName[] = L"\\\\.\\pipe\\mcbe-harness";
constexpr DWORD   PipeBuffer = 1 << 16;

constexpr size_t PlayerActionStartSprinting = 9;

// These are the game's internal button ids used by dmf-mpk for the nine macro
// columns. They are not keyboard key codes.
constexpr std::uint32_t ActionW       = 3205050568u;
constexpr std::uint32_t ActionA       = 3528728616u;
constexpr std::uint32_t ActionS       = 375632893u;
constexpr std::uint32_t ActionD       = 3949230141u;
constexpr std::uint32_t ActionSprint  = 2427380341u;
constexpr std::uint32_t ActionSneak   = 4009177245u;
constexpr std::uint32_t ActionJump    = 2406325663u;
constexpr std::uint32_t ActionRmb     = 4073882376u;
constexpr std::uint32_t ActionLmb     = 2320499196u;
constexpr std::array<std::uint32_t, 9> ActionIds{
    ActionW, ActionA, ActionS, ActionD, ActionSprint, ActionSneak, ActionJump, ActionRmb, ActionLmb
};
constexpr size_t MoveInputStateSprintDown   = static_cast<size_t>(MoveInputState::Flag::SprintDown);
constexpr size_t MoveInputStateSneakDown    = static_cast<size_t>(MoveInputState::Flag::SneakDown);
constexpr size_t MoveInputStateJumpDown     = static_cast<size_t>(MoveInputState::Flag::JumpDown);
constexpr size_t MoveInputStateUp           = static_cast<size_t>(MoveInputState::Flag::Up);
constexpr size_t MoveInputStateDown         = static_cast<size_t>(MoveInputState::Flag::Down);
constexpr size_t MoveInputStateLeft         = static_cast<size_t>(MoveInputState::Flag::Left);
constexpr size_t MoveInputStateRight        = static_cast<size_t>(MoveInputState::Flag::Right);
constexpr size_t MoveInputComponentSneaking = static_cast<size_t>(MoveInputComponent::Flag::Sneaking);
constexpr size_t MoveInputComponentSprinting = static_cast<size_t>(MoveInputComponent::Flag::Sprinting);
constexpr size_t MoveInputComponentJumping  = static_cast<size_t>(MoveInputComponent::Flag::Jumping);

constexpr std::uint64_t MaxRecordedTicks = 200000;

float finiteOrZero(float value) { return std::isfinite(value) ? value : 0.0f; }

float wrapDegrees(float value) {
    while (value > 180.0f) {
        value -= 360.0f;
    }
    while (value < -180.0f) {
        value += 360.0f;
    }
    return value;
}

bool parseBool(nlohmann::json const& value, bool& out) {
    if (value.is_boolean()) {
        out = value.get<bool>();
        return true;
    }
    if (value.is_number_integer()) {
        out = value.get<long long>() != 0;
        return true;
    }
    if (value.is_string()) {
        auto const& text = value.get_ref<std::string const&>();
        if (text == "true" || text == "1") {
            out = true;
            return true;
        }
        if (text == "false" || text == "0") {
            out = false;
            return true;
        }
    }
    return false;
}

bool parseFloat(std::string const& text, float& out) {
    if (text.empty()) {
        return false;
    }
    char* end = nullptr;
    errno     = 0;
    float value = std::strtof(text.c_str(), &end);
    if (end == text.c_str() || end == nullptr || *end != '\0' || errno == ERANGE || !std::isfinite(value)) {
        return false;
    }
    out = value;
    return true;
}

std::string formatFloat(float value) {
    char buffer[32]{};
    std::snprintf(buffer, sizeof(buffer), "%.9g", static_cast<double>(value));
    return std::string{buffer};
}

nlohmann::json itemJson(ItemStack const& item) {
    if (item.isNull()) {
        return nullptr;
    }
    return {
        {"type", item.getTypeName()},
        {"name", item.getName()},
        {"count", static_cast<int>(item.mCount)},
        {"aux", static_cast<int>(item.mAuxValue)},
    };
}

bool makeItemStack(
    Level&             level,
    std::string const& name,
    int                count,
    int                aux,
    ItemStack&         out,
    std::string&       error
) {
    if (count <= 0) {
        out = ItemStack{};
        return true;
    }
    int  itemAux = aux;
    auto item    = level.getItemRegistry().lookupByName(itemAux, std::string_view{name});
    if (!item) {
        error = "unknown_item: " + name;
        return false;
    }
    out.reinit(*item, count, itemAux);
    return true;
}

std::string blockName(BlockSource const& source, int x, int y, int z) {
    BlockPos const pos{x, y, z};
    auto const&  block = source.getBlock(pos);
    return block.getTypeName();
}

struct InputSnapshot {
    bool  w      = false;
    bool  a      = false;
    bool  s      = false;
    bool  d      = false;
    bool  sprint = false;
    bool  sneak  = false;
    bool  jump   = false;
    float moveX  = 0.0f;
    float moveY  = 0.0f;
};

InputSnapshot readInput(ClientInstance& client) {
    InputSnapshot out;
    auto*         input = ClientMoveInputHandler::getMoveInput(client);
    if (!input) {
        return out;
    }
    auto const& state = *input->mInputState->mFlagValues;
    auto const& flags = *input->mFlagValues;

    out.moveX = input->mMove->x;
    out.moveY = input->mMove->y;

    out.w = state.test(MoveInputStateUp) || out.moveY > 0.01f;
    out.s = state.test(MoveInputStateDown) || out.moveY < -0.01f;
    out.a = state.test(MoveInputStateLeft) || out.moveX < -0.01f;
    out.d = state.test(MoveInputStateRight) || out.moveX > 0.01f;
    out.sprint = flags.test(MoveInputComponentSprinting) || state.test(MoveInputStateSprintDown);
    out.sneak = flags.test(MoveInputComponentSneaking) || state.test(MoveInputStateSneakDown);
    out.jump  = flags.test(MoveInputComponentJumping) || state.test(MoveInputStateJumpDown);
    return out;
}

InputHandler* inputHandler(ClientInstance& client) {
    auto minecraftInput = client.getMinecraftInput();
    if (!minecraftInput) {
        return nullptr;
    }
    return minecraftInput->mInputHandler.get();
}

InputEventQueue* inputQueue(ClientInstance& client) {
    auto* handler = inputHandler(client);
    return handler ? handler->mInputEventQueue.get() : nullptr;
}

void invokeButtonHandlers(InputHandler& handler, bool down, std::uint32_t id, ClientInstance& client) {
    auto& map   = down ? *handler.mButtonDownHandlerMap : *handler.mButtonUpHandlerMap;
    auto  range = map.equal_range(id);
    for (auto it = range.first; it != range.second; ++it) {
        auto const& entry = it->second;
        if (entry.second) {
            entry.second(FocusImpact::Neutral, client);
        }
    }
}

} // namespace

ProbeMod& ProbeMod::getInstance() {
    static ProbeMod instance;
    return instance;
}

bool ProbeMod::load() {
    getSelf().getLogger().info("harness: loading");
    return true;
}

bool ProbeMod::enable() {
    if (mRunning.exchange(true)) {
        return true;
    }

    mMouseListener = ll::event::EventBus::getInstance().emplaceListener<ll::event::input::MouseInputEvent>(
        [this](auto& event) {
            // MouseAction: ActionMove=0, ActionLeft=1, ActionRight=2, DataDown=1.
            int const action = static_cast<int>(event.actionButtonId());
            int const data   = static_cast<int>(event.buttonData());
            mMouseEventCount.fetch_add(1, std::memory_order_relaxed);
            mMouseLastAction = action;
            mMouseLastData   = data;
            if (action >= 0 && action < static_cast<int>(mMouseActionCounts.size())) {
                mMouseActionCounts[static_cast<size_t>(action)].fetch_add(
                    1,
                    std::memory_order_relaxed
                );
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
        }
    );

    std::error_code ec;
    std::filesystem::create_directories(macroDir(), ec);

    mServerThread = std::thread([this] { serverLoop(); });
    getSelf().getLogger().info("harness: enabled, pipe=\\\\.\\pipe\\mcbe-harness");
    return true;
}

bool ProbeMod::disable() {
    if (!mRunning.exchange(false)) {
        return true;
    }

    mMacroRecording = false;
    mMacroPlaying   = false;

    if (mMouseListener) {
        ll::event::EventBus::getInstance().removeListener<ll::event::input::MouseInputEvent>(mMouseListener);
        mMouseListener.reset();
    }

    // Wake a server thread blocked in ConnectNamedPipe, then let it exit.
    HANDLE wake = CreateFileW(PipeName, GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, 0, nullptr);
    if (wake != INVALID_HANDLE_VALUE) {
        CloseHandle(wake);
    }
    if (mServerThread.joinable()) {
        mServerThread.join();
    }

    {
        std::lock_guard lock(mQueueMutex);
        mQueue.clear();
    }
    mRecording = false;
    mInWorld   = false;
    getSelf().getLogger().info("harness: disabled");
    return true;
}

void ProbeMod::onPlayerTick(LocalPlayer& player) {
    if (!mRunning.load(std::memory_order_relaxed)) {
        return;
    }
    auto client = ll::service::getClientInstance();
    if (!client || client->getLocalPlayer() != &player) {
        mInWorld = false;
        return;
    }
    mInWorld    = true;
    mLastTickMs = GetTickCount64();

    {
        auto const& name = player.getRealName();
        std::lock_guard lock(mPlayerNameMutex);
        if (mLocalPlayerName != name) {
            mLocalPlayerName = name;
        }
    }

    latchActionClicks(player);

    // Queued teleport goes through the integrated server so the position is
    // authoritative; a client-only _setPos is corrected back on the next tick.
    {
        std::lock_guard lock(mTeleportMutex);
        if (mTeleportRequested) {
            mTeleportRequested = false;
            requestServerTeleport(player, mTeleportRequest);
        }
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
            server->queueForServerThread([name, enabled] {
                auto  minecraft = ll::service::bedrock::getMinecraft();
                Level* level    = minecraft ? minecraft->getLevel() : nullptr;
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
            getSelf().getLogger().info("harness: macro started at tick {}, range [{}, {})", mMacroIndex.load(), mMacroStart.load(), mMacroEnd.load());
        } else {
            enqueue(R"({"event":"error","reason":"macro_not_loaded"})");
        }
    }

    mMacroStopRequested.exchange(false);

    if (mMacroPlaying.load(std::memory_order_relaxed)) {
        std::shared_ptr<Macro> macro;
        {
            std::lock_guard lock(mMacroMutex);
            macro = mMacro;
        }
        int index = mMacroIndex.load(std::memory_order_relaxed);
        int end   = mMacroEnd.load(std::memory_order_relaxed);
        if (!macro || index < 0 || index >= static_cast<int>(macro->size()) || index >= end) {
            if (macro && mMacroLoop.load() && !macro->empty()) {
                index = mMacroStart.load();
                mMacroIndex.store(index);
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
    {
        std::lock_guard lock(mInputMutex);
        if (mDesiredTicks != 0) {
            state = mDesired;
            apply = state.active || state.jump;
            if (mDesiredTicks > 0) {
                --mDesiredTicks;
                if (mDesiredTicks == 0) {
                    mDesired = ControlState{};
                }
            }
        }
        if (mJumpRequested) {
            mJumpRequested = false;
            state.jump     = true;
            state.active   = true;
            apply          = true;
        }
    }
    if (apply) {
        applyControls(*client, player, state);
    } else {
        applyControls(*client, player, ControlState{});
    }

    // Absolute look targets are applied after movement so the rotation matches the current tick.
    bool  lookActive = false;
    bool  lookRel    = true;
    float lookYaw    = 0.0f;
    float lookPitch  = 0.0f;
    {
        std::lock_guard lock(mLookMutex);
        lookActive = mLookTicks != 0;
        lookRel    = mLookRelative;
        lookYaw    = mLookYaw;
        lookPitch  = mLookPitch;
        if (mLookTicks > 0) {
            --mLookTicks;
        }
    }
    if (lookActive) {
        auto const rot = player.getRotation();
        // _applyTurnDelta uses the opposite pitch sign from ActorRotation.x.
        float dy = lookRel ? lookYaw : wrapDegrees(lookYaw - rot.y);
        float dp = lookRel ? -lookPitch : (rot.x - lookPitch);
        if (std::fabs(dy) > 1.0e-5f || std::fabs(dp) > 1.0e-5f) {
            player._applyTurnDelta(Vec2{dp, dy});
        }
    }
}

void ProbeMod::afterPlayerTick(LocalPlayer& player) {
    if (!mRunning.load(std::memory_order_relaxed)) {
        return;
    }
    auto client = ll::service::getClientInstance();
    if (!client || client->getLocalPlayer() != &player) {
        return;
    }
    auto tickIndex = mTick.fetch_add(1, std::memory_order_relaxed) + 1;

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

    if (mMacroRecording.load(std::memory_order_relaxed)) {
        recordMacroTick(*client, player);
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
    j["on_ground"]         = player.isOnGround();
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

void ProbeMod::applySlotRequest(LocalPlayer& player, int slot) {
    slot = std::clamp(slot, 0, 8);

    bool applied = false;
    if (player.mInventory) {
        applied = player.mInventory->selectSlot(slot, ContainerID::Inventory);
    }
    if (!applied) {
        player.setSelectedSlot(slot);
        applied = player.getSelectedItemSlot() == slot;
    }

    nlohmann::json result;
    result["event"]   = "select_slot_done";
    result["slot"]    = slot;
    result["applied"] = player.getSelectedItemSlot();
    result["ok"]      = player.getSelectedItemSlot() == slot;
    auto const& item  = player.getSelectedItem();
    if (!item.isNull()) {
        result["item"] = {
            {"type", item.getTypeName()},
            {"count", static_cast<int>(item.mCount)},
        };
    } else {
        result["item"] = nullptr;
    }
    enqueue(result.dump());
}

void ProbeMod::requestServerTeleport(LocalPlayer& player, TeleportRequest const& request) {
    auto server = ll::service::bedrock::getServerInstance();
    if (!server) {
        enqueue(R"({"event":"error","reason":"teleport_no_server_instance"})");
        return;
    }

    // The integrated server owns actor positions. Moving the server-side player
    // emits a proper teleport packet back to this client, so the position sticks;
    // writing the client player only is corrected on the next server tick.
    std::string name = player.getRealName();
    auto const  uuid = player.getUuid();
    Vec3 const  target{request.x, request.y, request.z};

    server->queueForServerThread([this, name, uuid, request, target] {
        auto  minecraft = ll::service::bedrock::getMinecraft();
        Level* level    = minecraft ? minecraft->getLevel() : nullptr;
        if (level == nullptr) {
            enqueue(R"({"event":"error","reason":"teleport_no_server_level"})");
            return;
        }

        Player* serverPlayer = level->getPlayer(uuid);
        if (serverPlayer == nullptr && !name.empty()) {
            serverPlayer = level->getPlayer(name);
        }
        if (serverPlayer == nullptr) {
            enqueue(R"({"event":"error","reason":"teleport_player_not_found"})");
            return;
        }

        Vec2 const rotation
            = request.hasRotation ? Vec2{request.pitch, request.yaw} : serverPlayer->getRotation();
        serverPlayer->teleport(target, serverPlayer->getDimensionId(), rotation);

        nlohmann::json result;
        result["event"]     = "teleported";
        result["source"]    = "server";
        result["x"]         = request.x;
        result["y"]         = request.y;
        result["z"]         = request.z;
        result["has_rotation"] = request.hasRotation;
        enqueue(result.dump());
    });
}

void ProbeMod::performClick(LocalPlayer& player, int button) {
    mInjectingClick = true;
    struct InjectGuard {
        std::atomic_bool& flag;
        ~InjectGuard() { flag = false; }
    } guard{mInjectingClick};

    nlohmann::json result;
    result["event"]  = "click_result";
    result["button"] = button == 1 ? "left" : "right";
    result["game_mode"] = static_cast<bool>(player.mGameMode);

    if (!player.mGameMode) {
        enqueue(result.dump());
        return;
    }
    GameMode& gm  = *player.mGameMode;
    HitResult hit = player.traceRay(mClickReach.load(), true, true);
    result["hit_type"] = static_cast<int>(hit.mType);
    result["block"]    = {hit.mBlock.x, hit.mBlock.y, hit.mBlock.z};
    result["face"]     = static_cast<int>(hit.mFacing);
    result["hit_pos"]  = {hit.mPos.x, hit.mPos.y, hit.mPos.z};

    if (button == 1) {
        if (hit.mType == HitResultType::Tile) {
            bool destroyed = false;
            gm.startDestroyBlock(hit.mBlock, hit.mFacing, destroyed);
            if (!destroyed) {
                gm.destroyBlock(hit.mBlock, hit.mFacing);
            }
        } else if (hit.mType == HitResultType::Entity && hit.getEntity()) {
            gm.attack(*hit.getEntity(), hit.mPos);
        }
        enqueue(result.dump());
        return;
    }

    auto& item = const_cast<ItemStack&>(player.getSelectedItem());
    if (hit.mType == HitResultType::Tile) {
        auto const& block = player.getDimensionBlockSourceConst().getBlock(hit.mBlock);
        gm.useItemOn(item, hit.mBlock, hit.mFacing, hit.mPos, HandSlot::Mainhand, &block, true);
    } else if (hit.mType == HitResultType::Entity && hit.getEntity()) {
        // Empty-hand entity use dereferences a null item inside the game.
        if (!player.getSelectedItem().isNull()) {
            gm.interact(*hit.getEntity(), hit.mPos, HandSlot::Mainhand);
        }
    } else {
        gm.useItem(item, HandSlot::Mainhand);
    }
    enqueue(result.dump());
}

void ProbeMod::applyControls(ClientInstance& client, LocalPlayer& player, ControlState const& state) {
    if (state.active) {
        ClientMoveInputHandler::_updateClientMoveVector(state.strafe, state.forward, FocusImpact::Neutral, client);
        mInjected = true;
    } else if (mInjected) {
        ClientMoveInputHandler::_updateClientMoveVector(0.0f, 0.0f, FocusImpact::Neutral, client);
        mInjected = false;
    }

    bool const engaged = state.active;
    bool const jumpNow = engaged && state.jump;
    bool const jumpEdge = jumpNow && !mPrevJump;
    int const  mode     = mButtonMode.load();

    if ((mode & 1) && (engaged || mInputActive)) {
        // Directly maintain the local MoveInputComponent flags. Only touch them
        // while our own control is active; otherwise we would overwrite real
        // keyboard/mouse input every tick (this is what broke manual jump/fly).
        if (auto* input = ClientMoveInputHandler::getMoveInput(client)) {
            auto& flags      = *input->mFlagValues;
            auto& stateFlags = *input->mInputState->mFlagValues;
            flags.set(MoveInputComponentSneaking, engaged && state.sneak);
            flags.set(MoveInputComponentSprinting, engaged && state.sprint);
            flags.set(MoveInputComponentJumping, jumpNow);
            stateFlags.set(MoveInputStateSneakDown, engaged && state.sneak);
            stateFlags.set(MoveInputStateSprintDown, engaged && state.sprint);
            stateFlags.set(MoveInputStateJumpDown, jumpNow);
            stateFlags.set(24, !jumpNow && mPrevJump); // JumpInputWasReleased
            stateFlags.set(25, jumpEdge);              // JumpInputWasPressed
            stateFlags.set(26, jumpNow);               // JumpInputCurrentlyDown
        }
        if (engaged) {
            player.setSneaking(state.sneak);
        } else {
            player.setSneaking(false);
        }
        mInputActive = engaged;
    }

    if (mode & 2) {
        if (auto* queue = inputQueue(client)) {
            bool const buttons[9] = {
                false, false, false, false, state.sprint, state.sneak, state.jump, state.rmb, state.lmb
            };
            for (size_t i = 4; i < 9; ++i) {
                if (buttons[i] != mButtonStates[i]) {
                    queue->enqueueButton(
                        ActionIds[i],
                        buttons[i] ? ButtonState::Down : ButtonState::Up,
                        false,
                        static_cast<FocusImpact>(mInputFocus.load()),
                        mInputController.load(),
                        false,
                        0.0f,
                        mInputSource.load()
                    );
                    mButtonStates[i] = buttons[i];
                }
            }
        }
    }

    if (mode & 4) {
        if (auto* handler = inputHandler(client)) {
            bool const buttons[9] = {
                false, false, false, false, state.sprint, state.sneak, state.jump, state.rmb, state.lmb
            };
            for (size_t i = 4; i < 9; ++i) {
                if (buttons[i] != mHandlerStates[i]) {
                    invokeButtonHandlers(*handler, buttons[i], ActionIds[i], client);
                    mHandlerStates[i] = buttons[i];
                }
            }
        }
    }

    if (mode & 8) {
        if (state.sprint != mPrevSprint) {
            if (player.isSprinting() != state.sprint) {
                ClientMoveInputHandler::_toggleClientSprint(FocusImpact::Neutral, client);
            }
        }
        if (state.sneak != mPrevSneak) {
            player.setSneaking(state.sneak);
        }
        if (jumpEdge && player.isOnGround()) {
            player.jumpFromGround();
        }
    }

    mPrevJump = jumpNow;
    mPrevSprint = state.sprint;
    mPrevSneak  = state.sneak;

    if (state.yawDelta != 0.0f || state.pitchDelta != 0.0f) {
        player._applyTurnDelta(Vec2{state.pitchDelta, state.yawDelta});
    }
}

void ProbeMod::applyMacroTick(ClientInstance& client, LocalPlayer& player, MacroTick const& tick) {
    if (tick.rmb != mPrevMacroRmb) {
        if (tick.rmb) {
            performClick(player, 2);
        }
        mPrevMacroRmb = tick.rmb;
    }
    if (tick.lmb != mPrevMacroLmb) {
        if (tick.lmb) {
            performClick(player, 1);
        }
        mPrevMacroLmb = tick.lmb;
    }

    ControlState state;
    state.forward    = tick.forward();
    state.strafe     = tick.strafe();
    state.sprint     = tick.sprint;
    state.sneak      = tick.sneak;
    state.jump       = tick.jump;
    // Clicks are applied directly above (GameMode), not through input flags.
    state.rmb        = false;
    state.lmb        = false;
    state.active     = tick.w || tick.a || tick.s || tick.d || tick.sprint || tick.sneak || tick.jump;
    state.yawDelta   = tick.yaw;
    state.pitchDelta = tick.pitch;
    applyControls(client, player, state);
}

void ProbeMod::captureState(ClientInstance& client, LocalPlayer& player) {
    auto const& pos = player.getPosition();
    auto        vel = player.getVelocity();
    auto const  rot = player.getRotation();
    auto        in  = readInput(client);

    bool actionSprint = false;
    if (auto action = player.getEntityContext().tryGetComponent<PlayerActionComponent>()) {
        actionSprint = action->mPlayerActions->test(PlayerActionStartSprinting);
    }

    nlohmann::json j;
    j["event"]        = "state";
    j["in_world"]     = true;
    j["tick"]         = mTick.load();
    j["x"]            = finiteOrZero(static_cast<float>(pos.x));
    j["y"]            = finiteOrZero(static_cast<float>(pos.y));
    j["z"]            = finiteOrZero(static_cast<float>(pos.z));
    auto const feet   = player.getFeetPos();
    auto const head   = player.getHeadPos();
    j["feet_x"]       = finiteOrZero(static_cast<float>(feet.x));
    j["feet_y"]       = finiteOrZero(static_cast<float>(feet.y));
    j["feet_z"]       = finiteOrZero(static_cast<float>(feet.z));
    j["head_x"]       = finiteOrZero(static_cast<float>(head.x));
    j["head_y"]       = finiteOrZero(static_cast<float>(head.y));
    j["head_z"]       = finiteOrZero(static_cast<float>(head.z));
    j["vx"]           = finiteOrZero(static_cast<float>(vel.x));
    j["vy"]           = finiteOrZero(static_cast<float>(vel.y));
    j["vz"]           = finiteOrZero(static_cast<float>(vel.z));
    j["yaw"]          = finiteOrZero(rot.y);
    j["pitch"]        = finiteOrZero(rot.x);
    j["on_ground"]    = player.isOnGround();
    j["sprinting"]    = player.isSprinting();
    j["sneaking"]     = player.isSneaking();
    j["swimming"]     = player.isSwimming();
    j["flying"]       = player.isFlying();
    j["in_water"]     = player.isInWater();
    j["alive"]        = player.isAlive();
    j["health"]       = player.getHealth();
    j["max_health"]   = player.getMaxHealth();
    j["gamemode"]     = static_cast<int>(player.getPlayerGameType());
    j["dimension"]    = static_cast<int>(player.getDimensionId());
    j["name"]         = player.getRealName();
    j["uuid"]         = player.getUuid().asString();
    j["selected_slot"] = player.getSelectedItemSlot();

    auto attrValue = [&](Attribute const& attribute) -> std::optional<float> {
        auto instance = player.getAttribute(attribute);
        if (!instance.mPtr) {
            return std::nullopt;
        }
        return instance.mPtr->mCurrentValue;
    };
    if (auto value = attrValue(Player::HUNGER())) {
        j["hunger"] = finiteOrZero(*value);
    }
    if (auto value = attrValue(Player::SATURATION())) {
        j["saturation"] = finiteOrZero(*value);
    }
    if (auto value = attrValue(Player::EXHAUSTION())) {
        j["exhaustion"] = finiteOrZero(*value);
    }
    if (auto value = attrValue(Player::LEVEL())) {
        j["xp_level"] = finiteOrZero(*value);
    }
    if (auto value = attrValue(Player::EXPERIENCE())) {
        j["xp_progress"] = finiteOrZero(*value);
    }

    j["input"] = {
        {"w", in.w},
        {"a", in.a},
        {"s", in.s},
        {"d", in.d},
        {"sprint", in.sprint},
        {"sneak", in.sneak},
        {"jump", in.jump},
        {"move_x", finiteOrZero(in.moveX)},
        {"move_y", finiteOrZero(in.moveY)},
        {"action_sprint", actionSprint},
    };

    auto const& item = player.getSelectedItem();
    if (!item.isNull()) {
        j["item"] = {
            {"type", item.getTypeName()},
            {"name", item.getName()},
            {"count", static_cast<int>(item.mCount)},
            {"aux", static_cast<int>(item.mAuxValue)},
        };
    } else {
        j["item"] = nullptr;
    }

    try {
        auto const& source = player.getDimensionBlockSourceConst();
        int const   bx     = static_cast<int>(std::floor(feet.x));
        int const   by     = static_cast<int>(std::floor(feet.y));
        int const   bz     = static_cast<int>(std::floor(feet.z));
        int const   hx     = static_cast<int>(std::floor(head.x));
        int const   hy     = static_cast<int>(std::floor(head.y));
        int const   hz     = static_cast<int>(std::floor(head.z));
        j["block_feet"]    = blockName(source, bx, by, bz);
        j["block_below"]   = blockName(source, bx, by - 1, bz);
        j["block_head"]    = blockName(source, hx, hy, hz);
        j["block_above"]   = blockName(source, hx, hy + 1, hz);
    } catch (...) {
        // A block query can fail while chunks are unloading; state still remains useful.
    }

    enqueue(j.dump());
}

void ProbeMod::captureInventory(LocalPlayer& player) {
    nlohmann::json out;
    out["event"]         = "inventory";
    out["selected_slot"] = player.getSelectedItemSlot();

    auto& inventory = player.getInventory();
    int   size      = inventory.getContainerSize();
    out["size"]     = size;
    out["slots"]    = nlohmann::json::array();
    for (int i = 0; i < size; ++i) {
        out["slots"].push_back({
            {"slot", i},
            {"item", itemJson(inventory.getItem(i))},
        });
    }

    out["armor"] = nlohmann::json::array();
    for (int i = 0; i < static_cast<int>(SharedTypes::Legacy::ArmorSlot::HumanoidCount); ++i) {
        out["armor"].push_back({
            {"slot", i},
            {"item", itemJson(player.getArmor(static_cast<SharedTypes::Legacy::ArmorSlot>(i)))},
        });
    }
    out["offhand"] = itemJson(player.getOffhandSlot());

    enqueue(out.dump());
}

void ProbeMod::applyEntityAction(LocalPlayer& player, EntityAction const& action) {
    auto level = ll::service::bedrock::getLevel();
    if (!level) {
        enqueue(R"({"event":"error","reason":"level_unavailable"})");
        return;
    }

    Actor* target = nullptr;
    for (auto* actor : level->getRuntimeActorList()) {
        if (actor != nullptr
            && static_cast<std::uint64_t>(actor->getRuntimeID()) == action.runtimeId) {
            target = actor;
            break;
        }
    }
    if (target == nullptr) {
        enqueue(R"({"event":"error","reason":"entity_not_found"})");
        return;
    }

    if (action.kind == EntityAction::Kind::Info) {
        auto const& pos = target->getPosition();
        auto        vel = target->getVelocity();
        auto const  rot = target->getRotation();
        auto const& origin = player.getPosition();
        float       dx = static_cast<float>(pos.x - origin.x);
        float       dy = static_cast<float>(pos.y - origin.y);
        float       dz = static_cast<float>(pos.z - origin.z);

        nlohmann::json out;
        out["event"]       = "entity_info";
        out["runtime_id"]  = action.runtimeId;
        out["type"]        = target->getTypeName();
        out["name"]        = target->getNameTag();
        out["is_player"]   = target->isPlayer();
        out["is_alive"]    = target->isAlive();
        out["health"]      = target->getHealth();
        out["x"]           = finiteOrZero(static_cast<float>(pos.x));
        out["y"]           = finiteOrZero(static_cast<float>(pos.y));
        out["z"]           = finiteOrZero(static_cast<float>(pos.z));
        out["vx"]          = finiteOrZero(static_cast<float>(vel.x));
        out["vy"]          = finiteOrZero(static_cast<float>(vel.y));
        out["vz"]          = finiteOrZero(static_cast<float>(vel.z));
        out["yaw"]         = finiteOrZero(rot.y);
        out["pitch"]       = finiteOrZero(rot.x);
        out["distance"]    = finiteOrZero(std::sqrt(dx * dx + dy * dy + dz * dz));
        out["on_ground"]   = target->isOnGround();
        enqueue(out.dump());
        return;
    }

    if (!player.mGameMode) {
        enqueue(R"({"event":"error","reason":"gamemode_unavailable"})");
        return;
    }

    if (action.kind == EntityAction::Kind::Attack) {
        std::string const type         = target->getTypeName();
        float const       healthBefore = target->getHealth();
        bool              ok           = player.mGameMode->attack(*target, target->getHeadPos());
        enqueue(nlohmann::json{
            {"event", "entity_attacked"},
            {"runtime_id", action.runtimeId},
            {"ok", ok},
            {"type", type},
            {"health_before", healthBefore},
        }.dump());
        return;
    }

    if (action.kind == EntityAction::Kind::Interact) {
        // Calling Player::interact / GameMode::interact directly crashes this
        // build with a null internal item context. Instead, aim at the entity
        // and press the game's own use button for a few ticks.
        Vec3 const eye = player.getHeadPos();
        Vec3 const aim = target->getHeadPos();
        double     dx  = aim.x - eye.x;
        double     dy  = aim.y - eye.y;
        double     dz  = aim.z - eye.z;
        double     horiz = std::sqrt(dx * dx + dz * dz);
        constexpr double RadToDeg = 57.29577951308232;
        float yaw   = static_cast<float>(std::atan2(-dx, dz) * RadToDeg);
        float pitch = static_cast<float>(-std::atan2(dy, horiz) * RadToDeg);

        auto const rot    = player.getRotation();
        float      dyaw   = wrapDegrees(yaw - rot.y);
        float      dpitch = rot.x - pitch;
        if (std::fabs(dyaw) > 1.0e-5f || std::fabs(dpitch) > 1.0e-5f) {
            player._applyTurnDelta(Vec2{dpitch, dyaw});
        }
        {
            std::lock_guard lock(mInputMutex);
            mDesired      = ControlState{};
            mDesired.rmb  = true;
            mDesired.active = true;
            mDesiredTicks = 3;
        }

        enqueue(nlohmann::json{
            {"event", "entity_interacted"},
            {"runtime_id", action.runtimeId},
            {"ok", true},
            {"method", "input"},
            {"type", target->getTypeName()},
        }.dump());
        return;
    }

    if (action.kind == EntityAction::Kind::LookAt) {
        Vec3 const eye = player.getHeadPos();
        Vec3 const aim = target->getHeadPos();
        double     dx  = aim.x - eye.x;
        double     dy  = aim.y - eye.y;
        double     dz  = aim.z - eye.z;
        double     horiz = std::sqrt(dx * dx + dz * dz);
        constexpr double RadToDeg = 57.29577951308232;
        float yaw   = static_cast<float>(std::atan2(-dx, dz) * RadToDeg);
        float pitch = static_cast<float>(-std::atan2(dy, horiz) * RadToDeg);

        auto const rot = player.getRotation();
        float      dyaw   = wrapDegrees(yaw - rot.y);
        float      dpitch = rot.x - pitch;
        if (std::fabs(dyaw) > 1.0e-5f || std::fabs(dpitch) > 1.0e-5f) {
            player._applyTurnDelta(Vec2{dpitch, dyaw});
        }

        enqueue(nlohmann::json{
            {"event", "look_at_done"},
            {"runtime_id", action.runtimeId},
            {"yaw", yaw},
            {"pitch", pitch},
        }.dump());
    }
}

void ProbeMod::queueInventoryWrite(
    std::string const& container,
    int                slot,
    std::string const& itemName,
    int                count,
    int                aux
) {
    auto server = ll::service::bedrock::getServerInstance();
    if (!server) {
        enqueue(R"({"event":"error","reason":"inventory_no_server_instance"})");
        return;
    }
    std::string name;
    {
        std::lock_guard lock(mPlayerNameMutex);
        name = mLocalPlayerName;
    }
    if (name.empty()) {
        enqueue(R"({"event":"error","reason":"inventory_player_unknown"})");
        return;
    }

    server->queueForServerThread([this, name, container, slot, itemName, count, aux] {
        auto   minecraft = ll::service::bedrock::getMinecraft();
        Level* level     = minecraft ? minecraft->getLevel() : nullptr;
        Player* player   = (level != nullptr) ? level->getPlayer(name) : nullptr;
        if (player == nullptr) {
            enqueue(R"({"event":"error","reason":"inventory_player_not_found"})");
            return;
        }

        ItemStack   stack;
        std::string error;
        if (!makeItemStack(*level, itemName, count, aux, stack, error)) {
            enqueue(nlohmann::json{{"event", "error"}, {"reason", error}}.dump());
            return;
        }

        std::string applied = container;
        if (container == "armor") {
            if (slot < 0 || slot >= static_cast<int>(SharedTypes::Legacy::ArmorSlot::HumanoidCount)) {
                enqueue(R"({"event":"error","reason":"armor_slot_out_of_range"})");
                return;
            }
            player->setArmor(static_cast<SharedTypes::Legacy::ArmorSlot>(slot), stack);
        } else if (container == "offhand") {
            player->setOffhandSlot(stack);
            applied = "offhand";
        } else {
            int size = player->getInventory().getContainerSize();
            if (slot < 0 || slot >= size) {
                enqueue(R"({"event":"error","reason":"inventory_slot_out_of_range"})");
                return;
            }
            player->getInventory().setItem(slot, stack);
            applied = "inventory";
        }
        player->sendInventory(true);

        enqueue(nlohmann::json{
            {"event", "inventory_set"},
            {"container", applied},
            {"slot", slot},
            {"item", itemJson(stack)},
        }.dump());
    });
}

void ProbeMod::queueGive(std::string const& itemName, int count, int aux) {
    auto server = ll::service::bedrock::getServerInstance();
    if (!server) {
        enqueue(R"({"event":"error","reason":"inventory_no_server_instance"})");
        return;
    }
    std::string name;
    {
        std::lock_guard lock(mPlayerNameMutex);
        name = mLocalPlayerName;
    }
    if (name.empty()) {
        enqueue(R"({"event":"error","reason":"inventory_player_unknown"})");
        return;
    }

    server->queueForServerThread([this, name, itemName, count, aux] {
        auto   minecraft = ll::service::bedrock::getMinecraft();
        Level* level     = minecraft ? minecraft->getLevel() : nullptr;
        Player* player   = (level != nullptr) ? level->getPlayer(name) : nullptr;
        if (player == nullptr) {
            enqueue(R"({"event":"error","reason":"inventory_player_not_found"})");
            return;
        }

        ItemStack   stack;
        std::string error;
        if (!makeItemStack(*level, itemName, count, aux, stack, error)) {
            enqueue(nlohmann::json{{"event", "error"}, {"reason", error}}.dump());
            return;
        }
        bool ok = player->getInventory().add(stack);
        player->sendInventory(true);

        enqueue(nlohmann::json{
            {"event", "inventory_given"},
            {"ok", ok},
            {"requested", count},
            {"remaining", static_cast<int>(stack.mCount)},
            {"item", itemJson(stack)},
        }.dump());
    });
}

void ProbeMod::queueClearInventory() {
    auto server = ll::service::bedrock::getServerInstance();
    if (!server) {
        enqueue(R"({"event":"error","reason":"inventory_no_server_instance"})");
        return;
    }
    std::string name;
    {
        std::lock_guard lock(mPlayerNameMutex);
        name = mLocalPlayerName;
    }
    if (name.empty()) {
        enqueue(R"({"event":"error","reason":"inventory_player_unknown"})");
        return;
    }

    server->queueForServerThread([this, name] {
        auto   minecraft = ll::service::bedrock::getMinecraft();
        Level* level     = minecraft ? minecraft->getLevel() : nullptr;
        Player* player   = (level != nullptr) ? level->getPlayer(name) : nullptr;
        if (player == nullptr) {
            enqueue(R"({"event":"error","reason":"inventory_player_not_found"})");
            return;
        }

        ItemStack empty;
        auto&     inventory = player->getInventory();
        int       size      = inventory.getContainerSize();
        for (int i = 0; i < size; ++i) {
            inventory.setItem(i, empty);
        }
        for (int i = 0; i < static_cast<int>(SharedTypes::Legacy::ArmorSlot::HumanoidCount); ++i) {
            player->setArmor(static_cast<SharedTypes::Legacy::ArmorSlot>(i), empty);
        }
        player->setOffhandSlot(empty);
        player->sendInventory(true);

        enqueue(nlohmann::json{{"event", "inventory_cleared"}, {"size", size}}.dump());
    });
}

void ProbeMod::captureEntities(LocalPlayer& player) {
    auto level = ll::service::bedrock::getLevel();
    if (!level) {
        enqueue(R"({"event":"error","reason":"level_unavailable"})");
        return;
    }

    auto const origin = player.getPosition();
    float      radius = 16.0f;
    int        limit  = 64;
    {
        std::lock_guard lock(mEntityMutex);
        radius = mEntityRadius;
        limit  = mEntityLimit;
    }

    nlohmann::json out;
    out["event"]  = "entities";
    out["radius"] = radius;
    out["entities"] = nlohmann::json::array();

    int count = 0;
    for (auto* actor : level->getRuntimeActorList()) {
        if (actor == nullptr || !actor->isAlive()) {
            continue;
        }
        if (actor->isPlayer() && actor->getRuntimeID() == player.getRuntimeID()) {
            continue;
        }
        auto const& pos = actor->getPosition();
        float       dx  = static_cast<float>(pos.x - origin.x);
        float       dy  = static_cast<float>(pos.y - origin.y);
        float       dz  = static_cast<float>(pos.z - origin.z);
        float       distance = std::sqrt(dx * dx + dy * dy + dz * dz);
        if (distance > radius) {
            continue;
        }
        out["entities"].push_back({
            {"type", actor->getTypeName()},
            {"name", actor->getNameTag()},
            {"is_player", actor->isPlayer()},
            {"runtime_id", static_cast<std::uint64_t>(actor->getRuntimeID())},
            {"x", finiteOrZero(static_cast<float>(pos.x))},
            {"y", finiteOrZero(static_cast<float>(pos.y))},
            {"z", finiteOrZero(static_cast<float>(pos.z))},
            {"distance", finiteOrZero(distance)},
            {"health", actor->getHealth()},
        });
        if (++count >= limit) {
            break;
        }
    }
    out["count"] = count;
    enqueue(out.dump());
}

void ProbeMod::captureButtons(ClientInstance& client) {
    nlohmann::json out;
    out["event"] = "buttons";
    out["ids"]   = nlohmann::json::array();
    auto minecraftInput = client.getMinecraftInput();
    if (minecraftInput) {
        auto* handler = minecraftInput->mInputHandler.get();
        if (handler) {
            for (auto const& [id, handlerPair] : *handler->mButtonDownHandlerMap) {
                (void)handlerPair;
                out["ids"].push_back(id);
                if (out["ids"].size() >= 512) {
                    break;
                }
            }
            out["queue"] = handler->mInputEventQueue.get() != nullptr;
        }
    }
    enqueue(out.dump());
}

void ProbeMod::captureBlocks(LocalPlayer& player) {
    std::vector<BlockQuery> queries;
    {
        std::lock_guard lock(mBlockMutex);
        queries.swap(mBlockQueries);
    }
    if (queries.empty()) {
        enqueue(R"({"event":"blocks","blocks":[]})");
        return;
    }

    auto const& source = player.getDimensionBlockSourceConst();
    nlohmann::json out;
    out["event"] = "blocks";
    out["blocks"] = nlohmann::json::array();
    for (auto const& query : queries) {
        out["blocks"].push_back({
            {"x", query.x},
            {"y", query.y},
            {"z", query.z},
            {"name", blockName(source, query.x, query.y, query.z)},
        });
    }
    enqueue(out.dump());
}

void ProbeMod::latchActionClicks(LocalPlayer& player) {
    if (mInjectingClick.load(std::memory_order_relaxed)) {
        return;
    }
    auto action = player.getEntityContext().tryGetComponent<PlayerActionComponent>();
    if (!action) {
        return;
    }
    auto const& bits = *action->mPlayerActions;
    // PlayerActionType: StartDestroyBlock=0, InteractWithBlock=25,
    // PredictDestroyBlock=26, ContinueDestroyBlock=27, StartItemUseOn=28,
    // MissedSwing=31, StartUsingItem=37. Only the rising edge becomes a click,
    // otherwise mining would keep the LMB column true for the whole hold.
    bool const lmb = bits.test(0) || bits.test(26) || bits.test(27) || bits.test(31);
    bool const rmb = bits.test(25) || bits.test(28) || bits.test(37);
    if (lmb && !mPrevActionLmb) {
        mMouseLeftLatch = true;
    }
    if (rmb && !mPrevActionRmb) {
        mMouseRightLatch = true;
    }
    mPrevActionLmb = lmb;
    mPrevActionRmb = rmb;
}

void ProbeMod::recordMacroTick(ClientInstance& client, LocalPlayer& player) {
    auto in  = readInput(client);
    auto rot = player.getRotation();

    MacroTick row;
    row.w      = in.w;
    row.a      = in.a;
    row.s      = in.s;
    row.d      = in.d;
    row.sprint = in.sprint;
    row.sneak  = in.sneak;
    row.jump   = in.jump;
    row.rmb    = mMouseRight.load() || mMouseRightLatch.exchange(false);
    row.lmb    = mMouseLeft.load() || mMouseLeftLatch.exchange(false);

    if (mHavePrevRotation) {
        float yaw   = wrapDegrees(rot.y - mPrevYaw);
        // Store the same sign _applyTurnDelta consumes, so playback reproduces
        // the recorded rotation (and dmf's mouse-delta convention).
        float pitch = -(rot.x - mPrevPitch);
        row.yaw     = std::fabs(yaw) < 0.001f ? 0.0f : yaw;
        row.pitch   = std::fabs(pitch) < 0.001f ? 0.0f : pitch;
    }
    mPrevYaw          = rot.y;
    mPrevPitch        = rot.x;
    mHavePrevRotation = true;

    bool limitReached = false;
    {
        std::lock_guard lock(mRecordMutex);
        if (mRecordedMacro.size() < MaxRecordedTicks) {
            mRecordedMacro.push_back(row);
        }
        auto max = mMacroRecordMax.load(std::memory_order_relaxed);
        if (max != 0 && mRecordedMacro.size() >= max) {
            mMacroRecording = false;
            limitReached    = true;
        }
    }
    if (limitReached) {
        enqueue(R"({"event":"macro_rec_limit"})");
    }
}

void ProbeMod::finishMacro(std::string reason) {
    if (!mMacroPlaying.exchange(false)) {
        return;
    }
    std::string name;
    {
        std::lock_guard lock(mMacroMutex);
        name = mMacroName;
    }
    nlohmann::json j;
    j["event"]  = "macro_done";
    j["name"]   = name;
    j["reason"] = std::move(reason);
    j["index"]  = mMacroIndex.load();
    enqueue(j.dump());
}

std::filesystem::path ProbeMod::macroDir() const { return getSelf().getModDir() / "macros"; }

std::filesystem::path ProbeMod::macroPath(std::string const& name) const {
    return macroDir() / (name + ".json");
}

bool ProbeMod::loadMacroFrom(std::string const& path, std::string const& name, std::string& error) {
    Macro macro;
    auto  file = std::filesystem::path{path};
    if (!std::filesystem::exists(file)) {
        if (path.find('/') != std::string::npos || path.find('\\') != std::string::npos) {
            error = "macro file does not exist: " + path;
            return false;
        }
        file = macroPath(path);
    }
    if (!parseMacroFile(file, macro, error)) {
        return false;
    }
    if (macro.empty()) {
        error = "macro is empty";
        return false;
    }

    std::lock_guard lock(mMacroMutex);
    mMacro     = std::make_shared<Macro>(std::move(macro));
    mMacroName = name.empty() ? file.stem().string() : name;
    return true;
}

bool ProbeMod::parseMacroJson(nlohmann::json const& value, Macro& out, std::string& error) {
    if (!value.is_array()) {
        error = "macro must be a top-level array";
        return false;
    }
    Macro macro;
    macro.reserve(value.size());
    for (size_t i = 0; i < value.size(); ++i) {
        auto const& row = value[i];
        if (!row.is_array()) {
            error = "tick " + std::to_string(i) + " is not an array";
            return false;
        }
        if (row.size() != 9 && row.size() != 11) {
            error = "tick " + std::to_string(i) + " must contain 9 or 11 strings";
            return false;
        }

        MacroTick tick;
        bool*     buttons[9] = {
            &tick.w, &tick.a, &tick.s, &tick.d, &tick.sprint, &tick.sneak, &tick.jump, &tick.rmb, &tick.lmb
        };
        for (size_t b = 0; b < 9; ++b) {
            if (!parseBool(row[b], *buttons[b])) {
                error = "invalid button value in tick " + std::to_string(i);
                return false;
            }
        }
        if (row.size() == 11) {
            if (!row[9].is_string() || !row[10].is_string()) {
                error = "yaw/pitch must be strings in tick " + std::to_string(i);
                return false;
            }
            if (!parseFloat(row[9].get<std::string>(), tick.yaw)
                || !parseFloat(row[10].get<std::string>(), tick.pitch)) {
                error = "invalid yaw/pitch in tick " + std::to_string(i);
                return false;
            }
        }
        macro.push_back(tick);
    }
    out = std::move(macro);
    return true;
}

bool ProbeMod::macroToJson(Macro const& macro, nlohmann::json& out, std::string& error) {
    out = nlohmann::json::array();
    for (auto const& tick : macro) {
        if (!std::isfinite(tick.yaw) || !std::isfinite(tick.pitch)) {
            error = "macro angle must be finite";
            return false;
        }
        out.push_back(nlohmann::json::array({
            tick.w ? "true" : "false",
            tick.a ? "true" : "false",
            tick.s ? "true" : "false",
            tick.d ? "true" : "false",
            tick.sprint ? "true" : "false",
            tick.sneak ? "true" : "false",
            tick.jump ? "true" : "false",
            tick.rmb ? "true" : "false",
            tick.lmb ? "true" : "false",
            formatFloat(tick.yaw),
            formatFloat(tick.pitch),
        }));
    }
    return true;
}

bool ProbeMod::parseMacroFile(std::filesystem::path const& path, Macro& out, std::string& error) {
    std::ifstream file(path, std::ios::binary);
    if (!file) {
        error = "cannot open " + path.string();
        return false;
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    auto value = nlohmann::json::parse(buffer.str(), nullptr, false);
    if (value.is_discarded()) {
        error = "invalid JSON in " + path.string();
        return false;
    }
    return parseMacroJson(value, out, error);
}

bool ProbeMod::saveMacroFile(std::filesystem::path const& path, Macro const& macro, std::string& error) {
    nlohmann::json value;
    if (!macroToJson(macro, value, error)) {
        return false;
    }
    std::error_code ec;
    std::filesystem::create_directories(path.parent_path(), ec);
    std::ofstream file(path, std::ios::binary | std::ios::trunc);
    if (!file) {
        error = "cannot write " + path.string();
        return false;
    }
    file << value.dump(2);
    return true;
}

void ProbeMod::handleLine(std::string const& line) {
    auto j = nlohmann::json::parse(line, nullptr, false);
    if (j.is_discarded()) {
        enqueue(R"({"event":"error","reason":"bad_json"})");
        return;
    }

    auto cmd = j.value("cmd", std::string{});
    if (cmd == "ping") {
        enqueue(R"({"event":"pong","mod":"probe","protocol":2})");
        return;
    }
    if (cmd == "diag") {
        auto mc     = ll::service::bedrock::getMinecraft();
        auto server = ll::service::bedrock::getServerInstance();
        auto level  = ll::service::bedrock::getLevel();
        nlohmann::json out;
        out["event"]           = "diag";
        out["minecraft"]       = mc.has_value();
        out["server_instance"] = server.has_value();
        out["level"]           = level.has_value();
        out["server_level"]    = mc.has_value() && mc->getLevel() != nullptr;
        out["commands"]        = mc.has_value() && static_cast<bool>(mc->mCommands);
        enqueue(out.dump());
        return;
    }
    if (cmd == "mouse") {
        nlohmann::json out;
        out["event"]       = "mouse";
        out["count"]       = mMouseEventCount.load();
        out["last_action"] = mMouseLastAction.load();
        out["last_data"]   = mMouseLastData.load();
        out["left"]        = mMouseLeft.load();
        out["right"]       = mMouseRight.load();
        out["counts"]      = nlohmann::json::array();
        for (auto const& count : mMouseActionCounts) {
            out["counts"].push_back(count.load());
        }
        enqueue(out.dump());
        return;
    }
    if (cmd == "mouse_clear") {
        mMouseEventCount = 0;
        mMouseLastAction = -1;
        mMouseLastData   = -1;
        for (auto& count : mMouseActionCounts) {
            count = 0;
        }
        mMouseLeftLatch  = false;
        mMouseRightLatch = false;
        mMouseLeft       = false;
        mMouseRight      = false;
        mPrevActionLmb   = false;
        mPrevActionRmb   = false;
        enqueue(R"({"event":"mouse_cleared"})");
        return;
    }
    if (cmd == "command") {
        std::string command = j.value("command", std::string{});
        if (!command.empty() && command.front() == '/') {
            command.erase(0, 1); // MinecraftCommands expects the bare command text
        }
        if (command.empty()) {
            enqueue(R"({"event":"error","reason":"empty_command"})");
            return;
        }
        auto server = ll::service::bedrock::getServerInstance();
        if (!server) {
            enqueue(R"({"event":"error","reason":"command_no_server_instance"})");
            return;
        }
        std::string name = j.value("player", std::string{});
        if (name.empty()) {
            std::lock_guard lock(mPlayerNameMutex);
            name = mLocalPlayerName;
        }
        server->queueForServerThread([this, command, name] {
            auto  minecraft = ll::service::bedrock::getMinecraft();
            Level* level    = minecraft ? minecraft->getLevel() : nullptr;
            if (level == nullptr || !minecraft->mCommands) {
                enqueue(R"({"event":"error","reason":"command_no_server_level"})");
                return;
            }
            // A player origin keeps @s / ~ / rotation semantics intact; the
            // console origin is only a fallback when the name is unknown.
            std::unique_ptr<CommandOrigin> origin;
            Player*                        serverPlayer = name.empty() ? nullptr : level->getPlayer(name);
            if (serverPlayer != nullptr) {
                origin = std::make_unique<PlayerCommandOrigin>(*level, serverPlayer->getOrCreateUniqueID());
            } else {
                origin = std::make_unique<ServerCommandOrigin>(
                    "harness",
                    level->asServer(),
                    CommandPermissionLevel::Owner,
                    DimensionType{0}
                );
            }
            CommandContext context(
                command,
                std::move(origin),
                static_cast<int>(CurrentCmdVersion::Latest)
            );
            auto result = minecraft->mCommands->executeCommand(context, true);

            nlohmann::json out;
            out["event"]   = "command_done";
            out["command"] = command;
            out["success"] = static_cast<bool>(result.mSuccess);
            out["code"]    = static_cast<int>(result.mCode);
            enqueue(out.dump());
        });
        return;
    }
    if (cmd == "start") {
        mTick      = 0;
        mMaxTicks  = j.value("max_ticks", std::uint64_t{0});
        mRecording = true;
        enqueue(R"({"event":"started"})");
        return;
    }
    if (cmd == "stop") {
        mRecording = false;
        enqueue(R"({"event":"stopped","reason":"client"})");
        return;
    }
    if (cmd == "move") {
        {
            std::lock_guard lock(mInputMutex);
            mDesired.forward = j.value("forward", 0.0f);
            mDesired.strafe  = j.value("strafe", 0.0f);
            mDesired.active  = true;
            mDesiredTicks    = j.value("ticks", 1);
        }
        enqueue(R"({"event":"moving"})");
        return;
    }
    if (cmd == "stopmove") {
        {
            std::lock_guard lock(mInputMutex);
            mDesired      = ControlState{};
            mDesiredTicks = 0;
        }
        enqueue(R"({"event":"stopping"})");
        return;
    }
    if (cmd == "set") {
        {
            std::lock_guard lock(mInputMutex);
            mDesired.forward = j.value("forward", 0.0f);
            mDesired.strafe  = j.value("strafe", 0.0f);
            mDesired.sprint  = j.value("sprint", false);
            mDesired.sneak   = j.value("sneak", false);
            mDesired.jump    = j.value("jump", false);
            mDesired.rmb     = j.value("rmb", false);
            mDesired.lmb     = j.value("lmb", false);
            mDesired.active  = true;
            mDesiredTicks    = j.value("ticks", 1);
        }
        enqueue(R"({"event":"set_ok"})");
        return;
    }
    if (cmd == "jump") {
        {
            std::lock_guard lock(mInputMutex);
            mJumpRequested = true;
        }
        enqueue(R"({"event":"jump_ok"})");
        return;
    }
    if (cmd == "look") {
        {
            std::lock_guard lock(mLookMutex);
            mLookRelative = j.value("relative", true);
            mLookYaw      = j.value("yaw", 0.0f);
            mLookPitch    = j.value("pitch", 0.0f);
            mLookTicks    = j.value("ticks", 1);
        }
        enqueue(R"({"event":"look_ok"})");
        return;
    }
    if (cmd == "state") {
        auto const now = GetTickCount64();
        if (!mInWorld.load() || (mLastTickMs.load() != 0 && now - mLastTickMs.load() > 1000)) {
            enqueue(R"({"event":"state","in_world":false})");
        } else {
            mStateRequested = true;
        }
        return;
    }
    if (cmd == "input_cfg") {
        mInputSource.store(j.value("source", -1));
        mInputController.store(j.value("controller", 0));
        mInputFocus.store(j.value("focus", 0));
        if (j.contains("mode")) {
            mButtonMode.store(j.value("mode", 7));
            mButtonStates.fill(false);
            mHandlerStates.fill(false);
            mPrevJump = false;
        }
        enqueue(R"({"event":"input_cfg_ok"})");
        return;
    }
    if (cmd == "buttons") {
        mButtonsRequested = true;
        return;
    }
    if (cmd == "entities") {
        {
            std::lock_guard lock(mEntityMutex);
            mEntityRadius      = j.value("radius", 16.0f);
            mEntityLimit       = std::clamp(j.value("limit", 64), 1, 512);
            mEntitiesRequested = true;
        }
        return;
    }
    if (cmd == "blocks") {
        std::vector<BlockQuery> queries;
        if (j.contains("positions") && j["positions"].is_array()) {
            for (auto const& pos : j["positions"]) {
                if (!pos.is_array() || pos.size() != 3) {
                    continue;
                }
                queries.push_back(BlockQuery{
                    static_cast<int>(std::floor(pos[0].get<double>())),
                    static_cast<int>(std::floor(pos[1].get<double>())),
                    static_cast<int>(std::floor(pos[2].get<double>())),
                });
                if (queries.size() >= 256) {
                    break;
                }
            }
        }
        {
            std::lock_guard lock(mBlockMutex);
            mBlockQueries     = std::move(queries);
            mBlocksRequested  = true;
        }
        return;
    }
    if (cmd == "select_slot") {
        mSlotRequest.store(std::clamp(j.value("slot", 0), 0, 8));
        enqueue(R"({"event":"select_slot_ok"})");
        return;
    }
    if (cmd == "inventory") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
        } else {
            mInventoryRequested = true;
        }
        return;
    }
    if (cmd == "set_item" || cmd == "clear_item") {
        auto const container = j.value("container", std::string{"inventory"});
        if (container != "inventory" && container != "armor" && container != "offhand") {
            enqueue(R"({"event":"error","reason":"bad_container"})");
            return;
        }
        bool const clear = cmd == "clear_item";
        queueInventoryWrite(
            container,
            j.value("slot", 0),
            clear ? std::string{} : j.value("item", std::string{}),
            clear ? 0 : j.value("count", 1),
            clear ? 0 : j.value("aux", 0)
        );
        return;
    }
    if (cmd == "give") {
        queueGive(j.value("item", std::string{}), j.value("count", 1), j.value("aux", 0));
        return;
    }
    if (cmd == "clear_inventory") {
        queueClearInventory();
        return;
    }
    if (cmd == "entity" || cmd == "attack_entity" || cmd == "interact_entity"
        || cmd == "look_at_entity") {
        EntityAction action;
        if (cmd == "entity") {
            action.kind = EntityAction::Kind::Info;
        } else if (cmd == "attack_entity") {
            action.kind = EntityAction::Kind::Attack;
        } else if (cmd == "interact_entity") {
            action.kind = EntityAction::Kind::Interact;
        } else {
            action.kind = EntityAction::Kind::LookAt;
        }
        action.runtimeId = j.value("runtime_id", std::uint64_t{0});
        {
            std::lock_guard lock(mEntityActionMutex);
            mEntityAction = action;
        }
        return;
    }
    if (cmd == "click") {
        auto const button = j.value("button", std::string{"left"});
        mClickReach.store(j.value("reach", 5.5f));
        mClickRequest.store(button == "right" ? 2 : 1);
        return;
    }
    if (cmd == "fly") {
        mFlyRequest.store(j.value("enabled", true) ? 1 : 0);
        enqueue(R"({"event":"fly_ok"})");
        return;
    }
    if (cmd == "teleport") {
        {
            std::lock_guard lock(mTeleportMutex);
            mTeleportRequest.x     = j.value("x", 0.0f);
            mTeleportRequest.y     = j.value("y", 0.0f);
            mTeleportRequest.z     = j.value("z", 0.0f);
            mTeleportRequest.hasRotation = j.contains("yaw") || j.contains("pitch");
            mTeleportRequest.yaw   = j.value("yaw", 0.0f);
            mTeleportRequest.pitch = j.value("pitch", 0.0f);
            mTeleportRequested     = true;
        }
        // "teleported" is emitted from the server thread once the move is applied.
        return;
    }
    if (cmd == "macro_load") {
        std::string name = j.value("name", std::string{"default"});
        std::string path = j.value("path", std::string{});
        if (path.empty()) {
            path = macroPath(name).string();
        }
        std::string error;
        if (!loadMacroFrom(path, name, error)) {
            enqueue(nlohmann::json{{"event", "error"}, {"reason", error}}.dump());
            return;
        }
        size_t count = 0;
        {
            std::lock_guard lock(mMacroMutex);
            count = mMacro ? mMacro->size() : 0;
        }
        enqueue(nlohmann::json{
            {"event", "macro_loaded"},
            {"name", name},
            {"ticks", count},
            {"path", path},
        }.dump());
        return;
    }
    if (cmd == "macro_run") {
        std::shared_ptr<Macro> macro;
        {
            std::lock_guard lock(mMacroMutex);
            macro = mMacro;
        }
        if (!macro || macro->empty()) {
            enqueue(R"({"event":"error","reason":"no_macro_loaded"})");
            return;
        }
        if (mMacroPlaying.load()) {
            enqueue(R"({"event":"error","reason":"macro_already_playing"})");
            return;
        }
        int start = std::clamp(j.value("start", 0), 0, static_cast<int>(macro->size()) - 1);
        int end   = j.value("end", static_cast<int>(macro->size()));
        end       = std::clamp(end, start + 1, static_cast<int>(macro->size()));
        mMacroStart.store(start);
        mMacroEnd.store(end);
        mMacroIndex.store(start);
        mMacroLoop.store(j.value("loop", false));
        mMacroStartRequested = true;
        enqueue(nlohmann::json{
            {"event", "macro_started"},
            {"start", start},
            {"end", end},
            {"ticks", end - start},
            {"loop", mMacroLoop.load()},
        }.dump());
        return;
    }
    if (cmd == "macro_stop") {
        mMacroPlaying       = false;
        mMacroStopRequested = true;
        enqueue(R"({"event":"macro_stopped","reason":"client"})");
        return;
    }
    if (cmd == "macro_status") {
        nlohmann::json out;
        out["event"]           = "macro_status";
        out["in_world"]        = mInWorld.load();
        out["playing"]         = mMacroPlaying.load();
        out["index"]           = mMacroIndex.load();
        out["recording"]       = mMacroRecording.load();
        out["recorded_ticks"]  = 0;
        {
            std::lock_guard lock(mMacroMutex);
            out["loaded"] = static_cast<bool>(mMacro);
            out["name"]   = mMacroName;
            out["ticks"]  = mMacro ? mMacro->size() : 0;
        }
        {
            std::lock_guard lock(mRecordMutex);
            out["recorded_ticks"] = mRecordedMacro.size();
        }
        enqueue(out.dump());
        return;
    }
    if (cmd == "macro_list") {
        nlohmann::json out;
        out["event"] = "macro_list";
        out["macros"] = nlohmann::json::array();
        std::error_code ec;
        for (auto const& entry : std::filesystem::directory_iterator(macroDir(), ec)) {
            if (entry.is_regular_file() && entry.path().extension() == ".json") {
                out["macros"].push_back(entry.path().stem().string());
            }
        }
        enqueue(out.dump());
        return;
    }
    if (cmd == "macro_rec_start") {
        {
            std::lock_guard lock(mRecordMutex);
            mRecordedMacro.clear();
        }
        mHavePrevRotation = false;
        mMouseLeftLatch   = false;
        mMouseRightLatch  = false;
        mMouseLeft        = false;
        mMouseRight       = false;
        mPrevActionLmb    = false;
        mPrevActionRmb    = false;
        mMacroRecordMax   = j.value("max_ticks", std::uint64_t{0});
        mMacroRecording   = true;
        enqueue(R"({"event":"macro_rec_started"})");
        return;
    }
    if (cmd == "macro_rec_stop") {
        std::string name = j.value("name", std::string{"recorded"});
        Macro       rows;
        {
            std::lock_guard lock(mRecordMutex);
            rows = mRecordedMacro;
            mRecordedMacro.clear();
        }
        mMacroRecording = false;
        if (rows.empty()) {
            enqueue(R"({"event":"error","reason":"no_recorded_ticks"})");
            return;
        }
        std::string error;
        auto        path = macroPath(name);
        if (!saveMacroFile(path, rows, error)) {
            enqueue(nlohmann::json{{"event", "error"}, {"reason", error}}.dump());
            return;
        }
        enqueue(nlohmann::json{
            {"event", "macro_rec_saved"},
            {"name", name},
            {"ticks", rows.size()},
            {"path", path.string()},
        }.dump());
        return;
    }
    if (cmd == "macro_save") {
        Macro       rows;
        std::string error;
        if (!j.contains("ticks") || !parseMacroJson(j["ticks"], rows, error)) {
            enqueue(nlohmann::json{{"event", "error"}, {"reason", error.empty() ? "missing ticks" : error}}.dump());
            return;
        }
        std::string name = j.value("name", std::string{"saved"});
        auto        path = macroPath(name);
        if (!saveMacroFile(path, rows, error)) {
            enqueue(nlohmann::json{{"event", "error"}, {"reason", error}}.dump());
            return;
        }
        enqueue(nlohmann::json{
            {"event", "macro_saved"},
            {"name", name},
            {"ticks", rows.size()},
            {"path", path.string()},
        }.dump());
        return;
    }
    if (cmd == "status") {
        nlohmann::json out;
        out["event"]    = "status";
        out["in_world"] = mInWorld.load();
        out["recording"] = mRecording.load();
        out["tick"]      = mTick.load();
        out["macro_playing"] = mMacroPlaying.load();
        out["macro_index"]   = mMacroIndex.load();
        out["macro_recording"] = mMacroRecording.load();
        enqueue(out.dump());
        return;
    }
    enqueue(R"({"event":"error","reason":"unknown_cmd"})");
}

void ProbeMod::enqueue(std::string line) {
    std::lock_guard lock(mQueueMutex);
    if (mQueue.size() >= 20000) {
        mQueue.pop_front();
    }
    mQueue.push_back(std::move(line));
}

void ProbeMod::serverLoop() {
    HANDLE pipe      = INVALID_HANDLE_VALUE;
    bool   connected = false;

    while (mRunning.load(std::memory_order_relaxed)) {
        if (pipe == INVALID_HANDLE_VALUE) {
            pipe = CreateNamedPipeW(
                PipeName,
                PIPE_ACCESS_DUPLEX,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
                1,
                PipeBuffer,
                PipeBuffer,
                0,
                nullptr
            );
            if (pipe == INVALID_HANDLE_VALUE) {
                Sleep(500);
                continue;
            }
            mCommandBuffer.clear();
            connected = false;
        }

        if (!connected) {
            BOOL ok = ConnectNamedPipe(pipe, nullptr);
            if (!mRunning.load(std::memory_order_relaxed)) {
                break;
            }
            if (!ok && GetLastError() != ERROR_PIPE_CONNECTED) {
                CloseHandle(pipe);
                pipe = INVALID_HANDLE_VALUE;
                continue;
            }
            connected = true;
            {
                std::lock_guard lock(mQueueMutex);
                mQueue.clear();
            }
            auto const hello = std::string(R"({"event":"hello","protocol":2})") + "\n";
            DWORD      written = 0;
            WriteFile(pipe, hello.data(), static_cast<DWORD>(hello.size()), &written, nullptr);
        }

        DWORD available = 0;
        if (!PeekNamedPipe(pipe, nullptr, 0, nullptr, &available, nullptr)) {
            connected = false;
            DisconnectNamedPipe(pipe);
            CloseHandle(pipe);
            pipe = INVALID_HANDLE_VALUE;
            continue;
        }

        if (available > 0) {
            std::array<char, 4096> buf{};
            DWORD                  toRead = available < buf.size() ? available : static_cast<DWORD>(buf.size());
            DWORD                  read   = 0;
            if (!ReadFile(pipe, buf.data(), toRead, &read, nullptr) || read == 0) {
                connected = false;
                DisconnectNamedPipe(pipe);
                CloseHandle(pipe);
                pipe = INVALID_HANDLE_VALUE;
                continue;
            }
            mCommandBuffer.append(buf.data(), read);

            size_t pos = 0;
            while ((pos = mCommandBuffer.find('\n')) != std::string::npos) {
                std::string line = mCommandBuffer.substr(0, pos);
                mCommandBuffer.erase(0, pos + 1);
                if (!line.empty() && line.back() == '\r') {
                    line.pop_back();
                }
                if (!line.empty()) {
                    handleLine(line);
                }
            }
        }

        std::deque<std::string> batch;
        {
            std::lock_guard lock(mQueueMutex);
            batch.swap(mQueue);
        }

        bool failed = false;
        for (auto& line : batch) {
            std::string out     = line + "\n";
            DWORD       written = 0;
            if (!WriteFile(pipe, out.data(), static_cast<DWORD>(out.size()), &written, nullptr)) {
                failed = true;
                break;
            }
        }

        if (failed) {
            connected = false;
            DisconnectNamedPipe(pipe);
            CloseHandle(pipe);
            pipe = INVALID_HANDLE_VALUE;
            continue;
        }

        Sleep(1);
    }

    if (pipe != INVALID_HANDLE_VALUE) {
        if (connected) {
            DisconnectNamedPipe(pipe);
        }
        CloseHandle(pipe);
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


LL_REGISTER_MOD(probe::ProbeMod, probe::ProbeMod::getInstance());