# resume: liquid-falling-push

- version: `1.26.51.01`
- goal: 补充 2x2 水体邻接对照：确认水材质不算实心面
- environment: -
- current round: 4
- last record: r03-010
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-falling-push/journal.jsonl`

## Confirmed (static closure required)

- r01-003: 基岩 1.26.51.01 的下落水紧贴实心面时，LiquidPhysicsSystem 会给玩家一个恒定的竖直向下推力 0.014 格/tick（v' = 0.8v - 0.019，稳态 -0.095），静水只有 0.005 重力（稳态 -0.025） [code; per-block flow vector @0x14345DE50：自身 liquid_depth>=8 且 4 邻中某邻块或其上方一格的 material 实心标志 byte(+5)!=0 时，先归一化再 y += -6.0（常量 @0x14E76B07C = 0xC0C00000，写点 @0x14345E213），最后再归一化；caller @0x1495E44E0 对每个 flow source 累加、|Σ|>=1e-4 归一化、k=0.014（material==5，@0x14E7CA4D0）后加到 actor+0x18/0x1C/0x20（写点 @0x1495E5281/528B/5295）。门同 Java 26.2 FlowingFluid#getFlow 的 FALLING + isSolidFace。]
- r01-004: (-6) 偏置的触发条件是“液体块自身 liquid_depth>=8（下落）+ 四个水平邻块中任一邻块或其上方一格是实心材料”，不是“有流动源就无条件加”，也不是“竖井里任意位置有墙” [code; @0x14345E0DC..0x14345E1BD：先读自身 level（@0x14345D010），>=8 才进入循环；每个方向取 neighbor，读 block->[0x68]->[0x28]->+5 字节（@0x14345E172），再取 neighbor+1y 同一字节（@0x14345E1A8），任一非 0 即 break 并落到 -6 偏置；4 个方向都不命中则跳过偏置。动态对照：3x3 竖井中心（1 格外才是墙）为 -0.005，同一竖井贴墙为 -0.019。]
- r01-005: Java 26.2 仍保留 1.12.2 的垂直水流 (0,-6,0) 机制：只在 FALLING 且邻块/邻块上方是实心面时执行，最终归一化为单位向量；玩家推力系数 0.014 [code; fabric-loom 26.2 sources (minecraft-common sources jar)：net/minecraft/world/level/material/FlowingFluid.java#getFlow 第 55-97 行——`if (fluidState.getValue(FALLING))` 内对四个水平方向检查 `isSolidFace(level, blockPos, direction) || isSolidFace(level, blockPos.above(), direction)`，命中即 `flow = flow.normalize().add(0.0, -6.0, 0.0); break;`，函数结尾 `return flow.normalize();`；isSolidFace 第 106-117 行（同流体 false、冰 false、否则 isFaceSturdy）。net/minecraft/world/entity/EntityFluidInteraction.java#applyCurrentTo（玩家取 accumulatedCurrent/count 平均，其余实体 normalize）；Entity.java WATER_FLOW_SCALE = 0.014。]
- r01-006: 旧笔记 docs/notes/re-fluid-12651.md §2.10 把 -6 偏置写成“有流动源就无条件加”是错的，应更正为“下落 + 实心面”门控 [code; 同一反编译：-6 位于 0x14345E1DC..0x14345E213，外层有 `if (v25 >= 8)`（v25 为自身 liquid_depth，0x14345E0DC 前读取），内层有 material 实心字节循环；两个条件同时满足才执行。旧笔记把 sub_14345DE50 与 sub_1495E44E0 的伪代码合并，误读成 caller 无条件加。]

## Open / pending

- r02-008: 首轮探针 r01-001 的 a/b 最小二乘拟合被单 tick 采样毛刺污染（walled b=-0.0333），改为 b=median(v_{n+1}-0.8v_n) 后各场景稳定在 -0.005 / -0.019，结论不受影响 (inconclusive)
- r03-010: 水材质本身不算“实心面”：2x2 瀑布里流体的水平邻块是另一根水柱时，竖直流推不触发（与 Java isSolidFace 的同流体排除一致） (live-only)

## Next actions

- fill in the next hypothesis

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-falling-push/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-falling-push/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-falling-push/probes`
