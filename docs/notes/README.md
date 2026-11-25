# notes：逆向证据与调查原文

本目录保留可复核的原始证据，不作为第二套 API 或当前状态文档。现行使用方式看
`mod/README.md`，实现看 `docs/mod-internals.md`，未决范围看
`docs/reliability-repairs.md`。**按主题分类（结论正主 / 证据册 / 状态）见
[`docs/README.md`](../README.md) 的「逆向成果（按主题）」一节**；本页只列本目录文件。

| 文件 | 主题 | 内容 |
| --- | --- | --- |
| [`badpacket-setactordata-20261031.md`](badpacket-setactordata-20261031.md) | 网络与封包 | BadPacket 的入站切片、深拷贝、共享序列化缓冲竞态与撤回项 |
| [`badpacket-187-20261101.md`](badpacket-187-20261101.md) | 网络与封包 | packet 187 的现场与后续调查边界 |
| [`re-fluid-12651.md`](re-fluid-12651.md) | 流体与移动 | 流体、疾跑和泳姿的函数、偏移、公式及静态证据 |
| [`tick-order-12651.md`](tick-order-12651.md) | tick 顺序与 ECS | ECS 调度顺序、注册链和调用栈证据 |
| [`bounce-12651.md`](bounce-12651.md) | 黏液反弹 | 黏液反弹覆盖高度、落差与逐 tick 证据 |
| [`sin-table-12651.md`](sin-table-12651.md) | 正弦表与三角函数 | `65536` 项 `SIN_TABLE` 建表/查表、`269` 处/`193` 条目分工与加速筛选（Pattern A/B）、大小半角极值（`5898195°`/`-5898375°`）及客户端 `fmodf` 锁死与包级透传 |
| [`block-collisions-12651.md`](block-collisions-12651.md) | 方块与实体碰撞箱 | `1477` 类 / `22091` 状态全量导出、`Hopper`/`Happy Ghast`/`Shelf Mushroom` 补全与 `MCBE_Parkour_wiki` 勘误校对 |
| [`dolphins-grace-12651.md`](dolphins-grace-12651.md) | 海豚的恩惠 | `DolphinBoostSystem`（`±5.0` 盒/60t 周期/`SWIMMING` 门控）、`SwimSpeedMultiplier=2.0` 跳过 `0.546` 阻力与 `0.02(0.7+0.3L/3)×2` 闭式及迅捷反协同实测 |
| [`climb-blocks-12651.md`](climb-blocks-12651.md) | 攀爬方块 | 7 种 `Climbable \| CanHaltWhenClimbing` 方块、`isOnLadder()` 单点 vs `BlockClimberSystem` AABB 足迹、`AutoClimb` 跳重力/阻尼、脚手架无潜行免摔、`1.5×` 细雪推墙 `+0.300` b/t 与开活板门实测 |
| [`speed-limit-12651.md`](speed-limit-12651.md) | 速度钳位与移动边界 | `fetchCollisionShapes 0x1431c2f50` 对 `MoveRequest.mSpeed` 做三维模长 16 等比缩限（`256.0/16.0` 常量）、45° 斜向单轴 `11.3136`、灵魂疾行 III+迅捷 255 实测封顶、`mPosDelta` 无碰撞时保留 >16 动量、蓝冰 `(0.6/slip)³` 反直觉、`FLT_EPSILON` 归零与终速 `-3.92` |
| [`slowing-blocks-12651.md`](slowing-blocks-12651.md) | 减速方块、冰冻与灵魂沙 | 蜘蛛网/浆果丛/细雪三写入点与 `minss`、`0.998^3` 盒、创造 `Flying & Instabuild` 门控、细雪 `>2.5f` 下陷 `0.9`、冰冻 `+1/140`/`-2/140`/`40t` 伤与不减空速、灵魂沙 `1.225×` 摩擦与 `+0.1` 地毯半砖实测 |

地址和函数角色只对对应版本有效。换版本先校验 `skills/bedrock-re/roles/resolve.py`，不要把
旧地址复制到新 profile。动态观测与静态结论按原文标记区分，撤回项不能继续引用为结论。

开发提示词、交接稿和过程性痛点记录已移出 Git；需要追溯时查提交历史。合并时的脱敏约定
不变：提交前运行 `py -3.11 skills/bedrock-re/checks/lint_public.py`。
