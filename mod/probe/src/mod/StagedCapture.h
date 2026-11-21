#pragma once

#include <atomic>
#include <cstdint>
#include <memory>
#include <optional>
#include <utility>

namespace probe::detail {

// Cold, request-owned pipeline. Queues hold weak references, so cancelled work
// cannot retain the snapshot or act on a newer request. Work alone owns the
// snapshot until Ready; errors can be consumed while old work is still running.
template <class Query, class Snapshot, class Stage>
class StagedCapture {
public:
    explicit StagedCapture(char const* invalidStage = "capture_stage_invalid")
    : StagedCapture(Stage::Client, invalidStage) {}
    explicit StagedCapture(Stage firstStage, char const* invalidStage = "capture_stage_invalid")
    : mFirstStage(firstStage), mInvalidStage(invalidStage) {}

    struct Request {
        Request(
            Query         value,
            std::uint64_t now,
            std::uint64_t timeout,
            char const*   invalidStage,
            Stage         firstStage,
            std::uint64_t reqIdValue = 0
        )
        : query(value),
          reqId(reqIdValue),
          deadline(now + timeout),
          stageError(invalidStage),
          stage(firstStage) {}

        std::optional<Stage> dispatch() {
            auto expected = Phase::Pending;
            if (!phase.compare_exchange_strong(expected, Phase::Queued, std::memory_order_acquire)) return {};
            return stage;
        }

        template <class Work>
        void run(Stage expectedStage, std::uint64_t now, Work&& work) {
            if (now >= deadline) cancel("capture_timeout");
            auto expected = Phase::Queued;
            if (!phase.compare_exchange_strong(expected, Phase::Running, std::memory_order_acquire)) return;
            if (failure.load(std::memory_order_acquire)) {
                phase.store(Phase::Cancelled, std::memory_order_release);
                return;
            }
            try {
                if (stage != expectedStage) cancel(stageError);
                else stage = work(snapshot);
            } catch (...) {
                cancel("capture_failed");
            }
            phase.store(stage == Stage::Complete || snapshot.error ? Phase::Ready : Phase::Pending,
                        std::memory_order_release);
        }

        void cancel(char const* reason) {
            char const* expected = nullptr;
            failure.compare_exchange_strong(expected, reason, std::memory_order_release);
        }

        Query const         query;
        std::uint64_t const reqId = 0;
        Snapshot            snapshot;

    private:
        friend class StagedCapture;
        enum class Phase { Pending, Queued, Running, Ready, Cancelled };
        std::uint64_t const deadline;
        char const* const stageError;
        std::atomic<Phase> phase{Phase::Pending};
        std::atomic<char const*> failure{nullptr};
        std::atomic_bool consuming{false};
        Stage stage;
    };

    struct Completion {
        Completion() = default;
        Completion(Completion const&) = delete;
        Completion& operator=(Completion const&) = delete;
        Completion(Completion&& other) noexcept
        : request(std::move(other.request)), error(other.error), owner(std::exchange(other.owner, nullptr)) {}
        Completion& operator=(Completion&& other) noexcept {
            release();
            request = std::move(other.request);
            error = other.error;
            owner = std::exchange(other.owner, nullptr);
            return *this;
        }
        ~Completion() { release(); }
        bool release() {
            if (!owner) return false;
            auto* active = std::exchange(owner, nullptr);
            auto expected = request;
            return active->mCurrent.compare_exchange_strong(expected, {});
        }
        std::shared_ptr<Request> request;
        char const* error = nullptr;
    private:
        friend class StagedCapture;
        Completion(StagedCapture* source, std::shared_ptr<Request> value, char const* reason)
        : request(std::move(value)), error(reason), owner(source) {}
        StagedCapture* owner = nullptr;
    };

    std::shared_ptr<Request> begin(
        Query         query,
        std::uint64_t now,
        std::uint64_t timeout = 2000,
        std::uint64_t reqId   = 0
    ) {
        if (mCurrent.load(std::memory_order_acquire)) return {};
        std::shared_ptr<Request> request = std::make_unique<Request>(query, now, timeout, mInvalidStage, mFirstStage, reqId);
        std::shared_ptr<Request> expected;
        if (!mCurrent.compare_exchange_strong(expected, request)) return {};
        return request;
    }
    std::shared_ptr<Request> current() const { return mCurrent.load(std::memory_order_acquire); }
    Completion take(std::uint64_t now) {
        auto request = current();
        if (!request) return {};
        auto phase = request->phase.load(std::memory_order_acquire);
        auto error = request->failure.load(std::memory_order_acquire);
        if (!error && phase != Request::Phase::Ready) {
            if (now < request->deadline) return {};
            error = "capture_timeout";
        }
        bool expected = false;
        if (!request->consuming.compare_exchange_strong(expected, true)) return {};
        if (error) request->cancel(error);
        // A cancelled callback can still be running. Only successful Ready
        // publication permits the consumer to read snapshot.
        return {this, std::move(request), error};
    }
    void cancel(char const* reason) { if (auto request = current()) request->cancel(reason); }
    void reset() { if (auto request = mCurrent.exchange({})) request->cancel("capture_cancelled"); }

private:
    std::atomic<std::shared_ptr<Request>> mCurrent;
    Stage const mFirstStage;
    char const* const mInvalidStage;
};

} // namespace probe::detail
