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
