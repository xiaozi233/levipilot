#include "mod/Internal.h"
#include "mod/RecordModel.h"

#include <new>

using namespace probe::detail;

namespace probe::detail {

namespace {

RecordBuffer<RecordEntry, RecordCapacity> gRecords;
std::mutex gRingMutex; // Command-side lifecycle and dumps; never taken by tick.
std::atomic_bool gSampleFault{false};

// Liquid name hashes for the medium flag bits. blockNameHash takes std::string
// temporaries, so they are resolved here (command thread) instead of in the
// tick, and read back as plain relaxed loads.
std::atomic<std::uint32_t> gWaterHash{0};
std::atomic<std::uint32_t> gFlowingWaterHash{0};
std::atomic<std::uint32_t> gLavaHash{0};
std::atomic<std::uint32_t> gFlowingLavaHash{0};

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
BlockHashEntry        gBlockHashCache[kBlockHashCache]; // Tick writer; reset while captures are stopped.

// Union of the vanilla overworld/nether/end build ranges. Sampling outside it
// would ask BlockSource for a chunk section that does not exist.
constexpr int kMinSampleY = -64;
constexpr int kMaxSampleY = 319;

// structOffset walks a string-keyed table and notes misses under a mutex; the
// tick path must do neither, so the block offsets are resolved once per
// recording session on the command thread, before captures are admitted.
std::uint64_t gBlockLegacyPointerOffset = 0;
std::uint64_t gBlockLegacyIdOffset     = 0;

void resolveBlockOffsets() {
    gBlockLegacyPointerOffset = gBlockLegacyIdOffset = 0;
    std::uint64_t legacyOffset = 0;
    std::uint64_t idOffset     = 0;
    if (structOffset("Block", "LegacyPointer", legacyOffset)
        && structOffset("BlockLegacy", "BlockId", idOffset)) {
        gBlockLegacyPointerOffset = legacyOffset;
        gBlockLegacyIdOffset      = idOffset;
    }
}

std::uint32_t blockHashAt(BlockSource const& source, int x, int y, int z) {
    Block const& block = source.getBlock(BlockPos{x, y, z});
    if (gBlockLegacyPointerOffset != 0 && gBlockLegacyIdOffset != 0) {
        std::uint64_t legacy = 0;
        std::uint16_t id     = 0;
        auto const    base   = reinterpret_cast<std::uint64_t>(&block);
        if (readMemory(base + gBlockLegacyPointerOffset, &legacy, sizeof(legacy)) && legacy != 0
            && readMemory(legacy + gBlockLegacyIdOffset, &id, sizeof(id))) {
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
    // Table unavailable (compat mode): keep the old semantics, at the cost of
    // an allocation. Documented exception to the hot-path discipline.
    return blockNameHash(block.getTypeName());
}

} // namespace

bool recordStart(std::uint32_t period, std::string& error) {
    std::lock_guard lock(gRingMutex);
    if (!gRecords.start(period, [] {
            gWaterHash.store(blockNameHash("minecraft:water"), std::memory_order_relaxed);
            gFlowingWaterHash.store(blockNameHash("minecraft:flowing_water"), std::memory_order_relaxed);
            gLavaHash.store(blockNameHash("minecraft:lava"), std::memory_order_relaxed);
            gFlowingLavaHash.store(blockNameHash("minecraft:flowing_lava"), std::memory_order_relaxed);
            // Resolve profile data on the command thread, before admitting
            // captures. No string-keyed lookup or reset can race a sample.
            resolveBlockOffsets();
            std::fill(std::begin(gBlockHashCache), std::end(gBlockHashCache), BlockHashEntry{});
        })) {
        error = "alloc_failed";
        return false;
    }
    return true;
}

void recordStop() {
    std::lock_guard lock(gRingMutex);
    gRecords.stop();
}

bool recordActive() { return gRecords.active(); }

std::uint64_t recordHead() { return gRecords.head(); }

std::uint64_t recordRows() { return recordHead(); }

std::uint32_t recordPeriod() { return gRecords.period(); }

bool recordTakeFault() { return gSampleFault.exchange(false, std::memory_order_acq_rel); }

namespace {

void captureTickEntry(
    LocalPlayer& player, ClientInstance& client, std::uint64_t tick, bool jumpEdge, RecordEntry& entry
) {
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
    // mPlayerActions is inline TypedStorage (never null): operator-> reaches
    // the embedded bitset directly.
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

        auto const inWater = [](std::uint32_t hash) {
            return hash != 0
                && (hash == gWaterHash.load(std::memory_order_relaxed)
                    || hash == gFlowingWaterHash.load(std::memory_order_relaxed));
        };
        auto const inLava = [](std::uint32_t hash) {
            return hash != 0
                && (hash == gLavaHash.load(std::memory_order_relaxed)
                    || hash == gFlowingLavaHash.load(std::memory_order_relaxed));
        };
        if (inWater(entry.blockFeet) || inWater(entry.blockHead)) {
            entry.flags |= RecordFlagInWater;
        }
        if (inLava(entry.blockFeet) || inLava(entry.blockHead)) {
            entry.flags |= RecordFlagInLava;
        }
    }

    float const health = player.getHealth();
    entry.healthMilli  = health > 0.0f ? static_cast<std::uint32_t>(health * 1000.0f) : 0u;
}

// Keep SEH in a frame with no C++ objects. /EHs does not unwind a Capture for
// an access violation; converting it here lets the owner always release its
// admission lease and prevents a later record_start/stop from waiting forever.
__declspec(noinline) bool captureEntryWithSeh(
    LocalPlayer& player, ClientInstance& client, std::uint64_t tick, bool jumpEdge, RecordEntry& entry
) {
#if defined(_MSC_VER)
    __try {
        captureTickEntry(player, client, tick, jumpEdge, entry);
        return true;
    } __except (isFatalSehCode(GetExceptionCode()) ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH) {
        return false;
    }
#else
    captureTickEntry(player, client, tick, jumpEdge, entry);
    return true;
#endif
}

} // namespace

void recordTick(LocalPlayer& player, ClientInstance& client, std::uint64_t tick, bool jumpEdge) {
    auto capture = gRecords.capture();
    if (!capture) return;
    pathCount(PathCounter::Record);
    if (!capture.sampleDue()) return;

    struct CaptureSehFlag {
        bool previous = gDispatchSehActive;
        CaptureSehFlag() { gDispatchSehActive = true; }
        ~CaptureSehFlag() { gDispatchSehActive = previous; }
    } captureFlag;
    RecordEntry entry;
    if (!captureEntryWithSeh(player, client, tick, jumpEdge, entry)) {
        gSampleFault.store(true, std::memory_order_release);
        return;
    }
    capture.publish(entry);
}

nlohmann::json recordDumpJson(std::uint64_t since, std::size_t limit, bool binary) {
    std::lock_guard lock(gRingMutex);
    std::uint64_t const head   = gRecords.head();
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
        std::uint64_t count = 0;
        for (std::uint64_t index = start; index < end; ++index) {
            RecordEntry entry;
            if (!gRecords.read(index, entry)) {
                ++lost;
                continue;
            }
            ++count;
            auto const* bytes = reinterpret_cast<unsigned char const*>(&entry);
            for (std::size_t byte = 0; byte < RecordSize; ++byte) {
                hex.push_back(Digits[bytes[byte] >> 4]);
                hex.push_back(Digits[bytes[byte] & 0x0F]);
            }
        }
        out["encoding"] = "hex";
        out["data"]     = std::move(hex);
        out["count"]    = count;
        out["lost"]     = lost;
        return out;
    }

    out["rows"] = nlohmann::json::array();
    for (std::uint64_t index = start; index < end; ++index) {
        RecordEntry entry;
        if (gRecords.read(index, entry)) {
            out["rows"].push_back(entryJson(entry));
        } else {
            ++lost;
        }
    }
    out["count"] = out["rows"].size();
    out["lost"] = lost;
    return out;
}

} // namespace probe::detail
