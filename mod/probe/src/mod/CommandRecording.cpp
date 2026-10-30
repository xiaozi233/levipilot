#include "mod/Internal.h"
#include "mod/MacroRecordModel.h"

using namespace probe::detail;

namespace probe {

bool ProbeMod::dispatchRecordingCommand(std::string const& cmd, nlohmann::json& j) {
    if (cmd == "start") {
        mTick      = 0;
        mMaxTicks  = j.value("max_ticks", std::uint64_t{0});
        mRecording = true;
        enqueue(R"({"event":"started"})");
        return true;
    }
    if (cmd == "stop") {
        mRecording = false;
        flushSpool(true);
        enqueue(R"({"event":"stopped","reason":"client"})");
        return true;
    }
    if (cmd == "record_start") {
        if (!hooksAllowed()) {
            enqueue(R"({"event":"error","reason":"version_mismatch"})");
            return true;
        }
        std::uint32_t const period = std::clamp(j.value("period", 1), 1, 200);
        std::string         error;
        if (!recordStart(period, error)) {
            enqueue(
                nlohmann::json{
                    {"event",  "error"      },
                    {"reason", error        },
            }
                    .dump()
            );
            return true;
        }
        enqueue(
            nlohmann::json{
                {"event",     "record_started"  },
                {"period",    period            },
                {"capacity",  RecordCapacity    },
                {"entry_size", RecordSize       },
        }
                .dump()
        );
        return true;
    }
    if (cmd == "record_stop") {
        recordStop();
        enqueue(
            nlohmann::json{
                {"event",  "record_stopped"  },
                {"rows",   recordRows()      },
                {"head",   recordHead()      },
                {"period", recordPeriod()    },
        }
                .dump()
        );
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
            recording = mMacroRecording.load(std::memory_order_relaxed);
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
        out["recording"]      = mMacroRecording.load();
        out["recorded_ticks"] = 0;
        {
            std::lock_guard lock(mMacroMutex);
            out["loaded"] = static_cast<bool>(mMacro);
            out["name"]   = mMacroName;
            out["ticks"]  = mMacro ? mMacro->size() : 0;
        }
        {
            std::lock_guard lock(mMacroSessionMutex);
            out["recorded_ticks"] = mRecordedMacro.size();
            out["record_limit"] = macroRecordingLimit(mMacroRecordMax.load(std::memory_order_relaxed));
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
        {
            std::lock_guard lock(mMacroSessionMutex);
            if (mMacroRecording.load(std::memory_order_relaxed)) {
                enqueue(R"({"event":"error","reason":"macro_already_recording"})");
                return true;
            }
            if (mMacroPlaying.load(std::memory_order_acquire)
                || mMacroStartRequested.load(std::memory_order_acquire)) {
                enqueue(R"({"event":"error","reason":"macro_playing"})");
                return true;
            }
            mRecordedMacro.reserve(static_cast<std::size_t>(limit));
            mRecordedMacro.clear();
            ++mMacroRecordGeneration;
            mMacroRecordMax = limit;
            mHavePrevRotation = false;
            mMouseLeftLatch   = false;
            mMouseRightLatch  = false;
            mMouseLeft        = false;
            mMouseRight       = false;
            mPrevActionLmb    = false;
            mPrevActionRmb    = false;
            mMacroRecording   = true;
        }
        enqueue(nlohmann::json{
            {"event", "macro_rec_started"}, {"max_ticks", limit}, {"hard_limit", MaxMacroRecordedTicks}
        }.dump());
        return true;
    }
    if (cmd == "macro_rec_stop") {
        std::string name = j.value("name", std::string{"recorded"});
        Macro       rows;
        std::uint64_t generation = 0;
        {
            std::lock_guard lock(mMacroSessionMutex);
            mMacroRecording = false;
            generation = mMacroRecordGeneration;
            rows = mRecordedMacro;
        }
        if (rows.empty()) {
            enqueue(R"({"event":"error","reason":"no_recorded_ticks"})");
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
            if (mMacroRecordGeneration == generation) {
                mRecordedMacro.clear();
            }
        }
        enqueue(
            nlohmann::json{
                {"event", "macro_rec_saved"},
                {"name",  name             },
                {"ticks", rows.size()      },
                {"path",  path.string()    },
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
