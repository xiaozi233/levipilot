#include "mod/TaskModel.h"

#include <atomic>
#include <cassert>
#include <cstdlib>
#include <future>
#include <memory>
#include <new>
#include <thread>

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

int main() {
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
        assert(!tasks.completeUse(id, [&] {
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
        assert(tasks.completeUse(id, [] { return TaskOutcome{}; }));
        assert(!tasks.completeUse(id, [] {
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
        assert(tasks.completeUse(id, []() -> TaskOutcome { throw 42; }));
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
