#include "mod/InputSessionModel.h"

#include <cassert>
#include <cstdlib>
#include <new>

struct ControlState {
    float forward = 0, strafe = 0;
    bool active = false, jump = false;
};
struct KeyBindings { int forward = 87; };
using Session = probe::detail::InputSession<ControlState, KeyBindings>;
using probe::detail::InputInjection;
using probe::detail::InputKind;

std::atomic<unsigned> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto ptr = std::malloc(size)) return ptr;
    throw std::bad_alloc();
}
void operator delete(void* ptr) noexcept { std::free(ptr); }
void operator delete(void* ptr, std::size_t) noexcept { std::free(ptr); }

int main() {
    Session session;
    InputInjection key;
    key.kind = InputKind::Hardware;
    key.code = 87;
    key.data = 1;
    assert(session.events.push(key));
    auto const generation = session.events.generation();
    session.resetEvents();
    assert(session.events.generation() != generation);
    assert(session.replyGeneration == session.events.generation());

    session.activeInput.active = true;
    session.activeInputTicks = -1;
    session.activeLookTicks = 4;
    session.jumpRequestPending = true;
    session.buttonStates.fill(true);
    session.handlerStates.fill(true);
    session.prevJump = true;
    session.mouseLeft = session.mouseRight = true;
    session.mouseLeftLatch = session.mouseRightLatch = true;
    session.prevActionLmb = session.prevActionRmb = true;
    session.mouseEventCount = 9;
    auto const before = allocations.load();
    session.failStop();
    session.resetButtons();
    session.clearMouse(true);
    session.lifecycleCurrent = true;
    session.publishLifecycle(1);
    probe::detail::InputLifecycleSnapshot snapshot;
    assert(session.lifecycle.read(snapshot, 1) && !snapshot.active());
    assert(!session.activeInput.active && session.activeInputTicks == 0);
    assert(session.activeLookTicks == 0 && !session.jumpRequestPending);
    assert(!session.prevJump && !session.mouseLeft && !session.mouseRight);
    assert(!session.mouseLeftLatch && !session.mouseRightLatch);
    assert(!session.prevActionLmb && !session.prevActionRmb);
    assert(session.mouseEventCount == 0 && session.mouseLastAction == -1);
    assert(session.buttonStates[0] == false && session.handlerStates[8] == false);
    assert(allocations.load() == before);

    struct RequestBlock {
        ControlState  input{};
        int           inputTicks = 0;
        std::uint32_t inputSeq   = 0;
        int           lookTicks  = 0;
        std::uint32_t lookSeq    = 0;
    };
    RequestBlock continuous{{1.0f, 0.0f, true, true}, -1, 10, -1, 20};
    probe::detail::clearContinuousRequests(continuous);
    assert(!continuous.input.active && continuous.input.forward == 0.0f);
    assert(continuous.inputTicks == 0 && continuous.inputSeq == 11);
    assert(continuous.lookTicks == 0 && continuous.lookSeq == 21);

    RequestBlock finite{{0.5f, -0.5f, true, false}, 15, 10, 4, 20};
    probe::detail::clearContinuousRequests(finite);
    assert(finite.input.active && finite.input.forward == 0.5f);
    assert(finite.inputTicks == 15 && finite.inputSeq == 10);
    assert(finite.lookTicks == 4 && finite.lookSeq == 20);
}
