#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

bool ProbeMod::dispatchInputCommand(std::string const& cmd, nlohmann::json& j) {
    if (cmd == "selftest_controls") {
        if (!mAllowSelftestStress.load()) {
            enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
            return true;
        }
        auto const mode = j.value("mode", std::string{});
        if (mode == "off") {
            for (auto& test : mInputSession.controlTests) test = ControlTest::None;
            enqueue(R"({"event":"control_test_armed"})");
            return true;
        }
        if (!mInputSession.allowInject.load() || !mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"input_test_unavailable"})");
            return true;
        }
        ControlTest test;
        if (mode == "seh_before") test = ControlTest::SehBefore;
        else if (mode == "cpp_before") test = ControlTest::CppBefore;
        else if (mode == "seh_after") test = ControlTest::SehAfter;
        else if (mode == "cpp_after") test = ControlTest::CppAfter;
        else if (mode == "unavailable") test = ControlTest::Unavailable;
        else { enqueue(R"({"event":"error","reason":"bad_test_mode"})"); return true; }
        auto const operation = j.value("operation", std::string{});
        for (unsigned i = 0; i < ControlDiagnostics::Operations; ++i) {
            if (operation != controlOpName(static_cast<ControlOp>(i))) continue;
            mInputSession.controlTests[i] = test;
            enqueue(R"({"event":"control_test_armed"})");
            return true;
        }
        enqueue(R"({"event":"error","reason":"bad_test_operation"})");
        return true;
    }
    if (cmd == "selftest_input_events") {
        if (!mAllowSelftestStress.load()) {
            enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
            return true;
        }
        auto const mode = j.value("mode", std::string{});
        CaptureTest test;
        if (mode == "seh") test = CaptureTest::Fault;
        else if (mode == "cpp") test = CaptureTest::Cpp;
        else if (mode == "timeout") test = CaptureTest::Deferred;
        else { enqueue(R"({"event":"error","reason":"bad_test_mode"})"); return true; }
        auto const target = j.value("target", std::string{});
        if (target == "screen") queueUiQuery({UiOperation::Screen, 0, test});
        else if (target == "text" || target == "key") {
            if (!mInputSession.allowInject.load() || !mWorld.inWorld()) {
                enqueue(R"({"event":"error","reason":"input_test_unavailable"})");
            } else if (target == "text") queueTextInput("selftest", test);
            else if (test == CaptureTest::Deferred) enqueue(R"({"event":"error","reason":"bad_test_mode"})");
            else {
                InputInjection item;
                item.kind = InputKind::Hid;
                item.code = 72;
                item.test = test;
                auto id = queueInputInjection(item);
                if (!id) enqueue(R"({"event":"error","reason":"inject_queue_full"})");
                else enqueue(nlohmann::json{{"event", "key_event_queued"}, {"input_id", id}}.dump());
            }
        } else enqueue(R"({"event":"error","reason":"bad_test_target"})");
        return true;
    }
    if (cmd == "key_event" || cmd == "key_state" || cmd == "mouse_event") {
        if (!hooksAllowed()) {
            enqueue(R"({"event":"error","reason":"version_mismatch"})");
            return true;
        }
        if (!mInputSession.allowInject.load(std::memory_order_relaxed)) {
            enqueue(
                R"({"event":"error","reason":"input_inject_disabled","hint":"set allow_input_inject=true"})"
            );
            return true;
        }
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
    }
    if (cmd == "text_input") {
        if (!mInputSession.allowInject.load(std::memory_order_relaxed)) {
            enqueue(R"({"event":"error","reason":"input_inject_disabled"})");
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
        queueTextInput(std::move(text));
        return true;
    }
    if (cmd == "screen") {
        queueUiQuery({UiOperation::Screen});
        return true;
    }
    if (cmd == "key_event") {
        int const code = j.value("code", -1);
        if (code < 0 || code >= Keyboard::Count) {
            enqueue(R"({"event":"error","reason":"bad_key"})");
            return true;
        }
        bool const down   = j.value("down", true);
        bool const repeat = j.value("repeat", false) || j.value("repeat_ms", 0) > 0;
        std::string const via = j.value("via", std::string{"hid"});
        InputInjection item{};
        item.kind = via == "queue" ? InputKind::Queue : (via == "hardware" ? InputKind::Hardware
                    : (via == "raw" ? InputKind::Raw : InputKind::Hid));
        item.code   = static_cast<std::uint8_t>(code);
        item.data   = down ? 1 : 0;
        item.repeat = repeat;
        auto const inputId = queueInputInjection(item);
        if (!inputId) {
            enqueue(R"({"event":"error","reason":"inject_queue_full"})");
            return true;
        }
        bootLog(
            "input: key_event code=" + std::to_string(code) + " down=" + (down ? "1" : "0") + " via=" + via
        );
        enqueue(
            nlohmann::json{
                {"event",  "key_event_queued"},
                {"input_id", inputId},
                {"code",   code             },
                {"down",   down             },
                {"repeat", repeat           },
                {"via",    via              },
                {"events", mInputSession.keyEventCount.load()},
            }
                .dump()
        );
        return true;
    }
    if (cmd == "key_state") {
        int const code = j.value("code", -1);
        if (code < 0 || code >= Keyboard::Count) {
            enqueue(R"({"event":"error","reason":"bad_key"})");
            return true;
        }
        queueUiQuery({UiOperation::KeyState, code});
        return true;
    }
    if (cmd == "mouse_event") {
        int action = -1;
        if (j.contains("action") && j["action"].is_number_integer()) {
            action = j["action"].get<int>();
        } else if (j.contains("action") && j["action"].is_string()) {
            auto const name = j["action"].get<std::string>();
            if (name == "move") {
                action = 0;
            } else if (name == "left") {
                action = 1;
            } else if (name == "right") {
                action = 2;
            } else if (name == "middle") {
                action = 3;
            } else if (name == "wheel") {
                action = 4;
            } else if (name == "x1") {
                action = 5;
            } else if (name == "x2") {
                action = 6;
            } else if (name == "move_relative") {
                action = 7;
            }
        }
        if (action < 0 || action > 7) {
            enqueue(R"({"event":"error","reason":"bad_action"})");
            return true;
        }
        int data = j.value("data", 1);
        if (j.contains("data") && j["data"].is_string()) {
            data = j["data"].get<std::string>() == "up" ? 0 : 1;
        } else if (j.contains("down")) {
            data = j.value("down", true) ? 1 : 0;
        }
        auto const clampShort = [](int value) {
            return static_cast<std::int16_t>(std::clamp(value, -32768, 32767));
        };
        std::int16_t const dx = clampShort(j.value("dx", 0));
        std::int16_t const dy = clampShort(j.value("dy", 0));
        InputInjection item{};
        item.kind = InputKind::Mouse;
        item.code = static_cast<std::uint8_t>(action);
        item.data = static_cast<std::int8_t>(std::clamp(data, -128, 127));
        item.dx   = dx;
        item.dy   = dy;
        item.hasPos = j.contains("x") || j.contains("y");
        item.x      = clampShort(j.value("x", 0));
        item.y      = clampShort(j.value("y", 0));
        std::uint64_t const before = mInputSession.mouseEventCount.load(std::memory_order_relaxed);
        auto const inputId = queueInputInjection(item);
        if (!inputId) {
            enqueue(R"({"event":"error","reason":"inject_queue_full"})");
            return true;
        }
        bootLog(
            "input: mouse_event action=" + std::to_string(action) + " data=" + std::to_string(data)
            + " dx=" + std::to_string(dx) + " dy=" + std::to_string(dy)
        );
        enqueue(
            nlohmann::json{
                {"event",         "mouse_event_queued"},
                {"input_id", inputId},
                {"action",        action               },
                {"data",          data                 },
                {"dx",            dx                   },
                {"dy",            dy                   },
                {"count_before",  before               },
                {"injected_total", mInputSession.mouseInjected.load()},
            }
                .dump()
        );
        return true;
    }
    if (cmd == "mouse") {
        nlohmann::json out;
        out["event"]       = "mouse";
        out["count"]       = mInputSession.mouseEventCount.load();
        out["last_action"] = mInputSession.mouseLastAction.load();
        out["last_data"]   = mInputSession.mouseLastData.load();
        out["left"]        = mInputSession.mouseLeft.load();
        out["right"]       = mInputSession.mouseRight.load();
        out["counts"]      = nlohmann::json::array();
        for (auto const& count : mInputSession.mouseActionCounts) {
            out["counts"].push_back(count.load());
        }
        enqueue(out.dump());
        return true;
    }
    if (cmd == "mouse_clear") {
        mInputSession.clearMouse(true);
        enqueue(R"({"event":"mouse_cleared"})");
        return true;
    }
    if (cmd == "move") {
        double forward = 0.0;
        double strafe  = 0.0;
        if (!numberJson(j, "forward", -1e9, 1e9, 0.0, forward)
            || !numberJson(j, "strafe", -1e9, 1e9, 0.0, strafe)) {
            enqueue(R"({"event":"error","reason":"bad_param","param":"forward|strafe"})");
            return true;
        }
        int ticks = 1;
        if (!parseInputTicks(j, "ticks", 1, ticks)) {
            enqueue(R"({"event":"error","reason":"bad_param","param":"ticks"})");
            return true;
        }
        updateRequests([forward, strafe, ticks](RequestBlock& request) {
            request.input.forward = std::clamp(static_cast<float>(forward), -1.0f, 1.0f);
            request.input.strafe  = std::clamp(static_cast<float>(strafe), -1.0f, 1.0f);
            request.input.active  = true;
            request.inputTicks    = ticks;
            ++request.inputSeq;
        });
        enqueue(R"({"event":"moving"})");
        return true;
    }
    if (cmd == "stopmove") {
        updateRequests([](RequestBlock& request) {
            request.input      = ControlState{};
            request.inputTicks = 0;
            ++request.inputSeq;
        });
        enqueue(R"({"event":"stopping"})");
        return true;
    }
    if (cmd == "set") {
        double forward = 0.0;
        double strafe  = 0.0;
        if (!numberJson(j, "forward", -1e9, 1e9, 0.0, forward)
            || !numberJson(j, "strafe", -1e9, 1e9, 0.0, strafe)) {
            enqueue(R"({"event":"error","reason":"bad_param","param":"forward|strafe"})");
            return true;
        }
        int ticks = 1;
        if (!parseInputTicks(j, "ticks", 1, ticks)) {
            enqueue(R"({"event":"error","reason":"bad_param","param":"ticks"})");
            return true;
        }
        ControlState input;
        input.forward = std::clamp(static_cast<float>(forward), -1.0f, 1.0f);
        input.strafe  = std::clamp(static_cast<float>(strafe), -1.0f, 1.0f);
        input.sprint  = j.value("sprint", false);
        input.sneak   = j.value("sneak", false);
        input.jump    = j.value("jump", false);
        input.rmb     = j.value("rmb", false);
        input.lmb     = j.value("lmb", false);
        input.active  = true;
        updateRequests([input, ticks](RequestBlock& request) {
            request.input         = input;
            request.inputTicks    = ticks;
            ++request.inputSeq;
        });
        enqueue(R"({"event":"set_ok"})");
        return true;
    }
    if (cmd == "jump") {
        updateRequests([](RequestBlock& request) {
            request.jumpCancel = false;
            ++request.jumpSeq;
        });
        enqueue(R"({"event":"jump_ok"})");
        return true;
    }
    if (cmd == "look") {
        double yaw   = 0.0;
        double pitch = 0.0;
        // A non-finite yaw would spin wrapDegrees forever on the tick thread.
        if (!numberJson(j, "yaw", -1e9, 1e9, 0.0, yaw) || !numberJson(j, "pitch", -1e9, 1e9, 0.0, pitch)) {
            enqueue(R"({"event":"error","reason":"bad_param","param":"yaw|pitch"})");
            return true;
        }
        int ticks = 1;
        if (!parseInputTicks(j, "ticks", 1, ticks)) {
            enqueue(R"({"event":"error","reason":"bad_param","param":"ticks"})");
            return true;
        }
        bool const relative = j.value("relative", true);
        updateRequests([relative, ticks, yaw, pitch](RequestBlock& request) {
            request.lookRelative = relative;
            request.lookYaw      = static_cast<float>(yaw);
            request.lookPitch    = static_cast<float>(pitch);
            request.lookTicks    = ticks;
            ++request.lookSeq;
        });
        enqueue(R"({"event":"look_ok"})");
        return true;
    }
    if (cmd == "input_cfg") {
        mInputSession.inputSource.store(j.value("source", -1));
        mInputSession.inputController.store(j.value("controller", 0));
        mInputSession.inputFocus.store(j.value("focus", 0));
        if (j.contains("mode")) {
            int const mode = j.value("mode", 0);
            if (mode != 0 && !j.value("force", false)) {
                enqueue(nlohmann::json{
                    {"event",  "error"                          },
                    {"reason", "legacy_input_mode_requires_force"},
                    {"hint",   "mode=0 is real HID keys; non-zero reproduces old flag/queue/handler paths"},
                }.dump());
                return true;
            }
            mInputSession.inputMode.store(static_cast<ControlInputMode>(mode));
            mInputSession.resetInputButtons.store(true, std::memory_order_release);
        }
        if (j.contains("jump_impulse")) {
            int const impulse = std::clamp(j.value("jump_impulse", 0), 0, 3);
            if (impulse != 0 && !j.value("force", false)) {
                enqueue(nlohmann::json{
                    {"event",  "error"                            },
                    {"reason", "legacy_jump_impulse_requires_force"},
                    {"hint",   "jump_impulse=0 presses Space; non-zero reproduces the old direct impulse"},
                }.dump());
                return true;
            }
            mInputSession.jumpImpulseMode.store(static_cast<JumpImpulse>(impulse));
        }
        enqueue(R"({"event":"input_cfg_ok"})");
        return true;
    }
    if (cmd == "input_lock") {
        // Drops real hardware keys/mouse at their entry points while scripts
        // run; injected events keep working. Does not need allow_input_inject
        // because it only removes input.
        bool const on = j.value("on", true);
        mInputSession.hardwareLocked.store(on, std::memory_order_relaxed);
        enqueue(nlohmann::json{{"event", "input_lock_set"}, {"on", on}}.dump());
        return true;
    }
    if (cmd == "buttons") {
        if (!mWorld.inWorld()) enqueue(R"({"event":"error","reason":"not_in_world"})");
        else if (!mButtonCapture.begin({}, GetTickCount64(), 2000, activeReqId()))
            enqueue(R"({"event":"error","reason":"buttons_busy"})");
        return true;
    }
    if (cmd == "click") {
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        auto const button = j.value("button", std::string{"left"});
        double     reach  = 5.5;
        if (!numberJson(j, "reach", 0.0, 64.0, 5.5, reach)) {
            enqueue(R"({"event":"error","reason":"bad_param","param":"reach"})");
            return true;
        }
        mClickReach.store(static_cast<float>(reach));
        mClickReqId.store(activeReqId(), std::memory_order_release);
        mClickRequest.store(button == "right" ? 2 : 1, std::memory_order_release);
        return true;
    }
    if (cmd == "fly" || cmd == "togglefly") {
        FlightQuery query;
        query.operation = cmd == "fly" ? FlightOperation::Fly : FlightOperation::Toggle;
        query.target = cmd == "fly" ? (j.value("enabled", true) ? FlightTarget::On : FlightTarget::Off)
                     : (j.contains("on") ? (j.value("on", true) ? FlightTarget::On : FlightTarget::Off)
                                          : FlightTarget::Invert);
        queueFlight(query);
        return true;
    }
    if (cmd == "selftest_flight") {
        if (!mAllowSelftestStress.load()) {
            enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
            return true;
        }
        auto const mode = j.value("mode", std::string{});
        auto const stage = j.value("stage", std::string{});
        FlightQuery query;
        query.target = j.value("on", false) ? FlightTarget::On : FlightTarget::Off;
        if (mode == "seh") query.test = CaptureTest::Fault;
        else if (mode == "cpp") query.test = CaptureTest::Cpp;
        else if (mode == "timeout") query.test = CaptureTest::Deferred;
        else { enqueue(R"({"event":"error","reason":"bad_test_mode"})"); return true; }
        if (stage == "client") query.testStage = FlightStage::Client;
        else if (stage == "server") query.testStage = FlightStage::Server;
        else if (stage == "assert" && query.test != CaptureTest::Deferred) query.testStage = FlightStage::Assert;
        else { enqueue(R"({"event":"error","reason":"bad_test_stage"})"); return true; }
        auto const timeout = j.value("timeout_ms", 250);
        if (timeout < 100 || timeout > 10000) {
            enqueue(R"({"event":"error","reason":"bad_test_timeout"})"); return true;
        }
        queueFlight(query, static_cast<std::uint64_t>(timeout));
        return true;
    }
    return false;
}

} // namespace probe
