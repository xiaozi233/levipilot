# mcbe-harness

Minecraft Bedrock **1.26.45.01** 的实验驱动 harness。

目标：不再依赖 Computer Use / 合成键鼠输入，通过 frida RPC 和游戏内 ECS 组件
控制实验、逐 tick 采样，把“操作游戏”换成“调用游戏”。

## 约束

- 游戏安装目录只读，任何构建产物、脚本、日志都不写回安装目录：
  `%APPDATA%\levilauncher.exe\versions\1.26.45.01`
- 实验控制不发送合成键鼠输入。聊天命令只用于无法走 RPC 的 **setup**（如 `/fill`），
  传送和移动都走 frida RPC。
- 观测走内存/钩子，不靠截图；截图只用于最终视觉验收。
- 客户端与 BDS 必须同为 1.26.45.01，否则行为常数可能不同。

## 环境

- Python 3.11（`py -3.11`），已装 frida 17.18.0。
- Python 3.14 默认解释器没有 frida，不要用它跑 driver。
- 地址与组件布局见 [`docs/offsets-1.26.45.01.md`](docs/offsets-1.26.45.01.md)。

## 快速开始

游戏需已启动并进入世界。默认测试点是 **(0.5, -63, 0.5)**。

```bash
py -3.11 driver/check_binary.py             # 不连游戏：校验 EXE 与 agent 指纹
py -3.11 driver/run.py                      # 状态 / registry / 签名检查
py -3.11 driver/run.py --diagnose           # 组件、双 registry 诊断
py -3.11 driver/run.py --probe              # 传送 + 朝 +Z 走 20 tick，验证全链路
py -3.11 driver/run.py --scenario scenarios/example.json
py -3.11 driver/run.py --block 0 -63 0      # 读取单个方块名
py -3.11 driver/run.py --scan -4 4 -4 4     # 扫描区域，输出每列表面方块
py -3.11 driver/run.py 12345 --probe        # 按 PID
```

## 运行时模型（重要）

1. **进程里有两个 ECS registry**：
   - 客户端 registry：有 `LocalPlayerComponent`，用于读取玩家状态/预测。
   - 服务端 registry：没有 `LocalPlayerComponent`，但有 `PlayerComponent`，实体 id 可能不同。
2. **位置是服务端权威的**：
   - 写客户端 `StateVector` / `ActorSetPositionRequest` 会被服务端位置覆盖（实测无效）。
   - 默认 `tp` 走服务端 registry 的 `ActorSetPositionRequestComponent`，实测有效。
3. **移动输入要同时写两份**：客户端和服务端 registry 各有一份 `MoveInputComponent`。
   只写客户端会出现客户端预测与服务端校正互相拉扯（位置来回跳）。
   agent 会同时写两份，并在 `stop_input` 时写 0，避免残留输入。
4. **朝向很难改**：客户端 `ActorRotation` 每 tick 被相机系统回写，服务端另有自己的朝向。
   需要指定世界方向时用 `move_world(dx, dz)`：按服务端当前 yaw 反算 forward/strafe，
   不依赖改相机。
5. **失焦自动暂停已关闭**：
   `options.txt` 里 `pause_menu_on_focus_lost` 已改为 `0`（两个用户目录都改了）。
   实测游戏失焦 3 秒仍前进 65 tick，可以后台运行。
6. **方块读取**：hook 方块查询包装 `0x144767E30` 捕获查询上下文，缓存 accessor
   （`[ctx+456] → 虚表+96`）；`accessor 虚表+16(BlockPos) → Block*`；
   `Block+0x68 → BlockLegacy`，`+0xE0` 是名字的 FNV-1-64，`+0xE8` 是完整名字 `std::string`。
   首次需要一次移动/碰撞来捕获 accessor，之后 `block_at`/`--scan` 可直接查询。

## 目录

| 路径 | 用途 |
| --- | --- |
| `agent/` | frida agent，暴露 RPC（ECS 读写 + tick hook） |
| `driver/` | Python 场景驱动器（session / scenario / runner / output） |
| `driver/native/` | ZCode 原生 CUA helper 的最小封装，仅用于 setup 聊天命令 |
| `scenarios/` | 场景 DSL（JSON） |
| `behavior_pack/` | GameTest / mcfunction 场景准备 |
| `docs/` | 1.26.45.01 的地址、组件 id、字段偏移、运行时结论 |
| `out/` | 运行产物，已 gitignore |
| `tests/` | 不需要游戏的单元测试 |

## Scenario DSL

```json
{
  "name": "flat-walk",
  "description": "在默认测试点朝 +Z 走",
  "setup": { "world": "flat" },
  "steps": [
    { "type": "tp", "pos": [0.5, -63, 0.5] },
    { "type": "input", "world": [0, 1], "ticks": 20 },
    { "type": "stop" }
  ],
  "sample": ["pos", "vel", "on_ground", "flags", "yaw", "pitch", "input"],
  "output": "flat-walk"
}
```

| step | 参数 | 说明 |
| --- | --- | --- |
| `tp` | `pos`, `mode` (`server`/`request`/`direct`/`chat`), `settle_ticks` | 默认 `server`，走服务端 registry；`chat` 用游戏 `/tp` |
| `look` | `yaw`, `pitch`, `settle_ticks` | 钉住客户端/服务端 ActorRotation（只影响显示与服务端逻辑） |
| `vel` | `v:[x,y,z]`, `settle_ticks` | 直接写 StateVector 速度 |
| `input` | `world:[dx,dz]` 或 `forward`/`strafe`，`jump`/`sneak`/`sprint`，`ticks` | 推荐 `world`：按世界坐标移动 |
| `jump` | `ticks` | 单次起跳请求 |
| `wait` | `ticks` | 只等待 |
| `stop` | — | 清空输入（客户端+服务端都写 0） |

- `tp` 的坐标是**脚部坐标**（与 HUD/命令一致）；agent 内部会加眼睛高度 `1.6200101`。
- `setup.commands` 若提供且 CUA helper 可用，会在录制前通过游戏聊天执行（用于 `/fill` 等）；
  `--no-chat` 可禁用。普通传送不需要聊天。
- 默认平台 (0.5, -63, 0.5) 约 2.6×4.8 格，长距离直走会撞到边界；撞墙本身也是有效测试。

## 输出

每次运行写两个文件到 `out/`：

- `<name>-<时间>.jsonl`：第一行 meta、每个 tick 一行 sample、最后一行 summary。
- `<name>-<时间>.summary.json`：位移、最大/平均水平速度、接地比例、每个 step 的起止位置。

`sample` 控制 JSONL 里的字段；`flags` 展开为 `{lo, mid, hi}`（3×u64）。

## RPC

Python 侧用 `snake_case` 调用（frida-python 会自动转成 JS `camelCase`）：

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
| `block_at(x,y,z)` | `blockAt` | 读取方块：名字、FNV-1-64 哈希、Block/Legacy 指针 |
| `inspect_registries()` | `inspectRegistries` | 列出所有 registry 的组件/实体 |
| `start_recording(n)` / `stop_recording()` | `startRecording` / `stopRecording` | 逐 tick 采样 |
| `status()` / `diagnose()` | `status` / `diagnose` | 诊断 |

## 已验证（2026-10-18，游戏内实测）

- `getState` 与 HUD 对齐：X/Z 精确一致，`pos.y` 为脚部（StateVector 里是眼睛 = 脚部 + 1.6200101），yaw/pitch 精确。
- 输入：从 (0.5,-63,0.5) 朝 +Z 20 tick 走 **2.2 格**，max 0.1177 格/tick，停止后位移 0。
- 跳跃：脚部 y 从 -63 升到峰值约 -61.83（+1.17），约 5 tick 后落地。
- 服务端传送：客户端/服务端位置同步到目标点，`on_ground=true`。
- 后台：失焦 3 秒 tick 前进 65（20 tps 满速）。
- 方块读取：`--scan -4 4 -4 4` 得到平台表面 `stone`(-64) 为主，混有 `grass_block`/`slime`/
  `sweet_berry_bush`/`rose_bush`/`poppy`/`hopper`/`bed`/`bamboo`/`acacia_fence_gate`，
  另有 2 列 void；平台确实高度不一。
- 客户端写位置（StateVector / ActorSetPositionRequest）实测**无效**，会被服务端覆盖。

## 状态

- [x] 仓库与目录骨架
- [x] frida agent：读状态、tick 采样、输入、世界方向移动、跳跃、服务端传送、方块读取
- [x] driver：scenario JSON、逐步执行、逐 tick 采样、JSONL + summary、`--block` / `--scan`
- [x] 失焦暂停关闭与后台 tick
- [x] 无游戏单元测试（39 项，含 Python↔JS RPC 契约检查）
- [x] 版本指纹自检：`check_binary.py` 5/5 匹配安装目录的 EXE
- [ ] 命令派发 RPC（目前 setup 的 `/fill` 等仍走聊天）
- [ ] GameTest：`spawnSimulatedPlayer` 与真实输入的等价性实验
- [ ] 更多可回归场景（撞墙 / 台阶 / 跳跃距离）

## 测试

```bash
py -3.11 -m unittest discover -s tests -v
py -3.11 driver/check_binary.py
```

## 工具链经验（为什么不再用鼠标点击）

- PowerShell 的 `GetWindowRect` 在 DPI 缩放下返回虚拟化坐标，和实际窗口/截图不在一个坐标系，
  点击会整体偏移。
- Minecraft 对合成鼠标事件基本不响应（Raw Input），即使坐标正确也点不动。
- 可靠路径是 ZCode 原生 CUA helper 的键盘注入与窗口截图；传送最终也不需要 UI，
  走服务端 registry 的 frida RPC。
