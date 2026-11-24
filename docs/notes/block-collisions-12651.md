# 全游戏方块碰撞箱自动导出与 Wiki 校对（基岩版 1.26.51.01）

本文汇总基于 LeviPilot `dump_block_collisions` / `block_shapes` / `entities`（`Actor::getAABB()`）与离线 PE 静态反汇编对基岩版 `1.26.51.01` 全游戏方块及典型实体碰撞箱的自动导出、邻居/上下文实测与对照 [`MCBE_Parkour_wiki/Pages/blocks_collisions.md`](https://github.com/Acce5sDenied/MCBE_Parkour_wiki/blob/wiki-pages/Pages/blocks_collisions.md) 的逐项校对结果。

> **证据标记约定**：`[代码]` 指反编译指令/虚表分派、`[常量]` 指 `.rdata`/即时数 `float32` 常量、`[实测]` 指经由游戏服务端 `BlockSource` / `BlockType` / `Actor::getAABB()` 直接采样的运行期输出。完整采集/分析脚本见 [`../../skills/bedrock-re/findings/1.26.51.01/cases/block-collisions-12651/probes/collect_and_audit.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/block-collisions-12651/probes/collect_and_audit.py)，原始导出存于 gitignored `out/block-collisions-1.26.51.01/`。

---

## 一、全量导出概览与可复现口径

- `[实测]` 在 `超平坦测试` 服务端 `BlockSource` 上以基准点 `[0, 0, 0]` 遍历 `BlockTypeRegistry` 全部 **`1477`** 种 `BlockType`，以四种 `GetCollisionShapeInterface` 上下文分别导出（全部 **`faults = 0`**）：
  - `entity_mode="none"`（含空气）：**`22091`** 个 `Block` 状态，`921 ms`
  - `entity_mode="player"`（不含空气）：**`22090`** 个状态，`828 ms`
  - `entity_mode="leather_boots"`（穿皮革靴站立在方块上方，`isWearingLeatherBoots=true, canStandOnPowderSnow=true`）：**`22090`** 个状态，`859 ms`
  - `entity_mode="sneak_above"`（从上方按蹲穿越判定，`canDescendThroughBlock=true`）：**`22090`** 个状态，`906 ms`
- `[实测]` 在 `none` 全量导出中统计：
  - `1` 个空气类型（`minecraft:air`），`718` 个含完整 `1×1×1` 状态的类型，`252` 个含无碰撞（`EMPTY`）状态的类型，`537` 个含非整格有效碰撞状态的类型（注：如半砖、活板门、栅栏门、雪片等同一类型同时拥有不同/空状态，各分类非互斥）。
  - 去除完整整格与纯 `EMPTY` 后，全游戏非整格碰撞箱共有 **`233`** 种唯一几何签名（计入旋转、多层多盒 `addCollisionShapes` 与整体包络 `getCollisionShape`）。

---

## 二、对照 `MCBE_Parkour_wiki/Pages/blocks_collisions.md` 的勘误与过时项

下表仅列出 1.26.51.01 与当前 Wiki 表述存在实质差异、笔误、过时或需关键补充的条目：

| 条目 | Wiki 当前说法 | `1.26.51.01` 实测 / 静态结果 | 校对结论 |
| --- | --- | --- | --- |
| **Stairs(inner)** | 仅 2 盒（与 `Stairs(normal)` 相同），内角留有 `0.5×0.5×0.5` 虚空穿透区 | `[实测]` `minecraft:corner = inner_left / inner_right` 均返回 **3 盒**：底座 `1×0.5×1` + 主阶 `0.5×0.5×1` + **内角第三盒 `0.5×0.5×0.5`** | **Wiki 已过时**：1.26.51.01 内角楼梯已补齐第 3 盒，不存在虚空穿透区 |
| **End Portal Frame** | 仅 `1×1×0.8125`，“Adding eye does not change collision box”，选框 `Yes` | `[实测]` `end_portal_eye_bit=0` 为单盒 `[0,0,0]..[1,0.8125,1]`；`end_portal_eye_bit=1` 为 **2 盒复合**：框体 `[0,0,0]..[1,0.8125,1]` + 末影之眼 `[0.3125,0.8125,0.3125]..[0.6875,1.0,0.6875]`（`0.375×0.1875×0.375`），而 `outline` 仍为 `[0,0,0]..[1,0.8125,1]` | **Wiki 已过时**：放眼后新增顶盒，且此时选框**不含**末影之眼 |
| **Piston Head** (`piston_arm_collision`) | 2 盒（`Head: 1×1×0.25` + `Arm: 0.375×0.375×1`） | `[实测]` 朝上伸出态（`facing_direction=1`）`addCollisionShapes` 返回 **3 盒**：下段粗杆 `[0.3125,-0.25,0.3125]..[0.6875,0.25,0.6875]`（`0.375×0.5×0.375`）+ 上段细杆 `[0.375,0.25,0.375]..[0.625,0.75,0.625]`（`0.25×0.5×0.25`）+ 活塞盘 `[0,0.75,0]..[1,1,1]`（`1×0.25×1`） | **Wiki 简化不准确**：杆体分上下两段不同粗细（`0.375` 与 `0.25`），向下伸入活塞底座 `0.25` 格 |
| **Walls(2-opposite)** vs **Thin wall(2-opposite)** | 写“Needs another wall on top”（把是否上方有墙当成 `0.5` vs `0.375` 的条件） | `[实测][常量]` 2 对侧连接时决定宽度的是 `wall_post_bit`：`wall_post_bit=0` 为 **`0.375×1×1.5`**，`wall_post_bit=1`（如顶部插火把或有柱态）为 **`0.5×1×1.5`**；`wall_connection_type = tall`（上方接墙）仅把选框高度从 `0.8125` 抬高到 `0.9999`（`.text 0x146048d5c` 写入 `0x3f7ff972 = 0.9999f`），**不改变** `0.375` 宽度 | **Wiki 归因有误**：宽度切换条件是中心柱状态 `wall_post_bit`，不是“上方有没有墙” |
| **Chests** | `0.95×0.95×0.95`，注释写 `-0.25 on each side`；`Chests(1-side connected): 0.95×0.975` | `[实测][代码][常量]` 单箱为 `[0.025,0,0.025]..[0.975,0.95,0.975]`（每边缩进 **`0.025`**）。`ChestBlock` ctor（`0x148e0ab70`）向 `mVisualShape` 写入 `.rdata 0x14ea40130 = 0.025f/0.975f` 与 `0x3f79999a3f733334`（`0.95f/0.975f`），且 `ChestBlock::$vftable`（`0x14ea426f0`）未重写 `getCollisionShape`/`addCollisionShapes`/`addAABBs`/`getOutline`/`getVisualShape`，全二进制除该处外无第二个 `0.975f` | **Wiki `-0.25` 为小数点笔误**（应为 `-0.025`）；且 `1.26.51.01` `ChestBlock` 虚表无双箱碰撞重写（注：实机 `/setblock` 不自动配对双箱，`place_block` 未成功放箱，双箱 `BlockActor` 状态尚未现场实测） |
| **Composter** | 公式 `0.0625 + 0.125 * n`（无上下界说明） | `[实测]` `composter_fill_level = 0..8` 对应底板顶面为 `0.125, 0.1875, 0.3125, 0.4375, 0.5625, 0.6875, 0.8125, 0.9375, 0.9375`（`n=0` 实为 `0.125`；`n=1..7` 为 `0.0625 + 0.125*n`；`n=8` 满桶饱和在 `0.9375`） | **Wiki 公式缺 `n=0` 与 `n=8` 边界** |
| **Chorus Stem** (`chorus_plant`) | 写 `0.75×0.75×0.875` | `[实测]` 孤立悬空为 **`0.75×0.75×0.75`**（`[0.125,0.125,0.125]..[0.875,0.875,0.875]`）；当底部连接 `end_stone` 等可连方块时向下延至 `y=0`，变为 `0.75×0.875×0.75`；连四周/顶部后会对应贴边 | **Wiki 仅记述落地底接单态** |
| **Trapdoors & Doors**（`0.1825` 核查） | `Trapdoors: 1×1×0.1825`（选框 `Yes`），`Doors(half): 1×0.1825×1` | `[实测][代码][常量]` **`0.1825` 不是 Wiki 笔误！** `TrapDoorBlock::_getShape`（`0x1440320c0`）与 `DoorBlock::$getVisualShape`（`0x1497896c0`）均硬编码 `.rdata 0x14e8b333c = 0.1825f`（`0x3e3ae148`）与 `0x14e8b3338 = 0.8175f`（`0x3f5147ae`），并非 Java `3/16 = 0.1875`。另外 `TrapDoorBlock::$getOutline`（`0x144032570`）传 `r8b=1` 对非铰链面内缩 `.rdata 0x14e8425d8 = 0.0005f`（闭合选框为 `0.999×0.182×0.999`） | **厚度 `0.1825` 实锤成立**；仅 `Trapdoors` 选框应注 `No`（内缩 `0.0005`） |
| **Shelf Mushroom** | `big: 0.875×0.625×0.4375`, `small: 0.625×0.4375×0.4375`, `Yes*` | `[实测]` 碰撞上下界均为 `y:[0.25..0.6875]`；但选框**不按 `*` 规则延伸**——`growth=0`（小）选框为 `y:[0.5..0.6875]`（高 `0.1875`），`growth=1`（大）选框为 `y:[0.375..0.6875]`（高 `0.3125`） | 尺寸与上下界准确；选框高度小于碰撞箱 |
| **Trial Spawner**（历史表） | 记早期 `1.21` 为 `0.9998×0.9998×0.9998` | `[实测]` `1.26.51.01` 碰撞箱为完整 **`1×1×1`**（`[0,0,0]..[1,1,1]`），但选框 `outline` 仍保留 `[0.0001..0.9999]^3`（`0.9998^3`） | 碰撞已修复为整格，仅选框遗留 `-0.0001` 缩进 |
| **侧挂/横置状态与垂直位置补充** | 多处仅列底面正立态 | `[实测]` `Grindstone` 贴墙态为 `[0.125,0.125,0]..[0.875,0.875,1]`（或同族朝向）即 **`y:[0.125..0.875]`**；`Heads(wall)` 为 `y:[0.25..0.75]`；`End Rod`/`Lightning Rod` 横置高 `0.25`（`y:[0.375..0.625]`）；`Chains` 横置高 `0.1875`（`y:[0.40625..0.59375]`）；`Amethyst` 簇/芽贴墙时旋转长轴；`Conduit` 在方块内位于 **`y:[0..0.5]`** | 补充齐全方位碰撞高度范围 |

---

## 三、补全 Wiki 原 `[Todo]` / `?` 空白项

### 1. `Hopper`（漏斗，7 盒复合碰撞箱）

`[实测]` 注册表与实机 `block_shapes` 均确认 `minecraft:hopper` 的 `getCollisionShape` 包络为 `[0,0,0]..[1,1,1]`，实际碰撞由 `addCollisionShapes` 返回以下 **7 个 `AABB`**（漏斗体 6 盒固定 + 出口管 1 盒随 `facing_direction` 变化，`toggle_bit` 不影响形状）：

- **固定 6 盒**：
  1. 漏斗内底板：`[0, 0.625, 0] .. [1, 0.6875, 1]`（`1 × 0.0625 × 1`）
  2. 西壁（`-X`）：`[0, 0.6875, 0] .. [0.125, 1, 1]`（`0.125 × 0.3125 × 1`）
  3. 东壁（`+X`）：`[0.875, 0.6875, 0] .. [1, 1, 1]`（`0.125 × 0.3125 × 1`）
  4. 北壁（`-Z`）：`[0.125, 0.6875, 0] .. [0.875, 1, 0.125]`（`0.75 × 0.3125 × 0.125`）
  5. 南壁（`+Z`）：`[0.125, 0.6875, 0.875] .. [0.875, 1, 1]`（`0.75 × 0.3125 × 0.125`）
  6. 中段漏斗颈：`[0.25, 0.25, 0.25] .. [0.75, 0.625, 0.75]`（`0.5 × 0.375 × 0.5`）
- **第 7 盒（出口管 `0.25 × 0.25 × 0.25`，随朝向变化）**：
  - `facing_direction = 0`（下）：`[0.375, 0, 0.375] .. [0.625, 0.25, 0.625]`
  - `facing_direction = 2`（北 `-Z`）：`[0.375, 0.25, 0] .. [0.625, 0.5, 0.25]`
  - `facing_direction = 3`（南 `+Z`）：`[0.375, 0.25, 0.75] .. [0.625, 0.5, 1]`
  - `facing_direction = 4`（西 `-X`）：`[0, 0.25, 0.375] .. [0.25, 0.5, 0.625]`
  - `facing_direction = 5`（东 `+X`）：`[0.75, 0.25, 0.375] .. [1, 0.5, 0.625]`

### 2. `Shelf Mushroom` 反弹系数（`getBounciness` 全游戏审计）

`[代码][常量][实测]` 扫描 `.rdata` 全部 `262` 个 `BlockType` 虚表的 `slot[125]`（`BlockType::getBounciness`，默认 `0x140091a30` 返回 `0.0f`），全游戏仅存在 **4 个重写**，并与全量注册表导出 `bounciness` 字段完全吻合：

| 方块 | 虚表 `slot[125]` 实现 | 返回值 | 备注 |
| --- | --- | --- | --- |
| `minecraft:slime` | `0x1406db5f0`（读 `.rdata 0x14e5a7060`） | **`1.0`** | 完全弹性反弹基准 |
| `minecraft:bed` | `0x144adb980`（条件返回，非稻草床分支） | **`0.75`** | `[实测]` `minecraft:straw_bed` 返回 **`0.0`**（不反弹） |
| `minecraft:shelf_mushroom` | `0x142da3480`（读 `[rcx + 0x380]`） | **`0.75`** | **补全 Wiki `[Todo]`**：与普通床相同（`0.75`） |
| `minecraft:honey_block` | `0x1409276f0`（读 `.rdata 0x14e5e9578`） | **`-1.0`** | 特殊哨兵值（非实体反弹系数） |

### 3. 实测实体碰撞箱（`Happy Ghast`、`Boats`、`Shulker`）

`[实测]` 经由召唤实体并用 `entities` 读取 `Actor::getAABB()`（单精度 float32 坐标差值）：

| 实体 | Wiki 原值 | `1.26.51.01` 实测 `AABB` 尺寸 | 实测原始范围 / 说明 |
| --- | --- | --- | --- |
| **`minecraft:happy_ghast`** | `4×4×? [Todo]` | **`4.0 × 4.0 × 4.0`** | **补全 Wiki `? [Todo]`**：实测 `[x±2, y, z±2]` 到 `y+4`，高度精确为 **`4.0`** |
| **`minecraft:boat`** & **`chest_boat`** | `1.4×1.4×0.455` | **`1.4000015 × 0.4550018 × 1.4000015`** | 即 `1.4 × 1.4 × 0.455`（单精度舍入误差 `< 2e-6`） |
| **`minecraft:shulker`**（闭合） | `0.9998×0.9998×0.98` | **`0.9998016 × 0.9799995 × 0.9998016`** | 即 `0.9998 × 0.98 × 0.9998` |
| **`minecraft:shulker`**（侧开探头瞬间） | 横向展开 `~0.2060919` | **`1.2058945 × 0.9799995 × 0.9998016`** | 相对闭合态单向多出 `0.2060928`（即约 `0.20609`） |

---

## 四、Wiki 未收录的 `1.26.51.01` 非整格碰撞箱

在排除全整格与 Wiki 已有家族后，以下非整格方块尚未出现在 `Pages/blocks_collisions.md` 表格中（`[实测]`）：

| 方块 ID | 碰撞箱尺寸（W × D × H） | 本地坐标区间 | 选框是否一致 |
| --- | --- | --- | --- |
| `minecraft:sculk_sensor` | `1 × 1 × 0.5` | `[0, 0, 0] .. [1, 0.5, 1]` | 是 |
| `minecraft:calibrated_sculk_sensor` | `1 × 1 × 0.5` | `[0, 0, 0] .. [1, 0.5, 1]` | 是 |
| `minecraft:sculk_shrieker` | `1 × 1 × 0.5` | `[0, 0, 0] .. [1, 0.5, 1]` | 是 |
| `minecraft:end_portal` | `1 × 1 × 0.25` | `[0, 0, 0] .. [1, 0.25, 1]` | 是（实机待留意实际进入传送维度门触发，其静态 `getCollisionShape` 与 `getOutline` 均为 `0.25` 高） |
| `minecraft:pale_moss_carpet` | `1 × 1 × 0.0625`（下层） | `[0, 0, 0] .. [1, 0.0625, 1]` | 无侧蔓时一致；存在 `short`/`tall` 侧蔓时选框升至 `0.625`/`1.0`，碰撞仍仅底片 `0.0625`；`upper_block_bit=1` 无碰撞 |

---

## 五、专题全量审计汇总

### 1. 实体上下文敏感方块（全游戏仅此 2 种）

`[实测]` 在 `none`、`player`、`leather_boots`、`sneak_above` 四种上下文全量差分对比下，全游戏 1477 种方块中**只有 2 种**的碰撞箱依赖实体状态（合计 15 个 permutation 产生变化）：

| 方块 | `none` | `player` | `leather_boots` | `sneak_above` |
| --- | --- | --- | --- | --- |
| **`minecraft:powder_snow`** | `EMPTY` | `EMPTY` | **`1×1×1`**（`[0,0,0]..[1,1,1]`） | `EMPTY` |
| **`minecraft:scaffolding`**（全部 14 种 `stability`/`stability_check` 状态） | `EMPTY` | **`1×1×1`**（`[0,0,0]..[1,1,1]`） | **`1×1×1`**（`[0,0,0]..[1,1,1]`） | `EMPTY` |

### 2. 随机位置偏移方块（`randomlyModifyPosition`）

`[实测]` 共有 `57` 种方块 `randomlyModifyPosition(pos) != pos`（绝大多数为无碰撞的草/花/珊瑚/根），其中**实际具有有效碰撞箱**的仅有 **3 种**：

- `minecraft:bamboo`
- `minecraft:pointed_dripstone`
- `minecraft:sulfur_spike`

（与 Wiki `Pages/block_offsets.md` 的三项范围结论一致。）

### 3. 选框与碰撞箱不一致（`outline != collision`）

`[实测]` 在有碰撞的 537 种非整格方块及整格方块中，共发现 **`470`** 个 `BlockType` 的选框不完全等价于单盒碰撞箱（或与多盒各自不同），主要集中在以下家族：

- **高度/形态与碰撞不一致**：`*_wall`（`1.5` 碰撞 vs `0.8125/0.9999` 选框）、`*_fence` / `*_fence_gate` / `fence_gate`（`1.5` vs `1.0`）、`border_block`（`1.5` vs `1.0`）、`soul_sand` / `mud`（`0.875` vs `1.0`）、`snow_layer`（选框高为碰撞高 `+0.125`）、`big_dripleaf`（含茎与非整高选框）、`pitcher_crop`（`growth>=1` 选框向上包住植株，`y` 上界达 `0.875`/`1.0`）、`*_hanging_sign`（选框为完整牌面，碰撞仅顶梁 `0.125`）、`shelf_mushroom`（选框下沿缩于 `0.5`/`0.375`）、`pale_moss_carpet`（侧蔓态选框抬高）。
- **多盒复合碰撞**（`outline` 仅取整块或单个包络盒）：`*_stairs`、`cauldron`、`composter`、`brewing_stand`、`hopper`、`piston_arm_collision`、`end_portal_frame`（`eye=1`）。
- **整格碰撞但保留缩进选框**：`trial_spawner`（选框 `[0.0001..0.9999]^3`）、`*_carpet`（仅贴墙等特殊/边缘选框差异）与 `*_trapdoor`（选框非铰链侧内缩 `0.0005`）。

---

## 六、未覆盖与边界说明

1. **双箱实机配对状态**：因实机脚本中 `/setblock` 相邻放置不触发自动并箱、且 `place_block` 未成功把箱子放到测试位，双箱连接态（`Chests(1-side connected): 0.95×0.975`）未能完成实机邻居采样；目前仅静态核实 `ChestBlock::$vftable`（`0x14ea426f0`）未重写碰撞/选框虚函数，且全二进制无第二个 `0.975f` 常量。
2. **潜影盒开启态（`1.5` 高）**：静态注册表仅枚举静态状态（闭合态 `1×1×1`）；受 `BlockActor` 开盒动画进度控制动态升高的 `1.5` 状态未做实机开盒逐 tick 采样。
