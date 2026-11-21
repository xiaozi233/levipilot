# IDA MCP 排障

## 架构与端点错配

本环境的 `ida-pro-mcp` 不是直连 IDA，而是启动一个**长驻的 stdio 代理子进程**
（`ida_pro_mcp/server.py`），通过 HTTP 转发到 supervisor。代理默认连
`127.0.0.1:13337`，而 supervisor 默认监听 **8745**（且可能随配置改变），两者经常对不上；
仓库封装 `driver/ida.py`（以及 [`skills/bedrock-re/tools/ida.py`](../skills/bedrock-re/tools/ida.py)）
不假定固定端口，而是从自己生成的 `out/ida-supervisor.log` 中自动发现端点、缓存会话到
`out/ida-session.json` 并自动补传 `database=<session_id>`。

## 症状与处理

1. **假报错「Did you run Edit -> Plugins -> MCP?」**：该报错通常由代理子进程在 `http.client`
   中抛出，后端往往正常。先用 `py -3.11 driver/ida.py status` 直连检查；由于代理子进程只在
   启动时解析一次目标端口，supervisor 换端口后请直接使用 `driver/ida.py`（或仅终止卡住的代理子进程）。
2. **会话空闲回收**：worker 约 10 分钟无人访问会自动退出，`idb_list` 归零，库相关调用报
   `database is required` 或 `Session not found`。重新 `open`（`run_auto_analysis: false`）
   即可，不要重跑自动分析。
3. **不要在整库上跑 `search_text`**：按 listing 逐条渲染会把单 worker 队列堵死；改用
   `find_bytes` / `insn_query` 并限定 `segment/start/end/func`。

## 超时、卡死与坏库重建

- **per-tool 默认 60 s 太短**：supervisor 的**首次打开**也走该超时。本机打包后的
  `Minecraft.Windows.exe.i64` 约 5.9 GB、冷开约 2 分钟，60 s 时会报
  `Failed to open initial binary: Unexpected error: Tool timed out after 60.00s`。使用仓库封装
  （会代传 `IDA_MCP_TOOL_TIMEOUT_SEC`）：

  ```bash
  py -3.11 driver/ida.py start --wait 480      # 或 skills/bedrock-re/tools/ida.py serve
  py -3.11 driver/ida.py open                  # 丢回复会自动轮询 idb_list
  py -3.11 driver/ida.py decompile 0x141E144A0
  ```

- **绝不要对正在打开的 IDA/idalib 进程 `taskkill /F`**：强杀会把 `.i64` 写坏，导致 idalib
  和 `idat -A` 均报 `FATAL ERROR: Oops! internal error 1228 occurred.`，且删掉解包文件也无法修复。
  若数据库已损坏，需改名留证后从 exe 重建（311 MB exe 单核约 1 小时，内存峰值约 5 GB）：

  ```bash
  idat.exe -A -c -Lout/ida_rebuild.log "%APPDATA%\levilauncher.exe\versions\<版本>\Minecraft.Windows.exe"
  # 打包并回填 role 名称（需使用本地 out/apply_roles.py 与 out/pack_db.py）：
  IDA_ROLES_JSON=docs/roles/1.26.51.01.json \
    idat.exe -A -Lout/ida_pack.log \
      -S"out/apply_roles.py" -S"out/pack_db.py" \
      "%APPDATA%\levilauncher.exe\versions\<版本>\Minecraft.Windows.exe"
  ```

  两个注意事项：① 单独跑 `-A` 只生成解包态（`.id0/.id1/.id2/.nam/.til`），必须再显式调用
  `ida_loader.save_database()` 才能生成 idalib 所需的 `.i64`；② IDA 9.4 中 `-S"脚本 参数"` 的
  `ARGV` 为空，脚本参数需走环境变量（如 `IDA_ROLES_JSON`）。
- **正常收工顺序**：`idb_close(save=true)` → 确认 `idb_list` 为空且 worker 已退出 → 删除解包副本
  （只保留压缩的 `.i64`）→ 再按需关闭 supervisor。supervisor 控制台不响应普通 `taskkill`，确认库已关闭后才可用 `/F`。

## 另见

逆向导出脚本：[`../reverse/ida_export.py`](../reverse/ida_export.py)；逆向结论：
[`../reverse/dmf-mpk-analysis.md`](../reverse/dmf-mpk-analysis.md)。
