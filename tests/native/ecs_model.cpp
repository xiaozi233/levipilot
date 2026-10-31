#include "mod/EcsModel.h"

#include <cassert>
#include <cstdlib>
#include <future>
#include <new>
#include <thread>
#include <vector>

using namespace probe::detail;

std::atomic<unsigned> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto value = std::malloc(size)) return value;
    throw std::bad_alloc();
}
void operator delete(void* value) noexcept { std::free(value); }
void operator delete(void* value, std::size_t) noexcept { std::free(value); }

void boundedRows() {
    std::vector<std::pair<std::uint32_t, unsigned>> rows(EcsSnapshot::MaxVisited + 1);
    for (unsigned i = 0; i < rows.size(); ++i) rows[i] = {i, i};
    auto out = std::make_unique<EcsSnapshot>();
    unsigned calls = 0;
    auto probe = [&](unsigned id, auto& row, bool& present) {
        ++calls;
        if (id == 1) return false;
        if (id == 2) throw 42;
        present = id % 2 == 0;
        row.address = id + 100;
        return true;
    };
    auto const before = allocations.load();
    collectEcsRows(EcsOperation::Components, *out, rows, probe);
    assert(allocations.load() == before);
    assert(out->count == 1024 && out->rows.back().address == 2148);
    assert(out->skippedCount == 2 && out->skipped[0] == 1 && out->skipped[1] == 2);
    assert(out->truncated && calls == 2051);

    *out = {};
    calls = 0;
    collectEcsRows(EcsOperation::Entity, *out, rows, probe);
    assert(out->count == 2047 && out->visited == 4096 && out->truncated);
    assert(calls == 4096 && out->skippedCount == 2);
    *out = {};
    collectEcsRows(EcsOperation::Registries, *out, rows, probe);
    assert(out->count == 4096 && out->visited == 4096 && out->truncated && calls == 4096);

    *out = {};
    calls = 0;
    collectEcsRows(EcsOperation::Components, *out, rows, [&](unsigned, auto&, bool& present) {
        ++calls;
        present = false;
        return true;
    });
    assert(out->count == 0 && out->visited == 4096 && calls == 4096 && out->truncated);
    *out = {};
    collectEcsRows(EcsOperation::Components, *out, rows, [](unsigned, auto&, bool&) { return false; });
    assert(out->skippedCount == 1024 && out->visited == 1025 && out->truncated);
    assert(allocations.load() == before);
}

void stagesAndRetirement() {
    EcsCapture capture;
    auto request = capture.begin({}, 0, 10);
    assert(request && !capture.begin({}, 1));
    assert(request->dispatch() == EcsStage::Client);
    request->run(EcsStage::Client, 1, [](auto& out) {
        out.registry = 42;
        out.entity = 7;
        return EcsStage::Server;
    });
    assert(request->dispatch() == EcsStage::Server);
    std::promise<void> entered, leave;
    auto gate = leave.get_future();
    std::thread worker([&] {
        request->run(EcsStage::Server, 2, [&](auto& out) {
            assert(out.registry == 42 && out.entity == 7);
            entered.set_value();
            gate.wait();
            out.bytes.back() = 99;
            return EcsStage::Complete;
        });
    });
    entered.get_future().wait();
    auto timedOut = capture.take(10);
    assert(timedOut.request == request && std::string_view(timedOut.error) == "capture_timeout");
    assert(!capture.take(11).request && timedOut.release());
    auto fresh = capture.begin({}, 12);
    assert(fresh && fresh != request && fresh->snapshot.bytes.back() == 0);
    leave.set_value();
    worker.join();
    assert(fresh->snapshot.bytes.back() == 0);
    capture.cancel("world_left");
    auto cancelled = capture.take(13);
    assert(cancelled.request == fresh && std::string_view(cancelled.error) == "world_left");
    assert(cancelled.release());
    request = capture.begin({}, 14);
    assert(request->dispatch());
    request->run(EcsStage::Client, 15, [](auto&) -> EcsStage { throw 42; });
    auto failed = capture.take(16);
    assert(failed.request && std::string_view(failed.error) == "capture_failed");
    capture.reset();
    assert(!failed.release()); // Disconnect discards a completion already formatting.
    request = capture.begin({}, 17);
    std::weak_ptr<EcsCapture::Request> weak = request;
    capture.reset();
    request.reset();
    assert(weak.expired() && !capture.take(18).request);
}

void crossThreadPublication() {
    EcsCapture capture;
    for (unsigned i = 0; i < 500; ++i) {
        auto request = capture.begin({}, 0);
        assert(request && request->dispatch() == EcsStage::Client);
        std::thread client([&] {
            request->run(EcsStage::Client, 1, [&](auto& out) {
                out.registry = i;
                return EcsStage::Server;
            });
        });
        while (!request->dispatch()) std::this_thread::yield();
        std::thread server([&] {
            request->run(EcsStage::Server, 2, [&](auto& out) {
                assert(out.registry == i);
                out.bytes.back() = 255;
                return EcsStage::Complete;
            });
        });
        client.join();
        server.join();
        auto done = capture.take(3);
        assert(done.request && !done.error && done.request->snapshot.bytes.back() == 255);
    }
}

int main() {
    boundedRows();
    stagesAndRetirement();
    crossThreadPublication();
}
