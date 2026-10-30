#include "mod/EntityModel.h"

#include <cassert>
#include <future>
#include <thread>

using namespace probe::detail;

void stages() {
    EntityCapture capture;
    auto request = capture.begin({}, 0);
    assert(request && !capture.begin({}, 1));
    assert(request->dispatch() == EntityStage::Client);
    assert(!request->dispatch());
    request->run(EntityStage::Client, 1, [](auto& out) {
        out.selfId = 42;
        return EntityStage::Server;
    });
    assert(!capture.take(2).request);
    assert(request->dispatch() == EntityStage::Server);
    request->run(EntityStage::Server, 2, [](auto& out) {
        assert(out.selfId == 42);
        out.rows.back().name.assign("bounded last row");
        out.count = out.rows.size();
        return EntityStage::Aim;
    });
    assert(request->dispatch() == EntityStage::Aim);
    request->run(EntityStage::Aim, 3, [](auto& out) {
        out.ok = true;
        return EntityStage::Complete;
    });
    auto done = capture.take(4);
    assert(done.request == request && !done.error);
    assert(done.request->snapshot.ok && done.request->snapshot.count == 512);
    assert(done.request->snapshot.rows.back().name.view() == "bounded last row");
    assert(!capture.take(5).request);
    assert(!request->dispatch());
    assert(done.release());
}

void cancellation() {
    EntityCapture capture;
    auto old = capture.begin({}, 0, 10);
    assert(old->dispatch() == EntityStage::Client);
    std::promise<void> entered, release;
    auto gate = release.get_future();
    std::thread worker([&] {
        old->run(EntityStage::Client, 1, [&](auto& out) {
            entered.set_value();
            gate.wait();
            out.selfId = 99;
            return EntityStage::Server;
        });
    });
    entered.get_future().wait();
    assert(!capture.take(9).request);
    auto timedOut = capture.take(10);
    assert(timedOut.request == old && std::string_view(timedOut.error) == "capture_timeout");
    assert(timedOut.release());
    auto fresh = capture.begin({}, 11);
    assert(fresh && fresh != old && fresh->snapshot.selfId == 0);
    release.set_value();
    worker.join();
    if (auto stage = old->dispatch()) old->run(*stage, 12, [](auto&) {
        assert(false); // No later side effect after timeout, even with a live strong owner.
        return EntityStage::Complete;
    });
    assert(fresh->snapshot.selfId == 0);
    capture.cancel("world_left");
    auto done = capture.take(12);
    assert(done.request == fresh && std::string_view(done.error) == "world_left");
    assert(done.release());
    std::weak_ptr<EntityCapture::Request> weak;
    {
        auto abandoned = capture.begin({}, 20);
        weak = abandoned;
        capture.reset();
    }
    assert(weak.expired());
    assert(!capture.take(21).request);
}

void failures() {
    EntityCapture capture;
    auto request = capture.begin({}, 0);
    assert(request->dispatch());
    request->run(EntityStage::Client, 1, [](auto&) -> EntityStage { throw 42; });
    auto done = capture.take(2);
    assert(done.request && std::string_view(done.error) == "capture_failed");
    assert(done.release());
    request = capture.begin({}, 3);
    assert(request->dispatch());
    request->run(EntityStage::Server, 4, [](auto&) {
        assert(false);
        return EntityStage::Complete;
    });
    done = capture.take(5);
    assert(done.request && std::string_view(done.error) == "entity_stage_invalid");
    capture.reset();
    assert(!done.release()); // A reconnect discards a completion being formatted.
}

void publication() {
    EntityCapture capture;
    for (unsigned n = 0; n < 500; ++n) {
        auto request = capture.begin({}, 0);
        assert(request && request->dispatch() == EntityStage::Client);
        std::thread client([&] {
            request->run(EntityStage::Client, 1, [&](auto& out) {
                out.selfId = n;
                return EntityStage::Server;
            });
        });
        while (!request->dispatch()) std::this_thread::yield();
        std::thread server([&] {
            request->run(EntityStage::Server, 2, [&](auto& out) {
                assert(out.selfId == n);
                out.rows.front().runtimeId = n + 1;
                return EntityStage::Complete;
            });
        });
        client.join();
        server.join();
        auto done = capture.take(3);
        assert(done.request && !done.error && done.request->snapshot.rows.front().runtimeId == n + 1);
    }
}

void slots() {
    SlotCapture capture;
    assert(capture.begin({8}, 0, 2000));
    capture.advance([](auto const& query, auto& out) {
        out.slot = query.slot;
        out.applied = 8;
        out.present = true;
        out.type.assign("minecraft:stone");
        return true;
    });
    {
        auto done = capture.take(1);
        assert(done && done.snapshot().slot == 8 && done.snapshot().present);
    }
    assert(capture.begin({0}, 2, 2000));
    capture.advance([](auto const&, auto& out) {
        assert(!out.present && out.type.empty() && out.applied == -1);
        return true;
    });
    assert(capture.take(3));
}

int main() { stages(); cancellation(); failures(); publication(); slots(); }
