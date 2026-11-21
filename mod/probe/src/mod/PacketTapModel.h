#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string_view>
#include <type_traits>

namespace probe::detail {

// Budget: the explicit serialize_ids diagnostic stopped because the capture
// session already spent its sampling time. The row is published so the caller
// can see the cut-off instead of silently losing the tail.
enum class PacketByteError : std::uint8_t { None, Seh, Cpp, Reentrant, Budget };

struct PacketByteRecord {
    std::uint64_t   order = 0;
    std::uint32_t   id    = 0;
    std::uint32_t   size  = 0;
    std::uint32_t   tid   = 0;
    std::uint8_t    dir   = 0;
    PacketByteError error = PacketByteError::None;
    std::uint8_t    data[128]{};
};

class PacketSerializationGuard {
public:
    static bool enter(bool& active) noexcept {
        if (active) return false;
        active = true;
        return true;
    }
    explicit PacketSerializationGuard(bool& active) noexcept : mActive(active) {}
    ~PacketSerializationGuard() { mActive = false; }
    PacketSerializationGuard(PacketSerializationGuard const&)            = delete;
    PacketSerializationGuard& operator=(PacketSerializationGuard const&) = delete;

private:
    bool& mActive;
};

inline void copyPacketBytes(PacketByteRecord& out, std::string_view data) noexcept {
    out.size         = static_cast<std::uint32_t>(data.size());
    auto const count = std::min(data.size(), sizeof(out.data));
    if (count) std::memcpy(out.data, data.data(), count);
}

static_assert(std::is_trivially_copyable_v<PacketByteRecord>);

} // namespace probe::detail
