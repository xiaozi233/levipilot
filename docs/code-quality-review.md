# 2026-10-31 代码质量复核

范围：自有 C++ mod、Python 客户端/CLI/开发脚本、native/Python 测试、构建工作流和维护文档。
不把第三方 SDK、生成文件和历史逆向证据当作业务代码进行风格重构。
本报告区分静态可确认的实现问题、条件性故障风险和需要现场测量的性能影响；
测试通过不代表已证明没有竞态或游戏线程开销。

整体判断：领域划分和基础设施已有良好基础。Python 的连接/命令/门面分层、
请求代次隔离、ID 关联任务、版本 profile、独立 native 模型和完整 mod CI 构建值得保留。
主要技术债在 C++ 游戏线程边界和 ProbeMod 共享状态，而非简单的文件命名或目录布局。
适合沿既有边界逐项修复，不需要整体重写。

严重程度：P1 为条件满足时可能崩溃的高风险边界；P2 为确定性错误、无法恢复的等待或
可观的维护/时序风险；P3 为可读性和契约完善。以下保留复核顺序，完成范围随验收更新。

| 顺序 | 等级 / 处理 | 位置 | 具体问题、影响与建议 |
| --- | --- | --- | --- |
| 1 | P1 / 部分修复，其余立即处理 | `ProbeMod.cpp::onPlayerTick/afterPlayerTick`、宏播放等 | tick 外层只有 C++ try/catch；在 `/EHs` 下不能隔离访问违例。state/buttons/inventory/blocks/scan/select_slot、JSON 录制、跑酷与宏录制采样已加局部 SEH；实体移到独享队列会话且各阶段有局部 SEH。其他游戏读取仍需逐一收进局部边界，不能推广为整个 tick 安全。禁止直接包住持有 mutex 的整个 tick，以免跳过析构。 |
| 2 | P2 / 已修复 | `MacroRecordModel.h`、`Macro.cpp::recordMacroTick`、`CommandRecording.cpp` 的 `macro_rec_start` | 原默认 0/超上限会在满 200000 条后仍保持 recording=true。现统一有效上限，到顶停止并保留缓冲，只报告一次 limit；冷侧预留容量，追加不增长 vector。原生模型跑完整 200000 条，覆盖 0/超上限/边界/停止后不再追加；现场专项 17/17 验证配置、唯一终结、保存及无效参数不破坏已有数据。 |
| 3 | P2 / 已修复 | `WorldEntryModel.h`、`World.cpp::enterOnClient/probeEntryOnClient`、`WorldSession.cpp::pollEntry` | 已增加独立进入编号、30 秒截止、唯一终结及主线程实际状态观察；超时保留隔离，无玩家/安全菜单/服务端消失连续确认 500 ms 才恢复准入，避免重叠启动。Python 按编号匹配结果；native 验证截止/旧编号/竞争，专项现场 30/30 验证模拟超时、启动/观察 SEH 与真实缺失 join 后恢复。 |
| 4 | P2 / 部分重构 | `Inventory.cpp::applySlotRequest`、`Macro.cpp::finishMacro`、`ProbeMod.cpp::safeTickPost`、`Transport.cpp::enqueue` | 六类查询、JSON 录制与跑酷事件的自有 JSON/格式化/传输已迁出 tick，实体路径完全离开 tick。宏录制采样已使用预初始化帧存储、无锁准入与后台终结。SDK owning-string 名称访问器仍可能分配，宏播放和其他钩子等路径仍构造 JSON/string 或进入锁，可能制造抖动。继续用定长载荷交给消费者，分别验证游戏读取与生命周期；不能仅凭代码声称性能提高多少。 |
| 5 | P2 / 本批完成 | `State.cpp::captureScan`、`QueryModel.h::advanceScan` | 原实现缺少每 tick 预算，过滤 air 时小 limit 也读完整区域。现每 tick 最多 512 格，带取消/超时、tick 跨度和截断原因；native 验证顺序/边界/零分配，现场 32768 格为 64 批，重连和退出取消通过。扫描跨 tick，不能当成同 tick 的原子世界快照。 |
| 6 | P2 / 重构 | `ProbeMod.h` 宏/输入字段、`ProbeMod.cpp::startMacroIfRequested/tickMacroPlayback`、`Macro.cpp`、`CommandRecording.cpp`、`Tasks.cpp::beginTask` | ProbeMod 仍负责多领域状态；宏状态跨多个 TU、多个原子与两把锁，修改一个会话契约容易遗漏另一条 reset/stop 路径。按 MacroSession/InputSession 封装状态和操作，明确线程所有权与锁顺序，先迁移宏；保留 Internal.h 作为统一共享入口的仓库约定。 |
| 7 | P2 / 重构 | `Inventory.cpp` 四组服务端写入、`Server.cpp::requestServerTeleport`、`TaskEvents.cpp::useOnServerImpl`、`Entities.cpp::captureEntityStage` | 重复查询 Minecraft/Level/Player 和空值处理，玩家身份策略还分别使用 name 或 UUID+name。修复易漏改，错误语义容易漂移。提炼仅在服务端线程解析的上下文执行器，保留各领域既有错误事件，不跨线程保存 Actor 指针。 |
| 8 | P2 / 验证完善 | `driver/selftest.py::Checks.note/finish`、`check_task_operations` 等 | skip 只打印 NOTE，不进入结果分母；权限关闭或无空槽时，“全部通过”的数字会下降而看不出缺测。改为 pass/fail/skip 三态，输出原因和预期覆盖；完整验收模式遇必需检查 skip 应失败。拆分接近 3000 行的现场套件，以 fixture 明确前置状态与恢复。 |
| 9 | P2 / 本批完成 | `State.cpp::captureState/stateJson` | 原方块读取失败被静默吞掉，响应缺字段却像完整成功。现局部 SEH 返回 partial/warnings，正常查询可恢复；专项验证失败字段缺省、其他字段存在及后续恢复。整条采样 C++ 异常走明确的 capture_failed，不再静默成功。 |
| 10 | P3 / 重构 | `TaskModel.h::PlaceTask::phase`、`Tasks.cpp::tickPlace`、`Input.cpp::applyControls` | phase=0/1/2 和 mode 的位数语义分散在分支中，读代码时依赖记忆，新增阶段/模式容易漏分支。引入 PlacePhase 和具名输入模式位；只在协议边界保留兼容数字。顺带移除构建已报告的未用 Key 常量。 |
| 11 | P3 / 文档完善 | `docs/mod-internals.md` 的历史“移动、跳跃与输入”与实验辅助小节、`docs/architecture.md` 的 CI 说明、`skills/bedrock-re/SKILL.md` 的旧基线 | 局部仍把 jumpFromGround 写成默认路径，或称完整 mod 仅本机构建；与当前 HID 默认实现、GitHub mod-build 作业不符。把历史实测明确标为 legacy，把现行契约与验收基线集中维护；不要删除原始历史证据。 |
| 补充 | P2 / 已修复 | `OverheadModel.h`、`Overhead.cpp::overheadRecord/overheadJson` | 原普通 Sample 的跨线程数据竞争已改为 AtomicRing 原子载荷与完整窗口验证；读取失败显式 busy，不计算无效分位数。native 覆盖 20 万次写入/四读者与零分配，现场跨 1100 tick、1741 次读取全部一致，专项 7/7、完整 330/330。旧统计数字保留为历史证据，未用来宣称收益。 |

## 已修复问题与验收界限

原审阅的请求发布异常、录制丢 tick、开发配置副作用、按钮线程归属、采集环并发、
宏会话生命周期、Hook 槽旧 mem 数据、聊天请求生命周期已有修复和回归证据，
详见 `reliability-repairs.md`，本报告不把它们重复列成仍待修复。

本轮已完成原清单第 11 项：UseTask 进入 server_pending 后的 5 秒 wall-clock
截止、迟到副作用拒绝、投递/回调失败的同 ID 唯一终结，以及已捕获 SEH 的 first-chance
崩溃误报。离线全量 **282/282**、故障专项现场 **16/16**、完整现场 **253/253**（无跳过），
ruff check/format、mypy、profile 一致性与完整 C++ 构建通过。安装 DLL 与构建产物
SHA-256 相同。日志与复现入口见 `reliability-repairs.md`。

查询首批推进原清单第 12 项：四类查询定长快照、局部 SEH、后台格式化、扫描预算及生命周期。
离线 **283/283**、查询专项 **42/42**、重连/退出/重进专项 **9/9**、完整现场
**295/295**（无跳过）；构建、静态检查和安装产物一致性通过。以上只覆盖本批范围，
第 12 项其余读取边界与分配路径仍未完成，原清单仍有 5 大项未结。

背包后续已接入同一固定快照通道，并复用状态查询的物品快照与 SEH 工具。离线
**283/283**、查询专项 **52/52**、完整现场 **305/305**（无跳过），构建、静态检查与
安装产物一致性通过。库存字段保持兼容，新增显式截断、采样耗时和 busy/超时；
其他实体/快捷栏/legacy/事件/宏路径仍是后续范围。

宏硬上限补修：离线 **284/284**、专项现场 **17/17**、完整现场 **322/322**（无跳过），
构建、静态检查与安装产物一致性通过。原生模型实际跑到 200000 条；现场只验证限值配置和
3 tick 的真实终结/保存路径，未做 200000 tick 的长时录制。宏会话封装及其余 tick 治理仍未完成。

世界进入看门狗补修：离线 **287/287**、专项现场 **30/30**、完整现场 **324/324**
（无跳过），构建、静态检查、安装产物一致性通过，最终受控故障无新增 crash 文件。
同批补齐任务局部 SEH 的异常归属，防止已处理故障误写 dump；收紧导航后放置的站稳和
实体避让前提。一次 `use_failed` 未稳定复现，仍保留诊断证据；未把有限现场验收推广为
所有放置场景稳定，原第 12/15 项仍未完成。

Overhead 原子载荷补修：离线 **288/288**、专项现场 **7/7**、完整现场 **330/330**
（无跳过），构建、静态检查、安装一致性与无新增 crash 验证通过。第 12 项其余读取/
格式化/锁与 SDK 分配仍未完成。

实体与快捷栏后续：离线 **289/289**、查询专项 **99/99**、真实生命周期 **16/16**、
完整现场 **377/377**（无跳过）。实体三个阶段与快捷栏读写均有局部 SEH、唯一失败与恢复；
重连/退出取消、主菜单拒绝、重进恢复及安装哈希一致性通过，无新增/修改 crash 文件。
实体移除 tick 锁/任务分配，快捷栏自有 JSON 移到 spool；SDK 名称分配与 legacy/事件/宏
仍是第 12 项范围。完整跟踪与边界见 `reliability-repairs.md`。

JSON 录制与跑酷事件后续：离线 **296/296**，专项现场 **39/39**，完整现场
**411/411**（无跳过）；构建/静态/profile 与安装哈希一致，无新增/修改 crash 文件。
固定队列、局部 SEH、停止等待已准入采样与有序唯一终结、会话编号、明确失败及重进恢复
通过。Python 传播失败，跑酷实际 4 tick 宏排出 5 条连续采样；原生回归覆盖并发与零分配。
第 12 项的宏/其他读取与 SDK 分配仍未完成，原清单仍剩 5 大项。

宏录制采样后续：离线 **298/298**，专项现场 **29/29**（含退出/重进），完整现场
**435/435**（无跳过）；构建/静态/profile 与安装哈希一致，无新增/修改 crash 文件。
生产会话预初始化帧存储，tick 无录制锁/分配/格式化，局部 SEH 与唯一失败终结通过；
真实保存打开失败后保留 3 帧、重试成功。停止后独享快照与 record_id 隔离已接入，
鼠标 held 不再阻止消费点击 latch；部分文件明确失败，Python 保留原因和路径。
尚未实现原子文件保存；宏播放、hookTick、recordTickPose/其他读取与 SDK 分配仍属第 12 项，
MacroSession/InputSession 整体封装仍属第 13 项。原清单仍剩 5 大项。

下一步继续补其余游戏读取边界，
再迁其余 tick 格式化与拆分子系统，最后统一类型命名及文档。每批以 native 回归、
离线检查、完整构建和适用的现场回归独立验收，避免把未验证的行为变更混为一个大提交。
