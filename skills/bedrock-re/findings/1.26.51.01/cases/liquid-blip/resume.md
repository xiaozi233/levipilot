# resume: liquid-blip

- version: `1.26.51.01`
- goal: 静态定位基岩液体退出弹跳（Liquid Blip）的写入函数
- environment: -
- current round: 27
- last record: r27-105
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-blip/journal.jsonl`

## Confirmed (static closure required)

- r21-071: 岩浆同样触发出水贴墙弹跳：同一坐标同一堵墙只把水换成岩浆，跳+墙与 levitation+墙都能看到 vy 被写成 0.3 并按 0.8 衰减，不贴墙的对照不触发 [code; 静态侧：ClimbOutOfLiquid 的过滤器是泛液体标签 LiquidTravelFlagComponent（r20-067 证明它由 TravelTypeSensingSystem 与 Water/Lava 标签同批维护），盒内预过滤 sub_142D8A060 只问 Block→[+0x68]→[+0x28]→byte[+2] 是否为实体块，与液体种类无关；SDK MobMovementClimbOutOfLiquid.h 的 climbOutOfLiquid(StrictEntityContext, AABBShapeComponent, MobTravelComponent, StateVectorComponent&, IConstBlockSource) 签名里也没有液体种类参数。]（见 r21-074, r24-081 勘误）
- r22-075: BlockLegacy+0x28 指向的对象是 mMaterial（BlockType 的第三个成员：vtable + mDescriptionId(32B) 之后），+2 字节是 Material::mLiquid——ClimbOutOfLiquid 的盒内预过滤问的是「盒里有没有液体块」而不是「有没有实体块」，判为液体即跳过 blip [code; ① SDK refs/levilamina-26.51.7/src/mc/world/level/block/BlockType.h：`class BlockType {` 无基类，成员序为 vtable(+0x00,8B) → mDescriptionId(std::string,32B,+0x08) → **mMaterial(Material const&,8B,+0x28)** → mComponents(BlockComponentStorage,96B,+0x30) → …；与二进制 `mov rax,[rax+0x28]` 逐位吻合。② 同仓 refs/mc/world/level/material/Material.h：`TypedStorage<1,1,MaterialType> mType(+0x00)`、`mNeverBuildable(+0x01)`、**`mLiquid(+0x02)`**、`mBlocksMotion(+0x03)`、`mBlocksPrecipitation(+0x04)`、`mSolid(+0x05)`、`mSuperHot(+0x06)`——与 `cmp byte ptr [rax+2],0` 吻合。③ 独立交叉验证：SDK 虚函数序 ~BlockType(+0x00)/getNextBlockPermutation(+0x08)/hasTag(+0x10)/clip(+0x18)/getCollisionShape(+0x20)/getCollisionShapeForCamera(+0x28)/addCollisionShapes(+0x30)/**addAABBs(+0x38)**/getOutline(+0x40)；二进制里 +0xA0 收集器 sub_142D86DC0 正是按 `BlockLegacy->vtable+0x38(legacy, block, this, pos, box, &vector)` 调 addAABBs 并把 24 字节 AABB 追加进 vector，而 0x14204E840 走的是 `vtable+0x40`（getOutline，返回 AABB const&，代码随拷贝 24 字节）——两处调用点与 SDK 槽序一致，反证 +0x28 的偏移算法可信。④ IDA 反编译（重建库 session b2777074）0x14204E840：`if ( *(_BYTE *)(*(_QWORD *)(*(_QWORD *)((*(…)(a2, v19)) + 104) + 40LL) + 2LL) != 0 ) result = 0; else …`（104=0x68=mLegacy、40=0x28=mMaterial、+2=mLiquid），即「液体块直接返回 0（不构成障碍）」，与 Material::mLiquid 的语义自洽。]（见 r24-084 勘误）
- r25-087: StateVectorComponent+0x18/+0x1C/+0x20 是 mPosDelta（待执行位移，格/tick），不是 VelX/VelY/VelZ——blip 与水跳都写这个字段，FinalizeMove 只写 mPos，所以“已发生位移/待执行位移/换名后的速度”必须分成三个量 [code; ① SDK refs/levilamina-26.51.7/src/mc/deps/vanilla_components/StateVectorComponent.h：`TypedStorage<4,12,Vec3> mPos; mPosPrev; mPosDelta;` → +0x00/+0x0C/+0x18，总 36 字节，与 docs/offsets 的组件尺寸一致；Actor.h 的 `getPosDelta()` 内联返回 `mStateVectorComponent->mPosDelta`。② worker 0x1482A8D2F..0x1482A8DBB 把 +0x18/+0x1C/+0x20 当盒子平移的三轴（x 用 +0x18、z 用 +0x20），并在 +0x1C 写 0.3。③ 水跳 0x1495EACF1：`addss xmm8,[r12+1Ch]; movss [r12+1Ch],xmm8`——同一个字段做加法。④ @finalize_move_worker 0x1463B39A0（本次逐指令复核）：`[rcx]= (aabb.min.x+aabb.max.x)*0.5`、`[rcx+4]= Offsets[+0]+aabb.min.y`、`[rcx+8]= (min.z+max.z)*0.5`，只写 mPos，不写 +0x18/+0x0C。⑤ 实测（r25-086 重算）：入口处 mPosDelta.y=0.0343180 而同期已完成位移 mPos.y−mPosPrev.y=0.0491486，两者差正好等于水阻力式 0.8·0.0491486−0.005。]
- r25-088: 探针盒的逐指令构造已闭合：盒 = AABB + (mPosDelta.x, mPosBeforeMove.y−mPos.y+0.6f+mPosDelta.y, mPosDelta.z)，只在栈上构造一次；两个虚槽的 ABI 与 thunk 链已按被调方独立核对；全 exe 只有两处 0.3f 写入 [+0x1C] [code; ① 0x1482A8D2F..0x1482A8DBB：`movss xmm0,[r8+14h]`(MobTravel.mPosBeforeMove.y)→`subss xmm0,[r9+4]`(mPos.y)→`addss xmm0,0.6f`(cs:0x14E6AE498，字节 9A 99 19 3F)→`addss xmm0,[r9+1Ch]`；x 轴用 [rdx]+[r9+18h]，z 轴用 [rdx+8]+[r9+20h]；6 次 minss/maxss 后写栈 [rbp+var_70]，两个虚调用都用同一个指针，没有再读组件。无 FMA/epsilon/额外缩扩。② vtable 0x14E8554E0 的 +0x38 = 0x142D8A050，该地址只有一条 `jmp sub_142D8A060`（5 字节叶跳，不在 .pdata，所以 driver/pe_map 的 function_at 返回空是正常的）；+0xA0 = 0x142D86DC0。③ 被调方签名：(view, AABB const*) 返回 bool；(view, vector<AABB>*, AABB const*, bool) 追加 24 字节盒——无 sret、无隐藏返回。④ 字节扫描（r25-086）：`c7 <modrm> 1c 9a99993e` 全 .text 仅 0x14684BC0C 与 0x1482A8E46；前者一次写 +0x18/+0x1C/+0x20 三轴且只有数据引用，不在 blip 调用链上。]
- r25-089: gap≥0（含零）的触发边界有静态来源：预过滤把盒按整格 floor/ceil 枚举成 [floor(min), ceil(max))，盒底恰好等于液面时 floor(min.y) 从液面那一格开始，不包含液格 → 预过滤通过；不是经验阈值 [code; ① sub_142D8A060 反编译：x 轴 `v6=(int)fminf(floorf(min.x), ceilf(max.x)); v21=(int)fmaxf(...)`，循环 `while (v6 != v21)` 即 [floor(min), ceil(max)-1]；y 轴同样 `floorf(a2[1]+0.0)`..`ceilf(a2[4])`；z 轴同理；每格只查 `Block→[+0x68]→[+0x28]→byte[+2]`（Material::mLiquid），非 0 返回 1。② 因此 min.y 恰为 -46.0（液格 -47 的顶面）时 floor(-46.0)=-46，液格 -47 不在枚举内；min.y 只要低 1 ULP 就会落到 -47。③ 无液位/流体高度读取，整格判定；盒某轴退化时 floor==ceil，空枚举返回 0。]
- r25-090: “没有独立 Vy>0 门”的作用域是 worker+查询+访问器：查询要求五个组件全在（含两个 flag），worker 体只有“预过滤返回”和“vector 为空”两个分支；阴性最负 raw 只有 -1.95e-5，所以不能把结论扩成“任意下沉都能触发” [code; ① 查询构建器 0x1482EC650 传五个 id：0xBAC1B3CF/0x39CF8D64/0x1B5D5238/0x3E7D1356/0xD5EE0BD8（后两个是 flag，只进过滤）。② 逐实体访问器 0x1482EC770 除实体 id/维度→块源外无额外条件。③ worker 0x1482A8D00 的完整控制流只有 `test al,al; jnz`（预过滤）与 `cmp rdi,rbx; jnz`（vector 空判定），没有任何对符号/大小的比较；函数体不读输入位、不读全局状态（除 0.6f 常量与 guard 指针）。④ 触发样本（r25-086）中 raw 最负 -1.95130706e-05，其余负值在 -3e-6 量级。]
- r25-091: ClimbOutOfLiquidSystem 在移动注册大函数 sub_146822800 里只有一个代码调用点 0x1468265CA，晚于 WaterTravel/LavaTravel/WaterMove 与 0x142DE*** drag 组；整条注册链有两个独立入口，对应两侧 [code; ① xrefs 0x1482A8ED0：唯一代码引用 0x1468265CA（同在 sub_146822800 内）；同函数内既有注册点：0x1468230DA（sprint/input）、0x146826032（MobTravelUpdateSpeedsSystem）、0x1468261C6（WaterTravelSystem）、0x146826260（LavaTravelSystem）、0x14682629C（WaterMoveSystem）、0x14682658B..0x1468265BB（sub_142DE0950/58F0/36A0/1050/21D0 五个 0x142DE*** 系统）。② 注册上下文 [rbp+var_400] 在 0x146826560 装 vtable off_14E9A36D0，注册器取自 r14，随后各系统 ctor 依次调用它。③ 链路上游：sub_146822800 ← 0x146830A8A（sub_14682D3C0）← 0x1468375F5（sub_1468353C0）；sub_1468353C0 有两个代码调用者 sub_1467F0940 与 sub_14683E2F0，即两套注册入口。]
- r26-096: 术语与独立性口径定死：mPosDelta 是「等待后续运动应用阶段使用的位移量」，同 pass 尚未证明，不能写成「下一个世界 tick」；1004 条入口的独立性按实验窗口与初态重置计——98 条写点入口 = 49 个 (窗口, tick) 事件 × 客户端/服务端两侧，13 个窗口出现过写点，不能当成 98 次（更不能当成 1004 次）独立复现 [code; 字段侧沿用 r25-087 的静态闭环（SDK 三 Vec3 布局、blip/水跳同写 +0x1C、FinalizeMove 只写 mPos）：mPosDelta 的语义是「待应用的位移」，但没有任何静态证据说明它在同一次 tick 的哪个阶段被消费，所以只能写「后续运动应用阶段」。独立性侧是计数事实：r25-086 的 r25 统计与 recheck.json 的 lb-store 观察点显示 21 个窗口、16 个有入口、13 个有写点，写点入口 98 条且按 (run,tick) 去重为 49 个事件——每个事件两侧各一条，属于配对观测。]
- r26-098: 0x142DEE630 是每 tick 把阻力乘进 StateVector.mPosDelta 的写入者：水平分量乘 lerp(0.546f, friction, fraction)，竖直分量独立乘 0.8f；它就是「mPosDelta 由 travel 系统重算」的静态落点 [code; ① 0x142DEE76D..0x142DEE7D3 的逐指令：`movss xmm1,[rsi+18h]`→`mulss xmm1,xmm0`→`movss [rsi+18h],xmm1`；`movss xmm1,[rsi+1Ch]`→`mulss xmm1,cs:0x14E760F38`→`movss [rsi+1Ch],xmm1`；`mulss xmm0,[rsi+20h]`→`movss [rsi+20h],xmm0`。rsi 即 StateVector（三轴偏移 +0x18/+0x1C/+0x20）。② 常量字节：0x14E760F38 = 3F4CCCCD = 0.8f；0x14E85AF34 = 3F0BC6A9 = 0.546f。③ 水平因子的构造（同函数 0x142DEE7A8..0x142DEE7AE）：v16 = (*flags&8)?0.9f : (组件值?:0.8f)；v27 = (0.546f−v16)*fraction+v16；再按 `*a5 < 1.0f` 在 v16/v27 间选择。④ 调用者 0x142DEFBD0 从组件取 StateVector(36 字节 stride) 等实参后 tail-call 本函数；r26-097 在该函数体内观察到 +0x18/+0x1C 的写点（0x142DF17F5/0x142E0D290 是同级其它写者）。]（见 r26-103 勘误）
- r26-103: 部分勘误 r26-098：水平插值候选应写 v16+(0.546f-v16)*fraction，即 lerp(v16,0.546f,fraction)，不是 lerp(0.546f,friction,fraction)；保留三轴阻力乘法的既有静态记录 [code; 仅审计 r26-098 已记载的代数表达式 v27=(0.546f-v16)*fraction+v16：fraction=0 为 v16，fraction=1 为 0.546f。沿用原记录的分支在 v16/v27 中选择与竖直乘 0.8f；未重新核验二进制、分支含义或系统注册顺序。旧输出未展示 0x142DEE630 动态命中，不再把它作为该函数已命中的证据。]
- r26-104: 非逆向工程修复：Harness 的 watch_drain/hook_drain 透传 since=None，显式 0 兼容保留；新增被动三轴采集器，逐轴 4 字节、安装前游标、全局分页后分 ID、保留 lost、仅清理自有 ID 与首错；不改变输入或姿态 [code; driver/harness/facade.py 两个默认参数改为 int|None=None，对齐已有 commands/watch.py 与 hooks.py；driver/delta_watch.py 从运行中 profile 取布局，以 try/finally 释放逐轴已安装 ID，JSONL 逐页 flush 并标出不完整与清理错误。这里只证明 Python 工具控制流，不新增游戏机制结论。]
- r27-105: Python hook/watch 默认游标分别按环类型保留最近安装，不再由另一个类型的安装覆盖；显式 since 与按 ID 查询继续兼容 [code; driver/harness/commands/hooks.py 的 ring_head 在未指定 ID 时从 _ring_heads 按最新安装顺序查找同 kind 的 head；remember_ring_head 更新同 ID 时先移除再加入以维护顺序。移除原先单一 _last_ring 导致的跨类型覆盖；未改协议或游戏代码。]

## Open / pending

- r15-051: 实际提交给碰撞/液体查询的六浮点盒能否把触发边界闭合到 gap>=0 (inconclusive)
- r15-052: 脚本调试失败能否算作测量证据 (inconclusive)
- r19-064: 补充r15速度替换对照：只换原始VelY不能解释零头仍成立，但补回位置项可以解释 (live-only)
- r19-065: 同线程动态先后：位移发生后更新速度，再进入退出液体检测并覆写0.3 (live-only)
- r20-068: probe 链接修复：r05-011/r06-018/r08-025..r08-029/r10-032/r10-033/r16-053/r17-054/r17-055/r18-057 共 13 个 probe 的原始输出都已在 probes/*.out，其结论此前只以正文引用形式落在别的记录里，缺 ledger 的机器可读 probe_ref，导致 lint_report 判 pending；本条一次性补链接，不新增结论 (live-only)
- r25-092: 对 1004 条入口的独立重算成立：dy/gap 与写点归属都能从原始快照复现，写点不是由 gap 反推（无循环论证）；但样本独立性有限——98 次写点事件、56 条边界带样本，不是 1004 次独立复现 (live-only)
- r25-094: 悬浮 II 的空气段递推是 0.98×(0.8v+0.02)，不是 +0.01：0.30→0.254799→0.219364，残差≤2.3e-6；0.98×(0.8v+0.01) 的 0.245 序列属于 I 级悬浮 (live-only)
- r25-095: HorizontalCollisionFlag 的置位生产者在本次审计中仍未闭合：只找到清零/镜像路径（sub_14476CE40 与 sub_1402096F0 以 edx=0 调 sub_14253AB60；sub_141A1FE50 用 hasComponent 结果调同一 setter），没有定位“发生水平碰撞→置 1” (inconclusive)
- r26-101: 部分勘误 r26-099（并纠正 r26-097 标签）：+0x18,size=8 实际覆盖 mPosDelta.x/y，另一个 +0x1C,size=4 与 y 重叠，没有装 z 观察点；保留短窗口内 9 个 RIP 站点（8 个函数分组）的观察，不再称完整三轴/每 tick 写者集合，也不以缺少 y 命中推断只写水平 (live-only)
- r26-102: 部分勘误 r26-100：保留陆地/水中 ecs_context 报 component_not_found 与历史入口快照成功的观察；查询失败原因 unresolved，不能据此断言 MobTravel 只在 tick 内存在或不在该存储 (inconclusive)

## Retracted / errata

- r22-076 (erratum of r20-067): 勘误：r20-067 把 [+0x28]→byte[+2] 推断成“可碰撞/实体块标记”，实际是 Material::mLiquid（液体标记），预过滤的真实语义是「盒内有没有液体块」——方向正好相反 [refuted] — 原命题: 把StateVector.PositionY直接命名为当前眼睛Y，暗示蹲姿会随眼睛降低
- r24-081 (erratum of r21-071): 部分撤回r21-071的附带解释：第一轮岩浆全阴性、出液按0.8衰减、固定高出液面0.7格阈值均不成立；保留水/岩浆都能触发这一主结论 [refuted] — 原命题: 岩浆同样触发出水贴墙弹跳：同一坐标同一堵墙只把水换成岩浆，跳+墙与 levitation+墙都能看到 vy 被写成 0.3 并按 0.8 衰减，不贴墙的对照不触发
- r24-082 (erratum of r23-077): 撤回r23-077的岩浆初始深度硬门：从液面下0.6格或更深起跳/悬浮并非不可能触发 [refuted] — 原命题: 岩浆 blip 的适用边界：脚在液面下 0.6 格及更深时岩浆阻力让玩家浮不出水面（探针盒永远越不过液面），blip 不可能触发；只有浅位（约 0.3~0.5 格以内）起跳/levitation 才成立
- r24-083 (erratum of r21-074): 部分更正r21-074：原始probe链接关系保留，但其中再次称r21-072全阴性的解释撤回 [refuted] — 原命题: 勘误：r21-071（岩浆同样触发 blip）的 probe_ref 误写成不存在的 r20-066/r20-067；对应的两个探针是本轮重新入账的 r21-072 与 r21-073
- r24-084 (erratum of r22-075): 部分更正r22-075：预过滤Material::mLiquid定性保留；撤回0.7阈值旁证，并把role地址0x1428d8a060笔误改回既有证据的0x142d8a060 [refuted] — 原命题: BlockLegacy+0x28 指向的对象是 mMaterial（BlockType 的第三个成员：vtable + mDescriptionId(32B) 之后），+2 字节是 Material::mLiquid——ClimbOutOfLiquid 的盒内预过滤问的是「盒里有没有液体块」而不是「有没有实体块」，判为液体即跳过 blip
- r24-085 (erratum of r22-076): 更正r22-076的撤回目标：r20-067是PositionY命名勘误，并非实体块标记推断；该名称勘误仍有效 [refuted] — 原命题: 勘误：r20-067 把 [+0x28]→byte[+2] 推断成“可碰撞/实体块标记”，实际是 Material::mLiquid（液体标记），预过滤的真实语义是「盒内有没有液体块」——方向正好相反
- r25-093 (erratum of r24-080): 局部更正 r24-080 的命名：入口 +0x1C 是 mPosDelta（待执行位移），不是“检测 VelY”；dy=0.6+检测值−本拍位移的公式与 gap≥0 的观察都不变 [refuted] — 原命题: 独立动态复测支持完整偏移 dy=0.6+检测VelY−本拍位移；新增1004条两侧入口（各502）与同线程写点按gap>=0一致，含4条float32重建gap=0的触发样本
- r26-101 (erratum of r26-099): 部分勘误 r26-099（并纠正 r26-097 标签）：+0x18,size=8 实际覆盖 mPosDelta.x/y，另一个 +0x1C,size=4 与 y 重叠，没有装 z 观察点；保留短窗口内 9 个 RIP 站点（8 个函数分组）的观察，不再称完整三轴/每 tick 写者集合，也不以缺少 y 命中推断只写水平 [live-only] — 原命题: 客户端玩家 StateVector.mPosDelta 的每 tick 写者不是单一函数：行走+跳跃窗口内同线程（32784）观测到 9 个写点，主导者是 0x1431E9EA0（三轴批量写）、0x142E0D280、0x142DF1790，另有竖直加速度 0x148B618A0、0x147CD7120、0x1495E9200
- r26-102 (erratum of r26-100): 部分勘误 r26-100：保留陆地/水中 ecs_context 报 component_not_found 与历史入口快照成功的观察；查询失败原因 unresolved，不能据此断言 MobTravel 只在 tick 内存在或不在该存储 [inconclusive] — 原命题: MobTravelComponent 在离开液体后对 ecs_context 不可见，无法用外部查询拿到地址来装写观察点；mPosBeforeMove 的写者需要在水里用入口快照的 r8 地址或静态扫描定位
- r26-103 (erratum of r26-098): 部分勘误 r26-098：水平插值候选应写 v16+(0.546f-v16)*fraction，即 lerp(v16,0.546f,fraction)，不是 lerp(0.546f,friction,fraction)；保留三轴阻力乘法的既有静态记录 [confirmed] — 原命题: 0x142DEE630 是每 tick 把阻力乘进 StateVector.mPosDelta 的写入者：水平分量乘 lerp(0.546f, friction, fraction)，竖直分量独立乘 0.8f；它就是「mPosDelta 由 travel 系统重算」的静态落点

## Next actions

- 三轴乘法证据保留，其他分支语义及完整调度顺序仍按未决项管理。
- 新工具的现场逐轴计数、蹲姿/流动液体/复杂几何均未验收；静态写者与 pass 定位留给后续授权逆向。本轮不推送。
- 现场逐轴采集与未闭合的机制边界继续保留未验收；本轮没有新增逆向或游戏测量。

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-blip/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-blip/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/liquid-blip/probes`
