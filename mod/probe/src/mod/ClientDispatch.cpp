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

bool runGuardedClientTask(std::function<void()> const& task) {
    detail::HandledSehScope sehOwner;
    return runClientTaskWithSeh(task);
}
} // namespace

bool ProbeMod::queueClientTask(std::function<void()> task, std::function<void(char const*)> onFailure) {
    auto tracked = detail::trackedCallback(detail::gCallbacks, detail::CallbackDomain::Client, [this, task = std::move(task), onFailure] {
        if (!mRunning.load(std::memory_order_acquire)) return;
        try {
            if (!runGuardedClientTask(task)) {
                if (onFailure) onFailure("client_task_failed");
                else enqueue(R"({"event":"error","reason":"internal_error","phase":"client_task","message":"seh"})");
            }
        } catch (std::exception const& error) {
            if (onFailure) onFailure("client_task_failed");
            else enqueue(nlohmann::json{{"event", "error"}, {"reason", "internal_error"},
                                        {"phase", "client_task"}, {"message", error.what()}}.dump());
        } catch (...) {
            if (onFailure) onFailure("client_task_failed");
            else enqueue(R"({"event":"error","reason":"internal_error","phase":"client_task","message":"exception"})");
        }
    });
    if (!tracked) {
        if (onFailure) onFailure("quiescing");
        else enqueue(R"({"event":"error","reason":"quiescing"})");
        return false;
    }
    // LL's ClientThreadExecutor is driven by ClientLevelTickEvent, so it does
    // not run at the main menu. The platform queue runs throughout UI lifetime.
    auto platform = ll::service::getAppPlatform();
    if (!platform) {
        if (onFailure) onFailure("no_app_platform");
        else enqueue(R"({"event":"error","reason":"no_app_platform"})");
        return false;
    }
    std::function<void()> run = std::move(*tracked);
    std::function<void()> submit = [&] { platform->queueForMainThread_DEPRECATED(run); };
    try {
        if (runGuardedClientTask(submit)) return true;
    } catch (...) {}
    if (onFailure) onFailure("client_task_queue_failed");
    else enqueue(R"({"event":"error","reason":"client_task_queue_failed"})");
    return false;
}
} // namespace probe
