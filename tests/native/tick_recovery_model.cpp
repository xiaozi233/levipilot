#include "mod/TickRecoveryModel.h"
#include "mod/RequestModel.h"
#include "mod/MacroPlaybackModel.h"

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

void lifecycle() {
    TickRecovery recovery;
    assert(!recovery.active());
    auto const before = allocations.load();
    recovery.fail(TickPhase::Pre, "failure", 17);
    auto const first = recovery.snapshot();
    assert(first.id == 1 && first.phase == TickRecovery::Phase::Pending);
    assert(!recovery.finish(first.id, true, true));
    assert(recovery.prepare(first.id));
    assert(!recovery.finish(first.id, false, true)); // Uncertain release.
    assert(!recovery.finish(first.id, true, false)); // Busy mailbox: no fallback.
    recovery.fail(TickPhase::Post, nullptr, 18);
    assert(!recovery.prepare(first.id));
    assert(!recovery.finish(first.id, true, true)); // Old cleanup cannot resume.
    auto const second = recovery.snapshot();
    assert(second.id == 2 && recovery.prepare(second.id));
    assert(recovery.finish(second.id, true, true));
    assert(!recovery.active() && !recovery.finish(second.id, true, true));
    TickFailure row;
    assert(recovery.pop(row) && row.id == 1 && row.tick == 17);
    assert(row.message.view() == "failure" && row.phase == TickPhase::Pre && !row.truncated);
    assert(recovery.pop(row) && row.id == 2 && row.message.view() == "unknown exception");
    assert(!recovery.pop(row) && !recovery.takeLost());
    assert(allocations.load() == before);
}

void diagnostics() {
    TickRecovery recovery;
    char message[300]{};
    std::fill_n(message, 254, 'x');
    std::copy_n("你", 3, message + 254);
    std::fill_n(message + 257, 42, 'y');
    auto const before = allocations.load();
    for (unsigned i = 0; i < 100; ++i) recovery.fail(TickPhase::Pre, message, i);
    // Even the 68 lost diagnostics advance recovery, independently of rows.
    assert(recovery.snapshot().id == 100 && recovery.active());
    assert(!recovery.prepare(32));
    assert(recovery.takeLost() == 68 && recovery.takeLost() == 0);
    TickFailure row;
    for (unsigned i = 0; i < 32; ++i) {
        assert(recovery.pop(row) && row.id == i + 1 && row.truncated);
        assert(row.message.view().size() == 254); // Never split UTF-8.
    }
    assert(!recovery.pop(row));
    assert(recovery.prepare(100) && recovery.finish(100, true, true));
    assert(allocations.load() == before);
}

void concurrent() {
    TickRecovery recovery;
    constexpr unsigned count = 100000;
    std::atomic_bool done{false};
    std::uint64_t received = 0, lost = 0, last = 0;
    std::thread consumer([&] {
        TickFailure row;
        while (!done.load() || recovery.active()) {
            auto const state = recovery.snapshot();
            if (state.phase == TickRecovery::Phase::Pending) recovery.prepare(state.id);
            while (recovery.pop(row)) {
                assert(row.id > last && row.message.view() == "concurrent");
                last = row.id;
                ++received;
            }
            lost += recovery.takeLost();
        }
        while (recovery.pop(row)) ++received;
        lost += recovery.takeLost();
    });
    auto const before = allocations.load();
    for (unsigned i = 0; i < count; ++i) {
        recovery.fail(i & 1 ? TickPhase::Pre : TickPhase::Post, "concurrent", i);
        auto const id = recovery.snapshot().id;
        while (!recovery.finish(id, true, true)) std::this_thread::yield();
    }
    assert(allocations.load() == before);
    done = true;
    consumer.join();
    assert(received + lost == count && recovery.snapshot().id == count && !recovery.active());
}

void requestBarrier() {
    struct Controls { unsigned inputSeq = 0, lookSeq = 0; bool moving = false, turning = false; };
    RequestMailbox<Controls> mailbox;
    TickRecovery recovery;
    mailbox.update([](auto& r) { ++r.inputSeq; ++r.lookSeq; r.moving = r.turning = true; });
    Controls fallback;
    assert(mailbox.read(fallback) && fallback.moving && fallback.turning);
    recovery.fail(TickPhase::Pre, "stop", 1);
    // Cold cleanup publishes a replacement; the old fallback is unusable.
    mailbox.update([](auto& r) { ++r.inputSeq; ++r.lookSeq; r.moving = r.turning = false; });
    assert(recovery.prepare(1));
    assert(!recovery.finish(1, true, false) && fallback.moving);
    assert(mailbox.read(fallback));
    assert(!fallback.moving && !fallback.turning && fallback.inputSeq == 2 && fallback.lookSeq == 2);
    assert(recovery.finish(1, true, true));
    assert(blockedDuringTickRecovery("move") && blockedDuringTickRecovery("macro_run"));
    assert(blockedDuringTickRecovery("navigate") && blockedDuringTickRecovery("key_event"));
    assert(!blockedDuringTickRecovery("status") && !blockedDuringTickRecovery("state"));
    assert(!blockedDuringTickRecovery("input_lock") && !blockedDuringTickRecovery("stopmove"));
    assert(!blockedDuringTickRecovery("unknown_command"));
    assert(!macroSucceeded(MacroEnd::InternalError));
}

int main() {
    lifecycle();
    diagnostics();
    concurrent();
    requestBarrier();
}
