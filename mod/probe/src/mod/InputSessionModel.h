#pragma once

#include "mod/InputEventModel.h"
#include "mod/InputLifecycleModel.h"
#include "mod/MovementModel.h"

#include <array>
#include <atomic>
#include <cstdint>
#include <mutex>
#include <optional>

namespace probe::detail {

// Event admission, cancellation and cold completion use eventMutex. Tick owns
// held events, countdowns, key/button state and release retries without taking
// that lock. Atomics publish request/configuration state; no allocation occurs
// in these lifecycle helpers except the queue's preallocated operations.
template <class ControlStateT, class KeyBindingsT>
class InputSession {
public:
    void resetEvents() {
        std::lock_guard lock(eventMutex);
        events.cancel();
        replyGeneration = events.generation();
        textCapture.reset();
        uiCapture.reset();
    }
    void cancelEvents(char const* reason) {
        std::lock_guard lock(eventMutex);
        events.cancel();
        textCapture.cancel(reason);
        uiCapture.cancel(reason);
    }
    void clearMouse(bool resetCounters) {
        if (resetCounters) {
            mouseEventCount = 0;
            mouseLastAction = -1;
            mouseLastData = -1;
            for (auto& count : mouseActionCounts) count = 0;
        }
        mouseLeftLatch = false;
        mouseRightLatch = false;
        mouseLeft = false;
        mouseRight = false;
        prevActionLmb = false;
        prevActionRmb = false;
    }
    void resetButtons() {
        buttonStates.fill(false);
        handlerStates.fill(false);
        prevJump = false;
    }
    void failStop() {
        activeInput = ControlStateT{};
        activeInputTicks = activeLookTicks = 0;
        jumpRequestPending = false;
    }
    void publishLifecycle(std::uint64_t epoch) noexcept {
        InputLifecycleSnapshot snapshot;
        snapshot.epoch = epoch;
        snapshot.inputSeq = seenInputSeq;
        snapshot.jumpSeq = seenJumpSeq;
        snapshot.lookSeq = seenLookSeq;
        snapshot.inputTicks = activeInputTicks;
        snapshot.lookTicks = activeLookTicks;
        snapshot.jumpPending = jumpRequestPending;
        for (auto const& held : heldEvents) snapshot.heldEvents += held.has_value();
        snapshot.valid = lifecycleCurrent;
        lifecycle.publish(snapshot);
    }

    InputLifecycleStore lifecycle;
    bool lifecycleCurrent = false; // Game-thread owned, validated each pre/post.

    std::mutex eventMutex;
    InjectedEventQueue<> events;
    std::uint64_t replyGeneration = 1;
    UiCapture uiCapture;
    TextCapture textCapture;
    std::atomic<std::uint64_t> tickSerial{0};
    std::array<std::optional<InputInjection>, 264> heldEvents{};
    std::uint64_t eventGeneration = 0;

    std::atomic_bool allowInject{false};
    std::atomic_bool injecting{false};
    std::atomic_bool hardwareLocked{false};
    std::atomic_bool lockOnConnect{false};
    std::atomic<std::uint64_t> injectTurnUntilTick{0};
    std::atomic<std::uint64_t> keyEventCount{0}, keyInjectedSeen{0};
    std::atomic<std::int32_t> keyLastCode{-1}, keyLastAction{-1};
    std::atomic<std::uint64_t> mouseInjected{0};

    ControlDiagnostics controlDiagnostics;
    std::array<std::atomic<ControlTest>, ControlDiagnostics::Operations> controlTests{};
    std::atomic_bool controlCleanupPending{false};
    std::atomic<std::uint32_t> controlHeldMask{0};

    std::uint32_t seenInputSeq = 0;
    ControlStateT activeInput;
    int activeInputTicks = 0;
    std::uint32_t seenJumpSeq = 0;
    bool jumpRequestPending = false;
    std::uint32_t seenLookSeq = 0;
    bool activeLookRelative = true;
    float activeLookYaw = 0.0f, activeLookPitch = 0.0f;
    int activeLookTicks = 0;

    bool prevSprint = false, prevSneak = false, injected = false, inputActive = false;
    KeyBindingsT bindings;
    bool bindingsValid = false;
    std::uint64_t bindingsTick = 0;
    std::array<bool, 7> heldKeys{};
    bool heldLmb = false, heldRmb = false;
    int sprintTapPhase = 0;
    std::atomic_bool prevJump{false};
    bool jumpEdgeThisTick = false;
    bool prevMacroRmb = false, prevMacroLmb = false;
    std::atomic_bool mouseLeft{false}, mouseRight{false};
    std::atomic_bool mouseLeftLatch{false}, mouseRightLatch{false};
    std::atomic<std::uint64_t> mouseEventCount{0};
    std::atomic<int> mouseLastAction{-1}, mouseLastData{-1};
    std::array<std::atomic<int>, 8> mouseActionCounts{};
    std::atomic_bool injectingClick{false};
    std::atomic<std::uint32_t> injectingLook{0};
    std::atomic_bool prevActionLmb{false}, prevActionRmb{false};
    std::array<bool, 9> buttonStates{}, handlerStates{};
    std::atomic_bool resetInputButtons{false};

    std::atomic<int> inputSource{-1}, inputController{0}, inputFocus{0};
    std::atomic<ControlInputMode> inputMode{ControlInputMode::Hardware};
    std::atomic<JumpImpulse> jumpImpulseMode{JumpImpulse::None};
};

} // namespace probe::detail
