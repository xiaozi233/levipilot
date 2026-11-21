#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

namespace {
// The game's internal button ids used by dmf-mpk for the nine macro columns.
// They are not keyboard key codes.
constexpr std::uint32_t ActionW      = 3205050568u;
constexpr std::uint32_t ActionA      = 3528728616u;
constexpr std::uint32_t ActionS      = 375632893u;
constexpr std::uint32_t ActionD      = 3949230141u;
constexpr std::uint32_t ActionSprint = 2427380341u;
constexpr std::uint32_t ActionSneak  = 4009177245u;
constexpr std::uint32_t ActionJump   = 2406325663u;
constexpr std::uint32_t ActionRmb    = 4073882376u;
constexpr std::uint32_t ActionLmb    = 2320499196u;
constexpr std::array<std::uint32_t, 9>
    ActionIds{ActionW, ActionA, ActionS, ActionD, ActionSprint, ActionSneak, ActionJump, ActionRmb, ActionLmb};

// Physical key actions, in the same order as ControlState's digital inputs.
// The actual key codes come from the guest's keyboard remapping at runtime.
constexpr size_t KeyForward        = 0;
constexpr size_t KeyJump           = 5;
constexpr size_t KeyCount          = 7;
constexpr int    kBindRefreshTicks = 20;
// Construct SDK lookup keys before any tick; the SDK accepts const string refs.
std::array<std::string, 7> const
    kBindingNames{"key.forward", "key.back", "key.left", "key.right", "key.sneak", "key.jump", "key.sprint"};
} // namespace

bool ProbeMod::refreshBindings(bool releaseOnChange) {
    return guardedControl(ControlOp::Bindings, [&] {
        auto client = ll::service::getClientInstance();
        if (!client) {
            return false;
        }
        auto layout = client->getOptions().getCurrentKeyboardRemapping();
        if (!layout) {
            return false;
        }

        auto const read = [&layout](std::string const& action, int fallback) {
            auto const& mapping = layout->getKeymappingByAction(action);
            if (!mapping.isAssigned()) {
                return fallback;
            }
            auto const& keys = *mapping.mKeys;
            return keys.empty() ? fallback : keys.front();
        };

        KeyBindings next;
        next.forward = read(kBindingNames[0], 87); // W
        next.back    = read(kBindingNames[1], 83); // S
        next.left    = read(kBindingNames[2], 65); // A
        next.right   = read(kBindingNames[3], 68); // D
        next.sneak   = read(kBindingNames[4], 16); // Lshift
        next.jump    = read(kBindingNames[5], 32); // Space
        next.sprint  = read(kBindingNames[6], 0);  // 0 = unassigned

        bool const changed = !mInputSession.bindingsValid || next.forward != mInputSession.bindings.forward
                          || next.back != mInputSession.bindings.back || next.left != mInputSession.bindings.left
                          || next.right != mInputSession.bindings.right || next.sneak != mInputSession.bindings.sneak
                          || next.jump != mInputSession.bindings.jump || next.sprint != mInputSession.bindings.sprint;
        if (changed && releaseOnChange && !releaseRealInput()) return false;
        mInputSession.bindings      = next;
        mInputSession.bindingsValid = true;
        mInputSession.bindingsTick  = mTick.load(std::memory_order_relaxed);
        return true;
    });
}

void ProbeMod::publishHeldInput() {
    std::uint32_t mask = 0;
    for (unsigned i = 0; i < KeyCount; ++i)
        if (mInputSession.heldKeys[i]) mask |= 1u << i;
    if (mInputSession.heldLmb) mask |= 1u << 7;
    if (mInputSession.heldRmb) mask |= 1u << 8;
    mInputSession.controlHeldMask.store(mask);
}

bool ProbeMod::releaseRealInput() {
    bool      released        = true;
    int const codes[KeyCount] = {
        mInputSession.bindings.forward,
        mInputSession.bindings.back,
        mInputSession.bindings.left,
        mInputSession.bindings.right,
        mInputSession.bindings.sneak,
        mInputSession.bindings.jump,
        mInputSession.bindings.sprint,
    };
    InjectionScope injecting(mInputSession.injecting);
    for (size_t i = 0; i < KeyCount; ++i) {
        if (!mInputSession.heldKeys[i] || codes[i] == 0) {
            continue;
        }
        if (!trackedInputEdge(mInputSession.heldKeys[i], false, [&] {
                return guardedControl(ControlOp::KeyUp, [&] { return hidKeyEdge(codes[i], false, true) == nullptr; });
            }))
            released = false;
    }
    auto releaseMouse = [&](int button, bool& held) {
        return trackedInputEdge(held, false, [&] {
            return guardedControl(ControlOp::MouseUp, [&] {
                auto& device = Mouse::_instance();
                device.feed(static_cast<char>(button), 0, device._x, device._y, 0, 0, false);
                return true;
            });
        });
    };
    if (!releaseMouse(1, mInputSession.heldLmb)) released = false;
    if (!releaseMouse(2, mInputSession.heldRmb)) released = false;
    if (released) mInputSession.sprintTapPhase = 0;
    mInputSession.controlCleanupPending = !released;
    publishHeldInput();
    return released;
}

bool ProbeMod::performClick(LocalPlayer& player, int button) {
    ClickResult result;
    result.reqId                 = mClickReqId.exchange(0, std::memory_order_acquire);
    result.button                = button;
    mInputSession.injectingClick = true;
    try {
        if (!guardedGameRead([&] {
                injectCaptureFault(mClickCaptureTest.exchange(CaptureTest::None));
                result.gameMode = static_cast<bool>(player.mGameMode);
                if (!player.mGameMode) return;
                GameMode& gm    = *player.mGameMode;
                HitResult hit   = player.traceRay(mClickReach.load(), true, true);
                result.hitValid = true;
                result.hitType  = static_cast<int>(hit.mType);
                result.x        = hit.mBlock.x;
                result.y        = hit.mBlock.y;
                result.z        = hit.mBlock.z;
                result.face     = static_cast<int>(hit.mFacing);
                result.hitX     = hit.mPos.x;
                result.hitY     = hit.mPos.y;
                result.hitZ     = hit.mPos.z;
                if (button == 1) {
                    if (hit.mType == HitResultType::Tile) {
                        bool destroyed = false;
                        gm.startDestroyBlock(hit.mBlock, hit.mFacing, destroyed);
                        if (!destroyed) gm.destroyBlock(hit.mBlock, hit.mFacing);
                    } else if (hit.mType == HitResultType::Entity && hit.getEntity())
                        gm.attack(*hit.getEntity(), hit.mPos);
                    return;
                }
                auto& item = const_cast<ItemStack&>(player.getSelectedItem());
                if (hit.mType == HitResultType::Tile) {
                    auto const& block = player.getDimensionBlockSourceConst().getBlock(hit.mBlock);
                    gm.useItemOn(item, hit.mBlock, hit.mFacing, hit.mPos, HandSlot::Mainhand, &block, true);
                } else if (hit.mType == HitResultType::Entity && hit.getEntity()) {
                    if (!player.getSelectedItem().isNull()) gm.interact(*hit.getEntity(), hit.mPos, HandSlot::Mainhand);
                } else gm.useItem(item, HandSlot::Mainhand);
            }))
            result.error = "click_fault";
    } catch (...) {
        result.error = "click_failed";
    }
    mInputSession.injectingClick = false;
    if (!mClickResults.push(result)) mClickResultsLost.fetch_add(1, std::memory_order_relaxed);
    wakeSpool();
    return result.error == nullptr;
}

void ProbeMod::drainClicks() {
    ClickResult result;
    while (mClickResults.pop(result)) {
        nlohmann::json out{
            {"event",     "click_result"                       },
            {"button",    result.button == 1 ? "left" : "right"},
            {"game_mode", result.gameMode                      },
            {"ok",        result.error == nullptr              }
        };
        if (result.hitValid)
            out.update({
                {"hit_type", result.hitType                                                                   },
                {"block",    {result.x, result.y, result.z}                                                   },
                {"face",     result.face                                                                      },
                {"hit_pos",  {finiteOrZero(result.hitX), finiteOrZero(result.hitY), finiteOrZero(result.hitZ)}}
            });
        if (result.error) out["reason"] = result.error;
        attachReqId(out, result.reqId);
        enqueue(out.dump());
    }
    if (auto lost = mClickResultsLost.exchange(0))
        enqueue(
            nlohmann::json{
                {"event",  "click_diagnostic"},
                {"reason", "buffer_full"     },
                {"lost",   lost              }
        }.dump()
        );
}

void ProbeMod::drainControlDiagnostics() {
    for (unsigned i = 0; i < ControlDiagnostics::Operations; ++i) {
        auto const op     = static_cast<ControlOp>(i);
        auto const counts = mInputSession.controlDiagnostics.take(op);
        if (!(counts[0] || counts[1] || counts[2])) continue;
        enqueue(
            nlohmann::json{
                {"event",       "control_error"  },
                {"operation",   controlOpName(op)},
                {"faults",      counts[0]        },
                {"failures",    counts[1]        },
                {"unavailable", counts[2]        }
        }.dump()
        );
    }
}

bool ProbeMod::applyRealInput(LocalPlayer&, ControlState const& state) {
    // Finish uncertain releases before admitting another desired state.
    if (mInputSession.controlCleanupPending.load() && !releaseRealInput()) return false;
    std::uint64_t const tick = mTick.load(std::memory_order_relaxed);
    if (!mInputSession.bindingsValid || tick - mInputSession.bindingsTick >= kBindRefreshTicks) {
        if (!refreshBindings(true)) {
            mInputSession.controlCleanupPending = true;
            return false;
        }
    }

    // If the guest has a sprint key (currently often R), press it. If sprint is
    // unassigned, reproduce the vanilla double-tap-forward gesture instead.
    bool const sprintBound     = mInputSession.bindings.sprint != 0;
    bool const sprintRequested = state.sprint && state.forward > 0.0f;
    if (!sprintBound && sprintRequested) {
        if (mInputSession.sprintTapPhase == 0) {
            mInputSession.sprintTapPhase = mInputSession.heldKeys[KeyForward] ? 2 : 1;
        }
    } else {
        mInputSession.sprintTapPhase = 0;
    }
    bool const wantW = state.forward > 0.0f && (!sprintRequested || !sprintBound || mInputSession.sprintTapPhase != 2);

    bool const want[KeyCount] = {
        wantW,
        state.forward < 0.0f,
        state.strafe < 0.0f,
        state.strafe > 0.0f,
        state.sneak,
        state.jump,
        sprintBound && sprintRequested,
    };
    int const codes[KeyCount] = {
        mInputSession.bindings.forward,
        mInputSession.bindings.back,
        mInputSession.bindings.left,
        mInputSession.bindings.right,
        mInputSession.bindings.sneak,
        mInputSession.bindings.jump,
        mInputSession.bindings.sprint,
    };
    bool           applied = true;
    InjectionScope injecting(mInputSession.injecting);
    for (size_t i = 0; i < KeyCount; ++i) {
        if (mInputSession.heldKeys[i] == want[i] || codes[i] == 0) {
            continue;
        }
        // Hardware is the origin a physical keyboard produces. The injection
        // guard above keeps input_lock from dropping our own edge.
        if (i == KeyJump && want[i]) mInputSession.jumpEdgeThisTick = true;
        if (!trackedInputEdge(mInputSession.heldKeys[i], want[i], [&] {
                return guardedControl(want[i] ? ControlOp::KeyDown : ControlOp::KeyUp, [&] {
                    return hidKeyEdge(codes[i], want[i], true) == nullptr;
                });
            })) {
            applied = false;
            break;
        }
    }
    if (!sprintBound && sprintRequested) {
        if (mInputSession.sprintTapPhase == 1) {
            mInputSession.sprintTapPhase = 2; // next tick release forward
        } else if (mInputSession.sprintTapPhase == 2) {
            mInputSession.sprintTapPhase = 3; // next tick press forward and latch sprint
        }
    }

    auto button = [this](int action, bool down, bool& held) {
        return trackedInputEdge(held, down, [&] {
            return guardedControl(down ? ControlOp::MouseDown : ControlOp::MouseUp, [&] {
                auto& device = Mouse::_instance();
                device.feed(static_cast<char>(action), down ? 1 : 0, device._x, device._y, 0, 0, false);
                mInputSession.mouseInjected.fetch_add(1, std::memory_order_relaxed);
                return true;
            });
        });
    };
    if (applied) applied = button(1, state.lmb, mInputSession.heldLmb);
    if (applied) applied = button(2, state.rmb, mInputSession.heldRmb);
    mInputSession.controlCleanupPending = !applied;
    publishHeldInput();
    return applied;
}

bool ProbeMod::applyControls(ClientInstance& client, LocalPlayer& player, ControlState const& state) {
    // Default path: physical key edges only. Legacy flag/queue/handler/impulse
    // modes exist only for reproducing old measurements and require an explicit
    // input_cfg force.
    if (mInputSession.inputMode.load(std::memory_order_relaxed) == ControlInputMode::Hardware
        && mInputSession.jumpImpulseMode.load(std::memory_order_relaxed) == JumpImpulse::None) {
        return applyRealInput(player, state);
    }
    if (!releaseRealInput()) return false;
    return guardedControl(ControlOp::Legacy, [&] { return applyLegacyControls(client, player, state); });
}

bool ProbeMod::applyLegacyControls(ClientInstance& client, LocalPlayer& player, ControlState const& state) {

    if (state.active) {
        mInputSession.injected = true;
        ClientMoveInputHandler::_updateClientMoveVector(state.strafe, state.forward, FocusImpact::Neutral, client);
    } else if (mInputSession.injected) {
        ClientMoveInputHandler::_updateClientMoveVector(0.0f, 0.0f, FocusImpact::Neutral, client);
        mInputSession.injected = false;
    }

    bool const engaged  = state.active;
    bool const jumpNow  = engaged && state.jump;
    bool const jumpEdge = jumpNow && !mInputSession.prevJump;
    if (jumpEdge) {
        mInputSession.jumpEdgeThisTick = true;
    }
    auto const mode = mInputSession.inputMode.load();

    if (hasInputMode(mode, ControlInputMode::DirectFlags) && (engaged || mInputSession.inputActive)) {
        if (engaged) mInputSession.inputActive = true; // Retain cleanup intent across a partial SDK call.
        // Directly maintain the local MoveInputComponent flags. Only touch them
        // while our own control is active; otherwise we would overwrite real
        // keyboard/mouse input every tick (this is what broke manual jump/fly).
        if (auto* input = ClientMoveInputHandler::getMoveInput(client)) {
            auto& flags      = *input->mFlagValues;
            auto& stateFlags = *input->mInputState->mFlagValues;
            flags.set(MoveInputComponentSneaking, engaged && state.sneak);
            flags.set(MoveInputComponentSprinting, engaged && state.sprint);
            flags.set(MoveInputComponentJumping, jumpNow);
            stateFlags.set(MoveInputStateSneakDown, engaged && state.sneak);
            stateFlags.set(MoveInputStateSprintDown, engaged && state.sprint);
            stateFlags.set(MoveInputStateJumpDown, jumpNow);
            stateFlags.set(24, !jumpNow && mInputSession.prevJump); // JumpInputWasReleased
            stateFlags.set(25, jumpEdge);                           // JumpInputWasPressed
            stateFlags.set(26, jumpNow);                            // JumpInputCurrentlyDown
        }
        if (engaged) {
            player.setSneaking(state.sneak);
        } else {
            player.setSneaking(false);
        }
        mInputSession.inputActive = engaged;
    }

    if (hasInputMode(mode, ControlInputMode::Queue)) {
        if (auto* queue = inputQueue(client)) {
            bool const buttons[9] =
                {false, false, false, false, state.sprint, state.sneak, state.jump, state.rmb, state.lmb};
            for (size_t i = 4; i < 9; ++i) {
                // Sprint is already applied through the MoveInput flags above.
                // Re-sending it as the sprint *button* made the game jump on flat
                // ground (measured 2026-10-25: modes 2/4 + sprint=true + forward=0
                // produced a 0.42 arc), which contaminated sprint experiments.
                if (i == 4 && hasInputMode(mode, ControlInputMode::DirectFlags)) {
                    mInputSession.buttonStates[i] = false;
                    continue;
                }
                if (buttons[i] != mInputSession.buttonStates[i]) {
                    if (buttons[i]) mInputSession.buttonStates[i] = true;
                    queue->enqueueButton(
                        ActionIds[i],
                        buttons[i] ? ButtonState::Down : ButtonState::Up,
                        false,
                        static_cast<FocusImpact>(mInputSession.inputFocus.load()),
                        mInputSession.inputController.load(),
                        false,
                        0.0f,
                        mInputSession.inputSource.load()
                    );
                    mInputSession.buttonStates[i] = buttons[i];
                }
            }
        }
    }

    if (hasInputMode(mode, ControlInputMode::Handler)) {
        if (auto* handler = inputHandler(client)) {
            bool const buttons[9] =
                {false, false, false, false, state.sprint, state.sneak, state.jump, state.rmb, state.lmb};
            for (size_t i = 4; i < 9; ++i) {
                if (i == 4 && hasInputMode(mode, ControlInputMode::DirectFlags)) {
                    mInputSession.handlerStates[i] = false;
                    continue;
                }
                if (buttons[i] != mInputSession.handlerStates[i]) {
                    if (buttons[i]) mInputSession.handlerStates[i] = true;
                    invokeButtonHandlers(*handler, buttons[i], ActionIds[i], client);
                    mInputSession.handlerStates[i] = buttons[i];
                }
            }
        }
    }

    // Explicit legacy comparison only: the old direct/queue/handler pipeline
    // could delay the first three position samples. This historical result
    // does not apply to default Hardware HID, which never enters this branch.
    if (jumpNow && player.isOnGround()) {
        auto const impulse = mInputSession.jumpImpulseMode.load(std::memory_order_relaxed);
        if (hasJumpImpulse(impulse, JumpImpulse::FromGround)) {
            player.jumpFromGround();
        }
        if (hasJumpImpulse(impulse, JumpImpulse::PendingScale)) {
            MobJump::setJumpPendingScale(player.getEntityContext(), 1.0f);
        }
    }

    if (hasInputMode(mode, ControlInputMode::ToggleFallback)) {
        if (state.sprint != mInputSession.prevSprint) {
            mInputSession.prevSprint = state.sprint;
            if (player.isSprinting() != state.sprint) {
                ClientMoveInputHandler::_toggleClientSprint(FocusImpact::Neutral, client);
            }
        }
        if (state.sneak != mInputSession.prevSneak) {
            mInputSession.prevSneak = state.sneak;
            player.setSneaking(state.sneak);
        }
    }

    mInputSession.prevJump   = jumpNow;
    mInputSession.prevSprint = state.sprint;
    mInputSession.prevSneak  = state.sneak;

    if (state.yawDelta != 0.0f || state.pitchDelta != 0.0f) {
        return applyTurnDelta(player, Vec2{state.pitchDelta, state.yawDelta});
    }
    return true;
}

bool ProbeMod::applyMacroTickEvents(LocalPlayer& player, MacroTick const& tick) {
    // Event-level macro replay uses the same physical layer as set_input/move:
    // digital key states through Hardware HID, including the double-tap-W
    // sprint gesture. No MoveInput flags are written here.
    ControlState state;
    state.forward      = tick.forward();
    state.strafe       = tick.strafe();
    state.sprint       = tick.sprint;
    state.sneak        = tick.sneak;
    state.jump         = tick.jump;
    state.lmb          = tick.lmb;
    state.rmb          = tick.rmb;
    state.active       = tick.w || tick.a || tick.s || tick.d || tick.sprint || tick.sneak || tick.jump;
    bool const applied = applyRealInput(player, state);

    return applied && ((tick.yaw == 0.0f && tick.pitch == 0.0f) || applyTurnDelta(player, Vec2{tick.pitch, tick.yaw}));
}

bool ProbeMod::releaseMacroInputs() { return releaseRealInput(); }

bool ProbeMod::applyMacroTick(ClientInstance& client, LocalPlayer& player, MacroTick const& tick, bool eventLevel) {
    if (eventLevel) {
        return applyMacroTickEvents(player, tick);
    }
    if (tick.rmb != mInputSession.prevMacroRmb) {
        if (tick.rmb) {
            if (!performClick(player, 2)) return false;
        }
        mInputSession.prevMacroRmb = tick.rmb;
    }
    if (tick.lmb != mInputSession.prevMacroLmb) {
        if (tick.lmb) {
            if (!performClick(player, 1)) return false;
        }
        mInputSession.prevMacroLmb = tick.lmb;
    }

    ControlState state;
    state.forward = tick.forward();
    state.strafe  = tick.strafe();
    state.sprint  = tick.sprint;
    state.sneak   = tick.sneak;
    state.jump    = tick.jump;
    // Clicks are applied directly above (GameMode), not through input flags.
    state.rmb        = false;
    state.lmb        = false;
    state.active     = tick.w || tick.a || tick.s || tick.d || tick.sprint || tick.sneak || tick.jump;
    state.yawDelta   = tick.yaw;
    state.pitchDelta = tick.pitch;
    if (mInputSession.inputMode.load() == ControlInputMode::Hardware
        && mInputSession.jumpImpulseMode.load() == JumpImpulse::None) {
        bool const applied = applyRealInput(player, state);
        return applied
            && ((tick.yaw == 0.0f && tick.pitch == 0.0f) || applyTurnDelta(player, Vec2{tick.pitch, tick.yaw}));
    }
    return applyControls(client, player, state);
}


void ProbeMod::latchActionClicks(LocalPlayer& player) {
    if (mInputSession.injectingClick.load(std::memory_order_relaxed)) {
        return;
    }
    guardedControl(ControlOp::Actions, [&] {
        auto action = player.getEntityContext().tryGetComponent<PlayerActionComponent>();
        if (!action) {
            return true;
        }
        auto const& bits = *action->mPlayerActions;
        // PlayerActionType: StartDestroyBlock=0, InteractWithBlock=25,
        // PredictDestroyBlock=26, ContinueDestroyBlock=27, StartItemUseOn=28,
        // MissedSwing=31, StartUsingItem=37. Only the rising edge becomes a click,
        // otherwise mining would keep the LMB column true for the whole hold.
        bool const lmb = bits.test(0) || bits.test(26) || bits.test(27) || bits.test(31);
        bool const rmb = bits.test(25) || bits.test(28) || bits.test(37);
        if (lmb && !mInputSession.prevActionLmb) {
            mInputSession.mouseLeftLatch = true;
        }
        if (rmb && !mInputSession.prevActionRmb) {
            mInputSession.mouseRightLatch = true;
        }
        mInputSession.prevActionLmb = lmb;
        mInputSession.prevActionRmb = rmb;
        return true;
    });
}


} // namespace probe
