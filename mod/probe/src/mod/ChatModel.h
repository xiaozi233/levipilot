#pragma once

// Request-owned UI snapshots: a timed-out callback can finish without touching
// a newer request's storage. Only the pipe/spool side allocates these objects.
#include "mod/TaskModel.h"

#include <atomic>
#include <cstdint>
#include <memory>

namespace probe::detail {

class ChatCapture {
public:
    static constexpr std::uint64_t TimeoutMs = 2000;

    struct Snapshot {
        struct Line {
            FixedText<2048> text;
            int             type      = 0;
            bool            truncated = false;
        };
        std::array<Line, 64> lines{};
        std::size_t          count = 0, total = 0;
        char const*          error = nullptr;
    };

    struct Request {
        explicit Request(std::uint64_t now, std::uint64_t reqIdValue = 0)
        : reqId(reqIdValue),
          deadline(now + TimeoutMs) {}

        template <typename Capture>
        void run(std::uint64_t now, Capture&& capture) {
            if (now >= deadline) cancel("chat_history_timeout");
            if (failure.load(std::memory_order_acquire)) return;
            auto expected = Phase::Requested;
            if (!phase.compare_exchange_strong(expected, Phase::Capturing)) return;
            if (failure.load(std::memory_order_acquire)) return;
            try {
                capture(snapshot);
            } catch (...) {
                cancel("chat_history_failed");
            }
            phase.store(Phase::Ready, std::memory_order_release);
        }

        void cancel(char const* reason) {
            char const* expected = nullptr;
            failure.compare_exchange_strong(expected, reason, std::memory_order_release);
            auto pending = Phase::Requested;
            phase.compare_exchange_strong(pending, Phase::Cancelled);
        }

        std::uint64_t const reqId = 0;
        Snapshot            snapshot;

    private:
        friend class ChatCapture;
        enum class Phase { Requested, Capturing, Ready, Cancelled };
        std::uint64_t const      deadline;
        std::atomic<Phase>       phase{Phase::Requested};
        std::atomic<char const*> failure{nullptr}; // Static literals only.
    };

    struct Completion {
        std::shared_ptr<Request> request;
        char const*              error = nullptr;
    };

    std::shared_ptr<Request> begin(std::uint64_t now, std::uint64_t reqId = 0) {
        if (mCurrent.load(std::memory_order_acquire)) return {};
        // Queued work holds only a weak_ptr. Use a separate control block so
        // its weak reference cannot retain the 128 KiB snapshot after timeout.
        std::shared_ptr<Request> request = std::make_unique<Request>(now, reqId);
        std::shared_ptr<Request> expected;
        if (!mCurrent.compare_exchange_strong(expected, request)) return {};
        return request;
    }

    Completion take(std::uint64_t now) {
        auto request = mCurrent.load(std::memory_order_acquire);
        if (!request) return {};
        // Acquire publication before checking failure: a throwing capture
        // publishes failure before Ready, and must never look successful.
        auto const phase = request->phase.load(std::memory_order_acquire);
        auto       error = request->failure.load(std::memory_order_acquire);
        if (!error && phase != Request::Phase::Ready) {
            if (now < request->deadline) return {};
            error = "chat_history_timeout";
        }
        auto expected = request;
        if (!mCurrent.compare_exchange_strong(expected, {})) return {};
        if (error) request->cancel(error);
        // Never read snapshot when error != nullptr: an abandoned capture may
        // still be writing it. Its shared_ptr keeps that storage alive.
        return {std::move(request), error};
    }

    void cancel(char const* reason) {
        if (auto request = mCurrent.load(std::memory_order_acquire)) request->cancel(reason);
    }

    void reset() {
        if (auto request = mCurrent.exchange({})) request->cancel("chat_history_cancelled");
    }

private:
    std::atomic<std::shared_ptr<Request>> mCurrent;
};

} // namespace probe::detail
