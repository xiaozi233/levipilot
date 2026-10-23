# 架构与扩展指南

本项目分三层：游戏内的 C++ mod、JSON Lines 管道协议、Python 客户端。目标是让脚本/LLM
用少量命令控制玩家并读取状态，同时把需要逐 tick 反馈的逻辑留在游戏线程里。

```
scenario DSL / LLM / shell
          │
   driver/harness (Python)
   CLI 入口 → 参数 / 领域处理器 → commands → connection → protocol
   facade ────────────────────↗
          │  \\.\pipe\mcbe-harness  (UTF-8 JSON lines, protocol 4)
   mod/probe (LeviLamina client mod)
   Transport → Commands → Command<领域> → 请求 / Tasks / State / ...
          │
   Minecraft Bedrock 1.26.51.01
```

## C++ 模块

`mod/probe/src/mod/` 下每个文件只负责一类事情：

| 文件 | 职责 |
| --- | --- |
| `ProbeMod.h` | `ProbeMod` 的公开/私有接口、任务结构、所有成员状态 |
| `ProbeMod.cpp` | 生命周期（load/enable/disable）、tick 前后编排、钩子注册、错误上报 |
| `Internal.h` / `Internal.cpp` | 共享工具：数学、JSON 解析、物品/方块辅助、输入快照 |
| `Input.cpp` | 移动/视角注入、按键事件、左右键动作、macro 的单 tick 应用 |
| `InputEvents.cpp` | HID/键鼠事件注入队列与事件计数 |
| `Tasks.cpp` | `navigate` / `mine` / `place` / `use` 状态机与 `task_status` |
| `State.cpp` | `state`、`buttons`、`blocks` 快照 |
| `Inventory.cpp` | 背包快照与写入、give/clear、快捷栏选择 |
| `Entities.cpp` | 附近实体列表、实体信息/攻击/交互/看向 |
| `Server.cpp` | 服务端权威传送 |
| `World.cpp` | 世界列表、`enter_world` / `leave_world`、登录门、世界事件 |
| `SignIn.cpp` | 主菜单 Xbox Live 登录态查询（OreUI → MainMenuScreenModel） |
| `Checkpoints.cpp` | 命名脚部坐标存档、JSON 原子持久化 |
| `Options.cpp` | 运行时改选项并写回 `options.txt` |
| `Macro.cpp` | macro 文件加载/保存/回放记录 |
| `Commands.cpp` | 单次 JSON 解析、领域分发、未知命令、请求级 SEH/异常隔离 |
| `CommandDiagnostics.cpp` | 心跳、状态、版本诊断、panic、spool 与故障/开销自检命令 |
| `CommandInspection.cpp` | 内存、watch、hook 命令的校验与分发 |
| `CommandInput.cpp` | 移动/视角、键鼠、屏幕、点击与飞行命令 |
| `CommandPackets.cpp` | 封包采集/注入、权限与命令开关、符号解析命令 |
| `CommandRecording.cpp` | tick/二进制记录及 macro 命令 |
| `CommandQuery.cpp` | 玩家/方块/实体、背包与 ECS 查询和请求投递 |
| `CommandWorld.cpp` | 服务端命令、世界、选项、传送与 checkpoint 命令 |
| `CommandTasks.cpp` | 闭环任务参数解析与安装；逐 tick 实现仍在 `Tasks.cpp` |
| `Transport.cpp` | 命名管道服务端、写队列、`queueServerTask`、spool、诊断日志 |
| `Memory.cpp` | 内存读写、地址校验、Block/Legacy 偏移表与结构化 probe |
| `Watch.cpp` | 调试寄存器观察点（DR0–DR3）、VEH 命中环、崩溃 minidump |
| `RecordRing.cpp` | 64 字节定长 tick 环、降采样、jsonl/hex dump |
| `HookVa.cpp` | 运行时符号/VA detour、dry_run 校验、命中环 |
| `Ecs.cpp` | EnTT registry 枚举、组件地址/stride/原始字节 |
| `Packets.cpp` | send/recv 身份 tap、序列化白名单与命令包模板复用 |
| `Overhead.cpp` | tick/hook 开销采样与分位数、自检忙等 |
| `Resolve.cpp` | exe 版本指纹、符号锚点 + 24 字节签名、offsets.json 覆盖 |

`Internal.h` 是唯一允许被所有 TU 引用的内部头；它集中引入 Bedrock 头文件，避免每个
翻译单元各自维护一份包含列表。新增共享工具时放在 `probe::detail`。

领域处理器返回 `true` 表示已处理（包含拒绝或参数错误），只有不认识该命令才返回 `false`。
入口按顺序短路调用；输入权限检查和实际处理在同一个领域内。所有处理器仍受外层
`dispatchWithSeh` 保护，服务端回调继续由 `queueServerTask` 隔离。

## 线程模型

- **游戏线程**：`LocalPlayer::$normalTick` 钩子。`safeTickPre` 在 origin 前注入输入/推进任务，
  `safeTickPost` 在 origin 后采样状态。
- **管道线程**：`serverLoop` 阻塞在 `ConnectNamedPipe`/`PeekNamedPipe` 上，负责读写 JSON 行和
  分发命令。它不直接调用游戏对象，只设置请求标志或调用 `queueServerTask`。
- **服务端线程**：通过 `ServerInstance::queueForServerThread` 执行传送、命令、背包写入和使用物品。
  所有回调由 `queueServerTask` 包装，异常不会穿过游戏服务端 tick。
- **spool 线程**：每 25 ms 检查一次，把积压的记录按“100 ms 或 64 KB”批量写进
  `spool-<pid>.jsonl`；`world_left` / `panic` / `stop` / `disable` 会强制刷盘。
  启动时同 PID 的旧文件会改名为 `spool-<pid>-<n>.jsonl`，单文件超过 64 MiB 也会轮转；
  swap 与写文件由 `mSpoolWriteMutex` 串行化。它只碰文件和缓冲区，不碰任何游戏对象。
- **VEH**：`AddVectoredExceptionHandler` 同时负责观察点命中和进程级崩溃取证。请求分发期间的
  结构化异常由 `dispatchWithSeh` 转成 `internal_error`，不会写 minidump。

输入、视角、传送合并在 `RequestBlock`：写端串行更新，tick 用 seqlock 快照读取；
其他跨线程状态使用互斥量（任务、背包等请求）或原子量（各种 `*Requested`、`mRunning`）。
任务还有一个 `mTaskEpoch`：管道线程取消/替换任务时递增，tick 线程只在 epoch 未变时写回，
避免“已取消的任务复活”。

## 任务生命周期

任务只在客户端 tick 内推进，管道往返不进入反馈环。以 `navigate` 为例：

1. 命令写入 `mNavigateTask`，清空手动输入和 macro，返回 `navigate_started`。
2. 每个 pre-tick 计算距离/方位角，限幅转向、给前进量、卡住时跳一次。
3. 到达/超时/取消时清空任务，发一次 `navigated`，再由调用方决定下一步。

`mine` 用 `startDestroyBlock` + `continueDestroyBlock`，`place` 用真实 `traceRay` +
`useItemOn` 并在两 tick 后回读方块，`use` 在服务端玩家上执行 `Item::use`
（食物再走 `useTimeDepleted` + `Player::useItem`），完成后 `sendInventory(true)`。
这些选择都是为了让结果在服务端落库，而不是只改客户端预测。

## 管道协议

- 命名管道 `\\.\pipe\mcbe-harness`，字节模式，UTF-8 JSON Lines。
- 连接后 mod 先发 `{"event":"hello","protocol":4,"capabilities":[...],"spool":...,
  "replay_rows":N,...}`；客户端验证 hello 并保存协议号。新增字段是增量的，旧客户端仍可用。
- 请求是 `{"cmd": "...", ...}`；响应/事件用 `event` 区分。未知命令回
  `{"event":"error","reason":"unknown_cmd"}`。
- 服务端写入按 `WriteFile` 的实际返回值循环，避免半行 JSON；队列上限 20000，溢出计数在
  `status.queue_dropped`。
- 命令/事件的完整列表见 [`../mod/README.md`](../mod/README.md)。

## Python 包

```
driver/harness/
  __init__.py   # 公开导出
  errors.py     # ModPipeError / ModTimeout / ModDisconnected / ModProtocolError
  protocol.py   # 管道名、LineDecoder、kernel32 PeekNamedPipe 绑定
  client.py     # 兼容导出 ModClient / Harness
  connection.py # PipeConnection：连接、轮询读取、超时、重连与完整写入
  commands.py   # ModClient：命令 payload、响应事件与工作流
  facade.py     # Harness：脚本便捷方法、集合解包与组合操作
  hooks.py      # hook 命中解码、float bits
  memory.py     # 地址/hex 解析、FNV 辅助
  packets.py    # packet id 名称、字节布局解码
  record.py     # 64 字节记录解码、flags、方块名哈希
  spool.py      # 增量、可容忍轮转/半行的 spool 读取器
  watch.py      # watch 命中解码与可续读游标
  cli.py        # 稳定入口：离线命令、连接生命周期、错误与退出码
  cli_parser.py # argparse 定义；保留参数默认值、命令和帮助顺序
  cli_commands/ # diagnostics/input/inspection/packets/query/world/tasks/recording
driver/transport_mod.py  # 兼容门面，旧导入和脚本入口继续可用
driver/dev.py             # 直接启动 exe + 自动进世界的开发循环
driver/parkour.py         # tick 录制、跳跃分段、运行对比、attempts
```

`ModClient` 用独立轮询线程读管道：Windows 同步句柄上挂起的 `ReadFile` 会阻塞同一个句柄的
`WriteFile`，所以不能用一个阻塞读线程。请求带超时；超时会关闭连接，避免流错位。
`ModClient` 继承连接实现，`Harness` 通过组合使用 `ModClient`；依赖保持单向。
包根、`harness.client` 和 `transport_mod` 导出的两个类是同一对象，旧代码无需更改导入。
场景适配器直接依赖 `driver.harness`，不再经过包含 CLI 的历史入口。

## 世界与设置

- `worlds` 直接从 `IMinecraftGame::getLevelListCache()` 读缓存；实测在管道线程调用即可。
- `enter_world` 用 `LevelSummary` 构造 `LevelSettings`，再调纯虚的
  `IGameServerStartup::startLocalServerAsync`（vtable，不需要导入符号）。
  `startLocalServerAsyncFromSummary` 没有导出，不能直接链接。
- 世界事件 `ClientStartJoinLevelEvent` / `ClientJoinLevelEvent` / `ClientExitLevelEvent` /
  `ClientCancelJoinLevelEvent` 转成 `world_joining` / `world_entered` / `world_left` /
  `world_join_cancelled`。
- `options_set` 用 `IOptionRegistry::loadOptionsFromString` 实时应用（FOV/gamma 等立即生效），
  然后自己更新 `options.txt`（保留 CRLF 和未知行），因为游戏自身的
  `saveIfNeeded` 在当前版本不会立即落盘。
- `harness.json` 的 `auto_enter` 让直接启动 exe 后自动进世界；自动线程会等世界列表非空
  且游戏在前台再动手，避免在加载阶段或后台触发鼠标捕获。

## 跑酷数据

`start` 录制的 tick 事件包含 `jump_edge`、`in_air`、`block_below`、`block_feet`、
`block_head`。`driver/parkour.py` 不依赖游戏，负责：

- `segment_jumps`：用地面 true→false→true 切分，起跳前 4 tick 的 `jump_edge` 关联到该跳；
- `summarize`：距离、滞空、顶点、起跳速度；
- `splits`：按 checkpoint 找第一次进入半径的 tick；
- `compare_runs`：逐跳对齐两次运行；
- `record` / `run_attempts`：驱动 macro 回放、`reset`、录制和成功率统计。

细节见 [`parkour.md`](parkour.md)。

## 后台焦点守卫

Bedrock 的 raw input 在窗口失焦后仍可能驱动视角。mod 默认 `background_guard=true`：

- 失去前台时 `ClipCursor(nullptr)` 并平衡 `ShowCursor`，让光标可见；
- hook `LocalPlayer::_applyTurnDelta`，非前台且不是 mod 自己注入的转向时直接丢弃；
- mod 的 `look`/任务通过 `mInjectingLook` 放行；`auto_enter` 要求窗口在前台。

## 开发循环

```bash
py -3.11 driver/dev.py run --world "超平坦测试" --build
```

流程：`xmake -y` → 结束旧进程 → 复制 DLL → 直接 `Popen` exe → 等管道 → 等世界列表 →
`enter_world` → `wait_in_world`。全程不需要 Computer Use；CUA 只在需要看真实画面时用。
`--write-config` 会写 `harness.json`，之后直接启动 exe 也会自动进世界。

## 如何扩展

### 加一个管道命令

1. 在对应的 `Command<领域>.cpp` 加分支，处理后返回 `true`；只有新增领域才需修改
   `Commands.cpp` 的 `dispatchLine`。需要修改游戏状态时继续设置请求或通过 `queueServerTask`。
2. 在 `ProbeMod.h` 增加必要的请求字段/方法，在对应模块实现。
3. 在 `driver/harness/commands.py` 加 `ModClient` 方法（`request(..., "expected_event")`）。
4. 在 `facade.py` 的 `Harness` 加便捷封装，在 `cli_parser.py` 加参数定义、对应
   `cli_commands/<领域>.py` 加执行分支（已处理返回 true，未识别返回 false）。
5. 在 `tests/test_transport.py` 加协议/转发测试；需要游戏时在 `driver/selftest.py` 加检查。

### 加一个闭环任务

1. 在 `ProbeMod.h` 定义 `XxxTask` 和 `std::optional<XxxTask> mXxxTask`。
2. 在 `CommandTasks.cpp` 里解析参数、设置任务、`beginTask()`、回 `xxx_started`。
3. 在 `Tasks.cpp` 实现 `tickXxx(...)`，在 `tickTask()` 里分派，结束时发 `xxx_done`。
4. 在 `commands.py` / `facade.py` / CLI 加阻塞式封装。
5. 游戏内用 `blocks` / `state` 回读验证，不要只看返回值。

## 构建与检查

```bash
# C++（本机工具链见 docs/mod-build-environment.md）
cd mod/probe
xmake -j4 -y

# Python（不需要游戏）
py -3.11 -m ruff check driver tests
py -3.11 -m ruff format --check driver tests
py -3.11 -m mypy
py -3.11 -m unittest discover -s tests -v

# 需要游戏已进入存档
py -3.11 driver/selftest.py
```

C++ 风格由仓库根目录的 `.clang-format` 约束；Python 由 `pyproject.toml` 里的 ruff/mypy 配置约束。
CI 只跑 Python 检查；mod 构建仍在本机完成。
