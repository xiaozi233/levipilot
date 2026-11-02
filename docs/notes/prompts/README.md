# 提示词册索引

LeviPilot（`<repo>`）的开发任务提示词，按轮分册；已并入仓库 `docs/notes/prompts/`。

| 册 | 文件 | 内容 | 状态 |
| --- | --- | --- | --- |
| 第一轮 | [`task-prompts.md`](task-prompts.md) | T1–T9：崩溃落盘 / 内存原语 / 观察点 / 记录环 / 装钩 / ECS / 指纹 / panic / 开销 | 已完成 |
| 第二轮 | [`task-prompts-fixes.md`](task-prompts-fixes.md) | F1–F10：复核后的修复（缓冲上界、装钩窗口、槽位生命周期、flush 时机、offsets 接线、spool、watch、记录环、指标、防御项） | 已完成 |
| 第三轮 | [`task-prompts-input.md`](task-prompts-input.md) | G1–G6：事件级输入注入 / sprint 三组对照 / macro 事件级 / raw-input 兜底 / 世界内 UI | 已完成（G5 为条件项，未做） |
| 第四轮 | [`task-prompts-packets.md`](task-prompts-packets.md) | H0–H4：能力验收 / 被动封包 tap / CommandRequest 注入 / PlayerAction 注入 / Python 解码表 | 历史需求；主线已完成（H3 条件项未做） |
| 第四轮·续 | [`task-prompts-packet-tap.md`](task-prompts-packet-tap.md) | H1 封包 tap 修订版（H0 实测后重写：阶段 A 只读身份 + 先测"字节该从哪拿"，阶段 B 按测量选 B1/B2/B3） | 历史需求；主线已完成（**取代** packets 册里的 H1 草图） |
| 第四轮·续二 | [`task-prompts-packet-bytes.md`](task-prompts-packet-bytes.md) | H1-B 封包字节：先解 dllimport 桩（followThunk / IAT / 对象 vtable 三法）→ 只计数探针 → 按决策表选 B1/B2，B3 兜底 | 历史需求；B3 已落地为显式重序列化诊断（**取代** packet-tap 册的阶段 B） |
| 逆向·黏液 | [`task-bounce-fill.md`](task-bounce-fill.md) | R1–R3：截获 BlockCollisionsSystem 注册的 std::function 真实体 → 读出 `MoveRequest+0x58` 候选形状表的填充判据 → 与按 tick 对齐的 8 个样本对质并落盘 | 已完成（2026-10-29；实际走写观察点→`sub_1431C2F50`，见 `../../notes/bounce-12651.md` §3） |

## 使用规则

1. **公共前置只在第一册 §2**（仓库约定、架构硬约束、热路径纪律、协议增量、版本指纹）。
   派任何一册的任务时，把那段一起粘上。
2. **一轮一册，新建文件，不要追加到旧册** —— 单轮派活只给一个文件，diff 也干净。
3. 每册末尾自带**回归门禁**；新增任务时要同时更新对应册的门禁与状态。
4. 条件项（G5、H3）有明确的启动条件，**不满足就不做**，别提前上重武器。
5. 改动落地后，在仓库的 `mod/README.md`「实测 / 限制」补一行，并同步 `AGENTS.md` 的已知坑 ——
   文档要跟代码一起走。
6. **前提被实测推翻时，另起一册并注明"取代某册的某节"** —— 不要原地改历史，否则后来的人分不清哪版才是当前设计。

## 现行入口与历史边界

本目录是历史任务提示词，不是现行规范。当前工程收尾以
[`remaining-refactor-handoff-20261102.md`](remaining-refactor-handoff-20261102.md) 和
[`../../reliability-repairs.md`](../../reliability-repairs.md) 的工作表为准；API、限制和实测以
`mod/README.md` / `docs/mod-internals.md` 为准。旧 H3/G5 等条件项不自动变成新功能任务。

第四轮的封包身份、显式 B3 重序列化、命令包注入与解码已落地；`LocalNetworkPeer::sendPacket`
在本地 loopback 没有流量，不再是当前阻塞。疾跑 1.3× 已定位为移动速度属性的
`SPRINTING_BOOST`，命令派发已有 UUID/epoch 的服务端通道。BadPacket 根因见
[`../badpacket-setactordata-20261031.md`](../badpacket-setactordata-20261031.md) §49；
不重新打开已闭合调查。

逆向侧的黏液反弹候选表已在 2026-10-29 闭合（[`../bounce-12651.md`](../bounce-12651.md) §3）：
填充点在 `sub_1431C2F50`，收集盒下界 `min(preY − 0.5625×1.01, preY + δy − 0.2)`，
75 个按 tick 对齐样本 0 反例。该册留作方法与复现记录，不再派活。
