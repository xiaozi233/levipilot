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
            io.writefile(rakpeer, rp .. crlf .. '#include "mc/deps/raknet/ShadowBanList.h"' .. crlf)
            print("APPENDED " .. rakpeer)
        end
        local cc = "D:/Tools/LLVM22/bin/clang-cl.exe"
        if package:config("target_type") == "server" then
            import("package.tools.xmake").install(package, {"--cc=" .. cc, "--cxx=" .. cc})
        else
            import("package.tools.xmake").install(package, {"--target_type=client", "--cc=" .. cc, "--cxx=" .. cc})
        end
    end)
