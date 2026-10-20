# Scenario DSL

`scenarios/*.json` 是逐 tick 实验的描述文件。当前入口是 **`driver/run_mod.py`**（LeviLamina
mod 传输层）；旧的 `driver/run.py` 仍保留给 frida 路线，见
[`legacy-frida-1.26.45.md`](legacy-frida-1.26.45.md)。

```bash
py -3.11 driver/run_mod.py --scenario scenarios/example.json
```

## 示例

```json
{
  "name": "flat-walk",
  "description": "在测试平台上朝 +Z 走 60 tick",
  "setup": {
    "commands": ["tp @s 0.5 -49 0.5 0 0"],
    "verify": [{ "pos": [0, -50, 0], "block": "minecraft:stone" }]
  },
  "steps": [
    { "type": "tp", "pos": [0.5, -49, 0.5] },
    { "type": "look", "yaw": 0, "pitch": 0 },
    { "type": "wait", "ticks": 5 },
    { "type": "input", "world": [0, 1], "ticks": 60 },
    { "type": "stop" }
  ],
  "sample": ["pos", "vel", "on_ground", "yaw", "pitch", "input"],
  "output": "example-flat-walk"
}
```

## step

| step | 参数 | 说明 |
| --- | --- | --- |
| `tp` | `pos`, `settle_ticks` | 服务端权威传送，坐标是**脚部**；`mode` 仅为兼容旧 DSL 接受，mod 始终走服务端 |
| `look` | `yaw`, `pitch`, `mode` (`command`/`memory`), `settle_ticks` | 有命令通道时默认 `command`（`tp @s ~ ~ ~ yaw pitch`）；`memory` 只改客户端朝向 |
| `vel` | `v:[x,y,z]` | 旧 frida 能力；mod agent 会跳过并给 warning |
| `input` | `world:[dx,dz]` 或 `forward`/`strafe`，`jump`/`sneak`/`sprint`，`mode:"sneak"`，`ticks` | 推荐 `world`；`sneak` 会自动处理原版 0.3 倍速 |
| `jump` | `ticks` | 单次起跳请求 |
| `wait` | `ticks` | 只等待 |
| `stop` | — | 清空持续输入 |

`tp` 的坐标约定与 `/tp`、HUD 一致（脚部）。`input` 会持续到下一个 `input`/`stop`。

## setup

| 字段 | 说明 |
| --- | --- |
| `commands` | 录制前执行的服务器命令（场地搭建），走 mod 的 `command` 通道 |
| `verify` | `[{"pos": [x,y,z], "block": "minecraft:stone"}]`，命令后轮询方块直到匹配 |
| `teardown` | 跑完（含中途失败）执行清场命令 |
| `strict` | `true` 时命令缺失/校验不过直接报错，而不是只记 warning |

注意：方块类命令是 deferred，`command_done.success=false` 不代表失败；以 `verify` /
方块读取为准。`setup` 之后 runner 会自动多等 60 tick，吸收命令执行后的进程卡顿。

测试平台由 `scenarios/setup-platform.json` 铺在 y=-50（地表 -49，65×65），
`scenarios/teardown-platform.json` 清掉。其他场景默认都在这块平台上跑。

## 输出

```bash
py -3.11 driver/run_mod.py --scenario scenarios/example.json --out-dir out/mod
```

每次运行写两个文件：

- `<name>-<时间>.jsonl`：第一行 meta、每个 tick 一行 sample、最后一行 summary
- `<name>-<时间>.summary.json`：位移、最大/平均水平速度、接地比例、每个 step 的起止位置

`sample` 控制 JSONL 字段；`flags` 展开为 `{lo, mid, hi}`。

速度按相邻 tick 的位置差计算（`speed_basis: "pos_delta"`）；`vel` 是摩擦前速度，
只作参考，不能直接当速度用。
