#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {
bool ProbeMod::queueClientTask(
    std::function<void()>            task,
    std::function<void(char const*)> onFailure,
    SdkTaskTest                      test
) {
    std::uint64_t const reqId = activeReqId();
    auto                fail  = [this, onFailure, reqId](char const* reason, char const* message = "") {
        if (onFailure) {
            if (!guardedSdkCall([&] { onFailure(reason); })) bootLog("client task: failure handler failed");
        } else {
            ActiveReqIdScope reqScope(reqId);
            if (std::strcmp(reason, "client_task_failed") == 0)
                enqueue(
                    nlohmann::json{
                        {"event",   "error"         },
                        {"reason",  "internal_error"},
                        {"phase",   "client_task"   },
                        {"message", message         }
                }.dump()
                );
            else
                enqueue(
                    nlohmann::json{
                        {"event",  "error"},
                        {"reason", reason }
                }.dump()
                );
        }
    };
    auto tracked =
        trackedCallback(gCallbacks, CallbackDomain::Client, [this, task = std::move(task), fail, test, reqId] {
            if (!mRunning.load(std::memory_order_acquire)) return;
            ActiveReqIdScope reqScope(reqId);
            // Report outside the guard: a throwing failure handler must not be
            // mistaken for a second failure of the original task.
            std::string message;
            if (!guardedSdkCall(
                    [&] {
                        injectSdkTaskFault(test, SdkTaskPhase::Run);
                        task();
                    },
                    CaptureTest::None,
                    &message
                ))
                fail("client_task_failed", message.c_str());
        });
    if (!tracked) {
        fail("quiescing");
        return false;
    }
    // Unlike ClientThreadExecutor, the platform queue runs at the main menu.
    // Lookup can itself fault during teardown; protect it before dereferencing.
    decltype(ll::service::getAppPlatform()) platform;
    if (!guardedSdkCall([&] {
            injectSdkTaskFault(test, SdkTaskPhase::Lookup);
            platform = ll::service::getAppPlatform();
        })) {
        fail("client_task_queue_failed");
        return false;
    }
    if (!platform) {
        fail("no_app_platform");
        return false;
    }
    std::function<void()> run = std::move(*tracked);
    if (guardedSdkCall([&] {
            injectSdkTaskFault(test, SdkTaskPhase::Submit);
            platform->queueForMainThread_DEPRECATED(run);
        }))
        return true;
    fail("client_task_queue_failed");
    return false;
}
} // namespace probe
