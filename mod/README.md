# probe — LeviPilot 客户端 mod

LeviLamina 客户端 mod（游戏 **1.26.51.01**），在 `\\.\pipe\mcbe-harness` 上提供 JSON Lines API。
Python 客户端是 [`../driver/transport_mod.py`](../driver/transport_mod.py)。

## 构建

需要 VS Build Tools 2022、Windows SDK、LLVM 22 和 xmake 3.1.2。本机完整路径和两个必须保留的
工具链补丁见 [`../docs/mod-build-environment.md`](../docs/mod-build-environment.md)。
GitHub Actions 只运行 Python 单元测试；mod 构建依赖本机 LLVM22/xmake 补丁，是本地步骤。

```bash
cd mod/probe

export PATH="/d/Tools/LLVM22/bin:/d/Tools/xmake:$PATH"
export XMAKE_GLOBALDIR='D:\Tools\xmake-home'
export XMAKE_PKG_INSTALLDIR='D:\Tools\xmake-packages'

xmake f -c -y -p windows -a x64 -m release --target_type=client
xmake -y
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
| `mem_read` / `mem_write` | 读/写本进程内存（`addr,len` / `addr,hex`）；写需要 `allow_write=true`；坏地址回 `ok=false,reason=bad_addr` |
| `probe` | `kind=raw/block/legacy`；结构化读取 `Block*` → `BlockLegacy*`、名字哈希/字符串、block id 和两个 float |
| `watch_set` / `watch_clear` / `watch_list` / `watch_drain` | 硬件写观察点（DR0–DR3，size 1/2/4/8，mode `w`/`rw`，默认只装 tick 线程） |
| `record_start` / `record_stop` | 启动/停止定长二进制 tick 环（`period` 每 N tick 采样） |
| `record_dump` | `since,limit,format=jsonl/bin`；返回解码行或 64 字节记录的 hex 块，`next/head/lost` |
| `hook_install` | `symbol` 或 `va`，`tag/sample/dry_run`；符号钩用 typed detour，VA 安装需 `allow_hook_va` |
| `hook_rm` / `hook_list` | 卸钩 / 列出已装钩与命中统计 |
| `hook_drain` | `since,limit`；命中环（4 个整数参数、返回地址、4 帧栈、`b_float`） |
| `hook_call` | 调用一个已导出符号做自检（当前支持 `MobJump::setJumpPendingScale`） |
| `panic` / `unhook_all` | `level=hooks/tasks/all`；卸钩、清观察点、停任务并清输入，不杀进程 |
| `diagnose` | 版本、4 个符号锚点的地址/24 字节签名匹配/耗时 |
| `signature_check` | `name,expected_hex`；对任意期望签名做比对（负路径自检） |
| `overhead` | tick/钩子耗时 p50/p99/max、`tick_hz`、`calls_per_sec`、`paths.{record,watch,hook}` 调用率 |
| `selftest_busy` | 在 tick 内忙等 `us` 微秒 × `ticks`（≤5 ms/200 tick），需 `allow_selftest_stress=true` |
| `ecs_registries` | 客户端 EnTT registry 指针、组件 storage 数、entity id |
| `ecs_entity` | `runtime_id` → actor/context/registry/entity 地址、拥有组件数 |
| `ecs_components` | 枚举实体组件：运行时 `id`、index、地址、`stride`、storage `count` |
| `ecs_component` | 读组件原始字节（`len` 默认 stride，最大 512） |
| `spool_flush` | 立即把内存中的记录刷到 spool 文件 |
| `selftest_av` / `selftest_crash` | 自检：请求级访问违例被隔离 / 进程级崩溃取证（后者需 `allow_crash_test=true`） |
| `start` / `stop` | 逐 tick 采样（`tick` 事件） |
| `set` | `forward,strafe,sprint,sneak,jump,ticks`；`ticks=-1` 持续 |
| `look` | `yaw,pitch,relative,ticks`；`relative=false` 为绝对朝向 |
| `entities` | `radius,limit`，附近 Actor |
| `entity` | `runtime_id`；类型/名字/位置/速度/血量/距离 |
| `attack_entity` | `runtime_id`；对指定实体执行 `GameMode::attack` |
| `interact_entity` | `runtime_id`；转向实体并注入主手使用输入（best effort） |
| `look_at_entity` | `runtime_id`；把视角转向实体 |
| `blocks` | `positions:[[x,y,z],...]`，方块名和 `addr`（`Block*`，供 `probe` 使用） |
| `scan` | `from:[x,y,z],to:[x,y,z],include_air?,limit?`；区域方块扫描（最多 32768 格） |
| `checkpoint_save` / `checkpoint_load` / `checkpoint_list` / `checkpoint_delete` | 命名脚部坐标存档，持久化到 `mods/probe/checkpoints.json` |
| `navigate` | `x,z,tolerance,max_ticks,sprint`；`jump=auto/distance/edge/never` + `jump_distance`，mod 内逐 tick 闭环修正朝向 |
| `jump_when` | `mode=now/tick/distance/edge`；`distance,forward,sprint,yaw,after_ticks,max_ticks`；在 tick 内精确起跳 |
| `route` | `waypoints:[{x,z,tolerance,max_ticks,jump,jump_distance}]`；逐段跑点，`route_waypoint` / `route_done` / `route_failed` |
| `mine` | `x,y,z,max_ticks`；`startDestroyBlock` + `continueDestroyBlock`，完成后回报方块 |
| `place` | `x,y,z,against?,face?,expected?,slot?,max_ticks`；瞄准、放置并回读验证 |
| `use` | `ticks`；等待后在服务端玩家上走 `Item::use`（+ 食物的 `useTimeDepleted`），消耗、效果和背包同步都是权威的 |
| `task_status` / `task_stop` | 查询或取消当前闭环任务 |
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
| `teleport` | `x,y,z,yaw?,pitch?`；**脚部**坐标，服务端权威 |
| `reset` | `x,y,z,yaw?,pitch?`；清空任务/输入/macro/药水效果后传送到脚部坐标，用于跑酷重试 |
| `command` | `command`（可带 `/`）；服务器线程执行，保留 `@s`/`~` |
| `macro_load` / `macro_run` / `macro_stop` / `macro_status` / `macro_list` | macro 播放 |
| `macro_rec_start` / `macro_rec_stop` / `macro_save` | macro 录制/保存 |

`state` 关键字段：`x,y,z`（LocalPlayer 眼睛位置）、`feet_*`、`head_*`、`vx/vy/vz`、
`yaw/pitch`、`on_ground`、`sprinting/sneaking/swimming/flying/in_water/alive`、
`health/max_health`、`hunger/saturation/exhaustion`、`xp_level/xp_progress`、
`gamemode/dimension/name/uuid/selected_slot/item`、
`block_feet/block_below/block_head/block_above`、`effects`（`id/duration/amplifier/ambient`），
以及 `input` 子对象。

每个 tick 还会在事件流里发跑酷相关事件（不需要 `start`）：`player_landed`
（`fall_distance/health`）、`player_hurt`（`health/damage`）、`player_died`。

`start` 录制的每个 `tick` 事件额外带跑酷字段：`in_air`、`jump_edge`（本 tick 是否有起跳
按下沿）、`block_below` / `block_feet` / `block_head`（按脚部坐标取方块）。

握手 `hello` 现在带 `protocol:4`、`capabilities`（`tick_json`/`bin_record`/`mem`/`watch`/
`signin`/`hook`/`ecs`）、`spool`（本进程 spool 文件绝对路径）、`replay_rows`（没有客户端时积压、
本次连接会补发的行数）和 `queue_dropped`。`status` 额外带 `spool_rows`、
`spool_flushes`、`allow_write`、`tick_thread_id`、`tick_probe_addr`、`watch_count`、
`watch_hits`、`watch_dropped`、`record_active`、`record_rows`、`record_head`、
`record_period`、`record_capacity`、`build_id`、`version_ok`、`version_failures`。

调试命令（`diag` / `mouse` / `buttons` / `input_cfg`）和注入/传送/macro 的实现细节见
[`../docs/mod-internals.md`](../docs/mod-internals.md)。

### 自动进入世界

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
  "signin_wait_ms": 15000
}
```

`auto_enter` 是存档名（也接受 `id` 字段）；mod 会等世界列表准备好、并且游戏在**前台**后
再进入（后台进世界会触发鼠标捕获）。`options` 会先应用并写回 `options.txt`。
`background_guard` 默认开启：游戏失去前台时释放光标裁剪，并忽略后台鼠标引起的视角旋转；
它不会阻止 mod 自己的 `look`/任务。`allow_write` 才能用 `mem_write`；`allow_crash_test`
才能用会主动结束进程的 `selftest_crash`；`allow_hook_va` 才能按任意 VA 装钩（符号钩不需要）；
`allow_selftest_stress` 才允许 `selftest_busy` 在 tick 里忙等（默认关闭，避免误让 tick 卡近 1 秒）；
`signin_wait_ms` 是进世界前等 Xbox Live 登录的上限（默认 15000，设 0 表示查一次就走）。
不需要时删除该文件即可。

## 实测（1.26.51.01 创作存档）

- 步行注入峰值 **0.2159 格/tick**（原版 4.317 m/s）；`example.json` 60 tick 位移
  12.04 格、接地 100%
- 跳跃：起跳沿同时调用 `jumpFromGround`，逐 tick 位移为 0.4200、0.3332、0.2481、0.1648…，
  顶点 1.252 格、滞空 11 tick；修复了此前前 3 tick 不动、第 4 tick 一次性 +0.99 的“卡回弹”
- 潜行：20 tick 前进 4.316 格，潜行时 1.295 格（约 0.30x）
- `fly` 同时写服务端，传送后不会自己恢复飞行
- `click left` 破坏、`click right` 放置；`select_slot` 3→空手、0→草方块
- 服务端传送 2 秒后位置仍是目标值，`yaw/pitch` 生效
- `command` 的 `fill`/`setblock` 生效，`blocks` 能读回
- macro 播放/录制；真实鼠标左右键录进 macro（左键 4 tick、右键 3 tick 为 true）
- `driver/run_mod.py` 跑通 `setup-platform.json` 和 `example.json`
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
- `hook_install` 按符号 `MobJump::setJumpPendingScale` 装钩成功；`hook_call` 触发后
  `hook_drain` 读到 `args[0]=EntityContext*`、`b_float=1.0`、返回地址和 4 帧栈；
  `hook_rm` 后 `hook_list` 为空。VA dry_run 对同一地址返回 `executable=true`，
  对 `0x10` 返回 `false`
- `diagnose`：4 个锚点（normalTick/_applyTurnDelta/getHealth/isOnGround）24 字节全部匹配，
  `build_id=1.26.51.1`、`version_ok=true`
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
  命中 tid 等于 tick 线程；`watch_clear` 后不再增加
- `driver/selftest.py` 80/80 通过

## 限制

- 方块类命令是 deferred，`success=false` 不代表失败，以 `blocks` / `setup.verify` 为准。
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
- 鼠标录制只认 raw input；后台合成点击只有移动事件。
- 输入注入依赖 `LocalPlayer::$normalTick` 符号；游戏升级后需重新构建。
- 跳跃必须走本地 `jumpFromGround`；只靠输入标志/队列/handler 在 1.26.51.01 上会有
  3 tick 延迟并一次性补位移。`jump_impulse` 只用于对照实验，正常不要改。
- 只对应 **1.26.51.01**，不能直接套 1.26.45 的偏移。
- `probe` 的 Block/Legacy 字段偏移集中在 `Memory.cpp` 的布局常量里，仍是版本相关；
  签名解析是 T7 的工作。
- 硬件观察点全局共 4 个 DR 槽；线程已被调试器（或其他工具）占用时回 `dr_busy`，
  不会强抢。x86 的 `rw` 实际是“读写/IO”语义，没有纯读观察点。
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
- 版本校验比较 exe 文件版本前缀（1.26.51）和 4 个符号锚点的 24 字节入口签名；任一不符时
  tick 注入/观察、装钩、观察点、二进制记录全部拒绝（回 `version_mismatch`）；管道、
  `diagnose`、`mem_read`/`probe` 仍可用，方便排查。锚点先跟随 `jmp [rip+0]` 导入 thunk，再取真实函数。
- 可把 `docs/offsets/1.26.51.01.json` 复制到 `mods/probe/offsets.json` 覆盖内置签名，无需重编译。
- ECS 组件 id 是**运行时** EnTT id，跨进程/跨版本不稳定；`ecs_components` 只报 id、地址、
  stride、count，不给类型名。stride 由同一 storage 的两个元素地址差推断，只有一个元素时
  为 0（此时 `ecs_component` 默认读 64 字节）。目前只暴露客户端 registry。

## License

LGPL-3.0. See [`../LICENSE`](../LICENSE).
