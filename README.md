# mcbe-harness

Minecraft Bedrock **1.26.45.01** 的实验驱动 harness。

目标：**实验控制**不再依赖 Computer Use / 合成键鼠输入，通过 frida RPC 和游戏内 ECS 组件
控制实验、逐 tick 采样，把“操作游戏”换成“调用游戏”。**游戏 UI**（启动、进存档、菜单、
对话框）仍需要真正的桌面操作，那部分走电脑操作 helper 的键盘路径，见 skill
`mcbe-desktop-control`。

## 约束

- 游戏安装目录只读，任何构建产物、脚本、日志都不写回安装目录：
  `%APPDATA%\levilauncher.exe\versions\1.26.45.01`
- 实验控制不发合成键鼠输入，也不抢前台窗口。场地准备用 `/` 命令时，是把 `WM_CHAR`
  消息直接投递给游戏窗口（游戏在后台、未聚焦也能收到），不是模拟设备输入。
- UI 操作不算实验控制，可以用合成键鼠（helper 的 `pressKeyGlobal`/`holdKeyGlobal`），
  但它会抢前台焦点；实验中的位移/朝向一律仍然走 RPC。
- 观测走内存/钩子，不靠截图；截图只用于 UI 操作确认和最终视觉验收。
- 客户端与 BDS 必须同为 1.26.45.01，否则行为常数可能不同。

## 环境

- Python 3.11（`py -3.11`），已装 frida 17.18.0。
- Python 3.14 默认解释器没有 frida，不要用它跑 driver。
- 地址与组件布局见 [`docs/offsets-1.26.45.01.md`](docs/offsets-1.26.45.01.md)。

## 快速开始

游戏需已启动并进入世界。先铺测试平台，之后所有场景都在平台中心 (0.5, -49, 0.5) 跑。

```bash
py -3.11 driver/check_binary.py             # 不连游戏：校验 EXE 与 agent 指纹
py -3.11 driver/run.py                      # 状态 / registry / 签名检查
py -3.11 driver/run.py --diagnose           # 组件、双 registry 诊断
py -3.11 driver/run.py --scenario scenarios/setup-platform.json   # 铺 65x65 测试平台
py -3.11 driver/run.py --probe              # 传送 + 朝 +Z 走 20 tick，验证全链路
py -3.11 driver/run.py --scenario scenarios/wall-stop.json        # 撞墙停住回归
py -3.11 driver/run.py --scenario scenarios/example.json
py -3.11 driver/run.py --command "setblock 3 -49 3 stone"   # 直接发一条命令（不抢焦点）
py -3.11 driver/run.py --block 0 -50 0      # 读取单个方块名
py -3.11 driver/run.py --scan -4 4 -4 4     # 扫描区域，输出每列表面方块
py -3.11 driver/run.py --scenario scenarios/teardown-platform.json  # 清场
py -3.11 driver/run.py 12345 --probe        # 按 PID
```

### 游戏 UI：启动 / 进存档 / 退档 / 走路

实验之外还需要真人式操作（菜单、世界列表、对话框）时走电脑操作 helper。
**按按钮的正确姿势是"悬停 + 回车"**：合成鼠标的**悬停**生效并接管键盘焦点，
而**点击会被游戏丢弃**；所以流程是截图 → 在图上读目标像素 → `point` 悬停 → `Return`。

```bash
cua() { ELECTRON_RUN_AS_NODE=1 D:/ZCode/ZCode.exe driver/native/cua.js \
  "D:/ZCode/resources/tools/cua-helper/build/Release/ax_native.node" "$@"; }
cua shot  out/a.png     # 窗口级截图；所有坐标都在这张图上数
cua point 795 480       # 悬停到截图像素 (795,480)（气泡/高亮确认目标对）
cua key   Return        # 回车激活它
cua hold  w 1500        # 游戏内走路兜底（1.5 s ≈ +6.69 格）
cua cursor              # 读光标屏幕坐标（排查坐标问题时用）
py -3.11 driver/run.py --wait 30            # 进世界后重新 attach frida
py -3.11 driver/native/check_cua_pipes.py   # 官方 computerUse 路径是否已接上
py -3.11 driver/native/crop_png.py out/a.png out/a_zoom.png 300 690 640 780 3   # 局部放大复核
```

`point`/`click` 收的是**截图坐标**（自动换算屏幕物理像素，并回读 `cursorPoint` 打印
`offset`），所以窗口挪位置、缩放变了都不用改数字。
（`ELECTRON_RUN_AS_NODE=1` 必须是真正的环境变量前缀，放进字符串变量会 `command not found`。）

进存档的完整流程、像素参考表、坐标标定数据和排查顺序见 skill
**`mcbe-desktop-control`**（`~/.agents/skills/mcbe-desktop-control/SKILL.md`）。

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
   注入的输入能复现**原版步行速度**：逐 tick 位移收敛到 **0.2158 格/tick**（4.317 m/s），
   strafe/forward 对称，重复运行一致。
   ⚠️ 但 `getState().vel` **不能当速度用**：它存的是摩擦前的速度，正常地面上只有
   真实逐 tick 位移的 0.546 倍（0.546 = 0.6 滑度 × 0.91）。速度一律用相邻两 tick 的
   位置差算，`output.py` 已经这么做了（`speed_basis: "pos_delta"`，
   同时保留 `max_vel_h_speed` 便于对照）。
4. **朝向必须走命令通道**：写 `ActorRotation` 组件不管用——客户端相机每帧把它写回，
   服务端于是按**真实相机朝向**解释移动输入，两侧世界方向分叉，表现为每走一格就被
   服务端校正一次（橡皮筋）。用 `/tp @s ~ ~ ~ <yaw> <pitch>` 才能同时改掉两侧的
   权威朝向；实测之后客户端与服务端逐 tick 完全一致。`look` 步骤在有命令通道时默认走
   这条路（`mode: "command"`），`mode: "memory"` 只改组件并会给出警告。
   需要指定世界方向的移动用 `move_world(dx, dz)`，它按服务端 yaw 反算 forward/strafe。
5. **失焦自动暂停已关闭**：
   `options.txt` 里 `pause_menu_on_focus_lost` 已改为 `0`（两个用户目录都改了）。
   实测游戏失焦 3 秒仍前进 65 tick，可以后台运行。
6. **方块读取**：hook 方块查询包装 `0x144767E30` 捕获查询上下文，缓存 accessor
   （`[ctx+456] → 虚表+96`）；`accessor 虚表+16(BlockPos) → Block*`；
   `Block+0x68 → BlockLegacy`，`+0xE0` 是名字的 FNV-1-64，`+0xE8` 是完整名字 `std::string`。
   首次需要一次移动/碰撞来捕获 accessor，之后 `block_at`/`--scan` 可直接查询。
7. **命令通道（场地准备）**：`driver/postmsg.py` 把按键与字符作为窗口消息投递给游戏窗口
   （`WM_KEYDOWN`/`WM_CHAR`，`lParam` 带上 `MapVirtualKeyW` 算出的扫描码，SDL 靠它映射按键）。
   打开聊天框用 `/` 键，之后按字符投递、回车提交。**游戏不需要在前台**：实测
   `foreground: False` 时 `/setblock` 生效并被 `blockAt` 读到。
   原生 CUA helper（会抢焦点）保留为回退：`--command-channel cua`。
8. **UI 操作（菜单 / 世界列表 / 对话框）走电脑操作 helper**：`driver/native/cua.js`
   现在有 `info / shot / cursor / point / click / key / hold / type` 七个动作。实测三条硬结论：
   - 合成鼠标**悬停生效**（按钮高亮、出 tooltip）**并且会接管键盘焦点** ⇒
     把光标 `point` 到目标上再按 `Return` 就能"按"到它（两次独立确认）。
   - 合成鼠标**点击被游戏丢弃**（`clickAtPoint` 返回成功但屏幕毫无跳转），所以别用点击。
   - `point`/`click` **只在菜单里有效**：世界内游戏捕获鼠标、自己接管光标（实测 offset
     从 `[0,0]` 变成 `[-743,48]`），那段位移还会被当成转视角。要操作 UI 先开菜单。
   - 坐标是**截图坐标**：`point`/`click` 自动换算屏幕物理像素（实测偏移 +9px 左边框），
     还会回读 `cursorPoint` 打印 `offset` 供核对；PowerShell 截屏受 DPI 虚拟化影响，别用。
   冷启动游戏、主菜单进存档、暂停菜单保存并退出、游戏内走路（`hold w`）的完整流程，
   以及 ZCode 官方 in-session bridge（`agent.computerUse`）为何在这台机器上不可用，
   都记在 skill **`mcbe-desktop-control`**（`~/.agents/skills/mcbe-desktop-control/SKILL.md`）。

## 目录

| 路径 | 用途 |
| --- | --- |
| `agent/` | frida agent，暴露 RPC（ECS 读写 + tick hook） |
| `driver/` | Python 场景驱动器（session / scenario / runner / output） |
| `driver/postmsg.py` | 命令通道：窗口消息投递，不抢焦点（默认） |
| `driver/native/` | ZCode 电脑操作 helper 的封装（`info/shot/click/key/hold/type`）：命令通道回退 + 菜单/进存档等 UI 流程；流程细节见 skill `mcbe-desktop-control` |
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
| `look` | `yaw`, `pitch`, `mode` (`command`/`memory`), `settle_ticks` | 默认 `command`：`/tp @s ~ ~ ~ yaw pitch` 改权威朝向 |
| `vel` | `v:[x,y,z]`, `settle_ticks` | 直接写 StateVector 速度 |
| `input` | `world:[dx,dz]` 或 `forward`/`strafe`，`jump`/`sneak`/`sprint`，`ticks` | 推荐 `world`：按世界坐标移动 |
| `jump` | `ticks` | 单次起跳请求 |
| `wait` | `ticks` | 只等待 |
| `stop` | — | 清空输入（客户端+服务端都写 0） |

`setup` 字段：

| 字段 | 说明 |
| --- | --- |
| `commands` | 录制前执行的 `/` 命令（场地搭建），走命令通道 |
| `verify` | `[{"pos": [x,y,z], "block": "minecraft:stone"}]`：命令跑完后用 `blockAt` 校验 |
| `teardown` | 跑完（含中途失败）执行清场命令，避免污染世界 |
| `strict` | `true` 时命令缺失/校验不过直接报错，而不是只记 warning |

- `tp` 的坐标是**脚部坐标**（与 HUD/命令一致）；agent 内部会加眼睛高度 `1.6200101`。
- 命令通道不可用时用 `--command-channel none`；`setup.verify` 会因此记 warning（或 strict 报错）。
- 测试平台由 `scenarios/setup-platform.json` 铺在 y=-50（地表 -49，65×65），
  `scenarios/teardown-platform.json` 清掉。游戏世界底部是 y=-64（1.18+ 的地板），
  原生平台高度不一、宽度不够跑长距离，所有场景都应先铺平台再跑。

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
- **命令通道不抢焦点**：游戏 `foreground: False` 时 `/setblock 2 -60 2 stone` 生效，
  `block_at` 读到 `minecraft:stone`，随后 `/setblock ... air` 还原。
- **朝向**：`/tp @s ~ ~ ~ 0 0` 之后客户端与服务端 yaw 都稳定为 0（不再被相机回写），
  forward=1 → 两侧同步 +Z、strafe=1 → 两侧同步 +X、yaw=90 → 两侧同步 -X，
  逐 tick 位置吻合，橡皮筋消失。之前只写组件时两侧差一个 yaw 帧（58.97°）。
- **撞墙停住（`scenarios/wall-stop.json`）**：在测试平台 x=6 处砌 3 格高的墙，
  从平台中心朝 +X 走 80 tick：位移 **(+5.200, 0, 0)** 笔直，最大速度 **0.2159 格/tick**，
  接地 100%，停在 x=5.7（墙 6 − 玩家半宽 0.3）后速度归零，无丢帧。
  跑完 teardown 拆墙并把玩家放回平台中心（teardown 在记录窗口之外，不污染数据）。
- **移动速度**：四种输入（local/world × strafe/forward）逐 tick 位移都是
  首 tick 0.098 → 收敛到 **0.2158 格/tick = 4.317 m/s**，正是原版步行速度；
  两轴对称、两轮重复一致，客户端与服务端位置吻合。
- **跳跃（`scenarios/jump-distance.json`）**：全速起跳，滞空 4-5 tick，
  峰高 **+1.166 格**（脚部 -49.000 → -47.834），落地速度归零。
- **撞台阶（`scenarios/step-up.json`）**：x=4 处一格高的台阶，全速助跑起跳
  （起跳点约 x=3.2）**跳不上去**：滞空时箱子已越过台阶边缘（x=3.91），
  但下落时被台阶侧面挤出，落在旁边平台并被挡住（x=3.700）。
  滞空水平只推进 **0.630 格**（Bedrock 的低空中控制，见「未解决」）。
- 跳跃：脚部 y 从 -63 升到峰值约 -61.83（+1.17），约 5 tick 后落地。
- 服务端传送：客户端/服务端位置同步到目标点，`on_ground=true`。
- 后台：失焦 3 秒 tick 前进 65（20 tps 满速）。
- 方块读取：`--scan -4 4 -4 4` 得到原生平台表面 `stone`(-64) 为主，混有 `grass_block`/`slime`/
  `sweet_berry_bush`/`rose_bush`/`poppy`/`hopper`/`bed`/`bamboo`/`acacia_fence_gate`，
  另有 2 列 void；平台确实高度不一、宽度不够。测试平台已铺在 y=-50。
- 客户端写位置（StateVector / ActorSetPositionRequest）实测**无效**，会被服务端覆盖。

## 未解决

- **sprint / sneak 未注入**：两者是服务端状态位，目前只记录请求并返回 warning。
  想跑"跑跳上一格台阶"这类原版动作就得先补这个（Bedrock 的 walk-jump 空中推进只有
  ~0.14 格/tick，跳着走比走路还慢，所以 walk-jump 上不去一格台阶——这一点实测与
  原版一致，不是注入缺陷；逐字段对比见 `docs/offsets-1.26.45.01.md` §4）。
- **命令派发 RPC**：进程内直接发 `CommandRequestPacket`，彻底不依赖窗口消息。
- **GameTest**：`spawnSimulatedPlayer` 与真实输入的等价性实验。

## 采样口径（易踩的坑）

- **`vel` 不是速度**：`StateVector.vel` 是摩擦前速度，正常地面上约为真实逐 tick 位移的
  0.546 倍（0.546 = 0.6 滑度 × 0.91）。判速度用相邻 tick 位置差；`vel` 只在需要
  "游戏内部速度量"时参考。
- **tp 与 teardown 不是位移**：跨 step 的位移、`tp`/`vel` 步骤内的位移都不计入速度；
  记录窗口在 teardown 之前关闭。
- **命令通道后有一记卡顿（已定位、已规避）**：聊天命令执行后约 1.5 s，整个进程会卡住
  ~1.2 s 然后一次性补跑约 24 个 tick，期间玩家的位置不被积分（表现为"输入在动、人不动、
  tick 号跳号"）。它总落在 look 步骤之后的那个限时 step 里，所以**朝向命令要放进
  `setup`，并让 setup 之后空转 60 tick 把卡顿吸收掉**——runner 现在会自动这么做
  （有命令通道时 setup 之后 `wait_ticks(60)`）。怀疑是命令把世界标脏后触发的存档，
  未证实。
- **`pos.y` 是脚部**（StateVector 里存的是眼睛 = 脚部 + 1.6200101）。
- **`tp` 的 `mode=server` 写的是服务端 registry 的 `ActorSetPositionRequest`**，
  客户端会被同步；直接写客户端位置无效。
- **`look` 用 `mode=memory` 只改组件**，服务端仍按真实相机朝向走，会橡皮筋。

## 状态

- [x] 仓库与目录骨架
- [x] frida agent：读状态、tick 采样、输入、世界方向移动、跳跃、服务端传送、方块读取
- [x] driver：scenario JSON、逐步执行、逐 tick 采样、JSONL + summary、`--block` / `--scan`
- [x] 失焦暂停关闭与后台 tick
- [x] 命令通道：窗口消息投递（不抢焦点）+ `--command` / `--command-channel`
- [x] `setup.commands` / `setup.verify` / `setup.teardown` / `setup.strict`
- [x] 朝向走命令通道（`look mode=command`），修掉移动方向的橡皮筋
- [x] 65×65 测试平台（`setup-platform.json` / `teardown-platform.json`）
- [x] 首个回归场景：撞墙停住（`wall-stop.json`）
- [x] 回归场景：跳跃距离（`jump-distance.json`）、撞台阶（`step-up.json`）
- [x] 命令通道卡顿定位 + setup 后自动吸收（`wait_ticks(60)`）
- [x] 采样完整性：录制缓冲按实际开销放大（不再因前置步骤耗时丢样本）
- [x] 无游戏单元测试（76 项，含 Python↔JS RPC 契约检查）
- [x] 版本指纹自检：`check_binary.py` 6/6 匹配安装目录的 EXE
- [x] 速度口径修正：速度按位置差计算，`vel` 只作参考
- [ ] 滞空输入标定（见「未解决」）——挡着台阶/跳跃距离类实验的准确性
- [ ] 命令派发 RPC（进程内发 `CommandRequestPacket`，彻底不依赖窗口消息）
- [ ] GameTest：`spawnSimulatedPlayer` 与真实输入的等价性实验

## 测试

```bash
py -3.11 -m unittest discover -s tests -v
py -3.11 driver/check_binary.py
```

## 工具链经验（为什么不再用鼠标点击）

- PowerShell 的 `GetWindowRect` 在 DPI 缩放下返回虚拟化坐标，和实际窗口/截图不在一个坐标系，
  点击会整体偏移。
- Minecraft 对合成鼠标事件基本不响应（Raw Input），即使坐标正确也点不动；
  但**文本输入**走的是 `WM_CHAR`，直接投递窗口消息可以在不聚焦的情况下打字。
- 可靠路径是窗口消息投递（默认）或 ZCode 原生 CUA helper 的键盘注入（回退）；
  传送、输入、朝向最终都不需要 UI，走 frida RPC / 服务端命令。

## 附：IDA MCP 连不上时的排查结论（2026-10-18）

ZCode 的 `ida-pro-mcp` 不是直连 IDA，而是启动一个**长驻的 stdio 代理子进程**
（`ida_pro_mcp/server.py`），由它用 HTTP 转发到 `127.0.0.1:13337` 的 supervisor。
当工具调用报「Did you run Edit -> Plugins -> MCP?」时，报错其实是那个**代理子进程**
自己生成的（异常发生在 `http.client` 里），后端往往是好的：

- 直连 supervisor（`POST http://127.0.0.1:13337/mcp`）正常，说明后端没挂；
- 杀掉卡住的代理子进程（`taskkill /PID <pid> /F`）后 ZCode 会自动拉起新的，MCP 立刻恢复；
- 代理子进程**只在启动时**解析一次目标端口（默认 13337），所以端口变了它不会跟。
- 另一类失败是 supervisor 侧的**会话空闲回收**（worker 约 10 分钟无人访问就退出）：
  之后 `idb_list` 会显示 0 个会话，带库的工具报 `database is required` 或
  `Session not found`。重新 `idb_open`（`run_auto_analysis: false`）即可，不要重跑自动分析。
- 这套 supervisor 要求每次工具调用带 `database=<session_id>`；
  ZCode 装的工具 schema 里没有这个参数，所以库相关工具走直连
  `mcp.py <tool> '{"database":"mcbr", ...}'` 更省事（ZCode 路径只有 `idb_list`/`idb_open` 能用）。
