#pragma once

#include "mod/PlayerIdentityModel.h"
#include "mod/QueryModel.h"
#include "mod/StagedCapture.h"

namespace probe::detail {

enum class EntityOperation { List, Info, Attack, Interact, LookAt };
enum class EntityStage { Client, Server, Aim, Complete };

struct EntityQuery {
    EntityOperation operation = EntityOperation::List;
    std::uint64_t runtimeId = 0;
    float radius = 16;
    int limit = 64;
    CaptureTest test = CaptureTest::None;
    EntityStage testStage = EntityStage::Client;
};

struct EntitySnapshot {
    struct Vec { float x = 0, y = 0, z = 0; } origin, eye, aim;
    struct Row {
        FixedText<256> type;
        FixedText<4096> name;
        std::uint64_t runtimeId = 0;
        Vec pos, velocity;
        float health = 0, distance = 0, yaw = 0, pitch = 0;
        bool isPlayer = false, alive = false, onGround = false;
    };
    std::array<Row, 512> rows{};
    std::size_t count = 0;
    std::uint64_t selfId = 0;
    PlayerIdentity identity;
    float yaw = 0, pitch = 0;
    bool ok = false, truncated = false;
    char const* error = nullptr;
};

class EntityCapture : public StagedCapture<EntityQuery, EntitySnapshot, EntityStage> {
public:
    EntityCapture() : StagedCapture("entity_stage_invalid") {}
};

} // namespace probe::detail
