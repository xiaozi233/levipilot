#include "mod/SendProbeModel.h"

#include <atomic>
#include <cassert>
#include <cstdlib>
#include <new>
#include <thread>
#include <vector>

using probe::detail::SendProbeHistory;
using probe::detail::SendProbeRecord;

std::atomic<unsigned> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto ptr = std::malloc(size)) return ptr;
    throw std::bad_alloc();
}
void operator delete(void* ptr) noexcept { std::free(ptr); }
void operator delete(void* ptr, std::size_t) noexcept { std::free(ptr); }

SendProbeRecord record(std::uint32_t writer, std::uint32_t n) {
    auto const id = (std::uint64_t(writer) << 32) | n;
    return {id, ~id, writer, n, ~n};
}
void verify(SendProbeRecord const& row) {
    assert(row.net == ~row.tick);
    assert(row.tick == ((std::uint64_t(row.tid) << 32) | row.before));
    assert(row.after == ~row.before && row.reserved == 0);
}

int main() {
    SendProbeHistory history;
    assert(history.initialize());
    auto const before = allocations.load();
    {
        auto delayed = history.reserve();
        auto next = history.reserve();
        assert(next.publish(record(1, 1)));
        SendProbeRecord row;
        assert(history.read(0, row) == SendProbeHistory::ReadResult::pending);
        assert(history.read(1, row) == SendProbeHistory::ReadResult::ready);
        verify(row);
        assert(delayed.publish(record(1, 0)));
    }
    {
        auto abandoned = history.reserve();
    }
    SendProbeRecord row;
    assert(history.read(2, row) == SendProbeHistory::ReadResult::dropped);
    for (unsigned n = 0; n < 256; ++n) {
        auto slot = history.reserve();
        assert(slot.publish(record(1, n)));
    }
    assert(history.read(0, row) == SendProbeHistory::ReadResult::overwritten);
    assert(allocations.load() == before); // All publication/read operations are allocation-free.

    constexpr unsigned Writers = 4, Rows = 50000;
    std::atomic<unsigned> done{0};
    std::atomic<std::uint64_t> reads{0};
    std::vector<std::thread> workers;
    for (unsigned writer = 1; writer <= Writers; ++writer) {
        workers.emplace_back([&, writer] {
            for (unsigned n = 0; n < Rows; ++n) {
                auto slot = history.reserve();
                slot.publish(record(writer, n));
            }
            ++done;
        });
    }
    for (unsigned reader = 0; reader < 2; ++reader) {
        workers.emplace_back([&] {
            do {
                auto const head = history.head();
                auto const first = head > 64 ? head - 64 : 0;
                for (auto i = first; i < head; ++i) {
                    SendProbeRecord copy;
                    if (history.read(i, copy) == SendProbeHistory::ReadResult::ready) {
                        verify(copy);
                        ++reads;
                    }
                }
            } while (done.load() != Writers);
        });
    }
    for (auto& worker : workers) worker.join();
    assert(reads.load() > 0 && history.head() == 259 + Writers * Rows);
    for (auto i = history.head() - 64; i < history.head(); ++i) {
        assert(history.read(i, row) == SendProbeHistory::ReadResult::ready);
        verify(row);
    }
}
