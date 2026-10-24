# dmf-mpk v1.4.1 逆向分析与 harness 接口

> 目标：`%APPDATA%\levilauncher.exe\versions\1.26.51.01\mods\dmf-mpk\dmf.dll`
> 样本 SHA-256：`f63ba947387a9db2f0342c686355241d9dc606feff0b94a47dfcb84a28d53a97`
> 分析库：用户提供的 `dmf.dll.i64`（IDA Pro 9.4，3539 个函数，1332 条字符串）
> 平台：Windows x64，LeviLamina client mod，游戏 1.26.51.01

---

## 0. 结论（TL;DR）

1. `dmf-mpk` 是闭源客户端 mod，GitHub 仓库只有 README、LICENSE 和 Release 里的二进制，没有源码。它的核心不是网络协议，而是**直接调用 Bedrock 客户端运行时**：注入输入队列、改视角、读 Actor/BlockSource、画 HUD。
2. 它的 macro 文件格式已经完整还原：**顶层 JSON 数组，每个元素是 9 或 11 个字符串**，顺序为
   `W, A, S, D, Sprint, Sneak, Jump, RMB, LMB, Yaw, Pitch`。
   `Yaw/Pitch` 是**每 tick 的旋转增量（度）**，不是绝对朝向。
3. macro 播放挂在 `LocalPlayer::$normalTick` 的入口钩子上（先注入，再调用原函数），一 tick 消费一行；
   按键只在状态变化时通过 `InputEventQueue::enqueueButton` 注入，旋转通过
   `LocalPlayer::_applyTurnDelta` 应用。
   **只有自己托管的世界才能播放**（客户端检查 `LocalPlayer+0xCAA` 的 hosted 标志）。
4. macro 作为“逐 tick 输入序列”很有用；但它不提供运行时查询接口，也不适合直接给 LLM 读写。更稳的做法是**不 patch `dmf.dll`**，而是做一个伴生 mod：
   - 复用同样的 tick/输入/旋转注入方式；
   - 增加 named pipe JSON 接口，供 Python / LLM 查询玩家状态、控制移动/视角/跳跃/潜行/疾跑；
   - 直接读写 dmf 兼容的 macro JSON。
5. 本仓库已经把这个伴生 mod 扩展完成：`mod/probe`，协议 `\\.\pipe\mcbe-harness`，客户端 `driver/transport_mod.py`。dmf 的 macro 文件可以原样加载，也可以录制后导出成同样格式。

---

## 1. 样本与静态结构

### 1.1 文件

| 项 | 值 |
|---|---|
| 文件 | `dmf.dll` |
| 大小 | 1,144,320 bytes |
| SHA-256 | `f63ba947387a9db2f0342c686355241d9dc606feff0b94a47dfcb84a28d53a97` |
| 模块类型 | x64 PE DLL，client 平台 |
| 调试路径 | `C:\Users\hiroto\Downloads\Custom-Version-Text-main\...\dmf.pdb` |
| 导出 | `ll_mod_load`, `ll_memory_operator_overrided`, `DllEntryPoint` |
| 依赖 | `LeviLamina.dll`（52 个符号）+ `bedrock_runtime.dll`（94 个符号） |
| C++ 库 | MSVC 14.x STL、nlohmann/json 3.12.0 |

`LeviLamina.dll` 依赖说明它是标准 LL mod；`bedrock_runtime` 导入表给出了它实际使用的游戏函数，是还原功能边界的最快入口。

### 1.2 IDA 导出

`reverse/ida_export.py` 对 `.i64` 做了全量导出：

- 段：`HEADER`, `.text`（0x1000–0xCD000）, `.idata`, `.rdata`, `.data`, `.pdata`, `.reloc`
- 函数：3539
- 字符串：1332
- 反编译：`reverse/ida_out/pseudocode.c`（4.5 MB）

关键数据文件：

| 文件 | 用途 |
|---|---|
| `reverse/ida_out/functions.tsv` | 全部函数地址/大小/名字 |
| `reverse/ida_out/strings.jsonl` | 字符串 + 交叉引用 |
| `reverse/ida_out/imports.tsv` | 导入符号 |
| `reverse/ida_out/exports.tsv` | 导出符号 |
| `reverse/ida_out/pseudocode.c` | Hex-Rays 反编译全文 |
| `reverse/show_func.py` | 按地址抽取反编译函数 |

---

## 2. 模块架构

### 2.1 入口与事件

`ll_mod_load` → ModEntry 构造 → `onLoad/onEnable`。enable 逻辑集中在 `sub_180026750` 一带，注册了这些事件：

| 事件 | 用途 |
|---|---|
| `LocalPlayer::$normalTick`（函数 hook） | 每 tick 驱动 macro、HUD、practice、position checker；注入发生在原 tick 之前 |
| `ClientStartJoinLevelEvent` | 进入世界时重置状态，读取 `worldName/serverName` |
| `ClientCommandRegisterEvent` | 注册 `/mpk ...` 命令树 |
| `KeyInputEvent` | 键位、macro run、zoom、toggle sprint、practice |
| `MouseInputEvent` | 鼠标按键（RMB/LMB）、视角/点击相关功能 |

### 2.2 关键函数表

地址来自 IDA 基址 `0x180000000`。

| 地址 | 角色 | 证据 |
|---|---|---|
| `0x180026750` | 主 tick / macro 播放 / HUD / practice | 引用 `Macro stopped:`、`qword_18010F370` 输入队列、`_applyTurnDelta` |
| `0x18002BBF0` | 配置加载/保存/校验 | 引用全部 `config.json` 键和 `Config must be a JSON object` |
| `0x18002AC00` | 键位注册（zoom/togglesprint/setlb/macro/practice） | 引用 `mpk_zoom` 等 |
| `0x180077550` | `/mpk` 命令注册 | 引用 `macro`, `config`, `hudeditor`, `sensitivity` 等 |
| `0x180070740` | macro 保存 | `[MacroManager] ERROR: failed to save macro` |
| `0x1800713C0` | macro 行序列化 | `%.9g`、`Macro angle must be finite` |
| `0x180071B40` | macro JSON 加载 | `not a top-level array`、`tick must be an array` |
| `0x180072F20` | macro 单行解析 | `9 or 11 strings`、`Invalid macro button value` |
| `0x18007AE30` | `/mpk macro run` 入口 | `A macro is already playing` |
| `0x18004CB50` | 快捷键触发 macro | `No macro loaded` |
| `0x18004CF90` | 开始播放 | 锁、索引、世界检查 |
| `0x18004F100` | Input Checker 保存 | `[InputChecker] ERROR` |
| `0x180051D20` | Input Checker 解析 | `[InputChecker] ERROR: Failed to parse session` |
| `0x18008EC10` | Macro Editor / Input Checker UI | `MACRO EDITOR`, `INPUT CHECKER` |
| `0x18009F750` / `0x1800A3430` | Input Checker 表达式/指令引擎 | `rotation`, `sneak_delay`, `outvx` 等 |
| `0x18008A580` | Practice 停止 | `Practice mode stopped` |
| `0x1800B56C0` | UiScreen 推屏/关闭 | `[MPK][UiScreen] ...` |

### 2.3 配置

实际文件：`mods/dmf-mpk/config.json`。键与语义：

| 键 | 类型 | 语义 |
|---|---|---|
| `prefix` | string | 聊天前缀，默认 `<MPK>` |
| `labelColor` / `valueColor` | string | HUD 颜色，如 `§b` / `§f` |
| `df` | int | HUD 数值小数位 |
| `checkTick` | int | position checker 采样间隔 |
| `toggleSprint` | bool | 切换式疾跑 |
| `smoothMacro` | bool | macro 旋转平滑 |
| `frameBasedRotation` | bool | 旋转按帧而不是按 tick |
| `fullBright` | bool | 全亮 |
| `hidePlayers` | bool | 隐藏玩家 |
| `zoomEnable` / `zoomCinematic` / `zoomFactor` | bool/bool/float | 缩放 |
| `fixLadderCamera` | bool | 梯子相机修正 |
| `keystroke` | bool | 按键显示 |
| `lastMacroName` | string | 上次加载的 macro 名 |
| `labelPositions` | object | HUD 标签位置 |
| `hiddenLabels` / `disabledLabels` | array | HUD 标签过滤 |
| `keyBindings` | object | 键码数组，例如 `{"mpk_zoom":[67]}` |
| `sendOffsetToChat` | object | 把 offset/PB 发到聊天 |

配置加载失败时保留旧值并打印 `[MPK] Config load failed; previous settings retained`。

---

## 3. macro 格式（完整）

### 3.1 磁盘格式

路径：`mods/dmf-mpk/macros/<name>.json`。

顶层必须是数组；每个元素是一行 tick：

```json
[
  ["false","true","false","false","false","false","false","false","false","0","0"],
  ["false","false","false","true","false","true","false","true","true","0","0"]
]
```

字段顺序：

| 索引 | 字段 | 允许值 | 说明 |
|---|---|---|---|
| 0 | W | `"true"`/`"false"`（也接受 `"1"`/`"0"`） | 前进 |
| 1 | A | 同上 | 左移 |
| 2 | S | 同上 | 后退 |
| 3 | D | 同上 | 右移 |
| 4 | Sprint | 同上 | 疾跑键 |
| 5 | Sneak | 同上 | 潜行键 |
| 6 | Jump | 同上 | 跳跃键 |
| 7 | RMB | 同上 | 右键/使用 |
| 8 | LMB | 同上 | 左键/攻击 |
| 9 | Yaw | 浮点字符串 | 本 tick 的偏航增量（度） |
| 10 | Pitch | 浮点字符串 | 本 tick 的俯仰增量（度） |

要点：

- **9 个字符串**也合法，等价于 `Yaw=Pitch=0`。
- Yaw/Pitch 用 `%.9g` 写回。
- 解析时要求 tick 是数组，元素是字符串；按钮值支持 `true/false/1/0`。
- 角度必须是有限数，否则 `Macro angle must be finite`。
- `Tick` 不是字段，行号就是 tick。
- `Strafe` 是编辑器底部的模式开关，不是列。

### 3.2 播放语义

`sub_180026750` 是 `LocalPlayer::$normalTick` 的 hook 回调（`a1` 是 LocalPlayer），在末尾调用
原函数 trampoline。它每 tick：

1. 检查是否 hosted world：`*(bool*)(localPlayer + 0xCAA)`。不满足时停止并提示
   `Macros can only be played in a world you are hosting.`
2. 从全局 macro vector 取下一条（结构 20 字节：9 个 bool + 3 字节 padding + 2 个 float）。
3. 对 9 个按钮做边沿检测，只在 `old != new` 时调用
   `InputEventQueue::enqueueButton(queue, actionId, state, ...)`。
4. 旋转：
   - 普通模式：把当前行的 `(yaw,pitch)` 作为增量；
   - 平滑/按帧模式：`smoothMacro` / `frameBasedRotation` 会走 `sub_1800287C0` 的插值状态机；
   - 最终调用 `LocalPlayer::_applyTurnDelta(Vec2{pitch, yaw})`。
5. 播放结束/停止时，对仍按下的按钮补发 `state=0`，释放输入。

动作 ID（按宏列顺序）是游戏内部按钮哈希，逆向得到的常量：

| 列 | action id |
|---|---|
| W | `3205050568` (`0xBF0930C8`) |
| A | `3528728616` (`0xD2542028`) |
| S | `375632893` (`0x1663B3FD`) |
| D | `3949230141` (`0xEB64783D`) |
| Sprint | `2427380341` (`0x90AEE275`) |
| Sneak | `4009177245` (`0xEEF7309D`) |
| Jump | `2406325663` (`0x8F6D9D9F`) |
| RMB | `4073882376` (`0xF2D28308`) |
| LMB | `2320499196` (`0x8A5001FC`) |

这些 ID 只用于 `InputEventQueue::enqueueButton`；不是键码。

### 3.3 录制/编辑

- 编辑器列：`Tick, W, A, S, D, Sprint, Sneak, Jump, RMB, LMB, Yaw, Pitch`。
- 底部：`Add row`, `Duplicate`, `Delete`, `Undo`, `Redo`, `Strafe`, `Mothball!`。
- `Load / Create` 从 `macros/` 读取；`lastMacroName` 记上次名字。
- 保存时通过 `sub_1800713C0` 逐行序列化。

---

## 4. 其他功能模块

### 4.1 命令树

前缀 `/mpk`：

| 命令 | 作用 |
|---|---|
| `config` | 打开配置 GUI |
| `gui` | HUD 标签开关 |
| `hudeditor` | HUD 位置编辑 |
| `is` | Input Checker 编辑器 |
| `macro [run?]` | 打开 macro 编辑器 / 播放 |
| `setlb [target\|zneo\|x\|z\|hit]` | 设置落地方块 |
| `setmm ...` | 设置 momentum 方块 |
| `setbox <start> <end>` | 设置落地盒 |
| `lb <land\|zneo\|hit> [x\|z\|both]` | 修改落地类型/轴 |
| `clearlb` / `clearpb` | 清除落地块 / PB |
| `df 0..10` | 小数精度 |
| `color1/color2 <color>` | HUD 颜色 |
| `togglesprint` | 切换疾跑模式 |
| `os <rotation>` / `ss <sensitivity>` | 灵敏度/旋转优化 |
| `reload` / `resetdefault` | 重载/重置配置 |

### 4.2 HUD 与统计

字符串里能看到大量 parkour 指标：`Last Landing X/Y/Z`, `Hit X/Y/Z`, `Jump X/Y/Z`, `Speed X/Y/Z`, `Speed Vector`, `Airtime`, `Last Turning`, `Preturn`, `Second Turn`, `Last Sidestep`, `Last Timing`, `Offset X/Z`, `MM Offset X/Z`, `Max FMM`, `BWJam`, `Grind` 等。这些由 `0x180026750` 附近的 tick 逻辑更新，并通过 Ore UI / ScreenView 绘制。

### 4.3 Input Checker

`/mpk is` 打开的是另一套系统：一个带表达式的输入回放/检查器，持久化到 `mods/dmf-mpk/is/*.json`。字符串显示它支持：

- 赋值/读取：`setposx`, `setposz`, `setpos`, `setvx`, `setvz`, `setvec`, `zerox`, `zeroz`
- 物理参数：`speed`, `slowness`, `setslip`, `angles`, `fastmath`, `inertia`, `sneak_delay`
- 输出：`println`, `outvx`, `outvz`, `speedvector`, `outangle`, `outturn`, `outfacing`, `outspeed`, `outxz`, `outx0`, `outz0`
- 状态：`sprinting`, `sneaking`, `jumping`, `airborne`, `water`, `blocking`, `ladder`, `block`
- 控制：`duration`, `rotation`, `delay`, `sneak`, `sprint`, `define`

它不是 macro 的替代品；是 dmf 作者用来分析 parkour 输入的脚本层。

### 4.4 其他

- **Zoom**：`zoomEnable`, `zoomFactor`, `zoomCinematic`，默认 `C`。
- **Practice**：只在 hosted world 可用；记录/重置位置。
- **Landing block / momentum block / box**：用于落地判定。
- **Toggle sprint**：宏/按键共用的疾跑状态。
- **Sensitivity optimizer**：`/mpk os <rotation>` 调整灵敏度，使整数鼠标像素刚好转固定角度。
- **HUD editor**：`/mpk hudeditor`，标签位置保存在 `labelPositions`。

---

## 5. 与 harness 接口的关系

### 5.1 为什么不直接改 `dmf.dll`

- 闭源，无源码；改二进制只能做一次性 patch，版本一更新就失效。
- dmf 的 macro 播放依赖 hosted world 检查和内部全局状态，外部脚本很难安全地读。
- 它没有导出任何 RPC/pipe 接口，强行注入会与它的输入队列竞争。

因此新增的是**伴生 mod**：`mod/probe`（实现名 `harness`，管道仍是 `\\.\pipe\mcbe-harness`）。它和 dmf 可以同时加载，互不修改对方的 DLL。

### 5.2 harness 的玩家控制方式

harness 复用了 dmf 的注入位置（同一套 tick / 输入队列 / `_applyTurnDelta` 路径），
但具体实现、能力表与实测数字已演进多轮，统一维护在
[`../docs/mod-internals.md`](../docs/mod-internals.md)（输入、实体、世界、封包各节），
这里不再复制一份容易过时的函数表。

### 5.3 dmf macro 兼容

harness 的 macro 结构与 dmf 完全一致：

```cpp
struct MacroTick {
    bool w, a, s, d, sprint, sneak, jump, rmb, lmb;
    float yaw, pitch; // per-tick delta
};
```

- 加载：接受 9/11 字符串，`true/false/1/0`。
- 保存：输出 11 字符串，`%.9g` 角度。
- 播放：每 tick 一行；按键边沿触发；旋转调用 `_applyTurnDelta`。
- 录制：从 `MoveInputComponent` 读 WASD/Sprint/Sneak/Jump，从 `ActorRotation` 差分出 Yaw/Pitch；
  RMB/LMB 由 `MouseInputEvent` 监听器记录。回放时在行边沿直接调用 `traceRay + GameMode`，已实测
  左键破坏、右键放置。

dmf 生成的 `macros/*.json` 可以直接 `macro load --path ...` 加载。

---

## 6. 与 harness 的关系（现行文档索引）

本文成文时 harness 只有十来个命令；现在的协议、命令表、`state` 字段、`harness.json`
配置和使用限制远超那版记录，统一维护在下列位置。旧版命令表已删除，避免与现行协议混淆。

| 主题 | 现行文档 |
| --- | --- |
| 管道协议、命令表、`state` 字段、配置开关 | [`../mod/README.md`](../mod/README.md) |
| 各子系统实现细节（宏、输入、封包、观察点等） | [`../docs/mod-internals.md`](../docs/mod-internals.md) |
| 构建、安装与开发循环 | [`../README.md`](../README.md)、[`../docs/mod-build-environment.md`](../docs/mod-build-environment.md) |

与逆向结论直接相关、仍然有效的几点：

- 传输是命名管道 `\\.\pipe\mcbe-harness` 上的 UTF-8 JSON Lines（一行一条），协议号见 `hello`。
- dmf 的 macro 文件（9/11 字符串、每 tick 旋转增量）可以原样 `macro_load`，录制也能导出
  同样格式；格式与播放语义见 §3。
- 不要 patch `dmf.dll`：闭源且依赖 hosted world 内部状态（§5.1）；伴生 mod 与 dmf 可同时加载。
- dmf 的 macro/practice 受 hosted world 检查限制（§0.3），harness 没有复制这个限制。
- `smoothMacro` / `frameBasedRotation` 未逐行复刻，harness 按每 tick 增量直接应用。

## 7. 复现（IDA 导出）

构建与安装见 `docs/mod-build-environment.md` 与 `mod/README.md`；本目录只负责逆向导出：

```bash
cd ./reverse
IDA_EXPORT_DIR=... IDA_DECOMPILE_ALL=1   "C:/Program Files/IDA Professional 9.4/idat.exe" -A   -S"./reverse/ida_export.py"   -L"./reverse/idat.log"   "./reverse/dmf.dll.i64"
```

IDA MCP 连接问题见 [`../docs/ida-mcp-troubleshooting.md`](../docs/ida-mcp-troubleshooting.md)。
