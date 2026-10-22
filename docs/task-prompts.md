# 开发任务提示词（LeviPilot / mod 优先）

> 用途：把「补能力」拆成可逐条派给 agent 的独立任务。每条提示词自带背景、范围、实现要点与验收，
> 派活时只需在开头粘上本文的 **公共前置**，再把对应任务整块粘过去。
> 生成依据：对当前实现的代码级review（`mod/probe/src/mod/*`、`driver/harness/*`、`docs/*`），
> 重点是「纯 frida 路线踩过的坑」在 mod 路线上的映射与残余缺口。

## 0. 分工判据（什么该进 mod）

- **进 mod**：进程内、每 tick 需要、或需要零延迟反馈的能力（状态/输入/观察点/内存原语/记录环/封包 tap）。
- **留 Python**：编排、场景 DSL、macro、跑酷分析、断言与报告。
- **留 IDA**：静态发现（常量、xref、结构体布局）。mod 不应抢它的活，也不要硬编码它算出来的 VA。

## 1. 任务总览

| ID | 优先级 | 任务 | 依赖 |
| --- | --- | --- | --- |
| T1 | P0 | 崩溃取证 + tick 数据落盘 | — |
| T2 | P0 | 通用内存原语 mem_read / mem_write / probe | T1（错误路径） |
| T3 | P0 | 硬件观察点 watch（谁写了这个字段） | T2（地址校验）、T8（卸载） |
| T4 | P1 | 二进制环形记录通道 | T1 |
| T5 | P1 | 按 VA 装钩 hook_va | T4（命中缓冲）、T7（地址来源）、T8 |
| T6 | P1 | ECS / registry 内省 | T2、T7（组件表） |
| T7 | P2 | 版本指纹 + 符号/偏移解析器 | — |
| T8 | P2 | unhook_all / panic（可摘钩） | T3、T5 |
| T9 | P3 | tick 路径减锁 + 开销自测 | 无强依赖 |

**只做三件**：T1 → T2 → T3。做完即消除「游戏一崩全没了」与「谁写了这个字段」两个最痛的问题。

## 2. 公共前置（每条任务都要带上）

```text
仓库 .（LeviPilot）：LeviLamina 客户端 mod（mod/probe/src/mod/*.cpp）+
\\.\pipe\mcbe-harness 的 UTF-8 JSONL 协议（protocol 3）+ Python 客户端（driver/harness/）。
目标版本固定 Minecraft Bedrock 1.26.51.01 / LeviLamina 26.51.7；改完必须 xmake -y 通过。

必须遵守的既有架构约束：
- Internal.h 是唯一被所有 TU 引用的内部头；新共享工具放 probe::detail。
- 管道线程绝不直接碰游戏对象：只设请求标志或 queueServerTask()。
- 需要逐 tick 反馈的逻辑留在游戏线程（safeTickPre / safeTickPost 内），不要让 Python 进反馈环。
- 热路径（tick 内、任何可能被高频调用的钩子体）里禁止 std::string / nlohmann::json / 动态分配 / 新增 mutex。
- 错误一律变成事件（internal_error 等），不能让异常穿过游戏 tick；新增命令要加 tests/ 里的无游戏单测，
  并在 driver/selftest.py 加一条需要游戏的检查。
- 协议只做增量变更：hello 的 protocol 号升到 4 时，旧字段必须继续可用；未知命令仍回 error/unknown_cmd。
- 禁止把 1.26.45.01 的硬编码 VA 写进代码；锚点一律走符号或签名解析（见 T7）。
```

---

## T1（P0）崩溃取证 + tick 数据落盘 —— 修掉目前唯一「比 frida 更差」的地方

```text
目标：让 tick/事件记录在游戏崩溃时不丢；崩溃时留下可诊断的现场。当前最高优先级。

背景（为什么）：tick 记录只存在内存 std::deque<std::string>（ProbeMod.h 的 mQueue，上限 20000，
超出丢最旧，计数在 status.queue_dropped），管道线程只有客户端连上才排空，且 Transport.cpp 的
连接处理会清空队列。后果：跑长实验时游戏一崩，全部记录消失。旧 frida 路线是脚本内持续
File.write+flush，进程死后 3.5 万行记录仍完整 —— 这个能力现在退化了。

范围：Transport.cpp（队列与写入）、ProbeMod.cpp 的 safeTickPost 记录块与 enable/disable、
Commands.cpp 的 dispatchLine 外层、mod 初始化处。

实现要点：
1) Spool：记录同时 append 到 <modDir>/spool-<pid>.jsonl（或 .binl，配合 T4）；带缓冲写入器，
   每 100 ms 或每 64 KB flush，并在 world_left / stop / disable 时强制 flush。
2) connect 时不要清空 mQueue：先把旧内容 rotate 进 spool 再开始发送，并回一个事件告知 replay_rows。
   队列溢出策略由「丢最旧」改为「丢最旧并计入 spool 的 dropped 计数」。
3) SetUnhandledExceptionFilter + dbghelp MiniDumpWriteDump(MiniDumpNormal|WithThreadInfo) 写
   crash-<pid>-<tick>.dmp；同目录写 crash-<pid>.txt：最后命令、最后 tick 序号、最后 state、
   queue_dropped、spool 路径。
4) dispatchLine 每个 handler 外层包 __try/__except（或整个 mod 编译加 /EHa），使一次野指针
   只让该请求返回 internal_error，而不是带走进程。

不要做：不要改现有 event 的字段名；spool 必须写在 <modDir>，不得写进游戏安装目录。

验收：
- 录制中 taskkill /F 掉游戏 → 重启后 spool 能读到崩溃前最后 ≤100 ms 的记录；
- crash-<pid>.dmp 与 .txt 都生成，.txt 四个上下文字段齐全；
- 仅测试用命令故意解引用空指针 → 返回 internal_error，游戏继续跑、tick 不中断；
- tests/ 增加 spool rotate 与 queue-overflow 单测。
```

---

## T2（P0）通用内存原语：mem_read / mem_write / probe

```text
目标：让 harness 能读活对象（Block* / BlockLegacy* / 组件原始字节 / 任意地址），
补上旧 frida 分支 blockAt / inspect 那部分能力（主线已删）。这是所有逆向类功能的地基。

背景：现在 mod/probe/src/mod 里连一个十六进制字面量都没有，blockName 只回字符串
（Internal.cpp 里 source.getBlock(pos).getTypeName()），拿不到指针与哈希，
无法做「从指针读活对象」。

范围：新增 Memory.cpp/.hpp；Commands.cpp 注册命令；driver/harness/client.py + cli.py 加方法。

实现要点：
- mem_read {addr, len} → 返回 hex；len 上限 4096；每次访问用 __try/__except 包裹。
- mem_write {addr, len, hex}：默认禁用，需 harness.json 的 allow_write=true；同样 SEH 包裹，并留单行审计日志。
- probe {kind, addr}：结构化读取，至少支持
    kind=block  → Block+0x68 取 BlockLegacy*（同时返回该指针值）
    kind=legacy → +0xE0 u64 名字哈希、+0xE8 名字 std::string、+0x17E u16 mBlockId、
                  +0x15C / +0x178 两个 float
    kind=raw    → 同 mem_read 但给出 u8/u16/u32/u64/float 解析视图
  所有字段用逻辑名，偏移集中在 T7 的 offsets 表里，不要在 handler 里散写数字。
- 地址校验：VirtualQuery 确认落在已提交可读页，否则回 bad_addr（不要崩）。

不要做：不要把写能力作为默认路径；mem_write 必须显式开启。

验收：
- probe kind=block 对同一坐标返回的名字与现有 blocks 命令一致；legacy 的哈希等于该名字的 FNV-1-64；
- 对非法地址、已释放页各调用一次都回 bad_addr 且游戏存活；
- allow_write=false 时 mem_write 回拒绝事件。
```

---

## T3（P0）硬件观察点 watch —— 回答「谁写了这个字段」

```text
目标：对任意地址设写观察点，命中时报告 rip / 线程 / 栈，替代 frida 缺失的
Thread.setHardwareWatchpoint（旧路线因此只能靠「实测值→常量反查」的迂回手法）。

范围：新增 Watch.cpp/.hpp；与 T2 共用地址校验；事件走管道（建议配合 T4 的二进制环）。

实现要点：
- watch_set {addr, size, mode, thread, tag} → watch_id，size ∈ 1/2/4/8，mode ∈ w/rw，
  thread = "tick" 或具体 tid；watch_clear {id} / watch_list。
- 用调试寄存器 DR0–DR3（每线程 4 槽）+ DR7 的 L/G 位；安装前 SuspendThread + GetThreadContext，
  先读 DR7 检测是否被占用（被调试器占用则回 dr_busy，不要强抢）。
- AddVectoredExceptionHandler 捕 EXCEPTION_SINGLE_STEP；命中时清 DR6 对应位，只把
  {tag, rip, tid, tick, RtlCaptureStackBackTrace 8 帧} 写进预分配环形缓冲；
  命中路径内禁止 JSON/字符串/加锁，由管道线程批量取走并序列化。
- 环大小固定（如 65536 条），溢出计数进 status；提供 watch_drain {since}。
- 默认只装 tick 线程；"all" 模式需显式开启。

不要做：不要对全进程设单步；不要把 DR 用于实现 hook（hook 见 T5）。

验收：
- 对玩家 StateVector 的 vy 字段设 watch（size=4, mode=w），走/跳 30 秒 → 捕获 ≥1 条 hit，
  且 rip 落在反汇编确认的 vy 写者函数内（用 IDA 侧符号表核对，不许猜）；
- 设 4 个观察点后 status 里 tick 仍稳定 20 tps，watch 环无丢失；
- 清除后 DR7 恢复为安装前的值（打印前后值作证据）。
```

---

## T4（P1）二进制环形记录通道（替代 string deque）

```text
目标：把 tick/观察点/hook 记录从 std::string + JSON 改成定长二进制环形缓冲：
零分配、可回放、与 T1 的 spool 打通。

范围：新增 RecordRing.h/.cpp；改造 safeTickPost 的记录块；driver 侧新增 driver/harness/record.py 解码。

实现要点：
- 定长 struct（例如 64 B：tick u64、pos[3] f32、vel[3] f32、yaw/pitch f32、flags u32、
  block ids u32×3、pad）；预分配 N 条（如 1<<20），atomic head/tail；写入不做任何格式化。
- 命令：record_start {kind, period(每 N tick), ring} / record_stop /
  record_dump {since, format: bin|jsonl}；record_dump 按字节块写管道（4 字节长度前缀 + payload）。
- hello 增加 capabilities（如 bin_record / watch / hook_va），protocol 升 4；旧的 event:"tick"
  JSON 路径保留为 compat 开关。
- 与 T1 打通：record_dump 同时 append 到 spool。

验收：
- 连续记录 10 分钟：RSS 平稳、status.queue_dropped=0、Python 解码行数与 mod 计数一致；
- 关闭记录时该项零开销（同场景 tick 时长 p99 无劣化）。
```

---

## T5（P1）按 VA 装钩 hook_va（逆向观察用）

```text
目标：允许按运行时地址装 enter 型 detour，捕获寄存器快照，不必每次改代码重编译。

范围：新增 HookVa.cpp/.hpp；复用 LeviLamina 的动态 hook 能力；命令与 client 封装。

实现要点：
- hook_va {va, tag, capture:[rax,rdx,r8,r9,rsp], sample:1|N} → hook_id；hook_rm {id} / hook_list。
- 只允许装在已提交可执行页内；安装前校验地址是合法指令边界（由调用方从 IDA 传入，模组侧只做基本 sanity）。
- 命中路径只写定长快照进 T4 的环（含 sample 降采样），绝不格式化。
- dry_run=true：只校验地址/可执行性并回结果，不落钩。
- 与 T8 的 panic 联动：panic(level="hooks") 必须能全部卸掉。

不要做：不要自动扫描/自动找地址；不要在未给 sample 的情况下装到已知高频点
（默认拒装，要求显式 sample>=10 并给出理由字段）。

验收：
- 装到气泡柱推力入口的 51 版地址：进柱后捕获到寄存器（rdx/r8 指向同一对象），
  hook_list 显示 installed；卸钩后同场景 0 命中；
- 装错地址（落在指令中间或只读页）→ 回 hook_rejected 且游戏存活。
```

---

## T6（P1）ECS / registry 内省

```text
目标：能枚举 registry、实体、组件与元素字节，补上旧 frida 的 inspect_registries。

范围：新增 Ecs.cpp/.hpp；命令 + client。

实现要点：
- ecs_registries → registry 指针列表（区分客户端/服务端：有 LocalPlayerComponent 的是客户端）。
- ecs_view {registry, component_ids} → 实体 id 列表。
- ecs_component {registry, entity, component_id} → 原始字节（长度由组件表给出）+ 可选结构名。
- 组件 id → 名字/尺寸 表放 T7 的 offsets 文件；没有表项时只回字节与 stride。
- 服务端 registry 的操作仍必须经 queueServerTask。

验收：
- 能列出气泡柱实体上 24 B 的 vector 组件与 36 B 的 StateVectorComponent，元素 stride（20/36）
  与静态逆向一致；与 T5 钩到的 push 入口条目交叉一致；
- 空 registry / 不存在实体回 not_found，不崩。
```

---

## T7（P2）版本指纹 + 符号/偏移解析器

```text
目标：版本对不上时「明确拒绝」而不是「静默错钩后崩溃」；把 45 版积累的偏移变成可复用表。

范围：新增 docs/offsets/<version>.json；新增 Resolve.cpp/.hpp；hello/status/diagnose 增加字段。

实现要点：
- offsets 文件结构：
  {version, build_id,
   anchors:{name:{kind:"symbol"|"sigscan"|"offset", value, sig}},
   structs:{Block:{mLegacyBlock:0x68}, BlockLegacy:{mName:0xE0, mBlockId:0x17E, ...}},
   components:{...}}
- 启动时按 anchors 解析，任一关键锚点失败 → enable 中止，hello 带 version_mismatch + 失败清单。
- 支持至少 3 段字节签名校验（旧 frida 路线有 24 字节校验，思路照搬）。
- diagnose 输出每个锚点的解析方式与耗时；status 输出 build_id。

不要做：不要把某版本的 VA 写死在 .cpp（包括注释里的「历史地址」也不要当常量用）。

验收：
- 人为改 exe 一个签名字节 → mod 拒绝 enable 并指出哪个锚点不匹配；
- 正常版本下 anchors 全部 resolved，status.build_id 与 exe 一致；
- 把 1.26.45.01 的偏移表填进 docs/offsets/1.26.45.01.json（只作数据，不参与本次构建）。
```

---

## T8（P2）unhook_all / panic —— 出问题立刻卸钩，不杀游戏

```text
目标：对位 frida 时代「杀驱动留僵尸会话、只能重启游戏」的痛点：能在进程内优雅停用内省。

范围：ProbeMod 的 HookRegistrar 生命周期（现在是文件级 static，ProbeMod.cpp 末尾）、Commands.cpp。

实现要点：
- 把两个静态 HookRegistrar 改成 ProbeMod 成员：enable 时装、disable/panic 时 unhook。
- panic {level}：level ∈ hooks / tasks / all。hooks 卸掉 T2/T3/T5 装的东西并清空观察点；
  tasks 取消闭环任务并清输入；all 两者都做。
- 每次 panic 回 panic_done {hooks_removed, tasks_cancelled, dr7_restored}。

验收：
- 装了 watch + hook_va 后 panic → status 显示 hooks=0、DR7 回到初始值、游戏继续跑、
  tick 计数继续增长；之后还能重新装钩；
- 不 panic 时行为与现在完全一致（回归 selftest 全绿）。
```

---

## T9（P3）tick 路径减锁 + 开销自测

```text
目标：为将来敢钩高频路径铺路，并让「这钩子贵不贵」变成可观测数字，而不是靠游戏崩不崩判断。

背景：现在每个 tick 在 safeTickPre/safeTickPost 里要过 4–6 个 mutex
（mPlayerName/mTeleport/mInput/mLook/mTask）。20 tps 下无所谓，但一旦有人加高频钩子就是瓶颈；
而且目前没有任何开销测量，一次事故（同 tick 上万次回调）只能靠进程消失来发现。

实现要点：
- 请求块 seqlock：管道线程写一块 POD 请求结构 + 原子序号，tick 线程读一次原子快照，
  至少把 mInput/mLook/mTeleport 合成一块。
- hook 体首尾 RDTSC，维护 p50/p99/max（固定窗口，如 1024 样本，环形原子）；
  status.overhead 输出 {hook_p50, hook_p99, tick_p50, tick_p99, calls_per_sec}。
- 给 record / watch / hook 三条路径各自的 calls/sec 计数，便于发现异常频率。

验收：
- 同场景改前/改后 tick 时长 p99 不劣化（给出数字对比）；
- 人为在 hook 里加一次 1 ms 忙等，status.overhead 的 p99 能明显反映出来（证明可观测）。
```

---

## 3. 两条贯穿性纪律

1. **强能力默认关闭**：`mem_write` 与任意 `hook_va` 都必须显式开启并留单行审计日志。
   这既是安全边界，也是以后排查「到底是不是我在动内存」的唯一线索。
2. **热路径只写定长数据**：任何可能在 tick 内高频执行的代码，只允许写预分配的定长结构；
   格式化、字符串、JSON、加锁一律放到管道线程或 Python 侧。
   （纯 frida 路线曾因为 13 k/s 的 JS 回调让游戏进程退出；原生 detour 更便宜，但纪律必须提前立好。）
