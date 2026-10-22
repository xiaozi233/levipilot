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
} // namespace

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

void ProbeMod::applyControls(ClientInstance& client, LocalPlayer& player, ControlState const& state) {
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
                if (buttons[i] != mHandlerStates[i]) {
                    invokeButtonHandlers(*handler, buttons[i], ActionIds[i], client);
                    mHandlerStates[i] = buttons[i];
                }
            }
        }
    }

    // Apply the local jump impulse on the edge. Relying only on the input
    // pipeline (flags/queue/handlers) makes the client wait for the server: the
    // first three ticks stay on the ground and then the position snaps up by
    // V1+V2+V3, which looks like a rubber-band. dmf-mpk documents
    // MobJump::setJumpPendingScale as an auxiliary, but measured on this build
    // it only scales an already-triggered jump; jumpFromGround is what produces
    // the exact 0.42 / 0.3332 / 0.2481 ... arc from the first physics step.
    if (jumpEdge && player.isOnGround()) {
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

void ProbeMod::applyMacroTick(ClientInstance& client, LocalPlayer& player, MacroTick const& tick) {
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
