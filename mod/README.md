# probe — LeviPilot 客户端 mod

LeviLamina 客户端 mod（游戏 **1.26.51.01**），在 `\\.\pipe\mcbe-harness` 上提供 JSON Lines API。
Python 客户端是 [`../driver/transport_mod.py`](../driver/transport_mod.py)。

## 构建

需要 VS Build Tools 2022、Windows SDK、LLVM 22 和 xmake 3.1.2。本机完整路径和两个必须保留的
工具链补丁见 [`../docs/mod-build-environment.md`](../docs/mod-build-environment.md)。
GitHub Actions 运行 Python 检查和无 SDK 的 native 任务模型测试；完整 mod 构建依赖本机工具链。

```bash
cd mod/probe

export PATH="/d/Tools/LLVM22/bin:/d/Tools/xmake:$PATH"
export XMAKE_GLOBALDIR='D:\Tools\xmake-home'
export XMAKE_PKG_INSTALLDIR='D:\Tools\xmake-packages'

xmake f -c -y -p windows -a x64 -m release --target_type=client
xmake -j4 -y
```

产物在 `bin/probe/`：`probe.dll`、`probe.pdb`、`manifest.json`（client 平台）。

## 安装

把 `bin/probe/` 整个目录复制到实例的 `mods/`：

```
<versions>\1.26.51.01\mods\probe\
```

构建流程不会自动复制（游戏安装目录按只读处理）。

## API

管道 `\\.\pipe\mcbe-harness`，UTF-8 JSON Lines。

| 命令 | 说明 |
|---|---|
| `ping` / `status` / `state` | 心跳、状态、玩家快照 |
| `chat_history` | `limit`（1–64，默认 32）；客户端保留的聊天快照，含文字、类型、截断标记 |
| `mem_read` / `mem_write` | 读/写本进程内存（`addr,len` / `addr,hex`）；写需要 `allow_write=true`；坏地址回 `ok=false,reason=bad_addr` |
| `probe` | `kind=raw/block/legacy`；结构化读取 `Block*` → `BlockLegacy*`、名字哈希/字符串、block id 和两个 float |
| `watch_set` / `watch_clear` / `watch_list` / `watch_drain` | 硬件写观察点（DR0–DR3，size 1/2/4/8，mode `w`/`rw`，默认只装 tick 线程，`thread=all` 被拒绝）；`depth` 可选 1..32（默认 8）控制每次命中抓多少返回地址，深度越大 VEH 开销与环内存越高；`watch_drain` 的 `rip` 是触发后的下一条指令，附 `code_hex`=rip-16..rip |
| `record_start` / `record_stop` | 启动/停止定长二进制 tick 环（`period` 每 N tick 采样），返回 `record_id/ok/reason` |
| `record_dump` | `since,limit,format=jsonl/bin`；返回解码行或 64 字节记录的 hex 块，`next/head/lost` 及会话/失败元数据 |
| `hook_install` | `symbol` 或 `va`，`tag/sample/dry_run`；符号钩用 typed detour，VA 安装需 `allow_hook_va`；可选 `mem_arg(0..3)/mem_offset/mem_size(4..64, step 4)` 在 detour 内抓参数指向的短命缓冲 |
| `hook_rm` / `hook_list` | 卸钩 / 列出已装钩与命中统计 |
| `hook_drain` | `since,limit`；命中环（4 个整数参数、返回地址、4 帧栈、`b_float`）；开了内存采样时另带 `mem{arg,offset,len,ok,addr,hex,f32}` |
| `hook_call` | 调用一个已导出符号做自检（当前支持 `MobJump::setJumpPendingScale`） |
| `panic` / `unhook_all` | `level=hooks/tasks/all`；卸钩、清观察点、停任务并清输入，不杀进程 |
| `diagnose` | 版本/profile、4 个符号锚点的地址/24 字节签名匹配/耗时、能力表 |
| `profile_dump` | 导出当前实机的锚点字节、结构偏移、组件表，以及可直接保存的 profile JSON |
| `profile_probe` | `build_id`；不启动对应版本也能看它会选中哪个 profile（exact/prefix/none） |
| `signature_check` | `name,expected_hex`；对任意期望签名做比对（负路径自检） |
| `overhead` | tick/钩子耗时 p50/p99/max、`tick_hz`、`calls_per_sec`、`paths.{record,watch,hook}` 调用率 |
| `selftest_busy` | 在 tick 内忙等 `us` 微秒 × `ticks`（≤5 ms/200 tick），需 `allow_selftest_stress=true` |
| `ecs_context` | `context,component_id`；核对 hook 捕获的 `EntityContext*` 所属 registry 后在对应线程读组件（address/index/count/size/registry/scope） |
| `ecs_registries` | 客户端 EnTT registry 指针、组件 storage 数、entity id |
| `ecs_entity` | `runtime_id` → actor/context/registry/entity 地址、拥有组件数 |
| `ecs_components` | 枚举实体组件：运行时 `id`、index、地址、`stride`、storage `count`；单个 storage 探测失败时跳过并在 `skipped` 里报 id，不再整条命令回 `internal_error` |
| `ecs_component` | 读组件原始字节（`len=0` 依次取 profile 尺寸/stride/64，最大 512） |
| `spool_flush` | 立即把内存中的记录刷到 spool 文件 |
| `selftest_av` / `selftest_crash` | 自检：请求级访问违例被隔离 / 进程级崩溃取证（后者需 `allow_crash_test=true`） |
| `start` / `stop` | 逐 tick 采样（`tick` 事件） |
| `key_event` | `code,down,repeat,repeat_ms,via=hid/hardware/raw/queue`；`hid`=HID `onKeyDown`（Virtual）、`hardware`=Hardware 且标记注入、`raw`=Hardware 无注入标记（等价真人按键）、`queue`=`Keyboard::_inputs()`；需 `allow_input_inject=true`；接受回带 `input_id`，应用后发 `input_event_done/ok/reason` |
| `key_state` | `code`；游戏侧 `Keyboard::_states`、LL KeyInputEvent 计数、注入计数 |
| `mouse_event` | `action`（`move/left/right/middle/wheel/x1/x2/move_relative` 或 0–7）、`data/down`、`dx,dy,x,y`；走 `MouseDevice::feed` |
| `text_input` | `text`；通过 HID `onTextInput` 往当前输入框送字符（需 `allow_input_inject`）；queued 回带 `input_id`，独立 `text_input_done/ok/reason`；Python 可用 `text_input(..., wait=True)` 等完成 |
| `chat_send` | `text`；使用捕获的命令来源构造并发送 `CommandRequestPacket`（需 `allow_packet_inject`）；接受/完成均带 `packet_id` |
| `op_self` | `level`（0–4）；在服务端玩家上设置命令权限（H2 自检用，需 `allow_packet_inject`） |
| `commands_enabled` | 读/写世界的 `mCommandsEnabled`（`value` 可选） |
| `packet_capture` | `on,ids?,sample?`；只记录 `(tick,seq,id,tid)`，需 `allow_packet_capture=true`；denylist 硬挡 |
| `packet_drain` | `since,limit`；身份记录（无字节），`seq` 单调、`injected` 预留 |
| `packet_bytes` | `since,limit`；显式高成本重序列化诊断的 `{seq,id,size,hex}`（最多前 128 B），附 `source/captured_size/truncated` |
| `symbol_resolve` | `name`；编译期锚点 + E9/FF25 跟随，返回 `{anchor,real,module,rva}` |
| `violation_watch` | `on`（默认 true）、`addr`（**运行期**地址）、`mode=synthetic`；只读记录协议违规通知，需 `allow_hook_va` 且在世界内；`synthetic` 只验证记录设施 |
| `violation_stats` | 无参；`{active,address,callback_hooked,screen_hooked,inbound_hooked,inbound_hits,inbound_sampled,inbound_bytes,inbound_head,hits,synthetic,capacity}` |
| `disconnect_route` | `reason`（默认 90）；通过真实 `composeRoute` 渲染断线页路由，返回 `{route,length}`，用于验证 screen 侧记录 |
| `violation_drain` | `since,limit`；`{index,seq,tick,tid,source,response,verdict,packet_id,packet_size,error_value,category,context,detail,before_hex,context_hex,detail_hex,context_truncated,detail_truncated,synthetic}` |
| `violation_text` | `since,limit`；**整段原文**：违规 context 与错误页 details 复制进预分配 4 KB 槽（每类 8 槽），不受记录行上限约束；`{index,tick,kind,len,truncated,text}` |
| `inbound_drain` | `since,limit`；同一记录结构，`source:"inbound"` 的**解析入口切片**行；`context_hex` 从包 id 开始，`before_hex` 是切片前 16 字节（实测为无关堆数据，长度前缀不在其中） |
| `inbound_dump` | `path`；把入站环直接写成 JSON 文件（多客户端时管道回复可能被别的连接截走，取证用这条） |
| `packet_stats` | 记录数/丢失/denied/peer_sends/hook 状态 |
| `screen` | 当前界面名（`hud_screen` / `inventory_screen` / `chat_screen` / `pause_screen`） |
| `set` | `forward,strafe,sprint,sneak,jump,ticks`；默认读当前键位并合成真实 Hardware HID 按键（`state.bindings` 可见），`ticks=-1` 持续 |
| `look` | `yaw,pitch,relative,ticks`；`relative=false` 为绝对朝向 |
| `entities` | `radius,limit`，附近 Actor |
| `entity` | `runtime_id`；类型/名字/位置/速度/血量/距离 |
| `attack_entity` | `runtime_id`；在服务端线程用服务端玩家的 `GameMode::attack`（实体查找也在服务端线程） |
| `interact_entity` | `runtime_id`；转向实体并注入主手使用输入（best effort） |
| `look_at_entity` | `runtime_id`；把视角转向实体 |
| `blocks` | `positions:[[x,y,z],...]`，方块名和 `addr`（`Block*`，供 `probe` 使用） |
| `scan` | `from:[x,y,z],to:[x,y,z],include_air?,limit?`；最多 32768 格，每 tick 最多读取 512 格 |
| `checkpoint_save` / `checkpoint_load` / `checkpoint_list` / `checkpoint_delete` | 命名脚部坐标存档，持久化到 `mods/probe/checkpoints.json`；`checkpoint_load` 只恢复位置+朝向，不清药水效果 |
| `navigate` | `x,z,tolerance,max_ticks,sprint`；`jump=auto/distance/edge/never` + `jump_distance`，mod 内逐 tick 闭环修正朝向 |
| `jump_when` | `mode=now/tick/distance/edge`；`distance,forward,sprint,yaw,after_ticks,max_ticks`；在 tick 内精确起跳 |
| `route` | `waypoints:[{x,z,tolerance,max_ticks,jump,jump_distance,edge_lookahead}]`；逐段跑点，`route_waypoint` / `route_done` / `route_failed` |
| `mine` | `x,y,z,max_ticks`；`startDestroyBlock` + `continueDestroyBlock`，完成后回报方块 |
| `place` | `x,y,z,against?,face?,expected?,slot?,max_ticks`；瞄准、放置并回读验证 |
| `use` | `ticks`；等待后在服务端玩家上走 `Item::use`（+ 食物的 `useTimeDepleted`），消耗、效果和背包同步都是权威的 |
| `task_status` / `task_stop` | 查询或取消当前闭环任务 |

任务生命周期：所有六种任务共用单一活动槽；任何新任务都会替换旧任务。
`*_started`、完成事件、`task_status` 均带 `task_id`；取消发
`task_cancelled {task_id,task,ok:false,reason}`，reason 包括 `replaced/stopped/panic/reset/world_left/disabled/internal_error`。
新 Python 任务方法要求配套更新 mod，通过 ID 等待结果，取消抛 `ModTaskCancelled`。
`use` 等待服务端完成期间仍为 active，`phase=server_pending`，`server_timeout_ms=5000`。
预算从进入该阶段开始计时；超时回 `item_used {ok:false,reason:"server_timeout",task_id}`
并释放任务槽。取消、替换或超时后的迟到回调不会操作物品；已经进入游戏调用的回调不能被超时抢占。
投递失败回 `server_task_queue_failed`，回调异常回 `server_task_failed` 或 `server_task_fault`，均为同 ID 的唯一终结事件。
模式拼写错误、路线超过 64 点、非对象 waypoint、expected 超过 255 UTF-8 字节均明确拒绝，
不会改动当前任务。事件队列拥塞报 `task_backpressure`，消费端每 25 ms 处理定长任务记录。

| `worlds` | 列出缓存世界：`id` / `name` / `last_saved` / `game_type` |
| `enter_world` | `name`/`id`，可选 `intent`/`force`；先等 Xbox Live 登录，成功后发 `enter_world_settled` |
| `signin` | 主菜单的 Xbox Live 登录态：`known`、`signed_in` |
| `leave_world` | 退出当前世界回到主菜单 |
| `options_set` | `values` 对象或 `text`（`key:value` 行）；实时应用并写回 `options.txt` |
| `options_save` | 请求游戏自身保存选项（`options_set` 已包含落盘） |
| `click` | `button=left/right, reach`；左键破坏、右键放置 |
| `select_slot` | `slot=0..8` |
| `inventory` | 完整背包快照：`slots`、`armor`、`offhand`、`selected_slot` |
| `set_item` | `slot,item,count,aux,container`；`count=0` 清空；`container=inventory/armor/offhand` |
| `give` | `item,count,aux`；加到第一个空位 |
| `clear_item` | `slot,container` |
| `clear_inventory` | 清空背包、盔甲和副手 |
| `fly` | `enabled`；客户端与服务端分阶段设置，两侧完成后回 `fly_ok`；接受时另发 `flight_queued{flight_id}` |
| `togglefly` | `on`（可选，缺省=切换）；服务端唯一写 `MayFly`，客户端回读原生同步后回 `togglefly_ok{mayfly,gamemode,flying,flight_id}`，随后只读观察 8 tick；旁观回 `spectator_not_supported`。游戏内等价命令 `/togglefly` |
| `input_lock` | `on`；锁住真实硬件键鼠与相机转向（注入照常），见「限制」 |
| `teleport` | `x,y,z,yaw?,pitch?`；**脚部**坐标，服务端权威；**不动药水效果**；`teleport_id` 关联接受/唯一结果，忙时拒绝，2 秒截止 |
| `reset` | `x,y,z,yaw?,pitch?`；清空任务/输入/macro/药水效果后传送到脚部坐标，用于跑酷重试（只有它会清效果） |
| `command` | `command`（可带 `/`）、`player?`；服务端线程以本地 UUID 或显式完整玩家名执行，保留 `@s`/`~`；身份未知/玩家不存在明确失败，不改用控制台 |
| `macro_load` / `macro_run` / `macro_stop` / `macro_status` / `macro_list` | macro 播放 |
| `macro_rec_start` / `macro_rec_stop` / `macro_save` | macro 录制/保存；有效上限最多 200000 tick，达到上限停止并保留数据 |

`state` 关键字段：`x,y,z`（LocalPlayer 眼睛位置）、`feet_*`、`head_*`、`vx/vy/vz`、
`yaw/pitch`、`on_ground`、`sprinting/sneaking/swimming/flying/mayfly/in_water/alive`、
`health/max_health`、`hunger/saturation/exhaustion`、`xp_level/xp_progress`、
`gamemode/dimension/name/uuid/selected_slot/item`、
`block_feet/block_below/block_head/block_above`、`effects`（`id/duration/amplifier/ambient`），
以及 `input`、`bindings` 子对象（后者是 forward/back/left/right/sneak/jump/sprint 的当前 key code）。

每个 tick 还会在事件流里发跑酷相关事件（不需要 `start`）：`player_landed`
（`fall_distance/health`）、`player_hurt`（`health/damage`）、`player_died`。

`start` 录制的每个 `tick` 事件额外带跑酷字段：`in_air`、`jump_edge`（本 tick 是否有起跳
按下沿）、`block_below` / `block_feet` / `block_head`（按脚部坐标取方块）。

握手 `hello` 现在带 `protocol:4`、`capabilities`（`tick_json`/`bin_record`/`mem`/`watch`/
`signin`/`hook`/`ecs`/`task_lifecycle`/`chat_history`）、`spool`（本进程 spool 文件绝对路径）、`replay_rows`（没有客户端时积压、
本次连接会补发的行数）和 `queue_dropped`。`status` 额外带 `spool_rows`、
`spool_flushes`、`allow_write`、`tick_thread_id`、`tick_probe_addr`、`watch_count`、
`watch_hits`、`watch_dropped`、`record_active`、`record_rows`、`record_head`、
`record_period`、`record_capacity`、`build_id`、`version_ok`、`version_failures`。

调试命令（`diag` / `mouse` / `buttons` / `input_cfg`）和注入/传送/macro 的实现细节见
[`../docs/mod-internals.md`](../docs/mod-internals.md)。

## 配置（harness.json）

mod 目录下的 `harness.json` 可以让游戏启动后自己进世界（适合直接启动 exe 的开发循环）：

```json
{
  "auto_enter": "超平坦测试",
  "options": { "gfx_gamma": "0.5" },
  "delay_ms": 2000,
  "background_guard": true,
  "allow_write": false,
  "allow_crash_test": false,
  "allow_hook_va": false,
  "allow_selftest_stress": false,
  "allow_watch": true,
  "allow_input_inject": false,
  "allow_packet_capture": false,
  "allow_packet_inject": false,
  "lock_input": false,
  "signin_wait_ms": 15000
}
```

`auto_enter` 是存档名（也接受 `id` 字段）；mod 会等世界列表准备好、并且游戏在**前台**后
再进入（后台进世界会触发鼠标捕获）。`options` 会先应用并写回 `options.txt`。
`background_guard` 默认开启：游戏失去前台时释放光标裁剪，并忽略后台鼠标引起的视角旋转；
它不会阻止 mod 自己的 `look`/任务。`allow_write` 才能用 `mem_write`；`allow_crash_test`
才能用会主动结束进程的 `selftest_crash`；`allow_hook_va` 才能按任意 VA 装钩（符号钩不需要）；
`allow_selftest_stress` 才允许 `selftest_busy` 在 tick 里忙等（默认关闭，避免误让 tick 卡近 1 秒）；
`allow_watch`（默认 true）关闭后 `watch_set` 回 `watch_disabled`；
`allow_input_inject` 才能用 `key_event`/`mouse_event`（默认关闭，命令会写 boot log）；
`allow_packet_capture` 才能开 H1 的封包身份 tap（默认关闭）；
`allow_packet_inject` 才能用 `chat_send`/`op_self`（默认关闭）；
`lock_input` 启动即锁住真实键鼠（详见下条）；
`signin_wait_ms` 是进世界前等 Xbox Live 登录的上限（默认 15000，设 0 表示查一次就走）。
不需要时删除该文件即可。

## 实测（1.26.51.01 创作存档）

2026-11-03 后续稳定性验收：
- tick 的客户端实例/本地玩家读取已收进局部 SEH，pre/post 的 `client_player_read_fault`
  均进入既有恢复；`--tick-recovery-only` **55/55**。这不是整个 tick 的 SEH 保护。
- `leave_world_result/world_left` 增加 `leave_id`，`worlds` 增加退档序号与菜单快照；
  `--entry-lifecycle-world "超平坦测试"` **32/32**。正常退档的瞬时 toast/服务端未清空可见，
  复合菜单门禁保持不变；HBUI 异常退档的形成根因仍未闭合。
- 飞行效果夹具统一安装、验证和只删除哨兵，支持 `regeneration/resistance/absorption` 备用候选；
  前四种候选被占用时现场 **48/48**，原效果保留。原效果按 duration/tick 允许自然到期。
- 原“空闲热重载 **9/9**”已撤回：提交的 `/ll mod reload probe` 被拒绝（语法错误：意外的“mod”），
  旧检查只验证输入和重连，未证明发生重载。正确形式为 `/ll reload probe`；必须另验实例代次和
  disable/enable 记录，不能以管道恢复代替重载证据。
- 2026-11-03 修正后重验**仍未通过**：日志显示已经进入 `disable`/`unload()`；只发
  `/ll unload probe` 的对照里卸载成功（`probe.dll` 已释放、进程正常响应），但 `reload`
  可复现挂起（全部线程 Wait、`probe.dll` 仍映射、crash 文件无新增）。**具体阻塞点与归属
  unresolved**：`unload: disable returned 1` 只证明内部 `disable()` 返回，不能证明整个卸载
  回调已返回；缺 `load` 行也不能排除 probe 自己的静态初始化/析构/注册器路径。
  `lifecycle_prepare` 已覆盖全部 4 个静态钩子（以 `unhook()` 真实返回值判定状态），
  但不调用它也同样挂起。运行该专项需要 `allow_packet_inject` 与 `allow_packet_capture`
  （模板取自真实 `CommandRequestPacket`），4 键基线配置下会以 `packet_inject_disabled` 明确失败。
- 模板准入由 `status.command_template{ready,reason}` 回读，使用与 `chat_send` 相同的
  owner/generation/内容校验；attempt/fault 计数仅作诊断。无效或缺少可用性证据时不进入卸钩，
  明确的发包拒绝立即进入恢复。主错误与恢复错误同时发生时，JSON 分别保留 `error` 与
  `cleanup_error`，关闭连接与落盘仍执行。崩溃取证只注册可注销的 VEH，不替换进程全局异常过滤器。
- `--macro-long-recording-only` **9/9**：同一 `record_id=1` 实际录满 **200000 tick**，
  总耗时 10603 秒；文件 28600002 字节，SHA-256
  `8d334ad7417eeff36e446c65d11b58f0bd63a7116de5b06a7a81a6e29e8b282c`，重载 ticks=200000。
  等待器重启后 `--macro-resume` 从 71371 行接续，游戏和录制没有重开，不拼接两段文件。
- 2026-11-03 生命周期边界修复：离线 **411/411**，基础现场
  `--mod-core-live-only --require-complete` **44/44**，恢复核对 **16/16**；ruff/format、mypy、
  profiles、roles 和 C++ 构建通过。本轮没有重跑完整现场或热重载，旧完整基线不能代替这两项重验。
  逐项证据见 `docs/reliability-repairs.md` 的后续记录。
- 2026-11-03 结构拆分：`selftest.py` 降至约 630 行，默认套件、领域检查与共享运行时分离；
  封包/传送/飞行/legacy 录制请求分别由唯一状态对象持有，违规/断线页、入站与发送探针拆到
  `ViolationWatch.cpp`/`InboundWatch.cpp`/`SendProbe.cpp`。CLI、检查名和协议保持兼容。
  离线 **411/411**、ruff/format、mypy（109 files）、profiles 与完整 C++ 构建通过；
  本轮没有安装 DLL 或重跑现场，不能据此宣称热重载或既有完整现场已复验。

2026-11-01 Python 连接生命周期：新增 `py -3.11 driver/selftest.py --connection-only --require-complete`
专项，验证客户端遗弃（含订阅循环）、正常退出和 `os._exit` 后的真实重连。实现与本批验收见
[`../docs/mod-internals.md`](../docs/mod-internals.md#python-连接资源生命周期2026-11-01)。
这项修复只涉及 Python；未替换 DLL，也不代表所有 Errno 22 原因均已消除。

同日诊断导出后续：`py -3.11 driver/selftest.py --diagnostic-export-only --require-complete`
**3/3**。修复游戏与 driver 工作目录不同导致的相对路径失败，并等待导出回执，
避免旧错误串到下一条命令。只导出现有环，不安装 hook 或修改世界。
当时离线 **377/377**。2026-11-03 当前候选离线 **387/387**，ruff check/format、mypy（95 files）
和 profiles/roles/lint_public 检查通过；现场恢复范围另见修复跟踪。
旧实体/飞行身份候选现场28/29失败，当时已撤回源码和安装。2026-11-02 已重新按 UUID+epoch
统一实体攻击与飞行的服务端解析并删除名字兜底，身份专项 **44/44**；后续管道恢复范围见
[`修复跟踪`](../docs/reliability-repairs.md)。旧候选记录保留在
[`现场与交接`](../docs/notes/badpacket-187-20261101.md)。

传送已移出 tick，复用 UUID/世界代次解析，后台调度及回执。
reset/checkpoint 成功接受附 `teleport_id`，需等同编号 `teleported` 再回读到达；失败可能
已部分清效果/传送，不自动重放。忙时拒绝，退出/panic 取消，重连丢弃旧请求。
Python `panic()` 保留先于 `panic_done` 到达的领域取消事件，供原请求按编号消费；
其他错误照常抛出，等待超时断开连接以隔离迟到的无编号回执。
回执只确认服务端调用完成，下一次传送依赖上次朝向时先回读同步；自检要求连续三 tick
匹配目标，仍用 0.1° 容差。专项入口为 `driver/selftest.py --teleport-only`（45/45）/
`--teleport-world WORLD`（50/50）。意外退档停止自检并尽力保存诊断，诊断写入失败不覆盖首错。

`start/stop` 的 JSON 逐 tick 录制仍供 Python、DSL 与跑酷使用；内部采样改为固定队列，
后台生成 JSON。保留旧字段，新增 `record_id/truncated`；`stopped` 增加 `ok/rows/record_id`，
失败原因包括 `capture_fault/capture_failed/buffer_full/world_left/disabled`。Python 会传播明确
失败；重复 stop 保留原终结结果。在途 start 返回 `recording_busy`，主菜单不接受开始。
队列最多 256 条，消费者落后至队列满时明确终结，不静默覆盖。跑酷 landed/hurt/died
同样移到后台格式化，采样故障/事件丢失由 `parkour_diagnostic` 报告。

封包采集：身份读取和可选序列化有局部 SEH/C++ 边界；`packet_stats` 的
`id_faults/byte_faults` 统计观察失败，采集重新开始时清零。字节采集失败保留对应身份，
`packet_bytes` 返回同 seq 的 `serialized=false/size=0/hex=""/reason`，不发布部分数据；
Python `decode_row` 返回 capture_error。正常封包投递继续，断开报告会保留采集统计。
SDK 序列化仍使用可增长字符串，本批没有证明零分配或性能收益。

历史全绿基线：live selftest **992/992**（跑之前先 `clear @s` 清空快捷栏，否则
`task_use_server_terminal` / `task_operations` 会 skip；`harness.json` 开 `allow_input_inject`/
`allow_selftest_stress` 和 `allow_packet_capture`/`allow_packet_inject`；2026-10-31 全套，无跳过）、
离线单测 **346/346**。最终坐标/朝向/能力/背包/站稳、释放与解锁已回读恢复；113 个既有
crash 文件无新增/修改，安装/构建/打包哈希一致。此前身份批次的收尾退出异常在用户
正常退出重启后恢复。后续真实错误页 recover 已验收；仅确认 disconnect_error 或显式
--force 才强停，未知/退出中仍保留保存门禁。23:38 新现场已捕获 SetActorData（39）/
error_value=104/最终原因90，成因仍 unresolved；最新完整 465/466 中止，详见下文。
封包真实生命周期 **55/55**、传送 **45/45** / 生命周期 **50/50** 为此前验收记录。
常用实测数字：

内部 tick 错误：后台发送原有 error/internal_error（增加 recovery_id/tick/truncated），
消息最多 255 字节，非法 UTF-8 替换；溢出以 tick_error_diagnostic.lost 计数。恢复期间
控制命令返回 tick_recovering，status.tick_recovery.active=false 才恢复准入；
已有移动/视角、任务/宏等控制会话停止，需要新请求重新启动。释放失败逐 tick 重试，
不使用过期请求快照。查询和解锁仍可用；已开始的 SDK/服务端调用无法回滚。
`--tick-recovery-only` **51/51**、`--tick-recovery-world "超平坦测试"` **57/57**，
完整 **919/919** 通过；不是全 tick SEH 保护，封包模板/注入和其余边界继续治理。

移动/视角故障恢复：control_error 后台诊断和 status.controls；
move/set/look 的接受回执不代表应用成功，失败会停止该序列，默认路径先重试不确定的
按键/鼠标释放，再接纳新状态。`driver/selftest.py --controls-only` **126/126**，
任务 input_failed、两类宏 playback_fault 与恢复通过；默认键位 Hardware HID 路径不变。
本批合并传送后的完整/离线验收见上述基线。未做性能对照/现场热卸载，封包注入/模板
及其余 SDK 边界仍待治理，详见 docs/reliability-repairs.md。

2026-10-31 玩家身份：`--player-identity-world "超平坦测试"` **41/41**，完整现场
**698/698**（无跳过）、离线 **315/315**。UUID/世界代次快照替代每 tick 玩家名分配与锁；
命令、背包写入、权限及物品使用共用服务端解析。身份失败明确拒绝，命令不再回退控制台；
真实重连、退出/重进及旧代次拒绝通过。安装/构建/打包哈希一致，113 个既有 crash 文件
无新增/修改。收尾退出异常、恢复尝试与验收边界见 `docs/reliability-repairs.md`。

2026-10-31 飞行请求：`--flight-world "超平坦测试"` **50/50**，完整现场 **663/663**
（无跳过）、离线 **314/314**。两侧分阶段应用、局部 SEH/C++、忙/截止/唯一终结、
8 tick 重断言故障、真实重连/退出取消/主菜单拒绝及恢复通过；空中开启飞行有回读验收。
取消后的旧回调不能重新启动重断言。构建/静态/profile 与安装哈希一致，113 个既有
crash 文件无新增/修改。tick 不再为飞行复制玩家名、投递任务或生成 JSON；已开始的
调用不能被超时抢占或回滚，地面落地仍可结束 Flying，未做性能提升对照。

2026-10-31 事件输入/文本/UI 查询：`--input-events-world "超平坦测试"` **51/51**，
完整现场 **619/619**（无跳过）、离线 **313/313**。单事件局部 SEH/C++、编号结果、
128 项满队列、文本忙/截止、失败后取消 Enter、正常文本立即 Enter 的真实命令回显，
以及重连 held 释放、退出取消/主菜单查询/重进通过。构建/静态/profile、安装哈希一致，
113 个既有 crash 文件无新增/修改。tick 不再取文本锁或创建自有输入字符串/JSON；
SDK 内部及移动/飞行、玩家名、封包注入等其余边界仍待治理，未做性能提升对照。

2026-10-31 ECS 快照：`--ecs-capture-world "超平坦测试"` **51/51**，完整现场
**575/575**（无跳过）、离线 **308/308**。五个接口已移出 tick，客户端/服务端 context
匹配所属 registry；覆盖逐 storage 故障继续、两侧 SEH/C++/超时、busy 保留、真实重连/
退出/重进。安装/构建哈希一致，无新增/修改 crash 文件；输入/飞行等其余边界仍待治理。

2026-10-31 宏播放/点击：`--macro-playback-world "超平坦测试"` **56/56**，完整现场
**536/536**（无跳过）、离线 **307/307**。覆盖两种回放的 SEH/C++ 唯一失败、释放重试、
短宏替换/快速重启、任务接管、点击异常恢复及真实退出/重进。安装/构建哈希一致，
无新增/修改 crash 文件。tick 播放模型无自有分配/锁/JSON；SDK 内部及 ECS/输入等其余
游戏路径仍待治理，未现场热卸载或做性能提升对照。

2026-10-31 二进制录制失败传播：`--binary-capture-world "超平坦测试"` **33/33**，完整现场
**484/484**（无跳过）、离线 **305/305**。覆盖 SEH/C++ 唯一失败、部分帧与重复停止、
Python 失败传播/原异常保留、退出取消与重进恢复；分页禁止跨会话。修正实体朝向验收为
使用本次 eye/target，回读误差 <0.1°。构建/静态/profile 与安装哈希一致，无新增/修改 crash；
未现场热卸载 DLL，未用这轮宣称整个 tick 零分配。

2026-10-31 钩子调用/姿态：`--tick-safety-world "超平坦测试"` **32/32**，完整现场
**459/459**（无跳过）、离线 **299/299**。覆盖钩子异常/超时唯一失败与恢复、busy 参数保护、
真实重连/退出取消、主菜单拒绝，及姿态读取失败和世界代次隔离。安装哈希一致，
无新增/修改 crash 文件；宏播放/ECS/输入等其余 tick 路径仍待治理。

2026-10-31 宏录制采样：`--macro-capture-world "超平坦测试"` **29/29**，完整现场
**435/435**（无跳过）、离线 **298/298**。覆盖采样故障唯一终结、部分文件、真实保存失败
保留缓冲与重试、快速启停、主菜单拒绝及退出/重进恢复。无新增/修改 crash 文件；
宏播放与其余游戏读取路径仍待治理，未宣称整个 tick 零分配。

2026-10-31 JSON 录制/跑酷事件：`--tick-stream-world "超平坦测试"` **39/39**，完整现场
**411/411**（无跳过）、离线 **296/296**。覆盖有序终结、无迟到采样、异常/缓冲满唯一失败与
恢复、主菜单拒绝和退出重进；4 tick 宏排出 5 条连续样本，真实落地在故障后仍可观察。
构建/静态/profile 检查与安装哈希一致，无新增/修改 crash 文件。

2026-10-31 实体/快捷栏迁移：查询专项 **99/99**、断线/退出/重进 **16/16**，
完整现场 **377/377**（无跳过）、离线 **289/289**。实体查询、信息、攻击、交互和看向共用
独享分阶段会话，读取只在对应客户端/服务端队列执行，2 秒截止、在途拒绝 `entities_busy`，
退出取消、重连丢弃；至多 512 行，文字截断报告 `truncated`。实体 JSON 移到 spool。
快捷栏保持 tick 后应用和原 `select_slot_ok`/`select_slot_done`，完成回执改为后台格式化，
在途拒绝 `select_slot_busy`，主菜单明确拒绝。已开始的游戏调用不支持超时抢占；
SDK 名称分配与宏等热路径仍待治理。构建/静态/profile 检查与安装哈希一致，
无新增/修改 crash 文件。交互仍为输入 best effort，不保证所有实体都发生世界交互。

2026-10-31 Overhead 统计环：`--overhead-only` **7/7**，真实推进 1100 tick、
1741 次窗口读取全部一致；完整现场 **330/330**（无跳过），离线 **288/288**。
统计载荷改为原子发布，只有完整窗口才计算分位数；窗口竞争过强时回
`consistent=false/samples=0/reason=snapshot_busy`，不输出无效分位数。
新增 `sample_first/sample_head`，旧字段与别名保留。采样热路径无初始化、分配或 mutex。
构建、静态检查、安装 DLL 哈希一致性通过，无新增崩溃文件；本轮未做性能提升对照实验。

2026-10-31 世界进入看门狗：`--entry-lifecycle-world "超平坦测试"` **30/30**，
完整现场 **324/324**（无跳过）、离线 **287/287**。启动后 30 秒超时明确失败并保留隔离，
确认菜单就绪、旧服务端消失且稳定 500 ms 才允许再次启动；若实际玩家与 HUD 已就绪，
可补回缺失的加入结果。回执/终结增加 `entry_id`，Python 自动关联，兼容旧 mod。
启动/观察故障不退出观察链。现场超时用门控的 700 ms 模拟，真实缺失 join 检查实际重进世界。
同批修正已捕获任务故障误写 crash dump，放置自检增加站稳与实体避让前提。
构建、静态检查、安装 DLL 哈希一致性通过；最终运行无新增 crash 文件。
加载真实卡住且尚未满足安全门时保持隔离，不会强行重开服务端。

2026-10-31 宏硬上限修复：`--macro-limit-only` **17/17**，完整现场 **322/322**（无跳过），
离线 **284/284**；原生模型覆盖真实 200000 条上限及零分配追加，现场验证配置、唯一终结、
保存和错误参数保留缓冲。构建、静态检查与安装 DLL 一致性通过。

2026-10-31 背包采样迁移：查询专项扩展为 **52/52**，完整现场 **305/305**（无跳过），
新增背包 SEH/C++/超时的唯一终结、恢复和 busy 检查。离线、静态检查、完整构建通过，
安装 DLL 与构建产物 SHA-256 一致。

2026-10-31 查询采样改造：`--query-capture-only` **42/42**，
`--query-lifecycle-world "超平坦测试"` **9/9**；真实扫描在重连/退出时取消、主菜单拒绝、
重进恢复，受控采样 SEH/C++/超时只终结一次且无新增 crash 文件。
完整构建通过，安装 DLL 与构建产物 SHA-256 一致。

2026-10-31 服务端任务等待修复：`py -3.11 driver/selftest.py --use-lifecycle-only`
专项 **16/16**，覆盖超时、投递失败、回调 C++/SEH、物品操作内部 SEH、唯一终结和恢复；
受控已捕获故障没有新增 crash 文件。完整构建通过，安装 DLL 与构建产物 SHA-256 一致。

2026-10-29 工具链轮：`hook_install` 增加 `mem_arg/mem_offset/mem_size`（detour 内同步
采参数指向的短命缓冲）；`ecs_components` 改为逐 storage 探测，坏条目进 `skipped`
而不是整条 `internal_error`。验收数字见
[`../docs/notes/tooling-pain-points.md`](../docs/notes/tooling-pain-points.md)。

2026-10-28 客户端 I/O 验收：`py -3.11 driver/selftest.py --client-io-only`
离线检查 **20/20**，覆盖分片解码、录制事件保留/分页终止、spool 损坏行与临时 I/O 失败。
该模式不连接游戏、不注入输入；本轮没有重跑完整 live selftest，222/222 是既有现场基线。
解码微基准与具体边界见 [`../docs/mod-internals.md`](../docs/mod-internals.md#客户端-io2026-10-28离线)。

同日 mod 核心验收：`py -3.11 driver/selftest.py --mod-core-only` 跑三组 native 检查，
覆盖真实 Windows 管道取消/超时/重连/句柄回收、分帧与批量发送、录制重启与并发环绕覆写。
LLVM22 完整 mod 构建通过；备份并安装新 DLL 后，`--mod-core-live-only` **33/33** 通过，
覆盖管道分帧/超时重连、录制重启/导出与 spool。该模式只用普通管道与录制命令，不注入输入、
不修改世界，也不运行逆向相关检查；默认完整 222/222 套件本轮未重跑。具体范围见
[`../docs/mod-internals.md`](../docs/mod-internals.md#mod-核心2026-10-28离线与现场)。

- 步行注入峰值 **0.2159 格/tick**（原版 4.317 m/s）；潜行约 0.30x。
- 跳跃逐 tick 位移 0.4200 / 0.3332 / 0.2481 / 0.1648…（顶点 1.252 格、滞空 11 tick），
  连续跳与原版轨迹误差 < 0.0001 格。这组历史测量使用 legacy `jumpFromGround`；
  现行默认起跳使用 Hardware HID 按键，见下文「限制」，不能据旧数据恢复直接冲量。
- `navigate` 约 5.2 格 27 tick 到达（误差 0.26 格）；`mine` 创造模式 1 tick、`place`
  3 tick 回读验证；存档点 `checkpoint_load` 落点误差 0.000 格。
- 事件级宏回放三次落点误差 **0.0000**；疾跑三组对照 B≈C≈0.2805 格/tick。
- 双击空格飞行只在 `InputOrigin::Hardware` 上切换（Virtual 只起跳），且每次按下要跨
  至少 1 tick；`/togglefly` 开启后生存模式实测 2 tick 间隔连按即置 `flying=true`，
  关闭后同样连按不起飞；旁观模式命令回 `success=false`。
- `equip feet` 把手持/库存栈搬进穿戴槽并清空源槽（附魔随栈一起走，源槽回读为空）；
  记录环的 `in_water`/`in_lava` 位在脚下与头部方块为液体时全部命中（实测 9/9 tick）。
- 封包：身份 tap 3.5 秒 122 条 send 无丢失；`chat_send` 注入的 `/tp` 位移正好 1.000 格；
  接收侧流量全部走 `LoopbackPacketSender::$sendToClient`。
- 版本 profile：4 锚点全匹配、`struct_fields=12`；改坏任一签名即 `version_ok=false`
  并全线降级（不崩溃），改回即恢复。
- 逐项实测数字与复现要点（移动/输入、任务/世界、观察点/hook/ECS/profile、G/H 封包层）
  见 [`../docs/mod-internals.md`](../docs/mod-internals.md) 的「实测数字速查」。

## 限制

- `hook_call` 仅在世界中可用，独占请求槽、2 秒截止，在途重复请求返回 hook_call_busy。
  成功仍为 hook_call_done；采样故障/超时/退出以 error 和 phase=hook_call 明确报告，
  重连丢弃旧在途请求，已开始的游戏调用不能被超时抢占。
- `checkpoint_save` 使用单 tick 的一致姿态，回执增加 tick/pose_epoch。姿态读取失败、
  主菜单或新世界还没有新姿态时返回 no_pose；不会使用前一世界的残留坐标。

- 宏录制 `max_ticks` 须为非负整数，省略/0/超过 200000 均使用 200000 tick 上限。
  `macro_rec_started.max_ticks/hard_limit` 和 `macro_status.record_limit` 可查看实际限制；
  到顶只发一次 `macro_rec_limit`，`recording=false`，之后仍可保存。参数验证与容量预留
  先于替换录制数据，失败时保留原缓冲；启动时初始化全部帧槽，tick 不取录制会话锁。
  采样故障或退出世界会以 `macro_rec_stopped` 明确失败，已有帧仍可保存；
  `macro_rec_saved` 的 `ok=false/partial=true` 表示部分文件，Python 会报告错误并给出路径。
  写盘失败保留缓冲供重试。保存先写同目录唯一临时文件，检查 write/flush/close 后用
  `MoveFileExW(REPLACE_EXISTING|WRITE_THROUGH)` 替换；失败清理临时文件并保留旧目标。
  主菜单不能开始录制。

- `state/buttons/inventory/blocks/scan` 各有一个独占快照槽；重复在途请求返回对应 `*_busy`。
  除扫描外采样截止为 2 秒，扫描为 10 秒，超时回 `error/capture_timeout`；退出世界取消请求
  （`error/world_left`），断连丢弃旧的在途快照。`state` 保留既有字段并增加
  `partial/truncated/warnings/sample_us`；局部方块读取失败会明确标记部分结果。
  `scan` 增加 `visited/batches/batch_limit/max_batch_us/first_tick/last_tick`，结果数量
  截断时标记 `truncated_reason=result_limit`。扫描跨多个 tick，期间世界可能变化；
  格式化移出游戏线程不等于所有 SDK 名称读取和其他采样路径都已零分配。
  `inventory` 保留原字段，背包槽最多返回 256 个；槽位或文本截断时标记 `truncated`，
  另带 `sample_us`。普通 36 格背包完整返回；快照固定约 1.1 MiB。

- `chat_history` 使用平台主线程队列；主菜单回 `ok=false,reason=no_player`，等待超过
  2 秒回 `chat_history_timeout` 并自动恢复。退出世界会取消在途请求（`world_left`），
  断连或卸载丢弃旧工作。`py -3.11 driver/selftest.py --chat-lifecycle-world "超平坦测试"`
  可复现保存退出、主菜单连续请求和重进恢复（需开启 `allow_selftest_stress`）。

- 2026-10-30 审阅修复进行中，逐项状态及本轮证据见
  [`docs/reliability-repairs.md`](../docs/reliability-repairs.md)。请求 mailbox 与 DSL tick 消费已补
  回归；本批离线 **282/282**、现场 **237/237**，无跳过。
- `dev.py run --write-config` 只合并/备份配置，不要求 exe、不编译、不停止游戏、不安装 mod。
  普通 run 会清除旧 `auto_enter`/启动选项，保留手写能力开关；可用 `restore-config` 恢复。
- 普通 `dev.py run/stop` 只处理指定 exe 路径的 PID，先保存退出并等待主菜单就绪；
  无法确认保存完成时保留进程。仅故障恢复时显式使用 `--force-stop` 跳过保存。

- 组合压力验证可用 `py -3.11 driver/selftest.py --stress-cycles 30 --stress-world "超平坦测试"`
  复现（任务替换、重连、退出/重进世界）；超长未结束命令的丢弃状态在新连接重置。
  世界生命周期的内部设计见 `docs/mod-internals.md`「世界与设置」。
- 进入世界只在主菜单 screen（`start_screen` 或 HBUI 的 `ui://…`）发生；任何
  `*progress_screen`（saving/loading）都会继续等待，超过 `signin_wait_ms` 回 `world_busy`。
  换版本后如果菜单 screen 改名，先看启动日志的 `enter_world: start|blocked screen=…`。
- `auto_enter` 与显式 `enter_world` 不会并发：显式进入取消待执行的自动进入；已有进入
  在飞时第二次回 `already_starting`，由 join 事件收尾。
- **脚本运行时的真实输入锁**：`input_lock {on}`（CLI `input-lock [--off]`，配置 `lock_input`，
  dev.py `--lock-input`）在三个入口丢弃**硬件来源**输入——`HIDController::$onKeyDown` 的
  `InputOrigin::Hardware`、`MouseDevice::feed` 的非注入调用、以及非注入的相机转向；
  mod 自己的注入（Virtual 键、带 `mInjectingInput` 的硬件键/鼠标/宏）照常工作。
  **配置里的 `lock_input` 只在 harness 客户端连着时生效**：连接时上锁、断开/客户端崩溃时自动解锁，
  手动开游戏（没有人连）永远不会被锁 —— 早前是启动即锁，导致主界面按钮点不动。
  `state.input.locked` 反映当前锁状态。`driver/selftest.py` 启动自动上锁、结束解锁。
  `key_event` 的 `via` 决定走哪条：`hid`=Virtual、`hardware`=Hardware 且标记为注入
  （上锁时仍放行，双击空格必须用这条）、`raw`=Hardware 且**不带**注入标记（等价于真人按键，
  上锁时被丢弃，只用于验证锁本身）、`queue`=`Keyboard::_inputs` 直推（不发 LL 事件）。
  key-up 未加钩子，对游戏从未见过的按键是空操作。
- **`/togglefly`（游戏内命令）**：注册进本地服务端命令表，玩家直接在聊天框输入即可开关
  "双击空格切换飞行"的 `MayFly` 能力；生存/创造/冒险实测都生效、旁观模式回错误，`NotCheat`
  所以世界关掉作弊也能用（selftest `togglefly_not_cheat`）。
  **切换游戏模式会把能力重置成该模式默认值**（生存/冒险 false、创造 true），
  模式变了要重新 `/togglefly`。客户端 mod 在 `enable()`（主界面阶段）还没有服务器命令表，
  注册必须挂在 `ServerCommandRegisterEvent` 上——直接写在 `enable()` 里会启动即崩（已实测）。
  命令表按世界重建（退出/重进世界后再输入仍然可用），注册用 `CommandRegistry*` 身份判重，
  不会重复挂 execute。管道侧等价命令 `togglefly`（`{"cmd":"togglefly","on":bool}`，回
  `togglefly_ok{mayfly,gamemode,flying}`；CLI `togglefly [--on|--off]`）只在服务端队列写能力，
  客户端队列有界回读同步结果，随后 8 tick 只读观察。两种飞行请求共用独占槽，忙时回 `flight_busy`，
  2 秒超时或退出世界明确失败，重连丢弃未入传输队列的旧回执；接受回执带 `flight_id`。
  `client_applied` 与 `client_observed` 只有回读一致才为 true；`application=server_with_client_readback`
  和 `assertion=readback` 明确新路径，旧字段保留。成功表示服务端调用与客户端回读均完成，
  不等待 8 tick 观察结束；后续观察失败另发 `flight_assertion_failed`。失败的 `partial=true`
  表示能力可能已部分生效，已开始的调用不能被超时抢占或自动回滚。专项 **53/53**；本批未复现
  BadPacket，不宣称游戏共享缓冲竞态已根治。2026-11-03 自检收尾改用 `teleport`，避免 `reset`
  清空调用前效果；原效果按 duration/tick 允许自然到期，独立长时哨兵验证未提前清空且只移除
  哨兵，短/长复验均 **50/50**。同批完整 `--require-complete` 为 **1093/1093**
  （0 fail、0 skip、0 required skip）。
- `set_item --container armor` 的槽号是 Legacy `ArmorSlot`（0 头 / 1 胸 / 2 腿 / 3 脚）；
  往头槽放靴子不会报错但也不会生效。`equip` 只搬玩家自己的库存槽，不碰世界容器。
- 记录环的 `in_water`/`in_lava` 位是**脚/头方块名**判定（流动变体算液体），
  不是效果盒几何；浅水阈值（0.4 格）要另用速度行为判断。
- 起跳默认按当前 jump 绑定（默认 Space）发 Hardware HID：`jump` / `set --jump`
  （含任务与 `jump_when`）都走这条真实按键路径。`input_cfg jump_impulse` 默认 0；
  非零的直接冲量（1=jumpFromGround，2=setJumpPendingScale，3=两者）只在
  `force=true` 下用于复现旧测量，正常脚本不要用。
- `jump` 在空中发放会被丢弃（一次性请求，需 `onGround`），要等落地请用 `set --jump --ticks N`。
- HUD、跑酷统计、practice、Input Checker 不在本 mod 范围内，直接用 dmf-mpk；
  本 mod 只提供脚本/LLM 使用的 API。
- 同一时间只跑一个闭环任务；新任务会取消旧任务并停掉 macro。
- `place` 默认自动挑选一个可见的支撑面；也可以显式给 `against`/`face`。放置结果以回读方块为准。
- `use` 在服务端直接执行“使用完成”逻辑，不做事前可用性判断：满饥饿吃食物、没有箭拉弓也会消耗一次；
  弓/弩按 `Shoot` 处理，不消耗武器本身。
- `interact_entity` 只注入“使用”按钮（best effort）：直接调用 `Player::interact` /
  `GameMode::interact` 会在空物品上下文里崩溃，因此改为转向 + 输入注入；是否产生实际交互
  取决于手持物和瞄准。
- `smoothMacro` / `frameBasedRotation` 未复刻，保持逐 tick。
- 宏播放的启动/状态/停止回执增加 run_id，macro_done 增加 ok；播放或释放输入异常会
  明确终结失败，退出世界同样报告 world_left。停止回执的 release_pending 表示输入尚待
  游戏 tick 释放；释放失败会重试，macro_release_diagnostic 报告故障/异常计数。
  新播放先清理旧输入；主菜单拒绝启动。坏宏文件不会打断当前播放，成功换宏会停止旧播放。
- click_result 增加 ok/reason；click_fault/click_failed 表示隔离的 SEH/C++ 故障，
  click_diagnostic/buffer_full 表示结果队列溢出。调用成功不代替方块/实体效果回读。
- 鼠标录制只认 raw input；后台合成点击只有移动事件。事件级回放（`macro_run event_level=true`）
  用 G1/G2 注入，已经覆盖按键/鼠标边沿；录制端仍然依赖真实 raw input。
- `mouse_event` 的按键只到 `MouseInputEvent` 层：计数会涨，但在 1.26.51.01 上不会触发世界攻击；
  攻击仍用 `click`/`attack_entity`。`action=move` 的 dx 能转向。
- H1 常规身份 tap 只写定长记录，不调用 `Packet::write`。`serialize_ids` 是显式
  `sdk_hot_path_diagnostic`：在原投递上下文同步重序列化、可能分配；现在有硬预算
  `serialize_budget_us`（默认 1000000 即 1 秒，0 不限），累计超预算后不再重序列化，改为记一条
  `error=Budget` 并累加 `serialize_skipped`，`packet_stats` 同时给出
  `serialize_samples/us/max_us/skipped/budget_us`。
  不跨线程保留 `Packet*`，字节不是线上原帧。`packet_capture` 回执标明 `serialization/diagnostic`，
  `packet_stats` 同样可见；`packet_bytes` 保留旧字段并标记来源和截断。`peer_sends` 恒为 0
  是实测结果：单机回环不经过 `BatchedNetworkPeer` / `NetworkConnection::receivePacket`。
  专项 **41/41**，四组 1100 tick 开销对照见 `docs/mod-internals.md`，没有可重复的提速结论。
- H1-C 的 recv tap 是 opt-in（`directions` 默认只有 `send`）。`Packet::sendTo*` 辅助层的
  7 个钩子会装上但实测 0 调用；真正投递走 `LoopbackPacketSender::$sendToClient` 两个重载。
  重叠层可能对同一包各触发一次，记录按“同 tick + 同对象 + 同 id”去重，所以广播只看得到
  一条记录（要看投递次数用 `packet_stats.recv_anchors`）。
- H4 的 36/19 解码是标准 Bedrock 布局：36 是客户端→服务端，19 在单机里没有对端玩家，
  两者都仍然只有单测；接收侧已实测到 `Text(9)`、`AddActor(13)`、`MoveDeltaActor(111)`，
  但没有对应的解码器（保持原始 hex）。
- 入站切片测量：`violation_watch` 同一开关还装一只 `Packet::readNoHeader` 入口的只读钩子
  （五个 `void*` 原样透传，绝不虚调用，只 `readMemory`），采样 1/4 进 1024 环。静态证据已确认
  这条本地路径的元素是**深拷贝**，切片**以包 id 开头且没有帧长度前缀**；187/188 的 `payload+2`
  只是 id varint 宽度。`packet_size` 是实际切片长度，`context_size`/`context_truncated` 标记捕获
  边界；Python 摘要同时保留实际大小、捕获大小、完整性、seq/index/tick/tid。`before_hex` 只保留
  切片前的原始邻接堆字节，**不得解释为声明长度**，所以该路径的 `slice_verdict()` 恒为 `unknown`；
  `decode_framed_slice()` 只供另有真实前缀采集依据的路径使用，且 `before_hex` 必须从真实
  varint 首字节开始；只接受覆盖整个捕获窗口的候选，后缀巧合或起点不明保持 `unknown`，不按
  候选顺序猜长度。本地 loopback 钩子看到本进程
  解析的所有包（含客户端上行），不是只有下行。
- 违规守望 `violation_watch` 是**只读**诊断：装在被包裹函数的**运行期**地址上，需要
  `allow_hook_va`、在世界内、默认关闭，`panic` 会一并卸下。角色地址在 `docs/roles` 里是
  `image_base=0x140000000` 下的静态 VA；本机 exe 被 PeEditor 改写首选基址并 ASLR，
  所以运行期地址 = `diag.module_base + (role_va - image_base)`（已用 mem_read 在 RVA
  `0x12FA0F0` 处核对到标准函数序言）。该函数有 10 个参数（6 个在栈上），**不能**用通用
  四寄存器 VA hook，必须用同签名 typed wrapper。
  记录字段：`seq/tick/tid/response/packet_id/packet_size/error_value/category/context`
  （记录行内 context ≤191 B、detail ≤639 B 便于对齐；**完整原文**走 `violation_text`，单槽 4 KB，超出才置 `truncated`），环容量 256、回调内零分配/零 JSON/零锁，
  永远以原参数调用原实现，不改校验结果与断开行为。`mode=synthetic` 只验证记录设施，
  **不算**真实违规复现；自然样本另见下文 23:38 的验收记录。
  同一开关还会钩住 `OreUI::EntryPoints::DisconnectScreen::composeRoute`（普通导出符号，
  不需要 VA）：经过该函数时记一行 `source=screen`，`response` 为路由原因码，
  `context` 为码字，`detail` 为最多 639 字节的页面详情（整页约 570 字符，含 Date/Transport/各会话 GUID），可能为空或丢失非 ASCII 字符。
  这与 `source=callback` 的 `response`（违规处理等级）语义不同。
  手动路由验证只证明设施可用；当前人工 screen 行正确标 synthetic，旧版曾误标 false。
  `LEVIPILOT_VIOLATION_DUMP` 由所有 live 入口统一读取，导出起始游标后的增量；
  设施自测会恢复外层武装，`panic` 卸载断言后再武装，报告明确记录观察空窗。
  2026-10-31 22:32 的正常能力对照在未武装组 reset 后退档，
  第二组未执行，不算通过；证据与完整边界见 `docs/mod-internals.md` 的违规守望小节。
  后续在当前 DLL、同一进程完成先开启后关闭的正常能力对照，各 **33/33**；两组均未退档，
  环新增 0、lost=0，姿态/能力/站稳恢复并解锁，113 个 crash 无新增或修改。
  该对照没有自然断开，不能用来验证真实 screen/callback 链或根因，也不替代全套。
- H2 只能注入 `CommandRequestPacket(77)`，而且必须先有一条真实的 77 包做模板；
  `op_self` 会修改世界里的玩家权限（测试世界用，别在日常存档里开）。
  `chat_send_queued` 只表示接受；同域忙时回 `packet_inject_busy`，不会覆盖旧文本。
  `chat_sent {packet_id,ok,text,reason?,partial}` 在客户端发送调用完成后由后台发出；
  Python `chat_send(..., wait=True)` 可按编号等待。ok=true 只表示发送调用完成，命令效果须用
  `blocks`/位置回读。2 秒截止、退出/panic/内部错误会终止请求，重连丢弃旧请求；
  这些边界也会使来源模板失效，须开启 send capture 并重新发一条真实命令。
  模板保存完整 requestId（最多 512 字节，超限拒绝），不截断身份；失败不会回退旧模板。
  `partial=true` 保守表示发送或 SDK 构造可能部分执行，不能回滚或自动重试。
- 暂停菜单不响应注入按键（Esc/Enter 都无效）；退出暂停仍需桌面点击 Resume。背包（E）
  和聊天（`/`）可以开，背包用 Esc 能回到 hud。
- 输入注入依赖 `LocalPlayer::$normalTick` 符号；游戏升级后需重新构建。
- 移动/跳跃默认从 `IOptionRegistry` 读当前键位并合成真实 Hardware HID 按键，与玩家键盘同路径；
  `jump_impulse`、`mode` 等直接标志/冲量只在 `input_cfg force=true` 下用于复现旧测量，
  正常脚本不要改。
- 只支持 **1.26.51.01** 的实测结论；换版本按 [`docs/porting.md`](../docs/porting.md)
  走 profile 流程，不要直接套旧版本的偏移。
- 版本数据（锚点签名、结构偏移、组件 id）只存在于 `docs/offsets/*.json` 和生成出来的
  `GeneratedProfiles.h`；`.cpp` 里不允许再写死。改了 JSON 必须跑
  `py -3.11 driver/gen_profiles.py`，否则 `tests/test_profiles.py` 会挂。
- 没有匹配 profile 时 `version_ok=false`、偏移表为空；`allow_version_mismatch=true`
  可以在移植期间让钩子继续跑（`compat=true`），但它不校验签名，日常配置不要开。
- 硬件观察点全局共 4 个 DR 槽；线程已被调试器（或其他工具）占用时回 `dr_busy`，
  不会强抢。x86 的 `rw` 实际是“读写/IO”语义，没有纯读观察点。
  只有装过 DR 的线程才会被记为命中：别的线程上的 DR6 事件进 `foreign_hits`，
  不会污染命中环（曾经在旧线程遗留 DR 时误记成服务端线程写 tick 变量）。
- 玩家的 StateVector 由**集成服务端线程**写入（不是 tick 线程）；观察它用
  `watch_set(thread="both")`（tick + server）；`thread="all"` 会被拒绝
  （`thread_all_unsupported`，历史上装 100+ 线程会把游戏搞崩）。
- `ecs_entity/ecs_components` 按 runtime id 解析到的是服务端 Actor；客户端玩家的 context
  从 `state.context` 取，`ecs_context` 用它做交叉验证。不要在 tick 线程枚举客户端 registry
  （会和游戏线程竞争并崩溃）。
- ECS 请求由所属客户端/服务端队列采样，后台格式化。2 秒截止、同域重叠返回 ecs_busy，
  退出取消、重连丢弃旧结果。最多 1024 条组件/失败 id、4096 次 storage 访问、512 字节
  原始数据；超限标记 truncated，坏 storage 保留 skipped/partial。scope 表示本次实际读取侧。
  context 必须对应当前客户端或服务端 registry，否则返回 context_not_found；原始地址可能复用。
- 事件级输入的 128 项队列同时保存待应用事件和待消费结果；满时返回 inject_queue_full。
  key_event/mouse_event 的 queued 只表示接受，input_event_done 的 ok 表示注入调用成功，
  不保证 UI/世界采用该输入。断线/退出取消旧代次，后续 tick 释放跟踪的 held 键，失败重试。
  尚未进入传输队列的旧代次结果在重连时丢弃，已入队行仍遵循 replay 契约。
- 文本使用单独请求槽，重叠返回 text_input_busy；后序按键等待文本完成或 2 秒截止。
  文本失败/超时会取消后序旧输入，按编号回 input_cancelled，以免 Enter 提交残留文字；
  后续新请求仍可提交。完成回执只确认 HID 调用及 UI 消费窗口，
  实际效果须以聊天记录或世界状态回读验证。screen/key_state 由客户端队列读取并后台回复，
  共享的 UI 查询槽有 ui_query_busy 和 2 秒截止；文本在途不占用 UI 查询槽。
- spool 每 100 ms 或 64 KB 刷一次，进程被强杀时最多丢最后约 100 ms 的记录；队列溢出
  会写一条 `queue_dropped` 标记进 spool。
- `mem_write` 默认关闭，开启后每次写入在 `harness-boot.log` 留一行审计。
- 管道单次批量/大行写入预算为 **5 s**；超时会断开当前客户端，计数见
  `status.pipe_write_timeouts`，客户端可重连。输入行完整或分片都受 **1 MiB** 上限约束。
- 已知边界：长时间高频批量查询（约 600 次以上）后偶尔出现“连不上、只能重启”。
  当前只做诊断，**不做** mod 侧看门狗：回收卡住的管道要在另一个线程 `CloseHandle`，
  这与 `PipeChannel` 的阻塞/overlapped I/O 生命周期竞争（历史坑），在稳定复现前不动。
  规避：分批查询、脚本结束前 `close()`；卡住时重启游戏。
- Python 客户端已补遗弃连接的 GC 兜底，以及读线程 EOF/队列溢出后的主动关句柄。
  脚本仍应使用 `with ModClient()` 或 `try/finally: client.close()`；仍被引用的对象不会自动回收。
  `--connection-only` 会先拒绝活跃控制/任务/录制，再检查遗弃与进程退出后的重连；
  不清空世界、不注入输入。该验证不覆盖上面的高频查询挂起边界。
- 二进制环固定 **524288 条 × 64 B = 32 MiB payload**，加约 4 MiB 发布序号，单写者
  （tick 线程）；start/stop 等待已准入的采样完成，dump 不会读取被覆写到一半的行。
  `record_dump` 默认
  每次 1024 条，`since/next` 是绝对序号。因为传输是 JSONL，`bin`/`hex` 用 hex 分块而不是
  裸字节帧，避免破坏行协议。
- 二进制采样故障或离开世界会终止本次录制，异步通知为 `record_failed`；重复停止保留
  原失败，已发布部分帧仍可导出。Python `record_bin_stop()` 遇失败抛错，`record_dump_all()`
  默认拒绝失败/丢行，显式 `allow_partial=True` 可取证，但始终拒绝跨 `record_id` 拼接。
  `lab.record_session()` 在清理失败时仍保留部分 `entries`，实验主体的异常不会被覆盖。
- 记录里的 `block_below/feet/head` 是方块类型名的 FNV-1a 32 哈希；用
  `driver.harness.record.block_hash(name)` 对照。
- `hook_va` 的通用 detour 只保留 4 个整数参数寄存器（rcx/rdx/r8/r9）；float/xmm 参数、
  向量返回、超过 4 个参数或隐藏返回指针的目标**不安全**，需要 typed detour。目前 typed
  符号钩只实现了 `MobJump::setJumpPendingScale`。
- VA 只校验“已提交且可执行”的页，不校验指令边界；地址必须由 IDA 侧确认。
- `allow_hook_va` 默认关闭；`hook_install` 的 `sample` 用于降采样，命中环固定 4096 条
  （每条记录含最多 64 B 的可选内存快照；不开启时只多一次分支）。
- `hook_install` 的内存快照是**同步**读：`mem_arg` 指向的缓冲可以随 tick 重建/释放
  （形状表、栈上的 AABB），`hook_drain` 之后再用 `mem_read` 读同一地址通常已经是空容器。
  指针不可读时该条命中 `mem.ok=false`，不影响被钩函数。
- 版本校验：`build_id` 选中 profile 且 4 个锚点签名全匹配才 `version_ok=true`；不匹配时
  钩子/观察/记录回 `version_mismatch`，管道、`diagnose`、`profile_dump`、`mem_read`/`probe`
  仍可用（`allow_version_mismatch` 只改看 `hooks_allowed`，移植用）。锚点先跟随
  `jmp [rip+0]` 导入 thunk 再取真实函数。
- `mods/probe/offsets.json` 可以在运行时覆盖签名/结构偏移/组件 id，不用重编译；
  它只会改当前进程里已选中的 profile。
- ECS 组件 id 是**运行时** EnTT id，跨进程/跨版本不稳定；`ecs_components` 报 id、地址、
  stride、count，profile 已知组件附带 name/size。stride 由同一 storage 的两个元素地址差
  推断，只有一个元素时为 0；组件默认读取长度依次取 profile 尺寸、stride、64 字节。
  `ecs_registries` 列出客户端 registry；runtime-id 请求读取服务端，context 会核对所属侧。

### 诊断与完整覆盖门禁（2026-10-31 后续）

所有 live 自检入口均读取 `LEVIPILOT_VIOLATION_DUMP`，结束前导出增量、合成/真实行数、
丢失数、状态及观察空窗。设施自测和 panic 会实际卸下，恢复武装也不意味着没有空窗。
人工 `disconnect_route` 现在正确标 synthetic；屏幕的 `disconnect_reason/response` 为
路由原因码，`verdict=disconnect`，回调 response 仍是违规处理等级。已有屏幕无法补录，
详情限 639 字节并替换非 ASCII。自然断开完整采集链已有下述现场样本；当时根因未定位，
后续已由证据册 §49 闭合为游戏共享序列化缓冲的跨线程竞态。

自检显式输出 PASS/FAIL/SKIP；`--require-complete` 将必需 skip 判为失败，普通模式仍可
在权限关闭时降级，但数字会保留 skip。本批设施专项 21/21；关闭注入权限的门禁对照
2/3（1 required skip，预期 exit 1）。原函数槽生命周期已按 SDK 契约修复，未做热卸载
或任意并发启停证明，详见 mod-internals 的对应小节。

水中录制与 H0 方块身份检查现共用固定封闭水池；旧开放水源会留下流动水并污染后续
位置验证。专项复现 `py -3.11 driver/selftest.py --record-medium-only --require-complete`，
本批 11/11；首轮完整 1053/1055 的失败记录保留，完整重跑结果见 mod-internals。

2026-10-31 **23:38:53** 那轮完整自检在 flight finally 恢复期间自然退档，
结果 **465/466，1 fail、0 skip**。观察无事前空窗，新增两行非合成记录：
callback=`response=3/packet_id=39/packet_size=34/error_value=104`；
同 tick 861、同线程的 screen=`disconnect_reason=90/context=Block`。
SDK 公开枚举中 39 是 **SetActorData**。当时尚未确认 size/104 的边界，后续见下段。
这次已验证 mod 自己读到自然错误页原因，不需要桌面点击；上下文/详情均截断，不能当完整文本。
原配置恢复并重启加载，真实错误页 recover exit 0，最终状态回读 16/16，113 个 crash 无变化。
离线 **362/362**、ruff/format/mypy、C++ 构建与 profile 检查通过；**完整现场尚未全绿**，
水池修复仅专项验收，不据此提交为全套通过。证据与边界见
[`mod-internals`](../docs/mod-internals.md)，后续可转交
[`诊断提示词`](../docs/notes/prompts/badpacket-setactordata-20261031.md)。

2026-11-02 BadPacket 现行结论：证据册 §36–§49 已确认深拷贝路径无帧长度前缀，根因是游戏侧
`NetworkSystem` 的共享序列化缓冲被两个线程并发使用，产生粘连帧并触发 BadPacket(90)。
旧「声明 23」「邻居 payload 混入」「尚缺失败原帧」均为已撤回或历史阶段结论。harness 不补丁
游戏网络栈，只规避自有请求的不必要并发状态同步；有限未复现不等于根治。既有完整现场
**1076/1077** 的唯一必需 skip 是错误适用 framing 的 `inbound_slice_framing`；2026-11-02 已用
包头、实际样本边界、完整性与序号检查替代。守望专项 **41/41（0 fail、0 skip、0 required skip）**，
43 个真实切片全部通过；日志 `out/remaining-refactor-phase1-live.log`。
见 [`证据册`](../docs/notes/badpacket-setactordata-20261031.md) §49。

## License

LGPL-3.0. See [`../LICENSE`](../LICENSE).
