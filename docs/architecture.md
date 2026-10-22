# 架构与扩展指南

本项目分三层：游戏内的 C++ mod、JSON Lines 管道协议、Python 客户端。目标是让脚本/LLM
用少量命令控制玩家并读取状态，同时把需要逐 tick 反馈的逻辑留在游戏线程里。

```
scenario DSL / LLM / shell
          │
   driver/harness (Python)
   protocol → client → CLI
          │  \\.\pipe\mcbe-harness  (UTF-8 JSON lines, protocol 3)
   mod/probe (LeviLamina client mod)
   Commands → Tasks / State / Inventory / Entities / Macro
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
| `Tasks.cpp` | `navigate` / `mine` / `place` / `use` 状态机与 `task_status` |
| `State.cpp` | `state`、`buttons`、`blocks` 快照 |
| `Inventory.cpp` | 背包快照与写入、give/clear、快捷栏选择 |
| `Entities.cpp` | 附近实体列表、实体信息/攻击/交互/看向 |
| `Server.cpp` | 服务端权威传送 |
| `World.cpp` | 世界列表、`enter_world` / `leave_world`、世界事件 |
| `Options.cpp` | 运行时改选项并写回 `options.txt` |
| `Macro.cpp` | macro 文件加载/保存/回放记录 |
| `Commands.cpp` | 管道命令解析与分发、请求级 SEH/异常隔离 |
| `Transport.cpp` | 命名管道服务端、写队列、`queueServerTask`、spool、诊断日志 |
| `Memory.cpp` | 内存读写、地址校验、Block/Legacy 偏移表与结构化 probe |
| `Watch.cpp` | 调试寄存器观察点（DR0–DR3）、VEH 命中环、崩溃 minidump |

`Internal.h` 是唯一允许被所有 TU 引用的内部头；它集中引入 Bedrock 头文件，避免每个
翻译单元各自维护一份包含列表。新增共享工具时放在 `probe::detail`。

## 线程模型

- **游戏线程**：`LocalPlayer::$normalTick` 钩子。`safeTickPre` 在 origin 前注入输入/推进任务，
  `safeTickPost` 在 origin 后采样状态。
- **管道线程**：`serverLoop` 阻塞在 `ConnectNamedPipe`/`PeekNamedPipe` 上，负责读写 JSON 行和
  分发命令。它不直接调用游戏对象，只设置请求标志或调用 `queueServerTask`。
- **服务端线程**：通过 `ServerInstance::queueForServerThread` 执行传送、命令、背包写入和使用物品。
  所有回调由 `queueServerTask` 包装，异常不会穿过游戏服务端 tick。
- **spool 线程**：每 25 ms 检查一次，把积压的记录按“100 ms 或 64 KB”批量写进
  `spool-<pid>.jsonl`；`world_left` / `stop` / `disable` 会强制刷盘。它只碰文件和缓冲区，
  不碰任何游戏对象。
- **VEH**：`AddVectoredExceptionHandler` 同时负责观察点命中和进程级崩溃取证。请求分发期间的
  结构化异常由 `dispatchWithSeh` 转成 `internal_error`，不会写 minidump。

跨线程状态用互斥量（输入、任务、传送、背包请求）或原子量（各种 `*Requested`、`mRunning`）。
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
- 连接后 mod 先发 `{"event":"hello","protocol":3,"spool":...,"replay_rows":N,...}`；
  客户端校验协议号。新增字段是增量的，旧客户端仍可用。
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
  client.py     # ModClient（连接/超时/重连）+ Harness（便捷方法）
  memory.py     # 地址/hex 解析、FNV 辅助
  spool.py      # 增量、可容忍轮转/半行的 spool 读取器
  watch.py      # watch 命中解码与可续读游标
  cli.py        # argparse 入口
driver/transport_mod.py  # 兼容门面，旧导入和脚本入口继续可用
driver/dev.py             # 直接启动 exe + 自动进世界的开发循环
driver/parkour.py         # tick 录制、跳跃分段、运行对比、attempts
```

`ModClient` 用独立轮询线程读管道：Windows 同步句柄上挂起的 `ReadFile` 会阻塞同一个句柄的
`WriteFile`，所以不能用一个阻塞读线程。请求带超时；超时会关闭连接，避免流错位。

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

1. 在 `Commands.cpp` 的 `dispatchLine` 里加分支；需要修改游戏状态时通过 `queueServerTask`。
2. 在 `ProbeMod.h` 增加必要的请求字段/方法，在对应模块实现。
3. 在 `driver/harness/client.py` 加 `ModClient` 方法（`request(..., "expected_event")`）。
4. 在 `Harness` 加便捷封装，在 `cli.py` 加子命令。
5. 在 `tests/test_transport.py` 加协议/转发测试；需要游戏时在 `driver/selftest.py` 加检查。

### 加一个闭环任务

1. 在 `ProbeMod.h` 定义 `XxxTask` 和 `std::optional<XxxTask> mXxxTask`。
2. 在 `Commands.cpp` 里解析参数、设置任务、`beginTask()`、回 `xxx_started`。
3. 在 `Tasks.cpp` 实现 `tickXxx(...)`，在 `tickTask()` 里分派，结束时发 `xxx_done`。
4. 在 `client.py` / `Harness` / CLI 加阻塞式封装。
5. 游戏内用 `blocks` / `state` 回读验证，不要只看返回值。

## 构建与检查

```bash
# C++（本机工具链见 docs/mod-build-environment.md）
cd mod/probe
xmake -y

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