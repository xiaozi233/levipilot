#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

PlayerIdentity ProbeMod::localPlayerIdentity() const {
    PlayerIdentity identity;
    auto const     epoch = mPoseEpoch.load();
    if (!mWorld.inWorld() || !mPlayerIdentity.read(identity, epoch) || epoch != mPoseEpoch.load() || !mWorld.inWorld())
        return {};
    return identity;
}

void ProbeMod::capturePlayerIdentity(LocalPlayer& player) {
    auto const epoch = mPoseEpoch.load();
    auto const test  = mPlayerIdentityTest.load();
    mPlayerIdentity.capture(epoch, mTick.load(), [&](PlayerIdentity& out) {
        return guardedGameRead([&] {
            injectCaptureFault(test);
            if (test == CaptureTest::Partial) return; // Null UUID selftest.
            auto const uuid = player.getUuid();
            out.uuidA       = uuid.a;
            out.uuidB       = uuid.b;
        });
    });
}

} // namespace probe
