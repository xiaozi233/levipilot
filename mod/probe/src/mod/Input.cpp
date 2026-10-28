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
constexpr size_t KeyForward = 0;
constexpr size_t KeyBack    = 1;
constexpr size_t KeyLeft    = 2;
constexpr size_t KeyRight   = 3;
constexpr size_t KeySneak   = 4;
constexpr size_t KeyJump    = 5;
constexpr size_t KeySprint  = 6;
constexpr size_t KeyCount   = 7;
constexpr int    kBindRefreshTicks = 20;
} // namespace

bool ProbeMod::refreshBindings(bool releaseOnChange) {
    auto client = ll::service::getClientInstance();
    if (!client) {
        return false;
    }
    auto layout = client->getOptions().getCurrentKeyboardRemapping();
    if (!layout) {
        return false;
    }

    // Static strings: the lookup happens on the tick thread, so avoid
    // constructing std::string temporaries there.
    static std::string const kForward = "key.forward";
    static std::string const kBack    = "key.back";
    static std::string const kLeft    = "key.left";
    static std::string const kRight   = "key.right";
    static std::string const kSneak   = "key.sneak";
    static std::string const kJump    = "key.jump";
    static std::string const kSprint  = "key.sprint";

    auto const read = [&layout](std::string const& action, int fallback) {
        auto const& mapping = layout->getKeymappingByAction(action);
        if (!mapping.isAssigned()) {
            return fallback;
        }
        auto const& keys = *mapping.mKeys;
        return keys.empty() ? fallback : keys.front();
    };

    KeyBindings next;
    next.forward = read(kForward, 87);  // W
    next.back    = read(kBack, 83);     // S
    next.left    = read(kLeft, 65);     // A
    next.right   = read(kRight, 68);    // D
    next.sneak   = read(kSneak, 16);    // Lshift
    next.jump    = read(kJump, 32);     // Space
    next.sprint  = read(kSprint, 0);    // 0 = unassigned

    bool const changed = !mBindingsValid || next.forward != mBindings.forward
                      || next.back != mBindings.back || next.left != mBindings.left
                      || next.right != mBindings.right || next.sneak != mBindings.sneak
                      || next.jump != mBindings.jump || next.sprint != mBindings.sprint;
    if (changed && releaseOnChange) {
        releaseRealInput();
    }
    mBindings      = next;
    mBindingsValid = true;
    mBindingsTick  = mTick.load(std::memory_order_relaxed);
    return true;
}

void ProbeMod::releaseRealInput() {
    std::string error;
    int const   codes[KeyCount] = {
        mBindings.forward,
        mBindings.back,
        mBindings.left,
        mBindings.right,
        mBindings.sneak,
        mBindings.jump,
        mBindings.sprint,
    };
    mInjectingInput.store(true, std::memory_order_release);
    for (size_t i = 0; i < KeyCount; ++i) {
        if (!mHeldKeys[i] || codes[i] == 0) {
            continue;
        }
        hidKeyEvent(codes[i], false, true, error);
        mHeldKeys[i] = false;
    }
    auto& device = Mouse::_instance();
    if (mHeldLmb) {
        device.feed(1, 0, device._x, device._y, 0, 0, false);
        mHeldLmb = false;
    }
    if (mHeldRmb) {
        device.feed(2, 0, device._x, device._y, 0, 0, false);
        mHeldRmb = false;
    }
    mInjectingInput.store(false, std::memory_order_release);
}

void ProbeMod::performClick(LocalPlayer& player, int button) {
    mInjectingClick = true;
    struct InjectGuard {
        std::atomic_bool& flag;
        ~InjectGuard() { flag = false; }
    } guard{mInjectingClick};

    nlohmann::json result;
    result["event"]     = "click_result";
    result["button"]    = button == 1 ? "left" : "right";
    result["game_mode"] = static_cast<bool>(player.mGameMode);

    if (!player.mGameMode) {
        enqueue(result.dump());
        return;
    }
    GameMode& gm       = *player.mGameMode;
    HitResult hit      = player.traceRay(mClickReach.load(), true, true);
    result["hit_type"] = static_cast<int>(hit.mType);
    result["block"]    = {hit.mBlock.x, hit.mBlock.y, hit.mBlock.z};
    result["face"]     = static_cast<int>(hit.mFacing);
    result["hit_pos"]  = {hit.mPos.x, hit.mPos.y, hit.mPos.z};

    if (button == 1) {
        if (hit.mType == HitResultType::Tile) {
            bool destroyed = false;
            gm.startDestroyBlock(hit.mBlock, hit.mFacing, destroyed);
            if (!destroyed) {
                gm.destroyBlock(hit.mBlock, hit.mFacing);
            }
        } else if (hit.mType == HitResultType::Entity && hit.getEntity()) {
            gm.attack(*hit.getEntity(), hit.mPos);
        }
        enqueue(result.dump());
        return;
    }

    auto& item = const_cast<ItemStack&>(player.getSelectedItem());
    if (hit.mType == HitResultType::Tile) {
        auto const& block = player.getDimensionBlockSourceConst().getBlock(hit.mBlock);
        gm.useItemOn(item, hit.mBlock, hit.mFacing, hit.mPos, HandSlot::Mainhand, &block, true);
    } else if (hit.mType == HitResultType::Entity && hit.getEntity()) {
        // Empty-hand entity use dereferences a null item inside the game.
        if (!player.getSelectedItem().isNull()) {
            gm.interact(*hit.getEntity(), hit.mPos, HandSlot::Mainhand);
        }
    } else {
        gm.useItem(item, HandSlot::Mainhand);
    }
    enqueue(result.dump());
}

void ProbeMod::applyRealInput(LocalPlayer&, ControlState const& state) {
    std::uint64_t const tick = mTick.load(std::memory_order_relaxed);
    if (!mBindingsValid || tick - mBindingsTick >= kBindRefreshTicks) {
        refreshBindings(true);
    }

    // If the guest has a sprint key (currently often R), press it. If sprint is
    // unassigned, reproduce the vanilla double-tap-forward gesture instead.
    bool const sprintBound     = mBindings.sprint != 0;
    bool const sprintRequested = state.sprint && state.forward > 0.0f;
    if (!sprintBound && sprintRequested) {
        if (mSprintTapPhase == 0) {
            mSprintTapPhase = mHeldKeys[KeyForward] ? 2 : 1;
        }
    } else {
        mSprintTapPhase = 0;
    }
    bool const wantW = state.forward > 0.0f
                    && (!sprintRequested || !sprintBound || mSprintTapPhase != 2);

    bool const want[KeyCount] = {
        wantW,
        state.forward < 0.0f,
        state.strafe < 0.0f,
        state.strafe > 0.0f,
        state.sneak,
        state.jump,
        sprintBound && sprintRequested,
    };
    if (want[KeyJump] && !mHeldKeys[KeyJump]) {
        mJumpEdgeThisTick = true;
    }

    int const codes[KeyCount] = {
        mBindings.forward,
        mBindings.back,
        mBindings.left,
        mBindings.right,
        mBindings.sneak,
        mBindings.jump,
        mBindings.sprint,
    };
    std::string error;
    mInjectingInput.store(true, std::memory_order_release);
    for (size_t i = 0; i < KeyCount; ++i) {
        if (mHeldKeys[i] == want[i] || codes[i] == 0) {
            continue;
        }
        // Hardware is the origin a physical keyboard produces. The injection
        // guard above keeps input_lock from dropping our own edge.
        if (hidKeyEvent(codes[i], want[i], true, error)) {
            mHeldKeys[i] = want[i];
        }
    }
    if (!sprintBound && sprintRequested) {
        if (mSprintTapPhase == 1) {
            mSprintTapPhase = 2; // next tick release forward
        } else if (mSprintTapPhase == 2) {
            mSprintTapPhase = 3; // next tick press forward and latch sprint
        }
    }

    auto button = [this](int action, bool down, bool& held) {
        if (down == held) {
            return;
        }
        auto& device = Mouse::_instance();
        device.feed(
            static_cast<char>(action), down ? 1 : 0, device._x, device._y, 0, 0, false
        );
        mMouseInjected.fetch_add(1, std::memory_order_relaxed);
        held = down;
    };
    button(1, state.lmb, mHeldLmb);
    button(2, state.rmb, mHeldRmb);
    mInjectingInput.store(false, std::memory_order_release);
}

void ProbeMod::applyControls(ClientInstance& client, LocalPlayer& player, ControlState const& state) {
    // Default path: physical key edges only. Legacy flag/queue/handler/impulse
    // modes exist only for reproducing old measurements and require an explicit
    // input_cfg force.
    if (mButtonMode.load(std::memory_order_relaxed) == 0
        && mJumpImpulseMode.load(std::memory_order_relaxed) == 0) {
        applyRealInput(player, state);
        return;
    }
    releaseRealInput();

    if (state.active) {
        ClientMoveInputHandler::_updateClientMoveVector(state.strafe, state.forward, FocusImpact::Neutral, client);
        mInjected = true;
    } else if (mInjected) {
        ClientMoveInputHandler::_updateClientMoveVector(0.0f, 0.0f, FocusImpact::Neutral, client);
        mInjected = false;
    }

    bool const engaged  = state.active;
    bool const jumpNow  = engaged && state.jump;
    bool const jumpEdge = jumpNow && !mPrevJump;
    if (jumpEdge) {
        mJumpEdgeThisTick = true;
    }
    int const mode = mButtonMode.load();

    if ((mode & 1) && (engaged || mInputActive)) {
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
            stateFlags.set(24, !jumpNow && mPrevJump); // JumpInputWasReleased
            stateFlags.set(25, jumpEdge);              // JumpInputWasPressed
            stateFlags.set(26, jumpNow);               // JumpInputCurrentlyDown
        }
        if (engaged) {
            player.setSneaking(state.sneak);
        } else {
            player.setSneaking(false);
        }
        mInputActive = engaged;
    }

    if (mode & 2) {
        if (auto* queue = inputQueue(client)) {
            bool const buttons[9] =
                {false, false, false, false, state.sprint, state.sneak, state.jump, state.rmb, state.lmb};
            for (size_t i = 4; i < 9; ++i) {
                // Sprint is already applied through the MoveInput flags above.
                // Re-sending it as the sprint *button* made the game jump on flat
                // ground (measured 2026-10-25: modes 2/4 + sprint=true + forward=0
                // produced a 0.42 arc), which contaminated sprint experiments.
                if (i == 4 && (mode & 1)) {
                    mButtonStates[i] = false;
                    continue;
                }
                if (buttons[i] != mButtonStates[i]) {
                    queue->enqueueButton(
                        ActionIds[i],
                        buttons[i] ? ButtonState::Down : ButtonState::Up,
                        false,
                        static_cast<FocusImpact>(mInputFocus.load()),
                        mInputController.load(),
                        false,
                        0.0f,
                        mInputSource.load()
                    );
                    mButtonStates[i] = buttons[i];
                }
            }
        }
    }

    if (mode & 4) {
        if (auto* handler = inputHandler(client)) {
            bool const buttons[9] =
                {false, false, false, false, state.sprint, state.sneak, state.jump, state.rmb, state.lmb};
            for (size_t i = 4; i < 9; ++i) {
                if (i == 4 && (mode & 1)) {
                    mHandlerStates[i] = false;
                    continue;
                }
                if (buttons[i] != mHandlerStates[i]) {
                    invokeButtonHandlers(*handler, buttons[i], ActionIds[i], client);
                    mHandlerStates[i] = buttons[i];
                }
            }
        }
    }

    // Apply the local jump impulse whenever jump is held on the ground,
    // including after landing without a new button edge (holding Space).
    // Relying only on the input
    // pipeline (flags/queue/handlers) makes the client wait for the server: the
    // first three ticks stay on the ground and then the position snaps up by
    // V1+V2+V3, which looks like a rubber-band. dmf-mpk documents
    // MobJump::setJumpPendingScale as an auxiliary, but measured on this build
    // it only scales an already-triggered jump; jumpFromGround is what produces
    // the exact 0.42 / 0.3332 / 0.2481 ... arc from the first physics step.
    if (jumpNow && player.isOnGround()) {
        int const impulse = mJumpImpulseMode.load(std::memory_order_relaxed);
        if (impulse & 1) {
            player.jumpFromGround();
        }
        if (impulse & 2) {
            MobJump::setJumpPendingScale(player.getEntityContext(), 1.0f);
        }
    }

    if (mode & 8) {
        if (state.sprint != mPrevSprint) {
            if (player.isSprinting() != state.sprint) {
                ClientMoveInputHandler::_toggleClientSprint(FocusImpact::Neutral, client);
            }
        }
        if (state.sneak != mPrevSneak) {
            player.setSneaking(state.sneak);
        }
    }

    mPrevJump   = jumpNow;
    mPrevSprint = state.sprint;
    mPrevSneak  = state.sneak;

    if (state.yawDelta != 0.0f || state.pitchDelta != 0.0f) {
        applyTurnDelta(player, Vec2{state.pitchDelta, state.yawDelta});
    }
}

void ProbeMod::applyMacroTickEvents(LocalPlayer& player, MacroTick const& tick) {
    // Event-level macro replay uses the same physical layer as set_input/move:
    // digital key states through Hardware HID, including the double-tap-W
    // sprint gesture. No MoveInput flags are written here.
    ControlState state;
    state.forward = tick.forward();
    state.strafe  = tick.strafe();
    state.sprint  = tick.sprint;
    state.sneak   = tick.sneak;
    state.jump    = tick.jump;
    state.lmb     = tick.lmb;
    state.rmb     = tick.rmb;
    state.active  = tick.w || tick.a || tick.s || tick.d || tick.sprint || tick.sneak
                   || tick.jump;
    applyRealInput(player, state);

    if (tick.yaw != 0.0f || tick.pitch != 0.0f) {
        applyTurnDelta(player, Vec2{tick.pitch, tick.yaw});
    }
    mPrevMacroTick     = tick;
    mHavePrevMacroTick = true;
}

void ProbeMod::releaseMacroInputs() {
    if (!mHavePrevMacroTick) {
        return;
    }
    releaseRealInput();
    mPrevMacroTick     = MacroTick{};
    mHavePrevMacroTick = false;
    mInjectingInput.store(false, std::memory_order_relaxed);
}

void ProbeMod::applyMacroTick(ClientInstance& client, LocalPlayer& player, MacroTick const& tick) {
    if (mMacroEventLevel) {
        applyMacroTickEvents(player, tick);
        return;
    }
    if (tick.rmb != mPrevMacroRmb) {
        if (tick.rmb) {
            performClick(player, 2);
        }
        mPrevMacroRmb = tick.rmb;
    }
    if (tick.lmb != mPrevMacroLmb) {
        if (tick.lmb) {
            performClick(player, 1);
        }
        mPrevMacroLmb = tick.lmb;
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
    applyControls(client, player, state);
}


void ProbeMod::latchActionClicks(LocalPlayer& player) {
    if (mInjectingClick.load(std::memory_order_relaxed)) {
        return;
    }
    auto action = player.getEntityContext().tryGetComponent<PlayerActionComponent>();
    if (!action) {
        return;
    }
    auto const& bits = *action->mPlayerActions;
    // PlayerActionType: StartDestroyBlock=0, InteractWithBlock=25,
    // PredictDestroyBlock=26, ContinueDestroyBlock=27, StartItemUseOn=28,
    // MissedSwing=31, StartUsingItem=37. Only the rising edge becomes a click,
    // otherwise mining would keep the LMB column true for the whole hold.
    bool const lmb = bits.test(0) || bits.test(26) || bits.test(27) || bits.test(31);
    bool const rmb = bits.test(25) || bits.test(28) || bits.test(37);
    if (lmb && !mPrevActionLmb) {
        mMouseLeftLatch = true;
    }
    if (rmb && !mPrevActionRmb) {
        mMouseRightLatch = true;
    }
    mPrevActionLmb = lmb;
    mPrevActionRmb = rmb;
}


} // namespace probe
