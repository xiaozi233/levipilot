#include "mod/AtomicRing.h"
#include "mod/CallbackGate.h"

#include <array>
#include <atomic>
#include <cassert>
#include <thread>
#include <vector>

struct Entry {
    std::array<std::uint64_t, 2> words{};
};
using Ring = probe::detail::AtomicRing<Entry, 4>;

Entry value(std::uint64_t n) {
    Entry out;
    out.words = {n, ~n};
    return out;
}

void delayedPublication() {
    Ring ring;
    assert(ring.initialize());
    auto first = ring.reserve();
    auto second = ring.reserve();
    assert(second.publish(value(2)));
    Entry row;
    assert(ring.read(0, row) == Ring::ReadResult::pending);
    assert(ring.read(1, row) == Ring::ReadResult::ready && row.words[0] == 2);
    assert(first.publish(value(1)));
    assert(ring.read(0, row) == Ring::ReadResult::ready && row.words[0] == 1);
}

void abandonedReservationIsCounted() {
    Ring ring;
    assert(ring.initialize());
    {
        auto abandoned = ring.reserve();
        (void)abandoned;
    }
    Entry row;
    assert(ring.head() == 1);
    assert(ring.read(0, row) == Ring::ReadResult::dropped);
}

void overwrittenAndConcurrentWriters() {
    Ring ring;
    assert(ring.initialize());
    for (std::uint64_t i = 0; i < 8; ++i) {
        auto slot = ring.reserve();
        assert(slot.publish(value(i)));
    }
    Entry row;
    assert(ring.read(0, row) == Ring::ReadResult::overwritten);
    assert(ring.read(4, row) == Ring::ReadResult::ready && row.words[0] == 4);

    Ring concurrent;
    assert(concurrent.initialize());
    constexpr std::uint64_t kWriters = 4;
    constexpr std::uint64_t kRows    = 2000;
    std::vector<std::thread> writers;
    for (std::uint64_t writer = 0; writer < kWriters; ++writer) {
        writers.emplace_back([&concurrent, writer] {
            for (std::uint64_t i = 0; i < kRows; ++i) {
                auto slot = concurrent.reserve();
                (void)slot.publish(value((writer << 32) | i));
            }
        });
    }
    for (auto& writer : writers) {
        writer.join();
    }
    auto const head = concurrent.head();
    assert(head == kWriters * kRows);
    auto const begin = head > 4 ? head - 4 : 0;
    for (std::uint64_t i = begin; i < head; ++i) {
        assert(concurrent.read(i, row) == Ring::ReadResult::ready);
        assert(row.words[1] == ~row.words[0]);
    }
}

void callbackGateClosesEntryBeforeWaiting() {
    probe::detail::CallbackGate gate;
    assert(!gate.enter());
    gate.open();
    assert(gate.enter());

    std::atomic_bool closed{false};
    std::thread closer([&] {
        gate.closeAndWait();
        closed.store(true, std::memory_order_release);
    });
    while (gate.enter()) {
        gate.leave();
        std::this_thread::yield();
    }
    assert(!closed.load(std::memory_order_acquire));
    gate.leave();
    closer.join();
    assert(closed.load(std::memory_order_acquire));
    assert(!gate.enter());

    gate.open();
    assert(gate.enter());
    gate.leave();
    gate.closeAndWait();
}

int main() {
    delayedPublication();
    abandonedReservationIsCounted();
    overwrittenAndConcurrentWriters();
    callbackGateClosesEntryBeforeWaiting();
}
