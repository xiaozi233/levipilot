# IDA MCP 排障

## 架构

ZCode 的 `ida-pro-mcp` 不是直连 IDA，而是启动一个**长驻的 stdio 代理子进程**
（`ida_pro_mcp/server.py`），由它用 HTTP 转发到一个 supervisor。代理自己默认
`127.0.0.1:13337`，而 supervisor 的默认端口是 **8745**（配置还能改），所以两边经常对不上——
仓库里的 `driver/ida.py` 不假设端口，而是从自己写的 `out/ida-supervisor.log` 里发现端点。

## 症状与处理

工具调用报「Did you run Edit -> Plugins -> MCP?」时，报错其实是那个**代理子进程**自己
生成的（异常发生在 `http.client` 里），后端往往是好的。按顺序检查：

1. 直连 supervisor（`py -3.11 driver/ida.py status`，不要手写端口）正常，说明后端没挂。
2. 杀掉卡住的代理子进程（`taskkill /PID <pid> /F`）后 ZCode 会自动拉起新的，MCP 立刻恢复。
3. 代理子进程**只在启动时**解析一次目标端口（默认 13337），supervisor 换了端口它不会跟；
   这种时候走 `driver/ida.py`，不要指望 ZCode 面板里的工具。
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
  也没用。恢复只有一条路（2026-11-10 用同样流程重建过一遍，全程约 2 小时）：

  ```bash
  # 坏库改名留证，再从 exe 重建（本机 311 MB 的 exe，单核约 1 小时；内存峰值 5 GB 上下）
  idat.exe -A -c -Lout/ida_rebuild.log "%APPDATA%\levilauncher.exe\versions\<版本>\Minecraft.Windows.exe"
  # 重建完是「解包态」：只有 .id0/.id1/.id2/.nam/.til，没有 .i64，idalib 用不了。
  # 再把 role 名字回填并打包（脚本见 out/apply_roles.py 与 out/pack_db.py）：
  IDA_ROLES_JSON=docs/roles/1.26.51.01.json \
    idat.exe -A -Lout/ida_pack.log \
      -S"out/apply_roles.py" -S"out/pack_db.py" \
      "%APPDATA%\levilauncher.exe\versions\<版本>\Minecraft.Windows.exe"
  ```

  两个坑：① `-A` 单独跑完不会生成 `.i64`，必须再跑一次并用 `ida_loader.save_database()`
  存到 `.i64` 路径（`-P` 在已有解包库上不生效，因为没改动时 IDA 干脆不保存）；
  ② `-S"脚本 参数"` 在 IDA 9.4 里 `ARGV` 是空的，参数要走环境变量（`IDA_ROLES_JSON`）。
  每次 idalib 打开打包后的 `.i64`，都会在同目录重新生成/占用 `.id0/.id1/.nam`，
  换库或搬库前必须先 `idb_close`，否则 `mv` 报 `Device or resource busy`。

  要停后端就用 `idb_close(save=true)`，或让它自己空闲回收。`idb_close` 后磁盘上会同时留下
  打包的 `.i64` 和这次打开生成的解包副本（`.id0/.id1/.id2/.nam/.til`）；确认
  `idb_list` 为空、worker 已退出后，把解包副本删掉，只保留压缩的 `.i64`
  （本版本约 5.91 GB），下次打开会按需再生成。收工顺序：`idb_close(save=true)` → 删副本 →
  停 supervisor；supervisor 控制台进程不响应普通 `taskkill`，确认库已关后才可用 `/F`。

- **不要在整库上跑 `search_text`**（按 listing 逐条渲染）：在几百 MB 的 `.text` 上一次
  扫描就能把单 worker 的队列堵死。用 `find_bytes` / `insn_query` 并限定
  `segment/start/end/func`，确实要扫就自己切段。

- 打开后长时间无访问，worker 会空闲回收；`Session not found` 时重新 `open`
  （`run_auto_analysis: false`）即可，不用重跑分析。

## 另见

逆向导出脚本：[`../reverse/ida_export.py`](../reverse/ida_export.py)；逆向结论：
[`../reverse/dmf-mpk-analysis.md`](../reverse/dmf-mpk-analysis.md)。
