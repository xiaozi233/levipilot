package("levilamina")
    add_urls("https://github.com/LiteLDev/LeviLamina.git")
    add_versionfiles("versions/versions.txt")

    add_defines("ENTT_PACKED_PAGE=128", "ENTT_SPARSE_PAGE=4096", "ENTT_NO_MIXIN")

    add_configs("target_type", {default = "server", values = {"server", "client"}})

    on_load(function(package)
        print("USING LOCAL LEVILAMINA PACKAGE")
        import("core.base.semver")
        local version = package:version_str()
        local sem = semver.try_parse(version)
        if sem and sem:le("0.12.4") then
            version = "old"
        end
        version = string.gsub(version, "%.", "_")
        try { function()
            import("versions." .. version).load(package)
        end, catch { function(e)
            cprint(
                "${bright yellow}warning: ${clear}Unknown version: ${bright cyan}"
                .. version .. "${clear}, will use main branch dependencies."
            )
            import("versions.main").load(package)
        end } }
        if package:config("target_type") == "server" then
            package:add("defines", "LL_PLAT_S")
        else
            package:add("defines", "LL_PLAT_C")
        end
    end)

    on_install(function(package)
        print("LOCAL_INSTALL_CWD=" .. os.curdir())
        local crlf = string.char(13, 10)
        local function add_after(file, anchor, includes)
            local text = io.readfile(file)
            if text and not text:find(includes[1], 1, true) then
                local repl = anchor
                for _, inc in ipairs(includes) do
                    repl = repl .. crlf .. '#include "' .. inc .. '"'
                end
                io.replace(file, anchor, repl, {plain = true})
                print("PATCHED " .. file)
            end
        end
        add_after("src/mc/server/commands/MinecraftCommands.h",
            '#include "mc/server/commands/CurrentCmdVersion.h"', {
                "mc/server/commands/Command.h",
                "mc/server/commands/CommandOutputSender.h",
                "mc/server/commands/CommandRegistry.h",
                "mc/server/commands/DeferredCommandBase.h",
            })
        add_after("src/mc/world/level/BedrockSpawner.h",
            '#include "mc/world/level/chunk/SpawnPlacements.h"', {
                "mc/world/actor/ActorSpawnRuleGroup.h",
                "mc/world/actor/registry/SpawnGroupRegistry.h",
                "mc/world/level/chunk/EntityTypeCache.h",
            })
        add_after("src/mc/world/level/NaturalSpawner.h",
            '#include "mc/world/level/chunk/SpawnPlacements.h"', {
                "mc/world/actor/ActorSpawnRuleGroup.h",
                "mc/world/actor/registry/SpawnGroupRegistry.h",
                "mc/world/level/chunk/EntityTypeCache.h",
            })
        add_after("src/mc/world/actor/registry/SpawnGroupRegistry.h",
            '#include "mc/_HeaderOutputPredefine.h"', {
                "mc/world/actor/SpawnGroupData.h",
            })
        add_after("src/mc/world/level/chunk/EntityTypeCache.h",
            '#include "mc/_HeaderOutputPredefine.h"', {
                "mc/world/level/chunk/EntityType.h",
            })
        local rakpeer = "src/mc/deps/raknet/RakPeer.h"
        local rp = io.readfile(rakpeer)
        if rp and not rp:find("mc/deps/raknet/ShadowBanList.h", 1, true) then
            rp = rp .. crlf .. '#include "mc/deps/raknet/ShadowBanList.h"' .. crlf
            print("APPENDED " .. rakpeer)
        end
        -- RakPeer's inline defaulted destructor needs a complete ShadowBanList,
        -- but ShadowBanList.h includes RakPeer.h for the nested
        -- RemoteSystemStruct, so no include order satisfies both. Move the
        -- destructor out of line; a generated TU defines it after both types
        -- are complete. Without this, TUs compiled without the PCH fail on
        -- default_delete's incomplete-type static_assert.
        local dtorMarker = "virtual ~RakPeer() /*override*/ = default;"
        local dtorSource = "src/mc/deps/raknet/RakPeerDtorPatch.cpp"
        local dtorPatched = false
        if rp then
            local pos = rp:find(dtorMarker, 1, true)
            if pos then
                rp = rp:sub(1, pos - 1) .. "virtual ~RakPeer() /*override*/;" .. rp:sub(pos + #dtorMarker)
                dtorPatched = true
                print("PATCHED ~RakPeer out of line")
            end
            io.writefile(rakpeer, rp)
        end
        if dtorPatched and not os.isfile(dtorSource) then
            io.writefile(dtorSource,
                '#include "mc/deps/raknet/RakPeer.h"' .. crlf
                .. '#include "mc/deps/raknet/ShadowBanList.h"' .. crlf
                .. "namespace RakNet { RakPeer::~RakPeer() = default; }" .. crlf)
            print("ADDED " .. dtorSource)
        end
        -- optional_ref::and_then returns std::remove_cvref_t<Ret>{} in the
        -- empty branch. Some clang/SDK combinations reject void{} there
        -- ("illegal initializer type ... aka 'void'"), so guard that case.
        local optionalRef = "src/mc/deps/core/utility/optional_ref.h"
        local orf = io.readfile(optionalRef)
        if orf then
            local marker = "            return std::remove_cvref_t<Ret>{};"
            local pos = orf:find(marker, 1, true)
            if pos then
                orf = orf:sub(1, pos - 1)
                    .. "            if constexpr (std::is_void_v<Ret>) { return; } else { return std::remove_cvref_t<Ret>{}; }"
                    .. orf:sub(pos + #marker)
                io.writefile(optionalRef, orf)
                print("PATCHED optional_ref::and_then void return")
            end
        end
        -- The caller selects the toolchain; never bake a developer's drive
        -- layout into an SDK package recipe.
        import("lib.detect.find_tool")
        local llvm_root = os.getenv("LEVIPILOT_LLVM_ROOT")
        local cc
        if llvm_root then
            cc = path.join(llvm_root, "bin", "clang-cl.exe")
            assert(os.isfile(cc), "LEVIPILOT_LLVM_ROOT does not contain bin/clang-cl.exe")
        else
            local tool = find_tool("clang-cl")
            assert(tool, "clang-cl not found; set LEVIPILOT_LLVM_ROOT to an LLVM 22 installation")
            cc = tool.program
        end
        if package:config("target_type") == "server" then
            import("package.tools.xmake").install(package, {"--cc=" .. cc, "--cxx=" .. cc}, {jobs = 4})
        else
            import("package.tools.xmake").install(package, {"--target_type=client", "--cc=" .. cc, "--cxx=" .. cc}, {jobs = 4})
        end
    end)
