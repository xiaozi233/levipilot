#pragma once

#include "mod/FlightModel.h"

#include <mutex>

namespace probe::detail {

// One flight request owns both its staged mutation and bounded tick assertion.
struct FlightRequestState {
    FlightCapture              capture;
    FlightAssertion            assertion;
    std::mutex                 mutex;
    std::atomic<std::uint64_t> generation{0};
    std::uint64_t              nextId = 0, replyAfter = 0;
};

} // namespace probe::detail
