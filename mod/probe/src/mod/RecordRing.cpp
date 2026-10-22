#include "mod/Internal.h"

#include <new>

using namespace probe::detail;

namespace probe::detail {

namespace {

std::unique_ptr<RecordEntry[]> gRing;
std::atomic_bool               gRecording{false};
std::atomic<std::uint64_t>     gHead{0};
std::atomic<std::uint64_t>     gTickCounter{0};
std::atomic<std::uint32_t>     gPeriod{1};
std::mutex                     gRingMutex; // allocation/start vs. first use

nlohmann::json entryJson(RecordEntry const& entry) {
    return {
        {"tick",     entry.tick                  },
        {"x",        finiteOrZero(entry.x)       },
        {"y",        finiteOrZero(entry.y)       },
        {"z",        finiteOrZero(entry.z)       },
        {"vx",       finiteOrZero(entry.vx)      },
        {"vy",       finiteOrZero(entry.vy)      },
        {"vz",       finiteOrZero(entry.vz)      },
        {"yaw",      finiteOrZero(entry.yaw)     },
        {"pitch",    finiteOrZero(entry.pitch)   },
        {"feet_y",   finiteOrZero(entry.feetY)   },
        {"flags",    entry.flags                 },
        {"block_below", entry.blockBelow         },
        {"block_feet",  entry.blockFeet          },
        {"block_head",  entry.blockHead          },
        {"health_milli", entry.healthMilli       },
    };
}

} // namespace

std::uint32_t blockNameHash(std::string const& name) {
    std::uint32_t hash = 2166136261u; // FNV-1a 32
    for (unsigned char ch : name) {
        hash ^= ch;
        hash *= 16777619u;
    }
    return hash;
}

namespace {

// F8: per-tick sampling must not allocate. Resolve the block's numeric id via
// the F5 offset table and cache its name hash so getTypeName() runs once per
// block type instead of three times per tick.
struct BlockHashEntry {
    std::uint32_t id   = 0xFFFFFFFFu;
    std::uint32_t hash = 0;
};
constexpr std::size_t kBlockHashCache = 256;
BlockHashEntry        gBlockHashCache[kBlockHashCache]; // single writer (tick)

// Union of the vanilla overworld/nether/end build ranges. Sampling outside it
// would ask BlockSource for a chunk section that does not exist.
constexpr int kMinSampleY = -64;
constexpr int kMaxSampleY = 319;

std::uint32_t blockHashAt(BlockSource const& source, int x, int y, int z) {
    Block const& block = source.getBlock(BlockPos{x, y, z});
    std::uint64_t legacyOffset = 0;
    std::uint64_t idOffset     = 0;
    if (structOffset("Block", "LegacyPointer", legacyOffset)
        && structOffset("BlockLegacy", "BlockId", idOffset)) {
        std::uint64_t legacy = 0;
        std::uint16_t id     = 0;
        auto const    base   = reinterpret_cast<std::uint64_t>(&block);
        if (readMemory(base + legacyOffset, &legacy, sizeof(legacy)) && legacy != 0
            && readMemory(legacy + idOffset, &id, sizeof(id))) {
            BlockHashEntry& entry = gBlockHashCache[id % kBlockHashCache];
            if (entry.id == id) {
                return entry.hash;
            }
            std::uint32_t const hash = blockNameHash(block.getTypeName());
            entry.hash               = hash;
            entry.id                 = id;
            return hash;
        }
    }
    // Table unavailable: keep the old semantics, at the cost of an allocation.
    return blockNameHash(block.getTypeName());
}

} // namespace

bool recordStart(std::uint32_t period, std::string& error) {
    std::lock_guard lock(gRingMutex);
    if (!gRing) {
        gRing = std::unique_ptr<RecordEntry[]>(new (std::nothrow) RecordEntry[RecordCapacity]());
    }
    if (!gRing) {
        error = "alloc_failed";
        return false;
    }
    gHead.store(0, std::memory_order_relaxed);
    gTickCounter.store(0, std::memory_order_relaxed);
    gPeriod.store(std::clamp<std::uint32_t>(period, 1, 200), std::memory_order_relaxed);
    gRecording.store(true, std::memory_order_release);
    return true;
}

void recordStop() { gRecording.store(false, std::memory_order_release); }

bool recordActive() { return gRecording.load(std::memory_order_acquire); }

std::uint64_t recordHead() { return gHead.load(std::memory_order_acquire); }

std::uint64_t recordRows() { return recordHead(); }

std::uint32_t recordPeriod() { return gPeriod.load(std::memory_order_relaxed); }

void recordTick(LocalPlayer& player, ClientInstance& client, std::uint64_t tick, bool jumpEdge) {
    if (!gRecording.load(std::memory_order_acquire) || !gRing) {
        return;
    }
    pathCount(PathCounter::Record);
    std::uint64_t const counter = gTickCounter.fetch_add(1, std::memory_order_relaxed);
    std::uint32_t const period  = gPeriod.load(std::memory_order_relaxed);
    if (period > 1 && (counter % period) != 0) {
        return;
    }

    RecordEntry entry;
    entry.tick = tick;

    auto const& pos = player.getPosition();
    auto        vel = player.getVelocity();
    auto const& rot = player.getRotation();
    auto const  feet = player.getFeetPos();
    auto        in   = readInput(client);

    entry.x     = finiteOrZero(static_cast<float>(pos.x));
    entry.y     = finiteOrZero(static_cast<float>(pos.y));
    entry.z     = finiteOrZero(static_cast<float>(pos.z));
    entry.vx    = finiteOrZero(static_cast<float>(vel.x));
    entry.vy    = finiteOrZero(static_cast<float>(vel.y));
    entry.vz    = finiteOrZero(static_cast<float>(vel.z));
    entry.yaw   = finiteOrZero(rot.y);
    entry.pitch = finiteOrZero(rot.x);
    entry.feetY = finiteOrZero(static_cast<float>(feet.y));

    bool actionSprint = false;
    if (auto action = player.getEntityContext().tryGetComponent<PlayerActionComponent>()) {
        actionSprint = action->mPlayerActions->test(PlayerActionStartSprinting);
    }

    std::uint32_t flags = 0;
    if (player.isOnGround()) {
        flags |= RecordFlagOnGround;
    } else {
        flags |= RecordFlagInAir;
    }
    if (jumpEdge) {
        flags |= RecordFlagJumpEdge;
    }
    if (player.isSprinting()) {
        flags |= RecordFlagSprint;
    }
    if (player.isSneaking()) {
        flags |= RecordFlagSneak;
    }
    if (actionSprint) {
        flags |= RecordFlagActionSprint;
    }
    if (in.w) {
        flags |= RecordFlagInputW;
    }
    if (in.a) {
        flags |= RecordFlagInputA;
    }
    if (in.s) {
        flags |= RecordFlagInputS;
    }
    if (in.d) {
        flags |= RecordFlagInputD;
    }
    if (in.jump) {
        flags |= RecordFlagInputJump;
    }
    entry.flags = flags;

    auto const& source = player.getDimensionBlockSourceConst();
    int const   fx     = static_cast<int>(std::floor(feet.x));
    int const   fy     = static_cast<int>(std::floor(feet.y));
    int const   fz     = static_cast<int>(std::floor(feet.z));
    int const   hy     = static_cast<int>(std::floor(feet.y + 1.8f));
    if (fy - 1 < kMinSampleY || fy < kMinSampleY || hy < kMinSampleY
        || fy - 1 > kMaxSampleY || fy > kMaxSampleY || hy > kMaxSampleY) {
        entry.blockBelow = 0;
        entry.blockFeet  = 0;
        entry.blockHead  = 0;
        entry.flags |= RecordFlagBlockOob;
    } else {
        entry.blockBelow = blockHashAt(source, fx, fy - 1, fz);
        entry.blockFeet  = blockHashAt(source, fx, fy, fz);
        entry.blockHead  = blockHashAt(source, fx, hy, fz);
    }

    float const health = player.getHealth();
    entry.healthMilli  = health > 0.0f ? static_cast<std::uint32_t>(health * 1000.0f) : 0u;

    // Single writer (the client tick), so a plain store after the full record
    // is enough; readers acquire head before touching entries below it.
    std::uint64_t const index = gHead.load(std::memory_order_relaxed);
    gRing[index % RecordCapacity] = entry;
    gHead.store(index + 1, std::memory_order_release);
}

nlohmann::json recordDumpJson(std::uint64_t since, std::size_t limit, bool binary) {
    std::uint64_t const head   = gHead.load(std::memory_order_acquire);
    std::uint64_t const oldest = head > RecordCapacity ? head - RecordCapacity : 0;
    std::uint64_t       start  = since;
    std::uint64_t       lost   = 0;
    if (start < oldest) {
        lost  = oldest - start;
        start = oldest;
    }
    if (start > head) {
        start = head;
    }
    if (limit == 0) {
        limit = 1024;
    }
    limit = std::clamp<std::size_t>(limit, 1, 8192);
    std::uint64_t const end = std::min(head, start + limit);

    nlohmann::json out;
    out["event"]      = "record_dump";
    out["since"]      = since;
    out["next"]       = end;
    out["head"]       = head;
    out["lost"]       = lost;
    out["count"]      = end - start;
    out["entry_size"] = RecordSize;
    out["active"]     = recordActive();
    out["period"]     = recordPeriod();

    if (binary) {
        static constexpr char Digits[] = "0123456789abcdef";
        std::string           hex;
        hex.reserve(static_cast<std::size_t>(end - start) * RecordSize * 2);
        for (std::uint64_t index = start; index < end; ++index) {
            auto const* bytes = reinterpret_cast<unsigned char const*>(&gRing[index % RecordCapacity]);
            for (std::size_t byte = 0; byte < RecordSize; ++byte) {
                hex.push_back(Digits[bytes[byte] >> 4]);
                hex.push_back(Digits[bytes[byte] & 0x0F]);
            }
        }
        out["encoding"] = "hex";
        out["data"]     = std::move(hex);
        return out;
    }

    out["rows"] = nlohmann::json::array();
    for (std::uint64_t index = start; index < end; ++index) {
        out["rows"].push_back(entryJson(gRing[index % RecordCapacity]));
    }
    return out;
}

} // namespace probe::detail
