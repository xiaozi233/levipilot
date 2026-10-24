#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

bool ProbeMod::dispatchInputCommand(std::string const& cmd, nlohmann::json& j) {
    if (cmd == "key_event" || cmd == "key_state" || cmd == "mouse_event") {
        if (!hooksAllowed()) {
            enqueue(R"({"event":"error","reason":"version_mismatch"})");
            return true;
        }
        if (!mAllowInputInject.load(std::memory_order_relaxed)) {
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
        if (!mAllowInputInject.load(std::memory_order_relaxed)) {
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
        {
            std::lock_guard lock(mTextMutex);
            mTextPending = std::move(text);
        }
        enqueue(R"({"event":"text_input_queued"})");
        return true;
    }
    if (cmd == "screen") {
        auto              client = ll::service::getClientInstance();
        std::string const name   = client ? client->getScreenName() : std::string{};
        enqueue(
            nlohmann::json{
                {"event",    "screen"      },
                {"name",     name          },
                {"in_world", mWorld.inWorld()},
            }
                .dump()
        );
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
        item.kind   = via == "queue" ? 0 : (via == "hardware" ? 3 : 2);
        item.code   = static_cast<std::uint8_t>(code);
        item.data   = down ? 1 : 0;
        item.repeat = repeat;
        if (!queueInputInjection(item)) {
            enqueue(R"({"event":"error","reason":"inject_queue_full"})");
            return true;
        }
        bootLog(
            "input: key_event code=" + std::to_string(code) + " down=" + (down ? "1" : "0") + " via=" + via
        );
        enqueue(
            nlohmann::json{
                {"event",  "key_event_queued"},
                {"code",   code             },
                {"down",   down             },
                {"repeat", repeat           },
                {"via",    via              },
                {"events", mKeyEventCount.load()},
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
        auto& states = Keyboard::_states();
        enqueue(
            nlohmann::json{
                {"event",         "key_state"              },
                {"code",          code                     },
                {"down",          states[code] != 0        },
                {"events",        mKeyEventCount.load()    },
                {"injected_seen", mKeyInjectedSeen.load()  },
                {"last_code",     mKeyLastCode.load()      },
                {"last_action",   mKeyLastAction.load()    },
                {"pending",       mInjectHead.load() - mInjectTail.load()},
            }
                .dump()
        );
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
        item.kind = 1;
        item.code = static_cast<std::uint8_t>(action);
        item.data = static_cast<std::int8_t>(std::clamp(data, -128, 127));
        item.dx   = dx;
        item.dy   = dy;
        item.hasPos = j.contains("x") || j.contains("y");
        item.x      = clampShort(j.value("x", 0));
        item.y      = clampShort(j.value("y", 0));
        std::uint64_t const before = mMouseEventCount.load(std::memory_order_relaxed);
        if (!queueInputInjection(item)) {
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
                {"action",        action               },
                {"data",          data                 },
                {"dx",            dx                   },
                {"dy",            dy                   },
                {"count_before",  before               },
                {"injected_total", mMouseInjected.load()},
            }
                .dump()
        );
        return true;
    }
    if (cmd == "mouse") {
        nlohmann::json out;
        out["event"]       = "mouse";
        out["count"]       = mMouseEventCount.load();
        out["last_action"] = mMouseLastAction.load();
        out["last_data"]   = mMouseLastData.load();
        out["left"]        = mMouseLeft.load();
        out["right"]       = mMouseRight.load();
        out["counts"]      = nlohmann::json::array();
        for (auto const& count : mMouseActionCounts) {
            out["counts"].push_back(count.load());
        }
        enqueue(out.dump());
        return true;
    }
    if (cmd == "mouse_clear") {
        mMouseEventCount = 0;
        mMouseLastAction = -1;
        mMouseLastData   = -1;
        for (auto& count : mMouseActionCounts) {
            count = 0;
        }
        mMouseLeftLatch  = false;
        mMouseRightLatch = false;
        mMouseLeft       = false;
        mMouseRight      = false;
        mPrevActionLmb   = false;
        mPrevActionRmb   = false;
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
        updateRequests([&j, forward, strafe](RequestBlock& request) {
            request.input.forward = std::clamp(static_cast<float>(forward), -1.0f, 1.0f);
            request.input.strafe  = std::clamp(static_cast<float>(strafe), -1.0f, 1.0f);
            request.input.active  = true;
            request.inputTicks    = j.value("ticks", 1);
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
        updateRequests([&j, forward, strafe](RequestBlock& request) {
            request.input         = ControlState{};
            request.input.forward = std::clamp(static_cast<float>(forward), -1.0f, 1.0f);
            request.input.strafe  = std::clamp(static_cast<float>(strafe), -1.0f, 1.0f);
            request.input.sprint  = j.value("sprint", false);
            request.input.sneak   = j.value("sneak", false);
            request.input.jump    = j.value("jump", false);
            request.input.rmb     = j.value("rmb", false);
            request.input.lmb     = j.value("lmb", false);
            request.input.active  = true;
            request.inputTicks    = j.value("ticks", 1);
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
        updateRequests([&j, yaw, pitch](RequestBlock& request) {
            request.lookRelative = j.value("relative", true);
            request.lookYaw      = static_cast<float>(yaw);
            request.lookPitch    = static_cast<float>(pitch);
            request.lookTicks    = j.value("ticks", 1);
            ++request.lookSeq;
        });
        enqueue(R"({"event":"look_ok"})");
        return true;
    }
    if (cmd == "input_cfg") {
        mInputSource.store(j.value("source", -1));
        mInputController.store(j.value("controller", 0));
        mInputFocus.store(j.value("focus", 0));
        if (j.contains("mode")) {
            mButtonMode.store(j.value("mode", 7));
            mButtonStates.fill(false);
            mHandlerStates.fill(false);
            mPrevJump = false;
        }
        if (j.contains("jump_impulse")) {
            mJumpImpulseMode.store(std::clamp(j.value("jump_impulse", 1), 0, 3));
        }
        enqueue(R"({"event":"input_cfg_ok"})");
        return true;
    }
    if (cmd == "buttons") {
        mButtonsRequested = true;
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
        mClickRequest.store(button == "right" ? 2 : 1);
        return true;
    }
    if (cmd == "fly") {
        mFlyRequest.store(j.value("enabled", true) ? 1 : 0);
        enqueue(R"({"event":"fly_ok"})");
        return true;
    }
    return false;
}

} // namespace probe
