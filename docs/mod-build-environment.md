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

```bash
cd mod/probe

export PATH="/d/Tools/LLVM22/bin:/d/Tools/xmake:$PATH"
export XMAKE_GLOBALDIR='D:\Tools\xmake-home'
export XMAKE_PKG_INSTALLDIR='D:\Tools\xmake-packages'

xmake f -c -y -p windows -a x64 -m release --target_type=client
xmake -y
```

产物在 `bin/probe/`：`probe.dll`、`probe.pdb`、`manifest.json`
（`"platform": "client"`）。DLL 已确认导入 `LeviLamina.dll`。

## 必须保留的两个修正

### 1. 强制 xmake 使用 LLVM22，而不是 VS 自带的 clang 19

xmake 的 `clang-cl` 工具链会通过 VS 环境优先找到
`D:\Tools\VSBuildTools\VC\Tools\Llvm\x64\bin\clang-cl.exe`（clang 19），
而 LL 26.51.7 的源码需要 clang 22 才能稳定编译。

已修改（原文件备份为 `check.lua.orig`）：

```
D:\Tools\xmake\toolchains\clang-cl\check.lua
```

改动内容：把 `D:/Tools/LLVM22/bin` 放到搜索路径最前，并过滤掉
`VSBuildTools\...\Llvm` 路径。

> 不要恢复，否则构建会退回 clang 19 并失败。

### 2. LL 26.51.7 源码的完整类型/循环包含补丁

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
