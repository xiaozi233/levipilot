# resume: climb-blocks-12651

- version: `1.26.51.01`
- goal: Prove Bedrock 1.26.51.01 climbable block mechanics (ladders, vines, scaffolding, powder snow + leather boots, trapdoors): detection geometry, vertical/horizontal formulas, tick order, and JE differences
- environment: -
- current round: 2
- last record: r01-005
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/climb-blocks-12651/journal.jsonl`

## Confirmed (static closure required)

- r01-001: 全二进制共且仅有 7 种方块（ladder、vine、weeping_vines、twisting_vines、cave_vines、cave_vines_body_with_berries、cave_vines_head_with_berries）同时设置 BlockProperty::Climbable(bit 33) 与 CanHaltWhenClimbing(bit 34)；ScaffoldingBlock 仅设 Scaffolding(bit 25)|bit17，TrapDoorBlock 不设 Climbable，开活板门在梯子上方不可攀爬。 [constant; VanillaBlockTypeGroups::registerBlocks(0x14c665560..0x14c80476e) 中恰好 7 处执行 or byte ptr [reg+0x134], 6（0x14c67c27a, 0x14c68dd6a, 0x14c6eb5bb, 0x14c6fa04d, 0x14c703555, 0x14c703a15, 0x14c703ee5）；ScaffoldingBlock(0x1481ad550) 于 0x1481ad609 写 0x2020000；TrapDoorBlock 无 Climbable 置位。]
- r01-002: isOnLadder() 与 BlockClimberSystem 采用截然不同的检测几何：前者仅采样单格整数坐标 (floor(mPos.x), floor(AABB.min.y), floor(mPos.z))，后者扫描整个水平 AABB 足迹 [floor(min.x)..floor(max.x)] x [floor(min.z)..floor(max.z)]（脚底与脚下一格）写入 InScaffolding(69)/OverScaffolding(70)/InAscendableBlock(99)/OverDescendableBlock(100)。 [code; DefaultMoveSystems(0x148b61af0)、AutoClimbSystem(0x1482a7c30/0x1482a7e40)、JumpFromGroundSystem(0x1495ea890)、LadderResetFallDamageSystem(0x14c617b20)、ClimbingLadderBlockSystem(0x142e12b10) 与 LavaMoveSystem(0x14c61a3a0) 均对单点 mPos.x/z 与 AABB.min.y 取 floor；BlockClimberSystem(0x14489e590..0x14489e9cd 及其辅助 0x14489e9d0/0x14489eca0) 遍历整个 AABB 水平范围。]
- r01-003: 梯子与 4 种藤蔓在 MCBE 1.26.51.01 的运动学特征：被动下滑为 -0.20 b/t（非 Java -0.15）、全无 ±0.15 水平速度钳位、按潜行仅在 mPosDelta.y < 0 且含 CanHaltWhenClimbing 时清零、Space(+0.20) 与 AutoClimb(+0.20 + AutoClimbTravelFlagComponent 0x777a5678 跳过重力/阻尼) 存在松键 1 拍差异。 [code; DefaultMoveSystems(0x148b61af0) 在 0x148b61cd6 用 0xbe4ccccd(-0.2f) 钳位下限，0x148b61d5d..0x148b61de9 检查 SneakDown && mPosDelta.y<0 && ([rax+0x134]&4)，且 DefaultMoveSystems 与 MobMovementFriction(0x142df9da0/0x142de2010) 均无水平钳位；AutoClimbSystem(0x1482a7c30) 写 0x3e4ccccd(+0.2f) 并挂 0x777a5678，MobGroundGravity(0x142e013c0)/MobAirGravity(0x142e02ec0)/DragGround(0x142df1800)/DragAir(0x142df3850) 均排除 0x777a5678。]
- r01-004: 脚手架在有支撑时按 Space 以 +0.15 b/t 上升、按潜行以 -0.15 b/t 下降（下降态跳过重力但保留 0.98 阻尼，松键首拍为 -0.147）；悬空时顶面不置 OverDescendableBlock(100) 无法穿底，内部仍因 InScaffolding(69) 允许按 Space 上爬；且 BlockClimberSystem 对 69/70/99/100 任一位为真即清零 FallDistanceComponent，导致无潜行落入脚手架顶部同样 0 摔落伤害。 [code; ScaffoldingBlock::getCollisionShape(0x1481ae570) 校验 stability!=7 && !canDescendThroughBlock && min.y>=y+1-1e-6；ScaffoldingActionSystem(0x147cdcd20) 检查 OverDescendableBlock(100) 写 -0.15f(0xbe19999a)；JumpFromGroundSystem(0x1495ea9a8) 检查 (InScaffolding||InAscendableBlock)&&!DescendThroughBlock 写 +0.15f(0x3e19999a)；MobMovementGravity(0x142de3566) 跳过 (69||70)&&71 而 Drag(0x142de1050) 不跳过；BlockClimberSystem(0x14489e938..0x14489e948, 0xdec6d40f=BlockClimberComponent) 测试 0x1800000060 后写 FallDistanceComponent(0xce6b34f6)=0。]
- r01-005: 穿皮革靴进入细雪同时触发 BlockClimberSystem 与 isOnLadder()，叠加 BlockMovementSlowdownMultiplierSystem::applySlowdownOnMove(0x1448a98c0) 的 Y 向 1.5x 缩放（并清零 StateVector.mPosDelta），实测 Space +0.225 b/t、潜行 -0.225 b/t（首拍 -0.15）、推墙 AutoClimb +0.300 b/t，光脚则恒速 -0.1176 b/t 下沉；且细雪跌落均受 onFallOn 空实现与 FallDistance 清零保护为 0 伤害。 [code; JumpFromGroundSystem(0x1495ea9a8) Priority 1 命中 InAscendableBlock 写 +0.15f，AutoClimbSystem(0x1482a7c30) 命中 HorizontalCollision&&isOnLadder() 写 +0.20f，ScaffoldingActionSystem(0x147cdcd20) 写 -0.15f；BlockMovementSlowdownMultiplierSystem::applySlowdownOnMove(0x1448a98c0, 0x1448a9eeb..0x1448a9f5e) 将 MoveRequestComponent(0x4c8f7288) 乘以 slowdown(0xf6091f58) 并清零 mPosDelta；adjustFallDistance(0x1448a8d30) 与 PowderSnowBlock::onFallOn(MCFOLD) 免除摔落伤害。1.5f 写入源与 2.5f 跌落判定 VA 暂记 unresolved。]

## Open / pending

- none

## Next actions

- closed

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/climb-blocks-12651/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/climb-blocks-12651/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/climb-blocks-12651/probes`
