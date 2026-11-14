# Liquid Blip 全链独立复核（P0 → P2）

- 版本 / 加载器：MCBE `1.26.51.01` / LeviLamina `26.51.7`，映像基址 `0x140000000`。
- 静态环境：重建后的 `Minecraft.Windows.exe.i64`（session `b2777074`，本次 `decompile/disasm/xrefs` 全部走 `driver/ida.py`），常量与字节走 `driver/pe_map.py` 的只读映射。
- 动态材料：只重算既有原始数据（`probes/liquid-blip-recheck.json` 等），本轮**没有启动游戏、没有新增钻头、没有改任何游戏行为**。
- 证据标签沿用仓库约定：`[代码]` / `[常量]` / `[实测]` / `[推断]`。只有 `[代码]`+`[常量]` 闭环才写“确认”。

## 0. 结论摘要

| # | 命题 | 判定 | 关键新证据 |
| --- | --- | --- | --- |
| A | `StateVectorComponent+0x18/+0x1C/+0x20` 是 `mPosDelta`（待执行位移），**不是**速度 | **确认（并纠正命名）** | SDK 3×`TypedStorage<4,12,Vec3>` 布局；worker 把这三个分量当盒子平移；`FinalizeMove` 只写 `mPos`；实测 `raw(+0x1C)=0.0343` 而同期已完成位移 `0.0491` |
| B | 探针盒 = `AABB + (mPosDelta.x, mPosBeforeMove.y − mPos.y + 0.6 + mPosDelta.y, mPosDelta.z)` | **确认** | 逐指令：`movss xmm0,[r8+14h]`、`subss xmm0,[r9+4]`、`addss xmm0,0.6f`、`addss xmm0,[r9+1Ch]`；`0.6f` 字节 `9A 99 19 3F` @ `0x14E6AE498` |
| C | 写入点就是 `0x1482A8E46`（`mov [rsi+1Ch],0x3E99999A`，即 `0.3f`） | **确认** | 全 `.text` 只有两处 `[reg+0x1C]←0.3f` 立即数写：`0x14684BC0C`（另一系统的三轴批量写）与 `0x1482A8E46` |
| D | `+0x38` 虚槽 = 预过滤，`+0xA0` = 碰撞 AABB 收集 | **确认（补 thunk 事实）** | vtable `0x14E8554E0`：`+0x38 → 0x142D8A050`，该函数只有一条 `jmp sub_142D8A060`；`+0xA0 → 0x142D86DC0` |
| E | 触发边界是 `gap ≥ 0`（含零），源头是整格枚举的 `floor/ceil` | **确认（静态解释 + 实测夹逼）** | 预过滤按 `y ∈ [floor(min.y), ceil(max.y)-1]` 枚举；盒底恰好 `-46.0` 时 `floor(-46.0)=-46`，不包含液格 `-47`。实测最近阴性 `gap=-0.00305`，最近阳性 `gap=0.0` |
| F | 过滤条件 = 五个必需组件（AABB/MobTravel/StateVector/LiquidTravelFlag/HorizontalCollisionFlag） | **确认** | 查询构建器 `0x1482EC650` 五个 id 立即数；`0x1482EC8D0` 逐实体派发 |
| G | worker 内没有正速度门 | **确认（作用域限定到 worker + 查询）** | 函数体只有两个分支：预过滤返回、`vector.begin==vector.end`；触发样本里最负的 `raw=-1.95e-5`，**不是**“任何下沉都能触发” |
| H | `0.34 = 0.3 + 0.04`，`0.34→0.30` 是两次连续写入 | **确认（实测序列）** | `408199=0.34000015`、`408200=0.29999924`，之后才是空气 `0.98(v-0.08)` |
| I | 岩浆/水共用同一预过滤，深度不是硬门 | **确认（沿用 + 复核数据）** | 预过滤只看 `Material::mLiquid`，无液体种类参数；`r24` 各深度均触发 |
| J | 调度顺序：blip 系统在同一注册函数里晚于 travel/move 系统 | **部分确认** | `0x1468265CA` 注册，晚于 `0x1468261C6/260/29C` 与 `0x14682658B..5BB` 的 drag 组；集合遍历顺序仍受 `tick-order-12651` 的 pass/类别未决项约束 |
| K | 两侧都有该系统 | **确认（静态 + 实测）** | `sub_1468353C0 → sub_14682D3C0 → sub_146822800` 这条注册链有两个不同入口 `sub_1467F0940` / `sub_14683E2F0`；实测两侧各自命中入口与写点 |

## 1. P0-1 地址、函数身份、ABI 与命中点

### 1.1 函数边界（`.pdata`）

| 角色 | 起 | 长 | 说明 |
| --- | --- | --- | --- |
| `@liquid_exit_blip` | `0x1482A8D00` | `0x180` | 单实体 worker，`retn` 在 `0x1482A8E65`，尾部 watson 分支到 `0x1482A8E79` |
| `@liquid_blip_visitor` | `0x1482EC770` | `0x107` | 逐实体：先建块源视图，再间接调用回调 |
| `@liquid_blip_system` | `0x1482EC8D0` | — | 查询/派发壳 |
| `@liquid_blip_box_prefilter` | `0x142D8A060` | `0x1B5` | 真实函数体；`0x142D8A050` 是 5 字节 `jmp` 桩 |
| 碰撞收集器 | `0x142D86DC0` | — | vtable `+0xA0` |

`@liquid_blip_box_prefilter` 的 role 曾写成 `0x1428D8A060`（多一位）。本次用 `driver/pe_map.py 0x142D8A060` 得到 `.text +0x2D8A060`，且 vtable 槽 `+0x38` 指向 `0x142D8A050 → jmp 0x142D8A060`，两路一致。**旧地址不复活**。

### 1.2 命中点语义

`0x1482A8E46` 就是写指令本身：

```asm
1482a8e41  cmp rdi, rbx              ; vector.begin vs vector.end
1482a8e44  jnz short loc_1482A8E4D   ; 非空 → 跳过写
1482a8e46  mov dword ptr [rsi+1Ch], 3E99999Ah
```

执行断点落在 `0x1482A8E46` 时该指令尚未执行；`rsi` 是本函数的第 4 参（`StateVector`）。这与“命中后 RIP”“数据观察点”都不是一回事，动态报告里的写点计数按此解释成立。

### 1.3 五参与寄存器归属（由函数体自身证明，不靠调用者伪签名）

```c
// rcx = a1 (entity ctx)：函数体内只交给虚调用之外的逻辑，实际未被解引用
// rdx = a2 : float[6]  AABB（min.xyz, max.xyz）
// r8  = a3 : MobTravelComponent（只读 +0x14）
// r9  = a4 : StateVectorComponent（读 +0x04/+0x18/+0x1C/+0x20，写 +0x1C）
// [rsp+arg_20] = a5 : 块源视图（两个虚调用的 this）
```

调用点（`0x1482EC815` 起）也吻合：`rcx=&entityId`、`rdx=rbx`、`r8=rdi`、`r9=[rbp+arg_20]`、`[rsp+var_60]=holder+8`。ABI 上没有隐藏返回值/xmm 参数：两个虚调用都是 `(this, ...)`，返回 `bool`/`void`。

### 1.4 两个虚槽的真实 ABI（已按被调方独立核对）

| 槽 | 目标 | 实参 | 语义 |
| --- | --- | --- | --- |
| `+0x38` | `0x142D8A050`(`jmp`)→`0x142D8A060` | `(view, AABB const*)` | 盒内是否存在液体块；返回 1 即“有”，worker 直接返回 |
| `+0xA0` | `0x142D86DC0` | `(view, vector<AABB>*, AABB const*, bool=true)` | 逐块 `BlockLegacy::addAABBs`，把 24 字节 AABB 追加进 vector |

`+0xA0` 的被调方把第 2 参当 `{begin,end,cap}` 使用（`+8/+16` 比较、`+24` 步进），第 3 参当 6 个 float 的盒，第 4 参 `bool` 控制是否先做一次“盒与建造高度”的裁剪路径。没有 sret。

### 1.5 `@liquid_blip` 与 `@liquid_exit_blip`

`@liquid_blip` 是 2026-10-28（R-F14）阶段的旧别名，当时只记录了“贴墙+水面上+Space → 0.34→0.3”，地址为空。`@liquid_exit_blip` 是 R-F16 解析后的同一机制（`0x1482A8D00`）。两者**不是两种语义**，也没有第二个 0.3 写者参与：另一个 `[+0x1C]←0.3f` 写点 `0x14684BC0C` 属于另一系统，且同时写 `+0x18/+0x1C/+0x20`（见 §2.4）。

处理：`@liquid_blip` 保持 `unresolved`（repo 规则：不猜地址），但 evidence 里注明“已由 `@liquid_exit_blip` 取代，旧名仅作检索别名”。新代码一律用 `@liquid_exit_blip`。

## 2. P0-2 探针盒的六个 float

### 2.1 逐指令（`0x1482A8D2F..0x1482A8DBB`）

```asm
movss xmm0, [r8+14h]      ; MobTravel.mPosBeforeMove.y
movss xmm1, [r9+18h]      ; StateVector.mPosDelta.x
subss xmm0, [r9+4]        ;   − StateVector.mPos.y
addss xmm0, cs:0x14E6AE498;   + 0.6f   (字节 9A 99 19 3F)
movss xmm2, [r9+20h]      ; mPosDelta.z
addss xmm0, [r9+1Ch]      ;   + mPosDelta.y
...
addss xmm3, xmm2          ; max.z + dz
addss xmm4, xmm0          ; max.y + dy
addss xmm2, [rdx+8]       ; min.z + dz
addss xmm0, [rdx+4]       ; min.y + dy
addss xmm5, xmm1          ; max.x + dx
addss xmm1, [rdx]         ; min.x + dx
minss/maxss …             ; 存到栈上 6 个 float
```

- 没有 FMA、没有额外的 epsilon/缩扩盒；只有 6 条 `addss` 与 6 条 `minss/maxss`。
- x 用 `+0x18`、z 用 `+0x20` 的偏移也说明这三个分量是**同一个三元组**，不是“速度 + 额外项”。
- 盒在栈上只构造一次（`[rbp+var_70]`），两个虚调用都拿同一个指针；两次查询之间没有再次读组件。

### 2.2 常量

| 值 | 字节 | 位置 | 引用方式 |
| --- | --- | --- | --- |
| `0.6f` | `9A 99 19 3F` | `.rdata 0x14E6AE498` | `addss xmm0, cs:[…]` |
| `0.3f` | —— | 指令立即数 `0x3E99999A` | `mov dword ptr [rsi+1Ch], 3E99999Ah` |
| `0.5f` | `00 00 00 3F` | `0x14E5C6370` | `FinalizeMove` 求 AABB 中心 |

### 2.3 AABB 布局交叉

`AABBShapeComponent`：`TypedStorage<4,24,AABB> mAABB` + `TypedStorage<4,8,Vec2> mBBDim` = 32 字节，正是查询里的元素宽。快照里 `rdx` 的 32 字节后 8 字节恰为 `(0.6, 1.8)`（站立）/`(0.6, 1.49)`（蹲姿），与盒尺寸一致。

### 2.4 另一个 `0.3` 写者

`0x14684BC0C`（函数 `sub_14684B600`）：

```asm
mov  rax, [r13+218h]      ; Actor.BuiltInActorComponents → StateVector
movss [rax+18h], xmm6
mov  dword ptr [rax+1Ch], 3E99999Ah
movss [rax+20h], xmm7
```

它一次写三轴，且在遍历一个 0x98 步长的容器；只有 4 处数据引用（vtable/表），没有通往 `ClimbOutOfLiquidSystem` 的调用边。**它不可能产生只改 y 的 `0.3→0.34` 轨迹**。

## 3. P0-3 位置 / 速度 / 记录流的来源

### 3.1 字段身份

`StateVectorComponent`（36 字节）：`mPos`(+0x00)、`mPosPrev`(+0x0C)、`mPosDelta`(+0x18)。`Actor::getPosDelta()` 直接返回 `mPosDelta`。

- blip 写的是 `mPosDelta.y`；
- 水跳 `0x1495EACF1` 是 `addss xmm8,[r12+1Ch]; movss [r12+1Ch],xmm8`，加在同一个字段上；
- `FinalizeMove` worker `0x1463B39A0` 只写 `mPos`：`x=(min.x+max.x)*0.5`、`y=Offsets(+0)+AABB.min.y`、`z=(min.z+max.z)*0.5`；
- `ActorSetPosSystem` 用 `mPosPrev/mPosDelta` 做传送插值。

因此：**把 `+0x1C` 叫 `VelY` 是历史命名错误**。它是“等待后续运动应用阶段使用的位移量（格/tick）”；同 pass 尚未证明，不能断言它一定在下一个世界 tick 生效。数值上只有与已完成位移相同时才等于“速度”。

### 3.2 三个量必须分开

| 量 | 来源 | 例（W-jump-b 408266） |
| --- | --- | --- |
| 已完成的位移 | 实测等于 `mPos − mPosPrev`，也等于录制 `vy`（`getVelocity().y`；该函数体未定位，这里是 502/502 的实测关系） | `0.0491486` |
| 待执行位移 | `mPosDelta.y`（+0x1C） | `0.0343180` |
| 两者关系 | 水阻力 `0.8·v_prev − 0.005` | `0.8×0.0491486−0.005=0.0343189` ✓ |

这也解释了 AGENTS.md 里“地面行走时 `+0x18` 约为 `0.546×实际位移`”：地面摩擦 0.546 乘在上一拍位移上（`[推断]`：该系数与 `mPosDelta` 的写者尚未逐指令定位）。

### 3.3 `MobTravel.mPosBeforeMove`

SDK：`mLocalMovementVelocity(+0x00)`、`mMovementSpeed(+0x0C)`、`mPosBeforeMove(+0x10)`，所以 `+0x14` 是它的 `.y`。实测 1004 条入口全部满足 `mPosBeforeMove.y == mPosPrev.y`（`travel_previous_mismatches=0`）。

谁写 `mPosBeforeMove` **仍未定位**。r26 外部查询未能返回该组件
（陆地站立/行走 60+ tick、重建水池后 `in_water=True` 的 8/30 tick 轮询都报 `component_not_found`），
而 r24 入口快照里它的地址在各自窗口内稳定（client/server 各一个地址）。这两组不同场景的观察
不能解释查询失败的原因；组件 id、查询路径、registry 与生命周期差异仍是未排除的假设。
不能据此断言它“只在 tick 内存在”或“不在该存储”。若后续使用入口地址，须在同一窗口重新核对
实体、侧别与组件身份，不能沿用旧会话指针。这是 r26-100 的部分勘误，原始查询失败记录保留。

### 3.5 `mPosDelta` 写点候选（客户端短窗口；轴标签已勘误）

历史探针 r26-097 在一个“行走 + 三次跳跃”窗口内，同线程 `32784` 报告 9 个 RIP 站点，
归成下表 8 组。**旧标签 `xz` 错误：`+0x18,size=8` 实际覆盖 x、y；另一个观察点仅覆盖 y，
两者重叠，未安装 z 观察点。** 命中次数不能用作互斥轴计数，也不是完整每 tick 写者集合。
原始脚本、输出不改写；r26-099 的轴标签与完整性解释在账本另记部分勘误。

| 原报告函数分组 | 命中（xy 区间 / y 区间） | 说明 |
| --- | --- | --- |
| `0x1431E9EA0` | 47 / 58 | 12 字节三轴批量写（`[rbx+20h]=[rcx+8]`、`[rbx+18h]=qword[rcx]`） |
| `0x142E0D280` | 46 / 57 | 0x142E0 旅行家族 |
| `0x142DF1790` | 46 / 57 | 0x142DF 旅行家族 |
| `0x148B618A0` | 30 / 30 | 竖直加速度（r01-003 已记录 `0x148B61995`） |
| `0x147CD7120` | 13 / 24 | 未定名 |
| `0x142DF9DA0` | 13 / — | 仅在 xy 区间有记录；不据此推断 z 或“只写水平” |
| `0x142DE2010` | 33 / — | 仅在 xy 区间有记录；不据此推断 z 或“只写水平” |
| `0x1495E9200` | 3 / 3 | 未定名 |

独立的既有静态记录 r26-098 把 **`0x142DEE630` 定为阻力写点**：水平候选因子为
`v16 + (0.546f - v16) * fraction`，按原记录的分支选择该因子或 `v16`，即插值方向是
`lerp(v16, 0.546f, fraction)`，不是旧摘要的反向写法。随后 `[rsi+18h] *= 所选水平因子`、
`[rsi+1Ch] *= 0.8f`、`[rsi+20h] *= 同水平因子`（`0.8f`@`0x14E760F38`、`0.546f`@`0x14E85AF34`）。
此处只核对 r26-098 记录的代数式，没有重新核验二进制或分支含义。三轴乘法的既有静态证据
保留，不因探针轴标签出错而整体撤回；旧输出没有展示该函数的动态命中，不能拿它证明此处命中。

这组候选不是 pass 顺序证明；各写点的系统名、完整性与调用顺序仍未闭合。
工程替代工具为 `py -3.11 -m driver.delta_watch --ticks 80 --output out/delta-writes.jsonl`：
从运行中 profile 取布局，分别安装 x/y/z 三个 4 字节观察点，安装前取全局游标，保留原始分页
与 `lost`，只卸自己的 ID。该工具不移动玩家、不解析指令；仅完成离线回归，尚未现场重采，
所以本表没有新增逐轴命中数字。

### 3.4 记录流相位

入口快照自洽：`dy` 只需 `mPosBeforeMove.y`、`mPos.y`、`mPosDelta.y` 三项，不需要配对录制；配对只用于验证“已完成位移”。本轮重算确认 502 条客户端入口的 `mPos.y − mPosBeforeMove.y` 与配对录制 `vy` **逐位相等**（最大差 0）。

## 4. P0-4 系统顺序（按侧 / 按 pass）

### 4.1 注册链

```
sub_1467F0940 ─┐
               ├→ sub_1468353C0 → sub_14682D3C0 → sub_146822800（移动/行进注册大函数）
sub_14683E2F0 ─┘                                    ├ 0x1468230DA  sprint/input 组
                                                    ├ 0x146826032  MobTravelUpdateSpeedsSystem
                                                    ├ 0x1468261C6  WaterTravelSystem
                                                    ├ 0x146826260  LavaTravelSystem
                                                    ├ 0x14682629C  WaterMoveSystem
                                                    ├ 0x14682658B  sub_142DE0950（water drag 组）
                                                    ├ …0x1468265BB 另外四个 0x142DE*** 系统
                                                    └ 0x1468265CA  ClimbOutOfLiquidSystem
```

`ClimbOutOfLiquidSystem` 的注册器 `0x1482A8ED0` 只有一个代码调用者：`0x1468265CA`。也就是说，它在**同一个注册函数里、晚于**上述 travel/move/drag 组。若集合按注册追加、正序遍历（`tick-order-12651` §8 的静态结论），则同一 pass 内 blip 在 travel 之后执行——与实测“先移动、再更新位移、再检测”的相位一致。

### 4.2 仍受约束的边

`tick-order-12651` 记录了一个未决矛盾：sprint 写位与阻力写的实测先后与“纯注册序”不符，候选解释是不同类别（`E14/E1C/13584/1358C`）落在不同 pass。`ClimbOutOfLiquidSystem` 用与邻居相同的注册上下文（`[rbp+var_400]`，vtable `0x14E9A36D0`，registrar 来自 `r14`），但**本审计没有证明它和 WaterTravel 一定在同一个类别掩码里**。因此 §4.1 的“晚于”只在“同一 pass、按注册序”的前提下成立，这一点仍标未决。

### 4.3 两侧

注册链有两个独立入口（`sub_1467F0940`、`sub_14683E2F0`），与“客户端/服务端各建一套系统表”一致；实测两侧线程（`32784`/`38864`）各自命中入口与写点，组件地址不同。**两侧同构**在数据上成立（同一静态指令、同一公式、同一写点）。

### 4.4 `0.34` 的来源

`W-jump-a` 录制：`408198 vy=0.1615` → `408199 vy=0.34000015` → `408200 vy=0.29999924` → `408201 0.215599`。`0.34 = 0.3 + 0.04`，0.04 来自水跳（`0x1495EACF1`，同一字段 `addss`）；`0.30` 是后续一次 blip 再次写入（此时已出水，水跳不再加 0.04）。另一个三轴写者、复制/网络同步都无法解释这个只改 y 的序列。

## 5. P0-5 查询过滤与“没有正速度门”的作用域

- 查询构建器 `0x1482EC650`：`0xBAC1B3CF`(AABB)、`0x39CF8D64`(MobTravel)、`0x1B5D5238`(StateVector)、`0x3E7D1356`(LiquidTravelFlag)、`0xD5EE0BD8`(HorizontalCollisionFlag) 五个 id 全为**必需**；后两个只进过滤。
- 逐实体访问器 `0x1482EC770` 除“实体 id/维度 → 块源”外没有额外条件；worker 体只有两个分支（见 §1.2）。
- 因此“没有独立 `Vy>0` 门”的作用域是：**worker + 查询 + 访问器**。上游的 LiquidTravelFlag（`TravelTypeSensingSystem`）与 HorizontalCollisionFlag 仍会间接限制准入，这两个都不是速度符号门。
- HorizontalCollisionFlag 的置位点本轮只找到“清零/镜像”路径（`sub_14476CE40`、`sub_1402096F0` 以 `edx=0` 调 `sub_14253AB60`；`sub_141A1FE50` 用 `hasComponent` 结果调 setter）。**真正的“发生水平碰撞 → 置 1”写点未定位**，标 unresolved。它是否与碰撞同拍，只由 `r07-021` 的 2×2 实测支持，不升级为静态结论。
- 负速度触发样本最负 `raw=−1.95e-5`（`L-jump-060-b` 409323），其余在 `−3e-6` 量级。**“没有正速度门”成立，“任意下沉也能触发”不成立**。
- `TravelTypeSensingSystem` 内部如何区分水/岩浆（`ActorDataFlag` 的哪一位）仍未闭合。

## 6. P0-6 两个块查询与零边界

### 6.1 预过滤（`0x142D8A060`）

```c
int v6  = (int)fminf(floorf(min.x), ceilf(max.x));
int v21 = (int)fmaxf(floorf(min.x), ceilf(max.x));
for (x = v6; x != v21; ++x)  … 同样枚举 y、z …
    block = (*vtable+0x28)(this, pos);          // getBlock
    if (block->mLegacy->mMaterial->mLiquid) return 1;
return 0;
```

- 区间是 `[floor(min), ceil(max))`，整格枚举；**不看液位/流体高度**，只看该格 `Material::mLiquid`。源/流动/下落液体在该字节上没有区别。
- 边界相等：盒底 `min.y == -46.0`（液格 `-47` 的顶面）时 `floor(min.y) = -46`，枚举从 `-46` 开始，**不包含 `-47`** → 预过滤通过。这就是 `gap ≥ 0` 的静态来源，不是“浮点凑出来的经验阈值”。
- 负坐标：`floorf/ceilf` 先给出整数值，`(int)` 截断对整数值无误差。
- 无建造高度夹取（越界由块源 `getBlock` 返回空气）；没有 NaN/非法盒特判。
- 盒退化（某轴 `min==max`）时 `floor==ceil`，该轴枚举为空 → 返回 0（不构成障碍）。

### 6.2 碰撞收集（`0x142D86DC0`）

- 先按 `*(int16*)(this+58)`（建造上限）与 `this+56` 判断，必要时向 vector 追加一个“世界底面”平面盒（`min.y=-3.4028235e38`）。
- 逐格 `(*vtable+0x30)` 取块、`[block+0x68]` 取 `BlockLegacy`、`(*legacy_vtable+0x38)(legacy, block, this, pos, box, vec)` = `addAABBs`。
- 液体块没有碰撞形状，`addAABBs` 不产出盒，所以**收集器对液体是“空”，液体必须靠 `+0x38` 预过滤单独挡**——两条查询互补，不是重复。
- 枚举用 `+1.0/-1.0` 的边界偏移（与预过滤的 `floor/ceil` 写法不同），但两者都以“整格”为单位，不读液位。

### 6.3 `gap` 的实测夹逼

| 样本 | gap | 写点 |
| --- | --- | --- |
| `L-levII-100-a` 409055 | −0.003052 | 无 |
| `L-jump-180` 408978 | −0.005493 | 无 |
| `L-jump-080` 408703 | **0.000000** | 有 |
| `L-jump-100` 408810 | **0.000000** | 有 |
| 最近阳性非零 | +1.526e-5 | 有 |

1004 条里 `|gap|<0.02` 有 56 条、`|gap|<0.05` 有 132 条；阴性/阳性在 ±0.003 内完成翻转。**这是当前源液池（整格液面）上的结论**，不推广到流动液面、蹲姿或其它几何。

## 7. P1-1 1004 条配对的重算

本轮不采信既有 counts，直接从 `liquid-blip-recheck.json` 的原始快照重算：

| 检查 | 结果 |
| --- | --- |
| 入口总数 / 两侧 | 1004 / 502+502 ✓ |
| `dy` 重算 vs 存档 | 1004/1004 一致（<1e-6） |
| `v_move = mPos.y − mPosBeforeMove.y` vs 存档 | 1004/1004 逐位一致 |
| `mPosBeforeMove.y == mPosPrev.y` | 1004/1004 |
| 表面反推 `feet+dy−gap` | 每个 run 恒为 `-46.0` |
| `overwritten` 与 `lb-store` 观察点同线程窗口 | 98/98 一致，0 例外 |
| `gap ≥ 0` vs `overwritten` | 0 反例；严格 `>0` 有 4 条（全是 `gap==0` 且写） |
| 独立性单位 | 21 窗口 → 16 有入口 / 13 有写点；98 条写点入口 = 49 事件 × 2 侧 |

**不是循环论证**：`overwritten` 来自写指令上的执行观察点（同一线程两次入口之间），与 `gap` 的计算相互独立。**但**：

1. 写点窗口只按同线程 + QPC 归属，没有在写点记录实体指针；本场景只有玩家一个候选实体，所以归属安全，但不能外推到多实体场景。
2. 1004 条是“入口数”，不是 1004 次独立复现。独立性按**实验窗口 + 初态重置**计：
   - 21 个窗口里 16 个有入口、13 个出现过写点，每个窗口各建一次场、各重置一次初态；
   - 98 条带写点的入口 = **49 个 (窗口, tick) 事件 × 客户端/服务端两侧**，两侧是配对观测，不是两次独立重复；
   - 边界带 `|gap|<0.02` 的 56 条入口 = **28 个事件 × 两侧**（`|gap|<0.05` 是 66 个事件）；
   - 其余大多是“液面以下很深”的平凡阴性。
   因此能支持结论的独立重复上限是 **13 个窗口 / 49 个事件**，不能写成 98 次、更不能写成 1004 次。
3. 配对用 `y` 精确匹配且 `tick=入口+1`；本数据里没有第二行同 `y`，无多解；但分析方法本身没有独立的实体身份校验。

## 8. P1-2 0.3 / 0.34 / 衰减 / 悬浮

- 空气段（无悬浮，出液后）：`0.30 → 0.215599 → 0.132889 → 0.051830`，与 `0.98×(v−0.08)` 的残差 ≤ `1.72e-6`（float32 级）。**不是 ×0.8**。
- 悬浮 II 段：`0.30 → 0.254799 → 0.219364 → …`，与 `0.98×(0.8v + 0.02)` 的残差 ≤ `2.3e-6`。系数是 `(amp+1)×0.01 = 0.02`；旧文档里 `0.3→0.245→0.2019` 对应 **I 级**悬浮（`+0.01`），两组不能混写。
- `0.34 → 0.30` 是两次连续 blip 写，不是衰减：`408199` 在水里（水跳再 +0.04），`408200` 已出水（只写 0.3）。
- 水/岩浆出液后的相同序列只说明空气段一致，**不能**反推液内阻力相同（液内阻力由 `WaterTravel/LavaTravel` 各自决定）。

## 9. P1-3 1.62001 与蹲姿

- 常量 `1.62001f` 字节 `7D 5C CF 3F` @ `0x14E6AE444`；`0.4/0.2/0.6/0.5` 的地址与字节均复核。
- `FinalizeMove` 写 `mPos.y = Offsets.mHeightOffset + AABB.min.y`（逐指令见 §3.1）。
- 蹲姿对照（`r20-066.out`）：站立 `feet=-60.0, PositionY=-58.3799896, box 高 1.8`；蹲下 `feet 与 PositionY 完全不变，box 高 1.49，head_y 降 0.45`。所以 `PositionY` 不是眼睛/镜头高度，`dy` 的 `mPosBeforeMove.y − mPos.y` 也不受蹲姿高度影响（两项同在位置基准空间）。
- 不能由“1.62 相消”推出“蹲姿对 blip 无影响”：探针盒本身随姿态变矮，本轮**未测**蹲姿液内入口。

## 10. P1-4 首轮 W-slow-a 的输入消失

原始录制里 `w=True` 只出现在 `408002..408003` 两行，之后 `w=False` 直到结束；`record_stopped.reason="client"`，`rows=69`，无错误事件。也就是说“前进位提前消失”是**录制里真实可见的输入状态**，不是缺采。原因（任务提前取消、注入窗口、失焦释放中的哪一个）本轮未定位，保留 unresolved；该轮不作为墙面阴性，补跑的两组慢升起点均触发。

## 11. 复核结论分类

**独立证实**：A、B、C、D、F、G（限定作用域）、H、I、K；E 升级为“静态枚举 + 实测夹逼”。

**修正**：
1. `+0x18/+0x1C/+0x20` 的命名：`VelX/Y/Z` → `mPosDelta`（位移增量）。偏移不变，语义变。
2. `+0x38` 槽的地址链补上 `0x142D8A050` 的 `jmp` 桩；role `0x142D8A060` 指向真实体，不冲突。
3. 零边界从“live-only 观察”升级为“`floor()` 枚举的静态必然 + ±0.003 实测夹逼”。
4. 悬浮 II 的递推系数是 `0.02`，旧的 `0.245` 序列属于 I 级。
5. `@liquid_blip` 明确为旧别名，不是第二套机制。

**推翻/降级**：
- “`+0x1C` 是原始速度/最终速度”的任何写法 → 推翻。
- “1004 条 = 1004 次独立复现” → 1004 条两侧入口；其中 98 条写点入口对应 49 个配对事件、
  56 条边界带入口对应 28 个配对事件，写点独立重复上限为 13 个窗口。
- “固定 0.7 格阈值”“首轮岩浆全阴性”“起始深度 ≥0.6 禁区”“出液 ×0.8” → 沿用 r24-081/082/083 的撤回，本轮无新反证。

## 12. 未闭合（本轮没有装作闭合）

1. 水平碰撞标志的“置 1”写点（只找到清零/镜像路径）。
2. `TravelTypeSensingSystem` 内部水/岩浆判别使用 `ActorDataFlag` 的哪一位。
3. `MobTravel.mPosBeforeMove.y` 的写入者与外部查询失败的原因均未知（§3.3）。
4. `mPosDelta` 已有短窗口写点候选（§3.5，未覆盖 z）及独立的阻力写点静态记录，但这些写点各自的
   系统名、调用顺序、以及重力项 `−0.005`/`−0.08` 的具体归属还没有逐一对上。
5. `ClimbOutOfLiquidSystem` 与 travel 系统是否同一 pass/类别（受 `tick-order-12651` 的未决项约束）。
6. 蹲姿液内入口、流动液面、任意几何、最大可达深度。
7. 查询虚槽的六个 float **仍未直接抓取**；本轮只证明了“入口快照 → 盒”的逐指令构造与实测一致性，没有改变“重建而非直采”的性质。

## 13. 验收范围

非逆向工程修复的局部回归为 **51/51**（`test_hooks`、`test_delta_watch`、`test_landing`、
`test_scenario`，日志 `out/liquid-blip-engineering-tests.log`）；ruff check/format、mypy 与
账本/字段守卫通过。修正仅涉及 Python 工具、既有记录的代数核对及证据表述；没有新增逆向或
现场采样，不能据此宣称三轴重采、蹲姿/流动液体等边界已验收。

下表为既有静态审计的验收记录，并非本次工程修改重新运行的全套门禁：

| 检查 | 结果 |
| --- | --- |
| `py -3.11 skills/bedrock-re/checks/lint_public.py` | ok |
| `py -3.11 skills/bedrock-re/checks/lint_report.py --version 1.26.51.01 --case liquid-blip` | ok |
| `py -3.11 skills/bedrock-re/roles/resolve.py --version 1.26.51.01 --check` | 90/96 |
| `py -3.11 driver/gen_profiles.py --check` | up to date |
| `check_statevector.py` / `check_eye_feet.py` | ok / ok |
| `py -3.11 -m unittest discover -s tests` | 411 tests OK |
| `ruff check` / `ruff format --check` / `mypy` | 全过 / 115 files / 116 files |
| `git diff --check` | exit 0 |

另外修正了一个**生成器缺陷**（`driver/landing.py::generate_resume`）：此前被 `erratum_of` 撤回的
原 claim 仍会留在 `Open / pending` 或 `Confirmed` 里，现行摘要自相矛盾（例如 r23-077 的
“深度禁区”已被 r24-082 撤回，却还在 Open）。现在撤回目标从 Open 移除、在 Confirmed 里加
`（见 rXX 勘误）` 标注，并在 `Retracted / errata` 里同时打印“原命题”。`tests/test_landing.py`
增加了对应断言。

## 14. 四个验收问题

**Q1 游戏真正拿什么盒、什么速度、什么位置检测？**
盒 = 当前 AABB（移动后的脚部盒）平移 `(mPosDelta.x, mPosBeforeMove.y−mPos.y+0.6+mPosDelta.y, mPosDelta.z)`；
“速度”是 `StateVectorComponent.mPosDelta`（待执行位移），不是 `getVelocity()`；“位置”是
移动后的 `mPos.y` 与移动前的 `mPosBeforeMove.y`（两者同属 `feet+1.62001` 的位置基准空间）。

**Q2 为什么与录制速度不同？差异在哪几个阶段产生？**
在本轮已配对样本中，`getVelocity()`/录制 `vy` 等于已完成位移（`mPos−mPosPrev`）；入口读的是按阻力/重力
重算后的待应用位移。差异 = 本拍阻力/重力/悬浮的更新量（水中 `0.8v−0.005`，空气
`0.98(v−0.08)`，悬浮 II `0.98(0.8v+0.02)`），不是采样丢帧。

**Q3 0.3/0.34 与零边界有哪些静态证明和实测边界？**
0.3：唯一写点 `0x1482A8E46`，全 `.text` 只有两处该立即数；0.34：同一字段上水跳 `+0.04`
（`0x1495EACF1`），`408199=0.34 → 408200=0.30` 是两次连续写。零边界：预过滤 `[floor,ceil)`
整格枚举的静态结果；实测最近阴性 `−0.00305`、最小阳性 `0.0`（4 条全触发）。

**Q4 还有哪些结论没有资格写成确定机制？**
见 §12。最需要克制的是：调度顺序（pass 未定）、水平碰撞 flag 的生产者、`mPosDelta` 的
每 tick 写者、`mPosBeforeMove` 的写者、蹲姿/流动液面/任意几何、以及“查询虚槽实参已直采”
（没有直采，仍是重建）。

## 15. P2（现场边界实验）本轮未执行

原因：r24 已覆盖水/岩浆 × 深度 × 跳跃/悬浮 × 有无墙 × 有无观察点，且本轮结论没有依赖新的
现场窗口。按交接文档“只有静态模型明确后才补最小矩阵”的原则，以下留待需要扩展适用面时再做：

- 蹲姿液内入口（姿态改变 AABB 高度，预过滤枚举区间随之变化）。
- 流动液面（液位 ≠ 满格）与四方向贴墙。
- 上方/侧向碰撞各一个反例，验证三维盒而不是只看 `gap`。
- 同一几何下多实体/乘客，验证写点归属。

未跑这些不等于它们通过；上表与 §12 已把适用面限定在“整格源液池、站立、已测几何”。

## 16. 复现命令（静态部分）

```bash
py -3.11 driver/ida.py status
py -3.11 driver/ida.py decompile 0x1482A8D00      # worker
py -3.11 driver/ida.py disasm    0x1482A8D2F      # 盒子构造
py -3.11 driver/ida.py disasm    0x1482A8E41      # 写点
py -3.11 driver/ida.py disasm    0x142D8A050      # +0x38 thunk
py -3.11 driver/ida.py decompile 0x142D8A060      # 预过滤 floor/ceil
py -3.11 driver/ida.py decompile 0x142D86DC0      # 收集器
py -3.11 driver/ida.py decompile 0x1482EC650      # 五组件查询
py -3.11 driver/ida.py xrefs     0x1482A8ED0      # 注册调用点 0x1468265CA
py -3.11 out/va_read.py 0x14E8554E0 --qwords 26   # 块源 vtable
py -3.11 out/va_read.py 0x14E6AE498 --dwords 2    # 0.6f
```

字节级常数核验（`0.3f` 只有两处 `[reg+0x1C]` 立即数写）：

```bash
py -3.11 - <<'EOF'
import re, sys
sys.path.insert(0, 'driver'); import pe_map
from pathlib import Path
img = pe_map.PeImage(Path(pe_map.default_exe()))
text = next(s for s in img.sections if s.name == '.text')
data = img.read(img.image_base + text.va, text.vsize)
print([hex(img.image_base + text.va + m.start())
       for m in re.finditer(rb'\xc7([\x40-\x7f])\x1c\x9a\x99\x99\x3e', data)])
EOF
```
