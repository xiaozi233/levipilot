# 文档索引

先读根目录 `README.md` 了解使用方式，再按主题查本文档。这里只索引当前实现与逆向证据；
历史过程记录以 Git 历史为准。

## 使用与扩展

| 文档 | 内容 |
| --- | --- |
| [`architecture.md`](architecture.md) | 分层、线程模型、协议、生命周期和扩展步骤 |
| [`mod-internals.md`](mod-internals.md) | 子系统实现细节、容量/预算与诊断语义（验收数字见 [`reliability-repairs.md`](reliability-repairs.md)） |
| [`../mod/README.md`](../mod/README.md) | mod 构建、安装、API、配置与使用限制 |
| [`mod-build-environment.md`](mod-build-environment.md) | 本机与 CI 的 LLVM 22、xmake、SDK 补丁和构建故障排查 |
| [`porting.md`](porting.md) | 新版本 profile、生成头文件、兼容模式和验收流程 |
| [`scenario-dsl.md`](scenario-dsl.md) | 场景 JSON、步骤、输出和采样语义 |
| [`parkour.md`](parkour.md) | 跑酷录制、跳跃分析、路点、扫描与存档点 |
| [`ui-automation.md`](ui-automation.md) | 需要真实桌面操作时的菜单/对话框兜底流程 |

## 质量与验收

| 文档 | 内容 |
| --- | --- |
| [`reliability-repairs.md`](reliability-repairs.md) | 当前离线/现场基线、覆盖前提和未验收范围 |
| [`code-quality-review.md`](code-quality-review.md) | 当前未决风险、外部限制和维护优先级，不保留逐轮流水账 |
| [`../skills/project-docs/`](../skills/project-docs/) | 文档写作、同步、精简、删除和交付检查规范 |

## 逆向成果（按主题）

每条主题只在一个文档维护**结论**，其余是证据册或逐轮账本；引用结论前先看该主题的撤回项。
地址只对 `1.26.51.01` 有效，换版本先跑 `skills/bedrock-re/roles/resolve.py`。

| 主题 | 结论正主 | 证据与账本 | 状态 |
| --- | --- | --- | --- |
| 流体与液体运动（水/岩浆竖直与水平递推、流动推力、泳姿、入水判定、海豚的恩惠） | [`fluid-movement.md`](fluid-movement.md)；[`fluid-wiki.md`](fluid-wiki.md) 是精简版；[`dolphins-grace-wiki.md`](dolphins-grace-wiki.md) 是海豚的恩惠通俗版 | [`notes/re-fluid-12651.md`](notes/re-fluid-12651.md)；[`notes/dolphins-grace-12651.md`](notes/dolphins-grace-12651.md)；`lava-dim-fire-depth` / `liquid-blip` / `liquid-falling-push` / `dolphins-grace-12651` | 有撤回项，引用需带最新口径 |
| 鞘翅滑翔与烟花助推（每刻 6 步递推、等价 C++、创造 `+0.1`、`0.6×0.6` 碰撞箱、撞墙、耐久） | [`elytra-movement.md`](elytra-movement.md)；[`elytra-wiki.md`](elytra-wiki.md) 是精简与 JE 对照版 | 离线参考模型与对照脚本位于 gitignored `out/` | 主公式与机制已完成运行期对质 |
| 地面移动与疾跑顺序（E14 写位与水阻力谁先） | [`notes/tick-order-12651.md`](notes/tick-order-12651.md) | 同文疾跑小节；case `sprint-order-static` | 静态已证明顺序链 |
| tick 内调度与 ECS 顺序（双 pass、系统注册链、调用栈） | [`notes/tick-order-12651.md`](notes/tick-order-12651.md) | 同文调用栈与注册链证据 | 结论成立，覆盖范围限该文系统 |
| 黏液块反弹与上层覆盖判定 | [`notes/bounce-12651.md`](notes/bounce-12651.md) | case `slime-bounce-coverage`（判据接自由落体积分） | 已闭合，含撤回项 |
| 攀爬方块（7 种 `Climbable` 方块、单点 vs 足迹、`AutoClimb` 无水平限速、脚手架免摔、细雪+皮革靴 `1.5×`） | [`notes/climb-blocks-12651.md`](notes/climb-blocks-12651.md)；[`climb-blocks-wiki.md`](climb-blocks-wiki.md) 是通俗版 | case `climb-blocks-12651`（[`verify_climb_blocks.py`](../skills/bedrock-re/findings/1.26.51.01/cases/climb-blocks-12651/probes/verify_climb_blocks.py) + PE 静态反汇编） | 已闭合（细雪 `1.5f`/`2.5f` VA 见 [`notes/slowing-blocks-12651.md`](notes/slowing-blocks-12651.md)） |
| 16 b/t 速度钳位与移动边界（`fetchCollisionShapes` 三维模长 ≤16 缩限、45° 单轴 `11.3136`、极值与 `FLT_EPSILON`） | [`notes/speed-limit-12651.md`](notes/speed-limit-12651.md)；[`speed-limit-wiki.md`](speed-limit-wiki.md) 是通俗版 | case `speed-limit-12651`（[`verify_speed_limit.py`](../skills/bedrock-re/findings/1.26.51.01/cases/speed-limit-12651/probes/verify_speed_limit.py) + [`scan_move_pipeline.py`](../skills/bedrock-re/findings/1.26.51.01/cases/speed-limit-12651/probes/scan_move_pipeline.py)） | 主体已闭合；鞘翅/载具绕过、`PlayerMovementSettings` 默认阈值未定位 |
| 疾跑状态机（四系统与 `PlayerPreMoveSystem` 时序、±45° 扇区、`5e-5` 撞墙三区间与 `f65` `24.42°/65.58°`、疾跑跳 `+0.2`） | [`notes/sprint-12651.md`](notes/sprint-12651.md)；[`sprint-wiki.md`](sprint-wiki.md) 是通俗版 | case `sprint-12651`（[`verify_sprint.py`](../skills/bedrock-re/findings/1.26.51.01/cases/sprint-12651/probes/verify_sprint.py)、[`verify_sprint_tail.py`](../skills/bedrock-re/findings/1.26.51.01/cases/sprint-12651/probes/verify_sprint_tail.py) + 硬断言） | 已闭合（Round 4 复核；饥饿 `≤6` 实测、载具分支与 `PlayerPreMoveSystem` 注册点为如实边界） |
| 上台阶、Step-Assist Jump Cancel、固体 Blip 与 Grind（`0.5625` 步高、试探与终点全盒复核、`ClientVariableMaxAutoStep` 三值、`ClientAcceptanceSystem`、点按 `3` tick） | [`notes/stepping-12651.md`](notes/stepping-12651.md)；[`stepping-wiki.md`](stepping-wiki.md) 是通俗版 | case `stepping-12651`（[`verify_stepping.py`](../skills/bedrock-re/findings/1.26.51.01/cases/stepping-12651/probes/verify_stepping.py)、[`verify_stepping_tail.py`](../skills/bedrock-re/findings/1.26.51.01/cases/stepping-12651/probes/verify_stepping_tail.py) + PE 静态反汇编） | 已闭合（Round 3 收口；骑乘 `1.0625` 实测、组合不稳定连锁与 `ClientAcceptance` 形参映射为如实边界） |
| 潜行、爬行与姿态尺寸切换（`1.8/1.49/0.6` 级联与门槛 `1.79/1.48/0.59`、`0.6` 写链与 `ActorDataIDs [38,53,54]`、Swift Sneak 与 `0.025` 边缘防跌落） | [`notes/sneaking-crawling-12651.md`](notes/sneaking-crawling-12651.md)；[`sneaking-crawling-wiki.md`](sneaking-crawling-wiki.md) 是通俗版 | case `sneaking-crawling-12651`（[`verify_sneaking_crawling.py`](../skills/bedrock-re/findings/1.26.51.01/cases/sneaking-crawling-12651/probes/verify_sneaking_crawling.py)、[`verify_sneaking_crawling_tail.py`](../skills/bedrock-re/findings/1.26.51.01/cases/sneaking-crawling-12651/probes/verify_sneaking_crawling_tail.py) + watch + PE） | 已闭合（Round 5 + 独立复核；上游 `0x142A3BBC0` 无符号、`Tempted` 意图为语义推断） |
| 减速方块、细雪冰冻与灵魂沙（蜘蛛网/甜浆果/细雪 `minss` 与 `0.998³` 盒、冰冻与灵魂沙 `(1/1.225)³`） | [`notes/slowing-blocks-12651.md`](notes/slowing-blocks-12651.md)；[`slowing-blocks-wiki.md`](slowing-blocks-wiki.md) 是通俗版 | case `slowing-blocks-12651`（[`verify_slowing_blocks.py`](../skills/bedrock-re/findings/1.26.51.01/cases/slowing-blocks-12651/probes/verify_slowing_blocks.py) + PE 静态反汇编） | 已闭合 |
| 蜂蜜块（`0.875×1.0×0.875`、无条件 `0.4^n` 与 `-0.12`、起跳 `0.6×`、摔伤 `×0.5`、创造飞行与水中排除） | [`notes/honey-block-12651.md`](notes/honey-block-12651.md)；[`honey-block-wiki.md`](honey-block-wiki.md) 是通俗版 | case `honey-block-12651`（[`verify_honey_block.py`](../skills/bedrock-re/findings/1.26.51.01/cases/honey-block-12651/probes/verify_honey_block.py) + watch + PE 静态反汇编） | 已闭合（组件瞬态与飞行竖直覆写点标 `[推断]`） |
| 使用物品减速与望远镜加速（`ItemUseSlowdown` 默认 `0.35f` 经 `Apply` 平方得 `0.1225×`、单拍瞬态、组合规则与望远镜加速证伪） | [`notes/spyglass-item-use-12651.md`](notes/spyglass-item-use-12651.md)；[`spyglass-item-use-wiki.md`](spyglass-item-use-wiki.md) 是通俗版 | case `spyglass-item-use-12651`（[`verify_spyglass_item_use.py`](../skills/bedrock-re/findings/1.26.51.01/cases/spyglass-item-use-12651/probes/verify_spyglass_item_use.py) + watch + PE 静态反汇编） | 已闭合（Round 4 返工；刷子未测，盾牌格挡无 ECS 组件，FOV/载具/飞行未覆盖） |
| 创造飞行物理（水平 `v'=0.91v+0.049`、疾跑 `2×`、松键 `0.34125 = 0.91×0.375`、竖直 `0.6` 阻力与 `mPosDelta` 相位） | [`notes/creative-flying-12651.md`](notes/creative-flying-12651.md)；[`creative-flying-wiki.md`](creative-flying-wiki.md) 是通俗版 | case `creative-flying-12651`（[`verify_creative_flying.py`](../skills/bedrock-re/findings/1.26.51.01/cases/creative-flying-12651/probes/verify_creative_flying.py) + watch + PE 静态反汇编） | 已闭合；`a=flySpeed×0.98` 乘法指令与 `0.375` 消费点为 `[推断]` |
| 正弦表、`sinf`/`cosf` 分工与半角可达性（`SIN_TABLE` 65536 项、Pattern A/B 筛选、`fmodf` 锁死） | [`notes/sin-table-12651.md`](notes/sin-table-12651.md)；[`angles-wiki.md`](angles-wiki.md) 是通俗版 | 纯静态反汇编 + `float32` 推算（疾跑跳 `+0.2` 已另于 `sprint-12651` 闭合） | 静态与极值明确；突刺/激流/坐骑冲刺拟合与自定义 `mRot` 发包未做 |
| 全游戏方块与典型实体碰撞箱导出及 Wiki 校对（`1477` 类 / `22091` 状态与旧表勘误） | [`notes/block-collisions-12651.md`](notes/block-collisions-12651.md) | case `block-collisions-12651`（`dump_block_collisions` / `block_shapes` / `entities` + PE） | 主体已闭合；双箱实机配对与潜影盒开盒态未测 |
| 网络与封包（入站切片、无帧长度前缀、共享序列化缓冲竞态、BadPacket 90/187） | [`notes/badpacket-setactordata-20261031.md`](notes/badpacket-setactordata-20261031.md) | [`notes/badpacket-187-20261101.md`](notes/badpacket-187-20261101.md)；实现侧见 [`mod-internals.md`](mod-internals.md) 的 H1/PacketTap | 竞态已确认、属游戏侧，harness 不打补丁 |
| 跑酷数据采集与分析（工具，不是机制结论） | [`parkour.md`](parkour.md) | `scenarios/`、`driver/` 的录制与分析脚本 | 工具可用 |
| 后续研究课题与 `MCBE_Parkour_wiki` 对照选题清单（backlog，非已证结论） | [`research-roadmap.md`](research-roadmap.md) | 对照 `wiki-pages`/`main` 空缺与 `[Todo]` 梳理 | 待逐项立项调查 |
| 逆向方法与工具链 | [`reverse-engineering.md`](reverse-engineering.md)；[`ida-mcp-troubleshooting.md`](ida-mcp-troubleshooting.md) | [`../skills/bedrock-re/`](../skills/bedrock-re/)（SKILL、methods、experiments、pitfalls、x64dbg） | 现行方法 |
| 版本数据（偏移、签名、角色地址） | [`offsets/`](offsets/)（参与生成）与 [`roles/`](roles/)（静态 VA 证据；`unresolved` 不填地址） | `driver/gen_profiles.py --check`、`roles/resolve.py` | 按版本生效 |
| 第三方对照（dmf-mpk 接口与行为） | [`../reverse/dmf-mpk-analysis.md`](../reverse/dmf-mpk-analysis.md) | `reverse/` 下的 IDA 导出与脚本 | 只作对照，不覆盖本仓结论 |

`skills/bedrock-re/findings/<版本>/cases/<slug>/` 是**逐轮调查账本**：`case.json` 定目标、
`facts.md` 记已证事实、`journal.jsonl` 记每轮动作、`probes/` 放可复跑的命令与输出。
它回答"这条结论是怎么来的"，不替代上表的结论正主。

文档变更应更新现有正文，不再追加按日期排列的验收章节。旧提示词、交接稿和过程性报告
已从 Git 清理；需要追溯时查提交历史。
