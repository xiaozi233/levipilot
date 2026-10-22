#include "mod/Internal.h"

#include <array>

using namespace probe::detail;

namespace probe::detail {

namespace {

// ---------------------------------------------------------------------------
// Offset table for 1.26.51.01. Keep every numeric field offset here; command
// handlers must not grow their own magic numbers. T7 will move this table into
// docs/offsets/<version>.json once signature anchoring exists.
// ---------------------------------------------------------------------------
struct BlockLayout {
    static constexpr std::uint64_t LegacyPointer = 0x68;
};

struct BlockLegacyLayout {
    static constexpr std::uint64_t NameHash = 0xE0;
    static constexpr std::uint64_t Name     = 0xE8; // std::string
    static constexpr std::uint64_t BlockId  = 0x17E;
    static constexpr std::uint64_t FloatA   = 0x15C;
    static constexpr std::uint64_t FloatB   = 0x178;
};

// MSVC std::string layout (C++20):
//   union buffer [0x00, 0x10)  small string inline / heap pointer
//   size_t size   [0x10, 0x18)
//   size_t cap    [0x18, 0x20)
constexpr std::uint64_t StdStringSizeOffset = 0x10;
constexpr std::uint64_t StdStringSSOCap    = 15;
constexpr std::size_t   MaxStringLength    = 4096;

template <typename T>
bool readValue(std::uint64_t addr, T& out) {
    return readMemory(addr, &out, sizeof(T));
}

std::string readStdString(std::uint64_t addr) {
    std::uint64_t size = 0;
    if (!readValue(addr + StdStringSizeOffset, size) || size > MaxStringLength) {
        return {};
    }
    if (size <= StdStringSSOCap) {
        std::array<char, 16> buffer{};
        if (!readMemory(addr, buffer.data(), buffer.size())) {
            return {};
        }
        return std::string{buffer.data(), static_cast<std::size_t>(size)};
    }
    std::uint64_t pointer = 0;
    if (!readValue(addr, pointer) || pointer == 0) {
        return {};
    }
    std::string out(static_cast<std::size_t>(size), '\0');
    if (!readMemory(pointer, out.data(), out.size())) {
        return {};
    }
    return out;
}

bool isReadableProtection(DWORD protect) {
    if (protect & PAGE_GUARD) {
        return false;
    }
    switch (protect & 0xFF) {
    case PAGE_READONLY:
    case PAGE_READWRITE:
    case PAGE_WRITECOPY:
    case PAGE_EXECUTE_READ:
    case PAGE_EXECUTE_READWRITE:
    case PAGE_EXECUTE_WRITECOPY:
        return true;
    default:
        return false;
    }
}

bool isWritableProtection(DWORD protect) {
    if (protect & PAGE_GUARD) {
        return false;
    }
    switch (protect & 0xFF) {
    case PAGE_READWRITE:
    case PAGE_WRITECOPY:
    case PAGE_EXECUTE_READWRITE:
    case PAGE_EXECUTE_WRITECOPY:
        return true;
    default:
        return false;
    }
}

bool checkRange(std::uint64_t addr, std::size_t len, bool write) {
    if (addr == 0 || len == 0) {
        return false;
    }
    std::uint64_t const end = addr + len;
    if (end < addr) {
        return false; // overflow
    }
    std::uint64_t cursor = addr;
    while (cursor < end) {
        MEMORY_BASIC_INFORMATION info{};
        if (VirtualQuery(reinterpret_cast<LPCVOID>(cursor), &info, sizeof(info)) == 0) {
            return false;
        }
        if (info.State != MEM_COMMIT) {
            return false;
        }
        bool const ok = write ? isWritableProtection(info.Protect) : isReadableProtection(info.Protect);
        if (!ok) {
            return false;
        }
        std::uint64_t const regionEnd =
            reinterpret_cast<std::uint64_t>(info.BaseAddress) + info.RegionSize;
        if (regionEnd <= cursor) {
            return false;
        }
        cursor = regionEnd;
    }
    return true;
}

nlohmann::json valueViews(std::vector<std::uint8_t> const& data) {
    nlohmann::json out;
    out["u8"]  = nlohmann::json::array();
    out["u16"] = nlohmann::json::array();
    out["u32"] = nlohmann::json::array();
    out["u64"] = nlohmann::json::array();
    out["f32"] = nlohmann::json::array();

    for (std::size_t i = 0; i < data.size(); ++i) {
        out["u8"].push_back(data[i]);
    }
    for (std::size_t i = 0; i + 2 <= data.size(); i += 2) {
        std::uint16_t value = 0;
        std::memcpy(&value, data.data() + i, sizeof(value));
        out["u16"].push_back(value);
    }
    for (std::size_t i = 0; i + 4 <= data.size(); i += 4) {
        std::uint32_t value = 0;
        std::memcpy(&value, data.data() + i, sizeof(value));
        out["u32"].push_back(value);
        float asFloat = 0.0f;
        std::memcpy(&asFloat, data.data() + i, sizeof(asFloat));
        out["f32"].push_back(finiteOrZero(asFloat));
    }
    for (std::size_t i = 0; i + 8 <= data.size(); i += 8) {
        std::uint64_t value = 0;
        std::memcpy(&value, data.data() + i, sizeof(value));
        out["u64"].push_back(value);
    }
    return out;
}

} // namespace

bool readableRange(std::uint64_t addr, std::size_t len) { return checkRange(addr, len, false); }

bool writableRange(std::uint64_t addr, std::size_t len) { return checkRange(addr, len, true); }

bool readMemory(std::uint64_t addr, void* dst, std::size_t len) {
    if (addr == 0 || dst == nullptr || len == 0) {
        return false;
    }
    if (!readableRange(addr, len)) {
        return false;
    }
    SIZE_T read = 0;
    BOOL const ok =
        ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<LPCVOID>(addr), dst, len, &read);
    return ok != FALSE && read == len;
}

bool writeMemory(std::uint64_t addr, void const* src, std::size_t len) {
    if (addr == 0 || src == nullptr || len == 0) {
        return false;
    }
    if (!writableRange(addr, len)) {
        return false;
    }
    SIZE_T written = 0;
    BOOL const ok =
        WriteProcessMemory(GetCurrentProcess(), reinterpret_cast<LPVOID>(addr), src, len, &written);
    return ok != FALSE && written == len;
}

std::string hexEncode(void const* data, std::size_t len) {
    static constexpr char Digits[] = "0123456789abcdef";
    auto const*           bytes    = static_cast<std::uint8_t const*>(data);
    std::string           out;
    out.reserve(len * 2);
    for (std::size_t i = 0; i < len; ++i) {
        out.push_back(Digits[bytes[i] >> 4]);
        out.push_back(Digits[bytes[i] & 0x0F]);
    }
    return out;
}

bool parseHexBytes(std::string const& text, std::vector<std::uint8_t>& out) {
    out.clear();
    if (text.empty() || text.size() % 2 != 0) {
        return false;
    }
    auto nibble = [](char ch) -> int {
        if (ch >= '0' && ch <= '9') {
            return ch - '0';
        }
        if (ch >= 'a' && ch <= 'f') {
            return ch - 'a' + 10;
        }
        if (ch >= 'A' && ch <= 'F') {
            return ch - 'A' + 10;
        }
        return -1;
    };
    out.reserve(text.size() / 2);
    for (std::size_t i = 0; i < text.size(); i += 2) {
        int const high = nibble(text[i]);
        int const low  = nibble(text[i + 1]);
        if (high < 0 || low < 0) {
            out.clear();
            return false;
        }
        out.push_back(static_cast<std::uint8_t>((high << 4) | low));
    }
    return true;
}

bool parseAddress(nlohmann::json const& value, std::uint64_t& out) {
    if (value.is_number_unsigned()) {
        out = value.get<std::uint64_t>();
        return true;
    }
    if (value.is_number_integer()) {
        long long const signedValue = value.get<long long>();
        if (signedValue <= 0) {
            return false;
        }
        out = static_cast<std::uint64_t>(signedValue);
        return true;
    }
    if (value.is_string()) {
        auto const& text = value.get_ref<std::string const&>();
        if (text.empty()) {
            return false;
        }
        char*                   end   = nullptr;
        errno                         = 0;
        unsigned long long const parsed = std::strtoull(text.c_str(), &end, 0);
        if (text.front() == '-' || end == text.c_str() || end == nullptr || *end != '\0' || errno == ERANGE) {
            return false;
        }
        out = static_cast<std::uint64_t>(parsed);
        return true;
    }
    return false;
}

bool parseSize(nlohmann::json const& value, std::uint32_t& out) {
    if (!value.is_number_integer() && !value.is_number_unsigned()) {
        return false;
    }
    long long const parsed = value.get<long long>();
    if (parsed <= 0) {
        return false;
    }
    out = static_cast<std::uint32_t>(parsed);
    return true;
}

namespace {

bool addOffset(std::uint64_t base, std::uint64_t offset, std::uint64_t& out) {
    if (base > UINT64_MAX - offset) {
        return false;
    }
    out = base + offset;
    return true;
}

} // namespace

nlohmann::json probeMemory(std::uint64_t addr, std::string const& kind, std::size_t len) {
    nlohmann::json out;
    out["event"] = "probe";
    out["kind"]  = kind;
    out["addr"]  = addr;

    if (kind == "raw") {
        len = std::clamp<std::size_t>(len, 1, 64);
        std::vector<std::uint8_t> data(len);
        if (!readMemory(addr, data.data(), data.size())) {
            out["ok"]     = false;
            out["reason"] = "bad_addr";
            return out;
        }
        out["ok"]   = true;
        out["len"]  = len;
        out["hex"]  = hexEncode(data.data(), data.size());
        out["vals"] = valueViews(data);
        return out;
    }

    if (kind == "block") {
        std::uint64_t legacy     = 0;
        std::uint64_t legacyPtr  = 0;
        std::uint64_t nameAddr   = 0;
        if (!addOffset(addr, BlockLayout::LegacyPointer, legacyPtr)
            || !readValue(legacyPtr, legacy)) {
            out["ok"]     = false;
            out["reason"] = "bad_addr";
            return out;
        }
        out["ok"]     = true;
        out["block"]  = addr;
        out["legacy"] = legacy;
        // Read the name through the legacy pointer so callers can cross-check
        // against the existing `blocks` command without a second round trip.
        std::string name;
        if (addOffset(legacy, BlockLegacyLayout::Name, nameAddr)) {
            name = readStdString(nameAddr);
        }
        if (!name.empty()) {
            out["name"] = name;
        }
        return out;
    }

    if (kind == "legacy") {
        std::uint64_t hash      = 0;
        std::uint16_t blockId   = 0;
        float         valueA    = 0.0f;
        float         valueB    = 0.0f;
        std::uint64_t hashAddr  = 0;
        std::uint64_t idAddr    = 0;
        std::uint64_t floatAAdr = 0;
        std::uint64_t floatBAdr = 0;
        std::uint64_t nameAddr  = 0;
        if (!addOffset(addr, BlockLegacyLayout::NameHash, hashAddr)
            || !addOffset(addr, BlockLegacyLayout::BlockId, idAddr)
            || !addOffset(addr, BlockLegacyLayout::FloatA, floatAAdr)
            || !addOffset(addr, BlockLegacyLayout::FloatB, floatBAdr)
            || !addOffset(addr, BlockLegacyLayout::Name, nameAddr)
            || !readValue(hashAddr, hash)
            || !readValue(idAddr, blockId)
            || !readValue(floatAAdr, valueA)
            || !readValue(floatBAdr, valueB)) {
            out["ok"]     = false;
            out["reason"] = "bad_addr";
            return out;
        }
        out["ok"]       = true;
        out["legacy"]   = addr;
        out["hash"]     = hash;
        out["name"]     = readStdString(nameAddr);
        out["block_id"] = blockId;
        out["float_a"]  = finiteOrZero(valueA);
        out["float_b"]  = finiteOrZero(valueB);
        return out;
    }

    out["ok"]     = false;
    out["reason"] = "bad_kind";
    return out;
}

} // namespace probe::detail
