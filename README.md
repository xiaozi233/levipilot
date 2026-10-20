# LeviPilot

Minecraft Bedrock **1.26.51.01** 的客户端实验 harness：一个 LeviLamina mod 加一条命名管道 API，
让 Python 脚本或 LLM 直接读取游戏状态、控制玩家、跑逐 tick 实验。

## 能做什么

- **读**：玩家位置/速度/朝向/生命/饥饿/经验/手持物/输入状态、完整背包/盔甲/副手、附近实体、任意方块
- **控制**：移动、疾跑、潜行、跳跃、飞行、绝对/相对视角、左右键、快捷栏、服务端权威传送；
  按 `runtime_id` 查询/转向/攻击/交互实体
- **执行**：任意服务端命令（`fill`/`tp`/`setblock`…），保留 `@s`/`~` 语义
- **Macro**：dmf-mpk 兼容格式的加载、播放、录制，含真实鼠标左右键
- **实验**：逐 tick 采样、场景 DSL（setup → steps → verify/teardown → JSONL + summary）、
  `driver/selftest.py` live 自检

## 快速开始

前置：Windows、Minecraft Bedrock 1.26.51.01（LeviLauncher + LeviLamina）、Python 3.11。
构建 mod 还需要 xmake 与 LLVM 22，见 [`mod/README.md`](mod/README.md)。

```bash
# 1. 构建并把产物复制到实例的 mods/probe（构建不会自动写游戏目录）
cd mod/probe
xmake -y
cp bin/probe/* "<实例>/mods/probe/"

# 2. 进入存档后确认管道
py -3.11 driver/transport_mod.py ping
py -3.11 driver/transport_mod.py state

# 3. 常用操作
py -3.11 driver/transport_mod.py set --forward 1 --ticks 40
py -3.11 driver/transport_mod.py look --yaw 90 --pitch 0 --absolute
py -3.11 driver/transport_mod.py teleport 0.5 -49 0.5 --yaw 0 --pitch 0
py -3.11 driver/transport_mod.py command "fill -4 -60 -4 4 -60 4 stone"
py -3.11 driver/transport_mod.py slot 3
py -3.11 driver/transport_mod.py click left
py -3.11 driver/transport_mod.py macro load --name default
py -3.11 driver/transport_mod.py macro run

# 4. 场景 DSL
py -3.11 driver/run_mod.py --scenario scenarios/setup-platform.json
py -3.11 driver/run_mod.py --scenario scenarios/example.json
```

## 项目结构

| 路径 | 内容 |
| --- | --- |
| `mod/probe/` | LeviLamina 客户端 mod（C++），命名管道 `\\.\pipe\mcbe-harness` |
| `driver/transport_mod.py` | Python 客户端与 CLI |
| `driver/mod_agent.py`, `driver/run_mod.py` | 场景 DSL 适配器与执行入口 |
| `driver/macro.py` | dmf-mpk macro 文件工具 |
| `scenarios/` | 场景 JSON |
| `reverse/` | dmf-mpk 逆向报告与 IDA 导出脚本 |
| `docs/` | 补充文档 |
| `tests/` | 不依赖游戏的单元测试 |

## 文档

- Mod 构建、API、协议、实测结果与限制：[`mod/README.md`](mod/README.md)
- dmf-mpk 逆向报告：[`reverse/dmf-mpk-analysis.md`](reverse/dmf-mpk-analysis.md)
- 场景 DSL 与输出格式：[`docs/scenario-dsl.md`](docs/scenario-dsl.md)
- UI 自动化（启动 / 进存档 / 退档）：[`docs/ui-automation.md`](docs/ui-automation.md)
- 旧 frida 路线（1.26.45.01，历史资料；代码在 `legacy/frida` 分支）：[`docs/legacy-frida-1.26.45.md`](docs/legacy-frida-1.26.45.md)
- IDA MCP 排障：[`docs/ida-mcp-troubleshooting.md`](docs/ida-mcp-troubleshooting.md)

## 状态与限制

- 已在 1.26.51.01 创作存档内实测：状态读取、移动/视角/跳跃/飞行、左右键、快捷栏、
  服务端传送、服务端命令、macro 播放/录制、真实鼠标按键、场景 DSL。
- 步行注入峰值 **0.2159 格/tick**（原版 4.317 m/s）。
- `command` 的方块类命令是 deferred，`success=false` 不代表失败，效果以 `blocks`/verify 为准。
- `interact_entity` 是转向 + 注入使用按钮（best effort）；直接调用游戏的实体交互函数会崩溃。
- `smoothMacro` / `frameBasedRotation` 未复刻，实现是逐 tick 的。
- 只对应 **1.26.51.01**，游戏升级后需要重新构建。

## 测试

```bash
py -3.11 -m unittest discover -s tests -v   # 不需要游戏
py -3.11 driver/selftest.py                 # 需要游戏已进入存档
```

## License

LGPL-3.0. See [LICENSE](LICENSE).
