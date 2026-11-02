#pragma once

#include "mod/PlayerIdentityModel.h"
#include "mod/QueryModel.h"
#include "mod/StagedCapture.h"
#include "mod/TickStreamModel.h"

namespace probe::detail {

enum class FlightOperation { Fly, Toggle };
enum class FlightTarget { Off, On, Invert };
enum class FlightStage { Client, Server, Verify, Assert, Complete };
struct FlightQuery {
    FlightOperation operation = FlightOperation::Toggle;
    FlightTarget target = FlightTarget::Invert;
    std::uint64_t id = 0, generation = 0;
    CaptureTest test = CaptureTest::None;
    FlightStage testStage = FlightStage::Client;
};
struct FlightSnapshot {
    PlayerIdentity identity;
    bool target = false, flying = false, clientApplied = false, serverApplied = false;
    int gamemode = 0;
    char const* error = nullptr;
};
using FlightCapture = StagedCapture<FlightQuery, FlightSnapshot, FlightStage>;

// Cold publication, one tick reader, one cold result consumer. The command
// packs identity and target into one atomic word; stale failures cannot clear
// a replacement observation. Tick only reads the native ability state and
// retains no player pointer or owning string.
class FlightAssertion {
public:
    static constexpr unsigned Ticks = 8;
    struct Failure { std::uint64_t id = 0; char const* error = nullptr; };
    void prepare(std::uint64_t id) { mCommand.store(id << IdShift, std::memory_order_release); }
    bool start(std::uint64_t id, bool target, CaptureTest test = CaptureTest::None) {
        auto expected = id << IdShift;
        auto command = expected | Active | static_cast<unsigned>(target) | (static_cast<unsigned>(test) << 2);
        return mCommand.compare_exchange_strong(expected, command, std::memory_order_release);
    }
    void cancel() { mCommand.store(0, std::memory_order_release); }
    bool active() const { return (mCommand.load(std::memory_order_acquire) & Active) != 0; }
    template <class Apply>
    void tick(Apply&& apply) {
        auto command = mCommand.load(std::memory_order_acquire);
        if (!(command & Active)) { mRemaining = 0; return; }
        auto test = CaptureTest::None;
        if (command != mSeen) {
            mSeen = command;
            mRemaining = Ticks;
            test = static_cast<CaptureTest>((command >> 2) & 7);
        }
        char const* error = nullptr;
        try { error = apply((command & 1) != 0, test); }
        catch (...) { error = "flight_assert_failed"; }
        if (error) {
            if (!mFailures.push({command >> IdShift, error})) ++mLost;
            mRemaining = 0;
        } else --mRemaining;
        if (!mRemaining) mCommand.compare_exchange_strong(command, 0, std::memory_order_release);
    }
    bool pop(Failure& out) { return mFailures.pop(out); }
    std::uint64_t takeLost() { return mLost.exchange(0); }
private:
    static constexpr unsigned Active = 2, IdShift = 5;
    std::atomic<std::uint64_t> mCommand{0}, mLost{0};
    std::uint64_t mSeen = 0; // Tick-owned.
    unsigned mRemaining = 0;
    TickQueue<Failure, 16> mFailures;
};

} // namespace probe::detail
