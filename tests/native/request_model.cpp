#include "mod/RequestModel.h"

#include <cassert>
#include <future>
#include <stdexcept>
#include <thread>

struct Request {
    std::array<std::uint64_t, 16> values{};
    bool enabled = true; // Also exercise a non-word-aligned payload/default.
};
using Mailbox = probe::detail::RequestMailbox<Request>;

void transaction() {
    Mailbox mailbox;
    Request last;
    assert(mailbox.read(last) && last.enabled);
    mailbox.update([](auto& request) { request.values.fill(7); });
    try {
        mailbox.update([](auto& request) {
            request.values.fill(99);
            throw std::runtime_error("invalid input");
        });
        assert(false);
    } catch (std::runtime_error const&) {}
    assert(mailbox.read(last) && last.values[0] == 7);
    mailbox.update([](auto& request) { request.values.fill(42); });
    assert(mailbox.read(last) && last.values[15] == 42);

    std::promise<void> entered, release;
    auto ready = release.get_future();
    auto writer = std::async(std::launch::async, [&] {
        mailbox.update([&](auto& request) {
            request.values.fill(100);
            entered.set_value();
            ready.wait();
        });
    });
    entered.get_future().wait();
    // A slow/throwing update cannot block the reader or expose uncommitted data.
    assert(mailbox.read(last) && last.values[0] == 42);
    release.set_value();
    writer.get();
    assert(mailbox.read(last) && last.values[0] == 100);
}

void concurrentSnapshots() {
    Mailbox mailbox;
    std::atomic<int> done{0};
    auto write = [&] {
        for (int i = 0; i < 20000; ++i) {
            mailbox.update([](auto& request) { request.values.fill(request.values[0] + 1); });
        }
        ++done;
    };
    std::thread first(write), second(write);
    Request last;
    while (done.load() != 2) {
        auto const before = last;
        if (!mailbox.read(last)) assert(last.values == before.values);
        for (auto value : last.values) assert(value == last.values[0]);
        assert(last.enabled);
    }
    first.join();
    second.join();
    assert(mailbox.read(last) && last.values[0] == 40000);
}

int main() {
    transaction();
    concurrentSnapshots();
}
