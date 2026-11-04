# probe — LeviPilot 客户端 mod

LeviLamina 客户端 mod（Minecraft Bedrock **1.26.51.01**），通过
`\\.\pipe\mcbe-harness` 提供 UTF-8 JSON Lines API。Python 兼容入口为
[`../driver/transport_mod.py`](../driver/transport_mod.py)。

## 构建与安装

需要 VS Build Tools 2022、Windows SDK、LLVM 22 和 xmake 3.1.2。本机路径与 SDK 补丁见
[`../docs/mod-build-environment.md`](../docs/mod-build-environment.md)。

```bash
cd mod/probe
export PATH="/d/Tools/LLVM22/bin:/d/Tools/xmake:$PATH"
export XMAKE_GLOBALDIR='D:\Tools\xmake-home'
export XMAKE_PKG_INSTALLDIR='D:\Tools\xmake-packages'

xmake f -c -y -p windows -a x64 -m release --target_type=client
xmake -j4 -y
```

产物在 `bin/probe/`：`probe.dll`、`probe.pdb`、`manifest.json`。xmake 不自动安装；手动安装时
把整个 `probe/` 放到实例 `mods/` 下，日常开发可使用受控的 `driver/dev.py run`。

CI 运行 Python/native 检查，Windows `mod-build` 也构建完整客户端 mod，但不启动游戏。

## API

请求为 `{"cmd":"...",...}`，响应与通知用 `event` 区分。未知命令回
`error/unknown_cmd`；协议只做增量变更。

### 状态、诊断与内存

| 命令 | 说明 |
| --- | --- |
| `ping` / `status` / `state` | 心跳、mod/生命周期状态、玩家快照 |
| `chat_history` | `limit=1..64`；客户端保留的聊天快照，含截断标记 |
| `mem_read` / `mem_write` | 进程内存读写；写需 `allow_write=true`，坏地址回 `bad_addr` |
| `probe` | `kind=raw/block/legacy`；结构化 Block/Legacy 读取 |
| `watch_set` / `watch_clear` / `watch_list` / `watch_drain` | DR0–DR3 观察点；`depth=1..32`，`thread=all` 被拒绝 |
| `hook_install` / `hook_rm` / `hook_list` / `hook_drain` | 符号或 VA hook；VA 需 `allow_hook_va`，可选 `mem_arg` 同步快照 |
| `hook_call` | 调用已支持的导出符号；当前为 `MobJump::setJumpPendingScale` |
| `panic` / `unhook_all` | `level=hooks/tasks/all`；卸钩、清观察点、取消任务和输入，不杀进程 |
| `diagnose` / `profile_dump` / `profile_probe` / `signature_check` | 版本、profile、锚点和签名诊断 |
| `overhead` | tick/hook 耗时与调用率统计 |
| `spool_flush` | 立即刷写内存中的 spool 记录 |
| `selftest_av` / `selftest_crash` | 请求异常隔离 / 进程崩溃取证；后者需 `allow_crash_test=true` |
| `selftest_busy` | tick 忙等自检，需 `allow_selftest_stress=true` |
| `selftest_callback` | 压力专项内部夹具：client/server/tick 一次性延迟 1–2000 ms、冷线程排空或 `action=clear`；入口为 `--callback-drain-only` |
| `lifecycle_prepare` | `on`；检查自有工作并关闭/恢复普通请求准入；常驻 hook 保留至 disable 排空后移除；需压力权限 |
| `send_probe` | 显式发送/序列化 suspect 取证，默认关闭，需 `allow_hook_va` 且在世界内 |

### 记录与输入

| 命令 | 说明 |
| --- | --- |
| `start` / `stop` | legacy JSON 逐 tick 采样 |
| `record_start` / `record_stop` | 二进制 tick 环；`period` 每 N tick 采样 |
| `record_dump` | `since,limit,format=jsonl/bin`；含 `next/head/lost` 和会话元数据 |
| `key_event` | `code,down,repeat,repeat_ms,via`；`hid/hardware/raw/queue`，需 `allow_input_inject` |
| `key_state` | 游戏键状态、LL 事件和注入计数；计数不等于游戏已收键 |
| `mouse_event` | `move/left/right/middle/wheel/x1/x2/move_relative`；走 `MouseDevice::feed` |
| `text_input` | 向当前输入框送字符；接受带 `input_id`，可等待 `text_input_done` |
| `screen` / `buttons` / `mouse` / `input_cfg` | UI/输入诊断与 legacy 对照配置 |
| `set` | `forward,strafe,sprint,sneak,jump,ticks`；默认合成当前绑定的 Hardware HID 按键 |
| `look` | `yaw,pitch,relative,ticks`；`relative=false` 为绝对朝向 |
| `click` | `button=left/right,reach`；实际破坏/放置效果须回读 |
| `select_slot` | `slot=0..8`；接受与 `select_slot_done` 分离 |
| `input_lock` | `on`；锁真实硬件输入，注入照常；断开客户端自动解锁 |
| `fly` / `togglefly` | 飞行与 `MayFly`；服务端写、客户端回读，游戏内也可用 `/togglefly` |

### 封包与网络诊断

| 命令 | 说明 |
| --- | --- |
| `packet_capture` | `on,ids,sample,directions,serialize_ids,serialize_budget_us`；需 `allow_packet_capture` |
| `packet_drain` | 定长身份记录，不含字节 |
| `packet_bytes` | 显式重序列化诊断的 `{seq,id,size,hex}`，附来源和截断 |
| `packet_stats` | 记录、丢失、hook、锚点和重序列化预算统计 |
| `chat_send` | 用真实命令模板发送 `CommandRequestPacket`；需 `allow_packet_inject` |
| `op_self` | 设置服务端玩家命令权限；自检用，需 `allow_packet_inject` |
| `commands_enabled` | 读/写世界命令开关 |
| `symbol_resolve` | 跟随编译期锚点/导入桩，返回 module+RVA |
| `violation_watch` / `violation_stats` / `violation_drain` / `violation_text` | 只读违规与断线页观察；VA 需 `allow_hook_va`，`synthetic` 只验证设施 |
| `disconnect_route` | 经真实 `composeRoute` 验证断线页路由 |
| `inbound_drain` / `inbound_dump` / `inbound_sample` | 入站切片、导出和采样步长 |
| `memmove_probe` | 显式 copy 诊断，默认关闭 |

### 玩家、实体与世界

| 命令 | 说明 |
| --- | --- |
| `entities` / `entity` | 附近实体列表 / 单个实体信息 |
| `attack_entity` | 服务端玩家 `GameMode::attack` |
| `interact_entity` | 转向并注入使用按钮，best effort |
| `look_at_entity` | 转向实体，回执带实际使用的 eye/target |
| `blocks` / `scan` | 方块读取 / 跨 tick 有界区域扫描 |
| `inventory` | `slots`、`armor`、`offhand`、`selected_slot` |
| `set_item` / `give` / `clear_item` / `clear_inventory` | 背包、盔甲和副手操作 |
| `equip` | 把库存栈移入 armor/offhand，附魔随栈移动 |
| `teleport` / `reset` | 服务端权威传送；`reset` 额外清任务/输入/macro/药水效果 |
| `checkpoint_save` / `checkpoint_load` / `checkpoint_list` / `checkpoint_delete` | 命名脚部坐标与朝向；load 不清药水效果 |
| `command` | 以本地 UUID 或显式完整玩家名执行服务端命令，保留 `@s`/`~` |
| `worlds` / `enter_world` / `leave_world` / `signin` | 世界列表、进入、退出和登录态 |
| `options_set` / `options_save` | 应用并保存选项；`options_set` 已包含落盘 |

### 闭环任务与 macro

| 命令 | 说明 |
| --- | --- |
| `navigate` | 走到 x,z；支持 `jump=auto/distance/edge/never` |
| `jump_when` | `now/tick/distance/edge` 的 tick 内起跳 |
| `route` | 最多 64 个路点，每段独立起跳策略和预算 |
| `mine` / `place` / `use` | 破坏、放置和服务端物品使用 |
| `task_status` / `task_stop` | 查询/取消唯一活动任务 |
| `macro_load` / `macro_run` / `macro_stop` / `macro_status` / `macro_list` | macro 播放 |
| `macro_rec_start` / `macro_rec_stop` / `macro_save` | macro 录制/保存，硬上限 200000 tick |

所有任务共用一个活动槽。started、完成和 `task_status` 都带 `task_id`；取消 reason 包括
`replaced/stopped/panic/reset/world_left/disabled/internal_error`。`use` 的
`server_pending` 有 5 秒预算，迟到回调不能操作物品。

## `state` 与事件字段

关键字段：`x,y,z`（眼睛）、`feet_*`、`head_*`、`vx/vy/vz`、`yaw/pitch`、
`on_ground`、`sprinting/sneaking/swimming/flying/mayfly/in_water/alive`、
`health/max_health`、`hunger/saturation/exhaustion`、`xp_level/xp_progress`、
`gamemode/dimension/name/uuid/selected_slot/item`、`block_*`、`effects`、`input` 和 `bindings`。

跑酷事件不需要 `start`：`player_landed`、`player_hurt`、`player_died`。legacy `start`
的 tick 另有 `in_air/jump_edge/block_below/block_feet/block_head`。

握手含 `protocol:4`、`capabilities`、`spool`、`replay_rows` 和 `queue_dropped`。`status`
另有 spool、record、profile、生命周期、pipe 写入超时和诊断统计。

## 配置（`harness.json`）

```json
{
  "auto_enter": "超平坦测试",
  "options": {"gfx_gamma": "0.5"},
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

`auto_enter` 等安全世界列表且游戏在前台后进入。`background_guard` 忽略后台真实鼠标转向，
不拦 mod 注入。各 `allow_*` 分别控制内存写、崩溃测试、VA hook、压力测试、观察点、输入注入
和封包能力，默认应按最小权限开启。

`lock_input` 只在 harness 客户端连接时生效，断开自动解锁；没有人连接时不会锁住手动 UI。
`signin_wait_ms=0` 表示只检查一次登录态。不需要配置时删除文件即可。

## 使用限制

### 输入、任务与传送

- 默认移动/跳跃使用当前键位的 Hardware HID；`mode`/`jump_impulse` 只在 `force=true` 下做
  legacy 对照。双击空格飞行需要 Hardware origin 且按下跨 tick。
- `command` 的方块效果是 deferred，`success=false` 不代表失败；`click`、任务、封包注入的
  接受/完成回执也不代替 `blocks`/`state` 回读。
- `interact_entity` 只转向并注入使用按钮；`mouse_event` 左右键不等于 `click`。
- `reset` 清效果；普通 `teleport` 和 `checkpoint_load` 保留效果。传送接受后需按
  `teleport_id` 等完成，再回读位置/朝向。
- 查询、实体、ECS、飞行、传送和封包注入都有忙、截止、取消与旧代次隔离；已开始的 SDK
  调用不支持超时抢占或自动回滚。
- `scan` 每 tick 最多 512 格，响应带起止 tick，不能当成同 tick 原子世界快照。

### 录制、宏与管道

- 宏录制省略、0 或超上限均取 200000 tick；到顶保留缓冲并可保存。保存使用临时文件和原子
  替换，失败保留旧文件与缓冲。
- 二进制环为 524288 条 64 B 记录；失败/退出可导出部分帧，但不跨 record_id 拼接。
- 管道输入上限 1 MiB，输出批量/大行写入预算 5 秒，超时断开当前客户端。请求超时关闭连接，
  独立事件等待超时不关闭连接。
- Python 客户端必须显式 close；GC/EOF 恢复不证明所有 Errno 22 或高频查询挂起已解决。

### 封包与诊断

- `serialize_ids` 在原上下文同步重序列化，可能分配。默认累计预算 **1 秒**，超预算记
  `Budget`/`serialize_skipped`；不能中断正在执行的 SDK 调用，字节不是线上原帧。
- `chat_send` 需要真实 77 模板；`status.command_template{ready,reason}` 是准入依据。
  `chat_sent.ok=true` 只表示发送调用完成。
- 入站深拷贝切片没有帧长度前缀，`before_hex` 是邻接堆字节，不能猜成长度。
- `violation_watch`、`send_probe`、`memmove_probe` 和 `mem_arg` 都是默认关闭的显式诊断，
  可能改变时序；合成违规不等于自然 BadPacket。
- 游戏侧共享序列化缓冲竞态是外部限制，harness 不补丁游戏网络栈。

### 世界、版本与恢复

- HBUI 不等于安全菜单；保存/进入门禁不能凭 URL 放宽。
- 站稳、无输入/宏/录制/任务时，经 `lifecycle_prepare` 静默的真实 `/ll reload probe` 已验收。
  使用 `py -3.11 driver/selftest.py --hot-reload-only --require-complete`，需显式开启输入注入、
  压力检查、封包采集与注入。它检查同 PID 的代次、完整日志、hook、tick 和 `/togglefly`
  实际效果并恢复能力。卸载还跟踪排队任务的最后一份副本、执行中回调与 EventBus 发布快照；
  回调线程内卸载、持键/任务忙、活跃诊断会话或排空超时会明确拒绝。现场竞争专项见验收文档。
  `--callback-drain-only --require-complete` 另验证三种在途回调、超时恢复，以及未经 prepare
  的持键/诊断拒绝；使用相同权限。它包含有界游戏线程延迟，只在测试存档运行。
- 只支持 **1.26.51.01** 的实测结论。版本数据只放在 `docs/offsets/*.json` 与生成头；
  修改后必须运行 `py -3.11 driver/gen_profiles.py`。
- `allow_version_mismatch` 只用于移植，不校验 profile，日常关闭。
- `dev.py run/stop` 只处理选定 exe 的 PID，并先验证保存退出；无法确认时保留进程。
  只有明确放弃未保存更改才使用 force 选项。

实现细节见 [`../docs/mod-internals.md`](../docs/mod-internals.md)，当前验收与未覆盖范围见
[`../docs/reliability-repairs.md`](../docs/reliability-repairs.md)。当前基线为离线
**428/428**、完整现场 **1100/1100**，只在对应代码与权限前提下有效。

## License

LGPL-3.0. See [`../LICENSE`](../LICENSE).
