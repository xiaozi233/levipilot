# resume: swim-jump-sneak

- version: `1.26.51.01`
- goal: 确认泳姿下跳跃/潜行对竖直速度的额外项与终速
- environment: -
- current round: 1
- last record: r01-003
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/swim-jump-sneak/journal.jsonl`

## Confirmed (static closure required)

- r01-001: 水中的跳跃增量由独立系统施加，泳姿 lambda 只处理俯仰 [code; 反编译 0x1495EA890：v21=0.028/0.04；LABEL_40 处 *(v19+28) = v21 + vy；另有 0.2f/0.15f 分支；0x1490C0A00 仅按 SWIMMING + pitch 写 vy，无输入位读取]

## Open / pending

- r01-002: 泳姿只关掉重力，跳跃/潜行的固定项照常叠加 (live-only)

## Next actions

- fill in the next hypothesis

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/swim-jump-sneak/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/swim-jump-sneak/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/swim-jump-sneak/probes`
