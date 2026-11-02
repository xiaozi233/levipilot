#include "mod/FlightModel.h"

#include <cassert>
#include <cstdlib>
#include <new>

using namespace probe::detail;
std::atomic<unsigned> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto ptr = std::malloc(size)) return ptr;
    throw std::bad_alloc();
}
void operator delete(void* ptr) noexcept { std::free(ptr); }
void operator delete(void* ptr, std::size_t) noexcept { std::free(ptr); }

void assertions() {
    FlightAssertion assertion;
    assertion.prepare(1);
    assertion.start(1, true);
    auto const before = allocations.load();
    unsigned calls = 0;
    auto apply = [&](bool target, CaptureTest test) -> char const* {
        assert(target && test == CaptureTest::None); ++calls; return nullptr;
    };
    for (unsigned i = 0; i < 20; ++i) assertion.tick(apply);
    assert(calls == 8 && !assertion.active());
    assertion.prepare(2);
    assertion.start(2, false);
    assertion.tick([&](bool target, CaptureTest) -> char const* { assert(!target); ++calls; return nullptr; });
    assertion.cancel();
    assertion.tick(apply);
    assert(calls == 9 && !assertion.active());
    assertion.prepare(3);
    assertion.start(3, true);
    assertion.tick([](bool, CaptureTest) -> char const* { throw 42; });
    FlightAssertion::Failure failure;
    assert(!assertion.active() && assertion.pop(failure));
    assert(failure.id == 3 && std::string_view(failure.error) == "flight_assert_failed");
    assert(!assertion.pop(failure));
    // Replacing an assertion during a failing observation must retain the new one.
    assertion.prepare(4);
    assertion.start(4, false);
    assertion.tick([&](bool target, CaptureTest) -> char const* {
        assert(!target);
        assertion.prepare(5);
        assertion.start(5, true);
        return "fault";
    });
    assert(assertion.active() && assertion.pop(failure) && failure.id == 4);
    for (unsigned i = 0; i < 9; ++i) assertion.tick(apply);
    assert(calls == 17 && !assertion.active());
    assert(allocations.load() == before);
    for (unsigned i = 6; i < 26; ++i) {
        assertion.prepare(i);
        assertion.start(i, true);
        assertion.tick([](bool, CaptureTest) { return "failed"; });
    }
    unsigned rows = 0;
    while (assertion.pop(failure)) ++rows;
    assert(rows == 16 && assertion.takeLost() == 4 && assertion.takeLost() == 0);
    // A callback admitted before cancellation cannot revive its assertion.
    assertion.prepare(26);
    assertion.cancel();
    assert(!assertion.start(26, true));
    assertion.prepare(27);
    assert(!assertion.start(26, true) && !assertion.active());
    assert(assertion.start(27, false, CaptureTest::Fault));
    unsigned injected = 0;
    for (unsigned i = 0; i < 10; ++i) assertion.tick([&](bool target, CaptureTest test) -> char const* {
        assert(!target);
        if (test == CaptureTest::Fault) ++injected;
        return nullptr;
    });
    assert(injected == 1 && !assertion.active());
}

void stages() {
    FlightCapture capture;
    FlightQuery query;
    query.id = 1;
    auto first = capture.begin(query, 0);
    assert(first && !capture.begin(query, 1));
    assert(first->dispatch() == FlightStage::Client);
    first->run(FlightStage::Client, 1, [](auto& out) {
        out.identity.uuidA = 42;
        out.clientApplied = true;
        return FlightStage::Server;
    });
    assert(first->dispatch() == FlightStage::Server);
    auto expired = capture.take(2000);
    assert(expired.request == first && std::string_view(expired.error) == "capture_timeout");
    assert(expired.release() && !capture.take(3000).request);
    query.id = 2;
    auto fresh = capture.begin(query, 2001);
    bool called = false;
    first->run(FlightStage::Server, 2002, [&](auto&) { called = true; return FlightStage::Complete; });
    assert(!called);
    assert(fresh->dispatch());
    fresh->run(FlightStage::Client, 2003, [](auto&) { return FlightStage::Server; });
    assert(fresh->dispatch());
    capture.cancel("world_left");
    fresh->run(FlightStage::Server, 2004, [&](auto&) { called = true; return FlightStage::Complete; });
    assert(!called);
    auto cancelled = capture.take(2005);
    assert(cancelled.request == fresh && std::string_view(cancelled.error) == "world_left");
    assert(cancelled.release());
    auto reset = capture.begin(query, 3000);
    assert(reset && reset->dispatch());
    capture.reset();
    reset->run(FlightStage::Client, 3001, [&](auto&) { called = true; return FlightStage::Complete; });
    assert(!called && !capture.take(6000).request);
}
int main() { assertions(); stages(); }
