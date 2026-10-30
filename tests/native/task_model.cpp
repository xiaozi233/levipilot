#include "mod/TaskModel.h"

#include <atomic>
#include <cassert>
#include <chrono>
#include <cstdlib>
#include <future>
#include <memory>
#include <new>
#include <thread>
#include <vector>

std::atomic<int> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto p = std::malloc(size))
        return p;
    throw std::bad_alloc();
}
void operator delete(void* p) noexcept {
    std::free(p);
}
void operator delete(void* p, std::size_t) noexcept {
    std::free(p);
}

using namespace probe;
using namespace probe::detail;

std::uint64_t zeroClock() {
    return 0;
}

void dispatchUse(TaskController& tasks, std::uint64_t now) {
    assert(tasks.tick(
        [](TaskState& state, std::uint64_t) {
            std::get<UseTask>(state).dispatched = true;
            TaskStep step;
            step.useReady = true;
            return step;
        },
        [=] { return now; }));
    TaskEvent event;
    assert(tasks.pop(event) && event.kind == TaskEvent::Kind::UseReady);
    assert(!tasks.pop(event));
}

void expectUseFailure(TaskController& tasks, std::uint64_t id, std::string_view reason) {
    TaskEvent event;
    assert(tasks.pop(event) && event.id == id && event.kind == TaskEvent::Kind::Completed);
    assert(!event.outcome.ok && event.outcome.reason == reason);
    assert(tasks.snapshot().id == 0 && !tasks.pop(event));
}

void useTimeouts() {
    auto owner = std::make_unique<TaskController>();
    auto& tasks = *owner;
    auto id = tasks.start(UseTask{});
    assert(!tasks.expireUse(100000)); // Waiting ticks do not spend the server budget.
    dispatchUse(tasks, 1000);
    assert(std::get<UseTask>(tasks.snapshot().state).serverDeadlineMs == 6000);
    assert(!tasks.expireUse(5999));
    assert(tasks.expireUse(6000)); // Inclusive deadline, independent of game ticks.
    assert(!tasks.expireUse(6001));
    expectUseFailure(tasks, id, "server_timeout");
    bool effect = false;
    auto complete = [&] {
        effect = true;
        TaskOutcome result;
        result.ok = true;
        return result;
    };
    assert(!tasks.completeUse(id, [] { return 6001; }, complete) && !effect);
    assert(!tasks.failUse(id, "server_task_failed"));

    id = tasks.start(UseTask{});
    dispatchUse(tasks, 1000);
    assert(!tasks.completeUse(id, [] { return 6000; }, complete) && !effect);
    expectUseFailure(tasks, id, "server_timeout"); // Callback enforces deadline without a poll.

    id = tasks.start(UseTask{});
    dispatchUse(tasks, 1000);
    assert(tasks.completeUse(id, [] { return 5999; }, complete) && effect);
    assert(!tasks.expireUse(6000));
    TaskEvent event;
    assert(tasks.pop(event) && event.id == id && event.outcome.ok);
    assert(!tasks.pop(event));

    id = tasks.start(UseTask{});
    dispatchUse(tasks, 1000);
    assert(tasks.failUse(id, "server_task_queue_failed"));
    assert(!tasks.failUse(id, "server_task_queue_failed"));
    assert(!tasks.completeUse(id, zeroClock, complete));
    expectUseFailure(tasks, id, "server_task_queue_failed");
    auto const next = tasks.start(JumpTask{});
    assert(next > id && !tasks.completeUse(id, zeroClock, complete));
    assert(!tasks.failUse(id, "server_task_failed"));
    assert(!tasks.expireUse(100000) && tasks.snapshot().id == next);
    tasks.cancel("stopped");
    assert(tasks.pop(event) && event.id == next && !tasks.pop(event));

    // An expiry check must sample the clock after waiting for the task lock.
    id = tasks.start(UseTask{});
    dispatchUse(tasks, 1000);
    std::promise<void> entered, release, attempted;
    auto gate = release.get_future();
    std::thread ticker([&] {
        tasks.tick([&](TaskState&, std::uint64_t) {
            entered.set_value();
            gate.wait();
            return TaskStep{};
        });
    });
    entered.get_future().wait();
    std::promise<void> sampled;
    auto clockSample = sampled.get_future();
    std::atomic<std::uint64_t> now{5999};
    effect = false;
    std::thread callback([&] {
        attempted.set_value();
        assert(!tasks.completeUse(
            id,
            [&] {
                sampled.set_value();
                return now.load();
            },
            complete));
    });
    attempted.get_future().wait();
    assert(clockSample.wait_for(std::chrono::milliseconds(20)) == std::future_status::timeout);
    now.store(6000);
    release.set_value();
    ticker.join();
    callback.join();
    assert(!effect);
    expectUseFailure(tasks, id, "server_timeout");

    // Expiry, completion, cancellation, replacement and queue failure contend
    // for the same owner. Every ordering must publish just one terminal.
    for (int iteration = 0; iteration < 150; ++iteration) {
        id = tasks.start(UseTask{});
        dispatchUse(tasks, 1000);
        std::promise<void> start;
        auto go = start.get_future().share();
        std::thread expiry([&] {
            go.wait();
            tasks.expireUse(6000);
        });
        std::thread finish([&] {
            go.wait();
            tasks.completeUse(id, [] { return 6000; }, complete);
        });
        std::thread cancel([&] {
            go.wait();
            tasks.cancel("stopped");
        });
        std::thread replace([&] {
            go.wait();
            assert(tasks.start(JumpTask{}));
        });
        std::thread failure([&] {
            go.wait();
            tasks.failUse(id, "server_task_failed");
        });
        start.set_value();
        expiry.join();
        finish.join();
        cancel.join();
        replace.join();
        failure.join();
        tasks.cancel("stopped");
        unsigned terminals = 0, replacements = 0;
        while (tasks.pop(event)) {
            assert(event.kind == TaskEvent::Kind::Completed ||
                   event.kind == TaskEvent::Kind::Cancelled);
            if (event.id == id)
                ++terminals;
            else
                ++replacements;
        }
        assert(terminals == 1 && replacements == 1 && !effect);
    }

    auto const baseline = allocations.load();
    id = tasks.start(UseTask{});
    dispatchUse(tasks, 0);
    assert(tasks.expireUse(UseTask::ServerTimeoutMs));
    expectUseFailure(tasks, id, "server_timeout");
    assert(allocations.load() == baseline);
}

void stressLifecycle() {
    constexpr std::size_t cycles = 2500;
    auto owner = std::make_unique<TaskController>();
    auto& tasks = *owner;
    std::atomic_bool produced{false}, stopConsumer{false};
    std::vector<TaskEvent> events;
    std::array<unsigned, cycles + 1> effects{};
    std::thread consumer([&] {
        TaskEvent event;
        for (;;) {
            if (tasks.pop(event)) events.push_back(event);
            else if (stopConsumer.load()) break;
            else std::this_thread::yield();
        }
    });
    std::thread ticker([&] {
        while (!produced.load()) {
            tasks.tick([](TaskState& state, std::uint64_t) {
                TaskStep step;
                if (auto* use = std::get_if<UseTask>(&state)) {
                    if (!use->dispatched) {
                        use->dispatched = true;
                        step.useReady = true;
                    }
                } else {
                    step.finished = true;
                }
                return step;
            });
            auto const id = tasks.snapshot().id;
            if (id) {
                tasks.completeUse(id, zeroClock, [&] {
                    ++effects[id];
                    return TaskOutcome{};
                });
                if (id > 1)
                    tasks.completeUse(id - 1, zeroClock, [] {
                        assert(false && "stale server callback executed");
                        return TaskOutcome{};
                    });
            }
        }
    });
    TaskState const types[] = {NavigateTask{}, JumpTask{}, RouteTask{}, MineTask{}, PlaceTask{}, UseTask{}};
    for (std::size_t i = 1; i <= cycles; ++i) {
        std::uint64_t id = 0;
        while (!(id = tasks.start(types[i % 6]))) std::this_thread::yield();
        assert(id == i);
        if (i % 7 == 0) tasks.cancel("world_left");
        else if (i % 11 == 0) tasks.cancel("stopped");
    }
    produced.store(true);
    ticker.join();
    tasks.cancel("world_left");
    stopConsumer.store(true);
    consumer.join();
    std::array<unsigned, cycles + 1> terminals{};
    for (auto const& event : events) {
        assert(event.id > 0 && event.id <= cycles);
        if (event.kind == TaskEvent::Kind::Completed || event.kind == TaskEvent::Kind::Cancelled) {
            assert(++terminals[event.id] == 1);
            if (effects[event.id]) assert(event.kind == TaskEvent::Kind::Completed);
        }
    }
    for (std::size_t id = 1; id <= cycles; ++id) {
        assert(terminals[id] == 1 && effects[id] <= 1);
    }
    assert(tasks.snapshot().id == 0);
}

int main() {
    useTimeouts();
    stressLifecycle();
    {
        FixedText<5> text;
        text.assign("\xE4\xB8\xAD\xE6\x96\x87");
        assert(text.view() == "\xE4\xB8\xAD");
        text.assign("ab");
        assert(text.view() == "ab");
    }
    TaskState const types[] = {
        NavigateTask{}, JumpTask{}, RouteTask{}, MineTask{}, PlaceTask{}, UseTask{}};
    // Every ordered pair replaces the old task; no hidden task can resume.
    for (auto const& before : types)
        for (auto const& after : types) {
            auto owner = std::make_unique<TaskController>();
            auto& tasks = *owner;
            auto const first = tasks.start(before);
            auto const second = tasks.start(after);
            assert(first && second > first);
            assert(tasks.snapshot().state.index() == after.index());
            TaskEvent event;
            assert(tasks.pop(event) && event.id == first &&
                   event.kind == TaskEvent::Kind::Cancelled);
            assert(std::string_view(event.outcome.reason) == "replaced");
            tasks.tick([](TaskState&, std::uint64_t) {
                TaskStep s;
                s.finished = true;
                return s;
            });
            assert(tasks.pop(event) && event.id == second &&
                   event.kind == TaskEvent::Kind::Completed);
            assert(!tasks.tick([](TaskState&, std::uint64_t) { return TaskStep{}; }));
            assert(!tasks.pop(event));
        }
    {
        auto owner = std::make_unique<TaskController>();
        auto& tasks = *owner;
        auto const id = tasks.start(UseTask{});
        tasks.tick([](TaskState& state, std::uint64_t) {
            std::get<UseTask>(state).dispatched = true;
            TaskStep s;
            s.useReady = true;
            return s;
        });
        assert(tasks.snapshot().id == id); // Pending server work still owns the task.
        tasks.cancel("stopped");
        bool executed = false;
        assert(!tasks.completeUse(id, zeroClock, [&] {
            executed = true;
            return TaskOutcome{};
        }));
        assert(!executed);
    }
    {
        auto owner = std::make_unique<TaskController>();
        auto& tasks = *owner;
        auto const id = tasks.start(UseTask{});
        tasks.tick([](TaskState& state, std::uint64_t) {
            std::get<UseTask>(state).dispatched = true;
            TaskStep s;
            s.useReady = true;
            return s;
        });
        assert(tasks.completeUse(id, zeroClock, [] { return TaskOutcome{}; }));
        assert(!tasks.completeUse(id, zeroClock, [] {
            assert(false);
            return TaskOutcome{};
        }));
        assert(tasks.snapshot().id == 0);
    }
    {
        auto owner = std::make_unique<TaskController>();
        auto& tasks = *owner;
        auto id = tasks.start(UseTask{});
        tasks.tick([](TaskState& state, std::uint64_t) {
            std::get<UseTask>(state).dispatched = true;
            TaskStep step;
            step.useReady = true;
            return step;
        });
        assert(tasks.completeUse(id, zeroClock, []() -> TaskOutcome { throw 42; }));
        TaskEvent event;
        assert(tasks.pop(event) && event.kind == TaskEvent::Kind::UseReady);
        assert(tasks.pop(event) && event.id == id && !event.outcome.ok);
        assert(std::string_view(event.outcome.reason) == "server_task_failed");
        assert(tasks.snapshot().id == 0 && !tasks.pop(event));
    }
    {
        auto owner = std::make_unique<TaskController>();
        auto& tasks = *owner;
        std::uint64_t accepted = 0;
        while (tasks.start(JumpTask{}))
            ++accepted;
        auto const active = tasks.snapshot().id;
        bool prepared = false;
        assert(tasks.start(PlaceTask{}, [&] { prepared = true; }) == 0);
        assert(!prepared && tasks.snapshot().id == active);
        bool advanced = false;
        assert(tasks.tick([&](TaskState&, std::uint64_t) {
            advanced = true;
            return TaskStep{};
        }));
        assert(!advanced); // Backpressure pauses tick side effects too.
        tasks.cancel("stopped"); // Reserved capacity guarantees its terminal event.
        TaskEvent event;
        std::size_t events = 0;
        while (tasks.pop(event))
            ++events;
        assert(events == accepted);
        assert(tasks.start(NavigateTask{}) > active); // Draining relieves backpressure.
    }
    {
        auto owner = std::make_unique<TaskController>();
        auto& tasks = *owner;
        tasks.start(JumpTask{});
        std::promise<void> entered, release;
        auto gate = release.get_future();
        std::thread tick([&] {
            tasks.tick([&](TaskState&, std::uint64_t) {
                entered.set_value();
                gate.wait();
                TaskStep s;
                s.finished = true;
                return s;
            });
        });
        entered.get_future().wait();
        std::thread cancel([&] { tasks.cancel("stopped"); });
        release.set_value();
        tick.join();
        cancel.join();
        TaskEvent event;
        assert(tasks.pop(event) && event.kind == TaskEvent::Kind::Completed);
        assert(!tasks.pop(event)); // Exactly one terminal even across cancellation.
    }
    {
        auto owner = std::make_unique<TaskController>();
        auto& tasks = *owner;
        int const baseline = allocations.load();
        for (int i = 0; i < 1000; ++i) {
            tasks.start(RouteTask{});
            tasks.tick([](TaskState&, std::uint64_t) { return TaskStep{}; });
            tasks.cancel("stopped");
            TaskEvent event;
            while (tasks.pop(event)) {
            }
        }
        assert(allocations.load() == baseline);
    }
}
