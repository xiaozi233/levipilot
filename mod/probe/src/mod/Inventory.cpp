#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

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
            {"type",  item.getTypeName()           },
            {"count", static_cast<int>(item.mCount)},
        };
    } else {
        result["item"] = nullptr;
    }
    enqueue(result.dump());
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
            {"slot", i                             },
            {"item", itemJson(inventory.getItem(i))},
        });
    }

    out["armor"] = nlohmann::json::array();
    for (int i = 0; i < static_cast<int>(SharedTypes::Legacy::ArmorSlot::HumanoidCount); ++i) {
        out["armor"].push_back({
            {"slot", i                                                                        },
            {"item", itemJson(player.getArmor(static_cast<SharedTypes::Legacy::ArmorSlot>(i)))},
        });
    }
    out["offhand"] = itemJson(player.getOffhandSlot());

    enqueue(out.dump());
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

    queueServerTask([this, name, container, slot, itemName, count, aux] {
        auto    minecraft = ll::service::bedrock::getMinecraft();
        Level*  level     = minecraft ? minecraft->getLevel() : nullptr;
        Player* player    = (level != nullptr) ? level->getPlayer(name) : nullptr;
        if (player == nullptr) {
            enqueue(R"({"event":"error","reason":"inventory_player_not_found"})");
            return;
        }

        ItemStack   stack;
        std::string error;
        if (!makeItemStack(*level, itemName, count, aux, stack, error)) {
            enqueue(
                nlohmann::json{
                    {"event",  "error"},
                    {"reason", error  }
            }.dump()
            );
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

        enqueue(
            nlohmann::json{
                {"event",     "inventory_set"},
                {"container", applied        },
                {"slot",      slot           },
                {"item",      itemJson(stack)},
        }
                .dump()
        );
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

    queueServerTask([this, name, itemName, count, aux] {
        auto    minecraft = ll::service::bedrock::getMinecraft();
        Level*  level     = minecraft ? minecraft->getLevel() : nullptr;
        Player* player    = (level != nullptr) ? level->getPlayer(name) : nullptr;
        if (player == nullptr) {
            enqueue(R"({"event":"error","reason":"inventory_player_not_found"})");
            return;
        }

        ItemStack   stack;
        std::string error;
        if (!makeItemStack(*level, itemName, count, aux, stack, error)) {
            enqueue(
                nlohmann::json{
                    {"event",  "error"},
                    {"reason", error  }
            }.dump()
            );
            return;
        }
        bool ok = player->getInventory().add(stack);
        player->sendInventory(true);

        enqueue(
            nlohmann::json{
                {"event",     "inventory_given"             },
                {"ok",        ok                            },
                {"requested", count                         },
                {"remaining", static_cast<int>(stack.mCount)},
                {"item",      itemJson(stack)               },
        }
                .dump()
        );
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

    queueServerTask([this, name] {
        auto    minecraft = ll::service::bedrock::getMinecraft();
        Level*  level     = minecraft ? minecraft->getLevel() : nullptr;
        Player* player    = (level != nullptr) ? level->getPlayer(name) : nullptr;
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

        enqueue(
            nlohmann::json{
                {"event", "inventory_cleared"},
                {"size",  size               }
        }.dump()
        );
    });
}


} // namespace probe
