#pragma once

#include "mod/TeleportModel.h"

#include <mutex>

namespace probe::detail {

// The generation, id allocator and staged capture are invalidated together.
struct TeleportRequestState {
    TeleportCapture            capture{TeleportStage::Server, "teleport_stage_invalid"};
    std::mutex                 mutex;
    std::atomic<std::uint64_t> generation{0};
    std::uint64_t              nextId = 0;
};

} // namespace probe::detail
