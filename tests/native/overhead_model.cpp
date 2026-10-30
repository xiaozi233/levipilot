#include "mod/OverheadModel.h"

#include <atomic>
#include <cassert>
#include <cstdlib>
#include <limits>
#include <new>
#include <thread>
#include <vector>

std::atomic<unsigned> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto* value = std::malloc(size)) return value;
    throw std::bad_alloc();
}
void operator delete(void* value) noexcept { std::free(value); }
void operator delete(void* value, std::size_t) noexcept { std::free(value); }

using namespace probe::detail;
constexpr std::uint32_t Salt = 0xC3B42A19;

template <class Snapshot> void validate(Snapshot const& snapshot) {
    if (!snapshot.consistent) {
        assert(snapshot.count == 0);
        return;
    }
    assert(snapshot.count <= snapshot.rows.size() && snapshot.count <= snapshot.head);
    for (std::size_t i = 0; i < snapshot.count; ++i) {
        auto const expected = static_cast<std::uint32_t>(snapshot.head - snapshot.count + i);
        assert(snapshot.rows[i].tickUs == expected);
        assert(snapshot.rows[i].hookUs == (expected ^ Salt));
    }
}

int main() {
    OverheadWindow<16> window;
    window.record({1, 2}); // Before cold initialization, do not allocate or retain rows.
    assert(!window.available() && !window.snapshot().consistent);
    assert(window.initialize());
    auto empty = window.snapshot();
    assert(empty.consistent && empty.count == 0 && empty.head == 0);
    auto const baseline = allocations.load();
    for (std::uint32_t i = 0; i < 4096; ++i) {
        window.record({i, i ^ Salt});
        auto snapshot = window.snapshot();
        validate(snapshot);
        assert(snapshot.consistent && snapshot.count == std::min<std::size_t>(i + 1, 16));
        assert(snapshot.head == i + 1);
    }
    auto const boundedFailure = window.snapshot(0);
    assert(!boundedFailure.consistent && boundedFailure.count == 0);
    assert(allocations.load() == baseline); // recording and window copy allocate nothing

    OverheadWindow<1> boundary;
    assert(boundary.initialize());
    boundary.record({0, std::numeric_limits<std::uint32_t>::max()});
    auto edge = boundary.snapshot();
    assert(edge.consistent && edge.count == 1 && edge.rows[0].tickUs == 0
        && edge.rows[0].hookUs == std::numeric_limits<std::uint32_t>::max());
    boundary.record({99, 100});
    assert(boundary.snapshot().rows[0].tickUs == 99);

    std::atomic_bool start{false}, done{false};
    std::atomic<unsigned> checked{0};
    std::vector<std::thread> readers;
    for (unsigned n = 0; n != 4; ++n) {
        readers.emplace_back([&] {
            while (!start.load()) std::this_thread::yield();
            do {
                auto const snapshot = window.snapshot();
                validate(snapshot);
                if (snapshot.consistent) ++checked;
            } while (!done.load());
        });
    }
    start = true;
    for (std::uint32_t i = 4096; i != 204096; ++i) {
        window.record({i, i ^ Salt});
    }
    done = true;
    for (auto& reader : readers) reader.join();
    assert(checked > 0);
    auto const final = window.snapshot();
    validate(final);
    assert(final.head == 204096 && final.count == 16 && final.consistent);

    PathRateCounter rate;
    readers.clear();
    for (unsigned n = 0; n != 4; ++n) {
        readers.emplace_back([&, n] {
            for (unsigned i = 0; i != 10000; ++i) rate.record(100 + n);
        });
    }
    for (auto& reader : readers) reader.join();
    assert(rate.snapshot().total == 40000);
    assert(rate.snapshot().startedMs >= 100 && rate.snapshot().startedMs <= 103);
}
