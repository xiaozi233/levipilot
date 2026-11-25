# 疾跑状态机、撞墙断疾跑与疾跑跳（1.26.51.01）

> 版本：`Minecraft.Windows.exe` `1.26.51.01`（LeviLamina `26.51.7`，`image_base = 0x140000000`）。
> 证据约定：`[代码]` 反汇编/调用链；`[常量]` `.rdata` 浮点、组件 ID、结构布局；`[实测]` LeviPilot 逐 tick 录制与 ECS 回读；`[推断]` 由直接证据补出的推导。
> 复现脚本：[`verify_sprint.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/sprint-12651/probes/verify_sprint.py)。原始数据：gitignored `out/sprint-1.26.51.01.json`（本文所有数字均由该 JSON 导出）。
> 相位口径：录制 `vx/vy/vz` 是本拍已完成位移；watch `+0x18/+0x1C/+0x20` 是 `StateVectorComponent::mPosDelta`（摩擦/加速后的下一拍待执行位移）。两者的关系见 §5。

---

## 0. 结论摘要（返工 v2）

1. **四系统入口**：`SprintTimerSystem`（vt `0x14ea34f30`，`0x148b59df0/0x148b5a320`）、`SetRequestSprintTriggerSystem`（vt `0x14eac9f40`，`0x14b1a2670/0x14b1a2b50`）、`IntentSprintTriggerSystem`（vt `0x14eac9f80`，`0x14b1a3a80/0x14b1a3dc0`）、`ActionSprintTriggerSystem`（vt `0x14eaca020`，`0x14b1a9fe0/0x14b1aa080`）`[代码]` `[常量]`。
2. **起跑门控**：`0x14b1a4c80` 要求输入 `y ≥ 0.707107f`、`y > 0`、`|x| ≤ 0.707107f`、`sqrt(x²+y²) ≥ 0.707107f`，并读 `PlayerInputRequestComponent` 的允许/饥饿限制标志（`+0x0F == 1` 且 `+0x10 == 0`，由 `IntentSprintTriggerSystem 0x14b1a379f` 写入）。实测纯倒车 / 纯侧走 / 倒+侧 **永不进入疾跑** `[代码]` `[实测]`。
3. **保持与取消**：倒车 / 侧走 / 潜行在切换后的第一个状态拍取消；跳跃与斜前保持 `[实测]`。
4. **撞墙断疾跑**：**唯一** `5e-5f`（`0x14eac97e0`）只被 `0x14b1a4c80` 引用；完整绑定见 §3（`VanillaClientGameplayComponent.mLastDelta/mLastPos` + `StateVectorComponent.mPos` + `PlayerInputRequestComponent +0x0A` + `PlayerActionComponent` 的 `0x400`）。实测存在**三个角度区间** `[代码]` `[常量]` `[实测]`：
   - **单次取消**（0°~20°）：主轴被墙挡死且 `|Δ|<5e-5`，接触当拍断；
   - **逐拍高频闪烁**（23.5°~24.4°）：主轴在滑动轴与被挡轴之间逐拍交替，`sprint` 位每拍翻转；
   - **稳定滑行**（≥24.5°）：横向占优，不断。
   - X 墙的对应临界为 `90°−24.42° = 65.58°`：**65.6° 闪烁、≤65.58° 稳定**，与 Z 墙镜像一致。
5. **`f65` 现象被复现并解释**：它就是临界角附近的**逐拍 `sprint` 翻转**（不是“碰撞随机断”）；Z 墙在 `23.5°~24.4°`、X 墙在 `65.6°` 各测得 24~29 次翻转。
6. **地面与空中都没有 1-tick 延迟**：静止起步首拍走 `0.098`、疾跑 `0.1274`（`=0.098×1.3`）；空中首拍 `0.0196` / `0.02548`（`1.3×`）`[实测]`。
7. **疾跑跳水平 `+0.2` 的真实位置**：`JumpFromGroundSystem`（注册 `0x146823ebf`，vtable `0x14ea67240`，slots `0x149610170/0x1496169c0`）调用的 `0x1495e9200` 内 `0x1495e940c..0x1495e9478`：读 `ActorDataFlag bit3`（`SPRINTING`）→ `yaw × π/180 × 10430.378f` 查 `SIN_TABLE (0x151d6973c)` → `mPosDelta.x -= 0.2·sin`、`mPosDelta.z += 0.2·cos`（`0.2f=0x3e4ccccd`）`[代码]` `[常量]`。实测直线疾跑跳 `mpd_z 0.14578 → 0.34916`，`0.546×(0.14578+0.1274)+0.2 = 0.34916` 精确成立；贴墙滑行跳的**增量向量** `(-0.08450, 0.18126) = 0.2×(sin25°, cos25°)`，模长 `0.19999`；走路跳与“断疾跑后跳”均无该增量 `[实测]`。
8. **撤回**：上一版把 `0x1495eae35`（写 `[r12+0x1C]` = `mPosDelta.y`，门控为方块 `BlockProperty` bit 33 = `Climbable`）错标为疾跑跳冲量；那实际是**攀爬上行 `+0.2`**（梯子/藤蔓），与疾跑跳无关 `[代码]`。

---

## 1. 四个系统的注册与入口

| 系统 | 注册点（名字字面量） | vtable | slot[5] / slot[6] |
| --- | --- | --- | --- |
| `SprintTimerSystem` | `0x14681f3d6`（`0x14ec33eb0`） | `0x14ea34f30` | `0x148b59df0` / `0x148b5a320` |
| `SetRequestSprintTriggerSystem` | `0x14681f462`（`0x14ec3403c`） | `0x14eac9f40` | `0x14b1a2670` / `0x14b1a2b50` |
| `IntentSprintTriggerSystem` | `0x14681f4fa`（`0x14ec3405a`） | `0x14eac9f80` | `0x14b1a3a80` / `0x14b1a3dc0` |
| `ActionSprintTriggerSystem` | `0x14681fb62`（`0x14ec34074`） | `0x14eaca020` | `0x14b1a9fe0` / `0x14b1aa080` |

- `ActionSprintTriggerSystem` 单实体入口 `0x14b1aa080`：`mov edx, 0x3000f399` 读 `PlayerActionComponent`，`test ecx,0x200`（`StartSprinting`）/ `test ecx,0x400`（`StopSprinting`），随后 `Actor::setSprinting`（`0x14b17c820`，写 ADF bit3 并压 `SPRINTING_BOOST`）`[代码]`。
- 疾跑倍率归因保持 `0.3 MULTIPLY_TOTAL`（`(1+0.3)×0.1=0.13`），不是旧的 `DEFAULT_SPRINT_SPEED_MULTIPLIER`（`check_sprint_boost.py` 守卫）`[常量]`。

---

## 2. 起跑 / 保持 / 取消

### 2.1 输入门控（静态）

```
0x14b1a4d16  movss xmm0, [rdx+4]                 ; 输入分量 y（PlayerInputRequestComponent +0x04）
0x14b1a4d1b  ucomiss xmm0, 0.707107f             ; y >= 1/sqrt(2)
0x14b1a4d24  cmp  byte [rdx+0x0F], 1             ; 允许疾跑标志（Intent 0x14b1a379f 写入）
0x14b1a4d2a  cmp  byte [rdx+0x10], 0             ; 饥饿/状态限制标志（0 表示未受限）
0x14b1a4ec0  sqrt(x²+y²) >= 0.707107f
0x14b1a4ef0  y > 0
0x14b1a4f00  |x| <= 0.707107f
```

`PlayerInputRequestComponent`（`0x6b04a058`，SDK 无对应公开头文件，字段语义由反汇编定性 `[代码]` `[推断]`）：`+0x00/+0x04` 为输入 `(x, y)`、`+0x08` 为当前疾跑状态位、`+0x09` 为取消/条件位、`+0x0A` 为停止疾跑输出位、`+0x0F/+0x10` 为 `IntentSprintTriggerSystem` 核心 `0x14b1a34f0`（`0x14b1a379f`）写入的 2 字节允许/饥饿限制标记（食物 ≤6 时在 `0x14b1a378a` 置位限制）。

### 2.2 实测矩阵

| 输入 | 起跑 | 保持阶段切换 |
| --- | --- | --- |
| 前进 | ✅（第 1 状态拍置位） | — |
| 倒车 | ❌（全程 0） | 切换拍即取消 |
| 纯侧走 | ❌ | 切换拍即取消 |
| 倒车+侧走 | ❌ | — |
| 前进+潜行 | — | 切换拍即取消 |
| 前进+跳跃 | — | 保持 |

---

## 3. 撞墙断疾跑（完整绑定 + 三区间）

### 3.1 唯一 `5e-5f` 与组件绑定

`0x14eac97e0` 是**全二进制唯一**的 `5e-5f`（`u32=0x3851b717`），`--xrefs` 只有 `0x14b1a4f60` / `0x14b1a4f96` 两处，都在 `0x14b1a4c80..0x14b1a5097` 内 `[常量]`。

调用方 `0x14b1a7ec0` 经 `0x14b1a50a0` 取出 9 个组件（`--func 0x14b1a50a0` 的 `mov edx, ...`）：`0x18b1887`(MoveInput)、`0x1b5d5238`(StateVector)、`0xc67426f3`(ActorDataFlag)、`0x6b04a058`(PlayerInputRequest)、`0xa406686e`(VanillaClientGameplay)、`0x3000f399`(PlayerAction)、`0x6de78dc5`、`0xe6a1b550`、`0xbd68c2e2` `[代码]` `[常量]`。

**精确公式**（`rcx` = `VanillaClientGameplayComponent`，`rbx` = `StateVectorComponent`，`rdx` = `PlayerInputRequestComponent`，`rax` = `PlayerActionComponent`）：

```
起跑分支（0x14b1a4d4f..0x14b1a4e8f，未处于疾跑态时）：
  检查 mWasSneaking(+0x16) == 0 && (mWasRunning(+0x17) & 1) == 0 && mSprintTriggerTime(+0x00) > 0 等
  => 置 [rdx+0x08] = 1、PlayerAction +1 |= 0x02（StartSprinting 0x200）

撞墙取消分支（0x14b1a4e93..0x14b1a4fa6，已处于疾跑态时直接进入）：
  [前向 ±45° 扇区] && [rdx+0x0F] == 1
  => 若 |mLastDelta.z| > |mLastDelta.x| 且 |mPos.z − mLastPos.z| < 5e-5f
     或 |mLastDelta.x| > |mLastDelta.z| 且 |mPos.x − mLastPos.x| < 5e-5f
  => 写 [rdx+0x0A] = 1（0x14b1a4fa6），取消时写 mSprintingOnInput(+0x19) = 0（0x14b1a5073）、PlayerAction +1 |= 0x04（StopSprinting 0x400，0x14b1a5077）
```

字段来源区分：`VanillaClientGameplayComponent`（`VanillaClientGameplayComponent.h` SDK 头已逐一对照）的 `mSprintTriggerTime +0x00`、`mWasSneaking +0x16`、`mWasRunning +0x17`、`mSprintingOnInput +0x19`、`mLastDelta (Vec3) +0x1C`、`mLastPos (Vec3) +0x28`，以及 `StateVectorComponent.mPos`（`+0x00/+0x08`）为 `[常量]`；`PlayerInputRequestComponent`（SDK 无公开头文件）的 `+0x08`、`+0x0A`（停止疾跑输出位）、`+0x0F/+0x10`（允许/饥饿限制标志）语义由反汇编定性 `[代码]` `[推断]`。

> 上一版把 `[rdx+0xF]/[rdx+0x10]` 写成“疾跑请求/潜行标志”、把 `[rcx+…]` 留作“未知 Vec3”，并声称“没有水平碰撞标志才进入判定”——均已在本文更正：那里读的是 `IntentSprintTriggerSystem` 写入的两个状态/饥饿标记，且该段代码**不读** `HorizontalCollisionFlagComponent`。

### 3.2 实测：三个角度区间

41 tick 贴墙（起点距墙 5 格、墙长 61 格；**硬断言**：接触后全程 `on_ground`、法向速度 `≤0.02`、位置误差 `≤0.05`，否则脚本失败）：

| 与墙面法线夹角 | Z 墙 `toggles` | 结论 |
| --- | --- | --- |
| 0° / 1° / 5° / 10° / 15° / 20° | 2（初始 0→1，接触拍 1→0） | **单次取消** |
| **23.5° / 24.0° / 24.3° / 24.4°** | **24 ~ 25** | **逐拍闪烁** |
| 24.5° / 24.7° / 25° / 30° / 45° / 65° | 1（仅初始 0→1） | **稳定滑行** |

X 墙（`yaw 90` 正对 +x 墙，镜像角度）：

| yaw（与 X 墙法线夹角） | X 墙 `toggles` | 结论 |
| --- | --- | --- |
| 90°（0°）/ 89°（1°） | 2 | 单次取消 |
| **65.6°（24.4°）** | **29** | **逐拍闪烁** |
| 65.58°（24.42°）/ 65.5°（24.5°）/ 65°（25°）/ 45°（45°） | 1 | 稳定滑行 |

- 临界值：Z 墙落在 `24.4°~24.5°`，X 墙落在 `65.58°~65.6°`，两者互为补角（`24.42° + 65.58° = 90°`），与 `arctan(1−0.546) = 24.42°` 的预测一致 `[实测]` `[推断]`。
- **`f65` = 临界角附近的逐拍 `sprint` 翻转**：闪烁带内 `sprint` 序列呈 `0,1,0,1,…`，相邻拍速度在 `0.0986 ↔ 0.1065` 之间抖动；这是主轴在“被墙挡死轴”和“滑行轴”之间逐拍交替导致的判定翻转，不是随机断 `[实测]`。
- 贴墙起步（初始就在墙边）：0°/24.4° 单次取消；25° 闪烁（21 次）；45° 三次翻转后稳定；65° 稳定。

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

---

## 6. 与 Java 版对照

| 维度 | Java 1.8 | Java 1.19.4+ / 1.21.4+ | 基岩版 1.26.51.01 |
| --- | --- | --- | --- |
| 起跑输入 | 前进即可（`W`）；纯倒车/纯侧走不启动 | 同 | **前向 ±45° 扇区**（`sqrt≥0.707`、`y>0`、`|x|≤0.707`） |
| 倒车/侧走保持 | **不能保持**（只允许 `W+A/D` 斜前） | 同 | 保持拍即取消 |
| 撞墙断疾跑 | 任意水平碰撞即断 | 1.21.4+ 仅非轻微水平碰撞断 | **主轴 `|Δ|<5e-5` 才断**（≤20° 断；23.5~24.4° 闪烁；≥24.5° 稳定） |
| 空中 1-tick 延迟 | 有 | 无 | **无**（首拍 1.3×） |
| 疾跑跳水平冲量 | `+0.2`（朝向） | `+0.2` | **`+0.2`**（实测 `0.20000/0.19999`） |
| 乘数归因 | `0.3 MULTIPLY_TOTAL` | 同 | `0.3 MULTIPLY_TOTAL`（`(1+0.3)×0.1=0.13`） |

> 上一版把 Java 写成“倒车/侧走保持”“撞墙断疾跑无”——**已更正**：Java 同样不允许倒车/侧走保持，且 1.8~1.21.3 任意水平碰撞即断（1.21.4+ 才放宽为“非轻微”）。

---

## 7. 未闭合项

1. **`|Δ|` 判定的物理含义**：`mPos − mLastPos` 与 `mLastDelta` 的写入时序（哪一拍写、碰撞前后各写几次）尚未逐指令确认；公式的字段绑定已确定，时序仍属 `[推断]`。
2. **闪烁带的精细边界**：Z 墙落在 `24.4°~24.5°`、X 墙 `65.58°~65.6°`，未做 0.01° 级细扫；临界值的闭式推导（`arctan(1−0.546)`）仍是 `[推断]`。
3. **Intent 条件全集**：饥饿 `6.0f` 门槛已定位；失明、骑乘、使用物品/格挡、水中/游泳等分支未逐条静态确认，也未全部实机覆盖。
4. **`SprintTimerSystem` 双击窗口**：`0x148b5a320` 的计数器递减已见，精确 tick 上限未实测。
5. **`JumpFromGroundSystem` 的调用来源**：`0x1495e9200` 被 `0x1495e9520`/`0x1495e98d0` 与 `0x1496169c0` 内三处调用；各调用点的前置条件（离地/骑乘等）未逐条展开。

---

## 8. 复现

```bash
py -3.11 driver/dev.py run --no-build --world "超平坦测试" --background --allow allow_input_inject=1 --allow allow_mem_read=1
py -3.11 driver/dev.py exec "py -3.11 skills/bedrock-re/findings/1.26.51.01/cases/sprint-12651/probes/verify_sprint.py"
```

脚本内置夹具硬断言：宽 61 格平台 + 长墙、接触后全程 `on_ground`、法向速度 `≤0.02`、位置误差 `≤0.05`；任何场景未真正贴墙/离地都会直接失败。原始数据：`out/sprint-1.26.51.01.json`。
