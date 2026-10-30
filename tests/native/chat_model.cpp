#include "mod/ChatModel.h"

#include <cassert>
#include <future>
#include <stdexcept>
#include <string_view>
#include <thread>

using Chat = probe::detail::ChatCapture;

void ordinaryRequest() {
    Chat chat;
    auto request = chat.begin(100);
    assert(request && !chat.begin(101));
    assert(!chat.take(102).request);
    request->run(103, [](auto& snapshot) {
        snapshot.count = snapshot.total = 1;
        snapshot.lines[0].text.assign("retained message");
    });
    // A duplicated callback cannot write a snapshot already published.
    request->run(104, [](auto&) { assert(false); });
    auto done = chat.take(105);
    assert(done.request == request && !done.error);
    assert(done.request->snapshot.lines[0].text.view() == "retained message");
    assert(!chat.take(106).request);
    assert(chat.begin(107));
}

void queuedTimeoutAndCancel() {
    Chat chat;
    auto old = chat.begin(0);
    assert(!chat.take(Chat::TimeoutMs - 1).request);
    auto expired = chat.take(Chat::TimeoutMs);
    assert(expired.request == old);
    assert(std::string_view(expired.error) == "chat_history_timeout");
    auto next = chat.begin(Chat::TimeoutMs + 1);
    assert(next);
    old->run(Chat::TimeoutMs + 2, [](auto&) { assert(false); });
    chat.cancel("world_left");
    auto cancelled = chat.take(Chat::TimeoutMs + 3);
    assert(cancelled.request == next && std::string_view(cancelled.error) == "world_left");
    next->run(Chat::TimeoutMs + 4, [](auto&) { assert(false); });
    assert(!chat.take(Chat::TimeoutMs + 5).request);

    // Timeout is also checked at callback entry if the consumer was delayed.
    auto late = chat.begin(5000);
    late->run(5000 + Chat::TimeoutMs, [](auto&) { assert(false); });
    assert(std::string_view(chat.take(8000).error) == "chat_history_timeout");
}

void activeTimeoutDoesNotBlockNewRequest() {
    Chat chat;
    auto old = chat.begin(0);
    std::promise<void> entered, resume;
    auto released = resume.get_future();
    std::thread capture([&] {
        old->run(1, [&](auto& snapshot) {
            entered.set_value();
            released.wait();
            snapshot.count = 1;
            snapshot.lines[0].text.assign("stale");
        });
    });
    entered.get_future().wait();
    auto timeout = chat.take(Chat::TimeoutMs);
    assert(timeout.request == old && timeout.error);
    auto fresh = chat.begin(Chat::TimeoutMs + 1);
    fresh->run(Chat::TimeoutMs + 2, [](auto& snapshot) {
        snapshot.count = 1;
        snapshot.lines[0].text.assign("fresh");
    });
    resume.set_value();
    capture.join();
    auto done = chat.take(Chat::TimeoutMs + 3);
    assert(done.request == fresh && !done.error);
    assert(done.request->snapshot.lines[0].text.view() == "fresh");
    assert(!chat.take(Chat::TimeoutMs + 4).request);
}

void failureAndRestart() {
    Chat chat;
    auto request = chat.begin(1);
    request->run(2, [](auto&) { throw std::runtime_error("capture failed"); });
    assert(std::string_view(chat.take(3).error) == "chat_history_failed");
    auto cancelled = chat.begin(4);
    chat.reset(); // Disconnect/unload; discard old work without a stale reply.
    assert(!chat.take(5).request);
    auto fresh = chat.begin(6);
    cancelled->run(7, [](auto&) { assert(false); });
    fresh->run(8, [](auto& snapshot) { snapshot.total = 42; });
    auto done = chat.take(9);
    assert(done.request == fresh && done.request->snapshot.total == 42);

    // A callback still queued at timeout must not retain its large snapshot.
    std::weak_ptr<Chat::Request> queued = chat.begin(10);
    assert(!queued.expired());
    assert(chat.take(10 + Chat::TimeoutMs).error);
    assert(queued.expired());
}

void concurrentCompletionAndCancel() {
    Chat chat;
    for (int i = 0; i < 500; ++i) {
        auto request = chat.begin(0);
        std::thread capture([request] {
            request->run(1, [](auto& snapshot) {
                for (auto& line : snapshot.lines) line.text.assign("complete UTF-8 snapshot");
                snapshot.count = snapshot.lines.size();
            });
        });
        std::thread cancel([&] { chat.cancel("world_left"); });
        auto done = chat.take(Chat::TimeoutMs);
        capture.join();
        cancel.join();
        assert(done.request == request);
        if (!done.error) {
            assert(done.request->snapshot.count == 64);
            for (auto& line : done.request->snapshot.lines)
                assert(line.text.view() == "complete UTF-8 snapshot");
        }
        assert(!chat.take(Chat::TimeoutMs + 1).request);
    }
}

void concurrentFailurePublication() {
    Chat chat;
    for (int i = 0; i < 500; ++i) {
        auto request = chat.begin(0);
        std::thread capture([request] {
            request->run(1, [](auto&) { throw std::runtime_error("capture failed"); });
        });
        auto done = chat.take(Chat::TimeoutMs);
        capture.join();
        assert(done.request == request && done.error);
        assert(std::string_view(done.error) == "chat_history_timeout"
            || std::string_view(done.error) == "chat_history_failed");
    }
}

int main() {
    ordinaryRequest();
    queuedTimeoutAndCancel();
    activeTimeoutDoesNotBlockNewRequest();
    failureAndRestart();
    concurrentCompletionAndCancel();
    concurrentFailurePublication();
}
