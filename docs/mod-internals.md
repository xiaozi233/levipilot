# Mod 实现要点

本文记录当前实现中容易误用的语义、容量和诊断边界。架构导航见
[`architecture.md`](architecture.md)，命令表见 [`../mod/README.md`](../mod/README.md)，
当前验收数字见 [`reliability-repairs.md`](reliability-repairs.md)。

## 状态、查询与身份

- `state.x/y/z` 来自 LocalPlayer 的位置基准，`feet_*` 是脚部；本版本原地站蹲实测 Y 偏移约
  **脚部 + 1.6200101**，不是随姿态变化的头部/镜头高度。`head_*` 单独来自 `getHeadPos()`；
  该偏移来自 `OffsetsComponent.mHeightOffset`，由 `ActorOffset::setHeightOffset` 写入常量
  `1.62001f`（`.rdata 0x14E6AE444`，全 exe 唯一），尾差是 float32 加法量化，见
  [position-y-offset 账本](../skills/bedrock-re/findings/1.26.51.01/cases/position-y-offset/resume.md)。
- `state.vx/vy/vz` 来自 `Actor::getVelocity()`，不是 `StateVectorComponent+0x18` 的原始字段；
  后者在地表行走时约为 **0.546 × 实际位移**。实际移动速度仍以相邻 tick 位置差为准。
- `state` 的方块读取失败以 `partial/warnings` 标记，不把缺字段当成完整成功。文本和槽位
  截断有 `truncated`。
- `state/buttons/inventory/blocks` 通常有 2 秒截止，`scan` 为 10 秒；同域重复请求返回
  `*_busy`，退出取消，重连丢弃旧快照。
- `scan` 每 tick 最多 512 格、总量最多 32768 格、结果最多 16384 条；响应包含
  `visited/batches/batch_limit/max_batch_us/first_tick/last_tick`，结果上限另有
  `truncated_reason=result_limit`。
- 背包最多采样 256 个槽，快照约 1.1 MiB；实体快照约 2.2 MiB、最多 512 个实体。
  容量是预分配上限，不是每次响应都会返回这么多行。
- `PlayerIdentityStore` 只发布 UUID、tick 和世界 epoch，不在 tick 读取 owning 玩家名。
  无效或过期快照不能回退成上一身份。
- 服务端操作通过 `ServerPlayerContext` 在回调内重新验证 epoch 并解析 UUID。显式玩家名只在
  命令明确指定时使用；没有隐式名字前缀或控制台兜底。

## 输入、飞行与任务

默认移动、跳跃、任务和 `jump_when` 都使用当前键位对应的 Hardware HID 按键沿。直接标志、
queue/handler 和冲量路径只在 `input_cfg force=true` 下用于 legacy 对照。

事件队列固定 128 项，事件在 tick 应用与后台结果消费完成前都占有槽位。`input_event_done`
只表示注入调用完成，不证明 UI/世界采用输入。文本有独立请求槽与顺序屏障；失败/超时会取消
后序旧 Enter，避免提交残留文字。`screen/key_state` 使用独立 UI 查询槽。

输入锁按真实键状态和注入标志判断，不把 LL 全局事件计数当成游戏已收键。卸载准入使用
`InputLifecycleStore` 的一致快照，比较冷侧最新邮箱中的序号与实际剩余 tick；无有效快照、
世界代次不符或读取冲突均按 busy 处理。

飞行使用独占分阶段请求：客户端读取身份与目标，服务端写能力，客户端有界回读。
`client_applied/client_observed` 只在回读一致时为 true。8 tick `FlightAssertion` 只读，不阻塞
成功回执；失败另发 `flight_assertion_failed`。

`TaskController` 只有一个活动 variant。新任务替换旧任务，任务锁内不得重入或同步等待需要
同一把锁的线程。`use` 的服务端等待从进入 `server_pending` 起有 5 秒预算，迟到回调重新核对
ID 和截止时间；已经开始的 SDK 调用不能承诺抢占或回滚。

客户端/服务端任务把服务实例查找、SDK 投递和任务执行分别收进局部异常边界，复用
`guardedSdkCall`，同时处理 C++ 异常与受支持 SEH；捕获对象与回调 ticket 在原始 SEH frame
之外持有。无实例仍返回 `no_app_platform/no_server_instance`，查找或投递故障返回
`client_task_queue_failed/server_task_queue_failed`，执行故障走既有任务失败回调。失败处理器
在执行边界外只调用一次，其自身故障只记录日志，避免重复终结。命令注册、注册事件取值、
`/togglefly` 执行及错误输出同样有局部边界；注册失败不更新 registry 判重指针。

`selftest_sdk` 在 `allow_selftest_stress` 下向 lookup/submit/run 注入 SEH 或 C++ 异常，
现场检查唯一回执、任务所有权退休与后续任务恢复。注册故障在 SDK 调用前注入；
`command_boundary` 是合成保护入口，真实 `/togglefly` 另以能力变化和恢复验收。
这些检查不模拟 SDK 内部已经破坏的锁或内存，也不承诺失败后回滚部分游戏操作。

领域夹具恢复位置用 `teleport` 保留效果；明确要求清效果的 reset 语义专项仍使用 `reset`。
清理通过 `preserve_failure` 保留正在展开的首错。朝向同步仍要求 0.1° 内连续三个 tick；
`state()` 已等待 tick 快照，等待循环不再叠加额外 sleep，以免人为漏掉下一采样 tick。

## 录制、宏与 spool

legacy JSON 录制由 `LegacyStreamState` 统一持有流、冷锁、终结和世界退出状态。跑酷事件使用
固定队列和 generation，旧世界事件不进入新运行。

二进制记录环为 **524288 × 64 B = 32 MiB payload**，另有发布序号。tick 是单写者，停止等待
已准入采样。`record_dump` 的 `since/next` 是绝对序号，`bin`/`hex` 以 hex 分块保持 JSONL。
采样故障或离开世界终结 record_id；显式部分导出也不允许跨 record_id 拼接。

宏录制省略、0 或超过上限时都采用 200000 tick 硬上限。冷侧一次初始化帧槽，tick 只写已准入
槽位；到顶只发一次 `macro_rec_limit`，录制停止但数据仍可保存。保存先写同目录临时文件，检查
write/flush/close 后原子替换，失败保留旧目标和内存缓冲。

spool 每 100 ms 或 64 KB 刷盘，单文件超过 64 MiB 轮转。连接重放与文件刷盘是两条路径；
`queue_dropped` 表示传输队列溢出，不能据此推断 spool 文件完整。崩溃时最多可能损失最后一段
未刷盘记录。

## 内存、观察点与 hook

- 内存读写通过当前进程的 `ReadProcessMemory`/`WriteProcessMemory`，坏地址返回 `bad_addr`，
  不靠访问违例探测。`mem_write` 默认关闭并写审计日志。
- DR0–DR3 共四个观察点。只有装过 DR 的线程会进入命中环，其他线程事件计入 `foreign_hits`。
  `rip` 是触发后的下一条指令，`code_hex` 为其前 16 字节；`depth` 控制返回地址数量。
  `thread="all"` 被拒绝，玩家 StateVector 观察使用 `thread="both"`。
  `sample=true` 默认关闭；开启时 VEH 用一次固定长度的 `ReadProcessMemory` 读取被观察的
  1/2/4/8 字节，记录 `qpc/value_ok/value_size/value_hex`，drain 返回 `qpc_frequency`。
  值是触发指令执行后读到的内存，不是冻结的寄存器操作数，也不与其他线程原子同步。
  这是显式诊断成本：每次命中增加一次 QPC 和内核内存读取，未开启时不执行这两项。
  `mode=x,size=1` 使用 DR 执行断点，须额外开启 `allow_hook_va`，安装写审计日志。
  `sample=true` 时可指定最多三个 `snapshots`（寄存器 rcx/rdx/r8/r9、长度 1..64）；
  VEH 在当前指令执行前以定长内核拷贝记录参数指向的数据，再设置 RF 恢复执行。
  它不调用目标函数、不改参数；每行 `rip_semantics=current_instruction`，与数据观察点的
  `next_instruction` 分开。混合导出时顶层为 `per_hit`，以每行标记为准。执行观察不读取代码字节。
  多份快照只对当前线程暂停期间成立，不与其他线程原子同步；每命中另加至多三次内核读取，
  命中环为此预留固定存储，未请求快照时不读取这些内存。
- 通用 VA detour 只保留四个整数参数寄存器；XMM、向量返回、隐藏返回指针和更多参数必须使用
  typed detour。VA 校验只覆盖可执行页，不证明指令边界。
- hook/watch 命中环是进程级共享环。原始 JSON 命令省略 `since` 时从 0 读；Python
  `ModClient` 与 `Harness` 省略或传 `None` 时，使用指定 ID（未指定则该类最近一次安装）
  **安装后额外查询**所缓存的 head，缓存缺失时仍从 0 读。默认值只选择起点，不自动过滤 ID。
  需要覆盖安装最初的命中时，应在安装前显式读取 head 并传入 `since`。ID 过滤发生在全局分页
  之后，空的过滤结果也必须按 `next` 翻页；保留 `lost`，不能把缺采当成未命中。
- `hook_call` 是独占请求，2 秒截止、重连取消；成功只表示调用完成。`mem_arg` 在 detour 内
  同步复制短命缓冲，之后对同一地址 `mem_read` 不能替代这份快照。
- `panic` 可卸 hook、清观察点并取消任务/输入，但不杀进程。静态 origin 槽按 SDK 生命周期保留，
  不能把仍在回调使用的环或函数槽提前释放。

## ECS 与版本 profile

ECS 请求按 registry 所属线程执行。客户端 context 先核对当前客户端 registry，runtime id 请求
读取服务端 Actor；`scope` 表明实际读取侧。每次请求最多 1024 条组件/失败 id、4096 次 storage
访问和 512 字节原始组件数据，超限标 `truncated`，坏 storage 记入 `skipped/partial`。

组件 id 是运行时 EnTT id，不保证跨进程/版本稳定。`stride` 由同 storage 元素地址差推断，
只有一个元素时为 0；默认读取长度依次取 profile size、stride、64 字节。

`docs/offsets/*.json` 是签名、结构偏移和组件 id 的唯一数据源，`GeneratedProfiles.h` 是提交物。
`build_id` 选择 profile 且四个锚点都匹配时 `version_ok=true`。导入锚点先跟随 thunk，再比较
真实函数前 24 字节。没有匹配 profile 时能力降级；`allow_version_mismatch` 只用于移植，不能
代替签名验证。运行目录的 `offsets.json` 只覆盖当前进程的已选 profile。

## 封包与网络诊断

### 身份与重序列化

常规身份 tap 只写定长 `(tick,seq,id,tid)`，不调用 `Packet::write`。send/recv 默认同 tick、
同对象、同 id 去重；真实投递次数看 `packet_stats.recv_anchors`。单机接收侧有效锚点是
`LoopbackPacketSender::$sendToClient`，`peer_sends=0` 是预期现象。

`serialize_ids` 是 `sdk_hot_path_diagnostic`：在原投递上下文同步重序列化，可能分配，不能保留
`Packet*` 到其他线程，也不是线上原始字节。`serialize_budget_us` 默认 **1000000**（1 秒），
0 不限；累计超预算后停止后续重序列化，记 `error=Budget` 和 `serialize_skipped`，统计暴露
`samples/us/max_us/skipped/budget_us`。预算不能中断正在执行的 SDK 调用。

### 命令包注入

`chat_send` 只支持 `CommandRequestPacket(77)`，必须先通过真实 UI 命令捕获来源模板。模板保留
owner、generation、origin 字段和完整 requestId（最多 512 字节），不截断身份或回退旧模板。
`status.command_template{ready,reason}` 使用与发送端相同的 latest 校验。

请求由 `PacketRequestState` 独占，接受与完成均带 `packet_id`。`chat_sent.ok=true` 只表示发送
调用完成，命令效果必须回读。失败以 `partial=true` 保守表示 SDK 构造/发送可能已部分执行，
不能自动重试。

### 入站、违规与发送探针

`ViolationWatch.cpp` 负责违规回调和断线页，`InboundWatch.cpp` 负责解析/copy 探针，
`SendProbe.cpp` 负责发送/序列化 suspect 记录；共享头只提供定长记录和冷侧工具。

违规回调有十个参数，必须使用同签名 typed wrapper，不能用通用四寄存器 VA detour。角色是
静态 VA，安装时使用 `diag.module_base + (role_va - image_base)`。回调只写定长记录并以原参数
调用原实现，不改校验结果或断开行为。合成行只验证设施，不是自然违规。

本地入站路径使用深拷贝，切片以 packet id 开始且没有帧长度前缀。187/188 的 `payload+2`
来自 varint id 宽度。`before_hex` 是邻接堆字节，不能解释成声明长度；真实前缀解码只接受
覆盖完整捕获窗口的起点，歧义保持 `unknown`。

`SendProbeHistory` 使用 `AtomicRing<SendProbeRecord,64>`。hook 只写定长原子载荷，导出先复制
局部记录并只格式化 ready 行；未发布、放弃和已覆盖行不会冒充完整记录。suspect 文件
保留栈与前后长度。长度集合只是历史采样启发式，26 字节等未分类帧仍保留原样取证，
不能仅凭落盘就断言坏包。自检检查计数单调、采集/停止与管道存活；旧检查名
`send_probe_no_dumps` 仅保留为观察注记，不再把零 dump 当作包合法性的证明。

游戏侧共享序列化缓冲的跨线程竞态属于外部限制。相关证据见
[`notes/badpacket-setactordata-20261031.md`](notes/badpacket-setactordata-20261031.md)；
不要把有限未复现写成竞态已修复。

## DLL 生命周期

键盘、鼠标、玩家 tick 与转向四个常驻 hook 由 `enable/disable` 显式管理，状态初始为未安装。
不使用全局 `HookRegistrar`：它在静态构造/析构中暂停其他线程，会把卸钩与 DLL loader lock
组合在同一阶段。启用要求四个 hook 都安装成功，部分失败会尝试撤销；停用逐个尝试卸钩，
失败不得报告可卸载。`status.lifecycle.hooks` 提供各 hook 的当前安装标志。

游戏内重载不会再触发当前世界的命令注册事件。启用时另向服务端队列提交一次注册任务，
为已有 registry 绑定新的 `/togglefly` 处理器；主菜单启动仍由正常注册事件处理。

受控重载专项要求玩家站稳、无飞行和宏录制/播放，先拒绝不存在的 mod，再经
`lifecycle_prepare` 静默、发送 `/ll reload probe`。成功需要同 PID 的新代次、旧 disable 与
新 enable 日志、全部 hook 已安装、tick 前进及 `/togglefly` 能力变化与恢复。
仅 prepare 成功或管道重连不构成验收。回调排空竞争专项以当前验收文档为准。

`CallbackLifecycleModel.h` 统一跟踪 key/mouse/tick/turn、client/server/delay、EventBus、
诊断 detour、VEH、游戏命令和管道请求。热侧是定长原子计数与 TLS 深度；计数涵盖 origin
调用返回。队列准入位与所有权计数共用一个 CAS 字，任务闭包的所有副本共享 ticket；执行
结束不提前退票，用户捕获先销毁，ticket 最后释放。EventBus 移除监听器后还要等待外部
ListenerPtr 发布快照释放，不能只检查正在运行的 lambda。

`disable` 先拒绝自身回调线程和自有活动输入/宏/任务，再关闭队列准入，以 1500 ms 总预算
等待既有工作。移除七个监听器、四 hook 与 VEH 后，再等待所有权、入口计数和 DLL 返回
栈退出，才清理任务/状态、停管道和 spool 并 join。卸四 hook 前，在同一次 LL 全局暂停中
复查计数和线程指令位置，防止零计数与新入口竞态；当前仍位于 DLL 入口/返回指令时重试。
返回栈检查只在冷侧，逐线程短暂暂停并
用 Windows unwind 元数据检查，不读取游戏结构；无法完成检查就拒绝。超时恢复相同监听器、
原 hook 安装状态与原准入，保留 running/代次和玩家状态。恢复失败单列
`callback_rollback_failed`，不得报卸载成功。

活跃诊断会话尚不能事务性恢复，因此在任何移除前以 `diagnostic_sources_active` 拒绝卸载；
需要先显式停止 PacketTap、任意 VA hook、违规/入站/send probe 和 DR watch。所有诊断入口
仍纳入计数，VEH 即使准入关闭也必须消费属于本模块的 DR trap。PacketTap/VEH 移除失败
保留真实安装状态。违规守望的 stop 会同时移除它的解析/复制附加钩子。

`status.lifecycle.callbacks` 提供 closed/refusal 与各领域 owned/running/entered/completed；
owned 表示执行器持有的任务对象，可能已经执行完。`selftest_callback` 仅在压力权限开启时
可用，延迟 client/server/tick 1–2000 ms，或在专用、可 join 的冷线程验证排空超时/重新准入。
它不把测试等待误报成真实卸载。`--callback-drain-only` 结合该检查与真实重载、代次、持键
拒绝和最终姿态/能力/输入恢复；该专项不会默认混入普通全套。持键和诊断拒绝走游戏聊天
指令包，要求本次 boot 日志明确给出 busy/diagnostic_sources_active，避免由 callback_self
或旧 refusal 假充覆盖。中途失败也逐项尝试清延迟、释放按键、恢复姿态和输入锁，保留首错。

启用违规导出时，入口拥有最终连接关闭权。重载前将旧 DLL 的环与完整文本保存到观察事件的
`checkpoint`，重载后依据 generation 变化将游标归零并重新武装。报告保留 `gap_begin/end`
或 `gap_uncovered`，不会把卸载期间描述为连续观察；收尾错误不覆盖重载自身的首错。

## Python 连接与自检结构

`_PipeSession` 管理请求和订阅，`_PipeStream` 管理句柄、读队列和读线程。读线程/finalizer 只持
stream；EOF、接收溢出和显式 close 都会释放当前代次，旧 close 不影响新连接。GC 只兜底无引用
对象，不代替显式关闭。

请求只取本次响应，其他事件进入 pending；独立事件等待超时不关闭连接，请求超时关闭连接。
任务封装先等 started，再按 task_id 等终结，取消抛 `ModTaskCancelled`。

`selftest.py` 只负责 CLI、专项分发和兼容导出。`selftest_default.py` 编排默认套件，
`selftest_runtime.py` 统一 WorldLost、等待与水池夹具，其他 `selftest_<领域>.py` 分别承载
任务、查询、输入、封包、录制和恢复检查。三态结果与首错保护在 `selftest_support.py`，
诊断会话明确记录空窗，不用旧检查名掩盖缺少覆盖。

## 验证边界

当前验收数字及前提统一维护在 [`reliability-repairs.md`](reliability-repairs.md)。native
模型通过不代表现场时序，基础现场通过不代表完整现场，`lifecycle_prepare` 通过不代表真实
reload，完整现场通过也不覆盖异常 HBUI 的形成根因或游戏网络竞态。
