---
name: project-docs
description: LeviPilot 项目文档的写作、同步、精简和审查规范。Use whenever the user asks to write or update documentation, README, API references, migration guides, acceptance summaries, doc reviews, deduplication, or removal of stale reports and prompts; also use when code changes require documentation updates.
---

# LeviPilot 文档规范

目标：文档与当前代码一致、能直接执行、只保留必要信息，并让未验收范围和历史证据各自有清楚的位置。

## 何时使用

- 新建、更新、精简或删除项目文档。
- 代码、协议、命令、配置、构建或验收状态变化后同步文档。
- 审阅 README、指南、交接稿、验收报告、提示词或逆向公开结论。

## 先确定文档类型

| 类型 | 位置与维护方式 |
| --- | --- |
| 使用指南 | 根 `README.md`：项目用途、快速开始、测试和文档入口 |
| API/配置参考 | `mod/README.md`：当前命令、字段、配置和限制 |
| 架构与实现 | `docs/architecture.md`、`docs/mod-internals.md`：当前模块、线程、容量和诊断语义 |
| 当前验收 | `docs/reliability-repairs.md`：最新有效结果、前提和未覆盖范围 |
| 当前风险 | `docs/code-quality-review.md`：未决问题与维护顺序，不写逐轮流水账 |
| Agent 规则 | `AGENTS.md`：长期硬约束、常见坑和当前限制 |
| 逆向证据 | `docs/notes/`、`reverse/`、`skills/bedrock-re/findings/`：保存调查原文、证据等级和撤回关系 |
| 临时过程材料 | `out/`：交接稿、实验日志、候选报告和临时提示词，不随 Git 发布 |

不知道放哪里时，先更新最近的现行文档，不新建报告。通用文档规则放本 skill，逆向方法仍放
`bedrock-re`。

## 基本规则

1. **代码和实测优先**：旧提示词、旧报告和旧基线不能覆盖当前实现。无法确认的内容标为未知，
   不补写看似合理的结论。
2. **一份事实一个正主**：详细内容只维护一次，其他文档链接过去，避免复制后产生不同版本。
3. **当前文档不追加时间流水账**：直接更新正文。历史过程查 Git，逆向账本可按调查轮次追加，
   但撤回必须保留关联。
4. **保留验收边界**：区分离线/native/基础现场/完整现场，区分接受、完成和实际效果。未运行、
   required skip、未复现和未定位都要明说。
5. **精简而不丢约束**：删除重复历史和修辞，不删除命令前提、坐标语义、容量、权限、错误语义、
   未决限制或静态守卫要求的数值。
6. **公开内容脱敏**：使用仓库相对路径、`<mods>`、`%APPDATA%` 等，不写个人目录、会话令牌或
   私有运行信息。
7. **删除前先查引用**：过程性报告可以删，唯一事实、版本数据、逆向证据和未决问题不能仅因
   “旧”而删。修复所有指向已删除文件的链接。

详细写作与验收规则见 [`references/methods.md`](references/methods.md)，交付前检查见
[`references/checklist.md`](references/checklist.md)。

## 工作流程

1. 用 `git status` 和受 Git 管理的文档清单确定范围，保留他人未提交改动。
2. 对照代码、协议、测试和有效现场证据，列出“当前事实 / 待核实 / 可删除历史”。
3. 先修正事实错误，再去重和精简；不要用删减掩盖无法确认的内容。
4. 更新索引和交叉引用，清理过程性文件及其链接。
5. 执行与改动相称的检查，并如实记录结果。文档改动不宣称通过完整程序测试。

```bash
py -3.11 skills/bedrock-re/checks/lint_public.py
py -3.11 -m unittest tests.test_landing -v
py -3.11 skills/bedrock-re/checks/guards/check_statevector.py
py -3.11 skills/bedrock-re/checks/guards/check_eye_feet.py
```

同时检查所有 Markdown 相对链接的目标是否存在，并运行 `git diff --check`。涉及 offsets、
roles 或特定机制时，再运行对应生成/角色/机制守卫。

## 完成标准

- 当前行为、命令、字段和限制与代码一致；最新有效验收与未决范围清楚。
- 没有失效链接、重复正文、过期的“当前”数字或以旧报告覆盖新实现的表述。
- 精简后仍保留执行所需前提、关键数值、错误语义和逆向证据关联。
- 变更范围、实际运行的检查和未运行范围均可复核。
