# 开发任务提示词 · 黏液反弹「候选表填充」轮（R1–R3）

> 独立成册，单轮派活用这一个文件即可。公共前置（仓库约定、架构硬约束、协议纪律）见
> [`task-prompts.md`](task-prompts.md) §2。
> 上一轮的结论与证据：`docs/notes/bounce-12651.md`，账本
> `skills/bedrock-re/findings/1.26.51.01/cases/slime-bounce-coverage/`。

## 本轮要解决的问题（一句话）

**落地 tick 的 `MoveRequestComponent+0x58` 候选形状表，是由哪段代码、按什么判据填的？**
填完之后，才能把「为什么 0.375 的雪只在特定落差弹」从「凭经验扫描」变成「可预测」。

## 0. 已知事实（别重做）

| 结论 | 证据 | 位置 |
| --- | --- | --- |
| 弹/不弹 = 解析器选中的方块有没有 restitution | `[代码]` | handler `0x148B5DB00`，resolver `0x141E144A0` |
| 判据 = 形状中心 ≤ (AABB.minY − 0.2)，取最高者 | `[代码]` | 同上 |
| 覆盖块高 h、顶面与黏液顶重合时，h ≥ 0.4 才被选中 | 推导 + 实测两端 | `docs/notes/bounce-12651.md` §4 |
| 候选表宿主 = `MoveRequestComponent`（`0x4C8F7288`，152 B），容器在 `+0x58`，refs 在 `+0x70` | `[代码]` | 视图构造 `0x148B6AB80`；处理器 `a5+0x58` |
| 表项布局：`+0x00` 移动前 AABB、`+0x18` 竖向外扩 autoStep×1.01 的 AABB、`+0x34` 本 tick 竖直请求位移、`+0x58/+0x70` 两个向量 | `[代码]+[实测]` | 同上 |
| 碰撞向量每 tick 先清空再重建 | `[代码]` | 清除系统 `0x1448A4FD0`（`end = begin`） |
| 结算：落地 tick 的表里有没有黏液块决定弹否 | `[实测]` 按 tick 对齐 | `bounce-12651.md` §3 |

**已排除（别再试）**：

- `0x1448A2B80 / 3FB0 / 4BD0 / 5100` 不是 tick 体：运行期挂钩 20 tick 内 0 命中，是
  注册期的视图构造器。
- `BlockCollisionResolutionVectorComponent`（`0x2462ED53`）不是 bounce 读的那张表：它的
  向量在表项 `+0x20`，而 bounce 读 `MoveRequest+0x58`。
- 「扫掠盒下探到黏液顶 + 固定 margin」的单一阈值：按结果反推标签会得到互相矛盾的 margin
  （详见 `bounce-12651.md` §3.2 的两条测量陷阱）。

## R1（P1）截获 std::function 里的真实 system 体

```text
目标：拿到 BlockCollisionsSystem 注册的、真正每 tick 执行的函数地址。

做法：
1. 游戏停在主菜单（`leave_world`），装 enter hook：
   - `0x14488F8E0`（BlockCollisionsSystem 本体，注册发生在这里）
   - `0x1463B13E0`（registerSystem；a3 = 视图/可调用体结构，a4 = 名字）
   - `0x1448905E0`（在 0x14488F8E0 里把可调用体塞进 std::function 的助手）
   hook 均带 `mem_arg` + `mem_deref`（下一节），分别抓 a3/a4 指向的结构。
2. 进世界（`enter_world`），让注册跑一遍，`hook_drain`。
3. 在命中的结构里找代码指针（exe 范围内的 0x140000000+），逐个 decompile，
   找「写 MoveRequest+0x58/0x70」或「把 {AABB, Block*} push 进向量」的那一个。

备选（若注册钩子噪声太大）：读 `off_151805230` 分配器 + 24 字节分配点；
0x58 向量的元素是 24 B 形状 + 8 B 方块指针，24 字节分配的调用方就是候选。
```

## R2（P2）从填充代码得出每 tick 的判据

```text
目标：写出「给定 (移动前 AABB, 请求位移, 候选方块集合) → 表里有哪些形状」的规则，
      并与实测对质。

要求：
- 判据必须来自填充代码（哪个盒、什么交集、块坐标怎么取整），不是数值拟合。
- 对质只用「按 tick 对齐抓到的表」做标签（见 §2 协议），共 8 个已对齐样本：
  L3 d0.75/1.0/1.5/2.0 只剩雪；L3 d1.25/3.0、L2 d2.0 含黏液；
  L4 d2.0 只剩雪（旧的非对齐记录说含黏液，是错的）。
- 若规则与某个对齐样本冲突，先怀疑测量相位，不要改规则。
```

## R3（P2）落盘与门禁

```text
- `docs/notes/bounce-12651.md` §3：写清填充点地址、判据、对齐样本表；删掉/勘误
  「单一 margin 已证伪」里被新证据推翻的部分（用 erratum，不改历史）。
- 账本：`landing.py begin/claim/end/close`，新证据标 code/live。
- 若判据稳定，再补一列「预测 10 个新落差」的验证（预注册预测，再跑）。
- 文档改完跑：ruff/mypy + 离线单测；动到 mod 再跑 live selftest（基线 224/224）。
```

## §2 测量协议（这一轮的数据可信度全靠它）

1. **每次试次前回到静止地面**（`teleport(0.5, -59, 0.5)` + `wait_ticks(25)`），再传送到
   试验台。从弹跳中途直接传送会把落地 tick 的请求位移改掉（实测同一落差 `v` 从 −0.585
   变成 −0.14），结果翻面。
2. **每轮用 `blocks` 回读** `(300,-60,300)=slime`、`(300,-59,300)=snow_layer`；
   `prep()` 的 fill 会静默失败（区块未加载）。
3. **标签只取抓到的表**：resolver 在 T-1 和 T 各可能命中一次；把 T-1 的表配 T 的结果
   会得到假矛盾。
4. 一次只改一个变量；结论必须能解释「同落差重复 3 次结果一致」。

## §3 工具速查（本轮新加的能力）

**hook 内存采样**（`hook_install`）：

| 参数 | 含义 |
| --- | --- |
| `mem_arg` | 0..3 = rcx/rdx/r8/r9 |
| `mem_offset` | 有符号，−4096..4096；**负值可以从成员指针回到结构体起点** |
| `mem_size` | 4..128，按 4 递增 |
| `mem_deref` | 先读 `arg+offset` 的指针，再从指向处采 |

```python
h = client.hook_install(va=base + 0x1E144A0, mem_arg=0, mem_offset=-0x58, mem_size=120)
# drain 出的每条命中带 mem{arg,offset,len,ok,deref,addr,hex,f32}
```

CLI：`hook-install --va <runtime VA> --mem-arg 0 --mem-offset -88 --mem-size 120 [--mem-deref]`。
`decode_hook_hits` 的 `HookHit` 现在有 `mem/mem_ok/mem_addr/mem_f32/mem_u32`（NaN 解码成 nan）。

**IDA MCP**（坑已按记忆落盘）：

```bash
IDA_MCP_TOOL_TIMEOUT_SEC=1800 py -3.11 -m ida_pro_mcp.idalib_supervisor \
  "%APPDATA%/levilauncher.exe/versions/1.26.51.01/Minecraft.Windows.exe.i64"
# 端点 http://127.0.0.1:8745/mcp；工具调用要带 database=<idb_list 的 session>
# 默认 60s 打不开 892MB 库；就绪后会写 %APPDATA%\Hex-Rays\IDA Pro\mcp\instances\instance_<port>.json
```

会话里若 `mcp__ida-pro-mcp__*` 没出现，是因为 MCP 工具在会话启动时注册——先起后端再开会话。

**构建 / 安装 / 重启**：

```bash
cd mod/probe && xmake -j4 -y          # 带 XMAKE_GLOBALDIR / XMAKE_PKG_INSTALLDIR（见 AGENTS.md）
# 关游戏 → 复制 bin/probe/{probe.dll,probe.pdb,manifest.json} 到 mods/probe
py -3.11 driver/dev.py run --world "超平坦测试" --allow allow_hook_va=1 \
  --allow allow_input_inject=1 --allow allow_selftest_stress=1
py -3.11 driver/dev.py restore-config   # 还原 harness.json（dev.py 会留 .prev）
```

**当前环境**：游戏在「超平坦测试」，玩家在 `(0.5, −60, 0.5)` 草地；测试台已拆除；
`harness.json` 现有 `allow_input_inject / allow_selftest_stress / allow_hook_va = true`；
基线 live selftest **224/224**、离线 **232/232**。

## §4 关键地址（1.26.51.01，RVA = VA − 0x140000000）

| 角色 | VA |
| --- | --- |
| 落地处理器 `@bounce_handler` | `0x148B5DB00` |
| 位置→方块解析器 `@block_pos_resolver` | `0x141E144A0` |
| 0.080000117f 门槛 `@bounce_threshold_f32` | `0x14EA350B0` |
| bounce 系统执行器 / 包装 | `0x148B6AE40` / `0x148B6ACA0` |
| MoveRequest 视图构造器 `@move_request_view_builder` | `0x148B6AB80` |
| BlockCollisionsSystem 注册 | `0x14488F8E0` |
| 注册助手 registerSystem | `0x1463B13E0` |
| std::function 包装助手 | `0x1448905E0` |
| 碰撞向量清除系统（每 tick） | `0x1448A4FD0` |
| 按实体 runner / 位置请求累加 | `0x1448A4570` / `0x1448A4360` |

组件 id：`ApplyRestitution=0x7DF54114`、`StateVector=0x1B5D5238`、`AABBShape=0xBAC1B3CF`、
`MoveRequest=0x4C8F7288`、`ActorDataFlag=0xC67426F3`、
`BlockCollisionResolutionVector=0x2462ED53`、`ActorSetPositionRequest=0x5E3CA211`。

## §5 本轮的临时产物（不在仓库里）

`C:\Users\xiaozi\reverse-skill\work\mc-bedrock-12651-bounce\`：

- `dec_*.c`：handler / resolver / 执行器 / 视图构造 / BlockCollisionsSystem / invoker 的反编译；
- `scan_text.py` / `scan_container_write.py` / `scan_entry_58.py` / `scan_vec_write2.py`：锚点扫描；
- `mcp_call.py`：直连 idalib MCP 的小客户端（`py mcp_call.py decompile '{"addr":"0x..."}'`）；
- `live/`：`bounce_probe.py`、`sweep.py`、`hook_probe.py`（试验台 + hook 采样模板）。
