# 架构与扩展指南

LeviPilot 由游戏内 C++ mod、UTF-8 JSON Lines 管道协议和 Python 客户端组成。脚本/LLM 可以
读取状态、控制玩家并运行逐 tick 实验；需要逐 tick 反馈的逻辑留在游戏线程。

```text
scenario DSL / LLM / shell
          │
   driver/harness (Python)
   CLI → parser → 领域处理器 → commands → connection → protocol
   facade ────────────────────────↗
          │  \\.\pipe\mcbe-harness (protocol 4)
   mod/probe (LeviLamina client mod)
   Transport → Commands → Command<领域> → 请求/任务/快照
          │
   Minecraft Bedrock 1.26.51.01
```

## C++ 模块与所有权

`mod/probe/src/mod/` 按领域划分。下表是导航，不要求每个类型都经过 `ProbeMod` 的全局状态。

| 模块 | 职责 |
| --- | --- |
| `ProbeMod.*`、`WorldSession.*` | 生命周期、tick 编排、世界事件与恢复 |
| `Internal.*`、`ClientDispatch.cpp` | 统一内部入口、游戏平台主线程投递、局部 SEH/C++ 边界 |
| `Input.cpp`、`InputEvents.cpp`、`InputSessionModel.h` | Hardware HID 移动/视角、事件/文本、held/latch、输入锁与统一清理 |
| `InputLifecycleModel.h` | 游戏线程发布输入消费序号、剩余 tick 和事件持键的一致快照，供卸载准入冷读 |
| `TaskModel.h`、`Tasks.cpp`、`TaskEvents.cpp` | 单活动任务、逐 tick 定长计算与后台事件格式化 |
| `QueryModel.h`、`State.cpp`、`Inventory.cpp` | 定长查询快照、跨 tick 扫描、背包读写与快捷栏 |
| `Entities.cpp`、`Ecs.cpp`、`StagedCapture.h` | 实体/ECS 分阶段采样、服务端攻击与客户端交互 |
| `Server.cpp`、`ServerPlayerContext` | 服务端权威命令、传送和 UUID+epoch 玩家解析 |
| `Flight.cpp`、`FlightModel.h` | 服务端能力写入、客户端有界回读与 8 tick 只读观察 |
| `Macro*`、`InputSessionModel.h` | 宏加载/播放/录制、互斥、唯一终结和输入释放 |
| `TickStreamModel.h`、`TickStreams.cpp`、`RecordRing.cpp` | legacy JSON 录制、跑酷事件和 64 B 二进制记录环 |
| `LegacyStreamState.h` | legacy 录制的流、冷锁、终结与世界退出状态 |
| `PacketRequestState.h`、`Packets.cpp`、`PacketInjectionModel.h` | 命令模板、封包注入、代次/ID 与冷锁 |
| `TeleportRequestState.h`、`TeleportModel.h` | 服务端传送请求、代次、ID 和唯一回执 |
| `FlightRequestState.h` | 飞行分阶段请求、重断言、代次与冷锁 |
| `PacketTap.*` | send/recv 身份与可选重序列化诊断、锚点统计和后台 drain |
| `ViolationWatch.cpp`、`InboundWatch.cpp`、`SendProbe.cpp` | 违规/断线页、入站解析、发送/序列化探针 |
| `ViolationWatchShared.h`、`SendProbeModel.h` | 定长记录、冷侧格式化、原子发送历史 |
| `Transport.*`、`PipeChannel.h` | 命名管道、有界写入、spool、后台消费与 I/O 回收 |
| `Memory.cpp`、`Watch.cpp`、`HookVa.cpp` | 内存原语、DR 观察点、VEH、运行时 hook 与崩溃取证 |
| `Resolve.cpp`、`GeneratedProfiles.h` | 版本选择、锚点校验和生成 profile |
| `Options.cpp`、`Checkpoints.cpp`、`PoseModel.h` | 选项、存档点与单 tick 姿态快照 |

`Internal.h` 是所有 TU 的统一内部入口；共享工具放 `probe::detail`。`ProbeMod` 只组合领域
所有者，不保留同一请求的可变别名。`StagedCapture` 为实体、ECS、飞行、传送和封包注入提供
请求独享的分阶段生命周期，不能让迟到回调操作新请求。

## 线程与异常边界

- **客户端 tick**：`LocalPlayer::$normalTick` 的 pre/origin/post 编排输入、任务和定长采样。
  高频路径不分配、不 JSON、不取冷锁。
- **管道线程**：独占 `PipeChannel`，解析命令并发布请求，不直接访问游戏对象。完整和分片输入
  都受 1 MiB 限制；输出批量/大行写入有 5 秒总预算，超时关闭当前客户端。
- **集成服务端线程**：`queueServerTask()` 执行命令、传送、背包和物品使用，回调有局部异常隔离。
- **客户端平台主线程**：`queueClientTask()` 执行世界/UI、聊天、实体/ECS 客户端阶段。主菜单
  也能运行；LL 的世界 tick executor 不能替代它。
- **spool 线程**：每 25 ms 消费定长事件和快照，格式化响应、推进 wall-clock 截止并写
  `spool-<pid>.jsonl`。文件按 100 ms/64 KB 刷盘，启动和超过 64 MiB 时轮转。
- **VEH**：处理观察点和进程级崩溃取证。已处理的请求/采样异常先标记归属，避免误写崩溃 dump。

危险 SDK 读取使用局部 SEH/C++ 边界并转成失败结果；不要用包住整个持锁 tick 的 SEH 跳过
析构。tick 内部错误走 fail-stop、输入释放、后台清理和恢复编号；局部保护不代表整个 tick
或所有 SDK 调用都已隔离。

## 请求与生命周期

### 输入和玩家操作

移动/跳跃默认读取当前键位并合成 Hardware HID 按键沿。legacy 标志/queue/handler/冲量只在
`input_cfg force=true` 下作对照。输入锁区分真实硬件与注入来源，断开客户端自动解锁。

`InputLifecycleStore` 在 tick pre/post 发布同一份输入快照：世界代次、已消费 input/jump/look
序号、实际剩余 tick、待起跳和事件持键。`lifecycle_prepare` 用冷侧最新邮箱与快照比较，拒绝
未消费请求、持续 tick 和事件持键；已结束的有限请求不会因旧邮箱参数误报 busy。它只证明自有
工作准入，不证明任意在途游戏回调已退出。

飞行由服务端写能力，客户端回读原生同步；`FlightAssertion` 只读观察 8 tick。实体攻击、飞行、
命令、传送和背包默认按 UUID+epoch 解析玩家，显式完整玩家名仍可指定；不再做隐式名字兜底。

`reset` 清任务/输入/macro/药水效果后传送；普通 `teleport` 与 `checkpoint_load` 保留效果。
所有接受回执、后台完成回执和实际游戏效果是不同阶段，最终效果必须回读。

### 查询、实体与 ECS

`state/buttons/inventory/blocks/scan/select_slot` 使用请求独享的定长快照与 `*_busy`、截止、
取消语义。`scan` 每 tick 最多 512 格，响应带 `first_tick/last_tick`，不是同 tick 原子世界快照。
实体和 ECS 在所属客户端/服务端队列分阶段采样；队列只持弱引用，JSON 在 spool 格式化。
已开始的游戏调用不支持超时抢占或自动回滚。

ECS context 必须属于当前客户端或服务端 registry；runtime id 对应服务端 Actor，客户端玩家
context 来自 `state.context`。原始地址复用不能代替身份校验。

### 任务、宏与录制

`TaskController` 同时只持有一个任务。新任务替换旧任务，完成/失败/取消按 `task_id` 关联；
`use` 进入 `server_pending` 后有 5 秒 wall-clock 预算，迟到回调不得操作物品。任务事件使用
固定队列，禁止独立 optional 任务槽和跨 TU reset 清单。

宏录制和播放由 `MacroSession` 互斥。录制最多 200000 tick，到顶保留缓冲并可保存；保存使用
同目录临时文件和原子替换。播放/录制/释放失败都有唯一终结，重连或退出取消旧代次。

legacy JSON 录制和二进制记录是两套接口。前者使用 `LegacyStreamState`；后者是 64 B 定长环，
停止等待已准入采样，失败可导出部分帧但拒绝跨 record_id 拼接。

### 世界与卸载

世界进入有独立 `entry_id`、30 秒截止和安全菜单恢复；退档用 `leave_id` 关联，菜单快照只描述
当时状态。HBUI 不等于安全菜单，不能凭 URL 放宽进入或保存门禁。

`lifecycle_prepare off` 先拒绝自有在途工作，再进入 quiescing 并卸四个常驻 hook；失败恢复。
hook 由显式 enable/disable 管理，避免在 DLL 静态构造/析构中暂停其他线程。受控空闲真实重载
的验收见 `reliability-repairs.md`；不能用管道重连或 disable 日志
代替成功证据。

## 管道协议

- 握手：`{"event":"hello","protocol":4,"capabilities":[...],"spool":...,"replay_rows":N,...}`。
- 请求：`{"cmd":"...",...}`；未知命令回 `error/unknown_cmd`。协议只增量扩展，旧字段保持可用。
- 输出队列上限 20000，溢出计数在 `status.queue_dropped`；长时间录制必须持续消费。
- 身份 tap 只记录定长 `(tick,seq,id,tid)`。`serialize_ids` 是显式 SDK 重序列化诊断，可能分配，
  默认累计预算 1 秒；超预算停止后续采样并记 `Budget`/`serialize_skipped`。字节不是线上原帧。
- 入站深拷贝切片没有帧长度前缀；`before_hex` 只保留邻接堆字节，不用于猜长度。
- 命令表和字段见 [`../mod/README.md`](../mod/README.md)，诊断细节见
  [`mod-internals.md`](mod-internals.md)。

## Python 包

```text
driver/harness/
  connection.py  # 独立连接代次、轮询读取、事件缓冲、超时与重连
  commands/      # 按领域 mixin 组成 ModClient
  facade.py      # Harness 组合与脚本便捷操作
  client.py      # 兼容导出
  cli.py         # 稳定 CLI 入口和注册表分发
  cli_parser.py  # argparse 定义
  cli_commands/  # 领域执行与 COMMANDS 声明
  protocol.py    # JSONL、Hello/State 类型和管道绑定
  lab.py         # 建场回读、settle、录制与实验辅助

driver/selftest.py           # live CLI、专项分发与兼容导出
driver/selftest_default.py   # 默认完整套件
driver/selftest_runtime.py   # WorldLost、等待与共享夹具
driver/selftest_{core,tasks,queries,packets,streams,input,recovery}.py
driver/selftest_{diagnostics,flight,macro,lifecycle,support,fixtures}.py
```

每次连接有独立 `_PipeSession` 和 `_PipeStream`；读线程与 finalizer 只持 stream，避免订阅
循环保活客户端。脚本仍须显式 close。`exchange()` 串行化一个工作流；protocol 4 没有通用请求
ID，低层多步 `send`/`readline` 必须放在同一 exchange 中。请求超时关闭连接，独立事件等待
超时不关闭连接。

## 开发、构建与扩展

```bash
py -3.11 driver/dev.py run --world "超平坦测试"

cd mod/probe && xmake -j4 -y
py -3.11 -m ruff check driver tests
py -3.11 -m ruff format --check driver tests
py -3.11 -m mypy
py -3.11 -m unittest discover -s tests -v
py -3.11 driver/selftest.py --require-complete
```

开发循环会先处理保存/退出和进程选择，再构建、安装、启动并等待世界。CI 运行 Python/native
测试，Windows `mod-build` 也构建完整客户端 mod；CI 不启动 Minecraft，不能替代现场验收。

### 加管道命令

1. 在对应 `Command<领域>.cpp` 处理并返回 `true`；只有新领域才修改 `Commands.cpp`。
2. 请求字段归入对应状态所有者，实现实际模块。
3. 在 `driver/harness/commands/<领域>.py` 添加请求封装，必要时更新 facade。
4. 更新 `cli_parser.py`、领域执行模块和 `COMMANDS`。
5. 添加无游戏测试与对应领域 live 检查，保持协议/CLI 兼容并回读真实效果。

### 加闭环任务

1. `TaskModel.h` 定义定长任务并加入 `TaskState`。
2. `CommandTasks.cpp` 完整验证后 `startTask()`，返回带 ID 的 started。
3. `Tasks.cpp` 只填 `TaskStep`，`TaskEvents.cpp` 格式化后台事件。
4. Python 按 ID 等待结果；扩展 native 模型和 live selftest，以 `blocks`/`state` 验证。

## 另见

- API、配置与使用限制：[`../mod/README.md`](../mod/README.md)
- 容量、诊断与实现细节：[`mod-internals.md`](mod-internals.md)
- 当前验收与未覆盖范围：[`reliability-repairs.md`](reliability-repairs.md)
- 新版本移植：[`porting.md`](porting.md)
