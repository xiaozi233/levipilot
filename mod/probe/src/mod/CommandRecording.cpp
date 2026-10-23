#include "mod/Internal.h"

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
        size_t count = 0;
        {
            std::lock_guard lock(mMacroMutex);
            count = mMacro ? mMacro->size() : 0;
        }
        enqueue(
            nlohmann::json{
                {"event", "macro_loaded"},
                {"name",  name          },
                {"ticks", count         },
                {"path",  path          },
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
        if (mMacroPlaying.load()) {
            enqueue(R"({"event":"error","reason":"macro_already_playing"})");
            return true;
        }
        int start = std::clamp(j.value("start", 0), 0, static_cast<int>(macro->size()) - 1);
        int end   = j.value("end", static_cast<int>(macro->size()));
        end       = std::clamp(end, start + 1, static_cast<int>(macro->size()));
        mMacroStart.store(start);
        mMacroEnd.store(end);
        mMacroIndex.store(start);
        mMacroLoop.store(j.value("loop", false));
        mMacroEventLevel     = j.value("event_level", false);
        mHavePrevMacroTick   = false;
        mMacroStartRequested = true;
        enqueue(
            nlohmann::json{
                {"event",       "macro_started"},
                {"start",       start          },
                {"end",         end            },
                {"ticks",       end - start    },
                {"loop",        mMacroLoop.load()},
                {"event_level", mMacroEventLevel},
        }
                .dump()
        );
        return true;
    }
    if (cmd == "macro_stop") {
        mMacroPlaying       = false;
        mMacroStopRequested = true;
        enqueue(R"({"event":"macro_stopped","reason":"client"})");
        return true;
    }
    if (cmd == "macro_status") {
        nlohmann::json out;
        out["event"]          = "macro_status";
        out["in_world"]       = mInWorld.load();
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
            std::lock_guard lock(mRecordMutex);
            out["recorded_ticks"] = mRecordedMacro.size();
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
        {
            std::lock_guard lock(mRecordMutex);
            mRecordedMacro.clear();
        }
        mHavePrevRotation = false;
        mMouseLeftLatch   = false;
        mMouseRightLatch  = false;
        mMouseLeft        = false;
        mMouseRight       = false;
        mPrevActionLmb    = false;
        mPrevActionRmb    = false;
        mMacroRecordMax   = j.value("max_ticks", std::uint64_t{0});
        mMacroRecording   = true;
        enqueue(R"({"event":"macro_rec_started"})");
        return true;
    }
    if (cmd == "macro_rec_stop") {
        std::string name = j.value("name", std::string{"recorded"});
        Macro       rows;
        {
            std::lock_guard lock(mRecordMutex);
            rows = mRecordedMacro;
            mRecordedMacro.clear();
        }
        mMacroRecording = false;
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
