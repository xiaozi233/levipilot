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
| `watch_set` / `watch_clear` / `watch_list` / `watch_drain` | 硬件写观察点（DR0–DR3，size 1/2/4/8，mode `w`/`rw`，默认只装 tick 线程，`thread=all` 被拒绝）；`watch_drain` 的 `rip` 是触发后的下一条指令，附 `code_hex`=rip-16..rip |
| `record_start` / `record_stop` | 启动/停止定长二进制 tick 环（`period` 每 N tick 采样） |
| `record_dump` | `since,limit,format=jsonl/bin`；返回解码行或 64 字节记录的 hex 块，`next/head/lost` |
| `hook_install` | `symbol` 或 `va`，`tag/sample/dry_run`；符号钩用 typed detour，VA 安装需 `allow_hook_va` |
| `hook_rm` / `hook_list` | 卸钩 / 列出已装钩与命中统计 |
| `hook_drain` | `since,limit`；命中环（4 个整数参数、返回地址、4 帧栈、`b_float`） |
| `hook_call` | 调用一个已导出符号做自检（当前支持 `MobJump::setJumpPendingScale`） |
| `panic` / `unhook_all` | `level=hooks/tasks/all`；卸钩、清观察点、停任务并清输入，不杀进程 |
| `diagnose` | 版本/profile、4 个符号锚点的地址/24 字节签名匹配/耗时、能力表 |
| `profile_dump` | 导出当前实机的锚点字节、结构偏移、组件表，以及可直接保存的 profile JSON |
| `profile_probe` | `build_id`；不启动对应版本也能看它会选中哪个 profile（exact/prefix/none） |
| `signature_check` | `name,expected_hex`；对任意期望签名做比对（负路径自检） |
| `overhead` | tick/钩子耗时 p50/p99/max、`tick_hz`、`calls_per_sec`、`paths.{record,watch,hook}` 调用率 |
| `selftest_busy` | 在 tick 内忙等 `us` 微秒 × `ticks`（≤5 ms/200 tick），需 `allow_selftest_stress=true` |
| `ecs_context` | `context,component_id`；从 hook 捕获的 `EntityContext*` 直接解析组件（address/index/count/size） |
| `ecs_registries` | 客户端 EnTT registry 指针、组件 storage 数、entity id |
| `ecs_entity` | `runtime_id` → actor/context/registry/entity 地址、拥有组件数 |
| `ecs_components` | 枚举实体组件：运行时 `id`、index、地址、`stride`、storage `count` |
| `ecs_component` | 读组件原始字节（`len` 默认 stride，最大 512） |
| `spool_flush` | 立即把内存中的记录刷到 spool 文件 |
| `selftest_av` / `selftest_crash` | 自检：请求级访问违例被隔离 / 进程级崩溃取证（后者需 `allow_crash_test=true`） |
| `start` / `stop` | 逐 tick 采样（`tick` 事件） |
| `key_event` | `code,down,repeat,repeat_ms,via=hid/hardware/raw/queue`；`hid`=HID `onKeyDown`（Virtual）、`hardware`=Hardware 且标记注入、`raw`=Hardware 无注入标记（等价真人按键）、`queue`=`Keyboard::_inputs()`；需 `allow_input_inject=true` |
| `key_state` | `code`；游戏侧 `Keyboard::_states`、LL KeyInputEvent 计数、注入计数 |
| `mouse_event` | `action`（`move/left/right/middle/wheel/x1/x2/move_relative` 或 0–7）、`data/down`、`dx,dy,x,y`；走 `MouseDevice::feed` |
| `text_input` | `text`；通过 HID `onTextInput` 往当前输入框送字符（需 `allow_input_inject`） |
| `chat_send` | `text`；复制捕获的 `CommandRequestPacket` 模板并发往服务端（需 `allow_packet_inject`） |
| `op_self` | `level`（0–4）；在服务端玩家上设置命令权限（H2 自检用，需 `allow_packet_inject`） |
| `commands_enabled` | 读/写世界的 `mCommandsEnabled`（`value` 可选） |
| `packet_capture` | `on,ids?,sample?`；只记录 `(tick,seq,id,tid)`，需 `allow_packet_capture=true`；denylist 硬挡 |
| `packet_drain` | `since,limit`；身份记录（无字节），`seq` 单调、`injected` 预留 |
| `packet_bytes` | `since,limit`；B3 白名单序列化的 `{seq,id,size,hex}`（最多前 128 B） |
| `symbol_resolve` | `name`；编译期锚点 + E9/FF25 跟随，返回 `{anchor,real,module,rva}` |
| `packet_stats` | 记录数/丢失/denied/peer_sends/hook 状态 |
| `screen` | 当前界面名（`hud_screen` / `inventory_screen` / `chat_screen` / `pause_screen`） |
| `set` | `forward,strafe,sprint,sneak,jump,ticks`；`ticks=-1` 持续 |
| `look` | `yaw,pitch,relative,ticks`；`relative=false` 为绝对朝向 |
| `entities` | `radius,limit`，附近 Actor |
| `entity` | `runtime_id`；类型/名字/位置/速度/血量/距离 |
| `attack_entity` | `runtime_id`；在服务端线程用服务端玩家的 `GameMode::attack`（实体查找也在服务端线程） |
| `interact_entity` | `runtime_id`；转向实体并注入主手使用输入（best effort） |
| `look_at_entity` | `runtime_id`；把视角转向实体 |
| `blocks` | `positions:[[x,y,z],...]`，方块名和 `addr`（`Block*`，供 `probe` 使用） |
| `scan` | `from:[x,y,z],to:[x,y,z],include_air?,limit?`；区域方块扫描（最多 32768 格） |
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
`use` 等待服务端完成期间仍为 active，`phase=server_pending`；已取消的回调不消费物品。
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
| `fly` | `enabled`；客户端 + 服务端同时设置 |
| `togglefly` | `on`（可选，缺省=切换）；开关"双击空格切换飞行"的 `MayFly`，tick 线程写入 + 服务端镜像 + 8 tick 重断言，回 `togglefly_ok{mayfly,gamemode,flying}`；旁观回 `spectator_not_supported`。游戏内等价命令 `/togglefly` |
| `input_lock` | `on`；锁住真实硬件键鼠与相机转向（注入照常），见「限制」 |
| `teleport` | `x,y,z,yaw?,pitch?`；**脚部**坐标，服务端权威；**不动药水效果** |
| `reset` | `x,y,z,yaw?,pitch?`；清空任务/输入/macro/药水效果后传送到脚部坐标，用于跑酷重试（只有它会清效果） |
| `command` | `command`（可带 `/`）；服务器线程执行，保留 `@s`/`~` |
| `macro_load` / `macro_run` / `macro_stop` / `macro_status` / `macro_list` | macro 播放 |
| `macro_rec_start` / `macro_rec_stop` / `macro_save` | macro 录制/保存 |

`state` 关键字段：`x,y,z`（LocalPlayer 眼睛位置）、`feet_*`、`head_*`、`vx/vy/vz`、
`yaw/pitch`、`on_ground`、`sprinting/sneaking/swimming/flying/mayfly/in_water/alive`、
`health/max_health`、`hunger/saturation/exhaustion`、`xp_level/xp_progress`、
`gamemode/dimension/name/uuid/selected_slot/item`、
`block_feet/block_below/block_head/block_above`、`effects`（`id/duration/amplifier/ambient`），
以及 `input` 子对象。

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

当前基线：live selftest **215/215**（`harness.json` 开 `allow_input_inject`/
`allow_selftest_stress`）、离线单测 193/193。常用实测数字：

- 步行注入峰值 **0.2159 格/tick**（原版 4.317 m/s）；潜行约 0.30x。
- 跳跃逐 tick 位移 0.4200 / 0.3332 / 0.2481 / 0.1648…（顶点 1.252 格、滞空 11 tick），
  连续跳与原版轨迹误差 < 0.0001 格；起跳沿必须走 `LocalPlayer::jumpFromGround`。
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
  `togglefly_ok{mayfly,gamemode,flying}`；CLI `togglefly [--on|--off]`）走 tick 线程并带
  8 tick 重断言，抵消模式切换后迟到的能力同步（它会在开关之后才到、把开关覆盖掉）。
- `set_item --container armor` 的槽号是 Legacy `ArmorSlot`（0 头 / 1 胸 / 2 腿 / 3 脚）；
  往头槽放靴子不会报错但也不会生效。`equip` 只搬玩家自己的库存槽，不碰世界容器。
- 记录环的 `in_water`/`in_lava` 位是**脚/头方块名**判定（流动变体算液体），
  不是效果盒几何；浅水阈值（0.4 格）要另用速度行为判断。
- 起跳只有一条正确路径：`jump` 命令 / `set --jump`（含任务与 `jump_when`）都落到
  `LocalPlayer::jumpFromGround`（`input_cfg jump_impulse` 默认 1）。`jump_impulse=0`
  （只走输入管线：前 3 tick 原地、第 4 tick 补 V1+V2+V3 约 0.99 格）与 `=2`
  （`MobJump::setJumpPendingScale` 单独用不起跳）是实测坏路径，mod 直接回
  `jump_impulse_disabled` / `jump_impulse_pending_scale_only`；确需复现时带 `force=true`。
  `jump` 在空中发放会被丢弃（一次性请求，需 `onGround`），要等落地请用 `set --jump --ticks N`。
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
- 鼠标录制只认 raw input；后台合成点击只有移动事件。事件级回放（`macro_run event_level=true`）
  用 G1/G2 注入，已经覆盖按键/鼠标边沿；录制端仍然依赖真实 raw input。
- `mouse_event` 的按键只到 `MouseInputEvent` 层：计数会涨，但在 1.26.51.01 上不会触发世界攻击；
  攻击仍用 `click`/`attack_entity`。`action=move` 的 dx 能转向。
- H1 的身份 tap 始终可用；字节只对 `serialize_ids` 白名单生效（默认无），且只在 capture
  开启时调用 `Packet::write`（首次会分配一次线程局部缓冲）。`peer_sends` 恒为 0 是实测结果：
  单机回环不经过 `BatchedNetworkPeer` / `NetworkConnection::receivePacket`。
- H1-C 的 recv tap 是 opt-in（`directions` 默认只有 `send`）。`Packet::sendTo*` 辅助层的
  7 个钩子会装上但实测 0 调用；真正投递走 `LoopbackPacketSender::$sendToClient` 两个重载。
  重叠层可能对同一包各触发一次，记录按“同 tick + 同对象 + 同 id”去重，所以广播只看得到
  一条记录（要看投递次数用 `packet_stats.recv_anchors`）。
- H4 的 36/19 解码是标准 Bedrock 布局：36 是客户端→服务端，19 在单机里没有对端玩家，
  两者都仍然只有单测；接收侧已实测到 `Text(9)`、`AddActor(13)`、`MoveDeltaActor(111)`，
  但没有对应的解码器（保持原始 hex）。
- H2 只能注入 `CommandRequestPacket(77)`，而且必须先有一条真实的 77 包做模板；
  `op_self` 会修改世界里的玩家权限（测试世界用，别在日常存档里开）。
  `chat_send` 的 ok=true 只代表包已发出，命令是否执行要用 `blocks`/位置回读验证。
- 暂停菜单不响应注入按键（Esc/Enter 都无效）；退出暂停仍需桌面点击 Resume。背包（E）
  和聊天（`/`）可以开，背包用 Esc 能回到 hud。
- 输入注入依赖 `LocalPlayer::$normalTick` 符号；游戏升级后需重新构建。
- 跳跃必须走本地 `jumpFromGround`；只靠输入标志/队列/handler 在 1.26.51.01 上会有
  3 tick 延迟并一次性补位移。`jump_impulse` 只用于对照实验，正常不要改。
- 只支持 **1.26.51.01** 的实测结论；换版本按 [`docs/porting.md`](../docs/porting.md)
  走 profile 流程，不要直接套 1.26.45 的偏移。
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
- spool 每 100 ms 或 64 KB 刷一次，进程被强杀时最多丢最后约 100 ms 的记录；队列溢出
  会写一条 `queue_dropped` 标记进 spool。
- `mem_write` 默认关闭，开启后每次写入在 `harness-boot.log` 留一行审计。
- 二进制环固定 **524288 条 × 64 B ≈ 32 MB**，单写者（tick 线程）；`record_dump` 默认
  每次 1024 条，`since/next` 是绝对序号。因为传输是 JSONL，`bin`/`hex` 用 hex 分块而不是
  裸字节帧，避免破坏行协议。
- 记录里的 `block_below/feet/head` 是方块类型名的 FNV-1a 32 哈希；用
  `driver.harness.record.block_hash(name)` 对照。
- `hook_va` 的通用 detour 只保留 4 个整数参数寄存器（rcx/rdx/r8/r9）；float/xmm 参数、
  向量返回、超过 4 个参数或隐藏返回指针的目标**不安全**，需要 typed detour。目前 typed
  符号钩只实现了 `MobJump::setJumpPendingScale`。
- VA 只校验“已提交且可执行”的页，不校验指令边界；地址必须由 IDA 侧确认。
- `allow_hook_va` 默认关闭；`hook_install` 的 `sample` 用于降采样，命中环固定 4096 条。
- 版本校验：`build_id` 选中 profile 且 4 个锚点签名全匹配才 `version_ok=true`；不匹配时
  钩子/观察/记录回 `version_mismatch`，管道、`diagnose`、`profile_dump`、`mem_read`/`probe`
  仍可用（`allow_version_mismatch` 只改看 `hooks_allowed`，移植用）。锚点先跟随
  `jmp [rip+0]` 导入 thunk 再取真实函数。
- `mods/probe/offsets.json` 可以在运行时覆盖签名/结构偏移/组件 id，不用重编译；
  它只会改当前进程里已选中的 profile。
- ECS 组件 id 是**运行时** EnTT id，跨进程/跨版本不稳定；`ecs_components` 只报 id、地址、
  stride、count，不给类型名。stride 由同一 storage 的两个元素地址差推断，只有一个元素时
  为 0（此时 `ecs_component` 默认读 64 字节）。目前只暴露客户端 registry。

## License

LGPL-3.0. See [`../LICENSE`](../LICENSE).
