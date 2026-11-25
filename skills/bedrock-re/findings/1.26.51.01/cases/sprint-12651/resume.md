# resume: sprint-12651

- version: `1.26.51.01`
- goal: 返工 v2：修正疾跑跳 +0.2 真实写入点（JumpFromGroundSystem 0x1495e9200）、补全 5e-5 撞墙判定的组件字段绑定、复现 f65 三区间与 X/Z 墙镜像、修正 Java 对照与延迟归因
- environment: -
- current round: 2
- last record: r02-011
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/sprint-12651/journal.jsonl`

## Confirmed (static closure required)

- r01-001: 四个疾跑系统（SprintTimer / SetRequestSprintTrigger / IntentSprintTrigger / ActionSprintTrigger）的注册点、vtable 与 slot[5]/slot[6] 入口已定位；ActionSprintTrigger 的单实体入口 0x14b1aa080 读 PlayerActionComponent（0x3000f399）的 0x200/0x400 并写 ActorDataFlag bit3。 [code; 注册 0x14681f3d6/0x14681f462/0x14681f4fa/0x14681fb62；vtable 0x14ea34f30/0x14eac9f40/0x14eac9f80/0x14eaca020；slot 入口 0x148b59df0+0x148b5a320 / 0x14b1a2670+0x14b1a2b50 / 0x14b1a3a80+0x14b1a3dc0 / 0x14b1a9fe0+0x14b1aa080；0x14b1aa13b mov edx,0x3000f399，0x14b1aa298 test ecx,0x200，0x14b1aa2c3 test ecx,0x400。]
- r01-002: 起跑只接受前向扇区：疾跑判定函数 0x14b1a4c80 要求输入 y ≥ 0.707107f、y>0、|x| ≤ 0.707107f、sqrt(x²+y²) ≥ 0.707107f；因此纯倒车、纯侧走、倒车+侧走都无法起跑。 [code; 0x14b1a4d16 movss [rdx+4]; 0x14b1a4d1b ucomiss 0.707107f; 0x14b1a4ec0 sqrt(x²+y²) 与 0x14e60229c(0.707107f) 比较；0x14b1a4ef0 y>0；0x14b1a4f00 |x|≤0.707107f。]
- r01-003: 撞墙断疾跑由唯一的 5e-5f（0x14eac97e0，仅 0x14b1a4c80 引用）实现：比较水平两轴模长，取占优轴比较碰撞前后绝对差是否 < 5e-5f；实测与墙面法线夹角 ≤20° 断、≥25° 贴墙滑行不断，f65 高频闪断未复现。 [constant; 0x14eac97e0 为全二进制唯一 5e-5f（u32=0x3851b717）；xrefs 仅 0x14b1a4f60/0x14b1a4f96；0x14b1a4f30 |[rcx+0x24]| vs |[rcx+0x1c]|，0x14b1a4f3b [rbx+8]-[rcx+0x30]，0x14b1a4f72 [rbx]-[rcx+0x28]，0x14b1a4f68/0x14b1a4f96 ucomiss 5e-5f。]（见 r02-008 勘误）
- r01-004: 疾跑跳水平冲量精确 +0.2：跳跃系统 0x1495ea890..0x1495eae6b 内唯一 0.2f 存储在 0x1495eae35（mov dword [r12+0x1C], 0x3E4CCCCD）；实机疾跑跳 mPosDelta 相对摩擦基线多出 0.20001~0.20009，走路跳无。 [constant; 0x1495eae35 mov dword ptr [r12+0x1C], 0x3E4CCCCD（0.2f）；该函数内无第二处 0.2f 存储。]（见 r02-007 勘误）
- r01-005: IntentSprintTriggerSystem 核心 0x14b1a34f0 用 6.0f（0x14e6022b4）与 [r8+0x28] 比较，即食物等级 ≤6 的疾跑取消门槛。 [constant; 0x14b1a3782 movss xmm0,[0x14e6022b4]=6.0f；0x14b1a378a ucomiss xmm0,[r8+0x28]；0x14b1a3797 cmp byte [r8+0x2c],0。]
- r02-009: 疾跑跳水平 +0.2 的静态写入点为 0x1495e940c..0x1495e9478（JumpFromGroundSystem 调用的 0x1495e9200 内）：读 ActorDataFlag bit3，yaw 经 π/180 与 65536/2π 转 SIN_TABLE 索引，mPosDelta.x -= 0.2·sin、mPosDelta.z += 0.2·cos。 [constant; 0x1495e940c test byte [rbx],8；0x1495e9416 mulss 0.0174533f；0x1495e941e mulss 10430.378f；0x1495e942d lea rdx,[0x151d6973c]；0x1495e9434 movss 0.2f(0x3e4ccccd)；0x1495e944c subss [r12+0x18]；0x1495e946b addss [r12+0x20]。]
- r02-010: 5e-5 撞墙判定的组件字段绑定：rcx=VanillaClientGameplayComponent(mSprintingOnInput +0x19、mLastDelta +0x1C、mLastPos +0x28)、rbx=StateVectorComponent(mPos +0/+8)、rdx=PlayerInputRequestComponent(mStopSprinting +0x0A、mCanSprint +0x0F)、rax=PlayerActionComponent(+1 |= 4)；判定为 |mLastDelta 主轴| 占优且 |mPos−mLastPos| 同轴 < 5e-5f。 [constant; 0x14b1a4d24 cmp byte [rdx+0x0F],1；0x14b1a4d2a cmp byte [rdx+0x10],0；0x14b1a4f30 andps |[rcx+0x24]|/|[rcx+0x1C]|；0x14b1a4f3b/0x14b1a4f72 分别取 [rbx+8]-[rcx+0x30] 与 [rbx]-[rcx+0x28]；0x14b1a4fa6 mov [rdx+0x0A],r9b；0x14b1a5073 mov byte [rcx+0x19],0；0x14b1a5077 or byte [rax+1],4。]

## Open / pending

- r01-006: 实机行为矩阵：倒车/侧走/潜行在切换拍取消疾跑；地面与空中首拍均为 1.3×（0.098/0.1274 与 0.0196/0.02548），无 1-tick 延迟；疾跑跳冲量与 sprint 位绑定而非当前速度。 (live-only)
- r02-011: 实机三区间与 f65：Z 墙 ≤20° 单次断、23.5~24.4° 逐拍闪烁、≥24.5° 稳定；X 墙 65.6° 闪烁、≤65.58° 稳定；临界互补 24.42°+65.58°=90°；贴墙起步与跑入触墙的区间不同（贴墙 25° 闪烁）。 (live-only)

## Retracted / errata

- r02-007 (erratum of r01-004): r01-004 把 0x1495eae35 的 0.2f 误标为疾跑跳水平冲量：该指令写的是 StateVectorComponent.mPosDelta.y，门控为方块 BlockProperty bit 33（Climbable），属于攀爬上行；真正的疾跑跳 +0.2 在 JumpFromGroundSystem 调用的 0x1495e9200 内 0x1495e940c..0x1495e9478（ADF bit3 + SIN_TABLE → mPosDelta.x/z）。 [refuted] — 原命题: 疾跑跳水平冲量精确 +0.2：跳跃系统 0x1495ea890..0x1495eae6b 内唯一 0.2f 存储在 0x1495eae35（mov dword [r12+0x1C], 0x3E4CCCCD）；实机疾跑跳 mPosDelta 相对摩擦基线多出 0.20001~0.20009，走路跳无。
- r02-008 (erratum of r01-003): r01-003 的“两个未知 Vec3 / 没有水平碰撞标志”与“≥25° 不断、f65 未复现”不成立：5e-5 判定读 VanillaClientGameplayComponent.mLastDelta(+0x1C/+0x24) 与 mLastPos(+0x28/+0x30)、StateVectorComponent.mPos(+0/+8)、PlayerInputRequestComponent.mCanSprint(+0x0F/+0x10)/mStopSprinting(+0x0A)、PlayerActionComponent(+1 bit2=0x400)；实测三区间：≤20° 单次断、23.5~24.4° 逐拍闪烁（f65）、≥24.5° 稳定，X 墙 65.6° 闪烁。 [refuted] — 原命题: 撞墙断疾跑由唯一的 5e-5f（0x14eac97e0，仅 0x14b1a4c80 引用）实现：比较水平两轴模长，取占优轴比较碰撞前后绝对差是否 < 5e-5f；实测与墙面法线夹角 ≤20° 断、≥25° 贴墙滑行不断，f65 高频闪断未复现。

## Next actions

- closed

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/sprint-12651/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/sprint-12651/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/sprint-12651/probes`
