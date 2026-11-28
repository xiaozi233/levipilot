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
| [`sin-table-12651.md`](sin-table-12651.md) | 正弦表与三角函数 | `65536` 项 `SIN_TABLE`、Pattern A/B 加速筛选、大小半角极值及 `fmodf` 锁死与发包透传 |
| [`block-collisions-12651.md`](block-collisions-12651.md) | 方块与实体碰撞箱 | `1477` 类 / `22091` 状态导出、`Hopper`/`Happy Ghast`/`Shelf Mushroom` 补全与 Wiki 勘误 |
| [`dolphins-grace-12651.md`](dolphins-grace-12651.md) | 海豚的恩惠 | `DolphinBoostSystem`（`±5.0` 盒/60t/`SWIMMING`）、`SwimSpeedMultiplier=2.0` 与迅捷反协同实测 |
| [`climb-blocks-12651.md`](climb-blocks-12651.md) | 攀爬方块 | 7 种 `Climbable` 方块、`isOnLadder()` vs `BlockClimberSystem`、脚手架免摔与细雪+皮革靴 `1.5×` |
| [`speed-limit-12651.md`](speed-limit-12651.md) | 速度钳位与移动边界 | `fetchCollisionShapes` 对 `MoveRequest.mSpeed` 三维模长 `16` 缩限、极值、`FLT_EPSILON` 与 `-3.92` |
| [`sprint-12651.md`](sprint-12651.md) | 疾跑状态机 | 四系统与 `PlayerPreMoveSystem` 时序、±45° 起跑门控、`5e-5f` 三区间与 `f65` 转折、疾跑跳 `+0.2` 与 Intent 对照 |
| [`stepping-12651.md`](stepping-12651.md) | 上台阶、Blip 与 Grind | `0.5625` 步高、试探滤天花板盒与终点全盒复核、`ClientVariableMaxAutoStep` 三值、`ClientAcceptanceSystem`、Blip 与点按 `3` tick |
| [`sneaking-crawling-12651.md`](sneaking-crawling-12651.md) | 潜行、爬行与姿态尺寸 | `1.8/1.49/0.6` 级联与 `1.79/1.48/0.59` 门槛、`0.6` 写链与 `ActorDataIDs [38,53,54]`、Swift Sneak 调度序、`0.025` 防跌落与对角回退 |
| [`slowing-blocks-12651.md`](slowing-blocks-12651.md) | 减速方块、冰冻与灵魂沙 | 蜘蛛网/浆果丛/细雪 `minss` 与 `0.998³` 盒、细雪 `1.5f`/`2.5f` VA、冰冻 `+1/140`/`-2/140` 与灵魂沙 `1.225×` 摩擦 |
| [`honey-block-12651.md`](honey-block-12651.md) | 蜂蜜块 | `0.875×1.0×0.875` 盒、无条件 `0.4^n` 与 `-0.12`、起跳 `0.6×`、摔伤 `×0.5`、顶面/飞行与 `WasInWater` 排除 |
| [`creative-flying-12651.md`](creative-flying-12651.md) | 创造飞行物理 | 水平 `v'=0.91v+0.049`、疾跑 `2×`、松键 `0.34125 = 0.91×0.375`、`MobMovementFriction` 玩家路径、竖直递推与 `mPosDelta` |
| [`spyglass-item-use-12651.md`](spyglass-item-use-12651.md) | 使用物品与望远镜加速 | `ItemUseSlowdown` 三系统与 `0.35² = 0.1225`、`sizeof=4` 单拍瞬态、11 物品表、组合规则与望远镜加速证伪 |
| [`collisions-precision-12651.md`](collisions-precision-12651.md) | float32 碰撞箱精度与卡墙挤出 | `[Y,X,Z]` 双语义裁剪（贴面/MTD）、AABB 增量平移（跨界 `2^n` 漂移 + 重建量化 `2^20` 宽 `0.5`）、`0.1` 推向空隙写 `mPosDelta`（欧氏模长 `0.10000000`）、跨 `y=128` 连跳高度 `+1 ULP/跳`、实体推挤 `PushMode` 4 路公式与玩家免疫 |

地址和函数角色只对对应版本有效。换版本先校验 `skills/bedrock-re/roles/resolve.py`，不要把
旧地址复制到新 profile。动态观测与静态结论按原文标记区分，撤回项不能继续引用为结论。

开发提示词、交接稿和过程性痛点记录已移出 Git；需要追溯时查提交历史。合并时的脱敏约定
不变：提交前运行 `py -3.11 skills/bedrock-re/checks/lint_public.py`。
