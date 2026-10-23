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
        std::string name = j.value("player", std::string{});
        if (name.empty()) {
            std::lock_guard lock(mPlayerNameMutex);
            name = mLocalPlayerName;
        }
        queueServerTask([this, command, name] {
            auto   minecraft = ll::service::bedrock::getMinecraft();
            Level* level     = minecraft ? minecraft->getLevel() : nullptr;
            if (level == nullptr || !minecraft->mCommands) {
                enqueue(R"({"event":"error","reason":"command_no_server_level"})");
                return;
            }
            // A player origin keeps @s / ~ / rotation semantics intact; the
            // console origin is only a fallback when the name is unknown.
            std::unique_ptr<CommandOrigin> origin;
            Player*                        serverPlayer = name.empty() ? nullptr : level->getPlayer(name);
            if (serverPlayer != nullptr) {
                origin = std::make_unique<PlayerCommandOrigin>(*level, serverPlayer->getOrCreateUniqueID());
            } else {
                origin = std::make_unique<ServerCommandOrigin>(
                    "harness",
                    level->asServer(),
                    CommandPermissionLevel::Owner,
                    DimensionType{0}
                );
            }
            CommandContext context(command, std::move(origin), static_cast<int>(CurrentCmdVersion::Latest));
            auto           result = minecraft->mCommands->executeCommand(context, true);

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
        enterWorld(
            name,
            id,
            std::clamp(j.value("intent", 0), 0, 16),
            j.value("force", false)
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
        setOptions(text);
        return true;
    }
    if (cmd == "options_save") {
        saveOptions();
        return true;
    }
    if (cmd == "teleport") {
        updateRequests([&j](RequestBlock& request) {
            request.teleport.x           = j.value("x", 0.0f);
            request.teleport.y           = j.value("y", 0.0f);
            request.teleport.z           = j.value("z", 0.0f);
            request.teleport.hasRotation = j.contains("yaw") || j.contains("pitch");
            request.teleport.yaw         = j.value("yaw", 0.0f);
            request.teleport.pitch       = j.value("pitch", 0.0f);
            ++request.teleportSeq;
        });
        // "teleported" is emitted from the server thread once the move is applied.
        return true;
    }
    if (cmd == "reset") {
        resetPlayer(
            j.value("x", 0.0f),
            j.value("y", 0.0f),
            j.value("z", 0.0f),
            j.contains("yaw"),
            j.value("yaw", 0.0f),
            j.contains("pitch"),
            j.value("pitch", 0.0f)
        );
        return true;
    }
    return false;
}

} // namespace probe
