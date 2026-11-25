# 16 b/t 速度钳位与移动速度边界（1.26.51.01）

> 版本：`Minecraft.Windows.exe` `1.26.51.01`（LeviLamina `26.51.7`，`image_base = 0x140000000`）。
> 证据约定：`[代码]` 反汇编/调用链；`[常量]` `.rdata` 浮点、组件 ID、结构布局；`[实测]` LeviPilot 逐 tick 录制与 ECS 回读；`[推断]` 由直接证据补出的推导。
> 复现脚本：[`verify_speed_limit.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/speed-limit-12651/probes/verify_speed_limit.py)（逐 tick 实测）、[`scan_move_pipeline.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/speed-limit-12651/probes/scan_move_pipeline.py)（种子 + 1~2 跳 callee 的常量/钳位扫描）。原始输出落盘于 gitignored `out/speed-limit-1.26.51.01.json` 与 `out/scan-move-pipeline.txt`。

---

## 0. 结论摘要

1. **`16 b/t` 绝对速度上限存在**，位置在 `MoveCollisionSystem::fetchCollisionShapes`（`0x1431c2f50..0x1431c4494`）的入口：对 `MoveRequestComponent` 的 `+0x3C/+0x40/+0x44`（SDK 布局中的 `mSpeed`）做 **三维欧氏模长** 检查，`x²+y²+z² > 256.0f` 时归一化并乘以 `16.0f` 写回。分轴不是独立钳位，而是球形范数等比缩放 `[代码]` `[常量]`。
2. **时序**：钳位发生在碰撞箱抓取之前、`MoveRequestComponent.mSpeed`（`+0x3C/+0x40/+0x44`）作为碰撞解算输入的阶段；解算结果经 `ActorMoveSystem` 更新碰撞箱与 `MoveRequest`，再由 `FinalizeMoveSystem` 把 AABB 中心写回位置输出，而**不改写无碰撞轴上的 `StateVectorComponent::mPosDelta`**。因此它只约束**当拍实际移动位移**，不会反向抹掉 `mPosDelta` 里尚未截断的内部动量 `[代码]` `[实测]`。
3. **`mPosDelta` 在无碰撞轴上保留 >16 动量**：`safeTickPost` 采到的 `mPosDelta` 已经经过 `ApplyFriction`（地面 `×0.546`）。若 `mPosDelta` 同样受 `16.0` 截断，地面稳态上限只能是 `16.0 × 0.546 = 8.736`；但实测灵魂疾行 III + 迅捷 255 疾跑钳位拍的 `mPosDelta.z` 依次涨至 **`9.06598`（`= 16.60436 × 0.546`）→ `10.81418`（`= 19.80619 × 0.546`）**，走路也达 `9.05284 → 9.45374`；45° 斜向离台腾空拍 `mPosDelta.y = -0.15523` 未被截断后的 `-0.07555` 覆盖，下一拍直接释放 **`9.09278`** 的实际空中位移（超过 `8.736`）`[实测]`。
4. **触发条件**：请求速度必须超过 16。迅捷 255 + 疾跑在普通地面稳态只有 `14.65`，蓝冰因加速度按 `(0.6/slip)³` 缩放反而更低（实测拟合 `11.42` / `14.85`）；**灵魂疾行 III + 迅捷 255 + 疾跑** 的目标约 `21.4+`，实测连续多 tick 精确停在 **`15.9998 ~ 16.0`** `[实测]`。
5. **45° 斜向**：钳位拍单轴各 `11.3136`（`16/√2`），三维模长 `16.0` —— 验证是欧氏球形范数，而不是“每轴 16”的立方体 `[实测]`。
6. **上轮结论已撤回**：旧报告“`16 b/t` 不存在、`256.0` 零命中”是**假阴性**。旧扫描只覆盖“函数体自身含组件哈希立即数”的函数，且只匹配 `minss/maxss`；`fetchCollisionShapes` 通过指针参数接收 `MoveRequestComponent&`、函数体内不含 `0x4c8f7288`，且用 `ucomiss + jbe + sqrtss + divss + mulss` 实现，被整体漏掉。新扫描（种子 + 1~2 跳 callee、`ucomiss/comiss/minss/maxss/minps/maxps` 全模式）在 2100 个可达函数里复现出该点 `[代码]`。
7. **保留有效的旧结论**：`ApplyFriction` 的 `FLT_EPSILON` 归零、服务端 `16.0f` 平方距离接受、`FinalizeMoveSystem` 自身无钳位、自由落体 `-3.92` 递推、迅捷 255 的 `MovementSpeedComponent=5.22` 线性、`AirSpeed=0.02`、飞行 `0.512/1.085`、TNT 冲量 `0.70`。
8. **蓝冰不是“更快”**：Bedrock 加速度随滑度按 `(0.6/slip)³` 衰减。实测首 tick `1.142`（走）/`1.485`（疾跑），恰为 `0.98 × 5.22 × (0.6/0.989)³`；配合 `f=0.9` 的摩擦，稳态仅 `11.42`/`14.85` —— 低于 16，永远触发不了钳位 `[实测]`。

---

## 1. 钳位的静态证据

### 1.1 指令序列（`0x1431c2fd4..0x1431c3043`）

```
0x1431c2fd4  mov  r13, qword ptr [rbp + 0x5d0]      ; r13 = MoveRequestComponent&
0x1431c2fdb  movss xmm2, [r13 + 0x3C]                ; mSpeed.x
0x1431c2fe1  movss xmm1, [r13 + 0x40]                ; mSpeed.y
0x1431c2ff9  movss xmm0, [r13 + 0x44]                ; mSpeed.z
0x1431c2ffe  ...                                     ; xmm3 = x² + y² + z² (lenSq)
0x1431c300a  ucomiss xmm3, [rip + 0xB5E1283]         ; 0x14E7A4294 = 256.0f (16²)
0x1431c3011  jbe  0x1431c3049                        ; lenSq <= 256 跳过
0x1431c3013  sqrtss xmm3, xmm3                       ; |v|
0x1431c3017  divss xmm0/xmm1/xmm2, xmm3              ; 归一化
0x1431c3023  movss xmm3, [rip + 0xB59FD19]           ; 0x14E762D44 = 16.0f
0x1431c302b  mulss xmm0/xmm1/xmm2, xmm3              ; ×16
0x1431c3037  movss [r13 + 0x3C], xmm2
0x1431c303d  movss [r13 + 0x40], xmm1
0x1431c3043  movss [r13 + 0x44], xmm0                ; 写回 mSpeed
```

- 常量：`256.0f` 在 `0x14E7A4294`（`u32=0x43800000`），`16.0f` 在 `0x14E762D44`（`u32=0x41800000`）`[常量]`。
- 写回字段：`MoveRequestComponent` 的 `+0x3C/+0x40/+0x44`。SDK 布局为 `mPreCollisionSpeed(+0x30)` → `mSpeed(+0x3C)` → `mMaxDepenetration(+0x48)`，因此被钳的是 `mSpeed`（返工单里称 `mRawMove` 的是同一组字节）`[常量]`。
- 语义：`lenSq > 256` 才触发；触发后**保持方向、等比缩到模长 16**。`lenSq <= 256` 时完全不改写 `[代码]`。

### 1.2 调用链与注册点

| 环节 | 地址 | 说明 |
| --- | --- | --- |
| 注册 | `0x14681aef5`（名字 `0x14ec34b7d` = `"MoveCollisionSystem"`） | 构造 vtable `0x14e871920` 并登记系统 `[代码]` |
| slot[6] 实现 | `0x1431dc500..0x1431dc8d8` | 在 `0x1431dc897` `call 0x1431c2f50` `[代码]` |
| slot[5] 辅助 | `0x1431dd080..0x1431dd2a0`（由 slot[5] `0x1431dba10` 调用） | 在 `0x1431dd25c` `call 0x1431c2f50` `[代码]` |
| 钳位函数 | `0x1431c2f50..0x1431c4494` | 只被上述两处调用（`--xrefs` 2 命中）`[代码]` |

上游 `TravelMoveRequestSystem`（注册 `0x1468264f3`）→ `applySlowdownOnMove`（`0x1448a98c0`）把移动意图写进 `MoveRequest`；下游 `ActorMoveSystem`（`0x1490283a0`，worker `0x1482a4890`）更新 `AABBShapeComponent` 与 `MoveRequestComponent`（`mPreCollisionSpeed +0x30` / `mSpeed +0x3C`），`FinalizeMoveSystem`（`0x1463b39a0`）更新 `onGround`/碰撞标志并将 AABB 中心写回 `StateVectorComponent::mPos`（无碰撞轴上不回写 `mPosDelta`）`[代码]`。

> 上轮把 `sub_142DE0950` 误标为“碰撞形状抓取”。实际反汇编该函数引用 `str='MobMovementDrag::Water'`，是水/岩浆阻尼注册；碰撞形状抓取就是本文的 `0x1431c2f50` `[代码]`。

### 1.3 全管线复扫（修正上轮盲区）

[`scan_move_pipeline.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/speed-limit-12651/probes/scan_move_pipeline.py)：

1. **种子**：函数体含移动组件 ID 立即数者，共 **738** 个（比上轮多纳入 `PlayerMovementSettings` / `ServerPlayer*`）。
2. **扩展**：沿直接 `call 0x...` 展开 **1~2 跳 callee**，共 **2100** 个可达函数。
3. **常量交叉**：`16.0 / 256.0 / 0.003 / 0.005 / 3.92 / 0.42 / 0.98` 的 `.text/.rdata` 常量 xref 在可达集内的命中共：
   - `16.0`：**3 处** —— 服务端位置接受 `0x14291dfc0`、本文钳位 `0x1431c2f50`、区块局部坐标换算 `0x14661c780`（`×16 − 0.5` 后夹到 `15/14`，与速度无关）`[代码]` `[常量]`；
   - `256.0`：**1 处** —— 本文钳位的 `lenSq` 阈值；
   - `0.98`：`0x1448bd6d0`（未展开定性）。
4. **全模式钳位清单**：对 2100 个函数搜 `ucomiss/comiss/minss/maxss/minps/maxps` + rip 常量。除本文钳位外，其余命中均为有限性判据（`2²⁵`）、阈值比较（`-0.0801`、`1.2`、`0.1`、`144`、`64`、`1e-4` 等），未再发现第二处速度截断 `[代码]`。

---

## 2. 数据流：钳位之后怎么走

1. **输入侧**：travel/摩擦/疾跑等把本 tick 的移动意图写入 `MoveRequestComponent.mSpeed`。
2. **钳位**：`fetchCollisionShapes` 在抓取碰撞箱前把 `mSpeed` 缩到模长 ≤16（§1.1）。
3. **碰撞解算**：`MoveCollisionSystem` 的两个 slot 用钳位后的 `mSpeed` 推进 AABB；无碰撞时也走同一路径（钳位发生在抓取入口，与是否撞墙无关）。
4. **写回**：`ActorMoveSystem`（`0x1490283a0`，`0x149028535..0x1490285ef`）只更新 `AABBShapeComponent`（`[rbx]`）和 `MoveRequestComponent`（`+0x30` / `+0x3C`），`FinalizeMoveSystem`（`0x1463b39a0`）把 AABB 中心写回 `StateVectorComponent::mPos`；两者都不在无碰撞轴上回写 `StateVectorComponent::mPosDelta`（`+0x18/+0x1C/+0x20`）`[代码]`。
5. **实测核对（精确吻合未截断递推）**：
   - `axis_sprint`（每拍地面加速 `10.74020`，`ApplyFriction` 乘 `0.546`）：
     - `t=2`：请求 `10.74020` → 完成位移 `10.74020`，`mPosDelta.z = 5.86415`（`= 10.74020 × 0.546`）；
     - `t=3`：未截断请求 `5.86415 + 10.74020 = 16.60435 > 16` → 当拍水平位移被缩限至 **`15.99982`**，但 `mPosDelta.z = 9.06598`（精确等于 **`16.60436 × 0.546`**，高于截断上界 `16.0 × 0.546 = 8.736`）；
     - `t=4`：未截断请求 `9.06598 + 10.74020 = 19.80618 > 16` → 当拍水平位移被缩限至 **`15.99987`**，但 `mPosDelta.z` 继续升至 **`10.81418`**（精确等于 **`19.80619 × 0.546`**）。
   - `axis_walk` 同理：`t=5` `mPosDelta.z = 9.05284`（`= 16.58029 × 0.546`），`t=6` `9.45374`（`= 17.31454 × 0.546`）。
   - `diagonal_sprint`：`t=3` 离台腾空时三维位移被等比缩至 `(-11.31358, -0.07555, 11.31359)`（模长 `16.0`），但同拍 `mPosDelta = (-6.41061, -0.15523, 6.41062)` —— 水平 `9.06597 = 16.60436 × 0.546` 且垂直 `-0.15523 = (-0.0784 - 0.08) × 0.98` 未被 `-0.07555` 覆盖；下一空中拍 `t=4` 实测位移直接释放到 **`9.09278`**（仍高于 `8.736`）`[实测]`。

结论：**`16 b/t` 只截断当拍传入碰撞解算的 `MoveRequestComponent.mSpeed`（实测实际移动位移），在无碰撞轴上 `StateVectorComponent::mPosDelta` 保持未截断动量并继续累积超 `16` 速度**（遇到方正阻挡碰撞清零或触顶除外）。

---

## 3. 实机：如何真正超过 16

### 3.1 普通地面 / 迅捷 255

普通地面（`f≈0.546`，加速度 `0.98m`）在 `m=5.22` 时稳态 `14.65`（疾跑），**到不了 16**，因此上轮“怎么跑都没撞顶”并不能反证钳位。

### 3.2 蓝冰：滑度越高反而越慢

Bedrock 的加速度项随滑度按 `(0.6/slip)³` 缩放（`slip=0.989` 时为 `0.223`），与 `f = 0.989×0.91 = 0.9` 组合后稳态只有：

| 场景 | 首 tick | 拟合 `f` | 稳态 |
| --- | --- | --- | --- |
| 蓝冰 + 迅捷 255 走 | `1.142` | `0.9` | **`11.42`** |
| 蓝冰 + 迅捷 255 疾跑 | `1.485` | `0.9` | **`14.85`** |

首 tick 与 `0.98 × 5.22 × (0.6/0.989)³ = 1.142` 逐位吻合 `[实测]`。**蓝冰不能用来触发 16 钳位**。

### 3.3 灵魂疾行 III + 迅捷 255：目标 >21，触发钳位

灵魂沙跑道 + 钻石靴（`/enchant @s soul_speed 3` 后 `equip feet`）+ 迅捷 255：

| 场景 | 逐 tick 完成位移模长 | 命中 16 的 tick |
| --- | --- | --- |
| 单轴疾跑（5 tick） | `10.74 → 15.99982 → 15.99987` | 2 |
| 单轴走路（7 tick） | `8.26 → 12.77 → 15.24 → 15.99983 → 15.99983` | 2 |
| 45° 斜向疾跑（5 tick） | `10.74 → 16.0 → 9.09` | 1 |

- 斜向钳位拍的分量：`(-11.31358, -0.07555, 11.31359)` —— 单轴 `11.3136 = 16/√2`，模长 `16.0`。若是分轴钳位，两轴应各为 16 `[实测]`。
- **`15.99982 → 15.99987` 的精确来源**：并非 float32 舍入误差，而是地面上每拍预先叠加的重力 **`mPosDelta.y = -0.0784`** 在碰撞置地之前先参与了三维模长缩限（地面碰撞后垂直位移清零，只剩水平分量）：
  - `t=3`（`v_h = 16.60435, v_y = -0.0784`）：$16 \times 16.60435 / \sqrt{16.60435^2 + 0.0784^2} = \mathbf{15.999821}$（实测 `15.99982`）；
  - `t=4`（`v_h = 19.80618, v_y = -0.0784`）：$16 \times 19.80618 / \sqrt{19.80618^2 + 0.0784^2} = \mathbf{15.999874}$（实测 `15.99987`）。
  这也反向印证第 4 拍输入动量确实继续增长至 `19.806`，而非停在 `16` `[实测]`。
- 钳位拍 `mPosDelta`：单轴疾跑 `9.06598 → 10.81418`（走路 `9.05284 → 9.45374`）、斜向 `(-6.41061, -0.15523, 6.41062)`（模长 `9.06731`，均超过截断阈值 `16 × 0.546 = 8.736` 且斜向次拍空中位移释放出 `9.09278`），确证动量未被截断 `[实测]`。

### 3.4 绕过与不可比路径

| 路径 | 观测 | 说明 |
| --- | --- | --- |
| 旁观者飞行 | `0.47 b/t` | 照常经过移动系统，未绕过 `[实测]` |
| 创造飞行 | `0.512` / 疾跑 `1.085` | 远低于 16，钳位不触发 `[实测]` |
| 传送 / `/tp` | 位置直接设置 | 不经过 `MoveRequest`，与“速度上限”不可比 `[代码]` |
| 鞘翅 `GlideMoveSystem` / 载具乘客 | 未实测 | 静态上仍在 actor 移动链内，但未验证是否所有分支都走 `fetchCollisionShapes`，列 `unresolved` |

---

## 4. 其它速度边界（保留结论）

| 项目 | 结论 | 证据 |
| --- | --- | --- |
| 极小速度归零 | `ApplyFriction 0x142de2010` 用 `cmpless \|v\|, 1.1920929e-7f`（`0x14e6ae4c0`）归零，非 Java 的 `0.003/0.005`；实测末值 `1.1920928955078125e-07` | `[代码]` `[常量]` `[实测]` |
| 服务端 `16.0f` | `ServerPlayerMovementComponent::mAcceptClientPosIfWithinDistanceSq` 的兜底（`16=4²` 平方距离），单机 bitset=0、optional 空时生效，与速度无关 | `[代码]` `[常量]` `[实测]` |
| `FinalizeMoveSystem` | 自身只含 `0.5f`、`2²⁵`、`FLT_EPSILON`，无速度钳位（钳位在上游） | `[代码]` `[常量]` |
| 自由落体 | `v' = 0.98(v−0.08)`，极限 `-3.92`；127 tick 到 `-3.6306`，递推残差 max `2.78e-5`、超 `1e-3` 的 0 条 | `[实测]` |
| 迅捷曲线 | `MovementSpeedComponent = 0.1×(1+0.2×(a+1))`，255 级 `5.22`；普通地面稳态走 `11.27`/疾跑 `14.65`（未撞顶） | `[实测]` |
| 空中控制 | `AirSpeedComponent = 0.02` | `[实测]` |
| TNT 冲量 | 2.5 格爆炸峰值 `0.70 b/t`（`vy 0.386`、水平 `0.585`） | `[实测]` |

---

## 5. 与 Java 版对照

| 维度 | Java 版 | 基岩版 1.26.51.01 |
| --- | --- | --- |
| 绝对速度上限 | 无 | **有：三维模长 16 b/t**（`fetchCollisionShapes` 入口，等比缩限） |
| 截断形式 | — | 球形范数，非分轴 |
| 截断对象 | — | `MoveRequestComponent.mSpeed`（碰撞输入），不直接改 `mPosDelta`/`getVelocity` |
| 微小速度归零 | `0.003`/`0.005` | `FLT_EPSILON 1.19e-7` |
| 终速 | `-3.92` b/t | `-3.92` b/t |

---

## 6. 未闭合项

1. **鞘翅/载具绕过**：未实测 `GlideMoveSystem` 与载具乘客是否都经过 `fetchCollisionShapes`；静态上未见第二处钳位。
2. **`MoveRequestComponent` 不可 watch**：`watch_set` 对该组件返回 `watch_mask=0`（组件不在可观察实体上），因此只能以 `mPosDelta` + 完成位移做实测。
3. **`PlayerMovementSettings` 默认阈值**：组件不在本地玩家实体上（`component_not_found`），`mPositionThreshold/Acceptance/Persuasion` 运行期值未取得。
4. **`MOVEMENT_SPEED` 的 `AttributeInstance` 默认 max**：`Attribute` 本体无 min/max，上限在 `AttributeInstance+0x74/+0x78`；玩家默认 max 常量未定位，但灵魂疾行/迅捷叠加未触发属性上限（钳位先于属性上限生效）。

---

## 7. 复现

```bash
# 静态：种子 + 1~2 跳 callee 的常量与钳位扫描
py -3.11 skills/bedrock-re/findings/1.26.51.01/cases/speed-limit-12651/probes/scan_move_pipeline.py

# 实机：自由落体、迅捷曲线、飞行、微小衰减、TNT、蓝冰对照、灵魂疾行触发钳位
py -3.11 driver/dev.py run --no-build --world "超平坦测试" --background --allow allow_input_inject=1 --allow allow_mem_read=1
py -3.11 driver/dev.py exec "py -3.11 skills/bedrock-re/findings/1.26.51.01/cases/speed-limit-12651/probes/verify_speed_limit.py"
```

原始数据：`out/speed-limit-1.26.51.01.json`、`out/scan-move-pipeline.txt`（gitignored）。
