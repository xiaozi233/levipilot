#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

// True while dispatchWithSeh owns the current exception. The crash VEH checks
// this so a deliberately faulting request is handled as an internal_error
// instead of producing a minidump.
thread_local bool gDispatchSehActive = false;

void ProbeMod::recordCommand(std::string const& line) {
    std::size_t const length = line.size() < sizeof(mLastCommand) - 1 ? line.size() : sizeof(mLastCommand) - 1;
    std::memcpy(mLastCommand, line.data(), length);
    mLastCommand[length] = '\0';
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
    recordCommand(line);
    // Any structured exception raised while handling this command belongs to
    // the request, not to the process. The flag must be visible to the crash
    // VEH, which runs before the __except filter below.
    struct DispatchFlag {
        bool previous = gDispatchSehActive;
        DispatchFlag() { gDispatchSehActive = true; }
        ~DispatchFlag() { gDispatchSehActive = previous; }
    } dispatchFlag;
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
    if (j.is_discarded()) {
        enqueue(R"({"event":"error","reason":"bad_json"})");
        return;
    }

    auto cmd = j.value("cmd", std::string{});
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
