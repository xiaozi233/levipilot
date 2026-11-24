#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {
namespace {

class ProbeCollisionContext final : public GetCollisionShapeInterface {
public:
    ProbeCollisionContext(BlockPos const& pos, std::string_view mode)
    : mFeetY(static_cast<float>(pos.y) + 1.0f),
      mEntityBox(
          Vec3{static_cast<float>(pos.x) + 0.2f, static_cast<float>(pos.y) + 1.0f, static_cast<float>(pos.z) + 0.2f},
          Vec3{static_cast<float>(pos.x) + 0.8f, static_cast<float>(pos.y) + 2.8f, static_cast<float>(pos.z) + 0.8f}
      ),
      mDescend(mode == "sneak_above"),
      mLeatherBoots(mode == "leather_boots"),
      mStandOnPowderSnow(mode == "leather_boots") {}

    float         getFeetAttachPosY() const override { return mFeetY; }
    bool          canDescendThroughBlock() const override { return mDescend; }
    float         getFallDistance() const override { return 0.0f; }
    ::ActorType   getEntityTypeId() const override { return ActorType::Player; }
    bool          isWearingLeatherBoots() const override { return mLeatherBoots; }
    bool          canStandOnPowderSnow() const override { return mStandOnPowderSnow; }
    ::AABB const& getAABB() const override { return mEntityBox; }
    bool          isWorldBuilder() const override { return false; }
    bool          isRiding() const override { return false; }
    bool          hasPassenger() const override { return false; }

private:
    float mFeetY;
    AABB  mEntityBox;
    bool  mDescend;
    bool  mLeatherBoots;
    bool  mStandOnPowderSnow;
};

nlohmann::json aabbJson(AABB const& box) {
    float const minX  = finiteOrZero(box.min.x);
    float const minY  = finiteOrZero(box.min.y);
    float const minZ  = finiteOrZero(box.min.z);
    float const maxX  = finiteOrZero(box.max.x);
    float const maxY  = finiteOrZero(box.max.y);
    float const maxZ  = finiteOrZero(box.max.z);
    bool const  empty = minX >= maxX || minY >= maxY || minZ >= maxZ;
    return {
        {"min",   {minX, minY, minZ}},
        {"max",   {maxX, maxY, maxZ}},
        {"empty", empty             },
    };
}

nlohmann::json statesToJson(CompoundTag const& serId) {
    nlohmann::json out = nlohmann::json::object();
    if (!serId.contains("states", Tag::Compound)) return out;
    Tag const* rawStates = serId.get("states");
    if (!rawStates) return out;
    auto const& states = *static_cast<CompoundTag const*>(rawStates);
    for (auto const& [k, v] : states.mTags) {
        if (v.hold(Tag::Byte)) out[k] = static_cast<int>(v.get<ByteTag>().data);
        else if (v.hold(Tag::Short)) out[k] = static_cast<int>(v.get<ShortTag>().data);
        else if (v.hold(Tag::Int)) out[k] = v.get<IntTag>().data;
        else if (v.hold(Tag::Int64)) out[k] = v.get<Int64Tag>().data;
        else if (v.hold(Tag::Float)) out[k] = finiteOrZero(v.get<FloatTag>().data);
        else if (v.hold(Tag::Double)) {
            double const d = v.get<DoubleTag>().data;
            out[k]         = std::isfinite(d) ? d : 0.0;
        } else if (v.hold(Tag::String)) out[k] = static_cast<std::string const&>(v.get<StringTag>());
        else out[k] = v.get().toString();
    }
    return out;
}

nlohmann::json
sampleBlockShape(Block const& block, BlockSource const& region, BlockPos const& pos, std::string_view entityMode) {
    FixedText<256> nameText;
    ushort         data            = 0;
    std::uint32_t  runtimeId       = 0;
    bool           btSolid         = false;
    bool           blkSolid        = false;
    bool           isAir           = false;
    float          friction        = 0.0f;
    float          bounciness      = 0.0f;
    bool           isFence         = false;
    bool           isThinFence     = false;
    bool           isWall          = false;
    bool           isSlab          = false;
    bool           isChest         = false;
    int            blockEntityType = 0;
    bool           addRet          = false;
    AABB           collisionBox{};
    AABB           outlineBox{};
    AABB           visualBox{};
    AABB           worldVisualBox{};
    Vec3           randPos{};
    AABB const     intersectBox{
        Vec3{static_cast<float>(pos.x - 4), static_cast<float>(pos.y - 4), static_cast<float>(pos.z - 4)},
        Vec3{static_cast<float>(pos.x + 5), static_cast<float>(pos.y + 5), static_cast<float>(pos.z + 5)}
    };
    std::vector<AABB> boxes;
    boxes.reserve(32);

    ProbeCollisionContext                          ctx(pos, entityMode);
    optional_ref<GetCollisionShapeInterface const> entityRef = (entityMode == "none")
                                                                 ? optional_ref<GetCollisionShapeInterface const>{}
                                                                 : optional_ref<GetCollisionShapeInterface const>{ctx};

    bool const ok = guardedGameRead([&] {
        auto const& bt = block.getBlockType();
        nameText.assign(block.getTypeName());
        data            = block.getData();
        runtimeId       = block.mNetworkId;
        btSolid         = bt.mSolid;
        blkSolid        = block._isSolid();
        isAir           = block.isAir();
        friction        = bt.mFriction;
        blockEntityType = static_cast<int>(bt.mBlockEntityType);
        isFence         = bt.isFenceBlock();
        isThinFence     = bt.isThinFenceBlock();
        isWall          = bt.isWallBlock();
        isSlab          = bt.isSlabBlock();
        isChest         = bt.isChestBlock();

        collisionBox = bt.getCollisionShape(block, region, pos, entityRef);
        addRet       = bt.addCollisionShapes(block, region, pos, &intersectBox, boxes, entityRef);

        AABB outlineBuf{};
        outlineBox = bt.getOutline(block, region, pos, outlineBuf);

        AABB visualBuf{};
        visualBox = bt.getVisualShape(block, visualBuf);

        AABB worldVisualBuf{};
        worldVisualBox = bt.getVisualShapeInWorld(block, region, pos, worldVisualBuf);

        bounciness = bt.getBounciness(region, pos);
        randPos    = bt.randomlyModifyPosition(pos);
    });

    if (!ok) {
        return {
            {"pos",   {pos.x, pos.y, pos.z}},
            {"fault", true                 },
        };
    }

    nlohmann::json statesJson = nlohmann::json::object();
    (void)guardedGameRead([&] { statesJson = statesToJson(*block.mSerializationId); });

    nlohmann::json boxesJson = nlohmann::json::array();
    for (auto const& box : boxes) {
        boxesJson.push_back(aabbJson(box));
    }

    float const offX = finiteOrZero(randPos.x - static_cast<float>(pos.x));
    float const offY = finiteOrZero(randPos.y - static_cast<float>(pos.y));
    float const offZ = finiteOrZero(randPos.z - static_cast<float>(pos.z));

    return {
        {"name",            nameText.c_str()        },
        {"data",            data                    },
        {"runtime_id",      runtimeId               },
        {"pos",             {pos.x, pos.y, pos.z}   },
        {"states",          std::move(statesJson)   },
        {"solid",           btSolid                 },
        {"block_solid",     blkSolid                },
        {"air",             isAir                   },
        {"friction",        finiteOrZero(friction)  },
        {"bounciness",      finiteOrZero(bounciness)},
        {"random_offset",   {offX, offY, offZ}      },
        {"collision",       aabbJson(collisionBox)  },
        {"add_ret",         addRet                  },
        {"boxes",           std::move(boxesJson)    },
        {"outline",         aabbJson(outlineBox)    },
        {"visual",          aabbJson(visualBox)     },
        {"visual_in_world", aabbJson(worldVisualBox)},
        {"flags",
         {{"fence", isFence},
          {"thin_fence", isThinFence},
          {"wall", isWall},
          {"slab", isSlab},
          {"chest", isChest},
          {"block_entity_type", blockEntityType}}   },
    };
}

} // namespace

void ProbeMod::runBlockShapesQuery(
    PlayerIdentity const&   identity,
    std::vector<BlockPoint> positions,
    std::string             entityMode
) {
    queueServerTask([this, identity, positions = std::move(positions), entityMode = std::move(entityMode)] {
        auto const context = serverPlayerContext(identity, mPoseEpoch.load(), mWorld.inWorld());
        if (!context.level || !context.player) {
            enqueue(R"({"event":"error","reason":"player_not_found"})");
            return;
        }
        auto const&    region = context.player->getDimensionBlockSourceConst();
        nlohmann::json out{
            {"event",       "block_shapes"         },
            {"ok",          true                   },
            {"entity_mode", entityMode             },
            {"blocks",      nlohmann::json::array()},
        };
        for (auto const& pt : positions) {
            BlockPos const bpos{pt.x, pt.y, pt.z};
            Block const*   blockPtr = nullptr;
            bool const     gotBlock = guardedGameRead([&] { blockPtr = &region.getBlock(bpos); });
            if (!gotBlock || !blockPtr) {
                out["blocks"].push_back({
                    {"pos",   {pt.x, pt.y, pt.z}},
                    {"fault", true              },
                });
                continue;
            }
            out["blocks"].push_back(sampleBlockShape(*blockPtr, region, bpos, entityMode));
        }
        enqueue(out.dump());
    });
}

void ProbeMod::runDumpBlockCollisions(
    PlayerIdentity const& identity,
    std::string           filename,
    bool                  hasPos,
    BlockPoint            pos,
    bool                  includeAir,
    std::string           entityMode
) {
    queueServerTask(
        [this, identity, filename = std::move(filename), hasPos, pos, includeAir, entityMode = std::move(entityMode)] {
            auto const startMs = GetTickCount64();
            auto const context = serverPlayerContext(identity, mPoseEpoch.load(), mWorld.inWorld());
            if (!context.level || !context.player) {
                enqueue(R"({"event":"error","reason":"player_not_found"})");
                return;
            }
            BlockPos origin{pos.x, pos.y, pos.z};
            if (!hasPos) {
                origin = BlockPos{0, 0, 0};
            }
            auto const& region = context.player->getDimensionBlockSourceConst();
            auto const  dir    = getSelf().getModDir();
            auto const  target = dir / filename;
            auto const  temp   = dir / (filename + ".tmp");

            std::ofstream file(temp, std::ios::binary | std::ios::trunc);
            if (!file) {
                enqueue(R"({"event":"error","reason":"open_failed"})");
                return;
            }

            std::size_t blockTypes   = 0;
            std::size_t permutations = 0;
            std::size_t faults       = 0;

            BlockTypeRegistry::get().forEachBlockType([&](BlockType const& bt) {
                ++blockTypes;
                std::vector<Block const*> permPtrs;
                if (!guardedGameRead([&] {
                        auto const& vec = *bt.mBlockPermutations;
                        permPtrs.reserve(vec.size());
                        for (auto const& ptr : vec) {
                            if (ptr) permPtrs.push_back(ptr.get());
                        }
                    })) {
                    ++faults;
                    return true;
                }
                for (Block const* blockPtr : permPtrs) {
                    bool isAir = false;
                    if (!guardedGameRead([&] { isAir = blockPtr->isAir(); })) {
                        ++faults;
                        continue;
                    }
                    if (!includeAir && isAir) continue;
                    auto row = sampleBlockShape(*blockPtr, region, origin, entityMode);
                    if (row.value("fault", false)) ++faults;
                    ++permutations;
                    file << row.dump() << '\n';
                }
                return true;
            });

            file.flush();
            file.close();
            if (!file) {
                std::error_code ec;
                std::filesystem::remove(temp, ec);
                enqueue(R"({"event":"error","reason":"write_failed"})");
                return;
            }
            if (!MoveFileExW(temp.c_str(), target.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
                std::error_code ec;
                std::filesystem::remove(temp, ec);
                enqueue(R"({"event":"error","reason":"replace_failed"})");
                return;
            }

            auto const elapsedMs = GetTickCount64() - startMs;
            enqueue(
                nlohmann::json{
                    {"event",        "block_collisions_dumped"     },
                    {"ok",           true                          },
                    {"path",         target.string()               },
                    {"block_types",  blockTypes                    },
                    {"permutations", permutations                  },
                    {"faults",       faults                        },
                    {"include_air",  includeAir                    },
                    {"entity_mode",  entityMode                    },
                    {"origin",       {origin.x, origin.y, origin.z}},
                    {"elapsed_ms",   elapsedMs                     },
            }
                    .dump()
            );
        }
    );
}

} // namespace probe
