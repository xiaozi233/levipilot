#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

namespace {
// The Windows client uses the GameCore platform shim, whose first members
// include the HIDController shared_ptr. A wrong layout faults here, and this
// is guarded locally as well as by each input operation. The raw cast stays
// inside its own __try (no C++ objects in this function).
HIDControllerGameCoreDesktop* gamecoreHid(void* shim) {
    __try {
        return static_cast<Bedrock::Platform_GameCore*>(shim)->mHIDController.get();
    } __except (
        isFatalSehCode(GetExceptionCode()) ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH
    ) {
        return nullptr;
    }
}
} // namespace

// G1/G2: push prepared input into the game's own queues instead of faking OS
// messages. Keyboard::_inputs() is drained by the game's input pipeline (which
// emits KeyInputEvent and runs bindings); MouseDevice::feed() is the publisher
// behind MouseInputEvent. Both run on the client tick thread.
char const* ProbeMod::hidKeyEdge(int code, bool down, bool hardware) {
    auto platform = ll::service::getAppPlatform();
    if (!platform) {
        return "no_platform";
    }
    auto* shim = platform->getPlatformShim();
    if (shim == nullptr) {
        return "no_platform_shim";
    }
    auto* hid = gamecoreHid(shim);
    if (hid == nullptr) {
        // Either the shared_ptr is empty or the shim layout faulted under SEH.
        return "no_hid_controller";
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
    return nullptr;
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
    auto* hid = gamecoreHid(shim);
    if (hid == nullptr) {
        error = "no_hid_controller";
        return false;
    }
    hid->onTextInput(text);
    return true;
}

std::uint64_t ProbeMod::queueInputInjection(InputInjection const& item) {
    std::lock_guard lock(mInputSession.eventMutex);
    return mInputSession.events.push(item);
}

char const* ProbeMod::applyInputEvent(InputInjection const& item) {
    char const* error = nullptr;
    // Restore the flag outside the raw SEH frame, including partial SDK calls.
    auto const previous = mInputSession.injecting.exchange(item.kind != InputKind::Raw);
    try {
        if (!guardedGameRead([&] {
            injectCaptureFault(item.test);
            if (item.kind == InputKind::Queue) {
                auto& states = Keyboard::_states();
                KeyboardAction action{};
                action.state = item.data ? KeyboardAction::Keydown : KeyboardAction::Keyup;
                action.key = item.code;
                action.controllerId = Keyboard::_gameControllerId();
                action.lshiftState = states[Keyboard::Lshift];
                action.controlState = states[Keyboard::Control];
                action.menuState = states[Keyboard::Menu];
                action.isRepeatedEvent = item.repeat;
                Keyboard::_inputs().push_back(action);
            } else if (item.kind != InputKind::Mouse) {
                error = hidKeyEdge(item.code, item.data != 0, item.kind != InputKind::Hid);
            } else {
                auto& device = Mouse::_instance();
                device.feed(static_cast<char>(item.code), static_cast<schar>(item.data),
                            item.hasPos ? item.x : device._x, item.hasPos ? item.y : device._y,
                            item.dx, item.dy, false);
                mInputSession.mouseInjected.fetch_add(1, std::memory_order_relaxed);
                if ((item.code == 0 || item.code == 7) && (item.dx || item.dy))
                    mInputSession.injectTurnUntilTick.store(mTick.load(std::memory_order_relaxed) + 2,
                                              std::memory_order_relaxed);
            }
        })) error = "input_fault";
    } catch (...) { error = "input_failed"; }
    mInputSession.injecting.store(previous, std::memory_order_release);
    return error;
}

bool ProbeMod::releaseInputEvents() {
    bool released = true;
    for (auto& held : mInputSession.heldEvents) {
        if (!held) continue;
        auto up = *held;
        up.data = 0;
        up.test = CaptureTest::None;
        if (applyInputEvent(up)) released = false;
        else held.reset();
    }
    return released;
}

void ProbeMod::tickInputInjection() {
    mInputSession.tickSerial.fetch_add(1, std::memory_order_release);
    auto const generation = mInputSession.events.generation();
    if (generation != mInputSession.eventGeneration) {
        if (!releaseInputEvents()) return; // Retry failed releases before admitting new input.
        mInputSession.eventGeneration = generation;
    }
    mInputSession.events.tick([&](InputInjection const& item) {
        std::size_t const slot = item.kind == InputKind::Mouse ? 256 + item.code : item.code;
        bool const track = item.kind != InputKind::Mouse
            || (item.code != 0 && item.code != 4 && item.code != 7);
        // A fault may happen after an edge reaches the SDK. Keep its release
        // even if the call reports failure; an extra key-up is harmless.
        if (track && item.data) mInputSession.heldEvents[slot] = item;
        auto error = applyInputEvent(item);
        if (track && !item.data && !error) mInputSession.heldEvents[slot].reset();
        return error;
    }, mInputSession.eventGeneration);
}

bool ProbeMod::queueTextInput(std::string text, CaptureTest test) {
    std::lock_guard lock(mInputSession.eventMutex);
    TextQuery query{std::move(text), mInputSession.events.nextId(), mInputSession.events.generation(), test};
    auto request = mInputSession.textCapture.begin(std::move(query), GetTickCount64());
    if (!request) { enqueue(R"({"event":"error","reason":"text_input_busy"})"); return false; }
    InputInjection barrier;
    barrier.kind = InputKind::Text;
    if (!mInputSession.events.push(barrier)) {
        mInputSession.textCapture.reset();
        enqueue(R"({"event":"error","reason":"inject_queue_full"})");
        return false;
    }
    enqueue(nlohmann::json{{"event", "text_input_queued"}, {"input_id", request->query.id}}.dump());
    return true;
}

bool ProbeMod::queueUiQuery(UiQuery query) {
    std::lock_guard lock(mInputSession.eventMutex);
    if (mInputSession.uiCapture.begin(query, GetTickCount64())) return true;
    enqueue(R"({"event":"error","reason":"ui_query_busy"})");
    return false;
}

void ProbeMod::resetInputEvents() { mInputSession.resetEvents(); }
void ProbeMod::cancelInputEvents(char const* reason) { mInputSession.cancelEvents(reason); }

void ProbeMod::drainInputEvents() {
    std::lock_guard lock(mInputSession.eventMutex);
    InputOutcome row;
    while (mInputSession.events.pop(row)) {
        if (row.kind == InputKind::Text || row.generation < mInputSession.replyGeneration) continue;
        nlohmann::json out{{"event", "input_event_done"}, {"input_id", row.id}, {"ok", !row.error}};
        if (row.error) out["reason"] = row.error;
        enqueue(out.dump());
    }
    if (auto request = mInputSession.textCapture.current(); request && mInputSession.events.textReady(request->query.id)) {
        if (auto stage = request->dispatch(); stage && request->query.test != CaptureTest::Deferred) {
            auto weak = std::weak_ptr{request};
            auto failed = [weak](char const* error) { if (auto active = weak.lock()) active->cancel(error); };
            try {
                queueClientTask([this, weak, stage = *stage] {
                    if (auto active = weak.lock()) active->run(stage, GetTickCount64(), [&](TextSnapshot& out) {
                        if (stage == InputStage::Flush)
                            return mInputSession.tickSerial.load(std::memory_order_acquire) >= out.resumeTick
                                ? InputStage::Complete : InputStage::Flush;
                        std::string error;
                        if (!guardedGameRead([&] {
                            injectCaptureFault(active->query.test);
                            if (active->query.generation != mInputSession.events.generation() || !mWorld.inWorld())
                                out.error = "world_left";
                            else if (!hidTextInput(active->query.text, error)) out.error = "text_input_failed";
                        })) out.error = "text_input_fault";
                        // onTextInput queues UI work. Cross two player tick
                        // boundaries before releasing a following Enter edge.
                        out.resumeTick = mInputSession.tickSerial.load(std::memory_order_acquire) + 2;
                        return out.error ? InputStage::Complete : InputStage::Flush;
                    });
                }, failed);
            } catch (...) { request->cancel("text_input_queue_failed"); }
        }
    }
    if (auto done = mInputSession.textCapture.take(GetTickCount64()); done.request) {
        auto error = done.error ? done.error : done.request->snapshot.error;
        nlohmann::json out{{"event", "text_input_done"}, {"input_id", done.request->query.id}, {"ok", !error}};
        if (error) out["reason"] = error;
        auto line = out.dump();
        // A queued Enter must never submit a partially applied/old command
        // after text fails. Keep numbered cancellation replies on this client;
        // reconnect alone advances the reply cutoff and discards old work.
        if (error && done.request->query.generation == mInputSession.events.generation()) mInputSession.events.cancel();
        mInputSession.events.completeText(done.request->query.id);
        if (done.release()) enqueue(std::move(line));
    }
    if (auto request = mInputSession.uiCapture.current()) {
        if (auto stage = request->dispatch(); stage && request->query.test != CaptureTest::Deferred) {
            auto weak = std::weak_ptr{request};
            auto failed = [weak](char const* error) { if (auto active = weak.lock()) active->cancel(error); };
            try {
                queueClientTask([this, weak] {
                    if (auto active = weak.lock()) active->run(InputStage::Client, GetTickCount64(), [&](UiSnapshot& out) {
                        std::string name;
                        if (!guardedGameRead([&] {
                            injectCaptureFault(active->query.test);
                            out.inWorld = mWorld.inWorld();
                            if (active->query.operation == UiOperation::Screen) {
                                auto client = ll::service::getClientInstance();
                                if (client) name = client->getScreenName();
                                out.name.assign(name);
                                out.truncated = out.name.view().size() != name.size();
                            } else {
                                if (!out.inWorld) { out.error = "not_in_world"; return; }
                                out.down = Keyboard::_states()[active->query.code] != 0;
                                out.events = mInputSession.keyEventCount.load();
                                out.injectedSeen = mInputSession.keyInjectedSeen.load();
                                out.lastCode = mInputSession.keyLastCode.load();
                                out.lastAction = mInputSession.keyLastAction.load();
                                out.pending = mInputSession.events.pending();
                            }
                        })) out.error = "ui_query_fault";
                        return InputStage::Complete;
                    });
                }, failed);
            } catch (...) { request->cancel("ui_query_queue_failed"); }
        }
    }
    if (auto done = mInputSession.uiCapture.take(GetTickCount64()); done.request) {
        auto error = done.error ? done.error : done.request->snapshot.error;
        nlohmann::json out;
        if (error) out = {{"event", "error"}, {"phase", "ui_query"}, {"reason", error}};
        else {
            auto const& s = done.request->snapshot;
            if (done.request->query.operation == UiOperation::Screen)
                out = {{"event", "screen"}, {"name", s.name.c_str()}, {"in_world", s.inWorld}, {"truncated", s.truncated}};
            else out = {{"event", "key_state"}, {"code", done.request->query.code}, {"down", s.down},
                        {"events", s.events}, {"injected_seen", s.injectedSeen}, {"last_code", s.lastCode},
                        {"last_action", s.lastAction}, {"pending", s.pending}};
        }
        auto line = out.dump();
        if (done.release()) enqueue(std::move(line));
    }
}

// input_lock: while scripts own the session, real hardware keys must not reach
// the game. Both the platform shim and our injections enter through the same
// HID override, so filter on the origin and on our injection guard.
LL_TYPE_INSTANCE_HOOK(
    ProbeHardwareKeyHook,
    ll::memory::HookPriority::Normal,
    HIDControllerGameCoreDesktop,
    &HIDControllerGameCoreDesktop::$onKeyDown,
    void,
    int keyCode,
    Bedrock::Input::KeyboardEventProcessor::InputOrigin inputOrigin
) {
    ::probe::detail::CallbackScope callback(::probe::detail::gCallbacks, ::probe::detail::CallbackDomain::Key);
    auto& mod = ::probe::ProbeMod::getInstance();
    // Origin is the whole test: a real key press is Hardware. Scripted presses
    // that must reproduce one (double-space flight only reacts to the Hardware
    // path) arrive through the inject ring under mInputSession.injecting, so they pass
    // while the guest's own keyboard is dropped.
    if (mod.hardwareInputLocked()
        && inputOrigin == Bedrock::Input::KeyboardEventProcessor::InputOrigin::Hardware
        && !mod.injectingInput()) {
        return;
    }
    origin(keyCode, inputOrigin);
};

// The same entry publishes real mouse buttons/motion and our injected ones;
// injections run under mInputSession.injecting, so everything else is hardware.
LL_TYPE_INSTANCE_HOOK(
    ProbeHardwareMouseHook,
    ll::memory::HookPriority::Normal,
    MouseDevice,
    &MouseDevice::feed,
    void,
    char  actionButtonId,
    schar buttonData,
    short x,
    short y,
    short dx,
    short dy,
    bool  forceMotionlessPointer
) {
    ::probe::detail::CallbackScope callback(::probe::detail::gCallbacks, ::probe::detail::CallbackDomain::Mouse);
    auto& mod = ::probe::ProbeMod::getInstance();
    if (mod.hardwareInputLocked() && !mod.injectingInput()) {
        return;
    }
    origin(actionButtonId, buttonData, x, y, dx, dy, forceMotionlessPointer);
};

bool ProbeMod::setLifecycleKeyHook(bool on, bool suspendThreads) {
    if (on) {
        if (mLifecycleKeyHooked.load(std::memory_order_acquire)) return true;
        int const status = ProbeHardwareKeyHook::hook(suspendThreads);
        if (status != 0) {
            bootLog("lifecycle: key hook restore failed status=" + std::to_string(status));
            return false;
        }
        mLifecycleKeyHooked.store(true, std::memory_order_release);
        return true;
    }
    if (!mLifecycleKeyHooked.exchange(false, std::memory_order_acq_rel)) return true;
    // The flag must follow the real teardown state: reporting "off" while the
    // detour is still installed would let the caller unload a DLL that is still
    // being called.
    if (!ProbeHardwareKeyHook::unhook(suspendThreads)) {
        mLifecycleKeyHooked.store(true, std::memory_order_release);
        bootLog("lifecycle: key hook unhook failed");
        return false;
    }
    return true;
}

bool ProbeMod::setLifecycleMouseHook(bool on, bool suspendThreads) {
    if (on) {
        if (mLifecycleMouseHooked.load(std::memory_order_acquire)) return true;
        int const status = ProbeHardwareMouseHook::hook(suspendThreads);
        if (status != 0) {
            bootLog("lifecycle: mouse hook restore failed status=" + std::to_string(status));
            return false;
        }
        mLifecycleMouseHooked.store(true, std::memory_order_release);
        return true;
    }
    if (!mLifecycleMouseHooked.exchange(false, std::memory_order_acq_rel)) return true;
    if (!ProbeHardwareMouseHook::unhook(suspendThreads)) {
        mLifecycleMouseHooked.store(true, std::memory_order_release);
        bootLog("lifecycle: mouse hook unhook failed");
        return false;
    }
    return true;
}

} // namespace probe
