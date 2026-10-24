# 移植到新的游戏版本

本文说明换一个 Minecraft Bedrock / LeviLamina 版本时怎样**只改数据、不改逻辑**。
先按下面的三类判断成本，再走「快速流程」；每一步都有可验证的输出，不要跳步。

本仓库把版本相关的内容分成三类，移植成本依次递增：

| 类别 | 例子 | 移植方式 |
| --- | --- | --- |
| 符号（函数地址） | `LocalPlayer::$normalTick`、`LoopbackPacketSender::$sendToClient` | 跟着 LeviLamina 头文件走，重新编译即自动解析 |
| 数据（签名/偏移/组件 id） | 24 字节锚点签名、`BlockLegacy.NameHash`、`StateVector` 组件 id | 改 `docs/offsets/*.json`，重新生成头文件，重编 |
| 代码（API 形状） | LL 删改符号、虚函数签名变化、包结构变化 | 必须改 C++ |

三类里只有第三类需要真正读代码；前两类已经在数据层解决。

## 快速流程

### 1. 升级 LeviLamina 依赖并编译

```bash
# xmake.lua: add_requires("levilamina <new-version>")
cd mod/probe && xmake -j4 -y
```

符号 hook 在编译期绑定 LL 的导入符号，只要新版本还导出同名函数就能直接工作。
链接失败说明符号改名/移除了，属于第三类，需要改代码。

### 2. 启动游戏，先看能力表

```bash
py -3.11 -m driver.harness.cli status
```

关注三个字段：

```json
{
  "build_id": "1.27.0.1",
  "version_ok": false,
  "hooks_allowed": false,
  "capabilities": {"hooks": false, "offsets": false, "...": false},
  "profile": {"profile_found": false, "match": "none"}
}
```

`profile_found=false` 是正常的：新版本还没有 profile（版本数据文件）。这时管道、`status`、`diagnose`、
`profile_dump`、`mem_read`/`probe` 仍然可用。

### 3. 打开兼容模式试跑钩子

在 `<mods>/probe/harness.json` 里加：

```json
{"allow_version_mismatch": true}
```

重启后 `hooks_allowed=true`，tick 钩子、watch、hook、输入注入、封包 tap 都可以试。
这是**显式的不安全开关**（没有 profile 校验，偏移可能读到错误字段），只在移植期间打开，
拿到 profile 后关掉。

### 4. 从实机导出 profile

```bash
py -3.11 -m driver.harness.cli profile-dump --write docs/offsets/1.27.0.1.json
```

导出的内容：

- `anchors.<name>.expected_hex`：当前 4 个锚点真实的前 24 字节（已经跟过导入桩）；
- `structs`：当前生效的结构偏移（如果还是空表，说明没有 profile，需要手工补）；
- `components`：当前组件 id/size；
- 顶层的 `runtime`、`version`、`build_id`、`version_prefix`。

检查并修正两处：

1. `version` 改成游戏显示版本（例如 `1.27.0.01`），`version_prefix` 保持
   `major.minor.build`；
2. 没有 profile 时 `structs` 是空的。用 `blocks` / `probe block` / `probe legacy`
   在实机上重新确认 `Block`、`BlockLegacy` 字段偏移，填进 JSON。字段填 `null` 表示
   “这个版本没有该字段”，`probe` 会回 `no_offset` 而不是误读。

### 5. 生成头文件并重编

```bash
py -3.11 driver/gen_profiles.py      # 生成 mod/probe/src/mod/GeneratedProfiles.h
cd mod/probe && xmake -j4 -y
```

`GeneratedProfiles.h` 是提交进仓库的生成物；`tests/test_profiles.py` 会检查它和 JSON 是否
同步，忘记重新生成会让 CI 失败。

### 6. 关掉兼容模式，跑验收

把 `allow_version_mismatch` 删掉或置 `false`，重启，然后：

```bash
py -3.11 driver/selftest.py
py -3.11 -m unittest discover -s tests
```

`status.version_ok` 必须为 `true`，`profile.match` 为 `exact`（或小版本修订时的 `prefix`）。

## 不重编的临时路径

如果只是想先跑起来：

```json
// <mods>/probe/offsets.json
{
  "anchors": {"LocalPlayer::normalTick": {"expected_hex": "..."}},
  "structs": {"BlockLegacy": {"NameHash": "0xE4"}},
  "components": {"StateVector": {"id": 459100728, "size": 36}}
}
```

配合 `allow_version_mismatch=true` 使用。运行时覆盖只影响当前进程，且
`status.profile.offset_source` 会显示 `offsets.json`，方便确认生效了没有。

## 排查命令

| 命令 | 用途 |
| --- | --- |
| `status` / `diagnose` | profile 选择结果、能力表、锚点匹配、结构表 |
| `profile_probe --build-id 1.27.0.1` | 不启动新版本也能验证 profile 会被哪个版本选中 |
| `profile_dump` | 导出当前实机的锚点字节/偏移/组件 |
| `signature_check --name ... --expected-hex ...` | 验证签名比较（负路径应返回 `match=false`） |
| `symbol_resolve --name ...` | 跟导入桩、给出 module+rva、判断页可执行 |

## 什么情况下必须改代码

- LL 不再导出某个 `$` 符号（链接报 LNK2019）→ 换锚点或改用 `SymbolView`/签名。
- 虚函数签名、参数个数变化（例如 `Packet::sendTo` 重载变了）→ 改 typed hook。
- 包 payload 布局变化 → 改 `driver/harness/packets.py` 的解码器；
  `PACKET_NAMES` 可以直接从新版本的 `MinecraftPacketIds.h` 重新生成。
- 新增能力需要新的锚点 → 在 `Resolve.cpp` 的固定锚点集合里加一项，并在 JSON profile
  里补 `expected_hex`。

## 不该做的事

- 不要在 `.cpp` 里写死版本号、绝对 VA、结构偏移或组件 id；这些都属于 profile。
- 不要把 `allow_version_mismatch` 留在日常配置里；它是移植开关，不是兼容层。
- 不要跳过 `driver/gen_profiles.py`；手改 `GeneratedProfiles.h` 会被 CI 的
  `tests/test_profiles.py` 抓到。

## 另见

- 版本 profile 的实际内容与生成规则：[`../docs/offsets/`](offsets/) 与
  [`../driver/gen_profiles.py`](../driver/gen_profiles.py)（生成 `GeneratedProfiles.h`）。
- 签名/锚点的运行机制：[`mod-internals.md`](mod-internals.md) 的「版本指纹与锚点」。
- 构建环境与工具链补丁：[`mod-build-environment.md`](mod-build-environment.md)。
