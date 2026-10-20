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

## 物品栏

- 读：客户端 `LocalPlayer` 的 `Inventory`（slots）、`getArmor(ArmorSlot)`、`getOffhandSlot()`，
  在 tick 之后采样。
- 写：服务器线程拿到服务端 `Player`，用 `Inventory::setItem` / `Player::setArmor` /
  `setOffhandSlot` 修改，再 `sendInventory(true)` 同步回客户端。
- 物品解析：`Level::getItemRegistry().lookupByName(aux, name)` →
  `ItemStack::reinit(item, count, aux)`；名字找不到返回 `unknown_item`。
- `give` 用 `Inventory::add`；`clear_inventory` 逐个槽位写空 stack。

## 实体

- 按 `runtime_id` 在 `Level::getRuntimeActorList()` 里查找目标；查询/攻击/交互都在客户端 tick 中执行。
- `attack_entity` → `GameMode::attack(actor, actor->getHeadPos())`
- `interact_entity` → 转向实体 + 注入主手使用按钮（3 tick）。**不要**直接调用
  `Player::interact` / `GameMode::interact`：空 item 上下文会触发 `[rsi+0x1D0]`（RSI=0）
  崩溃。输入注入是 best effort，是否产生实际交互取决于手持物和瞄准。
- `look_at_entity` 用玩家头部到实体头部的向量算 yaw/pitch：
  `yaw = atan2(-dx, dz)`、`pitch = -atan2(dy, horizontal)`，再走 `_applyTurnDelta`。

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
