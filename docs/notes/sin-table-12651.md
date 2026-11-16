# 正弦查找表（`SIN_TABLE`）与 `sinf`/`cosf` 分工说明（`1.26.51.01`）

> 目标：记录 Minecraft Bedrock **`1.26.51.01`**（`Minecraft.Windows.exe`）中 65536 项正弦查找表
> `SIN_TABLE` 的地址、初始化方式、查表规则，对比 Java Edition / Minecraft Parkour Wiki
> 的 `Angles` 约定，并厘清“为何有些代码仍查表，而普通 WASD 移动已改用 CRT `sinf`/`cosf`”。
>
> **证据标签**：`[代码]` = 本版本反汇编直接确认；`[常量]` = 只读段字面量/位模式确认；
> `[推断]` = 由静态公式推得的行为推论；`[未核]` = 本轮尚未开展实机或逐槽位比对验证的边界。

---

## 一、核心结论

1. **基岩版 `1.26.51.01` 仍保留与 Java 同规模的 `65536` 项正弦查找表**：首址 `0x151d6973c`，
   占用 `0x40000` 字节（`65536` 个 `float`），在启动时由 `.CRT$XCU` 静态初始化器调用
   CRT `sinf(float(i) / 10430.378f)` 填充 `[代码][常量]`。
2. **基岩版与 Java/MCPK `Angles` 高度相似，但初始化方式有细微差异**：两者均采用
   `10430.378f`（$\approx 65536 / (2\pi)$）与 `16384.0f`（$\pi/2$ 槽位偏移）做 `int32`
   截断后取低 16 位索引；`.rdata` 中**不存在** OptiFine FastMath 的 `4096` 槽因子
   `651.8986f`（`0x4422f983`）。但 Java 使用双精度 `Math.sin(i * 2π / 65536.0)` 建表，
   基岩版改用单精度 `sinf(float(i) / 10430.378f)` 建表 `[代码][常量]`。
3. **运动与玩法系统已分化为两条路径**（全二进制静态扫描共定位 `269` 处 RIP-相对直接引用，分布在
   `193` 个函数/叶函数簇条目中；其中 `48` 个条目含 `sqrt` 指令，`145` 个不含 `sqrt` `[代码]`）：
   - **普通地面/空中 WASD 水平加速**（`DefaultMoveSystems - Ground/Air`）等已改用 MSVC CRT
     `sinf` / `cosf` `[代码]`；
   - **疾跑跳跃 `0.2f` 水平冲量**（`JumpFromGroundSystem`）、**蓄力跳 `0.4f` 冲量**
     （`ApplyJumpModifierSystem`）、**鞘翅滑翔**（`GlideMoveSystem`）、**长矛突刺**
     （`LungeEnchant`）、**坐骑冲刺**（`DashActionSystem`）、**激流三叉戟**
     （`StartSpinAttackActionSystem`）、**游泳俯仰**（`SwimControlSystem`）、**击退/推挤**、
     **投射物/AI/视角/Molang 缓动/方块网格/渲染粒子/相机/`/tp`** 等仍内联查 `SIN_TABLE` `[代码]`。
     其中 **激流三叉戟** 与 **水下坐骑冲刺** 虽查 `SIN_TABLE`，但先做了 `sqrtss` 三维单位化
     再乘系数，不吃未归一化 LUT 模长偏差 `[代码]`。
4. **全二进制 `193` 条目中真正吃未归一化 LUT 模长增量的移动玩法仅两类 5 处**：
   - **A 类（直接正角公式）**：`JumpFromGroundSystem`（疾跑跳跃 `0.2f` 冲量）、`DashActionSystem`
     陆地分支（陆地坐骑冲刺）、`ApplyJumpModifierSystem`（`PowerJumpFlagComponent` 蓄力跳
     `0.4f * scale` 冲量）`[代码]`；
   - **B 类（`-π - yaw_rad` 反号偏移公式）**：`LungeEnchant::slot[10]`（长矛突刺）与
     `GlideMoveSystem` 烟花推进支路（鞘翅烟花）`[代码]`；
   - 另有 `LocalMoveVelocityComponent` 相关系统（`0x1496090e0`）在低速微步分支施加 `0.1f`
     LUT 步进，其余已排查高频条目（载具寻路、划桨动画、环形渲染、击退/推挤、激流、水下坐骑冲刺等）
     均无正交配对模长 `> 1` 加速窗口 `[代码]`。
5. **最大大半角理论增益为 `+0.3063%`，但客户端原版旋转与 `/tp` 均将其锁死在 `[-180°, +180°)`**：
   - **A 类**小半角最大在 `134.961548°`（`1.0000480358`），`+64` 档最大大半角在
     **`5898195.0°`**（`1.0030630315`）；**B 类**在 `[-180°, +180°]` 内**不存在** `+1`
     档小半角（须越界到 `-314.961548°`），其 `+64` 档最大大半角在 **`-5898375.0°`**
     （同样命中 `1.0030630315`），而 `±5898195.0°` 对 B 类完全不加速 `[常量][推断]`；
   - 客户端 `CameraDirectLookSystem` 与 `TickUpdatePlayerFromCamera` 每 tick 从相机四元数反解欧拉角并用
     `fmodf(..., 360.0f) - 180.0f` 折回 `[-180°, +180°)`，`/tp` 亦然；因此原版手动转视角、
     宏和 `/tp` 仅能触及 **A 类 `134.961548°` 小半角**。仅网络入口 `PlayerAuthInputPacket::isValid`
     放行任意有限 `float32` 朝向且服务端原样同步 `[代码]`。

---

## 二、`SIN_TABLE` 结构、初始化与查表公式

### 1. 内存布局与运行期初始化

| 项目 | 数值 / 地址（`1.26.51.01`） | 说明与证据 |
| --- | --- | --- |
| 表首址（`SIN_TABLE`） | `0x151d6973c`（RVA `0x11d6973c`） | 位于 `.data` 段未初始化 BSS 尾区（紧随其后 `0x151da973c` 即 `+0x40000` 处为 `4×4` 单位矩阵）`[代码]` |
| 表长度与类型 | `65536` 项 `float32`（`0x40000` = `262144` 字节） | 由初始化循环 `cmp rsi, 0x10000` 与 `memcpy` 长度 `0x40000` 确认 `[代码]` |
| 静态初始化函数 | `.CRT$XCU` 项 `0x14f0fc888` → `0x142647070..0x14264719f` | 归零前置状态 `0x151d696f0` 后按 4 路展开计算并写入 `0x151d6973c` `[代码]` |
| 建表公式 | `sinf(float(i) / 10430.378f)`，$i \in [0, 65535]$ | 单精度 `cvtsi2ss` + `divss [0x14e6ae484]` 后调用 IAT `0x14f1045b8`（`api-ms-win-crt-math-l1-1-0.dll!sinf`），再整块 `memcpy` 到表首址 `[代码]` |
| 代码引用规模 | `.text` 共 `269` 处 RIP-相对直接访问（`193` 个条目：`186` 个 `.pdata` 函数 + `7` 个无 `.pdata` 叶函数簇） | 按索引寻址 `[reg + idx*4 + 0x11d6973c]` 为 `0` 处；早期仅统计 `lea` + `.pdata` 的 `220` / `182` 口径漏掉了直接 `[SIN_TABLE]` 的 `movss`/`addss`/`mulss` 及非 `.pdata` 叶函数（如 `0x149024fba`、`0x14026e450`/`0x14026e4f0`）。`193` 条目中 `48` 个含 `sqrt` 指令，`10` 个同时调用 CRT `sinf`/`cosf`；作为对照，CRT IAT `sinf`（`0x14f1045b8`）与 `cosf`（`0x14f104488`）在 `.text` 分别有 `101` / `96` 处引用 `[代码]` |

### 2. 查表公式与关键 `.rdata` 常量

| 常量 / 字段 | VA | IEEE-754 位模式 | `float32` 精确值 |
| --- | --- | --- | --- |
| 角度转弧度 `deg2rad` | `0x14e6ae480` | `0x3c8efa35` | `0.01745329238474369`（`0.017453292f`）`[常量]` |
| 弧度转槽位 `rad2idx` | `0x14e6ae484` | `0x4622f983` | `10430.3779296875`（`10430.378f`）`[常量]` |
| 余弦四分之一周期偏移 | `0x14e6ae488` | `0x46800000` | `16384.0`（`16384.0f`）`[常量]` |
| 疾跑跳水平冲量 | `0x14e6ae454` | `0x3e4ccccd` | `0.20000000298023224`（`0.2f`）`[常量]` |
| 蓄力跳水平冲量基数 | `0x14e602290` | `0x3ecccccd` | `0.4000000059604645`（`0.4f`）`[常量]` |

以内联的 `mce::Math::sin` / `cos` 为例，角度转索引与读取指令模式完全一致 `[代码]`：

```text
angle_rad = yaw_deg * 0.017453292f          ; 如调用方从角度制出发
sin_idx   = (uint16_t)(int32_t)(angle_rad * 10430.378f)
cos_idx   = (uint16_t)(int32_t)(angle_rad * 10430.378f + 16384.0f)

sin_val   = SIN_TABLE[sin_idx]              ; cvttss2si r32, xmm + movzx r32, r16
cos_val   = SIN_TABLE[cos_idx]              ; movss xmm, dword ptr [base + r64*4]
```

- `cvttss2si` 为向零截断有符号 `int32`，`movzx ..., r16` 截取低 16 位，对通常角度范围
  （$\lvert \text{index} \rvert < 2^{31}$）与 Java `(int) x & 65535` 等价，负角度同样按补码低
  16 位回绕到 `[0, 65535]` `[代码]`。

---

## 三、与 Java Edition / MCPK `Angles`（及 FastMath）对比

| 维度 | Java `MathHelper`（MCPK 默认口径） | OptiFine FastMath | 基岩版 `1.26.51.01`（`SIN_TABLE`） |
| --- | --- | --- | --- |
| 表容量 | `65536` 项 `float` | `4096` 项 `float` | **`65536` 项 `float`** `[代码]` |
| 弧度乘子 / `cos` 偏移 | `10430.378F` / `+ 16384.0F` | `651.8986F` / `+ 1024.0F` | **`10430.378f` / `+ 16384.0f`**；`.rdata` 查无 `651.8986f`（`0x4422f983`）或合一度数常量 `182.04445f`（`0x43360b61`）`[常量]` |
| 角度→索引乘法顺序 | `deg * 0.017453292F` 后再 `* 10430.378F` | 同左逻辑转 4096 槽 | **与 Java 相同的两段 `mulss` 顺序**（保留中间 `float32` 舍入）`[代码]` |
| 建表计算方式 | `(float) Math.sin((double) i * Math.PI * 2.0D / 65536.0D)`（双精度、精确周期 $2\pi$） | 同双精度缩减至 4096 项 | **`sinf((float) i / 10430.378f)`**；因 `10430.378f` 单精度实值为 `10430.3779296875`（略小于 $65536/(2\pi)$），满 `65536` 项会微幅越过 $2\pi$ 且带单精度 `sinf` 舍入 `[代码]` |
| 常规 WASD 移动是否查表 | 是（`moveFlying` 等统一用 `MathHelper`） | 是 | **否**：`DefaultMoveSystems` 已直接调用 CRT `sinf`/`cosf` `[代码]` |

> 说明：基岩版 `SIN_TABLE` 各槽位与 Java 原版 65536 表的具体 ULP 差值，以及两者在实际斜跑/起跳轨迹上的数值分歧大小，本轮未做逐槽打印比对 `[未核]`。

---

## 四、`SIN_TABLE` 与 CRT `sinf`/`cosf` 在移动、战斗与其余模块中的实际分工

下表列出与玩家、坐骑及实体位移/战斗直接相关的核心系统；涉及位置增量时，写回对象均为
`StateVectorComponent::mPosDelta`（即待运动阶段应用的位移增量，而非上拍已完成位移
`mPos - mPosPrev`）`[代码]`：

### 1. 玩家、坐骑移动与战斗核心路径

| 机制 / 系统 | 注册或函数地址（`1.26.51.01`） | 三角函数来源与归一化 | 关键行为与触发条件 |
| --- | --- | --- | --- |
| **普通地面/空中 WASD 水平加速** | `DefaultMoveSystems - Ground`（`0x148b7f7f0`）、`Air`（`0x148b82540`）→ 共享核心 `0x148b61af0` → `0x148b618a0` | **CRT `sinf` / `cosf`**（IAT `0x14f1045b8` / `0x14f104488`） | 先检查原始三轴输入平方和 `distSq < 0.0001f`（`0x14e75d7c4` = `0x38d1b717`）则跳过；否则 `yaw_rad = mRot.y * 0.017453292f` 送入 `sinf`/`cosf`，按 `MobTravelComponent+0xc / max(1.0f, dist)` 旋转后叠加到 `mPosDelta.(x, z)` `[代码]`。常规地面行走与空中按键加速不存在 `65536` 查表量化 `[推断]` |
| **疾跑跳跃水平冲量** | `JumpFromGroundSystem`（注册 `0x146823ebf` → 核心 `0x1495e9200` `0x1495e940c..0x1495e9472`） | **`SIN_TABLE`**（未归一化，无 `sqrt`） | 门控为 `ActorDataFlagComponent` bit 3（`SPRINTING`）置位 `[代码]`。**只看实体当前水平朝向 `mRot.y`（不依赖按键方向）**，向 `mPosDelta.(x, z)` 写入：`x -= 0.2f * SIN_TABLE[sin_idx]`、`z += 0.2f * SIN_TABLE[cos_idx]` `[代码]`。覆盖 `LocalPlayerJumpRequestComponent`（`0xda6293f1`）、`OtherJumpRequestComponent`（`0xdcca6ef9`）、`SquidJumpRequestComponent`（`0x830513d5`）；`LavaSlimeJumpRequestComponent`（`0x18ba6395`，`0x1495e9620`）不进此路径 `[代码]` |
| **蓄力跳附加水平冲量** | `ApplyJumpModifierSystem`（`0x14903eb20` → `0x14903ee30` → `0x149024ef0` 内嵌无 `.pdata` 叶块 `0x149024fba..0x14902501a`） | **`SIN_TABLE`**（未归一化，无 `sqrt`） | 受 `PowerJumpFlagComponent`（`0x706f184f`）门控，按同套查表向 `mPosDelta.(x, z)` 施加 `± 0.4f * JumpPendingScale * SIN_TABLE[...]`；普通玩家不触发 `[代码]` |
| **长矛突刺（Spear Lunge）** | `LungeEnchant::slot[10]`（`0x14519ca20..0x14519d0d7`，vtable `0x14e872de0` / slot `0x14e872e30`，`"enchantment.lunge"` ID `0x29` = `41`，`item.enchant.lunge1/2/3`） | **`SIN_TABLE`**（未归一化，无 `sqrt`） | 校验 `!PassengerComponent`（`0x98e40c0e`）、`!WasInWaterFlagComponent`（`0x78e89f39`）、`!ActorDataFlag 0x20`（`32 = GLIDING`）；非创造模式（GameType `1`）或特定旁观态（`5` 且 `+0x460 == 1`）要求 `minecraft:player.hunger > 6.0f`（`0x14e6022b4 = 6.0`, `0x151d1ecf8` 经 `0x140247da8`）；同步处理虚表 `+0x9e8`、`0x142835d80`（`ClientAcceptanceThresholdsComponent`）、`ExhaustionComponent+0x24`、音效 `0x236/0x237/0x238` 与耐久 `[代码]`。向 `mPosDelta` 施加纯水平冲量：`x -= 0.458f * level * cos(pitch) * SIN_TABLE[uint16((-pi - yaw_rad) * 10430.378f)]`、`y += 0`、`z -= 0.458f * level * cos(pitch) * SIN_TABLE[uint16((-pi - yaw_rad) * 10430.378f + 16384.0f)]` `[代码]`；在 `yaw ∈ [-720°, 720°]` 上 2D LUT 模长范围 `[0.9999520, 1.0000479]`（`min` 在 `-135.181°`，`max` 在 `-315.401°`），最大偏差仅 `±0.0048%` `[推断]` |
| **激流三叉戟发射（Riptide）** | `StartSpinAttackActionSystem`（注册 `0x14b17b540` → `0x14b198c70` / vtable `0x14eac9d90..0x14eac9df0` → 核心 `0x14b198490..0x14b198837`，查询 `0x14eac9dc0`） | **`SIN_TABLE` + `sqrtss` 单位化** | 查询 `ActorRotationComponent`、`OnGroundFlagComponent`、`WasInWaterFlagComponent`、`ActorHeadInWaterFlagComponent`、`ActorIsImmobileFlagComponent`、`MovementAttributesComponent`、`ActorIsKnockedBackOnDeathFlagComponent`、`MovedOnSpawnComponent`、`PlayerIsSleepingFlagComponent`、`PlayerComponent` `[代码]`。在 `0x14b19861e` 用 `SIN_TABLE` 算三维方向后，**立即在 `0x14b19869c` 用 `sqrtss`（`>= 0.0001f`）严格单位化**，再乘 `3.0f * (float(level) + 1.0f) * 0.25f`（1/2/3 级分别对应 `1.5` / `2.25` / `3.0`），并在分支 `cl != 0` 时做垂直补偿（`al == 0` 加 `+0.08f`，否则 `/ 0.8f * 0.98f`）后写入 `mPosDelta`（`0x14b19872a..0x14b198743`）`[代码]`。发射模长不会因 LUT 产生额外放大 `[推断]` |
| **坐骑冲刺（Mount Dash）** | `DashActionSystem`（`0x148b5fe80`，`minecraft:dash_action`）与水下辅助 `0x148b5fc80` | **陆地未归一化 `SIN_TABLE`；水下 `SIN_TABLE` + `sqrtss`** | 读取 `cooldown_time`、`horizontal_momentum`、`vertical_momentum`、`can_dash_underwater` `[代码]`。`can_dash_underwater == 0`（如骆驼）走 `0x148b60200..0x148b6024b` 直接用未归一化 2D `SIN_TABLE` 写入 `mPosDelta`；`can_dash_underwater != 0` 走 `0x148b5ffb4..0x148b601fb` → `0x148b5fc80`，于 `0x148b5fd44` 经 `sqrtss`（`>= 0.0001f`）归一化三维向量后写入 `mPosDelta` `[代码]` |
| **鞘翅滑翔视线与投影因子** | `GlideMoveSystem`（`sub_146667B10`，注册 `0x146826333`） | **`SIN_TABLE`**（内部含 `sqrtss` 水平与三维投影） | 先做 $360^\circ$ 包角插值算三维视线向量 $(l_x, l_y, l_z)$（`0x146667c35..0x146667ca4`），再读取未插值 `mRot.x * 0.017453292f` 算 `d3` 的 `cos(p_rad)`（`0x146667caa..0x146667d1e`），两处均内联查 `SIN_TABLE`，并计算 $l_H = \sqrt{l_x^2 + l_z^2}$ 等投影项 `[代码]`；公式见 [`../elytra-movement.md`](../elytra-movement.md) |
| **动能武器射线判定（Kinetic Weapon）** | `DealKineticDamageSystem`（`0x148b603f0`）与 `0x146d93fa0` | **`SIN_TABLE` + `sqrtss`**（`0x146d940b2`） | 用 `SIN_TABLE` 计算攻击视线/判定框射线几何，不写回玩家 `mPosDelta` `[代码]` |
| **游泳俯仰控速（SwimControl）** | `SwimControlSystem`（`0x1490c0a00`，worker `0x1490cc320`） | **`SIN_TABLE`**（一维俯仰，无 `sqrt`） | 以 `SIN_TABLE` 计算目标垂直速率 $\tau = -\sin(\text{pitch})$，通过 `vy += k * (tau - vy)` 或置 `0` 更新 `mPosDelta.y` `[代码]`；流体实机验证见 [`re-fluid-12651.md`](re-fluid-12651.md) |
| **击退与实体推挤（Knockback / Push）** | `ApplyKnockbackRulesComponent`（`0x143271fc0`）、`PlayerComponent`+`PushableByEntityComponent`（`0x143271e40`）、`PushableByEntityComponent`（`0x1433db040`） | **`SIN_TABLE` + `sqrtss`** | 三者均包含 `sqrt` 归一化；其中 `0x143271fc0` 混用 `sqrtss` 向量归一化与基于 `SIN_TABLE` 的偏转/角度计算（含常数 `1.6f`）`[代码]` |
| **局部移动速度与乘客偏航限制** | `LocalMoveVelocityComponent`（`0x1496090e0`）、`PassengerYRotLimitComponent`（`0x148b874d0`） | **`SIN_TABLE`**（无 `sqrt`） | `0x1496090e0` 用 `SIN_TABLE` 与 `0.01f`/`0.1f` 更新 `(x, z)`；`0x148b874d0` 限制乘客水平朝向 `[代码]` |
| **三维朝向向量共用辅助函数** | `getViewVector`（`0x1419f8860`）与无 `.pdata` 叶函数 `0x14026e450`、`0x14026e4f0` | **`SIN_TABLE`**（无 `sqrt`） | `0x1419f8860` 对 `mRot`/`mRotPrev` 做 $360^\circ$ 包角插值后查 `SIN_TABLE` 输出三维视线向量（`.text` 被 `32` 个调用方调用 `35` 次，涵盖投射物、`CameraAimAssist`、`VanillaCamera`、寻路、`Flocking`、`ScriptActor`、`GameTest` 与粒子）；`0x14026e450..0x14026e4e6`（被 `0x14265ad90`、`0x1426785a0` 调用 `3` 次）与 `0x14026e4f0..0x14026e583`（被 `0x142345780` 调用 `1` 次）共处同一 308B 非 `.pdata` 叶函数簇 `[代码]` |

### 2. 其余 `SIN_TABLE` 引用分布总览（`193` 条目全盘）

全 `.text` 静态扫出 `269` 处 RIP-相对直接访问，归属 `193` 个条目（`186` 个 `.pdata` 函数 + `7` 个无 `.pdata` 叶函数簇，其中 `0x14026e450`/`0x14026e4f0` 合并为同一簇；`48` 个条目含 `sqrt`、`145` 个不含 `sqrt`、`10` 个兼调 CRT `sinf`/`cosf`）。按静态分类汇总如下 `[代码]`：

| 模块 / 类别 | 规模与代表地址（`1.26.51.01`） | 说明 |
| --- | --- | --- |
| **玩家 / 实体物理与战斗** | 上表主体 + 关联函数（静态物理战斗组 `14` 个及 `0x1495e9200`、`0x149024fba`、`0x14020f430`、`0x145d82170`、`0x1490c0e60`、`0x14c9eafd0` 等） | 包含冲量、俯仰控速、冲刺、击退、推挤与动能攻击射线判定 `[代码]` |
| **共用视线向量辅助** | `0x1419f8860`（`32` 调用方 / `35` 次调用）及叶函数簇 `0x14026e450`/`0x14026e4f0` | 为投射物、相机辅助瞄准、脚本、测试与粒子提供统一 `getViewVector` 查表 `[代码]` |
| **Molang / 缓动函数 / 朝向查询** | `21` 个函数 | 含表达式求值/优化器三角分支（`0x140aea370/470/560`、`0x140aeb360/4c0/620`，后者由 `0x140ae6140` 调用）、`12` 个缓动函数（`0x140b2fbb0..0x140b335c0`，含常数 `1.570796`/`3.141593`/`6.283185`/`-0.075`/`0.3`/`10.0`）以及 `query.cardinal_player_facing` / `query.cardinal_facing` / `query.cardinal_facing_2d`（`0x142225390/5b0/7d0`）`[代码]` |
| **投射物与发射** | `12` 个函数 | 发射初速方向分解与散布计算 `[代码]` |
| **生物 AI / LookControl / 寻路 / Goals** | `18` 个函数 | 朝向目标、随机游走、视角转向与移动目标计算 `[代码]` |
| **相机（Camera）** | `3` 个主条目（`0x1466fe8c0`、`0x146730450`、`0x146730930`）+ `0x1467628d0` | 视角与相机姿态变换 `[代码]` |
| **方块与地形网格几何（`0x1460xxxx`）** | `5` 个函数（`0x146038030..0x1460a4760`） | 局部旋转与网格顶点变换 `[代码]` |
| **渲染 / 粒子 / UI / 客户端表现** | `33` 个函数 | 客户端模型旋转、粒子、动画与 UI 绘制 `[代码]` |
| **世界生成 / 结构 / 命令** | `5` 个函数 | 含 `minecraft:ore_feature`（`0x14b6bc3f0`）、`TeleportCommand` `/tp` 朝向计算（`0x14b607140`）以及 `0x14230af10`、`0x146aa7d10`、`0x14c56a2d0` `[代码]` |
| **其余叶函数簇与未展开辅助函数** | 其余无 `.pdata` 叶块 `0x141759340`、`0x141c43350`、`0x14231230d`、`0x142584582`、`0x142640160`（3 处引用）及未逐一命名的零散内部辅助函数 | 合计构成全部 `193` 个条目；零散内部辅助函数尚未逐个逆向命名 `[未核]` |

### 顺带：疾跑起跳 `tick t` 与 `tick t+1` 的速度衔接与阻力顺序

结合 [`tick-order-12651.md`](tick-order-12651.md) 已确认的 ECS 调度链，疾跑起跳拍不存在“跳起瞬间立刻切为空中摩擦”的错觉 `[代码]`：

1. **起跳拍 `tick t`**：`JumpFromGroundSystem` 与跳后辅助系统（`0x14961a7a0`、`0x14961a410`）在加竖直速度与 `0.2f` 疾跑冲量时**均不移除 `OnGroundFlagComponent`（`0xc29078a0`）**，该标记直到本拍移动与碰撞收尾的 `FinalizeMoveSystem`（`0x1463b39a0`，`0x1463b3d22..0x1463b3fb6`）才被移走 `[代码]`。
2. 因此 `tick t` 的移动阶段中，`TravelTypeSensingSystem`（`0x1490dc7a0`）仍看见 `OnGroundFlagComponent`，分配 `GroundTravelFlagComponent`（`0xe1ca9e6b`）：先经 `GroundTravelTypeSystem`（`0x14666cee0`）与 `DefaultMoveSystems - Ground` 叠加地面 WASD 加速（走 `sinf`/`cosf`），碰撞移动后在 `MobMovementFriction::Ground`（`0x142df9da0`）对 `mPosDelta` 乘地面摩擦 `S * 0.91f`（默认地面 `0.546f`）`[代码]`。
3. **次拍 `tick t+1`**：`OnGroundFlagComponent` 已清除，`TravelTypeSensingSystem` 改派 `AirTravelFlagComponent`（`0xb147b030`），由 `MobAirSpeed`（`0x142e10b00`）写入 `AirSpeedComponent`（`0x9b8f9061`），经 `DefaultMoveSystems - Air` 施加空中加速（走 `sinf`/`cosf`），拍末 `MobMovementFriction::Air`（`0x142dfbd20` → `0x142de2010`）仅乘空气阻力 `0.91f`（`0x3f68f5c3`）`[代码]`。

---

## 五、真正存在加速收益的系统筛选

对全二进制 `193` 个 `SIN_TABLE` 调用条目（含 `145` 个不含 `sqrt` 的条目）逐一核查寄存器写入去向、是否与 `90°` 配对及是否随后除以 `sqrt` 模长后，**真正把未归一化 LUT 的 `(sin, cos)` 正交配对模长偏差直接放大并写回 `mPosDelta` 的核心玩法只有两类 5 处** `[代码]`：

| 角度公式分类 | 系统 / 函数 | RVA / VA | 触发与作用方式 | 写回目标 | 状态 |
| --- | --- | --- | --- | --- | --- |
| **A 类**：直接正角 | `JumpFromGroundSystem` | `0x1495e9200` | 疾跑起跳水平 `0.2f` 冲量 | 水平 `mPosDelta.(x, z)` | `[代码]` |
| **A 类**：直接正角 | `DashActionSystem`（陆地分支） | `0x148b5fe80` | `can_dash_underwater == 0` 坐骑陆地冲刺直接 2D LUT 冲量（水下分支转调 `0x148b5fc80` 经 `sqrtss` 单位化，不吃放大） | `mPosDelta` | `[代码]` |
| **A 类**：直接正角 | `ApplyJumpModifierSystem` | `0x149024ef0` / `0x149024fba` | `0.4f * JumpPendingScale` 蓄力跳水平冲量（受 `PowerJumpFlagComponent` 门控，普通玩家不触发） | 水平 `mPosDelta.(x, z)` | `[代码]` |
| **B 类**：`-π - yaw_rad` | `LungeEnchant::slot[10]` | `0x14519ca20` | 长矛突刺附魔，以 `0.458f * level * cos(pitch)` 乘未归一化 2D LUT | 水平 `mPosDelta.(x, z)` | `[代码]` |
| **B 类**：`-π - yaw_rad` | `GlideMoveSystem`（烟花推进尾段） | `0x146667b10`（`0x146667c85..0x146667ca4`、`0x146667f51..0x146667fc0`） | 烟花支路计算 `mPosDelta += 0.1f * l + 0.5f * (1.5f * l - mPosDelta)`；`0x146667d0e..0x146667d2d` 的 `sqrtss` 仅用于重力/升力缩放，滑翔重定向（`0x146667de5`、`0x146667e48`、`0x146667e8a`、`0x146667eb1`）按 `sqrt(l_x^2 + l_z^2)` 归一化，唯独烟花加法支路未除三维 `sqrtss` 模长 | 三维 `mPosDelta` | `[代码]` |

> 注：`GlideMoveSystem` 烟花支路还会叠加 `0.5f * mPosDelta` 混合与按 `360°` 包角插值的视线向量，实际单拍放大比例取决于当下朝向与现有 `mPosDelta` `[代码][推断]`。

### 其余零散微步与逐项排除的高疑条目

1. **零散移动 / 限制写入项**：
   - `LocalMoveVelocityComponent` 相关系统（`0x1496090e0`）：仅在水平分量平方 `< 0.01f` 时用未归一化 `SIN_TABLE` 施加 `0.1f` 微步更新 `[代码]`；
   - `PassengerYRotLimitComponent`（`0x148b874d0`）：仅写回角度或乘员旋转限制分量，不直接施加超额直线加速 `[代码]`。
2. **逐项反汇编后已排除正交配对模长 `> 1` 增益的条目**：
   - **激流三叉戟与击退/推挤**：`StartSpinAttackActionSystem`（`0x14b198490`）及击退/推挤函数（`0x143271fc0`、`0x143271e40`、`0x1433db040`）虽查 `SIN_TABLE`，但均调用 `sqrtss` 先单位化方向矢量再乘力度 `[代码]`；
   - **载具 AI 寻路**：`VehicleGoal`（`0x14c9eafd0`，位于 `0x146dafc60` 行为群组下）在移动写点处改用 `sqrtss`（`0x14c9eb7d3`）与 CRT `sinf`/`cosf`（`0x14c9eb800`/`0x14c9eb80c`）计算 `mPosDelta` `[代码]`；
   - **一维或标志/动画/渲染用途**：`SwimControlSystem`（`0x1490c0a00`）仅查一维 `sin(pitch)`；`BoatPostNormalTickSystem`（`0x1482cfc90`、`0x1482d0450`）只算 `10.0f` 划桨动画标量；`SwimNeighbor`（`0x1490c0e60`）为位标志判断与 `sqrt`；`LocalPlayer` 辅助（`0x145d82170`）仅算 `fmodf` 与插值视线；`NeighborGlide`（`0x14663bc00`）为环形渲染几何循环 `[代码]`。

---

## 六、小半角与最大加速大半角（Pattern A / Pattern B）

### 1. 机制与全 `float32` 空间上限

游戏查表先按 `base` 计算正弦索引 `i_s = int32(base) & 0xFFFF`，再在单精度下加 `16384.0f` 得到余弦索引 `i_c = int32(base + 16384.0f) & 0xFFFF` `[代码]`。当 `|base|` 增长到 `float32` 相邻可表示数的间距（ULP）`≥ 1` 时，`base + 16384.0f` 会向上舍入进位，使两个索引相对理想 `+16384` 相位差额外偏出 `Δ = ((i_c - i_s - 16384) mod 65536)` 个槽位 `[代码][推断]`。

基于从 PE 提取的建表公式 `sinf(float(i) / 10430.378f)` 与查表常量（`0x151d6973c`、`10430.378f`、`16384.0f`），在单精度 `base ∈ [2^27, 2^31)` 区间穷举 `[常量][推断]`：

- **理论最大槽偏与峰值模长**：最大有效偏置为 **`Δ = +64` 槽**，对应全表格最大水平欧氏模长 **`1.0030630315`**（**`+0.306303%`**），精确出现在 `base = 1073733568.0`（IEEE-754 位模式 `0x4e7fff7f`，查表槽位对 `i = (57280, 8192)`）；
- **负半轴不触发大半角**：在 `base ∈ [-2^31, -2^14]` 区间穷举，单精度负向向偶舍入无法产生 `+1..+64` 槽的正向增量，整个负半轴最大模长仅 `1.0000002270`（`base = -39918.0`）。因此无论 A 类还是 B 类，**最大加速角必须使各自的 `base` 为接近 `+2^30` 的正值** `[常量][推断]`。

### 2. A 类与 B 类半角数值全览

两类核心加速公式如下 `[代码]`：

- **Pattern A**（`JumpFromGroundSystem`、陆地 `DashActionSystem`、`ApplyJumpModifierSystem`）：
  `base = (yaw_deg * 0.017453292f) * 10430.378f`
- **Pattern B**（`LungeEnchant::slot[10]`、`GlideMoveSystem` 烟花支路）：
  `base = (-π - yaw_deg * 0.017453292f) * 10430.378f`

| 挡位（槽偏 `Δ`） | Pattern A 最大角 (`yaw_deg`) | Pattern B 最大角 (`yaw_deg`) | LUT 二维模长 `√(s²+c²)` | 相对增益 | 证据 |
| --- | --- | --- | --- | --- | --- |
| **`+64`（最大值）** | **`5898195.0°`**（`0x4ab3ffa6`，`ulp = 0.5`） | **`-5898375.0°`**（`0xcab4010e`，`ulp = 0.5`） | **`1.0030630315`** | **`+0.306303%`** | `[常量][推断]` |
| `+64` 邻近候选 1 | `5898196.5°` | `-5898376.5°` | `1.0030602304` | `+0.306023%` | `[常量][推断]` |
| `+64` 邻近候选 2 | `5898193.5°` | `-5898373.5°` | `1.0030584969` | `+0.305850%` | `[常量][推断]` |
| `+64` 邻近候选 3 | `5898197.5°` | `-5898377.5°` | `1.0030501316` | `+0.305013%` | `[常量][推断]` |
| `+32` | `2949075.0°` | `-2949254.75°` | `1.0015326439` | `+0.153264%` | `[常量][推断]` |
| `+16` | `1474515.0°` | `-1474694.88°` | `1.0007665059` | `+0.076651%` | `[常量][推断]` |
| `+8` | `737235.125°` | `-737415.125°` | `1.0003834095` | `+0.038341%` | `[常量][推断]` |
| `+4` | `368595.625°` | `-368775.625°` | `1.0001917374` | `+0.019174%` | `[常量][推断]` |
| `+2` | `184274.516°` | `-184454.516°` | `1.0000958446` | `+0.009584%` | `[常量][推断]` |
| `+1`（小半角峰值） | **`134.961548°`**（`0x4306f628`） | **`-314.961548°`**（`0xc39d7b14`） | **`1.0000480358`** | **`+0.004804%`** | `[常量][推断]` |

需要特别注意 Pattern B 与 Java / Pattern A 的两个直觉陷阱 `[常量][推断]`：

1. **直接复用 Java 大半角对长矛 / 鞘翅完全无效**：在 Pattern B 下，代入 `+5898195.0°` 得到模长 `0.9999998986`（`Δ = 0`），直接取反代入 `-5898195.0°` 得到 `1.0000000940`（`Δ = 0`）；必须额外扣除 `-180°`（由公式中的 `-π` 产生）至 **`-5898375.0°`** 才会命中同一槽位 `base = 1073733568.0`。
2. **`[-180°, +180°]` 内对 Pattern B 不存在 `+1` 档小半角**：在 `[-180°, +180°]` 全区间做 3,600,001 点扫描，Pattern B 最大模长仅 **`1.0000002270`**（`yaw = 39.276199°`），必须越出 `[-180°, +180°)` 到 **`-314.961548°`** 才有 `+1` 档 `1.0000480358`。而 Pattern A 在 `[-180°, +180°]` 内的最佳小半角为 `134.961548°`（略偏离 Java 版 `135.005493°` / `0x43070168` 的 `1.0000479893`，源于基岩版用 `0.017453292f`、`10430.378f` 与单精度建表 `sinf`）。

---

## 七、朝向归一化锁与原版手动不可达闭环

Java 版哪怕不用命令也可靠连转宏缓慢累积 `yaw` 到 `5898195°`；但基岩版 `1.26.51.01` 的客户端相机与旋转管线每 tick 都强行折叠角度，**导致原版手动旋转、转视角宏与 `/tp` 全部被锁死在 `[-180°, +180°)`** `[代码]`：

1. **`CameraDirectLookSystem`**（系统注册 `@0x1408b9044`，tick `0x146703a30`）：对增量累加后的弧度俯仰/偏航直接调用 `fmodf(angle + π, 2π)`（`0x146703c3e`）折回 `[-π, +π)`，写入相机四元数（`[rsi+0x30..0x3c]`），相机本身不累积多圈角度 `[代码]`。
2. **`TickUpdatePlayerFromCamera`**（系统注册 `0x1408b9a67..0x1408b9a9f`、字符串 `@0x14eca31bb`、vtable `@0x14e99fdc0` `+0x28`=`0x14676ce10` → `0x146733b00` → `0x146733290`）：
   - 先由相机四元数经 `atan2f` 还原欧拉角（`yaw = atan2f(...) * 57.2958 - 90`、`pitch = atan2f(...) * -57.2958`），写入 `[Actor+0x228]`（即 `ActorRotationComponent*`，可由 `0x141a21430..0x141a214cd` 按组件哈希 `0x75df36b7` 写入 `[r13+0x228]` 佐证）及 `ActorHeadRotationComponent`（`0xbabe7211`）；
   - 随后在 `0x1467339be..0x146733ab7` 对 `yaw`（`[rsi+4]`）、`yawPrev`（`[rsi+0xc]`）、`pitch`（`[rsi]`）、`pitchPrev`（`[rsi+8]`）全部执行正余数 **`fmodf(angle + 180.0f, 360.0f) - 180.0f`**，强行锁回 `[-180°, +180°)`；
   - 即使命中 `ControlSchemeComponent` 分支（`0x146733524`）跳过四元数重算，控制流仍汇入 `0x1467339be` 执行同一套 `fmodf` `[代码]`。
3. **其余相机与命令入口均折叠**：
   - `CameraInputTransformSystem`（`@0x14ec34c3d`，`0x1448c3da0` → `0x1448c2d10`）含 `fmodf(..., 360.0f) - 180.0f`；`CameraVehicleRotationLookSystem`（`0x146769a30` → `0x1467327f0`）含 `fmodf(..., 2π) - π` `[代码]`；
   - **`TeleportCommand`**：数值朝向分支（`0x14b606df0`，`0x14b607057..0x14b6070bc`）与 facing 分支（`0x14b608670`，`0x14b608872..0x14b60896a`）同样对 `yaw` 执行 `fmodf(yaw + 180.0f, 360.0f) - 180.0f` 并将 `pitch` 夹在 `[-89°, +89°]` `[代码]`。
4. **网络包校验与服务端同步反而放行**：
   - `PlayerAuthInputPacket::isValid`（`0x143c8b9c0`）对 `mRot`（`+0x30`、`+0x34`）仅检查绝对值 `<= 0x7f7fffff`（即合法有限 `float32`，非 NaN/Inf），不做 `[-180, 180)` 截断；
   - `"Player Rotation"` 序列化器（`0x143d6f8f0`/`0x143d6faf0`）、`UpdateServerPlayerInputSystem` worker（`0x1428bd7d0` → `0x1428a23d0`）及 `SetPreviousPosRotSystem`（`0x141a78390`/`0x14686c670`）均原样拷贝 `mRot` 到服务端组件且无 `fmodf` `[代码]`。

### 可行性总表

| 操作方式 | `yaw` 可达范围 | Pattern A 可用加速 | Pattern B（长矛突刺 / 烟花鞘翅）可用加速 |
| --- | --- | --- | --- |
| 原版手动转视角 / 转视角宏 | `[-180°, +180°)` | 仅小半角 `134.961548°`（`+0.004804%`） | **无 `+1` 档半角**（最大 `1.0000002270`） |
| `/tp` 数值朝向 / `facing` | `[-180°, +180°)` | 仅小半角 `134.961548°`（`+0.004804%`） | **无 `+1` 档半角**（最大 `1.0000002270`） |
| 自定义 `PlayerAuthInputPacket::mRot` / 内存写角度 | 任意有限 `float32` | 最高 **`5898195.0°`**（`+0.306303%`） | **`-314.961548°`**（`+0.004804%`）/ 最高 **`-5898375.0°`**（`+0.306303%`） |

> **未核实边界**：本笔记中 `SIN_TABLE` 建表/查表、`269` 处/`193` 条目分布、加速系统筛选、`tick t / t+1` 地面→空中过渡，以及客户端相机/`/tp`/`PlayerAuthInputPacket` 朝向链均基于 `1.26.51.01` **纯静态反汇编核实** `[代码]`；各半角数值由提取出的单精度 `sinf`/查表常量经全 `float32` 空间计算推导 `[常量][推断]`。疾跑跳跃、长矛突刺、激流发射与坐骑冲刺的实机逐 tick 位置差拟合，以及自定义 `PlayerAuthInputPacket::mRot` 发包在实机端到端的物理/防作弊响应**尚未上机实测** `[未核]`；鞘翅滑翔与游泳俯仰的既有实机对照分别见 [`../elytra-movement.md`](../elytra-movement.md) 与 [`re-fluid-12651.md`](re-fluid-12651.md)。
