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
// F9: seqlock around the ring so readers never mix pre/post-wrap samples.
std::atomic<std::uint64_t> gEpoch{0};
std::atomic<std::uint32_t> gBusyUs{0};
std::atomic<std::uint32_t> gBusyTicks{0};
std::uint64_t              gFrequency = 0;
std::mutex                 gInitMutex;

// Per-path rate windows. A window starts on the first call after the counter
// is read for the first time and is never reset; the rate therefore smooths
// over the whole session, which is what matters for spotting a storm.
struct RateCounter {
    std::atomic<std::uint64_t> total{0};
    std::atomic<std::uint64_t> windowStartMs{0};
    std::atomic<std::uint64_t> windowCount{0};
};

RateCounter gRecordRate;
RateCounter gWatchRate;
RateCounter gHookRate;

RateCounter& rateCounter(PathCounter path) {
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

void rateCount(RateCounter& counter) {
    counter.total.fetch_add(1, std::memory_order_relaxed);
    std::uint64_t const now   = GetTickCount64();
    std::uint64_t       start = counter.windowStartMs.load(std::memory_order_relaxed);
    if (start == 0) {
        if (counter.windowStartMs.compare_exchange_strong(
                start,
                now,
                std::memory_order_relaxed,
                std::memory_order_relaxed
            )) {
            counter.windowCount.store(0, std::memory_order_relaxed);
        }
    }
    counter.windowCount.fetch_add(1, std::memory_order_relaxed);
}

nlohmann::json rateJson(RateCounter& counter) {
    std::uint64_t const now   = GetTickCount64();
    std::uint64_t const start = counter.windowStartMs.load(std::memory_order_acquire);
    std::uint64_t const count = counter.windowCount.load(std::memory_order_relaxed);
    double              rate  = 0.0;
    if (start != 0 && now > start) {
        rate = static_cast<double>(count) * 1000.0 / static_cast<double>(now - start);
    }
    return nlohmann::json{
        {"calls_per_sec", rate                                         },
        {"calls_total",   counter.total.load(std::memory_order_relaxed)},
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
    // Single writer: the client tick thread. The epoch lets readers detect a
    // half-written window across the ring wrap.
    std::uint64_t const epoch  = gEpoch.load(std::memory_order_relaxed);
    gEpoch.store(epoch + 1, std::memory_order_relaxed); // odd: writing
    std::atomic_thread_fence(std::memory_order_release);
    std::uint64_t const index  = gHead.load(std::memory_order_relaxed);
    Sample&             sample = gSamples[index % kOverheadCapacity];
    sample.tickUs              = tickUs;
    sample.hookUs              = hookUs;
    gHead.store(index + 1, std::memory_order_release);
    gEpoch.store(epoch + 2, std::memory_order_release);
}

nlohmann::json overheadJson() {
    nlohmann::json out;
    out["event"]              = "overhead";
    out["paths"]              = pathRatesJson();
    out["window_samples"]     = kOverheadCapacity;
    out["qpc_resolution_ns"]  =
        gFrequency > 0 ? 1'000'000'000.0 / static_cast<double>(gFrequency) : 0.0;

    std::uint64_t head  = 0;
    std::size_t   count = 0;
    std::vector<std::uint32_t> tick;
    std::vector<std::uint32_t> hook;
    bool consistent = false;
    for (int attempt = 0; attempt < 64 && !consistent; ++attempt) {
        std::uint64_t const before = gEpoch.load(std::memory_order_acquire);
        if ((before & 1u) != 0) {
            continue; // writer is mid-sample
        }
        head  = gHead.load(std::memory_order_acquire);
        count = static_cast<std::size_t>(
            std::min<std::uint64_t>(head, kOverheadCapacity)
        );
        tick.clear();
        hook.clear();
        if (gSamples && count > 0) {
            std::uint64_t const first = head - count;
            tick.reserve(count);
            hook.reserve(count);
            for (std::size_t offset = 0; offset < count; ++offset) {
                Sample const& sample = gSamples[(first + offset) % kOverheadCapacity];
                tick.push_back(sample.tickUs);
                hook.push_back(sample.hookUs);
            }
        }
        std::atomic_thread_fence(std::memory_order_acquire);
        consistent = gEpoch.load(std::memory_order_relaxed) == before;
    }
    if (!gSamples || count == 0) {
        out["samples"] = 0;
        return out;
    }
    std::sort(tick.begin(), tick.end());
    std::sort(hook.begin(), hook.end());

    std::uint64_t sum = 0;
    for (auto value : tick) {
        sum += value;
    }
    double const average = count > 0 ? static_cast<double>(sum) / static_cast<double>(count) : 0.0;

    out["samples"]       = count;
    out["consistent"]    = consistent;
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

void pathCount(PathCounter path) { rateCount(rateCounter(path)); }

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
