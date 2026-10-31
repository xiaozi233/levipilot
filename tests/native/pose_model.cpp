#include "mod/PoseModel.h"

#include <cassert>
#include <cstdlib>
#include <limits>
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

using namespace probe::detail;

bool fill(PoseSnapshot& row, std::uint64_t tick) {
    row.x = row.y = row.z = row.yaw = static_cast<float>(tick);
    row.feetX = row.feetY = row.feetZ = row.pitch = -static_cast<float>(tick);
    return true;
}

void validity() {
    PoseStore store;
    PoseSnapshot out;
    out.x = 17;
    assert(!store.latest(out) && out.x == 17);
    auto const before = allocations.load();
    store.capture(1, 2, [](auto& row) { return fill(row, 2); });
    assert(!store.read(out, 2) && out.x == 17);
    assert(store.read(out, 1) && out.x == 2 && out.tick == 2 && out.epoch == 1);
    store.capture(1, 3, [](auto&) { return false; });
    assert(!store.latest(out) && out.x == 2); // No stale valid pose after fault.
    store.capture(1, 4, [](auto&) -> bool { throw 42; });
    assert(!store.latest(out));
    store.capture(1, 5, [](auto& row) {
        row.pitch = std::numeric_limits<float>::infinity();
        return true;
    });
    assert(!store.latest(out));
    auto failures = store.takeFailures();
    assert(failures.fault == 1 && failures.cpp == 1 && failures.nonFinite == 1);
    auto cleared = store.takeFailures();
    assert(!cleared.fault && !cleared.cpp && !cleared.nonFinite);
    store.capture(2, 6, [](auto& row) { return fill(row, 6); });
    assert(!store.read(out, 1) && store.read(out, 2) && out.tick == 6);
    assert(allocations.load() == before);
}

void concurrentSnapshots() {
    PoseStore store;
    std::atomic_bool done{false};
    std::atomic<unsigned> checked{0};
    std::thread reader([&] {
        while (!done.load()) {
            PoseSnapshot out;
            if (!store.latest(out))
                continue;
            auto const value = static_cast<float>(out.tick);
            assert(out.valid && out.epoch == out.tick / 1000 + 1);
            assert(out.x == value && out.y == value && out.z == value && out.yaw == value);
            assert(out.feetX == -value && out.feetY == -value && out.feetZ == -value &&
                   out.pitch == -value);
            ++checked;
        }
    });
    auto const before = allocations.load();
    for (std::uint64_t i = 1; i <= 200000; ++i) {
        store.capture(i / 1000 + 1, i, [&](auto& row) { return fill(row, i); });
        if (i % 1024 == 0)
            std::this_thread::yield();
    }
    assert(allocations.load() == before);
    done = true;
    reader.join();
    assert(checked > 0);
}

int main() {
    validity();
    concurrentSnapshots();
}
