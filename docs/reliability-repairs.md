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
| 12 | 游戏线程采样异常边界、JSON、分配与锁 | 进行中 | state/buttons/inventory/blocks/scan/select_slot、JSON 录制与跑酷事件已迁固定快照、局部 SEH、后台格式化；实体迁到独享分阶段会话，宏及其余采样仍待治理 |
| 13 | ProbeMod 跨领域状态耦合 | 待做 | 优先宏会话、输入会话，明确线程所有权 |
| 14 | 服务端玩家解析重复 | 待做 | 统一上下文执行，保留领域错误 |
| 15 | 交互覆盖、自检跳过统计 | 集成覆盖已补一部分；统计待做 | 新增异常/并发/真实请求交错回归 |
| 16 | 类型码命名、失效文档 | 输入旧结论文档已澄清；类型枚举待做 | README 与默认 HID 契约一致 |

原清单完成 11/16；第 12 项进行中，13–16 未完成。补充审阅的宏硬上限、世界进入
看门狗与 Overhead 统计环并发已修复；第 12 项其余范围不能因局部验收通过而自动算完成。

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
