#include "mod/QueryModel.h"

#include <atomic>
#include <cassert>
#include <cstdlib>
#include <future>
#include <limits>
#include <memory>
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

void lifecycle() {
    StateCapture capture;
    assert(capture.begin({}, 100, 2000));
    assert(!capture.begin({}, 101, 2000));
    assert(!capture.take(102));
    assert(!capture.advance([](auto const&, auto&) { return false; }));
    assert(capture.advance([](auto const&, auto& out) {
        out.tick = 42;
        return true;
    }));
    assert(!capture.advance([](auto const&, auto&) {
        assert(false);
        return true;
    }));
    {
        auto result = capture.take(103);
        assert(result && !result.error && result.snapshot().tick == 42);
        assert(!capture.take(104));
        assert(!capture.begin({}, 104, 2000)); // Consumer owns the result until released.
        capture.reset();                       // Must not overwrite a response being formatted.
        assert(result.snapshot().tick == 42);
        assert(!result.release()); // Reset discards a response already being formatted.
    }
    assert(capture.begin({}, 200, 10));
    assert(!capture.take(209));
    {
        auto result = capture.take(210);
        assert(result && std::string_view(result.error) == "capture_timeout");
    }
    assert(capture.begin({}, 300, 10));
    assert(capture.advance([](auto const&, auto&) -> bool { throw 42; }));
    {
        auto result = capture.take(301);
        assert(result && std::string_view(result.error) == "capture_failed");
    }
    assert(capture.begin({}, 400, 10));
    capture.cancel("world_left");
    {
        auto result = capture.take(401);
        assert(result && std::string_view(result.error) == "world_left");
    }
    assert(capture.begin({}, 500, 10));
    capture.reset();
    assert(!capture.take(501));
    assert(capture.begin({}, 502, 10));
    capture.advance([](auto const&, auto& out) {
        assert(out.tick == 0);
        return true;
    });
    assert(capture.take(503));
}

void runningCancellation(bool reset) {
    StateCapture capture;
    assert(capture.begin({}, 0, 10));
    std::promise<void> entered, release;
    auto gate = release.get_future();
    std::thread producer([&] {
        capture.advance([&](auto const&, auto& out) {
            entered.set_value();
            gate.wait();
            out.tick = 999; // A stale batch may finish, but cannot touch new storage.
            return false;
        });
    });
    entered.get_future().wait();
    if (reset)
        capture.reset();
    else
        capture.cancel("world_left");
    assert(!capture.take(10));
    assert(!capture.begin({}, 11, 10)); // Never recycle a running batch.
    release.set_value();
    producer.join();
    if (reset)
        assert(!capture.take(12));
    else {
        auto done = capture.take(12);
        assert(done && std::string_view(done.error) == "world_left");
    }
    assert(capture.begin({}, 13, 10));
    capture.advance([](auto const&, auto& out) {
        assert(out.tick == 0);
        out.tick = 1;
        return true;
    });
    assert(capture.take(14).snapshot().tick == 1);
}

void scans() {
    auto storage = std::make_unique<ScanSnapshot>();
    auto& out = *storage;
    ScanQuery query{{31, 31, 31}, {0, 0, 0}, 1, false};
    auto const baseline = allocations.load();
    unsigned reads = 0;
    for (unsigned batch = 0; batch < 64; ++batch) {
        auto const before = out.visited;
        bool done = advanceScan(query, out, 100 + batch, [&](BlockRow& row) {
            assert(row.pos.x == static_cast<int>(reads % 32));
            assert(row.pos.y == static_cast<int>((reads / 32) % 32));
            assert(row.pos.z == static_cast<int>(reads / 1024));
            row.name.assign("minecraft:air");
            ++reads;
            return true;
        });
        assert(out.visited - before == 512 && done == (batch == 63));
    }
    assert(reads == 32768 && out.count == 0 && out.batches == 64 && !out.truncated);
    assert(out.firstTick == 100 && out.lastTick == 163);
    assert(allocations.load() == baseline);

    std::destroy_at(&out);
    std::construct_at(&out);
    query = {{0, 0, 0}, {31, 31, 31}, 3, true};
    assert(advanceScan(query, out, 1, [](BlockRow& row) {
        row.name.assign("minecraft:stone");
        return true;
    }));
    assert(out.count == 3 && out.visited == 4 && out.truncated);
    std::destroy_at(&out);
    std::construct_at(&out);
    query.to = {2, 0, 0};
    assert(advanceScan(query, out, 1, [](BlockRow& row) {
        row.name.assign("minecraft:stone");
        return true;
    }));
    assert(out.count == 3 && !out.truncated); // Exactly limit is still complete.
    std::destroy_at(&out);
    std::construct_at(&out);
    assert(advanceScan(query, out, 1, [](auto&) { return false; }));
    assert(std::string_view(out.error) == "scan_fault");
    std::destroy_at(&out);
    std::construct_at(&out);
    query.to.x = std::numeric_limits<int>::max();
    query.from.x = std::numeric_limits<int>::min();
    assert(advanceScan(query, out, 1, [](auto&) {
        assert(false);
        return true;
    }));
    assert(std::string_view(out.error) == "bad_region");
}

void inventoryPublication() {
    auto capture = std::make_unique<InventoryCapture>();
    auto const baseline = allocations.load();
    assert(capture->begin({}, 0, 2000));
    capture->advance([](auto const&, InventorySnapshot& out) {
        out.size = static_cast<int>(InventorySnapshot::Capacity);
        out.count = InventorySnapshot::Capacity;
        out.slots.back().present = true;
        out.slots.back().type.assign("minecraft:stone");
        out.slots.back().name.assign("stone");
        out.slots.back().count = 64;
        out.offhand.present = true;
        out.offhand.type.assign("minecraft:shield");
        return true;
    });
    {
        auto done = capture->take(1);
        assert(done && !done.error);
        auto const& out = done.snapshot();
        assert(out.count == InventorySnapshot::Capacity && out.slots.back().count == 64);
        assert(!out.slots.front().present && out.offhand.type.view() == "minecraft:shield");
    }
    assert(capture->begin({}, 2, 2000));
    capture->advance([](auto const&, InventorySnapshot& out) {
        assert(out.count == 0 && !out.slots.back().present && !out.offhand.present);
        return true;
    });
    assert(capture->take(3));
    assert(allocations.load() == baseline);
}

void publication() {
    StateCapture capture;
    std::atomic_bool stop{false};
    std::thread tick([&] {
        while (!stop.load()) {
            capture.advance([](auto const&, auto& out) {
                out.tick = 123;
                for (auto& text : out.blocks)
                    text.assign("consistent");
                return true;
            });
            std::this_thread::yield();
        }
    });
    for (unsigned n = 0; n < 1000; ++n) {
        assert(capture.begin({}, 0, 1000));
        while (true) {
            auto done = capture.take(1);
            if (done) {
                assert(!done.error && done.snapshot().tick == 123);
                for (auto const& text : done.snapshot().blocks)
                    assert(text.view() == "consistent");
                break;
            }
            std::this_thread::yield();
        }
    }
    stop = true;
    tick.join();
    auto const baseline = allocations.load();
    for (unsigned n = 0; n < 500; ++n) {
        assert(capture.begin({}, 0, 1000));
        capture.advance([](auto const&, auto& out) {
            out.tick = 1;
            return true;
        });
        assert(capture.take(1));
    }
    assert(allocations.load() == baseline);
}

void hookCallOwnership() {
    HookCallCapture call;
    auto const before = allocations.load();
    assert(call.begin({0.25f}, 0, 2000));
    assert(!call.begin({0.75f}, 1, 2000));
    call.advance([](auto const& request, auto& out) {
        assert(request.scale == 0.25f); // Rejected overlap cannot replace the request.
        out.scale = request.scale;
        return true;
    });
    {
        auto done = call.take(2);
        assert(done && !done.error && done.snapshot().scale == 0.25f);
    }
    assert(call.begin({0.5f}, 0, 2000));
    {
        auto expired = call.take(2000);
        assert(expired && std::string_view(expired.error) == "capture_timeout");
    }
    call.advance([](auto const&, auto&) {
        assert(false); // An expired request cannot invoke the game later.
        return true;
    });
    assert(call.begin({0.75f}, 0, 2000));
    call.cancel("world_left");
    call.advance([](auto const&, auto&) {
        assert(false);
        return true;
    });
    {
        auto cancelled = call.take(1);
        assert(cancelled && std::string_view(cancelled.error) == "world_left");
    }
    assert(call.begin({}, 0, 2000));
    call.advance([](auto const&, auto&) -> bool { throw 42; });
    auto failed = call.take(1);
    assert(failed && std::string_view(failed.error) == "capture_failed");
    assert(allocations.load() == before);
}

void scanVolumeBounds() {
    assert(scanVolumeWithinLimit({0, 0, 0}, {31, 31, 31}));
    assert(!scanVolumeWithinLimit({0, 0, 0}, {31, 31, 32}));
    assert(!scanVolumeWithinLimit({-30000000, -4096, -30000000}, {30000000, 4096, 30000000}));
    assert(!scanVolumeWithinLimit(
        {std::numeric_limits<int>::min(), -4096, std::numeric_limits<int>::min()},
        {std::numeric_limits<int>::max(), 4096, std::numeric_limits<int>::max()}
    ));

    auto storage = std::make_unique<ScanSnapshot>();
    ScanQuery overflow{{-30000000, -4096, -30000000}, {30000000, 4096, 30000000}, 4096, false};
    bool called = false;
    bool const done = advanceScan(overflow, *storage, 1, [&](BlockRow&) {
        called = true;
        return true;
    });
    assert(done && !called && std::string_view(storage->error) == "bad_region");
}

int main() {
    hookCallOwnership();
    lifecycle();
    runningCancellation(false);
    runningCancellation(true);
    scans();
    scanVolumeBounds();
    inventoryPublication();
    publication();
}
