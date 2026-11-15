# 文档索引

先读根目录 `README.md` 了解使用方式，再按主题查本文档。这里只索引当前实现与逆向证据；
历史过程记录以 Git 历史为准。

## 使用与扩展

| 文档 | 内容 |
| --- | --- |
| [`architecture.md`](architecture.md) | 分层、线程模型、协议、生命周期和扩展步骤 |
| [`mod-internals.md`](mod-internals.md) | 子系统实现细节、容量/预算、诊断语义和当前验收 |
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
| 流体与液体运动（水/岩浆：竖直递推、水平阻力、流动推力、泳姿、入水判定） | [`fluid-movement.md`](fluid-movement.md)；[`fluid-wiki.md`](fluid-wiki.md) 是精简版 | [`notes/re-fluid-12651.md`](notes/re-fluid-12651.md)（函数/偏移/常量位模式）；`skills/bedrock-re/findings/1.26.51.01/cases/` 的 `lava-dim-fire-depth`、`liquid-blip`、`liquid-falling-push` | 有撤回项，引用需带最新口径 |
| 鞘翅滑翔与烟花助推（每刻递推公式、创造 `+0.1` 微升、`0.6 × 0.6` 碰撞箱、撞墙、耐久） | [`elytra-movement.md`](elytra-movement.md)；[`elytra-wiki.md`](elytra-wiki.md) 是精简版 | 离线参考模型与运行期对照脚本位于 gitignored `out/` | 主公式与机制已完成运行期对质 |
| 地面移动与疾跑顺序（E14 写位与水阻力谁先） | [`notes/tick-order-12651.md`](notes/tick-order-12651.md) | 同上证据册的疾跑小节；case `sprint-order-static` | 静态已证明顺序链 |
| tick 内调度与 ECS 顺序（六步、系统注册链、调用栈） | [`notes/tick-order-12651.md`](notes/tick-order-12651.md) | 同文内的调用栈与注册链证据 | 结论成立，覆盖范围限该文列出的系统 |
| 黏液块反弹与上层覆盖判定 | [`notes/bounce-12651.md`](notes/bounce-12651.md) | case `slime-bounce-coverage`（判据接自由落体积分） | 已闭合，含撤回项 |
| 网络与封包（入站切片、无帧长度前缀、共享序列化缓冲竞态、BadPacket 90/187） | [`notes/badpacket-setactordata-20261031.md`](notes/badpacket-setactordata-20261031.md) | [`notes/badpacket-187-20261101.md`](notes/badpacket-187-20261101.md)；实现侧见 [`mod-internals.md`](mod-internals.md) 的 H1/PacketTap | 竞态已确认、属游戏侧，harness 不打补丁 |
| 跑酷数据采集与分析（工具，不是机制结论） | [`parkour.md`](parkour.md) | `scenarios/`、`driver/` 的录制与分析脚本 | 工具可用 |
| 逆向方法与工具链 | [`reverse-engineering.md`](reverse-engineering.md)；[`ida-mcp-troubleshooting.md`](ida-mcp-troubleshooting.md) | [`../skills/bedrock-re/`](../skills/bedrock-re/)（SKILL、methods、experiments、pitfalls、x64dbg） | 现行方法 |
| 版本数据（偏移、签名、角色地址） | [`offsets/`](offsets/)（参与生成）与 [`roles/`](roles/)（静态 VA 证据；`unresolved` 不填地址） | `driver/gen_profiles.py --check`、`roles/resolve.py` | 按版本生效 |
| 第三方对照（dmf-mpk 接口与行为） | [`../reverse/dmf-mpk-analysis.md`](../reverse/dmf-mpk-analysis.md) | `reverse/` 下的 IDA 导出与脚本 | 只作对照，不覆盖本仓结论 |

`skills/bedrock-re/findings/<版本>/cases/<slug>/` 是**逐轮调查账本**：`case.json` 定目标、
`facts.md` 记已证事实、`journal.jsonl` 记每轮动作、`probes/` 放可复跑的命令与输出。
它回答"这条结论是怎么来的"，不替代上表的结论正主。

文档变更应更新现有正文，不再追加按日期排列的验收章节。旧提示词、交接稿和过程性报告
已从 Git 清理；需要追溯时查提交历史。
