#pragma once

#include "mod/PlayerIdentityModel.h"
#include "mod/StagedCapture.h"

namespace probe::detail {

enum class TeleportStage { Server, Complete };
enum class TeleportTest { None, Seh, Cpp, Deferred, Queue, AfterEffects, AfterTeleport, Obsolete };
struct TeleportQuery {
    PlayerIdentity identity;
    std::uint64_t  id = 0, generation = 0, deadline = 0;
    float          x = 0, y = 0, z = 0, yaw = 0, pitch = 0;
    bool           hasRotation = false, clearEffects = false;
    TeleportTest   test = TeleportTest::None;
};
struct TeleportSnapshot {
    bool        effectsCleared = false, applied = false;
    char const* error = nullptr;
};
using TeleportCapture = StagedCapture<TeleportQuery, TeleportSnapshot, TeleportStage>;

// Check again immediately before each mutation. An SDK call already in
// progress cannot be interrupted or rolled back by cancellation/timeout.
inline char const* teleportAdmission(
    TeleportQuery const& query,
    std::uint64_t        generation,
    std::uint64_t        epoch,
    bool                 inWorld,
    std::uint64_t        now
) {
    if (query.generation != generation || !query.identity.matches(epoch, inWorld)) return "world_left";
    if (now >= query.deadline) return "teleport_timeout";
    return nullptr;
}

// The production operation order also runs in native tests. All callbacks
// execute on the server thread, within its local SEH frame; C++ exceptions
// terminate the request through StagedCapture, preserving partial side effects.
template <class Admit, class Prepare, class Clear, class Move, class Fault>
void applyTeleport(
    TeleportQuery const& query,
    TeleportSnapshot&    out,
    Admit&&              admit,
    Prepare&&            prepare,
    Clear&&              clear,
    Move&&               move,
    Fault&&              fault
) {
    if ((out.error = admit())) return;
    fault(TeleportTest::Seh);
    if ((out.error = prepare())) return;
    if (query.clearEffects) {
        if ((out.error = admit())) return;
        clear();
        out.effectsCleared = true;
    }
    fault(TeleportTest::AfterEffects);
    if ((out.error = admit())) return;
    move();
    out.applied = true;
    fault(TeleportTest::AfterTeleport);
}

} // namespace probe::detail
