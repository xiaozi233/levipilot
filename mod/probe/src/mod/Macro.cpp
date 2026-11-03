#include "mod/Internal.h"
#include "mod/MacroRecordModel.h"

using namespace probe::detail;

namespace probe {

void ProbeMod::recordMacroTick(ClientInstance& client, LocalPlayer& player) {
    mMacroSession.recording.tick([&](MacroRecordSession::Sample& sample) {
        return guardedGameRead([&] {
            injectCaptureFault(mMacroSession.recordTest.exchange(CaptureTest::None));
            auto const in  = readInput(client);
            auto const rot = player.getRotation();
            auto&      row = sample.buttons;
            row.w          = in.w;
            row.a          = in.a;
            row.s          = in.s;
            row.d          = in.d;
            row.sprint     = in.sprint;
            row.sneak      = in.sneak;
            row.jump       = in.jump;
            // Always consume a latch, even while the corresponding button is
            // held, so releasing a held click cannot replay an old press.
            row.rmb      = consumeMacroClick(mInputSession.mouseRight, mInputSession.mouseRightLatch);
            row.lmb      = consumeMacroClick(mInputSession.mouseLeft, mInputSession.mouseLeftLatch);
            sample.yaw   = rot.y;
            sample.pitch = rot.x;
        });
    });
}

void ProbeMod::drainMacroRecordingLocked() {
    if (mMacroSession.recordWorldLeft.exchange(false)) mMacroSession.recording.stop(MacroRecordEnd::WorldLeft);
    if (auto done = mMacroSession.recording.takeCompletion()) {
        if (done->reason == MacroRecordEnd::Client) return; // Save is its normal completion receipt.
        enqueue(
            nlohmann::json{
                {"event",     done->reason == MacroRecordEnd::Limit ? "macro_rec_limit" : "macro_rec_stopped"},
                {"record_id", done->recordId                                                                 },
                {"ticks",     done->rows                                                                     },
                {"ok",        done->ok()                                                                     },
                {"reason",    macroRecordEndName(done->reason)                                               }
        }.dump()
        );
    }
}

void ProbeMod::drainMacroRecording() {
    std::lock_guard lock(mMacroSession.mutex);
    drainMacroRecordingLocked();
}

void ProbeMod::drainMacroPlaybackLocked(bool wait) {
    auto const errors = mMacroSession.playback.takeReleaseErrors();
    if (errors.fault || errors.cpp)
        enqueue(
            nlohmann::json{
                {"event",      "macro_release_diagnostic"},
                {"faults",     errors.fault              },
                {"exceptions", errors.cpp                }
        }.dump()
        );
    if (auto done = mMacroSession.playback.takeCompletion(wait)) {
        // These cancellations already have their command/task receipt.
        if (done->ok() && done->reason != MacroEnd::End) return;
        enqueue(
            nlohmann::json{
                {"event",  "macro_done"              },
                {"name",   mMacroSession.playback.name()     },
                {"reason", macroEndName(done->reason)},
                {"index",  done->index               },
                {"run_id", done->runId               },
                {"ok",     done->ok()                }
        }.dump()
        );
    }
}

void ProbeMod::drainMacroPlayback() {
    std::lock_guard lock(mMacroSession.mutex);
    drainMacroPlaybackLocked();
}

void ProbeMod::releaseMacroIfRequested() {
    mMacroSession.playback.releaseRequested([&] {
        bool released = false;
        return guardedGameRead([&] {
                   injectCaptureFault(mMacroSession.releaseTest.exchange(CaptureTest::None));
                   released = releaseMacroInputs();
               })
            && released;
    });
    // SEH can bypass injection guards inside game APIs. Restore the harness
    // flags after returning through the local boundary, including failed releases.
    mInputSession.injecting = false;
    mInputSession.injectingClick = false;
}

void ProbeMod::tickMacroPlayback(ClientInstance& client, LocalPlayer& player) {
    mMacroSession.playback.tick(
        [&](MacroTick const& row, bool eventLevel, bool first) {
            if (first) {
                mInputSession.prevSprint = mInputSession.prevSneak = false;
                mInputSession.prevMacroRmb = mInputSession.prevMacroLmb = false;
                mInputSession.injected = false;
            }
            bool applied = false;
            return guardedGameRead([&] {
                       injectCaptureFault(mMacroSession.playbackTest.exchange(CaptureTest::None));
                       applied = applyMacroTick(client, player, row, eventLevel);
                   })
                && applied;
        },
        [&] {
            bool released = false;
            return guardedGameRead([&] {
                       injectCaptureFault(mMacroSession.releaseTest.exchange(CaptureTest::None));
                       released = releaseMacroInputs();
                   })
                && released;
        }
    );
    mInputSession.injecting = false;
    mInputSession.injectingClick = false;
}

std::filesystem::path ProbeMod::macroDir() const { return getSelf().getModDir() / "macros"; }

std::filesystem::path ProbeMod::macroPath(std::string const& name) const { return macroDir() / (name + ".json"); }

bool ProbeMod::loadMacroFrom(std::string const& path, std::string const& name, std::string& error, bool& stopped) {
    Macro macro;
    auto  file = std::filesystem::path{path};
    if (!std::filesystem::exists(file)) {
        if (path.find('/') != std::string::npos || path.find('\\') != std::string::npos) {
            error = "macro file does not exist: " + path;
            return false;
        }
        file = macroPath(path);
    }
    if (!parseMacroFile(file, macro, error)) {
        return false;
    }
    if (macro.empty()) {
        error = "macro is empty";
        return false;
    }

    auto            loadedName = name.empty() ? file.stem().string() : name;
    std::lock_guard lock(mMacroSession.mutex);
    stopped = mMacroSession.playback.active();
    mMacroSession.playback.stop(MacroEnd::Loaded);
    drainMacroPlaybackLocked(true);
    mMacroSession.playback.load(std::move(macro), std::move(loadedName));
    return true;
}

bool ProbeMod::parseMacroJson(nlohmann::json const& value, Macro& out, std::string& error) {
    if (!value.is_array()) {
        error = "macro must be a top-level array";
        return false;
    }
    Macro macro;
    macro.reserve(value.size());
    for (size_t i = 0; i < value.size(); ++i) {
        auto const& row = value[i];
        if (!row.is_array()) {
            error = "tick " + std::to_string(i) + " is not an array";
            return false;
        }
        if (row.size() != 9 && row.size() != 11) {
            error = "tick " + std::to_string(i) + " must contain 9 or 11 strings";
            return false;
        }

        MacroTick tick;
        bool*     buttons[9] =
            {&tick.w, &tick.a, &tick.s, &tick.d, &tick.sprint, &tick.sneak, &tick.jump, &tick.rmb, &tick.lmb};
        for (size_t b = 0; b < 9; ++b) {
            if (!parseBool(row[b], *buttons[b])) {
                error = "invalid button value in tick " + std::to_string(i);
                return false;
            }
        }
        if (row.size() == 11) {
            if (!row[9].is_string() || !row[10].is_string()) {
                error = "yaw/pitch must be strings in tick " + std::to_string(i);
                return false;
            }
            if (!parseFloat(row[9].get<std::string>(), tick.yaw)
                || !parseFloat(row[10].get<std::string>(), tick.pitch)) {
                error = "invalid yaw/pitch in tick " + std::to_string(i);
                return false;
            }
        }
        macro.push_back(tick);
    }
    out = std::move(macro);
    return true;
}

bool ProbeMod::macroToJson(Macro const& macro, nlohmann::json& out, std::string& error) {
    out = nlohmann::json::array();
    for (auto const& tick : macro) {
        if (!std::isfinite(tick.yaw) || !std::isfinite(tick.pitch)) {
            error = "macro angle must be finite";
            return false;
        }
        out.push_back(
            nlohmann::json::array({
                tick.w ? "true" : "false",
                tick.a ? "true" : "false",
                tick.s ? "true" : "false",
                tick.d ? "true" : "false",
                tick.sprint ? "true" : "false",
                tick.sneak ? "true" : "false",
                tick.jump ? "true" : "false",
                tick.rmb ? "true" : "false",
                tick.lmb ? "true" : "false",
                formatFloat(tick.yaw),
                formatFloat(tick.pitch),
            })
        );
    }
    return true;
}

bool ProbeMod::parseMacroFile(std::filesystem::path const& path, Macro& out, std::string& error) {
    std::ifstream file(path, std::ios::binary);
    if (!file) {
        error = "cannot open " + path.string();
        return false;
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    auto value = nlohmann::json::parse(buffer.str(), nullptr, false);
    if (value.is_discarded()) {
        error = "invalid JSON in " + path.string();
        return false;
    }
    return parseMacroJson(value, out, error);
}

bool ProbeMod::saveMacroFile(std::filesystem::path const& path, Macro const& macro, std::string& error) {
    nlohmann::json value;
    if (!macroToJson(macro, value, error)) {
        return false;
    }
    auto const data = value.dump(2);
    static std::atomic<std::uint64_t> tempSerial{0};
    auto temp = path;
    temp += ".tmp-" + std::to_string(GetCurrentProcessId()) + "-"
        + std::to_string(tempSerial.fetch_add(1, std::memory_order_relaxed) + 1);
    auto fail = [&](char const* action) {
        error = "cannot " + std::string(action) + " " + path.string();
        std::error_code cleanup;
        std::filesystem::remove(temp, cleanup);
        return false;
    };
    std::error_code ec;
    std::filesystem::create_directories(path.parent_path(), ec);
    if (ec) return fail("create directory for");
    std::ofstream file(temp, std::ios::binary | std::ios::trunc);
    if (!file) return fail("open");
    file.write(data.data(), static_cast<std::streamsize>(data.size()));
    file.flush();
    file.close();
    if (!file) return fail("write");
    if (!MoveFileExW(temp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        return fail("replace");
    return true;
}


} // namespace probe
