#include "mod/Internal.h"

#include <fstream>

using namespace probe::detail;

namespace probe::detail {

namespace {

constexpr std::size_t kAnchorCount    = 4;
constexpr std::size_t kSignatureBytes = 24;
constexpr int         kExpectedMajor  = 1;
constexpr int         kExpectedMinor  = 26;
constexpr int         kExpectedBuild  = 51;

struct Anchor {
    char const*                name;
    std::uint64_t              address = 0;
    std::uint64_t              resolved = 0;
    std::array<std::uint8_t, kSignatureBytes> expected{};
    std::size_t                expectedLength = 0;
    bool                       match          = true;
    std::uint64_t              elapsedUs      = 0;
};

struct VersionInfo {
    bool        prefixOk = false;
    std::string buildId;
    int         major = 0;
    int         minor = 0;
    int         build = 0;
    int         revision = 0;
};

Anchor         gAnchors[kAnchorCount];
VersionInfo    gVersion;
std::atomic_bool gVersionOk{true};
std::string    gSource = "builtin";

// ---------------------------------------------------------------------------
// F5: struct/component offset table. Built-ins mirror docs/offsets/<ver>.json;
// an offsets.json in the mod directory may override individual fields.
// ---------------------------------------------------------------------------
struct StructField {
    char const*   type;
    char const*   field;
    std::uint64_t offset      = 0;
    bool          overridden  = false;
    bool          present     = true;
};

StructField kStructFields[] = {
    {"Block",       "LegacyPointer", 0x68,  false},
    {"BlockLegacy", "NameHash",      0xE0,  false},
    {"BlockLegacy", "Name",          0xE8,  false},
    {"BlockLegacy", "BlockId",       0x17E, false},
    {"BlockLegacy", "FloatA",        0x15C, false},
    {"BlockLegacy", "FloatB",        0x178, false},
    // ECS component layout used by the H0 watch check (stride 36, 3 Vec3s).
    {"StateVector", "PositionX",     0x00,  false},
    {"StateVector", "PositionY",     0x04,  false},
    {"StateVector", "PositionZ",     0x08,  false},
    {"StateVector", "VelX",          0x18,  false},
    {"StateVector", "VelY",          0x1C,  false},
    {"StateVector", "VelZ",          0x20,  false},
};

struct ComponentEntry {
    std::uint32_t id         = 0;
    std::uint32_t size       = 0;
    std::string   name;
    bool          overridden = false;
};

constexpr std::size_t kMaxComponents = 64;
// 459100728 was read live from a 1.26.51.01 pig entity (stride 36).
ComponentEntry        kComponents[kMaxComponents] = {
    {459100728, 36, "StateVector", false},
};
std::size_t kComponentCount = 1;
std::atomic<std::uint32_t> gMissingFields{0};
std::mutex                 gMissingMutex;
char                       gLastMissing[160]{};

void noteMissing(char const* type, char const* field) {
    gMissingFields.fetch_add(1, std::memory_order_relaxed);
    std::lock_guard lock(gMissingMutex);
    std::snprintf(gLastMissing, sizeof(gLastMissing), "%s.%s", type, field);
}

bool parseOffsetValue(nlohmann::json const& value, std::uint64_t& out) {
    if (value.is_number_unsigned()) {
        out = value.get<std::uint64_t>();
        return true;
    }
    if (value.is_number_integer()) {
        long long const signedValue = value.get<long long>();
        if (signedValue < 0) {
            return false;
        }
        out = static_cast<std::uint64_t>(signedValue);
        return true;
    }
    if (value.is_string()) {
        auto const& text = value.get_ref<std::string const&>();
        if (text.empty() || text.front() == '-') {
            return false;
        }
        char*                   end    = nullptr;
        errno                          = 0;
        unsigned long long const parsed = std::strtoull(text.c_str(), &end, 0);
        if (end == text.c_str() || end == nullptr || *end != '\0' || errno == ERANGE) {
            return false;
        }
        out = static_cast<std::uint64_t>(parsed);
        return true;
    }
    return false;
}

void loadStructOverrides(nlohmann::json const& root) {
    auto structs = root.find("structs");
    if (structs == root.end() || !structs->is_object()) {
        return;
    }
    for (auto typeIt = structs->begin(); typeIt != structs->end(); ++typeIt) {
        if (!typeIt.value().is_object()) {
            continue;
        }
        for (auto fieldIt = typeIt.value().begin(); fieldIt != typeIt.value().end(); ++fieldIt) {
            // "field": null removes a built-in entry, which makes probe report
            // no_offset instead of silently reading the wrong field.
            bool const remove = fieldIt.value().is_null();
            std::uint64_t offset = 0;
            if (!remove && !parseOffsetValue(fieldIt.value(), offset)) {
                ProbeMod::getInstance().bootLog(
                    "resolve: bad offset " + typeIt.key() + "." + fieldIt.key()
                );
                continue;
            }
            bool matched = false;
            for (auto& field : kStructFields) {
                if (typeIt.key() == field.type && fieldIt.key() == field.field) {
                    if (remove) {
                        field.present = false;
                    } else {
                        field.offset    = offset;
                        field.present   = true;
                    }
                    field.overridden = true;
                    matched          = true;
                    break;
                }
            }
            if (!matched) {
                ProbeMod::getInstance().bootLog(
                    "resolve: unknown struct field " + typeIt.key() + "." + fieldIt.key()
                );
            }
        }
    }
}

void loadComponentOverrides(nlohmann::json const& root) {
    auto components = root.find("components");
    if (components == root.end() || !components->is_object()) {
        return;
    }
    for (auto it = components->begin(); it != components->end(); ++it) {
        if (!it.value().is_object() || !it.value().contains("id") || !it.value().contains("size")) {
            continue;
        }
        std::uint64_t id   = 0;
        std::uint64_t size = 0;
        if (!parseOffsetValue(it.value()["id"], id) || !parseOffsetValue(it.value()["size"], size)) {
            continue;
        }
        if (size == 0 || size > 4096 || kComponentCount >= kMaxComponents) {
            continue;
        }
        kComponents[kComponentCount++] = ComponentEntry{
            static_cast<std::uint32_t>(id),
            static_cast<std::uint32_t>(size),
            it.key(),
            true,
        };
    }
}


std::uint64_t anchorAddress(std::size_t index) {
    switch (index) {
    case 0:
        return reinterpret_cast<std::uint64_t>(ll::memory::toFuncPtr(&LocalPlayer::$normalTick));
    case 1:
        return reinterpret_cast<std::uint64_t>(ll::memory::toFuncPtr(&LocalPlayer::_applyTurnDelta));
    case 2:
        return reinterpret_cast<std::uint64_t>(ll::memory::toFuncPtr(&Actor::getHealth));
    default:
        return reinterpret_cast<std::uint64_t>(ll::memory::toFuncPtr(&Actor::isOnGround));
    }
}

// LL import stubs are `jmp qword ptr [rip+0]`; follow them to the real game
// function so signatures are not polluted by ASLR-dependent pointers.
std::uint64_t followThunk(std::uint64_t address) {
    std::array<std::uint8_t, 6> bytes{};
    if (!readMemory(address, bytes.data(), bytes.size())) {
        return address;
    }
    if (bytes[0] != 0xFF || bytes[1] != 0x25) {
        return address;
    }
    std::uint64_t target = 0;
    if (!readMemory(address + 6, &target, sizeof(target))) {
        return address;
    }
    return target != 0 ? target : address;
}


VersionInfo readVersion() {
    VersionInfo out;
    wchar_t     path[MAX_PATH]{};
    if (GetModuleFileNameW(nullptr, path, MAX_PATH) == 0) {
        return out;
    }
    DWORD handle = 0;
    DWORD size   = GetFileVersionInfoSizeW(path, &handle);
    if (size == 0) {
        return out;
    }
    std::vector<std::uint8_t> data(size);
    if (!GetFileVersionInfoW(path, 0, size, data.data())) {
        return out;
    }
    VS_FIXEDFILEINFO* info = nullptr;
    UINT              length = 0;
    if (!VerQueryValueW(data.data(), L"\\", reinterpret_cast<void**>(&info), &length) || info == nullptr
        || length < sizeof(VS_FIXEDFILEINFO)) {
        return out;
    }
    out.major    = static_cast<int>(HIWORD(info->dwFileVersionMS));
    out.minor    = static_cast<int>(LOWORD(info->dwFileVersionMS));
    out.build    = static_cast<int>(HIWORD(info->dwFileVersionLS));
    out.revision = static_cast<int>(LOWORD(info->dwFileVersionLS));
    out.buildId  = std::to_string(out.major) + "." + std::to_string(out.minor) + "."
                + std::to_string(out.build) + "." + std::to_string(out.revision);
    out.prefixOk =
        out.major == kExpectedMajor && out.minor == kExpectedMinor && out.build == kExpectedBuild;
    return out;
}

bool parseHexInto(std::string const& text, std::array<std::uint8_t, kSignatureBytes>& out, std::size_t& length) {
    std::vector<std::uint8_t> bytes;
    std::string               cleaned;
    for (char ch : text) {
        if (ch != ' ' && ch != '\t' && ch != '\n' && ch != '\r') {
            cleaned.push_back(ch);
        }
    }
    if (!parseHexBytes(cleaned, bytes) || bytes.empty() || bytes.size() > kSignatureBytes) {
        return false;
    }
    out.fill(0);
    std::copy(bytes.begin(), bytes.end(), out.begin());
    length = bytes.size();
    return true;
}

void loadOverrides(std::filesystem::path const& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        return;
    }
    auto parsed = nlohmann::json::parse(in, nullptr, false);
    if (parsed.is_discarded() || !parsed.is_object()) {
        ProbeMod::getInstance().bootLog("resolve: offsets json invalid");
        return;
    }
    gSource = path.filename().string();
    auto anchors = parsed.find("anchors");
    if (anchors != parsed.end() && anchors->is_object()) {
        for (auto it = anchors->begin(); it != anchors->end(); ++it) {
            if (!it.value().is_object() || !it.value().contains("expected_hex")) {
                continue;
            }
            for (auto& anchor : gAnchors) {
                if (it.key() == anchor.name) {
                    std::size_t length = 0;
                    if (parseHexInto(it.value().value("expected_hex", std::string{}), anchor.expected, length)) {
                        anchor.expectedLength = length;
                    }
                }
            }
        }
    }
    loadStructOverrides(parsed);
    loadComponentOverrides(parsed);
}

} // namespace

void resolveInit(std::filesystem::path const& modDir) {
    // Populate the built-in signature table first; a user-supplied offsets.json
    // may override individual anchors without a rebuild.
    // Filled from the live 1.26.51.01 build; see docs/offsets/1.26.51.01.json.
    static constexpr std::array<std::array<std::uint8_t, kSignatureBytes>, kAnchorCount> Builtin{{
        {0x55, 0x41, 0x56, 0x56, 0x57, 0x53, 0x48, 0x81, 0xec, 0xf0, 0x00, 0x00, 0x00, 0x48, 0x8d, 0xac, 0x24, 0x80, 0x00, 0x00, 0x00, 0x0f, 0x29, 0x75},
        {0x56, 0x57, 0x53, 0x48, 0x83, 0xec, 0x50, 0x48, 0x89, 0xce, 0x8b, 0x05, 0x20, 0x72, 0x0e, 0x00, 0x65, 0x48, 0x8b, 0x0c, 0x25, 0x58, 0x00, 0x00},
        {0x48, 0x83, 0xc1, 0x08, 0x48, 0xff, 0x25, 0x95, 0x43, 0x26, 0x00, 0xcc, 0xcc, 0xcc, 0xcc, 0xcc, 0x48, 0x83, 0xc1, 0x08, 0x48, 0xff, 0x25, 0xe5},
        {0x48, 0x8b, 0x51, 0x10, 0x8b, 0x41, 0x18, 0x48, 0x8b, 0x4a, 0x48, 0x4c, 0x8b, 0x42, 0x50, 0x49, 0x29, 0xc8, 0x49, 0xc1, 0xe8, 0x03, 0x41, 0xff},
    }};
    static constexpr std::array<std::size_t, kAnchorCount> BuiltinLength{{24, 24, 24, 24}};
    static constexpr char const* Names[kAnchorCount] = {
        "LocalPlayer::normalTick",
        "LocalPlayer::_applyTurnDelta",
        "Actor::getHealth",
        "Actor::isOnGround",
    };

    gVersion = readVersion();
    for (std::size_t index = 0; index < kAnchorCount; ++index) {
        Anchor& anchor       = gAnchors[index];
        anchor.name          = Names[index];
        anchor.address       = anchorAddress(index);
        anchor.resolved      = followThunk(anchor.address);
        anchor.expected      = Builtin[index];
        anchor.expectedLength = BuiltinLength[index];
        anchor.match         = true;
        anchor.elapsedUs     = 0;
    }
    loadOverrides(modDir / "offsets.json");

    bool signaturesOk = true;
    for (auto& anchor : gAnchors) {
        if (anchor.expectedLength == 0) {
            continue; // unconfigured: version check only
        }
        std::uint64_t const start = qpcNow();
        std::array<std::uint8_t, kSignatureBytes> actual{};
        bool const readable = readMemory(anchor.resolved, actual.data(), anchor.expectedLength);
        anchor.elapsedUs    = qpcElapsedUs(start, qpcNow());
        anchor.match        = readable
                        && std::memcmp(actual.data(), anchor.expected.data(), anchor.expectedLength) == 0;
        if (!anchor.match) {
            signaturesOk = false;
        }
    }
    bool const ok = gVersion.prefixOk && signaturesOk;
    gVersionOk.store(ok, std::memory_order_release);
    ProbeMod::getInstance().bootLog(
        "resolve: version=" + gVersion.buildId + " expected=" + std::to_string(kExpectedMajor) + "."
        + std::to_string(kExpectedMinor) + "." + std::to_string(kExpectedBuild) + " ok="
        + (ok ? "1" : "0") + " source=" + gSource
    );
}

bool versionOk() { return gVersionOk.load(std::memory_order_acquire); }

std::string buildId() { return gVersion.buildId; }

bool structOffset(char const* type, char const* field, std::uint64_t& out) {
    for (auto const& entry : kStructFields) {
        if (std::strcmp(type, entry.type) == 0 && std::strcmp(field, entry.field) == 0) {
            if (!entry.present) {
                noteMissing(type, field);
                return false;
            }
            out = entry.offset;
            return true;
        }
    }
    noteMissing(type, field);
    return false;
}

bool componentSize(std::uint32_t id, std::uint32_t& size) {
    for (std::size_t index = 0; index < kComponentCount; ++index) {
        if (kComponents[index].id == id) {
            size = kComponents[index].size;
            return true;
        }
    }
    return false;
}

std::string componentName(std::uint32_t id) {
    for (std::size_t index = 0; index < kComponentCount; ++index) {
        if (kComponents[index].id == id) {
            return kComponents[index].name;
        }
    }
    return {};
}

nlohmann::json structTableJson() {
    nlohmann::json fields = nlohmann::json::array();
    for (auto const& entry : kStructFields) {
        fields.push_back({
            {"struct",     entry.type      },
            {"field",      entry.field     },
            {"offset",     entry.offset    },
            {"overridden", entry.overridden},
            {"present",    entry.present   },
        });
    }
    nlohmann::json components = nlohmann::json::array();
    for (std::size_t index = 0; index < kComponentCount; ++index) {
        components.push_back({
            {"id",         kComponents[index].id         },
            {"name",       kComponents[index].name       },
            {"size",       kComponents[index].size       },
            {"overridden", kComponents[index].overridden },
        });
    }
    std::string lastMissing;
    {
        std::lock_guard lock(gMissingMutex);
        lastMissing = gLastMissing;
    }
    return nlohmann::json{
        {"source",             gSource                                  },
        {"struct_fields",      std::move(fields)                        },
        {"struct_field_count", std::size(kStructFields)                 },
        {"components",         std::move(components)                    },
        {"component_count",    kComponentCount                          },
        {"missing_lookups",    gMissingFields.load(std::memory_order_relaxed)},
        {"last_missing",       lastMissing                              },
    };
}

nlohmann::json diagnoseJson() {
    nlohmann::json out;
    out["event"] = "diagnose";
    out["version"] = {
        {"expected",  "1.26.51.01"  },
        {"file",      gVersion.buildId},
        {"prefix_ok", gVersion.prefixOk},
    };
    out["version_ok"] = versionOk();
    out["source"]     = gSource;
    out["structs"]    = structTableJson();
    out["anchors"]    = nlohmann::json::array();
    for (auto const& anchor : gAnchors) {
        out["anchors"].push_back({
            {"name",       anchor.name                     },
            {"address",    anchor.address                  },
            {"resolved",   anchor.resolved                 },
            {"configured", anchor.expectedLength != 0      },
            {"match",      anchor.match                    },
            {"signature_bytes", anchor.expectedLength      },
            {"elapsed_us", anchor.elapsedUs                },
        });
    }
    return out;
}

nlohmann::json signatureCheck(std::string const& name, std::string const& expectedHex) {
    for (auto const& anchor : gAnchors) {
        if (name != anchor.name) {
            continue;
        }
        std::array<std::uint8_t, kSignatureBytes> expected{};
        std::size_t                               length = 0;
        if (!parseHexInto(expectedHex, expected, length)) {
            return nlohmann::json{
                {"event",  "error"      },
                {"reason", "bad_hex"    },
            };
        }
        std::array<std::uint8_t, kSignatureBytes> actual{};
        bool const readable = readMemory(anchor.resolved, actual.data(), length);
        bool const match    = readable && std::memcmp(actual.data(), expected.data(), length) == 0;
        return nlohmann::json{
            {"event",   "signature_check"          },
            {"name",    name                       },
            {"address", anchor.address             },
            {"resolved", anchor.resolved           },
            {"length",  length                     },
            {"match",   match                      },
            {"readable", readable                  },
            {"actual_hex", readable ? hexEncode(actual.data(), length) : ""},
        };
    }
    return nlohmann::json{
        {"event",  "error"         },
        {"reason", "unknown_anchor"},
        {"name",   name            },
    };
}

} // namespace probe::detail
