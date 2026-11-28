# resume: collisions-precision-12651

- version: `1.26.51.01`
- goal: Round 4 返工：补测跨 2^n 边界水平行走与垂直跳跃 AABB 漂移、修正 y=101 高度引述、静态闭合 Mob::pushActors/PushMode 4 路推挤公式并修复 S4 生存模式+非零偏移夹具、修正 S2 L1/L2 模长与拍数口径并清理账本重复项
- environment: -
- current round: 5
- last record: r04-055
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/collisions-precision-12651/journal.jsonl`

## Confirmed (static closure required)

- r02-033: AABB 精度三定律实测成立：(a) 重建量化——传送/姿态重建把盒落到 ULP(|x±0.3|) 网格，宽度实测 2^10=0.6000977、2^14=0.6015625、2^17=0.59375、2^20=0.5（理论 0.6 在 2^20 不可表示）；(b) 增量平移守恒——原点 31 拍与四个高坐标各 16 拍行走的逐拍 d_width/d_height 全为 0；(c) 站立下沉-顶回精确恒等——y=-60 与 y=101 各 150/42 拍 min_y 单值，12 连跳落地高度全部 1.7999992 [code; AABB 增量平移写回由 @actor_move_solve 0x1490283A0 逐轴加增量路径支撑（代码），重建量化由传送/姿态重建路径产生；round(k+δ)=k+round(δ) 守恒为 [推断]，行为逐位实证。]（见 r04-055 勘误）
- r02-034: 卡墙 0.1 挤出实测：浅嵌（1 列石墙）弹出方向 (+1,+1)/√2 对角自由空间，mPosDelta 残差 0.038608 起按 0.546 摩擦链衰减至 5e-05，与 0.1/√2 写入逐位吻合；深嵌（5×3×5 全封闭，出面 1.8~2.8 格）92 拍盒子逐位冻结，mPosDelta.x/z=0 仅保留 y=-0.0784 静止签名；mBits 卡死态 22(WasPenetrating+IsStuckInCollider+PushTowardsClosestSpace)、未穿透态 16，ADF byte13 全程 0x20（能力位出生即置）；玩家 mMinDepenetration=0.01 [code; @push_toward_closest_space 0x1431E2010：n̂×0.1f、±1 步自由空间搜索失败清零、仅 |mPosDelta[axis]|<|push| 写 +0x18/+0x20；@update_depenetration 状态机位；@configure_depenetration 表选择。实测数字逐位对质该静态模型。]
- r02-035: 实体推挤只作用于服务端 movement-tick 实体：重叠双牛在数拍内分离（pair_gap_seq 0.05→0.06→0.54→2.52，2-tick 轮询粒度，峰值单拍约 0.5~1.0 量级），分离完成即无持续力；本地玩家静止重叠牛 52 拍 mPosDelta 全零、行走玩家径直穿过站立牛（cow_x 246.5 前后不变）——客户端权威玩家不在推挤集内 [code; @push_actors_system 0x147CBE4D0 视图 {ActorOwner, ActorMovementTickNeeded, MobFlag, PushActorsRequest}：推挤以 movement-tick 请求为前提（代码），客户端权威玩家不在请求集；per-entity 推挤公式本身 unresolved（虚表 slot≈144 未定位），实测只定界峰值量级。]
- r03-036: AABB 精度三定律实测成立：(a) 重建量化——传送/姿态重建把盒落到 ULP(|x±0.3|) 网格，宽度实测 2^10=0.6000977、2^14=0.6015625、2^17=0.59375、2^20=0.5（理论 0.6 在 2^20 不可表示）；(b) 增量平移守恒——原点 31 拍与四个高坐标各 16 拍行走的逐拍 d_width/d_height 全为 0；(c) 站立下沉-顶回精确恒等——y=-60 与 y=101 各 150/42 拍 min_y 单值，12 连跳落地高度全部 1.7999992 [code; AABB 增量平移写回由 @actor_move_solve 0x1490283A0 逐轴加增量路径支撑（代码），重建量化由传送/姿态重建路径产生；round(k+δ)=k+round(δ) 守恒为 [推断]，行为逐位实证。]
- r03-037: 卡墙 0.1 挤出实测：浅嵌（1 列石墙）弹出方向 (+1,+1)/√2 对角自由空间，mPosDelta 残差 0.038608 起按 0.546 摩擦链衰减至 5e-05，与 0.1/√2 写入逐位吻合；深嵌（5×3×5 全封闭，出面 1.8~2.8 格）92 拍盒子逐位冻结，mPosDelta.x/z=0 仅保留 y=-0.0784 静止签名；mBits 卡死态 22(WasPenetrating+IsStuckInCollider+PushTowardsClosestSpace)、未穿透态 16，ADF byte13 全程 0x20（能力位出生即置）；玩家 mMinDepenetration=0.01 [code; @push_toward_closest_space 0x1431E2010：n̂×0.1f、±1 步自由空间搜索失败清零、仅 |mPosDelta[axis]|<|push| 写 +0x18/+0x20；@update_depenetration 状态机位；@configure_depenetration 表选择。实测数字逐位对质该静态模型。]
- r03-038: 实体推挤只作用于服务端 movement-tick 实体：重叠双牛在数拍内分离（pair_gap_seq 0.05→0.06→0.54→2.52，2-tick 轮询粒度，峰值单拍约 0.5~1.0 量级），分离完成即无持续力；本地玩家静止重叠牛 52 拍 mPosDelta 全零、行走玩家径直穿过站立牛（cow_x 246.5 前后不变）——客户端权威玩家不在推挤集内 [code; @push_actors_system 0x147CBE4D0 视图 {ActorOwner, ActorMovementTickNeeded, MobFlag, PushActorsRequest}：推挤以 movement-tick 请求为前提（代码），客户端权威玩家不在请求集；per-entity 推挤公式本身 unresolved（虚表 slot≈144 未定位），实测只定界峰值量级。]
- r04-050: 跨 2^n 边界的增量平移漂移实测成立：往返跨界行走（去 12 拍 + 回 12 拍）在 x=1024 边界产生 8 次 ±6.103515625e-05 的 d_width 跳变（宽度集合 3 值）、x=16384 边界 4 次 ±9.765625e-04（4 值）、x=1048576 边界 7 次跳变（宽度在 0.375~0.875 的 8 个值间摆动，跨界段每拍 ±0.0625 = min 用 ULP_below/max 用 ULP_above 各自舍入），往返后盒子 [1048574.75, 1048575.125] 宽度 0.375（较理论 0.6 缩 0.225）——首个非零漂移与 straddle 拍精确对应；同 binade 内行走（原点 31 拍）宽度仍逐位守恒 [code; @actor_move_solve 0x1490283A0 的增量平移写回路径（min/max 逐轴加解算增量）+ f32 binade 舍入机理：跨界时 ULP(max)=2×ULP(min) 两侧进位不同步。]
- r04-051: 跨 y=128 边界的连跳单调放大垂直碰撞箱：平台顶面 y=127（盒 [127.0,128.8] straddle 128）连续 15 跳落地高度 1.800018310546875 → 1.800445556640625，每跳步进恰为 +3.0517578125e-05 = ULP(128.x)，无一回退；min_y 每跳精确落回 127.0，max_y 在 128 区间网格累积——社区「特定 Y 高度连跳改碰撞箱」实测成立。对照：跨 y=64 边界（y=63 平台）15 跳高度恒 1.8000030517578125 单值；天花板对照（y=65 石层，mpd_y=+0.42 起跳 6 次）头部精确夹至 65.0、min_y 同步下压——天花板是整盒推出不缩高度；y=101 站立 120 拍高度实测 1.8000030517578125（更正 Round 1 文档误引 1.7999992——那是原点 y=-60 的值） [code; 落地「下沉-顶回」往返落在 @actor_move_solve 0x1490283A0 的增量平移与 @clip_collide 贴面路径上（代码）；y127 每跳 +1 ULP(128.x) 的偏置方向由 f32 binade 舍入机理（[推断]）解释，行为逐跳实证。]
- r04-052: S2 卡墙推挤模长修正：欧氏模长峰值实测 0.10000000461060017 = n̂ × 0.1f 精确（Round 1 的 0.14142136 是 L1 曼哈顿和 2×0.07071068 的口径错误）；浅嵌逐拍 mpd_x/z = 0.07071068 = 0.1/√2；深嵌 98 拍冻结（min_y ≡ -60.0、mpd_x/z ≡ {0.0}） [code; @push_toward_closest_space 0x1431E2010 的 push = n̂ × 0.1f 写入与 clamp 门（代码）；探针改为 math.hypot(mpd_x, mpd_z) 计算欧氏模长。]
- r04-053: 实体推挤静态闭合：PushActorsSystem 逐实体经 Actor 虚表 slot 144（+0x480）调 Mob::pushActors（SDK Mob.h:147，0x142086170）——0.2f 外扩收集候选、查 PushableByEntityComponent(0xE0F57B82)、骑乘链 walker(0x1419F2FC0) 后逐候选 0x1433DA1D0；分派器按 SharedTypes::PushMode 4 路（Default=1→0x1433DB540、LegacyBoat=2→0x1433DB040、LegacyMinecart=3→0x1433DB2D0、Ball=4→0x1433DAF50）；Default：d=sqrt(max(|dx|,|dz|))、d<mMinDistance(+0x48) 短路（同心重叠命中此分支——Round 1 同心夹具假阴性根源）、d−自身半宽>mMaxDistance(+0x4C) 短路、推力=min(1,1/d)×mStrengthMultiplier(+0x44)×(1−对方 pushThrough[actor+0x194])、∓方向分别乘 +0x54/+0x58 两倍率（+0x58 自推仅 canPush==0 时计算）；0x1433DB650 把向量累加进双方 StateVector.mPosDelta（Actor+0x218 指针字段 6/7/8），类型 88/83（船/矿车）走虚表 slot 138 专用路径，PushedByComponent(0xBE9C0F2C) 经 0x14253A5C0 挂到候选，音效按 preset+0x6C/+0x70/+0x78 门控 [code; dec_142086170.c（0.2f 外扩 + 0xE0F57B82 查询 + 逐候选 0x1433DA1D0）、dispatch.txt（d 公式/双距离门/4 路跳表 0x14E880500）、default.txt（min(1,1/d)×+0x44×(1−pushThrough)、±方向、+0x54/+0x58 倍率）、dec_1433db650.c（mPosDelta 累加 + 88/83 船/矿车分支 + PushedBy 挂载）；SDK Mob.h:147 virtual void pushActors()、PushMode.h 4 值枚举、v1_26_40 PushableByEntityPreset 字段名对齐。]
- r04-054: 生存模式 + 非零偏移实测（修正 Round 1 同心短路夹具）：A 生存玩家 dx=0.15 静止重叠牛——玩家 mPosDelta ≡ 0、位移 0，牛被自推 238.5→237.49（-1.01，分离后停推）；B 生存行走穿越牛位——玩家 244.52→250.67 自由穿越、牛 247.5 精确不动（12 次轮询，尾部 3 次 None 为如实边界）；C 创造对照同几何——玩家免疫、牛自推至 237.24，创造/生存无差异；D 双牛 dx=0.3 重叠——首 2-tick 采样间隔内 gap 0.3→1.56（每牛峰值约 0.6/tick）后稳定 1.57±0.01。结论：推挤只落在 movement-tick 实体一侧，玩家（生存/创造）免疫（写入门在玩家侧不放行 [推断]），牛以 canPush==0 的自推路径分离 [code; 0x1433DB540 的 canPush==0 分支（vec2 自推）+ 0x1433DB650 写入门（sub_1419F1ED0(…,256)/slot39 在玩家侧不放行）+ skipPush 骑乘短路；玩家免疫的虚函数语义未逐条命名，为 [推断] 边界。]
- r04-055: 勘误：r02-033/r03-036（AABB 三定律中「行走零漂移」「连跳证伪」两条）与 r02-035/r03-038（实体推挤 unresolved）被本轮 r04-049 的新测量与静态闭合取代；r02-034/r03-037 的 0.1 挤出主体结论保留但模长口径（0.14142136 为 L1 和）与拍数（92→98）以本轮为准 [code; 见本轮四条 confirmed claim 的静态闭环与 docs/notes/collisions-precision-12651.md §7 勘误表。]

## Open / pending

- r01-023: full-case (inconclusive)
- r01-026: full-case (inconclusive)
- r01-027: full-case (inconclusive)
- r01-029: full-case (inconclusive)
- r01-031: full-case (inconclusive)
- r04-040: full-case (inconclusive)
- r04-042: full-case (inconclusive)
- r04-044: full-case (inconclusive)
- r04-046: full-case (inconclusive)
- r04-048: full-case (inconclusive)

## Retracted / errata

- r04-055 (erratum of r02-033): 勘误：r02-033/r03-036（AABB 三定律中「行走零漂移」「连跳证伪」两条）与 r02-035/r03-038（实体推挤 unresolved）被本轮 r04-049 的新测量与静态闭合取代；r02-034/r03-037 的 0.1 挤出主体结论保留但模长口径（0.14142136 为 L1 和）与拍数（92→98）以本轮为准 [confirmed] — 原命题: AABB 精度三定律实测成立：(a) 重建量化——传送/姿态重建把盒落到 ULP(|x±0.3|) 网格，宽度实测 2^10=0.6000977、2^14=0.6015625、2^17=0.59375、2^20=0.5（理论 0.6 在 2^20 不可表示）；(b) 增量平移守恒——原点 31 拍与四个高坐标各 16 拍行走的逐拍 d_width/d_height 全为 0；(c) 站立下沉-顶回精确恒等——y=-60 与 y=101 各 150/42 拍 min_y 单值，12 连跳落地高度全部 1.7999992

## Next actions

- 实机对质推挤方向与每拍增量；虚表 slot144 具体实现用实机数字回查常量

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/collisions-precision-12651/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/collisions-precision-12651/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/collisions-precision-12651/probes`
