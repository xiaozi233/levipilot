# 潜行、爬行与姿态尺寸切换（1.26.51.01）

> 版本：`Minecraft.Windows.exe` `1.26.51.01`（LeviLamina `26.51.7`，`image_base = 0x140000000`）。
> 证据约定：`[代码]` 反汇编/控制流；`[常量]` `.rdata` 浮点、组件哈希、注册点；`[实测]` LeviPilot 逐 tick 录制与 ECS 回读；`[推断]` 由直接证据补出的环节。
> 复现脚本：[`verify_sneaking_crawling.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/sneaking-crawling-12651/probes/verify_sneaking_crawling.py)（Round 1–3，`r03-048`）与 [`verify_sneaking_crawling_tail.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/sneaking-crawling-12651/probes/verify_sneaking_crawling_tail.py)（Round 5，`r05-068`）。原始数据：gitignored `out/sneaking-crawling-1.26.51.01.json` 与 `out/sneaking-crawling-tail-1.26.51.01.json`。
> 相位口径：录制 `vx/vy/vz` 是**已完成位移**；`watch` 的 `AABBShapeComponent+0x1C` 是**当前碰撞箱高度**；`state.head_y` 是独立的镜头高度，**不等于**碰撞箱高度。
> **勘误说明**：本版含 v2 四处静态纠正（§9 勘误表）、v3 露天水池对照纠正、v4 → v5 的 Round 5 纠正（§11：Swift Sneak 调度序、0.91 空气阻力算式、静态消费点、台沿符号），以及独立复核静态补正（`TickServerMoveInputHandler 0x1404495e0` 为主消费点解释爬行/2 拍滞后、`0x141A1C130` 跳表为 `ActorDataIDs [38,53,54]`）；Round 1/2 的 `pose_swim sneaking=true` 为水池 `y=-37` 封顶导致 `1.56 < 1.79` 的夹具假象，改露天水池后实测 `sneaking=false`。

---

## 0. 结论摘要

| # | 结论 | 证据 |
| --- | --- | --- |
| 1 | 玩家 AABB：站立 **0.6 × 1.8**、潜行 **0.6 × 1.49**、爬行/游泳 **0.6 × 0.6**（露天游泳 `sneaking=false`） | `[常量]` `[实测]` |
| 2 | 姿态由 `0x148FEA2E0` **每 tick 用三个级联碰撞盒**重新判定：站立盒 `1.8` → 潜行盒 `1.49` → 爬行盒 `0.6`，每个盒**六面各内缩 `0.01f`** ⇒ 真实门槛 **`1.79 / 1.48 / 0.59`** | `[代码]` `[常量]` `[实测]` |
| 3 | 潜行/强制潜行/爬行移速 = **`0.3 + 0.15 × SwiftSneak`**，上限 `1.0`，对爬行与强制潜行同样生效 | `[代码]` `[实测]` |
| 4 | 边缘防跌落的门控：**玩家读 `MoveInputComponent+0x00 bit0`（潜行按键）**；只有**没有 `MoveInput` 的非玩家**才退回 `ADF bit1`；全函数不读 `bit114`。另有 `ADF bit6` 会整段跳过夹取 | `[代码]` `[实测]` |
| 5 | 因此**低顶强制潜行（不按键）不防跌落**（实测坠落）；按住潜行键才停住；边缘下方落差 `≤ 0.5625` 走下去、`≥ 0.625` 拦停 | `[代码]` `[实测]` |
| 6 | 探针盒水平四边各内缩 **`0.025`**、下探 **`1.01 × maxAutoStep = 0.568125`**、回退量子 **`0.05`**；回退按 **X → Z → 对角** 三段处理 | `[代码]` `[实测]` |
| 7 | 同 tick 顺序：姿态/潜行组（`0x14681DA00`）整组先于移动碰撞组（`0x14681AC20`）；`SneakMovementSystem` 夹在 `MoveCollisionSystem` 与 `ActorMoveSystem` 之间 | `[代码]` `[常量]` |

---

## 1. 姿态尺寸与判定链

### 1.1 潜行高度由**世界版本**选，不是按实体选

`PlayerBoundingBoxStateUpdateSystem`（注册点 `0x14681E6CA`，vtable `0x14EA4F990`，slot[5] `0x148FE9A20`）先按实体取
`BaseGameVersionComponent`（`0x75C382E5`），再决定这一版世界的**潜行高度参数** `[代码]` `[常量]`：

```
0x148FE9B35  cmp dword ptr [r8 + 0x28], 0x75C382E5   ; BaseGameVersionComponent
0x148FE9B5A  mov edi, 0x3FE66666        ; 1.8f —— 旧版世界
0x148FE9B5F..0x148FE9B98                 ; 四条开关（全局 0x151D348D8 / 栈 0x3C0 /
                                         ; 全局 0x151D348C6 bit0 / 栈 0x3AF + 0x14027E990）
0x148FE9B9A  mov edi, 0x3FBEB852        ; 1.49f —— 现代版世界（本版本默认走这支）
0x148FE9B9F  mov [rbp + 0x408], edi     ; 传给逐实体 worker 的 sneakHeight
```

这个值随后经 `0x148FFB1E0` 的虚调用 `0x148FE9BDC` 传进**真正的逐实体 worker `0x148FEA2E0`** `[代码]`。
所以 `1.8/1.49` 的差别是**同一世界内的全局参数**，与「玩家现在是否蹲着」无关。

### 1.2 真正的三档判定：`0x148FEA2E0`

worker 对实体的 AABB 依次做**三个级联短路测试**，每个盒子都在**六个面各内缩 `0.01f`**
（`0x14E7B9B80 = +0.01f`、`0x14E7D5AB8 = -0.01f`），命中就清掉对应的能力字节并继续降级 `[代码]`：

| 序 | 盒高 | 盒顶（相对脚） | 命中时写 | 地址 |
| --- | --- | --- | --- | --- |
| 1 | `1.8f`（`0x14E6AE49C`） | `feet + 1.8 − 0.01 = 1.79` | `[rsi + 0x0C] = 0`（不能站） | `0x148FEA45C..0x148FEA5B3` |
| 2 | `sneakHeight`（本版 `1.49`，`[rbp+0x110]`） | `feet + 1.49 − 0.01 = 1.48` | `[rsi + 0x0D] = 0`（不能蹲） | `0x148FEA5B7..0x148FEA742` |
| 3 | `0.6f`（`0x14E6AE498`） | `feet + 0.6 − 0.01 = 0.59` | `[rsi + 0x0E] = 0`（不能爬） | `0x148FEA746..0x148FEA8B8` |

等价于（每个盒都先 `min += 0.01`、`max −= 0.01`，宽度不足 `0.02` 时用中点回退）：

```cpp
canStand = !collides(box(feet, 1.8  - 0.01));
canSneak = !collides(box(feet, 1.49 - 0.01));
canCrawl = !collides(box(feet, 0.6  - 0.01));
```

**净空门槛因此是 `1.79 / 1.48 / 0.59`，不是 `1.8 / 1.49 / 0.6`** `[代码]` `[常量]`。
三个字节即「本拍允许的姿态集合」，由下游系统折算成最终 AABB 高度。

`0.6f` 在静态里只以**判定盒**的身份出现（`0x148FEA75A`）；真正写进
`ShouldUpdateBoundingBoxRequestComponent`（`0x752C8E04`）的 `(w,h)` 是**表/组件驱动**的，
Round 4 已把整条链闭合 `[代码]` `[常量]` `[实测]`：

| 步 | 地址 | 动作 |
| --- | --- | --- |
| ① 姿态参数 | `0x148FE9B5A`=1.8、`0x148FE9B9A`=1.49、`0x148FEA45C/5B7/75A`=1.8/1.49/0.6 三档盒 | 1.8/1.49 是**世界版本参数**，0.6 是爬行判定盒；三者都不直接写请求 |
| ② 尺寸数据 | `ActorDataBoundingBoxComponent (0x5ACD5A3E)` | 实测 32B：`+0x00 scale=1.0`、`+0x04 width=0.6`、`+0x08 height`（站立 `1.8` / 潜行 `1.49` / 爬行 `0.6`） |
| ③ 同步数据回调 | `0x141A1C130`（`Actor::onSynchedDataUpdate`，跳表 `0x14E7D0400`） | 下标 `[38, 53, 54]`（SDK `ActorDataIDs::Reserved038/053/054`，即 Scale/Width/Height）落入 `0x141A1C175`，经 `0x141E15010` 取 `ActorDataBoundingBoxComponent` 的 `+0x04/+0x08` → `xmm1/xmm2` |
| ④ **写请求** | `0x1419F93A0`：`movss [req], xmm7` @`0x1419F9536`、`movss [req+4], xmm6` @`0x1419F953A`、`mov byte [req+8],1` @`0x1419F9532` | 写 `ShouldUpdateBoundingBoxRequestComponent` 的 `(w,h,dirty)` |
| ⑤ 消费请求 | `0x14289AFB0`（系统体）→ thunk `0x14289AEE0` → `0x14289A870` | 写 `AABBShapeComponent +0x18 width` / `+0x1C height`（`0x14289A9C0` / `0x14289A9C5`），再用自身 `min.y` 与宽高重建六面 |
| ⑥ 回写尺寸数据 | `0x14289A870` 尾部 `0x14289AA60` / `0x14289AA75` | 同一 `(w,h)` 写回 `ActorDataBoundingBoxComponent +0x04/+0x08`，并置 `[flags+6]` bit5/bit6 |

**动态实锤**（Round 4 `watch_set(thread="both")`）：`AABBShapeComponent+0x1C` 写观察在潜行按下拍命中
`rip=0x14289A9C5`、写入值 `1.49`；松开拍同址写回 `1.8`；`+0x18` 宽度全程 `0.6`。
执行观察 `0x141A1C130` 在"按下/松开"两拍各命中一次，栈为
`0x141A1C130 ← 0x1401E78B6 ← 0x149046586 ← … ← 0x1425C846F（foreachTickingSystem）← 0x1463B0C27（主 pass）`，
证明走的是每 tick 的 ECS pass，而不是 spawn/传送的一次性路径。

**如实边界**：`0x141A1C130` 只是在 `ActorDataIDs [38, 53, 54]` 变动时转发碰撞箱请求，并不存在“站/蹲/爬跳表下标”；真正按姿态把 `(0.6, 1.8/1.49/0.6)` 写进 `ActorDataBoundingBoxComponent` 的上游是姿态切换拍的独立写链（`0x142A3BBC0` 的 `0x142A3BCC1/0x142A3BCCA`，上游 `0x142A1DBA0 → 0x143DFAF20 → 0x14204C3A0 → 0x1401E8570`），该上游写链无符号可读，标 `unresolved`。

### 1.3 实测尺寸

| 姿态 | 宽 | 高 | `state.sneaking` | 潜行**按键** `input.sneak` | `head_y − feet_y` |
| --- | --- | --- | --- | --- | --- |
| 站立 | `0.600006` | **`1.799999`** | `false` | `false` | `+1.520012` |
| 潜行（按住键） | `0.600006` | **`1.490002`** | `true` | `true` | `+1.170353` |
| 爬行（1.0 格通道） | `0.600006` | **`0.599998`** | `false` | `false` | `+0.300598` |
| 游泳（露天 3 格深水池） | `0.600006` | **`0.599998`** | **`false`** | **`false`** | **`+0.319054`** |

> **v3 夹具勘误**：Round 1 / v2 中 `pose_swim` 记录为 `sneaking=true`（`feet_y=-38.56`），原因是旧水池在 `y=-37` 封了石顶，头顶净空仅 `1.56 < 1.79` 而被压成强制潜行。改为**露天水池**后，`r03-048` 实测 `pose_swim` 为 `feet_y=-38.707706`、`head_y=-38.388653`、`swimming=true`、**`sneaking=false`**、`input.sneak=false`（`state.sneaking` 只在真正潜行或低顶压低时置位；注：`StateVector.y` 仍为 `feet + 1.6200101`，表最后一列为客户端 `state.head_y` 回读）。

### 1.4 组件与角色

| 组件/角色 | 哈希 / 地址 | 作用 |
| --- | --- | --- |
| `ShouldUpdateBoundingBoxRequestComponent` | `0x752C8E04`（12B） | 尺寸请求 `(w, h, dirty)` |
| `AABBRelativeSizeUpdateComponent` | `0x615CEE62`（48B） | 头/眼/嘴偏移相对系数 |
| `AbsoluteSizeUpdateComponent` / `CustomSizeUpdateComponent` | `0xBE91FCC3` / `0x42637D8D` | 绝对/自定义尺寸覆盖 |
| `AABBShapeComponent` | `0xBAC1B3CF`（32B） | 真实碰撞箱；高度在 `+0x1C` |
| `BaseGameVersionComponent` | `0x75C382E5` | 决定潜行高度参数 `1.49` / `1.8` |
| `IsHorizontalPoseFlagComponent` | `0x2430E499` | 水平姿态（爬行/游泳）；`VanillaOffsetSystem` 命中时镜头偏移取 `0.2f` |
| `MoveInputComponent` | `0x018B1887`（步长 `0x64`） | `+0x00 bit0` = 潜行按键 |
| `SneakingComponent` / `SwiftSneakEnchantComponent` | `0x8918332C` / `0xCDD62A2C` | 潜行态 / 迅捷潜行等级（int32） |

---

## 2. 净空阶梯与姿态选择

### 2.1 精细净空边界实测（`[实测]`，全部 `block_shapes` 回读）

| 实测净空 | 地面采样方块 | AABB 高度 | `sneaking` | 判定 |
| --- | --- | --- | --- | --- |
| `2.000` | `stone` | `1.799999` | `false` | 站立 |
| `1.9375` | `white_carpet` | `1.799999` | `false` | 站立 |
| **`1.817501`** | `trapdoor`（闭合底置） | **`1.799999`** | `false` | 站立（`> 1.79` 仍站立） |
| **`1.750`** | `snow_layer`（3 层） | **`1.490002`** | **`true`** | 强制潜行（`< 1.79` 降级） |
| **`1.500`** | `normal_stone_slab` | **`1.490002`** | `true` | 强制潜行（`≥ 1.48` 仍潜行） |
| **`1.4375`** | `stonecutter_block` | **`0.599998`** | `false` | 强制爬行（`< 1.48` 降级） |
| **`0.625`** | `daylight_detector` | **`0.599998`** | `false` | 强制爬行（`≥ 0.59` 仍爬行） |
| **`0.5625`** | `campfire` | 被推出（`z=209.3` 站起 `1.799999`） | `false` | 塞不下（`< 0.59`） |
| **`0.500`** | `normal_stone_slab` | 被推出（`z=211.2` 站起 `1.799999`） | `false` | 塞不下（`< 0.59`） |

精细实测与 `0x148FEA2E0` 的六面 `0.01f` 内缩门槛完全吻合：站↔蹲严格落在 `(1.750, 1.8175]`（静态 `1.79`）、蹲↔爬落在 `(1.4375, 1.500]`（静态 `1.48`）、爬↔无法容纳落在 `(0.5625, 0.625]`（静态 `0.59`）；结合 Round 1 0.125 步长扫描（`1.875→1.8`、`1.625→1.49`、`1.375..0.750→0.6`），三道分界全部闭合 `[代码]` `[实测]`。

### 2.2 进入矮顶的路径与退出行为（`[实测]`）

| 场景 | 结果（`r03-048`） |
| --- | --- |
| 站立走进 `1.0` 格通道 | 卡在洞口 `x=213.700`（前沿 `214`），高度保持 `1.799999` |
| 潜行走进 `1.0` 格通道 | 同样卡在 `x=213.700`，高度保持 `1.490002` |
| **传送进** `1.0` 格通道 | 落地首拍（`tick 880`）即 `0.60000`，零过渡帧 |
| **活板门压头进爬行** | 在站立玩家头顶 `(205,-39,210)` 闭合 `trapdoor`：当拍从 `1.8 → 0.6`（`tick 5047→5048`），`feet_y=-40.0`、`sneaking=false` |
| **露天水池游进 `1.0` 干通道** | 经敞开 `fence_gate` 游出水面（`tick 5180 z=221.879 water=True` → `tick 5181 z=222.069 water=False`），终态 `z=225.789`、`feet_y=-41.0`、`swimming=false`，全程保持 `0.599998` 爬行 |
| **低顶松开潜行再走出** | `1.50` 缝下松键保持 `1.490002 / sneaking=true`；走到开放台面（`x=247.387, feet_y=-40.0`）自动恢复 `1.799999 / sneaking=false` |

引擎不会为了「挤进洞」提前降姿态：姿态来自**当前所在空间**的碰撞盒测试结果，而走向洞口时身体还在外侧高空间里；因此实机里进爬行空间需依赖**活板门压头、游泳出水、活塞或下落**等先将碰撞盒置入矮空间的手段。

---

## 3. 潜行/爬行移速与迅捷潜行

### 3.1 静态（`SneakingSystem`，vtable `0x14EAC9C30`，slot[5] `0x14B192C50`）

视图为 `ActorDataFlagComponent + SneakingComponent + ActorMovementTickNeededComponent` `[常量]`：

```
0x14B192D04  0.3f   (0x14E758C38)   基础
0x14B192D0C  0.15f  (0x14E6AE4DC)   每级
0x14B192D14  1.0f   (0x14E5A7060)   上限
0x14B192EE0  取 SwiftSneakEnchantComponent(0xCDD62A2C)
0x14B192F6B  test byte [rbx], 2        ; ADF bit1（潜行）
0x14B192F70  test byte [rbx+0xE], 4    ; ADF bit114（爬行/水平姿态）
0x14B192F80  cvtsi2ss xmm0, [rcx+r9*4] ; 等级
0x14B192F86  mulss xmm0, xmm7          ; ×0.15
0x14B192F8A  addss xmm0, xmm6          ; +0.3
0x14B192F91  minss xmm0, xmm8          ; min(…, 1.0)
0x14B192F9A  movss [rcx+rdi*4], xmm0   ; 每实体乘数
```

同段在三处视图分支各出现一次（`0x14B192F86` / `0x14B1931A4` / `0x14B1933D1`）`[代码]`。
注意这里的 `ADF bit1 / bit114` 是**移速乘数**的门控，与 §4 的防跌落门控是两套东西。

### 3.2 实测（`[实测]`，稳态 8 tick 滑动中位数，`r03-048`）

| 配置 | 速度（格/tick） | 相对行走 | 高度 |
| --- | --- | --- | --- |
| 行走 | `0.215866089` | `1.000000` | `1.8` |
| 潜行 | `0.064758301` | `0.299993` | `1.49` |
| **强制潜行（1.5 净空，不按键）** | **`0.064758301`** | **`0.299993`** | `1.49` |
| 爬行 | `0.064758301` | `0.299993` | `0.6` |
| 爬行 + 潜行键 | `0.064758301` | `0.299993` | `0.6` |
| 爬行 + 疾跑键 | `0.064758301` | `0.299993`（疾跑位始终 0） | `0.6` |
| 潜行 + 迅捷潜行 I / II / III | `0.097137 / 0.129517 / 0.161888` | `0.45 / 0.60 / 0.75` | `1.49` |
| 爬行 + 迅捷潜行 I / II / III | `0.097137 / 0.129517 / 0.161896` | `0.45 / 0.60 / 0.75` | `0.6` |
| **强制潜行 + 迅捷潜行 III** | **`0.161895752`** | **`0.749982`** | `1.49` |

`ss3` 的 `SwiftSneakEnchantComponent` 原始字节读到 `03000000` `[实测]`，与反汇编读 int32 一致。
**爬行不能疾跑**（44 tick 内 sprint 位恒 0）、**爬行可以跳**（1.0 格通道内 `vy` `0 → +0.400002`，随后 `-0.0784 / -0.155231 / -0.16637` 三拍落回，即完整 `+0.42` 被 0.4 的头顶净空裁短），并且**爬行可以自动上台阶**：在 `1.0` 格高通道中设置 `0.375` 格高 `daylight_detector`（台阶上方净空 `0.625 ≥ 0.59`），从 `(x=206.5, feet_y=-40.0)` 平滑走上台阶至 `(x=215.948, feet_y=-39.625)`，全程保持 `height=0.599998` `[实测]`。

### 3.3 首拍加速度与空中控制 `[代码]` `[实测]`

`verify_sneaking_crawling_tail.py`（Round 5，账本 `r05-068`，`exit: 0`）从静止起跑逐拍录制。
**"首拍"取第一个有位移的拍**：录制行的 `input.w` 标记会比实际移动早一拍，直接用会读出 `0`。

**夹具修正（Round 5）**：`set_input(..., ticks=N)` 到第 `N` 拍会**自动松开**该键；Round 4 的
`pre_sneak` 先发 `set_input(sneak=True, ticks=6)` 再起跑，潜行键在起跑前已经松开，所以
`sneak+ss3_held` 与 `sneak+ss3` 跑出了逐位相同的 `0.300` 首拍。Round 5 改为预滚请求
`ticks=pre_sneak+ticks+12`，并在起跑请求里继续 `sneak=True`，用 `state()` 断言
`sneaking=True / height≈1.49` 后才加前进键，全程没有松键/重按。

**地面首拍**（格/tick；首拍 = 第一个有位移的拍）

| 配置 | 首拍速度 | 首拍/行走 | 稳态速度 | 稳态/行走 | 姿态高度 |
| --- | --- | --- | --- | --- | --- |
| 行走 | `0.098007` | `1.000` | `0.213448` | `1.000` | `1.80` |
| 潜行（按键） | `0.029404` | `0.300` | `0.064034` | `0.300` | `1.49` |
| 强制潜行（1.5 净空、不按键） | `0.029404` | `0.300` | `0.064034` | `0.300` | `1.49` |
| 爬行（1.0 通道） | `0.029404` | `0.300` | `0.064034` | `0.300` | `0.60` |
| 潜行 + 迅捷潜行 III（**同拍**按下 W+Sneak） | `0.029404` | `0.300` | `0.157532` | `0.738` | `1.49` |
| 潜行 + 迅捷潜行 III（**先按住潜行 6 拍**） | **`0.073502`** | **`0.750`** | `0.160080` | `0.750` | `1.49` |
| 爬行 + 迅捷潜行 III（矮顶强制） | **`0.073502`** | **`0.750`** | `0.160080` | `0.750` | `0.60` |

> `sneak+ss3` 的稳态 `0.738` 是 10 拍窗口内速度尚未完全收敛（前几拍仍按 `0.3` 起步）；
> `sneak+ss3_held` 的 `0.750` 是同一窗口的对照。首拍比值才是本节判据。

**空中控制**（先点跳离地再按住前进；`a_i = v[i+1] − 0.91·v[i]`，`a_0 = v[0]`；`0.91f` 在
`0x14E76D2D8`）

| 配置 | 首拍加速度 | 首拍/空中行走 | 稳定最大加速度 | 最大/空中行走 |
| --- | --- | --- | --- | --- |
| 空中行走 | `0.019608` | `1.000` | `0.019604` | `1.000` |
| 空中潜行（无附魔，空中新按） | `0.005875` | `0.300` | `0.005889` | `0.300` |
| 空中潜行 + 迅捷潜行 I（起跳前按住） | `0.008820` | `0.450` | `0.008829` | `0.450` |
| 空中潜行 + 迅捷潜行 II（起跳前按住） | `0.011765` | `0.600` | `0.011767` | `0.600` |
| 空中潜行 + 迅捷潜行 III（起跳前按住） | `0.014694` | `0.750` | `0.014704` | `0.750` |
| 空中潜行 + 迅捷潜行 I/II/III（**空中新按**） | `0.005875` | `0.300` | `0.008824 / 0.011774 / 0.014714` | `0.450 / 0.601 / 0.751` |

结论 `[代码]` `[实测]`：

- **行走 / 潜行 / 强制潜行 / 爬行的乘数第一拍就满额**（`0.300` 精确，`0.098007 → 0.029404`），
  没有 Java 1.8 式的地面 1 tick 延迟；稳态比值同样是 `0.300`（`0.213448 → 0.064034`）。
- **迅捷潜行的"额外"加成滞后 2 拍，源自同 tick 三系统调度顺序**：在 `0x14681DA00` 组内，`TickServerMoveInputHandler`（注册 `0x14681dcf4`，vt `0x14EA34B10`，slot[5] `0x148B459F0` → `0x148B45510` → `0x148B2D870` → 消费叶 `0x1404495e0`）排在 `SneakingSystem`（`0x14B192C50`）**之前**，后者又排在 `SneakTriggerActionSystem`（`0x14B190C50`，写 `ADF bit1`）**之前**。因此站立同拍按下 `W+Sneak` 时：
  1. 第 1 拍：`0x1404495e0` 先跑，凭 `MoveInput & 0x40001` 命中、读旧 `SneakingComponent = 0.3`（地面 `0.029404` / 空中 `0.005875`）；随后 `SneakingSystem` 仍见 `ADF bit1=0` 写入 `0.3`；再之后 `Action` 才置 `ADF bit1=1`；
  2. 第 2 拍：`0x1404495e0` 再次先于 `SneakingSystem` 跑，**仍读上一拍的 `0.3`**（地面 `0.045456` / 空中 `0.01123` 仍按 `0.3×` 加速）；随后 `SneakingSystem` 见 `ADF bit1=1` 才写入 `0.75`；
  3. 第 3 拍：`0x1404495e0` 才读到 `0.75`（地面 `0.098312` / 空中 `0.019043..0.024918`）。
  先按住潜行 6 拍（`ADF bit1` 与 `SneakingComponent=0.75` 均已就绪）或矮顶强制爬行（`ADF bit114` 已置位）时，**首拍直接就是 `0.750`**（`0.073502`）。这条顺序来自 §5.1 注册表，不是"装备复制延迟"。
- **地面/空中/爬行共享主消费点 `0x1404495e0`**：`SneakingComponent` 的每实体乘数由 `SneakingSystem` 写 `0x14B192F9A`；**主消费点**是 `TickServerMoveInputHandler` 内的 `0x148B2DD41 → 0x1404495e0`（直接乘进 `MoveInputComponent +0x24/+0x28` `mMoveVector`）：门控 `MoveInput & 0x40001 || ADF bit1 (Sneaking) || ADF bit114 (Crawling)`，在 `isFlying / WasInWater / ADF bit57 (Swimming)` 时跳过，**没有任何 `on_ground` 分支**，读 `[SneakingComponent]` 缺省 `0.3f` 并执行 `0x1404497f9 mulss`。次级消费点 `0x144A26290`（`0x144A281FF/3F9/5DF` → `LocalMoveVelocityComponent 0x10257C61`）仅查 `ADF bit1` 与 `!bit57`，不查 `bit114` 与 `MoveInput`。按 `a_i = v[i+1]−0.91·v[i]` 复算，三档空中加速度精确落在 `0.450 / 0.600 / 0.750`（`<0.02` 容差），与地面稳态比值一致。
- **空中新按潜行同样滞后 2 拍**：空中才第一次按潜行时，前两拍加速度 `a_0 / a_1` 仍是 `0.300`（`0.005875 / 0.005885`），第 3 拍 `a_2` 起才升到 `0.45/0.60/0.75`（`0.008823 / 0.011753 / 0.014698`）；这与地面同拍按 `W+Sneak` 完全同源，不是空中特例。

---

## 4. 边缘防跌落：门控、探针与回退

### 4.1 门控：玩家读**潜行按键**，非玩家才退回 `ADF bit1`

`SneakMovementSystem` 注册点 `0x14681B057`（vtable `0x14EAC9B10`，slot[5] `0x14B18C5E0`，slot[6] `0x14B18D220`），
算法体 `0x14B18BB00`。两只入参组件对分别来自 `MoveInput`（`0x018B1887`）与 `ADF`（`0xC67426F3`）`[常量]`：

```
; —— 玩家路径：实体有 MoveInput（步长 0x64） ——
0x14B18BC11  mov ecx, esi
0x14B18BC1D  shr ecx, 4
0x14B18BC20  mov rcx, [r10 + rcx]      ; MoveInput 池
0x14B18BC27  imul r10, rsi, 0x64       ; stride 0x64
0x14B18BC2B  mov ecx, [rcx + r10]      ; MoveInput + 0x00
0x14B18BC2F  and ecx, 1                ; bit0 = 潜行按键
0x14B18BC32  mov [rax + 0x55], cl      ; → MoveRequest + 0x55
0x14B18BC35  jne 0x14B18BCA7           ; 按着才继续做夹取
0x14B18BC37  jmp 0x14B18BE61           ; 没按 → 整段跳过

; —— 非玩家路径：没有 MoveInput 才走这里 ——
0x14B18BC8E  mov rcx, [rdx]            ; ADF
0x14B18BC91  and ecx, 2                ; bit1 = SNEAKING
0x14B18BC9A  mov [rax + 0x55], r10b
0x14B18BCA1  je 0x14B18BE61            ; 位为 0 → 跳过
```

结论：

- **玩家**的防跌落门控是 **`MoveInputComponent + 0x00 bit0`（潜行按键）**，不是 `ADF bit1`；
- 只有**没有 `MoveInputComponent` 的非玩家生物**才退回 `ADF bit1`；
- **整个函数从不读 `bit114`**（v1 的写法有误）；
- `0x14B18BCA7 test byte [rdx], 0x40` 是 **`ActorFlags::Tempted`（bit 6）**：置位则 `jne 0x14B18BE61`
  **整段夹取跳过** `[代码]` `[常量]`。枚举名取自本机 LeviLamina `26.51.7`
  `include/mc/world/actor/ActorFlags.h`（`Onfire=0, Sneaking=1, …, Tempted=6, …, Crawling=114`）；
  二进制里 `minecraft:behavior.tempt`（`0x14EBCC0C5`）与 `FloatTempt`（`0x14EBCC0DE`）是该行为的注册/参数面，
  即该位由"被玩家手持食物吸引"的生物行为写入。
- **门控位置**：bit6 检查在**玩家按键路径（`0x14B18BC35 jne 0x14B18BCA7`）与非玩家 `ADF bit1` 路径
  （`0x14B18BCA1 je` 之后同址）汇合之后**，所以对两类实体都生效；玩家不会带 `Tempted`
  （`r03-048` 站立/潜行/爬行/游泳四态该位均为 0），实际效果只是**让被吸引的生物不被潜行机制拉回台沿**，
  不影响玩家 `[代码]` `[实测]`（"为什么设计成这样"属语义解释，标 `[推断]`）。
- 同段 `test byte [rbx+0xE], 4` = `ActorFlags::Crawling`（bit 114），是**移速乘数**的门控（§3.1），
  与这里的防跌落门控是两套东西。

### 4.2 探针盒与回退

```
0x14B18BCB6  movss xmm2, [0x14E760F30]  ; +0.025
0x14B18BCCD  addss xmm6, xmm2           ; min.x + 0.025
0x14B18BCF0  addss xmm7, [0x14E760F34]  ; max.x − 0.025   （Z 轴同构）
0x14B18BD00  mulss xmm11, [0x14E5C6370] ; 宽度不足 0.05 → 中点回退
0x14B18BD52  mulss xmm0, [0x14E7F8A04]  ; maxAutoStep × 1.01 = 0.568125
0x14B18BE47  movss xmm13, [0x14E6AE530] ; +0.05 回退量子
0x14B18BE50  movss xmm2,  [0x14E7D1AF8] ; −0.05
```

执行结构（Round 4 逐分支展开，X → Z → 对角三段共用同一个探针与同一份量子）`[代码]` `[常量]`：

```
一次性算好下探深度：xmm0 = maxAutoStep × 1.01 → [rsp+0xC]          0x14B18BD52（1.01f @0x14E7F8A04）
探针盒：水平两轴各内缩 0.025（xmin=min.x+0.025 / xmax=max.x-0.025，Z 同构）
        0x14B18BCCD / 0x14B18BCF0 / 0x14B18BD2A / 0x14B18BD2E（±0.025f @0x14E760F30/F34）
        内缩后宽度不足 0.05 → 回退到中点 0.5×(min+max)   0x14B18BD00 / 0x14B18BD3F（0.5f @0x14E5C6370）
        Y 取 AABB 自身 min.y/max.y，再减去 [rsp+0xC]      0x14B18BDD2 subss
X 段（0x14B18BDE4..0x14B18BF98，dx = MoveRequest+0x3C）:
    loop: 探针盒沿 x 平移 dx，扫碰撞盒（stride 0x18）
          有交叠 → 接受当前 dx（进 Z 段）
          无交叠 → dx = dx<0 ? dx+0.05 : dx-0.05（不跨 0）  0x14B18BEB6 的 min/max/andnps 混合
                   写回 [MoveRequest+0x3C]（0x14B18BEDC），回到 loop
Z 段（0x14B18BF9B..0x14B18C0D0，dz = MoveRequest+0x44）: 与 X 段同构（0x14B18BF91 写 0 / 0x14B18BF98 清零）
对角段（0x14B18C101..0x14B18C2A6）:
    if (dx == 0 && dz == 0) → 收尾（0x14B18C2AC）
    loop: 探针盒 = 内缩盒按 (dx,dz) 整体平移（Z 两端点各加 dz、X 两端点各加 dx，
          再取 min/max 定序，0x14B18C159..0x14B18C195）
          扫碰撞盒；有交叠 → 接受 (dx,dz)
          无交叠 → dx、dz **各减一个 0.05 量子**（符号感知、不跨 0）
                   0x14B18C1F0..0x14B18C236（dx）与 0x14B18C23B..0x14B18C287（dz）
                   两轴都写回 MoveRequest 后回到 loop；dx==dz==0 时退出
收尾（0x14B18C2AC）: |dx| ≤ 1.19209e-07 → [rcx+0x18]=0；|dz| 同 → [rcx+0x20]=0（0x34 000000 @0x14E6AE4C0）
```

要点：

- **`0.05` 量子同样作用于对角段**，而且**每轮同时把 `dx` 与 `dz` 各减一个量子**——不是逐轴分别回退，也不是沿对角向量等比例缩放；
- 对角段仍用**同一个 0.025 水平内缩**，竖直下探仍用**同一次算出的 `1.01 × maxAutoStep`**（`[rsp+0xC]` 在两段之间不重算，只在 `0x14B18C118` 再用一次）；
- 对角段每轮都以 `(dx,dz)` 组合平移做一次完整的六面重叠测试，所以"两轴都无支撑才一起归零"是**逐轮收缩到 `dx=dz=0`** 的结果，不是一次性清零。

### 4.3 实测（`[实测]`，`r03-048`）

台面外沿 `edge_z = 247.0`（最后一块在 `z=246`），玩家半宽 `0.3`：

| 场景 | 停点 `z` | 相对台沿 | `feet_y` | `sneaking` / 按键 |
| --- | --- | --- | --- | --- |
| 站立走 | `291.539`（继续前进） | — | `-60.0` | `false` / `false` |
| **手动潜行（按键）** | **`247.273529`** | 中心越沿 `0.274`、前沿越沿 `0.574`（重叠 `0.026`） | `-40.0` | `true` / `true` |
| 爬行（不按键） | `276.687`（继续前进） | — | `-60.0` | `false` / `false` |
| **爬行 + 按键** | **`247.266022`** | 中心越沿 `0.266`、前沿越沿 `0.566`（重叠 `0.034`） | `-40.0` | `false` / `true`（高度仍 `0.6`） |
| **低顶强制潜行（`1.5` 净空、不按键）** | **`276.399`（坠落）** | — | **`-60.0`** | 起步 `true` / **`false`** |
| **低顶强制潜行 + 按键** | **`247.266022`** | 中心越沿 `0.266`、前沿越沿 `0.566` | **`-39.5`**（半砖顶） | `true` / `true`（高度 `1.49`） |

**45° 斜向贴台角（`r05-068`）**：浮空岛 12×12，`+x/+z` 台角在 `(208.0, 258.0)`；玩家半宽 `0.3`，
探针水平内缩 `0.025`，所以停点中心可以**越过台角** `0.275`，箱体仍保留 `[0.025, 0.05)` 的重叠。
四种跑法（潜行 260 tick，`diag45_walk` 120 tick）结果：

| 场景 | 停点 `(x, z)` | 越沿量 | 与台面重叠 `(x, z)` | `feet_y` |
| --- | --- | --- | --- | --- |
| **45° 潜行（`diag45_sneak`）** | **`(208.271042, 258.274506)`** | `x +0.271`、`z +0.275` | `0.028958 / 0.025494` | `-40.0` |
| 单轴 X 潜行（`axis_x_sneak`） | `(208.272644, 252.5)` | `x +0.273` | `0.027356 / —` | `-40.0` |
| 单轴 Z 潜行（`axis_z_sneak`） | `(200.5, 258.272644)` | `z +0.273` | `— / 0.027356` | `-40.0` |
| 45° 不按潜行（`diag45_walk`） | `(217.300, 267.300)` | — | — | **`-60.0`（坠落）** |

`inset_limit = (208.275, 258.275)` 是"中心可达的上限"；斜向停点 `(208.271, 258.275)` 与单轴
`208.273 / 258.273` 都在其内侧一个 `0.05` 量子内，且两轴同时被夹取。三个停点的箱体重叠都
落在 `[0.025, 0.05)`，与 §4.2 的"`0.025` 内缩 + `0.05` 回退量子"模型一致；不按潜行键的斜向
对照直接坠落，证明夹取仍由潜行按键门控。

**低矮落差边界**（台面 `-40.0` 外侧铺支撑，潜行带按键走向 `edge_z = 247.0`，`block_shapes` 回读顶面高度）：

| 落差 | 支撑方块 | 停点 `z` | 终态 `feet_y` | 结果 |
| --- | --- | --- | --- | --- |
| `0.500` | `normal_stone_slab` | `254.5605` | `-40.500` | **放行（走下下层）** |
| `0.5625` | `campfire` | `254.5605` | `-40.5625` | **放行（走下下层）** |
| **`0.625`** | `daylight_detector` | **`247.273529`** | **`-40.0`** | **拦截（停在台沿）** |
| `1.000` | `stone`（低 1 格） | **`247.273529`** | **`-40.0`** | **拦截（停在台沿）** |

两项结果与静态 `MoveInput+0x00 bit0` 门控、水平 `0.025` 内缩（叠加 `0.05` 步长量子落于 `[0.025, 0.05)`）和 `1.01 × maxAutoStep = 0.568125` 下探完全闭环 `[代码]` `[实测]`。

---

## 5. 系统注册与同 tick 顺序

### 5.1 姿态/潜行组（`0x14681DA00`，按注册先后；vtable 已逐块复核）`[常量]`

| 序 | 系统 | 名字字面量 | vtable | slot[5] / slot[6] |
| --- | --- | --- | --- | --- |
| 1 | `TickServerMoveInputHandler`（`0x14681dcf4`） | `0x14EC07660` | `0x14EA34B10` | `0x148B459F0` / — |
| 2 | `PlayerBoundingBoxStateUpdateSystem` | `0x14EC3570C` | `0x14EA4F990` | `0x148FE9A20` / ret |
| 3 | `SneakTriggerIntentSystem` | `0x14EC33121` | `0x14EAC9B60` | `0x14B18E400` / ret |
| 4 | `SneakingSystem` | `0x14EC3544A` | `0x14EAC9C30` | `0x14B192C50` / `0x14B193600` |
| 5 | `JumpInputSystem` | `0x14EC32BCD` | — | — |
| 6 | `SneakingChangedEventSystem` | `0x14EC330D3` | **`0x14EAC9BF0`** | `0x14B191C90` / `0x14B192180` |
| 7 | `SneakTriggerActionSystem` | `0x14EC348C4` | **`0x14EAC9BB0`** | `0x14B190C50` / `0x14B190CF0` |
| 8 | `UpdateHorizontalPoseSystem` | `0x14EC3579B` | `0x14E83A6E0` | `0x1428B8980` / ret |

### 5.2 移动/碰撞组（`0x14681AC20`）`[常量]`

`MoveCollisionSystem` → `CollisionShapesCopySystem` → `Store nearby on move request` →
**`SneakMovementSystem`（`0x14681B057`）** → `ConfigureDepenetration` → `ActorMoveSystem` → …
⇒ 夹取发生在 `ActorMoveSystem` 之前，也就是**先改速度再位移** `[代码]` `[推断]`。

顶层 `0x1468230DA`（姿态组）早于 `0x14682655B`（碰撞组），与 [`stepping-12651.md`](stepping-12651.md) §1 同源 `[代码]`。

---

## 6. 与 Java 版对照（`1.21.4+ / 26.2`）

| 维度 | Java | 基岩 `1.26.51.01` |
| --- | --- | --- |
| 低顶姿态 | `Player#updatePlayerPose()` 同样按净空在 `STANDING 1.8 → CROUCHING 1.5 → SWIMMING 0.6` 间降级 | 同思路，三档自动拟合（`0x148FEA2E0`） |
| 碰撞测试盒 | **六面内缩 `1.0E-7`** | **六面各内缩 `0.01f`** ⇒ 门槛 `1.79 / 1.48 / 0.59` |
| 潜行高度 | `1.5` | **`1.49`** |
| 潜行/爬行移速 | `0.3 ×`；`Swift Sneak` `0.3 + 0.15 × 等级` | 同（实测 `0.45 / 0.60 / 0.75`），且对爬行与强制潜行生效 |
| 边缘防跌落 | `maybeBackOffFromEdge()` 的 `0.05` 是**回退步长量子**，**不做水平 AABB 内缩**；判 `isStayingOnGroundSurface()`（Shift 键） | 除 `0.05` 量子外，**水平四边再内缩 `0.025`**，保留约 `0.025` 重叠 |
| 爬行 / 强制潜行 + Shift | 同样防跌落（不按 Shift 均会掉） | 同：玩家只看潜行按键（`MoveInput+0x00 bit0`） |

Java 侧为机制对照，未在本机复测 `[推断]`。

---

## 7. 未闭合项

**无**（Round 4 清零；Round 5 补上 `SneakingComponent` 的静态消费点后仍无未闭合项）。原 5 项的去向：

| 原尾项 | 闭合位置 | 证据 |
| --- | --- | --- |
| 1. `0.6` 写进请求的最终写点 | §1.2 的六步链（尺寸组件 → 同步数据回调 `0x141A1C130` (`ActorDataIDs [38,53,54]`) → 请求写点 `0x1419F93A0`） | `[代码] [常量]` + `watch_set` 命中 `0x14289A9C5`（1.49）/ 回写 1.8 |
| 2. `ADF bit6` 枚举语义 | §4.1：`ActorFlags::Tempted`，门控在玩家/非玩家两路汇合之后 | SDK `ActorFlags.h` + `0x14B18BCA7` |
| 3. 对角复核逐分支 | §4.2 伪代码（X/Z/对角三段 + 收尾） | `[代码] [常量]` |
| 4. `0.05` 与 `1.01` 的交互 | §4.2：**对角段同样每轮 dx、dz 各减 0.05**，深度 `1.01×maxAutoStep` 与 `0.025` 内缩复用 | 同上 + 45° 实测停点 |
| 5. 首拍加速度 / `Swift Sneak` 空中控制 | §3.3 + `verify_sneaking_crawling_tail.py`（`r05-068`） | `[代码]` `[实测]` 硬断言 |

**保留的如实边界**：

- `0x141A1C130` 仅是 `ActorDataIDs [38,53,54]` 的同步数据属性转发；`ActorDataBoundingBoxComponent` 在姿态切换拍的上游写链（`0x142A3BBC0`，上游 `0x142A1DBA0 → 0x143DFAF20 → 0x14204C3A0 → 0x1401E8570`）无调试符号可读，标 `unresolved`，不猜。
- `ADF bit6` 的**设计意图**（为什么"被食物吸引"要跳过潜行夹取）属语义解释，标 `[推断]`；静态能证的只是门控位置与枚举名。

Round 4 曾把 `Swift Sneak` 的首拍 `0.30` 记成"客户端装备同步的 1~2 拍追赶"；Round 5 用不松键夹具与静态注册顺序证明这是**同 tick 调度序**（`SneakingSystem` 第 3 个先于 `SneakTriggerActionSystem` 第 6 个），已撤回，不再作为边界。

> **交叉引用（已于其他专题闭合，不计为本仓未闭合项）**：鞘翅（`gliding`）`0.6 × 0.6` 碰撞箱、以及 `UpdateBoundingBoxes`（注册点 `0x146820677`，与 `UpdateHorizontalPoseSystem 0x146820595` 同属姿态组 `0x14681DA00` 末尾，整组早于移动碰撞组 `0x14681AC20`）已在 [`../elytra-movement.md`](../elytra-movement.md) 闭合。

---

## 8. 复现

```bash
py -3.11 driver/dev.py run --no-build --world "超平坦测试" --background --allow allow_input_inject=1 --allow allow_mem_read=1
py -3.11 driver/dev.py exec "py -3.11 skills/bedrock-re/findings/1.26.51.01/cases/sneaking-crawling-12651/probes/verify_sneaking_crawling.py"
py -3.11 driver/dev.py exec "py -3.11 skills/bedrock-re/findings/1.26.51.01/cases/sneaking-crawling-12651/probes/verify_sneaking_crawling_tail.py"
```

- `verify_sneaking_crawling.py`：Round 1–3 主脚本（姿态尺寸、净空阶梯、移速、边缘、爬行机动）。
- `verify_sneaking_crawling_tail.py`：**Round 5 尾项**，覆盖首拍加速度（行走/潜行/强制潜行/爬行/潜行+SS3 同拍与先按住/爬行+SS3）、
  空中控制（潜行 0/SS I/II/III 的"起跳前按住"与"空中新按"两组）与 45° 贴角（含单轴对照与不按潜行的坠落对照）；
  预滚请求不松键、空中加速度按 `a_i = v[i+1] − 0.91·v[i]` 复算；浮空台与通道由分片 `fill` + 逐片回读确认，
  逐场景 `settle` + 硬断言。原始数据：`out/sneaking-crawling-tail-1.26.51.01.json`（`r05-068`）。

脚本内置硬断言（`r03-048` 全绿通过）：浮空台与每格净空/落差由 `block_shapes` 回读；每个场景先 `settle`；
姿态用 `AABBShapeComponent` 高度 + `entity.aabb.size` 双向确认；速度按「众数姿态 + 8 tick 滑动中位数」；
`Swift Sneak` 逐级读 `0xCDD62A2C` 确认等级；边缘、强制潜行与爬行/活板门/游泳入口全部有硬断言。原始数据：`out/sneaking-crawling-1.26.51.01.json`。

---

## 9. 勘误（v1 → v2 → v3）

| 原结论（v1 / v2） | 更正 | 依据 |
| --- | --- | --- |
| `0x148FE9B5A..0x148FE9B9F` 是「按实体在 1.8 / 1.49 间选姿态」（v1） | 是**按世界版本（`BaseGameVersionComponent`）选潜行高度参数**；逐实体三档判定在 `0x148FEA2E0` | `0x148FE9B35 cmp [r8+0x28], 0x75C382E5` |
| 三档净空判定「只有实测」（`live-only`），`0.6` 为 `unresolved`（v1） | 三档判定**静态闭合**：三个级联盒 + 六面 `0.01` 内缩 ⇒ `1.79 / 1.48 / 0.59` | `0x148FEA45C / 0x148FEA5B7 / 0x148FEA746`、`r03-048` |
| 防跌落门控「ADF bit1 与 bit114，不读潜行键」（v1） | **玩家读 `MoveInput+0x00 bit0`；非玩家才读 `ADF bit1`；从不读 bit114**；另有 `ADF bit6` 跳过整段 | `0x14B18BC11..0x14B18BCA1` |
| 强制潜行与手动潜行「防跌落完全一样」（v1） | 姿态/移速相同，但**不按潜行键就不执行夹取**（实测坠落至 `-60`） | `0x14B18BC11..0x14B18BC37`、`r03-048` |
| `SneakTriggerActionSystem` vtable `0x14EAC9BF0`；`SneakingChangedEventSystem` `0x14EB40930`（v1） | 前者 **`0x14EAC9BB0`**，后者 **`0x14EAC9BF0`**（v1 整体错位一格） | `0x1468204F7` / `0x146820467` |
| 游泳姿态 `sneaking=true`（Round 1/2 封顶水池） | `r03-048` 改**露天水池**实测：`sneaking=false`、`input.sneak=false`；旧值系 `y=-37` 封顶导致 `1.56 < 1.79` 强制潜行假象 | `r03-048` `pose_swim` |

---

## 10. 勘误（v3 → v4，Round 4）

| 原结论（v3） | 更正 | 依据 |
| --- | --- | --- |
| `0.6` 写回 `ShouldUpdateBoundingBoxRequestComponent` 的最终写点 `unresolved` | 表/组件驱动六步链：姿态参数 → `ActorDataBoundingBoxComponent (0x5ACD5A3E)` → 种类派发 `0x141A1C130` → 请求写点 `0x1419F93A0`（`0x1419F9536/0x1419F953A`）→ 消费 `0x14289AFB0`/`0x14289AEE0`/`0x14289A870` 写 `AABBShapeComponent +0x18/+0x1C`（`0x14289A9C0/0x14289A9C5`） | §1.2 + `watch_set` 命中 `0x14289A9C5`（1.49） |
| `ADF bit6`（`0x14B18BCA7`）枚举语义 `unresolved` | **`ActorFlags::Tempted`**（SDK `ActorFlags.h`：`Tempted=6`）；门控在玩家按键路径与非玩家 `ADF bit1` 路径汇合之后，玩家永不置位 | §4.1 |
| 对角复核只到"两轴都无支撑才一起归零"的行为级；`0.05` 是否作用于对角未展开 | 三段共用同一探针：对角段每轮 **`dx`、`dz` 各减一个 `0.05` 量子**（符号感知、不跨 0），水平仍内缩 `0.025`、下探仍是 `1.01 × maxAutoStep`；收尾清 `1.19e-07` 残量 | §4.2 |
| 首拍加速度与 `Swift Sneak` 空中控制未测 | 首拍 `1.0 / 0.3 / 0.3 / 0.3`（行走/潜行/强制潜行/爬行）无 1-tick 延迟；空中共享同一乘数。~~`Swift Sneak` 额外加成有 1~2 拍追赶~~（已被 §11 撤回：同 tick 调度序） | §3.3 + `verify_sneaking_crawling_tail.py` |

---

## 11. 勘误（v4 → v5，Round 5）

| 原结论（v4 / `r04-063` / `r04-064`） | 更正 | 依据 |
| --- | --- | --- |
| `Swift Sneak` 的额外倍率有**客户端装备同步的 2 拍追赶**；同拍按下、甚至先按住潜行 6 拍，首拍仍是 `0.300`，稳态才到 `0.738`（`r04-064`） | **同 tick 调度序**：`SneakingSystem`（`0x14B192C50`，组 `0x14681DA00` 第 3 个）先于 `SneakTriggerActionSystem`（`0x14B190C50`，第 6 个）执行，后者才写 `ADF bit1`。同拍 `W+Sneak` 首拍 `0.300`；**先按住潜行 6 拍**（`ADF bit1` 已置位）或**矮顶强制爬行**（`ADF bit114` 已置位）首拍直接 `0.750`（`0.073502`）。Round 4 的 `pre_sneak` 夹具在第 `N` 拍自动松键，所以 `sneak+ss3_held` 与 `sneak+ss3` 逐位相同 | §3.3 + §5.1 + `r05-068`（`sneak+ss3` `0.300038` vs `sneak+ss3_held` `0.750016`） |
| 空中迅捷潜行的"最大逐拍增量" `0.398 / 0.548 / 0.698`，用未扣空气阻力的 `v[i+1]−v[i]` 估计（`r04-064`） | 按 **`a_i = v[i+1] − 0.91·v[i]`（`a_0 = v[0]`，`0.91f` @`0x14E76D2D8`）** 复算，空中加速度精确为 **`0.450 / 0.600 / 0.750`**（`0.008820 / 0.011765 / 0.014694`，容差 `<0.02`）；"空中新按潜行"第一拍仍是 `0.300`，第二拍起升到目标档 | §3.3 + `r05-068` |
| 斜向停点"落在台沿 `−0.275` 的一个量子内"（`r04-063` / `r04-064`） | 停点中心**越过台沿约 `+0.27`**：`diag45_sneak (208.271042, 258.274506)`，单轴 `208.272644 / 258.272644`，台角 `(208, 258)`；`inset_limit = (208.275, 258.275)`，箱体重叠 `0.028958 / 0.025494` 与 `0.027356`，全部落在 `[0.025, 0.05)` | §4.3 + `r05-068` |
| `SneakingComponent` 的浮点乘数只有实测消费，未定位静态读取点（`r04-064` 的边界） | 静态消费点闭合：`0x144A26290` 读该组件的每实体浮点（缺省 `0.3f`），`0x144A2642C mulss xmm6, xmm0` 乘进移动输入标量；调用点 `0x144A281FF / 0x144A283F9 / 0x144A285DF` 在玩家每 tick 移动更新 `0x144A27D20` 内，结果写 `LocalMoveVelocityComponent (0x10257C61)`；helper 只按 `Sneaking`/`Swimming` 门控，无 on_ground 分支 ⇒ 地面与空中共享同一条乘数链 | §3.3 + `pe_map --ecs-hash 0x8918332C` 的 13 处命中（描述符/视图/事件分发之外唯一乘法点） |
| **独立复核补正**：称 `0x144A26290` 是唯一乘法消费点，且同拍 `W+Sneak` 滞后 1 拍（`r05-069`） | **主消费点实为 `TickServerMoveInputHandler 0x1404495e0`**（`0x14681dcf4` → `0x148B459F0` → `0x148B45510` → `0x148B2D870` → `0x1404495e0` 写 `MoveInputComponent +0x24/+0x28`，含 `MoveInput & 0x40001`、`ADF bit1`、`ADF bit114 (Crawling)`，无 `on_ground` 分支）；`0x144A26290` 只是次级消费点。输入缩放早于 `SneakingSystem` 又早于 `Action` ⇒ **同拍按 `W+Sneak` 共滞后 2 拍**（地面 `[0.0294, 0.0455, 0.0983]`、空中 `[0.005875, 0.005885, 0.014698]` 第 3 拍才切 `0.75`） | §3.3 + §5.1 + `r05-068` JSON 逐拍序列 |
| **独立复核补正**：`0x141A1C130` 跳表被描述为“无符号站/蹲/爬尺寸种类派发”（`r04-061`） | 解码 `0x14E7D0400` 跳表 128 项仅 **`[38, 53, 54]`**（SDK `ActorDataIDs::Reserved038/053/054`，即 Scale/Width/Height）落入 `0x141A1C175`，故 `0x141A1C130` 是同步属性回调，不是站/蹲/爬种类枚举；未符号化环节改回上游 `0x142A3BBC0` 写链 | §1.2 + `0x14E7D0400` 跳表全表解码 |
