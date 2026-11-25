# resume: honey-block-12651

- version: `1.26.51.01`
- goal: 第3轮：撤回r02-015水中include误读（0x1463c51d8..0x1463c5220为entt::exclude_t），封闭静水池与fall_clear着陆/五拓扑就地重建复测收口
- environment: -
- current round: 2
- last record: r02-022
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/honey-block-12651/journal.jsonl`

## Confirmed (static closure required)

- r01-004: 站上蜂蜜顶面时 onStandOn 以 k=0.4+0.2|mPosDelta.y| 缩放水平 mPosDelta，ADF 的 Sneaking 位跳过该乘数；但 0.8 滑度仍降低地面加速度，所以潜行只能部分缓解而不能免疫。 [code; HoneyBlock::onStandOn 0x1466ba420..0x1466ba5b6：取 ADFComponent 0xc67426f3 后 test dil,2 命中即返回；否则取 StateVector 0x1b5d5238，mpd.y<0.1f(0x14e6ae460) 时 k=0.4(0x14e602290)+0.2(0x14e6ae454)*|mpd.y|(掩码 0x14e5c6380)，乘 mpd.x(+0x18)/mpd.z(+0x20)；滑度 +0x15C/+0x178=0.8f 见工厂 0x14c6e7857。]（见 r02-010 勘误）
- r01-005: EntityInsideSystem 先于 InsideHoneyBlockSystem::fireEventsSystem 注册并被调度：前者把命中的蜂蜜块 (BlockPos, Block*) 写入 InsideBlockWithPosAndBlockComponent<HoneyBlockFlag>(0xc83bbc35) 并在同拍消费执行 -0.12/0.4，后者读取同一组件触发滑落音效/粒子。 [code; 同一初始化函数 0x1468353c0..0x14683ab80：0x146838546 注册 EntityInsideSystem（对象 0x14e98a910，tick 0x1463c0410/0x1463c0f90），0x14683913a 注册 InsideHoneyBlockSystem::fireEventsSystem（vt 0x14eb40470，tick 0x14c600670/0x14c600df0）；0xc83bbc35 条目 24 字节（BlockPos + Block* 于 +0x10），追加点 0x1463bfe75；fireEvents 在 0x14c600950 调 0x1466ba110(triggerSlidingDownEffects)，条件同 0.9375/0.43125。]
- r02-011: 贴墙滑落的 0.4 水平乘数与 -0.12 竖直钳位对 0xc83bbc35 的每个条目无条件执行，不受 onGround/mpd.y 符号/方块高度/水平距门控；几何门只负责清零 FallDistanceComponent 与播放滑落效果，且其竖直条件是 mPos.y<=block.y+0.9375（等价 feet<=block.y-0.6825）、水平是两轴 '>' 的 OR。 [code; 0x1463af2e4..0x1463af30f 与 0x1463c7575..0x1463c75a0；0x1463af2d0 清零 r9(FallDistanceComponent 0xce6b34f6)；0x1463af31f 读 StateVector.mPos.y；0x1463af359/0x1463af380 的 ja + OR；0x14c600950 以同一条件调 0x1466ba110 播效果。]
- r02-012: 0.4 水平乘数按同拍命中的蜂蜜块逐块连乘（0.4^n）；单块/垂直2/水平2/内角2/接缝+双层 分别进入 1/2/2/2/4 个条目，干净自由滑行的相邻拍比值为 0.91*0.4^n。 [code; 0x1463af270 循环体在 0x1463af303/0x1463af30f 对 mpd.x/mpd.z 各乘一次 0.4，循环边界为 0xc83bbc35 的 begin/end，无 min/提前退出；0xc83bbc35 条目 24 字节（BlockPos+Block*）由 0x1463bfdf8 无条件追加。]
- r02-013: 蜂蜜块起跳削弱 0.6x 来自 BlockLegacy+0x130 的 bit30(PreventsJumping)：命中时 (0.42+JumpBoost) 整体乘 0.6f 写入 mPosDelta.y；蜂蜜块运行期该位为 1，黏液/石头为 0，故两者起跳不削弱。 [code; JumpFromGroundSystem worker 0x1495e9200..0x1495e9515：0x1495e92b0 读 +0x130、0x1495e92b7 test 0x40000000，未命中再查 y-1（0x1495e9304 test byte [rax+0x133],0x40）；命中 0x1495e9348 取 0.6f(0x14e6ae498)，0x1495e9367 取 0.42f，0x1495e93f2 mulss 后写 mpd.y。]
- r02-014: 蜂蜜块摔落减伤是把摔落距离乘 0.5 再套普通公式（伤害=ceil(0.5h-3)），不是 JE 的最终伤害×0.2；持续贴墙滑落每 tick 清零摔落距离，因此着陆伤害为 0。 [code; 工厂 0x14c6e7872 注册的回调 0x1466b9f10(HoneyBlock::onFallOn)：0x1466b9ff1 取 [rsi+0x28]，0x1466b9ff6 mulss 0.5f(0x14e5c6370)，0x1466b9ffe 伤害乘数 1.0f(0x14e5a7060)，0x1466ba00c call 0x1481cc310，后者 0x1481cc43c call 0x141a03090(Actor::causeFallDamage)。]
- r02-015: WasInWaterFlagComponent 不是蜂蜜 inside 查询的排除项而是 include：水中贴墙仍受 0.4 水平缩放，但水中下沉速度低于 0.12 所以竖直钳位观察不到。 [code; 0x1463c4db0 在 0x1463c4e37..0x1463c4e62 把 WasInWater(0x78e89f39) 作为视图构造 0x1428a7cf0 的第5个参数；0x14c600270 的查询表把 OnGround(0xc29078a0) 与 WasInWater 放在同一 8 字节条目；实测有水仍处理，证明是 include。]（见 r02-021 勘误）
- r02-016: 潜行贴墙仍被钳到 -0.12；创造模式未飞行时地面/下落与生存一致；创造飞行只免竖直钳位（下降可达 -0.34），水平仍按 0.4^n 缩放（贴墙 0.050171 vs 开阔 0.374797）。 [code; 滑落乘算与 onStandOn 的 Sneaking 分支互不影响（0x1463af2e4 循环不在 onStandOn 内）；飞行仅覆写竖直位移、水平仍走 EntityInsideSystem 的 0x4 乘算；竖直覆写的具体系统未定位，标 [推断]。]
- r02-017: EntityInsideSystem 的 inside 判定盒覆盖到整格单元边界：AABB 与方块整格单元有正重叠即进入组件（贴面 0.0625 生效），不是缩水碰撞盒；因此 n 可按整格单元精确计数。 [code; 逐块回调 0x1463bf8d0 的蜂蜜分支 0x1463bfc1e→0x1463bfdf8 只做 get-or-add 与追加，无相交判断；相交由调用者体素遍历保证，判定面由实测钉在单元边界。]
- r02-018: 第 1 轮把起跳 0.6x 留作 live-only；第 2 轮已用静态（BlockLegacy+0x130 bit30 → 0.6f 乘 (0.42+JumpBoost)）+ 实机（蜂蜜 0.514 vs 石/黏液 1.252197）闭合，撤回 live-only 标记。 [code; JumpFromGroundSystem worker 0x1495e9200..0x1495e9515：0x1495e92b7 test 0x40000000；命中 0x1495e9348 取 0.6f；0x1495e93f2 mulss 后写 mpd.y；运行期 BlockType+0x130 蜂蜜 0x40020000、石头 0x40000、黏液 0x60000。]
- r02-019: 第 1 轮把摔落 ×0.5 留作 live-only；第 2 轮已用静态（onFallOn 把 fallDistance×0.5f 传给 causeFallDamage）+ 实机（10/15/20 格蜂蜜 2/5/7、石面 7/12/16）闭合，撤回 live-only 标记。 [code; 0x1466b9f10(HoneyBlock::onFallOn)：0x1466b9ff1 取 [rsi+0x28]，0x1466b9ff6 mulss 0.5f(0x14e5c6370)，0x1466b9ffe 伤害乘数 1.0f，0x1466ba00c call 0x1481cc310 → 0x1481cc43c call 0x141a03090。]
- r02-022: WasInWaterFlagComponent(0x78e89f39)是蜂蜜EntityInsideSystem消费视图的entt::exclude_t排除项，水中实体完全跳过0.4/-0.12处理；tall_wall连续滑落至end_y=-60.0着陆伤害仍为0，五拓扑就地重建(z=608..619)均通过。 [code; 0x1463c4db0(0x1463c4e37..0x1463c4e62)将0x78e89f39传入0x1428a7cf0(+0x20)；0x1463c51d8..0x1463c5220于0x1463c51f8/0x1463c520a/0x1463c521a仅在不含WasInWater时进入0x1463c4fe6调用0x1463af270，命中则jmp 0x1463c5090跳过。]

## Open / pending

- none

## Retracted / errata

- r02-009 (erratum of r01-002): 第1轮把 0x1463af270 的几何判断当成 0.4/-0.12 的门控，并写成 '竖直 <= 且水平 && 且按 feet_y'；实际 0.4 与 -0.12 对每个组件条目无条件执行，几何门只清零 FallDistanceComponent 与播滑落效果，比较的是 mPos.y(=feet+1.6200101) 且水平为 '>' 的 OR。 [refuted] — 原命题: 贴墙滑落把 StateVectorComponent 的 mPosDelta.y 按 max(dy,-0.12) 钳到 -0.12（不改速度、不是重力前叠加），水平 mPosDelta 每块乘 0.4；触发几何门用 block.y+0.9375 与 0.5*AABBShape.mBBDim.x+0.43125，贴面即算 inside。
- r02-010 (erratum of r01-004): 第1轮称黏液块潜行可以完全跳过顶面粘滞而蜂蜜块做不到；实际两者共享 0.8 滑度与 0.4+0.2|dy| 的 onStandOn 乘数，蜂蜜/黏液的走路与潜行走速度逐位相同。 [refuted] — 原命题: 站上蜂蜜顶面时 onStandOn 以 k=0.4+0.2|mPosDelta.y| 缩放水平 mPosDelta，ADF 的 Sneaking 位跳过该乘数；但 0.8 滑度仍降低地面加速度，所以潜行只能部分缓解而不能免疫。
- r02-018 (erratum of r01-006): 第 1 轮把起跳 0.6x 留作 live-only；第 2 轮已用静态（BlockLegacy+0x130 bit30 → 0.6f 乘 (0.42+JumpBoost)）+ 实机（蜂蜜 0.514 vs 石/黏液 1.252197）闭合，撤回 live-only 标记。 [confirmed] — 原命题: 在蜂蜜块顶面起跳的冲量由 0.42 降至 0.252（0.6×），升幅 1.252197→0.514 格；脚底中心移出台面后恢复全力起跳。
- r02-019 (erratum of r01-007): 第 1 轮把摔落 ×0.5 留作 live-only；第 2 轮已用静态（onFallOn 把 fallDistance×0.5f 传给 causeFallDamage）+ 实机（10/15/20 格蜂蜜 2/5/7、石面 7/12/16）闭合，撤回 live-only 标记。 [confirmed] — 原命题: 蜂蜜块的摔落减伤是把摔落距离乘 0.5 再套普通公式（伤害=ceil(0.5h-3)），不是 JE 的最终伤害×0.2；从墙顶下方起滑到地面不产生摔落伤害。
- r02-020 (erratum of r01-008): 第 1 轮称创造飞行完全免疫蜂蜜效果；实际只免竖直 -0.12 钳位（下降可达 -0.34），水平仍按 0.4^n 缩放（贴墙 0.050171 vs 开阔 0.374797）。 [refuted] — 原命题: 创造模式双击飞行时，贴墙下降不受 -0.12 滑落钳位；站立（未飞行）时与生存一致。
- r02-021 (erratum of r02-015): 第2轮称WasInWaterFlagComponent为include且水中贴墙仍乘0.4；实际0x1463c51d8..0x1463c5220在检测到WasInWater时直接跳过，第2轮0.0136系无围墙水柱逆流造成，撤回r02-015。 [refuted] — 原命题: WasInWaterFlagComponent 不是蜂蜜 inside 查询的排除项而是 include：水中贴墙仍受 0.4 水平缩放，但水中下沉速度低于 0.12 所以竖直钳位观察不到。

## Next actions

- closed

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/honey-block-12651/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/honey-block-12651/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/honey-block-12651/probes`
