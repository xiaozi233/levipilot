# probe — LeviPilot 客户端 mod（xmake 工程目录）

本目录是 `probe.dll` 的 xmake 构建根（`xmake.lua`、`src/mod/`、`local-repo/`）。
构建说明、管道 API、`harness.json` 配置与使用限制统一维护在上一级，避免重复：

| 主题 | 文档 |
| --- | --- |
| 构建、安装、API 命令表、配置、实测基线与限制 | [`../README.md`](../README.md) |
| 本机 / CI 的 LLVM 22 + xmake 3.1.2 工具链与 SDK 头文件补丁 | [`../../docs/mod-build-environment.md`](../../docs/mod-build-environment.md) |
| 各子系统实现细节与实测数字 | [`../../docs/mod-internals.md`](../../docs/mod-internals.md) |
| 换游戏版本的移植流程 | [`../../docs/porting.md`](../../docs/porting.md) |

快速构建（务必带 `-j4` 限制并发）：

```bash
export PATH="/d/Tools/LLVM22/bin:/d/Tools/xmake:$PATH"
export XMAKE_GLOBALDIR='D:\Tools\xmake-home'
export XMAKE_PKG_INSTALLDIR='D:\Tools\xmake-packages'
xmake -j4 -y
```

产物在 `bin/probe/`：`probe.dll`、`probe.pdb`、`manifest.json`（client 平台）。
