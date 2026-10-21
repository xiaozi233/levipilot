#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

void ProbeMod::handleLine(std::string const& line) {
    // nlohmann throws on type mismatches, and a bad line from a script must not
    // kill the pipe thread (which would make the whole harness unreachable).
    try {
        dispatchLine(line);
    } catch (std::exception const& e) {
        enqueue(
            nlohmann::json{
                {"event",   "error"      },
                {"reason",  "bad_command"},
                {"message", e.what()     },
        }
                .dump()
        );
    } catch (...) {
        enqueue(R"({"event":"error","reason":"bad_command","message":"unknown exception"})");
    }
}

void ProbeMod::dispatchLine(std::string const& line) {
    auto j = nlohmann::json::parse(line, nullptr, false);
    if (j.is_discarded()) {
        enqueue(R"({"event":"error","reason":"bad_json"})");
        return;
    }

    auto cmd = j.value("cmd", std::string{});
    if (cmd == "ping") {
        enqueue(R"({"event":"pong","mod":"probe","protocol":3})");
        return;
    }
    if (cmd == "diag") {
        auto           mc     = ll::service::bedrock::getMinecraft();
        auto           server = ll::service::bedrock::getServerInstance();
        auto           level  = ll::service::bedrock::getLevel();
        nlohmann::json out;
        out["event"]           = "diag";
        out["minecraft"]       = mc.has_value();
        out["server_instance"] = server.has_value();
        out["level"]           = level.has_value();
        out["server_level"]    = mc.has_value() && mc->getLevel() != nullptr;
        out["commands"]        = mc.has_value() && static_cast<bool>(mc->mCommands);
        enqueue(out.dump());
        return;
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
        return;
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
        return;
    }
    if (cmd == "command") {
        std::string command = j.value("command", std::string{});
        if (!command.empty() && command.front() == '/') {
            command.erase(0, 1); // MinecraftCommands expects the bare command text
        }
        if (command.empty()) {
            enqueue(R"({"event":"error","reason":"empty_command"})");
            return;
        }
        auto server = ll::service::bedrock::getServerInstance();
        if (!server) {
            enqueue(R"({"event":"error","reason":"command_no_server_instance"})");
            return;
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
        return;
    }
    if (cmd == "start") {
        mTick      = 0;
        mMaxTicks  = j.value("max_ticks", std::uint64_t{0});
        mRecording = true;
        enqueue(R"({"event":"started"})");
        return;
    }
    if (cmd == "stop") {
        mRecording = false;
        enqueue(R"({"event":"stopped","reason":"client"})");
        return;
    }
    if (cmd == "move") {
        {
            std::lock_guard lock(mInputMutex);
            mDesired.forward = j.value("forward", 0.0f);
            mDesired.strafe  = j.value("strafe", 0.0f);
            mDesired.active  = true;
            mDesiredTicks    = j.value("ticks", 1);
        }
        enqueue(R"({"event":"moving"})");
        return;
    }
    if (cmd == "stopmove") {
        {
            std::lock_guard lock(mInputMutex);
            mDesired      = ControlState{};
            mDesiredTicks = 0;
        }
        enqueue(R"({"event":"stopping"})");
        return;
    }
    if (cmd == "set") {
        {
            std::lock_guard lock(mInputMutex);
            mDesired.forward = j.value("forward", 0.0f);
            mDesired.strafe  = j.value("strafe", 0.0f);
            mDesired.sprint  = j.value("sprint", false);
            mDesired.sneak   = j.value("sneak", false);
            mDesired.jump    = j.value("jump", false);
            mDesired.rmb     = j.value("rmb", false);
            mDesired.lmb     = j.value("lmb", false);
            mDesired.active  = true;
            mDesiredTicks    = j.value("ticks", 1);
        }
        enqueue(R"({"event":"set_ok"})");
        return;
    }
    if (cmd == "jump") {
        {
            std::lock_guard lock(mInputMutex);
            mJumpRequested = true;
        }
        enqueue(R"({"event":"jump_ok"})");
        return;
    }
    if (cmd == "look") {
        {
            std::lock_guard lock(mLookMutex);
            mLookRelative = j.value("relative", true);
            mLookYaw      = j.value("yaw", 0.0f);
            mLookPitch    = j.value("pitch", 0.0f);
            mLookTicks    = j.value("ticks", 1);
        }
        enqueue(R"({"event":"look_ok"})");
        return;
    }
    if (cmd == "state") {
        auto const now = GetTickCount64();
        if (!mInWorld.load() || (mLastTickMs.load() != 0 && now - mLastTickMs.load() > 1000)) {
            enqueue(R"({"event":"state","in_world":false})");
        } else {
            mStateRequested = true;
        }
        return;
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
        enqueue(R"({"event":"input_cfg_ok"})");
        return;
    }
    if (cmd == "buttons") {
        mButtonsRequested = true;
        return;
    }
    if (cmd == "entities") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return;
        }
        {
            std::lock_guard lock(mEntityMutex);
            mEntityRadius      = j.value("radius", 16.0f);
            mEntityLimit       = std::clamp(j.value("limit", 64), 1, 512);
            mEntitiesRequested = true;
        }
        return;
    }
    if (cmd == "blocks") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return;
        }
        std::vector<BlockQuery> queries;
        if (j.contains("positions") && j["positions"].is_array()) {
            for (auto const& pos : j["positions"]) {
                if (!pos.is_array() || pos.size() != 3 || !pos[0].is_number() || !pos[1].is_number()
                    || !pos[2].is_number()) {
                    continue;
                }
                queries.push_back(
                    BlockQuery{
                        static_cast<int>(std::floor(pos[0].get<double>())),
                        static_cast<int>(std::floor(pos[1].get<double>())),
                        static_cast<int>(std::floor(pos[2].get<double>())),
                    }
                );
                if (queries.size() >= 256) {
                    break;
                }
            }
        }
        {
            std::lock_guard lock(mBlockMutex);
            mBlockQueries    = std::move(queries);
            mBlocksRequested = true;
        }
        return;
    }
    if (cmd == "select_slot") {
        mSlotRequest.store(std::clamp(j.value("slot", 0), 0, 8));
        enqueue(R"({"event":"select_slot_ok"})");
        return;
    }
    if (cmd == "inventory") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
        } else {
            mInventoryRequested = true;
        }
        return;
    }
    if (cmd == "set_item" || cmd == "clear_item") {
        auto const container = j.value("container", std::string{"inventory"});
        if (container != "inventory" && container != "armor" && container != "offhand") {
            enqueue(R"({"event":"error","reason":"bad_container"})");
            return;
        }
        bool const clear = cmd == "clear_item";
        queueInventoryWrite(
            container,
            j.value("slot", 0),
            clear ? std::string{} : j.value("item", std::string{}),
            clear ? 0 : j.value("count", 1),
            clear ? 0 : j.value("aux", 0)
        );
        return;
    }
    if (cmd == "give") {
        queueGive(j.value("item", std::string{}), j.value("count", 1), j.value("aux", 0));
        return;
    }
    if (cmd == "clear_inventory") {
        queueClearInventory();
        return;
    }
    if (cmd == "entity" || cmd == "attack_entity" || cmd == "interact_entity" || cmd == "look_at_entity") {
        EntityAction action;
        if (cmd == "entity") {
            action.kind = EntityAction::Kind::Info;
        } else if (cmd == "attack_entity") {
            action.kind = EntityAction::Kind::Attack;
        } else if (cmd == "interact_entity") {
            action.kind = EntityAction::Kind::Interact;
        } else {
            action.kind = EntityAction::Kind::LookAt;
        }
        action.runtimeId = j.value("runtime_id", std::uint64_t{0});
        {
            std::lock_guard lock(mEntityActionMutex);
            mEntityAction = action;
        }
        return;
    }
    if (cmd == "click") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return;
        }
        auto const button = j.value("button", std::string{"left"});
        mClickReach.store(j.value("reach", 5.5f));
        mClickRequest.store(button == "right" ? 2 : 1);
        return;
    }
    if (cmd == "navigate") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return;
        }
        NavigateTask task;
        task.x         = j.value("x", 0.0f);
        task.z         = j.value("z", 0.0f);
        task.tolerance = std::max(0.05f, j.value("tolerance", 0.35f));
        task.sprint    = j.value("sprint", false);
        task.maxTicks  = std::clamp(j.value("max_ticks", 600), 1, 200000);
        {
            std::lock_guard lock(mTaskMutex);
            ++mTaskEpoch;
            mNavigateTask = task;
            mMineTask.reset();
            mPlaceTask.reset();
            mUseTask.reset();
        }
        beginTask();
        enqueue(
            nlohmann::json{
                {"event",     "navigate_started"},
                {"x",         task.x            },
                {"z",         task.z            },
                {"tolerance", task.tolerance    },
                {"max_ticks", task.maxTicks     },
        }
                .dump()
        );
        return;
    }
    if (cmd == "mine") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return;
        }
        MineTask task;
        task.x        = j.value("x", 0);
        task.y        = j.value("y", 0);
        task.z        = j.value("z", 0);
        task.maxTicks = std::clamp(j.value("max_ticks", 400), 1, 200000);
        {
            std::lock_guard lock(mTaskMutex);
            ++mTaskEpoch;
            mMineTask = task;
            mNavigateTask.reset();
            mPlaceTask.reset();
            mUseTask.reset();
        }
        beginTask();
        enqueue(
            nlohmann::json{
                {"event",     "mine_started"},
                {"x",         task.x        },
                {"y",         task.y        },
                {"z",         task.z        },
                {"max_ticks", task.maxTicks },
        }
                .dump()
        );
        return;
    }
    if (cmd == "place") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return;
        }
        PlaceTask task;
        task.x        = j.value("x", 0);
        task.y        = j.value("y", 0);
        task.z        = j.value("z", 0);
        task.maxTicks = std::clamp(j.value("max_ticks", 30), 2, 200);
        task.expected = j.value("expected", std::string{});
        task.slot     = j.value("slot", -1);
        if (task.slot < -1 || task.slot > 8) {
            task.slot = -1;
        }
        if (j.contains("face")) {
            task.face = std::clamp(j.value("face", -1), -1, 5);
        }
        if (j.contains("against") && j["against"].is_array() && j["against"].size() == 3) {
            task.againstX    = static_cast<int>(std::floor(j["against"][0].get<double>()));
            task.againstY    = static_cast<int>(std::floor(j["against"][1].get<double>()));
            task.againstZ    = static_cast<int>(std::floor(j["against"][2].get<double>()));
            task.autoAgainst = false;
            if (task.face < 0) {
                task.face = faceBetween(
                    BlockPos{task.againstX, task.againstY, task.againstZ},
                    BlockPos{task.x, task.y, task.z}
                );
            }
        }
        {
            std::lock_guard lock(mTaskMutex);
            ++mTaskEpoch;
            mPlaceTask = task;
            mNavigateTask.reset();
            mMineTask.reset();
            mUseTask.reset();
        }
        beginTask();
        enqueue(
            nlohmann::json{
                {"event",     "place_started"},
                {"x",         task.x         },
                {"y",         task.y         },
                {"z",         task.z         },
                {"face",      task.face      },
                {"max_ticks", task.maxTicks  },
        }
                .dump()
        );
        return;
    }
    if (cmd == "use") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return;
        }
        UseTask task;
        task.ticks = std::clamp(j.value("ticks", 32), 1, 2000);
        {
            std::lock_guard lock(mTaskMutex);
            ++mTaskEpoch;
            mUseTask = task;
            mNavigateTask.reset();
            mMineTask.reset();
            mPlaceTask.reset();
        }
        beginTask();
        enqueue(
            nlohmann::json{
                {"event", "use_started"},
                {"ticks", task.ticks   }
        }.dump()
        );
        return;
    }
    if (cmd == "task_status") {
        enqueue(taskStatusJson().dump());
        return;
    }
    if (cmd == "task_stop") {
        cancelTasks();
        beginTask();
        enqueue(R"({"event":"task_stopped"})");
        return;
    }
    if (cmd == "fly") {
        mFlyRequest.store(j.value("enabled", true) ? 1 : 0);
        enqueue(R"({"event":"fly_ok"})");
        return;
    }
    if (cmd == "teleport") {
        {
            std::lock_guard lock(mTeleportMutex);
            mTeleportRequest.x           = j.value("x", 0.0f);
            mTeleportRequest.y           = j.value("y", 0.0f);
            mTeleportRequest.z           = j.value("z", 0.0f);
            mTeleportRequest.hasRotation = j.contains("yaw") || j.contains("pitch");
            mTeleportRequest.yaw         = j.value("yaw", 0.0f);
            mTeleportRequest.pitch       = j.value("pitch", 0.0f);
            mTeleportRequested           = true;
        }
        // "teleported" is emitted from the server thread once the move is applied.
        return;
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
            return;
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
        return;
    }
    if (cmd == "macro_run") {
        std::shared_ptr<Macro> macro;
        {
            std::lock_guard lock(mMacroMutex);
            macro = mMacro;
        }
        if (!macro || macro->empty()) {
            enqueue(R"({"event":"error","reason":"no_macro_loaded"})");
            return;
        }
        if (mMacroPlaying.load()) {
            enqueue(R"({"event":"error","reason":"macro_already_playing"})");
            return;
        }
        int start = std::clamp(j.value("start", 0), 0, static_cast<int>(macro->size()) - 1);
        int end   = j.value("end", static_cast<int>(macro->size()));
        end       = std::clamp(end, start + 1, static_cast<int>(macro->size()));
        mMacroStart.store(start);
        mMacroEnd.store(end);
        mMacroIndex.store(start);
        mMacroLoop.store(j.value("loop", false));
        mMacroStartRequested = true;
        enqueue(
            nlohmann::json{
                {"event", "macro_started"  },
                {"start", start            },
                {"end",   end              },
                {"ticks", end - start      },
                {"loop",  mMacroLoop.load()},
        }
                .dump()
        );
        return;
    }
    if (cmd == "macro_stop") {
        mMacroPlaying       = false;
        mMacroStopRequested = true;
        enqueue(R"({"event":"macro_stopped","reason":"client"})");
        return;
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
        return;
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
        return;
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
        return;
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
            return;
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
            return;
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
        return;
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
            return;
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
            return;
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
        return;
    }
    if (cmd == "status") {
        nlohmann::json out;
        out["event"]           = "status";
        out["in_world"]        = mInWorld.load();
        out["recording"]       = mRecording.load();
        out["tick"]            = mTick.load();
        out["macro_playing"]   = mMacroPlaying.load();
        out["macro_index"]     = mMacroIndex.load();
        out["macro_recording"] = mMacroRecording.load();
        out["queue_dropped"]   = mQueueDropped.load();
        enqueue(out.dump());
        return;
    }
    enqueue(R"({"event":"error","reason":"unknown_cmd"})");
}


} // namespace probe
