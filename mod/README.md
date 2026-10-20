# probe — LeviPilot 客户端 mod

LeviLamina 客户端 mod（游戏 **1.26.51.01**），在 `\\.\pipe\mcbe-harness` 上提供 JSON Lines API。
Python 客户端是 [`../driver/transport_mod.py`](../driver/transport_mod.py)。

## 构建

需要 VS Build Tools 2022、Windows SDK、LLVM 22 和 xmake 3.1.2。本机完整路径和两个必须保留的
工具链补丁见 [`../docs/mod-build-environment.md`](../docs/mod-build-environment.md)。

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
| `start` / `stop` | 逐 tick 采样（`tick` 事件） |
| `set` | `forward,strafe,sprint,sneak,jump,ticks`；`ticks=-1` 持续 |
| `look` | `yaw,pitch,relative,ticks`；`relative=false` 为绝对朝向 |
| `entities` | `radius,limit`，附近 Actor |
| `blocks` | `positions:[[x,y,z],...]`，方块名 |
| `click` | `button=left/right, reach`；左键破坏、右键放置 |
| `select_slot` | `slot=0..8` |
| `fly` | `enabled`；客户端 + 服务端同时设置 |
| `teleport` | `x,y,z,yaw?,pitch?`；**脚部**坐标，服务端权威 |
| `command` | `command`（可带 `/`）；服务器线程执行，保留 `@s`/`~` |
| `macro_load` / `macro_run` / `macro_stop` / `macro_status` / `macro_list` | macro 播放 |
| `macro_rec_start` / `macro_rec_stop` / `macro_save` | macro 录制/保存 |

`state` 关键字段：`x,y,z`（LocalPlayer 眼睛位置）、`feet_*`、`head_*`、`vx/vy/vz`、
`yaw/pitch`、`on_ground`、`sprinting/sneaking/swimming/flying/in_water/alive`、
`health/max_health`、`hunger/saturation/exhaustion`、`xp_level/xp_progress`、
`gamemode/dimension/name/uuid/selected_slot/item`、
`block_feet/block_below/block_head/block_above`，以及 `input` 子对象。

调试命令（`diag` / `mouse` / `buttons` / `input_cfg`）和注入/传送/macro 的实现细节见
[`../docs/mod-internals.md`](../docs/mod-internals.md)。

## 实测（1.26.51.01 创作存档）

- 步行注入峰值 **0.2159 格/tick**（原版 4.317 m/s）；`example.json` 60 tick 位移
  12.04 格、接地 100%
- 跳跃起跳约 1.23 格；树叶/天花板下会被挡
- `fly` 同时写服务端，传送后不会自己恢复飞行
- `click left` 破坏、`click right` 放置；`select_slot` 3→空手、0→草方块
- 服务端传送 2 秒后位置仍是目标值，`yaw/pitch` 生效
- `command` 的 `fill`/`setblock` 生效，`blocks` 能读回
- macro 播放/录制；真实鼠标左右键录进 macro（左键 4 tick、右键 3 tick 为 true）
- `driver/run_mod.py` 跑通 `setup-platform.json` 和 `example.json`

## 限制

- 方块类命令是 deferred，`success=false` 不代表失败，以 `blocks` / `setup.verify` 为准。
- `smoothMacro` / `frameBasedRotation` 未复刻，保持逐 tick。
- 鼠标录制只认 raw input；后台合成点击只有移动事件。
- 输入注入依赖 `LocalPlayer::$normalTick` 符号；游戏升级后需重新构建。
- 只对应 **1.26.51.01**，不能直接套 1.26.45 的偏移。

## License

LGPL-3.0. See [`../LICENSE`](../LICENSE).
