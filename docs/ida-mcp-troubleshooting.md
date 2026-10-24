# IDA MCP 排障

## 架构

ZCode 的 `ida-pro-mcp` 不是直连 IDA，而是启动一个**长驻的 stdio 代理子进程**
（`ida_pro_mcp/server.py`），由它用 HTTP 转发到 `127.0.0.1:13337` 的 supervisor。

## 症状与处理

工具调用报「Did you run Edit -> Plugins -> MCP?」时，报错其实是那个**代理子进程**自己
生成的（异常发生在 `http.client` 里），后端往往是好的。按顺序检查：

1. 直连 supervisor（`POST http://127.0.0.1:13337/mcp`）正常，说明后端没挂。
2. 杀掉卡住的代理子进程（`taskkill /PID <pid> /F`）后 ZCode 会自动拉起新的，MCP 立刻恢复。
3. 代理子进程**只在启动时**解析一次目标端口（默认 13337），端口变了它不会跟。
4. 另一类失败是 supervisor 侧的**会话空闲回收**（worker 约 10 分钟无人访问就退出）：
  之后 `idb_list` 会显示 0 个会话，带库的工具报 `database is required` 或
  `Session not found`。重新 `idb_open`（`run_auto_analysis: false`）即可，
  不要重跑自动分析。
5. 这套 supervisor 要求每次工具调用带 `database=<session_id>`；ZCode 装的工具 schema
  里没有这个参数，所以库相关工具走直连 `mcp.py <tool> '{"database":"mcbr", ...}'`
  更省事（ZCode 路径只有 `idb_list`/`idb_open` 能用）。

## 另见

逆向导出脚本：[`../reverse/ida_export.py`](../reverse/ida_export.py)；逆向结论：
[`../reverse/dmf-mpk-analysis.md`](../reverse/dmf-mpk-analysis.md)。
