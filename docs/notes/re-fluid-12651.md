# 1.26.51.01 流体运动 —— 反编译证据册

> 配套文档：[`fluid-movement.md`](../fluid-movement.md)（行为/实测结论）。本文只放**静态证据**：
> 函数地址、写入点、门条件、常量位模式、组件 id。
>
> 目标二进制：Minecraft Bedrock **1.26.51.01**（LeviLamina 26.51.7），
> `%APPDATA%\levilauncher.exe\versions\1.26.51.01\Minecraft.Windows.exe`。
> 静态读库：同目录 `.i64`（IDA 9.4，**只读复用**，不重跑自动分析）。
> 版本相关：所有地址只对 51 有效；45 的地址见 §4 对照表。
>
> 证据标签沿用仓库约定：**[R]** 本文的函数级静态证据（带地址）；**[实测]** 见行为文档；
> **[J]** Java 26.2 jar 反编译（只作对照，不是基岩事实）。

---

## 0. 证据索引

| ID | 机制 | 函数（51） | 关键写入点 | 结论 |
|---|---|---|---|---|
| R-F1 | 水水平/竖直阻力 | `sub_142DEE630` | `0x142DEE7B7` / `0x142DEE7C9` / `0x142DEE7D3` | `vx,vz *= d`，`vy *= 0.8`；`d = 疾跑?0.9 : (WaterMovement ?? 0.8)`，深海行者向 0.546 混 |
| R-F2 | 水（玩家）重力 | `sub_142E08780`、`sub_142E09020` | `0x142E08816`、`0x142E09125` | 写入 **-0.005f**（`0xBBA3D70A`）；门 = ADF bit57 SWIMMING = 0（查询上下文含 `WaterTravelFlag`/`Player`） |
| R-F3 | 重力施加 | `sub_142E0D280` | `0x142E0D28A` | `vy += *(float*)gravityComp`；`gravityComp[4]==1` 时清摔伤 |
| R-F4 | 岩浆阻力 | `sub_142DF0B80` | `0x142DF0F9A/FA8/FB6`、`0x142DF0DB1/DBF/DCD` | 三个速度分量 **各 ×0.5**；系统过滤器要求 `LavaTravelFlag` |
| R-F5 | 岩浆重力 | `sub_142DE3260`（兄弟 `sub_142DE2FC0`） | `0x142DE352F`、`0x142DE3238` | 写入 **-0.02f**（`0xBCA3D70A`）；±半格方块探针 |
| R-F6 | 液体判定盒 | `sub_142DEB930`、`sub_142DEB4B0` | `0x142DEB9BA/E4/F4` 等 | AABB 内缩 0.1(X/Z)、0.4(Y) + SubBBs；`ImmuneToLavaDrag` 短路 |
| R-F7 | 空气重力选择 | `sub_142DE3560` | `0x142DE3662` / `0x142DE3682` | 缓降且 `vy<0` → -0.01；否则 **-0.08** |
| R-F8 | 空气竖直阻力 | `sub_142DF1790` | `0x142DF179C` | **0.98**（另有 0.91 的鞘翅/滑翔分支） |
| R-F9 | 泳姿俯仰控制 | lambda `sub_1490C0A00`（worker `sub_1490CC320`） | `0x1490C0B0F` 平滑 / `0x1490C0B31` 置 0 | `τ=-sin(pitch)`；`k={1.3 飞行, 0.085 τ<-0.2, 0.06}`；**平滑门与置零门见 §2.8** |
| R-F10 | 水中跳跃上浮 | `sub_1495EA890`（worker `sub_14961FD60`） | `0x1495EACF1` | 液体盒判定通过时 `vy += 0.04`（首 tick 0.028 分支；**实测未命中**，见 §2.9）；系统过滤器含 `MobIsJumpingFlag` |
| R-F11 | 液体流动推力 | `sub_1495E44E0`（LiquidPhysicsSystem） | `0x1495E5281/528B/5295` | 单位流向量 ×k 加到**三个**分量；k=0.014(水)/0.0035(浆)/1.0(其他) |
| R-F12 | 悬浮（Levitation） | `sub_142DE57E0` | `0x142DE585A` | `vy = 0.8·vy + (n+1)·0.01`；**与游泳无关** |
| R-F13 | 常量存在性/缺失 | — | — | `0.028`、`0.014`、`0.0035`、`0.54600006` 全库唯一；**不存在 -0.00625 / .text 内 -0.04** |

---

## 1. 每 tick 管线（**代数顺序**）

> 下面是"谁先乘谁"的代数顺序，由实测递推与常量缺失共同确定；不声称各 ECS 系统的调度先后
> （调度表不在本轮静态工作范围内）。
>
> **2026-10-27 补充**：调度侧的静态调查单独落在 `tick-order-12651.md`（调度入口
> `EntitySystemsManager::tickEntitySystems`、两条主 pass 的类别、注册顺序、写位/阻力两个系统
> 的 vtable 与调用链，以及"插入序 vs 同线程观察点"的未闭合矛盾）。本节的代数顺序不受影响。

水（非游泳态、无输入）：

```
R-F1  WaterTravel_DragPerEntity   vy *= 0.8; vx,vz *= d
R-F11 LiquidPhysicsSystem         只在有流动源时：v += k·û（û 见 §2.10；静水无源 ⇒ 不执行）
R-F3  Gravity_ApplyToStateVector  vy += (-0.005)          ← R-F2 提供
```

水（游泳态，ADF bit57 置位）：

```
R-F1  WaterTravel_DragPerEntity   vy *= 0.8; vx,vz *= d
R-F9  SwimControl lambda          vy += k(τ - vy)         ← 必须在本 tick 的阻力之后
(重力层 R-F2 被 !SWIMMING 门关掉)
```

岩浆：

```
R-F4  Lava travel drag            vx,vy,vz *= 0.5
R-F11 LiquidPhysicsSystem         k = 0.0035
R-F3  Gravity_ApplyToStateVector  vy += (-0.02)           ← R-F5 提供
```

全文的 $g$ 指**每 tick 直接加到 $v_y$ 上的量**（$v' = d\,v + g$）。水/岩浆的顺序是
"先阻力、后重力"，静态依据不是调度表，而是常量位模式：

- `-0.005f` 在 `sub_142E08780` 有立即数写入点 ⇒ 水 `v' = 0.8v + (-0.005)`。
- 若把 $g$ 挪进括号写成 `0.8(v+g)`，需要 `-0.00625`；全盘扫描 `0xBBCCCCCD`
  （`-0.00625f`）**0 命中** ⇒ 代码不是 `d(v+g)` 形式。
- 岩浆同理：`-0.02f` 有立即数写入点（R-F5）⇒ `0.5v + (-0.02)`；
  把 $g$ 挪进括号需要 `-0.04`，`.text` 内 **0 命中**。
- **空气例外**：R-F7 写 `-0.08`，R-F8 乘 `0.98`，实测首 tick `-0.0784 = 0.98×(-0.08)`
  ⇒ 空气是**先加重力、再乘阻力**（`v' = 0.98(v-0.08)`），与水/岩浆相反。

竖直速度与位移的对应关系（`Δy ≈ v_y`）是引擎的既有事实（位移使用 tick 起始速度），
不在本文重复推导。

---

## 2. 函数级证据

### 2.1 R-F1 水阻力 `sub_142DEE630`

签名（IDA）：`__int64 sub_142DEE630(entity, void*, compCtx, BYTE *adf, int *swimMul, int *depthLevel, float *state)`。

反编译要点（省略无关的 ECS 取值模板）：

```c
if ( (*adf & 8) != 0 )            v16 = 0.89999998;        // 0x142DEE6C5  ADF bit3 = SPRINTING
else if ( waterMovementComp )     v16 = *waterMovementComp; // 0x142DEE6BF  WaterMovementComponent
else                              v16 = 0.80000001;        // 0x142DEE6CF
...
if ( !onGround )                  v25 = v25 * 0.5;         // 0x142DEE775  不接地 ⇒ 等级减半
v26 = v25 / capLevel;                                      // cap 由虚调用返回（深海行者上限 3，行为吻合）
v27 = (0.54600006 - v16) * v26 + v16;                      // 0x142DEE781/791
if ( swimSpeedMultiplier > 1.0 )  v27 = v16;               // 0x142DEE79D  SSM>1 跳过混合
state[6] *= v27;                  // 0x142DEE7B7  vx
state[7] *= 0.80000001;           // 0x142DEE7C9  vy（固定，与水/疾跑无关）
state[8] *= v27;                  // 0x142DEE7D3  vz
```

组件 id（FNV-1a 32 反查）：`WaterMovementComponent 0xF465B380`、
`OnGroundFlagComponent 0xC29078A0`、`ActorDataFlagComponent 0xC67426F3`、
`SwimSpeedMultiplierComponent 0x60FC5A9A`。
修改器表项：`.rdata 0x14F02B720`（表基址 `0x14F02B700`，u32 RVA 数组，共 92 项）。

### 2.2 R-F2 玩家水重力 `sub_142E08780` / `sub_142E09020`

```c
void sub_142E08780(int *entity, __int64 *adf, __int64 ctx) {
  if ( (*(BYTE*)(adf + 7) & 2) == 0 ) {          // 0x142E08789
      ... 取 8 字节重力组件 ...
      *v9 = -1146890486;                          // 0x142E08816  = 0xBBA3D70A = -0.005f
  }
}
```

- `adf + 7` 的第 1 位 = `ActorDataFlagComponent` 第 57 位 = **SWIMMING**。
  条件为"SWIMMING **未置位**"才写 -0.005 —— 与"游泳时由 R-F9 接管竖直速度"一致。
- 查询上下文（`sub_142E08830` 里四个组件查询）：
  `ActorDataFlagComponent 0xC67426F3`、`WaterTravelFlagComponent 0x2802839D`、
  `PlayerComponent 0xF95D258F`、`LevitateTravelFlagComponent 0xFE3B5CE4`。
  代码会取其中**最小的组件存储**来迭代，所以这不是"全部必需"的硬过滤器；但
  `PlayerComponent` 出现在同一上下文里，支持"玩家专属水重力层"的归属。
- 第二个写点 `sub_142E09020`（`0x142E09125`）值相同，多一道 24 字节组件的
  `+4 & 0x02000000`（仍是 ADF bit57）与一次方块/组件查询；两者都注册在
  `0x14F02B7F8` / `0x14F02B800`（同一修改器表）。

对照：`sub_142DE2FC0` / `sub_142DE3260` 写的是 **-0.02f**，门是"±半格两处方块探针"，
不是 SWIMMING 位；这是岩浆重力，不能当玩家水重力（那会推出 $-0.08$）。
51 的玩家水实测是 $-0.025$，对应 -0.005 层。

### 2.3 R-F3 重力施加 `sub_142E0D280`

```c
void sub_142E0D280(entity, gravityComp, state, float *fallDamage) {
  *(float*)(state + 28) = *(float*)gravityComp + *(float*)(state + 28);   // 0x142E0D28A  vy += g
  if ( gravityComp[4] == 1 ) *fallDamage = 0;                            // 缓降免摔伤
}
```

由系统 worker `sub_142E0D4F0`（每实体回调 `sub_142E0D3D0`）调用；参数顺序
`(gravity 8B, StateVector 36B, 8B)`。该 worker 由修改器表项 `0x14F02B82C` 注册。

**硬件观察点交叉验证**：在 `StateVector+0x1C` 上装写观察点（`thread=both`），
被动下沉每 tick 的写者只有三处，换算成 RVA（以升段 Space 的唯一新写者
`0x1495EACF8` 反推模块基址）为：

| RIP（运行期） | RVA | 归属 |
|---|---|---|
| `0x7ff747a2e7ce` | `0x142DEE7CE` | R-F1 `state[7] *= 0.8` 的 store 后一条 |
| `0x7ff747a4d290` | `0x142E0D290` | R-F3 `vy += g` 的 store 后一条 |
| `0x7ff747e29ffb` | `0x1431E9FFB` | `mov [rbx+18h], rax`（8 字节重叠覆盖 +0x18..0x1F，属于位姿/速度同步层，非物理层） |
| `0x7ff74e22acf8`（仅按 Space） | `0x1495EACF8` | R-F10 `vy += 0.04` |

这是运行期证据，仅用于确认"静态定位的写点确实是实际生效的那一处"。

### 2.4 R-F4 岩浆阻力 `sub_142DF0B80`

系统过滤器（`sub_142DF07D0` → `sub_142DF08B0(ctx, 3784701548)`）要求
**`LavaTravelFlagComponent`（0xE195F66C）**。每个实体：

```c
v33[6] = v33[6] * 0.5;   // 0x142DF0F9A  vx
v33[7] = v33[7] * 0.5;   // 0x142DF0FA8  vy
v33[8] = v33[8] * 0.5;   // 0x142DF0FB6  vz
```

另一分支 `0x142DF0DB1/DBF/DCD` 同样三轴 ×0.5，门里带一次
`NavigationComponent`（0xBB725EBE）首字节的符号判断。结论：**岩浆阻力就是"三个分量各乘 0.5"**，
不存在独立的三分量公式（载具/玩家走同一路径）。

### 2.5 R-F5 岩浆重力 `sub_142DE3260`

```c
... 若实体处 ±(高度×0.5) 两处方块探针都为液体 ...
*(_DWORD *)result = -1130113270;   // 0x142DE352F = 0xBCA3D70A = -0.02f
```

兄弟函数 `sub_142DE2FC0` 在 `0x142DE3238` 写同一个值。两者都写在 8 字节重力组件上，
由 R-F3 施加。与 R-F4 的 0.5 组合：

$$v_\infty = 0.5v + (-0.02) \;\Rightarrow\; -0.04 \text{ 格/tick}$$

与实测 `0 → -0.02 → -0.03 → -0.035 → -0.0375` 逐位一致。

### 2.6 R-F6 液体判定盒

`sub_142DEB930`（SwimAmount 用）与 `sub_142DEB4B0`（阻力/免疫判定用）共用几何：

```c
minY += 0.4f;   // 0x142DEB9BA
maxY -= 0.4f;   // 0x142DEB9E4
minX += 0.1f;  minZ += 0.1f;  maxX -= 0.1f; maxZ -= 0.1f;   // 0x142DEB9C3/F4 …
```

外加 `SubBBsComponent 0x0B0822D1`；`ImmuneToLavaDragComponent 0xDD2DA6E4` 在
`sub_142DEB4B0` 里整体短路。对 1.8 格高玩家，检测带是 `[脚+0.4, 脚+1.4]` ⇒
液位 ≤3（<0.4 格）完全无液体物理。

### 2.7 R-F7/R-F8 空气重力与阻力

`sub_142DE3560`（`Gravity_SelectBySlowFalling` 同构）：

```c
v8 = vy < 0 && (实体缺 xmmword_151DBF538 对应的定义组件);
... 慢落且 vy<0 ⇒ v15 = -1138501878 (0xBC23D70A = -0.01f)  // 0x142DE3662
    否则          v15 = -1113336054 (0xBDA3D70A = -0.08f)  // 0x142DE3682
```

门里另有 `actorFlags & 0x2000000000000`（bit49）与 flagsHi 的 0x60/0x80 组合，
对应"有重力/脚手架/穿越"等状态。

`sub_142DF1790`（竖直阻力施加）：

```c
if ( (*(BYTE*)(a2+16) & 1) != 0 )      v4 = 0.91000003;   // 0x142DF17A6
else if ( (*(BYTE*)(a2+6) & 2) != 0 )  v4 = 0.98000002;   // 0x142DF179C
else return;
vy *= 1.0 - max(0, (1-v4)*percentModifier);
```

### 2.8 R-F9 泳姿俯仰控制 lambda `sub_1490C0A00`

worker `sub_1490CC320` 的过滤器（启动序列里的组件 id 立即数）：

| 组件 | id |
|---|---|
| ActorDataFlagComponent | 0xC67426F3 |
| MovementAbilitiesComponent | 0x6DE78DC5 |
| ActorRotationComponent | 0x75DF36B7 |
| MoveInputComponent | 0x018B1887 |
| PlayerInputRequestComponent | 0x6B04A058 |
| StateVectorComponent | 0x1B5D5238 |
| MobTravelComponent | 0x39CF8D64 |
| WasInWaterFlagComponent | 0x78E89F39 |
| MobIsJumpingFlagComponent | 0x8FD20E98 |

lambda 体（`a2`=ADF，`a3`=MovementAbilities，`a4`=ActorRotation，`a5`=MoveInput，`a6`=PIR）：

```c
if ( (*(BYTE*)(a2 + 7) & 2) == 0 ) return;                 // 必须有 SWIMMING
tau = sinTable[ (pitch) * -0.017453292 * 10430.378 ];      // τ = -sin(pitch)
if ( (*(DWORD*)(a3 + 4) & 1) != 0 ) k = 1.3;               // 飞行
else k = flt_14EA51BF0[ tau < -0.2 ];                      // {0.06, 0.085}
if ( tau <= 0.0 || *(BYTE*)(a6 + 33) != 0
     || (flying && (*(BYTE*)(a6 + 32) != 1 || (*a5 & 4) != 0)) )
    vy += k * (tau - vy);                                   // 0x1490C0B0F
else
    vy = 0;                                                 // 0x1490C0B31
```

要点：

- **置零条件是"抬头且头部无遮挡且非飞行"**（`tau>0 && PIR+0x21==0`），不是"低头"。
  旧文档把门条件写反了。
- `PIR+0x21 != 0` 表示头部所在方块存在（HeadroomSystem 写 `+0x20/+0x21`），
  此时允许抬头/平视也按 $\tau$ 平滑。
- lambda 内**没有 0.8 阻力**。实测逐 tick 因子 0.731 与稳态 $0.317\tau$ 只与
  "先 R-F1 阻力、后本 lambda"自洽：$v'=0.8v+k(\tau-0.8v)$ ⇒
  $v_\infty = k\tau/(0.2+0.8k)$。若反过来（先 lambda 后阻力）强迫项会变成 $0.8k\tau$，
  稳态 $0.2537\tau$ —— 与五档实测不符。

### 2.9 R-F10 水中跳跃上浮 `sub_1495EA890`

系统过滤器（`sub_14961F770`）包含：`AABBShape 0xBAC1B3CF`、`SwimAmount 0xCF19787C`、
`MobEffects 0xE6A1B550`、`SubBBs 0x0B0822D1`、`ActorDataFlag`、`MobJump 0xC52C8F78`、
`StateVector`、`ActorMovementTickNeeded 0x94154CCE`、`MobFlag 0x14EA24A2`、
**`MobIsJumpingFlag 0x8FD20E98`**、`Passenger 0x98E40C0E`。

```c
if ( *(BYTE*)(a16 + 12) == 1 ) { *(BYTE*)(a16 + 12) = 0; xmm8 = 0.028; }  // 0x1495EA96B
else                             xmm8 = 0.04;                             // 0x1495EA976
...
if ( LiquidBoxTest(a19, a11, v69, v49) )                                  // 0x1495EACD9
    *(float *)(state + 0x1C) += xmm8;                                     // 0x1495EACF1
```

- `MobIsJumpingFlag` 在过滤器里 ⇒ **只有跳跃中的实体进入该系统**，所以静置不会上浮。
- 首 tick 的 `0.028` 由 `a16+12` 字节闩锁决定（字段语义未直接命名，推断为"入水首 tick"；
  用完立刻清零）。
- **实测（2026-10-25，修复 sprint 注入后）**：入水前按住 Space、落入开顶池，`in_water`
  首次为真后第 3 个 tick 才切水物理，该 tick 的增量是 **+0.04**（对 `0.8v-0.005` 的残余
  `+0.04000`），**没有 0.028**；入水后再按 Space，第一个 +0.04 出现在 2–4 tick 后
  （输入队列约 2 tick，水物理切换再加 1–2 tick）。所以 0.028 的**可达性未被证明**，
  只能写作"静态存在的分支"。
- 同函数的另外两个常量**不属于流体**：
  - `0.15f`（`0x1495EA9E4`）：门 = flagsHi bit99 `IN_ASCENDABLE_BLOCK`，或
    bit69 `IN_SCAFFOLDING` 且非 bit71 `DESCEND_THROUGH_BLOCK` ⇒ **脚手架上升**。
  - `0.2f`（`0x1495EAE35`）：门 = flag bit18 `WALLCLIMBING`，或 bit19 `CANCLIMB`
    + 方块 bit33（可攀爬）⇒ **梯子/藤蔓爬升**。
- 辅助分支 `sub_1495EA680` 会**赋值** `vy = 0.42 + 0.1(n+1)` 或 `0.2 + 0.1(n+1)`
  （`0x1495EA755/786`），门里带一个 88 字节组件与 `+80` 的虚表；它也是"游泳稳态 ≈0.2"
  的候选来源，但门条件尚未认名（§6）。

### 2.10 R-F11 流动推力 `sub_1495E44E0`

函数内的断言字符串是 `"1 LiquidPhysicsSystem"` / `"2 LiquidPhysicsSystem"`，可据此认系统。
每 tick：

```c
for each flowSource:  sub_14345DE50(&vec, blockSource, pos, material);  // 单块流向向量
sum += vec;                                   // 三个分量
if ( |sum| > 1e-4 ) sum /= |sum|;             // 跨源归一化（0x1495E51E3..5243）
k = (material == 5) ? 0.014                    // 0x14E7CA4D0 水
  : flt_14E7CA4C8[material == 6];               // 表 {1.0, 0.0035, 0.014, 0, 0.9, 0.2}
v += k * sum;                                   // 0x1495E5281(+0x18) / 528B(+0x1C) / 5295(+0x20)
```

`sub_14345DE50`（`@liquid_flow_vector`）内部才是 -6 偏置，而且**是带门的**（2026-10-28 更正）：

```c
self = liquid_depth(pos);                     // sub_14345D010，非该液体返回 -1
for 4 dirs:                                   // 水平分量
    n = liquid_depth(pos + dir);  if (n >= 8) n = 0;
    s = liquid_depth(pos);        if (s >= 8) s = 0;
    f += (n - s) * dir;
if (self >= 8) {                              // 自身是下落块（0x14345E0DC 起）
    solid = false;
    for 4 dirs:
        if (material_solid(pos + dir)          ||  // 0x14345E172: byte[block+0x68 -> +0x28 +5]
            material_solid(pos + dir + up))    {   // 0x14345E1A8（邻块上方一格）
            solid = true; break;                   // 命中即退出（至多一次）
        }
    if (|f| >= 1e-4) { f /= |f|; f.y += -6.0; }    // 0x14345E213，常量 0x14E76B07C=0xC0C00000
    else             { f = (0,-6,0); }
}
f /= |f|;                                     // 最终单位化（0x14345E242..279）
```

- **三个分量都写**（包括 `vy`），但竖直流推只在"下落 + 邻块/邻块上方实心"时出现；
  水平流（自身不下落、或旁边没有实心面）的 `f.y == 0`。
- 液位差来自 `sub_14345DE50` 的 4 邻采样，`level ∈ [0,7]`；`level >= 8` 归一为 0。
- 常量表 `0x14E7CA4C8` 的实际内容（小端 float）：
  `{1.0, 0.0035, 0.014, 0, 0.9, 0.2}`。
- 静水实测：`v' = 0.8v - 0.005` 逐位成立 ⇒ 静水时该系统的推力分支没有执行；
  水平流实测：`v_\infty = 0.014/(1-0.8) = 0.07001` 逐位成立 ⇒ 推力发生在 R-F1 之后。
- **竖直流推实测（2026-10-28）**：水阻力仍是 0.8，强制项
  $b=\mathrm{median}(v_{n+1}-0.8v_n)$：静水 $-0.005$；1×1 贴墙瀑布、3×3 贴墙瀑布、
  露天+上悬 $-0.019$；露天无墙、3×3 竖井中心、2×2 水体邻接 $-0.005$。
  $b=-0.019=-0.005-0.014$，稳态 $-0.095$ vs 静水 $-0.025$。账本
  `findings/1.26.51.01/cases/liquid-falling-push`（probe `r01-002` / `r03-009`），
  行为文档 `docs/fluid-movement.md` §3.3。水材质不算实心面（2×2 对照），
  与 Java 的同流体排除一致。
- 旧笔记把 -6 写成"有流动源就无条件加"是错的：它读的是**自身** `liquid_depth>=8`
  加上邻块/邻块上方 material 字节，和 Java 26.2 的 `FALLING && isSolidFace(...)` 同构。


### 2.11 R-F12 悬浮 `sub_142DE57E0`

```c
vy = vy * 0.80000001;                                  // 0x142DE5837
vy = (*(int*)(comp + 32) + 1) * 0.01f + vy;            // 0x142DE585A  +32 是每实体计数器
/* 之后 get-or-add LevitateTravelFlagComponent 0xFE3B5CE4 */
```

与 Java `LevitationEffect` 的
`vy += 0.2*(0.05*(amp+1) - vy)` 完全同构（`= 0.8vy + 0.01(amp+1)`）。
调用者 `sub_142E0EE60` 的过滤器含 `MobEffectsComponent 0xE6A1B550`。
**它不是游泳层，不能用来解释游泳稳态 +0.2。**

### 2.12 R-F13 常量存在性

| 值 | 位模式 | 位置 | 说明 |
|---|---|---|---|
| 0.028f | `0x3CE56042` | `.rdata 0x14EA669A0`（全库 1 处），xref `0x1495EA96B` | 入液首 tick 上浮 |
| 0.014f | `0x3C656042` | `.rdata 0x14E7CA4D0`（1 处），xref `0x1495E5268` | 水推力 |
| 0.0035f | `0x3B656042` | `.rdata 0x14E7CA4CC`（1 处），表内读取 | 岩浆推力 |
| 0.54600006f | `0x3F0BC6A9` | 全库 **1 处**（`.text` 立即数 `0x142DEE781`） | 深海行者目标值 |
| -0.005f | `0xBBA3D70A` | `.text` 39 处（37 在液体类型记录构造器一带，2 处是重力写点 `0x142E08816`/`0x142E09125`） | 水重力 |
| -0.00625f | `0xBBCCCCCD` | **0 处** | 证明"阻力在前" |
| -0.04f | `0xBD23D70A` | `.text` **0 处**；`.rdata 0x14E7FC134` 1 处（16 个引用，均不在本轮流体函数内） | 证明岩浆是 -0.02 施加 |
| -0.08f | `0xBDA3D70A` | `.rdata 0x14E7E14FC` / `0x14E9962F4` | 空气重力 |
| 0.98f | `0x3F7AE148` | `.rdata 0x14E7E1500` | 空气阻力 |

> 更正：旧文档说"-0.005 在 .rdata 中只出现一次，可作版本锚点"不成立（22 处）。
> 可作锚点的是 `0.028`、`0.014`、`0.0035`、`0.54600006`。

### 2.13 R-F14 液体退出弹跳（Liquid Blip，2026-10-28）

用户 1.12.2/1.8.9 文章里的 water blip：离开液体时若水平方向发生碰撞、且把碰撞箱沿
预测速度上抬 0.6 后既无固体也无液体，则 $v_y := 0.3$。

- **Java 26.2 仍然保留**（`LivingEntity#jumpOutOfFluid`）：
  ```java
  if (this.horizontalCollision
      && this.isFree(movement.x, movement.y + 0.6F - this.getY() + oldY, movement.z))
      this.setDeltaMovement(movement.x, 0.3F, movement.z);
  ```
  `travelInWater` / `travelInLava` 末尾各调用一次；`isFree = noCollision && !containsAnyLiquid`。
  26.2 的 0.3 是覆盖：水跳 `+0.04` 在更早的 `aiStep` 里加，最终被顶掉。
- **基岩 1.26.51.01 实测存在**（live-only，静态写点未锁定）：2 深水池、贴墙、W+Space，
  `record` 的 $v_y$ 序列 `… 0.0536 → 0.34 → 0.3 → 0.2156 …`，位置每 tick +0.3 并离开水面。
  对照：无墙最大 0.098；深水贴墙无正 $v_y$；朝东贴墙同样触发（0.3）⇒ 1.8.9 的东南浮点
  bug 在基岩不复现。
- **基岩版本要跳跃输入**：不按跳跃、脚在水面下 0.4（水物理确实生效、抬 0.6 的探针盒
  已离开水面）、水平碰撞成立的对照组仍然不触发；26.2 的 `jumpOutOfFluid` 不检查跳跃输入。
  这是两者最明确的行为差异，可能是基岩把弹跳挂进了水跳系统/跳跃边沿。
- 0.34 = 0.3 + 0.04，与水跳系统 `sub_1495EA890` 的 0.04 相加顺序一致（26.2 是覆盖，只到 0.3）。
- **静态搜索**：`StateVector+0x1C` 写观察点在弹跳 tick 的命中是
  `0x148B61995`（`sub_148B618A0`，竖直加速度加法）、`0x1431EC3FB`
  （`sub_1431EC2A0`，Vec3 拷贝）、`0x142E0D290`（重力）、`0x142DF17F5`（空气阻力），
  没有直接的 `0.3` store；`sub_1495EA890` 内也只有 0.028/0.04。0.3 很可能来自移动输入
  的竖直分量（由 `sub_148B618A0` 加进速度），待用 `MoveInput` 侧地址复测。
  账本 `findings/1.26.51.01/cases/liquid-blip`。

### 2.14 R-F15 维度 / 抗火 / 岩浆深度（2026-10-28）

**Java 26.2**：

- 维度只通过 `EnvironmentAttributes.FAST_LAVA` 接进移动：`Entity.java:1663`
  的岩浆流推力 `0.007`（下界）vs `0.0023333333333333335`（其他）；
  `LavaFluid` 的 `getTickDelay` 10 vs 30、`getDropOff` 1 vs 2、
  `getSlopeFindDistance` 4 vs 2。下界 `WATER_EVAPORATES=true`（水放不下来）。
- 抗火只出现在伤害减免（`LivingEntity.java:1178`），移动路径不读这个效果。
- 岩浆深度：`travelInLava` 用 `isInShallowFluid(LAVA)`（液面高度 $\le$
  `getFluidJumpThreshold()` = 0.4）：
  - 浅：`multiply(0.5,0.8,0.5)` → `-g/16` → `-g/4` ⇒ $0.8v-0.025$，稳态 $-0.125$；
  - 深：`scale(0.5)` → `-g/4` ⇒ $0.5v-0.02$，稳态 $-0.04$；
  - 水平两支都是 0.5；疾跑跳过 `-g/16`。

**基岩 1.26.51.01 实测 + 静态复核**（probe `findings/1.26.51.01/cases/lava-dim-fire-depth/r01-001`）：

- **静态（2026-10-28）**：
  - 岩浆阻力 `sub_142DF0B80`：两条路径都在 `0x142DF0F9A/FA8/FB6`、`0x142DF0DB1/DBF/DCD`
    对三轴各乘 0.5；门是 `LavaTravelFlag`/`Navigation` 符号位，没有高度/维度/效果读取。
  - 岩浆重力 `sub_142DE3260`：`0x142DE352F` 写 `-0.02f`（`0xBCA3D70A`），只有 ±半格探针。
  - 流推 `sub_1495E44E0`：`0x1495E5256` 按 material 选 k（5→0.014，6→表 `[1]=0.0035`），
    没有维度参数。三者都不读 `MobEffects`/`FIRE_RESISTANCE`。
- 深度：脚在液面下 0.55 与 3 格都得到 `b50=-0.02`、`steady=-0.04`（`a=0.5`）⇒ 没有浅浆分支；
  与静态一致。结构原因：液体判定盒底部内缩 0.4，进入岩浆物理时液面高度必然 $>0.4$。
- 抗火：有无抗火的 $v_y$ 序列落在同一条递推上（health 20 vs 17），不影响移动。
- 维度：下沉都是 $-0.04$；流动岩浆里的漂移稳态都是 $0.007$（$=0.0035/(1-0.5)$）⇒
  没有 Java 的“下界推力 ×3”（静态：k 只按 material）。岩浆扩散下界更快
  （80 tick 前沿 $+7$ vs 主世界 $+3$），静态入口是液体扩散函数 `sub_14345B370`：
  它读 `BlockSource → [vtable+0x138] → +0x1A6` 的维度字节（下界为 1），
  `drop-off = 2 - flag`（`0x14345B40F/41B`）、`tick delay = flag ? 10 : 30`
  （`0x14345B46A/47C`）。前沿 $7$ vs $3$ 正好是液位 7 减 drop-off 1 / 2。
  这个维度标志**不进**实体推力（流推 k 只按 material），所以“流得更远”和“推得更快”
  在基岩是分开的两件事。
- 坑：第一次从主世界传进刚建好的下界池时，前十几 tick 可能停在原地（区块/填充未就绪），
  不要当成物理；等区块加载后重传一次。

---

## 3. 组件与偏移

| 名称 | id | 步长 | 用途 |
|---|---|---|---|
| StateVectorComponent | 0x1B5D5238 | 36 | pos@0/4/8，vel@0x18/1C/20 |
| ActorDataFlagComponent | 0xC67426F3 | 24 | bit3 SPRINTING、bit18 WALLCLIMBING、bit19 CANCLIMB、bit57 SWIMMING（`byte[7]&2`） |
| PlayerInputRequestComponent | 0x6B04A058 | — | `+0x20` / `+0x21` 头部空间字节（HeadroomSystem 写） |
| ActorRotationComponent | 0x75DF36B7 | — | pitch/yaw 及其上一 tick 值 |
| MoveInputComponent | 0x018B1887 | — | byte0 bit2（`&4`） |
| MovementAbilitiesComponent | 0x6DE78DC5 | — | `+4` bit0 = 飞行中 |
| WaterTravelFlagComponent | 0x2802839D | — | 水旅行门 |
| LavaTravelFlagComponent | 0xE195F66C | — | 岩浆阻力门 |
| WaterMovementComponent | 0xF465B380 | — | 覆盖基础水阻力 |
| SwimSpeedMultiplierComponent | 0x60FC5A9A | — | >1 时跳过深海行者混合 |
| SwimAmountComponent | 0xCF19787C | — | 入液上浮状态（含 `+12` 首 tick 字节） |
| MobIsJumpingFlagComponent | 0x8FD20E98 | — | 水中跳跃系统门 |
| LevitateTravelFlagComponent | 0xFE3B5CE4 | — | 悬浮层 |
| AABBShapeComponent | 0xBAC1B3CF | 24 | 液体盒 |
| SubBBsComponent | 0x0B0822D1 | — | 液体盒附加子盒 |
| OnGroundFlagComponent | 0xC29078A0 | — | 深海行者等级减半门 |
| PlayerComponent | 0xF95D258F | — | 玩家专属重力层的过滤器 |
| ImmuneToLavaDragComponent | 0xDD2DA6E4 | — | 短路液体拖拽 |
| NavigationComponent | 0xBB725EBE | 96 | 岩浆阻力分支里的首字节符号判断 |

重力组件本身是 8 字节：`{float g; uint8 slowFalling}`（R-F3 的 `+4==1` 清摔伤）。
步长标 `—` 的是代码里未直接体现的，未在 51 单独验证。

---

## 4. 验证方法（可复现）

1. **文件侧常量扫描**：对版本 EXE 做 float32 模式扫描（含 `.text` 立即数），
   再用 IDA `xrefs_to` 把 `.rdata` 常量反查回函数；立即数写入点直接读 4 字节。
2. **反编译**：IDA 9.4 Hex-Rays，只读 `.i64`；大函数用 `disasm` 逐段核对，
   避免 Hex-Rays 截断。
3. **跨版本定位（可选）**：拿已知函数的字节片段在新版 `.text` 里做滑窗匹配，
   候选以"指令片段命中 + 反编译内容一致"双条件确认。
4. **运行期交叉验证**：在 `StateVector+0x1C` 上装硬件写观察点，
   记录 RIP 后按"升段唯一新写者 = `0x1495EACF8`"反推模块基址，把 RIP 换成 RVA，
   与静态写点一一对应。

---

## 5. Java 26.2 对照（[J]，不是基岩事实）

来源：fabric-loom 26.2 merged jar 反编译（`LivingEntity`、`Entity`、`EntityFluidInteraction`、
`BubbleColumnBlock`、`ScaffoldingBlock`）。

| 项 | Java 26.2 | 基岩 51 | 差异 |
|---|---|---|---|
| 水竖直阻力 | 0.8 | 0.8（R-F1） | 相同 |
| 水中下沉 | `-gravity/16 = -0.005` ⇒ $v_\infty=-0.025$（疾跑时不扣） | -0.005（R-F2） | **相同** |
| 岩浆竖直阻力 | 深浆三轴 ×0.5；浅浆 (0.5, 0.8, 0.5) | ×0.5（R-F4），无浅浆分支 | 浅浆分支在基岩不可达 |
| 岩浆下沉（深浆） | `scale(0.5)` 再 `-gravity/4` ⇒ $0.5v-0.02$，$v_\infty=-0.04$ | $0.5v-0.02$（R-F4/R-F5）⇒ $-0.04$ | **相同** |
| 岩浆下沉（浅浆） | `multiply(0.5,0.8,0.5)` 再 `-g/16` 再 `-g/4` ⇒ $0.8v-0.025$，$v_\infty=-0.125$ | 没有浅浆分支（无条件三轴 0.5）⇒ $-0.04$ | **Java 浅浆更慢** |
| 水推力 | 0.014（玩家取**平均**流向，0.0045 下限） | 0.014（**单位向量**，无下限） | 弱流幅度不同 |
| 岩浆推力 | 0.002333（`FAST_LAVA` 时 0.007） | 0.0035 | 约 1.5 倍 |
| 水中跳跃 | `aiStep` 先 `+=0.04` 再 travel ⇒ 水 $v_\infty=0.135$ | 阻力后 `+=0.04` ⇒ $v_\infty=0.175$（R-F10） | 施加位置不同 |
| 判定盒 | `deflate(0.001)` + 流体高度缩放 | 内缩 0.1/0.4，硬截断（R-F6） | 浅液行为不同 |
| 游泳门 | `sprinting && isUnderWater && 水` | `SWIMMING(bit57)` + sprint 状态 + 头部字节（R-F9） | 近似 |
| 脚手架/梯子 | `ScaffoldingBlock` / `onClimbable` | `sub_1495EA890` 的 0.15/0.2 分支（R-F10） | 数值一致 |
| 悬浮 | `0.8vy + 0.01(amp+1)` | 同构（R-F12） | 相同 |

---

## 6. 未闭合项（静态侧）

1. **流推向量的 -6 偏置在实测中的竖直贡献（2026-10-28 已闭合）**：-6 在
   `sub_14345DE50` 内部，门控是"自身 `liquid_depth>=8` + 邻块或邻块上方 material 实心"，
   不是无条件。满足时水每 tick $-0.014$，稳态 $-0.095$；开放空气/竖井中心为 $-0.025$。
   六场景对照与账本见 §2.10 与 `findings/1.26.51.01/cases/liquid-falling-push`。
   仍未测的只剩"水平强流 + 下落"同时存在时的合成方向。
2. **`sub_1495EA680` 两个分支（0.42 / 0.2）的门**：只知道需要 88 字节组件 +
   一个虚表调用；不确定是否与跳跃/冲刺/骑乘有关。
3. **气泡柱**：51 未复核；45 的 `work/mc-bedrock-bubble-column` 只有部分结论。
4. **浅浆分支**（Java 的 `(0.5, 0.8, 0.5)`）：51 的 R-F4 是无条件三轴 0.5，
   没有浅/深分支的证据；若实测出现竖直 0.8，说明还有另一条路径。
5. **入液首 tick 的 0.028 闩锁写者与可达性**：`a16+12` 由谁置 1 未定位；入水/深水实测
   只看到 +0.04，0.028 分支没有被触发（§2.9）。
6. ~~游泳态"前进+疾跑缓慢上浮 +0.16~0.21"~~ **已撤回（2026-10-25）**：那是
   `set_input(sprint=True)` 的隐藏 jump，不是游泳层。修复输入注入后：平视 `vy=0`、
   抬头 pitch $-30°$ 稳态 $+0.121=0.242\tau$、Space 与不游泳一样 $+0.175$。
7. ~~岩浆"前进+疾跑顶浮 +0.04"~~ **已撤回（2026-10-25）**：同一个隐藏 jump；修复后
   26 tick 尾段恒 $-0.04$（与 HID 真键一致）。
8. **疾跑起步的水/陆差异（2026-10-26 定位写者与顺序）**：
   - **写者**：ADF bit3（SPRINTING）由 `Actor::setSprinting` = `sub_14B17C820` 写入：
     `if (a5 == ((*adf & 8) != 0)) return; *adf = (*adf & ~8) | 8*a5; *dirty |= 1;`
     （`0x14B17C82x` 附近）。两个调用者：
     `sub_14B1A9F50`（读输入边沿位 `0x200` 置 1 / `0x400` 清 0）、
     `sub_14B1AA080`（按实体从 368 字节组件取同一组边沿位再调用）。
   - **读侧**：水阻力 `sub_142DEE630` 在入口直接看 `(*adf & 8)`
     （`0x142DEE6C5`），即读取瞬间的 ADF 值。
   - **同 tick 顺序（硬件观察点，thread=both，同一 tid）**：在一 tick 内先命中阻力写
     `vx` 的 `0x142DEE7BC`（`a7[6] *= d` 的 store 之后），后命中 `setSprinting`
     的 `0x14B17C870`。模块基址由 `0x7ff77c2ee7bc - 0x142DEE7BC` 得
     `0x7ff639500000`，同一基址把另一次命中 `0x7ff77c6e9ffb` 映射回
     `0x1431E9FFB`（与 §2.3 的旧运行期记录一致），映射自洽。⇒ 这是旧观察点顺序，
     不等于 ECS 调度顺序；静态注册序显示写位在阻力之前（`tick-order-12651.md` §8–§10）。
   - **时间线（常规手动相位，t0 = 按下 W、`vz=0`）**：前两个移动 tick 与走路相同，
     第三个切 0.9：`0.01959 / 0.03528 / 0.05133 / 0.06580`（t3 生效，手动实测）。
     早相位（疾跑键在采样前已按住/注入落在采样前）：`0.01959 / 0.03723 / 0.05310 /
     0.06738`（t2 生效）。不变式：onset = 记录里的 `sprint` 位（`Player::isSprinting()`）
     置位 tick + 2；写位在阻力之前同 tick 完成，延迟来自写入前的状态链，不是阻力读滞后。
     `record.sprint` 不是阻力读的 `ADF & 8`。
   - **早相位不是 harness 假象**：真实玩家在采样前按住疾跑键再前进，也会出现
     `0.01959/0.03723/...`；常规手动相位多一拍，只是按键落在采样点之后。
   - 早期开放水面（无围边，`/fill` 的水会向平台外扩散）读数仍逐位吻合（内点无流推），
     但场地不成立；已改为带围边的封闭池并逐格校验，结论不变。
   - 相位扫描（tick 边界后 0/10/20/30/40 ms 发 `set_input`）t3/t4 两种形态都复现 ⇒
     同一代码路径，差别只在输入落点。HID 的 W/Ctrl（hid/hardware/raw）干净状态下 9 tick
     全是 0.8，进不了疾跑；按键路径以真实键盘实测为准（t3）。
   - 平地 `set_input` 30 次：4 次正常（第一移动 tick `0.0980` 时 sprint=1，第二 `0.1809`），
     26 次延迟（第一 `0.0980` 时 sprint=0，第二 `0.1515` 走，第三 `0.2101` 起疾跑）。
     水面后两行与这个延迟同源（注入标志晚到一拍），不是额外的流体层；地面是状态 +1、
     水面是状态 +2。
     **地面 ×1.3 的来源是输入层的疾跑标志**（① 采样，③ 直接用），不是 `isSprinting`（⑥）
     也不是 ADF 位（⑤）：手动同按 1 tick W+疾跑，第一脚就是 `0.1274`；上面 `0.0980` 的
     两次注入是因为标志落在采样点之后，不是地面本身有延迟。
   - **站着不动时按疾跑键不会预置 sprint**：`forward=0, sprint=True` 保持 8 tick，
     实测 `client_sprint=0`、ADF bit3=0、ADF bit57=0；sprint 状态从**第一次前进移动**才
     开始。
   - **待查的输入锁存**：当前世界里静止时 `state.input.sprint` 就是 `true`，
     HID 只按 W 或 `set_input(forward=1, sprint=False)` 都会在第一个移动 tick 进疾跑
     （ADF bit3=1、稳态 0.28）。`lab.settle(..., input_sprint=False)` 已加为前置断言；
     锁存来自玩家存档、输入请求组件还是 HID 虚拟来源尚未定位。
   - **地面加速度施加点已静态定位（2026-10-27）**：在客户端 StateVector `vx` 上装写观察点，
     走/跑时出现的第三个写者是 `0x148B61989`，所在函数 `sub_148B618A0`
     （`0x148B618A0-0x148B61AE6`）。它只做：读 12 字节方向向量 + `+12` 的模长，按 yaw 旋转后
     加到 `state[6..8]`；函数体内**没有 ADF/疾跑判断**。包装 `sub_148B61AF0` / `sub_148B61E10`
     只透传向量，系统 worker 为 `0x148B7F7F0` / `0x148B82540` / `0x148B85430`。
     同一次观察里摩擦写者是 `0x142DF9FA4`（`sub_142DF9DA0`），姿态同步是 `0x1431E9FFB`。
     ⇒ 地面的 ×1.3 不可能发生在加速度施加点，只能在上游构建这个"方向+模长"向量的输入代码里。
   - **地面 ×1.3 的最终来源（2026-10-27，来源已判定）**：运行期用 VA 钩子挂 `sub_148B618A0`
     （applier），r9 = 向量指针；疾跑时向量为 `(0, 0, 0.98, 0.13)`、走路 `(0, 0, 0.98, 0.1)`，
     `0.13×0.98 = 0.1274` 与实测逐位一致。模长写者：`0x14666D1E8`
     （`sub_14666CEE0`：`*(vec+12) = v25 * (0.546/friction)^3`，v25 = `a4+0`，疾跑 0.13 /
     走路 0.1）、`0x1431D2551`（初始化）、`0x1431C1625`（方向 ×0.98）。
     钩 `sub_14666CEE0` 读 `a4+0`：走路 0.1、疾跑 0.13（同一对地址
     `0x240E80B1AE0`/`0x240E80BD940`）。该地址的写者 `0x14289A321` 属
     `sub_14289A180`：它把**属性聚合表**里 `+124` 的当前值拷进移动结构
     （`*(a2+off) = *(float*)(v22+124); *(a2+off+4)=1`）。⇒ **最终 ×1.3 来自移动速度属性的
     modifier**（0.1→0.13），不是原始输入标志，也不是下面那张 0.02 表。
   - **同一路径上的直接 ADF 读取（另一个字段）**：`sub_142E11910`
     （`0x142E11910-0x142E11933`，23 B）：
     ```c
     *(a3+12) = 0x3CA3D70A;                 // 0.02f 默认
     result = (*a2 & 8) == 0;               // ← ADF bit3（SPRINTING）
     *(a3+12) = dword_14E85AF48[result];    // 表值 (0.026, 0.02) = 0.02×{1.3, 1}
     ```
     表在 `.rdata 0x14E85AF48`，运行期读出 `(0.026, 0.02)`。它确实直接读 ADF bit3，但写的是
     0.02/0.026 的**基础加速度字段**，与最终 0.1/0.13 差 5 倍，**不是最终乘数的来源**。
   - **modifier 机制与最终来源（2026-10-27 闭合）**：`sub_142859B40`（add）/
     `sub_14285A7D0`（remove）/ `sub_1431A1BE0`（wrapper）/ `sub_14285B440`（重算，写
     `entry+124`）。`Actor::setSprinting`（`sub_14B17C820`）在写 ADF bit3 的同一处，向属性
     组件（key `1255454079`）的待处理队列压一条 32 字节请求
     `{shared_ptr<AttributeModifier>, attribute=&unk_151DC5488, flag(1=remove,0=add)}`；
     `a5==0` 时压移除。共享 modifier `SharedModifiers::SPRINTING_BOOST()` 的静态初始化在
     `1431BD020` 内（`qword_151DC52F0`/`qword_151DC52F8`）：
     `mName="Sprinting speed boost"`、`mAmount=0.3f`（`0x3E99999A`）、
     `mOperation=2 (OperationMultiplyTotal)`、`mOperand=2 (OperandCurrent)`、
     UUID `D208FC00-42AA-4AAD-9276-D5446530DE43`、`mSerialize=false`
     （布局按 LL SDK `AttributeModifier.h`：vptr+0/amount+8/op+12/operand+16/name+24/id+56）。
     重算 `sub_142859E10` 对 MULTIPLY_TOTAL 执行 `(amount+1.0)*value` ⇒ `1.3*0.1=0.13`。
     **代码里没有 1.3；只有 0.3，+1.0 是重算时加的**。
     **撤回**：`self::DEFAULT_SPRINT_SPEED_MULTIPLIER`（`.rdata 0x14EF2D02D`）是 **AI goal**
     的 JSON 字段（`sprint_speed_multiplier`），解析点 `sub_14CCAE920`（同函数解析
     `DEFAULT_MAX_SPRINT_RANGE`）；原版数据 ocelotattack 1.33 / piglin 1.1 / hoglin 1.0，
     与玩家移动速度无关。同名干扰：灵魂疾行（附魔 id 36）也建一个 `"Sprinting speed boost"`
     modifier（`sub_142080250`，amount `(1+0.35*等级)*0.03`，UUID `xmmword_151E18970`，
     目标同一属性 `&unk_151DC5488`），是另一个 modifier。
     另：早期钩 `sub_14289A180` 读 `a1` 失败与 `sub_1419FDE60` 调用者属于寻路/转向，均与本
     结论无关。

### 已撤回的旧结论

| 旧结论 | 现状 | 依据 |
|---|---|---|
| `vy=0.15/0.2` 是 SwimAmount 的游泳/流体层 | **错**：分别是脚手架上升、梯子/藤蔓爬升 | R-F10 |
| `sub_142DE57E0` 是"前进游泳缓慢上浮" | **错**：是悬浮（Levitation） | R-F12 |
| 入水 $-0.41$ 处有一次性 $+0.083$ 阻尼 | **撤回**：干净开顶池、三种落速（$v_y=-0.78/-1.27/-1.58$）都逐位纯递推；旧数据来自封顶池的碰撞伪影 | §7 新实测 |
| 水中 $-0.02$ ⇒ $v_\infty=-0.08$ | **错**：玩家水重力是 -0.005 层 | R-F2 |
| Java 岩浆终速 $-0.02$、基岩快 2 倍 | **错**：Java 深浆同样是 `0.5v-0.02` ⇒ $-0.04$；只有浅浆（$0.8v-0.025$）不同 | 26.2 字节码 `travelInLava` |
| 游泳态 $+0.20$ / 平视也有 $+0.16\sim0.21$ 独立向上层 | **撤回**：旧 `set_input(sprint=True)` 把 sprint 当按钮，触发 0.42 跳跃；修复后平视 $v_y=0$、抬头按 $\lambda$、Space $+0.175$ | §7 新实测 |
| 岩浆前进+疾跑顶浮 $+0.04$ | **撤回**：同一个隐藏 jump；修复后恒 $-0.04$ | §7 新实测 |
| $0.028$ 首 tick "已验证" | **更正**：静态分支存在，实测未命中；首拍 $+0.04$ | §2.9 / §7 |

---

## 7. 本轮新增实测（2026-10-25 第二轮回核）

场地：水 `(300, 300)` 石底/石壁 `y=-64..-41`、水 `y=-49..-41`（**开顶**，水面顶 -40）；
岩浆 `(330, 300)` 同规格；干地起步平台 `(997..1005, -61, 1000)`。生存模式、无药水；
每步先 `lab.settle(...)` 验证状态。

1. **入水链无额外阻尼**。三种落速的首个水物理 tick 与 `0.8v - 0.005` 逐位一致：

| 入水前 $v_y$ | 首个水 tick | 预期 |
|---|---|---|
| -0.781132 | -0.703491 | -0.703492 |
| -1.274929 | -1.024944 | -1.024943 |
| -1.623753 | -1.304001 | -1.304002 |

   旧记录里 `-0.412 → -0.2516` 的 `+0.083` 不再出现；那次池子是"石盖封顶"
   （`fill … -44 stone` 把最上层留成石头），玩家在水下撞到盖板，速度被碰撞层改写。

2. **Space 上浮瞬态**（从 $-0.025$ 稳态起）：`0.015 → 0.047 → 0.0726 → 0.0931 → … → 0.1742`，
   正是 `v' = 0.8v + 0.04 - 0.005`；松键后 `0.1742 → 0.1344 → 0.1025 → …` 同样逐位。

3. **入水跳跃没有 0.028**：入水前按住 Space，`in_water` 首真后第 3 个 tick 才切水物理，
   该 tick 残余 `+0.04000`；入水后再按，2–4 tick 后才出现第一个 +0.04。
   这与"刚进水跳不起来"的手感一致（输入队列 + 水物理切换的延迟，不是门坏了）。

4. **游泳向上分支（修复 sprint 注入后）**：9 格深池、pitch $0°$ 稳态 `vy=0`；
   pitch $-30°$（$\tau=0.5$）稳态 `+0.121`（$=0.242\tau$）；$-60°/-90°$ 会先撞水面。
   旧的"平视 +0.11 / 与俯仰无关 +0.16~0.21"不再出现。

5. **疾跑注入曾带隐藏 jump**：旧默认模式下 `set_input(sprint=True)`（甚至 forward=0）
   会触发 0.42 跳跃；`Input.cpp` 在 mode 1 同时开启时跳过 sprint 按钮后，平地
   `sprint=true + forward=1` 的 26 tick 岩浆尾段恒 $-0.04$，干地起步前两个记录 tick 为 0、
   第一个移动 tick 走/跑同为 `0.0980`、第二个起分叉（`0.1515` vs `0.1809`），
   稳态 `0.2157` vs `0.2804`。回归检查 `sprint_no_jump`。
   首 tick 已经是 0.04（不是 0.028），因为入水已 40 tick，`a16+12` 闩锁早已清零 ——
   与 R-F10 的"首 tick 0.028"互补。

---

## 8. 工具与产物

- 案例目录：`reverse-skill/work/mc-bedrock-fluid-12651/`（`mcp.py` JSON-RPC 客户端、
  `map45.py` 字节匹配、`start.ps1` 只读开库）。
- 本轮反编译导出与探针脚本在仓库 `out/`（未提交，gitignore）：
  `out/rev51/dec_*.c`（函数级）、`out/probe_entry_clamp2.py`、`out/probe_space_rise.py`。
- 旧的 45 专项报告：`reverse-skill/work/mc-bedrock-fluid-swim/`
  （`report_fluid_swim.md` + `FORMULAS_fluid_swim.md`），本文对其中两处已作勘误（§6）。
