#include "mod/Internal.h"

namespace probe {
namespace {
bool runClientTaskWithSeh(std::function<void()> const& task) {
    __try {
        task();
        return true;
    } __except (detail::isFatalSehCode(GetExceptionCode()) ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH) {
        return false;
    }
}
} // namespace

void ProbeMod::queueClientTask(std::function<void()> task) {
    // LL's ClientThreadExecutor is driven by ClientLevelTickEvent, so it does
    // not run at the main menu. The platform queue runs throughout UI lifetime.
    auto platform = ll::service::getAppPlatform();
    if (!platform) {
        enqueue(R"({"event":"error","reason":"no_app_platform"})");
        return;
    }
    platform->queueForMainThread_DEPRECATED([this, task = std::move(task)] {
        if (!mRunning.load(std::memory_order_acquire)) return;
        try {
            if (!runClientTaskWithSeh(task)) {
                enqueue(R"({"event":"error","reason":"internal_error","phase":"client_task","message":"seh"})");
            }
        } catch (std::exception const& error) {
            enqueue(
                nlohmann::json{
                    {"event",   "error"         },
                    {"reason",  "internal_error"},
                    {"phase",   "client_task"   },
                    {"message", error.what()    }
            }.dump()
            );
        } catch (...) {
            enqueue(R"({"event":"error","reason":"internal_error","phase":"client_task","message":"exception"})");
        }
    });
}
} // namespace probe
