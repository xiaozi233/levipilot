# AGENTS.md

给在本仓库工作的 agent 的常驻约定。人类开发者请先读 [`README.md`](README.md) 与
[`docs/architecture.md`](docs/architecture.md)；本文件只写「必须遵守」与「容易踩」。

## 项目是什么

**LeviPilot**：Minecraft Bedrock **1.26.51.01**（LeviLamina 26.51.7）的客户端实验 harness。
一个 C++ mod（`mod/probe/`）+ 命名管道 `\\.\pipe\mcbe-harness` 的 UTF-8 JSONL 协议 +
Python 客户端（`driver/harness/`）。目标是让脚本/LLM 读状态、控玩家、跑逐 tick 实验。

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

# 逆向账本：begin → probe → claim → end → close（账本在 skills/bedrock-re/findings）
py -3.11 driver/landing.py begin --case <slug> --goal "..."
```

`py -3.11` 是硬要求（默认解释器可能没有依赖）。

## 目录地图

| 路径 | 内容 |
| --- | --- |
| `mod/probe/src/mod/` | mod 全部实现：`ProbeMod`（生命周期/tick）、`WorldSession`（世界生命周期）、`ClientDispatch`（主线程投递/SEH）、`PacketTap`（封包 tap）、`Commands`（管道分发/SEH 隔离）、`Transport`（管道/spool）、`Memory`（内存原语）、`Watch`（DR 观察点/崩溃取证）、`RecordRing`（64 B tick 环）、`HookVa`（运行时 detour）、`Ecs`（组件内省）、`Resolve`（版本/签名）、`SignIn`（主菜单登录态）、`Checkpoints`（存档点）、`Input`/`Tasks`/`State`/`Inventory`/`Entities`/`Server`/`World`/`Options`/`Macro`、`Internal`（唯一共享头） |
| `driver/harness/` | Python 客户端：`connection`（连接/超时）/`commands/`（按领域 mixin 组成 `ModClient`）/`facade`（Harness）/`client`（兼容导出）/`protocol`/`errors`/`lab`（实验辅助：回读验证建场、录制会话、逐 tick 拟合）；CLI 为 `cli`（入口/注册表分发）/`cli_parser`（参数）/`cli_commands`（领域执行，各带 `COMMANDS` 声明），以及解码辅助模块 |
| `driver/dev.py` | 启动 + 自动进世界的开发循环 |
| `driver/gen_profiles.py` | 把 `docs/offsets/*.json` 生成 `mod/probe/src/mod/GeneratedProfiles.h`（`--check` 给 CI 用） |
| `driver/parkour.py` | tick 录制、跳跃分段、两次运行对比、attempts |
| `driver/landing.py` | 每轮逆向落盘：`begin/probe/claim/end/close/abandon`；账本在 `skills/bedrock-re/findings/` |
| `scenarios/` | 场景 DSL 的 JSON |
| `skills/bedrock-re/` | 静态优先的逆向 + 实验 skill（取代 `bedrock-client-re` / `reverse-security` / `mcbe-harness-experiments`），**真身、随仓库提交**；`.zcode/skills/bedrock-re` 与 `~/.agents/skills/bedrock-re` 是目录联接 |
| `docs/` | 架构、场景 DSL、跑酷、流体运动（`fluid-movement.md`）、UI 自动化、mod 构建、移植流程（`porting.md`）、静态优先的逆向方法与踩坑（`reverse-engineering.md`）、`offsets/` 版本 profile |
| `docs/notes/` | 证据册与任务提示词（由外部 notes 工作树合并；`re-fluid-12651.md` / `tick-order-12651.md` / `prompts/`） |
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
   移植流程见 `docs/porting.md`。RE 侧的函数/全局角色写 `docs/roles/<version>.json`
   （与 offsets 分开；`unresolved` 不填地址），由 `skills/bedrock-re/roles/resolve.py` 校验。
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
- **移动输入契约（2026-10-28）**：`set_input` / `move` / `jump` 默认把数字状态翻译成
  **当前键位绑定的 Hardware HID 按键沿**：每次从
  `IOptionRegistry::getCurrentKeyboardRemapping()` 读 `key.forward/back/left/right/sneak/jump/sprint`，
  `state.bindings` 暴露实际 key code；疾跑按当前绑定键（现在是 R），未绑定才回退双击前进。
  与真实键盘同 origin；禁止把直接写 `MoveInputComponent` / `MoveInputState`、
  queue/handler 重发、`jumpFromGround` 当默认路径。legacy `mode`/`jump_impulse` 只在
  `input_cfg force=true` 下作对照实验，正常脚本不要用。
- 游泳态（ADF bit57）由**客户端 sprint 状态**门控：深水里前进 + 当前疾跑键
  （`set_input` 自动完成）4–8 tick 置位；只给前进、不给 sprint 进不去。
  **如果实测仍进不去，先查 HID 注入的时序/origin，不要把状态位直写加回来。**
  旧“纯 HID 进不去游泳态”的结论是旧注入组合下的 harness 现象，待用新路径复核。
  `lab.swim_drive()` / CLI `swim --drive` 同样走输入路径；`jump-key` 是拍空格（HID Space）。
- 移动输入由游戏自己的输入管线同步到服务端；不要再在 harness 里手工“客户端+服务端双写”
  来补偿，那会制造正常玩家到不了的状态。
- **`set_input(sprint=True)` 旧实现**（legacy mode 1|2|4）会在 queue/handler 里重发 sprint 按钮，
  并在 1.26.51.01 触发一次 0.42 跳跃；该路径现在只在 `force=true` 下可复现。
  默认路径已不再经过它，回归检查 `sprint_no_jump`。
  实验每一步先 `lab.settle(...)` 验证 `on_ground`/静止/`in_water`/`sprinting` 再注入，
  不要用裸 `sleep` 当前提（`swim_start_still` 也是回归检查）。
- **水里的疾跑延迟 = 记录位 → 输入/疾跑状态 → ADF 写位，约 2 tick**：`record`/`state` 的 `sprint`
  （`Player::isSprinting()`）不是水阻力读的位；ADF bit3 由 `Actor::setSprinting`
  （`sub_14B17C820`，写点 `0x14B17C870`）写入，但中间还有一拍状态链。同一 tick 内
  E14 主 pass 的注册序 ActionSprintTrigger 早于水阻力系统，阻力在写位之后读到**新位**
  （集合追加+正序；调用栈 [实测] 确认主 pass，catchup/correction 不在链上。旧“观察点显示
  阻力写 `vx` 早于写位 ⇒ 读上一 tick”的结论已撤回）。所以 0.9 落在 ADF 写位那一拍，而写位
  比记录位晚约 2 tick：常规手动相位（按键落在采样点后）序列
  `0.01959/0.03528/0.05133`（t3）；早相位（疾跑键在采样前已按住/注入落在采样前）
  `0.01959/0.03723`（t2）。差别是输入落点，不是阻力读旧位——0.03723 不再是纯 harness 假象。
  地面加速的最终模长来自**移动速度属性**（疾跑时
  `Actor::setSprinting` 把共享 modifier `SPRINTING_BOOST` 压进属性队列：amount 0.3f、
  `MULTIPLY_TOTAL`，重算 `(0.3+1)×0.1=0.13`；代码里没有 1.3 常数，早前归给实体定义字段
  `DEFAULT_SPRINT_SPEED_MULTIPLIER` 是错的——那是 AI goal 的 JSON 字段）
  （0.1→0.13，`sub_14289A180` 把属性实例 `+124` 的值拷进移动结构；`sub_14666CEE0` 再写
  `speed*(0.546/friction)^3`），不是原始输入标志。同路径的 `sub_142E11910` 也直接读 ADF bit3
  （`(*adf & 8) == 0` → 表 {0.026,0.02}），但那是 0.02/0.026 的基础加速度字段，不是最终
  0.13 的来源。地面状态 +1 来自速度属性、水状态 +2 来自 ADF 位状态链。
- **`state.input.sprint` 可能是静止锁存的 true**：当前世界里只按 W（HID）或
  `set_input(forward=1, sprint=False)` 都会在第一个移动 tick 进疾跑（0.28 稳态）。
  做走/跑对照前让 `lab.settle(..., input_sprint=False)` 通过，否则走路基线其实是疾跑。
- 跳跃不能只靠输入标志/队列/handler：1.26.51.01 上前 3 tick 位置不动、第 4 tick 一次性补
  V1+V2+V3（约 0.99 格）。起跳沿必须调 `LocalPlayer::jumpFromGround()`；
  `MobJump::setJumpPendingScale` 单独用不解决。水中按 Space 到第一个 `+0.04` 有 2–4 tick
  延迟（输入队列 + 水物理切换），"刚进水跳不起来"是延迟不是门。
- 版本锚点的导入符号是 `jmp [rip+0]` thunk，且指针随 ASLR 变化；签名必须先跟 thunk 到真实
  函数，绝不能把 thunk 里的绝对地址当签名。
- 运行时 hook 的通用 VA detour 只保留 rcx/rdx/r8/r9；float/xmm 参数、向量返回、超过 4 个
  参数或隐藏返回指针的目标必须写 typed detour，否则 origin 会收到错误参数。
  VA 只校验“可执行页”，指令边界由 IDA 侧确认。
- `StateVectorComponent+0x18` 的**原始字段**不是 harness 报的速度：地表行走时它是
  0.546 × 真实位移（实测位移 0.2159 格/tick，`mem_read` 读到 0.1179），站着不动时 vy 仍留着
  −0.0784。`state`/`record` 的 `vx/vy/vz` 来自 `Actor::getVelocity()`，等于下一 tick 的位移
  （液体里与 Δy 1:1），不要再乘 0.546；判速度仍以相邻 tick 位置差为准。
- `pos.y` 是**脚部**；StateVector 里是眼睛（脚部 + 1.6200101）。
- 失焦是否暂停世界由 `options.txt` 的 `pause_menu_on_focus_lost` 决定（置 0 后后台仍满速 tick）；
  raw input 在后台仍可能转视角，由 `_applyTurnDelta` 钩子 + `background_guard` 拦掉。
- `harness.json` 的 `auto_enter` 要求游戏在**前台**（后台进世界会触发鼠标捕获）。
- 脚本运行会锁真实输入：`input_lock {on}`（配置 `lock_input` / `dev.py --lock-input`；selftest 自动上锁）
  丢弃硬件来源键鼠与转向；注入照常，但 `key_event` 的 `via` 决定是否过锁——
  `hid`(Virtual) 与 `hardware`(Hardware+注入标记) 放行，`raw`(Hardware 无标记) 被丢。
  实验脚本建议开头 `client.input_lock(True)`、结束时解锁。
  **配置里的 `lock_input` 只在客户端连着时生效**（连接时上锁、断开自动解锁）：启动即锁会让手动开的
  游戏在主界面点不动按钮（2026-10-25 踩过，`harness.json` 残留 `lock_input:true` 就会中招）。
  当前锁状态看 `state.input.locked`。
- 双击空格飞行只认 `InputOrigin::Hardware`（`via=hid` 只起跳、不翻转 `flying`），
  且每次按下要跨至少 1 tick；用 `lab.double_space_fly()`，不要手搓零长度的 down/up。
  能力门是 `MayFly`，脚本侧用 `togglefly` 封装（带 8 tick 重断言）；切换游戏模式会把它
  重置回该模式默认值（生存/冒险 false、创造 true），模式变了要重新开。
- 客户端 mod 在 `enable()` 里注册游戏内命令会 `0xC0000005`（主界面阶段还没有服务器命令表）：
  必须挂 `ServerCommandRegisterEvent`，并按 `CommandRegistry*` 身份判重（命令表按世界重建）。
  只有服务端侧注册才有意义——能力是服务端权威。
- 请求块里的 `TeleportRequest.clearEffects` 是**共享字段**：`reset` 会写 true，`teleport` 必须显式写回
  false（2026-10-25 修过一次泄漏 —— 做过一次 reset 后，之后每次 teleport 都静默清空所有药水效果，
  表现为"刚挂的抗火/水下呼吸凭空消失"）。`checkpoint_load` 走 `resetPlayer(..., clearEffects=false)`。
- 接收侧封包只能挂 `LoopbackPacketSender::$sendToClient`（两个重载）；`Packet::sendTo*`
  辅助层在单机里实测 0 调用，`NetworkConnection::receivePacket` 也是 0。recv 默认关闭，
  用 `packet_capture {directions:["send","recv"]}` 开；同一 tick 同对象会去重，
  原始投递次数看 `packet_stats.recv_anchors`。
- 改 `docs/offsets/*.json` 后必须跑 `py -3.11 driver/gen_profiles.py`；`GeneratedProfiles.h`
  是提交物，`tests/test_profiles.py` 会比对，忘了生成 CI 直接红。
- 只有带 `"runtime": true` 的 profile 才参与生成；旧 schema（绝对 VA）没有这个标记，
  生成器会跳过。
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
2026-10-24/25 流体复核 + 输入锁 + ECS 线程归属修复轮、2026-10-25 双击空格/`/togglefly` 轮）
全部完成；使用限制在 `mod/README.md`，实现细节与逐项验收数字在 `docs/mod-internals.md`，
执行记录在 git 历史与 `docs/refactoring.md`。当前基线：live selftest **222/222**
（`harness.json` 开 `allow_input_inject`/`allow_selftest_stress`）、离线单测 223/223、
ruff/mypy（driver+tests）全绿。

2026-10-28 mod 核心补充验收：native `--mod-core-only` **3/3**；安装新 DLL 后
`--mod-core-live-only` **33/33**（仅管道与录制，无逆向命令/输入注入/世界修改）。
完整 222/222 是此前现场基线，本轮未重跑默认全套。

任务提示词不在仓库内（本地维护）。

可选的后续结构改善（无遗留功能缺陷，动前先想验证手段）：

- ProbeMod.h 其余字段组按 `ChatCapture`/`PacketTap` 的范式继续子系统化
  （下一个候选：宏管理器 `mMacro*`，跨 5 个 TU）
- `captureState/captureScan`/legacy JSON 录制等「tick 内一请求式 JSON」家族迁到消费者线程
- UseTask 服务端回调超时、chat_history Requested 状态超时自愈、`mEntryInFlight` 看门狗
- `mButtonStates/mHandlerStates` 数组的跨线程写（并入 RequestBlock 或走 client 任务）

> 任务提示词已并入 `docs/notes/prompts/`（历史记录，不是现行规范）；验收标准与结论：
> 使用限制与基线数字在 `mod/README.md`，实现细节与逐项实测数字在 `docs/mod-internals.md`
> （含「实测数字速查」），以后看这两处。

改完任何一项，都要在 `driver/selftest.py` 留一条可复现的检查；使用限制/基线变化补进
`mod/README.md` 的「实测 / 限制」，逐项实测数字补进 `docs/mod-internals.md` 的对应
小节或「实测数字速查」—— 这个仓库的文档是活的，别让它和代码脱节。
