#include "mod/CallbackLifecycleModel.h"
#include <cassert>
#include <cstdlib>
#include <latch>
#include <new>
#include <vector>

using namespace probe::detail;
using namespace std::chrono_literals;

std::atomic<unsigned> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto ptr = std::malloc(size)) return ptr;
    throw std::bad_alloc();
}
void operator delete(void* ptr) noexcept { std::free(ptr); }
void operator delete(void* ptr, std::size_t) noexcept { std::free(ptr); }

int main() {
    CallbackLifecycle model;
    auto const before = allocations.load();
    for (unsigned i = 0; i < kCallbackDomains; ++i) {
        auto const domain = static_cast<CallbackDomain>(i);
        { CallbackScope scope(model, domain); assert(scope.admitted()); assert(model.counts(domain).running == 1); }
        assert(model.counts(domain).running == 0);
    }
    assert(allocations.load() == before); // Hot entry/exit and snapshots never allocate.
    int calls = 0;
    auto queued = trackedCallback(model, CallbackDomain::Client, [&] { ++calls; });
    auto copy = queued;
    assert(model.counts(CallbackDomain::Client).queued == 1);
    assert(model.close());
    assert(!trackedCallback(model, CallbackDomain::Server, [] {}));
    assert(!model.wait(2ms, [] { return true; })); // Not started, still owned.
    (*queued)();
    queued.reset();
    assert(calls == 1 && !model.idle()); // Executor retained another copy.
    copy.reset();
    assert(model.wait(20ms, [] { return true; }));
    model.reopen();

    // Captures may themselves run module code during destruction. Their last
    // destructor must execute while the ownership ticket is still outstanding.
    bool captureDestroyed = false;
    auto capture = std::shared_ptr<int>(new int(1), [&](int* value) {
        assert(model.counts(CallbackDomain::Client).queued == 1);
        captureDestroyed = true;
        delete value;
    });
    auto retained = trackedCallback(model, CallbackDomain::Client, [capture] {});
    capture.reset();
    assert(model.close());
    retained.reset();
    assert(captureDestroyed && model.idle());
    model.reopen();

    std::latch entered(1), release(1);
    auto running = trackedCallback(model, CallbackDomain::Server, [&] {
        assert(!model.close()); // Reentrant unload never waits on itself.
        entered.count_down(); release.wait();
    });
    std::thread worker([task = std::move(*running)] { task(); });
    running.reset();
    entered.wait();
    assert(model.close());
    assert(model.counts(CallbackDomain::Server).running == 1);
    assert(!model.wait(2ms, [] { return true; }));
    model.reopen(); // Timeout rollback: original admitted callback continues.
    assert(model.reserve(CallbackDomain::Client));
    release.count_down(); worker.join();
    assert(model.idle());

    // A publish snapshot can own a listener before it starts. Removing the
    // EventBus registration is insufficient until that external ref retires.
    auto listener = std::make_shared<int>(0);
    auto publishSnapshot = listener;
    assert(model.close());
    assert(!model.wait(2ms, [&] { return listener.use_count() == 1; }));
    publishSnapshot.reset();
    assert(model.wait(20ms, [&] { return listener.use_count() == 1; }));
    model.reopen();

    std::vector<std::thread> threads;
    std::atomic<unsigned> completed{0};
    for (unsigned t = 0; t < 12; ++t) threads.emplace_back([&] {
        for (unsigned n = 0; n < 10000; ++n) {
            auto task = trackedCallback(model, CallbackDomain::Delay, [&] { ++completed; });
            assert(task); (*task)();
            CallbackScope hot(model, CallbackDomain::Diagnostic);
        }
    });
    for (auto& thread : threads) thread.join();
    assert(completed == 120000 && model.idle());
    auto n = model.counts(CallbackDomain::Delay);
    assert(n.entered == n.completed && n.completed == 120001);
    assert(n.queued == 0 && n.running == 0);

    // Closing concurrent with reservation must account for every winning CAS.
    std::atomic<bool> start{false};
    std::thread producer([&] {
        while (!start.load()) {}
        for (unsigned i = 0; i < 50000; ++i) {
            auto task = trackedCallback(model, CallbackDomain::Client, [] {});
            if (task) (*task)();
        }
    });
    start = true;
    assert(model.close());
    producer.join();
    assert(model.wait(100ms, [] { return true; }));
    model.reopen();
    auto throwing = trackedCallback(model, CallbackDomain::Event, [] { throw 1; });
    try { (*throwing)(); assert(false); } catch (int) {}
    throwing.reset();
    assert(model.idle());
}
