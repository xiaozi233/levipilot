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

void WorldSession::enterOnClient(
    std::string const& name,
    std::string const& id,
    int                intent,
    bool               force,
    bool               automatic,
    std::uint64_t      deadline
) {
    if (mEntryInFlight.load(std::memory_order_acquire)) {
        if (!automatic) {
            mOwner.enqueue(
                R"({"event":"enter_world_result","ok":true,"reason":"already_starting"})"
            );
        }
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
    bool const menuReady = isMenuScreen(screen);
    bool const leaving   = mPhase.load() == Phase::Leaving;
    bool const leaveDone = !leaving
                        || (game.isLeaveGameDone() && client->isLeaveGameDone()
                            && game.canStartLocalServer());
    if (!menuReady || !leaveDone) {
        if (GetTickCount64() >= deadline) {
            if (auto old = mSignInRetry.exchange({})) old->cancel();
            mOwner.bootLog(
                "enter_world: blocked screen=" + screen + " phase=" + std::string{phaseName()}
                + " leave_done=" + (leaveDone ? "1" : "0")
            );
            mOwner.enqueue(R"({"event":"enter_world_result","ok":false,"reason":"world_busy"})");
            return;
        }
        auto pending = after([this, name, id, intent, force, automatic, deadline] {
            enterOnClient(name, id, intent, force, automatic, deadline);
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
        auto pending = after([this, name, id, intent, force, automatic, deadline] {
            enterOnClient(name, id, intent, force, automatic, deadline);
        }, 250);
        if (auto old = mSignInRetry.exchange(std::move(pending))) old->cancel();
        return;
    }
    if (auto old = mSignInRetry.exchange({})) old->cancel();

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
        mOwner.enqueue(result.dump());
        mOwner.bootLog(
            "enter_world: start phase=" + std::string{phaseName()} + " screen=" + client->getScreenName()
            + " leave_done=" + (game.isLeaveGameDone() ? "1" : "0")
            + " client_leave_done=" + (client->isLeaveGameDone() ? "1" : "0")
            + " can_start=" + (game.canStartLocalServer() ? "1" : "0")
        );

        mEntryInFlight.store(true, std::memory_order_release);
        mPhase = Phase::Entering;
        // startLocalServerAsyncFromSummary is not exported by LeviLamina's
        // import library; the pure-virtual overload is reachable through the
        // vtable and takes the same data explicitly.
        game.startLocalServerAsync(
            std::string{copy.mId},
            std::string{copy.mName},
            copy.mPremiumTemplateContentIdentity,
            settings,
            static_cast<StartIntent>(intent)
        );
    } catch (std::exception const& e) {
        finishEntry();
        mPhase = Phase::Menu;
        mOwner.bootLog(std::string{"enter_world: exception "} + e.what());
        mOwner.enqueue(
            nlohmann::json{
                {"event",   "enter_world_result"},
                {"ok",      false               },
                {"reason",  "exception"         },
                {"message", e.what()            },
        }
                .dump()
        );
    }
}

void WorldSession::leaveOnClient() {
    auto client = ll::service::getClientInstance();
    if (!client) {
        mOwner.enqueue(R"({"event":"leave_world_result","ok":false,"reason":"no_client"})");
        return;
    }
    auto const previous = mPhase.exchange(Phase::Leaving);
    try {
        client->getMinecraftGame().requestLeaveGame(true, true);
        mOwner.enqueue(R"({"event":"leave_world_result","ok":true})");
    } catch (std::exception const& e) {
        mPhase = previous;
        mOwner.enqueue(
            nlohmann::json{
                {"event",   "leave_world_result"},
                {"ok",      false               },
                {"reason",  "exception"         },
                {"message", e.what()            },
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
            {"ready", !mInWorld.load() && isMenuScreen(client->getScreenName())
                && client->getMinecraftGame().isLeaveGameDone() && client->isLeaveGameDone()
                && client->getMinecraftGame().canStartLocalServer()},
            {"worlds", nlohmann::json::array()}
        };
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
