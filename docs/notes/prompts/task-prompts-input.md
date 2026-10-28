# 开发任务提示词 · 第三轮（输入事件层 G1–G6）

> 独立成册。**公共前置见 [`task-prompts.md`](task-prompts.md) §2**，派活时一并粘上。
> 前两轮解决的是「动作级」注入（MoveInput / `_applyTurnDelta` / `GameMode`），
> 本轮补「事件级」：让游戏自己的绑定、边沿、长按、双击、输入缓冲跑起来。
> 强能力（`allow_input_inject` / `allow_raw_input_hook`）默认关闭。

> 前两轮解决的是**动作级**注入：`MoveInputComponent`（位移）、`_applyTurnDelta`（视角，已在
> `ProbeMod.cpp:745` 走真实入口）、`GameMode::attack/useItemOn`（左右键）。本轮补**事件级**：
> 让游戏自己的绑定、边沿、长按、双击、输入缓冲逻辑跑起来。这是「sprint 1.3× 定位」与
> 「macro 保真录制」的前置。
> 判据沿用公共前置；强能力一律默认关闭、显式开启、留审计日志。

## 第三轮总览

| ID | 优先级 | 任务 | 依赖 |
| --- | --- | --- | --- |
| G1 | P1 | 键位事件注入 `key_event` | F5（offsets 表） |
| G2 | P1 | 鼠标事件注入 `mouse_event` | G1 的注入框架 |
| G3 | P1 | sprint 三组对照实验 | G1/G2 + record |
| G4 | P2 | macro 走事件级录制/回放 | G1/G2 |
| G5 | P3（条件） | 原始输入 hook 兜底 | G3 的结论 |
| G6 | P3（可选） | 世界内 UI 交互（背包/聊天） | G1 |

> 先把 G1/G2 做完再动 G3 —— 否则你只能证明"注入标志位不行"，证明不了"输入层是不是原因"。

---

## G1（P1）键位事件注入 `key_event`

```text
目标：让任意按键走游戏自己的输入事件层（绑定/边沿/长按/双击都能被触发），
而不是只改 MoveInput 的标志位。

背景：现在 `set` 只能给 forward/strafe/sneak/sprint/jump 这些布尔；
`_applyTurnDelta` 解决了视角；但没有任何"按键事件"。于是依赖输入事件的机制
（按键绑定、双击疾跑、输入缓冲、UI 开关）在实验里全都做不了。
注意：不要用 SendInput/keybd_event —— 旧路线实测鼠标点击被游戏丢弃、视角不转，
且强依赖前台与焦点。

范围：新增 InputEvents.cpp/.hpp；Commands.cpp 注册命令；client.py/cli.py 封装；
harness.json 增加 allow_input_inject（默认 false）；tests/ 与 selftest 各加检查。

实现要点：
1) 先用 IDA 定位键盘输入事件类型与它的发布/处理函数：从 InputHandler/ClientInputHandler
   的虚表、RTTI 名、或 MouseInputEvent 的同族入手（ProbeMod.cpp:28 已证明 EventBus 可以
   listener，publisher 就在同一族里）。锚点走 F5 的 offsets 表，不要硬编码 VA。
2) 优先"调用 publisher / 事件分发函数"并传入构造好的事件对象；不要伪造 OS 消息、
   不要伪造 RAWINPUT（那是 G5 的兜底，不是这里）。
3) 事件对象按 IDA 布局构造：键码、按下/抬起、是否重复、设备来源、时间戳。
   所有偏移来自 offsets 表；缺表项就回 no_offset，不要猜。
4) 注入必须可识别：维护 mInjectingInput 标记（或设置事件的来源位），
   让 mod 自己的 listener 能区分注入与真实输入，避免自激与重复计数。
5) 门：仅 in_world；后台/前台守卫保持现状；allow_input_inject=false 时直接拒。
6) 命令：key_event {code, down, repeat_ms?}、key_state {code}（读回游戏侧按键状态，用来自证）。

不要做：不要覆盖真实输入（真实按键优先，注入只做增量）；不要在加载/菜单期注入；
不要顺手把 G5 的 raw-input hook 一起做了。

验收：
- 注入 E（开背包）→ 游戏 UI 真的打开（用游戏状态或一次截图确认，别只看返回值）；
- 注入 1..9 → slot/手持物随之变化，与 slot 命令的结果一致；
- 挑一个无副作用的绑定键注入 → 对应游戏行为出现；
- 同一按键连续注入 100 次 vs 真实按 100 次 → 事件计数一致（±0），无重复；
- allow_input_inject=false → 被拒且不产生任何事件。
```

---

## G2（P1）鼠标事件注入 `mouse_event`

```text
目标：鼠标按键与相对位移进入 MouseInputEvent 同一条路，让游戏的输入统计/绑定也看到。

背景：现在 MouseInputEvent 只有 listener（ProbeMod.cpp:28），`mouse` 命令是**只读计数器**
（count/last_action/last_data/left/right/counts）；`click` 直接调 GameMode，绕过了输入层。
视角转动已经走 `_applyTurnDelta`（真实入口），不要在这里重复实现。

范围：InputEvents.cpp 扩展；Commands.cpp 加 mouse_event；client/cli；沿用 G1 的配置门。

实现要点：
- 找到 MouseInputEvent 的 publisher（与 G1 同一族），构造 {action, button, dx, dy} 注入；
  action 取值与枚举要与 mMouseActionCounts 的下标一致（先读现有 listener 的取值域）。
- 注入时置 mInjectingInput（或复用 mInjectingLook 的语义扩展），让背景守卫与 mod 自己的
  统计都能区分。
- mouse_event {action, button?, dx?, dy?}；dx/dy 只用于走"原始位移"的路径，
  相机转向仍建议用现有 look/_applyTurnDelta，事件里注明这点。
- 返回里带上注入前后 mMouseEventCount，便于调用方立即自证。

不要做：不要用 GameMode 调用来"假装"鼠标事件（会绕过绑定与统计）；不要改真实输入的优先级。

验收：
- mouse_event left down + up → mouse.count +2、last_action 正确，且世界里产生一次攻击
  （与 click 对比：动作一致但 count 也涨了）；
- 注入 dx/dy 后 actor yaw 变化与 look 的结果一致（同一输入量对比）；
- 后台守卫不会把注入的转向丢掉；
- 每 10 次注入后 mouse.count 恰好 +10（无丢无重）。
```

---

## G3（P1）sprint 三组对照：把 1.3× 的差异定位到层

```text
目标：用三组输入方式跑同一段直线，判定疾跑 1.3× 的差异在「输入事件层」还是「服务端/请求层」。

背景：旧 frida 路线遗留未解项——真按 ctrl+w 得 0.2805 格/tick（×1.3），
只注入 sprint 标志位仍是走路速度 0.2158。已排除 ADF 各位、MoveInput 偏移、
PlayerInputRequest 前 2KB 与全部 ECS 组件。现在有 G1/G2 与 record，可以做成对照实验。

范围：scenarios/ 新增 sprint-compare.json；driver 侧一条对比脚本；
不改 mod 的既有行为（只增加记录字段时走已有 record）。

实验设计（三组，各跑 60 tick 直线，重复 3 次）：
  A 真人按 ctrl+w（一次性人工前置）；
  B `set --forward 1 --sprint 1`；
  C G1 注入 ctrl 按下 + forward（走事件层）。
记录：record_start 每 tick 的 pos/vel/ADF/input flags/手持；速度一律用相邻 tick 位置差
（不要用 StateVector.vel，它是摩擦前的，约 0.546 倍）。

判据：
  - 期望基线：走路 0.2158、疾跑 0.2805 格/tick；
  - 若 C ≈ A 且 B < A ⇒ 差异在**输入事件层**（那么 G1 就是解，且能直接用于实验）；
  - 若 C ≈ B < A ⇒ 差异在**服务端/输入请求层** ⇒ 下一步做封包 tap（PlayerInputRequest /
    PlayerActionPacket），而不是继续在组件里试位。

不要做：不要在记录里混入 tp/teardown 的位移（沿用旧口径）；不要用 vel 判速度。

验收：
- 输出一张三组表（稳态均值、标准差、内存位差、是否一致）；
- 同一组复跑 3 次，稳态值波动 < 1%；
- 给出明确结论句 + 下一步指向（G1 收工 或 packet tap）。
```

---

## G4（P2）macro 走事件级录制/回放

```text
目标：修掉「鼠标录制只认 raw input」，让 macro 的按键/鼠标事件可被注入式回放。

背景：现状是录制靠 latch PlayerActionComponent、回放直接调 GameMode；
mod/README 的限制里明确写了「鼠标录制只认 raw input；后台合成点击只有移动事件」。

范围：Macro.cpp 的录制与回放路径；不改 macro 文件格式（dmf-mpk 兼容要保持）。

实现要点：
- 录制：保留现有真实来源字段，同时记录"事件序号 + 键码/按钮 + 按下/抬起 + tick"，
  以便回放走事件层；
- 回放：通过 G1/G2 的注入通道重放，并置 mInjectingInput；
- 旧 macro 文件回放仍走旧的日 GameMode 路径（向后兼容），由文件里的 version/flags 判定；
- 回放结束清空 state 与 flag，避免残留（沿用现有 stop_input 语义）。

验收：
- 录一段含左右键 + 移动 + 视角的 macro，回放 3 次落点误差 < 1e-4 格（沿用现有 reset 口径）；
- 鼠标事件计数：录制 N 次 → 回放后计数恰为 N；
- 旧格式 macro 仍能播放；
- 回放期间不触发背景守卫丢事件。
```

---

## G5（P3，条件项）原始输入 hook 兜底

```text
启动条件（必须同时满足，否则不做）：
  1) G3 的结论是「差异在输入事件层」；
  2) G1/G2 注入仍复现不出真按键的行为（用同一实验证明）。

目标：让游戏读到"看起来来自真实设备"的原始输入，作为事件层注入的最后兜底。

范围：HookVa 上装一个专用 detour；默认关闭（allow_raw_input_hook=false）。

实现要点：
- hook 游戏读取 raw input 的**包装函数**（不是系统 API 全局，避免波及别的进程/模块）；
  仅在"有待注入数据"时替换返回的 buffer，否则原样放行；
- 设备 handle 复用一次真实事件的（先被动捕获），不要伪造未知 handle；
- 只影响鼠标相对位移与按键；不碰 WM_CHAR / 文本输入；
- panic 必须能完整恢复；install/remove 与 F2/F3 的槽位纪律一致。

不要做：不要 hook GetRawInputData 的系统级入口去影响全局；不要默认开启；
不要用它替代 G1/G2（它是兜底，不是主路径）。

验收：
- 注入的位移/按键在下游与真实设备不可区分（同一段输入的位置/朝向逐 tick 差异 < 1e-6）；
- 不注入时零影响（与未装 hook 的基线逐 tick 一致）；
- panic 后立即恢复，且 DR/字节与安装前一致。
```

---

## G6（P3，可选）世界内 UI 交互

```text
目标：用 G1 注入把**世界内** UI（背包/聊天/暂停菜单）也纳入脚本化，不再依赖 CUA。

范围：只做世界内；主菜单/世界列表/加载期明确不支持（仍用 docs/ui-automation.md 的 CUA）。

实现要点：
- 复用 G1 的 key_event（E 开背包、数字键选槽、/ 开聊天、Esc 返回）；
- 每一步都用游戏状态回读确认（背包是否打开、槽位是否切换），不要只看按键返回值；
- 与 CUA 的边界写进文档：哪些 UI 可以脚本化、哪些必须人工。

验收：
- 打开背包 → 选槽 3 → 关闭，全流程无 CUA，且 state 里手持物符合预期；
- 聊天发送一条 /say，服务端回显可见（或 blocks/命令回执可验证）；
- 主菜单场景调用时明确回 unsupported（不静默失败）。
```

---

## 第三轮回归门禁

沿用前两轮的门禁，另加三条：

1) **事件计数一致性**：注入 N 次 → `mouse`/键位计数恰好 +N（无丢无重）；
2) **门禁**：`allow_input_inject` / `allow_raw_input_hook` 关闭时，对应命令被拒且无副作用；
3) **panic 后输入恢复**：panic → 注入停止、真实输入不受影响、`mouse` 计数继续随真实操作增长。

改完在 `mod/README.md` 的「实测 / 限制」补一行（尤其是"宏录制是否还只认 raw input"这条），
并同步 `AGENTS.md` 的已知坑。
