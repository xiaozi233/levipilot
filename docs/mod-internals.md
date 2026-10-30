# Mod 实现要点

面向维护者。用户 API 见 [`../mod/README.md`](../mod/README.md)。

2026-10-30 审阅修复：请求发布改用私有事务 + 原子载荷 mailbox，tick 读端竞争时保留已验证
快照；按钮数组重置投递到 tick。Python 场景录制使用连接代次内的通知订阅，在请求消费时
同步收集 tick，停止失败会传递错误。离线全量 **280/280**，现场 **228/228**（未跳过）；
真实场景适配器等待 12 tick 收集 13 条连续样本，pending=0。本轮逐项状态和现场证据见
[`reliability-repairs.md`](reliability-repairs.md)。

## 注入时机

- Hook `LocalPlayer::$normalTick`（LL 实例 hook），在 `origin()` **之前**注入输入、之后采样。
  这与 dmf-mpk 的 macro 注入时机一致；`ClientLevelTickEvent` 偏晚，跳跃/按键会被覆盖。
- 移动（默认，2026-10-28）：先把 `ControlState` 的数字量翻译成动作，再从
  `IOptionRegistry::getCurrentKeyboardRemapping()` 读 `key.forward/back/left/right/
  sneak/jump/sprint` 的**当前键位**，发 Hardware HID 按键沿；`state.bindings` 暴露
  实际 key code（当前疾跑=R）。未绑定的疾跑才回退双击前进。不再直接写
  `MoveInputComponent`/`MoveInputState`，也不走 queue/handler 重发。
- 视角：`LocalPlayer::_applyTurnDelta(Vec2{pitchDelta, yawDelta})`
  （pitch 符号与 `ActorRotation.x` 相反）
- 跳跃（默认）：Space 按下/松开；`jump` 命令就是 1 tick 的 Space。实测 HID Space 的
  逐 tick 位移仍是 0.4200、0.3332、0.2481、0.1648…（顶点 1.252 格，滞空 11 tick），
  `selftest` 的 `jump_motion_*` 比较 HID Space、持续 `set` 和单次 `jump_when` 的完整轨迹。
- Legacy 诊断：`input_cfg mode` 位掩码（1=直接标志，2=队列，4=handler，8=toggle fallback）与
  `jump_impulse`（1=jumpFromGround，2=setJumpPendingScale，3=两者）默认都是 0；
  非零只在 `force=true` 下接受，用来复现旧测量，不是正常移动路径。
- 潜行：默认 Lshift；legacy mode 1 仍走 `LocalPlayer::setSneaking(true)` + 标志。
  实测 20 tick 前进 4.316 格，潜行 1.295 格（约 0.30x 速度）。
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

- `getRuntimeActorList()` 归**集成服务端线程**所有；客户端 tick 线程不遍历它。所有
  `runtime_id` 查找与快照都在 `queueServerTask` 里执行：命令把客户端侧数据（玩家位置、
  头部、uuid/名字）在 tick 线程读好后投递，服务端线程找到目标并快照（类型/血量/头位）。
- `attack_entity` 全程在服务端线程执行：解析服务端玩家（uuid→name 回退，同
  `requestServerTeleport`），用**服务端玩家的** `GameMode::attack(actor, headPos)`；
  Actor* 不跨线程传递（一帧旧的指针曾在 client 任务里 SEH）。
- `interact_entity` / `look_at_entity` 只需要目标的头部位置快照：服务端任务取
  `headPos` 后，瞄准与输入注入经平台主线程队列（`queueClientTask`）回到玩家上执行，
  转向/注入语义与之前一致（3 tick 使用按钮，best effort）。**不要**直接调用
  `Player::interact` / `GameMode::interact`：空 item 上下文会触发 `[rsi+0x1D0]`（RSI=0）
  崩溃。
- `look_at_entity` 用玩家头部到实体头部的向量算 yaw/pitch（`probe::detail::yawTo/
  pitchTo` 共享公式），再走 `_applyTurnDelta`。

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

- `WorldSession` 集中持有世界状态、四个事件订阅及登录/自动进入计时器。
  `worlds` / `enter_world` / `leave_world` / `signin` 均经游戏平台主线程队列执行，
  管道线程只提交参数副本；主线程入口由 SEH 与 C++ 异常边界隔离。
- `worlds`：主线程调用 `IMinecraftGame::getLevelListCache().getLevelList(...)`。
- `enter_world`：按名字或 id 取 `LevelSummary`，用它构造 `LevelSettings`，再调用
  `IGameServerStartup::startLocalServerAsync(levelId, levelName, contentIdentity, settings, intent)`。
  `startLocalServerAsyncFromSummary` 没有被 LeviLamina 导出，直接链接会报 LNK2019；纯虚重载
  走 vtable。实测 `intent=0` 能从主菜单进"超平坦测试"。
- **登录门**：世界列表来自本地缓存，可能早于异步 Xbox Live 登录；这时启动本地服务器会弹出
  "你必须通过 Microsoft 服务身份验证"。进入前通过 `SceneProvider → FacetRegistryFactory →
  ScreenModelProvider::getMainMenuScreenModel() → isSignedInToXBL()` 查询，未登录则轮询等待
  `signin_wait_ms`（默认 15 s），仍未登录回 `signin_required`（`force=true` 可跳过）。
  轮询通过 wall-clock timer 延迟后重新排入主线程，禁止在主线程 Sleep。
  LL 默认 `ClientThreadExecutor` 只随世界 tick 执行，主菜单不能使用。
  join 结果用 `enter_world_settled` 通知，超时/取消回 `ok=false` 和原因。
- `leave_world`：`IGameServerShutdown::requestLeaveGame(true, true)`。
- 世界事件：`ClientStartJoinLevelEvent` → `world_joining`，`ClientJoinLevelEvent` →
  `world_entered`，`ClientExitLevelEvent` → `world_left`，`ClientCancelJoinLevelEvent` →
  `world_join_cancelled`。
- `options_set`：`IOptionRegistry::loadOptionsFromString` 实时应用（gamma/FOV 立刻可见），
  然后自行更新 `options.txt`。游戏自带的 `saveIfNeeded` 在当前版本不会立刻写盘，所以
  mod 读改写同一个文件（保留 CRLF 和未修改的行），再 `MoveFileExW` 原子替换。
- `harness.json`：`auto_enter` / `options` / `delay_ms`。`WorldSession` 用可取消的延迟回调，
  在主线程等客户端存在、世界列表非空且游戏在前台，再应用选项并进世界。
  disable 会更新调度代次并取消计时器，已排队的旧代次回调不会再触碰游戏对象。

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
  tid。`thread="all"` 已被显式拒绝（`thread_all_unsupported`）——给 100+ 线程装 DR
  实测会搞崩游戏；VEH 读取 armed 线程表是无锁的定长数组（原子计数发布），异常路径
  不拿 mutex。挂起窗口内不做任何可能分配的操作（日志延迟到 Resume 之后）。
- 命中由 VEH 处理：清 DR6 对应位，把 `{watchId, tid, tick, rip, depth 帧栈}` 写进预分配的
  65536 条定长环；命中路径不做 JSON/字符串/加锁。`watch_drain` 在管道线程把环序列化成
  JSON，`since`/`next` 是绝对序号，`lost` 表示已被覆盖的条数。
- 清除时对每个安装过的线程恢复 DR7 并清零 DRn。

## 二进制 tick 环（T4）

- 记录是 64 字节 POD：`tick u64`、`x/y/z`、`vx/vy/vz`、`yaw/pitch`、`feet_y`、
  `flags u32`、三个方块名哈希、`health_milli`。`static_assert(sizeof == 64)`。
- 写者在 `safeTickPost`：`RecordBuffer` 关闭时一次原子 load 返回，开启时获取无等待的
  capture 准入；`period>1` 时按 tick 取模降采样。记录填完后原子发布到 slot 再推进 head。
  模型的采样准入/发布无 mutex、字符串、JSON 或分配；profile 偏移查询移到 start 的命令线程。
- start/stop 在命令侧串行：关闭准入、等待已经开始的 capture 完成，再重置 head、计数和缓存。
  旧采样不能进入新会话，stop 返回后的 head 稳定。tick 不等待命令锁，也不进行重置。
- 环固定 `1<<19` 条，64 B payload 共 32 MiB，另加约 4 MiB 的发布序号。第一次 start 分配，
  stop 不释放，便于 dump。slot 的 payload 使用原子 word，读前/读后检查发布序号，避免
  环绕覆写时的数据竞争与撕裂；并发覆写导致无法复制的行计入 `lost`，`count` 为实际返回数。
- `record_dump` 在管道线程解码：`jsonl` 直接给字段数组，`bin`/`hex` 给 64 字节记录的
  hex 块（受 JSONL 传输限制，用分块 hex 而不是裸字节帧）。`since/next/head/lost` 都是
  绝对序号；`lost` 表示已被环覆盖的条数。
- dump 结果本身也走 `enqueue`，因此会自动进 T1 的 spool。
- JSON `start` 的逐 tick 事件保留为兼容路径，两者可以同时开。
- 采样的游戏 API 调用放在独立 SEH 边界内；结构化异常先转成失败，再正常退出 capture，
  避免 `/EHs` 未展开析构导致下一次 start/stop 永久等待。tick 只设置故障标志，spool
  线程合并发出 `internal_error(phase=record_tick)`；本轮没有在游戏中注入故障验证。

## 跑酷辅助（P2）

- `scan`：管道线程只存 `ScanRequest`，tick 线程在 `safeTickPost` 遍历区域并回 `scan`。
  体积上限 32768 格、结果上限 16384，避免拖慢 tick；默认跳过空气。
- 存档点：tick 线程每 tick 把脚部坐标/朝向写进原子量；`checkpoint_save` 在管道线程读这些
  原子量并写 `checkpoints.json`（`.tmp` + `MoveFileExW` 原子替换），所以文件 I/O 不进 tick。
  `checkpoint_load` 复用 `resetPlayer`，和 `reset` 一样清任务/输入，但**不清药水效果**
  （`resetPlayer` 现在带 `clearEffects` 参数：只有 `reset` 传 true）。
  <!-- (2026-10-25) 之前 `resetPlayer` 固定 clearEffects=true，且 `teleport` 处理器不重置这个
       共享请求字段，导致**做过一次 reset 之后，之后每次 teleport 都会清空药水效果**
       （保护药水在做实验时"凭空消失"就是这个）；现在 teleport 显式写 false。 -->
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
- `watch_set` 的 `depth`（1..32，默认 8）控制每次命中抓多少返回地址；记录固定 32 槽，
  实际只填 `depth` 帧。深度越大，VEH 每次命中多一次更深回溯、环占用更大
  （65536 条 × (48 + 8×depth) B）。越 1..32 回 `bad_depth`。
  注意前几帧常是 VEH 处理器的栈，8 帧实际只剩 3 层真实调用者；
  取自证 pass/调用链时用 20 左右。

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

## 版本指纹与锚点（T7 / 版本 profile）

- 启动时用 `GetFileVersionInfoW` 读主模块版本，得到 `build_id`（当前 `1.26.51.1`）。
  **不再在 C++ 里写期望版本号**：`build_id` 去 `GeneratedProfiles.h` 里找 profile。
- profile 数据源是 `docs/offsets/*.json`（带 `"runtime": true` 的文件），由
  `driver/gen_profiles.py` 生成 `mod/probe/src/mod/GeneratedProfiles.h`。加一个游戏版本 =
  加/改 JSON + 重新生成 + 重编，不改任何 C++ 逻辑。
- 选择顺序：`build_id` 精确匹配 → `version_prefix`（如 `1.26.51.`，兼容小版本修订）→
  `version` 字符串 → 没有 profile。`profile_probe {build_id}` 可以离线验证某版本会选中谁。
- 4 个锚点仍是固定集合（`LocalPlayer::$normalTick`、`LocalPlayer::_applyTurnDelta`、
  `Actor::getHealth`、`Actor::isOnGround`），地址走符号；LL 导入桩可能是
  `jmp qword ptr [rip+0]` thunk，先跟随到真实函数再读 24 字节。
  签名来自 profile 的 `anchors.<name>.expected_hex`；**没配置的锚点不参与判定**。
- 结构体偏移和组件 id 也来自 profile（不再有编译期常量）。运行时
  `<mods>/probe/offsets.json` 可以只覆盖其中几项；也可以追加 profile 里没有的字段/
  组件（用于新版本临时试跑，不必重编）。
- 门禁语义：
  - `version_ok` = 找到 profile 且所有已配置锚点匹配；
  - `hooks_allowed` = `version_ok` 或 `allow_version_mismatch=true`；
  - tick 钩子、`watch_set`、`hook_install`、`record_start`、输入注入、封包 tap 都看
    `hooks_allowed`；
  - 没有 profile 时偏移表为空，`probe`/`record` 的方块字段回 `no_offset`，其余功能仍可用。
- `status`/`diagnose` 输出 `profile` 和 `capabilities`（hooks/input/watch/record/packets/
  offsets/components/profile/compat），新版本上先看这两个就知道哪些能力可用。
- `profile_dump` 从**当前运行的游戏**读 4 个锚点的真实字节、当前结构偏移和组件表，并给出
  一份可直接保存为 `docs/offsets/<version>.json` 的 `profile` 对象。移植流程见
  [`porting.md`](porting.md)。
- `diagnose` 输出每个锚点的地址、resolved 地址、配置状态、匹配结果和耗时；
  `signature_check {name,expected_hex}` 用来验证负路径（传错签名应回 `match=false`）。
- 只有带 `"runtime": true` 的 profile 参与生成；旧 schema 的历史数据没有这个标记，会被跳过。

## 封包解码注册表（H4）

- `driver/harness/packets.py`：`PACKET_NAMES`（由 `MinecraftPacketIds.h` 全量生成的
  256 项 id→名字表）、`decode_packet(id, bytes)`、`decode_row(packet_bytes_row)`。
  未知 id 返回 `{id,name:"id_N",hex}`，不抛异常。
- 已实现布局：
  - `77 CommandRequest`：命令字符串、origin 类型/名字/UUID、requestId、playerId、版本。
    **用实机抓到的 77 包验证**（`/give @s minecraft:stone 5`，origin=ClientAutomation）。
  - `36 PlayerAction`：runtimeId varint、action varint、x/y/z float、face varint。
  - `19 MovePlayer`：runtimeId、位置、pitch/yaw/headYaw、mode、onGround、ridden、tick。
    36 是客户端→服务端；19 需要另一个玩家才会在单机出现，所以两者仍然只有合成样例单测
    （本客户端移动走 PlayerAuthInput(144)）。H1-C 的接收侧只实测到 9/13/65/111，
    这些 id 暂无解码器，保留 hex。
- B3 存的是 payload 字节，不含包 id varint；解码器从第一个字段开始读。
- CLI：`py -3.11 -m driver.harness.cli packet-decode --id 77 <hex>`。
- `driver/packet_dump.py` 把身份环 + 字节环按 seq 拼起来，解码后写
  `out/packets-<ts>.jsonl`（行内带 `dir`）；`--directions send,recv` 时实测 5 秒
  105 行 send、54 行 recv。

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

## 封包接收侧 tap（H1-C）

- 目标：补上服务端→客户端方向。单机里集成服务端同进程运行，回环投递不经过
  `NetworkConnection::receivePacket`（H1-B 实测 0），所以要挂投递层。
- 两层锚点，共 9 个钩子，`packet_capture {directions:["send","recv"]}` 才安装：
  1. `Packet::sendTo(Player)` / `sendTo(Actor, optional_ref)` / `sendTo(BlockPos, DimensionType, optional_ref)`、
     `Packet::sendToClient(NetworkIdentifier, SubClientId)` / `(NetworkIdentifierWithSubId)`、
     `Packet::sendToClients()` / `(exceptId, exceptSubId)`——服务端常用的发送辅助；
  2. `LoopbackPacketSender::$sendToClient(NetworkIdentifier, Packet const&, SubClientId)` 和
     `...(UserEntityIdentifierComponent const*, Packet const&)`——实际回环投递。
- 实测（1.26.51.01，超平坦，3.5 秒）：layer 1 **全部 0 hit**；layer 2
  `sendToClient` 66 hit、`sendToClient(user)` 4 hit。说明服务端代码直接走 sender 虚函数，
  不经过 `Packet::sendTo*` 辅助。`packet_stats.recv_anchors` 保留每个锚点的原始 hit 数。
- 接收方向主序列：`MoveDeltaActor(111)`（附近实体移动）、`AddActor(13)`、
  `LegacyTelemetryEvent(65)`、`Text(9)` 等。`/tp` 成功消息出现在 `Text(9)` payload 的
  JSON 原文里（`commands.tp.successVictim...`），可用来验证字节层。
- 去重：两层可能对同一包各触发一次；`onPacketReceived` 用
  “同 tick + 同 Packet 对象 + 同 id”折叠重复，广播到多个客户端只留一条身份记录。
  原始投递次数看 `recv_anchors`。
- 记录与字节环都新增 `dir` 字段（`"send"`/`"recv"`），`packet_drain` / `packet_bytes`
  原样返回；`selftest` 有 5 条 recv 检查（开启、记录、统计、字节、dir 字段）。
- 字节层：`serialize_ids` 对接收方向同样生效；`Packet::write` 是 const，在投递钩子里
  调用是安全的，白名单与 denylist 规则和发送方向一致。

## 封包身份 tap（H1 阶段 A）

- 状态（身份环、字节环、配置、统计）和 send/recv 共 9 个钩子都在
  `PacketTap`（`PacketTap.h/.cpp`）；`ProbeMod` 只保留 `onPacketSent` /
  `onPacketReceived` 转发，以及命令包模板注入。
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
- `thread="all"` 会给 100+ 个线程装 DR，实测导致游戏崩溃，mod 现在直接拒绝该模式；
  `status.server_thread_id` 在第一次 `queueServerTask` 后可用，`thread=server|both` 用它。
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
  在 `safeTickPre` 的 `tickInputInjection()` 里 drain；`via=hid`（默认，Virtual）/
  `hardware`（Hardware 且带 `mInjectingInput`）/`raw`（Hardware 不带注入标记，等价真人按键）/
  `queue`（`Keyboard::_inputs` 直推）。双击空格必须走 Hardware origin（脚本用 `hardware`；
  锁关着时 `raw` 也行，锁一开就被丢），`raw` 平时只用于验证 `input_lock`（见「真实输入锁」）。
  强能力由 `allow_input_inject` 门禁（默认 false），每次注入写 boot log。
- **鼠标（G2）**：`MouseDevice::feed(action, data, x, y, dx, dy, force)` 是 `MouseInputEvent`
  的发布入口；`mouse_event {action,...}` 同样在 tick 里 drain。
  `action=move`(0) + dx 能转向；`left/right` 只更新 `MouseInputEvent` 计数，在 1.26.51.01 上
  **不触发世界攻击**（攻击绑定不在这一层），攻击仍用 `click`/`attack_entity`。
- 后台守卫：`allowTurnDelta()` 放行 `mInjectingLook` 和注入鼠标转向后的 2 tick
  （`mInjectTurnUntilTick`）。注入标志只在调用期间短暂置位，避免整帧放行真实鼠标。
- **sprint 对照（G3，历史）**：`driver/sprint_compare.py` + `scenarios/sprint-compare.json`。
  旧对比里 B（直接 `set_input` 标志）0.2875、C（ctrl+W 事件）0.2806，基线 0.2805；
  说明事件层能复现疾跑。2026-10-28 起默认统一走 Hardware ctrl+W，B 只在 `force=true` 下保留。
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
- `PipeChannel` 独占 pipe/event 句柄，所有 pending I/O 在返回前取消并回收；connect/read/write
  等待每 25 ms 检查停止标志，`disable` 只需 join，不再存在一次性 CancelSynchronousIo 的窗口。
  发送合并成最多 64 KiB 的缓冲批次，大行复用其已有字符串；hello 同样走完整写入与错误处理。
  单次写入预算 5 s，超时断连并记录 `status.pipe_write_timeouts`；客户端可重新连接。
  `CommandFramer` 同时限制完整/分片行，不再每次重扫旧尾行或逐行 erase 前缀。
- 启动/管道状态写到 mod 目录下的 `harness-boot.log`（`load`、`enable`、`pipe: ...`），
  因为 LeviLauncher 的日志位置随启动方式变化。
- Python 侧读取用 `PeekNamedPipe` 轮询而不是阻塞 `read`：Windows 的同步管道句柄会把
  挂起的 `ReadFile` 和 `WriteFile` 串行化，阻塞读会让同一条连接上的发送永远等下去。
  每次请求都有超时（`ModTimeout`），连接关闭抛 `ModDisconnected`，协议错误抛
  `ModProtocolError`；`reconnect()` / `wait_ready()` 用于游戏重启后恢复。

## 单活动任务与会话所有权

任务类型、控制器与固定事件队列在 `TaskModel.h`，游戏计算在 `Tasks.cpp`，JSON 与 use
服务器调度在 `TaskEvents.cpp`。控制器统一拥有活动状态；替换/取消/tick/服务器副作用
共用同步边界。消费任务事件复用 spool 线程，tick 内不再分配任务 JSON/字符串。
终结事件带 task_id，取消后旧服务器回调直接跳过。任务 AV 在锁内被 SEH 转成失败，
`selftest_task_fault` 只在 allow_selftest_stress 开启时可用。

Python 每次连接创建 `_PipeSession`，旧读取线程不能访问新队列。`request` 将非目标事件
保留给 `readline/wait_event`，任务等待按 ID 关联。详细限制与迁移见 `architecture.md`
和 `refactoring.md` 的“行为重设计”。

`Chat.cpp` 将 `GuiData` 的聊天缓存作为只读快照开放。管道只提交一次客户端 executor
工作，客户端线程复制定长文字，spool 线程格式化 JSON。单槽 mailbox 的
Idle → Requested → Ready → Idle 通过 acquire/release 发布；繁忙时明确拒绝，
不增加 tick 锁或在 tick 格式化聊天 JSON。消息优先使用过滤后的文字，返回最近最多 64 条，
每条 2047 UTF-8 字节并带 truncated；不返回账号标识。此接口读取游戏保留的缓存，
不保证永久历史，也不把 `command_done` 的即时结果当作 deferred 命令最终效果。

## 实测数字速查（1.26.51.01 创作存档）

从 `mod/README.md` 迁入的逐项实测记录；与上文各专题小节互为补充（上文讲设计，这里记数字与复现要点）。

按主题归档的稳定结论；逐轮验收的执行记录（其中的通过数会随时间过时）见 git 历史
与 [`../docs/refactoring.md`](../docs/refactoring.md)。当前基线：live selftest **222/222**
（`allow_input_inject`/`allow_selftest_stress` 开启）、离线单测 **220/220**。

### 客户端 I/O（2026-10-28；离线）

- `protocol.LineDecoder` 只扫描新收到的字节，每次 feed 完成后统一压缩缓冲，
  移除逐行的额外 `bytes` 拷贝；UTF-8 替换、CRLF、空行及 pending 的行为不变。
- `RecordingCommands.record` 用现有 `wait_event` 保留异步通知，避免将同一条延后事件
  反复读回；移动发送或 sample 回调失败时 best-effort 发 stop 后保留原异常。
  整次录制/分页共用 `exchange`，防止其他请求交错消费响应。
- `record_dump_all` 按首个 head 限定后续分页大小；非空页游标不前进时抛
  `ModProtocolError`（仍属 `ModPipeError`），空页终止，不无限追赶正在增长的 head。
- `spool` 两个入口复用逐行解析；非法 dropped 计数、超长整数、坏 JSON 和过深 JSON
  计入 bad_lines 并继续处理。基于已打开文件的 fstat 限定读取窗口，成功读取后才提交
  offset/rotation；临时 I/O 失败保留状态供重试，轮转为空时清除 pending。
  `read_spool(max_bytes=0)` 返回空，负数报 ValueError；poll 单次仍最多读取 4 MiB。
- `py -3.11 driver/selftest.py --client-io-only`：**20/20**；此入口只跑本地回归夹具，
  不连接游戏。全量离线单测 **220/220**，ruff lint/format、mypy（79 文件）全绿。
  完整 live selftest 本轮未运行；没有构建/替换 mod 或执行逆向操作。

解码微基准：Windows / Python 3.11.9，对比提交 `27cb2d3`，9 轮中位数；随机种子
20261028 的 1000 组字节流输出与 pending 全部一致。以下仅表示 Python 解码吞吐，
不代表游戏 tick 性能提升。

| 输入 | 改前 | 改后 | 吞吐倍率 |
| --- | ---: | ---: | ---: |
| 1 MiB 长行，256 B 分片 | 52.091 ms | 2.003 ms | 26.00x |
| 20,000 行，一批输入 | 10.476 ms | 7.782 ms | 1.35x |
| 20,000 行，4 KiB 分片 | 10.406 ms | 7.951 ms | 1.31x |

本地基准脚本与日志：`out/bench-client-io.py`、`out/optimization-client-io-bench.log`
（忽略的运行产物）；可复现的功能边界已保留在上述 selftest 入口和 tests 中。

### Mod 核心（2026-10-28；离线与现场）

基于 `bc4e70d` 审阅和修改现有 C++ 源码，未执行逆向操作或打开 IDA；离线验收后保存退出世界、
备份旧 mod 与配置，安装新 DLL 并重启进入“超平坦测试”补做限定范围的现场自检。
新增 `PipeChannel`、`TransportModel`、`RecordModel` 三个不依赖 SDK 的内部头，分别负责
I/O 资源生命周期、线协议缓冲、录制并发模型；仅相应领域 TU 引用，`Internal.h` 保留公共入口。
wire 协议仍为 4，记录仍为 64 B；状态仅新增管道超时计数/预算。

复现入口：`py -3.11 driver/selftest.py --mod-core-only`，**3/3** native 集合：

- 分帧覆盖所有分块大小、UTF-8 字节和 CRLF、空行、恰好上限/超过上限、连续丢弃、
  同块恢复与连接重置；批量发送检查顺序、大行、空行、失败短路及复用缓冲清理。
- Windows 真命名管道传输 256 KiB；取消未连接等待、pending read、pending write；
  非读取客户端在 40 ms 测试预算下超时；取消/超时路径断言在 1.5 s 内返回。
  重连后可再次读写，100 次 open/析构前后进程句柄数一致。
- 录制检查降采样、period 边界、停止/初始化失败/异常展开、采样零分配；
  阻塞中的 start 等待旧 capture 退出后 head=0；100,000 次写入期间并发覆写读取，
  以及 **2500 次**会话重启均未读到撕裂字段或上一会话的数据。

LLVM22 / `xmake -j4 -y` 完整 mod 编译链接通过；离线单测 **223/223**，ruff lint/format、
mypy（80 文件）通过。此处的 native 测试使用生产模型和真实 Windows pipe API，
不连接 Minecraft；完整 live selftest **222/222** 仍是既有现场基线，本轮未重新验证。

现场复现入口：`py -3.11 driver/selftest.py --mod-core-live-only`，**33/33** 通过：

- 协议 4、加载的 5000 ms 写预算、状态查询、未知命令兼容；一批 64 个 ping、逐字节 UTF-8/CRLF，
  完整/分片超长行均只报一次 `line_too_long`，下一行 ping 正常。
- legacy JSON 录制 8 行；二进制 period=1/3 分别录到 19/7 行，JSON/hex 逐条相同，
  分页导出一致，停止后 head 不再变化；连续 12 次重启无上一会话样本。
- 74 行录制作为固定输出，原始管道客户端发送 64 次 dump 且不读响应，保持句柄打开 6.5 s；
  写超时计数 **0→1**，旧句柄尚未关闭时已可重连，tick 继续推进、世界状态可读。
- spool 无损坏 JSON 或 `internal_error`，另做 3 次重连均可 ping。没有调用内存/watch/hook、
  故障注入或移动命令，也未改方块、背包或生成实体；未启用额外 capability。

安装 DLL 与构建产物 SHA256 一致：
`5124d90c8e9a6c44fb140eae11f6c242b18e91256b2cbebcf8aeea898b75ee4f`。
备份在忽略的 `out/mod-before-live-20261028-213450/`；原 `harness.json` 保留。

传输微基准（Windows / clang++22 `-O2`，9 轮中位数，与原分帧算法比较）：
512 KiB 长行按 256 B 分片为 **8.838 → 0.537 ms（16.45x）**；
20,000 行按 4 KiB 分片为 **1.359 → 0.329 ms（4.13x）**。
同样 760,000 B / 20,000 行，批量写回调从 **20,000 次降至 12 次**，字节数与顺序一致。
回调次数用于衡量批量策略，不是实际系统调用或游戏帧率测量；部分写入可能增加实际调用次数。

验证日志在忽略的 `out/`：`mod-opt-native-selftest.log`、`mod-opt-tests-accepted.log`、
`mod-opt-build-accepted.log`、`mod-opt-bench.log`；微基准源码为 `out/mod-core-bench.cpp`。
后续现场日志为 `mod-core-live-selftest.log`，最终离线复核为 `mod-core-final-unittest.log`。
增加约 4 MiB 发布序号是为并发覆写安全付出的固定内存成本，未改变环容量或 wire 布局。

### 实验辅助（`driver/harness/lab.py`）

- `Builder.build`：命令 → 回读 → 不一致重试；`poke` 用邻块更新唤醒命令放置的液体。
- `RecordSession`：每个 session 从 `since=0` dump（`record_start` 会重置环形头）。
- `move_and_wait`：`set_input` 立即返回，等满输入窗口再返回。
- 纯函数 `fit_recursion` / `recursion` / `steady` / `segments` 有离线单测（`tests/test_lab.py`）。

### 起跳方式（2026-10-28 更新）

- 默认：`jump` / `set --jump` / 任务 / 宏回放都按当前 jump 绑定（默认 Space）发
  Hardware HID 按键；`jump_motion_matches_space` 逐位对比通过。
- `input_cfg jump_impulse` 默认 0；非零（1=jumpFromGround，2=setJumpPendingScale，
  3=两者）是 legacy 直接冲量，只在 `force=true` 下用于复现旧测量
  （selftest `jump_impulse_guard_*` / `legacy_input_force`）。

### 游泳/热键驱动（2026-10-24）

- `lab.swim_drive`：HID 前进+疾跑键 → 轮询 ADF bit57（`fnv1a("ActorDataFlagComponent")`，
  30 B 内位 57）；深水离底 4–5 tick 进入，松键即退出。
- `lab.jump_key`：拍空格（HID Space），与 `jump` 的 jumpFromGround 弧线一致（selftest 对比）。
- selftest `swim_drive_entered` / `swim_drive_dive`（俯冲 $v_y<-0.15$）/ `swim_stop_clears`。

### 真实输入锁（input_lock，2026-10-24）

- 三个拦截点：`HIDControllerGameCoreDesktop::$onKeyDown`（`InputOrigin::Hardware` 且
  **不带** `mInjectingInput` 时丢弃）、`MouseDevice::feed`（`mInjectingInput` 之外一律丢弃）、
  `allowTurnDelta`（锁定时非注入转向返回 false）。
- `key_event` 的四条注入路径与锁的关系：`hid`=Virtual（永远放行）、`hardware`=Hardware +
  注入标记（放行，双击空格必须用它）、`raw`=Hardware 无注入标记（被丢弃，等价真人按键，
  只用于验证锁）、`queue`=`Keyboard::_inputs` 直推（不经过这个钩子）。
- 配置 `lock_input`（默认 false）；命令 `input_lock {on}`；selftest 自动上锁并在 finally 解锁。
- 验证：`input_lock_on` / `input_lock_blocks_hardware`（`via=raw` 数字键不改槽位）/
  `input_lock_allows_injection`（Virtual 键改槽位）/ `input_lock_allows_mouse_injection`。
- （2026-10-24 已修）曾偶发在 tick 线程 `applyEcs` 枚举 registry 时崩溃，见下节「ECS 线程归属」。

### 双击空格飞行与 `/togglefly`（2026-10-25）

- 能力门是 `AbilitiesIndex::MayFly`；生存/创造/冒险都能被 `/togglefly` 或管道 `togglefly`
  打开（三模式实测），旁观模式拒绝（`spectator_not_supported` / `success=false`）。
- `CommandFlagValue::NotCheat` 生效：把世界的 `commands_enabled` 写成 false 后，聊天框输入
  `/togglefly` 仍然执行（`state.mayfly` 翻转，聊天回显正常），selftest `togglefly_not_cheat`。
- 开关能长期保持（创造关掉后 200 tick 仍为 false，生存打开后 200 tick 仍为 true），但
  **切换游戏模式会重置成该模式默认值**（生存/冒险 false、创造 true）：生存开启 →
  切创造（仍 true）→ 切回生存（变 false）。所以模式切换后要重新开。
- 切换只认 `InputOrigin::Hardware`：`via=hid`（Virtual）只起跳、从不翻转 `flying`；
  每次按下必须跨至少 1 tick（同 tick 内 down+up 被丢掉）。实测 hold=1/2 × gap=1/2/3 全部生效，
  hold=3 开始出现不生效样本，所以 `lab.double_space_fly` 默认 hold=1、gap=2、最多重试 3 次。
- 游戏内命令注册时机：客户端 mod 在 `enable()`（主界面阶段）时服务器命令表还不存在，
  直接 `getOrCreateCommand` 会 `0xC0000005` 崩在启动阶段（crash dump `tick=0`）。
  正确位置是 `ServerCommandRegisterEvent`；命令表按世界重建，用 `CommandRegistry*` 身份判重，
  避免同一张表重复挂 execute（重复执行会互相抵消）。
- 只注册服务端侧：能力是服务端权威，客户端侧命令会把客户端副本翻转后被同步覆盖；
  玩家在聊天框输入的 `/togglefly` 仍然会作为 `CommandRequestPacket` 到达服务端（实测命中）。
- 管道侧 `togglefly` 走 tick 线程写 LocalPlayer，再 `queueServerTask` 镜像服务端玩家，
  并保留 8 tick 重断言（模式切换后迟到的能力同步会覆盖单次写入，2026-10-25 实测）。

### ECS 线程归属（2026-10-24 修复）

- 根因：`findActorByRuntimeId` 走 `bedrock::getLevel()` 的 actor 列表（单机里是服务端），
  而 `applyEcs` 在客户端 tick（`safeTickPost`→`runCaptureRequests`）里枚举该 registry；
  服务端线程并发增删组件/实体 ⇒ `storage()` 迭代时 AV（崩溃栈：`applyEcs+0x129C` [Ecs.cpp:182]）。
- 修法：runtime-id 的 `Entity`/`Components`/`Component` 三类请求改由 `queueServerTask` 在服务端线程
  执行（tick 之间 registry 静止）；`runEcsGuarded` 用无析构对象的 POD 包装 `__try/__except`
  兜底，结构化异常回 `internal_error, phase=ecs`。
- `Registries`/`Context` 仍走客户端 tick（客户端 context，同线程；Context 本就有 SEH）。
- 验证：`ecs_entity`（115 组件）/`ecs_components`（115，94 有尺寸）/`ecs_component`（StateVector 36B）
  全部通过；selftest 221/221。

### 行为契约与加固

- 任务生命周期：单活动槽、ID 关联、统一取消；`TaskModel.h` 定长状态、tick 只填
  `TaskStep`、spool 线程格式化事件。自检入口 `check_task_lifecycle`；受控故障入口
  `selftest_task_fault` 需要 `allow_selftest_stress`。
- Python 会话：旧读线程与新会话隔离；请求保留异步事件，缓冲有界并显式报溢出。
- `chat_history`：客户端线程定长快照（最多 64 条、每条 2047 UTF-8 字节、截断显式
  标记），后台格式化。不是永久日志，重启或游戏清理的历史无法追回。
- `navigate`/`route` 共用 `walkToward` 驱动（转向/接近减速/卡住检测/跳跃冷却），route
  的边沿前瞻可按 waypoint 配 `edge_lookahead`；小容差下不再绕圈。
- `state.effects` 仅报告实际效果，过滤游戏清除后留下的 NO_EFFECT 空槽。
- 数值入参统一有限性 + 范围校验：`look`/`teleport`/`reset`/`click`/`move`/`set` 越界回
  `bad_param`，`blocks`/`scan`/`place` 的坐标超界回 `bad_coord` 或跳过该条；
  `wrapDegrees` 另有非有限兜底。
- `ecs_context` 对外部指针先 `readableRange` 预检、访问链整体 SEH；坏地址回 `bad_addr`
  而不是崩游戏。
- 宏安全：回放中 `macro_load` 先停回放（回执带 `stopped`）并按当前宏尺寸校验循环
  重启点；`macro_stop` 同时清掉 pending start。
- Watch：VEH 无锁读 armed 线程表；挂起窗口内不做任何分配；`thread="all"` 回
  `thread_all_unsupported`；`rw` 观察点不要求目标页可写。
- spool 打开/写失败回灌重试并计数；spool 线程整体异常隔离；`hook_va` 安装留 bootLog
  审计；seqlock 读侧有重试上限（写者卡死时降级为旧快照）。
- 实体链路：查找/快照/攻击全部在服务端线程执行（攻击用服务端玩家的 GameMode），
  客户端 tick 线程不遍历 `getRuntimeActorList()`。

### 移动、跳跃与输入
- 步行注入峰值 **0.2159 格/tick**（原版 4.317 m/s）；`example.json` 60 tick 位移
  12.04 格、接地 100%
- 跳跃：起跳沿调 `LocalPlayer::jumpFromGround`，逐 tick 位移 0.4200、0.3332、0.2481、
  0.1648…，顶点 1.252 格、滞空 11 tick；按住且接地时补调 `jumpFromGround`，连续 4 跳
  与 HID 空格绑定的逐 tick 轨迹误差 < 0.0001 格（不补调会出现前 3 tick 不动、
  第 4 tick 一次性 ~0.99 格的“卡回弹”）。
- 潜行：20 tick 前进 4.316 格，潜行时 1.295 格（约 0.30x）
- `fly` 同时写服务端，传送后不会自己恢复飞行
- `click left` 破坏、`click right` 放置；`select_slot` 3→空手、0→草方块
- 服务端传送 2 秒后位置仍是目标值，`yaw/pitch` 生效
- `command` 的 `fill`/`setblock` 生效，`blocks` 能读回
- macro 播放/录制；真实鼠标左右键录进 macro（左键 4 tick、右键 3 tick 为 true）
- `driver/run_mod.py` 跑通 `setup-platform.json` 和 `example.json`

### 闭环任务、世界与场景

- `navigate`：约 5.2 格直线，27 tick 到达，误差 0.26 格
- `mine`：`setblock` 放石头后 1 tick 挖掉（创造模式），`blocks` 读回空气
- `place`：放回同一格，3 tick 完成，回读为石头
- `use`：末影珍珠服务端层数 2→1、客户端背包同步为 1，并触发约 10 格传送
- `worlds` 返回存档列表；`enter_world` 从主菜单直接进"超平坦测试"（intent 0），
  `leave_world` 回到主菜单
- `harness.json` 的 `auto_enter` 在直接启动 exe 后自动进世界
- `options_set` 修改 `gfx_gamma` 实时生效（画面亮度变化），并写回 `options.txt`
- `driver/dev.py run --world ... --build` 一条命令完成编译、重启、进世界
- tick 事件带 `jump_edge` / `in_air` / `block_below`；`reset` 清零速度并让两次重试落点
  误差 < 1e-4 格
- 后台鼠标守卫：游戏不在前台时 `yaw/pitch` 不被鼠标改变，光标保持可见；mod 自己的
  `look` 仍生效
- `driver/parkour.py`：跳跃分段、checkpoint、两次运行对比、`attempts` 2/2 成功
- spool：录制中 `taskkill /F` 掉游戏，spool 里仍有崩溃前最后一条 tick 记录（读取端
  最后收到 57 tick，spool 写到 62，未丢 tick）
- 崩溃取证：`selftest_crash`（需 `allow_crash_test`）生成 `crash-<pid>-<tick>.dmp` 和
  `crash-<pid>.txt`，后者含异常码/地址/线程/最后命令/tick/位置/spool 路径
- 请求隔离：`selftest_av` 回 `internal_error`，随后 tick 继续增长
- 二进制记录环：连续 **10 分钟**写入 **12,082** 条，`record_dump` 的 JSONL 与 hex 解码数
  都等于 `head`（`lost=0`），`queue_dropped=0`；环固定 32 MB，RSS 在系统 trim 后由
  316 MB 回填到 369 MB（游戏自身内存，环不增长）

### 观察点、hook、ECS 与版本 profile

- `hook_install` 按符号 `MobJump::setJumpPendingScale` 装钩成功；`hook_call` 触发后
  `hook_drain` 读到 `args[0]=EntityContext*`、`b_float=1.0`、返回地址和 4 帧栈；
  `hook_rm` 后 `hook_list` 为空。VA dry_run 对同一地址返回 `executable=true`，
  对 `0x10` 返回 `false`
- `diagnose`：4 个锚点（normalTick/_applyTurnDelta/getHealth/isOnGround）24 字节全部匹配，
  `build_id=1.26.51.1`、`version_ok=true`
- 版本 profile（移植）：`GeneratedProfiles.h` 由 `docs/offsets/*.json` 生成，C++ 里不再有
  版本号、签名、结构偏移和组件 id。实测 `profile.match=exact`、`anchors_matched=4/4`、
  `struct_fields=12`、`components=1`；`profile_probe 1.26.51.7` 选中同一 profile（`prefix`），
  `9.9.9.9` 回 `none`。`profile_dump` 导出的 `profile` 可直接保存为新的 offsets JSON。
  `allow_version_mismatch=true` 时 `hooks_allowed=true`（`compat=true`），默认关闭。
- 负路径：把其中一个内置签名字节改掉后重启 → `version_ok=false`，
  `version_failures=["LocalPlayer::normalTick"]`，`hook_install`/`record_start`/`watch_set`
  全部回 `version_mismatch`，游戏仍存活；改回后 4 个锚点恢复匹配
- `overhead`（空闲，1024 样本）：`hook_p50=10 µs`、`hook_p99=38 µs`、`tick_p50=50.006 ms`、
  `tick_p99=50.970 ms`、20.0 Hz；同一场景改前 `tick_p99=54.596 ms`、`hook_p99=30 µs`
  （tick p99 未劣化）。注入 1 ms busy ×20 tick 后 `hook_p99=1013 µs`、`hook_max=1040 µs`
- 请求块合并成 seqlock 后，tick 读路径不再取 `mInput/mLook/mTeleport` 三个 mutex；
  管道线程仍是单写者（`mRequestSeq` 奇偶校验）
- `line_too_long`：发 2 MiB 无换行 → 收到 `error/line_too_long`（1 次），补一个换行后
  `ping` 仍正常，进程不增长
- `panic all`：先装 1 个 hook + 1 个 watch，`panic` 后两者列表都为空、任务空闲、
  tick 继续增长
- `ecs_entity`/`ecs_components`：猪实体读到 **113** 个组件（77 个能推断 stride），其中一个
  `stride=36`（StateVector 大小），`ecs_component` 按 36 字节读出 hex；客户端 registry
  有 512 个组件 storage
- `state.effects`：`/effect @s speed 10 2` 后读到 `id=1, amplifier=2`
- 存档点：`checkpoint_save` → 传送走 → `checkpoint_load` 落点误差 **0.000** 格
- `scan` 3×3×4 区域返回 6 个非空气方块
- `player_landed`：抬升 5 格后落地，`fall_distance=4.92`，血量 20→18（摔落伤害）
- `probe kind=block/legacy` 对同一坐标返回的名字与 `blocks` 一致；`legacy.hash` 等于
  名字的 FNV-1-64
- `watch_set` 观察 tick 计数（size=8, mode=w, thread=tick）：20 tick 捕获 20 次，
  命中 tid 等于 tick 线程；`watch_drain` 每行带 `code_hex`（32 位 hex），`rip` 是下一条指令
- offsets 表（F5）：`diagnose.structs` 列出 6 个字段/1 个组件；把 `offsets.json` 里
  `BlockLegacy.BlockId` 改成 `0x17C` 后 `probe legacy.block_id` 从 1 变成 0，改回即恢复；
  设成 `null` 后 `probe legacy` 回 `error/no_offset`，`probe block` 不受影响
- spool（F6）：每次启动使用 `spool-<pid>.jsonl`；若同 PID 的旧文件非空则改名为
  `spool-<pid>-<n>.jsonl`，单文件超过 64 MiB 也会轮转；swap+write 由同一把锁串行化
- 记录环（F8）：方块采样走数字 id + 256 项哈希缓存，稳态零 `std::string` 分配；
  y 超出 [-64,319] 时不读方块、三个 block 字段为 0 并置 `flags & 0x800`
- `overhead`（F9）：新增 `tick_interval_p50/p99/max_us`（与旧 `tick_*` 同值）、
  `window_samples=1024`、`qpc_resolution_ns=100`、`consistent=true`

### 输入注入对照（G1–G4）

- G1：`key_event(2)` 走 HID `onKeyDown(Virtual)` → `selected_slot` 1→2，LL `KeyInputEvent` +1；
  100 次注入事件计数恰好 +100、按下状态可读回；E → `inventory_screen`，Esc → `hud_screen`
- G2：左键 down+up → `mouse.counts[1]` +2、`last_action=1`；`move dx=200` → yaw +25.99°；
  左键没有触发世界攻击（攻击绑定不在此层）
- G3：`driver/sprint_compare.py` 三组对照 B=0.2875、C=0.2806 格/tick（基线 0.2805），
  结论：事件层注入能复现疾跑，1.3x 差异不在输入事件层，G5 不启动
- G4：`macro_run event_level=true` 三次回放落点误差 **0.0000**；左右键事件各 +2；
  旧路径（`event_level=false`）仍走 GameMode，MouseInputEvent 计数为 0

### 封包层（H0–H4）

- H0.1：对 StateVector.vy（offsets 表 `VelY=0x1C`）设写观察点，原地单跳后采样 25 tick；
  唯一写者是**集成服务端线程**（`thread="both"`），`code_hex` 反汇编为
  `mov qword ptr [rbx + 0x18], rax`（覆盖 0x1C）
- H0.2：同一坐标 `blocks` / `probe block` / `probe legacy` 名字与 FNV-1-64 一致；
  51 版方块 id：air=0、grass_block=2、water=9
- H0.3：hook 捕获的 `EntityContext*` == `state.context`；`ecs_context` 从两个来源解析到
  同一个 StateVector 地址、count=2、size=36
- H0.4：`driver/spool_forensics.py panic|taskkill|verify`；panic 与 taskkill 都单调连续
  （taskkill 丢失 0 tick）；leave_world 需桌面点击，脚本明确返回不支持
- H1 阶段 A：开启后 3.5 秒抓到 **122** 条客户端→服务端包，`seq` 0..121 无丢失，
  tid 固定 11684；主序列 `PlayerAuthInput(144)` + `SubChunkRequest(175)`，
  另有 Interact(33)、InventoryTransaction(30)、PlayerSkin(93) 等。`denied` 计数 >0，
  `packet_capture(ids=[1])` 回 `denied_id`
- H1 阶段 A 的字节层结论：`LocalNetworkPeer::$sendPacket` 在本构建里是 dllimport-only，
  链接不到导出 thunk ⇒ 不能按符号在 NetworkPeer 层拿序列化字节（`peer_hooked=false`）；
  字节若要拿，只能走服务端接收侧（B2）或白名单序列化（B3）
- H3 预演：B（`set_input` sprint）与 C（ctrl+W 事件）各 3 秒的 id 序列几乎相同
  （B: 144×65/175×3，C: 144×66/175×5）⇒ 身份层没有差异；结合 G3 的速度结论，不需要 packet tap 定案
- H1-B 地址解析：`BatchedNetworkPeer::$sendPacket` = `Minecraft.Windows.exe+0x46EB60`
  （无 thunk）；`LoopbackPacketSender::$sendToServer` 锚点 `+0xD809E0`，跟随 3 跳落到
  `dmf.dll+0x8AC10`（说明已被 dmf/LL 链式 hook）。`LocalNetworkPeer` 仍只有导入声明。
- H1-B 决策表（走/跳/命令）：`sendToServer` 有流量；`BatchedNetworkPeer::sendPacket`
  与 `NetworkConnection::receivePacket` 均为 **0** ⇒ 单机回环绕过字节层，走 B3。
- H1-B B3：`packet_capture {serialize_ids:[144]}` 后 `packet_bytes` 拿到
  `PlayerAuthInput` 的 91–93 字节，`seq` 与身份记录一一对应（32/32 匹配）；tick 19.7–20.0，
  `dropped=0`。序列化只在白名单 id 上发生，关闭 capture 时零调用。
- H2：`chat_send` 复制捕获到的 `CommandRequestPacket(77)` 模板，只替换命令字符串后
  走 `Packet::sendToServer()`。实测 `/setblock` 回读为 `gold_block`、`/tp @s ~1 ~ ~`
  位移正好 1.000 格、`/fill`/`/give` 生效；注入前后 tick p99 不变（19.8 Hz）。
  捕获模板需要先用 `/` + `text_input` + Enter 发一条真实命令；客户端命令还要求
  `op_self`（世界 commands 已开启时仍可能是 Member 权限）。
- H1-C 接收侧 tap：`directions:["send","recv"]` 时额外装 9 个钩子。单机里流量全部来自
  `LoopbackPacketSender::$sendToClient`（3.5 秒 66 hit）和 `...sendToClient(user)`（4 hit），
  `Packet::sendTo*` 辅助层实测 **0**。接收侧主序列 `MoveDeltaActor(111)`、`AddActor(13)`、
  `LegacyTelemetryEvent(65)`；`/say`/`/tp` 的 `Text(9)` payload 里能直接读到 JSON 原文。
  同一 tick 内同一 `Packet` 对象的重复投递会去重，per-anchor 计数仍保留原始次数。
- H4：`driver/harness/packets.py` 提供 `decode_packet(id, hex)` / `decode_row(row)`；
  id→名字表由 `MinecraftPacketIds.h` 全量生成（256 项，1.26.51.01），已实现 77/36/19
  三种布局，未知 id 保留 hex 不报错；CLI `packet-decode --id 77 <hex>`。
  77 的布局用真实抓包验证，36/19 用合成样例单测。
  `driver/packet_dump.py --seconds 5 --walk [--directions send,recv]` 导出
  `out/packets-<ts>.jsonl`（实测 105 行 send、54 行 recv）。
