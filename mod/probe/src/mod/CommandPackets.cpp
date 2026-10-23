#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

bool ProbeMod::dispatchPacketsCommand(std::string const& cmd, nlohmann::json& j) {
    if (cmd == "symbol_resolve") {
        enqueue(symbolResolve(j.value("name", std::string{})).dump());
        return true;
    }
    if (cmd == "op_self") {
        if (!mAllowPacketInject.load(std::memory_order_relaxed)) {
            enqueue(R"({"event":"error","reason":"packet_inject_disabled"})");
            return true;
        }
        int const level = std::clamp(j.value("level", 4), 0, 4);
        std::string name;
        {
            std::lock_guard lock(mPlayerNameMutex);
            name = mLocalPlayerName;
        }
        queueServerTask([this, name, level] {
            auto minecraft = ll::service::bedrock::getMinecraft();
            Level* lvl     = minecraft ? minecraft->getLevel() : nullptr;
            Player* target = (lvl != nullptr && !name.empty()) ? lvl->getPlayer(name) : nullptr;
            if (target == nullptr) {
                enqueue(R"({"event":"error","reason":"player_not_found"})");
                return;
            }
            target->setCommandPermissions(static_cast<CommandPermissionLevel>(level));
            enqueue(
                nlohmann::json{
                    {"event", "op_self"},
                    {"ok",    true    },
                    {"level", level   },
                    {"name",  name    },
                }
                    .dump()
            );
        });
        return true;
    }
    if (cmd == "commands_enabled") {
        bool const set   = j.contains("value");
        bool const value = j.value("value", false);
        queueServerTask([this, set, value] {
            auto level = ll::service::bedrock::getLevel();
            if (!level) {
                enqueue(R"({"event":"error","reason":"no_level"})");
                return;
            }
            auto& data = level->getLevelData();
            if (set) {
                data.setCommandsEnabled(value);
            }
            bool const enabled = static_cast<bool>(data.mCommandsEnabled);
            enqueue(
                nlohmann::json{
                    {"event",   "commands_enabled"},
                    {"enabled", enabled           },
                    {"changed", set               },
                }
                    .dump()
            );
        });
        return true;
    }
    if (cmd == "chat_send") {
        if (!hooksAllowed()) {
            enqueue(R"({"event":"error","reason":"version_mismatch"})");
            return true;
        }
        if (!mAllowPacketInject.load(std::memory_order_relaxed)) {
            enqueue(
                R"({"event":"error","reason":"packet_inject_disabled","hint":"set allow_packet_inject=true"})"
            );
            return true;
        }
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        std::string text = j.value("text", std::string{});
        if (text.empty() || text.size() > 512) {
            enqueue(R"({"event":"error","reason":"bad_text"})");
            return true;
        }
        {
            std::lock_guard lock(mChatMutex);
            mChatPending = std::move(text);
        }
        bootLog("input: chat_send queued");
        enqueue(R"({"event":"chat_send_queued"})");
        return true;
    }
    if (cmd == "packet_capture") {
        bool const enable = j.value("on", j.value("enable", true));
        if (!hooksAllowed()) {
            enqueue(R"({"event":"error","reason":"version_mismatch"})");
            return true;
        }
        if (enable && !mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        enqueue(packetCaptureStart(enable, j).dump());
        return true;
    }
    if (cmd == "packet_drain") {
        std::uint64_t const since = j.value("since", std::uint64_t{0});
        std::size_t const   limit = std::clamp<std::size_t>(j.value("limit", 1024), 1, 8192);
        enqueue(packetDrain(since, limit).dump());
        return true;
    }
    if (cmd == "packet_bytes") {
        std::uint64_t const since = j.value("since", std::uint64_t{0});
        std::size_t const   limit = std::clamp<std::size_t>(j.value("limit", 256), 1, 4096);
        enqueue(packetBytesDrain(since, limit).dump());
        return true;
    }
    if (cmd == "packet_stats") {
        enqueue(packetCaptureStatus().dump());
        return true;
    }
    return false;
}

} // namespace probe
