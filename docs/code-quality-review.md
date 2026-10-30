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
可观的维护/时序风险；P3 为可读性和契约完善。以下按建议实施顺序列出仍未完成的工作。

| 顺序 | 等级 / 处理 | 位置 | 具体问题、影响与建议 |
| --- | --- | --- | --- |
| 1 | P1 / 立即修复 | `ProbeMod.cpp::onPlayerTick/afterPlayerTick`、`State.cpp::captureState/captureScan` | tick 外层只有 C++ try/catch；在当前同步 C++ 异常配置（`/EHs`）下不能隔离访问违例。状态/方块采样没有统一的局部 SEH，遇失效游戏指针仍可能穿透 tick；尚未用受控故障证明这些路径安全。把危险读取收进无析构对象的局部 SEH 采样函数，再返回定长失败结果；不能直接包住持有 mutex 的整个 tick，以免跳过析构。 |
| 2 | P2 / 立即修复 | `Macro.cpp::recordMacroTick`、`CommandRecording.cpp` 的 `macro_rec_start` | 只在小于 200000 条时追加，但仅按用户的非零 max_ticks 结束。默认 0 或大于上限时，达到硬上限后永远保持 recording=true，后续帧静默丢失。统一有效上限并发布一次 limit 终结；覆盖 0、超上限、边界和重复停止。 |
| 3 | P2 / 可靠性修复 | `World.cpp::enterOnClient`、`WorldSession.cpp::enter/finishEntry` | 登录有截止时间，真正发起 startLocalServerAsync 后却仅靠加入/取消/退出事件清除 mEntryInFlight。若回调故障或事件没有到达，后续请求持续返回 already_starting。增加代次绑定的观察与超时诊断；只在确认已回菜单、服务端停止后恢复可进入状态，禁止简单清标志再启动第二个服务端。 |
| 4 | P2 / 重构 | `State.cpp::captureState/captureButtons/captureBlocks`、`ProbeMod.cpp::emitLegacyTickJson/tickParkourEvents`、`Macro.cpp::recordMacroTick`、`Transport.cpp::enqueue` | 游戏线程仍构造 JSON/string、dump、增长 vector，并进入传输锁。legacy 录制每 tick 执行，其余由请求触发；可能制造采样抖动，违背热路径约定。复用 RecordRing/ChatCapture 的定长快照模式，把格式化和传输留给消费者；实际耗时需测量，不能仅凭代码声称下降多少。 |
| 5 | P2 / 重构 | `State.cpp::captureScan`、`CommandQuery.cpp` 的 `scan` | 32768 格体积上限只限制总量，没有每 tick 扫描预算；排除 air 时，limit 很小也可能读完整区域。单 tick 集中读方块并构造最多 16384 行 JSON。增加固定批次、取消/世界代次、明确截断原因，并比较 tick 开销分位数。 |
| 6 | P2 / 重构 | `ProbeMod.h` 宏/输入字段、`ProbeMod.cpp::startMacroIfRequested/tickMacroPlayback`、`Macro.cpp`、`CommandRecording.cpp`、`Tasks.cpp::beginTask` | ProbeMod 仍负责多领域状态；宏状态跨多个 TU、多个原子与两把锁，修改一个会话契约容易遗漏另一条 reset/stop 路径。按 MacroSession/InputSession 封装状态和操作，明确线程所有权与锁顺序，先迁移宏；保留 Internal.h 作为统一共享入口的仓库约定。 |
| 7 | P2 / 重构 | `Inventory.cpp` 四组服务端写入、`Server.cpp::requestServerTeleport`、`TaskEvents.cpp::useOnServerImpl`、`Entities.cpp::applyEntityAction` | 重复查询 Minecraft/Level/Player 和空值处理，玩家身份策略还分别使用 name 或 UUID+name。修复易漏改，错误语义容易漂移。提炼仅在服务端线程解析的上下文执行器，保留各领域既有错误事件，不跨线程保存 Actor 指针。 |
| 8 | P2 / 验证完善 | `driver/selftest.py::Checks.note/finish`、`check_task_operations` 等 | skip 只打印 NOTE，不进入结果分母；权限关闭或无空槽时，“全部通过”的数字会下降而看不出缺测。改为 pass/fail/skip 三态，输出原因和预期覆盖；完整验收模式遇必需检查 skip 应失败。拆分接近 3000 行的现场套件，以 fixture 明确前置状态与恢复。 |
| 9 | P2 / 错误可观测性完善 | `State.cpp::captureState` 的方块查询 catch | C++ 异常被静默吞掉，响应仍像完整成功，只是少了 block 字段；调用方难以区分“没采到”和“合法缺省”。增量添加 partial/warnings 或明确的字段采样状态，记录限频诊断，测试部分失败后的恢复。该项不替代第 1 项的 SEH 修复。 |
| 10 | P3 / 重构 | `TaskModel.h::PlaceTask::phase`、`Tasks.cpp::tickPlace`、`Input.cpp::applyControls` | phase=0/1/2 和 mode 的位数语义分散在分支中，读代码时依赖记忆，新增阶段/模式容易漏分支。引入 PlacePhase 和具名输入模式位；只在协议边界保留兼容数字。顺带移除构建已报告的未用 Key 常量。 |
| 11 | P3 / 文档完善 | `docs/mod-internals.md` 的历史“移动、跳跃与输入”与实验辅助小节、`docs/architecture.md` 的 CI 说明、`skills/bedrock-re/SKILL.md` 的旧基线 | 局部仍把 jumpFromGround 写成默认路径，或称完整 mod 仅本机构建；与当前 HID 默认实现、GitHub mod-build 作业不符。把历史实测明确标为 legacy，把现行契约与验收基线集中维护；不要删除原始历史证据。 |

## 已修复问题与验收界限

原审阅的请求发布异常、录制丢 tick、开发配置副作用、按钮线程归属、采集环并发、
宏会话生命周期、Hook 槽旧 mem 数据、聊天请求生命周期已有修复和回归证据，
详见 `reliability-repairs.md`，本报告不把它们重复列成仍待修复。

本轮已完成原清单第 11 项：UseTask 进入 server_pending 后的 5 秒 wall-clock
截止、迟到副作用拒绝、投递/回调失败的同 ID 唯一终结，以及已捕获 SEH 的 first-chance
崩溃误报。离线全量 **282/282**、故障专项现场 **16/16**、完整现场 **253/253**（无跳过），
ruff check/format、mypy、profile 一致性与完整 C++ 构建通过。安装 DLL 与构建产物
SHA-256 相同。日志与复现入口见 `reliability-repairs.md`。

下一步应先补游戏线程采样的异常边界、宏硬上限与世界进入等待的恢复，再做固定快照/扫描
预算和子系统提取，最后统一类型命名及文档。每批以 native 回归、离线检查、完整构建和
适用的现场回归独立验收；不能把维护性重构与未验证的行为变更混为一个大提交。
