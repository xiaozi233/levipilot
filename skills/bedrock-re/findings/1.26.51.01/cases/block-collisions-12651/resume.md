# resume: block-collisions-12651

- version: `1.26.51.01`
- goal: 自动导出 1.26.51.01 全游戏方块及典型实体精确 float32 碰撞箱，并与 MCBE_Parkour_wiki/Pages/blocks_collisions.md 逐项校对及静态复核关键差异
- environment: -
- current round: 2
- last record: r01-006
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/block-collisions-12651/journal.jsonl`

## Confirmed (static closure required)

- r01-002: TrapDoorBlock 与 DoorBlock 的 0.1825 厚度是 1.26.51.01 真实引擎硬编码常量而非 Wiki 笔误；TrapDoorBlock::$getOutline 另对非铰链面内缩 0.0005。 [constant; TrapDoorBlock::_getShape(0x1440320c0) 与 DoorBlock::$getVisualShape(0x1497896c0) 读取 .rdata 0x14e8b333c=0.1825f(0x3e3ae148) 与 0x14e8b3338=0.8175f(0x3f5147ae)；TrapDoorBlock::$getOutline(0x144032570) 传 r8b=1 加载 .rdata 0x14e8425d8=0.0005f(0x3a03126f)。]
- r01-003: ChestBlock 单箱缩进为每边 0.025（0.95×0.95×0.95），Wiki 注释 -0.25 属笔误；1.26.51.01 ChestBlock 虚表未重写碰撞/选框函数。 [code; ChestBlock ctor(0x148e0ab70) 将 mVisualShape 写为 .rdata 0x14ea40130(0.025f,0,0.025f,0.975f) 与 0x3f79999a3f733334(0.95f,0.975f)，ChestBlock::$vftable(0x14ea426f0) slot[4/6/7/8/9/10] 均直指 BlockType 基类且全 PE 无第二处 0.975f。]
- r01-004: 全游戏 BlockType::getBounciness(slot[125]) 仅 4 个子类重写：slime=1.0、bed=0.75(straw_bed=0.0)、shelf_mushroom=0.75、honey_block=-1.0。 [code; 扫描 .rdata 262 个 BlockType 虚表 slot[125]（基类 0x140091a30 返回 0.0f），仅见 0x1406db5f0(+1.0f)、0x1409276f0(-1.0f)、0x142da3480([rcx+0x380]=0.75f) 与 0x144adb980(0.75f/0.0f)。]
- r01-005: WallBlock 2 对侧连接宽度 0.5 vs 0.375 由 wall_post_bit 决定，非“上方是否有墙”；wall_connection_type=tall 仅使选框顶部从 0.8125 升至 0.9999。 [constant; WallBlock 函数 0x146048d00 在 0x146048d25/0x146048d5c 依据条件读取 0.8125f(0x14e8b3388) 与 0.9999f(0x3f7ff972)。]

## Open / pending

- r01-001: 全游戏 1477 种 BlockType / 22091 个 Block 状态导出零故障，非整格碰撞箱共 537 种类型、233 种唯一几何签名，实体上下文敏感方块仅 minecraft:powder_snow 与 minecraft:scaffolding 两种。 (live-only)
- r01-006: 补全 Wiki 空缺与过时项：Hopper 为 7 盒复合碰撞，Happy Ghast AABB 为 4×4×4，Stairs(inner) 已含第 3 盒，End Portal Frame 装眼增顶盒，Piston Head 分 3 盒，Trial Spawner 碰撞已回整格仅选框留 0.9998。 (live-only)

## Next actions

- closed

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/block-collisions-12651/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/block-collisions-12651/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/block-collisions-12651/probes`
