#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

std::filesystem::path ProbeMod::checkpointsPath() const {
    return getSelf().getModDir() / "checkpoints.json";
}

void ProbeMod::loadCheckpoints() {
    try {
        std::ifstream in(checkpointsPath(), std::ios::binary);
        if (!in) {
            return;
        }
        auto parsed = nlohmann::json::parse(in, nullptr, false);
        if (parsed.is_discarded() || !parsed.is_object()) {
            bootLog("checkpoints: invalid json");
            return;
        }
        std::lock_guard lock(mCheckpointMutex);
        for (auto it = parsed.begin(); it != parsed.end(); ++it) {
            if (!it.value().is_object()) {
                bootLog("checkpoints: skipping invalid entry " + it.key());
                continue;
            }
            Checkpoint point;
            if (!safeJsonFloat(it.value(), "x", 0.0f, point.x)
                || !safeJsonFloat(it.value(), "y", 0.0f, point.y)
                || !safeJsonFloat(it.value(), "z", 0.0f, point.z)
                || !safeJsonFloat(it.value(), "yaw", 0.0f, point.yaw)
                || !safeJsonFloat(it.value(), "pitch", 0.0f, point.pitch)) {
                bootLog("checkpoints: skipping invalid entry " + it.key());
                continue;
            }
            mCheckpoints[it.key()] = point;
        }
        bootLog("checkpoints: loaded " + std::to_string(mCheckpoints.size()));
    } catch (std::exception const& e) {
        bootLog(std::string("checkpoints: exception: ") + e.what());
    } catch (...) {
        bootLog("checkpoints: unknown exception");
    }
}

bool ProbeMod::saveCheckpoints(std::string& error) {
    nlohmann::json out = nlohmann::json::object();
    {
        std::lock_guard lock(mCheckpointMutex);
        for (auto const& [name, point] : mCheckpoints) {
            out[name] = {
                {"x",     point.x    },
                {"y",     point.y    },
                {"z",     point.z    },
                {"yaw",   point.yaw  },
                {"pitch", point.pitch},
            };
        }
    }

    auto                path = checkpointsPath();
    auto                temp = path;
    temp += ".tmp";
    std::ofstream file(temp, std::ios::binary | std::ios::trunc);
    if (!file) {
        error = "open_failed";
        return false;
    }
    file << out.dump(2) << '\n';
    file.close();
    if (!file) {
        error = "write_failed";
        return false;
    }
    if (!MoveFileExW(
            temp.c_str(),
            path.c_str(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH
        )) {
        error = "replace_failed";
        return false;
    }
    return true;
}

void ProbeMod::checkpointSave(std::string const& name) {
    if (name.empty() || name.size() > 64) {
        enqueue(R"({"event":"error","reason":"bad_checkpoint_name"})");
        return;
    }
    PoseSnapshot pose;
    auto const   epoch = mPoseEpoch.load();
    if (!mWorld.inWorld() || !mPose.read(pose, epoch) || epoch != mPoseEpoch.load()) {
        enqueue(R"({"event":"error","reason":"no_pose"})");
        return;
    }
    Checkpoint point;
    point.x     = pose.feetX;
    point.y     = pose.feetY;
    point.z     = pose.feetZ;
    point.yaw   = pose.yaw;
    point.pitch = pose.pitch;
    {
        std::lock_guard lock(mCheckpointMutex);
        mCheckpoints[name] = point;
    }
    std::string error;
    if (!saveCheckpoints(error)) {
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
            {"event",      "checkpoint_saved"},
            {"name",       name              },
            {"x",          point.x           },
            {"y",          point.y           },
            {"z",          point.z           },
            {"yaw",        point.yaw         },
            {"pitch",      point.pitch       },
            {"tick",       pose.tick         },
            {"pose_epoch", pose.epoch        },
    }
            .dump()
    );
}

void ProbeMod::checkpointLoad(std::string const& name) {
    Checkpoint point;
    bool       found = false;
    {
        std::lock_guard lock(mCheckpointMutex);
        auto            it = mCheckpoints.find(name);
        if (it != mCheckpoints.end()) {
            point = it->second;
            found = true;
        }
    }
    if (!found) {
        enqueue(
            nlohmann::json{
                {"event",  "error"     },
                {"reason", "not_found" },
                {"name",   name        },
        }
                .dump()
        );
        return;
    }
    // Restoring a position must not strip potion effects (protection,
    // parkour potions) -- only the explicit reset command does that.
    auto const teleportId = resetPlayer(point.x, point.y, point.z, true, point.yaw, true, point.pitch, false);
    if (!teleportId) return;
    enqueue(
        nlohmann::json{
            {"event", "checkpoint_loaded"},
            {"teleport_id", teleportId},
            {"name",  name               },
            {"x",     point.x            },
            {"y",     point.y            },
            {"z",     point.z            },
            {"yaw",   point.yaw          },
            {"pitch", point.pitch        },
    }
            .dump()
    );
}

void ProbeMod::checkpointList() {
    nlohmann::json out;
    out["event"]       = "checkpoint_list";
    out["checkpoints"] = nlohmann::json::array();
    std::lock_guard lock(mCheckpointMutex);
    for (auto const& [name, point] : mCheckpoints) {
        out["checkpoints"].push_back({
            {"name",  name        },
            {"x",     point.x     },
            {"y",     point.y     },
            {"z",     point.z     },
            {"yaw",   point.yaw   },
            {"pitch", point.pitch },
        });
    }
    enqueue(out.dump());
}

void ProbeMod::checkpointDelete(std::string const& name) {
    bool removed = false;
    {
        std::lock_guard lock(mCheckpointMutex);
        removed = mCheckpoints.erase(name) > 0;
    }
    if (!removed) {
        enqueue(
            nlohmann::json{
                {"event",  "error"     },
                {"reason", "not_found" },
                {"name",   name        },
        }
                .dump()
        );
        return;
    }
    std::string error;
    if (!saveCheckpoints(error)) {
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
            {"event", "checkpoint_deleted"},
            {"name",  name                },
    }
            .dump()
    );
}

} // namespace probe
