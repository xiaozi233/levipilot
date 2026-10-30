#pragma once

#include "mod/AtomicRing.h"
#include "mod/ProbeMod.h"

#include "ll/api/event/EventBus.h"
#include "ll/api/event/client/ClientCancelJoinLevelEvent.h"
#include "ll/api/event/client/ClientExitLevelEvent.h"
#include "ll/api/event/client/ClientJoinLevelEvent.h"
#include "ll/api/event/client/ClientStartJoinLevelEvent.h"
#include "ll/api/event/input/KeyInputEvent.h"
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
#include "mc/client/gui/GuiData.h"
#include "mc/client/options/IOptionRegistry.h"
#include "mc/client/input/KeyboardRemappingLayout.h"
#include "mc/client/input/Keymapping.h"

#include "mc/client/game/StartIntent.h"
#include "mc/client/input/ClientMoveInputHandler.h"
#include "mc/client/input/MinecraftInputHandler.h"
#include "mc/client/player/LocalPlayer.h"
#include "mc/deps/application/AppPlatform.h"
#include "mc/deps/application/gamecore/Platform_GameCore.h"
#include "mc/deps/core/math/Vec2.h"
#include "mc/deps/core/math/Vec3.h"
#include "mc/deps/core/utility/BinaryStream.h"
#include "mc/deps/core/utility/MCRESULT.h"
#include "mc/deps/input/InputEventQueue.h"
#include "mc/deps/input/InputHandler.h"
#include "mc/deps/input/Keyboard.h"
#include "mc/deps/input/KeyboardAction.h"
#include "mc/deps/input/Mouse.h"
#include "mc/deps/input/MouseDevice.h"
#include "mc/deps/input/enums/ButtonState.h"
#include "mc/deps/input/enums/FocusImpact.h"
#include "mc/deps/input/win/HIDControllerGameCoreDesktop.h"
#include "mc/deps/shared_types/legacy/actor/ArmorSlot.h"
#include "mc/entity/components/MoveInputComponent.h"
#include "mc/entity/components/PlayerActionComponent.h"
#include "mc/input/MoveInputState.h"
#include "mc/legacy/ActorRuntimeID.h"
#include "mc/network/BatchedNetworkPeer.h"
#include "mc/network/LocalNetworkPeer.h"
#include "mc/network/LoopbackPacketSender.h"
#include "mc/network/MinecraftPacketIds.h"
#include "mc/network/NetworkConnection.h"
#include "mc/network/Packet.h"
#include "mc/network/packet/CommandRequestPacket.h"
#include "mc/network/packet/CommandRequestPacketPayload.h"
#include "mc/platform/UUID.h"
#include "mc/platform/brstd/bitset.h"
#include "mc/server/ServerInstance.h"
#include "mc/server/ServerLevel.h"
#include "mc/server/commands/CommandContext.h"
#include "mc/server/commands/CommandOriginData.h"
#include "mc/server/commands/CommandOriginType.h"
#include "mc/server/commands/CommandPermissionLevel.h"
#include "mc/server/commands/CurrentCmdVersion.h"
#include "mc/server/commands/MinecraftCommands.h"
#include "mc/server/commands/PlayerCommandOrigin.h"
#include "mc/server/commands/ServerCommandOrigin.h"
#include "mc/world/ContainerID.h"
#include "mc/world/Minecraft.h"
#include "mc/world/actor/Actor.h"
#include "mc/world/actor/player/AbilitiesIndex.h"
#include "mc/world/actor/player/LayeredAbilities.h"
#include "mc/world/actor/player/Inventory.h"
#include "mc/world/actor/player/Player.h"
#include "mc/world/actor/player/PlayerInventory.h"
#include "mc/world/actor/provider/MobJump.h"
#include "mc/world/attribute/AttributeInstance.h"
#include "mc/world/attribute/AttributeInstanceConstRef.h"
#include "mc/world/effect/MobEffectInstance.h"
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
#include "mc/world/level/storage/LevelData.h"
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

namespace probe {

// Set while a pipe command is being dispatched; the crash VEH uses it to tell
// a per-request fault from a genuine process crash.
extern thread_local bool gDispatchSehActive;

} // namespace probe

namespace probe::detail {

// SEH codes that mean "this request touched something invalid". C++ exceptions
// are deliberately excluded.
inline bool isFatalSehCode(DWORD code) {
    switch (code) {
    case EXCEPTION_ACCESS_VIOLATION:
    case EXCEPTION_ILLEGAL_INSTRUCTION:
    case EXCEPTION_PRIV_INSTRUCTION:
    case EXCEPTION_IN_PAGE_ERROR:
    case EXCEPTION_DATATYPE_MISALIGNMENT:
    case EXCEPTION_STACK_OVERFLOW:
        return true;
    default:
        return false;
    }
}

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
// Shared aim math (degrees, Bedrock convention): yaw from a horizontal delta,
// pitch from a vertical delta over horizontal distance.
float       yawTo(double dx, double dz);
float       pitchTo(double dy, double horiz);
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

// ---------------------------------------------------------------------------
// T2: generic memory primitives.
//
// All reads/writes go through ReadProcessMemory/WriteProcessMemory on the
// current process. Unlike a raw dereference this reports failure for an
// unmapped address instead of raising an access violation, so a bad address
// from a script can never take the game down. Field offsets live in
// Memory.cpp, not in the command handlers.
// ---------------------------------------------------------------------------
bool readableRange(std::uint64_t addr, std::size_t len);
bool writableRange(std::uint64_t addr, std::size_t len);
bool readMemory(std::uint64_t addr, void* dst, std::size_t len);
bool writeMemory(std::uint64_t addr, void const* src, std::size_t len);

std::string hexEncode(void const* data, std::size_t len);
bool        parseHexBytes(std::string const& text, std::vector<std::uint8_t>& out);
bool        parseAddress(nlohmann::json const& value, std::uint64_t& out);
bool        parseSize(nlohmann::json const& value, std::uint32_t& out);

// Numeric command parameters. nlohmann parses "1e400" into inf without an
// error; a non-finite value would spin wrapDegrees forever or hit UB in a
// double->int cast on the tick path, so every number that reaches a request
// block or a block coordinate goes through these first.
bool numberJson(nlohmann::json const& j, char const* key, double low, double high, double fallback, double& out);
bool numberValue(nlohmann::json const& value, double low, double high, double& out);

// World-space sanity bounds for block/command coordinates. A double outside
// these is rejected as a bad coordinate instead of being cast to int.
inline constexpr double CoordHorizonLimit = 3.0e7;
inline constexpr double CoordHeightLow    = -4096.0;
inline constexpr double CoordHeightHigh   = 4096.0;

// kind: "raw" | "block" | "legacy".
nlohmann::json probeMemory(std::uint64_t addr, std::string const& kind, std::size_t len);

// Reads the main-menu Xbox Live sign-in state through OreUI. Returns false when
// the state is unavailable (no client / model not created), never throws.
bool queryXblSignedIn(bool& signedIn);

// ---------------------------------------------------------------------------
// T3: hardware write watchpoints (debug registers DR0-DR3).
//
// The hit path runs in a vectored exception handler and only writes a fixed
// record into a preallocated ring; JSON/formatting happens later on the pipe
// thread in watchDrainJson.
// ---------------------------------------------------------------------------
extern std::atomic<std::uint64_t> gWatchTick;
extern std::atomic<std::uint32_t> gTickThreadId;
extern std::atomic<std::uint32_t> gServerThreadId;

void watchInit();
void watchShutdown();
nlohmann::json
watchInstall(
    std::uint64_t      addr,
    std::uint32_t      size,
    bool               readWrite,
    std::string const& thread,
    std::string const& tag,
    std::uint32_t      depth
);
nlohmann::json watchRemove(int id);
bool           watchRemoveAll();
nlohmann::json watchListJson();
nlohmann::json watchDrainJson(std::uint64_t since, std::size_t limit);
std::size_t    watchCount();
std::uint64_t  watchTotalHits();
std::uint64_t  watchDroppedHits();

// ---------------------------------------------------------------------------
// T4: fixed-size binary tick ring.
//
// The writer runs on the game tick and only stores a 64-byte POD; it does no
// formatting, allocation or locking. Dumping/decoding happens on the pipe
// thread. JSON `start` recording remains available as the compatibility path.
// ---------------------------------------------------------------------------
constexpr std::size_t RecordCapacity = 1 << 19; // 32 MiB payload + 4 MiB stamps, ~7 hours at 20 TPS
constexpr std::size_t RecordSize     = 64;

struct RecordEntry {
    std::uint64_t tick        = 0;
    float         x           = 0.0f;
    float         y           = 0.0f;
    float         z           = 0.0f;
    float         vx          = 0.0f;
    float         vy          = 0.0f;
    float         vz          = 0.0f;
    float         yaw         = 0.0f;
    float         pitch       = 0.0f;
    float         feetY       = 0.0f;
    std::uint32_t flags       = 0;
    std::uint32_t blockBelow  = 0; // FNV-1a 32 of the type name
    std::uint32_t blockFeet   = 0;
    std::uint32_t blockHead   = 0;
    std::uint32_t healthMilli = 0;
};

static_assert(sizeof(RecordEntry) == RecordSize, "record entry must stay 64 bytes");

// Flag bits stored in RecordEntry::flags.
constexpr std::uint32_t RecordFlagOnGround     = 1u << 0;
constexpr std::uint32_t RecordFlagInAir        = 1u << 1;
constexpr std::uint32_t RecordFlagJumpEdge     = 1u << 2;
constexpr std::uint32_t RecordFlagSprint       = 1u << 3;
constexpr std::uint32_t RecordFlagSneak        = 1u << 4;
constexpr std::uint32_t RecordFlagActionSprint = 1u << 5;
constexpr std::uint32_t RecordFlagInputW       = 1u << 6;
constexpr std::uint32_t RecordFlagInputA       = 1u << 7;
constexpr std::uint32_t RecordFlagInputS       = 1u << 8;
constexpr std::uint32_t RecordFlagInputD       = 1u << 9;
constexpr std::uint32_t RecordFlagInputJump    = 1u << 10;
// Sample position was outside the recorded world height, so no block read was
// attempted (the three block fields are 0 for that sample).
constexpr std::uint32_t RecordFlagBlockOob = 1u << 11;
// Coarse medium bits from the feet/head block names (flowing counts as still).
constexpr std::uint32_t RecordFlagInWater = 1u << 12;
constexpr std::uint32_t RecordFlagInLava  = 1u << 13;

std::uint32_t blockNameHash(std::string const& name);

bool          recordStart(std::uint32_t period, std::string& error);
void          recordStop();
bool          recordActive();
std::uint64_t recordHead();
std::uint64_t recordRows();
std::uint32_t recordPeriod();
bool          recordTakeFault(); // Consumed/formatted by the spool thread.
void          recordTick(LocalPlayer& player, ClientInstance& client, std::uint64_t tick, bool jumpEdge);

// format: "jsonl" (decoded rows) or "hex" ("bin" is accepted as an alias).
nlohmann::json recordDumpJson(std::uint64_t since, std::size_t limit, bool binary);

// ---------------------------------------------------------------------------
// T5: runtime enter detours.
//
// Symbol hooks use a typed detour (currently MobJump::setJumpPendingScale).
// VA hooks are for addresses obtained from IDA and are disabled unless
// allow_hook_va is set; their detour preserves the four integer argument
// registers only, so float/vector/stack arguments are not safe.
// ---------------------------------------------------------------------------
bool hookAddressExecutable(std::uint64_t addr);

// Optional per-hit memory snapshot, taken inside the detour. Four integer
// registers are not enough when the interesting data lives behind a pointer and
// is short-lived (a per-tick vector, an AABB passed by address): by the time
// hook_drain runs, that buffer is already gone. size == 0 disables the
// snapshot, and the detour then costs one predictable branch.
struct HookMemSpec {
    std::uint32_t arg    = 0; // 0..3 = rcx/rdx/r8/r9
    std::int32_t  offset = 0; // -4096..4096; negative reaches the base of a
                              // struct from a pointer to one of its members
    std::uint32_t size   = 0; // 0, or a multiple of 4 no larger than 128
    // Read a pointer at arg+offset first, then snapshot from the pointee. Use
    // it for vectors/arrays reached through a member (e.g. a shape list).
    bool          deref  = false;
};

nlohmann::json hookInstall(
    std::string const& symbol,
    std::uint64_t      va,
    bool               useVa,
    std::string const& tag,
    std::uint32_t      sample,
    bool               dryRun,
    bool               allowVa,
    bool               inWorld,
    bool               injectFail,
    HookMemSpec const& mem = {}
);
nlohmann::json hookRemove(int id);
int            hookRemoveAll();
nlohmann::json hookListJson();
nlohmann::json hookDrainJson(std::uint64_t since, std::size_t limit);
bool           hookRequestCall(std::string const& symbol, float scale, std::string& error);
bool           hookTick(LocalPlayer& player, nlohmann::json& result);

// ---------------------------------------------------------------------------
// T9: tick-path overhead measurement.
// ---------------------------------------------------------------------------
void           overheadInit();
void           overheadRecord(std::uint32_t tickUs, std::uint32_t hookUs);
nlohmann::json overheadJson();
void           overheadRequestBusy(std::uint32_t us, std::uint32_t ticks);
void           overheadBusyTick();
std::uint64_t  qpcNow();
std::uint64_t  qpcElapsedUs(std::uint64_t start, std::uint64_t end);

// Per-path call rates so an unexpected callback storm is visible before it
// shows up as a crash or a dropped tick.
enum class PathCounter { Record, Watch, Hook };
void           pathCount(PathCounter path);
nlohmann::json pathRatesJson();

// ---------------------------------------------------------------------------
// T7: executable version fingerprint and symbol-anchor signatures.
// ---------------------------------------------------------------------------
void resolveInit(std::filesystem::path const& modDir);
bool versionOk();
// Porting: hooks run when the build has a matching profile, or when the user
// explicitly opts into compat mode (allow_version_mismatch).
bool           hooksAllowed();
bool           allowVersionMismatch();
void           setAllowVersionMismatch(bool allow);
std::string    buildId();
nlohmann::json diagnoseJson();
nlohmann::json profileJson();
nlohmann::json profileDump();
nlohmann::json profileProbe(std::string const& buildId);
nlohmann::json capabilitiesJson();
nlohmann::json signatureCheck(std::string const& name, std::string const& expectedHex);

// F5: offsets are data, not code constants.
bool           structOffset(char const* type, char const* field, std::uint64_t& out);
bool           componentSize(std::uint32_t id, std::uint32_t& size);
std::string    componentName(std::uint32_t id);
nlohmann::json structTableJson();
nlohmann::json symbolResolve(std::string const& name);

} // namespace probe::detail
