# Mod 构建环境（本机参考）

`mod/probe` 在下面这套工具链上验证通过。路径是本机布局，换机器时只需要保证版本一致。

| 组件 | 版本 | 本机路径 |
| --- | --- | --- |
| Visual Studio Build Tools | 17.14.42 | `D:\Tools\VSBuildTools` |
| MSVC | 14.44.35207 | `D:\Tools\VSBuildTools\VC\Tools\MSVC\14.44.35207` |
| Windows SDK | 10.0.26100 | `C:\Program Files (x86)\Windows Kits\10` |
| LLVM / clang-cl | **22.1.8** | `D:\Tools\LLVM22` |
| xmake | 3.1.2 | `D:\Tools\xmake` |
| xmake 全局目录 | — | `D:\Tools\xmake-home` |
| xmake 包安装目录 | — | `D:\Tools\xmake-packages` |

> Windows SDK 被 VS 安装器固定在 C 盘；其余主体都在 D 盘。

## 构建

```powershell
$env:PATH = 'D:\Tools\xmake;' + $env:PATH
$env:XMAKE_GLOBALDIR = 'D:\Tools\xmake-home'
$env:XMAKE_PKG_INSTALLDIR = 'D:\Tools\xmake-packages'
./.github/scripts/build-mod.ps1 -LlvmRoot D:/Tools/LLVM22
```

脚本验证 LLVM 主版本为 22，以绝对路径将 `clang-cl.exe` 传给 xmake 的
`--cc` / `--cxx`，并设置 `LEVIPILOT_LLVM_ROOT`，让 SDK 包使用同一编译器。
构建使用 `-j4`。已用未经修改的 xmake 3.1.2 便携版本机验证；不再需要修改
全局 `toolchains/clang-cl/check.lua`，也不依赖开发者固定的磁盘布局。

产物在 `mod/probe/bin/probe/`：`probe.dll`、`probe.pdb`、`manifest.json`
（`"platform": "client"`）。构建日志写入 `out/ci-mod-build.log`。

## CI 完整构建

`tests.yml` 的 `mod-build` 在 Windows 2022 runner 上安装固定版本 LLVM 22.1.8
与 xmake 3.1.2，下载前后校验发布文件的 SHA256，缓存 SDK 依赖，再调用同一脚本
编译和链接完整客户端 mod。成功产物与构建日志作为 Actions artifacts 保留 7 天。
单测在独立 job 运行。CI 不启动 Minecraft，世界切换与输入行为由本机 live selftest 验证。

工具安装脚本见 `.github/scripts/setup-native.ps1`。如需升级工具链，版本、下载地址和
校验值应一起更新，并重新执行一次无缓存构建。

## SDK 头文件修正

### LL 26.51.7 源码的完整类型/循环包含补丁

LL 的 auto-generated 头文件大量使用“前向声明 + `std::unique_ptr`/`std::vector`”，
在当前 MSVC STL 14.44 下默认析构函数会要求完整类型。补丁放在本地包仓库：

```
mod/probe/local-repo/packages/l/levilamina/xmake.lua
```

它通过 `on_install` 在包源码里补这些 include：

- `MinecraftCommands.h`：`Command`、`CommandOutputSender`、`CommandRegistry`、`DeferredCommandBase`
- `BedrockSpawner.h` / `NaturalSpawner.h`：`ActorSpawnRuleGroup`、`SpawnGroupRegistry`、`EntityTypeCache`
- `SpawnGroupRegistry.h`：`SpawnGroupData`
- `EntityTypeCache.h`：`EntityType`
- `RakPeer.h`：在文件末尾追加 `ShadowBanList.h`（解决 `RakPeer.h` ↔ `ShadowBanList.h` 循环包含）

`mod/probe/xmake.lua` 里已加：

```lua
add_repositories("local-repo local-repo")
```

这个本地仓库会覆盖上游 `levimc-repo` 里的同名 `levilamina` 包，所以补丁是可复现的；
上游包源码被 xmake `git reset` 也不会丢。
