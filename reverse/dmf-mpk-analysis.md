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

| 能力 | 实现 |
|---|---|
| 连续移动 | `ClientMoveInputHandler::_updateClientMoveVector(strafe, forward, Neutral, client)` |
| 疾跑 | `InputEventQueue::enqueueButton` + `MoveInputComponent` 标志 + 按钮 handler 回调；也可切到 `_toggleClientSprint` 模式 |
| 潜行 | `LocalPlayer::setSneaking(bool)` + `MoveInputComponent` 标志 |
| 跳跃 | `MoveInputState::JumpDown` / `JumpInputWasPressed` / `JumpInputCurrentlyDown` + 按钮队列/handler；`MobJump::setJumpPendingScale` 作为辅助 |
| 视角 | `LocalPlayer::_applyTurnDelta(Vec2{pitchDelta, yawDelta})` |
| 绝对朝向 | 读取当前 `ActorRotation`，计算最短角度差后调用 `_applyTurnDelta` |
| 传送 | `ServerInstance::queueForServerThread` + 服务端 `Player::teleport`（服务端权威；坐标=脚部） |
| 服务端命令 | 服务器线程 + `PlayerCommandOrigin` + `MinecraftCommands::executeCommand` |
| 快捷栏 | `PlayerInventory::selectSlot(slot, ContainerID::Inventory)`（tick 之后） |
| 读状态 | `LocalPlayer` + `MoveInputComponent` + `PlayerActionComponent` + `BlockSource` + attributes |
| 实体 | `Level::getRuntimeActorList()`，按半径过滤 |
| 方块 | `BlockSource::getBlock(BlockPos)`，返回 `BlockType` 名 |

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

## 6. harness 协议

### 6.1 传输

- 命名管道：`\\.\pipe\mcbe-harness`
- JSON Lines，UTF-8，一行一条
- 客户端：`driver/transport_mod.py`
- 事件：`hello` / `pong` / `status` / `state` / `tick` / `entities` / `blocks` / `error` / macro 事件

### 6.2 命令

| 命令 | 关键字段 | 响应 |
|---|---|---|
| `ping` | — | `pong` |
| `status` | — | `status` |
| `state` | — | `state`（下一 tick） |
| `start` / `stop` | `max_ticks` | `started` / `stopped` |
| `move` | `forward`, `strafe`, `ticks` | `moving` |
| `stopmove` | — | `stopping` |
| `set` | `forward`, `strafe`, `sprint`, `sneak`, `jump`, `ticks` | `set_ok` |
| `jump` | — | `jump_ok` |
| `look` | `yaw`, `pitch`, `relative`, `ticks` | `look_ok` |
| `entities` | `radius`, `limit` | `entities` |
| `blocks` | `positions: [[x,y,z], ...]` | `blocks` |
| `click` | `button`, `reach` | `click_result`（射线命中/GameMode 信息；左键破坏、右键放置已实测） |
| `fly` | `enabled` | `fly_ok` |
| `teleport` | `x`, `y`, `z`, `yaw?`, `pitch?`（脚部坐标） | `teleported`（服务器线程应用后） |
| `select_slot` | `slot: 0..8` | `select_slot_ok`，随后 `state.selected_slot` 更新 |
| `command` | `command`（可带 `/`） | `command_done`（`success`, `code`；方块命令 deferred，`success=false` 不代表失败） |
| `diag` | — | `diag`（Minecraft/ServerInstance/Level/Commands 可见性） |
| `mouse` / `mouse_clear` | — | `mouse`（事件计数）/ `mouse_cleared` |
| `macro_load` | `name` 或 `path` | `macro_loaded` |
| `macro_run` | `start`, `end`, `loop` | `macro_started` |
| `macro_stop` | — | `macro_stopped` |
| `macro_status` | — | `macro_status` |
| `macro_list` | — | `macro_list` |
| `macro_rec_start` | `max_ticks` | `macro_rec_started` |
| `macro_rec_stop` | `name` | `macro_rec_saved` |
| `macro_save` | `name`, `ticks` | `macro_saved` |

`ticks = -1` 表示持续到 `stopmove` / 下一条 `set`。

### 6.3 `state` 字段

```json
{
  "event": "state",
  "tick": 123,
  "x": 0.5, "y": -49.0, "z": 0.5,
  "vx": 0.0, "vy": 0.0, "vz": 0.0,
  "yaw": 90.0, "pitch": 0.0,
  "on_ground": true,
  "sprinting": false, "sneaking": false, "swimming": false, "flying": false,
  "in_water": false, "alive": true,
  "health": 20, "max_health": 20,
  "hunger": 20.0, "saturation": 5.0, "exhaustion": 0.0,
  "xp_level": 0.0, "xp_progress": 0.0,
  "gamemode": 1, "dimension": 0,
  "name": "Player", "uuid": "....",
  "selected_slot": 0,
  "item": {"type": "minecraft:stone", "name": "Stone", "count": 64, "aux": 0},
  "feet_x": 0.5, "feet_y": -49.0, "feet_z": 0.5,
  "head_x": 0.5, "head_y": -47.38, "head_z": 0.5,
  "block_feet": "minecraft:air",
  "block_below": "minecraft:stone",
  "block_head": "minecraft:air",
  "block_above": "minecraft:air",
  "input": {
    "w": false, "a": false, "s": false, "d": false,
    "sprint": false, "sneak": false, "jump": false,
    "move_x": 0.0, "move_y": 0.0,
    "action_sprint": false
  }
}
```

### 6.4 示例

```bash
# 读状态
py -3.11 driver/transport_mod.py state

# 前进 40 tick，疾跑
py -3.11 driver/transport_mod.py set --forward 1 --sprint --ticks 40

# 绝对朝向 90/0
py -3.11 driver/transport_mod.py look --yaw 90 --pitch 0 --absolute

# 附近实体
py -3.11 driver/transport_mod.py entities --radius 16

# 方块
py -3.11 driver/transport_mod.py blocks 0 -50 0 0 -49 0

# 加载并播放 dmf macro
py -3.11 driver/transport_mod.py macro load --name default
py -3.11 driver/transport_mod.py macro run

# 录制 5 秒，导出 dmf 兼容 macro
py -3.11 driver/transport_mod.py macro rec-start
py -3.11 driver/transport_mod.py macro rec-stop --name my_run
```

Python 里：

```python
from driver.transport_mod import Harness

with Harness() as h:
    print(h.state()["x"], h.state()["z"])
    h.walk(forward=1.0, ticks=40, sprint=True)
    print(h.state()["x"], h.state()["z"])
    h.look_at(90, 0)
    h.jump()
```

---

## 7. 限制与注意事项

1. **hosted world**：dmf 的 macro/practice 只在自己托管的世界可用；harness 的 macro 播放没有复制这个限制，但客户端侧 macro 在远程服务器上可能被服务端校正/拒绝，实际用途仍建议本地测试世界。
2. **RMB/LMB**：macro 解析/保存保留这两个字段；播放时会尝试通过输入队列和按钮 handler 注入。
   如果服务端或界面状态不接受，仍可能无效。
3. **smoothMacro / frameBasedRotation**：dmf 的这两种旋转平滑模式没有逐行复刻；harness 按“每 tick 增量”直接应用。对逐 tick 宏来说行为一致。
4. **绝对朝向**：`look --absolute` 每次 tick 重新计算最短角差；`ticks=1` 是“转过去一次”，`ticks=-1` 是“持续跟踪目标”。
5. **跳跃**：需要头顶有足够空间；`block_head`/`block_above` 可以直接检查。站在树叶/天花板下方时，
   跳跃会在 0.2–0.5 格内被挡下，这不是接口失败。
6. **teleport**：已改为服务端权威（`queueForServerThread` + 服务端 `Player::teleport`），
   坐标是脚部坐标；不要再发 `MovePlayerPacket(Teleport)`（会导致“主机连接已断开”）。
   通用命令走 `command`，用 `PlayerCommandOrigin`，`@s`/`~` 语义与聊天一致。
7. **手动输入**：harness 只在自身控制活跃时写 `MoveInputComponent` 标志；空闲时不覆盖真实键鼠，
   否则会出现手动跳跃/飞行回弹。早期版本有这个问题，现已修复。
8. **飞行**：`fly` 通过 `Player::setAbility(MayFly/Flying)` 实现；仅在创造/允许飞行的世界生效。
   服务端 abilities 包可能在 teleport 后重新同步，必要时再发一次 `fly --off`。
9. **飞行状态一致性**：`fly` 现在同时写客户端和服务端玩家。只写客户端时，服务端 abilities
   会在 teleport 后把 `flying` 同步回来，导致走路出现 ±1 格校正、平均速度偏高；修复后
   步行峰值回到 0.2159 格/tick。
10. **鼠标录制**：`MouseInputEvent` 的 action 是 `MouseAction`：左=1、右=2（不是 0/1）；
    后台合成点击只产生 `ActionMove`，必须让游戏窗口在前台再用 raw 注入。
11. **快捷栏**：`select_slot` 在 tick 之后调用 `PlayerInventory::selectSlot`，不要在
    `onPlayerTick` 里改（会被原 tick 覆盖）。
12. **输入竞争**：如果玩家同时用真实键鼠，harness 的 `_updateClientMoveVector` 会覆盖移动向量；宏播放期间建议不要手动操作。
13. **版本**：所有组件/偏移对应 1.26.51.01；升级游戏后需要重新构建，不能直接复用 1.26.45 的偏移。
14. **二进制 patch**：不建议改 `dmf.dll`。伴生 mod 与 dmf 可以共存，升级 dmf 不影响 harness。

---

## 8. 复现

```bash
# 1. 导出 IDA（已有 .i64 时）
cd ./reverse
IDA_EXPORT_DIR=... IDA_DECOMPILE_ALL=1 \
  "C:/Program Files/IDA Professional 9.4/idat.exe" -A \
  -S"./reverse/ida_export.py" \
  -L"./reverse/idat.log" \
  "./reverse/dmf.dll.i64"

# 2. 构建 harness
cd ./mod/probe
export PATH="/d/Tools/LLVM22/bin:/d/Tools/xmake:$PATH"
export XMAKE_GLOBALDIR='D:\Tools\xmake-home'
export XMAKE_PKG_INSTALLDIR='D:\Tools\xmake-packages'
xmake -y

# 3. 安装（游戏目录）
cp -r bin/probe "%APPDATA%/levilauncher.exe/versions/1.26.51.01/mods/probe"

# 4. 进存档后
py -3.11 driver/transport_mod.py ping
py -3.11 driver/transport_mod.py state
```

> 第 3 步会写游戏实例目录；本仓库的构建流程不会自动执行它。