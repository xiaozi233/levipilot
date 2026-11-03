# notes：逆向证据与调查原文

本目录保留可复核的原始证据，不作为第二套 API 或当前状态文档。现行使用方式看
`mod/README.md`，实现看 `docs/mod-internals.md`，未决范围看
`docs/reliability-repairs.md`。

| 文件 | 内容 |
| --- | --- |
| [`badpacket-setactordata-20261031.md`](badpacket-setactordata-20261031.md) | BadPacket 的入站切片、深拷贝、共享序列化缓冲竞态与撤回项 |
| [`badpacket-187-20261101.md`](badpacket-187-20261101.md) | packet 187 的现场与后续调查边界 |
| [`re-fluid-12651.md`](re-fluid-12651.md) | 流体、疾跑和泳姿的函数、偏移、公式及静态证据 |
| [`tick-order-12651.md`](tick-order-12651.md) | ECS 调度顺序、注册链和调用栈证据 |
| [`bounce-12651.md`](bounce-12651.md) | 黏液反弹覆盖高度、落差与逐 tick 证据 |

地址和函数角色只对对应版本有效。换版本先校验 `skills/bedrock-re/roles/resolve.py`，不要把
旧地址复制到新 profile。动态观测与静态结论按原文标记区分，撤回项不能继续引用为结论。

开发提示词、交接稿和过程性痛点记录已移出 Git；需要追溯时查提交历史。合并时的脱敏约定
不变：提交前运行 `py -3.11 skills/bedrock-re/checks/lint_public.py`。
