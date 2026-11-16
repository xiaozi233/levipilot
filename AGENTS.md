# AGENTS.md

给在本仓库工作的 agent 的常驻约定。人类开发者先读 [`README.md`](README.md) 和
[`docs/architecture.md`](docs/architecture.md)；本文件只写必须遵守的规则、常见坑和当前限制。

## 项目与命令

**LeviPilot** 是 Minecraft Bedrock **1.26.51.01**（LeviLamina **26.51.7**）的客户端实验
harness：C++ mod + UTF-8 JSONL 命名管道 `\\.\pipe\mcbe-harness` + Python 客户端。

```bash
# C++ mod；本机工具链见 docs/mod-build-environment.md
cd mod/probe && xmake -j4 -y

# 按需构建、受控重启、等待菜单并进世界；--no-build 跳过，--build 强制
py -3.11 driver/dev.py run --world "超平坦测试"

# 附着到用户桌面已运行的游戏实例（不杀进程、不重装 mod，避开隔离桌面 exebox 不可见/失焦暂停）；
# 可选 --exec 在世界就绪后执行命令，--stop-after 在完成后按保存门禁停止游戏
py -3.11 driver/dev.py run --attach --world "超平坦测试"
py -3.11 driver/dev.py run --attach --exec "<cmd>" --stop-after

# 断开错误页恢复：先落盘诊断，再回菜单/重进；不要凭 HBUI 推断保存状态
py -3.11 driver/dev.py recover --world "超平坦测试"

# 实验前把游戏窗口拉到前台；未聚焦时世界会暂停（水/岩浆/沙不更新）
py -3.11 driver/dev.py focus

# 统一无游戏与文档检查（ruff/mypy/lint_public/lint_skill/守卫/gen_profiles/文档相对链接/git diff --check/unittest）
py -3.11 driver/check.py
py -3.11 driver/check.py --docs-only   # 或 --fast：跳过 unittest，仅跑静态与文档检查
py -3.11 -m unittest discover -s tests -v
py -3.11 -m ruff check driver tests
py -3.11 -m ruff format --check driver tests
py -3.11 -m mypy

# 离线 PE 静态分析（不启动游戏或 IDA，示例见 docs/reverse-engineering.md）
py -3.11 driver/pe_map.py --xrefs <va>
py -3.11 driver/pe_map.py --iat [filter]
py -3.11 driver/pe_map.py --import-xrefs <sym>
py -3.11 driver/pe_map.py --disasm <va> [-n N]
py -3.11 driver/pe_map.py --rtti <va_or_name>

# 需要已进入测试存档；权限或空槽不足产生的 required skip 在严格模式下也失败
py -3.11 driver/selftest.py --require-complete
py -3.11 driver/selftest.py --connection-only --require-complete
py -3.11 driver/selftest.py --diagnostic-export-only --require-complete
py -3.11 driver/selftest.py --lifecycle-prepare-only --require-complete

# 逆向账本
py -3.11 driver/landing.py begin --case <slug> --goal "..."
```

`py -3.11` 是硬要求。需要完整现场覆盖时先清空快捷栏并开启所需能力；普通模式中的
SKIP 仍保留在分母中。`--violation-only` 只验证合成记录，不是真实违规复现。

## 目录地图

| 路径 | 内容 |
| --- | --- |
| `mod/probe/src/mod/` | C++ 实现。`ProbeMod` 管生命周期/tick，`WorldSession` 管世界流程，`ClientDispatch` 管主线程投递，`Transport` 管管道/spool，`Commands` 管分发与异常隔离 |
| `mod/probe/src/mod/*RequestState.h` | 封包、传送、飞行和 legacy 录制请求的唯一状态所有者；不要把字段复制回 `ProbeMod` 或另建 reset 清单 |
| `mod/probe/src/mod/{ViolationWatch,InboundWatch,SendProbe}.cpp` | 违规/断线页、入站解析、发送探针；共享定长记录与冷侧格式化在 `ViolationWatchShared.h` |
| `driver/harness/` | Python 客户端：`connection`、`commands/`、`facade`、`client`、`protocol`、`errors`、`lab`；CLI 为 `cli`/`cli_parser`/`cli_commands/` |
| `driver/selftest.py` | live 自检 CLI、专项分发和兼容导出 |
| `driver/selftest_*.py` | `default` 默认全套，`runtime` 共享门禁/夹具，core/tasks/queries/packets/streams/input/recovery 等领域检查 |
| `driver/check.py` | 统一离线检查入口（ruff/mypy/lint/守卫/gen_profiles/文档链接/diff/unittest，`--docs-only` / `--fast`） |
| `driver/dev.py` | 受控启动、用户桌面 `--attach`、保存退出、重启、自动进世界与 `--exec`/`--stop-after` |
| `driver/pe_map.py` | 离线 PE 静态分析（`.pdata`、`--xrefs`、`--iat`、`--import-xrefs`、`--disasm`、`--rtti`） |
| `driver/gen_profiles.py` | 从 `docs/offsets/*.json` 生成 `GeneratedProfiles.h`；`--check` 用于 CI |
| `docs/offsets/`、`docs/roles/` | 版本 profile 与 RE 角色；不要混用 |
| `docs/notes/`、`reverse/`、`skills/bedrock-re/` | 逆向证据、调查原文和实验方法；不是第二套现行 API 文档 |
| `scenarios/`、`tests/` | 场景 DSL 与无游戏/native 测试 |

## 硬约束

1. **`Internal.h` 是所有 C++ TU 的统一内部入口**；共享工具放 `probe::detail`，不要新建第二套
   全局包含链。
2. **管道线程不直接访问游戏对象**。服务端操作走 `queueServerTask()`，世界/UI 操作走
   `queueClientTask()`。LL 的 `ClientThreadExecutor` 只随世界 tick 执行，不能用于主菜单。
3. **逐 tick 反馈留在游戏线程**（`safeTickPre`/`safeTickPost`），Python 不进入反馈环。
4. **热路径不分配、不 JSON、不新增锁**。tick 和高频 hook 只写预分配定长结构；格式化放到
   管道/spool 线程或 Python。显式诊断的额外成本必须单独披露。
5. **错误不能穿透游戏 tick**。MSVC `/EHsc` 下访问违例不是 C++ 异常；危险处理使用局部
   `__try/__except` 或既有 `guardedGameRead`，不要用包住整个持锁 tick 的 SEH 跳过析构。
6. **版本数据只进 profile**。版本号、签名、偏移和组件 id 写入 `docs/offsets/<version>.json`
   （`runtime:true`），再运行 `driver/gen_profiles.py`。RE 函数/全局角色写入 `docs/roles/`，
   `unresolved` 不填地址。没有 profile 时降级，不能崩溃。
7. **协议只做增量变更**。握手是 `hello.protocol=4`；旧字段保持可用，未知命令仍回
   `error/unknown_cmd`。
8. **强能力默认关闭**。内存写、任意 VA 装钩、输入/封包注入、压力与崩溃测试都要显式开启；
   需要审计的能力必须写日志。
9. **不擅自推送、不强停未保存会话**。保存状态不能确认时保留进程；只有调用者明确放弃未保存
   更改时才使用 `--force`/force-stop。

## 扩展步骤

### 加管道命令

1. 在对应 `Command<领域>.cpp` 处理并返回 `true`（拒绝和参数错误也算已处理）；只有新领域才
   修改 `Commands.cpp::dispatchLine`。
2. 请求状态归入对应领域所有者，在 `ProbeMod.h` 只声明必要方法/会话，不复制一份别名。
3. 在 `driver/harness/commands/<领域>.py` 添加 `request(..., expected_event)`；超时参数使用
   `float | None = None`。
4. 更新 `cli_parser.py` 与 `cli_commands/<领域>.py`，命令名加入该模块 `COMMANDS`。
5. 添加无游戏测试；live 检查放进对应 `selftest_<领域>.py`，保持 CLI 和检查名兼容。
6. 更新 `mod/README.md` 的 API/限制及 `docs/mod-internals.md` 的实现说明。

### 加闭环任务

1. `TaskModel.h` 定义定长 `XxxTask` 并加入 `TaskState`。
2. `CommandTasks.cpp` 完整验证后 `startTask()`，返回带 `task_id` 的 `xxx_started`。
3. `Tasks.cpp` 只计算并填 `TaskStep`；`TaskEvents.cpp` 负责后台状态/结果格式化。
4. Python 经 `_task_request`、facade 和 CLI 按 ID 等待结果。
5. 扩展 native 模型与 live selftest，用 `blocks`/`state` 回读真实效果。

禁止新增独立 optional 任务槽、跨 TU 手写 reset 列表或 tick 内 JSON。`TaskController` 锁内
回调不得重入控制器，也不得同步等待另一个需要任务锁的线程。

## 常见坑

### 管道、线程与生命周期

- Windows 同步句柄上的挂起 `ReadFile` 会阻塞同句柄 `WriteFile`；客户端必须使用
  `PeekNamedPipe` 轮询读线程。
- Python 探针使用 `with ModClient()` 或 `try/finally: client.close()`。读线程与 GC finalizer
  只持 `_PipeStream`，不能经订阅循环保活 client；GC 不回收仍被业务引用的对象。
- `command` 的方块操作是 deferred，`success=false` 不代表失败，以 `blocks`/verify 回读为准。
- 客户端位置会被服务端覆盖；位置走服务端权威路径。朝向走命令通道，不能只改组件。
- `enter_world` 必须等安全菜单；HBUI 不等于可进入。断开错误页同样可能是 HBUI，但
  `worlds.ready=false`；`world_state()` 的 `disconnect_error` 与 `exiting` 不得混淆。
- `lifecycle_prepare` 检查自有输入/任务/录制并关闭普通请求准入，保留常驻 hook。实际
  `disable` 用 CallbackLifecycle 排空；不得绕过 callback_self、busy、诊断会话或超时拒绝。
- 主菜单、暂停菜单和某些对话框仍需桌面 UI；暂停菜单不响应注入 Esc/Enter。
- tick 记录写 `<modDir>/spool-<pid>.jsonl`；崩溃取证由 VEH 写 crash 文件。请求级 AV 由
  `dispatchWithSeh` 转成 `internal_error`，不要把危险代码无保护地放进 tick。

### 输入与玩家操作

- 默认移动/跳跃读取当前键位，合成 **Hardware HID** 按键沿。禁止把直接写
  `MoveInputComponent`、queue/handler 重发或 `jumpFromGround` 恢复成默认路径；legacy
  `mode`/`jump_impulse` 只允许 `input_cfg force=true` 的对照实验。
- 输入锁丢弃真实硬件来源，注入仍可工作：`hid`=Virtual，`hardware`=Hardware+注入标记，
  `raw`=Hardware 无标记。配置的 `lock_input` 只在客户端连着时生效，断开自动解锁。
- 双击空格飞行要求 Hardware origin 且按下跨 tick，使用 `lab.double_space_fly()`。能力门是
  `MayFly`；切换游戏模式会重置能力。
- 实验先 `lab.settle(...)` 验证地面、静止、水中和 sprint 前提，不用裸 `sleep` 代替。
  `state.input.sprint` 可能锁存，走/跑对照前要明确清到 `input_sprint=False`。
- `StateVectorComponent+0x18/+0x1C/+0x20` 是 **`mPosDelta`（等待运动阶段应用的位移量，不保证是下一个世界 tick）**，不是速度：
  地表行走时约为 **0.546 × 实际位移**（地面摩擦乘在上一拍位移上，`[推断]`）；`state` 报告的
  `vx/vy/vz` 来自 `Actor::getVelocity()`，等于已完成位移（`mPos − mPosPrev`），而
  `state.pos_delta_x/y/z` 直接导出 `StateVectorComponent::mPosDelta`，`prev_yaw/prev_pitch`
  导出 `ActorRotationComponent::mRotPrev`，实际速度仍以相邻 tick 位置差判定。Liquid Blip 写的
  `+0x1C` 就是这个 `mPosDelta.y`；已完成位移 / 待执行位移 / 录制 `vy`（=已完成位移）是三个量，不要混。
  传送/场景的 `pos.y` 是脚部；StateVector/录制的 Y 是位置基准，本版本原地站蹲对照都为
  **脚部 + 1.6200101**，不随头部回读一起降低。不要称为当前眼睛或镜头高度；`state.head_y`
  与当前 AABB 需分别读取，蹲姿对照见 liquid-blip 的 `r20-066`。
- `GameMode::interact` 在空物品上下文会崩；`interact_entity` 只做转向和注入使用按钮。
  `mouse_event` 的左右键不等于 `click`/`attack_entity`。
- `reset` 清效果；普通 `teleport` 和 `checkpoint_load` 不清效果。共享传送请求必须显式设置
  `clearEffects`，不能沿用上次 reset 的值。
- 液体液位读 `blocks` 回包的 `addr` + `probe addr+288`（客户端封装 `liquid_depth`）；
  `/testforblock ... ["liquid_depth"=N]` 匹配不可靠，整池都会报 `=0`。
- `dev.py` 报 `could not focus the game window` 时世界可能处于暂停：`setblock`/`teleport`/
  方块回读都正常但水不流、沙不落，会被误判成“基岩不流动体”。先聚焦窗口再跑液体实验；
  若当前 shell 位于隔离桌面（如 `exebox-*`），拉起的窗口无法在物理屏幕可见或获取焦点，应在
  用户桌面手动启动游戏后用 `py -3.11 driver/dev.py run --attach`。

### 诊断、版本与逆向

- 版本锚点可能是 `jmp [rip+0]` thunk；先跟 thunk 再取签名。role 是静态 VA，运行期地址为
  `diag.module_base + (role_va - image_base)`。
- 通用 VA detour 只保留 rcx/rdx/r8/r9；XMM、向量返回、隐藏返回指针或更多参数必须 typed
  detour。VA 只校验可执行页，指令边界由 IDA 确认。
- `allow_version_mismatch` 只用于移植，不校验 profile，日常保持关闭。改 offsets 后必须重新
  生成 `GeneratedProfiles.h`。
- 硬件观察点只记录装过 DR 的线程，其他线程事件进入 `foreign_hits`；玩家 StateVector 观察用
  `thread="both"`，`thread="all"` 被显式拒绝。
- 接收封包走 `LoopbackPacketSender::$sendToClient`；`Packet::sendTo*` 辅助层和
  `NetworkConnection::receivePacket` 在单机实测为 0 调用。
- `serialize_ids` 在原上下文同步重序列化，可能分配；默认累计预算 1 秒，超预算记
  `Budget`/`serialize_skipped`。预算不能中断正在执行的 SDK 调用，也不是线上原帧。
- 入站深拷贝切片没有帧长度前缀，`before_hex` 是邻接堆字节，不得猜成声明长度。
- 不要在 tick 线程枚举客户端 ECS registry；runtime id 对应服务端 Actor，客户端 context
  使用 `state.context`。
- 不要对正在打开的 IDA/idalib 进程 `taskkill /F`。停止后端用 `idb_close(save=true)`；
  离线 PE 定位/xref/IAT/反汇编/RTTI 用 `driver/pe_map.py`（`--xrefs`、`--iat`、`--import-xrefs`、`--disasm`、`--rtti`），IDA 客户端用 `driver/ida.py`。
- `driver/ida.py` 报 `Failed to open database` 先跑 `driver/ida.py status`：2~5 秒就失败的
  是开库锁冲突（另一个 worker 占着同一个 `.i64`），不是库损坏；冷开成功要 3~12 分钟。
  `open_session`/`connect` 会先 adopt 同库的活会话，不会再开第二次。
- 流体、tick 顺序和 BadPacket 的证据分别看 `docs/fluid-movement.md`、`docs/notes/` 与
  `skills/bedrock-re/`，不要把历史观测当成静态机制结论。

## 当前状态与维护

当前基线为离线 **410/410**、完整现场 `--require-complete` **1169/1169**；另有生命周期
准入 **10/10**、输入专项 **46/46**、200000 tick 长录制 **9/9**。这些数字对应其代码与权限
前提，后续变更必须重新验证适用范围，不能永久当作“全绿”。

经 prepare 的受控空闲真实热重载已通过，详见当前验收文档；不要在 DLL 静态构造/析构中装卸
hook。在途回调排空竞争专项 **132/132**，包含真实重载、超时恢复、未经 prepare 的持键和
活跃诊断拒绝；它不证明任意外部 SDK/第三方回调时序。HBUI 异常退档根因、游戏侧共享
序列化缓冲竞态仍是独立边界。高频查询后的管道失联只保留诊断，不添加未经验证的跨线程
句柄看门狗。

写文档先读 [`skills/project-docs/SKILL.md`](skills/project-docs/SKILL.md)。文档只维护当前行为、
当前验收和未决限制；旧过程记录以 Git 历史为准，不再向文档末尾追加时间日志。改行为时同步
`mod/README.md`、`docs/mod-internals.md` 和相关指南；改验收时同步
`docs/reliability-repairs.md`，不要把未运行或未通过的范围写成完成。
