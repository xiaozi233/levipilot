#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

namespace {
constexpr std::uint64_t MaxRecordedTicks = 200000;
} // namespace

void ProbeMod::recordMacroTick(ClientInstance& client, LocalPlayer& player) {
    auto in  = readInput(client);
    auto rot = player.getRotation();

    MacroTick row;
    row.w      = in.w;
    row.a      = in.a;
    row.s      = in.s;
    row.d      = in.d;
    row.sprint = in.sprint;
    row.sneak  = in.sneak;
    row.jump   = in.jump;
    row.rmb    = mMouseRight.load() || mMouseRightLatch.exchange(false);
    row.lmb    = mMouseLeft.load() || mMouseLeftLatch.exchange(false);

    if (mHavePrevRotation) {
        float yaw = wrapDegrees(rot.y - mPrevYaw);
        // Store the same sign _applyTurnDelta consumes, so playback reproduces
        // the recorded rotation (and dmf's mouse-delta convention).
        float pitch = -(rot.x - mPrevPitch);
        row.yaw     = std::fabs(yaw) < 0.001f ? 0.0f : yaw;
        row.pitch   = std::fabs(pitch) < 0.001f ? 0.0f : pitch;
    }
    mPrevYaw          = rot.y;
    mPrevPitch        = rot.x;
    mHavePrevRotation = true;

    bool limitReached = false;
    {
        std::lock_guard lock(mRecordMutex);
        if (mRecordedMacro.size() < MaxRecordedTicks) {
            mRecordedMacro.push_back(row);
        }
        auto max = mMacroRecordMax.load(std::memory_order_relaxed);
        if (max != 0 && mRecordedMacro.size() >= max) {
            mMacroRecording = false;
            limitReached    = true;
        }
    }
    if (limitReached) {
        enqueue(R"({"event":"macro_rec_limit"})");
    }
}

void ProbeMod::finishMacro(std::string reason) {
    if (!mMacroPlaying.exchange(false)) {
        return;
    }
    if (mMacroEventLevel) {
        releaseMacroInputs();
    }
    std::string name;
    {
        std::lock_guard lock(mMacroMutex);
        name = mMacroName;
    }
    nlohmann::json j;
    j["event"]  = "macro_done";
    j["name"]   = name;
    j["reason"] = std::move(reason);
    j["index"]  = mMacroIndex.load();
    enqueue(j.dump());
}

std::filesystem::path ProbeMod::macroDir() const { return getSelf().getModDir() / "macros"; }

std::filesystem::path ProbeMod::macroPath(std::string const& name) const { return macroDir() / (name + ".json"); }

bool ProbeMod::loadMacroFrom(std::string const& path, std::string const& name, std::string& error) {
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

    std::lock_guard lock(mMacroMutex);
    mMacro     = std::make_shared<Macro>(std::move(macro));
    mMacroName = name.empty() ? file.stem().string() : name;
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
    std::error_code ec;
    std::filesystem::create_directories(path.parent_path(), ec);
    std::ofstream file(path, std::ios::binary | std::ios::trunc);
    if (!file) {
        error = "cannot write " + path.string();
        return false;
    }
    file << value.dump(2);
    return true;
}


} // namespace probe
