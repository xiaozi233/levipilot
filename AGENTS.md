# AGENTS.md

给在本仓库工作的 agent 的常驻约定。人类开发者请先读 [`README.md`](README.md) 与
[`docs/architecture.md`](docs/architecture.md)；本文件只写「必须遵守」与「容易踩」。

## 项目是什么

**LeviPilot**：Minecraft Bedrock **1.26.51.01**（LeviLamina 26.51.7）的客户端实验 harness。
一个 C++ mod（`mod/probe/`）+ 命名管道 `\\.\pipe\mcbe-harness` 的 UTF-8 JSONL 协议 +
Python 客户端（`driver/harness/`）。目标是让脚本/LLM 读状态、控玩家、跑逐 tick 实验。

`legacy/frida` 分支是旧的 frida 路线（1.26.45.01），**主分支只有文档** `docs/legacy-frida-1.26.45.md`；
不要把它当现行实现，但它的「运行时结论」和「采样口径」仍然是有效知识。

## 常用命令

```bash
# C++ mod（本机 xmake + LLVM22，见 docs/mod-build-environment.md）
cd mod/probe && xmake -j4 -y   # -j4 限制 CPU 占用

# 开发循环：编译 → 重启 exe → 等主菜单 → 直接进世界（不用点 UI）
py -3.11 driver/dev.py run --world "超平坦测试" --build

# 无游戏单测 / 静态检查
py -3.11 -m unittest discover -s tests -v
py -3.11 -m ruff check driver tests && py -3.11 -m mypy

# 需要游戏已进存档
py -3.11 driver/selftest.py
```

`py -3.11` 是硬要求（frida 时代的教训：默认解释器可能没有依赖）。

## 目录地图

| 路径 | 内容 |
| --- | --- |
| `mod/probe/src/mod/` | mod 全部实现：`ProbeMod`（生命周期/tick）、`WorldSession`（世界生命周期）、`ClientDispatch`（主线程投递/SEH）、`PacketTap`（封包 tap）、`Commands`（管道分发/SEH 隔离）、`Transport`（管道/spool）、`Memory`（内存原语）、`Watch`（DR 观察点/崩溃取证）、`RecordRing`（64 B tick 环）、`HookVa`（运行时 detour）、`Ecs`（组件内省）、`Resolve`（版本/签名）、`SignIn`（主菜单登录态）、`Checkpoints`（存档点）、`Input`/`Tasks`/`State`/`Inventory`/`Entities`/`Server`/`World`/`Options`/`Macro`、`Internal`（唯一共享头） |
| `driver/harness/` | Python 客户端：`connection`（连接/超时）/`commands/`（按领域 mixin 组成 `ModClient`）/`facade`（Harness）/`client`（兼容导出）/`protocol`/`errors`/`lab`（实验辅助：回读验证建场、录制会话、逐 tick 拟合）；CLI 为 `cli`（入口/注册表分发）/`cli_parser`（参数）/`cli_commands`（领域执行，各带 `COMMANDS` 声明），以及解码辅助模块 |
| `driver/dev.py` | 启动 + 自动进世界的开发循环 |
| `driver/gen_profiles.py` | 把 `docs/offsets/*.json` 生成 `mod/probe/src/mod/GeneratedProfiles.h`（`--check` 给 CI 用） |
| `driver/parkour.py` | tick 录制、跳跃分段、两次运行对比、attempts |
| `scenarios/` | 场景 DSL 的 JSON |
| `skills/mcbe-harness-experiments/` | 实验手册 skill（建场/录制/输入坑与实验闭环模板），**真身、随仓库提交**；
  本地发现路径 `.zcode/skills/` 是指向它的目录联接（.zcode 已忽略），用户级另有镜像
  `~/.agents/skills/mcbe-harness-experiments/SKILL.md` |
| `docs/` | 架构、场景 DSL、跑酷、流体运动（`fluid-movement.md`）、UI 自动化、mod 构建、移植流程（`porting.md`）、`offsets/` 版本 profile |
| `reverse/` | dmf-mpk 逆向报告与 IDA 导出脚本 |

## 硬约束（改代码前先读这段）

1. **`Internal.h` 是唯一被所有 TU 引用的内部头**；新共享工具放 `probe::detail`。
2. **管道线程绝不直接碰游戏对象**：只设请求标志，服务端操作走 `queueServerTask()`，
   世界/UI 操作走 `queueClientTask()`（游戏平台主线程队列，在主菜单也运行）。
   LL 的 `ClientThreadExecutor` 只随世界 tick 执行，不能用来进世界或等主菜单登录。
3. **需要逐 tick 反馈的逻辑留在游戏线程**（`safeTickPre` / `safeTickPost`），
   不要让 Python 进入反馈环。
4. **热路径纪律**：tick 内、以及任何可能被高频调用的钩子体里，禁止
   `std::string` / `nlohmann::json` / 动态分配 / 新增 mutex。只允许写预分配的定长结构，
   格式化放管道线程或 Python 侧。
5. **错误不穿透游戏 tick**：一切异常变成事件（`internal_error` 等）。注意 MSVC 默认
   `/EHsc` 下访问违例**不是**可捕获异常 —— 新写的 handler 用 `__try/__except` 包住。
6. **版本数据只进 profile**：版本号、锚点签名、结构偏移、组件 id 一律写
   `docs/offsets/<version>.json`（带 `"runtime": true`），再跑
   `py -3.11 driver/gen_profiles.py` 生成 `GeneratedProfiles.h`。`.cpp` 里禁止出现这些常量；
   没有 profile 时功能降级（`version_ok=false`、偏移表为空），不是崩溃。
   移植流程见 `docs/porting.md`。
7. **协议只做增量变更**：`{"event":"hello","protocol":4,"capabilities":[...]}` 是握手；
   升号时旧字段必须继续可用，未知命令仍回 `error/unknown_cmd`。
8. **强能力默认关闭**：任意内存写、任意地址装钩这类能力必须显式开启并留审计日志。

## 加东西的标准步骤

**加一个管道命令**：对应 `Command<领域>.cpp` 加分支（处理后返回 true，含拒绝/参数错误；未识别才返回 false）→
`ProbeMod.h` 加请求字段/方法 → 对应模块实现 → `driver/harness/commands/<领域>.py` 加
`request(..., expected_event)`（超时参数用 `float | None = None`）→
`cli_parser.py` 加参数、`cli_commands/<领域>.py` 加执行分支并把命令名登入该模块的
`COMMANDS`（`tests/test_cli.py` 会校验与 parser 一致）→ `tests/` 加无游戏单测（需要游戏的检查加到 `driver/selftest.py`）。
新增领域时才在 `Commands.cpp::dispatchLine` 接入；它统一负责 JSON 解析、未知命令与请求级异常隔离。

**加一个闭环任务**：`TaskModel.h` 定义定长 `XxxTask` 并加入 `TaskState` →
`CommandTasks.cpp` 完整验证参数后 `startTask()`、回带 `task_id` 的 `xxx_started` →
`Tasks.cpp` 实现 `tickXxx`，只填 `TaskStep` → `TaskEvents.cpp` 格式化状态/结果 →
`commands/tasks.py` 的 `_task_request`/`facade.Harness`/CLI 加阻塞封装 → 扩展 native 模型与 live selftest，
用 `blocks` / `state` **回读验证**。禁止新增独立 optional 任务槽、手写 reset 列表，或在 tick 内格式化任务 JSON。
`TaskController` 锁内回调不得重入控制器或同步等待需要任务锁的线程。

## 已知坑（别重复踩）

- Windows 同步句柄上挂起的 `ReadFile` 会阻塞同一句柄的 `WriteFile` ⇒ 客户端必须用轮询读线程
  （`PeekNamedPipe`），不能用阻塞读。
- `command` 的方块类命令是 deferred，`success=false` 不代表失败，以 `blocks` / verify 为准。
- `GameMode::interact` 在空物品上下文会崩 ⇒ `interact_entity` 只做转向 + 注入使用按钮（best effort）。
- 直接写客户端位置（`StateVector`/`ActorSetPositionRequest`）会被服务端覆盖；位置是服务端权威。
- 朝向必须走命令通道（`/tp @s ~ ~ ~ yaw pitch`）；只改组件会橡皮筋（客户端相机每帧写回）。
- 游泳态（ADF bit57）**只能由真实按键进入**：深水中按住 HID 前进+疾跑键，4–5 tick 置位；
  `set_input` 的合成标志不会触发，松开按键即退出。`lab.swim_drive()` / CLI `swim --drive`
  封装了这条路径；`jump-key` 是拍空格（HID Space）触发跳跃。
- 移动输入要同时写客户端与服务端两份，否则预测/校正互相拉扯。
- 跳跃不能只靠输入标志/队列/handler：1.26.51.01 上前 3 tick 位置不动、第 4 tick 一次性补
  V1+V2+V3（约 0.99 格）。起跳沿必须调 `LocalPlayer::jumpFromGround()`；
  `MobJump::setJumpPendingScale` 单独用不解决。
- 版本锚点的导入符号是 `jmp [rip+0]` thunk，且指针随 ASLR 变化；签名必须先跟 thunk 到真实
  函数，绝不能把 thunk 里的绝对地址当签名。
- 运行时 hook 的通用 VA detour 只保留 rcx/rdx/r8/r9；float/xmm 参数、向量返回、超过 4 个
  参数或隐藏返回指针的目标必须写 typed detour，否则 origin 会收到错误参数。
  VA 只校验“可执行页”，指令边界由 IDA 侧确认。
- `StateVector.vel` 是摩擦前速度，约为真实逐 tick 位移的 0.546 倍；判速度用相邻 tick 位置差。
- `pos.y` 是**脚部**；StateVector 里是眼睛（脚部 + 1.6200101）。
- 失焦是否暂停世界由 `options.txt` 的 `pause_menu_on_focus_lost` 决定（旧 frida 路线实测置 0 后
  后台仍满速 tick）；raw input 在后台仍可能转视角，由 `_applyTurnDelta` 钩子 + `background_guard` 拦掉。
- `harness.json` 的 `auto_enter` 要求游戏在**前台**（后台进世界会触发鼠标捕获）。
- 脚本运行会锁真实输入：`input_lock {on}`（配置 `lock_input` / `dev.py --lock-input`；selftest 自动上锁）
  丢弃硬件来源键鼠与转向，注入照常；实验脚本建议开头 `client.input_lock(True)`、结束时解锁。
- 接收侧封包只能挂 `LoopbackPacketSender::$sendToClient`（两个重载）；`Packet::sendTo*`
  辅助层在单机里实测 0 调用，`NetworkConnection::receivePacket` 也是 0。recv 默认关闭，
  用 `packet_capture {directions:["send","recv"]}` 开；同一 tick 同对象会去重，
  原始投递次数看 `packet_stats.recv_anchors`。
- 改 `docs/offsets/*.json` 后必须跑 `py -3.11 driver/gen_profiles.py`；`GeneratedProfiles.h`
  是提交物，`tests/test_profiles.py` 会比对，忘了生成 CI 直接红。
- `docs/offsets/1.26.45.01.json` 是旧 schema（绝对 VA），没有 `"runtime": true`，
  生成器会跳过；不要把它改成 runtime。
- `allow_version_mismatch` 只是移植开关：没有 profile 校验，偏移可能读错；日常配置保持关闭。
- 硬件观察点只记录**装过 DR 的线程**；其他线程上的 DR6 事件计入 `watch_list.foreign_hits`。
  旧线程遗留的 DR 曾导致服务端线程被误记为写 tick 变量。
- 主菜单/世界列表/暂停菜单/对话框仍需桌面 UI 操作（见 `docs/ui-automation.md`），
  但日常开发循环不需要。
- tick 记录会同时写入 `<modDir>/spool-<pid>.jsonl`（100 ms / 64 KB 刷盘）；连接时不再清空队列，
  而是先刷 spool 再补发。崩溃取证走 VEH 写 `crash-<pid>-<tick>.dmp` + `crash-<pid>.txt`。
- 请求级 AV 由 `dispatchWithSeh` 转成 `internal_error`；不要把会崩的代码放到 tick 线程，
  那里没有同样的保护。
- 键位事件优先走 `HIDController::onKeyDown(code, InputOrigin::Virtual|Hardware)`（`ll::service::getAppPlatform()
  → getPlatformShim() → Platform_GameCore::mHIDController`）；它才会触发 LL `KeyInputEvent` 和绑定。
  `Keyboard::_inputs().push_back(KeyboardAction)` 也能改槽位/开背包，但不发 LL 事件，只作对照路径。
- 暂停菜单不响应注入的按键（Esc/Enter 都无效），退出暂停仍需桌面点击 Resume；
  背包/聊天（`/`）可以开，背包用 Esc 回到 hud。
- `mouse_event action=move dx=...` 能转向；左/右键只到 `MouseInputEvent` 层并增加计数，
  在 1.26.51.01 上没有触发世界攻击（攻击绑定不在这一层），不要用它代替 `click`。
- 真实鼠标会转动相机并让宏回放落点漂移；`background_guard` 只拦后台转向，selftest 的宏
  确定性检查在检测到相机被外部移动时会标记 skipped。
- 玩家 StateVector 在集成服务端线程上写；观察用 `watch_set(thread="both")`。
  `thread="all"` 已被 mod 显式拒绝（`thread_all_unsupported`；历史上装 100+ 线程会搞崩游戏）。
- 按 runtime id 查 ECS 得到的是服务端 Actor；客户端 context 用 `state.context`，
  再走 `ecs_context`。不要在 tick 线程枚举客户端 registry。
- `xmake` 默认吃满所有核心，构建用 `xmake -j4`；只改 `.cpp` 是增量，改 `Internal.h`/`ProbeMod.h`
  会全量重编（约 2–3 分钟）。

## 当前状态

主线功能与历次修复/加固轮（T1–T9、F1–F10、H0–H4、G1–G6、2026-10-24 审阅加固与结构轮、
2026-10-24/25 流体复核 + 输入锁 + ECS 线程归属修复轮）全部完成；使用限制在 `mod/README.md`，实现细节与逐项验收数字在 `docs/mod-internals.md`，
执行记录在 git 历史与 `docs/refactoring.md`。当前基线：live selftest **204/204**
（`harness.json` 开 `allow_input_inject`/`allow_selftest_stress`）、离线单测 189/189、
ruff/mypy（driver+tests）全绿。

任务提示词不在仓库内（本地维护）。

可选的后续结构改善（无遗留功能缺陷，动前先想验证手段）：

- ProbeMod.h 其余字段组按 `ChatCapture`/`PacketTap` 的范式继续子系统化
  （下一个候选：宏管理器 `mMacro*`，跨 5 个 TU）
- `captureState/captureScan`/legacy JSON 录制等「tick 内一请求式 JSON」家族迁到消费者线程
- Transport 写阻塞看门狗（客户端存活但不读时管道线程永久阻塞）与 `disable()` 的
  CancelSynchronousIo TOCTOU（改循环 cancel-then-wait）
- `record_start` 打断在飞 `recordTick` 的世代竞态（可能混入上一会话的行）
- UseTask 服务端回调超时、chat_history Requested 状态超时自愈、`mEntryInFlight` 看门狗
- `mButtonStates/mHandlerStates` 数组的跨线程写（并入 RequestBlock 或走 client 任务）

> 任务提示词不随仓库分发；验收标准与结论：使用限制与基线数字在 `mod/README.md`，
> 实现细节与逐项实测数字在 `docs/mod-internals.md`（含「实测数字速查」），以后看这两处。

改完任何一项，都要在 `driver/selftest.py` 留一条可复现的检查；使用限制/基线变化补进
`mod/README.md` 的「实测 / 限制」，逐项实测数字补进 `docs/mod-internals.md` 的对应
小节或「实测数字速查」—— 这个仓库的文档是活的，别让它和代码脱节。
