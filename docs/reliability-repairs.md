# 2026-10-30 审阅修复跟踪

基于 `705b9e6` 的审阅，共 16 项。按确定性故障、并发/异常边界、异步生命周期、
结构与性能、验证与契约的顺序推进。完成必须有代码、对应回归和适用范围内的验收证据。

当前代码的补充复核、未完成问题与建议顺序见 [`code-quality-review.md`](code-quality-review.md)。

| 项 | 范围 | 当前状态 | 验证 |
| --- | --- | --- | --- |
| 1 | 请求更新异常、无效回退快照 | 完成 | 生产 RequestMailbox native 事务/并发检查；现场错误请求后 yaw_gap=0 |
| 2 | DSL 录制遗漏 tick、错误成功 | 完成 | 真实请求集成、5000 tick；现场等待 12 tick 采集 13 条，pending=0 |
| 3 | write-config 副作用、进程停止 | 完成 | 纯配置、路径/失败保护回归；现场保存退出 → 目标 PID 停止 → 重进世界并站稳 |
| 4 | RequestBlock、按钮数组跨线程访问 | 完成 | 原子载荷、有界有效快照；按钮重置归 tick；现场全套通过 |
| 5 | Hook/Watch/Packet 采集环并发与游标 | 完成 | `AtomicRing` 多生产者/延迟发布/环绕 native 回归；三类 drain 保留 pending 游标并统计覆写 |
| 6 | 服务端任务 SEH 边界 | 完成 | 独立 SEH wrapper + 异常归属；外层非法指令/C++ 与锁内物品 SEH 专项通过，故障后 tick/ping 正常且无误报 dump |
| 7 | 旧 auto_enter 清理被早退绕过 | 完成 | 临时文件清理、开关保留、备份恢复回归；离线全量与保存退出重启通过 |
| 8 | 宏录制会话生命周期 | 完成 | 共享会话锁、录制代次隔离；限长、重复启动、录制/播放互斥与事件级停止释放现场自检通过 |
| 9 | Hook 槽复用的旧 mem 数据 | 完成 | 命中记录改为零初始化定长载荷；无 mem 配置不会继承上一次快照 |
| 10 | Chat 请求超时、主菜单生命周期 | 完成 | 请求独享快照、2 秒超时、退出取消、主菜单平台队列；native 回归、专项现场 9/9、完整现场 237/237 |
| 11 | UseTask 服务端等待没有截止条件 | 完成 | 5 秒截止、迟到副作用拒绝、拥有者失败回调；native 边界/竞争/零分配、专项现场 16/16、完整现场 253/253 |
| 12 | 游戏线程采样异常边界、JSON、分配与锁 | 进行中 | 查询/JSON 录制/跑酷/宏录制已迁快照或定长队列；hook_call 已迁独占请求与后台回执，姿态已加局部 SEH/一致快照/世界代次；二进制录制已补唯一失败与部分数据传播；宏播放已迁无锁准入/后台终结、点击已迁固定队列；ECS 已移出 tick 并迁独占分阶段快照；事件输入已有局部边界、固定结果和文本屏障，文本/UI 迁客户端队列；飞行已迁分阶段请求、唯一终结和有界重断言；玩家名缓存已改 UUID/epoch 原子快照；移动/视角已加局部边界/失败传播/释放重试，传送已迁独占请求/后台调度；内部错误已迁定长队列/后台清理与两阶段准入；命令包模板/注入已迁定长采样与独占请求，通用 PacketTap 序列化及其余 SDK 边界仍待治理 |
| 13 | ProbeMod 跨领域状态耦合 | 未完成，播放模型已局部封装 | 宏录制/输入整体会话与其余字段所有权仍待推进 |
| 14 | 服务端玩家解析重复 | 部分接入；未完成 | 背包四组写入/命令/权限/物品使用/传送共用 ServerPlayerContext；实体/飞行仍待统一 |
| 15 | 交互覆盖、自检跳过统计 | 三态/必需门禁已实现；夹具与观察生命周期已补，验收见下 | 显式 SKIP、完整模式拒绝缺测、四组首错保护直接回归与固定水池专项 |
| 16 | 类型码命名、失效文档 | 完成原定范围 | `658d90a` 已落地 PlacePhase/ControlInputMode/JumpImpulse；本轮核对实现，清理仍写待做的状态及默认起跳/当前基线的陈旧说明 |

原清单第 1–11、16 项完成；第 12 项进行中，13–15 保留各自未结范围。补充审阅的宏硬上限、世界进入
看门狗与 Overhead 统计环并发已修复；第 12 项其余范围不能因局部验收通过而自动算完成。

## 2026-11-02 剩余工程收尾工作表

交接时重新核实 HEAD `be0f756`、工作树干净，游戏未运行；安装/构建 DLL SHA-256 同为
`8241c97db4f7bdf9bcd978eb796f27f49c07674f36ba68630cf3db8c90c68dc7`，配置原文与 123 个
crash 文件的哈希清单存于 `out/remaining-refactor-baseline.json`。交接前离线 **377/377**、
ruff check 通过；干净 HEAD 的 ruff format 已有 2 文件需要重排，不能记为交接前 format 全绿。

| 工作 | 位置 / 优先级 | 方案与依赖 | 验收条件 | 当前状态 |
| --- | --- | --- | --- | --- |
| 切片契约 | `lab.py`、`selftest.py`、`ViolationWatch.cpp` / P2 | 不再猜邻接堆字节；校验 id、实际大小、完整性、序号 | 离线 + 守望专项，无必需 skip | **完成**：定向 9/9、现场 41/41（0 skip） |
| 自有能力时序 | `Flight.cpp` / P1 | 先消除无必要的双侧写与写重断言，再做身份统一 | 最小能力对照、飞行专项与完整现场 | 进行中 |
| 热路径 | `PacketTap.cpp` 及其余 hook / P1 | 常规身份保持定长；显式 SDK 序列化单列高成本诊断 | native 边界/并发、packet 专项、受控开销对照 | 未结 |
| 玩家解析 | `Entities.cpp`、`Flight.cpp` / P2 | 服务端队列内 UUID+epoch，去掉隐式名字兜底 | 无玩家/换世界/旧请求/取消/故障恢复 | 未结，依赖上项先单独验收 |
| 会话所有权 | `ProbeMod.h`、Macro/Input / P2 | 封装录制与输入整体会话，保留 tick 无分配/无冷锁 | native 生命周期 + 宏/输入/控制专项 | 未结 |
| 自检真实性 | `selftest.py` 与领域 fixture / P2 | 拆分领域检查，补效果回读、首错和恢复 | 专项参数兼容、`--require-complete` | 未结 |
| 补充债务 | Macro 保存、Overhead、提示词索引 / P2 | 原子替换、同条件性能对照、历史状态校准 | 故障保留旧文件、保存再加载、统计与限制完整 | 未结 |

本轮不新增逆向作为前提；BadPacket 根因以证据册 §49 为准。上文未结项只有完成对应验收后才改状态，
旧「尚缺失败原帧」「声明长度 23」及 `inbound_slice_framing` skip 均为历史阶段记录，不是现行结论。

## 2026-11-01 22:20 后续与停止点

第14项实体/飞行共用 ServerPlayerContext 的候选通过编译、相关回归25/25和离线376/376，
现场飞行生命周期却在 tick1341 自然退档（packet187/size48/error104→90/Block），
**28/29，1 fail、0 skip**。候选未提交，已导出 `out/server-context-candidate.patch`、
保留候选产物并撤回源码及安装；该项仍未完成。没有对照足以判定是否由候选引入。
失败样本/采样边界见 [`现场记录`](notes/badpacket-187-20261101.md)。

当场补修独立的 Python 导出缺陷：游戏 cwd 与 driver cwd 不同，相对的 inbound_dump
路径 open_failed；send 后未消费回执，使下一条 packet_drain 收到旧错误。
现用绝对路径并等待 inbound_dump 回执。断开报告也统一绝对目录。
回归24/24，`--diagnostic-export-only --require-complete` **3/3**；仅导出现有环，
不安装 hook 或修改世界。最终离线与静态检查记录在 mod-internals。
最终全量离线377/377、ruff/format/mypy、profiles/roles通过；完整现场仍保留上述失败。

真实错误页 recover exit0，测试前 DLL/配置恢复，原世界状态回读11/11，
121个原有crash无新增/修改。本轮不新增逆向；后续请使用
[`可直接转交的提示词`](notes/prompts/badpacket-187-followup-20261101.md)。

## Python 连接遗弃回收（2026-11-01）

用户的 §41–§43 调查记录了探针未 close 后管道不可达的现场。自有客户端里可确认的
泄漏是后台读线程持有含订阅的 session：遗弃 client 仍留下句柄，回调捕获 client 时还会
通过线程形成永久可达循环。本批未新增逆向，也未改 C++ 或安装 DLL。

- `_PipeSession` 保留请求/订阅，独立 `_PipeStream` 保留 I/O；读线程与 finalizer 仅持 stream。
- 显式/GC 关闭、EOF、接收溢出共用释放。短锁转移资源，锁外关句柄/join；旧连接隔离。
- 线程建立、启动或握手失败（含 KeyboardInterrupt/非法 protocol）关闭当代句柄并重抛。
- `selftest --connection-only --require-complete` **11/11，0 fail、0 skip**：空闲准入、
  普通遗弃、订阅循环遗弃、子进程正常退出与 os._exit 后重连/ping。原进程 PID 39708，
  世界保持运行；结束时任务/录制/控制空闲，queue_dropped/pipe_write_timeouts 均为 0。
- 离线全量 **376/376**；其中 transport **46/46**。ruff check/format、mypy（93 文件）通过，
  profiles up to date、roles **56/63**。日志 `out/pipe-lifecycle-{offline,tests,live}.log`。
  首次全量的新增 EOF 假句柄缺少 fileno 已修正，上述数字是修正后的完整重跑。

仍要求探针用 `with ModClient()` 或 `try/finally: client.close()`；GC 不负责活跃引用。
本批证明遗弃连接和进程退出后可以重连，不宣称所有 Errno 22、历史高频查询挂起或
BadPacket 均已修复。完整游戏功能套件本批未重跑。用户最新 BadPacket 结论以证据册
§36–§43 为准（深拷贝、无长度前缀、尚缺失败原帧），下文为各历史轮次的原始记录。

## 诊断可靠性与自检结果收尾（2026-10-31 深夜）

### 最新现场：自然违规与错误页已关联，成因仍待定位

2026-10-31 北京时间 **23:38:53**，最新 DLL
`9262737efdf5a658e0aa51a6b6c1e85f36bd0e93419823104ebad21aab6e0cbe`
的第二次完整自检发生自然退档，结果 **465/466，1 fail、0 skip**：
最后通过 `flight_spectator_recovers`，`check_flight` finally 恢复期间的
`wait_ticks` 从 tick 859 开始，tick **861** 退档，首错为 `aborted_world_left`。
这不是完整通过；第一次完整的 1053/1055 水流夹具失败也保留在下文。

两条新增记录都为 `synthetic=false`，同 tick **861**、同 tid **30828**：

| 来源 | 现场字段 | 能支持的结论 |
| --- | --- | --- |
| callback | `response=3`、`packet_id=39`、`packet_size=34`、`error_value=104` | 违规回调要求终止连接；包编号、size 参数值和错误值已取得 |
| screen | `response=90`、`disconnect_reason=90`、`context=Block` | mod 在进程内读到最终 BadPacket 断开原因与码字，无需点界面 |

回调 context 为 `BinaryStream read() incomplete.readNoHeader fai`，
`context_truncated=true`；屏幕 detail 为
`Minecraft Bedrock.Win,D,,GC-10.0.26100.4351 Err`，`detail_truncated=true`。
两者上限已从 47 字节放宽到 191 字节（2026-11-01：截断正好切掉了 `mReadPointer`/`buffer Length`）。`category=140697646054272` 是本进程观测值，
尚未解释其语义，不能当作可跨进程比较的错误编号。

SDK 公开头文件 `refs/levilamina-26.51.7/src/mc/network/MinecraftPacketIds.h`
声明 **SetActorData=39**、UpdateAbilitiesPacket=187。此处只能按枚举命名为 SetActorData，
不能称为能力包。`packet_size=34` 究竟计入哪些边界、104 的具体含义、实际传输方向、
哪个字段或生产链导致不完整读取仍 **unresolved**；能力操作附近发生并不证明因果。
`packet_capture=false`、`chat_sent=0` 也不能排除 mod 通过原生状态同步间接影响封包。

观察从入口成功武装到退档前未经过设施自测/panic 空窗，
`real_count=2/synthetic_count=0/lost=0/observation_faults=0`。这次补齐了自然事件经过
callback 与 screen 两类观察点的现场样本，不证明任意启停并发/在途卸载安全。
原始证据：`out/diagnostic-full-final-live.log`、
`out/diagnostic-full-final-violation.json`、`out/disconnect-20261031-233853.json`；
日志、dump、原始 spool、事发配置与 integrity 已归档到
`out/diagnostic-validation-20261031-231752/natural-disconnect-233853/`。

先归档再逐字节恢复原配置，执行 `recover --world "超平坦测试"` **exit 0**，
确认错误页自停并重进世界。构建/安装 DLL 哈希相同；最终恢复 **16/16**：原姿态、
gamemode/mayfly/flying、效果、背包/装备/选中槽、干燥站稳、输入释放/解锁、守望关闭、
封包能力关闭、原配置哈希及 **113** 个 crash 文件无新增/修改均回读通过。
恢复证据为 `out/diagnostic-natural-recover.log` 与
`out/diagnostic-validation-20261031-231752/final-restoration.json`。

离线全量 **362/362**、ruff check/format、mypy（93 source files）、完整 C++ 构建通过；
profile `--check` 为 up to date，roles **50/57**。固定水池修复只有专项 **11/11**，
第二次完整在此前的 flight 检查中止，尚未走到新的水池检查。**不更新历史完整全绿基线**。
后续只提供诊断提示词，不执行新增逆向，也不反复跑完整套件碰运气：
[`badpacket-setactordata-20261031.md`](notes/prompts/badpacket-setactordata-20261031.md)。

### 本批已实施的加固

- SDK 文档明确会保留并动态更新 hook 的 origin 输出槽；改为静态存储，去掉栈临时槽及
  安装后有界自旋/跳过原调用。卸载结果与回读状态一致，panic 的 ok 纳入守望卸载，
  disable 同样卸下。环冷侧一次初始化后原子发布；SDK 参数读取局部隔离，失败计数后仍转发。
- 人工 disconnect_route 改为线程局部、可嵌套、异常恢复的 synthetic 标记；屏幕原因码
  不再套用 PacketViolationResponse 等级，增量字段 disconnect_reason 明确语义。
- 默认/专项共用观察生命周期，导出起始游标之后的记录；设施自测恢复外层观察，panic
  卸载断言完成后再武装，保留显式空窗事件。记录采集/写盘错误不覆盖主体故障。
- flight/controls/teleport/task_operations 的 finally 保留首个异常对象、类型及其栈，
  清理错误追加 notes；没有主体异常时仍让清理失败。对应四组直接回归已通过。
- Checks 改三态，必需 skip 在 --require-complete 下 exit 1。设施现场 **21/21**；
  关闭封包能力的门禁对照 **2/3**（1 required skip，预期 exit 1）。完整验收待下文更新。
  不声称零观察空窗或 SDK 在途热卸载已证明；自然事件的最新范围见上文，成因仍未定位。

### 现场暴露的水流夹具污染

首轮完整 `--require-complete` 为 **1053/1055，2 fail、0 skip**，失败为 checkpoint_load
（0.817 格）与 teleport（0.892 格）；没有退档，观察增量为 3 条 synthetic、0 real，lost=0。
保留 `out/diagnostic-full-live.log` 和 `out/diagnostic-full-violation.json`，不把首轮改记为通过。

控制复现中，reset 的首个采样恰为请求坐标；无输入、无附近实体时，随后水平速度升到
约 0.0493 格/tick，24 tick 后两轴各偏约 0.991 格。blocks 回读显示脚部周围仍有水流。
根因在自检夹具：H0 的开放单水源只删除原点，介质录制的两层开放水源只清 7×7 范围，
外侧水流继续回灌。它污染后续站立/存档点/传送前提，不能当作传送路径故障。
证据：`out/diagnostic-position-repro.json`，首个 reset 坐标与每次输入状态/方块均保留。

两类检查改共用固定封闭游泳池，在远处加载/建场并回读水与两侧石壁；采样结束恢复姿态。
`--record-medium-only --require-complete` 同时覆盖 H0 方块身份和介质录制，现场 **11/11**，
恢复点干燥且站稳。另修正 checkpoint_load 比较对象：比较 checkpoint_saved 的实际保存坐标，
不拿更早的 feet 变量充当存档点；保留原 0.3 格容差，普通传送仍为 0.2 格并补目标/实测详情。
清理此前夹具产生的已知区域水流时只替换 water，不清除其他方块；未做游戏机制逆向。

## 第 12 项：封包采集异常边界（2026-10-31）

- `PacketTap` 对 `getId()` 和 `packet.write()` 增加局部 SEH/C++ 边界；接收路径只读取一次
  id，再复用于去重和定长身份记录。失败只终止该次观察，不改变原封包或正常投递。
  回调门的 guard、已构造的流和缓冲所有权均在 raw SEH frame 外，故障后可以停止采集。
- `packet_stats` 增加 `id_faults/byte_faults/fault_pending`；新采集清零计数，停止清除模拟故障。
  id 读取失败不伪造身份；序列化失败保留身份记录，并发布同 seq/id/dir 的失败字节记录：
  `serialized=false`、`size=0`、空 hex 与明确 reason。部分字节不作为成功数据发布，后台
  格式化诊断；线程重入时拒绝复用正在写入的 TLS 缓冲。成功记录字段不变。
- Python 解码器显式保留 `capture_error`，不把采集失败解码为空包或使用残留 hex。
  断开现场报告新增 packet_stats，即使 state 探测失败也保留采集故障计数与身份环。
- `selftest_packet_tap` 受自检压力/采集开关门禁保护，只在采集侧模拟一次异常。
  `driver/selftest.py --packet-tap-only` 使用真实发送的 PlayerAuthInput 和接收的 Text，
  分别验证 id/序列化的 SEH/C++、故障计数、按 seq 关联的失败记录、正常后续采集、
  关闭/重启和无新增 crash；没有构造违规封包或新增钩子，也接入默认完整自检。
- SDK 的 `BinaryStream` 仍向可增长字符串写入；同步序列化的分配和执行时间仍属第 12 项
  未完成范围。本批没有重写线格式、跨线程保留 Packet 指针、修改 SDK 布局或做新增逆向，
  不能把局部边界验收推广为零分配、整个游戏 tick 安全或性能提高。

## 第 12 项：封包来源模板与独占注入（2026-10-31）

- 移除 tickPacketInject、单槽 mChatPending 及模板 mutex/owning payload。发送钩子只复制
  定长 origin 字段和完整 requestId（最多 512 字节），局部 SEH/C++；模板环冷侧初始化，
  最新捕获 pending/失败时不复用旧值。玩家 owner UUID 与 origin UUID 分开保存，
  用世界/请求代次防止旧世界身份复用，自身注入不会重新捕获来源。
- chat_send 共用 StagedCapture 独占生命周期，spool 调度客户端平台队列；真实 SDK
  构造器创建临时上下文/payload，完整恢复捕获来源后构造并发送，不伪造 SDK 对象布局。
  构造与发送前复核世界、代次、截止和实际客户端玩家 UUID。SDK owning 对象在 raw SEH
  frame 外持有，已完成构造正常析构；构造中途故障与已开始发送不能抢占、回滚或自动重试。
- 接受和唯一 chat_sent 回执均增加 packet_id，保留 ok/text/reason；忙时明确拒绝，
  不覆盖文本。2 秒截止、退出/panic/内部错误取消，重连丢弃旧请求，这些边界同时使模板失效。
  超时/取消不读取仍由回调持有的 snapshot，失败保守 partial=true；成功仅表示调用完成，
  命令效果仍以 blocks/state 为准。Python 默认等接受，可选 wait=True 按编号等完成。
- 原生回归覆盖 requestId embedded NUL/512 边界、最新失败与 pending 不回退、身份/代次、
  发送前截止、运行中超时/替换、旧回调与并发环绕、生产模板采样零自有分配。离线
  **322/322**，ruff/mypy/profile 与两次完整 C++ 构建通过。现场专项
  **55/55**（含真实重连/退出/重进），覆盖前后 SEH/C++、模拟投递拒绝/超时、busy、
  来源失败/超限不回退、panic/tick 恢复取消、唯一终结、blocks 回读和恢复，无新增 crash。
  首轮 41/42 因测试等待未先消费主动触发的 internal_error 诊断，清理又撞恢复期；
  修正事件关联/恢复状态等待后通过，C++ 实现未因此改动。
  复现 `driver/selftest.py --packet-injection-only` 或
  `--packet-injection-world "超平坦测试"`，默认全套也接入；需同时开启两类封包能力。
- 通用 PacketTap::write 的 SDK 序列化分配与其余高频钩子边界仍待治理；本批不声称
  所有封包路径零分配、全 tick SEH 或性能提高，第 12 项与 13–16 仍未完成。
- **首轮历史失败，后续验收见下**：2026-10-31 18:07:19（截图时间 10:07:19Z），
  check_flight 的 finally 恢复 gamemode/fly/togglefly 后，reset 的服务端成功回执已返回，
  约 6 tick 后在 tick 842 收到 world_left。用户提供的错误详情为 Block /
  ClientDisconnection-90；对应 SDK 枚举为 BadPacket=90（不是 BlockMismatch=63 或
  InvalidCommandRequestPacket=50），尚未定位产生该原因的实际包/方向/调用链。
  最后状态 packet_capture=false、控制 held/cleanup/injecting 均清空，进程/管道仍存活，
  服务端已释放、HBUI 错误页仍在，worlds.ready=false；113 个既有 crash 文件无新增/修改。
  后续 task_operations 因无世界仍读取 feet_x，产生次生 KeyError，不能覆盖原始断开。
  此前旧版也有 HBUI ready=false，但没有其错误详情，不能据此断言同根因或排除本批回归。
  用户要求本轮只评估/给方案，需逆向则仅提供提示词；未执行反编译/地址定位/新增 hook。
  事件与状态分别保存在 out/reliability-packet-injection-disconnect-timeline.json、
  out/reliability-packet-injection-block-incident.json；转交提示词
  out/block-badpacket-investigation-prompt.md。当时只恢复了磁盘配置，运行中配置尚需重启加载；
  最终姿态恢复与完整验收也未完成，因此当时保留 919/919 基线。后续完整验收与恢复结果
  见「传送检查同步前提与恢复保护」，不以重放成功抹去本次失败。

### BadPacket(90) 断开的静态定位与重放（2026-10-31 后续）

- **转交报告中的设置点已静态闭合**：客户端里 `closeConnection(..., DisconnectFailReason::BadPacket=90, "")`
  全库只有一处 —— `ClientNetworkHandler::handlePacketViolation`（角色
  `@client_handle_packet_violation`，`0x1412FA0F0`）。只有
  `response == PacketViolationResponse::TerminateConnection(3)` 才走
  `onClientLevelExit(client, 5)` + `closeConnection(netId, 90, "")`（`0x1412FA243`）；
  非主客户端分支是 `onDisconnect(..., 90, ..., "DisconnectPacket")`（`0x1412FA315`）。
  也就是说：该路径是“有包被判协议违规且严重度 = 终止”，不是 BlockMismatch=63 或
  InvalidCommandRequestPacket=50 这两个断开原因；不能据此排除命令请求包作为触发包。
- **`Block` 不是方块**：`data/definitions/disconnection_errors/disconnection_error_messaging.json`
  里 `ThemeName=Packet` 的 `DefaultStrings.Codeword=Block`，该主题覆盖
  `WorldTransferNotPrimaryClient / UnknownPacket / UnexpectedPacket /
  InvalidCommandRequestPacket / BadPacket` 五个原因；错误页只显示码字 +
  `ClientDisconnection-<reason>`。码字不能反推具体原因。
- `ClientDisconnection-<n>` 文案由 `@client_disconnect_text`（`0x14049AE20`）按**连接种类**
  拼出，只说明“客户端这条连接”，不区分是客户端自己断开还是服务端下发 DisconnectPacket。
- **本批 chat_send 在该窗口未被调用**：`onPacketSent` 的模板捕获要求 `capture && sendEnabled`，
  事发时 `packet_capture=false`；`chat_send` 未被调用（`chat_sent=9` 全部来自 18:05 的
  55/55 专项）。所以不能把“新注入路径”直接归因为本次断开，也没有证据排除它之前留下的状态。
- **重放结果（未复现）**：全新进程 + 允许 capture/inject 后
  （1）全量默认自检 **990/991**（唯一失败 `teleport_keeps_rotation`，见下）；
  （2）先跑 `--packet-injection-world`（55/55，含 9 次真实注入、op_self、commands_enabled）
  再在同一进程跑全量，仍 **990/991**，同样只有 `teleport_keeps_rotation` 失败。
  两次都没有 world_left、没有新增 crash；有限重放未复现，不能排除本批低频回归或
  故障进程更长历史相关的状态，具体触发包仍未定位（unresolved）。
- `teleport_keeps_rotation` 在完整套件里 2/2 失败，但 `--teleport-only` 44/44、
  `--controls-only` 后再 `--teleport-only` 44/44 通过；此前“相机干扰”的解释未被证明。
  后续最小复现与检查修复见下一小节；当时完整套件未通过的日志保留。
- 自检加固（本轮已改）：新增 `lab.world_state()` 区分 `world / menu / disconnect_error /
  exiting` —— HBUI URL 本身不能当主菜单（断开错误页也是 `ui://./hbui/index.html`），
  只有 `worlds.ready=true` 才算菜单；`check_flight` 收尾后调用 `require_world()`，
  意外 `world_left` 立即抛 `WorldLost` 终止整轮并保留首个失败，`check_task_operations`
  不再用 `feet_x` KeyError 覆盖原始故障；`LEVIPILOT_PACKET_DUMP=<path>` 可让整轮打开
  包身份环并在结束时落盘（`packet_tap_dump`）。离线 **326/326**、ruff/mypy 全绿。
- 错误页自愈（本轮已改）：`lab.capture_disconnect_report()` 把 state/status/screen/worlds/
  diag/包身份环写进 `out/disconnect-<时间>.json`（每项 best-effort，管道中途断也能出文件），
  自检在 `WorldLost` 分支自动调用；`driver/dev.py recover [--world ...] [--force]`
  负责“记录 → 受控重启 → 可回菜单/重进世界”。后续审阅收紧强停：HBUI/ready=false
  不能证明已刷盘；保存门禁失败保留进程，只有显式 `--force` 才能强停。
  现场验收：世界里 `recover` 会拒绝（`state=world`），`--force` 走完
  “记录 → 强停 → 重启 → 回菜单 → 重进世界”并 exit 0；`recover` 连续两次端到端通过。
  错误页分类最初由 `WorldStateTests` 4 项离线覆盖，后续补至 6 项；真实断开页样本仍缺（`/kick @s` 与
  `/stop` 在本地世界都被拒），所以“错误页上跑 recover”这一步只有代码路径、无现场样本。
- 真实错误页验收（2026-10-31 北京时间 20:41，pid 31300，12:27:44Z / 北京时间 20:27:44 那次断开的进程）：当时 `state=
  disconnect_error`、`screen=ui://./hbui/index.html`、`worlds.ready=false`、`diag` 全 false。
  `recover` 记录到 `out/disconnect-20261031-204157.json` 后**自行退出错误页**：
  “记录 → 强停（pid 31300）→ 重启 → 回菜单 → 重进世界”，exit 0，回读 `feet=(0.50,-49.00,4.60)`；
  世界 `last_saved` 正好是断开时刻，重进成功；未做完整存档前后比较，不宣称无损。
  至此“错误页上跑 recover”有了现场样本。
- 强停边界据此细化（与上一条“只有显式 --force 才强停”并列，不覆盖它）：
  `world_state() == disconnect_error` 本身要求 `in_world=false`（关卡已卸载）+ 世界列表不可用 +
  HBUI，且两次现场 `diag.server_instance=false`（没有服务端可刷盘），所以**只有这一种状态**
  允许 `recover` 自停；`world / menu / exiting` 仍然保留进程、必须显式 `--force`。
  `kill_game` 的保存门禁在 45 s 超时后也先复核该状态，避免 `stop`/`run` 在错误页上干等。
- `check_flight` finally 的清理不再掩盖原始故障：清理第一条是 `client.command(...)`，世界里已没了时
  会先抛 `command_player_unknown`，把正在展开的 `WorldLost`/`ModTimeout` 顶掉。现在整段清理
  包在 `try` 里，展开中失败一律转成 `WorldLost("flight cleanup: level gone (<原异常>)")`
  并链上原异常；没有原异常时照常抛出清理自身的错误。
- 第二次现场（2026-10-31 12:27:44Z / 北京时间 20:27:44）：last tick 763，`flight_toggle_server` 通过后
  `wait_ticks(12)` 期间 world_left；`packet_records=0`、`send_hooked/recv_hooked=false`、
  `chat_sent=0`、模板尝试 0；这些只约束采集与显式注入，不能排除 mod 的能力修改通过
  游戏原生同步路径影响封包。两次现场（tick 842 reset 之后、
  tick 763 togglefly 之后）都落在 `check_flight` 内的能力变更附近，但触发包仍未定位。
- 该阶段的下一步（后续实现见下节）：把 `@client_handle_packet_violation` 做成只读诊断（定长环记录
  response / packetId / error_code / 线程 / tick；`PacketViolationHandler::checkForViolation`
  是 4 寄存器参数的更安全入口），下次复发即可直接给出触发包与错误码，再决定是否修
  包构造 / 状态同步 / 顺序。

### 违规守望诊断设施（2026-10-31 后续）

- **只读诊断已实现并现场验收**：新模块 `ViolationWatch.cpp` / `ViolationWatchModel.h`
  在 `@client_handle_packet_violation` 上装 **typed wrapper**（同签名 10 参数），记录
  `seq/tick/tid/response/packet_id/packet_size/error_value/category/context`
  到预分配 256 环。回调内零分配、零 JSON、零锁；始终以原参数调用原实现，
  不改 response、error_code、包内容或断开决策。`violation_watch / violation_stats /
  violation_drain` 三条管道命令，默认关闭、需 `allow_hook_va`、需在世界内，`panic`
  会一并卸下。
- **角色地址的重定位前提**（新发现，写进 `docs/roles/1.26.51.01.json` 的 `image_base` 与 notes）：
  roles 里的地址是 `image_base=0x140000000` 下的静态 VA；本机 exe 被 PeEditor 改写首选基址并
  ASLR，内存里的 `OptionalHeader.ImageBase` 已经是加载地址，**按头部换算会得到 not_mapped**。
  正确换算：`runtime = diag.module_base + (role_va - image_base)`；
  在 RVA `0x12FA0F0` 处 `mem_read` 到 `5541574156415541545657534881ec38`（标准函数序言）核对通过。
- 离线模型：`tests/native/violation_watch_model.cpp` 覆盖上下文截断/内嵌 NUL、环环绕、
  4 线程并发发布、发布路径零分配。
- 现场：`selftest.py --violation-only` **13/13**（装钩、`violation_stats`、合成记录 drain、
  游标、ping/命令不受影响、卸载、无 crash）；默认自检 **934/934**，
  导出 **0 条真实违规记录**（dump 里 2 行都是 `synthetic`）。源码核对发现设施自测的
  finally 与后续 `panic` 会卸下守望，因此撤回“整轮持续武装”的表述；934 项也不能
  替代开启两类封包能力时的 992 项历史基线。所以：
  设施已验证 ≠ 根因已查明；两次断开的触发包仍然 unresolved。
- 另一次对照（最小能力序列）：同一进程、只跑 `check_flight`（fly/togglefly/reset 与
  两者事发时相同的收尾链），显式武装、不开包采集 → **44/44**。
  该函数包含受控故障注入，撤回此前“不做故障注入”的表述；
  仍然 **0 条真实违规**（环里 3 行全是历史 synthetic，说明环跨运行保留）。
  与"完整自检 934/934、0 条"一起，两次都只说明"这两次没触发"，不构成未复现的证明。
- 下次观察：`LEVIPILOT_VIOLATION_DUMP=out/violation-<时间>.json py -3.11 driver/selftest.py`
  在默认入口初始武装并在退出时导出，设施自测和 `panic` 之后存在观察空窗。
  专项入口不读取该变量。只有在已确认武装、处理器实际经过观察点且记录未丢失的情况下，
  才能从新增记录中定位第一次违规；目前没有真实违规样本验证这条完整链。
- 2026-10-31 北京时间 **22:32** 补做关闭/开启两组正常能力操作对照。
  第一组关闭守望，在 `reset` 服务端完成（teleport_id=103）后的等待期间退档，
  tick **73123 → 73127**；**25 项通过后中止，第二组未执行**。
  本轮没有调用 `selftest_flight` 或合成记录；环新增 0，原三行均为历史 synthetic。
  这次只能确认退档发生于未武装组，错误页详情码待确认，不将它直接归为 BadPacket。
  首错保留为 `WorldLost`，真实输入已解锁，held_mask=0。证据：
  `out/ability-watch-contrast-20261031-223225.json`、同名前缀的 `-timeline.json` 与
  `-spool.jsonl`，以及 `out/ability-watch-disconnect-20261031-223232.json`。
  profile `--check` up to date、roles `--check` 50/57；本次不更新完整现场全绿基线。

- **错误页原因改为进程内可读（2026-10-31 深夜）**：`violation_watch` 同一个开关还会钩住
  `OreUI::EntryPoints::DisconnectScreen::composeRoute`（`MCAPI` 导出符号，不需要 VA/角色）。
  断线页每构建一次就记一行 `source=screen`：`response` = 原因码（界面上的
  `ClientDisconnection-<n>`）、`context` = 码字、`detail` = 详情文本。
  现场验证：`violation_watch on` 返回 `screen_hooked=true`；`disconnect_route reason=90`
  渲染出 `/disconnected?reason=90&…&codeword=Block&errorDetails=`，并立刻产生一条
  `{source:"screen",response:90,context:"Block"}` 记录 —— 这条链路不需要桌面点击。
  专项扩到 **16/16**。局限：安装只对之后的事件生效，本次之前已发生的断开无法补录；
  `detail` 依赖游戏传入的详情串，可能为空，且只保留前 191 字节并替换非 ASCII 字符。
  `source=screen` 的 `response` 是路由原因码，`source=callback` 的同名字段是违规处理等级。
  当前 `disconnect_route` 的人工路由记录也带 `synthetic=false`；必须根据命令来源和新增
  游标区间区分，不能当作自然违规样本。自然断开经过两类观察点的完整链仍未验证。
- **22:53–22:54 最小能力序列对照补验**：在当前 DLL、同一进程 pid 14664、相同配置、
  起始位置/能力下，先武装后关闭，各 **33/33**，没有使用故障注入、合成记录或人工路由。
  每组 11 个采样点核对 `active/screen_hooked`，起止游标都是 3、lost=0、新增记录 0。
  武装组 tick 9108–9218，关闭组 10796–10911；两组均无退档、无新增或修改 crash（113 个），
  配置/DLL 保持一致，结束恢复姿态/能力/站稳，释放输入、解锁并关闭守望。
  原始报告与对照摘要：`out/ability-watch-contrast-20261031-225320.json`、
  `out/ability-watch-contrast-20261031-225445.json`、
  `out/ability-watch-pair-20261031-225320.json`。profile up to date，roles 50/57。
  当前 DLL 为 `6afc3aa4…52c3ed6e8`，不同于 22:32 失败时的 `a84985f9…b464b788`；
  后者不能用作仅改变武装状态的对照。单次固定顺序样本未重现断开，根因仍 unresolved，
  不更新完整套件全绿基线，也不宣称自然错误页采集已验收。

### 入站 SetActorData 反序列化失败：首个真实违规样本（2026-10-31 23:38）

- **守望第一次抓到真实违规**（非合成），同 tick 861、同 tid 30828 两条：
  `{source:callback, response:3, packet_id:39, packet_size:34, error_value:104,
  context:"BinaryStream read() incomplete.readNoHeader fai"}` 与
  `{source:screen, response:90, context:"Block"}`。证据
  `out/diagnostic-full-final-violation.json`、`out/diagnostic-full-final-live.log`。
- `[常量]` 39 = **SetActorData**（`MinecraftPacketIds.h`），不是能力包。
  结合 `handlePacketViolation` 只在 `TerminateConnection` 才用 90，链条是：
  **入站 SetActorData 读到 EOF → 违规判定终止 → 客户端以 BadPacket(90) 关闭 → 错误页 codeword Block**。
  方向由此确定：客户端**接收**侧（单机即集成服务端发来）。
- **自有代码核对结论：未发现直接缺陷。** `mod/probe/src/mod/Flight.cpp` 的服务端分支在
  `queueServerTask` 内、客户端分支在 `queueClientTask` 内写 `Player::setAbility(MayFly/Flying)`；
  `ProbeMod.cpp` 的断言路径在 tick 线程写 LocalPlayer。写入前都核对世界代数/`inWorld`，
  玩家对象每次按 UUID 重新解析。**我们从不写** `SynchedActorData` / `DataItem` /
  `PropertyComponent` / `SetActorDataPacket`；事发时 `packet_capture=false`，tap 也没有调用
  `packet.write`。所以“自有 mod 直接写坏载荷”没有静态依据。
- **仍未闭合**（不猜）：34 字节载荷在读侧哪一步缺字节、`error_value=104` 的语义、
  写侧是谁在什么时机广播（`packAll` 取值、是否由 `Player::setAbility` 间接触发
  `SynchedActorData` 变更）、`PropertySyncData` 计数是否可能不一致。
  `context`/`detail` 上限 191 字节，超出部分仍不能补写。
- **`error_value=104` 已闭合**：用记录里的 `category` 指针 + 当轮 `module_base` 换算成
  RVA（`0xE6ABB80`），取类别对象的 vtable：`name()` 返回 `"generic"`、`message()`
  走 `std::_Syserror_map` ⇒ 是 `std::generic_category()`；Windows SDK `ucrt/errno.h`
  里 `EBADMSG = 104` ⇒ **`std::errc::bad_message`（数据无效）**，不是 Win32 错误码。
  该指针是进程内的，必须和当轮 `module_base` 一起记录才有意义。
- **读侧已闭合到函数级**：`SetActorDataPacketPayload` 的虚表（`getId`=39 /
  `getName` / `getMaxSize`=10 MiB / `checkSize` / `writeWithSerializationMode` /
  `read`=`0x143DF7060` → `_read`=`0x143DF7CC0`）与 104 的生产点
  `0x143DF7362` 都已定位；该分支等价于 SDK 的
  `ReadOnlyBinaryStream::ensureReadCompleted()`（`mHasOverflowed || size != readPointer`
  ⇒ `std::errc::bad_message`）。构造函数把序列化模式钉成 **5 = CerealOnly**
  （`0x1416B4320` 的 `mov dword [rdi+98h], 5`），所以读写默认都走 cereal。
- **判别已完成（2026-11-01 00:5x）**：`check_flight` 在 `LEVIPILOT_VIOLATION_DUMP` 下
  武装 `packet_capture(serialize_ids=[39,111,187,188])` 并导出 `<dump>.bytes.json`
  + `.drain.json`（接线已落地，5 次运行各 200–256 行）。成功基线：187 恒 33、188 恒 5、111 13–22。
  新样本 **188 UpdateAdventureSettings：发送侧恒定 5 字节，到达读侧 `packet_size=2`，
  读侧要 3 字节即溢出（error 29）**；加上三次失败横跨 39/111/188 三个不同包，
  **排除“SetActorData schema 问题”与“序列化模式 override”**（后者会让固定包稳定失败）。
  机制收敛为**入站分帧/长度与实收字节不一致**（发送侧长度前缀、压缩路径或传输层），读侧只是受害者。
- `packet_size` 语义**不必再赌**：样本 C 中 188 的发送侧序列化恒为 5 字节而回调给 `packet_size=2`；
  无论该字段是包体长度还是含包头整包长度，包头只会增加长度，**5 → 2 只能意味着到达读侧的帧更短**。
- 接收侧钩子尝试**失败并已回退**：`Packet::readNoHeader`（`sub_1416C2310`，反编译签名带 sret 隐藏返回）——
  按 `(Packet*, stream, ctx, subid)` 写的 typed wrapper 与真实 ABI 不符，两次现场均 `0xC0000005`
  （新增 `crash-36456`、`crash-21624`）。已删除安装路径，`inbound_drain` 明确回
  `supported:false / read_no_header_hook_retired`；回退后守望安装/停止恢复正常、无新崩溃。
- **截短层已定位（2026-11-01 02:0x）**：`violation_watch` 同一开关再装一只只读钩子，
  钩 `Packet::readNoHeader` 的**原始机器形态**（5 个指针参数、无寄存器返回；早期按
  `(Packet*, stream, ctx, subid)` 写的 typed wrapper 崩过两次，改成五个 `void*` 透传才稳）。
  不假设流在哪个参数，用 `readMemory` 逐个探测（`+0x28/+0x30/+0x38/+0x40`），
  实测**流恒在 r8**；`inbound_dump` 由 mod 直接落盘（多客户端时管道回复会被另一连接截走）。
  同一 `--flight-only` 运行四份 dump 对齐后：**发送侧 187=33、188=5，入站切片恒为 +2
  （187→35、188→7）**；而失败样本 C 的 188 切片只有 **2**（应为 7）。
  ⇒ **截短发生在解析器上游的帧切分/解压/传输层**，不在 payload schema、不在 cereal、也不在解析之后。
  再往下的子问题（**声明长度 vs 实收字节**）现为 `[推断]`：读侧是逐包解析失败而非批级长度校验失败，
  且失败前的同批包都解析正常 ⇒ 更可能是**发送侧分帧**（头声明 2）而非传输剪尾；
  定案需要在解压输出/逐包切分前记录 `(批总长, 首个声明长度, 剩余)`。
- **收束（不阻塞行动）**：解析链是网络运行循环 `sub_1419D7D90` → runEvents 任务
  `sub_1416AA7F0` → 逐包解析循环 `sub_1416AB0E0` → `Packet::readNoHeader`，属于 Bedrock
  网络层，**不是本仓库代码**；无论子情形是发送侧分帧还是传输剪尾，
  mod 都没有**直接**写入点（我们不写这些包、不碰批缓冲）。这**不等于完全排除 mod**：
  对象共享、时序与自有 hook 的 ABI 都可能间接影响组帧，尚未验证。因此本项按
  “根因层=游戏网络栈入站组帧，早于包解析；mod 无直接写入点、责任归属未闭合” 收束，
  子情形的判定仪器见下一条；三次钩子尝试已产生 3 个 crash（`crash-36456/21624/11356`）。
- **批入口角色更正 + 切片字节落地（2026-11-01 03:0x）**：静态复核发现上一轮登记的
  `@client_packet_batch_entry = 0x1416AA7F0` 实为 `NetworkSystem::runEvents task`
  （字符串、`NonOwnerPointer<NetworkSessionOwner>` 断言、`sub_1400A2400` 任务提交），
  `sub_1419D7D90` 是网络运行循环而非批循环；角色已更正为
  `@client_packet_parse_loop = 0x1416AB0E0`，错误的 batch 钩子与 `batch_addr` 已移除。
  实测 1024 条入站切片：**切片以包 id 开头**（`27`=39 SetActorData、`6f`=111 MoveDeltaActor、
  `90 01`=144 PlayerAuthInput），长度前缀已被切分器消费；187/188 的 `+2` 是它们 id varint
  的宽度，不是固定包头；失败样本 188 的 2 字节切片即“只剩 id、payload 为 0”。
  探针同时复制切片前 16 字节（`before_hex`），但实测那里是无关堆数据（指针/零），
  帧的 length 前缀既不在切片里也不在切片前 —— `slice_verdict()` 对 `declared==0` 判 `unknown`，
  自检遇不可读声明即 skip，不制造假判定；要拿声明长度必须钩写帧/切分层（未做，见证据册 §13）；
  顺带修掉 `inbound_drain` 一直超时的真实原因——`drainRing` 把事件名硬编码成
  `violation_drain`，客户端等不到 `inbound_drain`（此前的“两客户端抢管道”解释不成立）。
  自检新增 `check_inbound_slices`（行数/id 范围/已知 id/声明==实收占比，读不到声明即 skip）。
- **字符串上限不再是问题（2026-11-01 13:0x）**：游戏给的是 `std::string`，没有固定上限；
  记录行只保留有界前缀（context 191 B / detail 639 B）是为了定长环。新增 `violation_text`：
  回调里 `memcpy` 进 8 个预分配 4 KB 槽（零分配、零 JSON、零锁），drain 时整段取回。
  现场验证：605 字符的 details 原样返回（`len=605, truncated=false`），
  合成的违规 context 也完整；错误页整页约 570 字符，行内 detail 也已能装下。
- **闭合（2026-11-01 13:06，tick 1881）**：第三次自然复现同时拿到「失败切片字节 + 同 tick 发送侧 body
  + 完整错误文本」。失败切片 `bc01 0000000001 0e010007078080c08480838003000000`（23 字节）
  = 188 的 id(2) + 自己的 body(5) + **16 字节的邻居 SetActorData payload**（与同 run 的 39 切片
  逐字节同构，只差演员 runtime id）。正常 188 切片 61 条全为 7 字节，发送侧 188 body 63 条全为 5 字节。
  ⇒ **不是传输/解压剪尾，而是帧写出侧把邻居 payload 并进了本帧**；解析器读完 5 字节后剩 16 字节，
  `ensureReadCompleted` 判 `mView.size() != mReadPointer` → `bad_message(104)`（与“短切片”同一个
  error code 和文本）→ `BadPacket(90)` / `ClientDisconnection-90` / `Block`。
  帧声明值目前只是**旁证倾向 23**（证据册 §17）：1662 条切片按 tick 分组后，多包 tick 的首包尺寸与
  单包 tick 完全一致（187 恒 35、188 恒 7、111 恒 21/22/23），说明切分独立于后续包；若切分器直接用
  声明长度，失败切片的 23 即声明值。**长度字段本身尚未直读**，定案需要读到长度解码点或拿到批缓冲。
  父流探测为负结果（四参数与调用者栈均无命中），已记录以免重复。复现配方与三次样本见证据册 §15。
- 证据册：[`docs/notes/badpacket-setactordata-20261031.md`](notes/badpacket-setactordata-20261031.md)。
- 边界：本节的“设施已验证”与“根因未查明”不能互相替代；完整现场当前仍有失败
  （`aborted_world_left`），不更新全绿基线。

### 传送检查同步前提与恢复保护（2026-10-31）

- 在同一进程、真实输入上锁时独立重放两条传送：先从 yaw=30/pitch=0 改到 55/15，
  紧接不带角度传送。零间隔首轮实际仍为 29.993/0；其余零间隔有成功，间隔 1/4 tick
  的样本成功。它足以否定“必须有前序全套或真实鼠标干扰才失败”，不能推断 SDK 内部时序。
  API 现有契约是等待服务端调用完成，朝向/位置同步须回读；并未承诺同步完成回执。
- `wait_rotation` 在 3 秒内要求连续三个不同 tick 的 yaw/pitch 匹配，保持 0.1 度容差，
  先验证第一条传送的前提，再执行无角度传送。没有重发传送、放宽容差或把失败计作跳过。
  专项 `--teleport-only` **45/45**；离线覆盖旧姿态、重复 tick、采样缺口、yaw 环绕、
  截止和退档。证据 `out/rotation-order-results.json`、`out/reliability-rotation-teleport-live.log`。
- `wait_ticks`/`wait_still` 观察到无世界即抛 `WorldLost`，默认入口也先检查世界，
  task 检查无世界时停止后续执行。诊断文件写入失败不覆盖原始错误；解锁失败仍关闭连接。
- 首次完整重跑通过朝向检查后，在取消传送的 `panic` 等待处遇到回执顺序竞态：
  `error/phase=teleport/reason=panic` 可能先于 `panic_done`，普通 request 将其误当请求失败。
  `DiagnosticsCommands.panic` 现保留该类会话取消事件，等到自身回执；其他错误仍抛出，
  超时关闭连接防止旧无编号回执串到下一次 panic。离线覆盖两种顺序、命令拒绝、其他故障
  和超时；传送自检使用生产 panic 封装，专项仍为 45/45。首次失败日志保留为
  `out/reliability-rotation-full-live.log`，不计为成功验收。
- `world_state` 的候选错误页还要求四个服务端对象明确为 false，进度界面/缺失诊断归
  `exiting`，分类不证明磁盘保存完成。`recover` 的保存门禁失败不再自动强停，显式
  `--force` 才能放弃会话；自定义 `--exe` 正确传给重启。上述路径有离线回归，真实错误页
  的恢复仍缺现场样本，不以主动制造违规封包或绕过安全门替代验收。
- 收尾时发现 `wait_for_menu` 会把保存/加载进度页上的缓存世界列表误认作菜单；现必须
  `worlds.ready is True`，安全菜单可以没有存档列表，缺少 ready 不算通过。
  离线覆盖缓存列表、安全空列表和缺失 ready。真实菜单预检按预期 exit 1、0/1，
  留下 `aborted_world_left` 与诊断，不执行世界修改；这不计为完整套件失败。
- 最终验收：离线 **346/346**，ruff check/format、mypy（92 files）、profile 生成一致性
  和 roles 校验（50/57）通过，`xmake -j4 -y` 成功。完整现场 **992/992，无跳过**；
  命令包来源/独占注入真实重连/退出/重进 **55/55**，传送专项 **45/45**，传送真实生命周期
  **50/50**。`recover` 世界内不带 force 正确拒绝（exit 2），正常保存后的 ready 菜单
  执行 `recover --force --world` 重启重进成功（exit 0）；这不是断开错误页的现场样本。
- 最终恢复检查 **17/17**：姿态/模式/飞行与 mayfly/效果/背包及选中槽恢复，站稳、控制释放、
  解锁、会话停止和恢复状态 idle 均回读通过。原配置逐字节恢复并重启加载，临时
  packet_capture/packet_inject 均关闭；113 个既有 crash 文件无新增/修改。安装与构建/打包
  DLL SHA-256 一致：`85BFF73BFC2F82A1D92C8A7ACF6789B94B601463B8FC45CE175BFC5153395628`。
  证据 `out/reliability-rotation-final.json`；离线、完整、封包生命周期和传送生命周期日志分别为
  `out/reliability-rotation-offline-complete.log`、`out/reliability-rotation-full-final-live.log`、
  `out/reliability-rotation-packet-world-live.log`、`out/reliability-rotation-teleport-world-live.log`。
  BadPacket 的具体触发包与根因仍 unresolved；第 12 项其余 SDK 边界及 13–16 继续保留。

## 第 12 项：tick 内部错误恢复（2026-10-31）

- `reportInternalError` 改为定长错误载荷、原子恢复编号及输入释放；不再更新带锁请求、
  取消任务、调用 logger 或构造 JSON/string。诊断队列最多 32 条，消息上限 255 字节，
  截断有标记；后台替换非法 UTF-8 并格式化，溢出独立计数，不影响停控状态。
- 恢复分 Pending/Prepared/Running。后台清理移动/跳跃/视角请求，取消任务、宏、
  事件输入、飞行/传送、实体请求和快捷栏请求；游戏线程重试不确定释放，只有释放完成、
  读到清理后的有效 mailbox 快照，才按同一恢复编号开放准入。旧编号不能清除新故障，
  旧无期限移动/视角不会自动重放。两类宏按 internal_error 终结，新命令可以重新启动。
- 冷侧请求处理与恢复清理共用准入锁，锁在 dispatchWithSeh 外持有；结构化异常返回后
  正常释放。恢复期间控制命令返回 tick_recovering，查询、停止/解锁、世界生命周期及
  unknown_cmd 语义保留。status.tick_recovery 提供恢复编号、active 和阶段。
  这不是整个 tick 的 SEH 边界，也不能抢占/回滚已经开始的 SDK 或服务端调用。
  本子批次当时尚未覆盖封包模板/注入；现已在上方后续批次接入取消与来源失效。
  录制和查询继续用于观察故障与恢复，通用 PacketTap 及其余 SDK 边界仍待治理。
- 已验：原生覆盖旧编号、释放/新快照双门、UTF-8 截断、队列溢出、10 万轮并发与
  热路径零自有分配；离线 **318/318**，ruff check/format、mypy（90 files）和 profile
  通过，完整及最终增量 C++ 构建通过。现场专项 **51/51**，实际重连/退出/重进专项
  **57/57**，完整现场 **919/919**（无跳过）。覆盖前/后 tick 的 std/unknown/长消息/
  非法 UTF-8、释放失败重试、恢复中拒绝、dispatch SEH 后解锁、两类宏/任务终止与
  新请求恢复。首轮 18/19 因测试输出 GBK 无法打印 U+FFFD 中断，日志改转义后重跑通过，
  C++ 实现未因此变动。最终坐标/朝向/能力/站稳、释放/解锁、恢复状态均回读通过，
  113 个既有 crash 文件无新增/修改；安装/构建/打包 DLL SHA-256 一致：
  `0B0898A5337AAD369F47E96E56396441F8BA01314B1A19A49BCDAAFDC8E50159`。
  证据 `out/reliability-tick-recovery-final.json`，专项/完整日志均以
  `out/reliability-tick-recovery-` 为前缀。未做性能对照或现场热卸载。
  复现入口 `driver/selftest.py --tick-recovery-only`，另有
  `--tick-recovery-world "超平坦测试"`，默认全套也接入。

## 第 12 项：传送独占请求（2026-10-31）

- 从 RequestBlock/tick 移除传送槽、getRealName 与服务端任务分配。复用 StagedCapture 的
  独享生命周期，支持直接从 Server 阶段开始；spool 冷侧调度与格式化，局部 SEH/C++。
  服务端共用 UUID/世界代次解析器，不再名字兜底；每次修改前复核代次、世界和截止。
- 同域忙时明确拒绝，拒绝的 reset/checkpoint 不取消现有任务，不假报 checkpoint_loaded。
  正常接受与唯一终结带 teleport_id；保留成功字段，reset/checkpoint 仍是接受回执。
  2 秒截止、退出/panic 取消、重连丢弃旧请求；超时/取消消费者不读取在途快照。
  SDK 已开始的清效果/传送不可抢占或回滚，失败保守 partial，不自动重放。
- 普通传送/checkpoint 保留效果，reset 清效果；不传角度保留朝向，只传一个角度时另一个
  保留原零默认值。生产操作顺序提为 applyTeleport，native 验证副作用顺序、修改前拒绝、
  截止边界、旧回调隔离、运行中超时后替换和零自有分配。
- `driver/selftest.py --teleport-only` 已加入默认全套，另有 `--teleport-world WORLD`；
  覆盖前/后故障、队列拒绝、过期身份、busy、效果/朝向回读、唯一终结及恢复。
  用户正常退出旧游戏后已备份并安装新 DLL，传送专项 **44/44**，真实重连/退出/重进
  专项 **49/49** 通过；完整现场 **868/868**（无跳过）。桌面助手不可用未影响此次
  开发循环，未放宽安全门。
  离线全量 **317/317**；ruff check/format、mypy（90 files）、profile 一致性通过。
  完整构建及最终增量构建通过；完整现场/清理后 113 个既有 crash 文件无新增/修改。
  安装前信息保留在 `out/reliability-teleport-pending.json`，现场日志为
  `out/reliability-teleport-live.log` / `out/reliability-teleport-world-live.log`。
  最终坐标/朝向/能力/站稳及按键释放/输入解锁已回读恢复；安装/构建/打包 SHA-256 同为
  `823E6BC830F2CE8218907C0FBC3670E9DBE576FDFE201D1353A32AF154406E8E`。
  最终证据 `out/reliability-controls-teleport-final.json`，完整日志
  `out/reliability-controls-teleport-full-live.log`。未做性能对照或现场热卸载，第 12/14 项仍未完成。

## 第 12 项：移动/视角故障恢复（2026-10-31）

- 代码已接入：绑定读取、逐 HID/鼠标边沿、视角读取/应用、动作锁存与 legacy 控制各自
  使用局部 SEH/C++ 边界；tick 只累计固定原子诊断，spool 格式化 control_error。
  status.controls 暴露 held_mask/cleanup_pending/两类注入标记。没有新增 tick 锁、
  自有字符串/JSON 或常规路径分配；SDK 内部的分配不在本批保证内。
- 按下前保守记录 held，释放成功才清除；不确定的按键/鼠标释放在后续 tick 重试，清理
  完成前不接纳新的默认控制状态。失败消费并终止原移动/视角序列，需新请求重新启动，
  不自动重放可能已部分执行的动作。移动接受回执仍只表示已接收；control_error 按
  operation 汇总 faults/failures/unavailable，不是逐请求完成回执，也不保证一行对应一次调用。
- applyControls/applyTurnDelta 返回成功与否，导航/跳跃/路线/挖掘/放置传播 input_failed，
  宏按既有 bool 失败语义传播 playback_fault，实体转向传播 look_failed。按键注入在局部边界外恢复嵌套标记；
  视角采用计数 scope，支持客户端平台队列与 tick 的交错结束，不会恢复成遗留 true。
  默认路径仍是当前键位的 Hardware HID；legacy 只增加局部隔离与保守清理记录，未宣称
  所有 SDK 副作用可以回滚。旧冲量注释已明确限定为 legacy 历史现象。
- 已有证据：离线 **316/316**；独立相关回归 **5/5**，含实际生产边沿 helper 的部分失败、
  释放重试、嵌套标记、交错视角 scope、两生产者/消费者 20 万计数和零自有分配。
  首轮离线的一项失败来自源码检查仍以 void 签名截取函数，修正为 bool 后全量通过。
  ruff check/format、mypy（90 files）与 profile 一致性通过。
- 完整 C++ 构建已通过；同模块未使用的 Key 常量随本批清理。新 DLL 已安装，专项
  **126/126** 通过。首轮为 123/126：宏 bool 失败的既有原因是 playback_fault，
  自检误写 playback_failed，已按生产模型修正；另一次朝向检查失败，复跑前增加每轮
  服务端朝向基准恢复，避免前一轮故障后局部转向/迟到同步影响下一轮，仍保留 0.05°
  停止后变化容差并输出实际数值。不把该次失败的具体同步来源描述为已确认机制。
- 新增复现入口 `driver/selftest.py --controls-only`，默认全套也接入：调用前/后 SEH/C++、
  不可用返回、实际键位与游戏输入回读、视角失败不重放、闭环任务/两类宏失败传播及恢复。
  局部边界、恢复及无新增 crash 均通过；日志 `out/reliability-controls-live-final.log`。
  本批合并传送后的离线 **317/317**，完整现场 **868/868**（无跳过），构建/静态/profile
  与安装哈希一致，最终清理和无新增 crash 已核对，详见上节。默认 HID 行为保持；
  封包注入、内部错误路径及其余 SDK 边界继续作为第 12 项推进。

## 第 12 项：玩家身份快照与服务端解析（2026-10-31）

- 删除每 tick 的 getRealName owning-string 调用及 mPlayerNameMutex/mLocalPlayerName。
  `PlayerIdentityStore` 固定发布 UUID/epoch/tick；原子载荷与最多 8 次序号验证，读失败
  不返回上次有效身份。局部 SEH/C++/空 UUID 都发布无效快照，累计计数从
  `status.player_identity` 可见。退出/卸载复用姿态 epoch 使旧世界身份失效，重连同一
  世界继续使用当前身份；不新增 tick 锁、字符串、JSON 或自有分配。
- 背包四组写入、命令、权限及服务端物品使用改为捕获值身份，共用仅在服务端线程调用的
  `serverPlayerContext`。解析前复核 epoch 与世界状态，指针只在回调中短暂使用；物品
  使用的解析仍在任务锁内的局部 SEH 帧里，防止异常跳出锁。领域成功字段/错误语义保留。
- 明确的行为收紧：默认命令按 UUID 创建 PlayerCommandOrigin，可显式指定完整玩家名；
  身份未知/玩家不存在明确失败，不再静默回退到 Owner 权限的控制台 origin。避免 @s/~
  或权限上下文在缺玩家时改变。传送/实体/飞行独立解析未统一，第 14 项只算部分推进。
- native 验证 20 万次发布、四读者、失败立即失效、空 UUID、旧 epoch 拒绝和零自有分配。
  现场专项覆盖 SEH/C++/空 UUID 后命令与四组写入拒绝、背包回读不变及恢复；保留实际身份
  后退出/重进，即使 UUID 相同，生产解析器仍拒绝旧 epoch。未把这项描述为延迟真实写入
  队列或已开始调用的抢占测试。
- 验收：native 模型专项 **18/18**、离线 **315/315**、身份专项 **41/41**、完整现场
  **698/698**（无跳过），构建、ruff check/format、mypy（90 files）和 profile 一致性通过。
  安装/构建/打包 DLL SHA-256 均为
  `1B0BDDD6BE2CD44410D4D18E2B09A780FAD4E851DBFD540B4EA37D705DBD8468`。
  113 个既有 crash 文件无新增/修改，未做性能提升对照或现场热卸载。
- 收尾限制：完整自检通过后，恢复 gamemode/fly 期间出现 world_left，后续 togglefly
  返回 not_in_world。主菜单为 HBUI，管道仍响应，diag 的 server_instance/level 等均为
  false，但 worlds.ready 持续 false；正常 leave_world 和带安全门的重进仍返回 world_busy。
  未确定退出触发来源，也未证明与本批代码有因果关系；桌面助手 native pipe 不可用，
  保留游戏进程，未强杀/绕过安全门。任务、两种录制、宏已停止并请求输入解锁，但最终
  玩家姿态未恢复，不能宣称最终已站稳。退出异常单独列为待复核，不隐藏在通过数字里。
- 复现：`driver/selftest.py --player-identity-only` /
  `--player-identity-world "超平坦测试"`；日志 `out/reliability-identity-*`，
  最终状态、哈希和清理限制在 `out/reliability-identity-final.json`。

## 补充待复核：完整套件后的世界退出（2026-10-31）

- 优先复核 `World.cpp::menuReadyOnClient/leaveOnClient` 的逐条件诊断，以及清理命令与
  ClientExitLevelEvent 的关联。当前只知道安全门拒绝启动，不能断言门本身错误或放宽条件。
- 用户正常退出旧进程后重新启动恢复；本批真实进出世界专项 49/49、完整 868/868 与
  最终姿态/能力/站稳恢复通过，未复现此前收尾退出。本项根因仍未确认，逐条件诊断仍待
  补齐，不把一次恢复等同于根因修复；原清单仍为 11/16 完成、第 12 项进行中。

## 第 12 项：飞行请求与重断言（2026-10-31）

- 删除 tick 中的玩家名复制、服务端任务分配/投递与飞行 JSON。`fly/togglefly` 共用
  独占 `FlightCapture`，客户端平台队列采集 UUID/完整名字兜底并应用能力，服务端队列
  解析当前玩家并应用；局部 SEH/C++ 隔离，spool 推进阶段和格式化。保留原成功事件与
  字段，增加接受通知 flight_queued、flight_id 和两侧应用结果；fly_ok 现在等待两侧调用。
- 2 秒截止、flight_busy、退出取消和重连丢弃未入传输队列的旧结果。失败保守标记
  partial=true，已开始的游戏调用不能被超时抢占或回滚。名字截断时禁用 fallback，
  防止用不完整身份找错玩家。成功只表示两侧调用完成，不保证游戏持续保留 Flying。
- toggle 保留 8 tick 重断言；编号、目标和测试模式在同一原子字发布，tick 无自有分配、
  请求锁、字符串或 JSON。取消/替换撤销编号准入，迟到回调不能复活旧重断言；一次
  重断言异常停止该编号，固定队列交后台报告。若请求已成功，后续失败单独发
  flight_assertion_failed。原生覆盖预算/取消/迟到/旧失败不清新请求/溢出及零自有分配。
- 首轮现场 47/48 的失败来自地面持续飞行前提；三次空中诊断均保持 Flying。第二轮
  49/50 暴露传送后 on_ground 尚未更新，最终改用 settle 回读离地并确认高度，再验证
  飞行保持；随后传回并验证站稳。没有为通过测试而强行保持地面 Flying。
- 最终离线 **314/314**、专项现场 **50/50**（真实重连/退出/重进）、完整现场
  **663/663**（无跳过）；最终构建、ruff check/format、mypy（90 files）及 profile
  一致性通过。安装/构建/打包 DLL SHA-256 均为
  `5BE37BCE5543B2607F1A73D5594009271E5A29B492FF45BD05F6137938EE4E2E`。
  113 个既有 crash 文件无新增/修改，最终站稳、输入解锁，任务/两种状态录制/宏停止。
- 复现 `driver/selftest.py --flight-only` / `--flight-world "超平坦测试"`；日志与诊断
  `out/reliability-flight-*`。SDK 内部、移动/视角、玩家名、封包注入等边界仍需治理；
  未现场热卸载或做受控性能提升对照。第 12 项保持进行中，13–16 未完成。

## 第 12 项：事件输入、文本顺序与 UI 查询（2026-10-31）

- 移除 tick 的文本锁/共享字符串与逐 HID 事件错误字符串。事件使用生产原生模型
  `InjectedEventQueue`，128 项同时保存输入和结果，消费完成前不复用，满时明确拒绝；
  单事件 SEH/C++ 边界、逐项发布结果和注入标记恢复，避免故障后整批重放。
- 文本改为请求独享的 `TextCapture`，忙时拒绝覆盖；前序按键先进入游戏，平台队列
  注入文本后跨两个 player tick 边界，才释放后序 Enter。2 秒截止与唯一编号终结；
  文本失败/超时取消后序旧输入，仍向同一连接报告 input_cancelled。重连才丢弃未入队的
  旧代次回执；已在传输队列的历史行遵循 replay 契约。默认移动/Space 的 HID 路径未变。
- `screen/key_state` 移到独立 `UiCapture` 的客户端平台队列，spool 格式化；保留原字段，
  显式 busy/截止/取消。文本在途不占用 UI 查询槽，主菜单仍能读 screen。
  输入代次改变后 tick 先释放跟踪的 held 键，释放失败保留重试。
- Python `text_input` 默认仍返回 queued；可选 wait=True 按编号等完成并传播失败，
  旧 mod 无编号时回退接受回执。完成仅代表注入调用/消费窗口，不证明输入框采纳；
  现场用实际聊天命令回显验证正常顺序，并验证失败文本后的 Enter 被取消、聊天保持打开。
- 完整首轮 612/614 的两项输入锁前提错误已修正：原 task 生命周期的三次重连会按设计
  解锁，却未恢复原锁状态；新专项明确上锁并回读，task/CLI 重连恢复原锁。
  修正后旧版本完整 615/615；其后补入文本失败取消的生产逻辑与现场检查。
- 最终验收：离线 **313/313**、专项现场 **51/51**（真实重连/退出/重进）、完整现场
  **619/619**（无跳过），最终构建、ruff check/format、mypy（90 files）与 profile 一致性
  通过。安装/构建/打包 DLL SHA-256 均为
  `34F743E7C41FD699DE6BFB96F66888DD8428DF7C861F7AB01B28A4205A81622E`。
  113 个既有 crash 文件无新增/修改，最终站稳、输入解锁，任务/两种状态录制/宏停止。
- 原第 12 项仍有移动/视角/飞行、玩家名、封包注入/模板与其他 SDK 分配/读取边界；
  第 13–16 项继续保留。未做现场热卸载或受控性能提升对照；SDK 内部不在原生队列的
  零分配保证内，held 释放失败重试的故障分支尚未现场注入验证。

复现 `driver/selftest.py --input-events-only` /
`--input-events-world "超平坦测试"`；日志 `out/reliability-input-*`。

## 第 12 项：ECS 所属线程与独享快照（2026-10-31）

- 移除 tick 的 ECS 请求锁、可覆盖单槽、JSON 与队列分配；五个接口统一冷侧准入，
  客户端平台队列读取客户端 registry，服务端队列读取 runtime-id 对应实体。context
  只先复制 registry 身份/entity id，匹配当前 registry 后在所属线程访问；陌生 registry
  拒绝。旧成功字段保留，增加 scope/registry/partial/visited/truncated 等诊断。
- 提取 `StagedCapture`，让实体与 ECS 共用 2 秒截止、取消、唯一消费和重连丢弃；
  旧实体错误语义保留，原实体 native 与完整现场继续验证。排队回调只持弱引用，超时
  消费者不读取运行中快照，旧回调不能碰新请求。游戏调用已开始后不能超时抢占。
- 每请求固定快照约 37 KiB；最多 1024 条结果/失败 id、4096 次 storage 访问、512 字节
  原始组件数据。逐 storage 的 contains/index/value/stride 都有局部 SEH/C++ 边界，
  坏条目保留 skipped/partial，其余结果继续返回。profile 名称与尺寸仅在后台格式化。
- native 验证容量/无结果仍有预算/故障条目/零分配、两阶段跨线程发布、500 次交接，
  以及运行中超时后新请求独立、退出取消、C++ 失败与消费时重连丢弃。
  最终专项 **51/51**（真实重连/退出/重进），完整现场 **575/575**（无跳过），离线
  **308/308**、完整构建与静态/profile 通过。安装/构建 DLL SHA-256 均为
  `70C229A1EA2F11ABFB985327C71A2B213223589F39CD679416B3616B53F3A3C0`。
  额外只读复核：可读的非 context 缓冲被拒绝为 context_not_found；113 个既有 crash 文件
  无新增/修改，最终站稳、输入解锁且任务/两种状态录制/宏停止。
- 首两轮现场的测试预期已修正：JSON 类型错误沿用统一 bad_command；组件列表间一个临时
  标签 id=4071315670 消失，142→141 且两侧 skipped=[]，恢复检查改比故障列表及稳定已知
  组件，避免要求游戏动态标签始终相同。诊断在 `out/reliability-ecs-storage-flow.*`。
  未现场热卸载；本批不宣称整个 tick 零分配或性能提升，第 12/13 项其余范围仍未完成。

复现 `driver/selftest.py --ecs-capture-only` /
`--ecs-capture-world "超平坦测试"`；日志 `out/reliability-ecs-*`。

## 第 12 项：宏播放与点击边界（2026-10-31）

- `MacroPlayback` 独占帧存储，冷侧统一加载/范围/循环/编号；tick 单次 CAS 准入，无会话锁、
  shared_ptr 复制、JSON 或自有分配。冷侧换帧/启动用 Writing 门等在途 lease 退出，
  完成在后台按 run_id 唯一消费；换帧前排出旧终结，避免名字或帧属于新一代。
- 应用/释放各有局部 SEH/C++ 边界，失败后恢复注入标记并请求清理；释放失败保留 pending
  重试，新运行首帧前也先清理。旧绑定释放失败保留绑定；普通 stop/load/task 不额外报告
  正常完成，手动 stop 回执后的释放故障仍有独立计数诊断。主菜单拒绝启动，世界退出取消
  与启动前后 epoch 复核共同隔离。保持范围结束时机和两种鼠标回放模式、默认 Hardware HID。
- 点击改为 128 项定长队列与后台回执，SEH/C++ 和队列满显式报告。Python 跑酷两个入口
  按 run_id 隔离旧终结；播放失败先排完状态样本再报错，单次录制保留部分 JSONL。
- 现场专项 **56/56**（含真实退出/重进），完整现场 **536/536**（无跳过），离线
  **307/307**；跑酷最后调整后补跑 **11/11**。native 覆盖 200000 帧零分配、区间/循环、
  在途停止与帧寿命、故障优先及 5000 次并发停止/换帧/重启。构建、ruff check/format、
  mypy 与 profile 一致性通过。安装/构建 DLL SHA-256 均为
  `6E461B3FD8E1034F6C1A1C367C7B108F5B44A55FDDE047AB349AC1E253D1D1FC`。
  无新增/修改 crash 文件，最终站稳、输入解锁且任务/两种状态录制/宏停止。
- 复现 `driver/selftest.py --macro-playback-only` /
  `--macro-playback-world "超平坦测试"`；日志 `out/reliability-macro-playback-*`。
  未现场热卸载、未做性能提升对照；SDK 内部不在模型零分配保证内。ECS/输入等其余边界
  仍属第 12 项；宏录制/输入完整所有权仍属第 13 项，不能因播放局部封装而标记完成。

## 第 12 项：二进制录制失败传播（2026-10-31）

- `RecordBuffer` 持有 record_id 与终结原因；SEH/C++ 采样失败停止本次录制，故障帧不发布，
  已发布部分帧保留。正常停止等待已准入采样，失败优先且重复停止不覆盖结果；初始化失败
  保留上一代数据。退出/卸载无等待取消，冷侧只发送一次 `record_failed`，新 start 前排出
  上一代未报告失败。启动前后校验世界代次；卸载等待采样后补排未消费结果并刷盘。
- 保留原 stop/dump 字段，增加会话、失败与 partial 元数据。Python checked stop 明确抛错，
  dump_all 默认拒绝失败/丢行；allow_partial 可读部分帧，仍拒绝跨 record_id。实验会话在
  抛错前保留 entries，主体异常不被清理失败掩盖。旧 mod 缺少新增字段仍兼容。
- 首轮完整现场 **483/484**，唯一失败为实体朝向比较了移动猪的后续坐标；另行复核时，
  目标在两次查询间移动约半格，回执与玩家实际朝向一致。`look_at_done` 增加实际使用的
  eye/target 阶段快照，验收用它们独立重算 yaw，回执误差 <0.001°、玩家回读 <0.1°，
  不再使用旧 20° 容差。最终完整现场 **484/484**（无跳过），回执重算误差约 0.000002°。
- 最终专项 **33/33**（真实退出/重进），离线 **305/305**；最终生命周期模型调整后补跑
  native 回归通过。原生覆盖失败优先、在途停止/取消、唯一通知与初始化失败保留；现场
  JSON/hex 部分帧逐条一致，head 停止增长，Python 明确失败、原异常保留及新会话恢复。
  ruff check/format、mypy、profile 与完整 C++ 构建通过。安装/构建 DLL SHA-256 均为
  `0C7599A84A80EDF5369391498062E74C43F87356C1D9EA7D3AA2EC52CA9C9540`。
  无新增/修改 crash 文件，最终站稳、输入解锁且任务/录制/宏停止。
- 复现 `driver/selftest.py --binary-capture-only` /
  `--binary-capture-world "超平坦测试"`，日志 `out/reliability-bin-record-*`。
  未现场热卸载 DLL；未进行满容量长时现场环绕或性能对照，不宣称整个 tick 零分配。
  宏播放、ECS/输入等其余路径和 SDK 分配仍待治理，第 12 项保持进行中，13–16 未完成。

## 第 12 项：钩子调用与姿态快照（2026-10-31）

- `hook_call` 从松散 pending/scale 原子量迁到独占 `SnapshotMailbox`，tick 只执行局部 SEH
  包裹的游戏调用、填 POD；spool 格式化成功/失败回执。在途 busy 不覆盖参数，非有限 scale
  被拒绝，2 秒截止；主菜单拒绝，退出/卸载取消，重连丢弃旧在途结果。已开始的游戏调用
  不能被超时抢占。保留 hook_call_done/symbol/scale，失败带 error/phase=hook_call。
- 原逐字段姿态原子量改为 `PoseStore`：单 tick 写者发布无填充的原子 word 载荷，读者
  最多 8 次序号校验，不等待写者。脚部/朝向不能混入其他 tick。局部 SEH、C++ 异常及
  非有限数字使本帧失效；后台报告故障计数，最终卸载同样排出。世界 epoch 隔离旧姿态，
  checkpoint_save 在主菜单/没有当前有效姿态时返回 no_pose，成功回执增加 tick/pose_epoch。
  崩溃报告同样使用有界一致快照并带 pose_valid；本轮未故意崩溃验证 dump 内容。
- 原生回归覆盖 200000 次并发姿态发布、一致载荷/代次/失败失效/零分配，以及钩子请求
  参数保护、超时后拒绝迟到执行、取消与异常。最终专项现场 **32/32**（真实重连/退出/重进），
  完整现场 **459/459**（无跳过）、离线 **299/299**；最终模型改动后补跑两个 native 用例，
  完整 C++ 构建、ruff check/format、mypy 和 profile 检查通过。
- 最终安装/构建 DLL SHA-256 均为
  `86AA34E86C7BBC9BC06D89A3A60072C9F562883376F6ECAE8E966AF8D2B0EA73`。
  无新增/修改 crash 文件，输入解锁、任务/录制/宏停止并站稳。
  复现 `driver/selftest.py --tick-safety-only` /
  `--tick-safety-world "超平坦测试"`，日志 `out/reliability-tick-safety-*`。
  第 12 项仍在进行：宏播放、ECS/输入等其余读取/格式化/锁及 SDK 分配仍待逐项治理；
  二进制录制的既有异常报告/失败传播仍需现场补验，不因本批通过而自动完成。

## 第 12 项：宏录制采样（2026-10-31）

- 录制帧、旋转差分和终结结果收进实际生产 `MacroRecordSession`。冷侧启动先分配并
  初始化全部帧槽，再发布会话；tick 单次 CAS 准入，不取会话锁、不增长 vector、不格式化。
  输入/朝向读取使用局部 SEH，故障只终结本次录制；停止等待已准入读取退出，故障优先于
  并发正常停止，后台只报告一次终结。首帧重建角度基线，yaw 用有界 ±180° 差分。
- 保存使用停止后的独享快照，按 record_id 清理；旧保存不能擦新会话。故障/退出后的
  部分帧保存明确 ok=false/partial=true，Python 传播失败和文件路径。先序列化再打开文件，
  检查 write/flush/close，失败保留缓冲供重试。点击 latch 不再因 held=true 短路而残留到松开后。
- 原生回归运行完整 200000 帧，覆盖零分配、并发状态读取、在途采样/停止竞争、故障优先、
  启动分配失败保留旧数据和跨会话旋转基线。专项现场 **29/29**（含真实退出/重进）、
  完整现场 **435/435**（无跳过）、离线 **298/298**；ruff check/format、mypy、profile 与
  完整 C++ 构建通过。真实目标打开失败保留 3 帧并重试保存成功；未模拟写满磁盘，
  未实现原子文件替换，现场未连续录制 200000 tick。
- 安装与构建 DLL SHA-256 均为
  `D29E1160F03B5227D573316DE639113FF5C1F4476840374C01EC62FB7C9F1134`；
  无新增/修改 crash 文件，最终输入解锁、任务/录制/宏停止且站稳。
  复现 `driver/selftest.py --macro-capture-only` /
  `--macro-capture-world "超平坦测试"`，日志 `out/reliability-macro-capture-*`。
  宏播放、hookTick、recordTickPose/其余游戏读取与 SDK 分配仍待治理；第 12 项保持进行中，
  这次录制会话封装不等于第 13 项 MacroSession/InputSession 整体完成。

## 第 12 项：JSON 录制与跑酷事件（2026-10-31）

- `start/stop` 改为固定 256 行单生产者队列，tick 不再构造 JSON/自有 string 或取传输锁；
  局部 SEH 保护游戏读取，SDK 方块名 const 引用直接拷入定长 UTF-8 文本。命令/spool
  使用冷侧锁推进会话，停止先等待已准入读取、排出样本再回执；最大条数终结同样有序唯一。
  增加 record_id/rows/ok，故障、缓冲满和离开世界明确失败；重复 stop 保留失败结果。
  参数错误/重复 start 不重置旧会话。队列基础设施有零分配检查，不宣称整个 tick 零分配。
- 跑酷事件用 tick-owned POD reducer 与固定队列，后台生成原事件，故障后和世界切换重建
  血量/落地基线，丢失/采样故障可见。Python 按录制编号消费并传播失败；跑酷工具识别真实
  `macro_done`，结束后排完采样再返回，保留异常前的部分样本。
- 原生回归覆盖 10 万次并发队列交接、满队列/环绕/零分配、停止撞上已准入采样、失败覆盖
  并发正常停止、唯一终结与跨故障/世界基线。现场专项 **39/39**（含真实退出/重进）、
  完整现场 **411/411**（无跳过）、离线 **296/296**；构建、ruff check/format、mypy 与
  profile 检查通过。4 tick 宏得到 5 条连续样本；故障后真实落地距离 2.9216，恢复正常。
  overflow 用受门控的 3 行有效容量/延迟消费验证生产分支，未制造 13 秒真实后台阻塞。
- 安装与构建 DLL SHA-256 均为
  `60756A2AB9FE4BD845C13F05C1AD9736B7B742670B99107DCD51D1EE29D19114`；
  无新增/修改 crash 文件，最终输入解锁、任务/录制/宏停止并站稳。
  复现 `driver/selftest.py --tick-stream-only` / `--tick-stream-world "超平坦测试"`，
  日志 `out/reliability-tick-stream-*`。宏采样/锁、其他游戏读取与 SDK 分配仍待治理，
  第 12 项保持进行中；不因此推进第 13–16 项为完成。

## 第 12 项：实体读取与快捷栏回执（2026-10-31）

- 实体请求改为独享分阶段会话：客户端平台队列读取上下文，服务端队列查找/读取/攻击，
  必要时客户端用值快照转向和交互。移除 tick 的实体锁、名称复制与任务分配；三个阶段
  各有局部 SEH，JSON 在 spool 格式化，SDK owning string/vector 放在 raw SEH 帧外。
  最多 512 行、约 2.2 MiB/请求，文字截断显式报告。2 秒截止、busy、退出取消和重连丢弃，
  弱引用队列与独享快照隔离迟到回调。客户端投递/执行失败支持拥有者回调。
- 快捷栏仍在原 tick 后应用，用户请求用固定快照加局部 SEH，后台生成兼容的完成回执；
  2 秒截止、busy、退出取消和重连丢弃。内部任务选择不再产生用户回执。
  SDK 物品类型名称仍分配；已开始的游戏调用不能被超时抢占，不宣称整个 tick 零分配。
- 原生模型覆盖跨线程三阶段发布、500 轮交接、边界槽位/旧物品清空、超时中仍运行的回调、
  新请求隔离和重连时丢弃已消费结果。查询专项 **99/99**、真实生命周期 **16/16**、
  完整现场 **377/377**（无跳过）、离线 **289/289**，构建、ruff check/format、mypy 与
  profile 一致性通过。安装/构建 DLL SHA-256 均为
  `B55D3DD56823A4C0CCAE438A4B8713054197E7465DFCD3AC53AD1C708F0063C5`，无新增/修改 crash 文件，
  最终输入解锁、录制/任务/宏停止。实体攻击回读血量 9→8，交互仍为 best effort。
- 复现：`driver/selftest.py --query-capture-only`、
  `driver/selftest.py --query-lifecycle-world "超平坦测试"`；日志 `out/reliability-entities-*`。
  剩余为 legacy/跑酷事件/宏采样与锁、其他 SDK 分配及游戏读取边界，第 12 项保持进行中。

## 第 12 项补充：Overhead 并发统计（2026-10-31）

- 新增 `OverheadWindow`，复用 `AtomicRing` 的原子载荷与逐槽发布，移除普通 Sample 的
  跨线程读写和仅靠 epoch 的伪保护。读取窗口有界重试，失败返回显式 busy，不计算无效
  分位数。采样路径不再初始化/分配/加锁，QPC 频率用原子值；路径计数移除首次并发调用
  时可能丢增量的第二计数器重置。旧指标、别名保留，增加采样区间。
- native 覆盖 20 万次写入与四个读者、环绕顺序/载荷、边界/失败窗口/零分配，及多线程
  路径计数；专项现场 **7/7**，实际推进 **1100 tick**，**1741 次**读取均得到一致窗口。
  完整现场 **330/330**（无跳过）、离线 **288/288**，构建、静态与 profile 检查通过。
  安装/构建 DLL 哈希相同、无新增 crash 文件；未执行性能提升对照实验。
- 复现：`driver/selftest.py --overhead-only`；日志 `out/reliability-overhead-*`。
  第 12 项其余实体读取、快捷栏回执、legacy/跑酷事件/宏采样及 SDK 分配仍待治理。

## 补充修复：世界进入看门狗（2026-10-31）

- 用 `WorldEntryModel` 替换永久依赖事件清除的标志；每次进入有独立编号与 30 秒截止。
  spool 推进截止，平台主线程探测实际玩家/HUD、菜单与服务端生命周期，观察回调绑定
  世界 epoch + 进入编号，单个回调的 C++/SEH 失败不会中止观察。启动 SEH/C++ 失败只发
  一次相关终结并保留隔离。迟到加入更新真实 Playing 状态但不重复终结。
- 隔离时拒绝再次启动；只有无玩家、菜单就绪、两侧退出完成、can-start 且服务端指针为空，
  连续确认 500 ms 后恢复准入。`status.world_entry` / `worlds.entry` 增加诊断，
  `worlds.ready` 包含隔离门，Python 按 `entry_id` 匹配结果并保留旧 mod 兼容。
- native 覆盖生产 30 秒边界、旧编号、连续恢复窗口与 1000 次加入/超时竞争；
  现场专项 **30/30**，含门控 700 ms 超时/模拟恢复阻挡及真实启动时丢弃 join 事件的恢复。
  未制造真实服务端挂死，不把短时模拟描述为长时卡死验证。
- 同批完整回归发现任务局部 SEH 的 crash dump 误报，增加边界归属与明确回归；
  一次放置 `use_failed` 在原坐标三次诊断中未复现，测试增加导航后回位、连续站稳和
  目标实体避让，调整后连续三轮 **42/42**。这是测试前提治理，不宣称所有放置拒绝都被修复。
- 最终离线 **287/287**、完整现场 **324/324**（无跳过），构建、ruff check/format、
  mypy 与 profile 一致性通过；安装/构建 DLL 哈希相同，最终故障检查无新增 crash 文件。
  复现：`driver/selftest.py --entry-lifecycle-world "超平坦测试"`；
  日志 `out/reliability-world-entry-*`。第 12/15 项其余范围保持未完成。

## 第 12 项：背包采样后续（2026-10-31）

- inventory 迁入独占定长快照，复用 state 的物品快照与局部 SEH；JSON/传输移到 spool，
  有 2 秒截止、busy、退出取消及重连丢弃。最多 256 个背包槽，显式报告文字/槽位截断，
  原 slots/armor/offhand/selected_slot 字段保留，增加 sample_us，固定存储约 1.1 MiB。
- 离线 **283/283**、查询专项 **52/52**、完整现场 **305/305**（无跳过），静态检查、
  profile 一致性及完整构建通过；安装与构建 DLL 哈希一致，受控异常无新增 crash 文件。
  原生模型覆盖边界槽位、旧物品清空与基础设施零分配。日志 `out/reliability-inventory-*`。
- 第 12 项仍在进行：实体前置读取、快捷栏回执、legacy/跑酷事件/宏采样、SDK 名称分配
  及 Overhead 并发问题尚未完成。

## 补充修复：宏录制硬上限（2026-10-31）

- `MacroRecordModel` 统一有效上限，省略/0/超大值均按 200000 tick；到顶停止且保留数据，
  不再静默丢帧而保持 recording=true。开始前验证并预留容量，再替换缓冲；参数/分配失败
  不破坏旧录制，追加不增长 vector。开始回执与 status 暴露有效限制，原事件保持兼容。
- 离线 **284/284**、宏专项 **17/17**、完整现场 **322/322**（无跳过），构建、静态检查、
  profile 一致性和安装 DLL 哈希一致性通过。原生模型跑完整 200000 条并验证停止后无追加、
  唯一终结与零分配；现场用 3 tick 验证同一终结路径，并覆盖无效参数保留缓冲和保存。
  没有在游戏里连续录制 200000 tick；不把原生边界测试描述为长时现场测试。
- 复现：`driver/selftest.py --macro-limit-only`；日志 `out/reliability-macro-limit-*`。
  原清单第 12/13 项的宏采样/子系统化仍待推进，硬上限修复不代表整个宏子系统已重构。

## 第 12 项：查询采样首批（2026-10-31）

- 已完成范围：state/buttons/blocks/scan 的定长快照、局部 SEH、后台 JSON，显式 busy、
  超时、退出取消和重连丢弃。扫描每 tick 最多读取 512 格，32768 格恰好 64 批；
  返回 tick 跨度与结果截断原因，state 的方块部分失败返回 partial/warnings。
- 验收：离线 **283/283**、查询专项 **42/42**、真实生命周期 **9/9**、完整现场
  **295/295**（无跳过）；ruff check/format、mypy、profile 一致性、完整构建通过。
  受控异常无新增 crash 文件，最终安装 DLL 与构建产物 SHA-256 相同。
- 实测：30 次 state 采样中位数 148.5 µs、最大 209 µs；32768 格空中扫描最大单批
  3160 µs。固定 scan 存储约 4.4 MiB。仅记录本轮数值，不宣称性能提升倍率。
- 未完成范围：inventory/entities 等游戏读取边界、legacy JSON/跑酷事件/宏采样，
  SDK owning-string 读取的分配，以及 Overhead 统计环并发安全。第 12 项保持进行中。
- 复现：`driver/selftest.py --query-capture-only`、
  `driver/selftest.py --query-lifecycle-world "超平坦测试"`；
  日志 `out/reliability-query-{unit,live,lifecycle,full-live,build,restart}.log`，
  原始采样 `out/reliability-query-{before,after}.json`。

## 第一批证据

- 离线全量单测：281/281（包含 native 事务、AtomicRing 并发发布、订阅代次/超时、DSL 录制集成、配置副作用与保存退出）。
- ruff check/format、mypy、生成 profile 一致性通过。
- C++ `xmake -j4 -y`：包含按钮重置线程归属与主菜单就绪判断的构建通过。
- 现场全套 **228/228**，未跳过；包括 `request_reject_set/move/look` 和恢复检查，yaw_gap=0。
- 宏会话生命周期修复后现场全套 **235/235**；新增限长录制保存、保存后清理、重复启动拒绝、播放中拒绝录制与停止释放检查均通过。
- 2026-10-31 聊天生命周期修复后：离线全量 **282/282**、专项现场 **9/9**、完整现场 **237/237**（无跳过）；
  最终构建通过，安装 DLL 与构建产物 SHA-256 相同。专项实测主菜单世界 tick 不推进时两次
  请求均返回 `no_player`，退出中的延迟请求返回 `world_left`，重进后快照恢复；默认套件新增
  2 秒超时终结和后续请求恢复。日志：`out/reliability-chat-{unit,live,full-live}.log`。
- 2026-10-31 服务端物品使用等待修复后：离线全量 **282/282**，专项现场 **16/16**，
  完整现场 **253/253**（无跳过）；ruff check/format、mypy、profile 一致性和
  `xmake -j4 -y` 全部通过。先保存退出、安装最终 DLL，再重进干净地面测试，安装 DLL
  与构建产物 SHA-256 相同。专项确认 `server_timeout/server_task_queue_failed/`
  `server_task_failed/server_task_fault` 均只终结一次，active 释放、tick/ping 恢复，
  受控已捕获异常不产生 crash 文件。日志：`out/reliability-use-{unit,live,full-live,build,restart}.log`。
- 真实 ModAgent 等待 12 tick：采集 13 条连续 tick（1..13），pending=0。
- 开发循环保存退出重启实测通过（包含旧 mod 的 HBUI 主菜单兼容路径），世界重进后站稳。
- 最终 DLL 保存退出/重启后核心现场验收 **33/33**；离线 selftest 客户端 **38/38**、native **5/5**。
- 采集环改造后 native `atomic_ring_model` 通过：延迟发布保持 pending、覆写显式返回
  `overwritten`，4 线程共 8000 次预留/提交的末尾记录载荷保持一致；PacketTap 关闭回调入口后
  等待活动采集回调退出，避免计数竞态。
- Hook/Watch/Packet 的 drain 现在在未提交槽位停留于当前 `next`，不会跳过后续记录；环绕
  覆写以 `lost` 计数。服务端任务回调的 AV/非法指令路径由独立 SEH wrapper 隔离。
- `worlds` 增量增加 `ready`：未在世界中、已回主菜单且服务端退出完成才为 true；
  dev 保存退出后以此作为停止目标 PID 的条件，旧 mod 回退主菜单屏幕 + 登录模型就绪。

复现入口：`driver/selftest.py --mod-core-only`（含请求 mailbox native 检查）、
`--client-io-only`（含场景录制与配置回归）；默认现场自检增加 `request_reject_*` 与
`request_recovers_after_bad_types`。
