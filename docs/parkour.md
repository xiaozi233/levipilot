# 跑酷记录与分析

本项目不重做 dmf-mpk 的 HUD、practice 或 Input Checker，而是提供逐 tick 数据采集、跳跃拆分、
运行对比和批量尝试。分析工具可离线运行。

## 一次运行

```bash
# macro 文件位于 mods/probe/macros/
py -3.11 driver/parkour.py record --macro route1 --max-ticks 400 --out out/run1.jsonl
py -3.11 driver/parkour.py analyze out/run1.jsonl --json out/run1.report.json
py -3.11 driver/parkour.py compare out/run1.jsonl out/run2.jsonl
py -3.11 driver/parkour.py attempts --macro route1 \
  --start 0 -49 0 --target 0 4 --attempts 10 --out out/route1
```

`record` 可先 `reset` 到 `--start`，再播放 macro 并录制到结束或 `--max-ticks`。`attempts`
每次都重置位置、速度、朝向和输入；`reset` 也会清药水效果，需要保留效果时不要用它恢复姿态。

## tick 字段

| 字段 | 含义 |
| --- | --- |
| `x/y/z` | 眼睛位置 |
| `vx/vy/vz` | `Actor::getVelocity()` 的参考值；实际位移以相邻 tick 位置差为准 |
| `yaw/pitch` | 朝向 |
| `on_ground` / `in_air` | 地面状态 |
| `jump_edge` | 本 tick 是否有起跳按下沿 |
| `block_below` | 脚下方块（`floor(feet_y)-1`） |
| `block_feet` / `block_head` | 脚部/头部所在方块 |
| `w/a/s/d/sprint/sneak/jump` | 输入状态 |
| `health` | 血量 |

`block_*` 按脚部计算，不要直接与眼睛的绝对 `y` 混用；报告中的高度比较应保持同一坐标口径。

## 跳跃分析与对比

`segment_jumps()` 用 `on_ground` 的 true→false→true 分段：

- 起跳 tick 是最后一个地面 tick，落地 tick 是之后第一个地面 tick，`air_ticks` 为两者之差。
- 报告包含水平距离、顶点、起跳/落地速度和脚下方块。
- 起跳前 4 tick 内的 `jump_edge` 用于关联输入；默认 Hardware HID 路径不使用
  `jumpFromGround` 冲量。
- 开头已在空中的片段忽略，结尾未落地的片段记 `landed=false`。

```json
{
  "ticks": 80,
  "summary": {"jumps": 1, "completed": 1, "avg_distance": 3.2, "avg_air_ticks": 10, "avg_apex": 1.2},
  "jumps": [{"takeoff_tick": 8, "landing_tick": 18, "air_ticks": 10, "distance": 1.33, "apex": 1.23}]
}
```

`--checkpoint X Z` 可重复使用，按顺序找第一次进入半径（默认 0.75）的 tick。
`compare_runs(a,b)` 按跳跃序号对齐，输出每跳的距离、滞空、落点和总 tick 差。

## 精确起跳与路点

这些原语在 mod tick 内闭环，Python 只发一次请求，不进入逐 tick 反馈环：

```bash
py -3.11 driver/transport_mod.py jump-when --mode now
py -3.11 driver/transport_mod.py jump-when --mode tick --after-ticks 5
py -3.11 driver/transport_mod.py jump-when --mode distance --x 4.5 --z 0.5 \
    --distance 1.0 --forward 1 --sprint --max-ticks 100
py -3.11 driver/transport_mod.py jump-when --mode edge

py -3.11 driver/transport_mod.py route '[
  {"x": 3.5, "z": 0.5, "tolerance": 0.4},
  {"x": 3.5, "z": 3.5, "tolerance": 0.4, "jump": "edge"}
]'
py -3.11 driver/transport_mod.py navigate 8.5 0.5 --jump distance --jump-distance 1.0
```

- `distance` 会转向目标，`edge` 保留当前朝向；`--yaw` 可显式指定。
- `navigate`/`route` 的 `jump` 为 `auto/distance/edge/never`，`edge_lookahead` 默认 0.55 格。
- `route` 每段发 `route_waypoint`，结束发 `route_done`，失败发 `route_failed`。
- `jump_when` 的超时回 `ok=false,reason=timeout`；任务结果按 `task_id` 关联。

默认起跳按当前 jump 绑定发 Hardware HID。旧直接输入标志曾造成补帧/回弹，不能据此恢复
`jumpFromGround` 或其他冲量注入。

## 区域扫描、存档点与事件

```bash
py -3.11 driver/transport_mod.py scan 0 -50 0 16 -40 16 --limit 4096
py -3.11 driver/transport_mod.py checkpoint save cp1
py -3.11 driver/transport_mod.py checkpoint load cp1
py -3.11 driver/transport_mod.py checkpoint list
```

- `scan` 跨 tick 执行，每 tick 最多读取 512 格；总量最多 32768 格，结果超过 `limit` 时
  `truncated=true`，响应带起止 tick。
- 存档点写在 `mods/probe/checkpoints.json`，使用临时文件和原子替换。`checkpoint_load`
  恢复位置与朝向，**不清药水效果**；只有显式 `reset` 会清效果。
- `state.effects` 可回读效果 id、duration、amplifier 和 ambient。
- `player_landed` 在落差至少 0.75 格时发出，另有 `player_hurt` 和 `player_died`。

## 焦点与分工

`background_guard` 默认忽略后台真实鼠标转向，不拦 mod 注入。`dev.py run` 尝试把窗口带到前台，
但 Windows 可能拒绝，不能把前台失败当成世界进入失败。

dmf-mpk 负责 HUD、practice、Input Checker 和 macro 界面；本仓库负责数据、分析、批量尝试和
脚本/LLM API。macro 文件格式兼容，可以互相导入。

## 另见

- 命令与字段：[`../mod/README.md`](../mod/README.md)
- 实现与容量：[`mod-internals.md`](mod-internals.md)
- dmf-mpk 分工：[`../README.md`](../README.md)、[`../reverse/dmf-mpk-analysis.md`](../reverse/dmf-mpk-analysis.md)
