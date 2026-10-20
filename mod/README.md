# 本地构建 client mod（LeviLamina 26.51.7 / 游戏 1.26.51.01）

这份目录是从官方 `levilamina-mod-template` 复制并修好工具链后的最小可编译工程。
当前产物：`mod/probe/bin/probe/probe.dll`（x64，client 平台，named pipe 已接好）。

## 工具链位置

| 组件 | 版本 | 路径 |
|---|---|---|
| Visual Studio Build Tools | 17.14.42 | `D:\Tools\VSBuildTools` |
| MSVC | 14.44.35207 | `D:\Tools\VSBuildTools\VC\Tools\MSVC\14.44.35207` |
| Windows SDK | 10.0.26100 | `C:\Program Files (x86)\Windows Kits\10` |
| LLVM / clang-cl | **22.1.8** | `D:\Tools\LLVM22` |
| LLVM（备用） | 23.1.3 | `D:\Tools\LLVM` |
| xmake | 3.1.2 | `D:\Tools\xmake` |
| xmake 全局目录 | — | `D:\Tools\xmake-home` |
| xmake 包安装目录 | — | `D:\Tools\xmake-packages` |

> Windows SDK 被 VS 安装器固定在 C 盘；其余主体都在 D 盘。

## 构建命令

```bash
cd /d/mcbe-harness/mod/probe

export PATH="/d/Tools/LLVM22/bin:/d/Tools/xmake:$PATH"
export HTTPS_PROXY=http://127.0.0.1:7897
export HTTP_PROXY=http://127.0.0.1:7897
export XMAKE_GLOBALDIR='D:\Tools\xmake-home'
export XMAKE_PKG_INSTALLDIR='D:\Tools\xmake-packages'

xmake f -c -y -p windows -a x64 -m release --target_type=client
xmake -y -v
```

产物在 `bin/probe/`：`probe.dll`、`probe.pdb`、`manifest.json`
（`"platform": "client"`）。DLL 已确认导入 `LeviLamina.dll`。

## 加载与测试

把 `bin/probe/` 整个目录复制到实例的 `mods/` 下：

```
<versions>\1.26.51.01\mods\probe\
```

按现有“游戏安装目录只读”的约束，构建流程**不会自动复制**；这一步需要你确认后执行。

进存档后运行：

```bash
py -3.11 driver/transport_mod.py ping
py -3.11 driver/transport_mod.py record --ticks 40 --out out/probe.jsonl
```

协议（JSON lines，管道 `\\.\pipe\mcbe-harness`）：

| 方向 | 消息 |
|---|---|
| Python → mod | `{"cmd":"ping"}` / `{"cmd":"status"}` / `{"cmd":"start","max_ticks":40}` / `{"cmd":"stop"}` |
| mod → Python | `hello` / `pong` / `started` / `tick` / `stopped` / `status` / `error` |

`tick` 样本字段：`tick, x, y, z, vx, vy, vz, sprint, action_sprint, input_sprint, input_sprint_down`。
其中 `action_sprint` 是 `PlayerActionComponent` 的 bit 9（`StartSprinting`），
`input_sprint` / `input_sprint_down` 分别是 `MoveInputComponent` 的 Sprinting flag 和
`MoveInputState` 的 SprintDown。

## 这台机器上必须保留的两个修正

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

恢复原状：

```bash
cp /d/Tools/xmake/toolchains/clang-cl/check.lua.orig \
   /d/Tools/xmake/toolchains/clang-cl/check.lua
```

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
- `RakPeer.h`：在**文件末尾**追加 `ShadowBanList.h`（解决 `RakPeer.h` ↔ `ShadowBanList.h` 循环包含）

`mod/probe/xmake.lua` 里已加：

```lua
add_repositories("local-repo local-repo")
```

这个本地仓库会覆盖上游 `levimc-repo` 里的同名 `levilamina` 包，所以补丁是可复现的；
上游包源码被 xmake `git reset` 也不会丢。

## 构建状态

- 对应 **1.26.51.01**，不能用于 1.26.45.01；45 的偏移/组件哈希不要直接套。
- 已在本机 1.26.51.01 创作存档内实测加载；协议与验收结果见文末“harness API”。
- 构建产物复制到实例 `mods/` 会写游戏安装目录，按现有约束需要确认后再做。
---

## 2026-10-20：harness API（1.26.51.01）

`probe` 已从“只采样”扩展为可供 Python/LLM 直接调用的 in-game harness。

### 关键实现

- 用 `LocalPlayer::$normalTick` 的 LL 实例 hook，在**原 tick 之前**注入输入，再调用 `origin()`；
  这与 dmf-mpk 的 macro 注入时机一致。`ClientLevelTickEvent` 的时机偏晚，跳跃/按键会被覆盖。
- 移动：`ClientMoveInputHandler::_updateClientMoveVector(strafe, forward, ...)`
- 视角：`LocalPlayer::_applyTurnDelta(Vec2{pitchDelta, yawDelta})`（pitch 符号与 `ActorRotation.x` 相反）
- 按钮：`MoveInputComponent` 标志 + `InputEventQueue::enqueueButton` + 输入 handler 回调；
  可用 `input_cfg` 的 `mode` 位掩码切换：1=直接标志，2=队列，4=handler，8=toggle fallback。
- 状态：`LocalPlayer` + `MoveInputComponent` + `PlayerActionComponent` + 属性 + `BlockSource` + `Level`
- macro：dmf 兼容的 9/11 字符串 tick，支持加载、播放、录制、保存。
- macro 点击：RMB/LMB 在行边沿直接走 `traceRay + GameMode`，左键破坏、右键放置；鼠标按键也由
  `MouseInputEvent` 监听器记录进 macro。

### 协议（`\.\pipe\mcbe-harness`，JSON lines）

| 命令 | 说明 |
|---|---|
| `ping` / `status` / `state` | 心跳、状态、玩家快照 |
| `start` / `stop` | 逐 tick 采样（`tick` 事件） |
| `set` | `forward,strafe,sprint,sneak,jump,rmb,lmb,ticks`；`ticks=-1` 持续 |
| `move` / `stopmove` / `jump` | 兼容旧命令 |
| `look` | `yaw,pitch,relative,ticks`；`relative=false` 为绝对朝向 |
| `entities` | `radius,limit`，附近 Actor |
| `blocks` | `positions:[[x,y,z],...]`，方块名 |
| `click` | `button=left/right, reach`；射线命中 + GameMode 左/右键 |
| `select_slot` | `slot=0..8`；tick 之后调用 `PlayerInventory::selectSlot`，`state.selected_slot` 会跟着变 |
| `fly` | `enabled`；设置 MayFly/Flying 能力（创造/允许飞行时有效） |
| `teleport` | `x,y,z,yaw?,pitch?`；坐标是**脚部**坐标，走集成服务器（服务端权威，不会回弹） |
| `command` | `command`（可带或不带前导 `/`）；在服务器线程用 `PlayerCommandOrigin` 执行 `/fill`、`/tp` 等。方块类命令是 deferred，`success=false` 不代表失败，效果要用 `blocks`/`setup.verify` 确认 |
| `diag` | 调试：报告 `Minecraft` / `ServerInstance` / 服务端 `Level` / `MinecraftCommands` 是否可见 |
| `mouse` / `mouse_clear` | 调试：`MouseInputEvent` 计数、最后 action/data、每个 MouseAction 的计数 |
| `macro_load` / `macro_run` / `macro_stop` / `macro_status` / `macro_list` | macro 播放 |
| `macro_rec_start` / `macro_rec_stop` / `macro_save` | macro 录制/保存 |
| `buttons` | 调试：列出已注册按钮 ID |
| `input_cfg` | 调试：`mode/focus/source/controller` |

### state 关键字段

`x,y,z`（Actor position）、`feet_x/y/z`、`head_x/y/z`、`vx/vy/vz`、`yaw/pitch`、
`on_ground`、`sprinting/sneaking/swimming/flying/in_water/alive`、
`health/max_health`、`hunger/saturation/exhaustion`、`xp_level/xp_progress`、
`gamemode/dimension/name/uuid/selected_slot/item`、
`block_feet/block_below/block_head/block_above`，以及 `input` 子对象。

### 已验证（1.26.51.01，创作存档）

- `state`、`blocks`、`entities`（排除自己）、`look`（绝对/相对）
- `set` 前进、疾跑、潜行；步行实测峰值 **0.2159 格/tick**（原版 4.317 m/s），
  `example.json` 行走 60 tick 位移 12.04 格、接地 100%
- 跳跃：平坦世界实测起跳高度 1.23 格；树叶/天花板下会被挡
- 飞行：`fly` 打开后 `state.flying=true`，按住跳跃可上升；关闭后正常落地
- 点击：`click left` 实测可把脚下方块打掉（草方块 → air）；`click right` 实测把手里的草方块放到
  `(0,-60,13)`（原来 air），创造模式物品不消耗
- macro `macro_save` → `macro_load` → `macro_run`，包含跳跃和疾跑
- macro `macro_rec_start` → 注入输入 → `macro_rec_stop`，生成 11 字符串 dmf 兼容文件
- macro 左键行：`(0,-61,13)` 草方块 → air
- macro 右键行：`(0,-61,13)` air → 草方块（手持草方块）
- `teleport`：服务端权威，连续 2 秒后位置仍是目标值；带 `yaw/pitch` 时朝向也生效
- `select_slot`：slot 3 → `selected_slot=3`、`item=null`；slot 0 → 草方块回来
- `command`：`fill 20 -50 20 22 -50 22 stone` 返回 `success=true`，`blocks` 读回 stone
- 鼠标录制：游戏窗口前台时用 Win32 `SendInput` 注入真实 raw 鼠标事件；
  `MouseInputEvent` 收到 `ActionLeft=1` / `ActionRight=2`（`DataDown=1`）。
  最终验收的 76 tick macro 里，左键行 4 个 tick、右键行 3 个 tick 为 true
  （更早一次 539 tick 录制也命中同样两段）。
- 场景 DSL：`driver/run_mod.py` 跑通 `setup-platform.json`（`fill` + `tp @s` + verify）
  和 `example.json`（服务端 tp、朝向、+Z 行走 60 tick、79 个样本）
- 卸载/重装：`probe.dll` 直接覆盖，`dmf-mpk` 可共存

### 场景 DSL（旧 frida runner 的 mod 版）

`driver/run_mod.py` 复用 `driver/scenario.py` + `driver/runner.py` + `driver/output.py`，
只把 agent 换成 `driver/mod_agent.py::ModAgent`：

```bash
py -3.11 driver/run_mod.py --state
py -3.11 driver/run_mod.py --command "fill -4 -60 -4 4 -60 4 stone"
py -3.11 driver/run_mod.py --block 0 -60 0
py -3.11 driver/run_mod.py --scenario scenarios/setup-platform.json
py -3.11 driver/run_mod.py --scenario scenarios/example.json
py -3.11 driver/run_mod.py --probe --probe-ticks 40
```

`setup.commands` / `setup.teardown` 直接走 `command` 通道；`tp` 走服务端 `teleport`；
`setup.verify` 走 `blocks`；逐 tick 采样走 `start`/`stop` 的 `tick` 事件。
输出仍是 `out/<name>-<stamp>.jsonl` + `.summary.json`。

### 已知限制

- **移动注入依赖客户端/服务端的飞行状态一致**：早期 `fly` 只写客户端，服务端仍认为在飞，
  走路时会出现 ±1 格位置校正、平均速度偏高（~0.37/tick）。`fly` 现在同时写服务端玩家；
  实测步行 41 tick 平均 0.199、峰值 0.2159 格/tick（原版 4.317 m/s），全程接地。
  如果以后又看到周期性跳变，先查 `state.flying` 和服务端 abilities。
- `select_slot` 依赖 `PlayerInventory::selectSlot`；如果后续版本改了库存布局需要重新确认。
- 鼠标录制只认 `MouseInputEvent`（raw input）。CUA 的合成点击和 `SendInput` 只有在
  游戏窗口前台时才会被 `MouseDevice::feed` 收到；后台合成点击只有移动事件。
- RMB/LMB 的 macro 行在播放时按边沿直接走 `GameMode`，`GameMode` 的回调可能因
  界面/服务端状态失败（如空手放置）。
- 视角平滑（`smoothMacro`/`frameBasedRotation`）未逐行复刻。
- 输入注入依赖 `LocalPlayer::$normalTick` 符号；游戏升级后需重新构建。
- 直接写 `MoveInputComponent` 标志只在 harness 自己控制期间发生；空闲时不再覆盖真实键鼠输入
  （早期版本会覆盖，导致手动跳跃/飞行回弹）。
