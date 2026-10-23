# Mod 实现要点

面向维护者。用户 API 见 [`../mod/README.md`](../mod/README.md)。

## 注入时机

- Hook `LocalPlayer::$normalTick`（LL 实例 hook），在 `origin()` **之前**注入输入、之后采样。
  这与 dmf-mpk 的 macro 注入时机一致；`ClientLevelTickEvent` 偏晚，跳跃/按键会被覆盖。
- 移动：`ClientMoveInputHandler::_updateClientMoveVector(strafe, forward, ...)`
- 视角：`LocalPlayer::_applyTurnDelta(Vec2{pitchDelta, yawDelta})`
  （pitch 符号与 `ActorRotation.x` 相反）
- 按钮：`MoveInputComponent` 标志 + `InputEventQueue::enqueueButton` + 输入 handler 回调；
  `input_cfg` 的 `mode` 位掩码：1=直接标志，2=队列，4=handler，8=toggle fallback。
- 跳跃：只靠上面的输入管道会等服务器确认——实测前 3 tick 位置不动，第 4 tick 一次性补
  `V1+V2+V3 ≈ 0.99` 格，看起来像卡回弹。起跳沿必须额外调用
  `LocalPlayer::jumpFromGround()`，之后逐 tick 的位移是 0.4200、0.3332、0.2481、0.1648…
  （顶点 1.252 格，滞空 11 tick）。`MobJump::setJumpPendingScale` 单独用仍是延迟的
  （dmf-mpk 报告里它只是辅助）。`input_cfg jump_impulse`：1=仅 jumpFromGround（默认），
  2=setJumpPendingScale，3=两者，0=都不做（对比用）。
- 潜行：`LocalPlayer::setSneaking(true)` + `MoveInputComponent` 标志；实测 20 tick 前进
  4.316 格，潜行 1.295 格（约 0.30x 速度）。
- 状态：`LocalPlayer` + `MoveInputComponent` + `PlayerActionComponent` + 属性 +
  `BlockSource` + `Level`。

## 服务端权威操作

- **传送**：`ServerInstance::queueForServerThread` → 按 UUID/名字找到服务端 `Player` →
  `Actor::teleport(pos, dimension, rotation)`。坐标是脚部。
- **飞行**：客户端和服务端玩家同时 `setAbility(MayFly/Flying)`；只写客户端时，传送后
  服务端 abilities 会把 `flying` 同步回来。
- **命令**：服务器线程 + `PlayerCommandOrigin` + `MinecraftCommands::executeCommand`，
  保留 `@s` / `~` 语义。方块类命令是 deferred，`success=false` 只表示“已提交/延迟执行”。
- **快捷栏**：在 tick 之后调用 `PlayerInventory::selectSlot(slot, ContainerID::Inventory)`；
  放在 tick 之前会被原 tick 覆盖。

## 物品栏

- 读：客户端 `LocalPlayer` 的 `Inventory`（slots）、`getArmor(ArmorSlot)`、`getOffhandSlot()`，
  在 tick 之后采样。
- 写：服务器线程拿到服务端 `Player`，用 `Inventory::setItem` / `Player::setArmor` /
  `setOffhandSlot` 修改，再 `sendInventory(true)` 同步回客户端。
- 物品解析：`Level::getItemRegistry().lookupByName(aux, name)` →
  `ItemStack::reinit(item, count, aux)`；名字找不到返回 `unknown_item`。
- `give` 用 `Inventory::add`；`clear_inventory` 逐个槽位写空 stack。

## 实体

- 按 `runtime_id` 在 `Level::getRuntimeActorList()` 里查找目标；查询/攻击/交互都在客户端 tick 中执行。
- `attack_entity` → `GameMode::attack(actor, actor->getHeadPos())`
- `interact_entity` → 转向实体 + 注入主手使用按钮（3 tick）。**不要**直接调用
  `Player::interact` / `GameMode::interact`：空 item 上下文会触发 `[rsi+0x1D0]`（RSI=0）
  崩溃。输入注入是 best effort，是否产生实际交互取决于手持物和瞄准。
- `look_at_entity` 用玩家头部到实体头部的向量算 yaw/pitch：
  `yaw = atan2(-dx, dz)`、`pitch = -atan2(dy, horizontal)`，再走 `_applyTurnDelta`。

## Macro

- 文件格式与 dmf-mpk 一致：每 tick 9 或 11 个字符串
  （`W,A,S,D,Sprint,Sneak,Jump,RMB,LMB,Yaw,Pitch`），保存时角度用 `%.9g`。
- 播放：每 tick 一行；按键按边沿；RMB/LMB 在行边沿直接走
  `traceRay + GameMode`（左键破坏、右键放置）。
- 录制：WASD/Sprint/Sneak/Jump 来自输入状态，Yaw/Pitch 由 `ActorRotation` 差分；
  RMB/LMB 由 `MouseInputEvent` 记录。

## 鼠标录制

- `MouseInputEvent::actionButtonId()` 是 `MouseAction`：`ActionLeft=1`、`ActionRight=2`
  （不是 0/1）；`buttonData` 的 `DataDown=1`。
- 按键同时做**事件锁存**和 `PlayerActionComponent` 动作位上升沿，短点击不会落在两个
  tick 之间被漏掉。
- 只认 raw input：CUA 的合成点击在后台只产生 `ActionMove`；要录按键，游戏窗口必须在
  前台（或使用真实鼠标）。
- `mouse` / `mouse_clear` 可查看各 MouseAction 的计数，用于排查。

## 调试命令

| 命令 | 用途 |
| --- | --- |
| `diag` | `Minecraft` / `ServerInstance` / 服务端 `Level` / `MinecraftCommands` 是否可见 |
| `mouse` | `MouseInputEvent` 总数、最后 action/data、每个 MouseAction 的计数 |
| `mouse_clear` | 清空鼠标计数和按住的电平状态 |
| `buttons` | 列出已注册的输入按钮 ID |
| `input_cfg` | `mode/focus/source/controller` 注入模式 |

## state 来源

`state` 的 `x,y,z` 来自 `Actor::getPosition()`（对 LocalPlayer 是眼睛位置）；
`feet_x/y/z` 和 `head_x/y/z` 分别是脚部/头部。`teleport` 和 DSL 的 `tp` 用**脚部**坐标。
`input.*` 来自 `ClientMoveInputHandler`，`action_sprint` 是
`PlayerActionComponent` 的 bit 9。

## 闭环任务（navigate / mine / place / use）

这些任务在 `LocalPlayer::$normalTick` 的前置钩子里推进，而不是由 Python 每 tick 发一条命令。
管道往返（约 1–5 ms）因此不进入反馈环，行为在 20 TPS 下是确定的。同一时间只允许一个任务；
启动新任务会清空手动输入、视角保持和 macro 播放。

- **navigate**：每 tick 计算目标方位角，`_applyTurnDelta` 限幅 35°/tick，`_updateClientMoveVector`
  给前进量；连续 5 tick 水平位移 < 0.02 且在地面时触发一次跳跃；到 `tolerance` 或
  `max_ticks` 结束，回报 `navigated`（`reason=arrived/timeout`）。
- **mine**：先用 `traceRay` 确定面（射线没命中时用视线主轴兜底），首 tick 调
  `GameMode::startDestroyBlock`，之后每 tick 调 `continueDestroyBlock`，并以方块变为空气
  作为完成条件。连续 40 tick 返回失败才判定 `cannot_continue`，因为部分方块不是每 tick
  都报告进度。
- **place**：分三阶段——瞄准、点击、回读。目标格必须是空气；支撑面优先选脚下，再选四周，
  最后选上方，并检查面法线与“眼睛→支撑块”向量同向。点击用真实的 `traceRay` 结果调用
  `GameMode::useItemOn`，然后等 2 tick 回读目标格，避免把预测放置当成成功。
- **use**：等待 `ticks` 后在**服务端玩家**上调用
  `Item::use(stack, player, hand)`（投掷/饮用等瞬时动作）；食物再调用
  `Item::useTimeDepleted` 并 `Player::useItem(method, consume=true)` 完成消耗。
  完成后 `sendInventory(true)` 把服务端层数推回客户端。客户端直接调用只会产生预测，
  背包和效果真正落库的是服务端玩家。这条路径不做事前可用性判断。

## 世界与设置

- `worlds`：`IMinecraftGame::getLevelListCache().getLevelList(...)`，在管道线程直接调用即可。
- `enter_world`：按名字或 id 取 `LevelSummary`，用它构造 `LevelSettings`，再调用
  `IGameServerStartup::startLocalServerAsync(levelId, levelName, contentIdentity, settings, intent)`。
  `startLocalServerAsyncFromSummary` 没有被 LeviLamina 导出，直接链接会报 LNK2019；纯虚重载
  走 vtable。实测 `intent=0` 能从主菜单进"超平坦测试"。
- **登录门**：世界列表来自本地缓存，可能早于异步 Xbox Live 登录；这时启动本地服务器会弹出
  "你必须通过 Microsoft 服务身份验证"。进入前通过 `SceneProvider → FacetRegistryFactory →
  ScreenModelProvider::getMainMenuScreenModel() → isSignedInToXBL()` 查询，未登录则轮询等待
  `signin_wait_ms`（默认 15 s），仍未登录回 `signin_required`（`force=true` 可跳过）。
  join 结果用 `enter_world_settled` 通知，超时/取消回 `ok=false` 和原因。
- `leave_world`：`IGameServerShutdown::requestLeaveGame(true, true)`。
- 世界事件：`ClientStartJoinLevelEvent` → `world_joining`，`ClientJoinLevelEvent` →
  `world_entered`，`ClientExitLevelEvent` → `world_left`，`ClientCancelJoinLevelEvent` →
  `world_join_cancelled`。
- `options_set`：`IOptionRegistry::loadOptionsFromString` 实时应用（gamma/FOV 立刻可见），
  然后自行更新 `options.txt`。游戏自带的 `saveIfNeeded` 在当前版本不会立刻写盘，所以
  mod 读改写同一个文件（保留 CRLF 和未修改的行），再 `MoveFileExW` 原子替换。
- `harness.json`：`auto_enter` / `options` / `delay_ms`。自动线程先等客户端存在且世界列表
  非空，再应用选项并进世界，避免在加载阶段调用。

## 记录落盘与崩溃取证

- `enqueue()` 同时把同一行追加到内存缓冲；独立的 spool 线程每 25 ms 检查，按
  “距上次 ≥100 ms 或 ≥64 KB”把缓冲 swap 出来写进 `spool-<pid>.jsonl`。写文件时用
  `ofstream::flush()`，进程被 `taskkill /F` 杀死时 OS 缓冲仍然落盘。
- 连接建立时不再清空 `mQueue`：先强制刷 spool，再把积压行补发给新客户端；
  `hello.replay_rows` 告诉客户端有多少行。队列溢出仍丢最旧，但会往 spool 追加一条
  `{"event":"queue_dropped","count":N}` 标记。
- 崩溃取证走 VEH（`AddVectoredExceptionHandler`），因为 LeviLamina/游戏可能在 mod 加载后
  覆盖 `SetUnhandledExceptionFilter`。致命结构化异常（AV、非法指令、栈溢出等）且不属于
  `dispatchWithSeh` 时，写：
  - `crash-<pid>-<tick>.dmp`：`MiniDumpNormal | MiniDumpWithThreadInfo`；
  - `crash-<pid>.txt`：异常码/地址/线程/tick/最后命令/最后位置/`queue_dropped`/spool 路径。
  写 dump 用 `mCrashDumpInProgress` 防重入，全程只用固定缓冲和 Win32 调用。
- `selftest_crash` 用于验证这条路径，必须显式打开 `allow_crash_test`，因为它会结束进程。

## 内存原语（T2）

- 读写都走 `ReadProcessMemory` / `WriteProcessMemory`（当前进程）：非法地址返回失败而不是
  触发 AV，所以脚本传错地址不会带走游戏。外层还有 `VirtualQuery` 检查
  `MEM_COMMIT`、可读/可写和保护位（含 `PAGE_GUARD`）。
- `probe kind=block`：读 `Block+0x68` 得到 `BlockLegacy*`；`kind=legacy` 读
  `+0xE0`（哈希）、`+0xE8`（MSVC `std::string`）、`+0x17E`（u16 block id）、
  `+0x15C`/`+0x178`（float）。偏移集中在 `Memory.cpp` 的 `BlockLayout` /
  `BlockLegacyLayout`，命令处理里不出现裸数字。
- `blocks` 每个结果附带 `addr`（`Block*`），这样 `probe` 可以直接对活对象回读，
  名字与 `blocks` 交叉校验。
- `mem_write` 默认关闭；开启后每次写 1 KB 以内的十六进制摘要写进 `harness-boot.log`。

## 硬件观察点（T3）

- 每个线程 4 个 DR 槽；`watch_set` 全局分配槽位（所有线程共用同一个 slot 编号，
  DR6 的位号才能唯一映射回 watch）。安装前 `SuspendThread` + `GetThreadContext`，
  如果 DR7 的低 8 位里有不属于我们的 enable 位，回 `dr_busy`，不抢调试器。
- `watch_set` 默认 `thread="tick"`（`safeTickPost` 记录 `mTickThreadId`）；也接受具体
  tid 或 `"all"`（`CreateToolhelp32Snapshot` 枚举本进程线程）。
- 命中由 VEH 处理：清 DR6 对应位，把 `{watchId, tid, tick, rip, 8 帧栈}` 写进预分配的
  65536 条定长环；命中路径不做 JSON/字符串/加锁。`watch_drain` 在管道线程把环序列化成
  JSON，`since`/`next` 是绝对序号，`lost` 表示已被覆盖的条数。
- 清除时对每个安装过的线程恢复 DR7 并清零 DRn。

## 二进制 tick 环（T4）

- 记录是 64 字节 POD：`tick u64`、`x/y/z`、`vx/vy/vz`、`yaw/pitch`、`feet_y`、
  `flags u32`、三个方块名哈希、`health_milli`。`static_assert(sizeof == 64)`。
- 写者在 `safeTickPost`：先检查 `gRecording`（关闭时每 tick 只有一次原子 load），
  填完整个记录后 `gHead.store(index+1, release)`；单写者，没有锁、没有字符串/JSON/分配。
  `period>1` 时按 tick 取模降采样。
- 环固定 `1<<19` 条（32 MB），第一次 `record_start` 分配，`record_stop` 只停写不释放，
  便于随后 dump。
- `record_dump` 在管道线程解码：`jsonl` 直接给字段数组，`bin`/`hex` 给 64 字节记录的
  hex 块（受 JSONL 传输限制，用分块 hex 而不是裸字节帧）。`since/next/head/lost` 都是
  绝对序号；`lost` 表示已被环覆盖的条数。
- dump 结果本身也走 `enqueue`，因此会自动进 T1 的 spool。
- JSON `start` 的逐 tick 事件保留为兼容路径，两者可以同时开。

## 跑酷辅助（P2）

- `scan`：管道线程只存 `ScanRequest`，tick 线程在 `safeTickPost` 遍历区域并回 `scan`。
  体积上限 32768 格、结果上限 16384，避免拖慢 tick；默认跳过空气。
- 存档点：tick 线程每 tick 把脚部坐标/朝向写进原子量；`checkpoint_save` 在管道线程读这些
  原子量并写 `checkpoints.json`（`.tmp` + `MoveFileExW` 原子替换），所以文件 I/O 不进 tick。
  `checkpoint_load` 复用 `resetPlayer`，和 `reset` 一样清任务/输入/药水效果。
- `state.effects` 直接读 `Actor::getAllEffects()`；`player_landed` 用起跳/落地的脚部高度差
  计算落差，`player_hurt`/`player_died` 由每 tick 的血量变化触发。

## 运行时装钩（T5）

- `hook_install` 支持两种目标：
  - `symbol`：编译期符号（`&MobJump::setJumpPendingScale`），使用 **typed detour**
    `void(EntityContext&, float)`，参数和返回值都按原签名传递；目前只登记了这一个符号。
  - `va`：来自 IDA 的运行时地址，使用通用 detour，只保存 rcx/rdx/r8/r9 四个整数寄存器；
    需要 `allow_hook_va=true` 才能真装，`dry_run` 只做地址校验。
- 地址校验：`VirtualQuery` 要求 `MEM_COMMIT`、非 `PAGE_GUARD`、`PAGE_EXECUTE*`；VA 的
  指令边界由调用方（IDA）负责，mod 只报 `instruction_boundary:"unverified"`。
- 安装/卸载走 `ll::memory::hook` / `unhook`（suspendThreads=true），原点指针由 LL 写回；
  失败返回 `hook_failed` + code，不会留下半装状态。
- 命中环固定 4096 条：每次命中只写 `{hookId,tid,tick,四个参数,返回地址,4 帧栈}`，
  按 `sample` 降采样；`hook_drain` 在管道线程序列化，`b_float` 把第二参数按 float 解释。
- `hook_call` 只是为了验证：tick 线程调用 `MobJump::setJumpPendingScale(ctx, scale)`，
  该调用会穿过 detour，因此能确定性地产生一条命中。

## ECS 内省（T6）

- 请求在 tick 线程处理（和 `entity` 动作同一个入口），通过 `Actor::getRuntimeActorList()`
  找目标，再取 `Actor::getEntityContext()`。
- `EntityContext::getRegistry()` 返回 EnTT v4 的 `basic_registry<EntityId>`；
  `registry.storage()` 迭代 `(runtime component id, storage)`，`storage.contains(entity)` /
  `storage.index(entity)` / `storage.value(entity)` 给出该实体的组件指针。
- 组件字节用 T2 的 `readMemory` 读，长度默认取 stride；stride 由同一 storage 前两个元素的
  `value()` 地址差推断（单元素 storage 为 0）。
- `ecs_registries` 报客户端 registry 指针、组件 storage 数和 entity id。服务端 registry
  没有暴露（服务端对象只能经 `queueServerTask` 访问）。
- 组件 id 是运行时 EnTT id，不是稳定的 ABI 常量；报告里只有 id/地址/stride/count，没有类型名。

## panic（T8）

- `panic {level:"hooks"|"tasks"|"all"}`：
  - `hooks`：`hookRemoveAll()` 逐个 `ll::memory::unhook`，再 `watchRemoveAll()` 清 DR 观察点；
    卸载失败的钩保留占用，便于重试。
  - `tasks`：`cancelTasks()` 递增 epoch 并清空所有闭环任务，`beginTask()` 停 macro、清移动输入。
  - `all`：两者都做。
- 回 `panic_done {ok, hooks_removed, watches_cleared, tasks_cancelled}`；进程和 tick 都不受影响，
  之后可以重新装钩/观察点。`unhook_all` 是 `panic hooks` 的别名。

## tick 开销测量与请求块（T9）

- 请求块 seqlock：`mInput`/`mLook`/`mTeleport` 合并成一个 `RequestBlock`。管道线程在
  `mRequestWriteMutex` 下写入并推进 `mRequestSeq`（奇数=写入中）；tick 线程每 tick 只做一次
  `readRequests()` 原子快照，**读路径不再取任何 mutex**。每条命令带自己的序号
  （`inputSeq/jumpSeq/lookSeq/teleportSeq`），tick 侧用本地计数器消费一次并自行递减
  `ticks`，所以 `ticks<0`（直到 stop）和一次性 jump/teleport 的语义不变。
  `beginTask`/内部错误仍会写块（罕见），但只占写锁，不走读路径。
- 快照同时用于 teleport（进 tick 前）、移动和 look（tick 后），保证同一 tick 看到一致的一组请求。
- `onPlayerTick` 进入时取一次 QPC，`safeTickPre` 结束后算 pre 耗时；`afterPlayerTick` 同理算
  post 耗时。`hook_us = pre + post`（不含 `origin()` 的游戏 tick），
  `tick_us` 是相邻两次 pre 开始的间隔。
- 1024 条定长样本环，tick 线程单写者（写完再 publish head）；`overhead`/`status.overhead`
  在管道线程复制、排序并给出 p50/p99/max、`tick_hz`、`calls_per_sec`。
- `overhead.paths.{record,watch,hook}` 给出三条路径各自的 `calls_per_sec` 与 `calls_total`
  （窗口从首次调用起算），用于发现异常回调频率。
- `selftest_busy {us,ticks}` 在 `safeTickPost` 里忙等，需要 `allow_selftest_stress=true`
  （默认拒绝，避免误让 tick 卡近 1 秒）；关闭时回 `error/stress_disabled`。
- 实机（1.26.51.01，20 TPS，seqlock 改后空闲 1024 样本）：`tick_p50=50.006 ms`、
  `tick_p99=50.970 ms`、`hook_p50=10 µs`、`hook_p99=38 µs`；改前空闲为
  `tick_p99=54.596 ms`、`hook_p99=30 µs`。tick p99 没有劣化。
  注入 1 ms ×20 tick 后 `hook_p99=1013 µs`、`hook_max=1040 µs`，证明指标可观测。

## hook_va 安装与生命周期（F2/F3）

- 槽位在 `gHookMutex` 内预留（`used=true`、`ready=false`）后**释放锁**再调
  `ll::memory::hook(..., suspend=true)`，避免持锁挂起线程造成堆/Loader 死锁。
- 补丁生效后、origin 发布前，detour 用 `ready` 有界自旋（200k 次 `YieldProcessor`），
  不再静默 `return 0`；origin 用原子发布，`ready` 最后置位。
- 失败路径：`status!=0` 直接释放槽位；`status==0 && origin==nullptr` 先 `unhook`，
  失败则保留槽位（`unhook_failed`，panic 可重试），成功才释放。同一地址重复安装回
  `hook_duplicate`。
- 主菜单（不在世界）安装回 `not_in_world`：LL 在该状态不挂起线程，直接改代码不安全。
- `hook_install` 的 `inject_fail`（仅 `allow_hook_va=true` 时生效）用于自检 F3 失败路径。

## offsets 表（F5）

- `Resolve.cpp` 内置一张 `struct -> field -> offset` 表（Block/BlockLegacy 共 6 项）和一个
  `components` 表（`id -> {name,size}`；StateVector 的 459100728/36 来自实机）。
  `<mods>/probe/offsets.json` 的 `structs`/`components` 会覆盖内置值。
- `structOffset("Block","LegacyPointer",...)` 是唯一查询入口；`Memory.cpp` 的
  `probe block/legacy` 与 `RecordRing.cpp` 的方块采样都走它，不再有魔法数字。
- JSON 里把某个字段设成 `null` 会移除该表项，`probe` 回 `{"event":"error","reason":"no_offset"}`，
  不会拿默认 0 去猜。未知字段只写 boot log，不生效。
- `diagnose.structs` 输出 `source/struct_fields/component_count/missing_lookups/last_missing`。
- 实测：把 `BlockLegacy.BlockId` 改成 `0x17C` 后 `probe legacy.block_id` 从 1 变 0；
  设 null 后 `probe legacy` 回 `no_offset`，`probe block` 仍正常。

## spool 顺序与轮转（F6）

- `flushSpool` 用 `mSpoolWriteMutex` 包住“swap 队列缓冲 + 写文件”，两个并发 flush 无法交错；
  `enqueue` 只取 `mQueueMutex`，tick 线程不会被文件 IO 阻塞。
- 启动时若 `spool-<pid>.jsonl` 已存在且非空，改名为 `spool-<pid>-<n>.jsonl`（PID 复用时
  不会把新会话追加到旧文件）；单文件写到 64 MiB 也会轮转。
- `spool_flush` 返回 `path`（当前文件名）。

## watch 细节（F7）

- foreign 检查只看目标槽的两个 DR7 位：`(Dr7 >> (2*slot)) & 3`；调试器占着别的槽不再
  阻断安装。
- 数据断点是执行后触发，`watch_drain` 的 `rip` 是**下一条**指令；每行附
  `code_hex = rip-16..rip`（32 位 hex），顶层 `rip_semantics="next_instruction"`。
- `allow_watch`（harness.json，默认 true）：关闭时 `watch_set` 回 `watch_disabled`；
  `watch_installed` 带 `suspends_tick_thread=true`。

## 记录环采样（F8）

- 每 tick 的三次方块采样不再调用 `getTypeName()`：先经 F5 表读 `BlockLegacy::BlockId`，
  再用 256 项 `id -> FNV-1a-32` 缓存，稳态零字符串分配（每 tick 每个方块类型只在首次出现时分配一次）。
- y 超出 [-64, 319]（三种原版维度高度的并集）时不调用 `getBlock`，三个 block 字段写 0，
  并置 `RecordFlagBlockOob = 1<<11`。

## 开销语义（F9）

- `tick_p50/p99/max_us` 保留旧名，语义是**相邻 tick 开始的间隔**；新增同值的
  `tick_interval_*_us` 以免误读。`hook_*_us` 才是钩子体（pre+post）的耗时。
- `window_samples=1024`、`qpc_resolution_ns` 随响应返回；样本环读写用 `gEpoch` 奇偶守卫，
  `consistent=false` 表示读侧重试 64 次仍未拿到完整窗口。

## 版本指纹与锚点（T7）

- 启动时用 `GetFileVersionInfoW` 读主模块版本，前缀必须是 **1.26.51**；`build_id` 记录完整
  文件版本（当前 `1.26.51.1`）。
- 4 个锚点都走符号地址：`LocalPlayer::$normalTick`、`LocalPlayer::_applyTurnDelta`、
  `Actor::getHealth`、`Actor::isOnGround`。LL 的导入符号可能是
  `jmp qword ptr [rip+0]` thunk，先跟随它到真实函数（`Actor::getHealth` 还带
  `add rcx,8` 的 this 调整，入口字节本身是稳定的），再读 **24 字节**比对。
- 内置签名来自实机 1.26.51.01，写在 `Resolve.cpp`；`docs/offsets/1.26.51.01.json` 是同一份
  数据，复制到 `<mods>/probe/offsets.json` 可覆盖 `expected_hex`，不改代码即可适配小版本。
- 任一锚点不匹配或版本前缀不符：`version_ok=false`，`safeTickPre/Post` 立即返回，
  `hook_install`/`watch_set`/`record_start` 回 `version_mismatch`；`allowTurnDelta` 放行，
  保证玩家正常操作。管道、`status`、`diagnose`、`mem_read`/`probe` 仍可用，便于对比签名。
- `diagnose` 输出每个锚点的地址、resolved 地址、配置状态、匹配结果和耗时；
  `signature_check {name,expected_hex}` 用来验证负路径（传错签名应回 `match=false`）。
- `docs/offsets/1.26.45.01.json` 只作历史数据，不参与当前构建。

## 封包解码注册表（H4）

- `driver/harness/packets.py`：`PACKET_NAMES`（常用 id）、`decode_packet(id, bytes)`、
  `decode_row(packet_bytes_row)`。未知 id 返回 `{id,name:"id_N",hex}`，不抛异常。
- 已实现布局：
  - `77 CommandRequest`：命令字符串、origin 类型/名字/UUID、requestId、playerId、版本。
    **用实机抓到的 77 包验证**（`/give @s minecraft:stone 5`，origin=ClientAutomation）。
  - `36 PlayerAction`：runtimeId varint、action varint、x/y/z float、face varint。
  - `19 MovePlayer`：runtimeId、位置、pitch/yaw/headYaw、mode、onGround、ridden、tick。
    36/19 是标准 Bedrock 布局，目前只有合成样例单测（本客户端移动走 PlayerAuthInput）。
- B3 存的是 payload 字节，不含包 id varint；解码器从第一个字段开始读。
- CLI：`py -3.11 -m driver.harness.cli packet-decode --id 77 <hex>`。

## 命令包注入（H2）

- `CommandRequestPacketPayload` 的默认构造没有导出，所以不自己拼 payload；
  走模板复用：
  1. 用 `/`（key 191）+ `text_input`（HID `onTextInput`）+ Enter 发一条真实命令；
  2. `sendToServer` 钩子在 id==77 时把 payload 拷贝进 `mCommandTemplate`
     （`CommandRequestPacket` 公开继承 payload，copy 后 origin/UUID 都是真实值）；
  3. `chat_send {text}` 在 tick 线程复制模板，只替换 `mCommand`，设置
     `mVersion=Latest`/`mInternalSource=false`，构造 `CommandRequestPacket` 并调
     `Packet::sendToServer()`。
- 门禁：`allow_packet_inject`（默认 false），只允许这一个 id；`chat_send` 只入队，
  真正发送和结果通过 `chat_sent {ok,text}` 事件回报。
- `op_self {level}` 在服务端玩家上 `setCommandPermissions`（测试世界用）；
  `commands_enabled` 读写 `LevelData::mCommandsEnabled`。客户端命令包仍受玩家权限约束，
  `commands_enabled=true` 不代表玩家是 Operator。
- 实测（1.26.51.01）：`/setblock` 回读 `gold_block`；`/tp @s ~1 ~ ~` 位移 1.000；
  `/fill`、`/give` 生效；注入前后 tick p99 不变，`chat_sent` 计数正确。
- 注意：`chat_sent ok=true` 只表示包已发出；命令是否执行必须用 `blocks`/位置回读。
  模板在每次真实 77 包发出时刷新，进程重启后需要重新发一条命令。

## 封包字节与层选择（H1-B）

- 运行时符号解析：`symbol_resolve {name}` 先查编译期锚点（`&BatchedNetworkPeer::$sendPacket`、
  `&LoopbackPacketSender::$sendToServer`、`&NetworkConnection::receivePacket`），再跟随
  `E9 rel32` / `FF 25 [rip+disp]`；返回 `{anchor, real, thunk_hops, module, rva, executable}`。
  LL 的 `SymbolView::resolve()` 对游戏导入名返回 0，不能依赖它。
- 实测：`BatchedNetworkPeer::$sendPacket` = `Minecraft.Windows.exe+0x46EB60`（无 thunk）；
  `LoopbackPacketSender::$sendToServer` 锚点 `+0xD809E0`，跟随 3 跳到 `dmf.dll+0x8AC10`
  （已被扩展链式 hook）；`LocalNetworkPeer::$sendPacket` 只有导入声明，链接不到。
- 计数决策（`hook_va` 只计数，不读参数）：
  `sendToServer` 走/跳/命令都有流量；`BatchedNetworkPeer::sendPacket` 和
  `NetworkConnection::receivePacket` 在单机里都是 0 ⇒ 本地回环绕过序列化/peer 层，
  B1/B2 拿不到字节，转 B3。
- B3：`packet_capture {serialize_ids:[144,77]}` 时，仅对这些 id 在 `sendToServer` 钩子里
  用 `BinaryStream(std::string&)` 调 `Packet::write`，前 128 B 写进 4096 条字节环；
  `seq` 复用身份环的 order，`packet_bytes` 返回 `{seq,id,size,hex}`。
  代价：首次调用分配线程局部缓冲；关闭 capture 时零调用；denylist 的 id 不能进白名单。
- 实测 `PlayerAuthInput(144)` 序列化后 91–93 B，前几字节是位置浮点（可读），
  32 条字节记录与身份记录 100% 对上。
- 注意：序列化在客户端线程、发包钩子内进行；只对 1–2 个白名单 id 启用，不要扩成全量。

## 封包身份 tap（H1 阶段 A）

- 锚点：`LoopbackPacketSender::$sendToServer(Packet&)`（LL 符号 hook，见
  `docs/offsets/1.26.51.01.json` 的 `packets.sendToServer`；24 B 签名待补）。
- 钩子体只做四件事：`packet.getId()`（虚函数，返回 `MinecraftPacketIds`，无分配）、
  `mPacketSeq.fetch_add`、`mTick`、`GetCurrentThreadId()`；写进 16384 条 MPSC 环，
  每条用自己的 `seq` 发布。**不调用 `getName()`**（返回 `std::string_view`，但约定是
  身份层不碰字符串）。
- 环记录：`{order, tick, id, tid, injected}`；`injected=false` 预留给 H2。
- denylist（硬挡，任何 `ids` 参数都无效）：KeepAlive 0、Login 1、PlayStatus 2、
  Handshake 3/4、Disconnect 5、ResourcePack 6/7/8/82/83/84、Transfer 85、
  SubclientLogin 94、NetworkSettings 143/193、ResourcePacksReady 340。
- 采样 `sample=1/N` 基于 order；`allow_packet_capture=false` 时不装钩、零记录；
  `panic`/`disable` 会 `packetCaptureStop()`。
- 实测：3.5 秒 122 条，`seq` 0..121 单调无丢失，tid 固定 11684；id 分布以
  `PlayerAuthInput(144)`、`SubChunkRequest(175)` 为主，攻击/放置产生
  `InventoryTransaction(30)`、`Interact(33)`。
- 字节层决策：`LocalNetworkPeer::$sendPacket` 是 dllimport-only（链接不到导出 thunk），
  所以 B1 的“NetworkPeer 字节 tap”无法按符号实现，`peer_sends` 恒为 0。
  如果要字节，走 B2（服务端接收侧，需按包类型/分派器定位）或 B3（白名单序列化）。
- H3 预演：B（标志）与 C（事件）各 3 秒的 id 序列几乎一致（144 占绝对多数），
  身份层无差异；G3 已证明速度也一致，所以不需要用封包解释疾跑。

## 能力可用性验收（H0）

- **StateVector 写者**：`state.runtime_id` / `state.context` 给出玩家标识；StateVector 的
  `VelY` 偏移来自 offsets 表（`0x1C`）。玩家位置/速度由**集成服务端线程**写入，所以观察点
  用 `watch_set(thread="both")`（tick + server）。实测命中指令为
  `mov qword ptr [rbx + 0x18], rax`（8 字节写覆盖 0x18–0x1F，包含 vy）。
- `thread="all"` 会给 100+ 个线程装 DR，实测导致游戏崩溃；`status.server_thread_id`
  在第一次 `queueServerTask` 后可用，`thread=server|both` 用它。
- **probe × blocks**：`blocks` 给出 `Block*`，`probe block` 跟到 legacy 指针，
  `probe legacy` 的 name/hash 与 blocks 一致。51 版实测 block id：air=0、grass_block=2、water=9
  （结构布局跨版本不变的假设目前成立）。
- **hook context × ECS**：`ecs_entity/ecs_components` 按 runtime id 解析到服务端 Actor；
  客户端玩家的 `EntityContext*` 从 `state.context` 取。`ecs_context {context,component_id}`
  在 tick 线程用这个指针直接查 registry，与 `state.context` 的查询结果（地址/count/size）
  一致。**不要**在 tick 线程枚举客户端 registry（会与游戏线程竞争并崩溃，已实测）。
- **落盘三连**：`driver/spool_forensics.py panic|taskkill|verify`。panic 路径 spool 单调连续
  且末事件为 `panic_done`；taskkill 最多丢 100 ms（实测 0 tick）；leave_world 需要桌面点
  “Save & Quit”，脚本明确返回不支持，不静默通过。
- selftest 对应检查：`h0_offsets_table` / `h0_statevector` / `h0_watch_*` / `h0_probe_*` /
  `h0_block_ids` / `h0_hook_*` / `h0_spool_*`。

## 输入事件层（G1–G6）

- **键盘（G1）**：`HIDController::onKeyDown(code, InputOrigin::Virtual|Hardware)` 是游戏自己的
  事件入口，会触发 LL `KeyInputEvent`、按键绑定和
  `Keyboard::_states()`。实例路径：
  `ll::service::getAppPlatform() → getPlatformShim() → Bedrock::Platform_GameCore::mHIDController`。
  另有对照路径 `Keyboard::_inputs().push_back(KeyboardAction)`：能改槽位/开背包，
  但**不会**发 LL 事件，所以事件计数必须以 HID 路径为准。
- `key_event {code,down,repeat,repeat_ms,via}` 先进入 128 项的 SPSC 环（管道线程写、tick 线程读），
  在 `safeTickPre` 的 `tickInputInjection()` 里 drain；`via=hid`（默认）/`hardware`/`queue`。
  强能力由 `allow_input_inject` 门禁（默认 false），每次注入写 boot log。
- **鼠标（G2）**：`MouseDevice::feed(action, data, x, y, dx, dy, force)` 是 `MouseInputEvent`
  的发布入口；`mouse_event {action,...}` 同样在 tick 里 drain。
  `action=move`(0) + dx 能转向；`left/right` 只更新 `MouseInputEvent` 计数，在 1.26.51.01 上
  **不触发世界攻击**（攻击绑定不在这一层），攻击仍用 `click`/`attack_entity`。
- 后台守卫：`allowTurnDelta()` 放行 `mInjectingLook` 和注入鼠标转向后的 2 tick
  （`mInjectTurnUntilTick`）。注入标志只在调用期间短暂置位，避免整帧放行真实鼠标。
- **sprint 对照（G3）**：`driver/sprint_compare.py` + `scenarios/sprint-compare.json`。
  逐 tick 位移中位数：B（`set_input` 标志）0.2875、C（ctrl+W 事件）0.2806，基线 0.2805。
  结论是事件层能复现疾跑，差异不在输入事件层；G5 的启动条件不成立，未实现 raw-input hook。
- **macro（G4）**：`macro_run {event_level:true}` 按相邻 tick 的按钮边沿走 G1/G2 事件注入，
  不再调 GameMode；旋转仍走 `_applyTurnDelta`。旧路径（默认 `event_level=false`）完全保留，
  文件格式未改。结束/取消时 `releaseMacroInputs()` 释放仍按下的键和鼠标。
  实测三次回放落点误差 0.0000（无真实鼠标干扰时）。
- **世界内 UI（G6）**：`screen` 返回 `ClientInstance::getScreenName()`。实测
  `hud_screen --E--> inventory_screen --Esc--> hud_screen`，`/` 打开 `chat_screen`；
  暂停菜单不响应注入按键，退出暂停仍需桌面点击 Resume。
- 门禁与回归：`allow_input_inject=false` 时 `key_event`/`mouse_event`/`key_state` 回
  `input_inject_disabled`；selftest 覆盖槽位切换、100 次计数一致、鼠标计数/转向、
  背包开关、事件级 macro 确定性与旧路径。

## 健壮性

- tick 前置/后置钩子各自 `try/catch`；异常会清空输入和任务，并限制为每秒最多上报一次
  `internal_error`，不会把异常抛回游戏主循环。
- `handleLine` 外面有 `try/catch`，并且 `dispatchLine` 包在 `__try/__except` 里：
  坏 JSON/类型错误回 `bad_command`，请求级的 AV 回 `internal_error`，管道线程和 tick 都继续。
  C++ 异常通过 `__except` 过滤器放行到外层 `catch`，不会被误当成崩溃。
- 所有 `queueForServerThread` 回调统一走 `queueServerTask`，在服务端线程里捕获异常，
  避免异常穿过游戏的服务端 tick。
- 管道写入按 `WriteFile` 的实际返回值循环，防止字节模式下的半行写入破坏 JSON Lines；
  队列超过 20000 条时丢弃最旧的并计数（`status.queue_dropped`），同时往 spool 写一条
  `queue_dropped` 标记。
- 管道用 `PIPE_REJECT_REMOTE_CLIENTS`，只接受本机客户端。
- 启动/管道状态写到 mod 目录下的 `harness-boot.log`（`load`、`enable`、`pipe: ...`），
  因为 LeviLauncher 的日志位置随启动方式变化。
- Python 侧读取用 `PeekNamedPipe` 轮询而不是阻塞 `read`：Windows 的同步管道句柄会把
  挂起的 `ReadFile` 和 `WriteFile` 串行化，阻塞读会让同一条连接上的发送永远等下去。
  每次请求都有超时（`ModTimeout`），连接关闭抛 `ModDisconnected`，协议错误抛
  `ModProtocolError`；`reconnect()` / `wait_ready()` 用于游戏重启后恢复。
