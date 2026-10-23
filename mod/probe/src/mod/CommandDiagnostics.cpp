#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

bool ProbeMod::dispatchDiagnosticsCommand(std::string const& cmd, nlohmann::json& j) {
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
        enqueue(out.dump());
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
        if (hooksLevel) {
            packetCaptureStop();
            watchesOk      = watchRemoveAll();
            watchesRemoved = watchesOk ? watchesBefore : 0;
        }
        if (tasksLevel) {
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
                {"ok",               watchesOk          },
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
    if (cmd == "status") {
        nlohmann::json out;
        out["event"]           = "status";
        out["in_world"]        = mWorld.inWorld();
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
        out["server_thread_id"] = gServerThreadId.load();
        out["packet_capture"]   = mPackets.capturing();
        out["packet_records"]   = mPackets.records();
        out["packet_recv_records"] = mPackets.recvRecords();
        out["packet_recv_hooked"]  = mPackets.recvEnabled();
        out["packet_peer_sends"] = mPackets.peerSends();
        out["packet_inject"]     = mAllowPacketInject.load();
        out["chat_sent"]         = mChatSent.load();
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
