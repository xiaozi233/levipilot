# resume: lava-dim-fire-depth

- version: `1.26.51.01`
- goal: 静态定位液体扩散的维度 fast-lava（drop-off/tick delay）
- environment: -
- current round: 4
- last record: r03-010
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/lava-dim-fire-depth/journal.jsonl`

## Confirmed (static closure required)

- r01-002: Java 26.2 里维度只通过 FAST_LAVA 影响岩浆相关的移动：下界岩浆流的实体推力是 0.007，其他维度是 0.002333；同时下界岩浆扩散更快（tick delay 10 vs 30、drop-off 1 vs 2、slope 4 vs 2） [code; Entity.java:1663 `level.environmentAttributes().getDimensionValue(EnvironmentAttributes.FAST_LAVA) ? 0.007 : 0.0023333333333333335`；EnvironmentAttributes.FAST_LAVA 由 DimensionTypes 的 NETHER 设为 true（Overworld/End 默认 false）；LavaFluid.java 的 getSlopeFindDistance/getDropOff/getTickDelay 都读 FAST_LAVA。WATER_EVAPORATES 只阻止下界放水，不改速度属性。]
- r01-003: Java 26.2 的抗火（Fire Resistance）只免疫火焰伤害，不改变任何移动参数 [code; 全库搜索 FIRE_RESISTANCE：LivingEntity.java:1178 只在 `source.is(DamageTypeTags.IS_FIRE) && hasEffect(FIRE_RESISTANCE)` 的伤害减免里出现；travelInWater/travelInLava/aiStep 与属性聚合里没有该效果读取。]
- r01-004: Java 26.2 的岩浆深度改变竖直阻力：浅浆（液面在脚上 ≤0.4）用 0.8 阻力 + g/16 + g/4（b=-0.025，稳态 -0.125），深浆用 0.5 阻力 + g/4（b=-0.02，稳态 -0.04）；水平阻力两者都是 0.5 [code; LivingEntity.java#travelInLava：`isInShallowFluid(LAVA)`（getFluidHeight <= getFluidJumpThreshold()=0.4，Entity.java:3668）走 `multiply(0.5,0.8,0.5)` + getFluidFallingAdjustedMovement（-g/16）+ `-g/4`；否则 `scale(0.5)` + `-g/4`。getFluidFallingAdjustedMovement 在疾跑时跳过 -g/16。]
- r02-008: 基岩 1.26.51.01 的岩浆阻力/重力/流推三个环节都不按深度、维度或抗火分支：阻力恒定三轴 ×0.5，重力恒定 -0.02，流推系数只按 material 选 [code; 岩浆阻力 sub_142DF0B80：两条路径都在 0x142DF0F9A/FA8/FB6 与 0x142DF0DB1/DBF/DCD 对 vx/vy/vz 各乘 0.5，门只是 LavaTravelFlag/Navigation 组件的符号位，没有液面高度、维度或效果读取；岩浆重力 sub_142DE3260：结尾 0x142DE352F 写 -0.02f（0xBCA3D70A），只有 ±半格方块探针，没有维度/效果；流推 sub_1495E44E0：0x1495E5256 `if (material==5) k=0.014 else k=flt_14E7CA4C8[material==6]`（表 [1]=0.0035），没有维度参数。三个函数体内都没有 MobEffects/FIRE_RESISTANCE 读取。]
- r03-010: 基岩 1.26.51.01 的液体扩散有维度 fast-lava：下界 drop-off 1、tick delay 10，其他维度 drop-off 2、delay 30；它只改扩散距离/速度，不改实体推力 [code; sub_14345B370（液体扩散，8 处调用 @liquid_flow_level）：material==6 时读 BlockSource→[vtable+0x138]→+0x1A6 的维度字节；0x14345B40F/41B `drop-off = 2 - flag`，0x14345B46A/47C `tick delay = flag ? 10 : 30`。实体推力 sub_1495E44E0 的 k 只按 material 选，不读这个字节。]

## Open / pending

- r01-005: 基岩 1.26.51.01 没有浅浆/深浆分支：脚在液面下 0.55 格与 3 格都按 0.5 阻力、-0.02 重力下沉（稳态 -0.04） (live-only)
- r01-006: 基岩 1.26.51.01 的抗火效果不改变岩浆中的移动：有无抗火的 vy 序列完全相同 (live-only)
- r01-007: 基岩 1.26.51.01 的维度不改变岩浆对实体的推力：下界与主世界流动岩浆里的漂移稳态都是 0.007；但下界岩浆扩散更快（80 tick 后前沿 +7 vs +3） (live-only)

## Next actions

- fill in the next hypothesis

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/lava-dim-fire-depth/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/lava-dim-fire-depth/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/lava-dim-fire-depth/probes`
