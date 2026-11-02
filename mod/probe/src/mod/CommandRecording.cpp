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
        bool        wasPlaying = false;
        if (!loadMacroFrom(path, name, error, wasPlaying)) {
            enqueue(
                nlohmann::json{
                    {"event",  "error"},
                    {"reason", error  }
            }.dump()
            );
            return true;
        }
        std::size_t count = 0;
        {
            std::lock_guard lock(mMacroSessionMutex);
            count = mMacroPlayback.size();
        }
        enqueue(
            nlohmann::json{
                {"event",   "macro_loaded"},
                {"name",    name          },
                {"ticks",   count         },
                {"path",    path          },
                {"stopped", wasPlaying    },
        }
                .dump()
        );
        return true;
    }
    if (cmd == "macro_run") {
        auto const worldEpoch = mPoseEpoch.load();
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
        // Parse before touching session state; bad input preserves the old run.
        int const       requestedStart = j.value("start", 0);
        int const       requestedEnd   = j.value("end", -1);
        bool const      loop           = j.value("loop", false);
        bool const      eventLevel     = j.value("event_level", false);
        std::lock_guard lock(mMacroSessionMutex);
        drainMacroPlaybackLocked(true);
        int const size = static_cast<int>(mMacroPlayback.size());
        if (!size) enqueue(R"({"event":"error","reason":"no_macro_loaded"})");
        else if (mMacroRecording.active()) enqueue(R"({"event":"error","reason":"macro_recording_active"})");
        else if (mMacroPlayback.active()) enqueue(R"({"event":"error","reason":"macro_already_playing"})");
        else {
            int const start    = std::clamp(requestedStart, 0, size - 1);
            int const end      = std::clamp(j.contains("end") ? requestedEnd : size, start + 1, size);
            mMacroPlaybackTest = mMacroReleaseTest = CaptureTest::None;
            mMacroPlayback.begin(start, end, loop, eventLevel);
            if (!mWorld.inWorld() || mPoseEpoch.load() != worldEpoch) mMacroPlayback.cancel(MacroEnd::WorldLeft);
            enqueue(
                nlohmann::json{
                    {"event",       "macro_started"       },
                    {"start",       start                 },
                    {"end",         end                   },
                    {"ticks",       end - start           },
                    {"loop",        loop                  },
                    {"event_level", eventLevel            },
                    {"run_id",      mMacroPlayback.runId()}
            }.dump()
            );
        }
        return true;
    }
    if (cmd == "macro_stop") {
        std::lock_guard lock(mMacroSessionMutex);
        mMacroPlayback.stop(MacroEnd::Client);
        drainMacroPlaybackLocked(true);
        enqueue(
            nlohmann::json{
                {"event",           "macro_stopped"                },
                {"reason",          "client"                       },
                {"run_id",          mMacroPlayback.runId()         },
                {"release_pending", mMacroPlayback.releasePending()}
        }.dump()
        );
        return true;
    }
    if (cmd == "macro_status") {
        std::lock_guard lock(mMacroSessionMutex);
        enqueue(
            nlohmann::json{
                {"event",           "macro_status"                 },
                {"in_world",        mWorld.inWorld()               },
                {"playing",         mMacroPlayback.active()        },
                {"index",           mMacroPlayback.index()         },
                {"loaded",          mMacroPlayback.size() != 0     },
                {"name",            mMacroPlayback.name()          },
                {"ticks",           mMacroPlayback.size()          },
                {"run_id",          mMacroPlayback.runId()         },
                {"release_pending", mMacroPlayback.releasePending()},
                {"recording",       mMacroRecording.active()       },
                {"recorded_ticks",  mMacroRecording.rows()         },
                {"record_limit",    mMacroRecording.limit()        },
                {"record_id",       mMacroRecording.recordId()     }
        }.dump()
        );
        return true;
    }
    if (cmd == "selftest_click_capture") {
        if (!mAllowSelftestStress.load()) enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
        else {
            auto const mode = j.value("mode", std::string{});
            if (mode != "seh" && mode != "cpp") enqueue(R"({"event":"error","reason":"bad_test_mode"})");
            else {
                mClickCaptureTest = mode == "seh" ? CaptureTest::Fault : CaptureTest::Cpp;
                enqueue(R"({"event":"click_capture_test_armed"})");
            }
        }
        return true;
    }
    if (cmd == "selftest_macro_playback") {
        if (!mAllowSelftestStress.load()) {
            enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
            return true;
        }
        auto const mode   = j.value("mode", std::string{});
        auto const target = j.value("target", std::string{"apply"});
        if ((mode != "seh" && mode != "cpp") || (target != "apply" && target != "release")) {
            enqueue(R"({"event":"error","reason":"bad_test_mode"})");
            return true;
        }
        std::lock_guard lock(mMacroSessionMutex);
        if (!mMacroPlayback.active()) enqueue(R"({"event":"error","reason":"not_playing"})");
        else {
            (target == "apply" ? mMacroPlaybackTest : mMacroReleaseTest) =
                mode == "seh" ? CaptureTest::Fault : CaptureTest::Cpp;
            enqueue(R"({"event":"macro_playback_test_armed"})");
        }
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
            if (mMacroPlayback.active()) {
                enqueue(R"({"event":"error","reason":"macro_playing"})");
                return true;
            }
            // Join an auto-stopped final sample and publish its completion
            // before beginning a new generation or replacing frame storage.
            mMacroRecording.stop();
            drainMacroRecordingLocked();
            mMacroRecording.begin(limit, [&] {
                mMacroRecordTest = CaptureTest::None;
                mInputSession.clearMouse(false);
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
