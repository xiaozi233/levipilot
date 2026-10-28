# 1.26.45.01（冻结）

这一版的事实库只作为历史对照，**不参与新版本解析**：

- 旧 `known-facts.md`（1,848 个地址）保存在用户级本地 `reverse-skill` pack，不整包复制进公开仓库；
- 地址、结构偏移与报告按本地证据册归档；需要对照时只读，不改写；
- 方法层已提炼到 [`references/methods.md`](../../references/methods.md)，
  版本角色只在 `docs/roles/<version>.json` 里解析；
- 新版本不要复用旧地址；先跟 thunk 到真实函数，再写角色。

公开文档里保留的历史结论见 `docs/legacy-frida-1.26.45.md`。
