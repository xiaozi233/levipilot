# 旧路线：frida RPC（Minecraft 1.26.45.01）

> 历史资料。当前主路径是 LeviLamina 客户端 mod，见仓库根目录的 README。
> 旧代码（`agent/`、`driver/run.py`、`driver/session.py`、`driver/postmsg.py` 等）
> 已从 `main` 移除，完整保留在 **`legacy/frida`** 分支；主分支只保留本文档。
> 这份文档记录旧路线的约束、运行时结论和标定结果，对理解游戏内部机制仍然有用。

## 约束

- 游戏安装目录只读，任何构建产物、脚本、日志都不写回安装目录。
- 实验控制不发合成键鼠输入，也不抢前台窗口。场地准备用 `/` 命令时，是把 `WM_CHAR`
  消息直接投递给游戏窗口（后台、未聚焦也能收到）。
- 观测走内存/钩子，不靠截图；截图只用于 UI 操作确认和最终视觉验收。
- 客户端与 BDS 必须同为 **1.26.45.01**，否则行为常数可能不同。

## 环境

- Python 3.11（`py -3.11`），frida 17.18.0；Python 3.14 默认解释器没有 frida。
- 地址与组件布局见 [`offsets-1.26.45.01.md`](offsets-1.26.45.01.md)。
- frida 每次加载 script 会在 `%TEMP%` 建一个 `frida-*` 目录（~44 MB），detach 后不自动消失。
  旧分支的 `driver/session.py` 会在 `close()` 时只删本次 attach 新建的那些。

## 快速开始

游戏需已启动并进入世界。先铺测试平台，之后所有场景都在平台中心 (0.5, -49, 0.5) 跑。

```bash
git switch legacy/frida                      # 旧代码只在这个分支
py -3.11 driver/check_binary.py             # 不连游戏：校验 EXE 与 agent 指纹
py -3.11 driver/bringup.py --launch --enter-world   # 重启后一键回到可实验状态（离线兜底）
py -3.11 driver/run.py                      # 状态 / registry / 签名检查
py -3.11 driver/run.py --diagnose           # 组件、双 registry 诊断
py -3.11 driver/run.py --scenario scenarios/setup-platform.json
py -3.11 driver/run.py --probe              # 传送 + 朝 +Z 走 20 tick
py -3.11 driver/run.py --scenario scenarios/wall-stop.json
py -3.11 driver/run.py --scenario scenarios/walk-sneak.json
py -3.11 driver/regress.py                  # 全部场景回归门禁（约 45 s）
py -3.11 driver/record_probe.py --seconds 45 --out out/probe.jsonl
py -3.11 driver/analyze_probe.py out/probe.jsonl
py -3.11 driver/probe_sprint.py             # 旧版标定（需要 helper 按键，已不推荐）
py -3.11 driver/run.py --command "setblock 3 -49 3 stone"
py -3.11 driver/run.py --block 0 -50 0
py -3.11 driver/run.py --scan -4 4 -4 4
py -3.11 driver/run.py --scenario scenarios/teardown-platform.json
py -3.11 driver/run.py 12345 --probe        # 按 PID
```

## 运行时模型

1. **进程里有两个 ECS registry**
   - 客户端 registry：有 `LocalPlayerComponent`，用于读取玩家状态/预测。
   - 服务端 registry：没有 `LocalPlayerComponent`，但有 `PlayerComponent`，实体 id 可能不同。
2. **位置是服务端权威的**
   - 写客户端 `StateVector` / `ActorSetPositionRequest` 会被服务端位置覆盖（实测无效）。
   - 默认 `tp` 走服务端 registry 的 `ActorSetPositionRequestComponent`，实测有效。
3. **移动输入要同时写两份**：客户端和服务端 registry 各有一份 `MoveInputComponent`。
   只写客户端会出现客户端预测与服务端校正互相拉扯（位置来回跳）。agent 会同时写两份，
   并在 `stop_input` 时写 0。注入的输入能复现原版步行速度：逐 tick 位移收敛到
   **0.2158 格/tick**（4.317 m/s）。
   ⚠️ `getState().vel` 不能当速度用：它是摩擦前速度，正常地面上只有真实逐 tick 位移的
   0.546 倍（0.546 = 0.6 滑度 × 0.91）。速度一律用相邻两 tick 的位置差算。
4. **朝向必须走命令通道**：写 `ActorRotation` 组件不管用——客户端相机每帧把它写回，
   服务端按真实相机朝向解释移动输入，表现为橡皮筋。用 `/tp @s ~ ~ ~ <yaw> <pitch>`
   才能同时改掉两侧的权威朝向。`look` 步骤在有命令通道时默认走这条路
   （`mode: "command"`）；`mode: "memory"` 只改组件并给警告。
5. **失焦自动暂停已关闭**：`options.txt` 的 `pause_menu_on_focus_lost` 改为 `0`。
6. **方块读取**：hook 方块查询包装 `0x144767E30` 捕获查询上下文，缓存 accessor
   （`[ctx+456] → 虚表+96`）；`accessor 虚表+16(BlockPos) → Block*`；
   `Block+0x68 → BlockLegacy`，`+0xE0` 是名字的 FNV-1-64，`+0xE8` 是完整名字 `std::string`。
   首次需要一次移动/碰撞来捕获 accessor。
7. **命令通道（场地准备）**：`../driver/postmsg.py` 把按键与字符作为窗口消息投递给游戏窗口
   （`WM_KEYDOWN`/`WM_CHAR`，`lParam` 带 `MapVirtualKeyW` 的扫描码）。打开聊天框用 `/`
   键，之后投递字符、回车提交。游戏不需要在前台。
8. **UI 操作**：见 [`ui-automation.md`](ui-automation.md)。

## RPC

Python 侧用 `snake_case` 调用（frida-python 自动转 JS `camelCase`）：

| Python | JS | 说明 |
| --- | --- | --- |
| `get_state()` | `getState` | 位置（脚部）/眼睛位置/速度/朝向/接地/ADF/输入 |
| `get_tick_count()` | `getTickCount` | tick 时钟（post-move hook） |
| `tp(x,y,z,mode)` | `tp` | 默认 `server`：服务端权威传送 |
| `set_input(obj)` | `setInput` | 局部坐标 `forward/strafe` |
| `move_world(dx,dz)` | `moveWorld` | 世界坐标移动，按服务端 yaw 反算 |
| `jump()` | `jump` | 单次起跳请求 |
| `stop_input()` | `stopInput` | 清空输入（两边写 0） |
| `set_yaw_pitch(y,p)` | `setYawPitch` | 钉住客户端/服务端朝向 |
| `set_vel(x,y,z)` | `setVel` | 写 StateVector 速度 |
| `server_state()` | `serverState` | 服务端实体的位置/速度/yaw |
| `server_tp(x,y,z)` | `serverTp` | 服务端传送（`tp` 的底层） |
| `block_at(x,y,z)` | `blockAt` | 读取方块：名字、FNV-1-64、Block/Legacy 指针 |
| `inspect_registries()` | `inspectRegistries` | 列出所有 registry 的组件/实体 |
| `start_recording(n)` / `stop_recording()` | `startRecording` / `stopRecording` | 逐 tick 采样 |
| `status()` / `diagnose()` | `status` / `diagnose` | 诊断 |

## 已验证（2026-10-18，游戏内实测）

- `getState` 与 HUD 对齐：X/Z 精确一致，`pos.y` 为脚部（StateVector 里是眼睛 = 脚部 +
  1.6200101），yaw/pitch 精确。
- **命令通道不抢焦点**：`foreground: False` 时 `/setblock 2 -60 2 stone` 生效。
- **朝向**：`/tp @s ~ ~ ~ 0 0` 之后客户端与服务端 yaw 都稳定为 0；forward=1 → 两侧同步 +Z，
  strafe=1 → 两侧同步 +X，yaw=90 → 两侧同步 -X，逐 tick 位置吻合，橡皮筋消失。
- **撞墙停住**（`../scenarios/wall-stop.json`）：x=6 处 3 格高的墙，从中心朝 +X 走 80 tick：
  位移 **(+5.200, 0, 0)**，最大速度 **0.2159 格/tick**，接地 100%，停在 x=5.7。
- **移动速度**：四种输入（local/world × strafe/forward）首 tick 0.098 → 收敛到
  **0.2158 格/tick = 4.317 m/s**；两轴对称、两轮重复一致。
- **跳跃**（`../scenarios/jump-distance.json`）：全速起跳，滞空 4–5 tick，
  峰高 **+1.166 格**（脚部 -49.000 → -47.834）。
- **撞台阶**（`../scenarios/step-up.json`）：一格高的台阶，全速助跑起跳跳不上去：
  滞空时已越过边缘（x=3.91），下落时被侧面挤出，落在 x=3.700。
  滞空水平只推进 **0.630 格**（Bedrock 的低空中控制）。
- 服务端传送：客户端/服务端位置同步到目标点，`on_ground=true`。
- 后台：失焦 3 秒 tick 前进 65（20 tps 满速）。
- 客户端写位置（StateVector / ActorSetPositionRequest）实测无效，会被服务端覆盖。

## 未解决

- **sprint 未复现（sneak 已注入）**：潜行用“输入向量 ×0.3”复现原版 0.3 倍速
  （0.0648 格/tick）。真实 `ctrl+w` 是 0.2805 格/tick（×1.3），但把内存差异
  （ADF bit3、`+0x00` bit8、`+0x54` bit1、`+0x60` bit1）全部注入后仍是走路速度，
  多半走服务端 sprint 状态（`PlayerInputRequest`/`PlayerActionPacket`）。
  已排除：ADF 各位、`MoveInput +0x00/+0x54/+0x60`、`PlayerInputRequest` 前 2 KB、
  以及全部 ECS 组件。放大输入也不管用（`forward=1.3` 被 clamp）。
  标定工具：`../driver/probe_sprint.py`，详见 [`offsets-1.26.45.01.md`](offsets-1.26.45.01.md) §4.1。
- **命令派发 RPC**：进程内直接发 `CommandRequestPacket`。静态入口已定位
  （见 offsets §10）：命令名、注册函数 `sub_14437CBF0`、注册总入口
  `sub_144336530`/`sub_14433B5E0`。IDA 访问用 `../driver/ida_call.py`。
  下一步是找客户端聊天提交函数或 registry vtable 的 `executeCommand`。
- **GameTest**：`spawnSimulatedPlayer` 与真实输入的等价性实验。

## 采样口径（易踩的坑）

- **`vel` 不是速度**：`StateVector.vel` 是摩擦前速度，约为真实逐 tick 位移的 0.546 倍。
  判速度用相邻 tick 位置差。
- **tp 与 teardown 不是位移**：跨 step 的位移、`tp`/`vel` 步骤内的位移不计入速度；
  记录窗口在 teardown 之前关闭。
- **命令通道后有一记卡顿**：聊天命令执行后约 1.5 s，进程卡住 ~1.2 s 再补跑 ~24 tick，
  期间位置不被积分。朝向命令要放进 `setup`，并让 setup 之后空转 60 tick 吸收卡顿
  （runner 已自动这么做）。
- **`pos.y` 是脚部**（StateVector 里是眼睛 = 脚部 + 1.6200101）。
- **`look` 用 `mode=memory` 只改组件**，服务端仍按真实相机朝向走，会橡皮筋。

## 状态清单（旧路线）

- [x] 仓库与目录骨架
- [x] frida agent：读状态、tick 采样、输入、世界方向移动、跳跃、服务端传送、方块读取
- [x] driver：scenario JSON、逐步执行、逐 tick 采样、JSONL + summary、`--block` / `--scan`
- [x] 失焦暂停关闭与后台 tick
- [x] 命令通道：窗口消息投递（不抢焦点）+ `--command` / `--command-channel`
- [x] `setup.commands` / `setup.verify` / `setup.teardown` / `setup.strict`
- [x] 朝向走命令通道，修掉移动方向的橡皮筋
- [x] 65×65 测试平台
- [x] 回归场景：撞墙、跳跃距离、撞台阶
- [x] 命令通道卡顿定位 + setup 后自动吸收
- [x] 无游戏单元测试
- [x] 版本指纹自检
- [x] 速度口径修正
- [x] 潜行注入（输入×0.3）
- [ ] 疾跑倍率来源
- [ ] 滞空输入标定
- [ ] 命令派发 RPC
- [ ] GameTest 等价性实验

## 工具链经验（为什么不再用鼠标点击）

- PowerShell 的 `GetWindowRect` 在 DPI 缩放下返回虚拟化坐标，和实际窗口/截图不在一个
  坐标系，点击会整体偏移。
- Minecraft 对合成鼠标事件基本不响应（Raw Input），即使坐标正确也点不动；
  但文本输入走 `WM_CHAR`，直接投递窗口消息可以在不聚焦时打字。
- 可靠路径是窗口消息投递或 ZCode 原生 CUA helper 的键盘注入；传送、输入、朝向最终都
  不需要 UI，走 RPC / 服务端命令。
