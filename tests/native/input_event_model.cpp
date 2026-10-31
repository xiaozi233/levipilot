#include "mod/InputEventModel.h"

#include <cassert>
#include <cstdlib>
#include <future>
#include <new>
#include <thread>

using namespace probe::detail;
std::atomic<unsigned> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto value = std::malloc(size)) return value;
    throw std::bad_alloc();
}
void operator delete(void* value) noexcept { std::free(value); }
void operator delete(void* value, std::size_t) noexcept { std::free(value); }

void barriersAndFailures() {
    InjectedEventQueue<4> queue;
    InputInjection key;
    key.kind = InputKind::Hid;
    InputInjection text;
    text.kind = InputKind::Text;
    assert(queue.push(key) == 1);
    assert(queue.push(text) == 2);
    assert(queue.push(key) == 3);
    unsigned calls = 0;
    auto apply = [&](auto const&) -> char const* { ++calls; return nullptr; };
    auto before = allocations.load();
    queue.tick(apply);
    assert(calls == 1 && queue.pending() == 2 && !queue.textReady(2));
    queue.tick(apply);
    assert(calls == 1 && queue.textReady(2));
    queue.tick(apply);
    assert(calls == 1);
    queue.completeText(2);
    queue.tick([](auto const&) -> char const* { throw 42; });
    InputOutcome row;
    assert(queue.pop(row) && row.id == 1 && !row.error);
    assert(queue.pop(row) && row.id == 2 && !row.error);
    assert(queue.pop(row) && row.id == 3 && std::string_view(row.error) == "input_failed");
    assert(!queue.pop(row) && !queue.pending());
    assert(queue.push(key) == 4);
    queue.tick(apply);
    assert(queue.pop(row) && row.id == 4 && !row.error && calls == 2);
    assert(allocations.load() == before);

    // Multiple timed-out texts can complete while tick is paused. Completing
    // a newer barrier must also release older completed markers in the ring.
    assert(queue.push(text) == 5);
    assert(queue.push(text) == 6);
    queue.completeText(6);
    queue.completeText(5);
    queue.tick(apply);
    assert(queue.pending() == 0);
    assert(queue.pop(row) && row.id == 5);
    assert(queue.pop(row) && row.id == 6);
}

void capacityAndCancellation() {
    InjectedEventQueue<2> queue;
    InputInjection key;
    unsigned calls = 0;
    auto apply = [&](auto const&) -> char const* { ++calls; return nullptr; };
    assert(queue.push(key) == 1 && queue.push(key) == 2 && !queue.push(key));
    queue.tick(apply);
    assert(!queue.push(key)); // Completed slots still belong to the consumer.
    InputOutcome row;
    assert(queue.pop(row) && row.id == 1);
    assert(queue.push(key) == 3);
    auto previous = queue.generation();
    queue.cancel();
    queue.tick(apply, previous); // Release pending keys before new-generation work.
    assert(queue.pending() == 1);
    queue.tick(apply, queue.generation());
    assert(queue.pop(row) && row.id == 2 && !row.error);
    assert(queue.pop(row) && row.id == 3 && std::string_view(row.error) == "input_cancelled");
    assert(calls == 2);
    assert(queue.push(key) == 4);
    queue.tick([&](auto const&) -> char const* { ++calls; queue.cancel(); return nullptr; }, queue.generation());
    assert(queue.pop(row) && row.id == 4 && calls == 3);
    assert(queue.push(key) == 5);
    queue.tick(apply, queue.generation());
    assert(queue.pop(row) && row.id == 5 && !row.error);
}

void concurrentWrap() {
    InjectedEventQueue<8> queue;
    constexpr unsigned count = 200000;
    std::atomic_bool done{false};
    unsigned applied = 0;
    std::thread tick([&] {
        while (!done.load()) queue.tick([&](InputInjection const& item) -> char const* {
            assert(item.dx == static_cast<std::int16_t>(applied));
            ++applied;
            return nullptr;
        });
    });
    unsigned pushed = 0, popped = 0;
    while (popped < count) {
        if (pushed < count) {
            InputInjection key;
            key.dx = static_cast<std::int16_t>(pushed);
            if (queue.push(key)) ++pushed;
        }
        InputOutcome row;
        if (queue.pop(row)) { ++popped; assert(row.id == popped && !row.error); }
    }
    done = true;
    tick.join();
    assert(applied == count);
}

void stagedOwnership() {
    TextCapture text;
    auto old = text.begin({"old", 1, 1}, 0);
    assert(old && !text.begin({"overwrite"}, 1));
    assert(old->dispatch());
    auto expired = text.take(2000);
    assert(expired.request == old && std::string_view(expired.error) == "capture_timeout");
    assert(expired.release());
    auto next = text.begin({"new", 2, 1}, 2001);
    bool called = false;
    old->run(InputStage::Client, 2002, [&](auto&) { called = true; return InputStage::Complete; });
    assert(!called && next->query.text == "new");
    assert(next->dispatch());
    next->run(InputStage::Client, 2003, [&](auto&) { called = true; return InputStage::Complete; });
    auto ready = text.take(2004);
    assert(called && ready.request == next && !ready.error && !ready.request->snapshot.error);
    assert(ready.release());
    UiCapture ui;
    auto request = ui.begin({}, 0);
    assert(request && request->dispatch());
    ui.reset();
    called = false;
    request->run(InputStage::Client, 1, [&](auto&) { called = true; return InputStage::Complete; });
    assert(!called && !ui.take(3000).request);
}
int main() { barriersAndFailures(); capacityAndCancellation(); concurrentWrap(); stagedOwnership(); }
