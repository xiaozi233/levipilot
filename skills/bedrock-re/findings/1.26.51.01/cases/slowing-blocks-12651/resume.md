# resume: slowing-blocks-12651

- version: `1.26.51.01`
- goal: 静态与逐tick实测闭合基岩版1.26.51.01减速方块(蜘蛛网/甜浆果丛/细雪与冰冻减速/灵魂沙)、0.998^3效果盒、多方块重叠min规则及细雪2.5格下沉碰撞箱
- environment: -
- current round: 1
- last record: r01-005
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/slowing-blocks-12651/journal.jsonl`

## Confirmed (static closure required)

- r01-001: 蜘蛛网(0.25,0.05,0.25)、细雪(0.90,1.50,0.90)与甜浆果丛(0.80,0.75,0.80)共享 BlockMovementSlowdownMultiplierComponent(0xf6091f58)：EntityInsideSystem 将实体 AABB 各向内缩 0.001f（0.998^3 效果盒）收集方块，多方块同时命中时按分量 minss 取小值；immunePlayer 仅当 MovementAbilitiesComponent(0x6de78dc5)+4 同时置位 Flying(bit 0) 与 Instabuild(bit 2) 即 (mFlagValues&5)==5 时免疫（故地面创造玩家仍受减速）；applySlowdownOnMove(0x1448a98c0) 乘法缩放 MoveRequest 并每拍清零 mPosDelta 与 0xf6091f58，adjustFallDistance(0x1448a8d30) 清零 FallDistanceComponent。 [code; 主注册 0x1468353c0 依次登记 EntityInsideSystem(0x146838546 -> 0x1463ae5a0/+0.001f 0x14e69fdc0/-0.001f 0x14e7ca488)、SweetBerryBushMovementSlowdownSystem(0x1468386a2 -> 0x14c606140)、InsideGenericBlockSystem 细雪(0x1463ad860..0x1463ada52 -> 0x1463c7650)与蜘蛛网(0x1463ada60..0x1463adc52 -> 0x1463c7850)、immunePlayer(0x14683898d -> 0x1448907a0, 与 0x1428af4a0+0x14e839cdc 映射)、adjustFallDistance(0x146838ae7 -> 0x1448a8d30) 及 applySlowdownOnMove(0x14681ad26 -> 0x1448a98c0)。]
- r01-002: 甜浆果丛对全部生长阶段 growth=0..3 都写入 InsideSlowingSweetBerryBushBlockComponent(0x03abf17e) 以施加 (0.80,0.75,0.80) 减速与摔落免伤，仅 growth > 0 时触发移动荆棘伤害。 [code; 0x1495cca80..0x1495cce58 开头无条件写入 InsideBlockComponent(0xc1c8fb69) 与 InsideSlowingSweetBerryBushBlockComponent(0x03abf17e)，随后于 0x1495ccb78/0x1495ccbac 检查 growth > 0 方才调用伤害路径 0x14244a700；SweetBerryBushMovementSlowdownSystem(0x14c606140) 读 0x03abf17e 写 (0.8f,0.75f,0.8f)。]
- r01-003: PowderSnowBlock::getCollisionShape 在 feetPos.y < block.max.y - eps 或潜行时返回空盒；当 fallingHeight > 2.5f 时返回高度 0.9 的降顶盒使实体（含穿皮革靴玩家）下陷 0.1 格继而穿顶落入；fallingHeight <= 2.5f 时对 FallingBlock(0x42) 或 canStandOnPowderSnow(0xb639c3b7||0x4eac501f) 返回完整立方体。 [code; PowderSnowBlock::getCollisionShape(0x149952000..0x149952292, vt 0x14ea77ff0) 于 0x1499521a2 比较 getFallingHeight() > 2.5f(0x14e76b088)，命中返回 [0,0,0..1,0.9,1](0x151db9d7c 初始化于 0x142deb0e0)；否则经 0x1412e2100/0x148245a10 判定可站立返回 [0,0,0..1,1,1]。]
- r01-004: FreezingSystem::tickSystem 使用相同 +0.001f/-0.001f 缩窄盒，以 +1/140 per tick 累加 FreezingComponent(0x47e62980) 至 1.0f、离雪 -2/140 per tick 至 0.0f，满冻时每 40 刻（[rdi+0x198]%40==0）扣 1.0 HP，并仅通过 SharedAttributes::MOVEMENT_SPEED 的 "Freeze effect" Addition(-0.05*p) 减缓地面速度至多 50%，不修改 AirSpeedComponent。 [code; FreezingSystem::tickSystem(Reg 0x1467f32bb -> 0x14665efa0..0x14665f8af) 在 0x14665f214..0x14665f28f 使用 +0.001f/-0.001f；0x14e942878(+1/140)、0x14e9962cc(-2/140)；0x142087210 向 SharedAttributes::MOVEMENT_SPEED(0x151dc5488) 施用 -0.05f*p(0x14e7d1af8, Op=0 Addition)，经 AttributeInstance::calculateValue(0x142859e10) Stage 0 计算为 0.10-0.05*p；0x14665f55b..0x14665f57e 校验 %40==0 扣 1.0f(0x2c8f0870 则 5.0f)。]
- r01-005: 灵魂沙地面检测仅采样 (floor(mPos.x), floor(AABB.min.y - 0.1f), floor(mPos.z))（与滑度同偏移，且受 SoulSpeedEnchantFlagComponent 0x6da32dd7 跳过），命中时将基础摩擦 0.6f 乘以 1.225f 得 m=0.735*0.91=0.66885f，使地面加速度按 (0.546/m)^3=(1/1.225)^3≈0.543991x 衰减，而 ApplyFriction 阻力与空中加速度均保持不变。 [code; 0x14666cee0..0x14666d222、0x14666f530 与 0x1466588d0 在 on_ground 且无 0x6da32dd7 时对 AABB.min.y-0.1f 取 floor 匹配 minecraft:soul_sand(0x93e2bb1cb7f99fc6)，将 0.6f 乘 1.225f(0x14e9962c8) 并按 (0.546f/m)^3 写入 MobTravelComponent(0x39cf8d64)+0x0c；ApplyFriction(0x142de2010) 不使用 1.225f。]

## Open / pending

- none

## Next actions

- closed

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/slowing-blocks-12651/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/slowing-blocks-12651/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/slowing-blocks-12651/probes`
