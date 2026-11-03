#pragma once

#include "mod/TickStreamModel.h"

#include <mutex>

namespace probe::detail {

// Cold commands and the spool consumer serialize this stream as one session.
struct LegacyStreamState {
    LegacyStream<>             stream;
    std::mutex                 mutex;
    LegacyStream<>::Completion lastCompletion;
    std::atomic<StreamTest>    test{StreamTest::None};
    std::atomic_bool           worldLeft{false};
    std::uint64_t              drainAfterMs = 0;
};

} // namespace probe::detail
