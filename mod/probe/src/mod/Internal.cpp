#include "mod/Internal.h"

#include "mc/client/game/ClientInstance.h"
#include "mc/client/input/ClientMoveInputHandler.h"
#include "mc/client/input/MinecraftInputHandler.h"
#include "mc/deps/core/math/Vec3.h"
#include "mc/deps/input/InputEventQueue.h"
#include "mc/deps/input/InputHandler.h"
#include "mc/deps/input/enums/FocusImpact.h"
#include "mc/world/item/ItemStack.h"
#include "mc/world/level/BlockPos.h"
#include "mc/world/level/BlockSource.h"
#include "mc/world/level/Level.h"

#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>

namespace probe::detail {

namespace {

int itemMaxDamage(ItemStack const& item) {
    auto const* def = item.mItem.get();
    return def ? static_cast<int>(def->getMaxDamage()) : 0;
}

std::unique_ptr<CompoundTag> newCompoundTag() {
    auto res = Tag::newTag(Tag::Compound);
    if (!res || !res.value()) {
        return {};
    }
    return std::unique_ptr<CompoundTag>(static_cast<CompoundTag*>(res.value().release()));
}

bool applyFireworkFlight(ItemStack& stack, std::int8_t flight, std::string& error) {
    std::unique_ptr<CompoundTag> root;
    std::unique_ptr<CompoundTag> fireworks;
    std::string                  sdkError;
    bool                         missingTag = false;
    bool const                   ok         = guardedSdkCall(
        [&] {
            root = stack.mUserData ? stack.mUserData->clone() : newCompoundTag();
            if (!root) {
                missingTag = true;
                return;
            }
            if (auto* existing = root->get("Fireworks"); existing && existing->getId() == Tag::Compound) {
                existing->as<CompoundTag>().putByte("Flight", static_cast<uchar>(flight));
            } else {
                fireworks = newCompoundTag();
                if (!fireworks) {
                    missingTag = true;
                    return;
                }
                fireworks->putByte("Flight", static_cast<uchar>(flight));
                root->putCompound("Fireworks", std::move(fireworks));
            }
            stack.setUserData(std::move(root));
        },
        CaptureTest::None,
        &sdkError
    );
    if (!ok || missingTag) {
        error = sdkError.empty() ? "item_flight_failed" : ("item_flight_failed: " + sdkError);
        return false;
    }
    return true;
}

} // namespace

float finiteOrZero(float value) { return std::isfinite(value) ? value : 0.0f; }

float wrapDegrees(float value) {
    // Non-finite input (e.g. 1e400 parsed by nlohmann) would never leave the
    // loops below; callers reject it, this is the last-resort guard.
    if (!std::isfinite(value)) {
        return 0.0f;
    }
    while (value > 180.0f) {
        value -= 360.0f;
    }
    while (value < -180.0f) {
        value += 360.0f;
    }
    return value;
}

bool numberValue(nlohmann::json const& value, double low, double high, double& out) {
    if (!value.is_number()) {
        return false;
    }
    double const parsed = value.get<double>();
    if (!std::isfinite(parsed) || parsed < low || parsed > high) {
        return false;
    }
    out = parsed;
    return true;
}

float yawTo(double dx, double dz) {
    return static_cast<float>(std::atan2(-dx, dz) * 57.29577951308232);
}

float pitchTo(double dy, double horiz) {
    return static_cast<float>(-std::atan2(dy, horiz) * 57.29577951308232);
}

bool numberJson(nlohmann::json const& j, char const* key, double low, double high, double fallback, double& out) {
    auto const it = j.find(key);
    if (it == j.end()) {
        out = fallback;
        return true;
    }
    return numberValue(*it, low, high, out);
}

bool parseBool(nlohmann::json const& value, bool& out) {
    if (value.is_boolean()) {
        out = value.get<bool>();
        return true;
    }
    if (value.is_number_integer()) {
        out = value.get<long long>() != 0;
        return true;
    }
    if (value.is_string()) {
        auto const& text = value.get_ref<std::string const&>();
        if (text == "true" || text == "1") {
            out = true;
            return true;
        }
        if (text == "false" || text == "0") {
            out = false;
            return true;
        }
    }
    return false;
}

bool parseFloat(std::string const& text, float& out) {
    if (text.empty()) {
        return false;
    }
    char* end   = nullptr;
    errno       = 0;
    float value = std::strtof(text.c_str(), &end);
    if (end == text.c_str() || end == nullptr || *end != '\0' || errno == ERANGE || !std::isfinite(value)) {
        return false;
    }
    out = value;
    return true;
}

std::string formatFloat(float value) {
    char buffer[32]{};
    std::snprintf(buffer, sizeof(buffer), "%.9g", static_cast<double>(value));
    return std::string{buffer};
}

nlohmann::json itemJson(ItemStack const& item) {
    if (item.isNull()) {
        return nullptr;
    }
    return {
        {"type",       item.getTypeName()                    },
        {"name",       item.getName()                        },
        {"count",      static_cast<int>(item.mCount)         },
        {"aux",        static_cast<int>(item.mAuxValue)      },
        {"damage",     static_cast<int>(item.getDamageValue())},
        {"max_damage", itemMaxDamage(item)                   },
    };
}

void captureItem(ItemStack const& item, ItemSnapshot& out, bool& truncated) {
    if (item.isNull()) return;
    out.present      = true;
    auto const& type = item.getTypeName();
    auto const& name = item.getName();
    out.type.assign(type);
    out.name.assign(name);
    out.count     = static_cast<int>(item.mCount);
    out.aux       = static_cast<int>(item.mAuxValue);
    out.damage    = static_cast<int>(item.getDamageValue());
    out.maxDamage = itemMaxDamage(item);
    truncated     = truncated || out.type.view().size() != type.size() || out.name.view().size() != name.size();
}

nlohmann::json itemJson(ItemSnapshot const& item) {
    if (!item.present) return nullptr;
    return {
        {"type",       item.type.c_str()},
        {"name",       item.name.c_str()},
        {"count",      item.count       },
        {"aux",        item.aux         },
        {"damage",     item.damage      },
        {"max_damage", item.maxDamage   },
    };
}

bool makeItemStack(
    Level&             level,
    std::string const& name,
    int                count,
    int                aux,
    ItemStack&         out,
    std::string&       error,
    int                damage,
    int                flight
) {
    if (count <= 0) {
        out = ItemStack{};
        return true;
    }
    int  itemAux = aux;
    auto item    = level.getItemRegistry().lookupByName(itemAux, std::string_view{name});
    if (!item) {
        error = "unknown_item: " + name;
        return false;
    }
    out.reinit(*item, count, itemAux);
    if (damage >= 0) {
        auto const clampedDamage = static_cast<short>(
            std::clamp(damage, 0, static_cast<int>(std::numeric_limits<short>::max()))
        );
        std::string sdkError;
        if (!guardedSdkCall([&] { out.setDamageValue(clampedDamage); }, CaptureTest::None, &sdkError)) {
            error = sdkError.empty() ? "item_damage_failed" : ("item_damage_failed: " + sdkError);
            return false;
        }
    }
    if (flight >= 0) {
        auto const clampedFlight = static_cast<std::int8_t>(std::clamp(flight, 0, 127));
        if (!applyFireworkFlight(out, clampedFlight, error)) {
            return false;
        }
    }
    return true;
}

std::string blockName(BlockSource const& source, int x, int y, int z) {
    BlockPos const pos{x, y, z};
    return source.getBlock(pos).getTypeName();
}

Vec3 blockCenter(BlockPos const& pos) {
    return Vec3{
        static_cast<float>(pos.x) + 0.5f,
        static_cast<float>(pos.y) + 0.5f,
        static_cast<float>(pos.z) + 0.5f,
    };
}

FaceOffset faceOffset(int face) {
    switch (face) {
    case 0:
        return {0, -1, 0};
    case 1:
        return {0, 1, 0};
    case 2:
        return {0, 0, -1};
    case 3:
        return {0, 0, 1};
    case 4:
        return {-1, 0, 0};
    case 5:
        return {1, 0, 0};
    default:
        return {0, 1, 0};
    }
}

int faceBetween(BlockPos const& from, BlockPos const& to) {
    int const dx = to.x - from.x;
    int const dy = to.y - from.y;
    int const dz = to.z - from.z;
    if (dy != 0) {
        return dy > 0 ? 1 : 0;
    }
    if (dz != 0) {
        return dz > 0 ? 3 : 2;
    }
    if (dx != 0) {
        return dx > 0 ? 5 : 4;
    }
    return -1;
}

InputSnapshot readInput(ClientInstance& client) {
    InputSnapshot out;
    auto*         input = ClientMoveInputHandler::getMoveInput(client);
    // mInputState/mFlagValues/mMove are inline TypedStorage in the handler
    // (never null); the only real pointer is the handler itself.
    if (input == nullptr) {
        return out;
    }
    auto const& state = *input->mInputState->mFlagValues;
    auto const& flags = *input->mFlagValues;

    out.moveX = input->mMove->x;
    out.moveY = input->mMove->y;

    out.w      = state.test(MoveInputStateUp) || out.moveY > 0.01f;
    out.s      = state.test(MoveInputStateDown) || out.moveY < -0.01f;
    out.a      = state.test(MoveInputStateLeft) || out.moveX < -0.01f;
    out.d      = state.test(MoveInputStateRight) || out.moveX > 0.01f;
    out.sprint = flags.test(MoveInputComponentSprinting) || state.test(MoveInputStateSprintDown);
    out.sneak  = flags.test(MoveInputComponentSneaking) || state.test(MoveInputStateSneakDown);
    out.jump   = flags.test(MoveInputComponentJumping) || state.test(MoveInputStateJumpDown);
    return out;
}

InputHandler* inputHandler(ClientInstance& client) {
    auto minecraftInput = client.getMinecraftInput();
    if (!minecraftInput) {
        return nullptr;
    }
    return minecraftInput->mInputHandler.get();
}

InputEventQueue* inputQueue(ClientInstance& client) {
    auto* handler = inputHandler(client);
    return handler ? handler->mInputEventQueue.get() : nullptr;
}

void invokeButtonHandlers(InputHandler& handler, bool down, std::uint32_t id, ClientInstance& client) {
    auto& map   = down ? *handler.mButtonDownHandlerMap : *handler.mButtonUpHandlerMap;
    auto  range = map.equal_range(id);
    for (auto it = range.first; it != range.second; ++it) {
        auto const& entry = it->second;
        if (entry.second) {
            entry.second(FocusImpact::Neutral, client);
        }
    }
}

bool isGameForeground() {
    HWND const foreground = GetForegroundWindow();
    if (foreground == nullptr) {
        return false;
    }
    DWORD pid = 0;
    GetWindowThreadProcessId(foreground, &pid);
    return pid == GetCurrentProcessId();
}

} // namespace probe::detail
