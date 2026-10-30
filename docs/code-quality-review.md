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
| 1 | P1 / 部分修复，其余立即处理 | `ProbeMod.cpp::onPlayerTick/afterPlayerTick`、`Inventory.cpp`、`Entities.cpp` 等 | tick 外层只有 C++ try/catch；在 `/EHs` 下不能隔离访问违例。本批 state/buttons/blocks/scan 已加局部 SEH 并通过受控故障恢复；其他游戏读取仍需逐一收进局部边界，不能把四类查询通过推广为整个 tick 安全。禁止直接包住持有 mutex 的整个 tick，以免跳过析构。 |
| 2 | P2 / 立即修复 | `Macro.cpp::recordMacroTick`、`CommandRecording.cpp` 的 `macro_rec_start` | 只在小于 200000 条时追加，但仅按用户的非零 max_ticks 结束。默认 0 或大于上限时，达到硬上限后永远保持 recording=true，后续帧静默丢失。统一有效上限并发布一次 limit 终结；覆盖 0、超上限、边界和重复停止。 |
| 3 | P2 / 可靠性修复 | `World.cpp::enterOnClient`、`WorldSession.cpp::enter/finishEntry` | 登录有截止时间，真正发起 startLocalServerAsync 后却仅靠加入/取消/退出事件清除 mEntryInFlight。若回调故障或事件没有到达，后续请求持续返回 already_starting。增加代次绑定的观察与超时诊断；只在确认已回菜单、服务端停止后恢复可进入状态，禁止简单清标志再启动第二个服务端。 |
| 4 | P2 / 部分重构 | `Inventory.cpp`、`ProbeMod.cpp::emitLegacyTickJson/tickParkourEvents`、`Macro.cpp::recordMacroTick`、`Transport.cpp::enqueue` | 四类查询的自有 JSON/格式化/传输已迁出 tick，其 SDK owning-string 名称访问器仍可能分配。inventory/legacy/事件/宏等路径仍构造 JSON/string、增长 vector 或进入传输锁，可能制造抖动。继续用定长载荷交给消费者，分别验证游戏读取与生命周期；不能仅凭代码声称性能提高多少。 |
| 5 | P2 / 本批完成 | `State.cpp::captureScan`、`QueryModel.h::advanceScan` | 原实现缺少每 tick 预算，过滤 air 时小 limit 也读完整区域。现每 tick 最多 512 格，带取消/超时、tick 跨度和截断原因；native 验证顺序/边界/零分配，现场 32768 格为 64 批，重连和退出取消通过。扫描跨 tick，不能当成同 tick 的原子世界快照。 |
| 6 | P2 / 重构 | `ProbeMod.h` 宏/输入字段、`ProbeMod.cpp::startMacroIfRequested/tickMacroPlayback`、`Macro.cpp`、`CommandRecording.cpp`、`Tasks.cpp::beginTask` | ProbeMod 仍负责多领域状态；宏状态跨多个 TU、多个原子与两把锁，修改一个会话契约容易遗漏另一条 reset/stop 路径。按 MacroSession/InputSession 封装状态和操作，明确线程所有权与锁顺序，先迁移宏；保留 Internal.h 作为统一共享入口的仓库约定。 |
| 7 | P2 / 重构 | `Inventory.cpp` 四组服务端写入、`Server.cpp::requestServerTeleport`、`TaskEvents.cpp::useOnServerImpl`、`Entities.cpp::applyEntityAction` | 重复查询 Minecraft/Level/Player 和空值处理，玩家身份策略还分别使用 name 或 UUID+name。修复易漏改，错误语义容易漂移。提炼仅在服务端线程解析的上下文执行器，保留各领域既有错误事件，不跨线程保存 Actor 指针。 |
| 8 | P2 / 验证完善 | `driver/selftest.py::Checks.note/finish`、`check_task_operations` 等 | skip 只打印 NOTE，不进入结果分母；权限关闭或无空槽时，“全部通过”的数字会下降而看不出缺测。改为 pass/fail/skip 三态，输出原因和预期覆盖；完整验收模式遇必需检查 skip 应失败。拆分接近 3000 行的现场套件，以 fixture 明确前置状态与恢复。 |
| 9 | P2 / 本批完成 | `State.cpp::captureState/stateJson` | 原方块读取失败被静默吞掉，响应缺字段却像完整成功。现局部 SEH 返回 partial/warnings，正常查询可恢复；专项验证失败字段缺省、其他字段存在及后续恢复。整条采样 C++ 异常走明确的 capture_failed，不再静默成功。 |
| 10 | P3 / 重构 | `TaskModel.h::PlaceTask::phase`、`Tasks.cpp::tickPlace`、`Input.cpp::applyControls` | phase=0/1/2 和 mode 的位数语义分散在分支中，读代码时依赖记忆，新增阶段/模式容易漏分支。引入 PlacePhase 和具名输入模式位；只在协议边界保留兼容数字。顺带移除构建已报告的未用 Key 常量。 |
| 11 | P3 / 文档完善 | `docs/mod-internals.md` 的历史“移动、跳跃与输入”与实验辅助小节、`docs/architecture.md` 的 CI 说明、`skills/bedrock-re/SKILL.md` 的旧基线 | 局部仍把 jumpFromGround 写成默认路径，或称完整 mod 仅本机构建；与当前 HID 默认实现、GitHub mod-build 作业不符。把历史实测明确标为 legacy，把现行契约与验收基线集中维护；不要删除原始历史证据。 |
| 补充 | P2 / 并发修复 | `Overhead.cpp::overheadRecord/overheadJson` 的 `gSamples` | 客户端 tick 写普通 Sample 字段，管道线程同时读取；epoch 检查只能发现版本变化，不能消除 C++ 数据竞争。统计读数及据此作出的性能判断不可靠。改为原子载荷或复用已有并发环模型，补覆写读取回归；修复前不以旧 overhead 分位数宣称收益。 |

## 已修复问题与验收界限

原审阅的请求发布异常、录制丢 tick、开发配置副作用、按钮线程归属、采集环并发、
宏会话生命周期、Hook 槽旧 mem 数据、聊天请求生命周期已有修复和回归证据，
详见 `reliability-repairs.md`，本报告不把它们重复列成仍待修复。

本轮已完成原清单第 11 项：UseTask 进入 server_pending 后的 5 秒 wall-clock
截止、迟到副作用拒绝、投递/回调失败的同 ID 唯一终结，以及已捕获 SEH 的 first-chance
崩溃误报。离线全量 **282/282**、故障专项现场 **16/16**、完整现场 **253/253**（无跳过），
ruff check/format、mypy、profile 一致性与完整 C++ 构建通过。安装 DLL 与构建产物
SHA-256 相同。日志与复现入口见 `reliability-repairs.md`。

本批推进原清单第 12 项：四类查询定长快照、局部 SEH、后台格式化、扫描预算及生命周期。
离线 **283/283**、查询专项 **42/42**、重连/退出/重进专项 **9/9**、完整现场
**295/295**（无跳过）；构建、静态检查和安装产物一致性通过。以上只覆盖本批范围，
第 12 项其余读取边界与分配路径仍未完成，原清单仍有 5 大项未结。

下一步继续补其余游戏读取边界，处理宏硬上限、世界进入等待恢复及 Overhead 原子载荷，
再迁其余 tick 格式化与拆分子系统，最后统一类型命名及文档。每批以 native 回归、
离线检查、完整构建和适用的现场回归独立验收，避免把未验证的行为变更混为一个大提交。
