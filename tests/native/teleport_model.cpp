#include "mod/TeleportModel.h"

#include <cassert>
#include <cstdlib>
#include <new>
#include <string_view>
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

TeleportQuery query(unsigned id = 1) {
    TeleportQuery value;
    value.id = id;
    value.generation = 4;
    value.deadline = 2000;
    value.identity = {7, 8, 3, 12, 1};
    return value;
}
bool is(char const* value, std::string_view expected) { return value && value == expected; }

void admission_and_operations() {
    auto q = query();
    assert(!teleportAdmission(q, 4, 3, true, 1999));
    assert(is(teleportAdmission(q, 4, 3, true, 2000), "teleport_timeout"));
    assert(is(teleportAdmission(q, 4, 4, true, 0), "world_left"));
    assert(is(teleportAdmission(q, 5, 3, true, 0), "world_left"));
    assert(is(teleportAdmission(q, 4, 3, false, 0), "world_left"));
    q.identity.valid = 0;
    assert(is(teleportAdmission(q, 4, 3, true, 0), "world_left"));
    q = query();
    unsigned prepared = 0, cleared = 0, moved = 0;
    auto prepare = [&]() -> char const* { ++prepared; return nullptr; };
    auto clear = [&] { ++cleared; };
    auto move = [&] { ++moved; };
    auto noFault = [](TeleportTest) {};
    auto admit = [&] { return teleportAdmission(q, 4, 3, true, 1); };
    auto const before = allocations.load();
    TeleportSnapshot out;
    applyTeleport(q, out, admit, prepare, clear, move, noFault);
    assert(prepared == 1 && !cleared && moved == 1 && out.applied && !out.effectsCleared);
    q.clearEffects = true;
    out = {};
    applyTeleport(q, out, admit, prepare, clear, move, noFault);
    assert(cleared == 1 && moved == 2 && out.effectsCleared && out.applied);
    // Faults after clearing/moving preserve those effects, never imply rollback.
    out = {};
    try {
        applyTeleport(q, out, admit, prepare, clear, move, [](TeleportTest point) {
            if (point == TeleportTest::AfterEffects) throw 42;
        });
        assert(false);
    } catch (int) {}
    assert(cleared == 2 && moved == 2 && out.effectsCleared && !out.applied);
    out = {};
    try {
        applyTeleport(q, out, admit, prepare, clear, move, [](TeleportTest point) {
            if (point == TeleportTest::AfterTeleport) throw 42;
        });
        assert(false);
    } catch (int) {}
    assert(cleared == 3 && moved == 3 && out.effectsCleared && out.applied);
    // A world change/timeout while preparing prevents every later mutation.
    out = {};
    auto generation = q.generation;
    applyTeleport(q, out,
        [&] { return teleportAdmission(q, generation, 3, true, 1); },
        [&]() -> char const* { ++generation; return nullptr; }, clear, move, noFault);
    assert(is(out.error, "world_left") && cleared == 3 && moved == 3);
    out = {};
    std::uint64_t now = 0;
    applyTeleport(q, out,
        [&] { return teleportAdmission(q, 4, 3, true, now); }, prepare,
        [&] { ++cleared; now = q.deadline; }, move, noFault);
    assert(is(out.error, "teleport_timeout") && out.effectsCleared && !out.applied && moved == 3);
    out = {};
    applyTeleport(q, out, admit, [] { return "teleport_player_not_found"; }, clear, move, noFault);
    assert(is(out.error, "teleport_player_not_found") && cleared == 4 && moved == 3);
    assert(allocations.load() == before);
}

void lifecycle() {
    TeleportCapture capture{TeleportStage::Server};
    auto first = capture.begin(query(), 0);
    assert(first && !capture.begin(query(2), 0));
    assert(first->dispatch() == TeleportStage::Server && !first->dispatch());
    auto timeout = capture.take(2000);
    assert(timeout.request == first && is(timeout.error, "capture_timeout"));
    assert(timeout.release() && !capture.take(2001).request);
    auto fresh = capture.begin(query(2), 2001);
    bool called = false;
    first->run(TeleportStage::Server, 2001, [&](auto&) { called = true; return TeleportStage::Complete; });
    assert(!called && fresh->dispatch() == TeleportStage::Server);
    fresh->run(TeleportStage::Server, 2002, [](auto& out) { out.applied = true; return TeleportStage::Complete; });
    auto success = capture.take(2003);
    assert(success.request == fresh && !success.error && success.request->snapshot.applied);
    assert(success.release());
    auto cancelled = capture.begin(query(3), 3000);
    assert(cancelled->dispatch());
    capture.cancel("world_left");
    cancelled->run(TeleportStage::Server, 3001, [&](auto&) { called = true; return TeleportStage::Complete; });
    assert(!called);
    auto left = capture.take(3002);
    assert(is(left.error, "world_left") && left.release());
    auto obsolete = capture.begin(query(4), 4000);
    std::weak_ptr weak = obsolete;
    assert(obsolete->dispatch());
    capture.reset();
    auto current = capture.begin(query(5), 4001);
    obsolete->run(TeleportStage::Server, 4002, [&](auto&) { called = true; return TeleportStage::Complete; });
    assert(!called && current && capture.current() == current);
    obsolete.reset();
    assert(weak.expired());
    assert(current->dispatch());
    current->run(TeleportStage::Server, 4002, [](auto&) -> TeleportStage { throw 42; });
    auto failed = capture.take(4003);
    assert(is(failed.error, "capture_failed") && failed.release());
}

void concurrent_cancel() {
    TeleportCapture capture{TeleportStage::Server};
    auto request = capture.begin(query(), 0);
    assert(request->dispatch());
    std::atomic_bool running{false}, proceed{false};
    std::thread server([&] {
        request->run(TeleportStage::Server, 1, [&](auto& out) {
            running = true;
            while (!proceed.load()) std::this_thread::yield();
            // A running SDK call can finish after timeout. Its private result
            // must not overwrite or complete a replacement request.
            out.applied = true;
            return TeleportStage::Complete;
        });
    });
    while (!running.load()) std::this_thread::yield();
    auto timeout = capture.take(2000);
    assert(timeout.request == request && is(timeout.error, "capture_timeout"));
    assert(timeout.release()); // Do not read the still-running snapshot.
    auto replacement = capture.begin(query(2), 2001);
    proceed = true;
    server.join();
    assert(capture.current() == replacement && !capture.take(2002).request);
    assert(!replacement->snapshot.applied);
}
int main() { admission_and_operations(); lifecycle(); concurrent_cancel(); }
