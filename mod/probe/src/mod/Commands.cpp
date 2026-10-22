#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

// True while dispatchWithSeh owns the current exception. The crash VEH checks
// this so a deliberately faulting request is handled as an internal_error
// instead of producing a minidump.
thread_local bool gDispatchSehActive = false;

void ProbeMod::recordCommand(std::string const& line) {
    std::size_t const length = line.size() < sizeof(mLastCommand) - 1 ? line.size() : sizeof(mLastCommand) - 1;
    std::memcpy(mLastCommand, line.data(), length);
    mLastCommand[length] = '\0';
}

bool ProbeMod::dispatchWithSeh(std::string const& line) {
#if defined(_MSC_VER)
    // A bad memory access from one handler must only fail that request. The
    // filter lets C++ exceptions through to the caller's try/catch.
    __try {
        dispatchLine(line);
        return true;
    } __except (isFatalSehCode(GetExceptionCode()) ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH) {
        return false;
    }
#else
    try {
        dispatchLine(line);
        return true;
    } catch (...) {
        return false;
    }
#endif
}

void ProbeMod::handleLine(std::string const& line) {
    recordCommand(line);
    // Any structured exception raised while handling this command belongs to
    // the request, not to the process. The flag must be visible to the crash
    // VEH, which runs before the __except filter below.
    struct DispatchFlag {
        DispatchFlag() { gDispatchSehActive = true; }
        ~DispatchFlag() { gDispatchSehActive = false; }
    } dispatchFlag;
    // nlohmann throws on type mismatches, and a bad line from a script must not
    // kill the pipe thread (which would make the whole harness unreachable).
    try {
        if (!dispatchWithSeh(line)) {
            enqueue(
                R"({"event":"error","reason":"internal_error","phase":"dispatch","message":"structured exception"})"
            );
        }
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
        enqueue(
            R"({"event":"pong","mod":"probe","protocol":4,"capabilities":["tick_json","bin_record","mem","watch","signin","hook"]})"
        );
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
    if (cmd == "mem_read") {
        nlohmann::json out;
        out["event"] = "mem_read";
        std::uint64_t addr = 0;
        if (!j.contains("addr") || !parseAddress(j["addr"], addr)) {
            out["ok"]     = false;
            out["reason"] = "bad_addr";
            enqueue(out.dump());
            return;
        }
        std::size_t len = std::clamp<std::size_t>(j.value("len", 16), 1, 4096);
        std::vector<std::uint8_t> bytes(len);
        out["addr"] = addr;
        out["len"]  = len;
        if (!readMemory(addr, bytes.data(), bytes.size())) {
            out["ok"]     = false;
            out["reason"] = "bad_addr";
        } else {
            out["ok"]  = true;
            out["hex"] = hexEncode(bytes.data(), bytes.size());
        }
        enqueue(out.dump());
        return;
    }
    if (cmd == "mem_write") {
        if (!mAllowMemoryWrite.load(std::memory_order_relaxed)) {
            enqueue(R"({"event":"error","reason":"write_disabled","hint":"set allow_write=true in harness.json"})");
            return;
        }
        nlohmann::json out;
        out["event"] = "mem_write";
        std::uint64_t addr = 0;
        if (!j.contains("addr") || !parseAddress(j["addr"], addr)) {
            out["ok"]     = false;
            out["reason"] = "bad_addr";
            enqueue(out.dump());
            return;
        }
        std::vector<std::uint8_t> bytes;
        if (!j.contains("hex") || !parseHexBytes(j.value("hex", std::string{}), bytes) || bytes.empty()) {
            out["ok"]     = false;
            out["reason"] = "bad_hex";
            enqueue(out.dump());
            return;
        }
        if (bytes.size() > 4096) {
            out["ok"]     = false;
            out["reason"] = "too_long";
            enqueue(out.dump());
            return;
        }
        if (!writeMemory(addr, bytes.data(), bytes.size())) {
            out["ok"]     = false;
            out["reason"] = "bad_addr";
        } else {
            out["ok"]      = true;
            out["addr"]    = addr;
            out["written"] = bytes.size();
            // Single-line audit trail: strong capabilities must leave a trace.
            bootLog(
                "mem_write addr=" + std::to_string(addr) + " len=" + std::to_string(bytes.size())
                + " hex=" + hexEncode(bytes.data(), bytes.size() > 64 ? 64 : bytes.size())
            );
        }
        enqueue(out.dump());
        return;
    }
    if (cmd == "probe") {
        nlohmann::json out;
        out["event"] = "probe";
        std::uint64_t addr = 0;
        if (!j.contains("addr") || !parseAddress(j["addr"], addr)) {
            out["ok"]     = false;
            out["reason"] = "bad_addr";
            enqueue(out.dump());
            return;
        }
        std::string const kind = j.value("kind", std::string{"raw"});
        std::size_t const len  = std::clamp<std::size_t>(j.value("len", 16), 1, 64);
        enqueue(probeMemory(addr, kind, len).dump());
        return;
    }
    if (cmd == "watch_set") {
        nlohmann::json out;
        out["event"] = "watch_set";
        std::uint64_t addr = 0;
        if (!j.contains("addr") || !parseAddress(j["addr"], addr)) {
            out["ok"]     = false;
            out["reason"] = "bad_addr";
            enqueue(out.dump());
            return;
        }
        std::uint32_t size = 1;
        if (j.contains("size") && !parseSize(j["size"], size)) {
            out["ok"]     = false;
            out["reason"] = "bad_size";
            enqueue(out.dump());
            return;
        }
        std::string const mode = j.value("mode", std::string{"w"});
        std::string       thread = "tick";
        if (j.contains("thread")) {
            if (j["thread"].is_string()) {
                thread = j["thread"].get<std::string>();
            } else if (j["thread"].is_number_integer()) {
                thread = std::to_string(j["thread"].get<long long>());
            }
        }
        std::string const tag = j.value("tag", std::string{});
        enqueue(watchInstall(addr, size, mode == "rw", thread, tag).dump());
        return;
    }
    if (cmd == "watch_clear") {
        if (j.value("all", false)) {
            bool const cleared = watchRemoveAll();
            enqueue(
                nlohmann::json{
                    {"event", "watch_cleared"},
                    {"all",   true           },
                    {"ok",    cleared        },
            }
                    .dump()
            );
            return;
        }
        int const id = j.value("id", -1);
        enqueue(watchRemove(id).dump());
        return;
    }
    if (cmd == "watch_list") {
        enqueue(watchListJson().dump());
        return;
    }
    if (cmd == "watch_drain") {
        std::uint64_t const since =
            j.contains("since") ? j.value("since", std::uint64_t{0}) : std::uint64_t{0};
        std::size_t const limit =
            std::clamp<std::size_t>(j.value("limit", 4096), 1, 65536);
        enqueue(watchDrainJson(since, limit).dump());
        return;
    }
    if (cmd == "hook_install") {
        bool const  useVa  = j.contains("va") && !j["va"].is_null();
        std::string symbol = j.value("symbol", std::string{});
        std::uint64_t va   = 0;
        if (useVa && !parseAddress(j["va"], va)) {
            enqueue(R"({"event":"error","reason":"bad_va"})");
            return;
        }
        if (!useVa && symbol.empty()) {
            enqueue(R"({"event":"error","reason":"missing_symbol"})");
            return;
        }
        std::uint32_t const sample =
            std::clamp(j.value("sample", 1), 1, 1000000);
        bool const dryRun = j.value("dry_run", false);
        std::string const tag = j.value("tag", std::string{});
        enqueue(
            hookInstall(
                symbol,
                va,
                useVa,
                tag,
                sample,
                dryRun,
                mAllowHookVa.load(std::memory_order_relaxed)
            )
                .dump()
        );
        return;
    }
    if (cmd == "hook_rm") {
        enqueue(hookRemove(j.value("id", -1)).dump());
        return;
    }
    if (cmd == "panic" || cmd == "unhook_all") {
        std::string const level =
            cmd == "unhook_all" ? std::string{"hooks"} : j.value("level", std::string{"all"});
        if (level != "hooks" && level != "tasks" && level != "all") {
            enqueue(R"({"event":"error","reason":"bad_panic_level","levels":["hooks","tasks","all"]})");
            return;
        }
        bool const        hooksLevel = level == "hooks" || level == "all";
        bool const        tasksLevel = level == "tasks" || level == "all";
        std::size_t const watchesBefore = hooksLevel ? watchCount() : 0;
        int const         hooksRemoved  = hooksLevel ? hookRemoveAll() : 0;
        std::size_t       watchesRemoved = 0;
        bool              watchesOk      = true;
        if (hooksLevel) {
            watchesOk      = watchRemoveAll();
            watchesRemoved = watchesOk ? watchesBefore : 0;
        }
        if (tasksLevel) {
            cancelTasks();
            beginTask();
        }
        enqueue(
            nlohmann::json{
                {"event",            "panic_done"       },
                {"level",            level              },
                {"ok",               watchesOk          },
                {"hooks_removed",    hooksRemoved       },
                {"watches_cleared",  watchesRemoved     },
                {"tasks_cancelled",  tasksLevel         },
        }
                .dump()
        );
        return;
    }
    if (cmd == "hook_list") {
        enqueue(hookListJson().dump());
        return;
    }
    if (cmd == "hook_drain") {
        std::uint64_t const since =
            j.contains("since") ? j.value("since", std::uint64_t{0}) : std::uint64_t{0};
        std::size_t const limit = std::clamp<std::size_t>(j.value("limit", 4096), 1, 65536);
        enqueue(hookDrainJson(since, limit).dump());
        return;
    }
    if (cmd == "hook_call") {
        std::string const symbol = j.value("symbol", std::string{});
        float const       scale  = j.value("scale", 1.0f);
        std::string       error;
        if (!hookRequestCall(symbol, scale, error)) {
            enqueue(
                nlohmann::json{
                    {"event",  "error"      },
                    {"reason", error        },
            }
                    .dump()
            );
        }
        return;
    }
    if (cmd == "spool_flush") {
        flushSpool(true);
        enqueue(
            nlohmann::json{
                {"event", "spool_flushed"      },
                {"path",  mSpoolPath.string()  },
                {"rows",  mSpoolRows.load()    },
        }
                .dump()
        );
        return;
    }
    if (cmd == "selftest_crash") {
        // Verifies the unhandled-exception filter and minidump writer. This
        // deliberately terminates the game; it is gated behind a config flag.
        if (!mAllowCrashTest.load(std::memory_order_relaxed)) {
            enqueue(R"({"event":"error","reason":"crash_test_disabled","hint":"set allow_crash_test=true"})");
            return;
        }
        enqueue(R"({"event":"selftest_crash_started"})");
        std::thread([] { RaiseException(EXCEPTION_ACCESS_VIOLATION, 0, 0, nullptr); }).detach();
        return;
    }
    if (cmd == "selftest_av") {
        // Isolation check: this is caught by dispatchWithSeh and must only fail
        // this request. RaiseException is deterministic (the optimizer cannot
        // delete it like a null dereference).
        RaiseException(EXCEPTION_ACCESS_VIOLATION, 0, 0, nullptr);
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
        flushSpool(true);
        enqueue(R"({"event":"stopped","reason":"client"})");
        return;
    }
    if (cmd == "record_start") {
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
            return;
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
        return;
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
        return;
    }
    if (cmd == "record_dump") {
        std::uint64_t const since =
            j.contains("since") ? j.value("since", std::uint64_t{0}) : std::uint64_t{0};
        std::size_t const limit = std::clamp<std::size_t>(j.value("limit", 1024), 1, 8192);
        std::string const format = j.value("format", std::string{"jsonl"});
        bool const        binary = format == "bin" || format == "hex" || format == "binary";
        enqueue(recordDumpJson(since, limit, binary).dump());
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
        if (j.contains("jump_impulse")) {
            mJumpImpulseMode.store(std::clamp(j.value("jump_impulse", 1), 0, 3));
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
    if (cmd == "scan") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return;
        }
        auto readCorner = [&](char const* key, int& x, int& y, int& z) -> bool {
            if (!j.contains(key) || !j[key].is_array() || j[key].size() != 3) {
                return false;
            }
            for (auto const& value : j[key]) {
                if (!value.is_number()) {
                    return false;
                }
            }
            x = static_cast<int>(std::floor(j[key][0].get<double>()));
            y = static_cast<int>(std::floor(j[key][1].get<double>()));
            z = static_cast<int>(std::floor(j[key][2].get<double>()));
            return true;
        };
        ScanRequest request;
        if (!readCorner("from", request.x1, request.y1, request.z1)
            || !readCorner("to", request.x2, request.y2, request.z2)) {
            enqueue(R"({"event":"error","reason":"bad_region"})");
            return;
        }
        std::int64_t const dx = std::llabs(static_cast<std::int64_t>(request.x2) - request.x1) + 1;
        std::int64_t const dy = std::llabs(static_cast<std::int64_t>(request.y2) - request.y1) + 1;
        std::int64_t const dz = std::llabs(static_cast<std::int64_t>(request.z2) - request.z1) + 1;
        if (dx * dy * dz > 32768) {
            enqueue(R"({"event":"error","reason":"region_too_large","limit":32768})");
            return;
        }
        request.includeAir = j.value("include_air", false);
        request.limit      = std::clamp(j.value("limit", 4096), 1, 16384);
        {
            std::lock_guard lock(mScanMutex);
            mScanRequest   = request;
            mScanRequested = true;
        }
        return;
    }
    if (cmd == "checkpoint_save") {
        checkpointSave(j.value("name", std::string{}));
        return;
    }
    if (cmd == "checkpoint_load") {
        checkpointLoad(j.value("name", std::string{}));
        return;
    }
    if (cmd == "checkpoint_list") {
        checkpointList();
        return;
    }
    if (cmd == "checkpoint_delete") {
        checkpointDelete(j.value("name", std::string{}));
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
    if (cmd == "ecs_registries") {
        EcsRequest request;
        request.kind = EcsRequest::Kind::Registries;
        {
            std::lock_guard lock(mEcsMutex);
            mEcsRequest = request;
        }
        return;
    }
    if (cmd == "ecs_entity" || cmd == "ecs_components" || cmd == "ecs_component") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return;
        }
        EcsRequest request;
        request.runtimeId = j.value("runtime_id", std::uint64_t{0});
        if (cmd == "ecs_entity") {
            request.kind = EcsRequest::Kind::Entity;
        } else if (cmd == "ecs_components") {
            request.kind = EcsRequest::Kind::Components;
        } else {
            request.kind        = EcsRequest::Kind::Component;
            request.componentId = j.value("component_id", std::uint32_t{0});
            request.length      = j.value("len", std::uint32_t{0});
        }
        {
            std::lock_guard lock(mEcsMutex);
            mEcsRequest = request;
        }
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
    if (cmd == "enter_world") {
        std::string name = j.value("name", std::string{});
        std::string id   = j.value("id", std::string{});
        if (name.empty() && id.empty()) {
            enqueue(R"({"event":"error","reason":"missing_world"})");
            return;
        }
        enterWorld(
            name,
            id,
            std::clamp(j.value("intent", 0), 0, 16),
            j.value("force", false)
        );
        return;
    }
    if (cmd == "signin") {
        reportSignInState();
        return;
    }
    if (cmd == "leave_world") {
        leaveWorld();
        return;
    }
    if (cmd == "worlds") {
        listWorlds();
        return;
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
            return;
        }
        setOptions(text);
        return;
    }
    if (cmd == "options_save") {
        saveOptions();
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
        std::string const jump = j.value("jump", std::string{"auto"});
        if (jump == "distance") {
            task.jumpMode = JumpMode::Distance;
        } else if (jump == "edge") {
            task.jumpMode = JumpMode::Edge;
        } else if (jump == "never") {
            task.jumpMode = JumpMode::Never;
        }
        task.jumpDistance  = std::clamp(j.value("jump_distance", 1.0f), 0.1f, 8.0f);
        task.edgeLookahead = std::clamp(j.value("edge_lookahead", 0.55f), 0.2f, 1.5f);
        {
            std::lock_guard lock(mTaskMutex);
            ++mTaskEpoch;
            mNavigateTask = task;
            mMineTask.reset();
            mPlaceTask.reset();
            mUseTask.reset();
            mJumpTask.reset();
            mRouteTask.reset();
        }
        beginTask();
        enqueue(
            nlohmann::json{
                {"event",     "navigate_started"},
                {"x",         task.x            },
                {"z",         task.z            },
                {"tolerance", task.tolerance    },
                {"max_ticks", task.maxTicks     },
                {"jump",      jump              },
                {"jump_distance", task.jumpDistance},
        }
                .dump()
        );
        return;
    }
    if (cmd == "jump_when") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return;
        }
        JumpTask task;
        std::string const mode = j.value("mode", std::string{"now"});
        if (mode == "distance") {
            task.mode = JumpMode::Distance;
        } else if (mode == "edge") {
            task.mode = JumpMode::Edge;
        } else if (mode == "tick") {
            task.mode = JumpMode::Tick;
        } else {
            task.mode = JumpMode::Now;
        }
        task.x             = j.value("x", 0.0f);
        task.z             = j.value("z", 0.0f);
        task.distance      = std::clamp(j.value("distance", 0.6f), 0.05f, 8.0f);
        task.forward       = std::clamp(j.value("forward", 0.0f), -1.0f, 1.0f);
        task.sprint        = j.value("sprint", false);
        task.hasYaw        = j.contains("yaw");
        task.yaw           = j.value("yaw", 0.0f);
        task.afterTicks    = std::clamp(j.value("after_ticks", 1), 1, 2000);
        task.maxTicks      = std::clamp(j.value("max_ticks", 60), 1, 5000);
        task.edgeLookahead = std::clamp(j.value("edge_lookahead", 0.55f), 0.2f, 1.5f);
        {
            std::lock_guard lock(mTaskMutex);
            ++mTaskEpoch;
            mJumpTask = task;
            mNavigateTask.reset();
            mRouteTask.reset();
            mMineTask.reset();
            mPlaceTask.reset();
            mUseTask.reset();
        }
        beginTask();
        enqueue(
            nlohmann::json{
                {"event",       "jump_when_started"},
                {"mode",        mode              },
                {"x",           task.x            },
                {"z",           task.z            },
                {"distance",    task.distance     },
                {"forward",     task.forward      },
                {"sprint",      task.sprint       },
                {"after_ticks", task.afterTicks   },
                {"max_ticks",   task.maxTicks     },
        }
                .dump()
        );
        return;
    }
    if (cmd == "route") {
        if (!mInWorld.load()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return;
        }
        if (!j.contains("waypoints") || !j["waypoints"].is_array() || j["waypoints"].empty()) {
            enqueue(R"({"event":"error","reason":"missing_waypoints"})");
            return;
        }
        RouteTask task;
        task.sprint = j.value("sprint", false);
        for (auto const& row : j["waypoints"]) {
            if (!row.is_object()) {
                continue;
            }
            RouteWaypoint point;
            point.x         = row.value("x", 0.0f);
            point.z         = row.value("z", 0.0f);
            point.tolerance = std::clamp(row.value("tolerance", 0.35f), 0.05f, 2.0f);
            point.maxTicks  = std::clamp(row.value("max_ticks", 600), 1, 200000);
            std::string const jump = row.value("jump", std::string{"auto"});
            if (jump == "distance") {
                point.jumpMode = JumpMode::Distance;
            } else if (jump == "edge") {
                point.jumpMode = JumpMode::Edge;
            } else if (jump == "never") {
                point.jumpMode = JumpMode::Never;
            }
            point.jumpDistance = std::clamp(row.value("jump_distance", 1.0f), 0.1f, 8.0f);
            task.points[task.count++] = point;
            if (task.count >= RouteTask::MaxWaypoints) {
                break;
            }
        }
        if (task.count == 0) {
            enqueue(R"({"event":"error","reason":"missing_waypoints"})");
            return;
        }
        {
            std::lock_guard lock(mTaskMutex);
            ++mTaskEpoch;
            mRouteTask = task;
            mNavigateTask.reset();
            mJumpTask.reset();
            mMineTask.reset();
            mPlaceTask.reset();
            mUseTask.reset();
        }
        beginTask();
        enqueue(
            nlohmann::json{
                {"event",     "route_started"    },
                {"waypoints", task.count         },
                {"sprint",    task.sprint        },
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
            mJumpTask.reset();
            mRouteTask.reset();
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
        out["spool"]           = mSpoolPath.string();
        out["spool_rows"]      = mSpoolRows.load();
        out["spool_flushes"]   = mSpoolFlushes.load();
        out["allow_write"]     = mAllowMemoryWrite.load();
        out["tick_thread_id"]  = mTickThreadId.load();
        out["tick_probe_addr"] =
            static_cast<std::uint64_t>(reinterpret_cast<std::uintptr_t>(&mTickProbe));
        out["watch_count"]   = watchCount();
        out["watch_hits"]    = watchTotalHits();
        out["watch_dropped"] = watchDroppedHits();
        out["record_active"]   = recordActive();
        out["record_rows"]     = recordRows();
        out["record_head"]     = recordHead();
        out["record_period"]   = recordPeriod();
        out["record_capacity"] = RecordCapacity;
        enqueue(out.dump());
        return;
    }
    enqueue(R"({"event":"error","reason":"unknown_cmd"})");
}


} // namespace probe
