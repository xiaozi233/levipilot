#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

void ProbeMod::applySlotRequest(LocalPlayer& player, SlotQuery const& request, SlotSnapshot& out) {
    out.slot = std::clamp(request.slot, 0, 8);
    // Keep the owning SDK return outside the raw SEH frame. The remaining SDK
    // name allocation is tracked separately from JSON/transport removal.
    std::string type;
    if (!guardedGameRead([&] {
            injectCaptureFault(request.test);
            bool const applied = player.mInventory && player.mInventory->selectSlot(out.slot, ContainerID::Inventory);
            if (!applied) player.setSelectedSlot(out.slot);
            out.applied = player.getSelectedItemSlot();
            auto const& item = player.getSelectedItem();
            if (!item.isNull()) {
                type = item.getTypeName();
                out.type.assign(type);
                out.present = true;
                out.count = static_cast<int>(item.mCount);
                out.truncated = out.type.view().size() != type.size();
            }
        })) out.error = "select_slot_fault";
}


void ProbeMod::captureInventory(LocalPlayer& player, CaptureRequest const& request, InventorySnapshot& out) {
    auto const start = qpcNow();
    if (!guardedGameRead([&] {
            injectCaptureFault(request.test);
            out.selectedSlot = player.getSelectedItemSlot();
            auto& inventory  = player.getInventory();
            out.size         = inventory.getContainerSize();
            if (out.size < 0) {
                out.error = "inventory_size_invalid";
                return;
            }
            out.count     = std::min(static_cast<std::size_t>(out.size), out.slots.size());
            out.truncated = out.count != static_cast<std::size_t>(out.size);
            for (std::size_t i = 0; i < out.count; ++i)
                captureItem(inventory.getItem(static_cast<int>(i)), out.slots[i], out.truncated);
            constexpr auto armorCount = static_cast<std::size_t>(SharedTypes::Legacy::ArmorSlot::HumanoidCount);
            static_assert(armorCount <= std::tuple_size_v<decltype(out.armor)>);
            out.armorCount = armorCount;
            for (std::size_t i = 0; i < out.armorCount; ++i)
                captureItem(
                    player.getArmor(static_cast<SharedTypes::Legacy::ArmorSlot>(i)),
                    out.armor[i],
                    out.truncated
                );
            captureItem(player.getOffhandSlot(), out.offhand, out.truncated);
        }))
        out.error = "inventory_fault";
    out.sampleUs = static_cast<std::uint32_t>(std::min<std::uint64_t>(qpcElapsedUs(start, qpcNow()), UINT32_MAX));
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
    auto const identity = localPlayerIdentity();
    if (!identity.valid) {
        enqueue(R"({"event":"error","reason":"inventory_player_unknown"})");
        return;
    }

    queueServerTask([this, identity, container, slot, itemName, count, aux] {
        auto const context = serverPlayerContext(identity, mPoseEpoch.load(), mWorld.inWorld());
        auto* level = context.level;
        auto* player = context.player;
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

void ProbeMod::queueEquip(std::string const& target, int slot) {
    auto server = ll::service::bedrock::getServerInstance();
    if (!server) {
        enqueue(R"({"event":"error","reason":"inventory_no_server_instance"})");
        return;
    }
    auto const identity = localPlayerIdentity();
    if (!identity.valid) {
        enqueue(R"({"event":"error","reason":"inventory_player_unknown"})");
        return;
    }

    queueServerTask([this, identity, target, slot] {
        auto const context = serverPlayerContext(identity, mPoseEpoch.load(), mWorld.inWorld());
        auto* player = context.player;
        if (player == nullptr) {
            enqueue(R"({"event":"error","reason":"inventory_player_not_found"})");
            return;
        }

        int const srcSlot = slot >= 0 ? slot : player->getSelectedItemSlot();
        if (srcSlot < 0 || srcSlot >= player->getInventory().getContainerSize()) {
            enqueue(R"({"event":"error","reason":"inventory_slot_out_of_range"})");
            return;
        }
        ItemStack stack = player->getInventory().getItem(srcSlot);
        if (stack.isNull()) {
            enqueue(R"({"event":"error","reason":"equip_empty_slot"})");
            return;
        }

        using SharedTypes::Legacy::ArmorSlot;
        int armorIndex = -1;
        if (target == "head") {
            armorIndex = static_cast<int>(ArmorSlot::Head);
        } else if (target == "torso") {
            armorIndex = static_cast<int>(ArmorSlot::Torso);
        } else if (target == "legs") {
            armorIndex = static_cast<int>(ArmorSlot::Legs);
        } else if (target == "feet") {
            armorIndex = static_cast<int>(ArmorSlot::Feet);
        } else if (target != "offhand") {
            enqueue(
                nlohmann::json{
                    {"event",  "error"           },
                    {"reason", "bad_equip_target"},
            }
                    .dump()
            );
            return;
        }

        if (armorIndex >= 0) {
            player->setArmor(static_cast<ArmorSlot>(armorIndex), stack);
        } else {
            player->setOffhandSlot(stack);
        }
        player->getInventory().setItem(srcSlot, ItemStack{});
        player->sendInventory(true);

        enqueue(
            nlohmann::json{
                {"event",  "equip_done"   },
                {"target", target         },
                {"slot",   srcSlot        },
                {"item",   itemJson(stack)},
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
    auto const identity = localPlayerIdentity();
    if (!identity.valid) {
        enqueue(R"({"event":"error","reason":"inventory_player_unknown"})");
        return;
    }

    queueServerTask([this, identity, itemName, count, aux] {
        auto const context = serverPlayerContext(identity, mPoseEpoch.load(), mWorld.inWorld());
        auto* level = context.level;
        auto* player = context.player;
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
    auto const identity = localPlayerIdentity();
    if (!identity.valid) {
        enqueue(R"({"event":"error","reason":"inventory_player_unknown"})");
        return;
    }

    queueServerTask([this, identity] {
        auto const context = serverPlayerContext(identity, mPoseEpoch.load(), mWorld.inWorld());
        auto* player = context.player;
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
