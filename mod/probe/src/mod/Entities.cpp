#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

void ProbeMod::applyEntityAction(LocalPlayer& player, EntityAction const& action) {
    auto level = ll::service::bedrock::getLevel();
    if (!level) {
        enqueue(R"({"event":"error","reason":"level_unavailable"})");
        return;
    }

    Actor* target = nullptr;
    for (auto* actor : level->getRuntimeActorList()) {
        if (actor != nullptr && static_cast<std::uint64_t>(actor->getRuntimeID()) == action.runtimeId) {
            target = actor;
            break;
        }
    }
    if (target == nullptr) {
        enqueue(R"({"event":"error","reason":"entity_not_found"})");
        return;
    }

    if (action.kind == EntityAction::Kind::Info) {
        auto const& pos    = target->getPosition();
        auto        vel    = target->getVelocity();
        auto const  rot    = target->getRotation();
        auto const& origin = player.getPosition();
        float       dx     = static_cast<float>(pos.x - origin.x);
        float       dy     = static_cast<float>(pos.y - origin.y);
        float       dz     = static_cast<float>(pos.z - origin.z);

        nlohmann::json out;
        out["event"]      = "entity_info";
        out["runtime_id"] = action.runtimeId;
        out["type"]       = target->getTypeName();
        out["name"]       = target->getNameTag();
        out["is_player"]  = target->isPlayer();
        out["is_alive"]   = target->isAlive();
        out["health"]     = target->getHealth();
        out["x"]          = finiteOrZero(static_cast<float>(pos.x));
        out["y"]          = finiteOrZero(static_cast<float>(pos.y));
        out["z"]          = finiteOrZero(static_cast<float>(pos.z));
        out["vx"]         = finiteOrZero(static_cast<float>(vel.x));
        out["vy"]         = finiteOrZero(static_cast<float>(vel.y));
        out["vz"]         = finiteOrZero(static_cast<float>(vel.z));
        out["yaw"]        = finiteOrZero(rot.y);
        out["pitch"]      = finiteOrZero(rot.x);
        out["distance"]   = finiteOrZero(std::sqrt(dx * dx + dy * dy + dz * dz));
        out["on_ground"]  = target->isOnGround();
        enqueue(out.dump());
        return;
    }

    if (!player.mGameMode) {
        enqueue(R"({"event":"error","reason":"gamemode_unavailable"})");
        return;
    }

    if (action.kind == EntityAction::Kind::Attack) {
        std::string const type         = target->getTypeName();
        float const       healthBefore = target->getHealth();
        bool              ok           = player.mGameMode->attack(*target, target->getHeadPos());
        enqueue(
            nlohmann::json{
                {"event",         "entity_attacked"},
                {"runtime_id",    action.runtimeId },
                {"ok",            ok               },
                {"type",          type             },
                {"health_before", healthBefore     },
        }
                .dump()
        );
        return;
    }

    if (action.kind == EntityAction::Kind::Interact) {
        // Calling Player::interact / GameMode::interact directly crashes this
        // build with a null internal item context. Instead, aim at the entity
        // and press the game's own use button for a few ticks.
        Vec3 const       eye      = player.getHeadPos();
        Vec3 const       aim      = target->getHeadPos();
        double           dx       = aim.x - eye.x;
        double           dy       = aim.y - eye.y;
        double           dz       = aim.z - eye.z;
        double           horiz    = std::sqrt(dx * dx + dz * dz);
        constexpr double RadToDeg = 57.29577951308232;
        float            yaw      = static_cast<float>(std::atan2(-dx, dz) * RadToDeg);
        float            pitch    = static_cast<float>(-std::atan2(dy, horiz) * RadToDeg);

        auto const rot    = player.getRotation();
        float      dyaw   = wrapDegrees(yaw - rot.y);
        float      dpitch = rot.x - pitch;
        if (std::fabs(dyaw) > 1.0e-5f || std::fabs(dpitch) > 1.0e-5f) {
            applyTurnDelta(player, Vec2{dpitch, dyaw});
        }
        {
            std::lock_guard lock(mInputMutex);
            mDesired        = ControlState{};
            mDesired.rmb    = true;
            mDesired.active = true;
            mDesiredTicks   = 3;
        }

        enqueue(
            nlohmann::json{
                {"event",      "entity_interacted"  },
                {"runtime_id", action.runtimeId     },
                {"ok",         true                 },
                {"method",     "input"              },
                {"type",       target->getTypeName()},
        }
                .dump()
        );
        return;
    }

    if (action.kind == EntityAction::Kind::LookAt) {
        Vec3 const       eye      = player.getHeadPos();
        Vec3 const       aim      = target->getHeadPos();
        double           dx       = aim.x - eye.x;
        double           dy       = aim.y - eye.y;
        double           dz       = aim.z - eye.z;
        double           horiz    = std::sqrt(dx * dx + dz * dz);
        constexpr double RadToDeg = 57.29577951308232;
        float            yaw      = static_cast<float>(std::atan2(-dx, dz) * RadToDeg);
        float            pitch    = static_cast<float>(-std::atan2(dy, horiz) * RadToDeg);

        auto const rot    = player.getRotation();
        float      dyaw   = wrapDegrees(yaw - rot.y);
        float      dpitch = rot.x - pitch;
        if (std::fabs(dyaw) > 1.0e-5f || std::fabs(dpitch) > 1.0e-5f) {
            applyTurnDelta(player, Vec2{dpitch, dyaw});
        }

        enqueue(
            nlohmann::json{
                {"event",      "look_at_done"  },
                {"runtime_id", action.runtimeId},
                {"yaw",        yaw             },
                {"pitch",      pitch           },
        }
                .dump()
        );
    }
}


void ProbeMod::captureEntities(LocalPlayer& player) {
    auto level = ll::service::bedrock::getLevel();
    if (!level) {
        enqueue(R"({"event":"error","reason":"level_unavailable"})");
        return;
    }

    auto const origin = player.getPosition();
    float      radius = 16.0f;
    int        limit  = 64;
    {
        std::lock_guard lock(mEntityMutex);
        radius = mEntityRadius;
        limit  = mEntityLimit;
    }

    nlohmann::json out;
    out["event"]    = "entities";
    out["radius"]   = radius;
    out["entities"] = nlohmann::json::array();

    int count = 0;
    for (auto* actor : level->getRuntimeActorList()) {
        if (actor == nullptr || !actor->isAlive()) {
            continue;
        }
        if (actor->isPlayer() && actor->getRuntimeID() == player.getRuntimeID()) {
            continue;
        }
        auto const& pos      = actor->getPosition();
        float       dx       = static_cast<float>(pos.x - origin.x);
        float       dy       = static_cast<float>(pos.y - origin.y);
        float       dz       = static_cast<float>(pos.z - origin.z);
        float       distance = std::sqrt(dx * dx + dy * dy + dz * dz);
        if (distance > radius) {
            continue;
        }
        out["entities"].push_back({
            {"type",       actor->getTypeName()                             },
            {"name",       actor->getNameTag()                              },
            {"is_player",  actor->isPlayer()                                },
            {"runtime_id", static_cast<std::uint64_t>(actor->getRuntimeID())},
            {"x",          finiteOrZero(static_cast<float>(pos.x))          },
            {"y",          finiteOrZero(static_cast<float>(pos.y))          },
            {"z",          finiteOrZero(static_cast<float>(pos.z))          },
            {"distance",   finiteOrZero(distance)                           },
            {"health",     actor->getHealth()                               },
        });
        if (++count >= limit) {
            break;
        }
    }
    out["count"] = count;
    enqueue(out.dump());
}


} // namespace probe
