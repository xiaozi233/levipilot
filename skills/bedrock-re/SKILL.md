---
name: bedrock-re
description: 静态优先的 Minecraft Bedrock 逆向与 LeviPilot 实验入口。用于定位并证明基岩版玩法机制（方块、移动、流体、ECS、封包、tick 顺序/写读顺序）的代码与数值，解析版本角色，跑逐 tick harness 实验，并把每轮结论落盘；也把非 MCBE 的逆向/安全任务路由到本地 reverse-skill pack。Use whenever the user mentions 基岩版/Bedrock 机制、Minecraft Windows 逆向、IDA-MCP、LeviPilot 实验、逐 tick 测量、方块/流体/移动数值，或需要反编译、CTF、APK、固件、malware 等授权安全任务。
---

# 基岩版逆向 + LeviPilot 实验（单一入口）

这个 skill 取代旧的 `bedrock-client-re`、`reverse-security`、`mcbe-harness-experiments` 三个入口。
**机制结论以静态为主**：动态只负责定位、对质、证伪，最后必须回到反编译、数据结构和调用关系上自洽。

## 0. 先读什么（渐进披露）

| 你在做什么 | 读 |
| --- | --- |
| 第一次进来、分诊 | 本文件 |
| 静态定位、证据分级、顺序问题 | [`references/methods.md`](references/methods.md) |
| 已知坑与回归守卫 | [`references/pitfalls.md`](references/pitfalls.md) |
| tick 管线/ECS 调度心智模型 | [`references/pipeline.md`](references/pipeline.md) |
| 建场、录制、输入注入、拟合 | [`references/experiments.md`](references/experiments.md) |
| 非 MCBE 的逆向/安全任务 | [`references/security-router.md`](references/security-router.md) |
| 版本角色 → 地址 | `docs/roles/<version>.json` + [`roles/resolve.py`](roles/resolve.py) |
| 旧账/本轮结论 | `findings/<version>/cases/<case>/`（`journal.jsonl` / `facts.md` / `resume.md`） |

## 1. 分诊

- **MCBE 玩法机制**（为什么这样表现、系数多少、谁写的、顺序是什么）→ 本 skill 的静态优先闭环。
- **LeviPilot 实验**（建场、传送、录制、注入、拟合）→ `references/experiments.md`；结论仍按 §2 升级。
- **非 MCBE 的逆向/安全**（APK、固件、协议、malware、pwn/CTF）→ `references/security-router.md`，
  路由到本地 reverse-skill pack；只做**授权目标**（自有系统、CTF、明确授权的委托）。
- **崩溃 / 协议 / 反作弊绕过**不是玩法数值问题：先按 `security-router.md` 找对应子技能，
  不要用本 skill 的机制流程硬套。

## 2. 铁律：静态优先

| 标签 | 含义 | 能拿来做什么 |
| --- | --- | --- |
| `[代码]` | 反编译里的控制流/数据流/写入点 | 可以支撑结论 |
| `[常量]` | 常量、结构布局、表、注册点 | 可以支撑结论 |
| `[实测]` | 运行期数值、调用栈、观察点、线程 | 只作现象/旁证，必须由静态解释 |
| `[推断]` | 补出来的、没有直接读到的环节 | 只能标推断，不能写成定案 |

- **动态只做三件事**：把范围缩到候选函数/分支；用实测数字对质静态模型；用反例证伪模型。
- `confirmed` 必须有 `[代码]` 或 `[常量]` 的静态闭环（写点、读序、注册顺序、数据流），
  并写清 `static_closure`。只有动态证据的结论标 `live-only`，不能进 `facts.md` 的结论区。
- **顺序类结论**必须落在同一个执行体（同线程/同 pass/同函数链），依据注册顺序、集合实现或调用图；
  跨线程、跨 pass、跨 tick 的“先后”不算调度顺序。调用栈只用于**定位**哪个 pass，
  pass 内的先后仍要静态证明。
- 静态定位失败就写 `unresolved`，**不许猜地址**；继续用动态缩小范围，但不要把猜测写进 roles。
- 一次只改一个变量；注入前先 `settle`；动态数据先问“它是不是 harness 假象”。

## 3. 五步闭环

1. **查旧账**：`findings/*/cases/*/facts.md`、`journal.jsonl`、`docs/*.md`、`AGENTS.md`、
   `mod/README.md`；再 `git log --oneline --grep <slug> -20`。别重做已结案的。
2. **静态定位**：按 `references/methods.md` 的 A–F 手法，优先“美化函数名 + 注释记录 → 代码引用”，
   其次 vtable/ctor、SDK 类型模板、常量 xref、调用者/被调用者、函数内指令扫描。
3. **静态证明**：画数据流（谁写、谁读），再证顺序（注册点、集合追加/正序、调用图）。
   最终结论必须能解释实测数字，而不是“观测到就算”。
4. **动态对质**：需要时启动游戏，用 `record` / `watch_set(depth=...)` / `blocks` 回读；
   动态只回答“是不是这条路径/这个值”，不替静态下结论。
5. **落盘 + 发布**：`driver/landing.py claim` 写证据等级与静态闭环；`end` 生成 `facts.md`/`resume.md`；
   可公开结论提升到 `docs/*.md`，账本里只留 `published: <path>#<anchor>` 指针。

```bash
py -3.11 driver/landing.py begin   --case <slug> --goal "..."
py -3.11 driver/landing.py probe   --case <slug> -- <command...>
py -3.11 driver/landing.py claim   --case <slug> --file claims.yaml
py -3.11 driver/landing.py end     --case <slug>
py -3.11 driver/landing.py close   --case <slug>
py -3.11 driver/landing.py abandon --case <slug> --reason "..."
```

## 4. 本仓库入口

```bash
# mod（xmake 默认吃满核心）
cd mod/probe && xmake -j4 -y

# 开发循环：编译 → 重启 → 等主菜单 → 直接进世界
py -3.11 driver/dev.py run --world "超平坦测试" --build

# 离线检查
py -3.11 -m unittest discover -s tests -v
py -3.11 -m ruff check driver tests && py -3.11 -m mypy

# 带游戏回归（当前基线 222/222）
py -3.11 driver/selftest.py

# 版本数据
py -3.11 driver/gen_profiles.py          # offset profile → GeneratedProfiles.h
py -3.11 skills/bedrock-re/roles/resolve.py --version 1.26.51.01 --check
```

- 版本相关数据只进 `docs/offsets/<version>.json`（mod 运行时）与 `docs/roles/<version>.json`
  （RE 角色）；`.cpp` 里不写这些常量。
- 改 mod 前读 `AGENTS.md` 的硬约束：管道线程不碰游戏对象、tick 内禁止分配/JSON/新锁、
  请求级异常变事件。
- 跑实验的硬坑（建场回读、液体戳醒、输入锁、settle、录制环）见 `references/experiments.md`。

## 5. 交付与停止条件

- 每条结论：证据标签 + 角色/地址 + 可复核路径（函数名、反编译片段、探针原文）。
- 旧结论被推翻：`erratum_of` 指向旧记录，并同步 `docs/` 的“已撤回”表；不静默改历史。
- **仓库是公开的**：不写本机用户目录的绝对路径、用户名、个人目录；用 `%APPDATA%`、相对路径或角色名。
- 连续 3 种静态策略都定不到 → 记 `unresolved`，写清已排除的假设，停下来问；不要猜。
- 静态与动态冲突 → 以静态为准，把动态结果标成待解释现象，并记录冲突与复现条件。

## 6. 旧入口

旧的 `bedrock-client-re`、`reverse-security`、`mcbe-harness-experiments` 已折叠到本 skill：
用户级/工作区的同名指针只指向这里；旧的 1.26.45.01 事实库按版本冻结，不参与新版本解析。
新机器/新克隆的目录联接步骤见 [`README.md`](README.md)。
