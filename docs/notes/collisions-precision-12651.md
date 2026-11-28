# float32 碰撞箱精度、卡墙挤出与方块/实体碰撞解算（1.26.51.01）

> 版本：`Minecraft.Windows.exe` `1.26.51.01`（LeviLamina `26.51.7`，`image_base = 0x140000000`）。
> 证据约定：`[代码]` 反编译/调用链；`[常量]` `.rdata` 浮点、组件 ID、结构布局；`[实测]` LeviPilot 逐 tick 录制与 ECS 原始字节回读；`[推断]` 由直接证据补出的推导。
> 复现脚本：[`verify_collisions_precision.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/collisions-precision-12651/probes/verify_collisions_precision.py)。原始数据：gitignored `out/collisions-precision-1.26.51.01.json`；本文全部 `[实测]` 数字逐位取自 **Round 4 最终 `exit: 0` 探针 `r04-049`** 写出的 JSON。旧轮（r01/r02/r03）中与本版冲突的结论已在账本以 `erratum_of` 撤回（§7）。
> 相位口径：录制 `vx/vy/vz` 是本拍已完成位移；watch `StateVector+0x18/+0x1C/+0x20` 是待执行位移 `mPosDelta`；`box_*` 是 `AABBShapeComponent` 的原始 `f32` 字段；`x/y/z` 与 `feet_y` 口径同 [`AGENTS.md`](../../AGENTS.md)。跑场基准：自然地表 `ground = -60.0`（草方块层 `layer = -61`）。

---

## 0. 结论摘要

1. **方块碰撞求解是「增量平移 + 双语义裁剪」**：`ActorMoveSystem`（`0x1490283A0`）把 `MoveRequest.mSpeed` 拆成 `[Y, X, Z]` 三轴增量，对每根轴增量遍历全部碰撞形状（倒序），逐形状调用 `@clip_collide`（`0x143327D80`）；解算后的 AABB **逐轴加增量写回 `AABBShapeComponent.mAABB`**，正常移动从不按 `mPos + (w, h)` 重算；`StateVector.mPos` 由最终 AABB 派生（`mPos.x/z = 盒中心`、`mPos.y = mHeightOffset + min.y`，`@finalize_move_worker 0x1463B39A0`）`[代码]`。
2. **`@clip_collide` 双语义**：两盒沿某轴不相交 → 单轴运动裁剪（贴面停止，不留 epsilon）；相交 → MTD 推出（三轴区间重叠深度取 argmin，X≤Y≤Z 同值优先，`push = dir·pen`，方向表 `{+1.0f, -1.0f}`）；死区 `1e-6f` `[代码]`。
3. **跨界增量平移漂移实测成立（修正 Round 1 的 binade 内假阴性）**：行走增量 `min += d; max += d` 在盒**跨骑 2^n 边界**（`ULP(max) = 2×ULP(min)`）时两侧各自舍入，宽度逐拍改变：
   - `x=1024` 边界：宽度在 `{0.5999755859375, 0.60003662109375, 0.60009765625}` 间跳变，8 个非零 `d_width`（±`6.103515625e-05` = ±1×ULP_below）；
   - `x=16384` 边界：`{0.5986328125, 0.599609375, 0.6005859375, 0.6015625}`，4 个非零漂移（±`9.765625e-04`）；
   - `x=1048576` 边界：**宽度在 `{0.375 … 0.875}` 8 个值之间摆动**（跨界段每拍 ±`0.0625`），往返走完后盒子停在 `[1048574.75, 1048575.125]`、**宽度 `0.375`**——增量平移把重建时的 `0.6` 永久走成了 `0.375` `[实测]`。binade 内行走（原点/同侧测量）宽度确实逐位守恒（`round(k+δ) = k+round(δ)`）——两类结果合并才是完整规律 `[实测]` `[推断]`。
4. **垂直 Hitbox Manipulation 实测成立（修正 Round 1 证伪）**：跨 `y=128` 边界的连续跳跃（平台顶面 `y=127`，盒 `[127.0, 128.8]` 跨骑 128）**每次落地高度单调 `+1 ULP`**：15 跳序列 `1.800018310546875 → 1.800445556640625`，步进恰为 `3.0517578125e-05 = ULP(128.x)`；`min.y` 每跳精确落回 `127.0`，`max.y` 在 128 区间网格上累积——「下沉-顶回」往返舍入在 `max.y` 侧每跳净 `+1 ULP`（不对称舍入 `[实测]`，偏置方向由坐标值决定 `[推断]`）。对照：跨 `y=64` 边界（平台顶面 `y=63`）15 跳高度恒 `1.8000030517578125` 单值（该往返恰好恒等）；天花板对照（`y=65` 石层、`mpd_y=+0.42` 起跳拍、头部夹至 `65.0`、整盒同步下压）证实**天花板碰撞是整体推出、不缩高度** `[实测]`。`y=101` 站立 120 拍高度实测 **`1.8000030517578125`**（`min_y=101.0`、`max_y=102.80000305175781` 单值）`[实测]`。
5. **卡墙挤出的「0.1 加速度」**：`@push_toward_closest_space`（`0x1431E2010`，系统名 `MoveTowardsClosestSpaceSystemFromActor`）方向 =（实体中心 − 周边碰撞盒质心）仅取 X/Z，±1 格自由空间试探失败则方向清零；**推挤幅度的欧氏模长实测峰值 `0.10000000461060017 = n̂ × 0.1f`**（Round 4 修正：Round 1 误把 L1 曼哈顿和 `0.14142136` 当模长），写入 `mPosDelta.x/z` 后按 `0.546` 摩擦链衰减 `[实测]`；仅 `|mPosDelta[axis]| < |push|` 时写、竖直不推 `[代码]`。深嵌（5×3×5 石腔）97 拍冻结、`mpd_x/z ≡ {0.0}` `[实测]`。
6. **实体推挤静态闭合（修正 Round 1 的 `unresolved`）**：`PushActorsSystem` 逐实体经 Actor 虚表 slot 144（`+0x480`）调 **`Mob::pushActors`（SDK `Mob.h:147`，本版本 `0x142086170`）**：0.2f 外扩收集候选、查 `PushableByEntityComponent (0xE0F57B82)`、逐候选 `0x1433DA1D0` 按 `SharedTypes::PushMode` 4 路分派（`Default=1 → 0x1433DB540`、`LegacyBoat=2 → 0x1433DB040`、`LegacyMinecart=3 → 0x1433DB2D0`、`Ball=4 → 0x1433DAF50`）；`Default` 分派器先按 `d_raw = max(|dx|,|dz|)` 检查双距离门 `[mMinDistance, mMaxDistance]`（后者减自身半宽），进 `0x1433DB540` 后取 `s = sqrt(d_raw)` 计算推力 `min(1, 1/s) × strength × (1 − pushThrough) × pushScale`，经 `0x1433DB650` **累加进双方 `StateVector.mPosDelta`**（船/矿车类型 88/83 走各自虚表路径）`[代码]`。实测（生存模式 + 非零偏移，修正 Round 1 同心短路夹具）：重叠时**牛被自推离玩家约 `1.0~1.26` 格直至分离**、**玩家（生存/创造）全程 `mPosDelta ≡ 0` 免疫**（写入门在玩家侧不放行，`[推断]`）；行走穿越牛时牛不动（`247.5` 精确 ×12 次轮询）`[实测]`。
7. **卡死状态机**：`mIsPenetrating` 驱动 `DepenetrationComponent.mBits` `bit1(WasPenetratingLastFrame) → bit2(IsStuckInCollider)` 进位；`ADF byte13 bit5`（`ActorFlags::PushTowardsClosestSpace = 109`，出生即置的能力位）同步 `mBits bit4`；`ADF109 && mIsPenetrating` 挂 `MoveTowardsClosestSpaceFlagComponent (0xE387CDF4)`；玩家 `mMinDepenetration = 0.01` `[代码]` `[实测]`。
8. **任务假设修正**：`PushableByBlockComponent (0x0DC5E610)` 是活塞/方块实体推挤路径，不是卡墙挤出 `[代码]`。
9. **管线顺序**：`MoveCollisionSystem`（`16 b/t` 缩限 + 形状收集）→ `ConfigureDepenetration` → `ActorMoveSystem` → `UpdateDepenetration` → `AutoStepFilterSystem` → `AutoStepSystem` → `ClientAcceptanceSystem` → `FinalizeMoveSystem`；`PushActorsSystem`（顶层 `0x14682791A` 注册）与 `MoveTowardsClosestSpaceSystemFromActor`（后段注册区 `0x14683982B`）在 pass 后段写 `mPosDelta`，下一拍生效 `[代码]`。

---

## 1. 方块碰撞求解管线（`[代码]`）

### 1.1 视图与逐实体解算 `@actor_move_solve 0x1490283a0`

`ActorMoveSystem` 注册于移动碰撞组（名字记录 `'ActorMoveSystem'` @`0x14ec3555a`，对象 0x50，vtable `0x14ea50160` slot[5]=`0x149028740`，视图 setup `0x149028150`），视图 = qword `0xBAC1B3CF_4C8F7288`（AABBShape 类型槽上的 MoveRequestComponent）+ `0x0B0822D1`（SubBBs）`[常量]`。逐实体包装：

1. 复制 `AABBShape.mAABB` → `MoveRequest.mOriginalAABBShape (+0x00)`；
2. 以 `mSpeed (+0x3C)` 构造增量数组 **`[(0, Y, 0), (X, 0, 0), (0, 0, Z)]`**；
3. 调 `@collision_axis_solver 0x1482a4890(out, deltas{3}, aabb, mCollisionShapes(+0x58), mMaxDepenetration(+0x48))`；
4. 解算盒增量写回 `AABBShape.mAABB`、`mSpeed ← 解算总增量`、`mPreCollisionSpeed(+0x30) ← 旧值`、`mIsPenetrating(+0x54) = 深度 ≥ 1e-6`；SubBBs 子盒平移同量；
5. NaN 校验串 `'1 ActorMoveSystem'` / `'2 ActorMoveSystem'` `[代码]`。

### 1.2 公共求解器与 `@clip_collide`

```
for delta in [Y, X, Z]:
    for shape in shapes (倒序):
        clip = @clip_collide(当前盒, shape, delta)
        work = (maxDep[axis] < clip.t) ? clip.pushMove : clip.pos
    盒 += work；深度 += clip.t
```

`@clip_collide 0x143327d80`：退化盒 early-return；每轴 `penA = self.max−block.min`、`penB = block.max−self.min` 经 `1e-6f` 死区 + `fmaxf(…,0)`；全重叠 → `t = fminf(三轴 min-pen)`、argmin（X≤Y≤Z 优先）、`pos[axis] = (push>0) ? max(delta,push) : min(delta,push)`；X 不相交 → 单轴运动裁剪 `pos[axis] = pushMove[axis] = dir*pen`（贴面）。方向表 `0x14E780AC0 = {+1.0f, -1.0f}`（pe_map 的 `f64=-0.0078125` 是 qword 注解假象）`[代码]`。

### 1.3 `FinalizeMoveSystem`

`mPos.x = (min.x+max.x)×0.5`、`mPos.z = (min.z+max.z)×0.5`、`mPos.y = mHeightOffset + min.y`；`"2 _move_ecs"` 校验；`0xD609B438` 水平碰撞双字节、`VerticalCollision (0xC6A02A9A)`、`OnGround (0xC29078A0)` 按 `|pre.y−speed.y| > FLT_EPSILON && pre.y < 0` 增删 `[代码]`。

---

## 2. float32 精度：量化、跨界漂移与垂直 Manipulation（`[实测]`）

### 2.1 重建量化（传送 / spawn）

传送落点处盒子由位置重建，min/max 各自落到 `ULP(|坐标|)` 网格：

| 中心 x | ULP | 实测宽度（理论 0.6） |
| --- | --- | --- |
| 242.5 | `1.53e-5` | `0.600006103515625` |
| 1024.5 | `1.22e-4` | `0.60009765625` |
| 16384.5 | `1.95e-3` | `0.6015625` |
| 131072.5 | `1.56e-2` | `0.59375` |
| 1048576.5 | `1.25e-1` | **`0.5`** |

`x ≈ 2^20` 处 `0.6` 不可表示（宽度只能是 `k×0.125`），实测收敛到 `0.5` `[实测]`。

### 2.2 跨 $2^n$ 边界的增量平移漂移

盒跨骑 `2^n` 边界时 `min` 与 `max` 处于不同 binade（`ULP(max) = 2×ULP(min)`），每拍 `min += d` 与 `max += d` 各自舍入，宽度逐拍改变。`r04-049` 的往返跨界行走（去程 12 拍 + 回程 12 拍，逐 tick 记录）：

| 边界 | straddle 拍数 | 非零 `d_width` | 宽度值集合 | 往返后宽度 |
| --- | --- | --- | --- | --- |
| `x=1024` | 6 | 8 次 ±`6.103515625e-05` | 3 个值（`0.5999755859375 … 0.60009765625`） | `0.60003662109375` |
| `x=16384` | 6 | 4 次 ±`9.765625e-04` | 4 个值（`0.5986328125 … 0.6015625`） | `0.6005859375` |
| `x=1048576` | 5 | 7 次（跨界段每拍 `±0.0625`，含一次重建 `−0.375`） | **8 个值（`0.375 … 0.875`）** | **`0.375`** |

首个非零漂移与 straddle 拍精确对应（如 `min_x=1023.455139, max_x=1024.055176 → d_width=+6.1e-5`）。`x=2^20` 往返结束后盒子 `[1048574.75, 1048575.125]`、宽度 **`0.375`**（较理论 `0.6` 缩 `0.225`）——**增量平移跨界行走会永久改变碰撞箱宽度** `[实测]`。机理：跨界段 `min` 用下方 binade 的 `ULP` 舍入、`max` 用上方 binade 的 `2×ULP` 舍入，两侧进位不同步 `[推断]`；同 binade 内 `round(k+δ) = k+round(δ)` 两侧同步，宽度守恒（本轮复现：原点 31 拍 `d_width/d_height` 全 0）——两类结果合并才是完整规律。

### 2.3 垂直方向：跨 $2^n$ 跳跃的 Hitbox Manipulation

平台顶面 `y=127`（方块 `y=126`），盒 `[127.0, 128.8]` 跨骑 128，连续 15 跳：

```
落地高度序列：1.800018310546875 → 1.800048828125 → … → 1.800445556640625
每跳步进 = +3.0517578125e-05 = ULP(128.x)，单调递增，无一回退
min_y 每跳精确落回 127.0；max_y 在 128 区间网格上逐跳 +1 ULP
```

**跨 `y=128` 边界的连跳会单调放大垂直碰撞箱**——社区的「特定 Y 高度连跳改变垂直碰撞箱」在本版本实测成立（`[实测]`）。机理：落地的「下沉-顶回」往返 `fl(fl(max − a) + a)` 在 `max.y` 位于更粗 binade 时每跳净 `+1 ULP`（`[推断]`，偏置方向由具体坐标值决定）。对照：

- 跨 `y=64` 边界（平台顶面 `y=63`，盒 `[63.0, 64.8]`，straddle 120 拍）15 跳高度**恒 `1.8000030517578125` 单值**——该往返恰为算术恒等（同 Round 1 原点测量）；
- 天花板对照（`y=65` 石层，`mpd_y=+0.42` 起跳拍 6 次）：头部精确夹至 **`65.0`**、`min_y` 同步压到 `63.0/63.12/63.2`——**天花板碰撞是整盒推出，高度不变** `[实测]`；
- `y=101` 站立 120 拍：`min_y=101.0`、`max_y=102.80000305175781`、高度 **`1.8000030517578125`** 单值（修正 Round 1 文档误引 `1.7999992`——那是原点 `y=−60` 的值）`[实测]`。

---

## 3. 卡墙挤出（Depenetration 链）

### 3.1 状态机（`[代码]` + `[实测]`）

每拍 `@configure_depenetration 0x149027730` 写 `mMaxDepenetration`（`MoveRequest+0x48`）：`IsStuckItem(bit3)` 或（one-way 空且 `bits∩0x15=0`）→ 基表 `{1,1,1}` @`0x14E6D7D88`，否则 `{0,0,0}` @`0x14E6D8608`；逐轴 `fmaxf(表, mMinDepenetration)`（玩家实测 `0.01`），`mTemporaryOverride` engaged（+52）再 `fmaxf(+40/44/48)`。`@update_depenetration 0x14901C270`：`ADF byte13 bit5`（`ActorFlags::PushTowardsClosestSpace = 109`）↔ `mBits bit4`；`mIsPenetrating` 驱动 `bit1 → bit2` 进位；每拍清 override；`ADF109 && mIsPenetrating` 确保 `MoveTowardsClosestSpaceFlagComponent (0xE387CDF4)` 存在。实测 `mBits`：站立 `16`、卡死 `22`；`ADF byte13` 全程 `0x20`（能力位，watch `mode=w` 200 次写命中分布于共享写尾）`[代码]` `[实测]`。

### 3.2 `@push_toward_closest_space 0x1431e2010`（0.1 推挤）

视图（`0x1431E0950`）：`{ActorDataFlag 0xC67426F3, AABBShape 0xBAC1B3CF, Depenetration 627786230, StateVector 0x1B5D5238, ActorMovementTickNeeded 0x94154CCE, MoveTowardsClosestSpaceFlag 0xE387CDF4, Passenger 0x98E40C0E}`。算法：实体盒 x/z ±1 扩张收集方块盒、剔除 `mOneWayPhysicsBlocks`；方向 =（实体中心 − 剩余盒质心）仅 X/Z（死区 FLT_EPSILON），±1 步自由空间试探失败则清零；`|d| ≥ eps` 时 `n̂ = d/|d|`、**`push = n̂ × 0.1f`**；仅 `|mPosDelta[axis]| < |push|` 时写 `StateVector.mPosDelta.x(+0x18)/z(+0x20) = clamp(old+push, ±|push|)` `[代码]`。

实测（S2 浅嵌：`x=236` 单列石墙封入玩家，录制窗覆盖 fill 拍）：fill 后第 4 拍起 `mPosDelta.x/z` **逐拍精确 `0.07071068`（= 0.1/√2）**，欧氏模长峰值 **`0.10000000461060017 = n̂ × 0.1f`**（Round 4 修正：Round 1 误用 L1 曼哈顿和 `0.14142136` 充当模长），随后 `× 0.546` 摩擦链衰减；最终盒子 `[237.13, 236.13]` 完全离墙 `[实测]`。深嵌（5×3×5 石腔，出面 1.8~2.8 格）：**97 拍逐位冻结**（`min_y ≡ −60.0`、`mpd_x/z ≡ {0.0}`），`mBits = 22` `[实测]`。

`PushableByBlockComponent (0x0DC5E610)` = 活塞/方块实体推挤（`0x1433474F0` archetype、`0x144AE3E00` 方向推），不是卡墙挤出 `[代码]`。

---

## 4. 实体推挤（PushActorsSystem）

### 4.1 静态链（`[代码]`）

1. **注册**：顶层 `0x14682791A`（对象 0x60，vtable `0x14EA06600` slot[5]=`0x147CBE4D0`，setup `0x147CBE1D0`），视图 `{ActorOwner 0x85B93800, ActorMovementTickNeeded 0x94154CCE, MobFlag 0x14EA24A2, PushActorsRequest 0x124F2E4B}`；主体 `0x147CB3020` 三路 join，tick 末清请求池。
2. **逐实体**：经 Actor 虚表 **slot 144（`+0x480`）= `Mob::pushActors`**（SDK `Mob.h:147` 声明；本版本 `0x142086170`）。
3. **`Mob::pushActors 0x142086170`**：`Actor::getAABB` 各向 **±0.2f 外扩** → 区块 actor 收集（vtable +248 调用）→ 查自身 `PushableByEntityComponent (0xE0F57B82)` 与骑乘链（`0x1419F2FC0` 根载具 walker，深 ≤ 0x3FF）→ 逐候选（跳过乘客、自身与已处理者）调 **`0x1433DA1D0(候选, 自身, canPush)`**。
4. **`0x1433DA1D0` 分派器**：入口 `skipPush`（`0x1433DA7C0`：骑乘双方、`vtable+0x9E8` 门等）→ 取候选的 `PushableByEntityComponent` → `0x1433DAB60` 选 preset（`+0x40` 模式字节非 0 才继续）→ `0x1433DAD20` → **`d = (mode==Default) ? max(|dx|,|dz|) : sqrt(dx²+dz²)`**（dx/dz = 双方位置差）→ `d < preset.mMinDistance(+0x48)` 短路（**同心重叠 d=0 即命中此分支——Round 1 夹具假阴性的根源**）→ `d −= 0.5×自身宽（AABBShape+0x18）`、`d > mMaxDistance(+0x4C)` 短路 → `preset+0x50 == 1` 时再过 `0x1433281F0` 重叠测试 → 按 `mode−1` 跳表分派 4 路（`Default=1 → 0x1433DB540`、`LegacyBoat=2 → 0x1433DB040`、`LegacyMinecart=3 → 0x1433DB2D0`、`Ball=4 → 0x1433DAF50`）。
5. **`Default 0x1433DB540`**：`s = sqrt(max(|dx|,|dz|))`、`inv = 1/s`、`scale = min(1.0f, inv) × preset.mStrengthMultiplier(+0x44) × (1 − 对方 pushThrough[actor+0x194])`；两个方向量 `∓(dx/s · scale)` 分别乘 `preset+0x54`（作用于候选）与 `preset+0x58`（作用于自身，仅 `canPush == 0` 时计算——对方不可推时**自身自推离**）。
6. **写入**：`0x1433DB650` 把向量**累加进 `StateVector.mPosDelta`**（`Actor+0x218` 指针的字段 6/7/8 = `+0x18/+0x1C/+0x20`）；类型 88/83（船/矿车）且非乘客走各自虚表 slot 138 路径；随后 `PushedByComponent (0xBE9C0F2C)` 经 `0x14253A5C0` 挂到候选；`preset+0x6C == 1`（播放音效）时按 `+0x70` 冷却与 `+0x78` 冲量阈值播声（`0x143E03960`）并更新 `mLastPlayedSoundAtTick`。运行时 preset 字段与 `SharedTypes::v1_26_40::PushableByEntityPreset` 定义对齐（`mPushMode/mStrengthMultiplier/mMinDistance/mMaxDistance/mPushScaleSelf/mPushScaleOther/mPlaySound/…`）`[代码]` `[常量]`。

### 4.2 实测对质（生存模式 + 非零偏移，`[实测]`；修正 Round 1 同心短路夹具）

| 夹具 | 设置 | 结果 |
| --- | --- | --- |
| A 生存静止重叠 | 玩家 `(238.65, 240.5)`，牛 `(238.5, 240.5)`，dx=0.15，slowness V | **玩家 `mPosDelta ≡ 0`、位移 0**；牛被自推 `238.5 → 237.49`（−1.01，分离后停推，仅剩 slowness 微漂移） |
| B 生存行走穿越 | 玩家从 244.52 行走至 250.67，穿越牛位 247.5 | 玩家自由穿越；**牛全程 `247.5` 精确不动**（12 次轮询；后 3 次轮询 `None`——牛在窗口尾部消失，如实边界） |
| C 创造对照 | 同 A 几何、创造模式 | 玩家 `mPosDelta ≡ 0`；牛自推 `238.5 → 237.24`（−1.26）——**创造/生存无差异** |
| D 双牛重叠 | dx=0.3，双牛 slowness V | 首个 2-tick 采样间隔内 gap `0.3 → 1.56`（每牛峰值约 `0.6/tick`），此后 gap 稳定 `1.57±0.01`（仅 AI 微漂） |

**结论**：推挤只在**牛（movement-tick 实体）一侧**落地——玩家（生存/创造）作为候选时向量不落到自身（写入门在玩家侧不放行，`[推断]`），牛则以「自推离」形式分离（`canPush == 0` 的 `vec2/mPushScaleSelf` 路径，方向 = 离开玩家）；行走穿越的瞬时重叠不触发（与静止重叠的差异机制 `[推断]`：候选收集/移动 tick 相位）。`D` 的初始分离峰值与 `min(1, 1/d) × strength × pushScale` 的量级一致（strength×scale 乘积未逐项定标）。

---

## 5. 管线顺序

`MoveCollisionSystem`（`fetchCollisionShapes 0x1431c2f50`：`16 b/t` 缩限 + 形状收集）→ `ConfigureDepenetration` → `ActorMoveSystem` → `UpdateDepenetration` → `AutoStepFilterSystem` → `AutoStepSystem` → `ClientAcceptanceSystem` → `FinalizeMoveSystem`（AABB → mPos 派生 + 落地标志）→ `PushActorsSystem` / `MoveTowardsClosestSpaceSystemFromActor`（后段写 `mPosDelta`，下一拍生效）`[代码]`。

---

## 6. 未决边界

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 玩家侧免推的精确门 | `[推断]` | `0x1433DB650` 写入前的 `sub_1419F1ED0(…, 256)` 与 Actor 虚表 slot 39 门在玩家侧不放行（A/B/C 三夹具一致），具体虚函数语义未逐条命名 |
| 行走穿越 vs 静止重叠的触发差异 | `[推断]` | B（穿越）牛不动、A（静止重叠）牛自推——候选收集相位/移动 tick 门控未逐条闭合 |
| `strength × pushScale` 乘积定标 | 未测 | 原版 cow preset 的 `mStrengthMultiplier/mPushScaleSelf/mPushScaleOther` 具体数值未从存档组件导出，只实测了乘积量级（分离峰值 ≈0.6/tick/牛） |
| `ADF bit109` 出生写入点 | `unresolved` | 共享标志写尾 200 次命中未逐一归因；语义（能力位、未卡时也置位）已定 |
| B 牛窗口尾部消失 | 如实边界 | 3 次轮询 `entity_not_found`（消失/超半径未归因），不影响冻结段数据 |
| 潜行/骑乘/多实体叠加对照 | 未测 | 静态上骑乘双方 skipPush 短路（`0x1433DA7C0`），潜行未实测 |

---

## 7. 勘误表（Round 4）

| 旧结论 | 处置 | 依据 |
| --- | --- | --- |
| r01/r02「binade 内行走宽度守恒 → 行走零漂移」 | **限定范围**：仅同 binade 内守恒；跨 `2^n` 边界行走宽度逐拍改变（`2^20` 往返后 `0.375`） | `r04-049` S3 |
| r01/r02「连跳改垂直碰撞箱在 vanilla 高度内证伪」 | **撤回**：跨 `y=128` 边界 15 连跳高度单调 `+1 ULP/跳`；`y=64` 边界恰好恒等 | `r04-049` S5 |
| r01/r02「`y=101` 站立高度 `1.7999992`」 | **更正**：`y=101` 实测 `1.8000030517578125`（`1.7999992` 是原点 `y=−60` 的值） | `r04-049` S5 |
| r01「推挤模长峰值 `0.14142136 = 0.1×√2`」 | **更正**：那是 L1 曼哈顿和；欧氏模长峰值 `0.10000000461060017 = n̂ × 0.1f` | `r04-049` S2 |
| r01/r02「本地玩家既不被推也不推人（创造模式夹具）」 | **修正夹具后现象成立但机理更新**：生存模式同样免疫（非同心偏移、`skipPush`/写入门），且静止重叠时**牛自推离**（Round 1 同心夹具命中的是 `min_distance` 短路） | `r04-049` S4 |
| r01「实体推挤精确公式 `unresolved`」 | **闭合**：`Mob::pushActors` → `PushMode` 4 路 → `Default` 公式 → 写入 `mPosDelta` | §4.1 |
| r02/r03 对 `r01-010`/`r01-016` 的重复 confirmed | 以 `erratum_of` 指向本轮 `r04-049` 勘误条目 | 账本 |
