# resume: liquid-blip

- version: `1.26.51.01`
- goal: 补充无跳跃对照：基岩弹跳是否要求跳跃输入
- environment: -
- current round: 3
- last record: r02-006
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-blip/journal.jsonl`

## Confirmed (static closure required)

- r01-002: Java 26.2 保留了 1.8.9 的液体退出弹跳：travelInWater/travelInLava 末尾调用 jumpOutOfFluid，在 horizontalCollision 且偏移 0.6 的盒子既无碰撞也无液体时把 vy 设为 0.3 [code; fabric-loom 26.2 sources：net/minecraft/world/entity/LivingEntity.java#travelInWater 与 #travelInLava 末尾都调用 jumpOutOfFluid(oldY)；#jumpOutOfFluid：`Vec3 m = getDeltaMovement(); if (horizontalCollision && isFree(m.x, m.y + 0.6F - getY() + oldY, m.z)) setDeltaMovement(m.x, 0.3F, m.z);`；Entity#print：isFree(AABB) = `level.noCollision(this, box) && !level.containsAnyLiquid(box)`。调用点在阻力/下落修正之后，0.3 覆盖本 tick 的 vy。]

## Open / pending

- r01-003: 基岩 1.26.51.01 存在同值的液体退出弹跳：贴墙、水面附近、按住跳跃时 vy 出现 0.34 → 0.3；不贴墙/深水/不按跳跃时不出现 (live-only)
- r01-004: 弹跳首 tick 的 0.34 是 0.3（liquid blip）+ 0.04（水跳边缘），之后的水跳边缘消失、只剩 0.3 (inconclusive)
- r02-006: 基岩 1.26.51.01 的液体退出弹跳挂在跳跃输入上：不按跳跃时，即使玩家处于水物理、水平碰撞成立、抬 0.6 的探针盒已经离开水面，也不会出现 0.3 (live-only)

## Next actions

- fill in the next hypothesis

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-blip/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-blip/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-blip/probes`
