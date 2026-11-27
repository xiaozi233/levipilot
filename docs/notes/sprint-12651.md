# 疾跑状态机、撞墙断疾跑与疾跑跳（1.26.51.01）

> 版本：`Minecraft.Windows.exe` `1.26.51.01`（LeviLamina `26.51.7`，`image_base = 0x140000000`）。
> 证据约定：`[代码]` 反汇编/调用链；`[常量]` `.rdata` 浮点、组件 ID、结构布局；`[实测]` LeviPilot 逐 tick 录制与 ECS 回读；`[推断]` 由直接证据补出的推导。
> 复现脚本：[`verify_sprint.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/sprint-12651/probes/verify_sprint.py)（Round 1/2）与 [`verify_sprint_tail.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/sprint-12651/probes/verify_sprint_tail.py)（Round 3 尾项 + Round 4 修正版）。原始数据：gitignored `out/sprint-1.26.51.01.json` 与 `out/sprint-tail-1.26.51.01.json`（Round 3 账本 `r03-014`、Round 4 重跑账本 `r04-027`，均 `exit: 0`）；本文 §2.3/§3.3/§3.4/§5.3 的数字以 Round 4 那次 `r04-027` 为准。
> 相位口径：录制 `vx/vy/vz` 是本拍已完成位移；watch `+0x18/+0x1C/+0x20` 是 `StateVectorComponent::mPosDelta`（摩擦/加速后的下一拍待执行位移）。两者的关系见 §5。

---

## 0. 结论摘要（返工 v2）

1. **四系统入口（已核对真实 vtable 槽位）**：
   - `SprintTimerSystem`（注册 `0x14681f3d6`，vt `0x14eac9f40`，`0x14b1a2670/0x14b1a2b50`）— 递减 `VanillaClientGameplayComponent::mSprintTriggerTime (+0x00)`（`>0` 时减 `1`）与 `+0x08`；
   - `SetRequestSprintTriggerSystem`（注册 `0x14681f462`，vt `0x14eac9f80`，`0x14b1a3a80/0x14b1a3dc0`，worker `0x14b1a34f0`）— 检查饥饿 `> 6.0f` 或载具控制座 + `CanVehicleSprintFlagComponent`，写 `PlayerInputRequestComponent +0x0F/+0x10`；
   - `IntentSprintTriggerSystem`（注册 `0x14681f4fa`，vt `0x14eac9fc0`，`0x14b1a5460/0x14b1a7ec0`，worker `0x14b1a4c80`）— **疾跑主状态机**（起跑、双击窗口置 `7`、失明/物品减速门控、`5e-5f` 撞墙取消、游泳/水中起跳取消）；
   - `ActionSprintTriggerSystem`（注册 `0x14681fb62`，vt `0x14eaca020`，`0x14b1a9fe0/0x14b1aa080`）— 消费 `0x200/0x400` 调 `Actor::setSprinting`；
   - 注：原笔记 §0/§1 把前三个系统向左错位了一格；紧邻 `SprintTimerSystem` 前方注册（`0x14681f39b`）的 `vt 0x14ea34f30`（`0x148b59df0/0x148b5a320`）实际按移动输入 `x²+y² > 0` 设置 `ActorDataFlag bit 34 (MOVING)`，不碰计时器 `[代码]` `[常量]`。
2. **起跑门控**：`IntentSprintTriggerSystem` 核心 `0x14b1a4c80` 要求输入 `y ≥ 0.707107f`、`y > 0`、`|x| ≤ 0.707107f`、`sqrt(x²+y²) ≥ 0.707107f`，读取 `SetRequestSprintTriggerSystem 0x14b1a34f0`（`0x14b1a379f`）写入的允许/饥饿限制标志（`+0x0F == 1` 且 `+0x10 == 0`），且阻断 `ItemUseSlowdownModifierComponent` 与 `MobEffect::BLINDNESS`（`0x151dbf478`）；**按独立疾跑键**（`MoveInput +0x60 bit 1`）在空中也可直接起跑，而**双击前进**（`mSprintTriggerTime = 7`）要求满足 `WasInWater || OnGround || isFlying`（空中不可双击 `W` 起跑）。实测纯倒车 / 纯侧走 / 倒+侧 **永不进入疾跑** `[代码]` `[实测]`。
3. **保持与取消**：倒车 / 侧走 / 潜行在切换后的第一个状态拍取消；跳跃与斜前保持。静态已闭合游泳态（`ActorDataFlag bit 57`）跳过 `5e-5` 撞墙与前向判定（仅离水取消），以及非游泳态 `WasInWater && MobIsJumping` 在水中起跳时取消疾跑 `[代码]` `[实测]`。
4. **撞墙断疾跑**：**唯一** `5e-5f`（`0x14eac97e0`）只被 `0x14b1a4c80` 引用；完整绑定见 §3（`VanillaClientGameplayComponent.mLastDelta/mLastPos` + `StateVectorComponent.mPos` + `PlayerInputRequestComponent +0x0A` + `PlayerActionComponent` 的 `0x400`）。实测存在**三个角度区间** `[代码]` `[常量]` `[实测]`：
   - **单次取消**（0°~20°）：主轴被墙挡死且 `|Δ|<5e-5`，接触后下一拍起断；
   - **逐拍高频闪烁**（23.5°~24.42°）：主轴在滑动轴与被挡轴之间逐拍交替，`sprint` 位每拍翻转；
   - **稳定滑行**（≥24.43°，细扫；粗扫只测到 ≥24.5°）：横向占优，触墙后全程保持 `1`。
   - X 墙的对应临界为 `90°−24.418° ≈ 65.58°`：**≥65.59° 闪烁、≤65.58° 稳定**，与 Z 墙镜像一致。
5. **`f65` 现象被复现并解释**：它就是临界角附近的**逐拍 `sprint` 翻转**（不是“碰撞随机断”）；Z 墙在 `23.5°~24.42°`、X 墙在 `65.59°~65.62°` 逐拍翻转（粗扫 `toggles` 24~29、细扫 `24`/`30`）。
6. **地面与空中都没有 1-tick 延迟**：静止起步首拍走 `0.098`、疾跑 `0.1274`（`=0.098×1.3`）；空中首拍 `0.0196` / `0.02548`（`1.3×`）`[实测]`。
7. **疾跑跳水平 `+0.2` 的真实位置**：`JumpFromGroundSystem`（注册 `0x146823ebf`，vtable `0x14ea67240`，slots `0x149610170/0x1496169c0`）调用的 `0x1495e9200` 内 `0x1495e940c..0x1495e9478`：读 `ActorDataFlag bit3`（`SPRINTING`）→ `yaw × π/180 × 10430.378f` 查 `SIN_TABLE (0x151d6973c)` → `mPosDelta.x -= 0.2·sin`、`mPosDelta.z += 0.2·cos`（`0.2f=0x3e4ccccd`）`[代码]` `[常量]`。实测直线疾跑跳 `mpd_z 0.14578 → 0.34916`，`0.546×(0.14578+0.1274)+0.2 = 0.34916` 精确成立；贴墙滑行跳的**增量向量** `(-0.08450, 0.18126) = 0.2×(sin25°, cos25°)`，模长 `0.19999`；走路跳与“断疾跑后跳”均无该增量 `[实测]`。
8. **撤回**：上一版把 `0x1495eae35`（写 `[r12+0x1C]` = `mPosDelta.y`，门控为方块 `BlockProperty` bit 33 = `Climbable`）错标为疾跑跳冲量；那实际是**攀爬上行 `+0.2`**（梯子/藤蔓），与疾跑跳无关 `[代码]`。
9. **Round 3 尾项（Round 4 修正后）**：`mLastDelta/mLastPos` 由 `PlayerPreMoveSystem`（vt `0x14EB40190`，slot[5] `0x14C5F1590`）每 tick 写入 —— 硬件写观察点 20 tick 命中 22 次、`rip=0x14C5F12CE` 实锤；`5e-5` 判定读的是**上一移动步骤**的净位移 vs 同一步骤的碰撞前期望主轴（判定滞后一拍，`PlayerPreMove` 落在 `Intent` 与 `MoveCollision` 之间；同 tick 顺序见 §3.3）。`0.01°` 细扫把转折精确到 Z `24.42↔24.43°`、X `65.58↔65.59°`，与 `24.418049°` 逐档吻合（§3.4，按"触墙且按住前进"窗口分类：Z `24.38~24.42` 闪烁 / `24.43~24.50` 稳定，X `65.50~65.58` 稳定 / `65.59~65.62` 闪烁）。失明/物品减速/水中按跳/游泳撞墙/双击窗口 7 组实测见 §2.3；`0x1495E9200` 的 5 处调用点对照见 §5.3（`bpl` = 竖直初速 `0.2f/0.42f`，跳跃提升与蜂蜜 `0.6×` 已归位）。**§7 已无未闭合项**（§3.3 的 `PlayerPreMove` 注册点是如实标注的 `unresolved` 边界，不影响结论）。

---

## 1. 四个系统的注册与入口

| 系统 | 注册点（名字字面量） | vtable | slot[5] / slot[6] |
| --- | --- | --- | --- |
| `SprintTimerSystem` | `0x14681f3d6`（`0x14ec33eb0`） | `0x14eac9f40` | `0x14b1a2670` / `0x14b1a2b50` |
| `SetRequestSprintTriggerSystem` | `0x14681f462`（`0x14ec3403c`） | `0x14eac9f80` | `0x14b1a3a80` / `0x14b1a3dc0` |
| `IntentSprintTriggerSystem` | `0x14681f4fa`（`0x14ec3405a`） | `0x14eac9fc0` | `0x14b1a5460` / `0x14b1a7ec0` |
| `ActionSprintTriggerSystem` | `0x14681fb62`（`0x14ec34074`） | `0x14eaca020` | `0x14b1a9fe0` / `0x14b1aa080` |

- 前置系统 `0x14681f39b`（vt `0x14ea34f30`，`0x148b59df0 / 0x148b5a320`）按移动输入 `x² + y² > 0` 读写 `ActorDataFlag bit 34`（`MOVING`），并非 `SprintTimerSystem` `[代码]` `[常量]`。
- `SprintTimerSystem`（`0x14b1a2b50` 内 `0x14b1a2bcb..0x14b1a2bd6`）：当 `VanillaClientGameplayComponent::mSprintTriggerTime (+0x00) > 0` 时每拍减 `1`，并同步递减 `+0x08` `[代码]`。
- `SetRequestSprintTriggerSystem`（`0x14b1a3dc0` 调 `0x14b1a34f0`）：判食物 `> 6.0f` 或载具控制座可疾跑，写入 `PlayerInputRequestComponent +0x0F/+0x10` `[代码]`。
- `IntentSprintTriggerSystem`（`0x14b1a7ec0` 调 `0x14b1a4c80`）：执行完整起跑、双击窗口置 `7`、失明/物品减速阻断、`5e-5` 撞墙取消及游泳/水中起跳取消 `[代码]`。
- `ActionSprintTriggerSystem` 单实体入口 `0x14b1aa080`：`mov edx, 0x3000f399` 读 `PlayerActionComponent`，`test ecx,0x200`（`StartSprinting`）/ `test ecx,0x400`（`StopSprinting`），随后 `Actor::setSprinting`（`0x14b17c820`，写 ADF bit3 并压 `SPRINTING_BOOST`）`[代码]`。
- 疾跑倍率归因保持 `0.3 MULTIPLY_TOTAL`（`(1+0.3)×0.1=0.13`），不是旧的 `DEFAULT_SPRINT_SPEED_MULTIPLIER`（`check_sprint_boost.py` 守卫）`[常量]`。

---

## 2. 起跑 / 保持 / 取消

### 2.1 输入门控（静态）

```
0x14b1a4d11  test bpl, bpl                       ; ItemUseSlowdownModifierComponent (0x867fc92c) 存在则无法起跑
0x14b1a4d16  movss xmm0, [rdx+4]                 ; 输入分量 y（PlayerInputRequestComponent +0x04）
0x14b1a4d1b  ucomiss xmm0, 0.707107f             ; y >= 1/sqrt(2)
0x14b1a4d24  cmp  byte [rdx+0x0F], 1             ; 允许疾跑标志（SetRequestSprintTriggerSystem 0x14b1a379f 写入）
0x14b1a4d2a  cmp  byte [rdx+0x10], 0             ; 饥饿/状态限制标志（0 表示未受限）
0x14b1a4d58  MobEffectsComponent[BLINDNESS=15]   ; 0x151dbf478，存在失明则跳至 0x14b1a508e 取消窗口
0x14b1a4e4d  test byte [rdi+0x60], 2             ; 独立疾跑键按下直接起跑；否则需 (WasInWater||OnGround||isFlying) 且 mSprintTriggerTime > 0（<=0 时 0x14b1a5088 置 7）
0x14b1a4ec0  sqrt(x²+y²) >= 0.707107f
0x14b1a4ef0  y > 0
0x14b1a4f00  |x| <= 0.707107f
```

- `SetRequestSprintTriggerSystem` 核心 `0x14b1a34f0` 读 `MovementAttributesComponent`（`0xa795a68c`）的 `+0x28`（食物 `> 6.0f`）与 `+0x2C`；若处于载具（`0x14b1a3150` 查 `VehicleComponent 0x161f61ea`、`ActorDataControllingSeatIndexComponent 0x45ecc051`、`CanVehicleSprintFlagComponent 0x37364548`）则改按载具可否疾跑决定 `+0x0F/+0x10` `[代码]` `[常量]`。
- `PlayerInputRequestComponent`（`0x6b04a058`，SDK 无对应公开头文件，字段语义由反汇编定性 `[代码]` `[推断]`）：`+0x00/+0x04` 为输入 `(x, y)`、`+0x08` 为当前疾跑状态位、`+0x09` 为触控取消位、`+0x0A` 为停止疾跑输出位、`+0x0F/+0x10` 为 `SetRequestSprintTriggerSystem 0x14b1a34f0`（`0x14b1a379f`）写入的 2 字节允许/饥饿限制标记（食物 `≤ 6.0f` 时在 `0x14b1a378a` 置位限制）。
- `IntentSprintTriggerSystem` 取消路径（`0x14b1a4e93..0x14b1a507b`）：若已处于**游泳态**（`ActorDataFlag bit 57`，`0x14b1a4fcc`），只检查是否仍在水中（`WasInWaterFlagComponent 0x78e89f39`），**跳过 `5e-5` 撞墙与前向输入检查**；非游泳态下，若 `WasInWater && MobIsJumping`（`0x14b1a4fd3..0x14b1a503f`）、偏离 `±45°` 扇区、`+0x0F != 1`、触控松键（`PlayerInputMode == 2`）、主轴 `5e-5` 撞墙或 `+0x10 == 1`，均触发 `StopSprinting` `[代码]`。

### 2.2 实测矩阵

| 输入 | 起跑 | 保持阶段切换 |
| --- | --- | --- |
| 前进 | ✅（第 1 状态拍置位） | — |
| 倒车 | ❌（全程 0） | 切换拍即取消 |
| 纯侧走 | ❌ | 切换拍即取消 |
| 倒车+侧走 | ❌ | — |
| 前进+潜行 | — | 切换拍即取消 |
| 前进+跳跃 | — | 保持 |

### 2.3 Intent 分支实机对照 `[实测]`

`verify_sprint_tail.py` 用 `LabSession`（默认 `lock_input=True`）、`settle` + 硬断言逐拍录制：

| 分支 | 前置 | 实测结果 |
| --- | --- | --- |
| **失明** | `effect @s blindness 20 0 true`（`effects` 回读 `id=15`） | 20 拍 `W+Sprint` 全程 `sprint=0`；`effect @s clear` 后同输入 14 拍 `sprint=1` |
| **低饥饿** | `effect @s hunger 255 20 true` + 连续 6×8 tick 行走 | `hunger` 恒为 `20.0 → 20.0`，`exhaustion` 在 `0 ~ 3.7` 反复归零，**本机效果无法把饥饿降到 ≤6**；效果期间仍能起跑（因 hunger 未降低），静态 `+0x0F/+0x10` 饥饿门结论不变 `[如实边界]` |
| **物品使用减速** | 手持面包（`selected_slot` 回读 `minecraft:bread`）长按 `rmb` 再 `W+Sprint` | 22 拍全程 `sprint=0`（`ItemUseSlowdownModifierComponent 0x867FC92C`） |
| **水中非游泳态按跳** | 2 格深水、`in_water=true`、`swimming=false`，先起跑再 `jump` | 起跑段 `sprint=1`；按跳当拍起清零 2 拍（`WasInWater && MobIsJumping` 分支），跳跃结束后又恢复 3 拍，松键段全 `0` |
| **游泳态正撞墙** | 5 宽 × 8 深水槽（四面石墙）、`swimming=true`、`yaw=0` 冲墙 55 tick | 湿样本 **57 拍为 `sprint=1`**（本轮 61 个湿样本、末尾 4 拍为 `0`；Round 3 那次 62 个湿样本、末尾 5 拍为 `0`——湿样本数随离水拍浮动，保持拍数两轮都是 `57`），`wet_toggles=1`（仅离水才断）；跳过了 `5e-5` 撞墙检查 |
| **双击 `W` 地面** | 两次 `W` 键沿间隔 `gap` tick（Hardware HID） | `gap=1 / 2` → 起跑；`gap=5 / 9` → 不起跑。**窗口本身仍是 `7` tick**：`gap=5` 时"按住 1 tick + 间隔 5 tick + 键沿排队延迟"已跨过从第一次按下起算的 `7` tick 倒计时 |
| **双击 `W` 空中** | 起跳离地后双击 `W` | `gap=1 / 2 / 5` 三档在起跳后 5 拍空中窗口内**全不起跑**；空中按独立疾跑键则立刻起跑（8 拍 `sprint=1`） |

> 双击窗口的相位注记：`mSprintTriggerTime` 按 tick 计数而非按落地状态，`gap=1` 的完整空中序列在
> **落地后**第 10 拍会合法起跑（`full_airborne_series` 末尾的 `1`）；判定只取严格离地窗口（前 5 拍），
> 避免把落地后的起跑误判成"空中双击成功"。全部序列保存在 JSON 中。

---

## 3. 撞墙断疾跑（完整绑定 + 三区间）

### 3.1 唯一 `5e-5f` 与组件绑定

`0x14eac97e0` 是**全二进制唯一**的 `5e-5f`（`u32=0x3851b717`），`--xrefs` 只有 `0x14b1a4f60` / `0x14b1a4f96` 两处，都在 `0x14b1a4c80..0x14b1a5097` 内 `[常量]`。

调用方 `0x14b1a7ec0` 经 `0x14b1a50a0` 取出 9 个组件（`--func 0x14b1a50a0` 的 `mov edx, ...`）：`0x6de78dc5`(MovementAbilities)、`0xe6a1b550`(MobEffects)、`0x18b1887`(MoveInput)、`0xbd68c2e2`(PlayerInputMode)、`0x1b5d5238`(StateVector)、`0xc67426f3`(ActorDataFlag)、`0x6b04a058`(PlayerInputRequest)、`0xa406686e`(VanillaClientGameplay)、`0x3000f399`(PlayerAction)；随后 `0x14b1a7ec0` 再查 4 个标志池：`0x78e89f39`(WasInWaterFlag)、`0xc29078a0`(OnGroundFlag)、`0x8fd20e98`(MobIsJumpingFlag)、`0x867fc92c`(ItemUseSlowdownModifier) `[代码]` `[常量]`。

**精确公式**（`rcx` = `VanillaClientGameplayComponent`，`rbx` = `StateVectorComponent`，`rdx` = `PlayerInputRequestComponent`，`rax` = `PlayerActionComponent`，`rdi` = `MoveInputComponent`）：

```
起跑分支（0x14b1a4d4f..0x14b1a4e8f，未处于疾跑态时）：
  无 MobEffect::BLINDNESS(0x151dbf478) 且无 ItemUseSlowdownModifier(0x867fc92c)
  && (MoveInput+0x60 & 2  // 按独立疾跑键，可空中起跑
      || ((WasInWater || OnGround || isFlying) && mWasSneaking(+0x16) == 0 && (mWasRunning(+0x17) & 1) == 0 && mSprintTriggerTime(+0x00) > 0))
  => 置 [rdx+0x08] = 1、mSprintingOnInput(+0x19) = 1/0、PlayerAction +1 |= 0x02（StartSprinting 0x200）
  （若未按独立键且 mSprintTriggerTime <= 0，在 0x14b1a5088 写入 mSprintTriggerTime = 7）

撞墙取消分支（0x14b1a4e93..0x14b1a507b，已处于疾跑态时直接进入）：
  若 ActorDataFlag bit 57 (SWIMMING)：仅 !WasInWater 时取消（跳过 5e-5 撞墙与前向输入检查）
  否则：若 WasInWater && MobIsJumping -> 直接取消；
  或未满足 [前向 ±45° 扇区] || [rdx+0x0F] != 1
  || (|mLastDelta.z| > |mLastDelta.x| 且 |mPos.z − mLastPos.z| < 5e-5f)
  || (|mLastDelta.x| > |mLastDelta.z| 且 |mPos.x − mLastPos.x| < 5e-5f)
  => 写 [rdx+0x0A] = 1（0x14b1a4fa6），取消时写 mSprintingOnInput(+0x19) = 0（0x14b1a5073）、PlayerAction +1 |= 0x04（StopSprinting 0x400，0x14b1a5077）
```

字段来源区分：`VanillaClientGameplayComponent`（`VanillaClientGameplayComponent.h` SDK 头已逐一对照）的 `mSprintTriggerTime +0x00`、`mWasSneaking +0x16`、`mWasRunning +0x17`、`mSprintingOnInput +0x19`、`mLastDelta (Vec3) +0x1C`、`mLastPos (Vec3) +0x28`，以及 `StateVectorComponent.mPos`（`+0x00/+0x08`）为 `[常量]`；`PlayerInputRequestComponent`（SDK 无公开头文件）的 `+0x08`、`+0x0A`（停止疾跑输出位）、`+0x0F/+0x10`（允许/饥饿限制标志）语义由反汇编定性 `[代码]` `[推断]`。

> 上一版把 `[rdx+0xF]/[rdx+0x10]` 写成“疾跑请求/潜行标志”、把 `[rcx+…]` 留作“未知 Vec3”，并声称“没有水平碰撞标志才进入判定”——均已在本文更正：那里读的是 `SetRequestSprintTriggerSystem` 写入的两个状态/饥饿标记，且该段代码**不读** `HorizontalCollisionFlagComponent`。

### 3.2 实测：三个角度区间

41 tick 贴墙（起点距墙 5 格、墙长 61 格；**硬断言**：接触后全程 `on_ground`、法向速度 `≤0.02`、位置误差 `≤0.05`，否则脚本失败）：

| 与墙面法线夹角 | Z 墙 `toggles` | 序列（原始） | 结论 |
| --- | --- | --- | --- |
| 0° / 1° / 5° / 10° / 15° / 20° | 2 | `0,1,…,1,0,0,…`：接触后**下一拍**断，此后整段为 `0` | **触墙后不再恢复** |
| **23.5° / 24.0° / 24.3° / 24.4°** | **24 ~ 25** | `…1,0,1,0,…` 逐拍交替 | **逐拍闪烁** |
| 24.5° / 24.7° / 25° / 30° / 45° / 65° | 1 | `0,1,1,…` 全程 `1` | **稳定滑行** |

X 墙（`yaw 90` 正对 +x 墙，镜像角度）：

| yaw（与 X 墙法线夹角） | X 墙 `toggles` | 结论 |
| --- | --- | --- |
| 90°（0°）/ 89°（1°） | 2 | 接触后下一拍断、不再恢复 |
| **65.6°（24.4°）** | **29** | **逐拍闪烁** |
| 65.58°（24.42°）/ 65.5°（24.5°）/ 65°（25°）/ 45°（45°） | 1 | 稳定滑行 |

> 这一节是 Round 2 的**粗扫**（步长 `0.5°~5°`，41 tick）。`toggles=2` 在本表里只出现在两组：0°~20° 的"断后不再恢复"和 ≥24.5° 的"从未断"——两者靠序列区分（前者 `contact+1` 起全 `0`，后者全程 `1`），不能只看翻转数；细扫的权威结论见 §3.4。

- 临界值（粗扫）：Z 墙落在 `24.4°~24.5°`，X 墙落在 `65.58°~65.6°`，两者互为补角（`24.42° + 65.58° = 90°`），与 `arctan(1−0.546) = 24.42°` 的预测一致 `[实测]` `[推断]`；`0.01°` 细扫把转折收紧到 Z `24.42↔24.43°`、X `65.58↔65.59°`（§3.4）。
- **`f65` = 临界角附近的逐拍 `sprint` 翻转**：闪烁带内 `sprint` 序列呈 `0,1,0,1,…`，相邻拍速度在 `0.0986 ↔ 0.1065` 之间抖动；这是主轴在“被墙挡死轴”和“滑行轴”之间逐拍交替导致的判定翻转，不是随机断 `[实测]`。
- 贴墙起步（初始就在墙边、同样按住前进+疾跑）：0°/24.4° 起跑后断、24 拍窗口内不再恢复（每拍“重起 + 同拍取消”）；25° 闪烁（21 次）；45° 三次翻转后稳定；65° 稳定。差别在滑动轴存量（起步为 0），不在按键。

### 3.3 写读时序：`mLastDelta` / `mLastPos` 是谁、在什么时候写的 `[代码]` `[常量]`

**写入者 = `PlayerPreMoveSystem`**（系统名 `"PlayerPreMove"`，字面即"移动前"）：

| 项 | 值 |
| --- | --- |
| 注册函数 / 系统名 | `0x14C5E8330`（名字符串由 `0x14C5E8E4D/0x14C5E8E5E` 两条 `movabs` 拼出） |
| vtable | `0x14EB40190` |
| slot[5] / slot[6] | `0x14C5F1590` / `0x14C5F1D90` |
| ECS 视图 | `MoveRequestComponent (0x4C8F7288)`、`StateVectorComponent (0x1B5D5238)`、`VanillaClientGameplayComponent (0xA406686E)`、`OnGroundFlagComponent (0xC29078A0)`、`WasOnGroundFlagComponent (0x85C63FFD)` |

写入 lambda `0x14C5F12A0`（由 slot[5] 的 `0x14C5F183E` 逐实体调用）：

```
0x14C5F12BF  mov esi, [r8+8]          ; StateVector.mPos.z
0x14C5F12C3  mov [r9+0x30], esi       ; mLastPos.z
0x14C5F12C7  mov r8,  [r8]            ; StateVector.mPos.xy
0x14C5F12CA  mov [r9+0x28], r8        ; mLastPos.xy
0x14C5F12CE  mov r8d, [rdx+0x44]      ; MoveRequest 位移向量 .z
0x14C5F12D2  mov [r9+0x24], r8d       ; mLastDelta.z
0x14C5F12D6  mov rdx, [rdx+0x3C]      ; MoveRequest 位移向量 .xy
0x14C5F12DA  mov [r9+0x1C], rdx       ; mLastDelta.xy
```

`r9` = `VanillaClientGameplayComponent`，`r8` = `StateVectorComponent`，`rdx` = `MoveRequestComponent`（`+0x3C/+0x44`）。
即：**`mLastPos` 存被快照那一步的起点位置，`mLastDelta` 存同一步碰撞前的期望位移方向**（成对写给后续判定读，相位见下文）。

**动态实锤**：硬件写观察点 `watch_set(VanillaClientGameplay+0x28, mode=w)` 在 20 tick 前进输入内命中 **22 次**，
`rip = 0x14C5F12CE`（正是 `0x14C5F12CA` 写 `mLastPos` 之后的指令地址），
`code_hex = 08418b7008418971304d8b004d894128` 与上述 4 条指令字节逐一吻合 → **每 tick 写一次**。

**同 tick 顺序（Round 4 修正）** `[代码]` `[常量]` `[推断]`：

Round 3 在这里同时写了"`PlayerPreMove` 先于 `Intent` 先于 `MoveCollision`"和"`Intent` 读碰撞**后**净位移"。这两句互斥：如果 `PlayerPreMove` 刚在 `Intent` 之前把 `mLastPos` 覆盖成当前 `mPos`，那 `|mPos − mLastPos|` 恒等于 `0`，任何角度都该断疾跑，与实测 `≥24.43°` 全程保持矛盾。原文撤回，改按下面三段重写。

**（a）注册侧能确证的相对顺序**（`sub_146822800` 内，地址序 = 调用序）：

| 注册点 | 目标 | 内容 |
| --- | --- | --- |
| `0x1468230DA` | `0x14681DA00` | 输入/疾跑大块，含 `IntentSprintTriggerSystem` 名 `0x14681F4FA`（str `0x14EC3405A`） |
| `0x146826032` / `0x1468261C6` / `0x146826260` / `0x14682629C` | 各自系统 | `MobTravelUpdateSpeedsSystem`、水/岩浆 travel、`WaterMoveSystem` |
| `0x14682655B` | `0x14681AC20` | 移动碰撞组：`MoveCollisionSystem`（名 `0x14EC34B7D`）、`CollisionShapesCopySystem`、`CopyCollisionShapesRewindSystem` |

配合集合实现（`registerTickingSystem 0x1425C5DD0` 末尾追加、`foreachTickingSystem 0x1425C6CF0 → 0x1425C82D0` 正序遍历，见 [`tick-order-12651.md`](tick-order-12651.md) §8.2/§8.3），同一类别内**注册序即执行序**。疾跑块经 `0x1466567E0 → sub_146662E90` 注册进 `[E14, 13584, 1358C, E1C]` **四个类别全量**（调用点 `0x1468231FB`）；碰撞组自身的注册是 `0x14681AC20` 内的 `call qword ptr`（collection 虚调用，静态读不到每个系统的 span），但组内系统可见的注册包装 `0x142DEDF10 → 0x142DEE020` 跨度是 `[E14, E1C]`，即**碰撞组的类别是四类的子集**。因此任何同时含两者的 pass 里，`IntentSprintTriggerSystem` 都排在 `MoveCollisionSystem` 之前。

**（b）`PlayerPreMoveSystem` 的注册点静态不可达（如实标注 `unresolved`）**：它的工厂 `0x14C5E8330` 只被 bundle vtable `0x14EB3FF40` 的 slot[4]（`0x14EB3FF60`）引用，bundle 构造函数 `0x14C596C40` 的 4 个调用点（`0x14C595B36` / `0x14C596888` / `0x14C5968D8` / `0x14C5D28AB`）自身也只经 vtable 调用，`--xrefs` 到此断链。Round 3 写的"客户端注册组在服务端主 setup 之前"取自**对象构造顺序**，不能当作 tick 顺序，撤回。

**（c）由约束反推的唯一自洽相位** `[推断]`（两条直接证据夹逼）：

1. `mLastPos` 与 `mLastDelta` 由同一次 lambda 调用成对写入：`mLastPos ← StateVector.mPos`、`mLastDelta ← MoveRequestComponent +0x3C/+0x44`，所以 `(mLastDelta, mLastPos, mPos)` 永远描述**同一次移动步骤**的"期望位移 / 步骤起点 / 步骤终点"。
2. 若 `PlayerPreMove` 在同一 pass 内先于 `Intent`，`mPos − mLastPos` 恒为 `0`（刚快照完），与 `≥24.43°` 稳定滑行矛盾；若它落在 `MoveCollision` 之后，`mLastPos` 已被覆盖成碰撞后位置，差值同样恒为 `0`。两种都不成立。
3. 因此同一 pass 内唯一自洽位置是 **`Intent` 之后、`MoveCollision` 之前**（这也正是系统名"PreMove"的字面含义：移动/碰撞前最后一次快照）。

**结论**：tick `t` 里 `IntentSprintTriggerSystem` 读到的是 **tick `t−1`** 那一步移动的结果——`mPos` = `t−1` 步碰撞裁剪后的位置，`mLastPos` = `t−1` 步移动前的位置，`mLastDelta` = `t−1` 步的碰撞前期望位移。`|[rcx+0x24]| vs |[rcx+0x1C]|` 选的是**上一步**的期望主轴，`|mPos − mLastPos|` 是**上一步**的实际净位移；本拍碰撞结果要到 tick `t+1` 才被读取。

| 读取量 | 对应值 | 相位（修正后） |
| --- | --- | --- |
| `\|[rcx+0x24]\|` vs `\|[rcx+0x1C]\|` | `mLastDelta` 主轴 | **上一移动步骤**的碰撞前期望位移方向 |
| `\|[rbx]−[rcx+0x28]\|` 或 `\|[rbx+8]−[rcx+0x30]\|` | `mPos − mLastPos` | **上一移动步骤**碰撞后的净位移 |
| `5e-5f`（`0x14EAC97E0`，`u32=0x3851B717`） | 净位移阈值 | 净位移 ≈ 0 即判定上一步被墙挡住 |

实测旁证与这个相位一致：`0.01°` 细扫里从"位置首次贴到墙面"这一拍到"第一个 `sprint=0`"正好差 `1` 拍（Z 墙 `contact_index=22` → 首个 `0` 在 `23`；X 墙 `17` → `18`；两个朝向一致）。

这解释了 `f65` 的 `1→0→1→0`：**期望主轴**（上一移动步骤的 `mLastDelta`）与**实际净位移**（同一移动步骤的碰撞后结果）在临界角附近逐拍交替占优，撞墙判定随之翻转，而不是"碰撞随机断"。判定与写入相差一拍，不改变 §3.4 的临界条件（比较的是同一步骤内的两个量，`tan θ = 1 − 0.546` 不变）。

### 3.4 `0.01°` 精细边界细扫 `[实测]`

`verify_sprint_tail.py` 对两个临界区各做 13 档 `0.01°` 步长扫描（每档 45 tick，接触后全程
`on_ground`、法向速度 `≤0.02`、位置误差 `≤0.05` 硬断言）。分类只在**"已触墙且前进/疾跑仍按住"**的窗口内统计
（`contact_index + 2` 起、到录制行 `input.w` 仍为 true 的最后一拍止），起步 `0→1` 与末尾松键 `1→0` 不参与判定：

| 墙 | 触墙后全程保持（`stable`） | 触墙后闪断（`flicker`） | 转折点 |
| --- | --- | --- | --- |
| **Z 墙** `24.38°..24.50°` | `24.43 ~ 24.50`（8 档，22 拍窗口内 `0` 次掉落） | `24.38 ~ 24.42`（5 档，22 拍窗口内 `11` 拍为 `0`） | **`24.42° ↔ 24.43°`** |
| **X 墙** `65.50°..65.62°` | `65.50 ~ 65.58`（9 档，27 拍窗口内 `0` 次掉落） | `65.59 ~ 65.62`（4 档，27 拍窗口内 `13` 拍为 `0`） | **`65.58° ↔ 65.59°`** |

> Round 3 把"整段 `toggles == 2`"当成"单次断"，正好把"触墙全程保持 `1`（只有起步 `0→1` 和末尾松键 `1→0`）"
> 误标成"单次断"，本表已按触墙窗口重算；原始 `toggles` 仍留在 JSON 里（Z 稳定档 `2`、闪烁档 `24`；
> X 稳定档 `2`、闪烁档 `30`），但不再用于分类。

理论值 `arctan(1 − 0.546) = 24.418049°`、补角 `65.581951°`。两个实测转折带是 Z `(24.42°, 24.43°)`、
X `(65.58°, 65.59°)`：Z 的理论值落在闪烁档 `24.42` 下方 `0.002°`，X 的补角落在稳定档 `65.58` 上方 `0.002°`，
即两个理论值都**贴着实测转折边缘、误差不超过半个 `0.01°` 步长**（脚本断言 `≤ 0.01°`），且两处互为 `90°` 补角。
闭式推导不再是 `[推断]`：由 §3.3 的写读相位可知，碰撞把期望位移投影到墙面切向，
当 `tan θ = 1 − 0.546` 时两轴净位移恰好相等，主轴判定在相邻拍翻转；判定读的是**上一移动步骤**，
所以第一次触发比贴墙拍晚一拍（见 §3.3 末的相位旁证）。

---

## 4. 地面 vs 空中延迟

| 场景 | 首拍水平速度 | 倍率 |
| --- | --- | --- |
| 地面走路 | `0.098` | 1.0 |
| 地面疾跑 | `0.1274` | **1.3×** |
| 空中走路（首拍） | `0.0196` | 1.0 |
| 空中疾跑（首拍） | `0.02548` | **1.3×** |

- 地面与空中**都在第一拍吃到 `1.3×`**，没有 Java 1.8 式的地面 1-tick 延迟 `[实测]`。
- 归因（修正上一版的自相矛盾）：`ActionSprintTriggerSystem`（`0x14681fb62`）在 E14 主 pass 内先于 `MobTravelUpdateSpeedsSystem`（`0x146826032`）和移动/阻力系统运行，同一 tick 内已写 `ActorDataFlag bit 3` 并施加疾跑加速倍率（`0.3 MULTIPLY_TOTAL`，即 `(1+0.3)×0.1 = 0.13`），因此地面首拍（`0.98×0.13 = 0.1274`）与空中首拍（`0.0196×1.3 = 0.02548`）都在当拍直接生效。`check_sprint_boost.py` 的 `0.13 = (0.3+1)×0.1` 归因不变 `[代码]` `[实测]`。

---

## 5. 疾跑跳 `+0.2` 水平冲量

### 5.1 静态（真实位置）

`JumpFromGroundSystem`（注册 `0x146823ebf`，vtable `0x14ea67240`，slot[5] `0x149610170`、slot[6] `0x1496169c0`）调用的 `0x1495e9200..0x1495e9515` 内：

```
0x1495e940c  test byte [rbx], 8          ; ActorDataFlag bit3 = SPRINTING
0x1495e940f  je   0x1495e9479            ; 非疾跑 -> 跳过
0x1495e9411  movss xmm0, [rdi+4]         ; yaw
0x1495e9416  mulss xmm0, 0.0174533f      ; × pi/180
0x1495e941e  mulss xmm0, 10430.378f      ; × 65536/(2pi)
0x1495e942d  lea  rdx, [0x151d6973c]     ; SIN_TABLE
0x1495e9434  movss xmm1, 0.2f            ; 0.2f = 0x3e4ccccd
0x1495e943c  xmm2 = sin[idx] * 0.2
0x1495e944c  xmm3 = [r12+0x18] - xmm2    ; mPosDelta.x -= 0.2 sin
0x1495e9450  xmm0 += 16384.0f            ; cos 相位
0x1495e9466  xmm1 = 0.2 * cos[idx]
0x1495e946b  xmm1 += [r12+0x20]          ; mPosDelta.z += 0.2 cos
```

`r12` 是 `StateVectorComponent`（`+0x18/+0x1C/+0x20` = `mPosDelta.x/y/z`）。**注意**：上一版引用的 `0x1495eae35`（`mov [r12+0x1C], 0.2f`）写的是 `mPosDelta.y`，其门控是方块 `BlockProperty` bit 33 = `Climbable`（`bt rcx,0x21`）——那是**攀爬上行**，不是疾跑跳。

### 5.2 实测（逐位）

| 场景 | 起跳拍前 `mpd` | 冲量拍 `mpd` | 增量 | 说明 |
| --- | --- | --- | --- | --- |
| 走路跳 | `(0, 0.11214)` | `(0, 0.11474)` | `0.546×(0.11214+0.098) = 0.11474` | **无 `+0.2`** |
| 直线疾跑跳 | `(0, 0.14578)` | `(0, 0.34916)` | `0.546×(0.14578+0.1274)+0.2 = **0.34916**` | 精确 `+0.2` |
| 断疾跑后跳（撞墙取消、速度 0） | `(0, 0)` | `(0, 0)` | `0` | 无冲量 |
| 贴墙滑行跳（yaw 25°，`sprint=1`、滑行速度 `0.11859`） | `(-0.06475, 0)` | `(-0.11986, 0.18126)` | 摩擦基线增量 `(-0.08450, 0.18126) = 0.2×(sin25°, cos25°)`，模长 `0.19999` | 冲量沿朝向，`+0.2` |

- 冲量写在**起跳拍**的 `mPosDelta`（该拍仍报 `on_ground=True`，下一拍才离地）。
- 门控看 `SPRINTING` 位而非当前速度：贴墙滑行（速度 `0.11859 < 0.20`）只要 `sprint=1`，起跳同样获得 `+0.2`；撞墙断位后（`sprint=0`、速度 0）起跳没有冲量 `[实测]` `[推断]`。

### 5.3 `0x1495E9200` 的 5 处调用点与前置条件 `[代码]` `[常量]`

`0x1495E9200..0x1495E9515` 是 `JumpFromGroundSystem` 的**共享冲量 helper**（不是 vtable 方法），
系统名 `0x14EC35C1B` 在 `0x146823EBF` 注册，vtable `0x14EA67240`（`0x146823EA7`），
slot[5] `0x149610170`（`JumpFromGroundFilterSystem` 调度器）、slot[6] `0x1496169C0`（批处理主入口）。

helper 内部（Round 4 逐条复核，三处旧读法已更正）：

```
0x1495E92B7  test ecx, 0x40000000         ; BlockLegacy+0x130 bit30 = PreventsJumping（蜂蜜块）
0x1495E9304  test byte [rax+0x133], 0x40  ; 下方一格同一 bit30
0x1495E9327  movss xmm6, 1.0f             ; 未命中：竖直初速不缩放
0x1495E9348  movss xmm6, 0.6f             ; 命中 PreventsJumping：×0.6
0x1495E9358  test bpl, bpl                ; bpl 只选**竖直起跳初速的基数**
0x1495E935D  movss xmm0, 0.2f             ; bpl=1 → 0.2f（0x14E6AE454）
0x1495E936F  movss xmm0, 0.42f            ; bpl=0 → 0.42f（0x14E85AF30）
0x1495E938C  call [vtable+0x28]           ; bpl=0 且 [参数11+0x50] 有对象时，用虚函数返回值覆盖基数
0x1495E9392  MobEffectsComponent → JUMP   ; 两条分支在此汇合（跳跃提升）
0x1495E93EE  addss xmm0, xmm1             ; 初速 += (amplifier + 1) × 0.1f
0x1495E93F2  mulss xmm0, xmm6             ; ×(1.0 或 0.6)
0x1495E93F6  maxss xmm0, [r12+0x1C]       ; 不低于当前 mPosDelta.y
0x1495E9405  movss [r12+0x1C], xmm0       ; 写 mPosDelta.y（**竖直**）
0x1495E940C  test byte [rbx], 8           ; ADF bit3 SPRINTING → 水平 +0.2（与 bpl 无关）
0x1495E9482  mov edx, 0xF37EFB0F          ; 移除 TriggerJumpRequestComponent
```

**三处更正**（Round 3 的读法全部撤回）：

1. **`bpl` 选的是竖直初速，不是水平 `+0.2` 冲量**：`0x1495E9358` 在 `0.2f` / `0.42f` 之间选择，结果经跳跃提升与缩放后写 `mPosDelta.y`（`[r12+0x1C]`，`0x1495E9405`）。水平 `+0.2` 疾跑跳冲量在下方的 `0x1495E940C..0x1495E946B`（`0x1495E9434 movss xmm1, 0.2f`，写 `mPosDelta.x/z`），与 `bpl` 无关。
2. **`0x1495E9392..0x1495E93EE` 是跳跃提升药水，不是"`bpl=1` 连跳衰减"**：`bpl=0`（`0x1495E937A`）与 `bpl=1`（`0x1495E9365`）两条分支都汇合到 `0x1495E9392`，经全局 `[0x151DBF440]`（`+8` 为 JUMP 在实例向量中的下标、元素步长 `0x90`、`+0x20` 为 int 等级、类型 id 与 `0x151DBF538` 比对）取 `MobEffect::JUMP`，把 `(等级 + 1) × 0.1f` 加到竖直初速上。
3. **`0x1495E92B7` + `0x1495E9348` 是蜂蜜块 `PreventsJumping` 的 `0.6×` 缩放**，不是"碰撞/攀爬缩放"；该位与实测已在 [`honey-block-12651.md`](honey-block-12651.md) §3 闭合（`BlockLegacy+0x130` bit30，石面 `1.252197` / 蜂蜜 `0.514`）。

`bpl` 的真实来源（能查到的部分）：

- helper A `0x1495E9520` 把**自己的第 15 个栈字节参数**（`[rsp+0xE8]`）原样透传（`0x1495E95D6 mov [rsp+0x50], bpl` → `0x1495E9607`）；该参数在 helper 内由 `0x1495E921F movzx ebp, byte [rsp+0xF0]` 读出。
- FilterSystem 调度器 `0x149610170` 把 helper A 注册**两趟**：第一趟伴随状态字节 `[rbp+0x4E0] = 0`（`0x1496101A9`），第二趟 `= 1`（`0x149610413`）；两趟的视图构造函数不同——`0x149617E50`（内含 `0x14960EED0`，补入 `OtherJumpRequestComponent 0xDCCA6EF9`）与 `0x149618160`（内含 `0x14960EBF0`，补入 `SquidJumpRequestComponent 0x830513D5`）。
- helper B `0x1495E98D0` 固定传 `0`（`0x1495E99F7 mov byte [rsp+0x50], 0`）。
- slot[6] `0x1496169C0` 三轮的实参：`0x149616DD6` 传 `0`、`0x1496171E8` 传 `1`、`0x1496178EB` 传 `0`；前两轮的视图构造分别是 `0x149617E50`（`0x149616AE2`）与 `0x149618160`（`0x149616EE2`）。

⇒ **`bpl=1` 对应 `SquidJumpRequestComponent`（鱿鱼跳跃请求）**：竖直初速取 `0.2f` 而非 `0.42f`；`bpl=0` 走 `OtherJumpRequestComponent` 的常规起跳（`0.42f`，可被实体自定义虚函数覆盖）。`LocalPlayerJumpRequestComponent (0xDA6293F1)` 与两者同在 `0x14EA66990` 邻域的声明视图表里，但"玩家起跳具体落在哪一条调用点"本轮未逐点闭合（`unresolved`，不猜测；实测只证明玩家的疾跑跳确实拿到水平 `+0.2`，见 §5.2）。

| 调用点 | 所在函数 | 粒度 | `bpl`（竖直初速基数） | 调用后 |
| --- | --- | --- | --- | --- |
| `0x1495E9607` | `0x1495E9520..0x1495E961E` helper A（FilterSystem 回调 #1） | 单实体 | **透传**调度器给的第 15 参数（同一 dispatcher 内先后 `0` / `1`；视图分别为 OtherJumpRequest / SquidJumpRequest） | — |
| `0x1495E9A05` | `0x1495E98D0..0x1495E9A27` helper B（回调 #2） | 单实体 | **固定 0**（`0.42f`） | `0x1495E9A12 or byte [rax+1], 1` |
| `0x149616DD6` | `0x1496169C0` slot[6] 第 1 轮 | 批处理逐实体 | **0**（`0.42f`；OtherJumpRequest 视图） | `0x149616DDB cmp byte [rbp+0x158],1` 命中时调 `0x142D7D280`（视图/守卫清理，不改组件） |
| `0x1496171E8` | slot[6] 第 2 轮 | 批处理逐实体 | **1**（`0.2f`；SquidJumpRequest 视图） | 同上 |
| `0x1496178EB` | slot[6] 第 3 轮 | 批处理逐实体 | **0**（`0.42f`） | `0x1496178F7 or byte [rax+1], 1` |

`0x1496169C0` 三轮的门控：第 1 轮 `0x149616A9C cmp [rcx+0x28], 0xC695B914`（`LocalConstBlockSourceFactoryComponent`）+
`0x149616B2C call 0x1428AAFC0`；第 2 轮同视图族；第 3 轮改走 `0x1496172E3 call 0x1428A8060` 与 `0x149617833 call 0x142DEB2F0`
两条谓词。三轮都查 `0x2F50F705 JumpControlComponent`（stride `0x58`），成功后由 helper 内的 `0x1495E9482` 移除
`0xF37EFB0F TriggerJumpRequestComponent`。

系统声明视图（`0x14EA671E0` / `0x14EA67278` 邻域）含 `MobIsJumpingFlagComponent (0x8FD20E98)`、
`OnGroundFlagComponent (0xC29078A0)`、`HorseFlagComponent (0x5A7992D7)`、
`MobAllowStandSlidingFlagComponent (0x403A4505)`、`CanStandOnPowderSnowComponent (0xB639C3B7)`
与 `CanStandOnPowderSnowFromEquipmentComponent (0x4EAC501F)`；第三轮更接近**粉雪/特殊介质站立跳跃**。
§7.4 由此闭合（`bpl` 语义与跳跃提升/蜂蜜缩放的归属均已改正）。

---

## 6. 与 Java 版对照

| 维度 | Java 1.8 | Java / Fabric 26.2（反编译源码直读） | 基岩版 1.26.51.01 |
| --- | --- | --- | --- |
| 起跑 / 保持输入 | 前进即可；倒车/纯侧走不保持 | `ClientInput#hasForwardImpulse()`：`moveVector.y > 1.0E-5F`（`KeyboardInput` 归一化后 `W=1.0`、`W+A/D≈0.7071`） | **前向 ±45° 扇区**（起跑 `y≥0.707`；保持 `sqrt≥0.707`、`y>0`、`\|x\|≤0.707`） |
| 双击前进窗口 | `7` tick | `Options.sprintWindow` **可调 `0..10` tick，默认 `7`**（`0` 关闭）；**空中也能双击起跑** | 固定 **`7` tick**（`0x14b1a5088`）；双击仅限 `OnGround \|\| WasInWater \|\| isFlying`（空中需按独立疾跑键） |
| 起跑 / 保持限制 | 食物 `> 6`、失明、潜行、使用物品 | `isSprintingPossible` 含 `!BLINDNESS`、`food>6 \|\| mayfly`、`!isInShallowWater`；`canStartSprinting` 含 `!UseEffects.canSprint()`、鞘翅、潜行/爬行 | 起跑禁失明、`ItemUseSlowdownModifier`、食物 `≤6`；保持禁非游泳态 `WasInWater && MobIsJumping`；游泳态跳过撞墙 |
| 撞墙断疾跑 | 任意水平碰撞即断 | `horizontalCollision && !minorHorizontalCollision`：输入与实际位移夹角 **`< 0.13962634F` rad（`< 8.0°`，距墙法线 `> 82°`）**才不断，无闪烁 | **主轴 `\|Δ\| < 5e-5` 才断**（≤20° 断；23.5~24.42° 闪烁；≥24.43° 稳定，`0.01°` 细扫） |
| 地面 / 空中延迟 | 地面 0、空中 1-tick 延迟 | **均为 `0` 延迟**（`Player#getSpeed()` 实时读属性，`getFlyingSpeed()` `0.025999999F : 0.02F`） | **均为 `0` 延迟**（首拍 1.3×） |
| 疾跑跳水平冲量 | `+0.2`（`65536` 项正弦表） | **`+0.2`**（`LivingEntity#jumpFromGround`，`65536` 项 `Mth.SIN` 表 `10430.378350470453`） | **`+0.2`**（`JumpFromGroundSystem` 走 `65536` 项 `SIN_TABLE`） |
| 乘数与水下阻力 | `0.3 MULTIPLY_TOTAL` / `0.9` | `0.3F ADD_MULTIPLIED_TOTAL` / `SPRINTING_WATER_DRAG = 0.9F`（游泳不按撞墙断） | `0.3 MULTIPLY_TOTAL` / `0.9`（游泳同样跳过撞墙） |

> 注：Java `26.2` 列对照本机 Fabric Loom 缓存的反编译源码（`LocalPlayer.java`、`ClientInput.java`、`KeyboardInput.java`、`Options.java`、`LivingEntity.java`、`Player.java`、`FoodData.java`、`UseEffects.java`、`Mth.java`），未在 Java 客户端另做实机录制。

---

## 7. 未闭合项

**无**（Round 4 修正后复核）。四项尾项的闭合位置：

| 原尾项 | 闭合位置 | 证据等级 | 账本 |
| --- | --- | --- | --- |
| 7.1 `mLastDelta` / `mLastPos` 写读时序 | §3.3 | `[代码] [常量]` + 硬件写观察点 | `r03-015`；相位与顺序见 `erratum_of: r03-016` |
| 7.2 `f65` 闪烁带精细边界 | §3.4 | `[实测]` 26 档 `0.01°` 扫描（触墙窗口分类） | `erratum_of: r03-018` |
| 7.3 Intent / 双击窗口实机覆盖 | §2.3 | `[实测]` 7 组逐拍录制 | `erratum_of: r03-019` |
| 7.4 `JumpFromGroundSystem` 5 处调用点 | §5.3 | `[代码] [常量]` | `erratum_of: r03-017` |

**保留的如实边界（不算未闭合）**：
- 本机 `/effect hunger` 不能把 `hunger` 降到 `≤6`，故"低饥饿禁止起跑"只在静态层闭合（`+0x0F/+0x10`），实机前置条件不可达，见 §2.3。
- 载具 `CanVehicleSprintFlagComponent` 分支仍只有静态证据（`0x14B1A34F0` / `0x14B1A3150`），本轮未做骑乘录制（原 §7.3 列举项之一，任务未要求补测）。
- `PlayerInputRequestComponent`（SDK 无公开头）的 `+0x0F/+0x10` 语义仍由反汇编定性 `[代码] [推断]`。

---

## 8. 复现

```bash
py -3.11 driver/dev.py run --no-build --world "超平坦测试" --background --allow allow_input_inject=1 --allow allow_mem_read=1
py -3.11 driver/dev.py exec "py -3.11 skills/bedrock-re/findings/1.26.51.01/cases/sprint-12651/probes/verify_sprint.py"
py -3.11 driver/dev.py exec "py -3.11 skills/bedrock-re/findings/1.26.51.01/cases/sprint-12651/probes/verify_sprint_tail.py"
```

若 `dev.py` 的默认路径找不到 `Minecraft.Windows.exe`，用环境变量 `LEVIPILOT_VERSION_DIR` 指向本机实例目录（不写入任何仓库文件）。实验结束 `py -3.11 driver/dev.py stop` 安全保存退出。

- `verify_sprint.py`：Round 1/2 的角度总表、起跑/保持矩阵、疾跑跳冲量；原始数据 `out/sprint-1.26.51.01.json`。
- `verify_sprint_tail.py`：Round 3 尾项 + Round 4 修正版，含 26 档 `0.01°` 细扫与 7 组 Intent 对照，全部场景 `settle` + 硬断言（贴墙后全程 `on_ground`、法向速度 `≤0.02`、位置误差 `≤0.05`；失明/物品/水中/双击均有序列断言；游泳水槽四面封石墙并回读 `minecraft:water`）。细扫分类只用**触墙且录制行 `input.w` 仍按住**的窗口（排除起步与末尾松键），并断言解析式临界角距转折边缘 `≤ 0.01°`；原始数据 `out/sprint-tail-1.26.51.01.json`（Round 3 账本 `r03-014`；Round 4 重跑账本见 `probes/r04-*`，`exit: 0`）。
