#include "mod/MacroRecordModel.h"

#include <array>
#include <cassert>
#include <cstdlib>
#include <future>
#include <limits>
#include <new>

std::atomic<unsigned> allocations{0};
std::atomic_bool failAllocation{false};
void* operator new(std::size_t size) {
    ++allocations;
    if (!failAllocation.load())
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

void limits() {
    constexpr std::array<std::array<std::uint64_t, 2>, 6> cases{
        {{0, 200000},
         {1, 1},
         {3, 3},
         {200000, 200000},
         {200001, 200000},
         {std::numeric_limits<std::uint64_t>::max(), 200000}}};
    MacroRecordSession session;
    for (auto const& [requested, expected] : cases) {
        assert(macroRecordingLimit(requested) == expected);
        assert(session.begin(requested, [] {}));
        assert(session.limit() == expected);
        assert(!session.begin(1, [] { assert(false); }));
        auto const before = allocations.load();
        for (std::uint64_t tick = 0; tick < expected + 3; ++tick) {
            session.tick([&](auto& sample) {
                sample.buttons.w = tick % 2;
                sample.yaw = float(tick);
                return true;
            });
        }
        assert(allocations.load() == before);
        assert(!session.active() && session.rows() == expected);
        auto done = session.takeCompletion();
        assert(done && done->ok() && done->reason == MacroRecordEnd::Limit &&
               done->rows == expected);
        assert(!session.takeCompletion());
        session.stop();
        auto snapshot = session.snapshot();
        assert(snapshot.rows.size() == expected && snapshot.completion.recordId == done->recordId);
        assert(!snapshot.rows.front().w && snapshot.rows.back().w == bool((expected - 1) % 2));
        if (expected > 1)
            assert(snapshot.rows.back().yaw == 1);
        assert(session.clear(done->recordId) && session.rows() == 0);
    }
}

void rotationsAndReplacement() {
    MacroRecordSession session;
    assert(session.begin(8, [] {}));
    for (auto [yaw, pitch] : {std::pair{179.f, 10.f},
                              std::pair{-179.f, 12.f},
                              std::pair{179.f, 11.f},
                              std::pair{179.0001f, 11.0001f}}) {
        session.tick([&](auto& sample) {
            sample.yaw = yaw;
            sample.pitch = pitch;
            return true;
        });
    }
    session.stop();
    auto old = session.snapshot();
    assert(old.rows[0].yaw == 0 && old.rows[0].pitch == 0);
    assert(old.rows[1].yaw == 2 && old.rows[1].pitch == -2);
    assert(old.rows[2].yaw == -2 && old.rows[2].pitch == 1);
    assert(old.rows[3].yaw == 0 && old.rows[3].pitch == 0);
    failAllocation = true;
    try {
        session.begin(4, [] { assert(false); });
        assert(false);
    } catch (std::bad_alloc const&) {
    }
    failAllocation = false;
    auto after = session.snapshot();
    assert(after.rows.size() == 4 && after.completion.recordId == old.completion.recordId);
    try {
        session.begin(4, [] { throw 42; });
        assert(false);
    } catch (int) {
    }
    assert(session.rows() == 4);
    assert(session.begin(2, [] {}));
    assert(!session.clear(old.completion.recordId)); // An old save cannot erase fresh storage.
    session.tick([](auto& sample) {
        sample.yaw = -17;
        sample.pitch = -33;
        return true;
    });
    session.stop();
    auto fresh = session.snapshot();
    assert(fresh.rows.size() == 1 && fresh.rows[0].yaw == 0 && fresh.rows[0].pitch == 0);
    assert(old.rows[1].yaw == 2); // Old saved snapshot is independent.
}

void faults() {
    MacroRecordSession session;
    for (auto failure : {MacroRecordEnd::CaptureFault,
                         MacroRecordEnd::CaptureFailed,
                         MacroRecordEnd::WorldLeft,
                         MacroRecordEnd::Disabled}) {
        assert(session.begin(8, [] {}));
        session.tick([](auto& sample) {
            sample.buttons.jump = true;
            return true;
        });
        if (failure == MacroRecordEnd::CaptureFault)
            session.tick([](auto&) { return false; });
        else if (failure == MacroRecordEnd::CaptureFailed)
            session.tick([](auto&) -> bool { throw 42; });
        else
            session.stop(failure);
        session.stop();
        auto done = session.takeCompletion();
        assert(done && !done->ok() && done->reason == failure && done->rows == 1);
        session.tick([](auto&) {
            assert(false);
            return true;
        });
        auto snapshot = session.snapshot();
        assert(snapshot.rows.size() == 1 && snapshot.rows[0].jump && !snapshot.completion.ok());
        assert(!session.takeCompletion());
    }
    assert(session.begin(2, [] {}));
    session.tick([](auto& sample) {
        sample.yaw = std::numeric_limits<float>::infinity();
        return true;
    });
    assert(!session.active() && session.rows() == 0 &&
           session.takeCompletion()->reason == MacroRecordEnd::CaptureFailed);
}

void stopWaitsForAdmission(bool fail) {
    MacroRecordSession session;
    assert(session.begin(8, [] {}));
    std::promise<void> entered, release;
    auto gate = release.get_future();
    std::thread producer([&] {
        session.tick([&](auto& sample) {
            entered.set_value();
            gate.wait();
            sample.buttons.w = true;
            return !fail;
        });
    });
    entered.get_future().wait();
    auto stop = std::async(std::launch::async, [&] { session.stop(); });
    while (session.active())
        std::this_thread::yield();
    assert(stop.wait_for(std::chrono::milliseconds(0)) != std::future_status::ready);
    assert(!session.takeCompletion());
    release.set_value();
    stop.get();
    producer.join();
    auto snapshot = session.snapshot();
    assert(snapshot.rows.size() == (fail ? 0 : 1));
    assert(snapshot.completion.ok() == !fail);
    assert(snapshot.completion.reason ==
           (fail ? MacroRecordEnd::CaptureFault : MacroRecordEnd::Client));
}

void publishCounts() {
    MacroRecordSession session;
    assert(session.begin(200000, [] {}));
    std::thread producer([&] {
        for (int i = 0; i < 200000; ++i)
            session.tick([&](auto& sample) {
                sample.buttons.w = i % 2;
                sample.buttons.jump = i % 3;
                return true;
            });
    });
    std::uint64_t last = 0;
    while (session.active()) {
        auto count = session.rows();
        assert(count >= last && count <= 200000);
        last = count;
    }
    session.stop();
    producer.join();
    auto snapshot = session.snapshot();
    for (std::size_t i = 0; i < snapshot.rows.size(); ++i) {
        assert(snapshot.rows[i].w == bool(i % 2) && snapshot.rows[i].jump == bool(i % 3));
    }
    assert(snapshot.rows.size() == 200000);
}

void clickEdges() {
    std::atomic_bool held{true}, edge{true};
    auto const before = allocations.load();
    assert(consumeMacroClick(held, edge));
    assert(!edge && consumeMacroClick(held, edge));
    held = false;
    assert(!consumeMacroClick(held, edge)); // A held press cannot survive release as a fresh click.
    edge = true; // A complete down/up between samples is still captured exactly once.
    assert(consumeMacroClick(held, edge) && !consumeMacroClick(held, edge));
    assert(allocations.load() == before);
}

int main() {
    clickEdges();
    limits();
    rotationsAndReplacement();
    faults();
    stopWaitsForAdmission(false);
    stopWaitsForAdmission(true);
    publishCounts();
}
