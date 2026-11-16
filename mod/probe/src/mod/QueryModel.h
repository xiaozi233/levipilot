#pragma once

#include "mod/SnapshotMailbox.h"
#include "mod/TaskModel.h"

namespace probe::detail {

enum class CaptureTest { None, Fault, Cpp, Partial, Deferred };
struct CaptureRequest {
    CaptureTest test = CaptureTest::None;
};

struct HookCallRequest {
    float       scale = 1;
    CaptureTest test  = CaptureTest::None;
};
struct HookCallSnapshot {
    float       scale = 1;
    char const* error = nullptr;
};
using HookCallCapture = SnapshotMailbox<HookCallRequest, HookCallSnapshot>;

struct ItemSnapshot {
    FixedText<256>  type;
    FixedText<4096> name;
    int             count = 0, aux = 0, damage = 0, maxDamage = 0;
    bool            present = false;
};

struct StateSnapshot {
    struct Vec {
        float x = 0, y = 0, z = 0;
    } pos, feet, head, velocity, posDelta;
    struct Input {
        bool  w = false, a = false, s = false, d = false, jump = false, sprint = false, sneak = false;
        bool  actionSprint = false, locked = false;
        float moveX = 0, moveY = 0;
    } input;
    struct Bindings {
        int forward = 0, back = 0, left = 0, right = 0, sneak = 0, jump = 0, sprint = 0;
    } bindings;
    struct Effect {
        int  id = 0, duration = 0, amplifier = 0;
        bool ambient = false;
    };
    ItemSnapshot                  item;
    std::array<Effect, 128>       effects{};
    std::size_t                   effectCount = 0;
    FixedText<1024>               name;
    std::uint64_t                 tick = 0, uuidA = 0, uuidB = 0, runtimeId = 0, context = 0;
    float                         yaw = 0, pitch = 0, prevYaw = 0, prevPitch = 0, health = 0, maxHealth = 0;
    bool                          onGround = false, sprinting = false, sneaking = false, swimming = false;
    bool                          flying = false, mayfly = false, inWater = false, alive = false;
    int                           gamemode = 0, dimension = 0, selectedSlot = 0, itemUseTicks = 0;
    std::array<float, 5>          attributes{};
    std::array<bool, 5>           haveAttribute{};
    std::array<FixedText<256>, 4> blocks{};
    std::array<bool, 4>           haveBlock{};
    bool                          truncated = false;
    char const*                   error     = nullptr;
    char const*                   warning   = nullptr;
    std::uint32_t                 sampleUs  = 0;
};

struct ButtonSnapshot {
    std::array<std::uint32_t, 512> ids{};
    std::size_t                    count     = 0;
    bool                           haveQueue = false, queue = false, truncated = false;
    char const*                    error = nullptr;
};

struct InventorySnapshot {
    static constexpr std::size_t       Capacity = 256;
    std::array<ItemSnapshot, Capacity> slots{};
    std::array<ItemSnapshot, 8>        armor{};
    ItemSnapshot                       offhand;
    std::size_t                        count = 0, armorCount = 0;
    int                                size = 0, selectedSlot = 0;
    bool                               truncated = false;
    char const*                        error     = nullptr;
    std::uint32_t                      sampleUs  = 0;
};

struct SlotQuery {
    int slot = 0;
    CaptureTest test = CaptureTest::None;
};
struct SlotSnapshot {
    FixedText<256> type;
    int slot = 0, applied = -1, count = 0;
    bool present = false, truncated = false;
    char const* error = nullptr;
};

struct BlockPoint {
    int x = 0, y = 0, z = 0;
};
struct BlockRow {
    BlockPoint     pos;
    FixedText<256> name;
    std::uint64_t  address = 0;
};
struct BlockRequest {
    std::array<BlockPoint, 256> positions{};
    std::size_t                 count = 0;
    CaptureTest                 test  = CaptureTest::None;
};
struct BlockSnapshot {
    std::array<BlockRow, 256> rows{};
    std::size_t               count     = 0;
    bool                      truncated = false;
    char const*               error     = nullptr;
};

struct ScanQuery {
    BlockPoint  from, to;
    int         limit      = 4096;
    bool        includeAir = false;
    CaptureTest test       = CaptureTest::None;
};
struct ScanSnapshot {
    static constexpr std::size_t   BatchSize = 512;
    static constexpr std::size_t   Capacity  = 16384;
    std::array<BlockRow, Capacity> rows{};
    BlockPoint                     from, to;
    std::size_t                    count = 0, visited = 0, batches = 0;
    std::uint32_t                  maxBatchUs = 0;
    std::uint64_t                  firstTick = 0, lastTick = 0;
    bool                           initialized = false, truncated = false, namesTruncated = false;
    char const*                    error = nullptr;
};

template <class Read>
bool advanceScan(ScanQuery const& request, ScanSnapshot& out, std::uint64_t tick, Read&& read) {
    if (!out.initialized) {
        out.from = {
            std::min(request.from.x, request.to.x),
            std::min(request.from.y, request.to.y),
            std::min(request.from.z, request.to.z)
        };
        out.to = {
            std::max(request.from.x, request.to.x),
            std::max(request.from.y, request.to.y),
            std::max(request.from.z, request.to.z)
        };
        out.firstTick   = tick;
        out.initialized = true;
    }
    auto const dx = std::int64_t(out.to.x) - out.from.x + 1;
    auto const dy = std::int64_t(out.to.y) - out.from.y + 1;
    auto const dz = std::int64_t(out.to.z) - out.from.z + 1;
    if (dx > 32768 || dy > 32768 || dz > 32768 || dx * dy * dz > 32768) {
        out.error = "bad_region";
        return true;
    }
    auto const total = static_cast<std::size_t>(dx * dy * dz);
    auto const limit = static_cast<std::size_t>(std::clamp(request.limit, 1, static_cast<int>(ScanSnapshot::Capacity)));
    ++out.batches;
    out.lastTick = tick;
    for (std::size_t n = 0; n < ScanSnapshot::BatchSize && out.visited < total; ++n) {
        auto const index = out.visited++;
        BlockRow   row;
        row.pos = {
            out.from.x + static_cast<int>(index % dx),
            out.from.y + static_cast<int>((index / dx) % dy),
            out.from.z + static_cast<int>(index / (dx * dy))
        };
        if (!read(row)) {
            out.error = "scan_fault";
            return true;
        }
        auto const name = row.name.view();
        if (!request.includeAir
            && (name == "minecraft:air" || name == "minecraft:cave_air" || name == "minecraft:void_air"))
            continue;
        if (out.count >= limit) {
            out.truncated = true;
            return true;
        }
        out.rows[out.count++] = row;
    }
    return out.visited == total;
}

using StateCapture     = SnapshotMailbox<CaptureRequest, StateSnapshot>;
using ButtonCapture    = SnapshotMailbox<CaptureRequest, ButtonSnapshot>;
using InventoryCapture = SnapshotMailbox<CaptureRequest, InventorySnapshot>;
using SlotCapture      = SnapshotMailbox<SlotQuery, SlotSnapshot>;
using BlockCapture     = SnapshotMailbox<BlockRequest, BlockSnapshot>;
using ScanCapture      = SnapshotMailbox<ScanQuery, ScanSnapshot>;

} // namespace probe::detail
