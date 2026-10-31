#include "mod/Internal.h"
#include "mod/MacroRecordModel.h"

using namespace probe::detail;

namespace probe {

bool ProbeMod::dispatchRecordingCommand(std::string const& cmd, nlohmann::json& j) {
    if (cmd == "start") {
        if (j.contains("max_ticks") && (!j["max_ticks"].is_number_integer()
            || (!j["max_ticks"].is_number_unsigned() && j["max_ticks"].get<std::int64_t>() < 0))) {
            enqueue(R"({"event":"error","reason":"bad_param"})");
            return true;
        }
        auto const maxTicks = j.value("max_ticks", std::uint64_t{0});
        if (!mWorld.inWorld()) enqueue(R"({"event":"error","reason":"not_in_world"})");
        else if (!hooksAllowed()) enqueue(R"({"event":"error","reason":"version_mismatch"})");
        else if (!startLegacy(maxTicks)) enqueue(R"({"event":"error","reason":"recording_busy"})");
        return true;
    }
    if (cmd == "stop") {
        stopLegacy(StreamEnd::Client, true);
        flushSpool(true);
        return true;
    }
    if (cmd == "selftest_tick_stream") {
        if (!mAllowSelftestStress.load()) {
            enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
            return true;
        }
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        auto const mode = j.value("mode", std::string{});
        StreamTest test = StreamTest::None;
        if (mode == "seh") test = StreamTest::Fault;
        else if (mode == "cpp") test = StreamTest::Cpp;
        else if (mode != "overflow") {
            enqueue(R"({"event":"error","reason":"bad_test_mode"})");
            return true;
        }
        auto const target = j.value("target", std::string{"legacy"});
        if (target == "parkour" && test != StreamTest::None) {
            mParkourTest.store(test);
            enqueue(R"({"event":"tick_stream_test_armed"})");
        } else if (target == "legacy") {
            if (!startLegacy(0, mode == "overflow" ? 3 : 256, test, mode == "overflow" ? 1000 : 0))
                enqueue(R"({"event":"error","reason":"recording_busy"})");
        } else enqueue(R"({"event":"error","reason":"bad_test_target"})");
        return true;
    }
    if (cmd == "record_start") {
        auto const worldEpoch = mPoseEpoch.load();
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        if (!hooksAllowed()) {
            enqueue(R"({"event":"error","reason":"version_mismatch"})");
            return true;
        }
        std::uint32_t const           period = std::clamp(j.value("period", 1), 1, 200);
        std::string                   error;
        std::optional<nlohmann::json> previous;
        bool const                    started = recordStart(period, error, previous);
        if (previous) enqueue(previous->dump());
        if (!started) {
            enqueue(
                nlohmann::json{
                    {"event",  "error"},
                    {"reason", error  },
            }
                    .dump()
            );
            return true;
        }
        // Exit can run while the cold start joins a preceding capture or
        // prepares storage. The pose epoch advances on every world exit.
        if (!mWorld.inWorld() || mPoseEpoch.load() != worldEpoch) recordCancel(RecordEnd::WorldLeft);
        auto out          = recordStateJson();
        out["event"]      = "record_started";
        out["capacity"]   = RecordCapacity;
        out["entry_size"] = RecordSize;
        enqueue(out.dump());
        return true;
    }
    if (cmd == "record_stop") {
        recordStop();
        auto out     = recordStateJson();
        out["event"] = "record_stopped";
        enqueue(out.dump());
        return true;
    }
    if (cmd == "selftest_record_capture") {
        if (!mAllowSelftestStress.load()) {
            enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
            return true;
        }
        auto const mode = j.value("mode", std::string{});
        if (mode != "seh" && mode != "cpp") enqueue(R"({"event":"error","reason":"bad_test_mode"})");
        else if (!recordArmTest(mode == "seh" ? CaptureTest::Fault : CaptureTest::Cpp))
            enqueue(R"({"event":"error","reason":"not_recording"})");
        else enqueue(R"({"event":"record_capture_test_armed"})");
        return true;
    }
    if (cmd == "record_dump") {
        std::uint64_t const since =
            j.contains("since") ? j.value("since", std::uint64_t{0}) : std::uint64_t{0};
        std::size_t const limit = std::clamp<std::size_t>(j.value("limit", 1024), 1, 8192);
        std::string const format = j.value("format", std::string{"jsonl"});
        bool const        binary = format == "bin" || format == "hex" || format == "binary";
        enqueue(recordDumpJson(since, limit, binary).dump());
        return true;
    }
    if (cmd == "macro_load") {
        std::string name = j.value("name", std::string{"default"});
        std::string path = j.value("path", std::string{});
        if (path.empty()) {
            path = macroPath(name).string();
        }
        std::string error;
        if (!loadMacroFrom(path, name, error)) {
            enqueue(
                nlohmann::json{
                    {"event",  "error"},
                    {"reason", error  }
            }.dump()
            );
            return true;
        }
        // Swapping the macro under a running (or just-requested) playback
        // would leave index/end pointing past the new, possibly much shorter
        // buffer. Clear a pending start too: the tick consumes it after this
        // returns and would restart playback on the new macro otherwise.
        bool wasPlaying = false;
        bool wasPending = false;
        {
            std::lock_guard sessionLock(mMacroSessionMutex);
            wasPlaying = mMacroPlaying.exchange(false);
            wasPending = mMacroStartRequested.exchange(false);
            if (wasPlaying) {
                mMacroStopRequested = true;
            }
        }
        size_t count = 0;
        {
            std::lock_guard lock(mMacroMutex);
            count = mMacro ? mMacro->size() : 0;
        }
        enqueue(
            nlohmann::json{
                {"event",   "macro_loaded"},
                {"name",    name          },
                {"ticks",   count         },
                {"path",    path          },
                {"stopped", wasPlaying || wasPending},
        }
            .dump()
        );
        return true;
    }
    if (cmd == "macro_run") {
        std::shared_ptr<Macro> macro;
        {
            std::lock_guard lock(mMacroMutex);
            macro = mMacro;
        }
        if (!macro || macro->empty()) {
            enqueue(R"({"event":"error","reason":"no_macro_loaded"})");
            return true;
        }
        int start = std::clamp(j.value("start", 0), 0, static_cast<int>(macro->size()) - 1);
        int end   = j.value("end", static_cast<int>(macro->size()));
        end       = std::clamp(end, start + 1, static_cast<int>(macro->size()));
        bool recording = false;
        bool playing   = false;
        {
            std::lock_guard sessionLock(mMacroSessionMutex);
            recording = mMacroRecording.active();
            playing   = mMacroPlaying.load(std::memory_order_relaxed)
                     || mMacroStartRequested.load(std::memory_order_relaxed);
            if (!recording && !playing) {
                mMacroStart.store(start);
                mMacroEnd.store(end);
                mMacroIndex.store(start);
                mMacroLoop.store(j.value("loop", false));
                mMacroEventLevel   = j.value("event_level", false);
                mHavePrevMacroTick = false;
                mMacroStartRequested.store(true, std::memory_order_release);
            }
        }
        if (recording || playing) {
            enqueue(recording ? R"({"event":"error","reason":"macro_recording_active"})"
                              : R"({"event":"error","reason":"macro_already_playing"})");
            return true;
        }
        enqueue(
            nlohmann::json{
                {"event",       "macro_started"},
                {"start",       start          },
                {"end",         end            },
                {"ticks",       end - start    },
                {"loop",        mMacroLoop.load()},
                {"event_level", mMacroEventLevel.load()},
        }
                .dump()
        );
        return true;
    }
    if (cmd == "macro_stop") {
        // Also drop a pending start so a stop that races macro_run cannot be
        // overwritten by the start block re-enabling playback next tick.
        {
            std::lock_guard sessionLock(mMacroSessionMutex);
            mMacroStartRequested = false;
            mMacroPlaying        = false;
            mMacroStopRequested  = true;
        }
        enqueue(R"({"event":"macro_stopped","reason":"client"})");
        return true;
    }
    if (cmd == "macro_status") {
        nlohmann::json out;
        out["event"]          = "macro_status";
        out["in_world"]       = mWorld.inWorld();
        out["playing"]        = mMacroPlaying.load();
        out["index"]          = mMacroIndex.load();
        out["recording"]      = mMacroRecording.active();
        out["recorded_ticks"] = 0;
        {
            std::lock_guard lock(mMacroMutex);
            out["loaded"] = static_cast<bool>(mMacro);
            out["name"]   = mMacroName;
            out["ticks"]  = mMacro ? mMacro->size() : 0;
        }
        {
            std::lock_guard lock(mMacroSessionMutex);
            out["recorded_ticks"] = mMacroRecording.rows();
            out["record_limit"]   = mMacroRecording.limit();
            out["record_id"]      = mMacroRecording.recordId();
        }
        enqueue(out.dump());
        return true;
    }
    if (cmd == "macro_list") {
        nlohmann::json out;
        out["event"]  = "macro_list";
        out["macros"] = nlohmann::json::array();
        std::error_code ec;
        for (auto const& entry : std::filesystem::directory_iterator(macroDir(), ec)) {
            if (entry.is_regular_file() && entry.path().extension() == ".json") {
                out["macros"].push_back(entry.path().stem().string());
            }
        }
        enqueue(out.dump());
        return true;
    }
    if (cmd == "selftest_macro_capture") {
        if (!mAllowSelftestStress.load()) {
            enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
            return true;
        }
        auto const mode = j.value("mode", std::string{});
        if (mode != "seh" && mode != "cpp") {
            enqueue(R"({"event":"error","reason":"bad_test_mode"})");
            return true;
        }
        std::lock_guard lock(mMacroSessionMutex);
        if (!mMacroRecording.active()) enqueue(R"({"event":"error","reason":"not_recording"})");
        else {
            mMacroRecordTest = mode == "seh" ? CaptureTest::Fault : CaptureTest::Cpp;
            enqueue(R"({"event":"macro_capture_test_armed"})");
        }
        return true;
    }
    if (cmd == "macro_rec_start") {
        // Validate and reserve before replacing a buffered session. A bad
        // parameter/allocation must not silently discard its recorded rows.
        auto const value = j.value("max_ticks", nlohmann::json(0));
        if (!value.is_number_integer()
            || (!value.is_number_unsigned() && value.get<std::int64_t>() < 0)) {
            enqueue(R"({"event":"error","reason":"bad_param","field":"max_ticks"})");
            return true;
        }
        auto const limit = macroRecordingLimit(value.get<std::uint64_t>());
        if (!mWorld.inWorld() || !hooksAllowed()) {
            enqueue(
                nlohmann::json{
                    {"event",  "error"                                               },
                    {"reason", mWorld.inWorld() ? "version_mismatch" : "not_in_world"}
            }
                    .dump()
            );
            return true;
        }
        {
            std::lock_guard lock(mMacroSessionMutex);
            drainMacroRecordingLocked();
            if (mMacroRecording.active()) {
                enqueue(R"({"event":"error","reason":"macro_already_recording"})");
                return true;
            }
            if (mMacroPlaying.load(std::memory_order_acquire)
                || mMacroStartRequested.load(std::memory_order_acquire)) {
                enqueue(R"({"event":"error","reason":"macro_playing"})");
                return true;
            }
            // Join an auto-stopped final sample and publish its completion
            // before beginning a new generation or replacing frame storage.
            mMacroRecording.stop();
            drainMacroRecordingLocked();
            mMacroRecording.begin(limit, [&] {
                mMacroRecordTest = CaptureTest::None;
                mMouseLeftLatch  = false;
                mMouseRightLatch = false;
                mMouseLeft       = false;
                mMouseRight      = false;
                mPrevActionLmb   = false;
                mPrevActionRmb   = false;
            });
            enqueue(
                nlohmann::json{
                    {"event",      "macro_rec_started"       },
                    {"max_ticks",  limit                     },
                    {"hard_limit", MaxMacroRecordedTicks     },
                    {"record_id",  mMacroRecording.recordId()}
            }.dump()
            );
        }
        return true;
    }
    if (cmd == "macro_rec_stop") {
        std::string name = j.value("name", std::string{"recorded"});
        MacroRecordSession::Snapshot snapshot;
        {
            std::lock_guard lock(mMacroSessionMutex);
            drainMacroRecordingLocked();
            mMacroRecording.stop();
            drainMacroRecordingLocked();
            snapshot = mMacroRecording.snapshot();
        }
        auto const& rows = snapshot.rows;
        auto const& done = snapshot.completion;
        if (rows.empty()) {
            enqueue(
                nlohmann::json{
                    {"event",     "error"                                                                            },
                    {"phase",     "macro_record"                                                                     },
                    {"record_id", done.recordId                                                                      },
                    {"reason",    done.recordId && !done.ok() ? macroRecordEndName(done.reason) : "no_recorded_ticks"}
            }
                    .dump()
            );
            return true;
        }
        std::string error;
        auto        path = macroPath(name);
        if (!saveMacroFile(path, rows, error)) {
            enqueue(
                nlohmann::json{
                    {"event",  "error"},
                    {"reason", error  }
            }.dump()
            );
            return true;
        }
        {
            std::lock_guard lock(mMacroSessionMutex);
            mMacroRecording.clear(done.recordId);
        }
        enqueue(
            nlohmann::json{
                {"event",     "macro_rec_saved"              },
                {"name",      name                           },
                {"ticks",     rows.size()                    },
                {"path",      path.string()                  },
                {"record_id", done.recordId                  },
                {"ok",        done.ok()                      },
                {"partial",   !done.ok()                     },
                {"reason",    macroRecordEndName(done.reason)},
        }
                .dump()
        );
        return true;
    }
    if (cmd == "macro_save") {
        Macro       rows;
        std::string error;
        if (!j.contains("ticks") || !parseMacroJson(j["ticks"], rows, error)) {
            enqueue(
                nlohmann::json{
                    {"event",  "error"                                },
                    {"reason", error.empty() ? "missing ticks" : error}
            }.dump()
            );
            return true;
        }
        std::string name = j.value("name", std::string{"saved"});
        auto        path = macroPath(name);
        if (!saveMacroFile(path, rows, error)) {
            enqueue(
                nlohmann::json{
                    {"event",  "error"},
                    {"reason", error  }
            }.dump()
            );
            return true;
        }
        enqueue(
            nlohmann::json{
                {"event", "macro_saved"},
                {"name",  name         },
                {"ticks", rows.size()  },
                {"path",  path.string()},
        }
                .dump()
        );
        return true;
    }
    return false;
}

} // namespace probe
