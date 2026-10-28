# MCBE 逆向 Skill 建设规格（v1 · 已落地）

> 状态：**v1 已落地（2026-10-28）**：`skills/bedrock-re/`、`driver/landing.py`、
> `docs/roles/1.26.51.01.json`、`skills/bedrock-re/checks/`；旧三个入口
> （`bedrock-client-re` / `reverse-security` / `mcbe-harness-experiments`）已移到
> `skills-backup/`，新实体通过目录联接/指针发现。评审决议见 §11。
> 读者：在本仓库（LeviPilot）工作区里做逆向的模型。
> 用法：先通读，再结合你在这个仓库里的实操经验做增删改；末尾「开放问题」请逐条回答。
> 冲突裁决：**仓库代码与实测 > 本规格 > 旧 skill 文档**。本规格只定结构、约束和验收，不规定具体结论。

---

## 0. 一句话目标

做一个**单一**的基岩版逆向 skill：方法层不随版本变、版本相关的事实靠解析得到、每跑完一轮自动落盘、每个已知坑都有可执行守卫。

三个硬指标，必须可测：

1. **方法层零裸地址** —— `references/methods.md` 里不出现 `0x1[45]xxxxxxxx`。
2. **换版本方法层零改动** —— 改 `docs/offsets/<version>.json` + `docs/roles/<version>.json`
   后重跑解析，`methods.md` 的 `git diff` 必须为空。
3. **每轮有账** —— 任意一轮结束后，`findings/<version>/cases/<case>/journal.jsonl` 里有该轮记录、
   `probes/` 里有探针原文、`resume.md` 已更新。

---

## 0.5 第一原则：静态优先（先于下面所有章节）

本工作区的工具几乎全是动态的（管道、录制环、观察点、输入注入），但它们只承担三件事：
**定位**（缩小到候选函数/分支）、**对质**（实测数字是否与静态模型一致）、**证伪**（让静态模型失败）。
**机制结论只能来自静态**：控制流、数据流、写入顺序、常量与结构。

- 允许：先用动态把范围缩小到候选函数，再回到 IDA 静态证明。
- 禁止：把「第 3 tick 出现 0.01959」这类观测直接写成机制结论；没有静态写者/读序证据时，
  只能记 `inconclusive` 或留在假设区。
- 每条 `confirmed` 必须指向至少一条 `[代码]`（控制/数据流）或 `[常量]`（常量/结构）证据；
  `[实测]` 只作旁证。动态独占结论标 `live-only`，不得进 `methods.md` / `facts.md` 的结论区。
- 顺序类结论必须给出静态顺序证据（注册点顺序、集合实现、调用图），不能只靠 tick 观测差异
  —— 1.26.51.01 的水阻力相位就吃过这个亏：早期「阻力读上一 tick 的位」由观察点顺序推断，
  静态复核（ActionSprintTrigger 注册点早于 travel + 集合追加/正序）后改成「写点在阻力之前」；
  主 pass 归属另由调用栈 `[实测]` 确认，不作为静态结论。
- 静态无法定位时，按 L2 写 `unresolved`，不猜地址；动态继续只用于缩小搜索。

证据分级沿用 [`docs/reverse-engineering.md`](reverse-engineering.md) §0：
`[代码]` > `[常量]` > `[实测]` > `[推断]`；只有前两级能支撑定案。

---

## 1. 非目标（明确不做）

- **不替代 `AGENTS.md`**。编码规范、加命令的标准步骤、构建流程仍以 `AGENTS.md` 为准，skill 引用而不复制。
- **不复制 `docs/` 已有内容**。`architecture.md`、`mod-internals.md`、`porting.md`、`scenario-dsl.md` 已存在，skill 只写到它们的指针。
- **不重造版本机制**。`docs/offsets/<version>.json` + `driver/gen_profiles.py` + `GeneratedProfiles.h` 已经是单一事实源，见 `AGENTS.md` 硬约束第 6 条。
- **不把旧 1.26.45.01 的 1848 个地址搬进来**。旧版结论只作为"历史对照"，按版本目录冻结，不参与新版本解析。
- **不新增 skill 副本**。`~/.agents/skills/<name>/` 必须是指向仓库内实体的链接或一行指针文件，不能是第二份正文。

---

## 2. 现状盘点（重建的理由，均有实测数字）

旧 skill（`reverse-skill/skills/reverse-engineering/bedrock-client-re/`）：

| 指标 | 数值 | 问题 |
| --- | --- | --- |
| `SKILL.md` | 31,011 B / 364 行 / 29 个标题 | 一次读完吃掉大量上下文，无渐进披露 |
| `known-facts.md` | 389,463 字符 / 6,262 行（560,970 B） | 方法、结论、地址混在一起 |
| 其中 9 位地址 | **1,848 个唯一** | 版本耦合全部隐式，无法判断哪条还成立 |
| 提到版本号的行 | **17 行** | 地址没有版本标签 |
| `[代码]/[实测]/[常量]` 标签 | **69 处**（含 `[推断]`） | 证据纪律只写在规范里，没落到事实里 |

新仓库已有的能力（**不要重复实现**）：

- `docs/offsets/<version>.json`（运行时偏移表，带 `"runtime": true`）+ `driver/gen_profiles.py` → `GeneratedProfiles.h`；无 profile 时 `version_ok=false` 降级而不是崩溃 —— **这就是 mod 层的"角色→值"解析**。
- `scenario-dsl`（setup → steps → verify/teardown → JSONL + summary）—— **现有的事件账本**。
- tick 记录 spool：`<modDir>/spool-<pid>.jsonl`（100 ms / 64 KB 刷盘），崩溃取证 `crash-<pid>-<tick>.dmp`。
- `docs/reverse-engineering.md` —— 静态优先工作流、无符号定位 A–F、顺序问题做法、动态取证边界、16 条坑。
  **这是 L1 的种子，不要另写一份**；skill 的 `methods.md` 由它提炼，而不是复制。
- `skills/bedrock-re/references/experiments.md` —— 在本仓库跑实验的操作手册（settle/录制/输入坑），
  已合并原 `mcbe-harness-experiments`，不再维护第二份。
  **不与本 skill 合并**，只交叉引用（见 §2.5.4）。
- `AGENTS.md` 的「已知坑」**36 条**（全文顶层 bullet 42 条），全部是实测踩出来的 ——
  这是 pitfalls 层最贵的原料。`mod/README.md` 的使用限制与 `docs/mod-internals.md` 的实测数字
  是另两处必须对齐的事实源。

**结论**：版本无关的机制在 mod 层已经成立；skill 层要做的是**对齐它并补上 RE 侧的角色解析与落盘**，不是另起一套。

---

## 2.5 本工作区的使用（动态只做辅助）

skill 不是独立工具链，必须复用本仓库已有入口；**不要重写 `driver/`，也不要另造 tick 记录格式**。

### 2.5.1 入口与基线

```bash
# mod（xmake 默认吃满核心，必须 -j4）
cd mod/probe && xmake -j4 -y

# 开发循环：编译 → 重启 → 等主菜单 → 直接进世界
py -3.11 driver/dev.py run --world "超平坦测试" --build

# 离线检查（无游戏）
py -3.11 -m unittest discover -s tests -v
py -3.11 -m ruff check driver tests && py -3.11 -m mypy

# 带游戏回归（改动后必须留一条检查）
py -3.11 driver/selftest.py        # 当前基线 221/221
```

Python 客户端是 `driver/harness/`（`facade.Harness`、`commands/*` 的 `ModClient`）；协议与超时语义
看 `connection.py` / `protocol.py`。需要改 mod 时遵守 `AGENTS.md` 的硬约束（tick 内禁止
`std::string`/JSON/动态分配、管道线程不碰游戏对象），本 skill 不教怎么改 mod。

### 2.5.2 实验闭环（只写指针，不复制 recipes）

- **建场 + 回读**：`lab.Builder`、`blocks`/verify；方块类 `command` 是 deferred，
  `success=false` 不作判据。
- **前置状态**：`lab.settle(...)`；液体场景用 `on_ground=None` + `include_vertical=False`；
  走/跑对照必须单独检查 `input_sprint=False`（静止锁存的 sprint 会把走路基线变成疾跑）。
- **逐 tick 数据**：`lab.record_session()` / `record_start`–`stop`（64 B/tick 环）；
  需要调用栈时 `watch_set(thread=..., depth=...)`（默认 8 帧，上限 32）。
- **输入**：脚本开头 `input_lock(True)`，异常路径也要解锁；`set_input` 同时写客户端与服务端。
- 具体步骤、坑与参数模板的唯一正文在 `skills/bedrock-re/references/experiments.md`，
  本 skill 只写指针。

### 2.5.3 静态侧

- IDA MCP **只读复用** `.i64`；无 RTTI 时用「美化函数名 + 注释」建立代码引用
  （`docs/reverse-engineering.md` §3A）。
- 版本数据：`docs/offsets/<ver>.json` + `driver/gen_profiles.py` → `GeneratedProfiles.h`；
  RE 侧的 `docs/roles/<ver>.json` 独立维护（见 L2），**不合并进 offsets**。
- 事实源：`AGENTS.md`（硬约束/坑）、`mod/README.md`（使用限制）、
  `docs/mod-internals.md`（实测数字）、`docs/fluid-movement.md`（已结案例）。

### 2.5.4 单一入口的边界

v1 已把 `mcbe-harness-experiments` 合并进本 skill：静态证明与动态实验都在
`skills/bedrock-re/` 内，实验 recipes 在 `references/experiments.md`，不再维护第二份。
非 MCBE 的逆向/安全任务由 `references/security-router.md` 路由到本地 reverse-skill pack；
同一坑只在一处写完整形态。

### 2.5.5 发布路径（`facts.md` → `docs/`）

`findings/<ver>/cases/<case>/facts.md` 是**工作账本草稿**，不是公开事实源。`close` 时把可公开结论提升到
`docs/*.md`（或 `mod/README.md`、`mod-internals.md`），并在 journal/facts 里留
`published: <path>#<anchor>`。`docs/` 仍是单一正文处；旧账、探针原始输出、个人路径不进 `docs/`。
仓库是公开的：任何要提交的文本先过脱敏（见 §7 第 1 步）。

---

## 3. 三层结构

按半衰期拆，不要按主题拆：

### L1 方法层（慢，`references/methods.md`）

- 内容：分诊、实验闭环（settle → 注入 → 逐 tick 采样 → 回读验证）、定位手法（常量反查 / 组件 id 认名 / vtable 差分 / 调用图）、验证纪律、可读化交付。
- 写法：只用**角色名**，例如 `@gate`、`@clip`、`@step_block`、`@move_attr`、`@sprint_boost`。
- 禁止：裸地址、具体版本号、具体组件的 32 位 id 数值（这些属于 L2/L3）。

### L2 解析层（中，`docs/roles/<version>.json`）

- 内容：RE 侧角色 → 解析策略与结果。**与 offsets 表分开放**：offsets 是 mod 运行时读的 C++ 结构偏移，roles 是给逆向用的函数/全局地址与签名，两者的生命周期和消费者不同。
- 结构建议：

```json
{
  "version": "1.26.51.01",
  "resolved_at": "2026-10-27T00:00:00Z",
  "roles": {
    "@clip": {
      "strategy": ["string:0x14EC05467", "vtable:0x14EA12D40+0x28", "callgraph:from:@move_runner"],
      "address": "0x14CC58710",
      "confidence": "code+live",
      "evidence": ["[代码] …", "[实测] …"]
    },
    "@gate": { "strategy": [...], "address": null, "status": "unresolved", "note": "本版本未找到，禁止猜测" }
  }
}
```

- 规则：
  - 多策略按序尝试，命中即停，**记录用了哪一条**。
  - 失败必须写 `"status": "unresolved"`，**不允许填猜测地址**。宁可让下游报"未知"，也不能给错地址。
  - 任何 `address` 必须能追溯到一条证据。
  - `roles` 不要求 `"runtime": true`（那是 mod 运行时开关）；RE 解析只认版本号，
    所以没有 runtime profile 的版本也能有 roles。

### L3 事实层（快，`findings/<version>/cases/<case>/`）

- `journal.jsonl`（append-only，工具自动写）
- `old-account.md`（`begin` 自动 grep 旧账；只作去重线索）
- `facts.md`（由 journal 生成，**禁手改**）
- `resume.md`（每轮生成，≤200 行，压缩/重连后读它续跑）
- `report.md`（`close` 生成：A 静态结论 / B 未定 / C 原始证据）
- `probes/<id>.{cmd,py,out}`（探针命令/原文与原始输出，原样保存）
- 旧版本目录原样冻结，不参与新版本解析。

### 单一定义规则

同一条事实只在一处写正文，其余位置放指针。具体：

- 工作账本（含假设、未定案、勘误）→ `findings/<ver>/cases/<case>/facts.md`（生成物）
- **已发布结论正文 → `docs/*.md`**（`facts.md` 里只留 `published: <path>#<anchor>` 指针；
  避免 skill 账本与仓库文档各写一份、随后漂移）
- 手法正文 → `references/methods.md`
- 坑正文 → `references/pitfalls.md`
- 探针原文 → `findings/<ver>/cases/<case>/probes/`（原样保存，不手改）
- 记忆（ZCode memory）→ **只留一行指针**，不写正文

---

## 4. 每轮自动落地（本规格的核心）

### 4.1 「一轮」的定义

**一条假设被证实、被证伪、或判为无法判定**，算一轮。不是"聊了几轮"，也不是每个小步。

### 4.2 双写

| 层 | 谁写 | 可靠性 |
| --- | --- | --- |
| 原始记录 `journal.jsonl` | **工具层自动**（`landing.py probe --` 包装每次探针） | 不依赖模型自觉 |
| 静态取证记录 | 模型经 `landing.py claim` 写入（IDA MCP 调用无法自动捕获），标 `origin: manual` | 靠 `close` 检查补齐 |
| 提炼记录（`facts.md` / 报告） | `landing.py end` 生成 | 允许模型忘记，可从原始记录重建 |
| **结论门** | 任何人 | `confirmed` 必须有 `[代码]`/`[常量]` 的静态闭环；`[实测]` 只作旁证 |

这条是长任务的生死线：模型会忘、上下文会被压缩、进程会死。把落盘挂在工具调用上，忘记也不会丢事实。
但**工具自动记录只能保证"做过"，不能保证"证明过"**；静态闭环仍必须在 `claim` 里写明。

### 4.3 journal 记录 schema

```json
{"round": 12, "seq": 1, "ts": "2026-10-27T23:40:12Z",
 "version": "1.26.51.01", "session": "harness-<pid>",
 "kind": "probe | claim | erratum | dead-end | decision",
 "origin": "tool | manual",
 "hypothesis": "水中可见的 0.9 来自主 pass 的 setSprinting 写入，且写入排在阻力之前",
 "probe": {"script": "probes/r12-01.py", "output": "probes/r12-01.out",
           "scenario": "out/fluid-verify/data/r12.jsonl",
           "scope": "tick 40..60 / in_water / sprint=on",
           "params": {"settle": true, "input_lock": true}, "limit": 200},
 "result": "confirmed | refuted | inconclusive | live-only",
 "evidence_tier": "code | constant | live | inference",
 "static_closure": "E14 主 pass 内调用写点；阻力读位在同 tick 其后（注册顺序 + 分派 + 0x14B17C870）",
 "evidence": "[代码] ActionSprintTrigger tick → Actor::setSprinting；[实测] 第 3 tick 位移 0.01959",
 "roles": {"@sprint_write": "0x14B17C820", "@move_attr": "0x14289A180"},
 "next": "验证输入晚一拍时的相位",
 "erratum_of": null}
```

**必填字段与理由**：

- `probe.scope` —— 负面结论只证明搜索口径。没有范围/参数/上限的否定结论无法复核，这是最高频的假结论来源。
- `probe.script` + `probe.output` —— py_eval / 场景脚本常是一次性的，压缩后写不回来；原文留存才允许重放。
- `probe.scenario` —— tick 级原始数据仍留在场景 JSONL，journal 只存路径，不重复记账。
- `evidence_tier` + `static_closure` —— 静态优先的落点：`confirmed` 只能配 `code` / `constant`，
  且必须写清静态证据（写点、注册顺序、数据流）；只有动态证据时记 `live-only`，不得进结论区。
- `origin` —— 区分工具自动记录与人工补录（IDA 侧），`close` 据此检查缺口。
- `erratum_of` —— 勘误可追溯，避免把上一轮的错结论原样搬进人读文档。
- `roles` —— 把本轮涉及的地址绑定到角色，换版本时可批量重解析。**示例里的地址只是示意：
  `@move_attr` 是属性聚合器，不是疾跑乘数；`@sprint_boost` 是 modifier 静态初始化，两者不要混用。**

### 4.4 命令

```bash
py -3.11 driver/landing.py begin   --case <slug>    # 建骨架 + grep 旧账（避免重做已结案）
py -3.11 driver/landing.py probe   --case <slug> -- <command>  # 包装探针：自动写 journal + 存原文
py -3.11 driver/landing.py claim   --case <slug> --file claims.yaml  # 静态结论/roles/证据等级
py -3.11 driver/landing.py end     --case <slug>    # 生成 facts.md 小节 + 更新索引 + 写 resume.md
py -3.11 driver/landing.py close   --case <slug>    # 三册报告 + 跑 checks
py -3.11 driver/landing.py abandon --case <slug> --reason ...  # 显式作废未结案，避免 pending 死锁
```

`landing.py` 放在 `driver/`（与 `dev.py`/`gen_profiles.py` 同级，纳入 ruff/mypy/离线测试）；
skill 目录不放第二份。IDA MCP 与 `py_eval` 的静态取证无法被包装，走 `claim` 的
`origin: manual` 补录，由 `close` 检查缺口。

### 4.5 强制门（防止跳过）

- `begin` 先检查上一轮的 `pending`（有 probe 但未提炼）条数 > 0 ⇒ 明确报「上轮未落盘」并拒绝开始新一轮；
  **但 `abandon` / `inconclusive` 结案可放行**，否则故意留下的未决探针会把流程永久卡死。
- `close` 跑 `checks/`：每轮有 journal 记录、有探针原文、结论带证据标签；`confirmed` 还必须有
  `evidence_tier ∈ {code, constant}` 与非空 `static_closure`。缺失标 `MISSING`，**不静默通过**。
- 崩溃后：journal 是 append-only + 幂等 `(round, seq)`，直接续写；`resume.md` 是恢复入口。
  探针输出可能只写了一半，`close` 用文件哈希/大小为 0 检出并标 `partial`。

### 4.6 与现有 DSL/spool 的关系

**优先复用** `scenario-dsl` 的 JSONL 字段与 summary 结构，不要发明第二套 tick 记录格式。`journal.jsonl` 只承载"结论与探针元数据"，tick 级原始数据仍留在场景输出里，两者用 `probe.output` 路径关联。

### 4.7 成本约束

每轮只写**结构化短记录**，不写散文；长论述留给 `close` 阶段的报告成文。否则"每轮写文档"会贵到模型自动偷懒。

---

## 5. 坑 → 可执行守卫

`references/pitfalls.md` 每条必须有归属：`guard: checks/xxx` 或 `unverified-manual`。不允许既无守卫也无标注。

优先把「会静默给出错结论」的坑做成守卫，例如（前 9 条来自 `AGENTS.md` 已知坑，
后 2 条来自本轮静态优先复核）：

| 坑 | 守卫形态 |
| --- | --- |
| 改了 `docs/offsets/*.json` 忘了生成 `GeneratedProfiles.h` | 已有 `tests/test_profiles.py`，skill 在 `close` 时调用 |
| 版本锚点是 `jmp [rip+0]` thunk，绝对地址随 ASLR 变 | 解析器强制"跟到真实函数"后再取地址；未跟到时置 `unresolved` |
| `StateVectorComponent+0x18` 不是速度（是 0.546×位移） | 判速度一律用相邻 tick 位置差；探针模板里注释掉该字段读取 |
| `pos.y` 是脚部，StateVector 里是眼睛（+1.6200101） | 回读校验函数断言两者关系 |
| `TeleportRequest.clearEffects` 共享字段泄漏 | `teleport` 后回读药水效果数量 |
| `set_input(sprint=True)` 可能触发一次 0.42 跳跃 | 每步前 `lab.settle()` 验证 `on_ground`/`sprinting`；已有 `sprint_no_jump` 回归 |
| `harness.json` 残留 `lock_input:true` 会让手动开的游戏点不动 | 开工前检查 `state.input.locked` |
| `command` 的方块类命令是 deferred | 一律以 `blocks` / verify 回读为准，不看 `success` |
| 请求级 AV 不是可捕获异常 | 新 handler 用 `__try/__except`；不要把会崩的代码放 tick 线程 |
| 顺序结论只靠 tick 观测差异 | `claim` 必须附注册顺序/写点/调用图；否则 `inconclusive` |
| 动态读数是唯一证据 | `evidence_tier` 必填；`confirmed` 只接受 `[代码]`/`[常量]` 闭环 |

**工具层薄封装**（不重写 `driver/`，只加预检）：

- `settle_before_inject()`：复用并扩展 `lab.settle(...)`，补场景预设（水/岩浆/骑乘/爬行）与
  失败原因输出，禁止裸 `sleep` 当前提。
- `readback_verify()`：写入类操作后回读，失败即 `inconclusive` 而不是 `confirmed`。
- `input_lock` 生命周期：脚本开始上锁、结束解锁、异常路径也要解。
- `landing.py claim`：`confirmed` 缺 `static_closure` 时直接拒绝写入（而不是事后检查）。
- IDA 侧：把超时、会话丢失、只读 `.i64`、`database` 必填封成默认，skill 只写语义调用。

---

## 6. 目录（最小增量）

```
skills/bedrock-re/                 # 仓库内唯一实体
├── SKILL.md                       # 6–8 KB：分诊 → 闭环 5 步 → 硬纪律 → 何时读哪个文件
├── README.md                      # 安装/联接与维护检查
├── references/
│   ├── methods.md                 # L1：只用 @role，零裸地址
│   ├── pitfalls.md                # 坑 + guard 归属
│   ├── pipeline.md                # tick 管线心智模型（角色名）
│   ├── experiments.md             # 建场/录制/输入/拟合（合并原 harness skill）
│   └── security-router.md         # 非 MCBE 任务 → 本地 reverse-skill pack
├── roles/
│   ├── resolve.py                 # 读取 docs/roles/<ver>.json → 供脚本/skill 使用
│   └── schema.json
├── tools/
│   └── ida.py                     # IDA MCP 薄封装（超时/只读/会话默认）
├── checks/
│   ├── lint_skill.py              # 零裸地址、链接可达、未见 `def ` 进 py_eval 等
│   ├── lint_report.py             # 证据标签、地址可解析、MISSING 检测
│   ├── lint_public.py             # 脱敏：无用户绝对路径/机器信息
│   └── guards/                    # 上表的具体断言
└── findings/
    ├── README.md
    ├── _template/claims.example.yaml
    └── <version>/
        ├── INDEX.md
        └── cases/<case>/
            ├── case.json
            ├── old-account.md # begin 生成
            ├── journal.jsonl
            ├── facts.md       # 生成物
            ├── resume.md      # 生成物
            ├── report.md      # close 生成
            └── probes/
```

落盘工具实现为 `driver/landing.py`（见 §4.4）；skill 目录不放第二份。
`~/.agents/skills/bedrock-re/SKILL.md` 是指针文件，**必须保留 `name`/`description` frontmatter**
才能被 ZCode 发现；优先用目录联接（参考 `.zcode/skills` 的现有做法），不要复制正文。

**不做**：`skills/` 下放第二份；`~/.agents/skills/` 放正文副本。

---

## 7. 迁移步骤（可回滚，按序）

1. **脱敏 + 冻结旧版**：先跑 `checks/lint_public.py`。旧 `known-facts.md` 含本机用户目录的
   绝对路径（实测 1 处），用户级指针 `SKILL.md` 也含绝对环境路径；**仓库是公开的**，
   这些一律删改成 `%APPDATA%`、`%USERPROFILE%` 或相对路径；然后才把 `known-facts.md` 按版本归档为
   `findings/1.26.45.01/`（只读），不再编辑。
2. **拆层**：脚本按正则把旧文件分成「含地址/常量」「含手法/陷阱」「其他」三堆；前两堆人工复核后分别进 L3 与 L1。
3. **建 roles**：只解析当前最常用的 10–15 个角色，跑通链路；其余留 `unresolved`，按需补。
4. **建落盘**：`landing.py` 六命令（begin/probe/claim/end/close/abandon）+ journal schema + 强制门。
5. **建守卫**：先做上表 11 条，其余标 `unverified-manual`。
6. **试跑两轮**：用一个新 case 验证"轮结束 → 账目完整 → resume 可续"。
7. **收敛副本（已执行）**：仓库内实体是唯一正文；`.zcode/skills/bedrock-re` 与
   `~/.agents/skills/bedrock-re` 已建立目录联接；旧三个入口移到 `skills-backup/`，
   不再被 skill 发现。

回滚：每步都是新增文件；第 7 步之前删掉新增目录即可复原。

---

## 8. 验收清单（可自动跑）

- [ ] `methods.md` 中 `grep -c '0x1[45][0-9A-Fa-f]\{7\}'` 为 0
- [ ] 所有 `@role` 引用都能在 `docs/roles/<ver>.json` 找到，或落在 `unresolved` 列表
- [ ] `docs/roles/<ver>.json` 与 `docs/offsets/<ver>.json` 版本一致；`unresolved` 不含猜测地址
- [ ] 换版本模拟：用只读 fixture（旧 schema 的 `docs/offsets/1.26.45.01.json` 或
      `tests/fixtures/` 副本）重跑，`methods.md` 的 `git diff` 为空；不修改 live profile
- [ ] 任取一轮：`journal.jsonl` 有记录、`probes/` 有 `.cmd`/`.py` 与 `.out`、`resume.md` 已更新
- [ ] 每条 `confirmed` 的 `evidence_tier ∈ {code, constant}` 且 `static_closure` 非空
- [ ] 顺序类结论附静态顺序证据（注册顺序/写点/调用图），否则为 `inconclusive`
- [ ] `close` 能对缺证据的结论报 `MISSING`（故意造一条验证）
- [ ] `lint_public.py` 全绿：仓库内无本机用户目录绝对路径、盘符项目路径、机器名
- [ ] `checks/` 全绿；`tests/test_profiles.py` 全绿
- [ ] 仓库内只有一份 skill 正文；`~/.agents/skills/bedrock-re/` 是目录联接或带 frontmatter 的指针

---

## 9. 给评审模型的开放问题

请逐条回答，答案直接决定 v1 的形态（本版答复已并入 §11）：

1. **`docs/roles/<version>.json` 是否该并入 `docs/offsets/<version>.json`？** 我倾向分开（消费者不同：C++ 运行时 vs 逆向脚本），但如果你认为维护两份 JSON 会漂移，请给出合并后的字段设计。
2. **哪些现有 `AGENTS.md` 已知坑应该升级为守卫，哪些必须留人工？** 请按"会不会静默给错结论"排序，给出你建议的前 10 条。
3. **`settle` 的成功判据在不同场景（水中/岩浆/骑乘/爬行）是否需要不同参数？** 现有 `lab.settle(...)` 的哪些检查在你实际跑的时候是假阳性来源？
4. **场景 DSL 的 JSONL 与我们定义的 `journal.jsonl` 会不会重复记账？** 如果会，应该合并还是分层（tick 原始 vs 结论元数据）？
5. **版本无关的边界在哪？** 哪些结论你认为本质上不可能版本无关（例如某版本的特性 glitch），需要在方法层显式写"此处允许版本特例"？
6. **`resume.md` 200 行够不够？** 你实际续跑时最少需要哪些字段？
7. **`landing.py begin` 的"查旧账"应该 grep 哪些范围**才算既不漏又不慢（`findings/*/facts.md` + `docs/*.md` + git log？）。
8. **现有多份 skill 副本**（本仓库 `skills/`、`~/.agents/skills/`，md5 相同）如何收敛最不痛：符号链接、指针文件、还是保留一份并从另一处删？

---

## 10. 现成素材处置表

| 素材 | 处置 |
| --- | --- |
| `AGENTS.md` 硬约束 / 加东西的标准步骤 | 引用，不复制；提炼成 `methods.md` 的角色流程 |
| `AGENTS.md` 已知坑（**36 条**） | 迁入 `pitfalls.md`，逐条加 guard 归属 |
| `docs/reverse-engineering.md` | **L1 的直接种子**：静态优先工作流、定位 A–F、顺序做法、动态边界、16 条坑 |
| `docs/architecture.md`、`mod-internals.md` | 只放指针 |
| `docs/porting.md` | 引用为"换版本流程"的权威 |
| `docs/offsets/<ver>.json` + `gen_profiles.py` | 视为 L2 的既有实现，扩展而非替代 |
| `skills/bedrock-re/references/experiments.md` | 实验操作手册已并入本 skill；不再单列 |
| `docs/scenario-dsl.md` | 引用为事件账本格式的权威 |
| `docs/legacy-frida-1.26.45.md` | 归档为历史路径，不作为现行方法 |
| `docs/offsets-1.26.45.01.md` | 冻结进旧版本 findings，不参与新版本解析 |
| 旧 `known-facts.md`（1,848 地址） | 只作为旧版本冻结事实；方法论段落拆出后原文归档 |
| 旧 `techniques.md`（31 个编号手法） | 拆入 `methods.md`，去掉地址后按角色重写 |

---

## 11. 评审决议（v1，2026-10-27）

评审结论：三层结构、单一事实源、自动落盘、坑→守卫四个方向成立，v0 采纳；本版已补
§0.5（静态优先）与 §2.5（本工作区使用），并修正了 §2 的实测数字。下面逐条回答 §9。

1. **roles 是否并入 offsets：不并。** 消费者与生命周期不同（C++ 运行时结构偏移 vs RE 函数/全局），
   合并会让每次逆向查询都触发 `GeneratedProfiles.h` 重生成。roles schema 增加
   `version` / `resolved_at` / `resolver` / `source_binary`（模块名 + 构建 id，不写绝对路径）/
   `status` / `offsets_ref`。CI 断言：两文件 `version` 相等；`offsets_ref` 必须存在；
   `address` 必须落在可执行段且有 evidence；`unresolved` 不允许有地址。
2. **升级为守卫的前 10 条**（按"静默给错结论"排序）：① `StateVectorComponent+0x18` 不是速度；
   ② `pos.y` 是脚部 / StateVector 是眼睛；③ 方块类 `command` 是 deferred，`success=false` 不判失败；
   ④ `state.input.sprint` 静止锁存，走路基线变疾跑；⑤ `set_input(sprint=True)` 的隐藏 0.42 跳跃；
   ⑥ `TeleportRequest.clearEffects` 共享字段泄漏；⑦ 把 `jmp [rip+0]` thunk 的绝对地址当签名；
   ⑧ 改 offsets 未跑 `gen_profiles.py`；⑨ 顺序结论只靠 tick 观测；⑩ 原始 StateVector 当速度
   （0.546× 与 `getVelocity()` 混淆）。留人工：请求级 AV/`__try`、暂停菜单按键、真实鼠标/后台、
   DR 线程归属、进世界 UI 自动化。
3. **settle 需要场景预设，不能一套参数走天下**：地面 `on_ground=True, include_vertical=True,
   input_sprint=False`；水/岩浆 `on_ground=None, include_vertical=False`，并显式设
   `in_water`/`in_lava`；骑乘/爬行现有 state 字段不足，应返回 `settle_unsupported`，不许假装通过。
   假阳性来源：`still` 用速度字段而非相邻 tick 位移；地面残留 `vy`；`sprinting`（ADF 位）与
   `input.sprint` 混用；`in_lava` 只按方块名匹配。建议 `settle` 返回每条检查的 `fail_reason`。
4. **journal 与场景 JSONL 分层，不合并。** 场景 JSONL 存 tick 原始数据（大、机器读）；
   journal 存结论与探针元数据（小、人/模型读），用 `probe.scenario` + 输出哈希关联。
   journal 里不再重复 tick 行；`close` 需要时做 join。
5. **版本无关的边界**：可无关——阻力→加速度→移动的顺序、属性聚合、modifier 乘法应用、
   ECS tick 顺序=注册顺序；必须版本特例——glitch/兼容行为（0.42 隐藏跳跃、0.028 闩锁、
   水中相位）、输入/UI 怪癖、数据驱动的实体定义乘数、版本常量。`methods.md` 允许
   `@version-exception` 块，但只写版本范围 + 角色指针，不写裸地址。
6. **`resume.md` 200 行够用**，前提是字段固定：case/goal、版本 + profile 哈希、环境
   （世界/模式/输入锁/前台）、当前假设、已确认事实（journal id）、未决假设、pending 探针及原因、
   最后轮次/seq、下一步 3 个动作、阻塞项、发布指针。旧历史只放链接，不写散文。
7. **`begin` 查旧账的范围**：先查结构化索引——`findings/*/cases/*/facts.md`、
   `findings/*/cases/*/journal.jsonl`（按 roles/地址/假设）、`findings/*/INDEX.md`、
   `docs/*.md`、`AGENTS.md`、`mod/README.md`；再 `git log --oneline --grep <slug> -20`。
   跳过 `probes/`、`out/`、二进制；整个查询限时 ~2 s，用 ripgrep 的 glob 排除。
8. **副本收敛（已执行）**：仓库实体是唯一正文。`.zcode/skills/bedrock-re` 与
   `~/.agents/skills/bedrock-re` 已改为目录联接；旧三个入口（`bedrock-client-re`、
   `reverse-security`、`mcbe-harness-experiments`）已移到 `skills-backup/`，不再被
   skill 发现。无联接权限时用带 frontmatter 的一行指针，不放正文副本。

---

*本规格只约束结构与验收；具体数值、地址、判据以仓库实测为准。*
