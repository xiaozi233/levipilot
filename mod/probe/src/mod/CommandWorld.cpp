#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

bool ProbeMod::dispatchWorldCommand(std::string const& cmd, nlohmann::json& j) {
    if (cmd == "command") {
        std::string command = j.value("command", std::string{});
        if (!command.empty() && command.front() == '/') {
            command.erase(0, 1); // MinecraftCommands expects the bare command text
        }
        if (command.empty()) {
            enqueue(R"({"event":"error","reason":"empty_command"})");
            return true;
        }
        auto server = ll::service::bedrock::getServerInstance();
        if (!server) {
            enqueue(R"({"event":"error","reason":"command_no_server_instance"})");
            return true;
        }
        auto const identity = localPlayerIdentity();
        if (!identity.valid) {
            enqueue(R"({"event":"error","reason":"command_player_unknown"})");
            return true;
        }
        std::string const name = j.value("player", std::string{});
        queueServerTask([this, command, name, identity] {
            auto const context = serverPlayerContext(identity, mPoseEpoch.load(), mWorld.inWorld(), &name);
            auto* minecraft = context.minecraft;
            auto* level = context.level;
            if (level == nullptr || !minecraft->mCommands) {
                enqueue(R"({"event":"error","reason":"command_no_server_level"})");
                return;
            }
            // Preserve @s / ~ / rotation semantics. Missing identity must not
            // silently execute a different command as the owner-level console.
            if (!context.player) {
                enqueue(R"({"event":"error","reason":"command_player_not_found"})");
                return;
            }
            auto origin = std::make_unique<PlayerCommandOrigin>(*level, context.player->getOrCreateUniqueID());
            CommandContext commandContext(command, std::move(origin), static_cast<int>(CurrentCmdVersion::Latest));
            auto           result = minecraft->mCommands->executeCommand(commandContext, true);

            nlohmann::json out;
            out["event"]   = "command_done";
            out["command"] = command;
            out["success"] = static_cast<bool>(result.mSuccess);
            out["code"]    = static_cast<int>(result.mCode);
            enqueue(out.dump());
        });
        return true;
    }
    if (cmd == "checkpoint_save") {
        checkpointSave(j.value("name", std::string{}));
        return true;
    }
    if (cmd == "checkpoint_load") {
        checkpointLoad(j.value("name", std::string{}));
        return true;
    }
    if (cmd == "checkpoint_list") {
        checkpointList();
        return true;
    }
    if (cmd == "checkpoint_delete") {
        checkpointDelete(j.value("name", std::string{}));
        return true;
    }
    if (cmd == "enter_world") {
        std::string name = j.value("name", std::string{});
        std::string id   = j.value("id", std::string{});
        if (name.empty() && id.empty()) {
            enqueue(R"({"event":"error","reason":"missing_world"})");
            return true;
        }
        EntryTest test = EntryTest::None;
        auto const testMode = j.value("_selftest", std::string{});
        if (!testMode.empty()) {
            if (!mAllowSelftestStress.load(std::memory_order_relaxed)) {
                enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
                return true;
            }
            if (testMode == "timeout") test = EntryTest::Timeout;
            else if (testMode == "probe_fault") test = EntryTest::ProbeFault;
            else if (testMode == "start_fault") test = EntryTest::StartFault;
            else if (testMode == "missing_join") test = EntryTest::MissingJoin;
            else {
                enqueue(R"({"event":"error","reason":"bad_entry_test"})");
                return true;
            }
        }
        mWorld.enter(
            name,
            id,
            std::clamp(j.value("intent", 0), 0, 16),
            j.value("force", false),
            test
        );
        return true;
    }
    if (cmd == "signin") {
        reportSignInState();
        return true;
    }
    if (cmd == "leave_world") {
        leaveWorld();
        return true;
    }
    if (cmd == "worlds") {
        listWorlds();
        return true;
    }
    if (cmd == "options_set") {
        std::string text = j.value("text", std::string{});
        if (text.empty() && j.contains("values") && j["values"].is_object()) {
            for (auto it = j["values"].begin(); it != j["values"].end(); ++it) {
                std::string value  = it.value().is_string() ? it.value().get<std::string>() : it.value().dump();
                text              += it.key() + ":" + value + "\n";
            }
        }
        if (text.empty()) {
            enqueue(R"({"event":"error","reason":"empty_options"})");
            return true;
        }
        // Options is a client-thread object; the harness thread only forwards
        // the text and the file persistence happens inside the task too.
        queueClientTask([this, text] { setOptions(text); });
        return true;
    }
    if (cmd == "options_save") {
        queueClientTask([this] { saveOptions(); });
        return true;
    }
    if (cmd == "teleport") {
        double x = 0.0, y = 0.0, z = 0.0;
        double yaw = 0.0, pitch = 0.0;
        // Non-finite or out-of-world values would flow into the server /tp
        // command and the float request block unchecked.
        if (!numberJson(j, "x", -CoordHorizonLimit, CoordHorizonLimit, 0.0, x)
            || !numberJson(j, "y", -CoordHorizonLimit, CoordHorizonLimit, 0.0, y)
            || !numberJson(j, "z", -CoordHorizonLimit, CoordHorizonLimit, 0.0, z)
            || !numberJson(j, "yaw", -1e9, 1e9, 0.0, yaw)
            || !numberJson(j, "pitch", -1e9, 1e9, 0.0, pitch)) {
            enqueue(R"({"event":"error","reason":"bad_param","param":"x|y|z|yaw|pitch"})");
            return true;
        }
        updateRequests([&j, x, y, z, yaw, pitch](RequestBlock& request) {
            request.teleport.x           = static_cast<float>(x);
            request.teleport.y           = static_cast<float>(y);
            request.teleport.z           = static_cast<float>(z);
            request.teleport.hasRotation = j.contains("yaw") || j.contains("pitch");
            request.teleport.yaw         = static_cast<float>(yaw);
            request.teleport.pitch       = static_cast<float>(pitch);
            // reset() stores true here; the flag must not leak into later
            // teleports, or one reset silently strips every potion effect for
            // the rest of the session (observed as "protection vanished").
            request.teleport.clearEffects = false;
            ++request.teleportSeq;
        });
        // "teleported" is emitted from the server thread once the move is applied.
        return true;
    }
    if (cmd == "reset") {
        double x = 0.0, y = 0.0, z = 0.0;
        double yaw = 0.0, pitch = 0.0;
        if (!numberJson(j, "x", -CoordHorizonLimit, CoordHorizonLimit, 0.0, x)
            || !numberJson(j, "y", -CoordHorizonLimit, CoordHorizonLimit, 0.0, y)
            || !numberJson(j, "z", -CoordHorizonLimit, CoordHorizonLimit, 0.0, z)
            || !numberJson(j, "yaw", -1e9, 1e9, 0.0, yaw)
            || !numberJson(j, "pitch", -1e9, 1e9, 0.0, pitch)) {
            enqueue(R"({"event":"error","reason":"bad_param","param":"x|y|z|yaw|pitch"})");
            return true;
        }
        // reset is the only command documented to drop potion effects.
        resetPlayer(
            static_cast<float>(x),
            static_cast<float>(y),
            static_cast<float>(z),
            j.contains("yaw"),
            static_cast<float>(yaw),
            j.contains("pitch"),
            static_cast<float>(pitch),
            true
        );
        return true;
    }
    return false;
}

} // namespace probe
