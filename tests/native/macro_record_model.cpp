#include "mod/MacroRecordModel.h"

#include <array>
#include <cassert>
#include <cstdlib>
#include <limits>
#include <new>

std::atomic<unsigned> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto* value = std::malloc(size)) return value;
    throw std::bad_alloc();
}
void operator delete(void* value) noexcept { std::free(value); }
void operator delete(void* value, std::size_t) noexcept { std::free(value); }

using namespace probe::detail;

int main() {
    constexpr std::array<std::array<std::uint64_t, 2>, 6> cases{{
        {0, 200000}, {1, 1}, {3, 3}, {200000, 200000}, {200001, 200000},
        {std::numeric_limits<std::uint64_t>::max(), 200000}
    }};
    for (auto const& [requested, expected] : cases) {
        std::vector<std::uint64_t> rows;
        auto const limit = macroRecordingLimit(requested);
        assert(limit == expected);
        rows.reserve(static_cast<std::size_t>(limit));
        std::atomic_bool active{true};
        unsigned terminals = 0;
        auto const baseline = allocations.load();
        for (std::uint64_t tick = 0; tick < limit + 3; ++tick) {
            bool const stopped = appendMacroRecording(rows, tick, requested, active);
            assert(stopped == (tick == limit - 1));
            terminals += stopped;
        }
        assert(!active && terminals == 1 && rows.size() == limit);
        assert(rows.front() == 0 && rows.back() == limit - 1);
        assert(allocations.load() == baseline);
        // Explicit stop is also final: no row or duplicate limit event.
        rows.clear();
        assert(!appendMacroRecording(rows, std::uint64_t{99}, requested, active));
        assert(rows.empty());
    }
    // Close an already full/oversized buffer without appending or losing data.
    std::vector<int> existing{1, 2, 3, 4};
    std::atomic_bool active{true};
    assert(appendMacroRecording(existing, 5, 3, active));
    assert(existing.size() == 4 && existing.back() == 4 && !active);
    assert(!appendMacroRecording(existing, 6, 3, active));
}
