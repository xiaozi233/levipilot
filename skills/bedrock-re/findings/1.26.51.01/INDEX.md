# 1.26.51.01 findings

当前版本的角色表在 [`docs/roles/1.26.51.01.json`](../../../../docs/roles/1.26.51.01.json)。各专题公开结论正主见 [`docs/README.md`](../../../../docs/README.md)；下方列出全部 case 账本。

## 已结案（17）

- [`block-collisions-12651`](cases/block-collisions-12651/report.md) — 全游戏 1477 类 / 22091 状态方块与实体碰撞箱导出及 Wiki 校对（正主：[`docs/notes/block-collisions-12651.md`](../../../../docs/notes/block-collisions-12651.md)）
- [`collisions-precision-12651`](cases/collisions-precision-12651/report.md) — float32 碰撞箱精度、卡墙挤出 `n̂×0.1` 与方块/实体碰撞解算（正主：[`docs/notes/collisions-precision-12651.md`](../../../../docs/notes/collisions-precision-12651.md)）
- [`climb-blocks-12651`](cases/climb-blocks-12651/report.md) — 7 种可攀爬方块、单点 vs 足迹、脚手架免摔与细雪+皮革靴 `1.5×`（正主：[`docs/notes/climb-blocks-12651.md`](../../../../docs/notes/climb-blocks-12651.md)）
- [`creative-flying-12651`](cases/creative-flying-12651/report.md) — 创造飞行水平/竖直递推、`0.34125 = 0.91 × 0.375` 与 `MobMovementFriction`（正主：[`docs/notes/creative-flying-12651.md`](../../../../docs/notes/creative-flying-12651.md)）
- [`dolphins-grace-12651`](cases/dolphins-grace-12651/report.md) — 海豚的恩惠扫描门控、`SwimSpeedMultiplier=2.0` 与深海探索者/迅捷交互（正主：[`docs/notes/dolphins-grace-12651.md`](../../../../docs/notes/dolphins-grace-12651.md)）
- [`honey-block-12651`](cases/honey-block-12651/report.md) — 蜂蜜块 `0.4^n`、`-0.12`、起跳 `0.6×`、摔落 `×0.5` 与水中排除（正主：[`docs/notes/honey-block-12651.md`](../../../../docs/notes/honey-block-12651.md)）
- [`input-joystick-strafe-12651`](cases/input-joystick-strafe-12651/report.md) — 移动输入合成与 `>1` 才归一化、键盘全矩阵与对角位混用、合成层无死区（前端 unresolved）、灵敏度曲线与键鼠 `9600(0.15+0.6v)³×0.3÷W`、望远镜阻尼 `min(1,0.05/D)`、pitch `±89.9`（第 2 轮含勘误）（正主：[`docs/notes/input-joystick-strafe-12651.md`](../../../../docs/notes/input-joystick-strafe-12651.md)；通俗版 [`docs/input-joystick-strafe-wiki.md`](../../../../docs/input-joystick-strafe-wiki.md)）
- [`lava-dim-fire-depth`](cases/lava-dim-fire-depth/report.md) — 岩浆扩散维度 `drop-off`、深度与抗火无关性（正主：[`docs/fluid-movement.md`](../../../../docs/fluid-movement.md)）
- [`liquid-falling-push`](cases/liquid-falling-push/report.md) — 下落水竖直流推邻接实心面条件（正主：[`docs/fluid-movement.md`](../../../../docs/fluid-movement.md)）
- [`slime-bounce-coverage`](cases/slime-bounce-coverage/report.md) — 黏液块反弹与上层覆盖落差判据（正主：[`docs/notes/bounce-12651.md`](../../../../docs/notes/bounce-12651.md)）
- [`slowing-blocks-12651`](cases/slowing-blocks-12651/report.md) — 蜘蛛网/甜浆果/细雪/冰冻与灵魂沙减速闭环（正主：[`docs/notes/slowing-blocks-12651.md`](../../../../docs/notes/slowing-blocks-12651.md)）
- [`sneaking-crawling-12651`](cases/sneaking-crawling-12651/report.md) — 潜行/爬行 `1.79/1.48/0.59` 门槛、`0.6` 写链、Swift Sneak 与 `0.025` 防跌落（正主：[`docs/notes/sneaking-crawling-12651.md`](../../../../docs/notes/sneaking-crawling-12651.md)）
- [`speed-limit-12651`](cases/speed-limit-12651/report.md) — `fetchCollisionShapes` `16 b/t` 三维欧氏钳位与移动极值（正主：[`docs/notes/speed-limit-12651.md`](../../../../docs/notes/speed-limit-12651.md)）
- [`sprint-12651`](cases/sprint-12651/report.md) — 疾跑状态机、`5e-5f` 撞墙三区间、`f65` 转折与疾跑跳 `+0.2`（正主：[`docs/notes/sprint-12651.md`](../../../../docs/notes/sprint-12651.md)）
- [`sprint-order-static`](cases/sprint-order-static/report.md) — E14 主 pass 内 `ActionSprintTrigger` 早于水阻力的静态顺序证明（正主：[`docs/notes/tick-order-12651.md`](../../../../docs/notes/tick-order-12651.md)）
- [`spyglass-item-use-12651`](cases/spyglass-item-use-12651/report.md) — `ItemUseSlowdown` `0.35² = 0.1225` 管线与望远镜加速证伪（正主：[`docs/notes/spyglass-item-use-12651.md`](../../../../docs/notes/spyglass-item-use-12651.md)）
- [`stepping-12651`](cases/stepping-12651/report.md) — 上台阶 `0.5625`、`ClientVariableMaxAutoStepSystem`、`ClientAcceptanceSystem`、固体 Blip 与点按 `3` tick（正主：[`docs/notes/stepping-12651.md`](../../../../docs/notes/stepping-12651.md)）

## 调查中（4）

- [`liquid-blip`](cases/liquid-blip/facts.md) — 液体退出弹跳（Liquid Blip）与水体边界行为（关联：[`docs/fluid-movement.md`](../../../../docs/fluid-movement.md)）
- [`liquid-effect-box-depth`](cases/liquid-effect-box-depth/facts.md) — 液体判定盒与液面/液位高度关系
- [`position-y-offset`](cases/position-y-offset/facts.md) — `StateVector` Y 基准相对脚部 `1.6200101` 偏移与头部偏移
- [`swim-jump-sneak`](cases/swim-jump-sneak/facts.md) — 泳姿下跳跃/潜行对竖直速度的影响
