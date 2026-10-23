#include "mod/Internal.h"

#include "mod/GeneratedProfiles.h"

#include <fstream>

using namespace probe::detail;

namespace probe::detail {

namespace {

constexpr std::size_t kAnchorCount    = 4;
constexpr std::size_t kSignatureBytes = 24;
constexpr std::size_t kMaxStructFields = 64;
constexpr std::size_t kMaxComponents   = 64;

struct Anchor {
    char const*                name = nullptr;
    std::uint64_t              address = 0;
    std::uint64_t              resolved = 0;
    std::array<std::uint8_t, kSignatureBytes> expected{};
    std::size_t                expectedLength = 0;
    bool                       configured     = false;
    bool                       match          = true;
    std::uint64_t              elapsedUs      = 0;
};

struct VersionInfo {
    std::string buildId;
    int         major = 0;
    int         minor = 0;
    int         build = 0;
    int         revision = 0;
    bool        profileFound = false;
    std::string profileVersion;
    std::string profileBuildId;
    char const* matchSource = "none";
    bool        signaturesOk = true;
};

struct StructField {
    std::string   type;
    std::string   field;
    std::uint64_t offset      = 0;
    bool          overridden  = false;
    bool          present     = true;
    bool          fromProfile = false;
};

struct ComponentEntry {
    std::uint32_t id         = 0;
    std::uint32_t size       = 0;
    std::string   name;
    bool          overridden = false;
    bool          fromProfile = false;
};

Anchor         gAnchors[kAnchorCount] = {};
VersionInfo    gVersion;
std::atomic_bool gVersionOk{false};
std::atomic_bool gHooksAllowed{false};
std::atomic_bool gAllowVersionMismatch{false};
std::string    gOffsetSource = "builtin";

StructField    gStructFields[kMaxStructFields];
std::size_t    gStructFieldCount = 0;
ComponentEntry gComponents[kMaxComponents];
std::size_t    gComponentCount = 0;

std::atomic<std::uint32_t> gMissingFields{0};
std::mutex                 gMissingMutex;
char                       gLastMissing[160]{};

void noteMissing(char const* type, char const* field) {
    gMissingFields.fetch_add(1, std::memory_order_relaxed);
    std::lock_guard lock(gMissingMutex);
    std::snprintf(gLastMissing, sizeof(gLastMissing), "%s.%s", type, field);
}

void resetTables() {
    gStructFieldCount = 0;
    gComponentCount   = 0;
}

std::string offsetText(std::uint64_t value) {
    char buffer[32]{};
    std::snprintf(buffer, sizeof(buffer), "0x%llX", static_cast<unsigned long long>(value));
    return buffer;
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

bool parseHexInto(
    std::string const&                         text,
    std::array<std::uint8_t, kSignatureBytes>& out,
    std::size_t&                               length
) {
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

// ---------------------------------------------------------------------------
// Version profiles. The data lives in docs/offsets/*.json and is compiled into
// GeneratedProfiles.h; adding a game version does not touch C++ code.
// ---------------------------------------------------------------------------
VersionProfile const* selectProfile(VersionInfo& version) {
    for (std::size_t index = 0; index < kProfileCount; ++index) {
        if (version.buildId == kProfiles[index].buildId) {
            version.matchSource = "exact";
            return &kProfiles[index];
        }
    }
    for (std::size_t index = 0; index < kProfileCount; ++index) {
        char const* prefix = kProfiles[index].versionPrefix;
        if (prefix == nullptr || *prefix == '\0') {
            continue;
        }
        std::string const anchored = std::string{prefix} + ".";
        if (version.buildId.rfind(anchored, 0) == 0) {
            version.matchSource = "prefix";
            return &kProfiles[index];
        }
    }
    for (std::size_t index = 0; index < kProfileCount; ++index) {
        if (version.buildId == kProfiles[index].version) {
            version.matchSource = "version";
            return &kProfiles[index];
        }
    }
    version.matchSource = "none";
    return nullptr;
}

void loadProfileAnchors(VersionProfile const& profile) {
    for (auto& anchor : gAnchors) {
        anchor.expectedLength = 0;
        anchor.configured     = false;
        anchor.expected.fill(0);
        for (std::size_t slot = 0; slot < profile.anchorCount; ++slot) {
            if (std::strcmp(anchor.name, profile.anchors[slot].name) != 0) {
                continue;
            }
            std::size_t length = 0;
            if (parseHexInto(profile.anchors[slot].expectedHex, anchor.expected, length)) {
                anchor.expectedLength = length;
                anchor.configured     = true;
            }
            break;
        }
    }
}

void loadProfileFields(VersionProfile const& profile) {
    for (std::size_t slot = 0; slot < profile.fieldCount && gStructFieldCount < kMaxStructFields;
         ++slot) {
        auto const& source = profile.fields[slot];
        char*                end    = nullptr;
        errno                       = 0;
        unsigned long long const parsed = std::strtoull(source.offset, &end, 0);
        if (source.offset == nullptr || end == source.offset || *end != '\0' || errno == ERANGE) {
            continue;
        }
        gStructFields[gStructFieldCount++] = StructField{
            source.type,
            source.field,
            static_cast<std::uint64_t>(parsed),
            false,
            source.present,
            true,
        };
    }
}

void loadProfileComponents(VersionProfile const& profile) {
    for (std::size_t slot = 0; slot < profile.componentCount && gComponentCount < kMaxComponents;
         ++slot) {
        auto const& source = profile.components[slot];
        gComponents[gComponentCount++] = ComponentEntry{
            source.id,
            source.size,
            source.name,
            false,
            true,
        };
    }
}

StructField* findStructField(std::string const& type, std::string const& field) {
    for (std::size_t index = 0; index < gStructFieldCount; ++index) {
        if (gStructFields[index].type == type && gStructFields[index].field == field) {
            return &gStructFields[index];
        }
    }
    return nullptr;
}

ComponentEntry* findComponent(std::string const& name) {
    for (std::size_t index = 0; index < gComponentCount; ++index) {
        if (gComponents[index].name == name) {
            return &gComponents[index];
        }
    }
    return nullptr;
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
            // "field": null removes an entry, so probe reports no_offset instead
            // of silently reading the wrong field.
            bool const    remove = fieldIt.value().is_null();
            std::uint64_t offset = 0;
            if (!remove && !parseOffsetValue(fieldIt.value(), offset)) {
                ProbeMod::getInstance().bootLog(
                    "resolve: bad offset " + typeIt.key() + "." + fieldIt.key()
                );
                continue;
            }
            if (auto* existing = findStructField(typeIt.key(), fieldIt.key())) {
                if (remove) {
                    existing->present = false;
                } else {
                    existing->offset  = offset;
                    existing->present = true;
                }
                existing->overridden = true;
                continue;
            }
            if (remove) {
                continue;
            }
            if (gStructFieldCount >= kMaxStructFields) {
                ProbeMod::getInstance().bootLog("resolve: too many struct fields");
                break;
            }
            gStructFields[gStructFieldCount++] = StructField{
                typeIt.key(),
                fieldIt.key(),
                offset,
                true,
                true,
                false,
            };
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
        if (size == 0 || size > 4096) {
            continue;
        }
        if (auto* existing = findComponent(it.key())) {
            existing->id         = static_cast<std::uint32_t>(id);
            existing->size       = static_cast<std::uint32_t>(size);
            existing->overridden = true;
            continue;
        }
        if (gComponentCount >= kMaxComponents) {
            ProbeMod::getInstance().bootLog("resolve: too many components");
            break;
        }
        gComponents[gComponentCount++] = ComponentEntry{
            static_cast<std::uint32_t>(id),
            static_cast<std::uint32_t>(size),
            it.key(),
            true,
            false,
        };
    }
}

void recomputeGates() {
    bool signaturesOk = true;
    for (auto const& anchor : gAnchors) {
        if (anchor.configured && !anchor.match) {
            signaturesOk = false;
            break;
        }
    }
    gVersion.signaturesOk = signaturesOk;
    bool const versionOk  = gVersion.profileFound && signaturesOk;
    gVersionOk.store(versionOk, std::memory_order_release);
    gHooksAllowed.store(
        versionOk || gAllowVersionMismatch.load(std::memory_order_relaxed),
        std::memory_order_release
    );
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
    return out;
}

void loadOffsetsFile(std::filesystem::path const& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        return;
    }
    auto parsed = nlohmann::json::parse(in, nullptr, false);
    if (parsed.is_discarded() || !parsed.is_object()) {
        ProbeMod::getInstance().bootLog("resolve: offsets json invalid");
        return;
    }
    gOffsetSource = path.filename().string();
    auto anchors  = parsed.find("anchors");
    if (anchors != parsed.end() && anchors->is_object()) {
        for (auto it = anchors->begin(); it != anchors->end(); ++it) {
            if (!it.value().is_object() || !it.value().contains("expected_hex")) {
                continue;
            }
            for (auto& anchor : gAnchors) {
                if (std::strcmp(it.key().c_str(), anchor.name) != 0) {
                    continue;
                }
                std::size_t length = 0;
                if (it.value().value("expected_hex", std::string{}).empty()) {
                    anchor.expectedLength = 0;
                    anchor.configured     = false;
                } else if (parseHexInto(
                               it.value().value("expected_hex", std::string{}),
                               anchor.expected,
                               length
                           )) {
                    anchor.expectedLength = length;
                    anchor.configured     = true;
                }
                break;
            }
        }
    }
    loadStructOverrides(parsed);
    loadComponentOverrides(parsed);
}

} // namespace

void resolveInit(std::filesystem::path const& modDir) {
    gVersion = readVersion();
    gOffsetSource = "builtin";

    static constexpr char const* Names[kAnchorCount] = {
        "LocalPlayer::normalTick",
        "LocalPlayer::_applyTurnDelta",
        "Actor::getHealth",
        "Actor::isOnGround",
    };
    for (std::size_t index = 0; index < kAnchorCount; ++index) {
        Anchor& anchor        = gAnchors[index];
        anchor.name           = Names[index];
        anchor.address        = anchorAddress(index);
        anchor.resolved       = followThunk(anchor.address);
        anchor.expected.fill(0);
        anchor.expectedLength = 0;
        anchor.configured     = false;
        anchor.match          = true;
        anchor.elapsedUs      = 0;
    }

    resetTables();
    VersionProfile const* profile = selectProfile(gVersion);
    if (profile != nullptr) {
        gVersion.profileFound   = true;
        gVersion.profileVersion = profile->version;
        gVersion.profileBuildId = profile->buildId;
        loadProfileAnchors(*profile);
        loadProfileFields(*profile);
        loadProfileComponents(*profile);
    }

    // Runtime override: same schema, individual fields only. This is how a new
    // version can be tried without touching the compiled profile.
    loadOffsetsFile(modDir / "offsets.json");

    for (auto& anchor : gAnchors) {
        if (!anchor.configured) {
            continue;
        }
        std::uint64_t const start = qpcNow();
        std::array<std::uint8_t, kSignatureBytes> actual{};
        bool const readable = readMemory(anchor.resolved, actual.data(), anchor.expectedLength);
        anchor.elapsedUs    = qpcElapsedUs(start, qpcNow());
        anchor.match        = readable
                       && std::memcmp(actual.data(), anchor.expected.data(), anchor.expectedLength) == 0;
    }

    recomputeGates();
    ProbeMod::getInstance().bootLog(
        "resolve: version=" + gVersion.buildId
        + " profile=" + (gVersion.profileFound ? gVersion.profileVersion : std::string{"none"})
        + " match=" + gVersion.matchSource + " ok=" + (versionOk() ? "1" : "0")
        + " hooks=" + (hooksAllowed() ? "1" : "0") + " source=" + gOffsetSource
    );
}

bool versionOk() { return gVersionOk.load(std::memory_order_acquire); }

bool hooksAllowed() { return gHooksAllowed.load(std::memory_order_acquire); }

void setAllowVersionMismatch(bool allow) {
    gAllowVersionMismatch.store(allow, std::memory_order_release);
    recomputeGates();
    if (allow && !versionOk()) {
        ProbeMod::getInstance().bootLog(
            "resolve: compat mode enabled; hooked features run without a matching profile"
        );
    }
}

bool allowVersionMismatch() { return gAllowVersionMismatch.load(std::memory_order_acquire); }

std::string buildId() { return gVersion.buildId; }

bool structOffset(char const* type, char const* field, std::uint64_t& out) {
    for (std::size_t index = 0; index < gStructFieldCount; ++index) {
        auto const& entry = gStructFields[index];
        if (entry.type == type && entry.field == field) {
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
    for (std::size_t index = 0; index < gComponentCount; ++index) {
        if (gComponents[index].id == id) {
            size = gComponents[index].size;
            return true;
        }
    }
    return false;
}

std::string componentName(std::uint32_t id) {
    for (std::size_t index = 0; index < gComponentCount; ++index) {
        if (gComponents[index].id == id) {
            return gComponents[index].name;
        }
    }
    return {};
}

nlohmann::json structTableJson() {
    nlohmann::json fields = nlohmann::json::array();
    for (std::size_t index = 0; index < gStructFieldCount; ++index) {
        auto const& entry = gStructFields[index];
        fields.push_back({
            {"struct",     entry.type      },
            {"field",      entry.field     },
            {"offset",     entry.offset    },
            {"overridden", entry.overridden},
            {"present",    entry.present   },
            {"builtin",    entry.fromProfile},
        });
    }
    nlohmann::json components = nlohmann::json::array();
    for (std::size_t index = 0; index < gComponentCount; ++index) {
        auto const& entry = gComponents[index];
        components.push_back({
            {"id",         entry.id         },
            {"name",       entry.name       },
            {"size",       entry.size       },
            {"overridden", entry.overridden },
            {"builtin",    entry.fromProfile},
        });
    }
    std::string lastMissing;
    {
        std::lock_guard lock(gMissingMutex);
        lastMissing = gLastMissing;
    }
    return nlohmann::json{
        {"source",             gOffsetSource          },
        {"profile_found",      gVersion.profileFound  },
        {"profile_version",    gVersion.profileVersion},
        {"struct_fields",      std::move(fields)      },
        {"struct_field_count", gStructFieldCount      },
        {"components",         std::move(components)  },
        {"component_count",    gComponentCount        },
        {"missing_lookups",    gMissingFields.load(std::memory_order_relaxed)},
        {"last_missing",       lastMissing            },
    };
}

nlohmann::json profileJson() {
    std::size_t configured = 0;
    std::size_t matched    = 0;
    for (auto const& anchor : gAnchors) {
        if (!anchor.configured) {
            continue;
        }
        ++configured;
        if (anchor.match) {
            ++matched;
        }
    }
    return nlohmann::json{
        // Legacy diagnose fields kept for older clients.
        {"expected",          gVersion.profileFound ? gVersion.profileVersion
                                                    : std::string{"unknown"}},
        {"file",              gVersion.buildId                  },
        {"prefix_ok",         gVersion.profileFound             },
        {"version",           gVersion.buildId                  },
        {"profile_found",     gVersion.profileFound             },
        {"profile_version",   gVersion.profileVersion           },
        {"profile_build_id",  gVersion.profileBuildId           },
        {"match",             gVersion.matchSource              },
        {"version_ok",        versionOk()                       },
        {"signatures_ok",     gVersion.signaturesOk             },
        {"compat",            allowVersionMismatch()            },
        {"hooks_allowed",     hooksAllowed()                    },
        {"anchors_configured", configured                       },
        {"anchors_matched",   matched                           },
        {"offset_source",     gOffsetSource                     },
        {"struct_fields",     gStructFieldCount                 },
        {"components",        gComponentCount                   },
    };
}

nlohmann::json capabilitiesJson() {
    bool const hooks = hooksAllowed();
    bool const offsets = gStructFieldCount > 0 || gComponentCount > 0;
    return nlohmann::json{
        {"hooks",      hooks          },
        {"input",      hooks          },
        {"watch",      hooks          },
        {"record",     hooks          },
        {"packets",    hooks          },
        {"offsets",    offsets        },
        {"components", gComponentCount > 0},
        {"profile",    gVersion.profileFound},
        {"compat",     allowVersionMismatch()},
    };
}

nlohmann::json profileDump() {
    nlohmann::json anchors = nlohmann::json::object();
    for (auto const& anchor : gAnchors) {
        std::size_t const length =
            anchor.expectedLength > 0 ? anchor.expectedLength : kSignatureBytes;
        std::array<std::uint8_t, kSignatureBytes> actual{};
        bool const readable = readMemory(anchor.resolved, actual.data(), length);
        anchors[anchor.name] = {
            {"kind",         "symbol"                                   },
            {"expected_hex", readable ? hexEncode(actual.data(), length) : ""},
        };
    }

    nlohmann::json structs = nlohmann::json::object();
    for (std::size_t index = 0; index < gStructFieldCount; ++index) {
        auto const& entry = gStructFields[index];
        structs[entry.type][entry.field] =
            entry.present ? nlohmann::json(offsetText(entry.offset)) : nlohmann::json(nullptr);
    }

    nlohmann::json components = nlohmann::json::object();
    for (std::size_t index = 0; index < gComponentCount; ++index) {
        auto const& entry = gComponents[index];
        components[entry.name] = {
            {"id",   entry.id  },
            {"size", entry.size},
        };
    }

    std::string const versionPrefix =
        std::to_string(gVersion.major) + "." + std::to_string(gVersion.minor) + "."
        + std::to_string(gVersion.build);

    nlohmann::json profile = {
        {"runtime",        true                  },
        {"version",        gVersion.buildId      },
        {"build_id",       gVersion.buildId      },
        {"version_prefix", versionPrefix         },
        {"note",           "Generated by profile_dump; save as docs/offsets/<version>.json and "
                           "run driver/gen_profiles.py."},
        {"anchors",        anchors               },
        {"structs",        structs               },
        {"components",     components            },
    };

    return nlohmann::json{
        {"event",         "profile_dump"            },
        {"version",       gVersion.buildId          },
        {"build_id",      gVersion.buildId          },
        {"version_prefix", versionPrefix            },
        {"profile_found", gVersion.profileFound     },
        {"profile_version", gVersion.profileVersion },
        {"profile_build_id", gVersion.profileBuildId},
        {"match",         gVersion.matchSource      },
        {"version_ok",    versionOk()               },
        {"compat",        allowVersionMismatch()    },
        {"anchors",       anchors                   },
        {"structs",       structs                   },
        {"components",    components                },
        {"profile",       profile                   },
    };
}

nlohmann::json profileProbe(std::string const& buildId) {
    VersionInfo probe;
    probe.buildId = buildId;
    VersionProfile const* profile = selectProfile(probe);
    nlohmann::json out{
        {"event",         "profile_probe"                     },
        {"build_id",      buildId                             },
        {"profile_found", profile != nullptr                  },
        {"match",         probe.matchSource                   },
        {"simulated",     buildId != gVersion.buildId         },
        {"current",       gVersion.buildId                    },
    };
    if (profile != nullptr) {
        out["profile_version"]  = profile->version;
        out["profile_build_id"] = profile->buildId;
        out["anchors"]          = profile->anchorCount;
        out["struct_fields"]    = profile->fieldCount;
        out["components"]       = profile->componentCount;
    }
    return out;
}

nlohmann::json symbolResolve(std::string const& name) {
    nlohmann::json out{
        {"event", "symbol_resolve"},
        {"name",  name            },
    };
    // H1-B: baked-in compile-time anchors for symbols that the import library
    // exposes; SymbolView alone does not resolve game imports.
    std::uint64_t anchor = 0;
    if (name == "BatchedNetworkPeer::$sendPacket") {
        anchor = reinterpret_cast<std::uint64_t>(
            ll::memory::toFuncPtr(&BatchedNetworkPeer::$sendPacket)
        );
    } else if (name == "NetworkConnection::receivePacket") {
        anchor = reinterpret_cast<std::uint64_t>(
            ll::memory::toFuncPtr(&NetworkConnection::receivePacket)
        );
    } else if (name == "LoopbackPacketSender::$sendToServer") {
        anchor = reinterpret_cast<std::uint64_t>(
            ll::memory::toFuncPtr(&LoopbackPacketSender::$sendToServer)
        );
    } else {
        try {
            anchor = reinterpret_cast<std::uint64_t>(ll::memory::SymbolView{name}.resolve());
        } catch (...) {
            anchor = 0;
        }
    }
    out["anchor"] = anchor;
    if (anchor == 0) {
        out["ok"]     = false;
        out["reason"] = "symbol_not_found";
        return out;
    }
    // Follow `jmp qword ptr [rip+disp]` import stubs to the real function.
    std::uint64_t real = anchor;
    int           hops = 0;
    while (hops < 4) {
        std::uint8_t bytes[6]{};
        if (!readMemory(real, bytes, sizeof(bytes))) {
            break;
        }
        std::uint64_t target = 0;
        if (bytes[0] == 0xFF && bytes[1] == 0x25) {
            // jmp qword ptr [rip+disp] (import stub)
            std::int32_t disp = 0;
            std::memcpy(&disp, bytes + 2, sizeof(disp));
            std::uint64_t const slot = real + 6 + static_cast<std::int64_t>(disp);
            if (!readMemory(slot, &target, sizeof(target))) {
                break;
            }
        } else if (bytes[0] == 0xE9) {
            // jmp rel32 (relative thunk, often into another module)
            std::int32_t rel = 0;
            std::memcpy(&rel, bytes + 1, sizeof(rel));
            target = real + 5 + static_cast<std::int64_t>(rel);
        } else {
            break;
        }
        if (target == 0) {
            break;
        }
        real = target;
        ++hops;
    }
    out["real"]       = real;
    out["thunk_hops"] = hops;
    out["thunked"]    = real != anchor;
    out["executable"] = hookAddressExecutable(real);

    HMODULE module = nullptr;
    if (GetModuleHandleExW(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            reinterpret_cast<LPCWSTR>(real),
            &module
        )
        && module != nullptr) {
        wchar_t path[MAX_PATH]{};
        if (GetModuleFileNameW(module, path, MAX_PATH) != 0) {
            out["module"] = std::filesystem::path(path).filename().string();
        }
        out["rva"] = real - reinterpret_cast<std::uint64_t>(module);
    }
    out["ok"] = out["executable"];
    return out;
}

nlohmann::json diagnoseJson() {
    nlohmann::json out;
    out["event"]      = "diagnose";
    out["version"]    = profileJson();
    out["version_ok"] = versionOk();
    out["source"]     = gOffsetSource;
    out["capabilities"] = capabilitiesJson();
    out["structs"]    = structTableJson();
    out["anchors"]    = nlohmann::json::array();
    for (auto const& anchor : gAnchors) {
        out["anchors"].push_back({
            {"name",       anchor.name                     },
            {"address",    anchor.address                  },
            {"resolved",   anchor.resolved                 },
            {"configured", anchor.configured               },
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
