# 攀爬方块（梯子 / 藤蔓 / 脚手架 / 细雪 / 活板门）完整机制与逐 tick 实测（1.26.51.01）

> 版本：`Minecraft.Windows.exe` `1.26.51.01`（LeviLamina `26.51.7`，`image_base = 0x140000000`）。
> 证据约定：`[代码]` 反编译/反汇编与调用链；`[常量]` `.rdata` 浮点、标志位与组件 ID；`[实测]` LeviPilot 逐 tick 录制与 ECS 内存采样；`[推断]` 由前述直接证据补出的推导。
> 复现脚本：[`verify_climb_blocks.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/climb-blocks-12651/probes/verify_climb_blocks.py)（原始逐 tick 输出落盘于 gitignored `out/climb-blocks-1.26.51.01/live-results.json`）。

---

## 0. 结论摘要

1. **全游戏共 7 种块设置 `Climbable | CanHaltWhenClimbing`，脚手架与活板门均不在此列**：
   - 在 `VanillaBlockTypeGroups::registerBlocks`（`0x14c665560..0x14c80476e`）中，仅 `minecraft:ladder`、`minecraft:vine`、`minecraft:weeping_vines`、`minecraft:twisting_vines`、`minecraft:cave_vines`、`minecraft:cave_vines_body_with_berries`、`minecraft:cave_vines_head_with_berries` 7 种方块将 `BlockProperty::Climbable`（bit 33）与 `CanHaltWhenClimbing`（bit 34）同时置位 `[代码]` `[常量]`。
   - `ScaffoldingBlock`（`0x1481ad550`）只设置 `BlockProperty::Scaffolding`（bit 25）与 bit 17，`TrapDoorBlock` 不设置 `Climbable`；开着的活板门叠在梯子上方**不可攀爬** `[代码]` `[实测]`。
2. **两套完全不同的空间检测几何**：
   - **`isOnLadder()` 单点检测**（梯子 / 4 种藤蔓 / 穿皮革靴时的细雪爬升分支共用）：仅采样单格整数坐标 `(floor(mPos.x), floor(AABB.min.y), floor(mPos.z))` `[代码]` `[实测]`。
   - **`BlockClimberSystem` 全水平 AABB 足迹扫描**（脚手架与穿皮革靴时的细雪）：扫描脚底与脚下一格的整个 `[floor(min.x)..floor(max.x)] × [floor(min.z)..floor(max.z)]`，从而允许“中心在脚手架外但碰撞箱边缘搭边”时直接起攀 `[代码]` `[实测]`。
3. **梯子 / 藤蔓与 Java 版的核心差异**：
   - **被动下滑速度为 `-0.20` b/t**（`DefaultMoveSystems` `0x148b61cd6` 钳位 `max(mPosDelta.y, -0.2f)`），而不是 Java 版的 `-0.15` b/t `[代码]` `[常量]` `[实测]`。
   - **零水平 `±0.15` 速度钳位**：`DefaultMoveSystems`（`0x148b61af0`）与 `MobMovementFriction`（`0x142df9da0` 等）均无任何水平速率上限，实测在藤蔓内疾跑水平速度可达 **`0.280609` b/t** `[代码]` `[实测]`。
   - **按 Space 上升与推墙自动攀爬走不同路径**：两者同样写入 `+0.20` b/t，但 `AutoClimbSystem`（`0x1482a7c30`/`0x1482a7e40`）额外挂上 `AutoClimbTravelFlagComponent`（`0x777a5678`），使本帧**跳过** `MobMovementGravity` 与 `MobMovementDrag` `[代码]` `[实测]`。
   - **按潜行悬停的判定**：`DefaultMoveSystems` 要求 `SneakDown && mPosDelta.y < 0 && CanHaltWhenClimbing` 才清零 `mPosDelta.y`；因此向上起跳过程中按住潜行不会立刻急停，而是滑行至重力把 `mPosDelta.y` 拉成负值后才刹住 `[代码]` `[实测]`。
4. **脚手架：无需按潜行也完全免除摔落伤害**：
   - 正常有支撑时按 Space 上升 **`+0.15` b/t**、按潜行下降 **`-0.15` b/t**；其中下降态在 `MobMovementGravity`（`0x142de3566`）跳过 `-0.08` 重力但保留 `0.98` 阻尼（松键首拍为 `-0.15 × 0.98 = -0.147` b/t）`[代码]` `[实测]`。
   - **重要机制（与 Java 相反）**：`BlockClimberSystem`（`0x14489e938..0x14489e948`）只要检测到 `InScaffolding(69) | OverScaffolding(70) | InAscendableBlock(99) | OverDescendableBlock(100)` 任意一位，就会把 `FallDistanceComponent` 清零。因此在脚手架顶部**不按潜行**直接落地同样 **0 摔落伤害**（实测 9 格高落至脚手架保持 `20.0` HP，对照石块 15 格跌落扣至 `9.0` HP）`[代码]` `[实测]`。
5. **细雪 + 皮革靴的双路径与 `1.5×` 垂直增幅（实测最快的攀爬方式）**：
   - 细雪同时激活 `BlockClimberSystem` 与 `isOnLadder()`，且 `BlockMovementSlowdownMultiplierSystem::applySlowdownOnMove`（`0x1448a98c0`）将移动请求 `MoveRequest` 的 Y 分量乘以 **`1.5`**（实测 watch 槽读出 `slow_y = 1.5`）并每 tick 清零 `mPosDelta` `[代码]` `[实测]`。
   - 结果：穿皮革靴在细雪中按 Space 上升 **`+0.225` b/t**（`+0.15 × 1.5`，实测 `+0.224998`）；按潜行下降 **`-0.225` b/t**（入方块首拍 `-0.150002`，随后稳定 `-0.224998`）；**推墙自动攀爬（`W` 顶墙）达到 `+0.300` b/t**（`+0.20 × 1.5`，实测 `+0.299999`，比梯子快 **50%**）；**光脚在细雪中恒速下沉 `-0.1176` b/t**（`-0.0784 × 1.5`，因 `mPosDelta` 每帧被清零无法重力累加）`[代码]` `[实测]`。

---

## 1. 全游戏可攀爬方块清单（`[代码]` `[常量]`）

在 `VanillaBlockTypeGroups::registerBlocks`（`0x14c665560..0x14c80476e`）的 `BlockType` 属性位构造中，字节偏移 `+0x134` 对应 `BlockProperty` 高 32 位（即总体第 32..63 位）：`bit 1` = 位 `33`（`Climbable`），`bit 2` = 位 `34`（`CanHaltWhenClimbing`）。全二进制通过 `or byte ptr [reg+0x134], 6` 设置这两位的方块**共且仅有下列 7 种** `[代码]` `[常量]`：

| 注册点 VA | 方块 ID | 名称全局地址 | `+0x134` 写入 | 说明 |
| --- | --- | --- | --- | --- |
| `0x14c67c27a` | `minecraft:ladder` | `0x151e2ede8` | `or byte ptr [reg+0x134], 6` | 梯子 |
| `0x14c68dd6a` | `minecraft:vine` | `0x151e2dfa8` | `or byte ptr [reg+0x134], 6` | 主世界藤蔓 |
| `0x14c6eb5bb` | `minecraft:weeping_vines` | `0x151e2dbe0` | `or byte ptr [reg+0x134], 6` | 垂泪藤 |
| `0x14c6fa04d` | `minecraft:twisting_vines` | `0x151e2d758` | `or byte ptr [reg+0x134], 6` | 缠怨藤 |
| `0x14c703555` | `minecraft:cave_vines` | `0x151df4728` | `or byte ptr [reg+0x134], 6` | 洞穴藤蔓（无浆果） |
| `0x14c703a15` | `minecraft:cave_vines_body_with_berries` | `0x151df46f8` | `or byte ptr [reg+0x134], 6` | 洞穴藤蔓（中部带浆果） |
| `0x14c703ee5` | `minecraft:cave_vines_head_with_berries` | `0x151df46c8` | `or byte ptr [reg+0x134], 6` | 洞穴藤蔓（头部带浆果） |

- **脚手架（`ScaffoldingBlock` `0x1481ad550`）**：`0x1481ad609` 执行 `mov qword ptr [rcx+0x130], 0x2020000`（设置 `BlockProperty::Scaffolding` 位 25 与位 17），不设置 `Climbable` 或 `CanHaltWhenClimbing` `[代码]` `[常量]`。
- **活板门（`TrapDoorBlock`）**：不设置 `Climbable` 与 `CanHaltWhenClimbing`，仅作为带方向实心碰撞体存在 `[代码]` `[实测]`。

---

## 2. 检测几何对比：`isOnLadder()` vs `BlockClimberSystem`

### 2.1 `isOnLadder()` 单点检测（`[代码]` `[实测]`）

以下 6 个系统使用完全一致的内联单点逻辑：`DefaultMoveSystems`（`0x148b61af0`）、`AutoClimbSystem`（`0x1482a7c30` / `0x1482a7e40`）、`JumpFromGroundSystem`（`0x1495ea890`）、`LadderResetFallDamageSystem`（`0x14c617b20`）、`ClimbingLadderBlockSystem`（`0x142e12b10`）及 `LavaMoveSystem`（`0x14c61a3a0`）：

1. 采样单个方块整数坐标 `(floor(StateVector.mPos.x), floor(AABB.min.y), floor(StateVector.mPos.z))`（与 `Actor::getAttachPos` / 头部偏移无关，`StateVector.mPos` 水平与脚底中心重合）`[代码]`。
2. 判定表达式为 `[代码]`：
   ```text
   isOnLadder = ActorFlags.Wallclimbing (bit 18, 0x40000)
     || ( ActorFlags.Canclimb (bit 19, 0x80000)
          && ( bt(blockProperties, 0x21 /* Climbable */)
               || (canStandOnPowderSnow && block == minecraft:powder_snow [0x151e05d60]) ) )
   ```
3. **实机对照**：玩家碰撞箱半径 `0.3`，梯子位于 `x ∈ [424, 425)`。在 `x = 423.85` 时，碰撞箱 `[423.55, 424.15]` 与梯子重叠但中心 `floor(423.85) = 423` 在外，按 Space **完全自由落体**（`vy = -0.230526 -> -0.304317 -> ... -> -0.934055`）；移至 `x = 424.10`（中心 `floor(424.10) = 424` 在内）立即以 **`+0.200001` b/t** 攀爬 `[实测]`。

### 2.2 `BlockClimberSystem` 全水平 AABB 足迹扫描（`[代码]` `[实测]`）

`BlockClimberSystem`（注册点 `0x146839c8f`，过滤器 `0x14489f100`，主循环 `0x14489e590..0x14489e9cd`，辅助扫描 `0x14489e9d0` / `0x14489eca0`，条件函数 `canBlockBeAscendedByJumping` `0x14488f820`）对两层高度的整个水平碰撞箱覆盖区域进行网格遍历 `[代码]`：

- 遍历范围：`x ∈ [floor(aabb.min.x) .. floor(aabb.max.x)]`、`z ∈ [floor(aabb.min.z) .. floor(aabb.max.z)]`。
- 两个采样高度层：`y_in = floor(aabb.min.y)`（体表层）与 `y_over = floor(aabb.min.y - 1.0f)`（脚下一格）。
- 写入 4 个 `ActorDataFlag` 状态位（位于 `ActorDataFlagComponent+8` 高字）：
  - `InScaffolding`（bit `69`）
  - `OverScaffolding`（bit `70`）
  - `InAscendableBlock`（bit `99`）
  - `OverDescendableBlock`（bit `100`）
- **实机对照**：脚手架位于 `x = 452`，将玩家置于 `x = 451.80, y = -54.0`（中心 `floor = 451` 在外，AABB `[451.50, 452.10]` 边缘搭进 `x = 452`）：采样立即读出 `in_scaffolding = true, over_scaffolding = true, in_ascendable_block = true, over_descendable_block = true`，按 Space 稳定以 **`+0.150002` b/t** 上升 `[实测]`。

---

## 3. 单轮 ECS 调度管线顺序与各系统数值公式（`[代码]` `[常量]`）

同一帧主移动管线（装配链 `0x1468353c0` 段，按追加正序执行 `[代码]`）：

| 顺序 | 系统 / 注册点 VA | 核心 Worker VA | 关键读写与常量公式 `[代码]` `[常量]` |
| --- | --- | --- | --- |
| 1 | `ScaffoldingIntentSystem` (`0x14681f7ff`) | `0x147ce0140` | 模式 3 触控/手柄长按 6 tick 后写入 `MoveInputState::SneakDown`（`+0x00` bit 0）与 `Sneaking`（`+0x60` bit 0） |
| 2 | `ScaffoldingActionSystem` (`0x146820252`) | `0x147cdcd20..0x147cdcdef` | 读下降输入（模式 1 查 `+0x60` bit 0；模式 2 查 `+0x00` bit 6；模式 3 全查）且 **`OverDescendableBlock(100)`** 为真时：置位 **`DescendThroughBlock(71)`**（`or rdx, 0x80`），写 **`mPosDelta.y = -0.15f`**（`0xbe19999a` at `0x147cdcd78`），清 **`FallDistance = 0.0f`**（`0x147cdcd80`）；否则清零 bit 71（`and rdx, -0x81`） |
| 3 | `LadderResetFallDamageSystem` (`0x14683098e`) | `0x14c617b20..0x14c617caf` | `isOnLadder()` 为真时写 **`FallDistance = 0.0f`**（`0x14c617c97`） |
| 4 | `JumpFromGroundSystem` (`0x146823ebf`) | `0x1495ea890` | 受 `MobIsJumpingFlag`（`0x8FD20E98`）门控，按严格优先级分支：<br>① `0x1495ea9a8..0x1495ea9f4`：`(InScaffolding(69) \|\| InAscendableBlock(99)) && !DescendThroughBlock(71)` → 写 **`mPosDelta.y = +0.15f`**（`0x3e19999a`），置 **`NoJumpDelay = 10`**（`0xa` at `[rsi+0x10]`），**无视现有 `NoJumpDelay` 直接每帧连发**<br>② `0x1495eabab..0x1495eae35`：否则若 `isOnLadder()` → 写 **`mPosDelta.y = +0.20f`**（`0x3e4ccccd`）<br>③ 否则地面跳（`NoJumpDelay == 0` 时 `+0.42f`） / ④ 水岩浆跳（`+= 0.04f`） |
| 5 | `DefaultMoveSystems` (`0x146826493`) | `0x148b61e70` → `0x148b61af0` | 调 `moveRelative`（`0x148b618a0`）后检查 `isOnLadder()`：<br>• 写 `FallDistance = 0.0f`（`0x148b61cc2`）<br>• 向下速度钳位：`mPosDelta.y = max(mPosDelta.y, -0.2f)`（`0xbe4ccccd` at `0x148b61cd6`）<br>• 若 `MoveInputState::SneakDown`（`0x148b61d5d`）且 **`mPosDelta.y < 0`** 且单点块含 `CanHaltWhenClimbing`（`0x148b61de0: test [rax+0x134], 4`），写 **`mPosDelta.y = 0.0f`**（`0x148b61de9`）<br>• **无水平速度 `±0.15` 钳位** |
| 6 | `MoveCollisionSystem` (`0x14682655b`) + `BlockMovementSlowdownMultiplierSystem::applySlowdownOnMove` (`0x14681ad26`) | `0x1448a98c0`（内循环 `0x1448a9eeb..0x1448a9f5e`） | 实际移动并处理碰撞；当 `BlockMovementSlowdownMultiplierComponent`（`0xf6091f58`）非零时，将 `MoveRequestComponent`（`0x4c8f7288`）`+0x3c/+0x40/+0x44` 乘以 `(mx, my, mz)`，**随后清零 `StateVector.mPosDelta`（`0x1b5d5238+0x18..+0x20`）** 并清零乘数 |
| 7 | `AutoClimbSystem` (`0x14682657f`) | `0x1482a8070` → `0x1482a7c30` / `0x1482a7e40` | 当 `HorizontalCollisionFlag`（`0xd5ee0bd8`）与 `isOnLadder()` 同为真时，写 **`mPosDelta.y = +0.20f`**（`0x3e4ccccd`）并挂上 **`AutoClimbTravelFlagComponent`**（`0x777a5678`） |
| 8 | `MobMovementGravity` (`0x1468265a3`) → `ApplyGravity` | `0x142e013c0` / `0x142e02ec0` → `0x142de3560` → `0x142e0d280` | **排除 `AutoClimbTravelFlagComponent`（`0x777a5678`）**；在 `0x142de3566..0x142de3578` 若 **`(InScaffolding(69) \|\| OverScaffolding(70)) && DescendThroughBlock(71)`** 或无 `HasGravity`（`0x2000000000000`）则直接返回；否则写入 `-0.08f`（缓降 `-0.01f`）由 `ApplyGravity` 累加至 `mPosDelta.y` |
| 9 | `MobMovementDrag` (`0x1468265af`) & `MobMovementFriction` (`0x1468265bb`) | `0x142df1800` / `0x142df3850` / `0x142de21d0` | `Drag` **排除 `0x777a5678`**，但**不因 `DescendThroughBlock` 跳过**（固定乘 `0.98f`）；`Friction` 不排除 `0x777a5678`，且内部无任何水平钳位 |
| 10 | `BlockMovementSlowdownMultiplierSystem::adjustFallDistance` (`0x146838ae7`) | `0x1448a8d30` (`0x1448a8eb4..0x1448a8ec0`) | 若 `BlockMovementSlowdownAppliedComponent`（`0xe315e68e`）激活，清零 `FallDistanceComponent`（`0xce6b34f6`） |
| 11 | `BlockClimberSystem` (`0x146839c8f`) | `0x14489e590..0x14489e9cd` | 按全水平 AABB 足迹更新 `69/70/99/100` 四位标志；若存在 `BlockClimberComponent`（`0xdec6d40f`，由 `0x142d02840` 确认组件名 `'BlockClimberComponent]'` at `0x14ecef8ea`）且 `[ActorDataFlag+8] & 0x1800000060 != 0`（上述 4 位任一为真），在 `0x14489e948` **清零 `FallDistanceComponent`** |

> **录制口径说明**：Harness 的 `RecordTick` 在 `ProbeMod::safeTickPost` 采集（此时本次已完成位移 `vy = mPos - mPosPrev` 与下一轮 `Move` 前写入的待用 `mPosDelta.y` 同帧共存；详见 [`verify_climb_blocks.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/climb-blocks-12651/probes/verify_climb_blocks.py) 采集序列）`[代码]` `[实测]`。

---

## 4. 四类方块机制细节与实测数据（`[代码]` `[实测]`）

### 4.1 梯子与四种藤蔓（`ladder` / `vine` / `twisting_vines` / `weeping_vines` / `cave_vines`）

| 测试场景 | 核心数值（`vy` 为 Completed Δy，单位 b/t） | 机制解释与首帧特征 `[代码]` `[实测]` |
| --- | --- | --- |
| **梯子 Space 上爬**（`ladder_space_climb`） | 稳定 **`+0.200001`**；松键第 1~3 拍为 **`+0.117599 → +0.036846 → -0.042290`** | `JumpFromGroundSystem` 写 `+0.20`；松键首帧经重力 `-0.08` 与阻尼 `0.98` 得 `(0.2 - 0.08) × 0.98 = +0.1176`，随后 `(0.1176 - 0.08) × 0.98 = +0.036848` |
| **梯子 `W` 顶墙自动攀爬**（`ladder_auto_climb`） | 撞墙前地面水平加速后撞墙首拍（`[06]` `t=216`）`vy=0.0, mpdY=+0.2`；随后稳定 **`+0.200001`**；松键首拍先维持 `+0.200001`，下一拍才降为 **`+0.117599`** | `MoveCollisionSystem` 产生碰撞并置于 `AutoClimbSystem` 之前，使首拍写入 `+0.20` 并由 `0x777a5678` 跳过同拍重力/阻尼；松键首拍仍完整消耗上拍保留的 `+0.20` |
| **梯子自由下滑**（`ladder_passive_slide`） | 全程恒定 **`-0.200001`**（末端 `mpdY = -0.2744`） | 每拍先在 `DefaultMoveSystems` 钳位 `max(-0.2744, -0.2) = -0.20`，移动后再经 `(-0.2 - 0.08) × 0.98 = -0.2744` 循环 |
| **梯子按潜行悬停**（`ladder_sneak_halt`） | 进入潜行后 **`vy = 0.000000`**（末端 `mpdY = -0.0784`）；松开潜行首拍以 **`-0.0784`** 起滑 | `DefaultMoveSystems` 将上拍的 `-0.0784 < 0` 清零为 `0.0`，随后重力+阻尼写回 `(0 - 0.08) × 0.98 = -0.0784` |
| **上升惯性中按潜行**（`ladder_sneak_after_rise`） | 松 Space 同时按住潜行：前两拍继续 **`+0.117599 → +0.036846`**（`snk=1`），直至第 3 拍 `mpdY` 变负（`-0.042289`）后才归零为 `0.000000` | 直接验证 `0x148b61d5d` 必须同时满足 `mPosDelta.y < 0` 才清零 |
| **4 种藤蔓对照**（`vine_variants`） | `vine`、`twisting_vines`、`weeping_vines`、`cave_vines` 均复现完全一致的 **`+0.200001` / `-0.200001` / `0.000000`** 曲线 | 7 种 `Climbable \| CanHaltWhenClimbing` 方块在移动与悬停计算上完全等价 |
| **藤蔓内地面疾跑穿行**（`vine_sprint_through`） | 穿过藤蔓全程水平速度持续逼近 **`0.280609` b/t**（在藤蔓格内 `0.279968 → 0.280426`） | 证明无 Java 版 `±0.15` 水平速度钳位 |

### 4.2 梯子上方的开活板门（`open_trapdoor_above_ladder` / `open_trapdoor_climb_from_ladder`）

- 实验布置：`y = -56..-54` 梯子上方放置朝 `-Z` 打开的活板门（`minecraft:trapdoor`, `direction=2, open_bit=1`，经 `block_shapes` 确认门板盒为 `[430, -53, 425]..[431, -52, 425.1825]`），玩家水平置于 `z = 425.5`（AABB `[425.2, 425.8]` 完全避开门板阻挡）。
- **实测结果**：
  1. 悬在活板门格内（`y = -52.8`）起点不激活攀爬：直接以纯重力 **`-0.0784 → -0.155231`** 下落到 `y = -53.03363`（脚底进入 `-54` 梯子），触发 `JumpFromGroundSystem` 反弹 `+0.200001` 回到 `-52.83363`，随后在活板门格内按纯空气递推 **`+0.117599 → +0.036846 → -0.042290`** 重新下落 `[实测]`。
  2. 从梯子 `y = -55.0` 连续按 Space 攀爬至 `y = -52.833622`（进入 `-53` 活板门格）：当帧立即脱离攀爬进入空气减速 **`+0.117599 → +0.036846`**。确证开活板门在基岩版**不可攀爬** `[代码]` `[实测]`。

### 4.3 脚手架（`scaffolding_supported` / `scaffolding_overhanging`）

- **碰撞形状 `ScaffoldingBlock::getCollisionShape`（`0x1481ae570`，虚表 `0x14ea10860`）**：当 `stability(0x151e2d0a0) != 7`、`entity != nullptr`、`!canDescendThroughBlock()`、`aabb.min.y >= float(pos.y + 1) - 1e-6f` 且水平相交时返回完整立方体 `[0,0,0]..[1,1,1]`，否则返回空盒 `[代码]`。
- **有下方支撑的脚手架**：
  - 站在顶部：`on_ground = true`、`over_scaffolding = true`、`over_descendable_block = true`。
  - **按潜行穿行下降**：写入 `DescendThroughBlock(71)`，稳态 **`-0.150002` b/t**；松潜行首拍 `mpdY = -0.147 = -0.15 × 0.98`（`vy = -0.146999`），下一拍才恢复重力 `(-0.147 - 0.08) × 0.98 = -0.22246`，直接证明 `MobMovementGravity` 在下降态跳过重力而 `MobMovementDrag` 仍乘 `0.98` `[代码]` `[实测]`。
  - **按 Space 上升**：稳态 **`+0.150002` b/t**；松键首拍为 **`(0.15 - 0.08) × 0.98 = +0.0686`**，之后为 `-0.011173` `[代码]` `[实测]`。
- **悬空（下格为空气）的脚手架**：
  - 站在悬空脚手架顶部：`over_scaffolding = true` 但 **`over_descendable_block = false`**；按住潜行后 `descend_through_block = false`，玩家稳稳停在 `y = -48.0, vy = 0.0` 不会穿底跌落 `[代码]` `[实测]`。
  - 处于悬空脚手架内部（`y = -48.6`）：`in_scaffolding = true, in_ascendable_block = false`；由于 `JumpFromGroundSystem`（`0x1495ea9a8`）判断 `InScaffolding || InAscendableBlock`，按 Space 仍正常以 **`+0.150002` b/t** 上升到顶部 `[代码]` `[实测]`。

### 4.4 细雪（`powder_snow`）+ 皮革靴 vs 光脚

| 测试分支 | 状态位与 `slow_y` | 实测垂直速度（b/t） | 精确解析式 `[代码]` `[实测]` |
| --- | --- | --- | --- |
| **穿皮革靴 + Space 上升** | `in_asc = 1, over_desc = 1, slow_y = 1.5` | **`+0.224998`**（`4.50` b/s） | `JumpFromGroundSystem` 优先命中 Priority 1（`InAscendableBlock`）写 `+0.15` → `applySlowdownOnMove` 乘 `1.5` 得 **`+0.225`** |
| **穿皮革靴 + `W` 顶墙自动攀爬** | `in_asc = 1, over_desc = 1, slow_y = 1.5` | **`+0.299999`**（`6.00` b/s） | 无 Space 时 `HorizontalCollisionFlag && isOnLadder()` 触发 `AutoClimbSystem` 写 `+0.20` → 乘 `1.5` 得 **`+0.300`**（比梯子快 **50%**）；松 `W` 后直接落回 `-0.117599`（因 `applySlowdownOnMove` 每拍清零 `mPosDelta`） |
| **穿皮革靴从顶面潜行下穿** | 顶面 `over_desc = 1, slow_y = 0.0`；入雪后 `slow_y = 1.5` | 首拍 **`-0.150002`**，次拍起恒定 **`-0.224998`** | 进入前 `BlockMovementSlowdownMultiplier` 尚为 0，首拍直接以 `-0.15` 穿入顶面；进入后每拍 `-0.15 × 1.5 = -0.225` |
| **光脚在细雪中下沉（含按 Space）** | `in_asc = 0, over_desc = 0, slow_y = 1.5` | 恒定 **`-0.117599`**（`-2.352` b/s） | `isOnLadder()` 与 `InAscendableBlock` 均为假，每次 `Move` 前只累积上一拍 `applySlowdownOnMove` 清零后的重力+阻尼 `(0 - 0.08) × 0.98 = -0.0784` → 乘 `1.5` 得 **`-0.1176`** 匀速下沉，不触发重力累加 |

### 4.5 摔落伤害实机对照（生存模式，`fallDamage = true`，`health` 满血 `20.0`）

| 跌落目标与方式 | 起始 Y → 落点 Y | 实测剩余生命值 | 机制归因 `[代码]` `[实测]` |
| --- | --- | --- | --- |
| **石块对照组（无缓冲）** | `-41.0 → -56.0` | **`9.0`**（扣血 `11.0`） | 验证生存摔落伤害生效 |
| **梯子柱** | `-41.0 → -48.10957` | **`20.0`**（`0` 伤） | `LadderResetFallDamageSystem`（`0x14c617b20`）与 `DefaultMoveSystems`（`0x148b61cc2`）清零 `FallDistance` |
| **脚手架（全程不按潜行）** | `-41.0 → -50.0` | **`20.0`**（`0` 伤） | **`BlockClimberSystem`（`0x14489e948`）在 `OverScaffolding(70)` 为真时清零 `FallDistanceComponent`** |
| **脚手架（按住潜行穿过）** | `-41.0 → -52.35180` | **`20.0`**（`0` 伤） | `ScaffoldingActionSystem`（`0x147cdcd80`）与 `BlockClimberSystem` 均清零 `FallDistance` |
| **细雪（光脚跌落穿透）** | `-39.0 → -50.09919` | **`20.0`**（`0` 伤） | `PowderSnowBlock::onFallOn` 为空实现（`MCFOLD`）且 `adjustFallDistance`（`0x1448a8eb4`）在 `slowdown` 激活时清零 `FallDistance` |
| **细雪（穿皮革靴跌落）** | `-39.0 → -49.0` | **`20.0`**（`0` 伤） | 实测穿靴高速落入仅下沉 1 格停在 `-49.0`，且同样受 `onFallOn` 空实现 + `BlockClimberSystem`/`adjustFallDistance` 保护无伤害 |

---

## 5. 基岩版 vs Java 版核心差异汇总

| 对比项 | Java Edition | Bedrock Edition `1.26.51.01` `[代码]` `[实测]` |
| --- | --- | --- |
| **梯子/藤蔓被动下滑** | `-0.15` b/t | **`-0.20` b/t**（`DefaultMoveSystems` `0x148b61cd6`） |
| **梯子/藤蔓水平速度上限** | 钳位至 `±0.15` b/t | **无任何钳位**（实测藤蔓内疾跑可达 `0.2806` b/t） |
| **上升速度（梯子 / 4 种藤蔓）** | `+0.20` b/t | `+0.20` b/t；推墙自动攀爬额外跳过当帧重力/阻尼（松键多保留 1 拍 `+0.20`） |
| **梯子上方开着的活板门** | **可继续攀爬** | **完全不可攀爬**（离开梯子立刻切回空气自由落体） |
| **梯子/藤蔓判定盒** | AABB 相交即判定 | 仅检测单点 `(floor(mPos.x), floor(AABB.min.y), floor(mPos.z))` |
| **脚手架判定盒与速率** | 全碰撞箱判定、`±0.15` b/t | 全水平 AABB 足迹判定（支持边缘搭边起攀）、`±0.15` b/t；下降态跳过重力但保留 `0.98` 阻尼 |
| **无潜行落地脚手架摔落伤害** | 正常扣摔落伤害 | **`0` 伤害**（`BlockClimberSystem` 清零 `FallDistanceComponent`） |
| **细雪 + 皮革靴** | 无 `1.5×` 垂直缩放 | Space **`+0.225` b/t**、潜行 **`-0.225` b/t**、推墙 **`+0.300` b/t**（全游戏最快攀爬）；光脚恒速下沉 **`-0.1176` b/t** |

---

## 6. 未闭合静态边界（诚实披露）

- **细雪 `1.5f` 乘数写入源 VA**：本轮已通过实机 `RecordSession` 观察槽确认 `BlockMovementSlowdownMultiplierComponent`（`0xf6091f58`）`+4` 为 `1.5`，且消费者侧 `BlockMovementSlowdownMultiplierSystem::applySlowdownOnMove`（`0x1448a98c0`）与 `adjustFallDistance`（`0x1448a8d30`）已静态完全闭合；细雪将 `(0.9f, 1.5f, 0.9f)` 写入该组件的精确写入点 VA 暂标 `unresolved`（保留至 `cobweb_slowing_blocks` 课题与冰冻/effect box 一并收尾）。
- **细雪 `PowderSnowBlock::getCollisionShape` 摔落高度下沉阈值静态地址**：穿皮革靴从高处跌落细雪会下沉 1 格停在 `-49.0` 已获 `[实测]` 确认，精确 `> 2.5f` 判断指令 VA 尚未单独拆读。
