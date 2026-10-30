#include "mod/Internal.h"

#include "mod/OverheadModel.h"

using namespace probe::detail;

namespace probe::detail {

namespace {

constexpr std::size_t kOverheadCapacity = 1024;

OverheadWindow<kOverheadCapacity> gSamples;
std::atomic<std::uint32_t> gBusyUs{0};
std::atomic<std::uint32_t> gBusyTicks{0};
std::atomic<std::uint64_t>  gFrequency{0};
std::mutex                 gInitMutex;

// Per-path rates use one count from the first call through the whole session.
// Never reset a second counter during first-call publication: another producer
// may already have incremented it.
PathRateCounter gRecordRate;
PathRateCounter gWatchRate;
PathRateCounter gHookRate;

PathRateCounter& rateCounter(PathCounter path) {
    switch (path) {
    case PathCounter::Record:
        return gRecordRate;
    case PathCounter::Watch:
        return gWatchRate;
    case PathCounter::Hook:
    default:
        return gHookRate;
    }
}

nlohmann::json rateJson(PathRateCounter const& counter) {
    std::uint64_t const now   = GetTickCount64();
    auto const snapshot = counter.snapshot();
    std::uint64_t const start = snapshot.startedMs;
    std::uint64_t const count = snapshot.total;
    double              rate  = 0.0;
    if (start != 0 && now > start) {
        rate = static_cast<double>(count) * 1000.0 / static_cast<double>(now - start);
    }
    return nlohmann::json{
        {"calls_per_sec", rate                                         },
        {"calls_total",   count},
    };
}

std::uint32_t percentile(std::vector<std::uint32_t> const& values, double fraction) {
    if (values.empty()) {
        return 0;
    }
    std::size_t index = static_cast<std::size_t>(fraction * static_cast<double>(values.size()));
    if (index >= values.size()) {
        index = values.size() - 1;
    }
    return values[index];
}

} // namespace

void overheadInit() {
    std::lock_guard lock(gInitMutex);
    gSamples.initialize();
    if (gFrequency == 0) {
        LARGE_INTEGER frequency{};
        if (QueryPerformanceFrequency(&frequency)) {
            gFrequency = static_cast<std::uint64_t>(frequency.QuadPart);
        }
    }
}

std::uint64_t qpcNow() {
    LARGE_INTEGER value{};
    QueryPerformanceCounter(&value);
    return static_cast<std::uint64_t>(value.QuadPart);
}

std::uint64_t qpcElapsedUs(std::uint64_t start, std::uint64_t end) {
    auto const frequency = gFrequency.load(std::memory_order_relaxed);
    if (frequency == 0 || end < start) {
        return 0;
    }
    return (end - start) * 1'000'000ull / frequency;
}

void overheadRecord(std::uint32_t tickUs, std::uint32_t hookUs) {
    // No lazy initialization, allocation or mutex on the sampling path.
    gSamples.record({tickUs, hookUs});
}

nlohmann::json overheadJson() {
    nlohmann::json out;
    out["event"]              = "overhead";
    out["paths"]              = pathRatesJson();
    out["window_samples"]     = kOverheadCapacity;
    auto const frequency = gFrequency.load(std::memory_order_relaxed);
    out["qpc_resolution_ns"]  =
        frequency > 0 ? 1'000'000'000.0 / static_cast<double>(frequency) : 0.0;

    auto const snapshot = gSamples.snapshot();
    auto const count = snapshot.count;
    out["samples"] = count;
    out["consistent"] = snapshot.consistent;
    out["sample_head"] = snapshot.head;
    out["sample_first"] = snapshot.head - count;
    if (!snapshot.consistent || count == 0) {
        if (!snapshot.consistent) out["reason"] = gSamples.available() ? "snapshot_busy" : "unavailable";
        return out;
    }
    std::vector<std::uint32_t> tick;
    std::vector<std::uint32_t> hook;
    tick.reserve(count);
    hook.reserve(count);
    for (std::size_t i = 0; i < count; ++i) {
        tick.push_back(snapshot.rows[i].tickUs);
        hook.push_back(snapshot.rows[i].hookUs);
    }
    std::sort(tick.begin(), tick.end());
    std::sort(hook.begin(), hook.end());

    std::uint64_t sum = 0;
    for (auto value : tick) {
        sum += value;
    }
    double const average = count > 0 ? static_cast<double>(sum) / static_cast<double>(count) : 0.0;

    // tick_p50/p99 keep their old names for callers; they measure the interval
    // between tick starts, not the hook's own cost.
    out["tick_p50_us"]   = percentile(tick, 0.50);
    out["tick_p99_us"]   = percentile(tick, 0.99);
    out["tick_max_us"]   = tick.back();
    out["tick_interval_p50_us"] = out["tick_p50_us"];
    out["tick_interval_p99_us"] = out["tick_p99_us"];
    out["tick_interval_max_us"] = out["tick_max_us"];
    out["hook_p50_us"]   = percentile(hook, 0.50);
    out["hook_p99_us"]   = percentile(hook, 0.99);
    out["hook_max_us"]   = hook.back();
    out["tick_hz"]       = average > 0.0 ? 1'000'000.0 / average : 0.0;
    out["calls_per_sec"] = average > 0.0 ? 1'000'000.0 / average : 0.0;
    return out;
}

void overheadRequestBusy(std::uint32_t us, std::uint32_t ticks) {
    gBusyUs.store(std::min<std::uint32_t>(us, 5000), std::memory_order_relaxed);
    gBusyTicks.store(std::min<std::uint32_t>(ticks, 200), std::memory_order_release);
}

void pathCount(PathCounter path) { rateCounter(path).record(GetTickCount64()); }

nlohmann::json pathRatesJson() {
    return nlohmann::json{
        {"record", rateJson(gRecordRate)},
        {"watch",  rateJson(gWatchRate) },
        {"hook",   rateJson(gHookRate)  },
    };
}

void overheadBusyTick() {
    if (gBusyTicks.load(std::memory_order_acquire) == 0) {
        return;
    }
    std::uint32_t const us    = gBusyUs.load(std::memory_order_relaxed);
    std::uint64_t const start = qpcNow();
    while (qpcElapsedUs(start, qpcNow()) < us) {
        // Intentionally burn the tick to prove the overhead metric is wired up.
    }
    gBusyTicks.fetch_sub(1, std::memory_order_acq_rel);
}

} // namespace probe::detail
