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
   里没有这个参数，所以库相关工具走 `driver/ida.py`（或 skill 里的
   `skills/bedrock-re/tools/ida.py`，同一实现的薄封装）：它会自己发现端点、开会话、
   带上 `database`，并缓存会话 id。
6. 端点默认是 8745，但 supervisor 的配置可能改掉它（实测绑过 13337）；`driver/ida.py`
   会从自己写的 `out/ida-supervisor.log` 里解析实际端口，两侧不用手工对齐。

## 超时、卡死与坏库（2026-10-30 修订）

- **per-tool 默认 60 s 太短**：supervisor 的**首次打开**也走这个超时。本机重建后的
  `Minecraft.Windows.exe.i64` 是 5.9 GB、冷开约 2 分钟，60 s 时日志会是
  `Failed to open initial binary: Unexpected error: Tool timed out after 60.00s`，
  端点根本起不来。用仓库里的封装（会代传 `IDA_MCP_TOOL_TIMEOUT_SEC`）：

  ```bash
  py -3.11 driver/ida.py start --wait 480      # 或 skills/bedrock-re/tools/ida.py serve
  py -3.11 driver/ida.py open                  # 丢回复会自动轮询 idb_list
  py -3.11 driver/ida.py decompile 0x141E144A0
  ```

  客户端会自己发现端点（supervisor 不一定绑 8745，实测绑过 13337）、缓存会话到
  `out/ida-session.json`、会话失效时重开一次，失败时把 supervisor 日志尾巴贴出来。

- **绝不要对正在打开的 IDA/idalib 进程 `taskkill /F`**（旧版本文档在这里建议过杀 worker，
  已撤回）。2026-10-30 就是这样把 `.i64` 写坏的：之后 64 KB exe、7 MB dll、原始 311 MB
  exe 都能正常打开，唯独该 `.i64` 在 idalib 和 `idat -A` 下都报
  `FATAL ERROR: Oops! internal error 1228 occurred.`。删 `.id0/.id1/.nam/.til` 重新解包
  也没用。恢复只有一条路：

  ```bash
  # 坏库改名留证，再从 exe 重建（本机 311 MB 的 exe，单核约 1 小时）
  idat.exe -A -Lout/ida_rebuild.log "%APPDATA%\levilauncher.exeersions\<版本>\Minecraft.Windows.exe"
  ```

  要停后端就用 `idb_close(save=true)`，或让它自己空闲回收。

- **不要在整库上跑 `search_text`**（按 listing 逐条渲染）：在几百 MB 的 `.text` 上一次
  扫描就能把单 worker 的队列堵死。用 `find_bytes` / `insn_query` 并限定
  `segment/start/end/func`，确实要扫就自己切段。

- 打开后长时间无访问，worker 会空闲回收；`Session not found` 时重新 `open`
  （`run_auto_analysis: false`）即可，不用重跑分析。

## 另见

逆向导出脚本：[`../reverse/ida_export.py`](../reverse/ida_export.py)；逆向结论：
[`../reverse/dmf-mpk-analysis.md`](../reverse/dmf-mpk-analysis.md)。
