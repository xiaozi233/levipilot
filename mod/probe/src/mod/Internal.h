#pragma once

#include "mod/ProbeMod.h"

#include "ll/api/event/EventBus.h"
#include "ll/api/event/client/ClientCancelJoinLevelEvent.h"
#include "ll/api/event/client/ClientExitLevelEvent.h"
#include "ll/api/event/client/ClientJoinLevelEvent.h"
#include "ll/api/event/client/ClientStartJoinLevelEvent.h"
#include "ll/api/event/input/MouseInputEvent.h"
#include "ll/api/memory/Hook.h"
#include "ll/api/mod/RegisterHelper.h"
#include "ll/api/service/Bedrock.h"
#include "ll/api/service/TargetedBedrock.h"
#include "ll/api/thread/ClientThreadExecutor.h"

#include "mc/client/game/ClientInstance.h"
#include "mc/client/game/IGameServerShutdown.h"
#include "mc/client/game/IGameServerStartup.h"
#include "mc/client/game/IMinecraftGame.h"
#include "mc/client/options/IOptionRegistry.h"

#include "mc/client/game/StartIntent.h"
#include "mc/client/input/ClientMoveInputHandler.h"
#include "mc/client/input/MinecraftInputHandler.h"
#include "mc/client/player/LocalPlayer.h"
#include "mc/deps/core/math/Vec2.h"
#include "mc/deps/core/math/Vec3.h"
#include "mc/deps/core/utility/MCRESULT.h"
#include "mc/deps/input/InputEventQueue.h"
#include "mc/deps/input/InputHandler.h"
#include "mc/deps/input/enums/ButtonState.h"
#include "mc/deps/input/enums/FocusImpact.h"
#include "mc/deps/shared_types/legacy/actor/ArmorSlot.h"
#include "mc/entity/components/MoveInputComponent.h"
#include "mc/entity/components/PlayerActionComponent.h"
#include "mc/input/MoveInputState.h"
#include "mc/legacy/ActorRuntimeID.h"
#include "mc/platform/UUID.h"
#include "mc/platform/brstd/bitset.h"
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
#include "mc/world/actor/Actor.h"
#include "mc/world/actor/player/AbilitiesIndex.h"
#include "mc/world/actor/player/Inventory.h"
#include "mc/world/actor/player/Player.h"
#include "mc/world/actor/player/PlayerInventory.h"
#include "mc/world/actor/provider/MobJump.h"
#include "mc/world/attribute/AttributeInstance.h"
#include "mc/world/attribute/AttributeInstanceConstRef.h"
#include "mc/world/gamemode/GameMode.h"
#include "mc/world/gamemode/InteractionResult.h"
#include "mc/world/item/Item.h"
#include "mc/world/item/ItemStack.h"
#include "mc/world/item/ItemUseMethod.h"
#include "mc/world/item/registry/ItemRegistryRef.h"
#include "mc/world/level/BlockPos.h"
#include "mc/world/level/BlockSource.h"
#include "mc/world/level/Level.h"
#include "mc/world/level/LevelSettings.h"
#include "mc/world/level/block/Block.h"
#include "mc/world/level/dimension/DimensionType.h"
#include "mc/world/level/storage/ILevelListCache.h"
#include "mc/world/level/storage/LevelSummary.h"
#include "mc/world/phys/HitResult.h"
#include "mc/world/phys/HitResultType.h"

#include <nlohmann/json.hpp>

#include <windows.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <exception>
#include <fstream>
#include <limits>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <utility>
#include <variant>

class BlockPos;
class BlockSource;
class ClientInstance;
class InputEventQueue;
class InputHandler;
class ItemStack;
class Level;
class Vec3;

namespace probe::detail {

inline constexpr wchar_t PipeName[] = L"\\\\.\\pipe\\mcbe-harness";
inline constexpr DWORD   PipeBuffer = 1 << 16;

constexpr size_t PlayerActionStartSprinting = 9;

// MoveInputState / MoveInputComponent flag indices. Kept here because both the
// input injector and the state capture read the same flags.
constexpr size_t MoveInputStateSprintDown    = static_cast<size_t>(MoveInputState::Flag::SprintDown);
constexpr size_t MoveInputStateSneakDown     = static_cast<size_t>(MoveInputState::Flag::SneakDown);
constexpr size_t MoveInputStateJumpDown      = static_cast<size_t>(MoveInputState::Flag::JumpDown);
constexpr size_t MoveInputStateUp            = static_cast<size_t>(MoveInputState::Flag::Up);
constexpr size_t MoveInputStateDown          = static_cast<size_t>(MoveInputState::Flag::Down);
constexpr size_t MoveInputStateLeft          = static_cast<size_t>(MoveInputState::Flag::Left);
constexpr size_t MoveInputStateRight         = static_cast<size_t>(MoveInputState::Flag::Right);
constexpr size_t MoveInputComponentSneaking  = static_cast<size_t>(MoveInputComponent::Flag::Sneaking);
constexpr size_t MoveInputComponentSprinting = static_cast<size_t>(MoveInputComponent::Flag::Sprinting);
constexpr size_t MoveInputComponentJumping   = static_cast<size_t>(MoveInputComponent::Flag::Jumping);

struct FaceOffset {
    int x = 0;
    int y = 0;
    int z = 0;
};

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

float       finiteOrZero(float value);
float       wrapDegrees(float value);
bool        parseBool(nlohmann::json const& value, bool& out);
bool        parseFloat(std::string const& text, float& out);
std::string formatFloat(float value);

nlohmann::json itemJson(ItemStack const& item);
bool makeItemStack(Level& level, std::string const& name, int count, int aux, ItemStack& out, std::string& error);

std::string blockName(BlockSource const& source, int x, int y, int z);
Vec3        blockCenter(BlockPos const& pos);

// Bedrock FacingID: 0=down, 1=up, 2=north(-z), 3=south(+z), 4=west(-x), 5=east(+x).
FaceOffset faceOffset(int face);
int        faceBetween(BlockPos const& from, BlockPos const& to);

InputSnapshot    readInput(ClientInstance& client);
InputHandler*    inputHandler(ClientInstance& client);
InputEventQueue* inputQueue(ClientInstance& client);
void             invokeButtonHandlers(InputHandler& handler, bool down, std::uint32_t id, ClientInstance& client);

// True when the foreground window belongs to this process (the game).
bool isGameForeground();

} // namespace probe::detail