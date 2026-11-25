# 基岩版移动与技术机制研究选题清单（1.26.51.01）

本文整理基于 LeviPilot 与静态逆向工具链的**后续研究方向与待闭合清单**，对照基准为：

- 本仓已完成研究与证据：见 [文档索引](README.md) 中的「逆向成果（按主题）」。
- 开源社区 Wiki：[`Acce5sDenied/MCBE_Parkour_wiki`](https://github.com/Acce5sDenied/MCBE_Parkour_wiki) 的 `wiki-pages` 分支（`Pages/` 共 29 个条目，其中 13 篇已成文、2 篇仅 `[Todo]` 占位、14 篇为 0 行空页）及尚未完全迁移的 `main` 分支 `README.md`。

> **状态边界**：本文列出的是**未研究、待核实或待回填的选题**；文中转述的社区经验值与猜想（如 `5e-5` 断疾跑阈值、`~0.34125` 飞行阻力、`~0.2060919` 潜影贝展开宽度等）均属**待验证对象**，不作为 `1.26.51.01` 已证结论引用。研究闭环方法见 [`reverse-engineering.md`](reverse-engineering.md) 与 [`../skills/bedrock-re/SKILL.md`](../skills/bedrock-re/SKILL.md)。

---

## 一、本仓已有成果：可直接回填 Wiki 或做轻量收尾

这一类课题在 `1.26.51.01` 中主体结论已经成型，投入很少就能补齐 Wiki 空白或完成最后实机确认：

| 课题 | Wiki 当前状态（`wiki-pages`） | 本仓现有进展与剩余工作 | 参考文档 |
| --- | --- | --- | --- |
| **鞘翅滑翔与烟花助推** | `Pages/` 尚未建立独立条目 | 每刻 6 步递推、创造 `+0.1`、缓降门控、碰撞箱、撞墙整数扣血与耐久已对质闭环，可直接迁为 Wiki 条目 | [`elytra-wiki.md`](elytra-wiki.md)、[`elytra-movement.md`](elytra-movement.md) |
| **正弦表与“半角”机制** | `README.md` 与 `Pages/general_movement.md` 仍称直接用三角函数、不存在半角 | 静态确认 `SIN_TABLE`（`65536` 项）与 5 处 Pattern A/B 加速及相机 `fmodf` 折回；**剩余收尾**：疾跑跳 / 长矛突刺 / 激流三叉戟 / 坐骑冲刺的逐 tick 位移拟合，以及自定义 `PlayerAuthInputPacket` `mRot` 实机回包验证 | [`angles-wiki.md`](angles-wiki.md)、[`notes/sin-table-12651.md`](notes/sin-table-12651.md) |
| **Fluid Blip（流体 Blip）** | `Pages/fluids.md` 仅标注 `[Todo]` 与 `Essentially identical to Java Edition 1.8.9 [Needs verification.]` | 已完成 `liquid-blip` 静态与逐 tick 实测调查，可对照基岩版判定与 Java 差异后回填 | [`fluid-movement.md`](fluid-movement.md)、[`notes/re-fluid-12651.md`](notes/re-fluid-12651.md) |
| **`main` → `wiki-pages` 纯迁移项** | `Pages/11_strafe.md`（0 行）、`Pages/sneaking_crawling.md`（0 行）为空 | 旧 `main` 分支已有 `11-strafe` 原理与潜行 `0.025` 边缘防跌落内容；可在迁移时顺带用 `1.26.51.01` 代码核实 `0.025` 精确判定框 | [wiki `main` README](https://github.com/Acce5sDenied/MCBE_Parkour_wiki/blob/main/README.md) |

---

## 二、`wiki-pages` 已建空壳、尚未逆向闭合的核心课题

`wiki-pages/Pages/` 中共有 16 个功能空壳（14 个 0 行文件 + 2 个仅 `[Todo]` 的 3 行文件）；扣除已明确归入第一类的 `11_strafe.md` 和非机制社区索引 `communities.md`，其余核心机制课题按研究价值与可工具化程度排序如下：

### 1. 望远镜加速（`Pages/spyglass_glitch.md`，0 行）

- **现状**：社区长期标为 `Super mysterious [Todo]`，缺少机制解释与复现条件。
- **研究切入点**：定位 `UsingItem` / `Blocking`（通用减速 `0.35^2 = 0.1225`）与望远镜使用状态在 ECS 中的写入、保持和清除顺序，用 `watch_set` 追踪速度属性与乘数改写源，确认 `1.26.51.01` 中该机制是否仍存活、触发窗口及加速倍率来源。

### 2. 上台阶与固体 Blip / Grind / Jump Cancel（`Pages/stepping.md`，0 行）→ 已结案

- **结论**：四系统在同一注册函数内按 `MoveCollisionSystem → ActorMoveSystem → AutoStepFilterSystem → AutoStepSystem → FinalizeMoveSystem` 登记；完整准入为 `maxAutoStep > 0` + 水平碰撞 +（`isFlying` / 落地拍 / `OnGroundFlagComponent` / `CanAlwaysAutoStepFlagComponent`）。上台阶试探前会临时剔除 `box.min.y >= mOriginalAABBShape.max.y` 的天花板盒，走 `Y↑step→X→Z→Y↓` 后比较 `dx²+dz²`，再对未过滤完整列表复核终点 AABB。步高实测 `0.5625`（`0.5`/`0.5625` 可平走、`0.625` 被挡，`0.2` 净空矮顶会拒绝上台阶），上台阶只改写 `MoveRequestComponent::mSpeed`、平移缓存碰撞盒并挂 `HasAutoSteppedComponent`，**不动 `mPosDelta`**；能走上的台阶不会触发 `5e-5` 断疾跑。**第 2 轮纠正**：贴台阶/天花板前缘起跳的 `+0.42` 会被 Step-Assist 整拍吃掉并直接放上台阶顶（保水平速度 `0.3274`）；把首次水平碰撞对齐到落地同拍后，基岩版存在保速固体 Blip（`vz` 与控制组一致、一步抬 `+0.4412`）；`10` tick 只是长按空格时 `MobJumpComponent::mNoJumpDelay` 的自动连跳冷却，松开即清零，点按可达 `3` tick 一跳、峰值 `0.6318`。第 1 轮 `r01-006` 的「无 Blip / 无 Jump Cancel / Grind 10 tick」已作为假阴性撤回。见 [`notes/stepping-12651.md`](notes/stepping-12651.md) 与 [`stepping-wiki.md`](stepping-wiki.md)。
- **未闭合**：可变步高三值（`0.5625/1.0625/0.5625`）选择分支的完整条件、求解器另一消费者 `0x1482d4710` 的所属系统名、`0.5625~0.625` 更细高度扫描、点按 `3` tick 极限的逐指令时序、台阶阶梯未能连锁 Jump Cancel。

### 3. 疾跑状态机、撞墙断疾跑与倒车疾跑（`Pages/sprint.md`，0 行）→ 已结案

- **结论**：四系统（SprintTimer / SetRequest / Intent / Action）注册与入口已定位；起跑要求前向 ±45° 扇区（`sqrt(x²+y²) ≥ 0.707`、`y>0`、`|x|≤0.707`、`mCanSprint` 允许），倒车/侧走/潜行立即取消；撞墙断疾跑由唯一 `5e-5f`（`0x14eac97e0`）与主轴差值判定，实测三区间：≤20° 单次断、23.5~24.4° 逐拍闪烁（即 `f65`，X 墙在 65.6° 镜像）、≥24.5° 稳定滑行；地面/空中均无 1-tick 延迟；疾跑跳 `+0.2` 的真实写入点在 `JumpFromGroundSystem`（`0x1495e9200`，`SIN_TABLE` → `mPosDelta.x/z`），实测 `0.19999~0.20000`。
- **未闭合**：5e-5 判定字段的逐字段绑定、Intent 条件全集（失明/骑乘/使用物品等）、`0.2f` 字段身份。见 [`notes/sprint-12651.md`](notes/sprint-12651.md) 与 [`sprint-wiki.md`](sprint-wiki.md)。

### 4. `float32` 精度与 Hitbox Manipulation / 微妙穿墙（`Pages/precision_glitches.md`，0 行）

- **现状**：社区已知“跨 $2^n$ 坐标会让碰撞箱缩涨、特定 Y 高度连续跳跃可改变垂直碰撞箱、卡墙会被 `0.1` 加速度挤出”，但缺乏源码级边界与可复现坐标段。
- **研究切入点**：检查 `AABB::move`（确认是增量平移 `min/max` 还是每拍由位置和尺寸重算 AABB）、`PushOutOfBlocks` 挤出判定与强度，配合 LeviPilot 逐 tick 回读真实 `float32` AABB，推导 ULP 漂移公式与触发阈值表。

### 5. 攀爬方块：梯子 / 藤蔓 / 脚手架 / 细雪 / 活板门（`Pages/climb_blocks.md`，3 行 `[Todo]` — **已闭合**）

- **现状**：**已闭合并产出双文档**（通俗指南 [`climb-blocks-wiki.md`](climb-blocks-wiki.md)、证据报告 [`notes/climb-blocks-12651.md`](notes/climb-blocks-12651.md)，可直接回填 Wiki）：全二进制 7 种 `Climbable|CanHaltWhenClimbing` 方块、`isOnLadder()` 单点判定 vs `BlockClimberSystem` AABB 足迹、梯子 `-0.20` b/t 被动下滑与零水平限速、`AutoClimbTravelFlagComponent` 跳过重力/阻尼、开活板门不可攀爬、脚手架无潜行免摔（`BlockClimberSystem` 清 `FallDistance`），以及细雪+皮革靴 `1.5×`（Space/潜行 `±0.225`、推墙 `+0.300`、光脚 `-0.1176`）。仅细雪 `1.5f` 写入源 VA 与高空跌落 `2.5f` 下沉阈值 VA 保留至下方 `cobweb_slowing_blocks` 专项。

### 6. 爬行与姿态尺寸切换（`Pages/sneaking_crawling.md`，0 行）

- **现状**：爬行（Crawling）在两个分支均完全空白；站立 / 潜行 / 爬行尺寸切换在 `wiki-pages/Pages/player_movement.md` 仅有占位表。
- **研究切入点**：查清触发爬行与自动站起时的头顶空间检查（精确高度留白与浮点边界）、`1.8 -> 1.49 -> 0.6` 转换链、爬行状态下是否仍保留潜行 `0.025` 防跌落边缘保护、`Swift Sneak` 附魔乘数，以及退出水体/活板门切爬行的 tick 时序。

### 7. 现行摇杆算法与三向斜走输入（`Pages/joystick.md`、`Pages/triple_component_strafe.md`、`Pages/camera_movement.md`）

- **现状**：`1.21.21` 修复 `11-strafe` 后的现行摇杆映射在 `Pages/joystick.md` 为 `[Todo]`；三向输入斜走异常 `Pages/triple_component_strafe.md` 为 `0` 行；`Pages/camera_movement.md` 的手柄摇杆（Stick）灵敏度公式为 `todo`。
- **研究切入点**：对照 `MoveInputHandler` / 摇杆归一化与死区裁剪代码，补齐现行全向摇杆数学模型、三键同按输入合成逻辑，以及手柄视角输入转换公式。

### 8. 速度上限、碰撞解算、Tap 实机校对与综述空页

- **`Pages/speed_limit.md`（0 行）→ 已结案**：`16 b/t` 上限存在，位于 `MoveCollisionSystem::fetchCollisionShapes`（`0x1431c2f50`）入口，对 `MoveRequestComponent.mSpeed`（`+0x3C/+0x40/+0x44`）做三维欧氏模长 ≤16 的等比缩限（`256.0f`/`16.0f`）；45° 斜向单轴 `11.3136` 验证球形范数；灵魂疾行 III+迅捷 255 实测封顶 `15.9998~16.0`，普通疾跑/迅捷 255（`14.65`）与蓝冰（`11.42/14.85`）都摸不到。见 [`notes/speed-limit-12651.md`](notes/speed-limit-12651.md) 与 [`speed-limit-wiki.md`](speed-limit-wiki.md)。
- **`Pages/collisions.md`（0 行）**：静态确认方块碰撞解析轴序（Y → X → Z 与历史差异）、`0.1` 卡墙挤出、实体碰撞推力及箱内判定。
- **`Pages/tapping.md`（0 行）**：`main` 分支的 Tap 起跳表标有 `[Needs verification.]`，适合直接用 LeviPilot 的 `Hardware HID` 按键注入 + 逐 tick 录制批量跑出实机基准表（walk / sprint / sneak / blocking / A7 tap）。
- **其余汇总类空页**：`Pages/movement_formulas.md`、`Pages/patched_glitches.md`、`Pages/strategies.md` 均为 0 行，待上述单项机制收齐后再做统一汇编。

---

## 三、散落在 `wiki-pages` 已成文页面中的零星 `[Todo]` 与存疑项

这类目标范围窄、验证路径明确，适合穿插清理或做工具化自动校对：

| 页面与位置 | 当前标记与待核实内容 | 建议验证方式 |
| --- | --- | --- |
| `Pages/fluids.md` L71 | **海豚的恩惠（Dolphin's Grace）**：当泳速乘数 `> 1.0` 时是否跳过 Depth Strider 的水平阻力插值（原标 `[Needs verification.]`） | **已闭合**：`SwimSpeedMultiplier=2.0` 跳过 `0.546` 插值并改用 $0.02(0.7+0.3L/3)\cdot 2.0$，忽略 `groundSpeed`/离地且仅在 `SWIMMING` 触发（见 [`notes/dolphins-grace-12651.md`](notes/dolphins-grace-12651.md)） |
| `Pages/bouncy-ness.md` L45 | **Shelf Mushroom（架状菌）** 反弹系数原标为 `[Todo]` | **已闭合**：`0x142da3480` 与实测均为 **`0.75`**（见 [`notes/block-collisions-12651.md`](notes/block-collisions-12651.md)） |
| `Pages/slime_honey_block.md` L32 | **蜂蜜块贴墙滑落（Side Sliding `[Todo]`）**：`-0.12` 垂直钳位盒距、`0.4` 水平乘数为什么叠加及顶面边缘（`honey-top-edge`）判定 | **已闭合**：`0.4`/`-0.12` 对组件每条目无条件执行，几何门只清零 `FallDistanceComponent` 与播效果（`mPos.y=feet+1.62`、水平 `>` 的 OR）；每块 `0.4^n`（干净滑行 `n=1/2` 实测 `0.3641/0.1449`）；起跳 `0.42→0.252` 由 `PreventsJumping` 位闭合；摔落距离 `×0.5` 由 `onFallOn` 闭合；蜂蜜与黏液顶面一致；创造飞行只免竖直钳位、`WasInWater` 完全排除——见 [`notes/honey-block-12651.md`](notes/honey-block-12651.md) 与 [`honey-block-wiki.md`](honey-block-wiki.md) |
| `Pages/cobweb_slowing_blocks.md` L22 | **蜘蛛网、细雪、甜浆果丛、冰冻与灵魂沙**：`0.9`/`1.5` 写入源、`fallHeight > 2.5f` 下陷、`0.998^3` 盒、创造免疫条件、冰冻与灵魂沙数值 | **已闭合**：[`notes/slowing-blocks-12651.md`](notes/slowing-blocks-12651.md)（静态+逐 tick 实测完整报告）与 [`slowing-blocks-wiki.md`](slowing-blocks-wiki.md)（攀爬部分见 [`notes/climb-blocks-12651.md`](notes/climb-blocks-12651.md)） |
| `Pages/constants.md` L31 | **创造飞行非加速水平阻力**：Wiki 仅给出经验近似值 `~0.34125` | 查飞行移动系统的摩擦分支，导出精确表达式与单精度常量 |
| `Pages/camera_movement.md` L25, L31 | **触屏与手柄摇杆灵敏度公式**：触屏为固定分辨率 `[WIP]`，手柄摇杆为 `todo` | 静态提取输入缩放常量与公式，补齐独立于单机分辨率的表达 |
| `Pages/blocks_collisions.md` L112, L127, L132 等 | **方块碰撞箱全表校对**：原缺 `Hopper [Todo]`、`Happy Ghasts` 高度 `? [Todo]`、`0.1825` 与历史项核实（仅剩双箱实机配对与潜影盒开盒态待补测） | **主表导出与静态复核已完成**：`1477` 类 / `22091` 状态全量导出与 Wiki 勘误见 [`notes/block-collisions-12651.md`](notes/block-collisions-12651.md) |

---

## 四、跳出当前 Wiki 目录的全新研究方向（零覆盖领域）

`MCBE_Parkour_wiki` 两个分支目前对以下玩法或底层机制几乎**零文档覆盖**，适合作为新开拓方向：

1. **风弹跳（Wind Charge）与伤害击退加速（Damage Boost / Knockback）**
   - 跑酷与速通高频机制：精确三维冲量公式、爆炸点距离与垂直角度依赖、连续风弹与跳跃/鞘翅/`16 b/t` 速度上限的结算顺序，以及受伤红闪无敌帧内的击退叠加规则。
2. **载具与实体推挤物理（冰船 / 活塞弹射 / 潜影盒与潜影贝 / 快乐恶魂）**
   - 基岩版冰船转弯角速度、冰面加速度、下船继承速度与卡块判定；活塞推动黏液块弹射玩家的真实作用力与时序；潜影盒开合瞬间推动、快乐恶魂靠近实体化碰撞与站立判定。
3. **服务端权威移动回滚（Server-Authoritative Movement / Rewind / Rubberbanding）**
   - 结合本仓现成的 `PacketTap` 与 `PlayerAuthInputPacket` 逆向能力，分析服务端移动校验的容差阈值、步数回溯与触发拉回（rubberbanding / desync）的精确边界条件。

---

## 五、快速选型建议与开工入口

### 按目标选型

| 如果你的主要目标是 | 优先推荐的课题 |
| --- | --- |
| **尽快给 `wiki-pages` 填缺纠错** | 先整理回填 [`elytra-wiki.md`](elytra-wiki.md)、[`angles-wiki.md`](angles-wiki.md)、[`dolphins-grace-wiki.md`](dolphins-grace-wiki.md)、已闭合的攀爬方块（[`climb-blocks-wiki.md`](climb-blocks-wiki.md) / [`notes/climb-blocks-12651.md`](notes/climb-blocks-12651.md)）与上台阶（[`stepping-wiki.md`](stepping-wiki.md) / [`notes/stepping-12651.md`](notes/stepping-12651.md)），并补全 `angles` 实机拟合 |
| **破一个悬案级冷门谜题** | **望远镜加速（`spyglass_glitch`）** 或 **`float32` 碰撞箱漂移（`precision_glitches`）** |
| **补强跑酷实战与物理还原** | 已结案的 **上台阶与固体 Blip（`stepping`）**、**撞墙断疾跑（`sprint`）** 可直接回填；下一步优先 **爬行与姿态切换（`sneaking_crawling`）** |
| **一次性产出全量精确数据资产** | 写自动化脚本调 `Block::getCollisionShape` **全量导出 1.26.51.01 方块碰撞箱**，顺带批量跑完 **`tapping`** 实机表 |
| **开拓全新高价值专题** | **风弹跳与击退加速**、**冰船物理** 或 **服务端移动回滚（Rewind）** |

### 开工标准流程

选定课题后，按 `bedrock-re` 账本与“静态优先 + 逐 tick 实测”流程立项：

```bash
# 1. 创建 case 账本（记录目标、已证事实与每轮 probe 输出）
py -3.11 driver/landing.py begin --case <slug> --goal "<要证明的机制与边界>"

# 2. 离线定位符号与函数（避免直接盲搜）
py -3.11 driver/pe_map.py --rtti <ClassName>
py -3.11 driver/pe_map.py --xrefs <va>
```

实验前置与注意事项遵循 [`../AGENTS.md`](../AGENTS.md) 与 [`reverse-engineering.md`](reverse-engineering.md)：逐 tick 实测前先用 `py -3.11 driver/dev.py focus` 确保游戏窗口处于前台，并区分 `StateVectorComponent+0x18`（待执行位移 `mPosDelta`）、`Actor::getVelocity()`（已完成位移）以及与脚部固定偏移 `1.6200101` 的位置基准口径。
