#include "mod/RecordModel.h"

#include <cassert>
#include <cstdlib>
#include <future>
#include <stdexcept>

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

struct Entry {
    std::array<std::uint64_t, 8> words{};
};
using Buffer = probe::detail::RecordBuffer<Entry, 8>;

Entry sample(std::uint64_t value) {
    Entry entry;
    entry.words.fill(value);
    return entry;
}

void lifecycle() {
    Buffer records;
    Entry entry;
    assert(!records.capture() && !records.read(0, entry));
    assert(records.start(3, [] {}));
    auto const before = allocations.load();
    for (int i = 0; i < 10; ++i) {
        auto capture = records.capture();
        assert(capture);
        if (capture.sampleDue())
            capture.publish(sample(i));
    }
    assert(allocations.load() == before);
    records.stop();
    assert(!records.capture() && !records.active());
    assert(records.head() == 4 && records.period() == 3);
    for (std::uint64_t i = 0; i < 4; ++i) {
        assert(records.read(i, entry));
        assert(entry.words[0] == i * 3);
    }
    assert(records.start(0, [] {}));
    assert(records.head() == 0 && records.period() == 1);
    try {
        auto capture = records.capture();
        assert(capture);
        throw std::runtime_error("capture failed");
    } catch (std::runtime_error const&) {
    }
    assert(records.capture()); // Unwinding released the capture.
    assert(records.start(999, [] {}));
    assert(records.period() == 200);
    try {
        records.start(1, [] { throw std::runtime_error("initialize failed"); });
        assert(false);
    } catch (std::runtime_error const&) {
    }
    assert(!records.active());
    assert(records.start(1, [] {}));
}

void resetWaitsForCapture() {
    Buffer records;
    assert(records.start(1, [] {}));
    std::future<bool> reset;
    {
        auto capture = records.capture();
        assert(capture);
        reset = std::async(std::launch::async, [&] { return records.start(2, [] {}); });
        while (records.active())
            std::this_thread::yield();
        assert(reset.wait_for(std::chrono::milliseconds(0)) != std::future_status::ready);
        capture.publish(sample(999));
    }
    assert(reset.get());
    assert(records.head() == 0 && records.period() == 2);
    {
        auto capture = records.capture();
        assert(capture.sampleDue());
        capture.publish(sample(1000));
    }
    Entry entry;
    assert(records.head() == 1 && records.read(0, entry));
    assert(entry.words[0] == 1000);
}

void wraparound() {
    Buffer records;
    assert(records.start(1, [] {}));
    for (std::uint64_t i = 0; i < 32; ++i) {
        auto capture = records.capture();
        capture.publish(sample(i));
    }
    Entry entry;
    assert(!records.read(0, entry));
    for (std::uint64_t i = 24; i < 32; ++i) {
        assert(records.read(i, entry) && entry.words[0] == i);
    }
    assert(records.start(1, [] {}));
    std::atomic_bool done{false};
    std::atomic<unsigned> checked{0};
    std::thread reader([&] {
        do {
            auto const head = records.head();
            auto const begin = head > 8 ? head - 8 : 0;
            for (auto i = begin; i < head; ++i) {
                Entry row;
                if (records.read(i, row)) {
                    for (auto value : row.words)
                        assert(value == i);
                    ++checked;
                }
            }
        } while (!done.load());
    });
    for (std::uint64_t i = 0; i < 100000; ++i) {
        auto capture = records.capture();
        capture.publish(sample(i));
        if (i % 128 == 0)
            std::this_thread::yield();
    }
    done = true;
    reader.join();
    assert(checked > 0 && records.head() == 100000);
}

void repeatedRestarts() {
    Buffer records;
    std::atomic_bool done{false};
    std::uint64_t generation = 0; // Published by start's capture admission.
    assert(records.start(1, [] {}));
    std::thread ticker([&] {
        while (!done.load()) {
            auto capture = records.capture();
            if (capture) {
                if (capture.sampleDue())
                    capture.publish(sample(generation));
            } else {
                std::this_thread::yield();
            }
        }
    });
    for (std::uint64_t session = 1; session <= 2500; ++session) {
        assert(records.start(1, [&] { generation = session; }));
        while (records.head() == 0)
            std::this_thread::yield();
        records.stop();
        auto const head = records.head();
        for (auto i = head > 8 ? head - 8 : 0; i < head; ++i) {
            Entry entry;
            assert(records.read(i, entry));
            for (auto value : entry.words)
                assert(value == session);
        }
    }
    done = true;
    ticker.join();
}

int main() {
    lifecycle();
    resetWaitsForCapture();
    wraparound();
    repeatedRestarts();
}
