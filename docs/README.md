# 文档索引

本目录只放与当前实现一致的资料。历史/参考材料单独标出，不要当成现行行为。

## 现行实现

| 文档 | 内容 |
| --- | --- |
| [`architecture.md`](architecture.md) | 进程内线程模型、管道协议、生命周期、spool、VEH |
| [`mod-internals.md`](mod-internals.md) | 各模块实现细节：内存原语、watch、hook、ECS、版本指纹、开销指标、输入事件层 |
| [`parkour.md`](parkour.md) | 跑酷相关的起跳/落点/记录口径与实验方法 |
| [`scenario-dsl.md`](scenario-dsl.md) | `scenarios/*.json` 的步骤、采样字段和运行方式 |
| [`mod-build-environment.md`](mod-build-environment.md) | 本机 xmake / LLVM / SDK 布局，构建命令（`xmake -j4`）和两个必须保留的补丁 |
| [`ui-automation.md`](ui-automation.md) | 主菜单/世界列表/暂停菜单的桌面 UI 操作；世界内 UI 已可由 `key_event` 完成 |
| [`offsets/`](offsets/) | 版本偏移与签名表（`1.26.51.01.json` 可复制为 `mods/probe/offsets.json` 覆盖） |

## 历史 / 参考

| 文档 | 说明 |
| --- | --- |
| [`legacy-frida-1.26.45.md`](legacy-frida-1.26.45.md) | 旧 frida 路线（1.26.45.01），只保留运行时结论和采样口径 |
| [`offsets-1.26.45.01.md`](offsets-1.26.45.01.md) | 旧版本偏移/组件写回记录，不参与当前构建 |
| [`offsets/1.26.45.01.json`](offsets/1.26.45.01.json) | 同上，JSON 形式 |
| [`ida-mcp-troubleshooting.md`](ida-mcp-troubleshooting.md) | IDA MCP 代理卡死等排障记录 |

## 约定

- 改代码后同步更新 `mod/README.md` 的「实测 / 限制」和这里的对应文档。
- 任务提示词不随仓库分发；验收结论直接写进 `mod/README.md` 与 `mod-internals.md`。
