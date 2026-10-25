#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

void ProbeMod::requestServerTeleport(LocalPlayer& player, TeleportRequest const& request) {
    auto server = ll::service::bedrock::getServerInstance();
    if (!server) {
        enqueue(R"({"event":"error","reason":"teleport_no_server_instance"})");
        return;
    }

    // The integrated server owns actor positions. Moving the server-side player
    // emits a proper teleport packet back to this client, so the position sticks;
    // writing the client player only is corrected on the next server tick.
    std::string name = player.getRealName();
    auto const  uuid = player.getUuid();
    Vec3 const  target{request.x, request.y, request.z};

    queueServerTask([this, name, uuid, request, target] {
        auto   minecraft = ll::service::bedrock::getMinecraft();
        Level* level     = minecraft ? minecraft->getLevel() : nullptr;
        if (level == nullptr) {
            enqueue(R"({"event":"error","reason":"teleport_no_server_level"})");
            return;
        }

        Player* serverPlayer = level->getPlayer(uuid);
        if (serverPlayer == nullptr && !name.empty()) {
            serverPlayer = level->getPlayer(name);
        }
        if (serverPlayer == nullptr) {
            enqueue(R"({"event":"error","reason":"teleport_player_not_found"})");
            return;
        }

        Vec2 const rotation = request.hasRotation ? Vec2{request.pitch, request.yaw} : serverPlayer->getRotation();
        if (request.clearEffects) {
            serverPlayer->removeAllEffects();
        }
        serverPlayer->teleport(target, serverPlayer->getDimensionId(), rotation);

        nlohmann::json result;
        result["event"]        = "teleported";
        result["source"]       = "server";
        result["x"]            = request.x;
        result["y"]            = request.y;
        result["z"]            = request.z;
        result["has_rotation"] = request.hasRotation;
        enqueue(result.dump());
    });
}


void ProbeMod::resetPlayer(
    float x,
    float y,
    float z,
    bool  hasYaw,
    float yaw,
    bool  hasPitch,
    float pitch,
    bool  clearEffects
) {
    cancelTasks("reset");
    beginTask();
    mClickRequest.store(-1);
    mSlotRequest.store(-1);
    if (!mWorld.inWorld()) {
        enqueue(R"({"event":"reset_done","ok":false,"reason":"not_in_world"})");
        return;
    }

    // The pipe thread must not read LocalPlayer (its rotation is written every
    // frame by the tick thread). When no rotation was supplied the server task
    // keeps the current one (requestServerTeleport), so nothing is lost.
    TeleportRequest request;
    request.x            = x;
    request.y            = y;
    request.z            = z;
    request.hasRotation  = hasYaw || hasPitch;
    request.clearEffects = clearEffects;
    request.yaw          = yaw;
    request.pitch        = pitch;
    updateRequests([&request](RequestBlock& block) {
        block.teleport    = request;
        ++block.teleportSeq;
    });

    nlohmann::json out{
        {"event", "reset_done"      },
        {"ok",    true              },
        {"x",     x                 },
        {"y",     y                 },
        {"z",     z                 },
    };
    if (request.hasRotation) {
        out["yaw"]   = request.yaw;
        out["pitch"] = request.pitch;
    } else {
        out["yaw"]   = nullptr;
        out["pitch"] = nullptr;
    }
    enqueue(out.dump());
}


} // namespace probe
