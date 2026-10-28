# LeviPilot

Minecraft Bedrock **1.26.51.01** 的客户端实验 harness：一个 LeviLamina mod 加一条命名管道 API，
让 Python 脚本或 LLM 直接读取游戏状态、控制玩家、跑逐 tick 实验。

## 能做什么

- **读**：玩家位置/速度/朝向/生命/饥饿/经验/手持物/输入状态、完整背包/盔甲/副手、附近实体、任意方块、客户端保留的聊天文字
- **控制**：移动、疾跑、潜行、跳跃、飞行、绝对/相对视角、左右键、快捷栏、服务端权威传送；
  按 `runtime_id` 查询/转向/攻击/交互实体。双击空格飞行用游戏内 `/togglefly` 开关
  （任意非旁观模式，管道侧同名命令）
- **执行**：任意服务端命令（`fill`/`tp`/`setblock`…），保留 `@s`/`~` 语义
- **Macro**：dmf-mpk 兼容格式的加载、播放、录制，含真实鼠标左右键
- **实验**：逐 tick 采样、场景 DSL（setup → steps → verify/teardown → JSONL + summary）、
  `driver/selftest.py` live 自检

## 与 dmf-mpk 的分工

dmf-mpk 是闭源 mod；逆向得到的是它的**行为、macro 格式和输入 ID**，代码本身不能照抄。
两者定位不同，可以共存：

- **dmf-mpk**：HUD、跑酷统计、practice、Input Checker、macro 编辑器/按键绑定等界面与玩法。
- **LeviPilot**：给脚本/LLM 的 API——状态、背包、实体、方块、移动/视角/点击/传送、
  服务端命令、macro 播放/录制、场景 DSL。

HUD / 跑酷 / 练习 / Input Checker **不在本项目的复刻范围内**，直接用 dmf-mpk 即可。

## 快速开始

前置：Windows、Minecraft Bedrock 1.26.51.01（LeviLamina）、Python 3.11。
构建 mod 还需要 xmake 与 LLVM 22，见 [`mod/README.md`](mod/README.md)。

游戏可以直接运行实例目录下的 `Minecraft.Windows.exe`，不必经过 LeviLauncher 的 UI。

```bash
# 开发循环：编译、重启 exe、等主菜单、直接进世界，全程不用点 UI
py -3.11 driver/dev.py run --world "超平坦测试" --build

# 只重启并进世界（不编译）
py -3.11 driver/dev.py run --world "超平坦测试"

# 手动启动时也可以让 mod 自己进世界：写 mods/probe/harness.json
py -3.11 driver/dev.py run --write-config --world "超平坦测试"

# 常用操作
py -3.11 driver/transport_mod.py ping
py -3.11 driver/transport_mod.py state
py -3.11 driver/transport_mod.py chat-history --limit 32
py -3.11 driver/transport_mod.py worlds
py -3.11 driver/transport_mod.py enter-world --name "超平坦测试" --wait
py -3.11 driver/transport_mod.py leave-world
py -3.11 driver/transport_mod.py options-set --set gfx_gamma=0.8
py -3.11 driver/transport_mod.py set --forward 1 --ticks 40
py -3.11 driver/transport_mod.py look --yaw 90 --pitch 0 --absolute
py -3.11 driver/transport_mod.py teleport 0.5 -49 0.5 --yaw 0 --pitch 0
py -3.11 driver/transport_mod.py command "fill -4 -60 -4 4 -60 4 stone"
py -3.11 driver/transport_mod.py slot 3
py -3.11 driver/transport_mod.py click left
py -3.11 driver/transport_mod.py macro load --name default
py -3.11 driver/transport_mod.py macro run

# 跑酷：录制一次 macro 跑法 → 跳跃分析 → 对比 → 重复尝试
py -3.11 driver/parkour.py record --macro p0_forward --max-ticks 80 --out out/run.jsonl
py -3.11 driver/parkour.py analyze out/run.jsonl --checkpoint 0 -6
py -3.11 driver/parkour.py compare out/run1.jsonl out/run2.jsonl
py -3.11 driver/parkour.py attempts --macro p0_forward \
    --start 0 -49 0 --target 0 4 --attempts 10

# 场景 DSL
py -3.11 driver/run_mod.py --scenario scenarios/setup-platform.json
py -3.11 driver/run_mod.py --scenario scenarios/example.json
```

## 项目结构

| 路径 | 内容 |
| --- | --- |
| `mod/probe/` | LeviLamina 客户端 mod（C++），命名管道 `\\.\pipe\mcbe-harness` |
| `driver/harness/` | Python 客户端包：连接 `connection`、按领域 mixin 组成的 `commands/`、脚本门面 `facade`、兼容导出 `client`、实验辅助 `lab`（回读验证建场 / 录制会话 / 逐 tick 拟合） |
| `driver/transport_mod.py` | 兼容入口（旧导入与 `python driver/transport_mod.py ...` 仍可用） |
| `driver/mod_agent.py`, `driver/run_mod.py` | 场景 DSL 适配器与执行入口 |
| `driver/macro.py` | dmf-mpk macro 文件工具 |
| `scenarios/` | 场景 JSON |
| `reverse/` | dmf-mpk 逆向报告与 IDA 导出脚本 |
| `docs/` | 补充文档 |
| `skills/` | 单一逆向 + 实验 skill（`bedrock-re`，ZCode 本地通过 `.zcode/skills/` 联接发现） |
| `tests/` | 不依赖游戏的单元测试 |

## 文档

- Mod 构建、API、协议与使用限制：[`mod/README.md`](mod/README.md)；实现细节与逐项实测数字：
  [`docs/mod-internals.md`](docs/mod-internals.md)（含「实测数字速查」）
- 架构与扩展指南：[`docs/architecture.md`](docs/architecture.md)
- 跑酷记录、跳跃分析与重复尝试：[`docs/parkour.md`](docs/parkour.md)
- dmf-mpk 逆向报告：[`reverse/dmf-mpk-analysis.md`](reverse/dmf-mpk-analysis.md)
- 场景 DSL 与输出格式：[`docs/scenario-dsl.md`](docs/scenario-dsl.md)
- 流体运动机制（水/岩浆，1.26.51.01 实测复核）：[`docs/fluid-movement.md`](docs/fluid-movement.md)；
  面向 wiki 的精简版：[`docs/fluid-wiki.md`](docs/fluid-wiki.md)
- 单一逆向 + 实验 skill（静态优先、版本角色、逐轮落盘、建场/录制/输入坑）：[`skills/bedrock-re/`](skills/bedrock-re/)
- UI 自动化（启动 / 进存档 / 退档）：[`docs/ui-automation.md`](docs/ui-automation.md)
- 旧 frida 路线（1.26.45.01，历史资料；代码在 `legacy/frida` 分支）：[`docs/legacy-frida-1.26.45.md`](docs/legacy-frida-1.26.45.md)
- IDA MCP 排障：[`docs/ida-mcp-troubleshooting.md`](docs/ida-mcp-troubleshooting.md)
- Agent 常驻约定（硬约束 / 已知坑 / 当前优先级）：[`AGENTS.md`](AGENTS.md)
- 文档索引：[`docs/README.md`](docs/README.md)

## 状态与限制

- 已在 1.26.51.01 创作存档内实测：状态读取、移动/视角/跳跃/飞行、左右键、快捷栏、
  服务端传送、服务端命令、macro 播放/录制、真实鼠标按键、场景 DSL，以及
  `navigate` / `mine` / `place` / `use` 四个 mod 内闭环任务。
- 脚本运行可锁真实键鼠（`dev.py --lock-input` / `input-lock`），游泳/跳跃/双击空格飞行等状态用
  热键驱动（`swim --drive` / `jump-key` / `lab.double_space_fly`）；`/togglefly` 是游戏内命令，
  玩家自己在聊天框输入就能开关双击空格飞行。细节见 `mod/README.md` 与实验手册 skill。
- 步行注入峰值 **0.2159 格/tick**（原版 4.317 m/s）。
- 闭环任务在客户端 tick 内推进，不依赖 Python 逐 tick 下发；同一时间只跑一个任务。
- `command` 的方块类命令是 deferred，`success=false` 不代表失败，效果以 `blocks`/verify 为准。
- `interact_entity` 是转向 + 注入使用按钮（best effort）；直接调用游戏的实体交互函数会崩溃。
- Python 客户端每次请求都有超时，断线抛 `ModDisconnected`，可用 `reconnect()` /
  `wait_ready()` 在游戏重启后恢复。
- `smoothMacro` / `frameBasedRotation` 未复刻，实现是逐 tick 的。
- 只对应 **1.26.51.01**，游戏升级后需要重新构建。

## 测试

```bash
py -3.11 -m unittest discover -s tests -v   # 不需要游戏
py -3.11 driver/selftest.py                 # 需要游戏已进入存档
```

GitHub Actions 运行 `tests.yml`（Python 单元测试及独立 C++ 任务模型）。mod 构建依赖本机的 LLVM22/xmake 补丁，
是本地步骤，见 [`docs/mod-build-environment.md`](docs/mod-build-environment.md)。

## License

LGPL-3.0. See [LICENSE](LICENSE).
