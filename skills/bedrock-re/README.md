# bedrock-re

静态优先的 Minecraft Bedrock 逆向 + LeviPilot 实验 skill。入口是 [`SKILL.md`](SKILL.md)；
本文件只讲安装/维护。

## 目录

| 路径 | 内容 |
| --- | --- |
| `SKILL.md` | 分诊、铁律、五步闭环、命令入口 |
| `references/methods.md` | 静态定位手法与证据分级（零裸地址） |
| `references/pitfalls.md` | 坑 + 可执行守卫/人工项 |
| `references/pipeline.md` | tick/ECS 管线心智模型（角色名） |
| `references/experiments.md` | 建场/录制/输入/拟合（原 `mcbe-harness-experiments`） |
| `references/security-router.md` | 非 MCBE 任务 → 本地 reverse-skill pack |
| `roles/` | 角色解析器与 schema；数据在 `docs/roles/<version>.json` |
| `tools/ida.py` | IDA MCP 只读薄封装 |
| `checks/` | 裸地址、证据、脱敏、角色与漂移守卫 |
| `findings/` | 每轮账本（case 级 journal/facts/resume/report/probes） |

## 安装（ZCode 发现）

仓库内实体是唯一正文。工作区/用户级用目录联接，不复制：

```bat
mklink /J <repo>\.zcode\skills\bedrock-re <repo>\skills\bedrock-re
mklink /J %USERPROFILE%\.agents\skills\bedrock-re <repo>\skills\bedrock-re
```

`.zcode/` 已被 git 忽略；新机器/新克隆要重新建联接。没有联接权限时，在
`%USERPROFILE%\.agents\skills\bedrock-re\SKILL.md` 放一个带 `name`/`description`
frontmatter 的一行指针，不要复制正文。

## 维护检查

```bash
py -3.11 skills/bedrock-re/checks/lint_skill.py
py -3.11 skills/bedrock-re/checks/lint_public.py
py -3.11 skills/bedrock-re/checks/guards/check_roles.py
py -3.11 skills/bedrock-re/roles/resolve.py --check
py -3.11 -m unittest tests.test_landing -v
```

`driver/landing.py close` 会跑 `lint_report` + 静态守卫 +（默认）`test_profiles`；
`docs/roles/<version>.json` 与 `docs/offsets/<version>.json` 的版本必须一致。

## 一轮的用法

```bash
py -3.11 driver/landing.py begin   --case <slug> --goal "..."
py -3.11 driver/landing.py probe   --case <slug> --scope "..." --scenario out/<case>/x.jsonl -- <command...>
py -3.11 driver/landing.py claim   --case <slug> --file claims.yaml
py -3.11 driver/landing.py end     --case <slug>
py -3.11 driver/landing.py close   --case <slug>
```

`begin` 会先查 `findings`/`docs`/`git log` 写 `old-account.md`；`claim` 的 `confirmed`
必须有 `evidence_tier: code|constant` 和非空 `static_closure`。
