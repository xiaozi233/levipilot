# mcbe-harness

Minecraft Bedrock 1.26.45.01 的实验驱动 harness。

目标：不再依赖 Computer Use / 合成键鼠输入，通过 frida RPC 和游戏内脚本 API
控制实验、逐 tick 采样，把"操作游戏"换成"调用游戏"。

## 约束

- 游戏安装目录只读，任何构建产物、脚本、日志都不写回安装目录：
  `%APPDATA%\levilauncher.exe\versions\1.26.45.01`
- 不发送合成键鼠输入，不抢前台窗口。
- 观测走内存/钩子，不靠截图；截图只用于最终视觉验收。
- 客户端与 BDS 必须同为 1.26.45.01，否则行为常数可能不同。

## 环境

- Python 3.11（`py -3.11`），已装 frida 17.18.0。
- Python 3.14 默认解释器没有 frida，不要用它跑 driver。

## 目录

| 路径 | 用途 |
| --- | --- |
| `agent/` | frida agent，暴露 `rpc.exports` |
| `driver/` | Python 场景驱动器 |
| `scenarios/` | 场景 DSL（JSON） |
| `behavior_pack/` | GameTest / mcfunction 场景准备 |
| `out/` | 运行产物，已 gitignore |

## 快速开始

```bash
py -3.11 driver/run.py            # 按进程名附加
py -3.11 driver/run.py <pid>      # 按 PID 附加
```

成功时输出 `pong`。

## 状态

- [x] 仓库与目录骨架
- [ ] frida RPC：`getState` / `tp` / `setVel` / `setInput` / `setYawPitch` / tick 采样
- [ ] driver：读取 scenario JSON，执行并写 JSONL
- [ ] GameTest：`spawnSimulatedPlayer` 与真实输入的等价性实验
- [ ] 第一个可回归场景（超平坦直走 / 撞墙 / 台阶）
