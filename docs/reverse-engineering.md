# 静态优先的逆向工作流（基岩版 1.26.51.01）

> 本文是给「用静态分析回答游戏机制问题」定的工作方法和踩坑记录。
> 用户明确要求：**一切以静态为主；需要时可以用动态取证，但结论必须从静态中来。**
> 动态只负责“把范围缩到某个函数/某条 pass/某个字段”，最终结论要回到反编译、数据结构
> 和调用关系上能自洽地解释，否则只能作为线索，不能写进结论。
>
> 可执行版本见 [`skills/bedrock-re/`](../skills/bedrock-re/)（SKILL.md + methods + landing）；
> 本文保留为方法说明与案例。

---

## 0. 证据分级（所有结论都要带标签）

| 标签 | 含义 | 可以拿来说什么 |
| --- | --- | --- |
| `[代码]` | 反编译的控制流、数据流、写入点 | 可以支撑结论 |
| `[常量]` | 常量、结构布局、表、注册点 | 可以支撑结论 |
| `[实测]` | 运行期数值、调用栈、观察点、线程 | 只作现象/旁证；要由静态解释后才升级 |
| `[推断]` | 由前三者补出的、没有直接读到的环节 | 必须写明“推断”，不能混进结论 |

顺序类结论的强度：**调用栈（且能落到 dispatcher/调用者）> 同 tick 同线程观察点 > 递推拟合**。
跨线程、跨 pass、跨 tick 的“先后”不能当调度顺序。

---

## 1. 目标与数据库

- 目标：`Minecraft.Windows.exe` 1.26.51.01（LeviLamina 26.51.7）。
- 数据库：`%APPDATA%\levilauncher.exe\versions\1.26.51.01\Minecraft.Windows.exe.i64`，
  **只读复用**。不要复制 `.i64`，不要另建库，不要重跑自动分析。
- 类布局/枚举模板：`refs/levilamina-26.51.7/src`（自动生成的头文件），
  用它把反编译里的 `+0x38`、`mode & 1` 对回 `mTickingSystemCategories`、`mNormalTick` 等。

**开库的磁盘纪律（踩过两次）**：

1. 开库会解包出 `.id0`（约 4.5 GB）+ `.id1`（约 1.2 GB）**到库所在盘**；
   先 `df` 确认可用空间 ≥ `.i64` 的 6 倍。
2. 收工顺序：`taskkill` supervisor 和 `idalib_server` → 再删解包的
   `.id0/.id1/.id2/.nam/.til`；进程没退时删会 `Device or resource busy`。
3. 千万别为了“绕开库损坏”去复制 900 MB 的 `.i64`：每份再配 6 GB 解包，
   几轮就能把盘塞爆。

---

## 2. 工具链

- **IDA Pro 9.4 + IDA MCP**（idalib 模式，`127.0.0.1:13337`，会话名如 `mcbr`）。
  关键工具：`decompile`、`disasm`、`xrefs_to`、`callees`、`insn_query`、`get_bytes`、
  `py_eval`、`find(type="string")`。
- 反编译结果尽量**落文件**离线读：大函数会被工具截断，屏幕上看不全。
- **MCP 不在时**（面板显示“服务器连接失败”，它只是 13337 的代理，需要 IDA 侧
  idalib server 在跑）：用 `idat.exe -A -S<script.py> -L<log> <db.i64>` 定点反编译，
  搜索改走 capstone。两个坑：
  - `ida_bytes.bin_search(start, end, patt, None, flags)` 在 9.4 上会抛异常
    （mask 不能是 `None`），用 `idc.find_binary` 或干脆不用 IDA 搜索；
  - 一次性把 240 MB `.text` 交给 `md.disasm()` 会让 Python 端吃 8 GB 内存；
    **按 1 MB 分块**喂 `md.disasm(blob, addr)`（`skipdata=True`），解析
    `[rip + disp]` 求目标地址。先在 `.rdata` 里定位常量，再用 capstone 找引用点，
    最后只让 IDA 反编译这几个地址。

### 2.1 钩住“只在几微秒内存在”的缓冲

入口 hook 默认只记录 `rcx/rdx/r8/r9` 四个整数；对“指针参数指向的临时向量/结构”
（每 tick 重建的形状表、按地址传入的 AABB），等 `hook_drain` 时那块内存已经没了
（`mem_read` 读到空容器）。用 in-detour 内存快照：

```bash
# 抓 resolver 的 r8（AABB，6 个 float）
py -3.11 driver/harness/cli.py hook-install --va <runtime VA> --mem-arg 2 --mem-size 24
```

```python
hook = client.hook_install(va=resolver_va, mem_arg=2, mem_size=24)
rows = [h for h in decode_hook_hits(client.hook_drain(0)) if h.hook == hook["id"]]
rows[-1].mem_f32      # (minx, miny, minz, maxx, maxy, maxz)
rows[-1].mem_ok       # False = 参数不是可读指针（不会崩）
```

约束：`mem_arg` 0..3 对应四个整数寄存器；`mem_size` 4..64、按 4 递增；
拷贝发生在 detour 里（`VirtualQuery` + `ReadProcessMemory`，无分配），未开启时
只多一次可分支。VA hook 仍受 `allow_hook_va` 管控。

---

## 3. 没有符号、没有 RTTI 时怎么定位（按成功率排序）

### A. 美化函数名 + 注释记录 → 代码引用（最有效）

exe 里有一批 `__FUNCSIG__` 风格的函数名和源路径（profiler 注释放的），例如
`void __cdecl EntitySystemsManager::tickEntitySystems(void)`。
每个名字都有一条 `{path, name, line…}` 数据记录；**代码里对这条记录的引用**就在函数内部
的静态初始化处：

1. `find(type="string", targets=["tickEntitySystems"])` 拿到名字地址；
2. `xrefs_to` 名字地址 → 数据记录（名字槽）；
3. 记录起点通常是名字槽 − 8（前面是 path 槽）；
4. `xrefs_to` 记录起点 → 函数里的 `lea`，即目标函数。

实例：`tickEntitySystems` 的名字在 `0x14EF8AF29`，记录引用在 `0x1411779C5`，
函数是 `0x141177690`。同一招也找到了 `Level::tick`、`ServerLevel::_subTick`。

### B. vtable / 构造函数

类的方法表在 `.rdata`：先找 create/ctor 里写 vtable 的位置（`lea rax, off_XXX; mov [obj], rax`），
再按 SDK 的虚函数顺序给槽位命名。实例：`DefaultEntitySystemsCollection` 的 vtable
`0x14E8285E0` 是在 create `0x1425C25A0` 里写进去的，于是拿到
`registerTickingSystem` / `foreachTickingSystem` 的实现地址。

### C. SDK 头文件当“类型模板”

把反编译的字段偏移、枚举值、虚表顺序对回 SDK 头文件。例：
`EntitySystemTickingMode { bool mNormalTick; bool mSingleTick; }` ⇒ 登记点的第三参 `1`
= `{normal=true, single=false}`；`Dependencies` 只有组件访问组，没有 before/after，
所以系统顺序只能来自注册顺序。

### D. 常量扫描 + xref

- 罕见 float/double（如 `0.028`、`0.026`）先全库扫，再对地址 `xrefs_to`。
- **立即数写入不会产生 xref**（`mov dword ptr [rax], 0BC23D70Ah`），
  “扫不到引用”不等于“不存在”，要看反编译上下文。

### E. 调用者/被调用者

`callees` 列出直接调用；虚调用只会出现 `__guard_dispatch_icall_fptr`，没有目标。

### F. 函数内指令扫描（比全段字节扫描可靠）

```python
for ea in idautils.Heads(func_start, func_end):
    if idc.print_insn_mnem(ea) == "call":
        ...
```

全段 `get_bytes` / `find_bytes` 会把 worker 搞崩；需要全段时用 4 MB 分块 +
`idc.get_item_head(ea) == ea` 校验指令边界。

---

## 4. 顺序/时序问题的做法

1. **先画数据流**：谁写这个字段、谁读这个字段。顺序是数据流之上的附加信息，数据流错了，
   顺序一定错。
2. 顺序结论必须落在**同一个执行体**（同一线程、同一 pass、同一函数链）里；
   观察点跨线程/跨 pass 的“先后”不算数。
3. 调用栈是首选证据，但要**能落到 dispatcher/调用者**；
   栈被截断时，先用可控手段加深（本轮把 `kMaxFrames` 提到 20 才看到
   `0x1463B0C27` = E14 主 dispatcher），再回静态。
4. 频率类结论（每 tick 跑几次、哪些 pass 存在）用**入口钩子 + tick 戳**，不靠猜。

**反例（本轮真实教训）**：8 帧观察点栈里前 4 帧是 VEH 噪声，只看得到
`setSprinting ← handler ← wrapper`，于是把“阻力写在写位之前”当成了调度顺序；
把栈加深到 20 帧后，链里出现 `0x1463B0C27`（E14 主 pass）与
`0x141177915`（`tickEntitySystems`），而集合实现（追加 + 正序）证明主 pass 内
ActionSprintTrigger 注册在先 ⇒ **写位先于阻力**。旧结论撤回。

---

## 5. 动态取证的正确用法（允许，但受限）

- 只用来取：调用栈、线程 id、命中 tick、某函数是否被调用、入口频率。
- 抓到的**运行时地址**必须换算回静态：`base` 每次启动都变（ASLR），
  用已知的静态 RIP 反算，或用 Toolhelp32 枚举模块取 `Minecraft.Windows.exe` 基址。
- 动态发现必须能回到静态解释（调度器实现、注册顺序、字段语义），否则只写“线索”。
- 输入注入前先 `settle`，一次只改一个变量；注入与游戏状态会竞争。

---

## 6. 踩坑清单

1. **IDA 库损坏**：headless `idat` 连续运行会写出 `Database is empty` /
   `internal error 1228`；下次打开报 “did not close properly”。用 MCP idalib 只读复用。
2. **磁盘**：开库解包数 GB；删之前必须退 supervisor + worker；不要复制 `.i64`。
3. **CFG thunk**：虚调用是 `call cs:__guard_dispatch_icall_fptr`，槽位在
   `mov reg, [reg+0x18]` 这类指令里；搜 `call [reg+18h]` 一定找不到。
4. **`insn_query` 的操作数只匹配立即数**，不能按内存位移或文本匹配。
5. **`RtlCaptureStackBackTrace` 前几帧是异常处理器噪声**；8 帧只剩 3 层真实调用者。
   现在 `watch_set(depth=…)` 可配（1..32，默认 8），但深度越大，VEH 每次命中越贵、
   环内存越大（32 帧约 20 MB）。
6. **钩子只有 4 个槽**（`no_free_slot`）；先规划钩谁，必要时先 `hook_rm`。
7. **VA 钩子要运行时地址**（`base + (static - 0x140000000)`），传静态 VA 会
   `not_executable`；`base` 用 Toolhelp32/psapi 枚举模块。
8. **观察点命中同一字节的任意写**：ADF 字节经常被 bit34、脏标记、XOR 同步写碰到；
   要按 `rip` + 位掩码区分，不能把“字节被写”当成“目标位被写”。
9. **环形缓冲不清空**：`watch_drain(since=0)` 会带回上一轮的旧命中（tick 号会跳）。
   每轮实验先 drain 到 `next` 作为基线。
10. **注入与锁存**：世界里 `state.input.sprint` 可能已经是 true；走/跑对照前
    `lab.settle(..., input_sprint=False)`。
11. **XboxGames 的 exe 普通用户读不了**（Python `PermissionError`）；只能经 IDA 读。
12. **反编译截断**：大函数 `decompile` 返回会被截断；写文件再读，`py_eval` 里写文件
    用绝对路径（IDA 进程 cwd 是安装目录）。
13. **类名不可靠**：没有 RTTI/符号时，靠字符串池、vtable 形状、字段偏移认类；
    不要把 `sub_XXXX` 当名字写进结论。
14. **版本数据只进 profile**：版本号、签名、结构偏移、组件 id 一律写
    `docs/offsets/<version>.json` 再生成头文件；`.cpp` 里不出现这些常量。
15. **立即数常量没有 xref**：扫描为空时先检查是不是立即数写入，再下“不存在”的结论。
16. **阈值/门限要读上下文**：例如 0.026/0.02 这种“看起来像 1.3 倍”的表，可能是另一个
    字段（基础加速度），不是最终乘数；最终值要沿数据流追到写入点。

---

## 7. 交付规范

- 每个结论：`[代码]/[常量]/[实测]/[推断]` + 地址 + 可复核路径（函数名/反编译行/命令）。
- 结论表之外单独维护“**已撤回**”表：旧结论、现状、依据。
- 详细地址与反编译放在 [notes/](notes/)（仓库文档只放可公开的结论与复核方法）。
- 改完机制相关代码/文档：离线单测 + 必要时 live selftest，并把限制/基线写回
  `mod/README.md`、`docs/mod-internals.md`。

---

## 8. 本轮新增的取证能力

`watch_set` 现在支持每观察点的栈深：

```python
w = client.watch_set(adf_addr, size=1, mode="w", thread="both", tag="adf", depth=20)
```

- `depth` 范围 1..32，默认 8；记录固定 32 槽，超出部分不写。
- 深度越大，VEH 每命中一次的回溯成本越高，环形缓冲占用越大（65536 条 × (48 + 8×depth) B）。
- CLI 暂未暴露 `--depth`（保持原 CLI 基线快照不变）；Python API 与管道协议已支持。
