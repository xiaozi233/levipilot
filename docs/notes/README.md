# notes：证据册与任务提示词

这个目录由外部 `mcbe-harness-notes` 工作树合并而来，**不再单独维护第二个仓库**。
内容只做两件事：

- 逆向证据与逐 tick 调查原文（`re-fluid-12651.md`、`tick-order-12651.md`）；
- 历轮开发任务提示词（`prompts/`）。

## 证据册

| 文件 | 内容 |
| --- | --- |
| [`re-fluid-12651.md`](re-fluid-12651.md) | 1.26.51.01 流体/疾跑/泳姿的静态证据册（函数、偏移、公式、撤回项） |
| [`tick-order-12651.md`](tick-order-12651.md) | ECS 调度顺序、E14/catchup/correction、注册链与调用栈证据 |

公开结论仍以 `docs/*.md` 为准；这两册是复核时用的原始证据，不是第二套正文。
地址与角色会随版本变化，换版本前先跑 `skills/bedrock-re/roles/resolve.py --check`。

## 提示词

[`prompts/README.md`](prompts/README.md) 是索引。提示词是开发过程记录，不是现行规范；
现行约束看 `AGENTS.md` 与 `docs/`。

## 脱敏

合并时已把本机用户目录替换为 `%USERPROFILE%`，仓库路径替换为 `<repo>` 或相对路径。
提交前仍建议跑：

```bash
py -3.11 skills/bedrock-re/checks/lint_public.py
```
