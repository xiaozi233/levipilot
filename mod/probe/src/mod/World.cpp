#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

void ProbeMod::enterWorld(std::string const& name, std::string const& id, int intent) {
    if (mInWorld.load(std::memory_order_relaxed)) {
        enqueue(R"({"event":"error","reason":"already_in_world"})");
        return;
    }

    auto client = ll::service::getClientInstance();
    if (!client) {
        enqueue(R"({"event":"enter_world_result","ok":false,"reason":"no_client"})");
        return;
    }

    try {
        auto& game  = client->getMinecraftGame();
        auto& cache = game.getLevelListCache();

        LevelSummary const* summary = nullptr;
        if (!id.empty()) {
            summary = cache.getLevelSummary(id);
        }
        if (summary == nullptr && !name.empty()) {
            summary = cache.getLevelSummaryByName(name);
        }
        if (summary == nullptr) {
            enqueue(
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
        enqueue(
            nlohmann::json{
                {"event",  "enter_world_result"   },
                {"ok",     true                   },
                {"name",   std::string{copy.mName}},
                {"id",     std::string{copy.mId}  },
                {"intent", intent                 },
        }
                .dump()
        );

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
        bootLog(std::string{"enter_world: exception "} + e.what());
        enqueue(
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

void ProbeMod::leaveWorld() {
    auto client = ll::service::getClientInstance();
    if (!client) {
        enqueue(R"({"event":"leave_world_result","ok":false,"reason":"no_client"})");
        return;
    }
    try {
        client->getMinecraftGame().requestLeaveGame(true, true);
        enqueue(R"({"event":"leave_world_result","ok":true})");
    } catch (std::exception const& e) {
        enqueue(
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

void ProbeMod::listWorlds() {
    bootLog("worlds: begin");
    auto client = ll::service::getClientInstance();
    if (!client) {
        enqueue(R"({"event":"worlds","worlds":[],"reason":"no_client"})");
        return;
    }
    bootLog("worlds: client");
    try {
        std::vector<LevelSummary> list;
        client->getMinecraftGame().getLevelListCache().getLevelList(list, true, false, false, false);
        bootLog("worlds: list=" + std::to_string(list.size()));

        nlohmann::json out{
            {"event",  "worlds"               },
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
        enqueue(out.dump());
    } catch (std::exception const& e) {
        bootLog(std::string{"worlds: exception "} + e.what());
        enqueue(
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