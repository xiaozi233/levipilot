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
| 12 | 游戏线程采样异常边界、JSON、分配与锁 | 进行中 | 查询/JSON 录制/跑酷/宏录制已迁快照或定长队列；hook_call 已迁独占请求与后台回执，姿态已加局部 SEH/一致快照/世界代次；二进制录制已补唯一失败与部分数据传播；宏播放已迁无锁准入/后台终结、点击已迁固定队列；ECS 已移出 tick 并迁独占分阶段快照；事件输入已有局部边界、固定结果和文本屏障，文本/UI 迁客户端队列；飞行已迁分阶段请求、唯一终结和有界重断言；玩家名缓存已改 UUID/epoch 原子快照；移动/视角、传送、封包注入及其余边界仍待治理 |
| 13 | ProbeMod 跨领域状态耦合 | 未完成，播放模型已局部封装 | 宏录制/输入整体会话与其余字段所有权仍待推进 |
| 14 | 服务端玩家解析重复 | 部分接入；未完成 | 背包四组写入/命令/权限/物品使用共用 ServerPlayerContext；传送/实体/飞行独立解析仍待统一 |
| 15 | 交互覆盖、自检跳过统计 | 集成覆盖已补一部分；统计待做 | 新增异常/并发/真实请求交错回归 |
| 16 | 类型码命名、失效文档 | 输入旧结论文档已澄清；类型枚举待做 | README 与默认 HID 契约一致 |

原清单完成 11/16；第 12 项进行中，13–16 未完成。补充审阅的宏硬上限、世界进入
看门狗与 Overhead 统计环并发已修复；第 12 项其余范围不能因局部验收通过而自动算完成。

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
- 待桌面控制恢复后检查实际界面，确认退出完成，再恢复玩家姿态并验证进出世界。
  本项未完成；它不改变原清单 11/16 完成、第 12 项进行中的计数。

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
