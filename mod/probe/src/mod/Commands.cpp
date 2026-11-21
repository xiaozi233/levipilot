#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

// Shared by request/capture/client/server SEH boundaries and the crash VEH.
thread_local bool gDispatchSehActive = false;

void ProbeMod::recordCommand(std::string const& line) {
    unsigned const   next   = 1u - (mLastCommandSlot.load(std::memory_order_relaxed) & 1u);
    std::size_t const length = line.size() < sizeof(mLastCommand[next]) - 1 ? line.size() : sizeof(mLastCommand[next]) - 1;
    std::memcpy(mLastCommand[next], line.data(), length);
    mLastCommand[next][length] = '\0';
    mLastCommandSlot.store(next, std::memory_order_release);
}

bool ProbeMod::dispatchWithSeh(std::string const& line) {
#if defined(_MSC_VER)
    // A bad memory access from one handler must only fail that request. The
    // filter lets C++ exceptions through to the caller's try/catch.
    __try {
        dispatchLine(line);
        return true;
    } __except (isFatalSehCode(GetExceptionCode()) ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH) {
        return false;
    }
#else
    try {
        dispatchLine(line);
        return true;
    } catch (...) {
        return false;
    }
#endif
}

void ProbeMod::handleLine(std::string const& line) {
    CallbackScope callback(gCallbacks, CallbackDomain::Request);
    std::lock_guard recovery(mTickRecoveryMutex);
    recordCommand(line);
    // Any structured exception raised while handling this command belongs to
    // the request, not to the process. The flag must be visible to the crash
    // VEH, which runs before the __except filter below.
    HandledSehScope dispatchFlag;
    ActiveReqIdScope reqScope(0);
    // nlohmann throws on type mismatches, and a bad line from a script must not
    // kill the pipe thread (which would make the whole harness unreachable).
    try {
        if (!dispatchWithSeh(line)) {
            enqueue(
                R"({"event":"error","reason":"internal_error","phase":"dispatch","message":"structured exception"})"
            );
        }
    } catch (std::exception const& e) {
        enqueue(
            nlohmann::json{
                {"event",   "error"      },
                {"reason",  "bad_command"},
                {"message", e.what()     },
        }
                .dump()
        );
    } catch (...) {
        enqueue(R"({"event":"error","reason":"bad_command","message":"unknown exception"})");
    }
}

void ProbeMod::dispatchLine(std::string const& line) {
    auto j = nlohmann::json::parse(line, nullptr, false);
    if (j.is_discarded() || !j.is_object()) {
        enqueue(R"({"event":"error","reason":"bad_json"})");
        return;
    }
    if (auto const it = j.find("req_id"); it != j.end()) {
        if (it->is_number_unsigned()) {
            auto const reqId = it->get<std::uint64_t>();
            if (reqId > 0) setActiveReqId(reqId);
        } else if (it->is_number_integer()) {
            auto const reqId = it->get<std::int64_t>();
            if (reqId > 0) setActiveReqId(static_cast<std::uint64_t>(reqId));
        }
    }

    auto cmd = j.value("cmd", std::string{});
    if (gCallbacks.closed() && cmd != "status" && cmd != "ping") {
        enqueue(R"({"event":"error","reason":"quiescing"})");
        return;
    }
    if (!mPipeAuthed.load(std::memory_order_acquire) && cmd != "auth" && cmd != "ping") {
        enqueue(R"({"event":"error","reason":"auth_required"})");
        return;
    }
    if (cmd == "auth") {
        auto const token = safeJsonString(j, "token", "");
        bool       ok    = mPipeToken.empty();
        if (!ok && token.size() == mPipeToken.size()) {
            unsigned char diff = 0;
            for (std::size_t i = 0; i < token.size(); ++i) {
                diff |= static_cast<unsigned char>(token[i]) ^ static_cast<unsigned char>(mPipeToken[i]);
            }
            ok = (diff == 0);
        }
        if (ok) {
            mPipeAuthed.store(true, std::memory_order_release);
            enqueue(nlohmann::json{{"event", "auth_result"}, {"ok", true}}.dump());
        } else {
            mPipeAuthed.store(false, std::memory_order_release);
            bootLog("pipe: auth failed");
            enqueue(R"({"event":"error","reason":"bad_token"})");
        }
        return;
    }
    if (mTickRecovery.active() && blockedDuringTickRecovery(cmd)) {
        enqueue(R"({"event":"error","reason":"tick_recovering"})");
        return;
    }
    // Quiesced for unload: no new work may be admitted between the last
    // in-flight request and the moment the hooks come off. Only the lifecycle
    // switch, liveness and the emergency stop stay reachable.
    if (mQuiescing.load(std::memory_order_acquire) && !quiesceExempt(cmd)) {
        enqueue(R"({"event":"error","reason":"quiescing"})");
        return;
    }
    // Each domain returns true only after consuming the command. Keep all
    // handlers under dispatchWithSeh, including their early error responses.
    if (dispatchDiagnosticsCommand(cmd, j)
        || dispatchInspectionCommand(cmd, j)
        || dispatchInputCommand(cmd, j)
        || dispatchPacketsCommand(cmd, j)
        || dispatchRecordingCommand(cmd, j)
        || dispatchQueryCommand(cmd, j)
        || dispatchWorldCommand(cmd, j)
        || dispatchTasksCommand(cmd, j)) {
        return;
    }
    enqueue(R"({"event":"error","reason":"unknown_cmd"})");
}


} // namespace probe
