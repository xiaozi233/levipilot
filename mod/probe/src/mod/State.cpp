#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {
namespace {

template <std::size_t N>
void copyText(FixedText<N>& out, std::string_view text, bool& truncated) {
    out.assign(text);
    truncated = truncated || out.view().size() != text.size();
}

StateSnapshot::Vec vectorSnapshot(Vec3 const& value) {
    return {finiteOrZero(value.x), finiteOrZero(value.y), finiteOrZero(value.z)};
}

bool readBlock(LocalPlayer& player, BlockRow& row, bool& truncated, bool fault = false) {
    return guardedGameRead([&] {
        if (fault) injectCaptureFault(CaptureTest::Fault);
        auto const& block = player.getDimensionBlockSourceConst().getBlock(BlockPos{row.pos.x, row.pos.y, row.pos.z});
        copyText(row.name, block.getTypeName(), truncated);
        row.address = reinterpret_cast<std::uint64_t>(&block);
    });
}

nlohmann::json stateJson(StateSnapshot const& s) {
    auto const&    in   = s.input;
    auto const&    keys = s.bindings;
    nlohmann::json out{
        {"event",          "state"                               },
        {"in_world",       true                                  },
        {"ok",             true                                  },
        {"tick",           s.tick                                },
        {"x",              s.pos.x                               },
        {"y",              s.pos.y                               },
        {"z",              s.pos.z                               },
        {"feet_x",         s.feet.x                              },
        {"feet_y",         s.feet.y                              },
        {"feet_z",         s.feet.z                              },
        {"head_x",         s.head.x                              },
        {"head_y",         s.head.y                              },
        {"head_z",         s.head.z                              },
        {"vx",             s.velocity.x                          },
        {"vy",             s.velocity.y                          },
        {"vz",             s.velocity.z                          },
        {"yaw",            s.yaw                                 },
        {"pitch",          s.pitch                               },
        {"on_ground",      s.onGround                            },
        {"sprinting",      s.sprinting                           },
        {"sneaking",       s.sneaking                            },
        {"swimming",       s.swimming                            },
        {"flying",         s.flying                              },
        {"mayfly",         s.mayfly                              },
        {"in_water",       s.inWater                             },
        {"alive",          s.alive                               },
        {"health",         s.health                              },
        {"max_health",     s.maxHealth                           },
        {"gamemode",       s.gamemode                            },
        {"dimension",      s.dimension                           },
        {"name",           s.name.c_str()                        },
        {"uuid",           mce::UUID{s.uuidA, s.uuidB}.asString()},
        {"runtime_id",     s.runtimeId                           },
        {"context",        s.context                             },
        {"selected_slot",  s.selectedSlot                        },
        {"item_use_ticks", s.itemUseTicks                        },
        {"input",
         {{"w", in.w},
          {"a", in.a},
          {"s", in.s},
          {"d", in.d},
          {"sprint", in.sprint},
          {"sneak", in.sneak},
          {"jump", in.jump},
          {"move_x", in.moveX},
          {"move_y", in.moveY},
          {"action_sprint", in.actionSprint},
          {"locked", in.locked}}                                 },
        {"bindings",
         {{"forward", keys.forward},
          {"back", keys.back},
          {"left", keys.left},
          {"right", keys.right},
          {"sneak", keys.sneak},
          {"jump", keys.jump},
          {"sprint", keys.sprint}}                               },
        {"item",           nullptr                               },
        {"effects",        nlohmann::json::array()               },
        {"partial",        s.warning != nullptr || s.truncated   },
        {"truncated",      s.truncated                           },
        {"sample_us",      s.sampleUs                            }
    };
    constexpr char const* attributes[] = {"hunger", "saturation", "exhaustion", "xp_level", "xp_progress"};
    for (std::size_t i = 0; i < s.attributes.size(); ++i)
        if (s.haveAttribute[i]) out[attributes[i]] = s.attributes[i];
    out["item"] = itemJson(s.item);
    for (std::size_t i = 0; i < s.effectCount; ++i) {
        auto const& effect = s.effects[i];
        out["effects"].push_back({
            {"id",        effect.id       },
            {"duration",  effect.duration },
            {"amplifier", effect.amplifier},
            {"ambient",   effect.ambient  }
        });
    }
    constexpr char const* blocks[] = {"block_feet", "block_below", "block_head", "block_above"};
    for (std::size_t i = 0; i < s.blocks.size(); ++i)
        if (s.haveBlock[i]) out[blocks[i]] = s.blocks[i].c_str();
    if (s.warning) out["warnings"] = {s.warning};
    return out;
}

} // namespace

void ProbeMod::captureState(
    ClientInstance&       client,
    LocalPlayer&          player,
    CaptureRequest const& request,
    StateSnapshot&        out
) {
    auto const start = qpcNow();
    auto const ok    = guardedGameRead([&] {
        injectCaptureFault(request.test);
        out.tick       = mTick.load(std::memory_order_relaxed);
        out.pos        = vectorSnapshot(player.getPosition());
        out.feet       = vectorSnapshot(player.getFeetPos());
        out.head       = vectorSnapshot(player.getHeadPos());
        out.velocity   = vectorSnapshot(player.getVelocity());
        auto const rot = player.getRotation();
        out.yaw        = finiteOrZero(rot.y);
        out.pitch      = finiteOrZero(rot.x);
        auto const in  = readInput(client);
        out.input      = {
            in.w,
            in.a,
            in.s,
            in.d,
            in.jump,
            in.sprint,
            in.sneak,
            false,
            hardwareInputLocked(),
            finiteOrZero(in.moveX),
            finiteOrZero(in.moveY)
        };
        if (auto action = player.getEntityContext().tryGetComponent<PlayerActionComponent>())
            out.input.actionSprint = action->mPlayerActions->test(PlayerActionStartSprinting);
        out.onGround  = player.isOnGround();
        out.sprinting = player.isSprinting();
        out.sneaking  = player.isSneaking();
        out.swimming  = player.isSwimming();
        out.flying    = player.isFlying();
        out.mayfly    = player.getAbilities().getBool(AbilitiesIndex::MayFly);
        out.inWater   = player.isInWater();
        out.alive     = player.isAlive();
        out.health    = player.getHealth();
        out.maxHealth = player.getMaxHealth();
        out.gamemode  = static_cast<int>(player.getPlayerGameType());
        out.dimension = static_cast<int>(player.getDimensionId());
        // These SDK accessors return owning strings. Bound their copies on the
        // owning thread; never move an ItemStack/Player pointer to the consumer.
        copyText(out.name, player.getRealName(), out.truncated);
        auto const uuid  = player.getUuid();
        out.uuidA        = uuid.a;
        out.uuidB        = uuid.b;
        out.runtimeId    = static_cast<std::uint64_t>(player.getRuntimeID());
        out.context      = reinterpret_cast<std::uint64_t>(&player.getEntityContext());
        out.selectedSlot = player.getSelectedItemSlot();
        Attribute const* attributes[] =
            {&Player::HUNGER(), &Player::SATURATION(), &Player::EXHAUSTION(), &Player::LEVEL(), &Player::EXPERIENCE()};
        for (std::size_t i = 0; i < out.attributes.size(); ++i) {
            auto instance = player.getAttribute(*attributes[i]);
            if (instance.mPtr) {
                out.haveAttribute[i] = true;
                out.attributes[i]    = finiteOrZero(instance.mPtr->mCurrentValue);
            }
        }
        refreshBindings(false);
        out.bindings = {
            mBindings.forward,
            mBindings.back,
            mBindings.left,
            mBindings.right,
            mBindings.sneak,
            mBindings.jump,
            mBindings.sprint
        };
        captureItem(player.getSelectedItem(), out.item, out.truncated);
        out.itemUseTicks = player.getItemUseDuration();
        auto const empty = static_cast<uint>(MobEffectInstance::NO_EFFECT().mId);
        for (auto const& effect : player.getAllEffects()) {
            if (static_cast<uint>(effect.mId) == empty) continue;
            if (out.effectCount == out.effects.size()) {
                out.truncated = true;
                break;
            }
            auto& row = out.effects[out.effectCount++];
            row       = {
                static_cast<int>(effect.mId),
                static_cast<int>(effect.mDuration->mValue),
                static_cast<int>(effect.mAmplifier),
                static_cast<bool>(effect.mAmbient)
            };
        }
        BlockPoint positions[] = {
            {static_cast<int>(std::floor(out.feet.x)),
             static_cast<int>(std::floor(out.feet.y)),
             static_cast<int>(std::floor(out.feet.z))},
            {static_cast<int>(std::floor(out.feet.x)),
             static_cast<int>(std::floor(out.feet.y)) - 1,
             static_cast<int>(std::floor(out.feet.z))},
            {static_cast<int>(std::floor(out.head.x)),
             static_cast<int>(std::floor(out.head.y)),
             static_cast<int>(std::floor(out.head.z))},
            {static_cast<int>(std::floor(out.head.x)),
             static_cast<int>(std::floor(out.head.y)) + 1,
             static_cast<int>(std::floor(out.head.z))}
        };
        for (std::size_t i = 0; i < out.blocks.size(); ++i) {
            BlockRow row;
            row.pos          = positions[i];
            out.haveBlock[i] = readBlock(player, row, out.truncated, request.test == CaptureTest::Partial && i == 0);
            if (out.haveBlock[i]) out.blocks[i] = row.name;
            else out.warning = "state_blocks_fault";
        }
    });
    if (!ok) out.error = "state_fault";
    out.sampleUs = static_cast<std::uint32_t>(std::min<std::uint64_t>(qpcElapsedUs(start, qpcNow()), UINT32_MAX));
}

void ProbeMod::captureButtons(ClientInstance& client, CaptureRequest const& request, ButtonSnapshot& out) {
    if (!guardedGameRead([&] {
            injectCaptureFault(request.test);
            auto  input   = client.getMinecraftInput();
            auto* handler = input ? input->mInputHandler.get() : nullptr;
            if (!handler) return;
            out.haveQueue = true;
            out.queue     = handler->mInputEventQueue.get() != nullptr;
            for (auto const& entry : *handler->mButtonDownHandlerMap) {
                if (out.count == out.ids.size()) {
                    out.truncated = true;
                    break;
                }
                out.ids[out.count++] = entry.first;
            }
        }))
        out.error = "buttons_fault";
}

void ProbeMod::captureBlocks(LocalPlayer& player, BlockRequest const& request, BlockSnapshot& out) {
    if (!guardedGameRead([&] {
            injectCaptureFault(request.test);
            for (std::size_t i = 0; i < request.count; ++i) {
                auto& row = out.rows[out.count];
                row.pos   = request.positions[i];
                if (!readBlock(player, row, out.truncated)) {
                    out.error = "blocks_fault";
                    break;
                }
                ++out.count;
            }
        }))
        out.error = "blocks_fault";
}

bool ProbeMod::captureScan(LocalPlayer& player, ScanQuery const& request, ScanSnapshot& out) {
    auto const start = qpcNow();
    bool       done  = true;
    if (!guardedGameRead([&] {
            injectCaptureFault(request.test);
            done = advanceScan(request, out, mTick.load(std::memory_order_relaxed), [&](BlockRow& row) {
                return readBlock(player, row, out.namesTruncated);
            });
        }))
        out.error = "scan_fault";
    out.maxBatchUs = std::max(
        out.maxBatchUs,
        static_cast<std::uint32_t>(std::min<std::uint64_t>(qpcElapsedUs(start, qpcNow()), UINT32_MAX))
    );
    return done || out.error != nullptr;
}

void ProbeMod::resetQueries() {
    cancelTeleport("capture_cancelled", true);
    cancelFlight("capture_cancelled", true);
    resetInputEvents();
    mStateCapture.reset();
    mButtonCapture.reset();
    mBlockCapture.reset();
    mScanCapture.reset();
    mInventoryCapture.reset();
    mSlotCapture.reset();
    mEntityCapture.reset();
    mEcsCapture.reset();
}
void ProbeMod::cancelQueries(char const* reason) {
    cancelTeleport(reason);
    cancelFlight(reason);
    cancelInputEvents(reason);
    mStateCapture.cancel(reason);
    mButtonCapture.cancel(reason);
    mBlockCapture.cancel(reason);
    mScanCapture.cancel(reason);
    mInventoryCapture.cancel(reason);
    mSlotCapture.cancel(reason);
    mEntityCapture.cancel(reason);
    mEcsCapture.cancel(reason);
}

void ProbeMod::drainQueries() {
    drainTeleport();
    drainFlight();
    drainInputEvents();
    drainEntities();
    drainEcs();
    auto const now     = GetTickCount64();
    auto       publish = [&](auto& done, nlohmann::json out) {
        auto line = out.dump();
        // A fast client may submit again as soon as enqueue exposes the line.
        // Release the snapshot before making its response visible.
        if (done.release()) enqueue(std::move(line));
    };
    auto emitFailure = [&](auto& done, char const* phase, char const* error) {
        publish(
            done,
            nlohmann::json{
                {"event",  "error"},
                {"reason", error  },
                {"phase",  phase  }
        }
        );
    };
    if (auto done = mSlotCapture.take(now)) {
        auto const& s = done.snapshot();
        if (auto error = done.error ? done.error : s.error) emitFailure(done, "select_slot", error);
        else {
            nlohmann::json item = nullptr;
            if (s.present) item = {{"type", s.type.c_str()}, {"count", s.count}};
            publish(done, {{"event", "select_slot_done"}, {"slot", s.slot}, {"applied", s.applied},
                           {"ok", s.slot == s.applied}, {"item", std::move(item)}, {"truncated", s.truncated}});
        }
    }
    if (auto done = mStateCapture.take(now)) {
        auto const& s = done.snapshot();
        if (auto error = done.error ? done.error : s.error) emitFailure(done, "state", error);
        else publish(done, stateJson(s));
    }
    if (auto done = mButtonCapture.take(now)) {
        auto const& s = done.snapshot();
        if (auto error = done.error ? done.error : s.error) emitFailure(done, "buttons", error);
        else {
            nlohmann::json out{
                {"event",     "buttons"              },
                {"ids",       nlohmann::json::array()},
                {"truncated", s.truncated            }
            };
            for (std::size_t i = 0; i < s.count; ++i) out["ids"].push_back(s.ids[i]);
            if (s.haveQueue) out["queue"] = s.queue;
            publish(done, std::move(out));
        }
    }
    if (auto done = mInventoryCapture.take(now)) {
        auto const& s = done.snapshot();
        if (auto error = done.error ? done.error : s.error) emitFailure(done, "inventory", error);
        else {
            nlohmann::json out{
                {"event",         "inventory"            },
                {"selected_slot", s.selectedSlot         },
                {"size",          s.size                 },
                {"slots",         nlohmann::json::array()},
                {"armor",         nlohmann::json::array()},
                {"offhand",       itemJson(s.offhand)    },
                {"truncated",     s.truncated            },
                {"sample_us",     s.sampleUs             }
            };
            for (std::size_t i = 0; i < s.count; ++i)
                out["slots"].push_back({
                    {"slot", i                   },
                    {"item", itemJson(s.slots[i])}
                });
            for (std::size_t i = 0; i < s.armorCount; ++i)
                out["armor"].push_back({
                    {"slot", i                   },
                    {"item", itemJson(s.armor[i])}
                });
            publish(done, std::move(out));
        }
    }
    if (auto done = mBlockCapture.take(now)) {
        auto const& s = done.snapshot();
        if (auto error = done.error ? done.error : s.error) emitFailure(done, "blocks", error);
        else {
            nlohmann::json out{
                {"event",     "blocks"               },
                {"blocks",    nlohmann::json::array()},
                {"truncated", s.truncated            }
            };
            for (std::size_t i = 0; i < s.count; ++i) {
                auto const& row = s.rows[i];
                out["blocks"].push_back({
                    {"x",    row.pos.x       },
                    {"y",    row.pos.y       },
                    {"z",    row.pos.z       },
                    {"name", row.name.c_str()},
                    {"addr", row.address     }
                });
            }
            publish(done, std::move(out));
        }
    }
    if (auto done = mScanCapture.take(now)) {
        auto const& s = done.snapshot();
        if (auto error = done.error ? done.error : s.error) emitFailure(done, "scan", error);
        else {
            nlohmann::json out{
                {"event",           "scan"                        },
                {"from",            {s.from.x, s.from.y, s.from.z}},
                {"to",              {s.to.x, s.to.y, s.to.z}      },
                {"blocks",          nlohmann::json::array()       },
                {"count",           s.count                       },
                {"truncated",       s.truncated                   },
                {"names_truncated", s.namesTruncated              },
                {"visited",         s.visited                     },
                {"batches",         s.batches                     },
                {"batch_limit",     ScanSnapshot::BatchSize       },
                {"max_batch_us",    s.maxBatchUs                  },
                {"first_tick",      s.firstTick                   },
                {"last_tick",       s.lastTick                    }
            };
            if (s.truncated) out["truncated_reason"] = "result_limit";
            for (std::size_t i = 0; i < s.count; ++i) {
                auto const& row = s.rows[i];
                out["blocks"].push_back({
                    {"x",    row.pos.x       },
                    {"y",    row.pos.y       },
                    {"z",    row.pos.z       },
                    {"name", row.name.c_str()}
                });
            }
            publish(done, std::move(out));
        }
    }
}

} // namespace probe
