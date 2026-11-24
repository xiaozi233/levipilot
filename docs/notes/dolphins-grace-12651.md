# 海豚的恩惠（Dolphin's Grace）完整机制与逐 tick 实测（1.26.51.01）

> 版本：`Minecraft.Windows.exe` `1.26.51.01`（LeviLamina `26.51.7`，`image_base = 0x140000000`）。
> 证据约定：`[代码]` 反编译/反汇编与调用链；`[常量]` `.rdata` 浮点与组件 ID；`[实测]` LeviPilot 逐 tick 录制与 ECS 内存采样；`[推断]` 由前述直接证据补出的推导。
> 复现脚本：[`verify_dolphins_grace.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/dolphins-grace-12651/probes/verify_dolphins_grace.py)（输出原始对照表至本地 `out/dolphins-grace-1.26.51.01/live-results.json`）。

---

## 0. 结论摘要

1. **底层不是 `MobEffect`，而是独立的 `MovementEffectType::DolphinBoost`**：全二进制不存在 `dolphin_grace` / `dolphins_grace` 药水效果字串；海豚的恩惠以 `MovementEffectType::DolphinBoost = 1` 写入 `MovementEffectsComponent`（`0x5c92321d`）槽位 `[1]`，通过 `SwimSpeedMultiplierComponent`（`0x60fc5a9a`）输出倍率 **`2.0f`**（而非 Java 版的状态效果叠加乘算）`[代码]` `[常量]`。
2. **必须处于 `SWIMMING` 泳姿才触发扫描与生效，但不会被障碍物阻挡、不要求海豚在水中**：
   - 仅当 `ActorDataFlagComponent` 含 `SWIMMING`（`ADF[+7] & 2`）时，`scan`（`0x148b94f30`）才递减 `ScanForDolphinTimerComponent`（`0xb8b9c219`）；当 `timer <= 1` 时重置为 **`60` ticks** 并在该帧以玩家当前 `AABBShapeComponent` **各轴向外扩展 `±5.0` 格** 扫描任意非自身的 `DolphinFlagComponent`（`0xcbd360fc`）实体 `[代码]` `[常量]` `[实测]`。
   - 只要玩家非泳姿（如水底涉水行走），即使站在海豚身旁，`timer` 也**完全冻结**且 `SwimSpeedMultiplier` 保持 `1.0`；一旦切入泳姿，若周围 `±5.0` 扩展盒内有活海豚（即使隔着实心石墙、海豚在无水房间内），`mDuration = 60` 与 `2.0f` 倍率立刻激活 `[代码]` `[实测]`。
3. **连续游动在 60-tick 周期交界处实现 0-tick 无缝续期**：同一调度函数 `0x1468353c0` 中，`DolphinBoostSystem`（`0x148b64570`）先于 `WaterTravelSystem`（`0x1468261c6`）及阻力系统 `WaterTravel_DragPerEntity`（`0x14682658B`），而 `MovementEffectsTick`（`0x1468399f1` → `0x1490daf80`）排在移动之后并在 `prev <= 1` 时清空槽位；因此第 61 拍由 `scan` 在阻力与移动前刷新回 `60`，速度曲线零断档 `[代码]` `[实测]`。
4. **跳过 Depth Strider 阻力插值，并改用独立加速公式（彻底屏蔽迅捷药水与落地减半）**：
   - **阻力侧（`WaterTravel_DragPerEntity` `0x142dee630`）**：`SwimSpeedMultiplier > 1.0f` 时跳过向 `0.546` 的插值，保留纯水水平阻力（疾跑 **`0.9`**，非疾跑 **`0.8`**）`[代码]` `[常量]` `[实测]`。
   - **加速侧（`WaterTravelSystem` `0x14c61f1d0` / `0x14207c030`）**：不再混合 `groundSpeed`（迅捷/缓慢与疾跑属性 `1.3x` **完全被忽略**），也不受 `OnGroundFlagComponent` 离地 `0.5x` 惩罚影响，改按下列闭式计算基础水加速度 `a`：
     $$a = 0.02 \cdot \left(0.7 + 0.3 \cdot \frac{\min(L, 3)}{3}\right) \cdot \text{SwimSpeedMultiplier}$$
     当 `SwimSpeedMultiplier = 2.0` 时，$L = 0, 1, 2, 3$ 对应 $a = 0.028 / 0.032 / 0.036 / 0.040$（即无海豚游速的 `1.4x / 1.6x / 1.8x / 2.0x`），正向前游（$M' = 0.98$）稳态速度依次为 **`0.2744` / `0.3136` / `0.3528` / `0.3920` b/t**；若穿 **Depth Strider III + Speed II 及以上药水**，开海豚的恩惠反而比无海豚更慢（如 Speed V 下 `0.3919` vs `0.4953`/`0.5612` b/t）`[代码]` `[实测]`。

---

## 1. 基岩版与 Java 版的机制差异对照

| 维度 | Java Edition | Bedrock Edition `1.26.51.01` `[代码]` `[实测]` |
| --- | --- | --- |
| 底层实现 | 原版状态效果 `MobEffect`（`dolphins_grace`），可由指令给予且显示 HUD 图标 | **`MovementEffectType::DolphinBoost = 1`**（`MovementEffectsComponent[1]`），无药水 ID 与 HUD 图标；通过 `SwimSpeedMultiplierComponent = 2.0f` 驱动 |
| 触发范围 | 玩家周围球形/半径搜索 + 药水效果持续 `5s`（`100` ticks） | 仅在 `SWIMMING` 态且 `ScanForDolphinTimer <= 1` 时，对玩家 `AABBShapeComponent` **三轴各扩 `±5.0` 格** 查 `DolphinFlagComponent` |
| 触发条件 | 在水中接近海豚（常规给予 5 秒效果） | 必须保持 `SWIMMING` 泳姿（非泳姿涉水不触发且倒计时冻结）；**不检查射线遮挡，也不要求海豚处于水中** |
| 与 Depth Strider 交互 | 在水阻力递推中乘以固定系数，可叠加深度探索者、迅捷与疾跑 | `SwimSpeedMultiplier > 1.0` 时跳过阻力向 `0.546` 的插值（保 `0.9`/`0.8`），改用 $0.02(0.7 + 0.3 \cdot L/3)\times 2.0$，**忽略 `groundSpeed` 与离地判定** |
| 与迅捷药水叠加 | 迅捷提高速度并与海豚加速叠加 | **`WaterTravelSystem` 在 `SwimSpeedMultiplier > 1.0` 分支完全不读 `groundSpeed`**；对穿 Depth Strider III 的玩家，迅捷 V + 海豚（`0.3919` b/t）反而明显慢于单迅捷 V（水底 `0.5612` / 悬空 `0.4953` b/t） |

---

## 2. ECS 管线顺序与五大子系统（静态闭包）

### 2.1 同一配置链内的调度顺序

参考 [tick-order-12651.md](tick-order-12651.md) 已证的 `DefaultEntitySystemsCollection` 追加+正序语义，在系统装配链 `0x1468353c0` 中，海豚加速、水上旅行、水阻力与移动效果递减的相对注册顺序如下 `[代码]`：

1. `0x1468375f5` → `0x14682d3c0` → `0x146822800` → `0x14681da00`（`0x1468230da`）→ **`DolphinBoostSystem::registerSystems`**（`0x148b64570`，调用点 `0x146820662`）
2. **`WaterTravelSystem`**（`0x1468261c6`）与水阻力系统 **`WaterTravel_DragPerEntity`**（`0x14682658B`）
3. `0x1468399f1` → `0x1490c4690` 登记 **`MovementEffectsTick`**（更新循环 `0x1490daf80`）

### 2.2 `DolphinBoostSystem::registerSystems`（`0x148b64570`）五个阶段

| 阶段 | 地址 | 读/写组件与核心常量 | 关键逻辑 `[代码]` `[常量]` |
| --- | --- | --- | --- |
| 1. `scan` | `0x148b94f30` | 读 `ADF[+7] & 2`（`SWIMMING`）；读写 `ScanForDolphinTimerComponent`（`0xb8b9c219`）；写 `ScanForDolphinFlagComponent` | 仅当 `SWIMMING` 为真时执行：若 `timer <= 1`，重置 `timer = 60`（`0x3c`）并挂上 `ScanForDolphinFlagComponent`；否则 `timer -= 1`。整个 PE 中仅有 6 个函数引用 `0xb8b9c219`，`scan` 是唯一重置点；退出泳姿时计时器停在当前值不递减 |
| 2. `findDolphins` | `0x148b95710` | 读 `ScanForDolphinFlagComponent`、`AABBShapeComponent`、`DolphinFlagComponent`（`0xcbd360fc`）；写 `IsNearDolphinsFlagComponent` | 取玩家 `AABBShapeComponent`（`min/max`），各轴加减 **`±5.0f`**（`0x40a00000` / `0xc0a00000`），查询空间内非自身的含 `DolphinFlagComponent` 实体；命中即加 `IsNearDolphinsFlagComponent`。无球形距离裁剪、无视线射线检测 |
| 3. `PredictDolphinBoost` | 客户端 `0x148b996f0`；服务端 `0x148b97d50` → `0x142fe8990` | 读 `IsNearDolphinsFlagComponent`；读写 `MovementEffectsComponent`（`0x5c92321d`） | 命中近海豚标记且 `[1].mDuration < 60 && [1].mDuration != -1` 时，按类型下标 `mType = 1` 写入 `{mStartTick=0, mDuration=60, mType=1}`（`0x10000003c`） |
| 4. `swimSpeedModifier` | `0x148b9a790` | 读 `ADF[+7] & 2`、`MovementEffectsComponent[1]`；写 `SwimSpeedMultiplierComponent`（`0x60fc5a9a`） | 当且仅当 `SWIMMING` 为真且 `effects.size() >= 2 && effects[1].mType != -1` 时写入 **`2.0f`**（`0x14e6022a8`），否则写入 **`1.0f`**（`0x14e5a7060`）。全 PE 15 处引用 `0x60fc5a9a` 的函数中，此处是唯一会写入 `!= 1.0f` 的节点 |
| 5. `RemoveDolphinScanComponents` | `0x148b943b0` | 清 `ScanForDolphinFlagComponent` 与 `IsNearDolphinsFlagComponent` | 清理本 tick 的瞬态扫描标记 |

### 2.3 `MovementEffectsTick`（`0x1490daf80`）递减与失效时序

`0x1490daf80` 遍历 `MovementEffectsComponent` 中的每个 16 字节 `MovementEffect`（`{uint64 mStartTick; int32 mDuration; int32 mType;}`）：

- 跳过 `mType == -1` 或 `mDuration == -1` 的永久/空槽；
- 若读取到的 `prev = mDuration == 0`，立即用全零常量 `0x14e6ae400` 清空整槽（使 `mType = -1` 生效于之后需要重新读槽的系统）；
- 否则写入 `mDuration = prev - 1`，并在 **`prev <= 1`**（`cmp edx, 1; jle 0x1490daf90`）时当场清槽 `[代码]`。

因此，当玩家停止游泳但 `effects[1]` 尚未清零时，`SwimSpeedMultiplier` 会立即因 `SWIMMING=false` 回落到 `1.0f`；若在 60 ticks 内重新进入泳姿且海豚已离开，只要 `[1]` 仍保留，加速会立刻重新激活直至原本的 `mDuration` 归零 `[推断]`。此外，若连续在海豚旁保持泳姿，`scan` 会在 `timer` 从 `60` 递减至 `1` 的那一帧（即第 61 拍）先于 `WaterTravel` 和 `MovementEffectsTick` 重新将 `[1]` 写回 `60`，实现 **0-tick 断档续期** `[代码]` `[实测]`。

---

## 3. `WaterTravelSystem` 与 `WaterTravel_DragPerEntity` 公式闭合

玩家分支 `0x14c61f1d0`（与生物镜像 `0x14207c030`，详参 [re-fluid-12651.md §R-F1/R-F2](re-fluid-12651.md)）先检查 `SwimSpeedMultiplierComponent`（`0x60fc5a9a`）：

### 3.1 `SwimSpeedMultiplier <= 1.0f`（常规水中与深海探索者分支）

先取基础水加速度 $a_{\text{base}} = 0.02$；若实体无 `OnGroundFlagComponent`（脚部离地悬空），额外乘 **`0.5f`** 得到有效等级比例 $h$：

$$h = \begin{cases}
\dfrac{\min(L, 3)}{3} & \text{接地（有 } \texttt{OnGroundFlagComponent}\text{）} \\[6pt]
\dfrac{1}{2} \cdot \dfrac{\min(L, 3)}{3} & \text{悬空（无 } \texttt{OnGroundFlagComponent}\text{）}
\end{cases}$$

随后同时向地面属性插值：

$$a_{\text{regular}} = a_{\text{base}} + (\text{groundSpeed} - a_{\text{base}}) \cdot h, \qquad
\rho = \rho_0 + (0.546 - \rho_0) \cdot h$$

其中 $\rho_0 = 0.9$（疾跑）或 $0.8$（非疾跑）；`groundSpeed` 受疾跑（`×1.3`）及迅捷/缓慢药水放大。

### 3.2 `SwimSpeedMultiplier > 1.0f`（海豚的恩惠激活分支）

1. **加速写入（`0x14c61f1d0`）**：跳过上述整段 `groundSpeed` 与 `OnGroundFlagComponent` 分支，直接执行 `[代码]` `[常量]`：
   $$a_{\text{boost}} = a_{\text{base}} \cdot \left(0.7 + 0.3 \cdot \frac{\min(L, 3)}{3}\right) \cdot \text{SwimSpeedMultiplier}$$
2. **阻力跳过（`WaterTravel_DragPerEntity` `0x142dee630`）**：检查 `SwimSpeedMultiplier > 1.0f` 时直接跳过向 `0.546` 的插值，保持 $\rho = \rho_0$（疾跑 `0.9`、非疾跑 `0.8`、垂直 `0.8`）`[代码]` `[常量]`。
3. **理论稳态表**（按 [fluid-movement.md](../fluid-movement.md) 正向前移动输入模长 $M' = 0.98$，`v_steady = (0.98 * a) / (1 - 0.9)`）：

| Depth Strider 等级 $L$ | `0.7 + 0.3*(L/3)` 因子 | 倍率 `2.0` 后 $a$ | 实操有效 $b = 0.98a$ | 阻力 $\rho$（疾跑泳） | 理论稳态速度 $v_{\infty}$（b/t） |
| --- | --- | --- | --- | --- | --- |
| `0` | `0.70` | `0.028` | `0.02744` | `0.9` | **`0.2744`**（`5.488` b/s） |
| `1` | `0.80` | `0.032` | `0.03136` | `0.9` | **`0.3136`**（`6.272` b/s） |
| `2` | `0.90` | `0.036` | `0.03528` | `0.9` | **`0.3528`**（`7.056` b/s） |
| `3` | `1.00` | `0.040` | `0.03920` | `0.9` | **`0.3920`**（`7.840` b/s） |

> **迅捷药水反协同阈值**：不带海豚的恩惠时，对于 $L = 3$ 且脚部离地（$h = 0.5$、$\rho = 0.723$）的玩家，当 $\text{groundSpeed}$ 满足 $0.98 \cdot (0.02 + 0.5(\text{groundSpeed} - 0.02)) / (1 - 0.723) > 0.3920$（即 $\text{groundSpeed} > 0.2016$）时，**不开海豚反而比开海豚更快**。常规疾跑基础属性 `0.13` 下，**Speed II**（`×1.4` → `0.182`）在水底贴地即可超过（$v_\infty \approx 0.3578$ vs 开海豚水底同样只有 `0.3920`，**Speed III** 悬空与水底均已反超）。本次使用 **Speed V**（`×2.0` → `groundSpeed = 0.26`）在实机上直接验证了这一负收益 `[实测]`。

---

## 4. 逐 tick 实测对质结果

所有试验在同一封闭超平坦水道会话内完成，通过 `RecordSession(period=1)` 采集玩家 `vz`（`Actor::getVelocity()` 已完成位移 `mPos - mPosPrev`）并利用 `fit_recursion(v_{t-1}, v_t)` 对加速段做线性回归，$a_{\text{fit}} = b_{\text{fit}} / 0.98$，`a_imp = (steady_vz * 0.1) / 0.98`（当 $\rho = 0.9$ 时）：

### 4.1 速度、阻力与药水/状态交互对照（14 组试次）

| 试次 | 条件 | `SwimSpeedMult` | `on_ground` | 拟合阻力 `fit_rho` | 拟合残差 | 拟合 `a_fit` | 实测稳态 `vz`（b/t） | 理论预测 / 结论对照 `[实测]` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `baseline_no_dolphin_ds0` | 无海豚，DS 0，泳姿 | `1.0` | `0.00` | `0.89998` | `2.63e-5` | `0.02000` | `0.19589` | 吻合 $\rho=0.9, a=0.02$（`0.1960`） |
| `no_dolphin_ds3_midwater` | 无海豚，DS III，悬浮泳 | `1.0` | `0.00` | `0.72310` | `2.35e-5` | `0.07497` | `0.26535` | 吻合 $h=0.5$：$\rho=0.723, a=0.075$（`0.26534`） |
| `no_dolphin_ds3_ground` | 无海豚，DS III，贴底泳 | `1.0` | `1.00` | `0.54719` | `2.31e-5` | `0.12966` | `0.28061` | 吻合 $h=1.0$：$\rho=0.546, a=0.130$（`0.28062`） |
| `dolphin_ds0_midwater` | 有海豚，DS 0，悬浮泳 | `2.0` | `0.00` | **`0.90002`** | `2.59e-5` | **`0.02800`** | **`0.27431`** | 精确匹配 `0.2744`（$0.028\times 0.98 / 0.1$） |
| `dolphin_ds1_midwater` | 有海豚，DS I，悬浮泳 | `2.0` | `0.00` | **`0.89999`** | `2.36e-5` | **`0.03200`** | **`0.31349`** | 精确匹配 `0.3136`（`coast` 已停加速段因 `SWIMMING` 随之退出、`mult` 归 `1.0`，阻力立即切回 `0.7576`） |
| `dolphin_ds2_midwater` | 有海豚，DS II，悬浮泳 | `2.0` | `0.00` | **`0.90002`** | `2.26e-5` | **`0.03599`** | **`0.35270`** | 精确匹配 `0.3528` |
| `dolphin_ds3_midwater` | 有海豚，DS III，悬浮泳 | `2.0` | `0.00` | **`0.89997`** | `2.95e-5` | **`0.04001`** | **`0.39190`** | 精确匹配 `0.3920`（跳过阻力插值至 `0.723`） |
| `dolphin_ds3_ground` | 有海豚，DS III，贴底泳 | `2.0` | `1.00` | **`0.89994`** | `2.88e-5` | **`0.04002`** | **`0.39188`** | 贴底不降也不增：阻力仍 `0.9`，不受 `OnGround` 影响；松键停止游泳第 2 拍切回 `0.546` |
| `speed5_ds3_no_dolphin_midwater` | Speed V + DS III，无海豚悬浮 | `1.0` | `0.00` | `0.72306` | `1.55e-5` | `0.13997` | **`0.49530`** | 吻合 $a=0.02+0.5(0.26-0.02)=0.14$（`0.49531`） |
| `speed5_ds3_no_dolphin_ground` | Speed V + DS III，无海豚贴底 | `1.0` | `1.00` | `0.54681` | `2.34e-5` | `0.25954` | **`0.56122`** | 吻合 $\rho=0.546, a=0.26$（`0.56123`） |
| `speed5_ds3_with_dolphin_midwater` | Speed V + DS III，**有海豚**悬浮 | `2.0` | `0.00` | **`0.89999`** | `2.18e-5` | **`0.04000`** | **`0.39193`** | **与无药水海豚完全一致**，速度反降 `~0.10`–`0.17` b/t |
| `walk_no_swim_near_dolphin` | 贴海豚旁水底行走（未进泳姿） | `1.0` | `1.00` | `0.80004` | `2.31e-5` | `0.02000` | `0.09798` | `effects[1].type == -1`，`SwimSpeedMult = 1.0` 未触发 |
| `dry_walled_dolphin_ds0` | 石墙完全隔断的无水小房间海豚 | `2.0` | `0.00` | **`0.90000`** | `2.58e-5` | **`0.02800`** | **`0.27395`** | 仍正常触发（`±5.0` AABB 扩展无视遮挡与干湿） |
| `far_dolphin_dist7_ds0` | 水平距离 `7.0` 格外的海豚（`timer` 预排空至 `4`） | `1.0` | `0.00` | `0.90007` | `2.70e-5` | `0.01999` | `0.19575` | 超出 `±5.0` 盒不触发（`effects[1].type == -1`） |

### 4.2 刷新与失效边界实测

- **`continuous_90t_refresh`（持续游过海豚侧旁，初始 `timer = 5`）**：
  - 第 `6` 帧起切入 `0.088104`（前一帧 `0.067413` → `0.067413 * 0.9 + 0.02744 = 0.088112`），随后连续加速并在第 60～79 帧稳定单调趋近 **`0.274323`**（`i=60..68` 依次为 `0.273773, 0.273834, 0.273895, 0.273926, 0.273987, 0.274017, 0.274078, 0.274109, 0.274139`），跨过 60-tick 周期边界**零掉速** `[实测]`。
- **`expire_after_kill`（单次触发后立即移出/击杀海豚）**：
  - 触发帧 `i=6`：`vz` 从 `i=5` 的 `0.053131`（$a = 0.02002$）跃升至 `0.075256`（$v_6 - 0.9 v_5 = 0.027438 \Rightarrow a = 0.02800$）。
  - 保持增益至 `i=66`（`vz = 0.274048`, $a = 0.02799$），至 `i=67` 单拍跌落至 `0.266235`（$0.266235 - 0.9 \times 0.274048 = 0.019592 \Rightarrow a = 0.01999$），此后按 $a=0.02, \rho=0.9$ 滑回常规稳态 `0.196`（`i=104` 时已降至 `0.197418`）。本次共维持了 `i=6..66` 共 `61` 帧增益（客户端写入 `mDuration = 60` 并于 `prev <= 1` 清槽，`61` 帧边界包含触发后服务端同周期状态同步延迟 `1` tick `[推断]`）。
- **实验坑记（`ScanForDolphinTimerComponent` 冻结）**：当玩家停止泳姿、`SWIMMING=false` 时，`scan` 跳过更新导致 `timer` **原地冻结在当前读数**；若不重新进入泳姿消耗剩余 `timer`，海豚即使在身旁也不会触发，且因 `swim_stop` 存在 ~3 tick 状态滞后，测试前预排空 `timer` 须在 `6..10` 提前停键以避免回绕重置到 `60` `[代码]` `[实测]`。
