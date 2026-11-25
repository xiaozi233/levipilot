# 上台阶系统、Step-Assist Jump Cancel、固体 Blip 与 Grind（1.26.51.01）— 返工 v2

> 版本：`Minecraft.Windows.exe` `1.26.51.01`（LeviLamina `26.51.7`，`image_base = 0x140000000`）。
> 证据约定：`[代码]` 反汇编/调用链；`[常量]` `.rdata` 浮点、组件 ID、SDK 结构布局；`[实测]` LeviPilot 逐 tick 录制与 ECS 回读；`[推断]` 由直接证据补出的推导。
> 复现脚本：[`verify_stepping.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/stepping-12651/probes/verify_stepping.py)。原始数据：gitignored `out/stepping-1.26.51.01.json`（本文所有 `[实测]` 数字均由该 JSON 导出；通俗版 [`stepping-wiki.md`](../stepping-wiki.md) 按 2~5 位有效数字修约）。
> 相位口径：录制 `vx/vy/vz` 是本拍**已完成位移**；watch `+0x18/+0x1C/+0x20` 是 `StateVectorComponent::mPosDelta`（待执行位移）；`+0x42` 起跳冲量写在**最后一个仍报 `on_ground` 的拍**，水平跨台阶/抬升结果出现在**下一拍**；`feet_y` 是脚底。参见 [`AGENTS.md`](../../AGENTS.md)。
> 数值口径：`1.0625`、`0.568125` 等是 `.rdata` 常量或算术推导，不属于逐 tick 实测数字；`[实测]` 数值逐位取自 `out/stepping-1.26.51.01.json`。跑场基准：`ground = -60.0`、`layer = -61`、台阶前沿 `47.7`；绝对坐标随每次跑场的出生点整体平移，本文引用的是最终一跑的 JSON。
> 返工说明：第 1 轮 `r01-006`「无固体 Blip / 无 Jump Cancel / Grind 为 10 tick 周期」是夹具算错落点 + 只测长按空格 + 漏读静态准入造成的假阴性，已在第 2 轮以 `erratum_of` 撤回（见 §9）。

---

## 0. 结论摘要（v2）

1. **注册顺序**（沿用 `r01-001`）：同一函数 `0x14681ac20..0x14681c6f8` 内 `MoveCollisionSystem 0x14681aef5` → `ActorMoveSystem 0x14681b173` → `AutoStepFilterSystem 0x14681b28f` → `AutoStepSystem 0x14681b319` → `FinalizeMoveSystem 0x14681b7eb`；顶层 `0x146822800..0x146829404` 中跳跃/疾跑/移速组都先于碰撞组 `[代码]` `[常量]`。`1/2 AutoStepSystem` 只是 `0x149049700` 内的 NaN 诊断标签 `[代码]`。
2. **完整准入**（硬伤 1）：`AutoStepFilterSystem 0x149048220` 要求 `maxAutoStep > 0`、发生水平碰撞，且 `isFlying` / **落地当拍** / **`OnGroundFlagComponent`** / `CanAlwaysAutoStepFlagComponent` 四者之一成立才挂 `AutoStepRequestFlagComponent`。由于 `OnGroundFlagComponent` 直到末尾 `FinalizeMoveSystem` 才更新，**从地面起跳的第 1 拍仍持有起拍初值**——这正是 Step-Assist Jump Cancel 与固体 Blip 的准入来源 `[代码]` `[推断]`。
3. **滤天花板盒**（硬伤 1）：`AutoStepSystem 0x149049826..0x149049888` 在两次求解前，把临时碰撞盒列表里 `box.min.y >= mOriginalAABBShape.max.y` 的盒（原头顶上方的天花板）**临时删掉**；试探完成、算出候选终点后，`0x149049a8b..0x149049c60` 再对**未过滤的完整列表**做终态 AABB 相交复核 `[代码]`。低顶走台阶对照实测：`1.2` 净空可上 `0.5` 台阶，`0.2` 净空被拒（`max_z = 47.700001`）`[实测]`。
4. **步高边界**（沿用 `r01-005`）：`0.5` / `0.5625` 可平走上去，`0.625` 挡在前沿；上台阶拍只改 `MoveRequestComponent::mSpeed`，`mPosDelta` 不动（`mpd_y = -0.07840000092983246`），水平速度不减 `[实测]` `[代码]`。
5. **Step-Assist Jump Cancel**（硬伤 2）：紧贴 `0.5` / `0.5625` 台阶前沿起跳，第 `1` 拍写入 `+0.42`，**下一拍**直接被放到台阶顶面（`feet_y = -59.5` / `-59.4375`，`vy = 0.5` / `0.5625`）、`mpd_y` 回到 `-0.07840000092983246`、`vz = 0.32740020751953125` 全速保留；下一次落地拍点按可立即重跳 `[实测]`。天花板前缘同样吃掉 `+0.42` 并全速钻进矮顶下 `[实测]`。
6. **固体 Blip 成立**（硬伤 3）：把首次水平碰撞对齐到**落地同一拍**后，`vz = 0.2523994445800781` 与控制组落地速度 `0.2523994445800781` **完全一致**、速度不丢，且当拍从 `-59.87871170043945` 一步抬到 `-59.4375`（`vy = 0.4412117004394531`）；第 1 轮提前 `2` 拍撞墙，才误得「先挡停、不保速」`[实测]`。
7. **Grind 的真实节拍**（硬伤 4）：`10` tick 只是长按空格时 `MobJumpComponent::mNoJumpDelay` 的自动连跳冷却；**松开跳跃键会清空该计数**。点按 `1` 拍按 / `1` 拍松最快可达 **每 `3` tick 一跳**（`35` 次冲量中间隔 `3~5` tick）、`max_vz = 0.631786`、`max_mpd_z = 0.504387`；长按仅 `0.482189`。台阶阶梯点按未能连锁 Jump Cancel（间隔 `12~15` tick）`[代码]` `[实测]`。

---

## 1. 系统注册与管线顺序（`[代码]` `[常量]`）

| 系统 | 注册点（名字字面量） | vtable | slot[5] / slot[6] |
| --- | --- | --- | --- |
| `MoveCollisionSystem` | `0x14681aef5` | `0x14e871920`（见 [`notes/speed-limit-12651.md`](speed-limit-12651.md)） | `0x1431dc500` / `0x1431dd080` |
| `ActorMoveSystem` | `0x14681b173`（`0x14ec3555a`） | `0x14ea50160` | `0x149028740` / `0x149028c70` |
| `AutoStepFilterSystem` | `0x14681b28f`（`0x14ec33d59`） | `0x14ea508f0` | `0x1490487a0` / `0x149048ab0` |
| `AutoStepSystem` | `0x14681b319`（`0x14ec342cf`） | `0x14ea50930` | `0x149049f80` / `0x14904abb0` |
| `FinalizeMoveSystem` | `0x14681b7eb` | — | — |

顶层注册函数 `0x146822800..0x146829404` 内的先后：`0x1468203da`（`JumpInputSystem`）、`0x1468230da`（`0x14681da00` 疾跑触发器组）、`0x146823b8d`（`DecrementNoJumpDelaySystem`）、`0x146823ce0`（`MobJumpSystem`）、`0x146823ebf`（`JumpFromGroundSystem`）、`0x14682414e`（`ResetNoJumpDelaySystem`）、`0x146826032`（`MobTravelUpdateSpeedsSystem`）、`0x14682655b`（`0x14681ac20` 移动/碰撞组）`[代码]`。

`AutoStepSystem` per-entity 入口 `0x149049f80` 经 `0x149049d20`（取 `MaxAutoStepComponent 0x9aae5d7f`、`MoveRequestComponent 0x4c8f7288`、`AABBShapeComponent 0xbac1b3cf`、`SubBBsComponent 0x0b0822d1`、`AutoStepRequestFlagComponent 0xbaa9695a`）与 `0x149049e40` 回调到 `0x149049700` `[代码]` `[常量]`。

---

## 2. `AutoStepFilterSystem` 完整准入（硬伤 1，`[代码]`）

视图构造 `0x149048e70` 取 6 个组件：`MoveRequestComponent`、`MaxAutoStepComponent`、`OnGroundFlagComponent (0xc29078a0)`、`MovementAbilitiesComponent (0x6de78dc5)`、`AutoStepRequestFlagComponent`、`CanAlwaysAutoStepFlagComponent (0x91e56036)` `[常量]`。调用者 `0x149048ab0` 在 `0x149048c43 / 0x149048c53 / 0x149048c63` 分别解析 `CanAlwaysAutoStep` / `MovementAbilities` / `OnGround` 三个池，连同 `MoveRequestComponent`、`MaxAutoStepComponent` 传给 worker `0x149048220` `[代码]`。

### 2.1 worker 逐条判定

```
0x149048350  xorps xmm0,xmm0 ; ucomiss xmm0,[r14] ; jae 0x1490484cc
             -> maxAutoStep > 0，否则跳过
0x14904835d  [rbx+0x30] vs [rbx+0x3c] ; [rbx+0x38] vs [rbx+0x44]
             -> mPreCollisionSpeed.x/z 与 mSpeed.x/z 至少一轴不等（水平碰撞），全等则跳过
0x1490483d5  test byte ptr [rcx+rax+4],1 ; jne 0x14904844f
             -> MovementAbilitiesComponent +4 bit0 = isFlying，直接放行
0x1490483dc  xmm1=[rbx+0x34] (pre.y) ; xmm2=[rbx+0x40] (speed.y)
             cmpneqps + cmpltps(xmm1,0) + andps
             -> mPreCollisionSpeed.y < 0 && mSpeed.y != mPreCollisionSpeed.y（本拍下落落地）
0x1490483f8  OnGroundFlagComponent 池存在性检查 -> cl
0x149048440  or cl,al（合并落地拍）
0x149048444  or cl,[rsp+0x2c]（CanAlwaysAutoStepFlagComponent 存在性，0x149048236..0x149048299）
0x14904844a  test cl,1 ; je 0x1490484cc
0x14904844f  emplace AutoStepRequestFlagComponent (0xbaa9695a)
```

等价于（与 JE 1.8.9 的准入同构）`[代码]` `[推断]`：

```cpp
if (maxAutoStep > 0.0f
    && (mPreCollisionSpeed.x != mSpeed.x || mPreCollisionSpeed.z != mSpeed.z)
    && (isFlying                                              // MovementAbilities+4 & 1
        || (mPreCollisionSpeed.y < 0.0f && mSpeed.y != mPreCollisionSpeed.y)
        || has<OnGroundFlagComponent>()
        || has<CanAlwaysAutoStepFlagComponent>()))
    add<AutoStepRequestFlagComponent>();
```

### 2.2 为什么「起跳第 1 拍」和「落地拍」都能进

`OnGroundFlagComponent` 由排在最后的 `FinalizeMoveSystem 0x1463b39a0`（`0x1463b3b5a`、`0x1463b3d3c`、`0x1463b3ea9`）在 tick 末尾按 `mPreCollisionSpeed.y < 0`、`mSpeed.y == 0` 增删 `[代码]`。因此在 `AutoStepFilterSystem` 运行时它仍是**起拍初值** `[推断]`：

- 从地面起跳第 1 拍：`OnGroundFlagComponent == True` → 允许 Step-Assist（§5）；
- 空中下落落地那一拍：`pre.y < 0 && speed.y != pre.y` → 允许固体 Blip（§6）；
- 两拍都不满足时（纯空中撞墙侧面），水平速度被碰撞清零而不会触发上台阶——这正是第 1 轮早撞 `2` 拍出现 `vz = 0.07194900512695312` 的原因 `[实测]`。

---

## 3. `AutoStepSystem` 试探算法与天花板过滤（硬伤 1，`[代码]`）

公共求解器 `0x1482a4890..0x1482a4bc4` 按数组顺序逐轴推进，共有 5 个调用点：`0x1482d4b2a`、`0x1482d4b88`（`0x1482d4710`）、`0x149028438`（`ActorMoveSystem`）、`0x1490498d6`、`0x149049918`（`AutoStepSystem`）`[常量]`。

### 3.1 试探前的天花板盒过滤循环

`0x149049761..0x149049821` 先把 `mCollisionShapes (+0x58)` 整表拷进临时缓冲，随即：

```
0x149049826  movss xmm0, dword ptr [rbx + 0x10]   ; mOriginalAABBShape.max.y (preMaxY)
0x149049840  movss xmm1, dword ptr [r14 + 4]      ; box.min.y
0x149049846  ucomiss xmm1, xmm0
0x149049849  jae 0x149049855                     ; box.min.y >= preMaxY -> 不再保留
0x149049869  ...
0x149049871  jae 0x149049860                     ; 跳过 box.min.y >= preMaxY 的盒
0x149049873  mov rdx,[rcx+0x10] ; movups [r14],...  ; 其余盒紧凑保留（0x18 B/个）
```

即**临时列表只保留 `box.min.y < preMaxY` 的盒**，把位于原头顶上方（`box.min.y >= preMaxY`）的天花板盒临时剔除，让 `[Y↑step, X, Z]` 与 `[Y↓]` 两次求解不会在抬升途中被天花板卡死 `[代码]`。

### 3.2 两次求解与采纳判据（沿用 `r01-003` 主体）

```
0x149049897  (0, stepHeight, 0) / (pre.x, 0, 0) / (0, 0, pre.z)  count=3
0x1490498d6  call 0x1482a4890                      ① Y↑ → X → Z
0x1490498e2  (0, -第一拍实际 dy, 0)  count=1
0x149049918  call 0x1482a4890                      ② 按实际抬升量回落
0x149049aa6  (dx1+dx2)² + (dz1+dz2)²
0x149049ae8  ucomiss xmm6, xmm5 ; jbe 跳过         > mSpeed.x²+mSpeed.z² 才采纳
```

`ActorMoveSystem 0x1490283a0` 构造 `(0,dy,0)/(dx,0,0)/(0,0,dz)` 三步，同样是 **Y→X→Z** `[代码]`。

### 3.3 终态全盒相交复核

采纳前 `0x149049a8b` 重新读**未过滤**的 `[rbx+0x58] / [rbx+0x60]`，`0x149049c2d..0x149049c5a` 逐盒比较候选 AABB 的六个面；任一天花板盒相交就放弃抬升 `[代码]`。成立后 `0x149049b11..0x149049b8f` 写回 `mSpeed (+0x3C/+0x40/+0x44)`、平移缓存盒，并在 `0x149049ba6` 挂 `HasAutoSteppedComponent (0x37c138fa)` `[代码]`。

**实机对照**（`low_ceiling_step_*`，建场后 `block_shapes` 回读天花板与台阶）`[实测]`：

| 头顶净空 | 天花板层 | 台阶 | `max_feet_y` | `max_z` | 结果 |
| --- | --- | --- | --- | --- | --- |
| `1.2` | `-57` | `0.5` | `-59.5` | `53.511646` | 走上台阶 |
| `0.2` | `-58` | `0.5` | `-60.0` | `47.700001` | 被拒，卡在前沿 `47.7` |

两者接触拍同为第 `27` 拍；唯一差别是天花板净空，说明「能否上台阶」由**终态 AABB 是否顶头**决定，而非台阶高度比较本身 `[实测]` `[推断]`。

---

## 4. 步高边界与状态写回（沿用 `r01-002/004/005/007`）

`VariableMaxAutoStepComponent` 默认 `{mBaseValue 0.5625, mControlledValue 1.0625, mJumpPreventedValue 0.5625, mHardcodedClientSide true}`（`.rdata 0x14e83aac8`；`0x142583226` `movabs 0x3f8800003f100000`）；`ClientVariableMaxAutoStepSystem` 回调 `0x1428ce9f0` 按条件选 `+0/+4/+8` 写入 `MaxAutoStepComponent` `[常量]` `[代码]`。`getMaxCollisionVolume 0x14b177650` 在 `0x14b177737` 把步高乘 `1.01f`，候选盒下界压到 `preY − 0.568125`（与 [`notes/bounce-12651.md`](bounce-12651.md) §3 同源）`[代码]` `[常量]`。

三档满宽单盒（`snow_layer height=4` / `stonecutter_block` / `snow_layer height=5`，均 `block_shapes` 回读）`[实测]`：

| 高度 | 走路 `max_feet_y` | 疾跑 `max_feet_y` | 走/跑 `step_index` | 结果 |
| --- | --- | --- | --- | --- |
| `0.5` | `-59.5` | `-59.5` | `27` / `21` | 走上 |
| `0.5625` | `-59.4375` | `-59.4375` | `28` / `21` | 走上 |
| `0.625` | `-60.0` | `-60.0` | — | 挡在 `max_z = 47.700001` |

走上 `0.5625` 台阶那一拍（走路第 `28` 拍）：`z 47.636871337890625 → 47.85272979736328`、`feet_y -60.0 → -59.4375`、`vy 0.0 → 0.5625`、`vz 0.21585845947265625` 不变、`mpd_y` 恒为 `-0.07840000092983246`、`mpd_z = 0.11785905063152313` `[实测]`。疾跑版 `vz = 0.28061676025390625`、`mpd_z = 0.1532159298658371`，`sprint_series` 翻转点 `[0, 52]`（上台阶不断疾跑）；仅 `0.625` 真撞墙时翻转点 `[1, 22]` `[实测]`，与 [`notes/sprint-12651.md`](sprint-12651.md) 的 `5e-5` 主轴判定一致 `[推断]`。

---

## 5. Step-Assist Jump Cancel（硬伤 2，`[实测]` `[代码]`）

`JumpFromGroundSystem` worker `0x1495e9200` 在 `0x1495e936f` 取 `0.42f`，`0x1495e93f6` `maxss xmm0,[r12+0x1c]` 后写 `mPosDelta.y`；疾跑时 `0x1495e940c..0x1495e9478` 按 `SIN_TABLE` 加 `+0.2` 水平冲量 `[代码]`。`+0.42` 写在仍报 `on_ground` 的那一拍，水平结果在下一拍 `[实测]`。

### 5.1 紧贴台阶前沿起跳

起跳点 `start_z = front_face_z − 0.1 = 47.6`（台阶前沿 `47.7`），硬断言 takeoff 拍 `z ≥ front − 0.02`（真的跨过前沿）且 `z ≤ front + 0.45`（未穿墙）`[实测]`：

| 台阶 | impulse 拍 | takeoff 拍 | `takeoff_feet_y` | `takeoff_vy` | `takeoff_vz` | `takeoff_mpd_y` | 后续冲量拍 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `0.5` | `2`（`mpd_y=0.42`） | `3` | `-59.5`（台阶顶） | `0.5` | `0.3274` | `-0.0784` | `[2, 4]` |
| `0.5625` | `1`（`mpd_y=0.42`） | `2` | `-59.4375`（台阶顶） | `0.5625` | `0.32740020751953125` | `-0.07840000092983246` | `[1, 4]` |

即：**起跳拍写入的 `+0.42` 被 Step-Assist 整拍吃掉，玩家不是跳起来而是被直接放到台阶顶面**，垂直位移等于台阶高度（`0.5` / `0.5625`），水平速度保持 `0.32740020751953125`（含疾跑跳 `+0.2`）。`takeoff_on_ground = False`，下一拍落回台阶面（`feet_y = -59.4375`、`on_ground = True`）；`retrigger_on_next_tap = True`，下一次落地拍点按立即产生第二份 `+0.42`，`max_feet_y = -58.247803` / `-58.185303` `[实测]`。

### 5.2 天花板前缘也是 Step-Assist Jump Cancel

`ceiling_front_step_assist`：天花板前缘在 `z = 43`（前沿 `42.7`，玩家起跑 `42.5`），第 `1` 拍写 `mpd_y = 0.41999998688697815`、`mpd_z = 0.20000000298023224`，第 `2` 拍 `z = 42.82740020751953` 已越过前沿 `42.7`、`feet_y = -60.0`、`vy = 0.0`、`vz = 0.32740020751953125`、`mpd_y = -0.07840000092983246` `[实测]`。硬断言确认 `+0.42` 被整拍吃回地面且水平全速保留；后续仍是 `[1, 11]` 的冷却周期冲量，`max_feet_y = -59.799999`、`max_vz = 0.480873` `[实测]`。

### 5.3 与「正上方矮顶截断」区分

`overhead_jump_cancel`（玩家整段处于 `0.2` 净空天花板下、原地起跳 `1` tick）：`max_feet_y = -59.799999`（实际只升 `+0.2`），`impulse_indices = [1]`，`max_mpd_y = 0.42`、`max_mpd_z = 0.0` `[实测]`。这里是**碰撞把 `+0.42` 位移裁成 `+0.2`**；而 §5.1/§5.2 是 **Step-Assist 接受抬升方案后把 `+0.42` 整拍替换掉**，两者机制与数值都不同 `[实测]` `[推断]`。

第 1 轮 `jump_into_step` 把 `0.5` 台阶放在 `z=26`、玩家却在 `z=23.5` 只按 `1` tick 前进，全程只在平地原地跳、根本没有触及台阶前沿；据此得出的「只有头顶天花板会裁掉垂直位移」无效，已随 `r01-006` 撤回 `[实测]`。

---

## 6. 固体 Blip：对齐落地拍后的定量判定（硬伤 3，`[实测]`）

### 6.1 早撞 `2` 拍的假阴性

`solid_blip_early`（复现第 1 轮夹具）：台阶前沿 `44.7`，首次水平接触在第 `11` 拍（比落地早 `2` 拍，`ticks_early = 2`），当时 `feet_y = -59.50480651855469` 仍悬空、`vy = -0.30153656005859375`，既不满足落地拍也不持有 `OnGroundFlagComponent`，水平速度被墙挡成 `vz = 0.07194900512695312`、`mpd_z = 0.0`；第 `12` 拍继续掉到 `-59.87871170043945`（`vz = 0.0`），第 `13` 拍才被抬上 `-59.4375`（`vy = 0.4412117004394531`）`[实测]`。第 1 轮据此写下的「固体 Blip 先挡停、不保速」是**夹具算错落点的假阴性** `[推断]`。

### 6.2 对齐落地同拍

`solid_blip_aligned` 先用无台阶控制弧测出落地拍 `landing_index = 13` 的 `z`，再以 `offset = 0.35417` 微调起步 `z`，把 `0.5625` 台阶前沿放到 `45.7`，硬断言 `contact_index == landing_index == 13`、`contact_z = 45.730003356933594`，且前一拍仍在空中、未穿墙 `[实测]`：

| 量 | 有台阶 | 无台阶控制 |
| --- | --- | --- |
| 落地/接触拍 `vz` | **`0.2523994445800781`** | **`0.2523994445800781`** |
| 该拍 `feet_y` | **`-59.4375`**（台阶顶） | `-60.0` |
| 该拍 `vy` | **`0.4412117004394531`** | — |

前一拍（第 `12` 拍）仍是 `feet_y = -59.87871170043945`、`vy = -0.3739051818847656`、`vz = 0.249359130859375`；接触拍 `mpd_y = 0.41999998688697815`、`mpd_z = 0.42968183755874634`、`on_ground = True` `[实测]`。结论：**基岩版存在保速的固体 Blip**——落地当拍 `mPreCollisionSpeed.y < 0 && mSpeed.y != mPreCollisionSpeed.y` 触发准入，`AutoStepSystem` 在水平碰撞同拍把人从 `-59.87871170043945` 抬到 `-59.4375`，水平速度一格不掉 `[实测]` `[推断]`。对照 `blip_drop_*`（从 `1.63` 格高自由落下）：控制组 `max_feet_y_after = -60.0`、`max_vy_after = 0.0`；有台阶组在落地后第 `9` 拍才以 `max_vy_after = 0.5625` 普通走上台阶 `[实测]`。

---

## 7. Grind 与 `10` tick 跳跃冷却（硬伤 4，`[代码]` `[常量]` `[实测]`）

### 7.1 静态：`MobJumpComponent::mNoJumpDelay`

SDK 头 [`MobJumpComponent.h`](../../refs/levilamina-26.51.7/src/mc/entity/components/MobJumpComponent.h)：`mJumpStartPos +0x00`、`mJumpVelRedux +0x0C`、`mNoJumpDelay +0x10 (int)`，组件哈希 `0xc52c8f78`，池步长 `0x14` `[常量]`。

| 动作 | 位置 | 指令 |
| --- | --- | --- |
| 置 `10` | `MobJumpSystem 0x1495ea890` | `0x1495ea882` / `0x1495ea9ed` `mov dword ptr [rsi+0x10], 0xa` |
| 门控 | 同上 | `0x1495eac38` / `0x1495ead4e` `cmp dword ptr [rsi+0x10], 0 ; jne 跳过` |
| 每拍递减 | `DecrementNoJumpDelaySystem 0x14961b5f0` | `0x14961b7ca` 读 `[pool+idx*20+0x10]`；`0x14961b7db dec ecx`；`0x14961b7dd mov [r8],ecx` |
| 清零 | `ResetNoJumpDelaySystem 0x149621750` 回调 | `0x1495eae70 mov dword ptr [rdx+0x10], 0` |

`ResetNoJumpDelaySystem` 的视图排除 `MobIsJumpingFlagComponent (0x8fd20e98)` 与 `PassengerComponent`；`JumpInputSystem 0x14c5f72c0` 按输入位增删 `MobIsJumpingFlagComponent` `[代码]`。因此**松开跳跃键 → 标志移除 → 该实体进入 reset 视图 → `mNoJumpDelay = 0`**；长按不松则只递减不重置，形成 `10` tick 自动连跳 `[代码]` `[推断]`。[`notes/climb-blocks-12651.md`](climb-blocks-12651.md) §4 已独立记录脚手架/可攀爬分支同样写 `NoJumpDelay = 10` `[常量]`。

### 7.2 长按 vs 点按

点按用 `1` 拍按 / `N` 拍松的固定模式；由于注入路径有约 `±1` tick 的相位抖动，最快的 `1` 拍按 / `1` 拍松模式重复测量 `3` 次并取聚合值，其余模式各测 `1` 次 `[实测]`。

| 模式 | 冲量间隔 | 冲量次数 | `max_mpd_z` | `max_vz` |
| --- | --- | --- | --- | --- |
| 自由疾跑（不跳） | — | — | `0.153217` | `0.280617` |
| 长按不松（`held_jump_grind`，`jump_ticks=24`） | `[10, 10]` | `3` | `0.354788` | `0.482189` |
| 点按 `1/1`（`3` 次聚合） | `3~5` | `35` | **`0.504387`** | **`0.631786`** |
| 点按 `1/2` | `4~6` | `10` | `0.43165` | `0.559052` |
| 点按 `1/3` | `4~7` | `8` | `0.415098` | `0.5425` |
| 点按 `1/4` | `6~8` | `7` | `0.371703` | `0.499104` |
| 点按 `1/9` | `10~12` | `4` | `0.354786` | `0.482185` |

`2` 格矮天花板（`ceiling_y = -58`）下点按 `1/1` 最快 **每 `3` tick 一跳**、水平峰值 `0.631786`，显著高于长按 `0.482189` 与自由疾跑 `0.280617`；且点按越密峰值越高 `[实测]`。单拍细节（`1/1` 第 `0` 次重复）：第 `1` 拍写 `mpd_y = 0.41999998688697815`、`mpd_z = 0.20000000298023224`，第 `2` 拍升到 `feet_y = -59.79999923706055`（`+0.2`），第 `3` 拍回落 `-59.87839889526367`，第 `4` 拍落地 `-60.0`——`3` tick 就是 `+0.2` 上升加 `0.0784 + 0.1216` 下落的物理下限 `[实测]`。第 1 轮 `r01-006` 把长按的 `10` tick 冷却当成 Grind 极限，是测法错误 `[推断]`。

### 7.3 天花板前缘与台阶阶梯

| 场景 | 冲量间隔 | 冲量次数 | `max_mpd_z` | `max_vz` |
| --- | --- | --- | --- | --- |
| 连续天花板前缘（点按 `1/2`） | `3~6` | `10` | `0.48012` | `0.607521` |
| 连续 `0.5` 台阶阶梯（点按 `1/2`） | `12~15` | `3` | `0.385822` | `0.513222` |

台阶阶梯未连锁 Jump Cancel：无天花板的阶梯上玩家直接完整起跳越过台阶（`max_feet_y = -58.247803`），点按大多落在 `2` 拍的接地窗口之外，间隔 `12~15` tick `[实测]` `[推断]`。

---

## 8. 与 Java 版对照（`[推断]`）

| 维度 | Java 1.8.9 | 基岩版 `1.26.51.01` |
| --- | --- | --- |
| 自动步高 | `0.6` | **`0.5625`**（`0.5`/`0.5625` 可上、`0.625` 挡住） |
| 上台阶准入 | `onGround` / 落地拍 / `isFlying` 等 | **同构**：`maxAutoStep > 0` + 水平碰撞 + (`isFlying` / 落地拍 / `OnGroundFlagComponent` / `CanAlwaysAutoStepFlagComponent`) |
| 试探轴序 / 采纳 | 分轴 `Y→X→Z`，比较水平距离 | **`Y→X→Z`**；上台阶路径 `Y↑→X→Z` 再 `Y↓` 回落，`dx²+dz²` 严格更优才采纳；试探前临时滤天花板盒、终点再全盒复核 |
| 状态写回 | 改写 velocity / `onGround` | 改 `MoveRequestComponent::mSpeed` + 缓存碰撞盒 + `HasAutoSteppedComponent`；**不动 `mPosDelta`** |
| 固体 Blip | 存在（保速、落地拍触发） | **存在**：对齐落地拍后 `vz` 与控制组一致（`0.2523994445800781`），一步抬 `0.4412117004394531` |
| Jump Cancel | 存在（Step-Assist 吃掉起跳） | **存在**：贴前沿起跳 `+0.42` 被吃掉、直接放上台阶顶；正上方矮顶则是 `+0.42 → +0.2` 截断 |
| Grind / 连跳 | 点按可快于长按；长按有自动连跳冷却 | **同族**：`MobJumpComponent::mNoJumpDelay = 10` 仅长按生效，松开清零；点按最快 `3` tick 一跳、`max_vz = 0.631786` |
| Ceiling Blip | 存在 | **天花板前缘是 Step-Assist Jump Cancel**（吃掉 `+0.42`、全速钻入），非额外抬升 |

> Java 侧为机制层面归纳，未在本机 Java 客户端复测，不写成本版本 `[实测]`。

---

## 9. 撤回记录

- `r01-006`「`1.26.51.01` 无 JE 1.8 式固体 Blip / Ceiling Blip；Jump Cancel 把 `+0.42` 裁成 `+0.2`；Grind 是 `10` tick 一次」**整体撤回**：三处结论分别被 §6.2（保速固体 Blip）、§5（Step-Assist Jump Cancel）、§7.2（点按 `3` tick 周期）证伪；根因是落地拍对齐错误、`jump_into_step` 未触及台阶、只测长按空格，以及漏读 `AutoStepFilterSystem` 准入与 `mNoJumpDelay` 清零 `[实测]` `[代码]`。
- 保留复用：`r01-001`（注册顺序与诊断标签）、`r01-002`（可变步高默认值与 `1.01` 盒）、`r01-003` 主体（补入 §3.1/§3.3）、`r01-004`（不改写 `mPosDelta`）、`r01-005`（步高边界）、`r01-007`（能走上的台阶不断疾跑）。

---

## 10. 未闭合项

1. `ClientVariableMaxAutoStepSystem` 回调 `0x1428ce9f0` 的 `+0/+4/+8` 三值选择分支完整语义（骑乘、跳跃被阻止、`0x40000000` 方块属性等）未逐条确认；`mControlledValue = 1.0625` 实机触发条件未测。
2. `0x1482d4710`（求解器另一消费者，vtable `0x14ea1a4c0`）的所属系统名未定位。
3. `0.5625~0.625` 之间未做更细高度扫描；`getMaxCollisionVolume` 的 `1.01` 扩展与采纳比较的严格浮点边界仍是 `[推断]`。
4. 点按冲量受注入路径约 `±1` tick 相位抖动影响，`3` tick 为多次聚合观测到的下限；「落地拍重触发」的逐指令时序未展开。
5. 台阶阶梯未能连锁 Jump Cancel；是否存在其它几何（连续天花板前缘 + 台阶组合）能稳定连锁未覆盖。
6. 鞘翅、载具乘客、潜行、水中/游泳姿态下的自动步高与 Blip 未覆盖。

---

## 11. 复现

```bash
py -3.11 driver/dev.py run --no-build --world "超平坦测试" --background --allow allow_input_inject=1 --allow allow_mem_read=1
py -3.11 driver/dev.py exec "py -3.11 skills/bedrock-re/findings/1.26.51.01/cases/stepping-12651/probes/verify_stepping.py"
```

脚本内置硬断言：建场后 `blocks`/`block_shapes` 回读方块状态与真实碰撞高度、落地/静止/清疾跑前提、接触前沿与起跳跨拍、落地拍对齐（`contact_index == landing_index`）、天花板净空、`mPosDelta` watch 解码、点按期间疾跑不掉；任何穿墙、离地、未触及台阶或未按预期抬升的场景直接抛错。最快的点按模式重复 `3` 次取聚合。原始数据：`out/stepping-1.26.51.01.json`。
