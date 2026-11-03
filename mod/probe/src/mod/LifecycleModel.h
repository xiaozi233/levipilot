#pragma once

#include <algorithm>
#include <charconv>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <string>
#include <string_view>

namespace probe::detail {

struct LifecycleCounters {
    std::uint64_t generation = 0;
    std::uint64_t disable = 0;
};

inline void observeLifecycleLine(std::string_view line, LifecycleCounters& counters) {
    if (line.find("enable: begin") != std::string_view::npos) ++counters.generation;
    auto const done = line.find("disable: done lifecycle=");
    if (done == std::string_view::npos) return;
    auto const id = line.find("disable_id=", done);
    if (id == std::string_view::npos) return;
    auto const* first = line.data() + id + sizeof("disable_id=") - 1;
    auto const* last = line.data() + line.size();
    std::uint64_t value = 0;
    auto const [end, error] = std::from_chars(first, last, value);
    if (error == std::errc{} && end != first) counters.disable = std::max(counters.disable, value);
}

inline LifecycleCounters readLifecycleCounters(std::filesystem::path const& path) {
    LifecycleCounters counters;
    std::ifstream input(path);
    std::string line;
    // Cold lifecycle initialization only: the log survives DLL unload and process restart.
    while (std::getline(input, line)) observeLifecycleLine(line, counters);
    return counters;
}

} // namespace probe::detail
