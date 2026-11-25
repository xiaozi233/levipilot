# resume: stepping-12651

- version: `1.26.51.01`
- goal: 返工第 2 轮：补全 AutoStepFilterSystem 准入/天花板过滤/跳跃冷却静态闭环，重测落地拍 Solid Blip、Step-Assist Jump Cancel、点按与台阶 Grind
- environment: -
- current round: 2
- last record: r02-015
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/stepping-12651/journal.jsonl`

## Confirmed (static closure required)

- r01-001: 1.26.51.01 的移动/碰撞注册函数 0x14681ac20..0x14681c6f8 内按 MoveCollisionSystem(0x14681aef5) → ActorMoveSystem(0x14681b173) → AutoStepFilterSystem(0x14681b28f) → AutoStepSystem(0x14681b319) → FinalizeMoveSystem(0x14681b7eb) 顺序登记；顶层 0x146822800..0x146829404 中跳跃/疾跑/移速系统组均先于该碰撞组。 [code; driver/pe_map.py --find-str/--xrefs/--func 对注册名字字面量与 vtable 槽位逐条实查；--disasm 0x14681b160/0x1468230c0/0x146826548 确认同一函数内的调用先后；cases/stepping-12651/probes/verify_stepping.py 提供复现入口。]
- r01-002: MaxAutoStepComponent 默认步高为 0.5625；VariableMaxAutoStepComponent 默认 {mBaseValue 0.5625, mControlledValue 1.0625, mJumpPreventedValue 0.5625, mHardcodedClientSide true}，由 ClientVariableMaxAutoStepSystem 回调按条件选 +0/+4/+8 写入；候选盒下界下探 step×1.01=0.568125。 [constant; driver/pe_map.py --func 0x142583226/0x1428ce9f0/0x14b177650/0x1431c314a 与 --find-f32 1.01；SDK 头 refs/levilamina-26.51.7/src/mc/entity/components/{MaxAutoStepComponent.h,VariableMaxAutoStepComponent.h} 与 auto_step_system/AutoStepSystem.h；cases/stepping-12651/probes/verify_stepping.py 复现。]
- r01-003: ActorMoveSystem 与 AutoStepSystem 共用求解器 0x1482a4890，常规移动按 Y→X→Z 三步；AutoStepSystem 先 Y↑step→X→Z 三步再按第一拍实际抬升量 Y↓ 回落，最后以水平位移平方 dx²+dz² 严格大于常规 mSpeed.x²+mSpeed.z² 为采纳条件。 [code; driver/pe_map.py --func 0x1490283a0/0x149049700/0x1482a4890 与 --xrefs 0x1482a4890（5 处调用点）；cases/stepping-12651/probes/verify_stepping.py 复现。]
- r01-004: 上台阶成功只改写 MoveRequestComponent::mSpeed 与缓存碰撞盒、挂 HasAutoSteppedComponent，不写 StateVectorComponent::mPosDelta；OnGround/HorizontalCollision 由 FinalizeMoveSystem 另写。 [code; driver/pe_map.py --func 0x149049700/0x1463b39a0/0x1490283a0；SDK 头 refs/levilamina-26.51.7/src/mc/deps/vanilla_components/MoveRequestComponent.h 与 StateVectorComponent.h；cases/stepping-12651/probes/verify_stepping.py 的 WATCH 复现。]
- r02-009: AutoStepFilterSystem worker 0x149048220 的完整准入条件是 maxAutoStep > 0 且发生水平碰撞，且 isFlying（MovementAbilitiesComponent +4 bit0）、落地拍（mPreCollisionSpeed.y < 0 && mSpeed.y != mPreCollisionSpeed.y）、has<OnGroundFlagComponent>、has<CanAlwaysAutoStepFlagComponent> 四者之一，之后才 emplace AutoStepRequestFlagComponent。 [code; driver/pe_map.py --func 0x149048220 / 0x149048ab0 / 0x149048e70 与 --ecs-hash 0xc29078a0 / 0x6de78dc5 / 0x91e56036 / 0xbaa9695a；SDK 头 refs/levilamina-26.51.7/src/mc/deps/vanilla_components/MoveRequestComponent.h；cases/stepping-12651/probes/verify_stepping.py 复现。]
- r02-010: AutoStepSystem 0x149049700 在把临时碰撞盒交给两次 0x1482a4890 试探之前，会先剔除 box.min.y >= mOriginalAABBShape.max.y 的天花板盒；采纳前再对未过滤的完整 mCollisionShapes 做终态 AABB 相交复核。 [code; driver/pe_map.py --func 0x149049700 与 --xrefs 0x1482a4890（5 处调用点）；SDK 头 refs/levilamina-26.51.7/src/mc/deps/vanilla_components/MoveRequestComponent.h（mOriginalAABBShape +0x00、mCollisionShapes +0x58）；cases/stepping-12651/probes/verify_stepping.py 的 low_ceiling_step_* 对照。]
- r02-011: 10 tick 连跳冷却的载体是 MobJumpComponent::mNoJumpDelay（组件 0xc52c8f78，偏移 +0x10，int，池步长 0x14）：起跳时置 10、每拍递减、减到 0 才允许再跳；松开跳跃键经 ResetNoJumpDelaySystem 清零，因此冷却只在长按不松时生效。 [constant; driver/pe_map.py --func 0x1495ea890 / 0x14961b5f0 / 0x149621750 / 0x14c5f72c0、--disasm 0x1495ea882 / 0x1495ea9ed / 0x1495eac38 / 0x14961b7ca / 0x1495eae70、--ecs-hash 0xc52c8f78 / 0x8fd20e98、--find-str DecrementNoJumpDelaySystem / ResetNoJumpDelaySystem / MobJumpSystem / JumpInputSystem；SDK 头 refs/levilamina-26.51.7/src/mc/entity/components/MobJumpComponent.h；cases/stepping-12651/probes/verify_stepping.py 的 held/tap 对照。]

## Open / pending

- r01-005: 实机步高边界：满宽单盒 0.5（雪片 height=4）与 0.5625（切石机）可平地走上，0.625（雪片 height=5）被挡在台阶前沿。 (live-only)
- r01-007: 能走上的 0.5/0.5625 台阶不会触发 5e-5 撞墙断疾跑，只有 0.625 真被挡停才断疾跑。 (live-only)
- r02-012: 低净空矮天花板会通过终态 AABB 复核拒绝上台阶：头顶净空 1.2 格时可走上 0.5 台阶，净空 0.2 格时被拒并卡在台阶前沿。 (live-only)
- r02-013: 紧贴 0.42 < h <= 0.5625 台阶前沿（或矮天花板前缘）起跳时，+0.42 起跳被 Step-Assist 整拍替换：玩家被直接放到台阶顶面（垂直位移等于台阶高）或保持地面，水平速度全速保留，且下一次落地拍点按可立即重跳。 (live-only)
- r02-014: 把首次水平碰撞对齐到落地同一拍后，基岩版 1.26.51.01 存在保速固体 Blip：落地/接触同拍水平速度与无台阶控制组完全一致，且当拍一步抬到 0.5625 台阶顶面。 (live-only)
- r02-015: 2 格矮天花板下点按跳跃显著快于长按：按 1 拍/松 1 拍可达每 3 tick 一跳（3 次重复共 35 次冲量、间隔 3~5 tick）、max_vz = 0.631786；长按 24 拍只有 10 tick 间隔、max_vz = 0.482189；连续 0.5 台阶阶梯点按未能连锁 Jump Cancel（间隔 12~15 tick）。 (live-only)

## Retracted / errata

- r02-008 (erratum of r01-006): 撤回第 1 轮 r01-006「1.26.51.01 无 JE 1.8 式固体 Blip / Ceiling Blip；Jump Cancel 把 +0.42 裁成 +0.2；Grind 是 10 tick 一次的冲量周期」：三处结论均为夹具/测法/静态漏读造成的假阴性。 [refuted] — 原命题: 1.26.51.01 无 JE 1.8 式固体 Blip / Ceiling Blip；Jump Cancel 把 +0.42 裁成实际 +0.2；Grind 是 10 tick 一次的冲量周期而非每拍 +0.2。

## Next actions

- closed

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/stepping-12651/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/stepping-12651/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/stepping-12651/probes`
