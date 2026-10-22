# 跑酷记录与分析

这一层不重做 dmf-mpk 的 HUD / practice / Input Checker，而是给跑酷提供**数据采集、跳跃
拆分、两次运行对比和重复尝试统计**。数据来源是 mod 的逐 tick 录制。

## 一次运行的流程

```bash
# 1. 录制一段 macro 跑法（macro 文件在 mods/probe/macros/）
py -3.11 driver/parkour.py record --macro route1 --max-ticks 400 --out out/run1.jsonl

# 2. 拆成跳跃并出报告
py -3.11 driver/parkour.py analyze out/run1.jsonl --json out/run1.report.json

# 3. 和另一次运行对比
py -3.11 driver/parkour.py compare out/run1.jsonl out/run2.jsonl

# 4. 同一段跑 N 次，统计成功率
py -3.11 driver/parkour.py attempts --macro route1 \
  --start 0 -49 0 --target 0 4 --attempts 10 --out out/route1
```

`record` 会先 `reset` 到 `--start`（如果给了），再启动 macro 并录制到 macro 结束或
`--max-ticks`。`attempts` 每次都会 `reset`，所以两次尝试的初始位置、速度、朝向一致；
实测两次落点差小于 1e-4 格。

## tick 字段

| 字段 | 含义 |
| --- | --- |
| `x/y/z` | 眼睛位置（`getPosition()`） |
| `vx/vy/vz` | 速度 |
| `yaw/pitch` | 朝向 |
| `on_ground` / `in_air` | 是否在地面 |
| `jump_edge` | 本 tick 是否有起跳按下沿（mod 注入或 macro） |
| `block_below` | 脚下方块（`floor(feet_y)-1`） |
| `block_feet` / `block_head` | 脚部/头部所在方块 |
| `w/a/s/d/sprint/sneak/jump` | 输入状态 |
| `health` | 血量 |

注意 `x/y/z` 是眼睛位置，`block_*` 是按脚部坐标算的；报告里的起跳/落点高度用 `y`，比较
同一次运行的相对值即可。

## 分析模型

`segment_jumps()` 用 `on_ground` 的 true→false→true 切分跳跃：

- 起跳 tick：最后一个在地面的 tick。
- 落地 tick：之后第一个在地面的 tick。
- `air_ticks`：两者之差。
- `distance`：起跳/落点的水平距离。
- `apex`：空中最高点相对起跳点的高度。
- `takeoff_speed` / `landing_speed`：水平速度。
- `takeoff_block` / `landing_block`：起跳/落点脚下方块。
- `jumped`：起跳前 4 tick 内是否出现 `jump_edge`。现在 `jumpFromGround` 让按键沿和第一
  物理步对齐（边缘 tick 的下一 tick 开始上升），4 tick 窗口仍兼容旧录制。

开头就在空中或结尾还在空中的片段：开头忽略，结尾记为 `landed=false`。

报告结构：

```json
{
  "ticks": 80,
  "summary": {"jumps": 1, "completed": 1, "avg_distance": 3.2, "avg_air_ticks": 10, "avg_apex": 1.2},
  "jumps": [{"takeoff_tick": 8, "landing_tick": 18, "air_ticks": 10, "distance": 1.33, "apex": 1.23}],
  "splits": [{"name": "cp1", "tick": 35, "time_s": 1.7}]
}
```

`--checkpoint X Z` 可以重复给多个点，按顺序找第一次进入半径（默认 0.75）的 tick。

## 对比

`compare_runs(a, b)` 按跳跃序号对齐，输出每跳的 `delta_distance`、`delta_air_ticks`、
落点方块，以及总 tick 差。它不依赖画面，适合找出“哪一跳短了”或“哪一段多花了 tick”。

## 精确起跳与路点（P1）

真实跑酷不能靠 Python 每 tick 发命令（管道延迟会错过起跳窗口）。这三个原语都在 mod 的
tick 内判断，Python 只发一次命令：

```bash
# 立即跳 / 5 tick 后跳
py -3.11 driver/transport_mod.py jump-when --mode now
py -3.11 driver/transport_mod.py jump-when --mode tick --after-ticks 5

# 朝 (4.5, 0.5) 跑，水平距离 <= 1.0 时起跳
py -3.11 driver/transport_mod.py jump-when --mode distance --x 4.5 --z 0.5 \
    --distance 1.0 --forward 1 --sprint --max-ticks 100

# 前方脚下没有支撑时起跳（站到方块边缘即可）
py -3.11 driver/transport_mod.py jump-when --mode edge

# 两段路点，每段都能设自己的起跳策略
py -3.11 driver/transport_mod.py route '[
  {"x": 3.5, "z": 0.5, "tolerance": 0.4},
  {"x": 3.5, "z": 3.5, "tolerance": 0.4, "jump": "edge"}
]'

# 走过去，在离目标 1.0 格时起跳过缺口
py -3.11 driver/transport_mod.py navigate 8.5 0.5 --jump distance --jump-distance 1.0
```

- `jump_when` 的 `mode=distance` 会同时把视角转向目标；`mode=edge` 保留当前朝向，可以用
  `--yaw` 固定。
- `navigate` / `route` 的 `jump` 取值：`auto`（默认，卡住才跳）、`distance`、`edge`、`never`。
  `edge_lookahead` 默认 0.55 格，决定提前多久看到缺口。
- `route` 每到一个路点发 `route_waypoint`（index/ticks），结束发 `route_done`；某段超时发
  `route_failed`（带 index/target/distance）。最多 64 个路点，超过请拆成多条命令。
- `jump_when` 返回 `jumped`：`ok`、`mode`、触发时的 `distance`、`ticks`、`on_ground`；
  超时回 `ok=false, reason=timeout`。

实测（超平坦）：`mode=now` 1 tick、`mode=tick after_ticks=5` 5 tick；距离模式在 0.88 格
起跳；edge 模式站在 1×1 石柱上第 1 tick 触发，滞空 11 tick、顶点 1.252 格；两路点
`route` 33 tick 完成；`navigate` 距离模式 33 tick 到达且 `jumps=1`。

起跳的逐 tick 位移现在是 0.4200、0.3332、0.2481、0.1648、0.0831、0.0030、-0.0754…，
与基岩版公式 `V1=0.42`、`Vt=(Vt-1-0.08)*0.98` 一致。此前只注入输入标志时，前 3 tick
位置不动、第 4 tick 一次性补 +0.99 格，视觉上就是“卡回弹”。

## 区域扫描、存档点与事件

```bash
# 扫一个区域（包含边界，最多 32768 格；默认跳过空气）
py -3.11 driver/transport_mod.py scan 0 -50 0 16 -40 16 --limit 4096

# 存/读脚部坐标 + 朝向（存一次即可，之后反复重试）
py -3.11 driver/transport_mod.py checkpoint save cp1
py -3.11 driver/transport_mod.py checkpoint load cp1
py -3.11 driver/transport_mod.py checkpoint list
```

- `scan` 在 tick 线程执行，返回 `{x,y,z,name}`；区域过大会回 `region_too_large`。
  结果超过 `limit` 时 `truncated=true`。
- 存档点写在 `mods/probe/checkpoints.json`（临时文件 + 原子替换），`checkpoint_load` 走
  `reset` 同一条清理路径：清任务/输入/macro/药水效果，然后服务端传送。返回 `checkpoint_loaded`。
- `state.effects` 给出当前药水效果（`id`、`duration`、`amplifier`、`ambient`），用于确认
  speed/jump boost 是否还在。
- 事件流里新增 `player_landed`（`fall_distance`、`health`）、`player_hurt`
  （`health`、`damage`）、`player_died`。落地事件只在落差 ≥0.75 格时发，避免每次小跳刷屏。

实测：`checkpoint load` 落点误差 0.000 格；3×3×4 区域 `scan` 返回 6 个非空气方块；
抬升 5 格落地得到 `fall_distance=4.92`、血量 20→18；`speed 10 2` 在 `state.effects`
里读回 `id=1, amplifier=2`。

## 设置与焦点

- `reset` 会清空任务/输入/macro/**药水效果**，然后走服务端传送；座标是**脚部**。传送在下一
  tick 生效，`attempts` 会等 4 tick 再开始。实测 speed V 在 reset 后不再影响位移。
- 游戏在后台时，mod 默认开启 `background_guard`：释放光标裁剪、显示光标，并忽略后台鼠标
  引起的 `_applyTurnDelta`；mod 自己的 `look`/任务不受影响。配置在
  `mods/probe/harness.json` 的 `background_guard`。
- `dev.py run` 会尝试把游戏窗口带到前台，但 Windows 的前台锁可能拒绝；失败时只打印警告，
  不影响进世界和后台控制。

## 与 dmf-mpk 的分工

dmf-mpk 负责 HUD、practice、Input Checker 和 macro 播放界面；本仓库负责逐 tick 数据、
跳跃拆分、运行对比、批量尝试和脚本/LLM 接口。macro 文件格式与 dmf 兼容，可以互相导入。