# resume: dolphins-grace-12651

- version: `1.26.51.01`
- goal: 静态与逐tick实测闭合基岩版1.26.51.01海豚的恩惠(DolphinBoostSystem)触发条件、60t周期与±5盒、SwimSpeedMultiplier=2.0、与深海探索者/迅捷药水/水阻力的交互公式
- environment: -
- current round: 2
- last record: r01-004
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/dolphins-grace-12651/journal.jsonl`

## Confirmed (static closure required)

- r01-001: MCBE 1.26.51.01 的海豚的恩惠不使用 MobEffect，而是通过 DolphinBoostSystem(0x148b64570) 将 MovementEffectType::DolphinBoost=1 写入 MovementEffectsComponent(0x5c92321d)[1]，并由 swimSpeedModifier 写入 SwimSpeedMultiplierComponent(0x60fc5a9a)=2.0f。 [code; DolphinBoostSystem::registerSystems(0x148b64570) 调用链经 0x1468375f5->0x14682d3c0->0x146822800->0x146820662 注册在 WaterTravelSystem(0x1468261c6) 之前；PredictDolphinBoost(0x148b996f0/0x148b97d50->0x142fe8990) 写 {0,60,1}(0x10000003c)；swimSpeedModifier(0x148b9a790) 读 ADF[+7]&2 与 [1].mType!=-1 后写 2.0f(0x14e6022a8) 或 1.0f(0x14e5a7060)，且为 0x60fc5a9a 全部 15 个引用中唯一写入 !=1.0f 的函数。]
- r01-002: 触发扫描仅在 SWIMMING(ADF[+7]&2) 为真时递减 ScanForDolphinTimerComponent(0xb8b9c219)，每 60 ticks 重置一次并以玩家 AABBShapeComponent 三轴各扩展 ±5.0 格扫描 DolphinFlagComponent(0xcbd360fc)，不检查视线遮挡或海豚是否在水中，非泳姿时计时器冻结且不触发。 [constant; scan(0x148b94f30) 门控 (ADF[+7]&2)!=0 并在 timer<=1 时写 0x3c(60)；findDolphins(0x148b95710) 使用 .rdata ±5.0f(0x40a00000/0xc0a00000) 扩展 AABB 并查 DolphinFlagComponent(0xcbd360fc)；0xb8b9c219 全 PE 仅 6 处引用，scan 为唯一重置点。]
- r01-003: SwimSpeedMultiplier > 1.0f 时，WaterTravel_DragPerEntity(0x142dee630) 跳过向 0.546 的阻力混合保持 ρ=0.9/0.8，WaterTravelSystem(0x14c61f1d0/0x14207c030) 忽略 groundSpeed（疾跑/迅捷药水）与 OnGroundFlagComponent，按 a = 0.02*(0.7 + 0.3*min(L,3)/3)*SwimSpeedMultiplier 结算。 [code; 0x14c61f1d0 与 0x14207c030 在 SwimSpeedMultiplier>1.0f 分支跳过 OnGround(0.5x) 与 groundSpeed 插值，直接计算 0.02*(0.7+0.3*min(L,3)/3)*mult；WaterTravel_DragPerEntity(0x142dee630) 在 mult>1.0f 时跳过 0.546 插值。]
- r01-004: 同一装配函数 0x1468353c0 中 DolphinBoostSystem(0x146820662) 与 WaterTravelSystem(0x1468261c6) 早于 MovementEffectsTick(0x1468399f1->0x1490daf80)，后者在 prev<=1 时清槽；持续靠近海豚游动在 60t 周期边界零掉速无缝续期。 [code; 0x1468353c0 注册顺位 0x1468375f5(->0x148b64570) -> 0x1468261c6 -> 0x1468399f1；0x1490daf80 反汇编显示先写 duration=prev-1，并在 cmp edx,1; jle 0x1490daf90 时以 0x14e6ae400 清零 MovementEffect 槽。]

## Open / pending

- none

## Next actions

- closed

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/dolphins-grace-12651/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/dolphins-grace-12651/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/dolphins-grace-12651/probes`
