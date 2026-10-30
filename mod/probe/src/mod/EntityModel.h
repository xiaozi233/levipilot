#pragma once

#include "mod/QueryModel.h"
#include <optional>

namespace probe::detail {

enum class EntityOperation { List, Info, Attack, Interact, LookAt };
enum class EntityStage { Client, Server, Aim, Complete };

struct EntityQuery {
    EntityOperation operation = EntityOperation::List;
    std::uint64_t runtimeId = 0;
    float radius = 16;
    int limit = 64;
    CaptureTest test = CaptureTest::None;
    EntityStage testStage = EntityStage::Client;
};

struct EntitySnapshot {
    struct Vec { float x = 0, y = 0, z = 0; } origin, eye, aim;
    struct Row {
        FixedText<256> type;
        FixedText<4096> name;
        std::uint64_t runtimeId = 0;
        Vec pos, velocity;
        float health = 0, distance = 0, yaw = 0, pitch = 0;
        bool isPlayer = false, alive = false, onGround = false;
    };
    std::array<Row, 512> rows{};
    std::size_t count = 0;
    std::uint64_t selfId = 0, uuidA = 0, uuidB = 0;
    FixedText<1024> selfName;
    float yaw = 0, pitch = 0;
    bool ok = false, truncated = false;
    char const* error = nullptr;
};

// Cold, request-owned pipeline: client context -> server actors -> optional
// client aiming. Queues hold weak references, so cancelled work cannot retain
// the large snapshot or act on a newer request. No actor pointer crosses stages.
class EntityCapture {
public:
    struct Request {
        Request(EntityQuery value, std::uint64_t now, std::uint64_t timeout)
        : query(value), deadline(now + timeout) {}

        std::optional<EntityStage> dispatch() {
            auto expected = Phase::Pending;
            if (!phase.compare_exchange_strong(expected, Phase::Queued, std::memory_order_acquire)) return {};
            return stage;
        }

        template <class Work>
        void run(EntityStage expectedStage, std::uint64_t now, Work&& work) {
            if (now >= deadline) cancel("capture_timeout");
            auto expected = Phase::Queued;
            if (!phase.compare_exchange_strong(expected, Phase::Running, std::memory_order_acquire)) return;
            if (failure.load(std::memory_order_acquire)) {
                phase.store(Phase::Cancelled, std::memory_order_release);
                return;
            }
            try {
                if (stage != expectedStage) cancel("entity_stage_invalid");
                else stage = work(snapshot);
            } catch (...) {
                cancel("capture_failed");
            }
            phase.store(stage == EntityStage::Complete || snapshot.error ? Phase::Ready : Phase::Pending,
                        std::memory_order_release);
        }

        void cancel(char const* reason) {
            char const* expected = nullptr;
            failure.compare_exchange_strong(expected, reason, std::memory_order_release);
        }

        EntityQuery const query;
        EntitySnapshot snapshot;

    private:
        friend class EntityCapture;
        enum class Phase { Pending, Queued, Running, Ready, Cancelled };
        std::uint64_t const deadline;
        std::atomic<Phase> phase{Phase::Pending};
        std::atomic<char const*> failure{nullptr};
        std::atomic_bool consuming{false};
        EntityStage stage = EntityStage::Client;
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
        friend class EntityCapture;
        Completion(EntityCapture* source, std::shared_ptr<Request> value, char const* reason)
        : request(std::move(value)), error(reason), owner(source) {}
        EntityCapture* owner = nullptr;
    };

    std::shared_ptr<Request> begin(EntityQuery query, std::uint64_t now, std::uint64_t timeout = 2000) {
        if (mCurrent.load(std::memory_order_acquire)) return {};
        std::shared_ptr<Request> request = std::make_unique<Request>(query, now, timeout);
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
};

} // namespace probe::detail
