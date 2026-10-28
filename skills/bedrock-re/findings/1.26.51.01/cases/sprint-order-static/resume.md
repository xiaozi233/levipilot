# resume: sprint-order-static

- version: `1.26.51.01`
- goal: 静态证明 E14 写位先于水阻力，并追状态链
- environment: -
- current round: 2
- last record: r01-002
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/sprint-order-static/journal.jsonl`

## Confirmed (static closure required)

- r01-001: E14 主 pass 内 ActionSprintTrigger 的注册索引早于水阻力，因此同 tick 写位先于阻力 [code; E14 类别 id word_151DB9E14；sub_1425C5DD0 在 mode&1 时把系统索引追加到 +0x38；ActionSprint 经 0x1466567E0/0x146662E90 注册 4 类含 E14；水阻力经 0x14685D0B0/0x142DEDF10/0x142DEE020 注册 2 类含 E14；setup 调用点 0x1468230DA 早于水 ctor 0x14682658B]

## Open / pending

- r01-002: 记录位到 ADF 写位的 2 tick 可由 PlayerActionComponent 边沿写者解释 (inconclusive)

## Next actions

- 定位 PlayerActionComponent 的写者及其 E14 注册位置，闭合 2 tick 状态链
- 从 PlayerActionComponent 的写入点反查输入系统，并确认它在 E14 向量中的位置

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/sprint-order-static/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/sprint-order-static/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/sprint-order-static/probes`
