#include "mod/InputSessionModel.h"

#include <atomic>
#include <cassert>
#include <thread>

struct Controls {};
struct Bindings {};
using Session = probe::detail::InputSession<Controls, Bindings>;
using probe::detail::InputLifecycleSnapshot;
using probe::detail::InputLifecycleStore;

int main() {
    Session session;
    InputLifecycleSnapshot out;
    assert(!session.lifecycle.read(out, 7));
    session.lifecycleCurrent = true;
    session.seenInputSeq = 1;
    session.seenJumpSeq = 2;
    session.seenLookSeq = 3;
    session.activeLookTicks = -1;
    session.publishLifecycle(7);
    assert(session.lifecycle.read(out, 7) && out.active());
    assert(!out.pending(1, 2, 3));
    assert(out.pending(2, 2, 3) && out.pending(1, 3, 3) && out.pending(1, 2, 4));

    // Finite requests retain their mailbox sequence and original duration.
    // Only the tick-owned remaining duration decides whether they are done.
    session.activeLookTicks = 0;
    session.publishLifecycle(7);
    assert(session.lifecycle.read(out, 7) && !out.active() && !out.pending(1, 2, 3));
    session.activeInputTicks = -1;
    session.publishLifecycle(7);
    assert(session.lifecycle.read(out, 7) && out.active());
    session.activeInputTicks = 0;
    session.jumpRequestPending = true;
    session.publishLifecycle(7);
    assert(session.lifecycle.read(out, 7) && out.active());
    session.jumpRequestPending = false;
    session.heldEvents[87] = probe::detail::InputInjection{};
    session.publishLifecycle(7);
    assert(session.lifecycle.read(out, 7) && out.active() && out.heldEvents == 1);
    assert(!session.lifecycle.read(out, 8)); // A previous world cannot authorize unload.
    session.lifecycleCurrent = false;
    session.publishLifecycle(7);
    assert(!session.lifecycle.read(out, 7));

    InputLifecycleStore store;
    std::atomic_bool done{false};
    std::thread writer([&] {
        for (std::uint64_t n = 1; n <= 200000; ++n) {
            store.publish({9, n, n + 1, n + 2, -std::int64_t(n), std::int64_t(n), n & 1, n % 264, 1});
        }
        done = true;
    });
    unsigned reads = 0;
    do {
        InputLifecycleSnapshot row;
        if (!store.read(row, 9)) continue;
        assert(row.jumpSeq == row.inputSeq + 1 && row.lookSeq == row.inputSeq + 2);
        assert(row.inputTicks == -std::int64_t(row.inputSeq) && row.lookTicks == std::int64_t(row.inputSeq));
        assert(row.jumpPending == (row.inputSeq & 1) && row.heldEvents == row.inputSeq % 264);
        ++reads;
    } while (!done.load());
    writer.join();
    assert(reads > 0 && store.read(out, 9) && out.inputSeq == 200000);
}
