#include "mod/MovementModel.h"

#include <cassert>
#include <cstdlib>
#include <new>
#include <thread>

using namespace probe::detail;
std::atomic<unsigned> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto ptr = std::malloc(size))
        return ptr;
    throw std::bad_alloc();
}
void operator delete(void* ptr) noexcept {
    std::free(ptr);
}
void operator delete(void* ptr, std::size_t) noexcept {
    std::free(ptr);
}

void edges() {
    auto const before = allocations.load();
    bool held = false, delivered = false;
    assert(!trackedInputEdge(held, true, [&] {
        delivered = true;
        return false;
    }));
    assert(held && delivered); // Delivery followed by an error still needs key-up.
    unsigned releases = 0;
    assert(!trackedInputEdge(held, false, [&] {
        ++releases;
        return false;
    }));
    assert(held);
    assert(trackedInputEdge(held, false, [&] {
        ++releases;
        return true;
    }));
    assert(!held && releases == 2);
    assert(trackedInputEdge(held, false, [] {
        assert(false);
        return true;
    }));
    try {
        trackedInputEdge(held, true, []() -> bool { throw 1; });
        assert(false);
    } catch (int) {
    }
    assert(held);
    assert(trackedInputEdge(held, false, [] { return true; }));
    assert(!held && allocations.load() == before);
}

void flags() {
    std::atomic_bool flag{false};
    auto const before = allocations.load();
    {
        InjectionScope outer(flag);
        assert(flag.load());
        try {
            InjectionScope inner(flag);
            throw 1;
        } catch (int) {
        }
        assert(flag.load());
    }
    assert(!flag.load());
    flag = true;
    {
        InjectionScope nested(flag);
    }
    assert(flag.load() && allocations.load() == before);
}

void diagnostics() {
    ControlDiagnostics counters;
    std::thread a([&] {
        for (unsigned i = 0; i < 100000; ++i)
            counters.record(ControlOp::Turn, ControlIssue::Fault);
    });
    std::thread b([&] {
        for (unsigned i = 0; i < 100000; ++i)
            counters.record(ControlOp::Turn, ControlIssue::Failed);
    });
    auto const before = allocations.load();
    ControlDiagnostics::Counts sum{};
    for (unsigned i = 0; i < 100000; ++i) {
        auto const counts = counters.take(ControlOp::Turn);
        for (unsigned n = 0; n < sum.size(); ++n)
            sum[n] += counts[n];
        counters.record(ControlOp::KeyUp, ControlIssue::Unavailable);
    }
    a.join();
    b.join();
    auto const tail = counters.take(ControlOp::Turn);
    assert(sum[0] + tail[0] == 100000 && sum[1] + tail[1] == 100000 && sum[2] == 0);
    assert(counters.take(ControlOp::KeyUp)[2] == 100000);
    assert(counters.take(ControlOp::KeyUp)[2] == 0);
    assert(allocations.load() == before);
}

void concurrentTurns() {
    std::atomic<std::uint32_t> depth{0};
    std::atomic_bool entered{false}, leave{false};
    std::thread other;
    {
        InjectionCountScope outer(depth);
        other = std::thread([&] {
            InjectionCountScope overlap(depth);
            entered = true;
            while (!leave.load())
                std::this_thread::yield();
            assert(depth.load() == 1);
        });
        while (!entered.load())
            std::this_thread::yield();
        assert(depth.load() == 2);
    }
    assert(depth.load() == 1);
    leave = true;
    other.join();
    assert(depth.load() == 0);
}

int main() {
    edges();
    flags();
    diagnostics();
    concurrentTurns();
}
