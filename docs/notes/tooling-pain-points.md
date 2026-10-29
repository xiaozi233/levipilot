# 工具链痛点与修复（2026-10-29 黏液反弹一轮的复盘）

> 目的：把「这一轮到底被什么卡住」写下来，能修的就地修掉，不能修的写清边界，
> 别让下一次逆向再把时间花在同一处。
> 每条都带现象、证据、状态。

## P1 hook 只带 4 个整数参数，读不到参数指向的短命缓冲（已修）

**现象**：要判断落地解析器选了哪个方块，必须看它的候选形状表；那张表只在移动的
几微秒内存在，`hook_drain` 之后再 `mem_read` 只能读到已清空的容器（实测 `n=0`，
同一地址在落地瞬间是 `n=2`）。上一轮只能绕道 Frida。

**修复**：`hook_install` 新增 `mem_arg` / `mem_offset` / `mem_size`：

- `mem_arg` 0..3 = `rcx/rdx/r8/r9`；`mem_size` 4..64、按 4 递增；
- detour 内用 `readMemory`（`VirtualQuery` + `ReadProcessMemory`，无异常路径）把
  `args[mem_arg] + mem_offset` 拷进预分配的 ring；不可读时 `mem_ok=false`，不会崩；
- `hook_drain` 每条命中带 `mem{arg, offset, len, ok, addr, hex, f32}`；
- 未开启时 detour 只多一次可预测分支。

用法：

```bash
py -3.11 driver/harness/cli.py hook-install --va <runtime VA> --mem-arg 2 --mem-size 24
```

```python
hook = client.hook_install(va=resolver_va, mem_arg=2, mem_size=24)   # r8 = AABB
rows = [h for h in decode_hook_hits(client.hook_drain(0)) if h.hook == hook["id"]]
rows[-1].mem_f32          # (minx, miny, minz, maxx, maxy, maxz)
rows[-1].mem_ok           # False = 参数不是可读指针
```

验收（本轮实跑，只用仓库工具）：

| 采样 | 结果 |
| --- | --- |
| resolver `mem_arg=2, size=24` | `[300.2, −58.625, 300.2, 300.8, −56.825, 300.8]`（AABB，minY = 雪顶） |
| resolver `mem_arg=0, size=24` | 容器 `{begin,end}`：3 层雪 drop1.0/2.0 → n=1；drop3.0 → n=2；2 层雪 → n=2 |

与 Frida 抓到的结论逐项一致；live selftest 新增 `h0_hook_mem`（抓 `args[0]` 8 字节，
再用 `mem_read` 复核同一地址，`hook_hex == read_hex`）。

## P2 `ecs_components` 被一个 storage 拖垮，整条命令回 internal_error（已修）

**现象**：同一次会话里，`ecs_entity`、`ecs_component` 正常，`ecs_components` 连续 12 次
都返回 `{"reason":"internal_error","phase":"ecs","message":"structured exception"}`；
selftest 因此直接中止（142 个 storage 一个都没返回）。

**根因边界**：枚举里对每个 storage 调 `value(ent)/index(ent)/stride`，其中一个会抛 AV；
外层 `__try` 把**整条命令**兜住，于是“一个坏 storage = 全空”。

**修复**：把每个 storage 的探测拆进独立的 SEH 小函数（只吃 POD，能放 `__try`）：

```c
probeStorageRow(&storage, entity, row);   // ok=false 表示这一条探测崩了
if (!row.ok) { skipped.push_back(id); continue; }
```

`ecs_components` 的回复新增 `skipped: [id...]`（协议只增字段）。坏条目被跳过并在
`skipped` 里点名，其余 140+ 条照常返回。修复后同一进程返回 `142 components, skipped=[]`，
selftest 里的 `ecs_components` 恢复 PASS。

## P3 批量查询把 mod 的管道线程卡死，只能重启游戏（诊断已加，未根治）

**现象**：连续 ~600 次 `ecs_context`/`mem_read` 之后，客户端再也连不上
（`[Errno 22] Invalid argument`），但游戏仍在 tick、spool 仍在增长；
`harness-boot.log` 停在 `pipe: hello written`，没有后续 `peek failed`。
单实例命名管道 + 卡住的监听线程 = 只能重启进程。

**本轮修复**：失败信息变成可操作提示（`driver/harness/connection.py::pipe_error_hint`）：

| WinError | 提示 |
| --- | --- |
| 2 | 游戏没开或 mod 没加载 |
| 5 | 拒绝访问（管理员/普通权限混用） |
| 87 | 实例状态异常（多为上一次脚本卡住管道线程）→ 重启游戏 |
| 231 | 管道忙：另一个客户端还连着 |
| 232/109 | 管道正在关闭/已断开 |

**未做**：mod 侧看门狗（跨线程 `CloseHandle` 回收卡住的实例）。`PipeChannel` 的注释
明确说过另一线程关句柄会与阻塞 I/O 竞争，这是历史坑；没有稳定复现前不做。
规避：批量查询分批并留出间隔，脚本结束前 `client.close()`。

## P4 dev.py 覆写 harness.json（已修）

**现象**：`dev.py run --allow allow_hook_va=1` 会整份重写配置，把用户原有的
`allow_input_inject`、`events` 等键抹掉；强能力开关还会留在文件里，下次启动继续生效。

**修复**：

- 改为**合并**：保留未知键与手写开关，只覆盖 `auto_enter`/`options`（这两项由
  dev.py 拥有，旧的 `auto_enter` 会和显式 `enter_world` 抢跑）；
- 首次写入前备份到 `harness.json.prev`，新增 `py -3.11 driver/dev.py restore-config`；
- `tests/test_dev_config.py` 覆盖合并、备份只写一次、恢复、空调用不动文件。

## P5 IDA MCP 不在时的静态路线（文档化 + 后端已拉起）

**现象**：ZCode 面板显示 `ida-pro-mcp` “服务器连接失败”。它只是
`127.0.0.1:13337` 的代理，需要 IDA 侧 GUI/idalib server 在跑；没有它，
`mcp__ida-pro-mcp__*` 不会出现在会话工具里。

**本轮可用路线**（不依赖 MCP）：

1. **capstone 线性扫描**常量/RVA 引用：`.text` 按 1 MB 分块喂
   `md.disasm(blob, addr)`（`skipdata=True`），解析 `[rip + disp]` 求目标地址。
   一次性把 240 MB 丢给 `md.disasm` 会让 Python 端吃 8 GB 内存；分块是必须的。
2. 找到锚点（组件 id 立即数、`0.080000117f`、`-0.2f`、`/24` 魔数）后再用
   headless IDA **只反编译这几个地址**。
3. `ida_bytes.bin_search(start, end, patt, None, flags)` 在 IDA 9.4 上会直接抛异常
   （mask 不能传 `None`）；用 `idc.find_binary` 或干脆不走 IDA 搜索。
4. `idautils.Strings()` 之后自动反编译“所有字符串引用”的小脚本会卡在未知大函数上；
   先列锚点、再定点反编译。

**把后端拉起来**（这样下一次会话可直接用 MCP 工具）：

```bash
# ZCode 里的 ida-pro-mcp 是 stdio 代理，真正干活的是 idalib worker
IDA_MCP_TOOL_TIMEOUT_SEC=900 py -3.11 -m ida_pro_mcp.idalib_supervisor \
    "%APPDATA%/levilauncher.exe/versions/1.26.51.01/Minecraft.Windows.exe.i64"
```

坑：默认工具超时 60 s，而这份 892 MB 的 `.i64` 首次 `idb_open` 要几分钟，会报
`Failed to open initial binary: Tool timed out after 60.00s`；用
`IDA_MCP_TOOL_TIMEOUT_SEC=900` 放宽。worker 就绪后会在
`%APPDATA%\Hex-Rays\IDA Pro\mcp\instances\instance_<port>.json` 登记，代理按这个目录发现实例。

本轮实测：worker 在 `127.0.0.1:5081` 打开 `Minecraft.Windows.exe.i64`，supervisor 的
MCP 端点在 `127.0.0.1:8745/mcp`；`tools/list` 返回 66 个工具，`idb_list` 给 session，
`decompile {"database": "<session>", "addr": "0x148B5DB00"}` 返回的 Hex-Rays 体与
capstone 定位的处理器一致（8 参数签名）。**注意**：这套 MCP 工具属于 ZCode 会话启动时的
注册结果，本会话启动时后端不在，所以工具没进工具表；后端已在跑，下一次会话（或重连 MCP）
即可直接用 `mcp__ida-pro-mcp__*`。

## P6 landing.py 的 claim YAML 细节（文档化）

- role 名以 `@` 开头，YAML 里必须加引号：`"@bounce_handler": "0x148B5DB00"`；
- `evidence_tier: code` 的 claim 除了 `static_closure` 还必须给 `evidence`；
- `roles` 的地址只接受 `0x...` 或 `null`，不能写语义值（如 `"@-0.2"`）。

## P7 机制文档缺口（已补）

本轮结论落在 [`bounce-12651.md`](bounce-12651.md)，账本在
`skills/bedrock-re/findings/1.26.51.01/cases/slime-bounce-coverage/`。

## 状态一览

| 项 | 状态 | 证据 |
| --- | --- | --- |
| P1 hook 内存采样 | 已修 | 本文件验收表；`h0_hook_mem` PASS |
| P2 ecs_components 单点故障 | 已修 | 142 条全返回、`skipped=[]`；selftest `ecs_components` PASS |
| P3 管道卡死 | 诊断已加 | `pipe_error_hint` + 单测 |
| P4 dev.py 配置 | 已修 | `tests/test_dev_config.py` |
| P5 IDA MCP | 文档化 + 加速后端 | 本文件 §P5 |
| P6 claim 细节 | 文档化 | 本文件 §P6 |

改完的验收（2026-10-29，干净地面、`allow_input_inject` + `allow_selftest_stress`）：

- live selftest **224/224**（新增 `h0_hook_mem`）；
- 离线单测 **238/238**；`ruff check` / `ruff format --check` / `mypy` 全绿。
