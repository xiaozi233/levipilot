#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

namespace {

// The menu screen is the only UI state in which startLocalServerAsync has a
// complete screen stack. isLeaveGameDone()/canStartLocalServer() both report
// true while "world_saving_progress_screen - world_loading_progress_screen"
// is still up; starting there corrupts the transition and crashes later.
// After leaving a world the menu can be either the native start screen or the
// HBUI main menu ("ui://./hbui/index.html"); world progress screens are never
// ready, whatever their URL.
bool isMenuScreen(std::string const& screen) {
    if (screen.empty() || screen.find("progress_screen") != std::string::npos) {
        return false;
    }
    return screen.find("start_screen") != std::string::npos || screen.rfind("ui://", 0) == 0;
}

} // namespace

bool WorldSession::menuReadyOnClient() const {
    auto client = ll::service::getClientInstance();
    if (!client) return false;
    auto& game = client->getMinecraftGame();
    return client->getLocalPlayer() == nullptr && isMenuScreen(client->getScreenName())
        && game.isLeaveGameDone() && client->isLeaveGameDone()
        && game.canStartLocalServer() && game.getServerInstance() == nullptr;
}

nlohmann::json WorldSession::menuDiagnosticsOnClient() const {
    bool present = false, localPlayer = false, leaveDone = false, clientLeaveDone = false;
    bool canStart = false, serverInstance = false;
    std::string screen;
    try {
        bool const readOk = guardedGameRead([&] {
            auto client = ll::service::getClientInstance();
            if (!client) return;
            present = true;
            auto& game = client->getMinecraftGame();
            screen = client->getScreenName();
            localPlayer = client->getLocalPlayer() != nullptr;
            leaveDone = game.isLeaveGameDone();
            clientLeaveDone = client->isLeaveGameDone();
            canStart = game.canStartLocalServer();
            serverInstance = game.getServerInstance() != nullptr;
        });
        if (!readOk || !present) return nullptr;
    } catch (...) {
        return nullptr;
    }
    return {
        {"screen", screen},
        {"local_player", localPlayer},
        {"leave_done", leaveDone},
        {"client_leave_done", clientLeaveDone},
        {"can_start", canStart},
        {"server_instance", serverInstance},
    };
}

void WorldSession::probeEntryOnClient(std::uint64_t id) {
    auto const state = mEntry.snapshot();
    if (state.id != id || state.phase == WorldEntryModel::Phase::Idle) return;
    bool safeMenu = false, playerReady = false;
    std::string screen;
    try {
        bool const readOk = guardedGameRead([&] {
            if (mEntryProbeFault.exchange(false)) RaiseException(EXCEPTION_ACCESS_VIOLATION, 0, 0, nullptr);
            auto client = ll::service::getClientInstance();
            if (!client) return;
            screen = client->getScreenName();
            playerReady = client->getLocalPlayer() != nullptr && screen == "hud_screen";
            auto& game = client->getMinecraftGame();
            safeMenu = client->getLocalPlayer() == nullptr && isMenuScreen(screen)
                && game.isLeaveGameDone() && client->isLeaveGameDone()
                && game.canStartLocalServer() && game.getServerInstance() == nullptr;
        });
        if (!readOk) {
            ++mEntryProbeErrors;
            mEntryProbeError = "probe_seh";
            // Faulted output is never evidence that the old server has stopped.
            safeMenu = playerReady = false;
        }
    } catch (...) {
        ++mEntryProbeErrors;
        mEntryProbeError = "probe_exception";
        safeMenu = playerReady = false;
    }
    auto const now = GetTickCount64();
    if (playerReady) {
        mInWorld = true;
        mPhase = Phase::Playing;
        emitEntryCompletion(mEntry.settle(id, true, "player_observed"));
        mEntryTest = EntryTest::None;
        return;
    }
    if (now < mEntryUnsafeUntil.load()) safeMenu = false;
    if (mEntry.recover(id, now, safeMenu)) {
        mEntryTest = EntryTest::None;
        mInWorld = false;
        mPhase = Phase::Menu;
        mOwner.enqueue(nlohmann::json{{"event", "enter_world_recovered"}, {"entry_id", id}}.dump());
    }
}

void WorldSession::enterOnClient(
    std::string const& name,
    std::string const& id,
    int                intent,
    bool               force,
    bool               automatic,
    std::uint64_t      deadline,
    EntryTest           test
) {
    if (mEntry.snapshot().phase != WorldEntryModel::Phase::Idle) {
        if (!automatic) replyEntryBusy();
        return;
    }
    if (mInWorld.load(std::memory_order_relaxed)) {
        mOwner.enqueue(R"({"event":"error","reason":"already_in_world"})");
        return;
    }

    auto client = ll::service::getClientInstance();
    if (!client) {
        mOwner.enqueue(R"({"event":"enter_world_result","ok":false,"reason":"no_client"})");
        return;
    }

    // ClientExitLevelEvent precedes completion of the old world's teardown.
    // Starting another server in that interval races the old server cleanup.
    // The screen gate is the authoritative one: the leave-done flags turn true
    // while a world progress screen is still stacked, and starting then leaves
    // a null UI model that crashes on the next transition.
    auto& game = client->getMinecraftGame();
    std::string const screen = client->getScreenName();
    bool const leaveDone = menuReadyOnClient();
    if (!leaveDone) {
        if (GetTickCount64() >= deadline) {
            if (auto old = mSignInRetry.exchange({})) old->cancel();
            mOwner.bootLog(
                "enter_world: blocked screen=" + screen + " phase=" + std::string{phaseName()}
                + " leave_done=" + (leaveDone ? "1" : "0")
            );
            mOwner.enqueue(R"({"event":"enter_world_result","ok":false,"reason":"world_busy"})");
            return;
        }
        auto pending = after([this, name, id, intent, force, automatic, deadline, test] {
            enterOnClient(name, id, intent, force, automatic, deadline, test);
        }, 100);
        if (auto old = mSignInRetry.exchange(std::move(pending))) old->cancel();
        return;
    }
    mPhase = Phase::Menu;

    // The level list is read from local storage and can be ready before the
    // asynchronous Xbox Live sign-in. Starting a local server in that window
    // produces the "you must authenticate to Microsoft services" screen, so
    // wait for the main-menu model to report a signed-in state first.
    bool signedIn = false;
    bool signInKnown = detail::queryXblSignedIn(signedIn);
    if (signInKnown && !signedIn && GetTickCount64() < deadline) {
        auto pending = after([this, name, id, intent, force, automatic, deadline, test] {
            enterOnClient(name, id, intent, force, automatic, deadline, test);
        }, 250);
        if (auto old = mSignInRetry.exchange(std::move(pending))) old->cancel();
        return;
    }
    if (auto old = mSignInRetry.exchange({})) old->cancel();

    std::uint64_t entryId = 0;
    try {
        auto& cache = game.getLevelListCache();

        LevelSummary const* summary = nullptr;
        if (!id.empty()) {
            summary = cache.getLevelSummary(id);
        }
        if (summary == nullptr && !name.empty()) {
            summary = cache.getLevelSummaryByName(name);
        }
        if (summary == nullptr) {
            mOwner.enqueue(
                nlohmann::json{
                    {"event",  "enter_world_result"},
                    {"ok",     false               },
                    {"reason", "world_not_found"   },
                    {"name",   name                },
                    {"id",     id                  },
            }
                    .dump()
            );
            return;
        }

        // The cache owns the summary; the async startup may outlive this call,
        // so hand it a copy.
        LevelSummary  copy = *summary;
        LevelSettings settings{copy};
        nlohmann::json result{
            {"event",  "enter_world_result"    },
            {"ok",     true                    },
            {"name",   std::string{copy.mName} },
            {"id",     std::string{copy.mId}   },
            {"intent", intent                  },
        };
        result["signed_in"] = signInKnown ? nlohmann::json(signedIn) : nlohmann::json(nullptr);

        if (!force && signInKnown && !signedIn) {
            result["ok"]     = false;
            result["reason"] = "signin_required";
            mOwner.enqueue(result.dump());
            return;
        }
        mOwner.bootLog(
            "enter_world: start phase=" + std::string{phaseName()} + " screen=" + client->getScreenName()
            + " leave_done=" + (game.isLeaveGameDone() ? "1" : "0")
            + " client_leave_done=" + (client->isLeaveGameDone() ? "1" : "0")
            + " can_start=" + (game.canStartLocalServer() ? "1" : "0")
        );

        mEntryTest = test;
        mEntryProbeErrors = 0;
        mEntryProbeError = nullptr;
        mEntryProbeFault = test == EntryTest::ProbeFault;
        mEntryUnsafeUntil = test == EntryTest::Timeout || test == EntryTest::ProbeFault
                         ? GetTickCount64() + 2000 : 0;
        mNextEntryProbeMs = GetTickCount64() + (test == EntryTest::MissingJoin ? 1000 : 100);
        entryId = mEntry.begin(GetTickCount64(),
            test == EntryTest::Timeout || test == EntryTest::ProbeFault ? 700 : WorldEntryModel::TimeoutMs);
        if (!entryId) {
            replyEntryBusy();
            return;
        }
        mPhase = Phase::Entering;
        result["entry_id"] = entryId;
        mOwner.enqueue(result.dump());
        if (test == EntryTest::Timeout || test == EntryTest::ProbeFault) return;
        // startLocalServerAsyncFromSummary is not exported by LeviLamina's
        // import library; the pure-virtual overload is reachable through the
        // vtable and takes the same data explicitly.
        std::string const worldId{copy.mId}, worldName{copy.mName};
        // Keep argument owners outside the raw SEH frame. An uncertain start
        // is quarantined; catching an AV must never admit a second server.
        bool const started = guardedGameRead([&] {
            if (test == EntryTest::StartFault) RaiseException(EXCEPTION_ACCESS_VIOLATION, 0, 0, nullptr);
            game.startLocalServerAsync(worldId, worldName,
                copy.mPremiumTemplateContentIdentity, settings, static_cast<StartIntent>(intent));
        });
        if (!started) emitEntryCompletion(mEntry.settle(entryId, false, "start_fault"));
    } catch (std::exception const& e) {
        mOwner.bootLog(std::string{"enter_world: exception "} + e.what());
        if (entryId) {
            emitEntryCompletion(mEntry.settle(entryId, false, "start_exception"));
            return;
        }
        mOwner.enqueue(
            nlohmann::json{
                {"event",   "enter_world_result"},
                {"ok",      false               },
                {"reason",  "exception"         },
                {"message", e.what()            },
        }
                .dump()
        );
    } catch (...) {
        if (entryId) emitEntryCompletion(mEntry.settle(entryId, false, "start_exception"));
        else mOwner.enqueue(R"({"event":"enter_world_result","ok":false,"reason":"exception"})");
    }
}

void WorldSession::leaveOnClient() {
    if (auto old = mSignInRetry.exchange({})) old->cancel();
    if (auto old = mAutoRetry.exchange({})) old->cancel();
    auto client = ll::service::getClientInstance();
    if (!client) {
        mOwner.enqueue(R"({"event":"leave_world_result","ok":false,"reason":"no_client"})");
        return;
    }
    auto const previous = mPhase.exchange(Phase::Leaving);
    auto const leaveId = mLeaveSerial.fetch_add(1) + 1;
    mLeaveCompleted.store(0);
    mLeaveRequest.store(leaveId);
    try {
        bool const requested = guardedGameRead([&] {
            client->getMinecraftGame().requestLeaveGame(true, true);
        });
        if (!requested) {
            mPhase = previous;
            mOwner.enqueue(nlohmann::json{{"event", "leave_world_result"}, {"ok", false},
                                          {"reason", "fault"}, {"leave_id", leaveId}}.dump());
            return;
        }
        mOwner.enqueue(nlohmann::json{{"event", "leave_world_result"}, {"ok", true}, {"leave_id", leaveId}}.dump());
    } catch (std::exception const& e) {
        mPhase = previous;
        mOwner.enqueue(
            nlohmann::json{
                {"event",   "leave_world_result"},
                {"ok",      false               },
                {"reason",  "exception"         },
                {"message", e.what()            },
                {"leave_id", leaveId             },
        }
                .dump()
        );
    }
}

void WorldSession::listOnClient() {
    mOwner.bootLog("worlds: begin");
    auto client = ll::service::getClientInstance();
    if (!client) {
        mOwner.enqueue(R"({"event":"worlds","worlds":[],"reason":"no_client"})");
        return;
    }
    mOwner.bootLog("worlds: client");
    try {
        std::vector<LevelSummary> list;
        client->getMinecraftGame().getLevelListCache().getLevelList(list, true, false, false, false);
        mOwner.bootLog("worlds: list=" + std::to_string(list.size()));

        nlohmann::json out{
            {"event",  "worlds"               },
            {"ready", mEntry.snapshot().phase == WorldEntryModel::Phase::Idle && menuReadyOnClient()},
            {"entry", entryStatus()},
            {"worlds", nlohmann::json::array()}
        };
        out["leave_request"] = mLeaveRequest.load();
        out["leave_completed"] = mLeaveCompleted.load();
        out["menu"] = menuDiagnosticsOnClient();
        if (out["menu"].is_object()) out["menu"]["world_list_available"] = true;
        for (auto const& summary : list) {
            out["worlds"].push_back({
                {"id",         std::string{summary.mId}                  },
                {"name",       std::string{summary.mName}                },
                {"last_saved", static_cast<long long>(summary.mLastSaved)},
                {"game_type",  static_cast<int>(summary.mGameType)       },
            });
        }
        mOwner.enqueue(out.dump());
    } catch (std::exception const& e) {
        mOwner.bootLog(std::string{"worlds: exception "} + e.what());
        mOwner.enqueue(
            nlohmann::json{
                {"event",   "worlds"               },
                {"worlds",  nlohmann::json::array()},
                {"reason",  "exception"            },
                {"message", e.what()               },
        }
                .dump()
        );
    }
}

} // namespace probe
