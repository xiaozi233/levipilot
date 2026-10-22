#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

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
    j["event"]         = "state";
    j["in_world"]      = true;
    j["tick"]          = mTick.load();
    j["x"]             = finiteOrZero(static_cast<float>(pos.x));
    j["y"]             = finiteOrZero(static_cast<float>(pos.y));
    j["z"]             = finiteOrZero(static_cast<float>(pos.z));
    auto const feet    = player.getFeetPos();
    auto const head    = player.getHeadPos();
    j["feet_x"]        = finiteOrZero(static_cast<float>(feet.x));
    j["feet_y"]        = finiteOrZero(static_cast<float>(feet.y));
    j["feet_z"]        = finiteOrZero(static_cast<float>(feet.z));
    j["head_x"]        = finiteOrZero(static_cast<float>(head.x));
    j["head_y"]        = finiteOrZero(static_cast<float>(head.y));
    j["head_z"]        = finiteOrZero(static_cast<float>(head.z));
    j["vx"]            = finiteOrZero(static_cast<float>(vel.x));
    j["vy"]            = finiteOrZero(static_cast<float>(vel.y));
    j["vz"]            = finiteOrZero(static_cast<float>(vel.z));
    j["yaw"]           = finiteOrZero(rot.y);
    j["pitch"]         = finiteOrZero(rot.x);
    j["on_ground"]     = player.isOnGround();
    j["sprinting"]     = player.isSprinting();
    j["sneaking"]      = player.isSneaking();
    j["swimming"]      = player.isSwimming();
    j["flying"]        = player.isFlying();
    j["in_water"]      = player.isInWater();
    j["alive"]         = player.isAlive();
    j["health"]        = player.getHealth();
    j["max_health"]    = player.getMaxHealth();
    j["gamemode"]      = static_cast<int>(player.getPlayerGameType());
    j["dimension"]     = static_cast<int>(player.getDimensionId());
    j["name"]          = player.getRealName();
    j["uuid"]          = player.getUuid().asString();
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
        {"w",             in.w                  },
        {"a",             in.a                  },
        {"s",             in.s                  },
        {"d",             in.d                  },
        {"sprint",        in.sprint             },
        {"sneak",         in.sneak              },
        {"jump",          in.jump               },
        {"move_x",        finiteOrZero(in.moveX)},
        {"move_y",        finiteOrZero(in.moveY)},
        {"action_sprint", actionSprint          },
    };

    auto const& item = player.getSelectedItem();
    if (!item.isNull()) {
        j["item"] = {
            {"type",  item.getTypeName()              },
            {"name",  item.getName()                  },
            {"count", static_cast<int>(item.mCount)   },
            {"aux",   static_cast<int>(item.mAuxValue)},
        };
    } else {
        j["item"] = nullptr;
    }
    // Non-zero while food/bow use is in progress; useful for verifying `use`.
    j["item_use_ticks"] = player.getItemUseDuration();

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


void ProbeMod::captureButtons(ClientInstance& client) {
    nlohmann::json out;
    out["event"]        = "buttons";
    out["ids"]          = nlohmann::json::array();
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

    auto const&    source = player.getDimensionBlockSourceConst();
    nlohmann::json out;
    out["event"]  = "blocks";
    out["blocks"] = nlohmann::json::array();
    for (auto const& query : queries) {
        BlockPos const pos{query.x, query.y, query.z};
        auto const&    block = source.getBlock(pos);
        out["blocks"].push_back({
            {"x", query.x},
            {"y", query.y},
            {"z", query.z},
            {"name", block.getTypeName()},
            // T2 anchor: lets probe(kind=block/legacy) work on a live object
            // without a separate "address of" command.
            {"addr",
             static_cast<std::uint64_t>(reinterpret_cast<std::uintptr_t>(&block))},
        });
    }
    enqueue(out.dump());
}


} // namespace probe
