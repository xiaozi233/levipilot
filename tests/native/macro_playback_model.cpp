#include "mod/MacroPlaybackModel.h"

#include <cassert>
#include <cstdlib>
#include <future>
#include <new>
#include <thread>

std::atomic<unsigned> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto* value = std::malloc(size))
        return value;
    throw std::bad_alloc();
}
void operator delete(void* value) noexcept {
    std::free(value);
}
void operator delete(void* value, std::size_t) noexcept {
    std::free(value);
}

using namespace probe;
using namespace probe::detail;

void rangesAndLoops() {
    MacroPlayback session;
    Macro frames(5);
    for (int i = 0; i < 5; ++i)
        frames[i].yaw = static_cast<float>(i);
    session.load(std::move(frames), "range");
    assert(!session.begin(-1, 4, false, true));
    assert(!session.begin(0, 6, false, true));
    assert(session.begin(1, 4, false, true));
    assert(!session.begin(0, 5, false, true));
    unsigned applied = 0, released = 0;
    auto release = [&] {
        ++released;
        return true;
    };
    auto apply = [&](auto const& row, bool events, bool first) {
        assert(events && row.yaw == static_cast<float>(applied + 1));
        assert(first == (applied == 0));
        ++applied;
        return true;
    };
    auto const before = allocations.load();
    for (int i = 0; i < 6; ++i)
        session.tick(apply, release);
    assert(allocations.load() == before && applied == 3 && released == 1);
    auto done = session.takeCompletion();
    assert(done && done->ok() && done->index == 4 && done->reason == MacroEnd::End);
    assert(!session.takeCompletion());
    assert(session.begin(2, 4, true, false));
    applied = 0;
    for (int i = 0; i < 200000; ++i) {
        session.tick(
            [&](auto const& row, bool events, bool first) {
                assert(!events && first == (applied == 0));
                assert(row.yaw == static_cast<float>(2 + applied % 2));
                ++applied;
                return true;
            },
            release);
    }
    assert(allocations.load() == before);
    assert(session.active() && session.index() == 4 && !session.takeCompletion());
    session.stop(MacroEnd::Client);
    assert(session.releasePending());
    session.releaseRequested(release);
    done = session.takeCompletion();
    assert(done && done->runId == 2 && done->reason == MacroEnd::Client && released == 2);
}

void failuresAndRestart() {
    MacroPlayback session;
    session.load(Macro(2), "fault");
    unsigned released = 0;
    auto release = [&] {
        ++released;
        return true;
    };
    for (bool cpp : {false, true}) {
        assert(session.begin(0, 2, false, true));
        session.tick(
            [&](auto const&, bool, bool) -> bool {
                if (cpp)
                    throw 42;
                return false;
            },
            release);
        auto done = session.takeCompletion();
        assert(done && !done->ok() && done->index == 0);
        assert(done->reason == (cpp ? MacroEnd::ApplyFailed : MacroEnd::ApplyFault));
        session.stop(MacroEnd::Client);
        assert(!session.takeCompletion() && !session.active() && !session.releasePending());
    }
    for (bool cpp : {false, true}) {
        assert(session.begin(0, 1, false, true));
        auto apply = [](auto const&, bool, bool) { return true; };
        auto badRelease = [&]() -> bool {
            if (cpp)
                throw 42;
            return false;
        };
        session.tick(apply, badRelease);
        session.tick(apply, badRelease);
        auto done = session.takeCompletion();
        assert(done && !done->ok() && session.releasePending());
        assert(done->reason == (cpp ? MacroEnd::ReleaseFailed : MacroEnd::ReleaseFault));
        assert(session.begin(0, 1, false, true)); // Cleanup precedes a new first frame.
        bool applied = false;
        session.tick(
            [&](auto const&, bool, bool first) {
                assert(first);
                applied = true;
                return true;
            },
            release);
        assert(applied && !session.releasePending());
        session.stop(MacroEnd::Client);
        session.releaseRequested(release);
        assert(session.takeCompletion());
    }
    assert(released == 6);
}

void admittedStopAndReplace() {
    MacroPlayback session;
    Macro frames(2);
    frames[0].yaw = 123;
    session.load(std::move(frames), "old");
    assert(session.begin(0, 2, true, true));
    std::atomic_bool entered{false}, leave{false}, cancelled{false};
    std::thread producer([&] {
        session.tick(
            [&](auto const& row, bool, bool) {
                entered = true;
                while (!leave.load())
                    std::this_thread::yield();
                assert(row.yaw == 123); // Storage stays alive until this lease exits.
                return false;
            },
            [] { return true; });
    });
    while (!entered.load())
        std::this_thread::yield();
    auto stop = std::async(std::launch::async, [&] {
        session.stop(MacroEnd::Client);
        cancelled = true;
    });
    while (session.active())
        std::this_thread::yield();
    assert(!cancelled.load() && !session.takeCompletion());
    leave = true;
    producer.join();
    stop.get();
    auto done = session.takeCompletion();
    assert(done && done->reason == MacroEnd::ApplyFault); // Failure wins the concurrent stop.
    session.load(Macro(1), "new");
    assert(session.name() == "new" && session.size() == 1);
    assert(session.begin(0, 1, false, false));
    session.cancel(MacroEnd::WorldLeft);
    done = session.takeCompletion();
    assert(done && done->reason == MacroEnd::WorldLeft && done->runId == 2);
}

void restartWithConcurrentCleanup() {
    MacroPlayback session;
    session.load(Macro(2), "restart");
    std::atomic_bool done{false};
    std::thread tick([&] {
        auto apply = [](auto const&, bool, bool) { return true; };
        auto release = [] { return true; };
        while (!done.load()) {
            session.releaseRequested(release);
            session.tick(apply, release);
        }
    });
    for (unsigned i = 0; i < 5000; ++i) {
        session.stop(MacroEnd::Loaded);
        session.takeCompletion();
        session.load(Macro(2), "restart");
        assert(session.begin(0, 2, true, true));
    }
    session.stop(MacroEnd::Disabled);
    done = true;
    tick.join();
    auto result = session.takeCompletion();
    assert(result && result->reason == MacroEnd::Disabled);
}

int main() {
    rangesAndLoops();
    failuresAndRestart();
    admittedStopAndReplace();
    restartWithConcurrentCleanup();
}
