#pragma once

#include <cstdint>
#include <mutex>
#include <optional>

namespace probe::detail {

// Cold lifecycle state only: never called by a tick/hook. No game pointers are
// retained and no game calls or event delivery take place under this lock.
class WorldEntryModel {
public:
    static constexpr std::uint64_t TimeoutMs        = 30000;
    static constexpr std::uint64_t RecoveryStableMs = 500;
    enum class Phase { Idle, Starting, Quarantined };
    struct Snapshot {
        std::uint64_t id = 0, deadline = 0;
        Phase         phase = Phase::Idle;
    };
    struct Completion {
        std::uint64_t id;
        bool          ok;
        char const*   reason; // Static literals owned by the caller.
    };

    std::uint64_t begin(std::uint64_t now, std::uint64_t timeout = TimeoutMs) {
        std::lock_guard lock(mMutex);
        if (mState.phase != Phase::Idle) return 0;
        mState = {++mGeneration, now + timeout, Phase::Starting};
        mReadySince.reset();
        return mState.id;
    }
    Snapshot snapshot() const {
        std::lock_guard lock(mMutex);
        return mState;
    }
    std::optional<Completion> settle(std::uint64_t id, bool ok, char const* reason) {
        std::lock_guard lock(mMutex);
        if (!current(id)) return {};
        bool const pending = mState.phase == Phase::Starting;
        // A late join is real gameplay, but must not emit a second terminal.
        if (ok) mState.phase = Phase::Idle;
        else mState.phase = Phase::Quarantined;
        mReadySince.reset();
        if (pending) return Completion{id, ok, reason};
        return {};
    }
    std::optional<Completion> expire(std::uint64_t now) {
        std::lock_guard lock(mMutex);
        if (mState.phase != Phase::Starting || now < mState.deadline) return {};
        mState.phase = Phase::Quarantined;
        mReadySince.reset();
        return Completion{mState.id, false, "entry_timeout"};
    }
    // Recovery is separate from completion. A timeout never admits another
    // server; both the menu and server absence must remain verified first.
    bool recover(std::uint64_t id, std::uint64_t now, bool safeMenu) {
        std::lock_guard lock(mMutex);
        if (!current(id) || mState.phase != Phase::Quarantined) return false;
        if (!safeMenu) {
            mReadySince.reset();
            return false;
        }
        if (!mReadySince) mReadySince = now;
        if (now - *mReadySince < RecoveryStableMs) return false;
        mState.phase = Phase::Idle;
        mReadySince.reset();
        return true;
    }
    void reset() {
        std::lock_guard lock(mMutex);
        mState = {++mGeneration, 0, Phase::Idle};
        mReadySince.reset();
    }

private:
    bool current(std::uint64_t id) const { return id != 0 && id == mState.id && mState.phase != Phase::Idle; }
    mutable std::mutex           mMutex;
    Snapshot                     mState;
    std::uint64_t                mGeneration = 0;
    std::optional<std::uint64_t> mReadySince;
};

enum class EntryTest { None, Timeout, ProbeFault, StartFault, MissingJoin };

} // namespace probe::detail
