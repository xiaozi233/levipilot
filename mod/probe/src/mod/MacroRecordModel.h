#pragma once

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <vector>

namespace probe::detail {

inline constexpr std::uint64_t MaxMacroRecordedTicks = 200000;

constexpr std::uint64_t macroRecordingLimit(std::uint64_t requested) {
    return requested == 0 ? MaxMacroRecordedTicks : std::min(requested, MaxMacroRecordedTicks);
}

// Caller holds the recording session lock. Reserve the effective limit before
// opening admission so this append never grows storage on the tick thread.
// Returns true only on the transition to the limit; retained rows can be saved.
template <class Tick>
bool appendMacroRecording(
    std::vector<Tick>& rows, Tick const& row, std::uint64_t requested, std::atomic_bool& active
) {
    if (!active.load(std::memory_order_relaxed)) return false;
    auto const limit = macroRecordingLimit(requested);
    if (rows.size() < limit) rows.push_back(row);
    if (rows.size() < limit) return false;
    active.store(false, std::memory_order_release);
    return true;
}

} // namespace probe::detail
