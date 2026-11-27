# resume: creative-flying-12651

- version: `1.26.51.01`
- goal: Round 3 返工：纠正 MobMovementFriction 玩家路径 0.91/0.375 消费链（0x142DFE950→0x142DE2086）、修正 VerticalFlySpeedControlSystem 水平/竖直输入位与 0.75f 分支、补跑松 W 按跳/潜行与平飞松跳实测、更正 §5 相位表标签与 Java 落地退出飞行对照
- environment: -
- current round: 4
- last record: r03-022
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/creative-flying-12651/journal.jsonl`

## Confirmed (static closure required)

- r01-005: 松开移动键后的水平阻力是两段：第一拍仍是 0.91，从第二拍起稳定在 0.34125 = 0.91 × 0.375；0.34125 不是独立 .rdata 常量，而是 0.91f（飞行空中摩擦）与 0.375f（FrictionModifierOverrideComponent， 由 VerticalFlySpeedControlSystem 在无竖直输入时写入）的乘积。 [constant; 0.34125（0x3EAEB852）全库仅 4 处 .rdata 命中（0x14E9794BC/4CC/F90/FB0）， 全部 0 条 .text 引用，且落在方块碰撞高度表内，与飞行无关 （driver/pe_map.py --find-f32 0.34125 / --xrefs 逐条 0 hit）。 0.375f 的写点：0x1428A7297 mov dword ptr [r15+r14*4],0x3EC00000 （VerticalFlySpeedControlSystem 逐实体 lambda 0x1428A7140，写 0xCBB7191 = FrictionModifierOverrideComponent）；同一 lambda 在 0x1428A72B5/0x1428A72BA 把 mPosDelta.y 乘 0.375f 当且仅当 jump/sneak 位与 ADF 0xC 全为 0。 0.91f：0x142DE2048 载入、0x142DE214B 乘入水平分量。 两因子相乘的消费点（谁先谁后）为推断；乘积与实测逐拍吻合。]（见 r03-017 勘误）
- r01-006: 竖直递推为 vy' = 0.6·vy + a：上升 a=+0.15、下降 a=-0.22、悬停时再加乘 0.375； 稳态 +0.375 / -0.55 格/tick；同时按跳与潜行为 0（mPosDelta.y 被强制清零）； 竖直阻力 0.6 来自 1 - 0.4f。 [code; 竖直位移 lambda 0x1428A7140（VerticalFlySpeedControlSystem slot[5]=0x1428DEF50 调用）： 0x1428A72EC..0x1428A7316 逐位累加 +0.15f(0x14E6AE4DC)、+0.05f(0x14E6AE530)、 -0.22f(0x14E83A410)、-0.15f(0x14E6AE4E8)； 0x1428A7374 取属性表（compare [rax+0x10],7）后 0x1428A7382 mulss、0x1428A7386 addss [rsi+0x1C]、 0x1428A738B 写回 mPosDelta.y； 0x1428A72D4 读 [rdx+0x60]，(~eax)&0xC == 0 时 0x1428A72E0 直接清零 [rsi+0x1C]。 竖直阻力：0x142DF50B0 movss xmm0,[0x14E85AF38](0.4f)，0x142DF50D5 subss(1-0.4)， 0x142DF50E1 mulss [r8+0x1C]；无方块因子时即 0.6。]
- r01-007: 创造飞行的同 tick 顺序：竖直飞行控制（姿态组）→ … → PlayerFlyingMoveSpeed → TravelMoveRequestSystem → 移动碰撞组 → 水/岩浆阻力/悬浮/重力 → MobMovementDrag::PlayerFlying → MobMovementFriction::PlayerFlying；三者同在 mode=1 的类别向量，注册顺序即 tick 顺序。 [constant; 三个注册函数同被 0x146822800 的主 setup 调用，调用点先后： 0x146826130（PlayerFlyingMoveSpeed 0x142DE5AD0）→ 0x1468264F3（TravelMoveRequestSystem vtable 0x14EA52070，slot[5] 0x1490DBD90）→ 0x146826555（移动碰撞组 0x14681AC20） → 0x14682658B/0x146826597/0x1468265A3（水/岩浆 drag、Levitate、重力） → 0x1468265AF（MobMovementDrag::PlayerFlying 0x142DE1050） → 0x1468265BB（MobMovementFriction::PlayerFlying 0x142DE21D0）。 VerticalFlySpeedControlSystem 注册在姿态组 0x14681DA00 内（0x1468200EA）， 该组由 0x1468230DA 调用，早于 0x146826130。 所有系统均以 r8w=1 追加进同一类别向量，@systems_foreach 正序遍历。]
- r01-008: 贴地飞行不读下方方块摩擦：同一高度（脚 -59.5、离地 0.5）下石面与蓝冰的 摩擦/加速度逐位相同；水中飞行同样走飞行公式，不叠加水体阻力； 迅捷/缓慢效果对水平与竖直飞行均无影响。 [constant; 飞行摩擦系统的视图只含 PlayerFlyingTravelComponent(0x42910726) 与 DiscardFrictionFlagComponent(0x40BED7F4)（0x142DE27A8 movabs 0x4291072640BED7F4）， 不带 MoveCollision/OnGround 之类的地面组件；0.91 在 0x142DE2048 无条件载入， 分支只为方块摩擦系数留了 r8 口，飞行路径的 r8 为空。]（见 r03-018 勘误）
- r01-009: 相位口径：StateVectorComponent+0x18 的 mPosDelta 是本拍摩擦后的待执行位移， 数值等于同拍已完成位移的 0.91；下一拍已完成位移 = 上一拍 mPosDelta + 0.049。 [constant; StateVectorComponent（0x1B5D5238）偏移 +0x18/+0x1C/+0x20 在 0x142DE2057/0x142DE206B/0x142DE21BC 被摩擦乘 0.91 后写回， 而 0x1428A738B 又把竖直增量加到同一 +0x1C；两处都是"先算位移、后乘阻力"。 watch 采样与录制同拍（record_start watch=[0x1B5D5238+0x18/0x1C/0x20]）。]（见 r03-019 勘误）
- r03-016: 【勘误 r01-004】玩家飞行的摩擦不走 0x142DE201D 的 Vex 分支：该字节是 VexFlagComponent(0x52857D3D)，0x142DE2024..0x142DE2075（含 0x142DE2048 的 0.91f） 只服务恼鬼。玩家路径由 MobMovementFriction::PlayerFlying 的 slot[6]（0x142DFE950） 压入 0.91f（0x142DFEB78 → [rsp+0x20]），并把 FrictionModifierOverrideComponent （0x0CBB7191）的指针经 0x142DFEA78/0x142DFEB5D 传入 r8；水平递推 v' = 0.91·v + 0.049 与稳态 0.5444 的结论不变。 [code; driver/pe_map.py --func 0x142DFE950：0x142DFEA78 mov edx,0xCBB7191（查覆盖组件）、 0x142DFEA96 mov edx,0x52857D3D（查 VexFlagComponent，与 --ecs-hash 0x52857D3D 命中一致）、 0x142DFEB5D add r8,[r9+rdx]（组件指针入 r8）、0x142DFEB78 mov dword [rsp+0x20],0x3F68F5C3、 0x142DFEB83/0x142DFEB86 call 0x142DE2010。 callee 0x142DE201D cmp byte [rsp+0x50],0（= Vex 位）：非零走 0x142DE2024 的 Vex 分支； 为零走 0x142DE207A 玩家分支。]
- r03-017: 【勘误 r01-005】0.34125 的消费链已闭合在玩家分支：0x142DE2080 取 0.91f 入 xmm0， r8 != 0 时 0x142DE208B movss xmm2,[r8]（读 0.375f）→ 0x142DE2090 mulss xmm3,xmm2， 再于 0x142DE214B mulss xmm3,xmm0 得 0.375×0.91=0.34125，经 0x142DE2176/0x142DE218A 写回 mPosDelta.x / mPosDelta.z。不再保留"消费点未定位、标推断"的旧说法。 [code; 0x142DE207A 玩家分支：0x142DE2080 movss xmm0,[rsp+0x40]（调用方压入的 0.91f）； 0x142DE2086 test r8,r8；0x142DE2089 je 0x142DE2099（无覆盖组件时走方块摩擦口）； 0x142DE208B movss xmm2,[r8]；0x142DE2090 mulss xmm3,xmm2；0x142DE2094 jmp 0x142DE214B； 0x142DE214B mulss xmm3,xmm0；0x142DE2176/0x142DE218A 两次 mulss + movss 写回 x/z。 0.375f 来源仍为 0x1428A7297 的写入（见 r03-017）。]
- r03-018: 【勘误 r01-008】"飞行路径 r8 为空"不成立：r8 就是 FrictionModifierOverrideComponent 的实例指针，空闲 tick 由竖直飞行系统写入 0.375 后非空；只有该组件缺失时才落到 0x142DE2099 的方块摩擦分支。贴地/蓝冰/水中/药水无影响的实测结论不变。 [code; 同 r03-016：0x142DFEA78 查询 0x0CBB7191，0x142DFEB45..0x142DFEB5D 用稀疏集页表 解出实例指针写入 r8；0x142DE2086 的 test/jne 决定是否用该值。 飞行摩擦视图仍只含 PlayerFlyingTravelComponent(0x42910726) 与 DiscardFrictionFlagComponent(0x40BED7F4)（0x142DE27A8 movabs）。]
- r03-019: 【勘误 r01-009】相位表两行标签写错：探针计算的是 median/max(|vx_{n+1} − (mPosDelta_n + a)|)，不是 |mPosDelta − vx|； 结论本身（mPosDelta 为本拍摩擦后的待执行位移、下一拍已完成位移 = 上一拍 mPosDelta + a） 不变。 [constant; 0x142DE2176/0x142DE218A 把摩擦后的值写回 StateVectorComponent+0x18/+0x20； 0x1428A738B 把竖直增量加到 +0x1C；两者都在"本拍位移已应用"之后， 故同一行里 pdx 与 vx 相差一个摩擦因子，而递推要跨拍比较。]
- r03-020: VerticalFlySpeedControlSystem（0x1428A7140）的输入字段分工：0x1428A7170/0x1428A7175 读的是水平移动模拟量（rdx+0x24/+0x28），≥0.01f 时 0x1428A71A1 整段跳过覆盖写入与 mPosDelta.y×0.375；0x1428A72A6/AE 的 [rdi+0x60] bit2/bit3 与 [rdx]&0xC 是竖直控制位， 只决定是否再乘 0.375；覆盖值按键鼠模式二选一：0x1428A7297 写 0.375f， 0x1428A72C9 写 0.75f（非键鼠模式）。 [code; 0x1428A7196 movss xmm0,0.01f；0x1428A719E ucomiss/jbe 0x1428A72D4； 0x1428A7286 mov ecx,[rbx]；0x1428A7288 cmp ecx,1 / 0x1428A728D..0x1428A7295 判模式 5； 0x1428A7297 mov dword [r15+r14*4],0x3EC00000（0.375f）； 0x1428A72C9 mov dword [r15+r14*4],0x3F400000（0.75f）； 0x1428A72B0 and eax,0xC / jne 0x1428A72D4 只跳过 0x1428A72BA 的 mulss。]
- r03-021: 松开 W 后按住 jump 或 sneak，水平滑行不受竖直键影响：首拍 0.91、第二拍起 0.34125； 同时竖直方向分别进入 +0.15 / -0.22 的加速。 [code; 水平覆盖的写入门控只看水平模拟量（0x1428A7170..0x1428A71A1），竖直位只影响 0x1428A72B5 的 mPosDelta.y 乘法；因此竖直键不会改变水平摩擦的 0.91/0.34125 切换。]
- r03-022: 按住 W 飞行时松开跳跃，竖直衰减首拍为 0.6 而不是 0.225——水平输入使 0x1428A71A1 跳过 mPosDelta.y×0.375。 [code; 同 r03-018 的 0x1428A7196..0x1428A71A1 门控；跳过时只剩 MobMovementDrag::PlayerFlying 的 1-0.4f（0x142DF50B0）。]

## Open / pending

- r01-010: 落地不会自动退出创造飞行；flying 位在触地后保持为真，竖直速度被清零。 退出只能靠再次双击空格或撤销 MayFly。 (live-only)
- r01-011: 1.26.51.01 创造飞行：水平/竖直递推、0.34125 滑行阻力、贴地/蓝冰/水中/药水对照、mPosDelta 相位 (inconclusive)
- r01-012: 1.26.51.01 创造飞行：水平/竖直递推、0.34125 滑行阻力、贴地/蓝冰/水中/药水对照、mPosDelta 相位 (inconclusive)
- r02-014: ruff 重构（zip→pairwise、格式）后复跑探针，全部关键拟合值逐位复现： f=0.90999982、a=0.04899699、疾跑比 1.999997、滑行 0.34125139、 竖直 +0.375/-0.54999924、相位残差 8.94e-07、水中 a=0.04899726、贴地 a=0.0489969。 (live-only)

## Retracted / errata

- r03-016 (erratum of r01-004): 【勘误 r01-004】玩家飞行的摩擦不走 0x142DE201D 的 Vex 分支：该字节是 VexFlagComponent(0x52857D3D)，0x142DE2024..0x142DE2075（含 0x142DE2048 的 0.91f） 只服务恼鬼。玩家路径由 MobMovementFriction::PlayerFlying 的 slot[6]（0x142DFE950） 压入 0.91f（0x142DFEB78 → [rsp+0x20]），并把 FrictionModifierOverrideComponent （0x0CBB7191）的指针经 0x142DFEA78/0x142DFEB5D 传入 r8；水平递推 v' = 0.91·v + 0.049 与稳态 0.5444 的结论不变。 [confirmed] — 原命题: 创造飞行的水平递推为 v' = 0.91·v + 0.049（格/tick），其中 0.91 是飞行摩擦系统 MobMovementFriction::PlayerFlying 的空中基值（0x14E76D2D8 = 0.91f）， 加速度 0.049 = flySpeed 0.05（MovementAbilitiesComponent+0x08）经 0.98 缩放后的实测值； 稳态 0.5444 格/tick，疾跑为精确 2.0 倍（1.0888）。
- r03-017 (erratum of r01-005): 【勘误 r01-005】0.34125 的消费链已闭合在玩家分支：0x142DE2080 取 0.91f 入 xmm0， r8 != 0 时 0x142DE208B movss xmm2,[r8]（读 0.375f）→ 0x142DE2090 mulss xmm3,xmm2， 再于 0x142DE214B mulss xmm3,xmm0 得 0.375×0.91=0.34125，经 0x142DE2176/0x142DE218A 写回 mPosDelta.x / mPosDelta.z。不再保留"消费点未定位、标推断"的旧说法。 [confirmed] — 原命题: 松开移动键后的水平阻力是两段：第一拍仍是 0.91，从第二拍起稳定在 0.34125 = 0.91 × 0.375；0.34125 不是独立 .rdata 常量，而是 0.91f（飞行空中摩擦）与 0.375f（FrictionModifierOverrideComponent， 由 VerticalFlySpeedControlSystem 在无竖直输入时写入）的乘积。
- r03-018 (erratum of r01-008): 【勘误 r01-008】"飞行路径 r8 为空"不成立：r8 就是 FrictionModifierOverrideComponent 的实例指针，空闲 tick 由竖直飞行系统写入 0.375 后非空；只有该组件缺失时才落到 0x142DE2099 的方块摩擦分支。贴地/蓝冰/水中/药水无影响的实测结论不变。 [confirmed] — 原命题: 贴地飞行不读下方方块摩擦：同一高度（脚 -59.5、离地 0.5）下石面与蓝冰的 摩擦/加速度逐位相同；水中飞行同样走飞行公式，不叠加水体阻力； 迅捷/缓慢效果对水平与竖直飞行均无影响。
- r03-019 (erratum of r01-009): 【勘误 r01-009】相位表两行标签写错：探针计算的是 median/max(|vx_{n+1} − (mPosDelta_n + a)|)，不是 |mPosDelta − vx|； 结论本身（mPosDelta 为本拍摩擦后的待执行位移、下一拍已完成位移 = 上一拍 mPosDelta + a） 不变。 [confirmed] — 原命题: 相位口径：StateVectorComponent+0x18 的 mPosDelta 是本拍摩擦后的待执行位移， 数值等于同拍已完成位移的 0.91；下一拍已完成位移 = 上一拍 mPosDelta + 0.049。

## Next actions

- closed

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/creative-flying-12651/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/creative-flying-12651/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/creative-flying-12651/probes`
