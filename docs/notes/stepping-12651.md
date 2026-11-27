# 上台阶系统、Step-Assist Jump Cancel、固体 Blip 与 Grind（1.26.51.01）— 返工 v3

> 版本：`Minecraft.Windows.exe` `1.26.51.01`（LeviLamina `26.51.7`，`image_base = 0x140000000`）。
> 证据约定：`[代码]` 反汇编/调用链；`[常量]` `.rdata` 浮点、组件 ID、SDK 结构布局；`[实测]` LeviPilot 逐 tick 录制与 ECS 回读；`[推断]` 由直接证据补出的推导。
> 复现脚本：[`verify_stepping.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/stepping-12651/probes/verify_stepping.py)（第 1/2 轮）与 [`verify_stepping_tail.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/stepping-12651/probes/verify_stepping_tail.py)（第 3 轮尾项）。原始数据：gitignored `out/stepping-1.26.51.01.json` / `out/stepping-tail-1.26.51.01.json`；第 3 轮数字逐位取自后者（账本 `r03-016`，`exit: 0`）。通俗版 [`stepping-wiki.md`](../stepping-wiki.md) 按 2~5 位有效数字修约。
> 相位口径：录制 `vx/vy/vz` 是本拍**已完成位移**；watch `+0x18/+0x1C/+0x20` 是 `StateVectorComponent::mPosDelta`（待执行位移）；`+0.42` 起跳冲量写在**最后一个仍报 `on_ground` 的拍**，水平跨台阶/抬升结果出现在**下一拍**；`feet_y` 是脚底。参见 [`AGENTS.md`](../../AGENTS.md)。
> 数值口径：`1.0625`、`0.568125` 等是 `.rdata` 常量或算术推导，不属于逐 tick 实测数字；`[实测]` 数值逐位取自最终 `exit: 0` 的 JSON。第 3 轮跑场基准：自然地表 `ground = -60.0`、`layer = -61`；绝对坐标随跑场整体平移。
> 返工说明：第 1 轮 `r01-006`「无固体 Blip / 无 Jump Cancel / Grind 为 10 tick 周期」是夹具算错落点 + 只测长按空格 + 漏读静态准入造成的假阴性，已在第 2 轮以 `erratum_of` 撤回（见 §9）。第 3 轮进一步证伪 §8 的「BE 可穿檐跨上无顶台阶」推断，并把可变步高三值、求解器第二消费者与点按 `3` tick 逐拍时序逐条闭合。

---

## 0. 结论摘要（v3）

1. **注册顺序**（沿用 `r01-001`）：同一函数 `0x14681ac20..0x14681c6f8` 内 `MoveCollisionSystem 0x14681aef5` → `ActorMoveSystem 0x14681b173` → `AutoStepFilterSystem 0x14681b28f` → `AutoStepSystem 0x14681b319` → `ClientAcceptanceSystem 0x1482a4d00`（`0x14681b78b`）→ `FinalizeMoveSystem 0x14681b7eb`；顶层 `0x146822800..0x146829404` 中跳跃/疾跑/移速组都先于碰撞组 `[代码]` `[常量]`。`1/2 AutoStepSystem` 只是 `0x149049700` 内的 NaN 诊断标签 `[代码]`。
2. **完整准入**（硬伤 1）：`AutoStepFilterSystem 0x149048220` 要求 `maxAutoStep > 0`、发生水平碰撞，且 `isFlying` / **落地当拍** / **`OnGroundFlagComponent`** / `CanAlwaysAutoStepFlagComponent` 四者之一成立才挂 `AutoStepRequestFlagComponent`。由于 `OnGroundFlagComponent` 直到末尾 `FinalizeMoveSystem` 才更新，**从地面起跳的第 1 拍仍持有起拍初值**——这正是 Step-Assist Jump Cancel 与固体 Blip 的准入来源 `[代码]` `[推断]`。
3. **滤天花板盒**（硬伤 1）：`AutoStepSystem 0x149049826..0x149049888` 在两次求解前，把临时碰撞盒列表里 `box.min.y >= mOriginalAABBShape.max.y` 的盒（原头顶上方的天花板）**临时删掉**；试探完成、算出候选终点后，`0x149049a8b..0x149049c60` 再对**未过滤的完整列表**做终态 AABB 相交复核 `[代码]`。低顶走台阶对照实测：`1.2` 净空可上 `0.5` 台阶，`0.2` 净空被拒（`max_z = 47.700001`）`[实测]`。
4. **步高边界**（沿用 `r01-005`）：`0.5` / `0.5625` 可平走上去，`0.59375`（横置铁链）与 `0.625` 挡在前沿；上台阶拍只改 `MoveRequestComponent::mSpeed`，`mPosDelta` 不动（`mpd_y = -0.07840000092983246`），水平速度不减 `[实测]` `[代码]`。
5. **Step-Assist Jump Cancel**（硬伤 2）：紧贴 `0.5` / `0.5625` 台阶前沿起跳，第 `1` 拍写入 `+0.42`，**下一拍**直接被放到台阶顶面（`feet_y = -59.5` / `-59.4375`，`vy = 0.5` / `0.5625`）、`mpd_y` 回到 `-0.07840000092983246`、`vz = 0.32740020751953125` 全速保留；下一次落地拍点按可立即重跳 `[实测]`。天花板前缘同样吃掉 `+0.42` 并全速钻进矮顶下 `[实测]`。
6. **固体 Blip 成立**（硬伤 3）：把首次水平碰撞对齐到**落地同一拍**后，`vz = 0.2523994445800781` 与控制组落地速度 `0.2523994445800781` **完全一致**、速度不丢，且当拍从 `-59.87871170043945` 一步抬到 `-59.4375`（`vy = 0.4412117004394531`）；第 1 轮提前 `2` 拍撞墙，才误得「先挡停、不保速」`[实测]`。
7. **Grind 的真实节拍**（硬伤 4）：`10` tick 只是长按空格时 `MobJumpComponent::mNoJumpDelay` 的自动连跳冷却；**松开跳跃键会清空该计数**。点按 `1` 拍按 / `1` 拍松最快可达 **每 `3` tick 一跳**（`35` 次冲量中间隔 `3~5` tick）、`max_vz = 0.631786`、`max_mpd_z = 0.504387`；长按仅 `0.482189`。台阶阶梯点按未能连锁 Jump Cancel（间隔 `12~15` tick）`[代码]` `[实测]`。
8. **可变步高三值**（第 3 轮）：`ClientVariableMaxAutoStepSystem` 回调 `0x1428ce9f0` 只在三种来源间选值——默认 `mBaseValue`，`mJumpPreventedValue`（+8，与 base 同为 `0.5625`）在“本地受控 + 脚下方块 `PreventsJumping`（`0x40000000`，或脚下方块为地毯 `0x80`/脚底恰在方块边界时其下一格 `PreventsJumping`）”时生效，`mControlledValue`（+4，`1.0625`）在“有 `VehicleComponent` 且乘客数 > `ActorDataControllingSeatIndex` 且 `ActorDataFlag` bit44 `WasdControlled`”时生效 `[代码]` `[常量]`。
9. **求解器第二消费者**（第 3 轮）：`0x1482d4710` 属于 **`ClientAcceptanceSystem`**（vtable `0x14ea1a4c0`，slot[5] `0x1482d4fc0` / slot[6] `0x1482d5330`），由 0x14681b78b 在 AutoStepSystem 之后、FinalizeMoveSystem 之前注册；它不是上台阶，而是把服务端 `ServerPlayerCurrentMovementComponent` 的权威位置与客户端预测位置的偏差，用同一求解器按“先竖直 `count=1`、再水平 `count=2`”重新夹到碰撞几何上（服务端权威移动的收回/接受，`ClientAcceptanceThresholdsComponent` 提供阈值与冷却预算）`[代码]` `[常量]`。
10. **`0.5625` 是精确边界**（第 3 轮）：`0.5` / `0.5625` 平走可上；新增的 **`0.59375`（横置铁链）**——介于 `0.5625` 与 `0.625` 之间的真实碰撞顶面——走路与疾跑都被挡在前沿，`0.625` 同样被挡；`getMaxCollisionVolume` 的 `1.01` 只把向下搜索下界扩展到 `0.568125`，抬升数组的 Y 分量仍恰为 `maxAutoStep`（`0.5625`），采纳比较 `dx²+dz² > mSpeed.x²+mSpeed.z²` 为严格 `>` `[代码]` `[实测]`。
11. **穿檐推断撤回**（第 3 轮）：原 §8 推断「矮天花板下向外跨无顶台阶时 BE 因试探前预删原头顶盒而可穿檐走上」**不成立**。终态复核遍历的是**未过滤**的完整 `mCollisionShapes`，候选 AABB 仍与身后矮顶在水平方向重叠时一票否决：矮顶直接压在台阶前缘时被拒（`max_z` 恰为前沿 `1413.699951`），把矮顶提前一格结束、让候选 AABB 完全离开后才可上（`max_feet_y = -59.5`）`[代码]` `[实测]`。
12. **点按 `3` tick 的逐拍序列**（第 3 轮）：最快一对冲量相隔 `3` tick，逐行是 impulse 拍 `mpd_y=0.42`、`feet=-60` → 下一拍 `feet=-59.799999`（`+0.2`）→ 再下一拍 `feet=-59.878399`（`-0.0784`，`mpd_y=-0.155232`）→ 落地拍 `feet=-60`（`-0.1216`）且同拍再次写入 `+0.42`；注册顺序 `JumpInputSystem → DecrementNoJumpDelay → MobJumpSystem → JumpFromGroundSystem → ResetNoJumpDelay` 保证落地拍松键清冷却、同拍/下一拍即可重触发 `[代码]` `[实测]`。
13. **姿态与组合几何**（第 3 轮）：潜行与创造飞行都**不关闭**自动步高——潜行可走上 `0.5625`、被 `0.625` 挡住；飞行（`flying=true`）可走上 `0.5625`。把 `0.5` 台阶、空档与 `2` 格矮顶前缘按 `6` 格周期组合后，点按 `1/2` 能产生 `10` 次冲量、最短间隔 `4` tick（纯台阶阶梯为 `10~14` tick、纯矮顶前缘为 `4~6` tick），属于可连锁但不如纯矮顶稳定 `[实测]`。

---

## 1. 系统注册与管线顺序（`[代码]` `[常量]`）

| 系统 | 注册点（名字字面量） | vtable | slot[5] / slot[6] |
| --- | --- | --- | --- |
| `MoveCollisionSystem` | `0x14681aef5` | `0x14e871920`（见 [`notes/speed-limit-12651.md`](speed-limit-12651.md)） | `0x1431dc500` / `0x1431dd080` |
| `ActorMoveSystem` | `0x14681b173`（`0x14ec3555a`） | `0x14ea50160` | `0x149028740` / `0x149028c70` |
| `AutoStepFilterSystem` | `0x14681b28f`（`0x14ec33d59`） | `0x14ea508f0` | `0x1490487a0` / `0x149048ab0` |
| `AutoStepSystem` | `0x14681b319`（`0x14ec342cf`） | `0x14ea50930` | `0x149049f80` / `0x14904abb0` |
| `ClientAcceptanceSystem` | `0x14681b78b` → `0x1482a4d00`（同函数还注册 `SetPlayerAcceptanceCooldown` / `SetPlayerPassengerAcceptanceCooldown`） | `0x14ea1a4c0` | `0x1482d4fc0` / `0x1482d5330` |
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
0x149048444  or cl,[rsp+0x2c]（CanAlwaysAutoStepFlagComponent 存在性，0x1490482bf..0x14904834a 读取后当拍移除该一次性标志）
0x14904844a  test cl,1 ; je 0x1490484cc
0x14904844f  emplace AutoStepRequestFlagComponent (0xbaa9695a)
```

等价于（与 JE 1.8.9 / 1.12.2 的准入同构）`[代码]` `[推断]`：

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

`OnGroundFlagComponent` 由排在最后的 `FinalizeMoveSystem 0x1463b39a0` 在 tick 末尾按垂直碰撞更新 `[代码]`：

- `0x1463b3cc7..0x1463b3e1d`：以 `|mPreCollisionSpeed.y - mSpeed.y| > FLT_EPSILON`（`1.19209e-7f`）判定并增删 `VerticalCollisionFlagComponent (0xc6a02a9a)`；
- `0x1463b3e23..0x1463b3fbc`：仅当 `VerticalCollision && mPreCollisionSpeed.y < 0` 时保留/写入 `OnGroundFlagComponent (0xc29078a0)`（若 `!VerticalCollision` 且 `mPreCollisionSpeed.y == 0` 则保留原值）；一旦发生 `mPreCollisionSpeed.y >= 0` 的垂直碰撞（例如 Step-Assist 吃掉 `+0.42` 起跳并改写成 `+0.5` / `+0.5625` / `0.0`），即移除 `OnGroundFlagComponent` 并清零后续竖直速度（`takeoff_on_ground = False`、`mpd_y = -0.0784`）`[代码]` `[推断]`。

因此在 `AutoStepFilterSystem` 运行时 `OnGroundFlagComponent` 仍是**起拍初值** `[推断]`：

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

**第 3 轮低檐前缘对照（`low_roof_*`）**：天花板固定为 `0.2` 净空（层 `-58`），`0.5` 台阶紧贴其前缘；`no_gap` 让矮顶的最后一格正好压在台阶前缘（`z0-1`），`with_gap` 让矮顶提前一格结束（到 `z0-2`），其余完全相同 `[实测]`：

| 几何 | 接触拍 | `max_feet_y` | `max_z` | 结果 |
| --- | --- | --- | --- | --- |
| 矮顶压在前缘（`no_gap`） | `22` | `-60.0` | `1413.699951`（前沿 `1413.7`） | **被拒，卡在前沿** |
| 矮顶提前一格（`with_gap`） | `23` | `-59.5` | `1423.065918` | **走上 `0.5` 台阶** |

两者唯一差别是候选 AABB 在终点是否还与矮顶盒水平重叠：`no_gap` 的候选盒后半仍与身后矮顶水平重叠（头顶比矮顶低 `0.2`、抬升后会穿入），终态复核一票否决；`with_gap` 的候选盒已完全离开矮顶（`min.z > 矮顶 max.z`），于是通过。**这直接否定了 §8 的“BE 可穿檐跨上无顶台阶”推断**：预删只影响试探，终点仍用未过滤全表复核 `[代码]` `[实测]`。

---

### 3.4 求解器的第二消费者：`ClientAcceptanceSystem`（第 3 轮）

`0x1482d4710` 不是另一个上台阶系统，而是 **`ClientAcceptanceSystem`** 的 per-entity worker：

- **名字与注册**：`0x1482a4d00` 依次注册 `SetPlayerAcceptanceCooldown`、`SetPlayerPassengerAcceptanceCooldown`、`ClientAcceptanceSystem`（三条名字字面量 + vtable `0x14ea1a440/0x14ea1a480/0x14ea1a4c0`），由移动/碰撞注册函数 `0x14681ac20..0x14681c6f8` 在 `0x14681b78b` 调用，位于 `AutoStepSystem` 之后、`FinalizeMoveSystem`（`0x14681b7eb`）之前 `[代码]` `[常量]`。
- **成员**：vtable `0x14ea1a4c0` 的 slot[5] = `0x1482d4fc0`、slot[6] = `0x1482d5330`，两者都把 worker `0x1482d4710` 作为回调传给视图；worker 的视图解析 `ServerPlayerCurrentMovementComponent (0xaf78dfa4)` 与 `ClientAcceptanceThresholdsComponent (0x759e3440)`，另取 `StateVectorComponent`（位置）、`AABBShapeComponent`（候选盒）、`MoveRequestComponent`（`mSpeed`/`mPreCollisionSpeed`/`mCollisionShapes`/`mPreviousHorizontalDelta`）`[代码]` `[常量]`。
- **它做什么**：用服务端 `ServerPlayerCurrentMovementComponent` 里的权威位置（`+56/+60/+64`）减去客户端 `StateVector` 位置与 `mSpeed`，得到三轴偏差；超过 `ClientAcceptanceThresholdsComponent` 的阈值（`mCurrentCooldown` 作为每拍预算）时，把偏差夹到当前碰撞几何上——**先竖直 `count=1`、再水平 `count=2`** 两次调用同一个 `0x1482a4890`，写回 `mSpeed`/候选 AABB，并递减 `mCurrentServerInitiatedMotionCooldown`。它是服务端权威移动的“收回/接受”修正，没有 `Y↑step` 试探、不读 `MaxAutoStep`、也不会上台阶 `[代码]` `[推断]`。
- **与 `AutoStepSystem` 的职责差异**：`AutoStepSystem` 是“撞到障碍后尝试抬升并采纳更远的水平位移”；`ClientAcceptanceSystem` 是“把客户端位置/速度拉回服务端已接受的位置”，只在有服务端修正帧时生效（单机不受影响）`[代码]`。

> arg→组件映射由 `0x1482d5330` 的视图解析与结构偏移/stride 补出，标 `[推断]`；控制流与组件哈希是 `[代码]` `[常量]`。



## 4. 步高边界、三值选择与状态写回（沿 `r01-002/004/005/007`；第 3 轮扩展）

### 4.1 可变步高的三值语义（第 3 轮，`[代码]` `[常量]` `[推断]`）

`VariableMaxAutoStepComponent` 默认 `{mBaseValue 0.5625, mControlledValue 1.0625, mJumpPreventedValue 0.5625, mHardcodedClientSide true}`（`.rdata 0x14e83aac8`：`0x3f100000/0x3f880000/0x3f100000`；`0x142583226` `movabs 0x3f8800003f100000`）。派发器 `0x1428d2480` 先看实体有没有 `VariableMaxAutoStepComponent`：没有且 `mHardcodedClientSide` 为真时用 `.rdata` 默认值，没有且为假时直接把 `MaxAutoStepComponent` 写 `0`，有则进 worker `0x1428ce9f0` `[代码]` `[常量]`。

worker 的输入（组件身份由 `0x1428D2280` 的依赖表与偏移/stride 补出，映射标 `[推断]`，控制流为 `[代码]`）：

| 形参 | 组件/量 | 用途 |
| --- | --- | --- |
| `a5` | `VariableMaxAutoStepComponent` | `+0` base、`+4` controlled、`+8` jumpPrevented、`+12` hardcoded |
| `a7` | `StateVectorComponent` | 脚部位置 `(x,y,z)`，用于 `floor` 取方块坐标 |
| `a6` | `AABBShapeComponent` | `+4` = AABB `min.y`（脚底）；判定“脚底恰在方块边界” |
| `a8` | `ActorDataFlagComponent` | `(flags >> 42) & 4` = bit44 `ActorFlags::WasdControlled` |
| `a9` | `ActorDataControllingSeatIndexComponent` | 控制座位号，与乘客数比较 |
| `a11` | `LocalConstBlockSourceFactoryComponent`/方块源 | 取脚下方块与下方一格方块 |
| `a10` | `MaxAutoStepComponent` | 输出选中的步高 |

判定顺序（`0x1428ce9f0`）：

```cpp
if (has<ControlledByLocalInstanceComponent>()
    && (blockAt(floor(pos)).has(PreventsJumping)                     // 0x40000000
        || ((blockAt(floor(pos)).has(Carpet)                         // 0x80
             || floor(pos.y) >= aabb.min.y)                          // 脚底恰在方块边界
            && blockBelow.has(PreventsJumping))))
    out = jumpPrevented;                                             // +8 = 0.5625
else if (has<VehicleComponent>()
         && passengers.size() > controllingSeatIndex
         && actorFlags.bit44 /* WasdControlled */)
    out = controlled;                                                // +4 = 1.0625
else
    out = base;                                                      // +0 = 0.5625
*maxAutoStep = out;
```

- `mJumpPreventedValue` 与 base 同为 `0.5625`，对玩家单独观察不到差异；它真正的作用是**压掉坐骑的 `1.0625`**——本地受控的坐骑站上蜂蜜块等 `PreventsJumping` 方块时回到 `0.5625` `[代码]` `[推断]`。
- `mControlledValue = 1.0625` 只在“有乘客且控制座位有效、`WasdControlled` 置位”的载具实体上生效（典型是被玩家骑乘的马）；未骑乘或无人控制的载具仍是 base `[代码]` `[推断]`。第 3 轮尝试用 `/ride` 直接上马做实测，`ride @s ride/mount/...` 全部被拒（未驯服/未装鞍的 summoned horse），故该分支仍**只有静态闭合**，如实记为 §10 边界。

### 4.2 精确边界：`0.5625`、`0.59375` 与 `1.01` 的方向（第 3 轮，`[代码]` `[实测]`）

`getMaxCollisionVolume 0x14b177650` 在 `0x14b177737` 取 `v19 = stepHeight * 1.01f`，只把候选盒的**向下**搜索下界压低（`preY − 0.568125`，与 [`notes/bounce-12651.md`](bounce-12651.md) §3 同源）；`AutoStepSystem` 的抬升数组在 `0x149049897` 写入的 Y 分量是 `MaxAutoStepComponent` 原值（`xmm8`），没有再乘 `1.01`；采纳比较 `0x149049aa6..0x149049aeb` 是 `dx² + dz² > mSpeed.x² + mSpeed.z²` 的严格 `ucomiss/jbe`。因此有效上限就是 `maxAutoStep = 0.5625`，`1.01` 只负责把“刚好在脚下”的方块纳入候选表 `[代码]` `[常量]`。

第 3 轮用真实碰撞顶面扫描 `0.5625` 与 `0.625` 之间：满宽/单盒方块没有落在这个区间的顶面，唯一可用的是**横置铁链**（`pillar_axis=x`，碰撞 `y:[0.40625,0.59375]`、`x` 满宽 `1.0`、`z` 厚 `0.1875`，`add_ret=true`），其顶面 `0.59375` 比步高高 `0.03125`：

| 台阶方块 | 碰撞顶面 | 走路 `max_feet_y` | 疾跑 `max_feet_y` | `max_z` / 前沿 | 结果 |
| --- | --- | --- | --- | --- | --- |
| `snow_layer height=4` | `0.5` | `-59.5` | — | `1423.311279` / `1413.7` | 走上 |
| `stonecutter_block` | `0.5625` | `-59.4375` | — | `1423.311279` / `1413.7` | 走上 |
| `iron_chain`（横置） | **`0.59375`** | **`-60.0`** | **`-60.0`** | `1414.106201` / `1414.10625` | **挡在前沿** |
| `snow_layer height=5` | `0.625` | `-60.0` | — | `1413.699951` / `1413.7` | 挡在前沿 |

四例的 `contact_index` 均为 `9`；`0.59375` 的走路/疾跑都停在 `1414.106201`（前沿 `1414.10625`，误差在 `5e-5` 内），说明疾跑不会“撞过去”，边界不是速度问题 `[实测]`。所有高度均由 `block_shapes` 回读（`collision.max.y − 方块 y`），不是按方块名推断。

第 2 轮的代表性写回拍（走路第 `28` 拍，跑场前沿 `47.7`）：`z 47.636871337890625 → 47.85272979736328`、`feet_y -60.0 → -59.4375`、`vy 0.0 → 0.5625`、`vz 0.21585845947265625` 不变、`mpd_y` 恒为 `-0.07840000092983246`、`mpd_z = 0.11785905063152313` `[实测]`。疾跑版 `vz = 0.28061676025390625`、`mpd_z = 0.1532159298658371`，`sprint_series` 翻转点 `[0, 52]`（上台阶不断疾跑）；仅 `0.625` 真撞墙时翻转点 `[1, 22]` `[实测]`，与 [`notes/sprint-12651.md`](sprint-12651.md) 的 `5e-5` 主轴判定一致 `[推断]`。

**姿态对照（第 3 轮，`r03-016`）**：潜行（`input.sneak = True`、`sneaking = True`、`head_y − feet_y = 1.2137603759765625`）时自动步高**仍然生效**——`0.5625` 台阶走上（`max_feet_y = -59.4375`、`max_z = 1416.941528`），`0.625` 被挡（`max_feet_y = -60.0`、`max_z = 1413.699951`）；创造飞行（`flying = True`）同样——平飞控制组 `max_feet_y = -60.0`、`max_z = 1429.205078`，飞向 `0.5625` 台阶时 `max_feet_y = -59.4375`、`max_z = 1429.148682`，说明 `AutoStepFilterSystem` 的 `isFlying` 分支确实把飞行状态放进准入且抬升照常 `[实测]` `[代码]`。潜行不改变 `MaxAutoStepComponent`，只是把移动速度降到 `min(0.3 + 0.15 × SwiftSneak, 1.0)` 倍（见 [`notes/sneaking-crawling-12651.md`](sneaking-crawling-12651.md)），所以边界仍是 `0.5625` `[推断]`。

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

SDK 头 `MobJumpComponent.h`（本地 `refs/levilamina-26.51.7/src/mc/entity/components/MobJumpComponent.h`）：`mJumpStartPos +0x00`、`mJumpVelRedux +0x0C`、`mNoJumpDelay +0x10 (int)`，组件哈希 `0xc52c8f78`，池步长 `0x14` `[常量]`。

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

**第 3 轮逐行对质（最终 `r03-016`，`tap_three_tick`）**：3 次重复共 `34` 次冲量、间隔 `3~5`，最短一对间隔 `3`，逐行 `[实测]`：

| 行 | tick | `feet_y` | `vy` | `mpd_y` | `on_ground` | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| impulse | `17819` | `-60.0` | `0.0` | `0.41999998688697815` | `True` | 起跳拍写 `+0.42` |
| rise | `17820` | `-59.79999923706055` | `0.20000076293945312` | `-0.07840000092983246` | `False` | 顶头裁到 `+0.2`（已完成） |
| fall | `17821` | `-59.87839889526367` | `-0.078399658203125` | `-0.1552319973707199` | `False` | 第一拍下落 `-0.0784` |
| landing | `17822` | `-60.0` | `-0.12160110473632812` | `0.41999998688697815` | `True` | 第二拍下落 `-0.1216` 落地，**同拍再次写 `+0.42`** |

静态顺序解释了“为什么落地拍能立刻重触发”：同一顶层注册函数 `0x146822800..0x146829404` 内的先后是 `0x1468203da`（`JumpInputSystem`）→ `0x146823b8d`（`DecrementNoJumpDelaySystem`）→ `0x146823ce0`（`MobJumpSystem`）→ `0x146823ebf`（`JumpFromGroundSystem`）→ `0x14682414e`（`ResetNoJumpDelaySystem`）`[代码]` `[常量]`。`JumpInputSystem 0x14c5f72c0` 按输入位增删 `MobIsJumpingFlagComponent (0x8fd20e98)`；`ResetNoJumpDelaySystem 0x149621750` 的视图排除该标志，回调 `0x1495eae70` 把 `mNoJumpDelay (+0x10)` 清零；`DecrementNoJumpDelaySystem 0x14961b5f0` 每拍 `dec` 一次；`MobJumpSystem` 只在 `mNoJumpDelay == 0` 时放行并置 `10`。因此“按 `1` 拍 / 松 `1` 拍”时，松键拍先清 `MobIsJumpingFlag`、同拍 Reset 把冷却清零，落地拍 `MobJumpSystem` 看到 `0` 即放行 `[代码]` `[推断]`。

`3` tick 下限的物理链：起跳拍 `T` 由 `JumpFromGroundSystem` 写 `mPosDelta.y = +0.42`，`0x1482a4890` 在 Y 轴扫描时用 `sub_143327D80` 把 `+0.42` 夹到矮顶下的 `+0.2`；`T+1` 重力写 `-0.0784`、`T+2` 写 `-0.1216`，两拍恰好落回地面；落地发生在 `T+2` 的移动阶段，`FinalizeMoveSystem` 在 tick 末尾才置 `OnGroundFlag`，所以第一个能再次起跳的 tick 是 `T+3`——冲量间隔 `3` 既是物理下限，也是“落地拍同拍重触发”的行语义 `[代码]` `[实测]` `[推断]`。

### 7.3 天花板前缘与台阶阶梯

| 场景 | 冲量间隔 | 冲量次数 | `max_mpd_z` | `max_vz` |
| --- | --- | --- | --- | --- |
| 连续天花板前缘（点按 `1/2`） | `3~6` | `10` | `0.48012` | `0.607521` |
| 连续 `0.5` 台阶阶梯（点按 `1/2`） | `12~15` | `3` | `0.385822` | `0.513222` |
| **`0.5` 台阶 + 空档 + `2` 格矮顶前缘（周期 `6`，点按 `1/2`）** | **`4~14`** | **`10`** | — | — |

台阶阶梯未连锁 Jump Cancel：无天花板的阶梯上玩家直接完整起跳越过台阶（`max_feet_y = -58.247803`），点按大多落在 `2` 拍的接地窗口之外，间隔 `12~15` tick `[实测]` `[推断]`。

第 3 轮在同一场地重新跑了三种几何的点按 `1/2` 对照 `[实测]`：纯矮顶前缘间隔 `4~6`、`9` 次冲量；纯 `0.5` 阶梯间隔 `10~14`、`4` 次冲量；**组合几何**（`0.5` 台阶两格 → 一格空档 → 两格矮顶前缘 → 一格空档，周期 `6`）间隔 `4~14`、`10` 次冲量、`max_z = 1430.219727`（从 `1414` 起推进约 `16` 格）。组合能产生比纯阶梯更多的 Jump Cancel 冲量（矮顶前缘仍按 `2` 拍窗口吃跳），但会被台阶与空档打断，最短 `4` tick、最长 `14` tick，**不是**纯矮顶那种稳定 `3~6` tick 连锁 `[实测]` `[推断]`。

---

## 8. 与 Java 版对照（`[代码]` `[实测]` `[推断]`）

| 维度 | Java `1.12.2`（`Entity#move`） | 基岩版 `1.26.51.01` |
| --- | --- | --- |
| 主移动轴序 | **Y → X → Z** | **Y → X → Z**（`ActorMoveSystem 0x1490283a0`） |
| 自动步高 | **`0.6F`** | **`0.5625F`**（`0.5`/`0.5625` 可上、`0.625` 挡住；`0.5625 < h ≤ 0.6` 组合落差仅 JE 可上） |
| 上台阶准入 | `stepHeight > 0 && (onGround \|\| (origY != y && origY < 0)) && (origX != x \|\| origZ != z)` | **同构**：`maxAutoStep > 0` + 水平碰撞 + (`isFlying` / 落地拍 / `OnGroundFlagComponent` / 一次性 `CanAlwaysAutoStepFlagComponent`) |
| 碰撞盒收集 | 进分支后用 `expand(origX, stepHeight, origZ)` 重查世界 | 复用开局 `MoveCollisionSystem` 预扩（`1.01 × step`）的 `mCollisionShapes` |
| 天花板与试探 | **双方案竞争**：`axisalignedbb2`（水平扩展盒限高 `Y↑`）与 `axisalignedbb4`（原盒 `Y↑step`）各跑 `X→Z` 择优再 `Y↓` | **单方案 + 预删 + 终态复核**：预删 `box.min.y >= origAABB.max.y` 的天花板盒，跑一次 `Y↑→X→Z→Y↓(-dy1)`，再对未过滤全盒做 6 面相交复核 |
| 采纳判据 | `step_dx² + step_dz² > normal_dx² + normal_dz²` | `step_dx² + step_dz² > normal_dx² + normal_dz²`（`0x149049ae8`） |
| 垂直碰撞 / `onGround` | `origY != y` 清 `motionY`；`onGround = (origY != y && origY < 0)` | **同构**：`\|preY - speedY\| > 1.192e-7f` 置垂直碰撞；`preY < 0` 才置 `OnGround` |
| 数值精度 | `double`（64 位） | `float`（32 位，`clipCollide 0x143327e5b` 内含 `1e-6f` 容差） |
| 状态写回 | 改写 velocity / `onGround` | 改 `MoveRequestComponent::mSpeed` + 缓存碰撞盒 + `HasAutoSteppedComponent`；**不动 `mPosDelta`** |
| 固体 Blip | 存在（保速、落地拍触发） | **存在**：对齐落地拍后 `vz` 与控制组一致（`0.2523994445800781`），一步抬 `0.4412117004394531` |
| Jump Cancel | 存在（Step-Assist 吃掉起跳） | **存在**：贴前沿起跳 `+0.42` 被吃掉、直接放上台阶顶；正上方矮顶则是 `+0.42 → +0.2` 截断 |
| Grind / 连跳 | 点按可快于长按；长按有自动连跳冷却 | **同族**：`MobJumpComponent::mNoJumpDelay = 10` 仅长按生效，松开清零；点按最快 `3` tick 一跳、`max_vz = 0.631786` |
| Ceiling Blip | 存在 | **天花板前缘是 Step-Assist Jump Cancel**（吃掉 `+0.42`、全速钻入），非额外抬升 |

天花板过滤差异带来的两项机制对照：
- **从矮天花板下向外跨上无顶台阶（原头顶净空 `<` 台阶高、台阶上方无顶）**：JE `1.12.2` 的双方案都会在 `Y↑` 阶段先被原头顶挡住；BE `1.26.51.01` 的试探虽然会先删掉 `box.min.y >= origAABB.max.y` 的原头顶盒，但**终态复核用的是未过滤全表**，候选 AABB 只要还和身后矮顶水平重叠就会被否决。第 3 轮 `low_roof_no_gap`（矮顶压在前缘）实测被拒、`low_roof_with_gap`（矮顶提前一格）实测可上，证明“穿檐”只有在**终点 AABB 完全离开矮顶**时成立；原 §8 的“仅靠预删即可穿檐”推断**撤回** `[代码]` `[实测]`。
- **平地紧贴矮天花板前缘起跳（Ceiling Front Step-Assist）**：JE `1.12.2` 靠扩展盒方案限高到 `0.2` 钻入再回落触发；BE `1.26.51.01` 靠预删前方矮顶盒抬满回落再通过终态复核触发，代码路径不同但吃跳表现殊途同归 `[代码]` `[实测]`。区别在于这一项的终点仍在地面高度、候选 AABB 的头顶低于矮顶底面，所以终态复核能通过；一旦终点被台阶抬高到 `0.5`，头顶就会穿入矮顶而被拒（上一项）。

> Java 侧为机制层面归纳，未在本机 Java 客户端复测，不写成本版本 `[实测]`。

---

## 9. 撤回记录

- `r01-006`「`1.26.51.01` 无 JE 1.8 式固体 Blip / Ceiling Blip；Jump Cancel 把 `+0.42` 裁成 `+0.2`；Grind 是 `10` tick 一次」**整体撤回**：三处结论分别被 §6.2（保速固体 Blip）、§5（Step-Assist Jump Cancel）、§7.2（点按 `3` tick 周期）证伪；根因是落地拍对齐错误、`jump_into_step` 未触及台阶、只测长按空格，以及漏读 `AutoStepFilterSystem` 准入与 `mNoJumpDelay` 清零 `[实测]` `[代码]`。
- **§8「从矮天花板下向外跨上无顶台阶时 BE 可穿檐走上」推断撤回**：第 3 轮 `low_roof_no_gap` 实测被拒（`max_feet_y = -60.0`、`max_z = 1413.699951`），只有 `low_roof_with_gap`（矮顶提前一格）才走上 `0.5`；`AutoStepSystem` 的终态复核遍历未过滤 `mCollisionShapes`，候选 AABB 仍与身后矮顶重叠即拒绝。原推断只看了试探前的预删，漏了终态全表复核 `[代码]` `[实测]`。
- 保留复用：`r01-001`（注册顺序与诊断标签）、`r01-002`（可变步高默认值与 `1.01` 盒）、`r01-003` 主体（补入 §3.1/§3.3）、`r01-004`（不改写 `mPosDelta`）、`r01-005`（步高边界）、`r01-007`（能走上的台阶不断疾跑）。第 3 轮新增 `r03-016` 的尾项数据（精细高度、低檐、3 tick 逐行、组合、潜行/飞行）全部落在 `out/stepping-tail-1.26.51.01.json`。

---

## 10. 未闭合项与如实边界

1. **`mControlledValue = 1.0625` 仍只有静态闭合**：三值选择分支、`VehicleComponent` 乘客数/控制座位/`WasdControlled` bit44 的条件已逐条展开（§4.1），但第 3 轮用 `/ride @s ride|mount|... @e[type=horse]` 均被拒（summoned horse 未驯服/未装鞍），没有做出“骑乘载具步高 `1.0625`”的实机对照；`mJumpPreventedValue` 与 base 同值，对玩家也没有可分辨的实机差异。载具乘客分支保留为**静态闭合、实机未触发**的边界。
2. **点按注入仍有约 `±1` tick 相位抖动**：最快 `3` tick 是 3 次重复聚合到的下限（`r03-016` 共 `34` 次冲量、间隔 `3~5`），逐行序列与静态顺序已在 §7.2 对质；`3` 是物理下限，注入本身不能保证每次都精确落在落地拍。
3. **组合几何只证明“可连锁但不稳定”**：`0.5` 台阶 + 空档 + 矮顶前缘的周期 `6` 布局最短 `4` tick、最长 `14` tick、`10` 次冲量；没有穷举其它跑酷布局，也没有找到能像纯矮顶那样稳定 `3~6` tick 的台阶组合。
4. **鞘翅、水中/游泳姿态、载具（船/矿车/马）乘客**未测；潜行与创造飞行已补实测（§4.2 的潜行/飞行 `0.5625` 可上、`0.625` 被挡，见 `r03-016`）。
5. **`ClientAcceptanceSystem` 的形参→组件映射**由 `0x1482d5330` 视图解析、stride 与偏移补出，标 `[推断]`；它是多人服务端修正路径，单机不触发，未做多人修正的实机对质。

---

## 11. 复现

```bash
py -3.11 driver/dev.py run --no-build --world "超平坦测试" --background --allow allow_input_inject=1 --allow allow_mem_read=1
py -3.11 driver/dev.py exec "py -3.11 skills/bedrock-re/findings/1.26.51.01/cases/stepping-12651/probes/verify_stepping.py"
py -3.11 driver/dev.py exec "py -3.11 skills/bedrock-re/findings/1.26.51.01/cases/stepping-12651/probes/verify_stepping_tail.py"
```

`verify_stepping.py`（第 1/2 轮）覆盖注册顺序、准入、三档步高、Jump Cancel、固体 Blip、点按/长按；`verify_stepping_tail.py`（第 3 轮，账本 `r03-016`，`exit: 0`）覆盖：`0.5/0.5625/0.59375/0.625` 精细高度（全部用 `block_shapes` 回读世界坐标碰撞盒并换算到方块顶面）、低檐前缘 `no_gap/with_gap` 对照、`tap_three_tick`（3 次重复 + 逐行 `+0.2/-0.0784/-0.1216/落地重触发` 硬断言）、纯矮顶/纯阶梯/组合几何的同场 `1/2` 点按对照、潜行与创造飞行的 `0.5625`/`0.625` 对照。脚本内置硬断言：自然地表与方块状态回读、接触前沿、无意外跳跃冲量、低檐 `stepped` 与几何一致、三档 `stepped` 与预期一致、最快点按间隔为 `3`、潜行/飞行的 `flying`/`sneaking` 前提；任何穿墙、离地、未触及前沿或未按预期抬升的场景直接抛错。原始数据：`out/stepping-1.26.51.01.json`（第 1/2 轮）与 `out/stepping-tail-1.26.51.01.json`（第 3 轮）；`landing.py probe` 的 `r03-016.out` 保留了最终 `exit: 0` 的 stdout。
