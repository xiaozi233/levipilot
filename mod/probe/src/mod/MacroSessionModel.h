#pragma once

#include "mod/MacroPlaybackModel.h"
#include "mod/MacroRecordModel.h"
#include "mod/QueryModel.h"
#include "mod/TickStreamModel.h"

#include <atomic>
#include <mutex>

namespace probe::detail {

// Cold admission/snapshot/drain uses mutex. Tick only calls cancelPlayback or
// accesses the lock-free playback/recording models; it never takes mutex.
class MacroSession {
public:
    void cancelPlayback(MacroEnd reason) { playback.cancel(reason); }
    void worldLeft() {
        recordWorldLeft.store(true, std::memory_order_release);
        playback.cancel(MacroEnd::WorldLeft);
    }
    void stopAll(MacroEnd playbackReason, MacroRecordEnd recordReason) {
        std::lock_guard lock(mutex);
        playback.stop(playbackReason);
        recording.stop(recordReason);
    }

    std::mutex mutex;
    MacroPlayback playback;
    MacroRecordSession recording;
    std::atomic<CaptureTest> playbackTest{CaptureTest::None};
    std::atomic<CaptureTest> releaseTest{CaptureTest::None};
    std::atomic<CaptureTest> recordTest{CaptureTest::None};
    std::atomic_bool recordWorldLeft{false};
};

} // namespace probe::detail
