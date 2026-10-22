# AGENTS.md

给在本仓库工作的 agent 的常驻约定。人类开发者请先读 [`README.md`](README.md) 与
[`docs/architecture.md`](docs/architecture.md)；本文件只写「必须遵守」与「容易踩」。

## 项目是什么

**LeviPilot**：Minecraft Bedrock **1.26.51.01**（LeviLamina 26.51.7）的客户端实验 harness。
一个 C++ mod（`mod/probe/`）+ 命名管道 `\\.\pipe\mcbe-harness` 的 UTF-8 JSONL 协议 +
Python 客户端（`driver/harness/`）。目标是让脚本/LLM 读状态、控玩家、跑逐 tick 实验。

`legacy/frida` 分支是旧的 frida 路线（1.26.45.01），**主分支只有文档** `docs/legacy-frida-1.26.45.md`；
不要把它当现行实现，但它的「运行时结论」和「采样口径」仍然是有效知识。

## 常用命令

```bash
# C++ mod（本机 xmake + LLVM22，见 docs/mod-build-environment.md）
cd mod/probe && xmake -j4 -y   # -j4 限制 CPU 占用

# 开发循环：编译 → 重启 exe → 等主菜单 → 直接进世界（不用点 UI）
py -3.11 driver/dev.py run --world "超平坦测试" --build

# 无游戏单测 / 静态检查
py -3.11 -m unittest discover -s tests -v
py -3.11 -m ruff check driver tests && py -3.11 -m mypy

# 需要游戏已进存档
py -3.11 driver/selftest.py
```

`py -3.11` 是硬要求（frida 时代的教训：默认解释器可能没有依赖）。

## 目录地图

| 路径 | 内容 |
| --- | --- |
| `mod/probe/src/mod/` | mod 全部实现：`ProbeMod`（生命周期/tick）、`Commands`（管道分发/SEH 隔离）、`Transport`（管道/spool）、`Memory`（内存原语）、`Watch`（DR 观察点/崩溃取证）、`RecordRing`（64 B tick 环）、`HookVa`（运行时 detour）、`Ecs`（组件内省）、`Resolve`（版本/签名）、`SignIn`（主菜单登录态）、`Checkpoints`（存档点）、`Input`/`Tasks`/`State`/`Inventory`/`Entities`/`Server`/`World`/`Options`/`Macro`、`Internal`（唯一共享头） |
| `driver/harness/` | Python 客户端：`protocol`/`client`/`errors`/`cli`，以及 `memory`/`spool`/`watch`/`record`/`hooks` 辅助模块 |
| `driver/dev.py` | 启动 + 自动进世界的开发循环 |
| `driver/parkour.py` | tick 录制、跳跃分段、两次运行对比、attempts |
| `scenarios/` | 场景 DSL 的 JSON |
| `docs/` | 架构、场景 DSL、跑酷、UI 自动化、mod 构建、旧 frida 资料、`offsets/` 版本表 |
| `reverse/` | dmf-mpk 逆向报告与 IDA 导出脚本 |

## 硬约束（改代码前先读这段）

1. **`Internal.h` 是唯一被所有 TU 引用的内部头**；新共享工具放 `probe::detail`。
2. **管道线程绝不直接碰游戏对象**：只设请求标志，或走 `queueServerTask()`。
3. **需要逐 tick 反馈的逻辑留在游戏线程**（`safeTickPre` / `safeTickPost`），
   不要让 Python 进入反馈环。
4. **热路径纪律**：tick 内、以及任何可能被高频调用的钩子体里，禁止
   `std::string` / `nlohmann::json` / 动态分配 / 新增 mutex。只允许写预分配的定长结构，
   格式化放管道线程或 Python 侧。
5. **错误不穿透游戏 tick**：一切异常变成事件（`internal_error` 等）。注意 MSVC 默认
   `/EHsc` 下访问违例**不是**可捕获异常 —— 新写的 handler 用 `__try/__except` 包住。
6. **版本**：只支持 1.26.51.01。禁止把 1.26.45.01 的硬编码 VA 写进 `.cpp`（注释里也不要当常量）；
   锚点走符号或签名解析（见 `docs/mod-internals.md` 的版本指纹一节）。
7. **协议只做增量变更**：`{"event":"hello","protocol":4,"capabilities":[...]}` 是握手；
   升号时旧字段必须继续可用，未知命令仍回 `error/unknown_cmd`。
8. **强能力默认关闭**：任意内存写、任意地址装钩这类能力必须显式开启并留审计日志。

## 加东西的标准步骤

**加一个管道命令**：`Commands.cpp` 的 `dispatchLine` 加分支 → `ProbeMod.h` 加请求字段/方法 →
对应模块实现 → `driver/harness/client.py` 加 `request(..., expected_event)` →
`cli.py` 加子命令 → `tests/` 加无游戏单测（需要游戏的检查加到 `driver/selftest.py`）。

**加一个闭环任务**：`ProbeMod.h` 定义 `XxxTask` 与 `std::optional<XxxTask>` → `Commands.cpp` 解析参数、
`beginTask()`、回 `xxx_started` → `Tasks.cpp` 实现 `tickXxx` 并在 `tickTask()` 分派、结束时发 `xxx_done` →
client/`Harness`/CLI 加阻塞封装 → 用 `blocks` / `state` **回读验证**，不要只看返回值。

## 已知坑（别重复踩）

- Windows 同步句柄上挂起的 `ReadFile` 会阻塞同一句柄的 `WriteFile` ⇒ 客户端必须用轮询读线程
  （`PeekNamedPipe`），不能用阻塞读。
- `command` 的方块类命令是 deferred，`success=false` 不代表失败，以 `blocks` / verify 为准。
- `GameMode::interact` 在空物品上下文会崩 ⇒ `interact_entity` 只做转向 + 注入使用按钮（best effort）。
- 直接写客户端位置（`StateVector`/`ActorSetPositionRequest`）会被服务端覆盖；位置是服务端权威。
- 朝向必须走命令通道（`/tp @s ~ ~ ~ yaw pitch`）；只改组件会橡皮筋（客户端相机每帧写回）。
- 移动输入要同时写客户端与服务端两份，否则预测/校正互相拉扯。
- 跳跃不能只靠输入标志/队列/handler：1.26.51.01 上前 3 tick 位置不动、第 4 tick 一次性补
  V1+V2+V3（约 0.99 格）。起跳沿必须调 `LocalPlayer::jumpFromGround()`；
  `MobJump::setJumpPendingScale` 单独用不解决。
- 版本锚点的导入符号是 `jmp [rip+0]` thunk，且指针随 ASLR 变化；签名必须先跟 thunk 到真实
  函数，绝不能把 thunk 里的绝对地址当签名。
- 运行时 hook 的通用 VA detour 只保留 rcx/rdx/r8/r9；float/xmm 参数、向量返回、超过 4 个
  参数或隐藏返回指针的目标必须写 typed detour，否则 origin 会收到错误参数。
  VA 只校验“可执行页”，指令边界由 IDA 侧确认。
- `StateVector.vel` 是摩擦前速度，约为真实逐 tick 位移的 0.546 倍；判速度用相邻 tick 位置差。
- `pos.y` 是**脚部**；StateVector 里是眼睛（脚部 + 1.6200101）。
- 失焦是否暂停世界由 `options.txt` 的 `pause_menu_on_focus_lost` 决定（旧 frida 路线实测置 0 后
  后台仍满速 tick）；raw input 在后台仍可能转视角，由 `_applyTurnDelta` 钩子 + `background_guard` 拦掉。
- `harness.json` 的 `auto_enter` 要求游戏在**前台**（后台进世界会触发鼠标捕获）。
- 主菜单/世界列表/暂停菜单/对话框仍需桌面 UI 操作（见 `docs/ui-automation.md`），
  但日常开发循环不需要。
- tick 记录会同时写入 `<modDir>/spool-<pid>.jsonl`（100 ms / 64 KB 刷盘）；连接时不再清空队列，
  而是先刷 spool 再补发。崩溃取证走 VEH 写 `crash-<pid>-<tick>.dmp` + `crash-<pid>.txt`。
- 请求级 AV 由 `dispatchWithSeh` 转成 `internal_error`；不要把会崩的代码放到 tick 线程，
  那里没有同样的保护。
- 键位事件优先走 `HIDController::onKeyDown(code, InputOrigin::Virtual|Hardware)`（`ll::service::getAppPlatform()
  → getPlatformShim() → Platform_GameCore::mHIDController`）；它才会触发 LL `KeyInputEvent` 和绑定。
  `Keyboard::_inputs().push_back(KeyboardAction)` 也能改槽位/开背包，但不发 LL 事件，只作对照路径。
- 暂停菜单不响应注入的按键（Esc/Enter 都无效），退出暂停仍需桌面点击 Resume；
  背包/聊天（`/`）可以开，背包用 Esc 回到 hud。
- `mouse_event action=move dx=...` 能转向；左/右键只到 `MouseInputEvent` 层并增加计数，
  在 1.26.51.01 上没有触发世界攻击（攻击绑定不在这一层），不要用它代替 `click`。
- 真实鼠标会转动相机并让宏回放落点漂移；`background_guard` 只拦后台转向，selftest 的宏
  确定性检查在检测到相机被外部移动时会标记 skipped。
- 玩家 StateVector 在集成服务端线程上写；观察用 `watch_set(thread="both")`。
  `thread="all"`（100+ 线程）实测会把游戏搞崩，不要再用。
- 按 runtime id 查 ECS 得到的是服务端 Actor；客户端 context 用 `state.context`，
  再走 `ecs_context`。不要在 tick 线程枚举客户端 registry。
- `xmake` 默认吃满所有核心，构建用 `xmake -j4`；只改 `.cpp` 是增量，改 `Internal.h`/`ProbeMod.h`
  会全量重编（约 2–3 分钟）。

## 当前优先级

当前完成度见下面的清单；任务提示词不在仓库内（本地维护）。

- **P0（已完成）**：T1 崩溃取证 + 数据落盘 → T2 内存原语（mem_read/mem_write/probe）→ T3 硬件观察点 watch
- **P1（已完成）**：T4 二进制记录环 → T5 运行时装钩（符号 typed detour + VA dry_run）→ T6 ECS/registry 内省；selftest 115/115
- **跑酷 P1/P2（已完成）**：jump_when / route / 对齐公式的起跳 / 区域扫描 / 存档点 / 效果与落地事件；selftest 115/115
- **P2（已完成）**：T7 版本指纹 + 签名/偏移解析；T8 unhook/panic
- **P3（已完成）**：T9 tick 开销自测 + 请求块 seqlock（input/look/teleport 合成一块，tick 读路径无锁）
- **修复轮 P1（已完成）**：F1 命令缓冲上界 + 超长行重同步 / F2 hook_va 安装窗口与挂起死锁 /
  F3 hook_va 槽位生命周期 / F4 world_left + panic 落盘；selftest 115/115
- **修复轮 P2（已完成）**：F5 offsets 表接线（structs/components、null 删除、no_offset）/
  F6 spool 顺序与轮转 / F7 watch 细节（目标槽 foreign、`code_hex`、`allow_watch`）/
  F8 记录环零分配方块采样 + 高度越界 / F9 开销语义（`tick_interval_*`、epoch 守卫）/
  F10 防御小项；selftest 115/115
- **H1-B 字节（已完成）**：导入桩跟随（E9/FF25）、计数决策表（peer/receive=0）、
  B3 白名单序列化 + `packet_bytes`；selftest 115/115
- **H1 阶段 A（已完成）**：`packet_capture/drain/stats` 身份 tap（id/tick/seq/tid、denylist、
  `allow_packet_capture`）；`LocalNetworkPeer` 符号不可用，字节层待 B2/B3；selftest 115/115
- **封包前验收 H0（已完成）**：StateVector.vy watch（写者=集成服务端线程）、
  probe×blocks 方块 id、hook context×ECS 交叉、spool panic/taskkill 取证；selftest 115/115
- **输入事件层（已完成）**：G1 `key_event` / G2 `mouse_event` / G3 sprint 三组对照 /
  G4 macro 事件级回放 / G6 世界内 UI；G5 未做（G3 结论：C≈B≈0.2805 格/tick，
  差异不在输入事件层，不需要 raw-input hook）


> 任务提示词不随仓库分发；本轮验收标准与结论已经写进 `mod/README.md` 的「实测 / 限制」
> 和 `docs/mod-internals.md`，以后看这两处即可。

改完任何一项，都要在 `driver/selftest.py` 留一条可复现的检查，并在 `mod/README.md` 的
「实测 / 限制」里补一行 —— 这个仓库的文档是活的，别让它和代码脱节。
