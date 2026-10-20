# Mod 实现要点

面向维护者。用户 API 见 [`../mod/README.md`](../mod/README.md)。

## 注入时机

- Hook `LocalPlayer::$normalTick`（LL 实例 hook），在 `origin()` **之前**注入输入、之后采样。
  这与 dmf-mpk 的 macro 注入时机一致；`ClientLevelTickEvent` 偏晚，跳跃/按键会被覆盖。
- 移动：`ClientMoveInputHandler::_updateClientMoveVector(strafe, forward, ...)`
- 视角：`LocalPlayer::_applyTurnDelta(Vec2{pitchDelta, yawDelta})`
  （pitch 符号与 `ActorRotation.x` 相反）
- 按钮：`MoveInputComponent` 标志 + `InputEventQueue::enqueueButton` + 输入 handler 回调；
  `input_cfg` 的 `mode` 位掩码：1=直接标志，2=队列，4=handler，8=toggle fallback。
- 状态：`LocalPlayer` + `MoveInputComponent` + `PlayerActionComponent` + 属性 +
  `BlockSource` + `Level`。

## 服务端权威操作

- **传送**：`ServerInstance::queueForServerThread` → 按 UUID/名字找到服务端 `Player` →
  `Actor::teleport(pos, dimension, rotation)`。坐标是脚部。
- **飞行**：客户端和服务端玩家同时 `setAbility(MayFly/Flying)`；只写客户端时，传送后
  服务端 abilities 会把 `flying` 同步回来。
- **命令**：服务器线程 + `PlayerCommandOrigin` + `MinecraftCommands::executeCommand`，
  保留 `@s` / `~` 语义。方块类命令是 deferred，`success=false` 只表示“已提交/延迟执行”。
- **快捷栏**：在 tick 之后调用 `PlayerInventory::selectSlot(slot, ContainerID::Inventory)`；
  放在 tick 之前会被原 tick 覆盖。

## Macro

- 文件格式与 dmf-mpk 一致：每 tick 9 或 11 个字符串
  （`W,A,S,D,Sprint,Sneak,Jump,RMB,LMB,Yaw,Pitch`），保存时角度用 `%.9g`。
- 播放：每 tick 一行；按键按边沿；RMB/LMB 在行边沿直接走
  `traceRay + GameMode`（左键破坏、右键放置）。
- 录制：WASD/Sprint/Sneak/Jump 来自输入状态，Yaw/Pitch 由 `ActorRotation` 差分；
  RMB/LMB 由 `MouseInputEvent` 记录。

## 鼠标录制

- `MouseInputEvent::actionButtonId()` 是 `MouseAction`：`ActionLeft=1`、`ActionRight=2`
  （不是 0/1）；`buttonData` 的 `DataDown=1`。
- 按键同时做**事件锁存**和 `PlayerActionComponent` 动作位上升沿，短点击不会落在两个
  tick 之间被漏掉。
- 只认 raw input：CUA 的合成点击在后台只产生 `ActionMove`；要录按键，游戏窗口必须在
  前台（或使用真实鼠标）。
- `mouse` / `mouse_clear` 可查看各 MouseAction 的计数，用于排查。

## 调试命令

| 命令 | 用途 |
| --- | --- |
| `diag` | `Minecraft` / `ServerInstance` / 服务端 `Level` / `MinecraftCommands` 是否可见 |
| `mouse` | `MouseInputEvent` 总数、最后 action/data、每个 MouseAction 的计数 |
| `mouse_clear` | 清空鼠标计数和按住的电平状态 |
| `buttons` | 列出已注册的输入按钮 ID |
| `input_cfg` | `mode/focus/source/controller` 注入模式 |

## state 来源

`state` 的 `x,y,z` 来自 `Actor::getPosition()`（对 LocalPlayer 是眼睛位置）；
`feet_x/y/z` 和 `head_x/y/z` 分别是脚部/头部。`teleport` 和 DSL 的 `tp` 用**脚部**坐标。
`input.*` 来自 `ClientMoveInputHandler`，`action_sprint` 是
`PlayerActionComponent` 的 bit 9。
