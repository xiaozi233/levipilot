#include "mod/Internal.h"

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <optional>
#include <sstream>
#include <vector>

using namespace probe::detail;

namespace probe {

namespace {

// The game applies loadOptionsFromString immediately but only persists options
// on its own schedule. The harness needs deterministic persistence, so it
// updates the same options.txt itself, preserving CRLF and unknown lines.
std::optional<std::filesystem::path> findOptionsFile(std::filesystem::path const& modDir) {
    std::vector<std::filesystem::path> roots;
    auto const                         versionDir = modDir.parent_path().parent_path();
    roots.push_back(versionDir / "Minecraft Bedrock" / "Users");
    wchar_t     appData[MAX_PATH]{};
    DWORD const appDataLen = GetEnvironmentVariableW(L"APPDATA", appData, MAX_PATH);
    if (appDataLen > 0 && appDataLen < MAX_PATH) {
        roots.push_back(std::filesystem::path{appData} / "Minecraft Bedrock" / "Users");
    }

    for (auto const& root : roots) {
        std::error_code ec;
        if (!std::filesystem::is_directory(root, ec)) {
            continue;
        }
        for (auto const& entry : std::filesystem::directory_iterator(root, ec)) {
            auto candidate = entry.path() / "games" / "com.mojang" / "minecraftpe" / "options.txt";
            if (std::filesystem::is_regular_file(candidate, ec)) {
                return candidate;
            }
        }
    }
    return std::nullopt;
}

bool persistOptions(
    std::filesystem::path const&              path,
    std::map<std::string, std::string> const& updates,
    std::string&                              error
) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        error = "options_open_failed";
        return false;
    }
    std::vector<std::string> lines;
    std::string              line;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        lines.push_back(line);
    }
    in.close();

    std::map<std::string, std::string> remaining = updates;
    for (auto& existing : lines) {
        auto const pos = existing.find(':');
        if (pos == std::string::npos || pos == 0) {
            continue;
        }
        auto it = remaining.find(existing.substr(0, pos));
        if (it != remaining.end()) {
            existing = it->first + ":" + it->second;
            remaining.erase(it);
        }
    }
    for (auto const& [key, value] : remaining) {
        lines.push_back(key + ":" + value);
    }

    auto tmp  = path;
    tmp      += ".harness.tmp";
    std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
    if (!out) {
        error = "options_tmp_open_failed";
        return false;
    }
    for (auto const& l : lines) {
        out << l << "\r\n";
    }
    out.close();
    if (!out) {
        error = "options_write_failed";
        std::filesystem::remove(tmp);
        return false;
    }

    if (!MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        error = "options_replace_failed";
        std::filesystem::remove(tmp);
        return false;
    }
    return true;
}

} // namespace

void ProbeMod::setOptions(std::string const& text) {
    auto client = ll::service::getClientInstance();
    if (!client) {
        enqueue(R"({"event":"options_set","ok":false,"reason":"no_client"})");
        return;
    }
    try {
        auto& options = client->getOptions();
        options.loadOptionsFromString(text);
        options.notifySaveAllowed();
        options.saveIfNeeded();

        std::map<std::string, std::string> updates;
        std::istringstream                 stream(text);
        std::string                        line;
        while (std::getline(stream, line)) {
            if (!line.empty() && line.back() == '\r') {
                line.pop_back();
            }
            auto const pos = line.find(':');
            if (pos == std::string::npos || pos == 0) {
                continue;
            }
            updates[line.substr(0, pos)] = line.substr(pos + 1);
        }

        bool saved = false;
        if (!updates.empty()) {
            if (auto path = findOptionsFile(getSelf().getModDir())) {
                std::string error;
                saved = persistOptions(*path, updates, error);
                if (!saved) {
                    bootLog("options: persist failed " + error);
                }
            } else {
                bootLog("options: options.txt not found");
            }
        }

        enqueue(
            nlohmann::json{
                {"event", "options_set"},
                {"ok",    true         },
                {"bytes", text.size()  },
                {"saved", saved        },
        }
                .dump()
        );
    } catch (std::exception const& e) {
        enqueue(
            nlohmann::json{
                {"event",   "options_set"},
                {"ok",      false        },
                {"reason",  "exception"  },
                {"message", e.what()     },
        }
                .dump()
        );
    }
}

void ProbeMod::saveOptions() {
    auto client = ll::service::getClientInstance();
    if (!client) {
        enqueue(R"({"event":"options_saved","ok":false,"reason":"no_client"})");
        return;
    }
    try {
        client->getOptions().notifySaveAllowed();
        client->getOptions().saveIfNeeded();
        enqueue(R"({"event":"options_saved","ok":true})");
    } catch (std::exception const& e) {
        enqueue(
            nlohmann::json{
                {"event",   "options_saved"},
                {"ok",      false          },
                {"reason",  "exception"    },
                {"message", e.what()       },
        }
                .dump()
        );
    }
}

} // namespace probe