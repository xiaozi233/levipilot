# resume: liquid-effect-box-depth

- version: `1.26.51.01`
- goal: 确认液体判定盒是否与流体液位/液面高度有关，纠正浅水无物理的旧结论
- environment: -
- current round: 1
- last record: r01-003
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-effect-box-depth/journal.jsonl`

## Confirmed (static closure required)

- r01-001: 液体判定盒由玩家 AABB 内缩 0.1(X/Z)/0.4(Y) 得到，以 6 个 float + 计数 6 交给 a1->vtable+0x40 求交；函数体内不读 liquid_depth [code; 0x142DEB4B0 反编译：min+=0.1/0.4、max-=0.1/0.4 组装 v71[6]，调用 (*(*a1+64))(a1, v71, 6)；SubBBs 逐个复用同一虚函数；全程无 liquid_depth 读取]

## Open / pending

- r01-002: 液体判定与流体液位无关：液位 0/3/7 站立 in_water 全为真，5 tick 跳跃顶点 0.641/0.608/0.641 vs 干地 1.249；6 tick 前向速度 水 0.0723 vs 干地 0.2101 (live-only)

## Next actions

- fill in the next hypothesis

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-effect-box-depth/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-effect-box-depth/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-effect-box-depth/probes`
