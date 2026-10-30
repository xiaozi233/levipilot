#include "mod/WorldEntryModel.h"

#include <atomic>
#include <cassert>
#include <thread>

using namespace probe::detail;

int main() {
    WorldEntryModel entry;
    auto const first = entry.begin(100);
    assert(first && !entry.begin(101));
    assert(!entry.expire(30099));
    auto const timedOut = entry.expire(30100);
    assert(timedOut && timedOut->id == first && !timedOut->ok);
    assert(!entry.expire(40000) && !entry.begin(40000));
    assert(!entry.recover(first, 40000, false));
    assert(!entry.recover(first, 41000, true));
    assert(!entry.recover(first, 41499, true));
    assert(!entry.recover(first, 41500, false)); // interrupted readiness resets grace
    assert(!entry.recover(first, 42000, true));
    assert(entry.recover(first, 42500, true));
    auto const second = entry.begin(43000);
    assert(second > first);
    assert(!entry.settle(first, true, "obsolete"));
    assert(!entry.recover(first, 99999, true));
    assert(entry.snapshot().id == second && entry.snapshot().phase == WorldEntryModel::Phase::Starting);
    auto const joined = entry.settle(second, true, "joined");
    assert(joined && joined->ok);
    assert(!entry.settle(second, false, "cancelled"));
    auto const third = entry.begin(44000, 1);
    assert(entry.expire(44001));
    assert(!entry.settle(third, true, "late_join")); // real join releases quarantine, no duplicate
    assert(entry.snapshot().phase == WorldEntryModel::Phase::Idle);
    auto const fourth = entry.begin(45000);
    assert(entry.settle(fourth, false, "cancelled"));
    assert(!entry.begin(99999)); // cancellation still needs server/menu evidence
    entry.reset();
    auto const fifth = entry.begin(100000);
    assert(fifth > fourth && !entry.settle(fourth, true, "obsolete"));
    entry.reset();

    // The UI join callback and spool deadline may race. Exactly one terminal
    // belongs to the request, including when the real join arrives after timeout.
    for (unsigned i = 0; i != 1000; ++i) {
        auto const id = entry.begin(0, 1);
        std::atomic<unsigned> count{0};
        std::thread timeout([&] { if (entry.expire(1)) ++count; });
        std::thread join([&] { if (entry.settle(id, true, "joined")) ++count; });
        timeout.join();
        join.join();
        assert(count == 1 && entry.snapshot().phase == WorldEntryModel::Phase::Idle);
    }
}
