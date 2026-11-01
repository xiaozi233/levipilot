#include "mod/Internal.h"
#include "mod/PipeChannel.h"

using namespace probe::detail;

namespace probe {

bool ProbeMod::dispatchDiagnosticsCommand(std::string const& cmd, nlohmann::json& j) {
    if (cmd == "selftest_tick_error") {
        if (!mAllowSelftestStress.load()) {
            enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
            return true;
        }
        auto const mode = j.value("mode", std::string{});
        if (mode == "off") {
            for (auto& test : mTickErrorTests) test = TickErrorTest::None;
            mTickRecoveryHoldUntilMs = 0;
        } else {
            if (!mAllowInputInject.load() || !mWorld.inWorld() || mTickRecovery.active()) {
                enqueue(R"({"event":"error","reason":"tick_test_unavailable"})");
                return true;
            }
            auto const phase = j.value("phase", std::string{});
            if (phase != "pre" && phase != "post") {
                enqueue(R"({"event":"error","reason":"bad_test_phase"})");
                return true;
            }
            TickErrorTest test;
            if (mode == "std") test = TickErrorTest::Standard;
            else if (mode == "unknown") test = TickErrorTest::Unknown;
            else if (mode == "long") test = TickErrorTest::LongMessage;
            else if (mode == "invalid") test = TickErrorTest::InvalidMessage;
            else { enqueue(R"({"event":"error","reason":"bad_test_mode"})"); return true; }
            auto const hold = std::clamp(j.value("hold_ms", 0), 0, 2000);
            mTickRecoveryHoldUntilMs = GetTickCount64() + hold;
            mTickErrorTests[phase == "pre" ? 0 : 1] = test;
        }
        enqueue(R"({"event":"tick_error_test_armed"})");
        return true;
    }
    if (cmd == "ping") {
        enqueue(
            R"({"event":"pong","mod":"probe","protocol":4,"capabilities":["tick_json","bin_record","mem","watch","signin","hook"]})"
        );
        return true;
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
        // Roles are static VAs under docs/roles' documented image base; this is
        // where the running image actually sits (ASLR / packed preferred base).
        out["module_base"]     = gameModuleBase();
        enqueue(out.dump());
        return true;
    }
    if (cmd == "violation_watch") {
        // Off by default; the address comes from docs/roles, never from a
        // constant here. Synthetic mode publishes one self-test row so the
        // record/drain plumbing can be checked without a real violation.
        auto const mode = j.value("mode", std::string{});
        if (mode == "synthetic") {
            enqueue(violationWatchSynthetic().dump());
            return true;
        }
        if (!j.value("on", true)) {
            enqueue(violationWatchStop().dump());
            return true;
        }
        std::uint64_t address = 0;
        if (j.contains("addr")) {
            auto const& value = j["addr"];
            if (value.is_string()) {
                try {
                    address = std::stoull(value.get<std::string>(), nullptr, 16);
                } catch (...) {
                    enqueue(R"({"event":"error","reason":"bad_addr"})");
                    return true;
                }
            } else if (value.is_number_unsigned()) {
                address = value.get<std::uint64_t>();
            } else {
                enqueue(R"({"event":"error","reason":"bad_addr"})");
                return true;
            }
        }
        if (address == 0) {
            enqueue(R"({"event":"error","reason":"bad_addr"})");
            return true;
        }
        std::uint64_t loopAddress = 0;
        if (j.contains("loop_addr")) {
            auto const& value = j["loop_addr"];
            if (value.is_string()) {
                try {
                    loopAddress = std::stoull(value.get<std::string>(), nullptr, 16);
                } catch (...) {
                    loopAddress = 0;
                }
            } else if (value.is_number_unsigned()) {
                loopAddress = value.get<std::uint64_t>();
            }
        }
        std::uint64_t assignAddress = 0;
        if (j.contains("assign_addr")) {
            auto const& value = j["assign_addr"];
            if (value.is_string()) {
                try {
                    assignAddress = std::stoull(value.get<std::string>(), nullptr, 16);
                } catch (...) {
                    assignAddress = 0;
                }
            } else if (value.is_number_unsigned()) {
                assignAddress = value.get<std::uint64_t>();
            }
        }
        std::uint64_t dispatchAddress = 0;
        if (j.contains("dispatch_addr")) {
            auto const& value = j["dispatch_addr"];
            if (value.is_string()) {
                try {
                    dispatchAddress = std::stoull(value.get<std::string>(), nullptr, 16);
                } catch (...) {
                    dispatchAddress = 0;
                }
            } else if (value.is_number_unsigned()) {
                dispatchAddress = value.get<std::uint64_t>();
            }
        }
        enqueue(violationWatchStart(
                    address,
                    mAllowHookVa.load(std::memory_order_relaxed),
                    mWorld.inWorld(),
                    loopAddress,
                    assignAddress,
                    dispatchAddress
        ).dump());
        return true;
    }
    if (cmd == "disconnect_route") {
        enqueue(disconnectRouteProbe(
                    j.value("reason", 90),
                    j.value("codeword", std::string{"Block"}),
                    j.value("details", std::string{})
        ).dump());
        return true;
    }
    if (cmd == "inbound_dump") {
        // Write the ring to disk instead of the pipe: with more than one client
        // attached the reply can be delivered to another connection, so the
        // evidence must not depend on who is listening.
        std::string const path = j.value("path", std::string{"inbound-dump.json"});
        std::ofstream     file(path, std::ios::binary | std::ios::trunc);
        if (!file) {
            enqueue(nlohmann::json{{"event", "error"}, {"reason", "open_failed"},
                                   {"path", path}}.dump());
            return true;
        }
        file << inboundDrainJson(0, 1024).dump();
        file.flush();
        enqueue(nlohmann::json{{"event", "inbound_dump"}, {"ok", true}, {"path", path}}.dump());
        return true;
    }
    if (cmd == "inbound_drain") {
        std::uint64_t const since = j.value("since", std::uint64_t{0});
        std::size_t const   limit = std::clamp<std::size_t>(j.value("limit", 512), 1, 1024);
        enqueue(inboundDrainJson(since, limit).dump());
        return true;
    }
    if (cmd == "violation_text") {
        std::uint64_t const since = j.value("since", std::uint64_t{0});
        std::size_t const   limit = std::clamp<std::size_t>(j.value("limit", 8), 1, 8);
        enqueue(violationTextJson(since, limit).dump());
        return true;
    }
    if (cmd == "memmove_probe") {
        enqueue(memmoveProbeJson(j.value("on", true), j.value("id", 0)).dump());
        return true;
    }
    if (cmd == "inbound_sample") {
        enqueue(inboundSampleJson(j.value("sample", 4)).dump());
        return true;
    }
    if (cmd == "violation_stats") {
        enqueue(violationWatchStats().dump());
        return true;
    }
    if (cmd == "violation_drain") {
        std::uint64_t const since = j.value("since", std::uint64_t{0});
        std::size_t const   limit = std::clamp<std::size_t>(j.value("limit", 512), 1, 256);
        enqueue(violationDrainJson(since, limit).dump());
        return true;
    }
    if (cmd == "diagnose") {
        enqueue(diagnoseJson().dump());
        return true;
    }
    if (cmd == "profile_dump") {
        enqueue(profileDump().dump());
        return true;
    }
    if (cmd == "profile_probe") {
        enqueue(profileProbe(j.value("build_id", std::string{})).dump());
        return true;
    }
    if (cmd == "signature_check") {
        enqueue(
            signatureCheck(
                j.value("name", std::string{}),
                j.value("expected_hex", std::string{})
            )
                .dump()
        );
        return true;
    }
    if (cmd == "panic" || cmd == "unhook_all") {
        std::string const level =
            cmd == "unhook_all" ? std::string{"hooks"} : j.value("level", std::string{"all"});
        if (level != "hooks" && level != "tasks" && level != "all") {
            enqueue(R"({"event":"error","reason":"bad_panic_level","levels":["hooks","tasks","all"]})");
            return true;
        }
        bool const        hooksLevel = level == "hooks" || level == "all";
        bool const        tasksLevel = level == "tasks" || level == "all";
        std::size_t const watchesBefore = hooksLevel ? watchCount() : 0;
        int const         hooksRemoved  = hooksLevel ? hookRemoveAll() : 0;
        std::size_t       watchesRemoved = 0;
        bool              watchesOk      = true;
        bool              violationStopped = true;
        if (hooksLevel) {
            packetCaptureStop();
            violationStopped = !violationWatchStop().value("active", true);
            watchesOk      = watchRemoveAll();
            watchesRemoved = watchesOk ? watchesBefore : 0;
        }
        if (tasksLevel) {
            cancelPacketInjection("panic");
            cancelTeleport("panic");
            cancelTasks("panic");
            beginTask();
        }
        // F4: panic is usually called before an incident, so make sure the
        // evidence already queued reaches disk before we answer.
        std::uint64_t const errorsBefore = mSpoolErrors.load(std::memory_order_relaxed);
        std::uint64_t const flushedRows  = flushSpool(true);
        bool const          flushOk      = mSpoolErrors.load(std::memory_order_relaxed) == errorsBefore;
        enqueue(
            nlohmann::json{
                {"event",            "panic_done"       },
                {"level",            level              },
                {"ok",               watchesOk && violationStopped},
                {"violation_watch_stopped", violationStopped},
                {"hooks_removed",    hooksRemoved       },
                {"watches_cleared",  watchesRemoved     },
                {"tasks_cancelled",  tasksLevel         },
                {"flushed_rows",     flushedRows        },
                {"flush_ok",         flushOk            },
        }
                .dump()
        );
        mSpoolForce.store(true, std::memory_order_release);
        return true;
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
        return true;
    }
    if (cmd == "selftest_crash") {
        // Verifies the unhandled-exception filter and minidump writer. This
        // deliberately terminates the game; it is gated behind a config flag.
        if (!mAllowCrashTest.load(std::memory_order_relaxed)) {
            enqueue(R"({"event":"error","reason":"crash_test_disabled","hint":"set allow_crash_test=true"})");
            return true;
        }
        enqueue(R"({"event":"selftest_crash_started"})");
        std::thread([] { RaiseException(EXCEPTION_ACCESS_VIOLATION, 0, 0, nullptr); }).detach();
        return true;
    }
    if (cmd == "selftest_av") {
        // Isolation check: this is caught by dispatchWithSeh and must only fail
        // this request. RaiseException is deterministic (the optimizer cannot
        // delete it like a null dereference).
        RaiseException(EXCEPTION_ACCESS_VIOLATION, 0, 0, nullptr);
        return true;
    }
    if (cmd == "overhead") {
        enqueue(overheadJson().dump());
        return true;
    }
    if (cmd == "selftest_chat_timeout") {
        if (!mAllowSelftestStress.load(std::memory_order_relaxed)) {
            enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
        } else {
            // Leave a real request pending without scheduling UI work. The
            // spool consumer must time it out and free the slot by itself.
            requestChatHistory(1, true);
        }
        return true;
    }
    if (cmd == "selftest_pose_capture") {
        if (!mAllowSelftestStress.load()) {
            enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
            return true;
        }
        if (!mWorld.inWorld()) {
            enqueue(R"({"event":"error","reason":"not_in_world"})");
            return true;
        }
        auto const mode = j.value("mode", std::string{});
        if (mode != "seh" && mode != "cpp" && mode != "off") {
            enqueue(R"({"event":"error","reason":"bad_test_mode"})");
            return true;
        }
        mPoseTest = mode == "seh" ? CaptureTest::Fault : mode == "cpp" ? CaptureTest::Cpp : CaptureTest::None;
        enqueue(R"({"event":"pose_capture_test_armed"})");
        return true;
    }
    if (cmd == "selftest_busy") {
        // F10: this can stall the tick for ~1 s, so it needs an explicit opt-in.
        if (!mAllowSelftestStress.load(std::memory_order_relaxed)) {
            enqueue(
                R"({"event":"error","reason":"stress_disabled","hint":"set allow_selftest_stress=true"})"
            );
            return true;
        }
        std::uint32_t const us    = std::clamp(j.value("us", 1000), 1, 5000);
        std::uint32_t const ticks = std::clamp(j.value("ticks", 5), 1, 200);
        overheadRequestBusy(us, ticks);
        enqueue(
            nlohmann::json{
                {"event", "busy_test_started"},
                {"us",    us                 },
                {"ticks", ticks              },
        }
                .dump()
        );
        return true;
    }
    if (cmd == "selftest_player_identity") {
        if (!mAllowSelftestStress.load()) {
            enqueue(R"({"event":"error","reason":"selftest_stress_disabled"})");
            return true;
        }
        auto const mode = j.value("mode", std::string{});
        if (mode == "remember") {
            auto const identity = localPlayerIdentity();
            if (!identity.valid) { enqueue(R"({"event":"error","reason":"identity_unavailable"})"); return true; }
            mPlayerIdentityRemembered = identity;
            enqueue(nlohmann::json{{"event", "identity_remembered"}, {"epoch", identity.epoch}}.dump());
        } else if (mode == "resolve") {
            auto const identity = mPlayerIdentityRemembered;
            queueServerTask([this, identity] {
                auto const context = serverPlayerContext(identity, mPoseEpoch.load(), mWorld.inWorld());
                enqueue(nlohmann::json{{"event", "identity_resolved"}, {"ok", context.player != nullptr},
                    {"epoch", identity.epoch}, {"current_epoch", mPoseEpoch.load()}}.dump());
            });
        } else {
            if (!mWorld.inWorld()) { enqueue(R"({"event":"error","reason":"not_in_world"})"); return true; }
            CaptureTest test;
            if (mode == "seh") test = CaptureTest::Fault;
            else if (mode == "cpp") test = CaptureTest::Cpp;
            else if (mode == "zero") test = CaptureTest::Partial;
            else if (mode == "off") test = CaptureTest::None;
            else { enqueue(R"({"event":"error","reason":"bad_test_mode"})"); return true; }
            mPlayerIdentityTest = test;
            enqueue(R"({"event":"identity_test_armed"})");
        }
        return true;
    }
    if (cmd == "status") {
        nlohmann::json out;
        out["event"]           = "status";
        out["in_world"]        = mWorld.inWorld();
        auto const identity = localPlayerIdentity();
        auto const identityFailures = mPlayerIdentity.failures();
        out["player_identity"] = {{"valid", identity.valid != 0}, {"epoch", mPoseEpoch.load()},
            {"sample_tick", identity.tick}, {"uuid_a", identity.uuidA}, {"uuid_b", identity.uuidB},
            {"faults", identityFailures.fault}, {"failures", identityFailures.cpp}, {"empty", identityFailures.empty}};
        out["controls"] = {{"injecting_input", mInjectingInput.load()}, {"injecting_look", mInjectingLook.load() != 0},
            {"cleanup_pending", mControlCleanupPending.load()}, {"held_mask", mControlHeldMask.load()}};
        auto const recovery = mTickRecovery.snapshot();
        out["tick_recovery"] = {{"recovery_id", recovery.id}, {"active", recovery.phase != TickRecovery::Phase::Running},
            {"phase", recovery.phase == TickRecovery::Phase::Running ? "running"
                      : recovery.phase == TickRecovery::Phase::Pending ? "pending" : "prepared"}};
        out["world_entry"]     = mWorld.entryStatus();
        out["recording"]       = mLegacyStream.active();
        out["tick"]                  = mTick.load();
        out["macro_playing"]         = mMacroPlayback.active();
        out["macro_index"]           = mMacroPlayback.index();
        out["macro_recording"]       = mMacroRecording.active();
        out["queue_dropped"]   = mQueueDropped.load();
        out["pipe_write_timeouts"] = mPipeWriteTimeouts.load();
        out["pipe_write_timeout_ms"] = PipeChannel::DefaultTimeoutMs;
        out["spool"]           = mSpoolPath.string();
        out["spool_rows"]      = mSpoolRows.load();
        out["spool_flushes"]   = mSpoolFlushes.load();
        out["allow_write"]     = mAllowMemoryWrite.load();
        out["tick_thread_id"]  = mTickThreadId.load();
        out["server_thread_id"] = gServerThreadId.load();
        out["packet_capture"]   = mPackets.capturing();
        out["packet_records"]   = mPackets.records();
        out["packet_recv_records"] = mPackets.recvRecords();
        out["packet_recv_hooked"]  = mPackets.recvEnabled();
        out["packet_peer_sends"] = mPackets.peerSends();
        out["packet_inject"]     = mAllowPacketInject.load();
        out["chat_sent"]         = mChatSent.load();
        out["command_template_attempts"] = mCommandTemplates.attempts();
        out["command_template_faults"] = mPacketTemplateFaults.load();
        out["tick_probe_addr"] =
            static_cast<std::uint64_t>(reinterpret_cast<std::uintptr_t>(&mTickProbe));
        out["watch_count"]   = watchCount();
        out["watch_hits"]    = watchTotalHits();
        out["watch_dropped"] = watchDroppedHits();
        out["record_active"]   = recordActive();
        out["record"]           = recordStateJson();
        out["record_rows"]     = recordRows();
        out["record_head"]     = recordHead();
        out["record_period"]   = recordPeriod();
        out["record_capacity"] = RecordCapacity;
        out["overhead"]        = overheadJson();
        out["build_id"]        = buildId();
        out["version_ok"]      = versionOk();
        out["hooks_allowed"]   = hooksAllowed();
        out["compat"]          = mAllowVersionMismatch.load();
        out["profile"]         = profileJson();
        out["capabilities"]    = capabilitiesJson();
        out["version_failures"] = nlohmann::json::array();
        {
            auto diagnostics = diagnoseJson();
            for (auto const& anchor : diagnostics["anchors"]) {
                if (!anchor.value("match", false)) {
                    out["version_failures"].push_back(anchor.value("name", std::string{}));
                }
            }
        }
        enqueue(out.dump());
        return true;
    }
    return false;
}

} // namespace probe
