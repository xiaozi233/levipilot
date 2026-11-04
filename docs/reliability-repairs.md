# 可靠性与验收边界

本文只保留当前可复现的验收基线、前提和未覆盖范围。旧修复轮次与历史报告以 Git 历史和
`out/` 的原始日志为准，不再逐轮追加。

## 验收规则

- 离线、native 模型、基础现场和完整现场是不同层次，不能互相替代。
- PASS/FAIL/SKIP 分开统计；`--require-complete` 将 required skip 判为失败。
- 生命周期测试必须检查唯一终结、拒绝/超时、旧编号或旧世界隔离、恢复和实际副作用回读。
- 保存状态不确定时保留游戏进程；只有明确放弃未保存更改才允许 force-stop。
- 原生模型中的零分配、并发和容量结论，不证明真实游戏中的性能或所有 SDK 边界安全。

## 当前基线

| 范围 | 当前结果 | 覆盖重点 |
| --- | --- | --- |
| 完整离线 | **438/438** | Python、native 模型、CLI、协议与资源生命周期；含回调排空、SDK 失败回执/恢复、效果保留、首错保护与探针候选语义回归 |
| 静态检查 | ruff check/format、mypy（115 files）、profiles、roles（56/63）、lint_public 通过 | 另经 LLVM 22 C++ 构建 |
| 完整现场 `--require-complete` | **1169/1169**，0 fail、0 skip、0 required skip | 当前默认全套，含新增 69 项 SDK 边界与权限/空槽前提；未开 `LEVIPILOT_VIOLATION_DUMP` |
| SDK 局部边界 | **69/69** | client/server lookup、submit、run 的 SEH/C++ 故障，唯一失败回执、所有权退休、后续任务及真实 `/togglefly` 恢复；注册与命令边界是调用前合成故障 |
| 领域效果保留 | **126/126**；进入/退档 **35/35** | 夜视跨查询/聊天退档、宏播放、任务及世界进入恢复仍存在，结束只清理测试效果 |
| 朝向/传送专项 | **45/45** | 保持 0.1° 容差与连续三个 tick；移除 state 快照之后的额外延迟 |
| 有界查询/重连 | **8/8** | 四轮共 1040 次查询，约 24–25 次/秒，显式关闭并重连；仅本轮未复现失联 |
| `lifecycle-prepare-only` | **10/10** | 持续/到期输入、未消费序号、事件持键、hook 恢复与姿态恢复 |
| 受控空闲真实热重载 | **22/22 × 4**，0 fail、0 skip | 竞争专项内在同一 PID 反复完成受控重载；完整停用/启用日志、四 hook、tick、命令能力变化与恢复 |
| 守望开启时的真实重载 | **25/25**，0 fail、0 skip | 旧环 checkpoint、明确观察空窗、新 DLL 环游标归零、重新武装和最终导出/停止；generation 398→399 |
| 在途回调竞争专项 | **132/132**，0 fail、0 skip、0 required skip | client/tick 在执行时排空并重载，server 排队未执行时超时拒绝/恢复再重载；各域同线程卸载拒绝，游戏包通道的持键 busy/活跃诊断拒绝，以及旧代次隔离和姿态/能力/输入恢复 |
| 输入事件专项 | **46/46** | 真实键状态、raw/注入对照、标记恢复和输入释放 |
| tick 客户端读取恢复 | **55/55** | pre/post 局部 SEH 与既有恢复链，不是整个 tick 的 SEH |
| 世界进入/退档 | **32/32** | leave_id、菜单快照和正常恢复；异常 HBUI 根因不在覆盖内 |
| 飞行短/长复验 | **50/50** | 效果保留、自然到期、哨兵占用与姿态恢复 |
| 长录制 | **9/9**（既有记录，本轮未复跑） | 同一 record_id 的 200000 tick、保存、重载与等待器接续 |
| 最终环境恢复 | **17/17** | 原四键配置逐字节恢复并重启加载，姿态/能力/效果/背包/输入回读，四 hook、空回调所有权、DLL 一致和 124 个 crash 文件无变化 |

这些结果只在对应代码、游戏版本和权限前提下有效。文档或后续代码变更需要按影响范围重跑，
不能把这些数字解释成未来提交永久全绿。

主要入口：`py -3.11 driver/selftest.py --sdk-boundaries-only --require-complete`；
回调竞争使用 `--callback-drain-only`，权限同真实热重载专项。
最终完整现场、离线日志分别为 `out/final-engineering-full-delivery.log`、
`out/final-engineering-offline-release.log`；SDK、效果与查询证据分别为
`out/final-engineering-sdk-live.log`、`out/final-engineering-fixture-effects.log`、
`out/final-engineering-entry-effect.log`、`out/final-engineering-query-soak.json`。
本轮回调复验为 `out/final-engineering-callbacks.log`，最终配置与状态恢复报告为
`out/final-engineering-integrity.json`。首次恢复时 yaw 差 0.0055°，保留在
`out/final-engineering-integrity-first.json`；正常 `look` 校正后与原值一致，最终 17/17。
其余专项若未列本轮日志，沿用原范围的既有验收，
未当作本轮重新运行；200000 tick 长录制未重复执行。

保留两轮未通过记录：`out/final-engineering-full.log` 为 1168/1169，启动回执缺少 dumps
基线字段，已增量补齐；`out/final-engineering-full-final.log` 在朝向等待中断，角度正确但
额外 sleep 增加漏采 tick 的机会。移除后专项与完整重跑通过，未放宽角度/连续 tick 判据。
构建中遗漏的函数重命名
引用已修正，最终产物构建日志为 `out/final-sdk-build4.log`。

## 仍未覆盖或未闭合

| 项目 | 当前边界 |
| --- | --- |
| 任意外部回调时序 | 已接入统一计数与所有权，现场覆盖 client/server/tick 竞争；未逐个注入所有 SDK、第三方回调和任意异常栈时序 |
| 活跃诊断下卸载 | 有明确拒绝及保持 hook/准入的现场验收；需先显式停止诊断，不支持把活跃诊断会话事务性迁移到新 DLL |
| HBUI 异常退档 | 正常退档/恢复通过，`worlds.ready=false` 异常状态的形成根因未闭合 |
| 显式诊断开销 | `serialize_ids` 可能分配且受默认 1 秒累计预算；正在执行的 SDK 调用不能被预算中断。没有可重复的提速结论 |
| 游戏网络竞态 | 共享序列化缓冲的跨线程使用是外部限制；有限未复现不等于根治 |
| 高频管道查询 | 历史高频查询后失联只保留诊断；客户端 GC/EOF 恢复不等于所有 Errno 22 已解决 |

## 取证与恢复

`LEVIPILOT_VIOLATION_DUMP` 在所有 live 入口启用增量导出，报告包含真实/合成记录、丢失和
观察空窗。合成违规与手动路由只验证设施，不能当作自然断开。

BadPacket 的静态/实测证据见 `notes/badpacket-setactordata-20261031.md`：本地深拷贝路径没有
帧长度前缀，粘连帧来自游戏共享序列化缓冲竞态。邻接堆字节不得解释为声明长度。

恢复验收应同时保存首错、配置/姿态/能力/背包/输入状态和 crash 文件清单。恢复失败不能覆盖
原始失败，也不能为通过检查而放宽保存或进入门禁。
