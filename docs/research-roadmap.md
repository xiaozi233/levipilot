# 基岩版移动与技术机制研究选题清单（1.26.51.01）

本文整理基于 LeviPilot 与静态逆向工具链的**待闭合课题、轻量收尾与已结案总览**，对照基准为：

- 本仓已完成研究与证据：见 [文档索引](README.md) 中的「逆向成果（按主题）」。
- 开源社区 Wiki：[`Acce5sDenied/MCBE_Parkour_wiki`](https://github.com/Acce5sDenied/MCBE_Parkour_wiki) 的 `wiki-pages` 分支与尚未完全迁移的 `main` 分支 `README.md`。

> **状态边界**：第一、二节列出的是**待核实、待闭合或零覆盖方向**；文中转述的社区经验值（如摇杆死区与灵敏度经验值等）均属待验证对象，不作为 `1.26.51.01` 已证结论引用。研究闭环方法见 [`reverse-engineering.md`](reverse-engineering.md) 与 [`../skills/bedrock-re/SKILL.md`](../skills/bedrock-re/SKILL.md)。

---

## 一、当前待研究的核心课题（尚未闭合）

### 1. Tap 起跳实机基准表与汇总空页（`Pages/tapping.md` 等）

- **`Pages/tapping.md`（0 行）**：`main` 分支的 Tap 起跳表标有 `[Needs verification.]`，适合直接用 LeviPilot 的 `Hardware HID` 按键注入 + 逐 tick 录制批量产出实机基准表（walk / sprint / sneak / blocking / A7 tap）。
- **汇总类空页**：`Pages/movement_formulas.md`、`Pages/patched_glitches.md`、`Pages/strategies.md` 均为 0 行，待上述单项机制收齐后再做统一汇编。

### 2. 跳出当前 Wiki 目录的全新研究方向（零覆盖领域）

`MCBE_Parkour_wiki` 两个分支目前对以下机制几乎**零文档覆盖**，适合作为新开拓方向：

1. **风弹跳（Wind Charge）与伤害击退加速（Damage Boost / Knockback）**：精确三维冲量公式、距离与仰角依赖、连续风弹与跳跃/鞘翅/`16 b/t` 速度钳位的结算顺序，以及无敌帧内击退叠加规则（起手线索：`BreezeWindChargeProjectile`、`WindBurstOnHitSubcomponentDefinition`）。
2. **载具与实体推挤物理（冰船 / 活塞弹射 / 潜影盒与潜影贝 / 快乐恶魂）**：冰船角速度与冰面加速度、下船继承速度、活塞推动黏液块弹射玩家的时序、潜影盒开合推动与快乐恶魂碰撞站立判定。
3. **服务端权威移动回滚（Server-Authoritative Movement / Rewind / Rubberbanding）**：结合本仓 `PacketTap` 与 `PlayerAuthInputPacket` 能力，分析服务端移动校验容差、步数回溯与触发拉回（desync）边界。

---

## 二、已结案专题的轻量收尾尾巴

主体结论均已闭环，仅剩明确范围的局部尾巴：

| 专题 | 剩余收尾项 | 正主文档 |
| --- | --- | --- |
| 正弦表与半角 | 长矛突刺 / 激流三叉戟 / 坐骑冲刺的逐 tick 位移拟合，以及自定义 `PlayerAuthInputPacket` `mRot` 实机回包验证（疾跑跳 `+0.2` 已另于 `sprint-12651` 闭合） | [`notes/sin-table-12651.md`](notes/sin-table-12651.md)、[`angles-wiki.md`](angles-wiki.md) |
| 方块碰撞箱 | 双箱（Double Chest）实机配对与潜影盒（Shulker Box）开盒动态 `1.5` 态采样 | [`notes/block-collisions-12651.md`](notes/block-collisions-12651.md) |
| 创造飞行 | `a = flySpeed × 0.98f` 乘法指令与 `0.375f` 在横向速度公式内的消费点静态定案 | [`notes/creative-flying-12651.md`](notes/creative-flying-12651.md) |
| 上台阶与固体 Blip | `mControlledValue = 1.0625` 骑乘实测、鞘翅/水中游泳/载具乘客对照，以及 `ClientAcceptanceSystem` 形参映射定案 | [`notes/stepping-12651.md`](notes/stepping-12651.md) |
| 移动输入与视角灵敏度 | **触屏虚拟摇杆前端**（像素拖动→`mAnalogMoveVector` 的死区/半径/中心/疾跑区）、D-pad 角键命中区、鼠标事件生产者（`/renderWidth` 除法指令；已实测事件进入 handler 链时已是 `dx/W`）、slot 14 的 `edx` 语义与 `0x1404BFDED` 分支、`hotbar_only_touch` 存档映射、`touch_invert_y_axis` 消费点 | [`notes/touch-input-turn-12651.md`](notes/touch-input-turn-12651.md)、[`notes/input-joystick-strafe-12651.md`](notes/input-joystick-strafe-12651.md) |
| 使用物品与望远镜加速 | 刷子实机使用窗口与倍率、山羊角/弩提前结束使用的静态原因、盾牌格挡 ECS 判定、`ItemStack vtable[9]`/`VanillaClientGameplay` 之外与 FOV/载具/飞行/多人边界 | [`notes/spyglass-item-use-12651.md`](notes/spyglass-item-use-12651.md) |

---

## 三、已闭合成果总览（可直接回填 `MCBE_Parkour_wiki`）

以下课题在 `1.26.51.01` 中主体机制与逐 tick 实测均已闭环，详细地址、反汇编与如实边界以各正主文档为准：

| 课题 | Wiki 对应页 | 核心结论摘要 | 通俗指南 / 证据报告 |
| --- | --- | --- | --- |
| **流体与 Fluid Blip** | `Pages/fluids.md` | 水/岩浆竖直与水平递推、流动推力、入水深度、`liquid-blip` 与 Java 差异（含撤回项校验） | [`fluid-wiki.md`](fluid-wiki.md) / [`fluid-movement.md`](fluid-movement.md) |
| **海豚的恩惠** | `Pages/fluids.md` L71 | 仅 `SWIMMING` 生效、`±5.0` 盒 60 tick 扫描、`SwimSpeedMultiplier=2.0` 跳过 `0.546` 阻力插值并改用 $0.02(0.7+0.3L/3)\cdot 2.0$ | [`dolphins-grace-wiki.md`](dolphins-grace-wiki.md) / [`notes/dolphins-grace-12651.md`](notes/dolphins-grace-12651.md) |
| **鞘翅滑翔与烟花助推** | 需新建条目 | 每刻 6 步递推、创造 `+0.1` 微升、缓降门控、`0.6×0.6` 碰撞箱、撞墙整数扣血与耐久 | [`elytra-wiki.md`](elytra-wiki.md) / [`elytra-movement.md`](elytra-movement.md) |
| **正弦表与半角可达性** | `README.md` / `general_movement.md` | 65536 项 `SIN_TABLE`、5 处 Pattern A/B 加速筛选、相机 `fmodf` 折回锁死与大小半角极值 | [`angles-wiki.md`](angles-wiki.md) / [`notes/sin-table-12651.md`](notes/sin-table-12651.md) |
| **全游戏方块碰撞箱** | `Pages/blocks_collisions.md` / `bouncy-ness.md` | `1477` 类 / `22091` 状态导出；`Hopper` 7 盒、`Happy Ghast` `4³`、`Shelf Mushroom` 反弹 `0.75`、`Trapdoor`/`Door` `0.1825` 与旧表勘误 | [`notes/block-collisions-12651.md`](notes/block-collisions-12651.md) |
| **攀爬方块** | `Pages/climb_blocks.md` | 全库 7 种 `Climbable\|CanHaltWhenClimbing` 方块、`isOnLadder()` vs `BlockClimberSystem`、开活板门不可爬、脚手架免摔、细雪+皮革靴 `1.5×` | [`climb-blocks-wiki.md`](climb-blocks-wiki.md) / [`notes/climb-blocks-12651.md`](notes/climb-blocks-12651.md) |
| **减速方块、冰冻与灵魂沙** | `Pages/cobweb_slowing_blocks.md` | 蜘蛛网/甜浆果/细雪 `minss` 与 `0.998³` 盒、细雪 `1.5f` 与 `2.5f` 下沉 VA、冰冻 `+1/140`/`-2/140`、灵魂沙 `(1/1.225)³` 与 `+0.1` 地毯对照 | [`slowing-blocks-wiki.md`](slowing-blocks-wiki.md) / [`notes/slowing-blocks-12651.md`](notes/slowing-blocks-12651.md) |
| **蜂蜜块** | `Pages/slime_honey_block.md` | `0.875×1.0×0.875` 盒、无条件 `0.4^n` 水平乘数与 `-0.12` 垂直钳位、顶面与黏液一致、起跳 `0.6×`（`0.252`）、摔伤 `×0.5`、`WasInWater` 排除 | [`honey-block-wiki.md`](honey-block-wiki.md) / [`notes/honey-block-12651.md`](notes/honey-block-12651.md) |
| **16 b/t 速度上限** | `Pages/speed_limit.md` | `fetchCollisionShapes` 对 `MoveRequest.mSpeed` 做三维欧氏模长 `≤16` 等比缩限（`256.0f/16.0f`）、45° 单轴 `11.3136`、灵魂疾行 III+迅捷 255 封顶 | [`speed-limit-wiki.md`](speed-limit-wiki.md) / [`notes/speed-limit-12651.md`](notes/speed-limit-12651.md) |
| **疾跑状态机与撞墙** | `Pages/sprint.md` | 四系统与 `PlayerPreMoveSystem` 时序、±45° 扇区、`5e-5f` 三区间与 `f65` 转折（`24.42°/65.58°`）、地面/空中零延迟、疾跑跳 `+0.2`（`SIN_TABLE`） | [`sprint-wiki.md`](sprint-wiki.md) / [`notes/sprint-12651.md`](notes/sprint-12651.md) |
| **上台阶、Blip 与 Grind** | `Pages/stepping.md` | `0.5625` 步高、试探滤天花板盒与终点全盒复核（不可穿檐）、`ClientVariableMaxAutoStepSystem` 三值与 `ClientAcceptanceSystem`、前沿跳与落地同拍 Blip、点按 `3` tick 极限 | [`stepping-wiki.md`](stepping-wiki.md) / [`notes/stepping-12651.md`](notes/stepping-12651.md) |
| **潜行、爬行与姿态切换** | `Pages/sneaking_crawling.md` | 六面 `0.01f` 内缩级联净空门槛 `1.79/1.48/0.59`、`0.6` 写链与 `ActorDataIDs [38,53,54]`、潜行/爬行 `0.3+0.15×SwiftSneak`（`TickServerMoveInputHandler`）、`0.025` 边缘防跌落与对角回退 | [`sneaking-crawling-wiki.md`](sneaking-crawling-wiki.md) / [`notes/sneaking-crawling-12651.md`](notes/sneaking-crawling-12651.md) |
| **创造飞行物理** | `Pages/constants.md` L31 | 水平 `v'=0.91v+0.049`（疾跑 `2×`）、松键首拍 `0.91` 之后 `0.34125 = 0.91 × 0.375`（无独立 `0.34125` 常量）、竖直 `vy'=0.6vy+a`、`mPosDelta` 相位 | [`creative-flying-wiki.md`](creative-flying-wiki.md) / [`notes/creative-flying-12651.md`](notes/creative-flying-12651.md) |
| **使用物品与望远镜加速** | `Pages/spyglass_glitch.md` | `ItemUseSlowdown` 默认 `0.35f` 经 `Apply` 平方一次得 `0.1225×`、单拍瞬态清空、与潜行相乘、疾跑开镜慢 1 拍过渡、切槽按新物品、**望远镜加速不存在** | [`spyglass-item-use-wiki.md`](spyglass-item-use-wiki.md) / [`notes/spyglass-item-use-12651.md`](notes/spyglass-item-use-12651.md) |
| **float32 碰撞箱精度与卡墙挤出** | `Pages/precision_glitches.md` / `Pages/collisions.md`（待回填） | `[Y,X,Z]` 双语义裁剪（贴面/MTD argmin）、AABB 增量平移（跨界 `2^n` 行走漂移：`2^20` 往返宽 `0.375`）与重建量化（`2^20` 宽 `0.5`）、卡墙 `n̂×0.1` 写 `mPosDelta`（欧氏模长 `0.10000000`、±1 格空隙搜索外冻结）、跨 `y=128` 连跳高度 `+1 ULP/跳`、实体推挤 `Mob::pushActors`→`PushMode` 4 路公式闭合、玩家免疫 | [`collisions-precision-wiki.md`](collisions-precision-wiki.md) / [`notes/collisions-precision-12651.md`](notes/collisions-precision-12651.md) |
| **移动输入合成与视角灵敏度** | `Pages/joystick.md` / `Pages/triple_component_strafe.md` / `Pages/camera_movement.md` | `mMove` 合成与符号（左正/前正）、模拟向量任一非 0 即整段跳过数字位、**`len²>1.0f` 才归一化**（亚单位慢走成立、模长不超 1）、`0.0001f` 为不可达防御位、**合成层不做摇杆死区**（前端未定位）、键盘 `W+A+D`/`W+S+A` 折叠与斜走不加速、非 45° 需对角位 + 正交位混用（`26.565°`）、`v=0.81(1.1S)^0.6125`、键鼠 `9600(0.15+0.6v)³×0.3÷W`（W=物理客户区宽度）、望远镜阻尼 **`min(1, 0.05/D)`（Wiki 的 `5/D`）四点实测闭合**、pitch `−89.9f`/`90°−0.1°`（`<180°` 步进稳定复现） | [`input-joystick-strafe-wiki.md`](input-joystick-strafe-wiki.md) / [`notes/input-joystick-strafe-12651.md`](notes/input-joystick-strafe-12651.md) |
| **触屏视角转角与输入处理器家族** | `Pages/camera_movement.md`（Touch 栏）/ `Pages/joystick.md` | 单一 vtable `0x14E760FA0` 107 槽、视角事件回调链 `0x144B22290→0x144B22580→adapter`、slot 27 动作分发（action 1 累加 / action 8 直转）、slot 14 = 触屏 `|x|^1.2/(n+1)` 非线性级 ⇒ `Δ=0.15·g·0.4·|A|^1.2/(n+1)`、`(0.15+0.6v)^3.6` 即 Wiki `^3.6` 来源、事件在 handler 链上游已是 `dx/W`（`W=2560` 实测）、`0.7f` 为俯仰分量 | [`touch-input-turn-wiki.md`](touch-input-turn-wiki.md) / [`notes/touch-input-turn-12651.md`](notes/touch-input-turn-12651.md) |

---

## 四、选型建议与开工入口

| 如果你的主要目标是 | 优先推荐的课题 |
| --- | --- |
| **尽快给 `wiki-pages` 填缺纠错** | 先迁移回填已闭合条目（`elytra`、`angles`、`dolphins-grace`、`climb-blocks`、`stepping`、`sprint`、`sneaking-crawling`、`spyglass`、`collisions-precision`、`joystick`/`triple_component_strafe`/`camera_movement` 等） |
| **破一个悬案级冷门谜题** | **风弹跳（Wind Charge）与伤害击退加速**：精确三维冲量、仰角依赖与 `16 b/t` 钳位的结算顺序 |
| **补强现行输入与视角数学模型** | **触屏虚拟摇杆前端与 D-pad 角键命中区**：从 `0x140707DE0`（joystick_frame/knob）与 `0x144D89B40`（`:touch_virtual_joystick_*:`）起手，找像素拖动→`mAnalogMoveVector` 的写入者 |
| **批量补齐剩余数据资产与收尾** | 跑完 **`tapping`** 实机表，或收掉 §二 列出的轻量尾巴（`block-collisions` 双箱/潜影盒、`angles` 实机拟合等） |
| **开拓全新高价值专题** | **风弹跳与击退加速**、**冰船物理** 或 **服务端移动回滚（Rewind）** |

```bash
# 1. 创建 case 账本
py -3.11 driver/landing.py begin --case <slug> --goal "<要证明的机制与边界>"

# 2. 离线定位符号、ECS 组件与函数
py -3.11 driver/pe_map.py --ecs-find <ComponentName> --with-xrefs
py -3.11 driver/pe_map.py --rtti <ClassName>
py -3.11 driver/pe_map.py --xrefs <va>
```

实验前置遵循 [`../AGENTS.md`](../AGENTS.md) 与 [`reverse-engineering.md`](reverse-engineering.md)：逐 tick 实测前先确保窗口焦点（或使用 `dev.py run --background` / `--attach`），并严格区分 `StateVectorComponent+0x18`（待执行位移 `mPosDelta`）、`Actor::getVelocity()`（已完成位移）以及与脚部固定偏移 `1.6200101` 的位置基准口径。
