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
        auto const identity = localPlayerIdentity();
        queueServerTask([this, identity, level] {
            auto const context = serverPlayerContext(identity, mPoseEpoch.load(), mWorld.inWorld());
            auto* target = context.player;
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
                    {"name",  target->getRealName()},
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
    if (cmd == "selftest_packet_template") {
        if (!mAllowSelftestStress.load() || !mAllowPacketInject.load()) {
            enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
            return true;
        }
        auto const mode = j.value("mode", std::string{});
        if (mode == "seh") mPacketTemplateTest = CaptureTest::Fault;
        else if (mode == "cpp") mPacketTemplateTest = CaptureTest::Cpp;
        else if (mode == "long") mPacketTemplateTest = CaptureTest::Partial;
        else if (mode == "off") mPacketTemplateTest = CaptureTest::None;
        else { enqueue(R"({"event":"error","reason":"bad_test_mode"})"); return true; }
        enqueue(R"({"event":"packet_template_test_armed"})");
        return true;
    }
    if (cmd == "chat_send" || cmd == "selftest_packet_injection") {
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
        PacketInjectionQuery query;
        query.text = std::move(text);
        std::uint64_t timeout = 2000;
        if (cmd == "selftest_packet_injection") {
            if (!mAllowSelftestStress.load()) {
                enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
                return true;
            }
            auto const mode = j.value("mode", std::string{});
            if (mode == "seh") query.test = PacketInjectionTest::Seh;
            else if (mode == "cpp") query.test = PacketInjectionTest::Cpp;
            else if (mode == "timeout") query.test = PacketInjectionTest::Deferred;
            else if (mode == "queue") query.test = PacketInjectionTest::Queue;
            else if (mode == "after_send") query.test = PacketInjectionTest::AfterSend;
            else if (mode == "stale_epoch") query.test = PacketInjectionTest::Obsolete;
            else if (mode == "wrong_owner") query.test = PacketInjectionTest::WrongOwner;
            else { enqueue(R"({"event":"error","reason":"bad_test_mode"})"); return true; }
            timeout = std::clamp<std::uint64_t>(j.value("timeout_ms", 250), 100, 10000);
        }
        queuePacketInjection(std::move(query), timeout);
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
        if (enable && !mAllowPacketCapture.load(std::memory_order_relaxed)) {
            enqueue(
                R"({"event":"error","reason":"packet_capture_disabled","hint":"set allow_packet_capture=true"})"
            );
            return true;
        }
        enqueue(mPackets.start(enable, j).dump());
        return true;
    }
    if (cmd == "packet_drain") {
        std::uint64_t const since = j.value("since", std::uint64_t{0});
        std::size_t const   limit = std::clamp<std::size_t>(j.value("limit", 1024), 1, 8192);
        enqueue(mPackets.drain(since, limit).dump());
        return true;
    }
    if (cmd == "packet_bytes") {
        std::uint64_t const since = j.value("since", std::uint64_t{0});
        std::size_t const   limit = std::clamp<std::size_t>(j.value("limit", 256), 1, 4096);
        enqueue(mPackets.bytesDrain(since, limit).dump());
        return true;
    }
    if (cmd == "packet_stats") {
        enqueue(mPackets.status().dump());
        return true;
    }
    return false;
}

} // namespace probe
