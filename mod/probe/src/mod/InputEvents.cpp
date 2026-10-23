#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

// G1/G2: push prepared input into the game's own queues instead of faking OS
// messages. Keyboard::_inputs() is drained by the game's input pipeline (which
// emits KeyInputEvent and runs bindings); MouseDevice::feed() is the publisher
// behind MouseInputEvent. Both run on the client tick thread.
bool ProbeMod::hidKeyEvent(int code, bool down, bool hardware, std::string& error) {
    auto platform = ll::service::getAppPlatform();
    if (!platform) {
        error = "no_platform";
        return false;
    }
    auto* shim = platform->getPlatformShim();
    if (shim == nullptr) {
        error = "no_platform_shim";
        return false;
    }
    // The Windows client uses the GameCore platform shim, whose first members
    // include the HIDController shared_ptr. A wrong layout would fault; the
    // command dispatcher's SEH guard turns that into internal_error.
    auto* gamecore = static_cast<Bedrock::Platform_GameCore*>(shim);
    std::shared_ptr<HIDControllerGameCoreDesktop>& hid = gamecore->mHIDController;
    if (!hid) {
        error = "no_hid_controller";
        return false;
    }
    if (down) {
        hid->onKeyDown(
            code,
            hardware ? Bedrock::Input::KeyboardEventProcessor::InputOrigin::Hardware
                     : Bedrock::Input::KeyboardEventProcessor::InputOrigin::Virtual
        );
    } else {
        hid->onKeyUp(code);
    }
    return true;
}

bool ProbeMod::hidTextInput(std::string const& text, std::string& error) {
    auto platform = ll::service::getAppPlatform();
    if (!platform) {
        error = "no_platform";
        return false;
    }
    auto* shim = platform->getPlatformShim();
    if (shim == nullptr) {
        error = "no_platform_shim";
        return false;
    }
    auto* gamecore = static_cast<Bedrock::Platform_GameCore*>(shim);
    std::shared_ptr<HIDControllerGameCoreDesktop>& hid = gamecore->mHIDController;
    if (!hid) {
        error = "no_hid_controller";
        return false;
    }
    hid->onTextInput(text);
    return true;
}

bool ProbeMod::queueInputInjection(InputInjection const& item) {
    std::uint32_t const head = mInjectHead.load(std::memory_order_relaxed);
    std::uint32_t const tail = mInjectTail.load(std::memory_order_acquire);
    if (head - tail >= kInputInjectRing) {
        return false;
    }
    mInjectRing[head % kInputInjectRing] = item;
    mInjectHead.store(head + 1, std::memory_order_release);
    return true;
}

void ProbeMod::tickInputInjection() {
    std::string text;
    {
        std::lock_guard lock(mTextMutex);
        text.swap(mTextPending);
    }
    if (!text.empty()) {
        std::string error;
        mInjectingInput.store(true, std::memory_order_release);
        hidTextInput(text, error);
        mInjectingInput.store(false, std::memory_order_release);
    }

    std::uint32_t tail = mInjectTail.load(std::memory_order_acquire);
    std::uint32_t head = mInjectHead.load(std::memory_order_acquire);
    if (tail == head) {
        return;
    }

    auto& keyboardInputs = Keyboard::_inputs();
    auto& keyboardStates = Keyboard::_states();
    mInjectingInput.store(true, std::memory_order_release);
    while (tail != head) {
        InputInjection const& item = mInjectRing[tail % kInputInjectRing];
        if (item.kind == 0) {
            KeyboardAction action{};
            action.state           = item.data != 0 ? KeyboardAction::Keydown : KeyboardAction::Keyup;
            action.key             = item.code;
            action.controllerId    = Keyboard::_gameControllerId();
            action.lshiftState     = keyboardStates[Keyboard::Lshift];
            action.controlState    = keyboardStates[Keyboard::Control];
            action.menuState       = keyboardStates[Keyboard::Menu];
            action.isRepeatedEvent = item.repeat;
            keyboardInputs.push_back(action);
        } else if (item.kind == 2 || item.kind == 3) {
            std::string error;
            hidKeyEvent(item.code, item.data != 0, item.kind == 3, error);
        } else {
            auto& device = Mouse::_instance();
            device.feed(
                static_cast<char>(item.code),
                static_cast<schar>(item.data),
                item.hasPos ? item.x : device._x,
                item.hasPos ? item.y : device._y,
                item.dx,
                item.dy,
                false
            );
            mMouseInjected.fetch_add(1, std::memory_order_relaxed);
            if ((item.code == 0 || item.code == 7) && (item.dx != 0 || item.dy != 0)) {
                // The game applies the turn later in this frame, after our
                // transient flag is cleared; allow it for the next two ticks.
                mInjectTurnUntilTick.store(
                    mTick.load(std::memory_order_relaxed) + 2,
                    std::memory_order_relaxed
                );
            }
        }
        ++tail;
    }
    mInjectTail.store(tail, std::memory_order_release);
    mInjectingInput.store(false, std::memory_order_release);
}

} // namespace probe
