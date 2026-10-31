#include "mod/PlayerIdentityModel.h"

#include <cassert>
#include <cstdlib>
#include <new>
#include <thread>

using namespace probe::detail;
std::atomic<unsigned> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto ptr = std::malloc(size)) return ptr;
    throw std::bad_alloc();
}
void operator delete(void* ptr) noexcept { std::free(ptr); }
void operator delete(void* ptr, std::size_t) noexcept { std::free(ptr); }

void invalidation() {
    PlayerIdentityStore store;
    PlayerIdentity out;
    out.uuidA = 123;
    assert(!store.read(out, 1) && out.uuidA == 123);
    auto const before = allocations.load();
    store.capture(1, 1, [](auto& row) { row.uuidA = 42; row.uuidB = 99; return true; });
    assert(store.read(out, 1) && out.uuidA == 42 && out.uuidB == 99 && out.tick == 1);
    auto const saved = out;
    assert(saved.matches(1, true) && !saved.matches(1, false) && !saved.matches(2, true));
    assert(!store.read(out, 2) && out.epoch == 1);
    store.capture(1, 2, [](auto&) { return false; });
    assert(!store.read(out, 1)); // No last-known-good identity after failed read.
    store.capture(1, 3, [](auto&) -> bool { throw 42; });
    assert(!store.read(out, 1));
    store.capture(1, 4, [](auto&) { return true; });
    assert(!store.read(out, 1));
    auto const failures = store.failures();
    assert(failures.fault == 1 && failures.cpp == 1 && failures.empty == 1);
    store.capture(2, 5, [](auto& row) { row.uuidB = 99; return true; });
    assert(store.read(out, 2) && !store.read(out, 1));
    assert(out.uuidA == 0 && out.uuidB == 99 && out.epoch == 2);
    assert(!saved.matches(out.epoch, true)); // Same UUID in another world is obsolete.
    assert(allocations.load() == before);
}

void concurrentSnapshots() {
    PlayerIdentityStore store;
    std::atomic_bool done{false};
    std::atomic<unsigned> checked{0};
    auto reader = [&] {
        while (!done.load()) {
            PlayerIdentity out;
            if (!store.read(out, 1)) continue;
            assert(out.valid && out.epoch == 1 && out.uuidA == out.tick && out.uuidB == ~out.tick);
            ++checked;
        }
    };
    std::thread a(reader), b(reader), c(reader), d(reader);
    auto const before = allocations.load();
    for (std::uint64_t i = 1; i <= 200000; ++i) store.capture(1, i, [&](auto& row) {
        row.uuidA = i; row.uuidB = ~i; return true;
    });
    assert(allocations.load() == before);
    done = true;
    a.join(); b.join(); c.join(); d.join();
    assert(checked > 0);
}

int main() { invalidation(); concurrentSnapshots(); }
