#include "mod/Internal.h"

using namespace probe::detail;

namespace probe {

bool ProbeMod::dispatchInspectionCommand(std::string const& cmd, nlohmann::json& j) {
    if (cmd == "mem_read") {
        nlohmann::json out;
        out["event"] = "mem_read";
        std::uint64_t addr = 0;
        if (!j.contains("addr") || !parseAddress(j["addr"], addr)) {
            out["ok"]     = false;
            out["reason"] = "bad_addr";
            enqueue(out.dump());
            return true;
        }
        std::size_t len = std::clamp<std::size_t>(j.value("len", 16), 1, 4096);
        std::vector<std::uint8_t> bytes(len);
        out["addr"] = addr;
        out["len"]  = len;
        if (!readMemory(addr, bytes.data(), bytes.size())) {
            out["ok"]     = false;
            out["reason"] = "bad_addr";
        } else {
            out["ok"]  = true;
            out["hex"] = hexEncode(bytes.data(), bytes.size());
        }
        enqueue(out.dump());
        return true;
    }
    if (cmd == "mem_write") {
        if (!mAllowMemoryWrite.load(std::memory_order_relaxed)) {
            enqueue(R"({"event":"error","reason":"write_disabled","hint":"set allow_write=true in harness.json"})");
            return true;
        }
        nlohmann::json out;
        out["event"] = "mem_write";
        std::uint64_t addr = 0;
        if (!j.contains("addr") || !parseAddress(j["addr"], addr)) {
            out["ok"]     = false;
            out["reason"] = "bad_addr";
            enqueue(out.dump());
            return true;
        }
        std::vector<std::uint8_t> bytes;
        if (!j.contains("hex") || !parseHexBytes(j.value("hex", std::string{}), bytes) || bytes.empty()) {
            out["ok"]     = false;
            out["reason"] = "bad_hex";
            enqueue(out.dump());
            return true;
        }
        if (bytes.size() > 4096) {
            out["ok"]     = false;
            out["reason"] = "too_long";
            enqueue(out.dump());
            return true;
        }
        if (!writeMemory(addr, bytes.data(), bytes.size())) {
            out["ok"]     = false;
            out["reason"] = "bad_addr";
        } else {
            out["ok"]      = true;
            out["addr"]    = addr;
            out["written"] = bytes.size();
            // Single-line audit trail: strong capabilities must leave a trace.
            bootLog(
                "mem_write addr=" + std::to_string(addr) + " len=" + std::to_string(bytes.size())
                + " hex=" + hexEncode(bytes.data(), bytes.size() > 64 ? 64 : bytes.size())
            );
        }
        enqueue(out.dump());
        return true;
    }
    if (cmd == "probe") {
        nlohmann::json out;
        out["event"] = "probe";
        std::uint64_t addr = 0;
        if (!j.contains("addr") || !parseAddress(j["addr"], addr)) {
            out["ok"]     = false;
            out["reason"] = "bad_addr";
            enqueue(out.dump());
            return true;
        }
        std::string const kind = j.value("kind", std::string{"raw"});
        std::size_t const len  = std::clamp<std::size_t>(j.value("len", 16), 1, 64);
        enqueue(probeMemory(addr, kind, len).dump());
        return true;
    }
    if (cmd == "watch_set") {
        if (!hooksAllowed()) {
            enqueue(R"({"event":"error","reason":"version_mismatch"})");
            return true;
        }
        if (!mAllowWatch.load(std::memory_order_relaxed)) {
            enqueue(R"({"event":"error","reason":"watch_disabled","hint":"set allow_watch=true"})");
            return true;
        }
        nlohmann::json out;
        out["event"] = "watch_set";
        std::uint64_t addr = 0;
        if (!j.contains("addr") || !parseAddress(j["addr"], addr)) {
            out["ok"]     = false;
            out["reason"] = "bad_addr";
            enqueue(out.dump());
            return true;
        }
        std::uint32_t size = 1;
        if (j.contains("size") && !parseSize(j["size"], size)) {
            out["ok"]     = false;
            out["reason"] = "bad_size";
            enqueue(out.dump());
            return true;
        }
        std::string const mode = j.value("mode", std::string{"w"});
        std::string       thread = "tick";
        if (j.contains("thread")) {
            if (j["thread"].is_string()) {
                thread = j["thread"].get<std::string>();
            } else if (j["thread"].is_number_integer()) {
                thread = std::to_string(j["thread"].get<long long>());
            }
        }
        std::string const tag = j.value("tag", std::string{});
        std::uint32_t     depth = 8;
        if (j.contains("depth")) {
            if (!j["depth"].is_number_integer()) {
                out["ok"]     = false;
                out["reason"] = "bad_depth";
                enqueue(out.dump());
                return true;
            }
            depth = j["depth"].get<std::uint32_t>();
        }
        enqueue(watchInstall(addr, size, mode == "rw", thread, tag, depth).dump());
        return true;
    }
    if (cmd == "watch_clear") {
        if (j.value("all", false)) {
            bool const cleared = watchRemoveAll();
            enqueue(
                nlohmann::json{
                    {"event", "watch_cleared"},
                    {"all",   true           },
                    {"ok",    cleared        },
            }
                    .dump()
            );
            return true;
        }
        int const id = j.value("id", -1);
        enqueue(watchRemove(id).dump());
        return true;
    }
    if (cmd == "watch_list") {
        enqueue(watchListJson().dump());
        return true;
    }
    if (cmd == "watch_drain") {
        std::uint64_t const since =
            j.contains("since") ? j.value("since", std::uint64_t{0}) : std::uint64_t{0};
        std::size_t const limit =
            std::clamp<std::size_t>(j.value("limit", 4096), 1, 65536);
        enqueue(watchDrainJson(since, limit).dump());
        return true;
    }
    if (cmd == "hook_install") {
        if (!hooksAllowed()) {
            enqueue(R"({"event":"error","reason":"version_mismatch"})");
            return true;
        }
        bool const  useVa  = j.contains("va") && !j["va"].is_null();
        std::string symbol = j.value("symbol", std::string{});
        std::uint64_t va   = 0;
        if (useVa && !parseAddress(j["va"], va)) {
            enqueue(R"({"event":"error","reason":"bad_va"})");
            return true;
        }
        if (!useVa && symbol.empty()) {
            enqueue(R"({"event":"error","reason":"missing_symbol"})");
            return true;
        }
        std::uint32_t const sample =
            std::clamp(j.value("sample", 1), 1, 1000000);
        bool const dryRun = j.value("dry_run", false);
        std::string const tag = j.value("tag", std::string{});
        HookMemSpec mem;
        if (j.contains("mem_size")) {
            mem.arg    = j.value("mem_arg", 0u);
            mem.offset = j.value("mem_offset", 0u);
            mem.size   = j.value("mem_size", 0u);
            if (mem.arg > 3 || mem.size % 4 != 0 || mem.size > 64 || mem.offset > 4096) {
                enqueue(
                    R"({"event":"error","reason":"bad_mem_spec","hint":"mem_arg 0..3, mem_size 4..64 step 4, mem_offset <= 4096"})"
                );
                return true;
            }
        }
        enqueue(
            hookInstall(
                symbol,
                va,
                useVa,
                tag,
                sample,
                dryRun,
                mAllowHookVa.load(std::memory_order_relaxed),
                mWorld.inWorld(),
                j.value("inject_fail", false)
                    && mAllowHookVa.load(std::memory_order_relaxed),
                mem
            )
                .dump()
        );
        return true;
    }
    if (cmd == "hook_rm") {
        enqueue(hookRemove(j.value("id", -1)).dump());
        return true;
    }
    if (cmd == "hook_list") {
        enqueue(hookListJson().dump());
        return true;
    }
    if (cmd == "hook_drain") {
        std::uint64_t const since =
            j.contains("since") ? j.value("since", std::uint64_t{0}) : std::uint64_t{0};
        std::size_t const limit = std::clamp<std::size_t>(j.value("limit", 4096), 1, 65536);
        enqueue(hookDrainJson(since, limit).dump());
        return true;
    }
    if (cmd == "hook_call") {
        std::string const symbol = j.value("symbol", std::string{});
        float const       scale  = j.value("scale", 1.0f);
        std::string       error;
        if (!hookRequestCall(symbol, scale, error)) {
            enqueue(
                nlohmann::json{
                    {"event",  "error"      },
                    {"reason", error        },
            }
                    .dump()
            );
        }
        return true;
    }
    return false;
}

} // namespace probe
