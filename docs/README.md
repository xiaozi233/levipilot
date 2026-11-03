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

## 流体与逆向资料

| 文档 | 内容 |
| --- | --- |
| [`fluid-movement.md`](fluid-movement.md) | 水/岩浆运动机制、公式与静态/实测复核 |
| [`fluid-wiki.md`](fluid-wiki.md) | 流体结论的 wiki 精简版 |
| [`reverse-engineering.md`](reverse-engineering.md) | 静态优先的逆向方法与证据纪律 |
| [`ida-mcp-troubleshooting.md`](ida-mcp-troubleshooting.md) | IDA MCP 端点、超时、会话与坏库恢复 |
| [`notes/`](notes/) | BadPacket、流体、ECS 顺序和黏液反弹等原始证据 |
| [`offsets/`](offsets/) | 版本 profile；`runtime:true` 的 JSON 参与生成 |
| [`roles/`](roles/) | RE 角色与静态地址证据；`unresolved` 不猜地址 |
| [`../skills/bedrock-re/`](../skills/bedrock-re/) | 逆向与逐 tick 实验 skill |

文档变更应更新现有正文，不再追加按日期排列的验收章节。旧提示词、交接稿和过程性报告
已从 Git 清理；需要追溯时查提交历史。
