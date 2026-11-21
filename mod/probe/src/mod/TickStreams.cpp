#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {
namespace {

void injectStreamFault(StreamTest test) {
    if (test == StreamTest::Fault) RaiseException(EXCEPTION_ACCESS_VIOLATION, 0, 0, nullptr);
    if (test == StreamTest::Cpp) throw std::runtime_error("selftest tick stream");
}

nlohmann::json legacyJson(LegacyTick const& row) {
    return {
        {"event",             "tick"               },
        {"tick",              row.tick             },
        {"record_id",         row.recordId         },
        {"x",                 row.x                },
        {"y",                 row.y                },
        {"z",                 row.z                },
        {"vx",                row.vx               },
        {"vy",                row.vy               },
        {"vz",                row.vz               },
        {"yaw",               row.yaw              },
        {"pitch",             row.pitch            },
        {"on_ground",         row.onGround         },
        {"in_air",            !row.onGround        },
        {"jump_edge",         row.jumpEdge         },
        {"block_below",       row.blocks[0].c_str()},
        {"block_feet",        row.blocks[1].c_str()},
        {"block_head",        row.blocks[2].c_str()},
        {"sprint",            row.sprint           },
        {"sneak",             row.sneak            },
        {"jump",              row.jump             },
        {"w",                 row.w                },
        {"a",                 row.a                },
        {"s",                 row.s                },
        {"d",                 row.d                },
        {"action_sprint",     row.actionSprint     },
        {"input_sprint",      row.inputSprint      },
        {"input_sprint_down", row.inputSprint      },
        {"health",            row.health           },
        {"truncated",         row.truncated        }
    };
}

nlohmann::json stoppedJson(LegacyStream<>::Completion const& done) {
    return {
        {"event",     "stopped"                 },
        {"reason",    streamEndName(done.reason)},
        {"ok",        done.ok()                 },
        {"record_id", done.recordId             },
        {"rows",      done.rows                 }
    };
}

} // namespace

bool ProbeMod::startLegacy(std::uint64_t maxTicks, std::size_t capacity, StreamTest test, std::uint64_t drainDelayMs) {
    std::lock_guard lock(mLegacy.mutex);
    if (mLegacy.worldLeft.exchange(false)) mLegacy.stream.stop(StreamEnd::WorldLeft);
    drainLegacyLocked();
    if (!mLegacy.stream.ready()) return false;
    mLegacy.lastCompletion = {};
    mLegacy.test.store(test, std::memory_order_release);
    mLegacy.drainAfterMs = GetTickCount64() + drainDelayMs;
    // Preserve the old global reset, but number JSON samples within their own
    // session so a tick already in progress cannot create gaps or end early.
    mTick = 0;
    mLegacy.stream.begin(maxTicks, capacity);
    enqueue(
        nlohmann::json{
            {"event",     "started"                },
            {"record_id", mLegacy.stream.recordId()},
            {"max_ticks", maxTicks                 }
    }.dump()
    );
    return true;
}

bool ProbeMod::drainLegacyLocked() {
    LegacyTick row;
    while (mLegacy.stream.pop(row)) enqueue(legacyJson(row).dump());
    if (auto done = mLegacy.stream.takeCompletion()) {
        mLegacy.lastCompletion = *done;
        enqueue(stoppedJson(*done).dump());
        return true;
    }
    return false;
}

void ProbeMod::stopLegacy(StreamEnd reason, bool reply) {
    std::lock_guard lock(mLegacy.mutex);
    if (reason == StreamEnd::Client && (mLegacy.worldLeft.exchange(false) || !mWorld.inWorld())) {
        reason = StreamEnd::WorldLeft;
    }
    mLegacy.stream.stop(reason);
    if (!drainLegacyLocked() && reply) {
        auto done = mLegacy.lastCompletion;
        if (!done.recordId) done.reason = reason;
        // An explicit idempotent stop acknowledges the request using the last
        // outcome. It cannot turn an auto-stop failure into success.
        enqueue(stoppedJson(done).dump());
    }
    mSpoolForce = true;
    wakeSpool();
}

void ProbeMod::captureLegacyTick(ClientInstance& client, LocalPlayer& player) {
    mLegacy.stream.tick([&](LegacyTick& out) {
        return guardedGameRead([&] {
            injectStreamFault(mLegacy.test.exchange(StreamTest::None));
            auto const& pos = player.getPosition();
            auto const  vel = player.getVelocity();
            auto const  rot = player.getRotation();
            auto const  in  = readInput(client);
            out.x           = finiteOrZero(pos.x);
            out.y           = finiteOrZero(pos.y);
            out.z           = finiteOrZero(pos.z);
            out.vx          = finiteOrZero(vel.x);
            out.vy          = finiteOrZero(vel.y);
            out.vz          = finiteOrZero(vel.z);
            out.yaw         = finiteOrZero(rot.y);
            out.pitch       = finiteOrZero(rot.x);
            out.onGround    = player.isOnGround();
            out.jumpEdge    = mInputSession.jumpEdgeThisTick;
            out.sprint      = player.isSprinting();
            out.sneak       = player.isSneaking();
            out.jump        = in.jump;
            out.w           = in.w;
            out.a           = in.a;
            out.s           = in.s;
            out.d           = in.d;
            out.inputSprint = in.sprint;
            out.health      = player.getHealth();
            if (auto action = player.getEntityContext().tryGetComponent<PlayerActionComponent>()) {
                out.actionSprint = action->mPlayerActions->test(PlayerActionStartSprinting);
            }
            auto const                    feet   = player.getFeetPos();
            auto const                    x      = static_cast<int>(std::floor(feet.x));
            auto const                    y      = static_cast<int>(std::floor(feet.y));
            auto const                    z      = static_cast<int>(std::floor(feet.z));
            auto const                    headY  = static_cast<int>(std::floor(feet.y + 1.8f));
            auto const&                   source = player.getDimensionBlockSourceConst();
            std::array<BlockPos, 3> const positions{
                BlockPos{x, y - 1, z},
                BlockPos{x, y,     z},
                BlockPos{x, headY, z}
            };
            for (std::size_t i = 0; i < positions.size(); ++i) {
                auto const& name = source.getBlock(positions[i]).getTypeName();
                out.blocks[i].assign(name);
                out.truncated = out.truncated || out.blocks[i].view().size() != name.size();
            }
        });
    });
}

void ProbeMod::tickParkourEvents(LocalPlayer& player, std::uint64_t tick) {
    auto const generation = mParkourGeneration.load(std::memory_order_acquire);
    if (generation != mParkourSeenGeneration) {
        mParkourSeenGeneration = generation;
        mParkourTracker.reset();
    }
    ParkourPose pose;
    try {
        if (!guardedGameRead([&] {
                injectStreamFault(mParkourTest.exchange(StreamTest::None));
                auto const feet = player.getFeetPos();
                pose            = {
                    finiteOrZero(feet.x),
                    finiteOrZero(feet.y),
                    finiteOrZero(feet.z),
                    static_cast<float>(player.getHealth()),
                    player.isOnGround()
                };
            })) {
            mParkourFaults.fetch_add(1, std::memory_order_relaxed);
            mParkourTracker.reset();
            return;
        }
    } catch (...) {
        mParkourFailures.fetch_add(1, std::memory_order_relaxed);
        mParkourTracker.reset();
        return;
    }
    auto event       = mParkourTracker.observe(pose, tick);
    event.generation = generation;
    if (event.flags && !mParkourEvents.push(event)) mParkourLost.fetch_add(1, std::memory_order_relaxed);
}

void ProbeMod::drainTickStreams() {
    {
        std::lock_guard lock(mLegacy.mutex);
        if (mLegacy.worldLeft.exchange(false) || !mWorld.inWorld()) mLegacy.stream.stop(StreamEnd::WorldLeft);
        if (!mRunning.load()) mLegacy.stream.stop(StreamEnd::Disabled);
        if (!mLegacy.stream.active() || GetTickCount64() >= mLegacy.drainAfterMs) drainLegacyLocked();
    }
    ParkourEvent event;
    while (mParkourEvents.pop(event)) {
        if (event.generation != mParkourGeneration.load(std::memory_order_acquire)) continue;
        auto const& p = event.pose;
        if (event.flags & ParkourEvent::Landed) {
            enqueue(
                nlohmann::json{
                    {"event",         "player_landed"         },
                    {"tick",          event.tick              },
                    {"fall_distance", finiteOrZero(event.fall)},
                    {"x",             p.x                     },
                    {"y",             p.y                     },
                    {"z",             p.z                     },
                    {"health",        p.health                }
            }.dump()
            );
        }
        if (event.flags & ParkourEvent::Hurt) {
            enqueue(
                nlohmann::json{
                    {"event",  "player_hurt"             },
                    {"tick",   event.tick                },
                    {"health", p.health                  },
                    {"damage", finiteOrZero(event.damage)}
            }.dump()
            );
        }
        if (event.flags & ParkourEvent::Died) {
            enqueue(
                nlohmann::json{
                    {"event", "player_died"},
                    {"tick",  event.tick   },
                    {"x",     p.x          },
                    {"y",     p.y          },
                    {"z",     p.z          }
            }.dump()
            );
        }
    }
    for (auto [counter, reason] : {
             std::pair{&mParkourLost,     "buffer_full"   },
             std::pair{&mParkourFaults,   "capture_fault" },
             std::pair{&mParkourFailures, "capture_failed"}
    }) {
        if (auto count = counter->exchange(0)) {
            enqueue(
                nlohmann::json{
                    {"event",  "parkour_diagnostic"},
                    {"reason", reason              },
                    {"count",  count               }
            }.dump()
            );
        }
    }
}

} // namespace probe
