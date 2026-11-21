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

# 按需重建、重启并进世界；--no-build 可跳过构建
py -3.11 driver/dev.py run --world "超平坦测试"

# 附着到用户桌面已运行的游戏实例（不强杀重启、不重装 mod，避开隔离桌面 exebox 不可见与失焦暂停）
py -3.11 driver/dev.py run --attach --world "超平坦测试"
# 就绪后执行命令；加 --stop-after 在完成后按保存门禁退出游戏
py -3.11 driver/dev.py run --attach --exec "py -3.11 driver/selftest.py --connection-only" --stop-after

# 手动启动时也可以让 mod 自己进世界：写 mods/probe/harness.json
py -3.11 driver/dev.py run --write-config --world "超平坦测试"

# 卡在断开错误页时（HBUI 且 worlds.ready=false 只是候选，不能据此认定保存已完成）：
# 先落盘现场到 out/disconnect-<时间>.json，再受控重启回菜单或重进世界
py -3.11 driver/dev.py recover --world "超平坦测试"
# 保存门禁不能确认时默认保留进程；明确放弃未保存更改才添加 --force

# 常用操作（主入口 `driver.harness.cli`；兼容旧脚本 `driver/transport_mod.py` 仍可用）
py -3.11 -m driver.harness.cli ping
py -3.11 -m driver.harness.cli state
py -3.11 -m driver.harness.cli chat-history --limit 32
py -3.11 -m driver.harness.cli worlds
py -3.11 -m driver.harness.cli enter-world --name "超平坦测试" --wait
py -3.11 -m driver.harness.cli leave-world
py -3.11 -m driver.harness.cli options-set --set gfx_gamma=0.8
py -3.11 -m driver.harness.cli set --forward 1 --ticks 40
py -3.11 -m driver.harness.cli look --yaw 90 --pitch 0 --absolute
py -3.11 -m driver.harness.cli teleport 0.5 -49 0.5 --yaw 0 --pitch 0
py -3.11 -m driver.harness.cli command "fill -4 -60 -4 4 -60 4 stone"
py -3.11 -m driver.harness.cli slot 3
py -3.11 -m driver.harness.cli click left
py -3.11 -m driver.harness.cli macro load --name default
py -3.11 -m driver.harness.cli macro run

# 跑酷：录制一次 macro 跑法 → 跳跃分析 → 对比 → 重复尝试
py -3.11 driver/parkour.py record --macro p0_forward --max-ticks 80 --out out/run.jsonl
py -3.11 driver/parkour.py analyze out/run.jsonl --checkpoint 0 -6
py -3.11 driver/parkour.py compare out/run1.jsonl out/run2.jsonl
py -3.11 driver/parkour.py attempts --macro p0_forward \
    --start 0 -49 0 --target 0 4 --attempts 10

# 场景 DSL
py -3.11 driver/run_mod.py --scenario scenarios/setup-platform.json
py -3.11 driver/run_mod.py --scenario scenarios/example.json

# 离线 PE 静态分析（不启动游戏或 IDA；示例见 docs/reverse-engineering.md）
py -3.11 driver/pe_map.py --xrefs <va>
py -3.11 driver/pe_map.py --iat [filter]
py -3.11 driver/pe_map.py --import-xrefs <sym>
py -3.11 driver/pe_map.py --disasm <va> [-n N]
py -3.11 driver/pe_map.py --rtti <va_or_name>
```

## 项目结构

| 路径 | 内容 |
| --- | --- |
| `mod/probe/` | LeviLamina 客户端 mod（C++），命名管道 `\\.\pipe\mcbe-harness` |
| `driver/harness/` | Python 客户端包：`connection`、`commands/`、`facade`、`client`、`cli`、`lab` |
| `driver/transport_mod.py` | 向后兼容入口（保留旧模块导入与 CLI 调用） |
| `driver/dev.py`, `driver/check.py` | 游戏受控运行/附着/恢复与统一离线校验 |
| `driver/pe_map.py`, `driver/ida.py`, `driver/landing.py` | 离线 PE 分析、IDA MCP 客户端与逆向账本 |
| `driver/mod_agent.py`, `driver/run_mod.py` | 场景 DSL 适配器与执行入口 |
| `driver/macro.py` | dmf-mpk macro 文件工具 |
| `driver/selftest.py`, `driver/selftest_*.py` | live 自检入口、默认套件、共享夹具与领域检查 |
| `scenarios/` | 场景 JSON |
| `reverse/` | dmf-mpk 逆向分析与 IDA 导出脚本 |
| `docs/` | 现行指南、机制 wiki 与逆向证据 |
| `skills/` | 逆向实验（`bedrock-re`）与文档规范（`project-docs`）；`.zcode/skills/` 为本地目录联接 |
| `tests/` | 不依赖游戏的 Python 与 native 模型单元测试 |

## 文档

- Mod 构建、API、协议与使用限制：[`mod/README.md`](mod/README.md)
- 架构与扩展指南：[`docs/architecture.md`](docs/architecture.md)
- 子系统实现与诊断语义：[`docs/mod-internals.md`](docs/mod-internals.md)
- 当前验收与未覆盖范围：[`docs/reliability-repairs.md`](docs/reliability-repairs.md)
- 跑酷、场景 DSL：[`docs/parkour.md`](docs/parkour.md)、[`docs/scenario-dsl.md`](docs/scenario-dsl.md)
- 机制研究（流体、鞘翅、角度/正弦表）：[`docs/fluid-movement.md`](docs/fluid-movement.md)、[`docs/elytra-movement.md`](docs/elytra-movement.md)、[`docs/angles-wiki.md`](docs/angles-wiki.md)
- 逆向方法与证据：[`docs/reverse-engineering.md`](docs/reverse-engineering.md)、[`docs/notes/`](docs/notes/) 与 [`reverse/dmf-mpk-analysis.md`](reverse/dmf-mpk-analysis.md)
- 逆向实验与文档规范：[`skills/bedrock-re/`](skills/bedrock-re/)、[`skills/project-docs/`](skills/project-docs/)
- UI 与 IDA 排障：[`docs/ui-automation.md`](docs/ui-automation.md)、[`docs/ida-mcp-troubleshooting.md`](docs/ida-mcp-troubleshooting.md)
- Agent 常驻约定：[`AGENTS.md`](AGENTS.md)
- 完整索引：[`docs/README.md`](docs/README.md)

## 状态与限制

- **已实测能力**（1.26.51.01 创作/测试存档）：状态读取、Hardware HID 移动/视角/跳跃/游泳/双击空格飞行（含游戏内 `/togglefly`）、左右键、快捷栏、服务端传送、命令执行、macro 播放/录制、场景 DSL，以及 `navigate` / `mine` / `place` / `use` 闭环任务（完整基线见 [`docs/reliability-repairs.md`](docs/reliability-repairs.md)）。
- **核心使用边界**：
  - 闭环任务在客户端 tick 内推进，同一时间只运行一个任务；注入步行峰值 **0.2159 格/tick**（原版 4.317 m/s），实验运行可锁定真实键鼠（`dev.py --lock-input` / `input-lock`）。
  - `command` 的方块操作是 deferred，`success=false` 不代表失败，最终效果以 `blocks`/`verify` 回读为准；`interact_entity` 仅转向并注入使用按钮（best effort）。
  - Python 客户端每次请求均有超时，断线抛 `ModDisconnected`，可在重启后通过 `reconnect()` / `wait_ready()` 恢复。
  - `smoothMacro` 与 `frameBasedRotation` 尚未复刻（当前均为逐 tick 实现）；版本数据与偏移绑定 **1.26.51.01**，升级版本见 [`docs/porting.md`](docs/porting.md)。
  - 更多限制与字段细节见 [`mod/README.md`](mod/README.md) 与 [`skills/bedrock-re/references/experiments.md`](skills/bedrock-re/references/experiments.md)。

## 测试

```bash
py -3.11 driver/check.py                    # 统一离线检查（ruff/mypy/lint/守卫/gen_profiles/文档链接/diff/unittest）
py -3.11 driver/check.py --docs-only        # 仅跑静态、守卫、文档相对链接与 diff 检查（--fast 同样跳过 unittest）
py -3.11 -m unittest discover -s tests -v   # 不需要游戏
py -3.11 driver/selftest.py                 # 需要游戏已进入存档
```

GitHub Actions 的 `tests.yml` 运行 Python/native 检查，Windows `mod-build` 也构建完整客户端
mod。CI 不启动 Minecraft；现场验收仍需本机游戏，工具链见
[`docs/mod-build-environment.md`](docs/mod-build-environment.md)。

## License

LGPL-3.0. See [LICENSE](LICENSE).
