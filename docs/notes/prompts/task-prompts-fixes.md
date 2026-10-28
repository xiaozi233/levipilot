# 开发任务提示词 · 第二轮（修复轮 F1–F10）

> 独立成册，便于单轮派活与 diff。**公共前置（仓库约定、架构硬约束、脚本纪律）见
> [`task-prompts.md`](task-prompts.md) §2**，每条任务派活时把那段一起粘上。
> 本轮的依据是对 T1–T9 落地实现的复核；F1–F4 为 P1，F5–F10 为 P2。

# 第二轮：修复轮（F1–F10）

> 依据：对已落地实现的复核（`Watch.cpp` / `Resolve.cpp` / `HookVa.cpp` / `Transport.cpp` /
> `RecordRing.cpp` 逐行读；`Memory.cpp`/`Ecs.cpp`/`Overhead.cpp` 由代码审查给出）。
> 每条仍然是「目标 / 背景 / 范围 / 要点 / 不要做 / 验收」，公共前置沿用上一节。
> **F1–F4 是 P1（先修）；F5–F10 是 P2（做完 P1 再排）。**

## 修复轮总览

| ID | 优先级 | 问题 | 位置 |
| --- | --- | --- | --- |
| F1 | P1 | 命令缓冲无上界 | `Transport.cpp` 追加处 |
| F2 | P1 | `hook_va` 安装窗口：origin 未发布时 detour 静默返回 0；持锁挂起线程 | `HookVa.cpp` 安装段 |
| F3 | P1 | `hook_va` 失败路径泄漏活钩子；无重复 VA 防护 | `HookVa.cpp` 失败分支 |
| F4 | P1 | `world_left` / `panic` 不落盘（与文档不符） | `ProbeMod.cpp` 事件、`Commands.cpp` panic |
| F5 | P2 | offsets 表只驱动签名，`structs`/`components` 无人消费 | `Resolve.cpp` / `Memory.cpp` / `Ecs.cpp` |
| F6 | P2 | spool 无 rotation；两线程 swap/write 顺序可交错 | `Transport.cpp` |
| F7 | P2 | watch：foreign 检测过严、`rip` 语义未说明、无门禁 | `Watch.cpp` |
| F8 | P2 | tick 路径三次 `getTypeName()` 分配 + 越界 `getBlock` 风险 | `RecordRing.cpp` |
| F9 | P2 | `tick_us` 实为 tick 间隔，非开销；读窗口可撕裂 | `Overhead.cpp` |
| F10 | P2 | 输入校验/卸载阻塞等防御性小项 | `Memory.cpp` / `ProbeMod.cpp` / `Commands.cpp` |

---

## F1（P1）给命令缓冲加上界

```text
目标：任何客户端输入都不能让 mod 的缓冲区无界增长。

背景：Transport.cpp 只在收到 '\n' 时消费 mCommandBuffer（append 后 while find('\n')），
连接时清一次；发一个不含换行的超长写入，缓冲区就会一直涨到 bad_alloc。
mQueue 有 20000 上限，这个没有。

范围：Transport.cpp 的读循环（append 与 find 之间）。

要点：
- 上限 1 MiB；超限时丢弃并回 {"event":"error","reason":"line_too_long","bytes":N}，
  然后 clear()，保持连接（不要断，方便客户端看到错误）。
- 已解析出的完整行照常处理，不要因为超限丢掉它们。

不要做：不要把上限做成可配置项再引入新配置面；写死 1 MiB 即可。

验收：
- 一次发 2 MiB 无换行 → 收到 line_too_long，进程 RSS 不持续增长，后续正常命令仍可用；
- 发 2 MiB 且有换行（多行拼在一起）→ 逐行正常处理，不报错；
- driver/selftest.py 加一条：发送超长行 → 断言收到 line_too_long 且随后 ping 仍通。
```

---

## F2（P1）修 `hook_va` 的安装窗口与挂起死锁

```text
目标：装钩的那一刻起，目标函数的语义必须保持正确；安装过程不能死锁。

背景：HookVa.cpp 的安装序列是
    ll::memory::hook(watch.target, watch.detour, &originRaw, Normal, true);   // 补丁已生效
    ... 直到 30 行后才 gGenericOrigins[slot] = originRaw;
而 detourGeneric 在 origin 为空时 return 0（静默给出假返回值）。
另外全局 gHookMutex 是持有状态下调用 hook()，而 hook() 内部会挂起所有其他线程；
若某个被挂起的线程正持有堆/Loader 锁，而 hook() 又要分配跳板内存，就死锁。
最后：主菜单（GamingStatus::Default）下 hook() 内部不挂起线程，补丁是在别的线程
可能正在执行的代码上直接改的。

范围：HookVa.cpp 的 install 段与 detour 模板；不改变对外命令形状。

要点：
1) 用 std::atomic<GenericFn> + std::atomic<bool> ready 发布 origin；
   detour 在 !ready 时有界自旋（YieldProcessor 若干万次；这是微秒级窗口），
   不要 return 0。所有退出路径（成功/失败/回滚）都必须把 ready 置位，
   失败时先在 ready 置位前 unhook 掉补丁，保证不会永久自旋。
2) 安装期间自己控制挂起：不要持 gHookMutex 调 hook()。
   建议流程：先在锁内占槽并标记 pending → 释放锁 → 自己 SuspendThread 全进程（或
   让 hook() 的 suspend=true 但把锁释放）→ hook(..., suspend=false) → 发布 origin+ready
   → 恢复线程 → 回填槽位为 used。任何一步失败都回滚并恢复线程。
3) 主菜单状态下拒绝安装：shouldHookSuspendThreads() 为 false 时回
   {"event":"error","reason":"not_in_world"}（或强制挂起并在事件里标注 forced）。

不要做：不要把 detour 改成「无 origin 时直接跳到 targetAddress」——补丁已在原处，会自环。

验收：
- 对一个每 tick 都调用的函数装钩：装钩后 100 次调用的返回值与未装钩时逐位一致
  （用 record 对比）；dry_run 仍然完全不改字节；
- 连续在同一函数上 install/panic 20 次，无死锁、无崩溃；
- 主菜单调用 hook_install → not_in_world，且 hook_list 为空。
```

---

## F3（P1）修 `hook_va` 的槽位生命周期

```text
目标：任何失败路径都不留「装上了但摘不掉」的钩子；同一地址不能被装两次。

背景：失败分支是
    if (status != 0 || originRaw == nullptr) { target = detour = nullptr; return hook_failed; }
当 status == 0（补丁成功）但 originRaw == nullptr 时，补丁已经在代码里，槽位却被标记为空，
之后 hook_rm / panic 都找不到它。（status != 0 那条分支本来就没装上，不是泄漏。）

范围：HookVa.cpp 的失败分支、findFreeSlot 之后的槽位占用、hookRemove/hookRemoveAll、captureHit。

要点：
- status == 0 && originRaw == nullptr → 先 ll::memory::unhook(target, detour, true)，
  成功后再释放槽位；unhook 失败则保留槽位并回 unhook_failed，让 panic 能重试。
- 安装前检查目标地址是否已在某个槽里（含已装钩的 trampoline 范围），是则回 hook_duplicate。
- captureHit 读到的 sample/id/tag 改成原子或安装期只读的不可变快照
  （detour 可能在发布 ready 之前就在别的线程跑起来）。
- hookRemove/hookRemoveAll 失败时保持 used，不要释放槽位（现在 Watch.cpp 已经这么做，照抄）。

验收：
- 同一 VA 连装两次 → 第二次 hook_duplicate，且第一次仍工作；
- 用测试钩子伪造「hook 成功但 origin 为空」（例如加一个仅测试用的 inject_fail 开关）→
  回 hook_failed 且 hook_list 为空、目标函数行为不变、panic 后无残留；
- 现有 125 项单测与 selftest 全绿。
```

---

## F4（P1）把 flush 接到 `world_left` 与 `panic`

```text
目标：世界退出与 panic 之后，磁盘上必须已经有此前的全部记录。

背景：flushSpool 的调用点只有：spool 线程、disable、spool_flush 命令、record stop、pipe 断开。
ClientExitLevelEvent 只 enqueue 了 world_left，panic 只卸钩/停任务 —— 都没有 flush。
panic 恰恰是用在"出事之前"，这里不落盘等于最需要证据的时候没有证据。

范围：ProbeMod.cpp 的 ClientExitLevelEvent 处理、Commands.cpp 的 panic 分支。

要点：
- world_left 的 enqueue 之后强制 flushSpool(true) 一次；panic 在所有清理完成后同样强制一次。
- 不要把阻塞文件 IO 放进 tick 线程：事件里只置一个 force 标志，由 spool 线程立刻处理；
  若事件本身就跑在管道/非 tick 线程，可以直接 flush。
- panic 的返回事件里带上 flush 结果（rows 或 ok=false），让调用方知道有没有落盘。

验收：
- record_start → 走几秒 → leave_world → 立刻 taskkill /F → 重启后 spool 里包含 world_left
  及其之前的全部 tick（允许丢最后 <=100 ms）；
- 装 watch+hook 后 panic → panic_done 里 flush 成功，spool 末行为 panic 相关事件；
- 文档（docs/architecture.md / AGENTS.md）与实际调用点一致。
```

---

## F5（P2）把 offsets 表真正接进代码

```text
目标：结构体偏移由 docs/offsets/<ver>.json 驱动，而不是散落在 .cpp 里。

背景：Resolve.cpp 只消费 anchors[].expected_hex；docs/offsets/*.json 里的 structs/components
没有任何代码读。Memory.cpp 的 probe 仍然写死 0x68 / 0xE0 / 0xE8 / 0x17E / 0x15C / 0x178。
后果：结构体布局变了时签名校验不会报警（签名只覆盖函数序言），probe 会静默读错。

范围：Resolve.cpp（加查询接口）、Memory.cpp（probe）、Ecs.cpp（组件尺寸/偏移）、docs/offsets/*.json。

要点：
- Resolve 暴露 structOffset("Block","mLegacyBlock") / ("BlockLegacy","mBlockId") 之类查询，
  以及 componentSize(id) / componentName(id)；查不到回明确错误，不要回默认 0 去猜。
- Memory.cpp 的 probe 全部改走查询；缺表项时回 {"event":"error","reason":"no_offset","field":"..."}。
- 把旧版本的 structs 填全（本轮实测值）：
  Block.mLegacyBlock=0x68；BlockLegacy.mName=0xE0（哈希）、mNameText=0xE8、mBlockId=0x17E（u16）、
  mFriction=0x15C、mFrictionGeneric=0x178。components 先把已知的气泡柱条目与 StateVector 填上。
- diagnose 里列出"已从表读取的字段数 / 缺表字段"。

不要做：不要在两处维护同一偏移（表 + 代码常量）；不要为缺失字段编默认值。

验收：
- 把 JSON 里 mBlockId 改成 0x17C → probe 读出的字段跟着变（证明真接线），改回后恢复；
- 删掉某个键 → probe 回 no_offset，不读错数据；
- diagnose 的"已接线字段"计数与代码里的使用点一致。
```

---

## F6（P2）spool 的顺序与轮转

```text
目标：并发 flush 时记录顺序单调；长时间运行不会长成无限大的单文件。

背景：flushSpool 先在 mQueueMutex 下 swap 数据，再在 mSpoolFileMutex 下写文件。
两个调用者（spool 线程、pipe 线程的 spool_flush）可以 A swap、B swap、B 写、A 写，
tick 行序会交错。另外文件以 ios::app 打开后从不轮转，PID 复用时新会话会追加进旧文件。

范围：Transport.cpp 的 flushSpool 与 spool 路径初始化。

要点：
- 让 spool 线程成为唯一写文件者；pipe 线程只置 mSpoolForce 标志并等一个 ack
  （或把 swap 与 write 放进同一把锁——二选一，但别留两个临界区）。
- 启动时若 <modDir>/spool-<pid>.jsonl 已存在且非空 → rename 成 spool-<pid>-<n>.jsonl
  （选第一个不冲突的 n），再新建；或按 64 MiB 切分。写失败时把 data 拼回缓冲并 reopen。
- spool_flush 的返回里带上文件名，方便调用方直接读。

验收：
- 两个线程各并发 flush 1000 次 → spool 里 tick 序号严格递增（脚本断言）；
- 重启游戏 5 次 → 每次生成新文件，旧文件保留且可读；
- 制造写失败（把目录设为只读）→ 数据回到缓冲、恢复可写后能补写，不丢。
```

---

## F7（P2）watch 的三个细节

```text
目标：观察点可用性更好，且上报数据能直接对上 IDA。

背景：
1) foreign 检测是 Dr7 & 0xFF & ~ownedSlotMask()，只要调试器占了任意一个槽就拒绝所有安装；
   应该只看目标槽的两个位。
2) 数据断点是 trap（指令执行后触发），所以上报的 rip 是写指令的下一条；现在事件里没有说明，
   对 IDA 核对时会差一条。
3) watch_set 没有配置门（mem_write / hook_va 都有），而且会挂起 tick 线程改 DR 寄存器。

范围：Watch.cpp 的 install/violation 处理与事件构造；Commands.cpp 的 watch_set；harness.json 配置。

要点：
- foreign 判断改为只检查 (Dr7 >> (2*slot)) & 3。
- 事件里加 code_hex：rip-16..rip 的 16 字节（离线解码即可定位写指令）；
  文档明确"rip 是跟随指令（下一条）"，或用 code_hex 让调用方自己算。
- 加 allow_watch 配置项（默认 true 也可，但必须出现在 harness.json 文档里）；
  watch_installed 事件里带 "suspends_tick_thread": true 作提示。

验收：
- 用一个自己占用 DR0 的测试钩子（或调试器）→ watch_set 到 slot1 仍能成功；占满 4 槽才回 no_free_slot；
- 对某字段设 watch 采样一次 → code_hex 里能用反汇编找到那条 store，且与 rip 相差 <=15 字节；
- harness.json 里 allow_watch=false 时 watch_set 被拒。
```

---

## F8（P2）记录环的 tick 路径不做字符串

```text
目标：tick 写入路径零分配，且不会因为越界坐标把游戏搞崩。

背景：RecordRing.cpp 每 tick 做三次 source.getBlock(BlockPos{...}).getTypeName() 再哈希，
每次 getTypeName 返回 std::string（三次分配）。这与"写入不做格式化/分配"的约定相反；
另外 fy-1 / hy 在世界上下界外时，Bedrock 的 getBlock 可能断言。

范围：RecordRing.cpp 的 recordTick 采样段。

要点：
- 用方块数字 id（BlockLegacy 的 mBlockId，见 F5 的表）或按坐标缓存的哈希，替代 getTypeName；
  需要名字哈希时在 dump 阶段（非 tick 线程）换算。
- 坐标越界时写 0 并置一个 flags 位，不要调用 getBlock。
- 若必须保留名字哈希语义，做一个小 LRU（预分配、无分配）缓存 pos→hash。

验收：
- 连续记录 10 分钟 RSS 平稳（与改前对比给出数字）；
- 在全球高度上限（及下限）各记录 30 秒不崩、不 assert；
- 记录里的 block_below/feet/head 与 blocks 命令读回值一致。
```

---

## F9（P2）开销指标的语义与一致性

```text
目标：status 里的数字名副其实，读窗口不被写者撕裂。

背景：tick_us 是 qpcElapsedUs(mPrevTickStartQpc, mTickStartQpc)，即相邻 tick 的间隔
（20 TPS 下约 50000 µs），不是钩子开销；calls_per_sec 与 tick_hz 重复。
读侧在环回绕时可能拿到混合样本。

范围：Overhead.cpp、ProbeMod.cpp 的喂入点、status 事件。

要点：
- 要么把 tick_us 改名 tick_interval_us 并保留（它本身有用），要么改成真正的 pre+post 钩子耗时；
  hook_us 保持为钩子体耗时。补 qpc_resolution_ns 与样本窗口大小。
- 读侧加 seq/epoch 守卫（与 Watch/HookVa 的 seq 发布一致），或做双缓冲快照。
- 保持 status 里原有字段名不删（调用方可能在用），新增字段即可。

验收：
- selftest_busy 注入 1 ms 忙等 → hook_us p99 明显抬升，而 tick_interval_us 基本不变（两字段语义可分）；
- 连续调用 status 1000 次与记录并存 → 无异常值（如 p99 < p50 或超过窗口范围）；
- 文档里每个字段一句话说明。
```

---

## F10（P2）防御性小项

```text
目标：把零散的输入校验与卸载阻塞补齐，避免以后踩。

范围（逐条，都是小改）：
1) Memory.cpp：parseAddress 拒绝前导 '-'（现在 "-1" 会经 strtoull 变成 UINT64_MAX）；
   任何 addr + <字段偏移> 之前检查 addr > UINT64_MAX - off（现在只查 addr+len 溢出）。
2) ProbeMod.cpp disable()：先 CancelSynchronousIo(pipeThreadHandle)（或改 overlapped 写）再 join；
   否则客户端连上但不读时，卸载会卡在 WriteFile 上。
3) Commands.cpp：selftest_busy 加配置门（allow_selftest_stress，默认关），
   因为它能让 tick 卡近 1 秒。
4) Commands.cpp：gDispatchSehActive 改成保存/恢复（bool prev = ...; ... = prev;），
   为将来嵌套分发留余地；给 dispatchLine 加 __declspec(noinline) 防 LTCG 触发 C2712。
5) 只改上面四处，不要顺手改其它 handler。

验收：
- mem_read 传 "-1" → bad_addr；
- 客户端连接后停止读取 → leave_world/panic/disable 在 2 秒内完成；
- allow_selftest_stress=false 时 selftest_busy 被拒；
- 现有单测与 selftest 全绿。
```

---

## 修复轮的回归门禁（每次提交都跑）

```bash
py -3.11 -m unittest discover -s tests -v      # 现有 125 项必须全绿
py -3.11 -m ruff check driver tests && py -3.11 -m mypy
cd mod/probe && xmake -y
py -3.11 driver/selftest.py                    # 需要游戏
```

新增用例至少要覆盖这三条"出错路径"（现在恰好没测）：
1) `hook_va` 装钩失败后的槽位与字节回滚；
2) `watch_set` 在槽位被占/线程已退出时的回滚；
3) `panic` 之后的 spool 落盘。

改完在 `mod/README.md` 的「实测 / 限制」补一行，并把 `AGENTS.md` 的已知坑同步。
