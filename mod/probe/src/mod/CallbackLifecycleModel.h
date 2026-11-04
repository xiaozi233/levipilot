#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <thread>

namespace probe::detail {
static_assert(std::atomic<std::uint64_t>::is_always_lock_free);

enum class CallbackDomain : unsigned { Key, Mouse, Tick, Turn, Client, Server, Delay, Event, Diagnostic, Veh, Command, Request, Count };
inline constexpr unsigned kCallbackDomains = static_cast<unsigned>(CallbackDomain::Count);
inline constexpr char const* kCallbackNames[] = {
    "key", "mouse", "tick", "turn", "client", "server", "delay", "event", "diagnostic", "veh", "command", "request"
};

// The closed bit and queue ownership count share one CAS word. Closing cannot
// race a successful reservation that has not yet become visible to the waiter.
// Entry scopes count the ENTIRE callback, including calls to the original.
// Removing a source is still required: zero with a live source is not a fence.
class CallbackLifecycle {
    static constexpr std::uint64_t Closed = 1ull << 63;
    std::atomic<std::uint64_t> mOwners{0};
    std::array<std::atomic<std::uint64_t>, kCallbackDomains> mQueued{}, mRunning{}, mEntered{}, mCompleted{};
    static inline thread_local unsigned sDepth = 0;

public:
    struct Counts { std::uint64_t queued, running, entered, completed; };
    class Scope {
        CallbackLifecycle& mOwner;
        unsigned mDomain;
        bool mAdmitted;
    public:
        Scope(CallbackLifecycle& owner, CallbackDomain domain) noexcept
        : mOwner(owner), mDomain(static_cast<unsigned>(domain)), mAdmitted(!owner.closed()) {
            ++sDepth;
            ++mOwner.mRunning[mDomain];
            ++mOwner.mEntered[mDomain];
        }
        ~Scope() {
            ++mOwner.mCompleted[mDomain];
            --mOwner.mRunning[mDomain];
            --sDepth;
        }
        Scope(Scope const&) = delete;
        bool admitted() const noexcept { return mAdmitted; }
    };
    class Ticket {
        friend class CallbackLifecycle;
        CallbackLifecycle& mOwner;
        unsigned mDomain;
    public:
        Ticket(CallbackLifecycle& owner, unsigned domain) : mOwner(owner), mDomain(domain) {}
        ~Ticket() {
            --mOwner.mQueued[mDomain];
            mOwner.mOwners.fetch_sub(1);
        }
        Ticket(Ticket const&) = delete;
    };

    // Only the cold submission path allocates. All closure copies share one
    // ticket; it survives execution until the executor releases its last copy.
    std::shared_ptr<Ticket> reserve(CallbackDomain domain) {
        auto state = mOwners.load();
        do {
            if (state & Closed) return {};
        } while (!mOwners.compare_exchange_weak(state, state + 1));
        auto const i = static_cast<unsigned>(domain);
        ++mQueued[i];
        try { return std::make_shared<Ticket>(*this, i); }
        catch (...) { --mQueued[i]; mOwners.fetch_sub(1); throw; }
    }
    bool close() noexcept {
        if (sDepth != 0) return false; // Never wait on this thread's own callback.
        return !(mOwners.fetch_or(Closed) & Closed);
    }
    void reopen() noexcept { mOwners.fetch_and(~Closed); }
    bool closed() const noexcept { return (mOwners.load() & Closed) != 0; }
    static bool insideCallback() noexcept { return sDepth != 0; }
    Counts counts(CallbackDomain domain) const noexcept {
        auto const i = static_cast<unsigned>(domain);
        return {mQueued[i].load(), mRunning[i].load(), mEntered[i].load(), mCompleted[i].load()};
    }
    bool idle() const noexcept {
        if ((mOwners.load() & ~Closed) != 0) return false;
        for (auto const& n : mRunning) if (n.load() != 0) return false;
        return true;
    }
    template<class Predicate>
    bool wait(std::chrono::milliseconds timeout, Predicate&& sourcesReleased) const {
        if (sDepth != 0) return false;
        auto const deadline = std::chrono::steady_clock::now() + timeout;
        do {
            if (idle() && sourcesReleased()) return true;
            if (std::chrono::steady_clock::now() >= deadline) return false;
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        } while (true);
    }
};

inline CallbackLifecycle gCallbacks;
using CallbackScope = CallbackLifecycle::Scope;

// Declaration order matters: destroy user captures BEFORE releasing ownership.
struct CallbackTask {
    std::shared_ptr<CallbackLifecycle::Ticket> ticket;
    CallbackLifecycle* owner;
    CallbackDomain domain;
    std::function<void()> function;
    void operator()() const {
        CallbackScope scope(*owner, domain);
        function(); // Already admitted at submission; allowed to finish closed.
    }
};
inline std::optional<CallbackTask> trackedCallback(CallbackLifecycle& owner, CallbackDomain domain, std::function<void()> fn) {
    auto ticket = owner.reserve(domain);
    if (!ticket) return {};
    return CallbackTask{std::move(ticket), &owner, domain, std::move(fn)};
}
} // namespace probe::detail
