#pragma once

#include "mod/AtomicRing.h"

namespace probe::detail {

struct SendProbeRecord {
    std::uint64_t tick = 0, net = 0;
    std::uint32_t tid = 0, before = 0, after = 0, reserved = 0;
};

// Serialization can run on several threads. Atomic payloads and per-slot
// publication keep concurrent dumps from observing partially replaced rows.
using SendProbeHistory = AtomicRing<SendProbeRecord, 64>;

} // namespace probe::detail
