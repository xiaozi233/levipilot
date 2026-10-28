# 文档索引

本目录只放与当前实现一致的资料。历史/参考材料单独标出，不要当成现行行为。

## 现行实现

| 文档 | 内容 |
| --- | --- |
| [`architecture.md`](architecture.md) | 进程内线程模型、管道协议、生命周期、spool、VEH |
| [`mod-internals.md`](mod-internals.md) | 各模块实现细节（内存原语、watch、hook、ECS、版本 profile、开销指标、输入事件层、封包层）与「实测数字速查」 |
| [`porting.md`](porting.md) | 换游戏版本的移植流程：profile、生成头文件、兼容模式与排查命令 |
| [`parkour.md`](parkour.md) | 跑酷相关的起跳/落点/记录口径与实验方法 |
| [`fluid-movement.md`](fluid-movement.md) | 水/岩浆流体运动机制（三介质递推、流推、泳姿、深海行者；1.26.51.01 实测 + 45→51 静态比对） |
| [`fluid-wiki.md`](fluid-wiki.md) | 上者的 wiki 精简版：只保留结论、公式与实用数据，适合对外发布 |
| [`scenario-dsl.md`](scenario-dsl.md) | `scenarios/*.json` 的步骤、采样字段和运行方式 |
| [`mod-build-environment.md`](mod-build-environment.md) | 本机与 CI 的 xmake / LLVM / SDK 布局、构建命令（`xmake -j4`）、必须保留的 SDK 补丁与常见问题 |
| [`ui-automation.md`](ui-automation.md) | 主菜单/世界列表/暂停菜单的桌面 UI 操作；世界内 UI 已可由 `key_event` 完成 |
| [`offsets/`](offsets/) | 版本 profile（`runtime:true` 的 JSON 会被编进 `GeneratedProfiles.h`） |
| [`roles/`](roles/) | RE 角色 → 静态地址与证据（与 offsets 分开；`unresolved` 不猜地址） |
| [`ida-mcp-troubleshooting.md`](ida-mcp-troubleshooting.md) | IDA MCP 代理卡死等排障（逆向工具链） |
| [`reverse-engineering.md`](reverse-engineering.md) | **静态优先**的逆向工作流与踩坑：证据分级、无符号定位手法（注释记录/vtable/SDK）、顺序类问题的判据、磁盘与库纪律、动态取证的边界 |

## 历史 / 参考

| 文档 | 说明 |
| --- | --- |
| [`refactoring.md`](refactoring.md) | 2026-10-23/24 结构重构、任务与会话重设计、世界进入崩溃修复的**决策与验收记录**；其中的通过数随时间过时，现行结构看 `architecture.md` 与 `mod-internals.md` |
| [`legacy-frida-1.26.45.md`](legacy-frida-1.26.45.md) | 旧 frida 路线（1.26.45.01），只保留运行时结论和采样口径 |
| [`offsets-1.26.45.01.md`](offsets-1.26.45.01.md) | 旧版本偏移/组件写回记录，不参与当前构建 |
| [`offsets/1.26.45.01.json`](offsets/1.26.45.01.json) | 同上，JSON 形式 |

## 约定

- 改代码后同步更新：使用限制/基线进 `mod/README.md` 的「实测 / 限制」，逐项实测数字
  进 `mod-internals.md` 的对应小节或「实测数字速查」，并同步这里的相关文档。
- 任务提示词不随仓库分发；验收结论直接写进 `mod-internals.md` 与 `mod/README.md`。
- 静态优先的逆向 + 实验手册（建场回读、录制语义、输入/热键坑、版本角色、逐轮落盘）在仓库根目录
  [`skills/bedrock-re/`](../skills/bedrock-re/)；ZCode 本地通过 `.zcode/skills/` 联接发现，
  用户级通过 `~/.agents/skills/` 联接发现。
