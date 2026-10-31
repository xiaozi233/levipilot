#include "mod/TickStreamModel.h"

#include <cassert>
#include <cstdlib>
#include <future>
#include <new>

using namespace probe::detail;

std::atomic<unsigned> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto p = std::malloc(size))
        return p;
    throw std::bad_alloc();
}
void operator delete(void* p) noexcept {
    std::free(p);
}
void operator delete(void* p, std::size_t) noexcept {
    std::free(p);
}

struct Pair {
    std::uint64_t a = 0, b = 0;
};

void queueOwnership() {
    TickQueue<Pair, 7> queue;
    auto const before = allocations.load();
    for (std::uint64_t i = 0; i < 7; ++i)
        assert(queue.push({i, ~i}));
    assert(!queue.push({99, 99}));
    Pair row;
    for (std::uint64_t i = 0; i < 7; ++i) {
        assert(queue.pop(row));
        assert(row.a == i && row.b == ~i);
    }
    assert(!queue.pop(row) && queue.empty());
    assert(allocations.load() == before);
    std::thread producer([&] {
        for (std::uint64_t i = 0; i < 100000; ++i) {
            while (!queue.push({i, ~i}))
                std::this_thread::yield();
        }
    });
    for (std::uint64_t i = 0; i < 100000; ++i) {
        while (!queue.pop(row))
            std::this_thread::yield();
        assert(row.a == i && row.b == ~i);
    }
    producer.join();
    assert(queue.empty());
}

void lifecycle() {
    LegacyStream<4> stream;
    LegacyTick row;
    assert(stream.begin(3));
    auto const id = stream.recordId();
    assert(!stream.begin(1));
    auto const before = allocations.load();
    for (unsigned i = 0; i < 4; ++i)
        stream.tick([](auto& out) {
            out.x = 123;
            return true;
        });
    assert(!stream.active() && !stream.takeCompletion());
    for (std::uint64_t i = 1; i <= 3; ++i) {
        assert(stream.pop(row) && row.tick == i && row.recordId == id && row.x == 123);
    }
    auto done = stream.takeCompletion();
    assert(done && done->ok() && done->rows == 3 && done->reason == StreamEnd::MaxTicks);
    assert(!stream.takeCompletion() && !stream.pop(row));
    assert(stream.begin(0));
    stream.tick([](auto& out) { return out.tick == 1; });
    stream.stop(StreamEnd::Client);
    stream.tick([](auto&) {
        assert(false);
        return true;
    });
    assert(stream.pop(row) && row.tick == 1 && row.recordId == id + 1);
    done = stream.takeCompletion();
    assert(done && done->ok() && done->rows == 1);
    assert(allocations.load() == before);
}

void failures() {
    LegacyStream<4> stream;
    LegacyTick row;
    for (auto reason : {StreamEnd::CaptureFault, StreamEnd::CaptureFailed, StreamEnd::BufferFull}) {
        assert(stream.begin(0, 2));
        stream.tick([](auto&) { return true; });
        if (reason == StreamEnd::CaptureFault)
            stream.tick([](auto&) { return false; });
        else if (reason == StreamEnd::CaptureFailed)
            stream.tick([](auto&) -> bool { throw 42; });
        else {
            stream.tick([](auto&) { return true; });
            stream.tick([](auto&) { return true; });
        }
        stream.stop(StreamEnd::Client);
        std::uint64_t count = 0;
        while (stream.pop(row))
            assert(row.tick == ++count);
        auto done = stream.takeCompletion();
        assert(done && !done->ok() && done->rows == count && done->reason == reason);
    }
    assert(stream.begin(0));
    stream.stop(StreamEnd::WorldLeft);
    auto done = stream.takeCompletion();
    assert(done && done->reason == StreamEnd::WorldLeft && !done->ok());
    stream.stop(StreamEnd::Disabled); // Closed sessions do not mutate the next one.
    assert(stream.begin(1));
    stream.tick([](auto&) { return true; });
    assert(stream.pop(row));
    assert(stream.takeCompletion()->ok());
}

void admittedStop(bool fail) {
    LegacyStream<4> stream;
    assert(stream.begin(0));
    std::promise<void> entered, release;
    auto gate = release.get_future();
    std::thread producer([&] {
        stream.tick([&](auto& out) {
            entered.set_value();
            gate.wait();
            out.x = 42;
            return !fail;
        });
    });
    entered.get_future().wait();
    auto stop = std::async(std::launch::async, [&] { stream.stop(StreamEnd::Client); });
    while (stream.active())
        std::this_thread::yield();
    assert(!stream.takeCompletion());
    assert(stop.wait_for(std::chrono::milliseconds(0)) != std::future_status::ready);
    release.set_value();
    stop.get();
    producer.join();
    LegacyTick row;
    assert(stream.pop(row) == !fail);
    if (!fail)
        assert(row.x == 42 && row.tick == 1);
    auto done = stream.takeCompletion();
    assert(done && done->ok() == !fail && done->rows == (fail ? 0 : 1));
    assert(done->reason == (fail ? StreamEnd::CaptureFault : StreamEnd::Client));
}

void parkour() {
    ParkourTracker tracker;
    auto const before = allocations.load();
    assert(!tracker.observe({0, 10, 0, 9, true}, 1).flags); // Joining injured is not damage.
    assert(!tracker.observe({0, 15, 0, 9, false}, 2).flags);
    auto event = tracker.observe({0, 10, 0, 0, true}, 3);
    assert(event.flags == (ParkourEvent::Landed | ParkourEvent::Hurt | ParkourEvent::Died));
    assert(event.fall == 5 && event.damage == 9 && event.tick == 3);
    assert(!tracker.observe({0, 10, 0, 0, true}, 4).flags);
    tracker.reset();
    assert(!tracker.observe({0, -60, 0, 4, true}, 5).flags);
    tracker.observe({0, -59.5f, 0, 4, false}, 6);
    assert(!tracker.observe({0, -60, 0, 4, true}, 7).flags);
    tracker.observe({0, 99, 0, 4, false}, 8);
    tracker.reset(); // Fault / new world cannot inherit a fall or health delta.
    assert(!tracker.observe({0, -60, 0, 1, true}, 9).flags);
    assert(allocations.load() == before);
}

int main() {
    queueOwnership();
    lifecycle();
    failures();
    admittedStop(false);
    admittedStop(true);
    parkour();
}
