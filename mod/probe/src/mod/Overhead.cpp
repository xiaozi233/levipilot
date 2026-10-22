#include "mod/Internal.h"

#include <new>

using namespace probe::detail;

namespace probe::detail {

namespace {

constexpr std::size_t kOverheadCapacity = 1024;

struct Sample {
    std::uint32_t tickUs = 0;
    std::uint32_t hookUs = 0;
};

std::unique_ptr<Sample[]>  gSamples;
std::atomic<std::uint64_t> gHead{0};
std::atomic<std::uint32_t> gBusyUs{0};
std::atomic<std::uint32_t> gBusyTicks{0};
std::uint64_t              gFrequency = 0;
std::mutex                 gInitMutex;

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
    if (!gSamples) {
        gSamples = std::unique_ptr<Sample[]>(new (std::nothrow) Sample[kOverheadCapacity]());
    }
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
    if (gFrequency == 0 || end < start) {
        return 0;
    }
    return (end - start) * 1'000'000ull / gFrequency;
}

void overheadRecord(std::uint32_t tickUs, std::uint32_t hookUs) {
    if (!gSamples) {
        overheadInit();
    }
    if (!gSamples) {
        return;
    }
    // Single writer: the client tick thread.
    std::uint64_t const index  = gHead.load(std::memory_order_relaxed);
    Sample&             sample = gSamples[index % kOverheadCapacity];
    sample.tickUs              = tickUs;
    sample.hookUs              = hookUs;
    gHead.store(index + 1, std::memory_order_release);
}

nlohmann::json overheadJson() {
    std::uint64_t const head  = gHead.load(std::memory_order_acquire);
    std::size_t const   count = static_cast<std::size_t>(
        std::min<std::uint64_t>(head, kOverheadCapacity)
    );
    nlohmann::json out;
    out["event"] = "overhead";
    if (!gSamples || count == 0) {
        out["samples"] = 0;
        return out;
    }
    std::uint64_t const first = head - count;
    std::vector<std::uint32_t> tick;
    std::vector<std::uint32_t> hook;
    tick.reserve(count);
    hook.reserve(count);
    for (std::size_t offset = 0; offset < count; ++offset) {
        Sample const& sample = gSamples[(first + offset) % kOverheadCapacity];
        tick.push_back(sample.tickUs);
        hook.push_back(sample.hookUs);
    }
    std::sort(tick.begin(), tick.end());
    std::sort(hook.begin(), hook.end());

    std::uint64_t sum = 0;
    for (auto value : tick) {
        sum += value;
    }
    double const average = count > 0 ? static_cast<double>(sum) / static_cast<double>(count) : 0.0;

    out["samples"]       = count;
    out["tick_p50_us"]   = percentile(tick, 0.50);
    out["tick_p99_us"]   = percentile(tick, 0.99);
    out["tick_max_us"]   = tick.back();
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
