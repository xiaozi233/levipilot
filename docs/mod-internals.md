# Mod 实现要点

面向维护者。用户 API 见 [`../mod/README.md`](../mod/README.md)。

2026-10-30 审阅修复：请求发布改用私有事务 + 原子载荷 mailbox，tick 读端竞争时保留已验证
快照；按钮数组重置投递到 tick。Python 场景录制使用连接代次内的通知订阅，在请求消费时
同步收集 tick，停止失败会传递错误。该批历史离线 **346/346**、现场 **992/992**（未跳过）；
真实场景适配器等待 12 tick 收集 13 条连续样本，pending=0。本轮逐项状态和现场证据见
[`reliability-repairs.md`](reliability-repairs.md)。

BadPacket 最近既有完整现场为 **1076/1077**（0 failed、1 required skip），唯一 skip 是
适用范围错误的 `inbound_slice_framing`；此前 465/466 是自然退档那轮。证据册 §49 已闭合根因：
游戏侧 `NetworkSystem` 的共享序列化缓冲被两个线程并发使用，产生粘连帧并触发 BadPacket(90)。
harness 不修改游戏网络栈，只减少自有调用的不必要并发状态同步。2026-11-02 已把切片检查改为
包头、实际边界、捕获完整性与序号验证，现场复验结果见 `reliability-repairs.md`；历史调查见
`notes/badpacket-setactordata-20261031.md` §36–§49。下列新连接专项不替代完整现场验收。

## 注入时机

移动/视角调用使用局部 SEH/C++、固定诊断计数、失败传播与默认按键释放重试。
`--controls-only` **126/126**，本批合并传送后的离线 **317/317**、完整现场 **868/868**。
失败消费并停止原输入/视角序列；默认路径的 held 释放成功才清除，不自动重放不确定按下。
status.controls 暴露 held/cleanup/注入标记，control_error 是按操作汇总的后台诊断，
不等同于逐请求完成。默认键位 Hardware HID 契约不变；范围与限制见 reliability-repairs.md。

- Hook `LocalPlayer::$normalTick`（LL 实例 hook），在 `origin()` **之前**注入输入、之后采样。
  这与 dmf-mpk 的 macro 注入时机一致；`ClientLevelTickEvent` 偏晚，跳跃/按键会被覆盖。
- 移动（默认，2026-10-28）：先把 `ControlState` 的数字量翻译成动作，再从
  `IOptionRegistry::getCurrentKeyboardRemapping()` 读 `key.forward/back/left/right/
  sneak/jump/sprint` 的**当前键位**，发 Hardware HID 按键沿；`state.bindings` 暴露
  实际 key code（当前疾跑=R）。未绑定的疾跑才回退双击前进。不再直接写
  `MoveInputComponent`/`MoveInputState`，也不走 queue/handler 重发。
- 视角：`LocalPlayer::_applyTurnDelta(Vec2{pitchDelta, yawDelta})`
  （pitch 符号与 `ActorRotation.x` 相反）
- 跳跃（默认）：Space 按下/松开；`jump` 命令就是 1 tick 的 Space。实测 HID Space 的
  逐 tick 位移仍是 0.4200、0.3332、0.2481、0.1648…（顶点 1.252 格，滞空 11 tick），
  `selftest` 的 `jump_motion_*` 比较 HID Space、持续 `set` 和单次 `jump_when` 的完整轨迹。
- Legacy 诊断：`input_cfg mode` 位掩码（1=直接标志，2=队列，4=handler，8=toggle fallback）与
  `jump_impulse`（1=jumpFromGround，2=setJumpPendingScale，3=两者）默认都是 0；
  非零只在 `force=true` 下接受，用来复现旧测量，不是正常移动路径。
- 潜行：默认 Lshift；legacy mode 1 仍走 `LocalPlayer::setSneaking(true)` + 标志。
  实测 20 tick 前进 4.316 格，潜行 1.295 格（约 0.30x 速度）。
- 状态：`LocalPlayer` + `MoveInputComponent` + `PlayerActionComponent` + 属性 +
  `BlockSource` + `Level`。

## 查询采样（2026-10-31）

`state/buttons/inventory/blocks/scan` 使用 `QueryModel.h` 定长载荷与
`SnapshotMailbox.h` 单槽所有权。管道提交不再增长方块查询 vector；tick 用 CAS 推进，
只采样固定存储；spool 每 25 ms 消费、生成 JSON 并送入传输队列。消费格式化期间仍独占
快照，发送前释放槽，避免快速客户端收到完成事件后立即重提却误报 busy。
取消/重连不会覆盖仍运行的批次或仍在格式化的快照。

- 请求不合并：同一领域在途时回 `*_busy`。state/buttons/inventory/blocks 2 秒、scan 10 秒 wall-clock
  截止；无游戏 tick 时 spool 仍可终结等待，但不会抢占已经运行的有界采样批次。
- 危险读取在局部 `__try/__except` 返回之后才释放拥有者；受控异常由线程归属标记避免
  first-chance VEH 误报 dump。整条采样失败回 `error`，带 `reason` 和目标 `phase`。
  state 方块部分失败仍回状态，标记 `partial=true/warnings`，正常后续采样恢复。
- 文本按 UTF-8 边界截断：玩家名 1023 字节、物品类型/方块名 255 字节、物品显示名
  4095 字节；effects 最多 128 条、buttons 最多 512 条、blocks 最多 256 条，均显式报告截断。
  SDK `getRealName/getTypeName/getName` 返回 owning string，仍可能分配；本批只迁移
  自有 JSON/传输工作，未宣称所有游戏访问器零分配，也未覆盖快捷栏回执/legacy/宏等路径。
- scan 每次最多访问 512 格，即使过滤 air 且 limit 很小也遵守预算。保持 x 最快、再 y、
  再 z；32768 格最多 64 批。仅发现第 limit+1 个匹配项才报告结果截断，恰好 limit 条
  不误报。固定结果区约 4.4 MiB，不在游戏栈上构造多 MiB 临时量；后台清零和格式化。
  `first_tick/last_tick` 表示采样跨度，无法保证期间世界未变化。

复现：`driver/selftest.py --query-capture-only`（故障、唯一终结、恢复、预算），以及
`--query-lifecycle-world "超平坦测试"`（真实扫描重连取消、退出取消、主菜单与重进恢复）。

本批专项 **42/42**、生命周期 **9/9**、完整现场 **295/295**（无跳过），离线 **283/283**；
ruff check/format、mypy、profile 一致性及完整 C++ 构建通过。安装 DLL 与构建产物
SHA-256 相同：`32DD07EEA2C07BE6A0F1FCD9E42327C652650AD42C00B9F902F66302E8D5DC44`。
同一空中 32768 格区域实测 64 批、跨度 63 tick，单批最大 3160 µs；连续 30 次 state
采样中位数 148.5 µs、最大 209 µs。这里只记录本机这轮采样值，不代表稳定上界或性能提升倍率。
该轮 `Overhead.cpp` 旧统计环尚有普通字段并发读写，改前后窗口也非同一批样本，不用其分位数
宣称重构收益；现行修复见“开销语义（F9）”。日志与原始数字在 `out/reliability-query-*`。

## 服务端权威操作

- **传送**：spool 从 `TeleportCapture` 投递
  `ServerInstance::queueForServerThread` → 按 UUID/世界代次共用 `ServerPlayerContext` →
  `Actor::teleport(pos, dimension, rotation)`。坐标是脚部；不再读取客户端玩家名或在 tick 投递。
  `teleport_queued`/`reset_done`/`checkpoint_loaded` 带 `teleport_id`，成功接受后唯一终结为
  `teleported` 或带 `phase=teleport` 的 error；2 秒截止，忙时 `teleport_busy`，退出/panic
  取消、重连丢弃旧回执。错误保守 `partial=true`，不重试已开始的副作用；仅 reset 清效果，
  普通 teleport/checkpoint 保留效果。未给角度保留朝向，只给一个角度时另一个仍为零。
  `teleported` 只确认服务端调用完成，客户端姿态随后同步；连续传送测试须先回读首条目标
  朝向，再检查第二条无角度传送的保留语义。`wait_rotation` 要求连续三 tick 匹配，容差仍为 0.1°。
  `selftest_teleport` 需 `allow_selftest_stress`；`driver/selftest.py --teleport-only` 验证故障、
  效果/朝向回读与恢复（45/45），`--teleport-world` 另验真实重连/退出/重进（50/50）。
- **飞行**：客户端和服务端玩家同时 `setAbility(MayFly/Flying)`；只写客户端时，传送后
  服务端 abilities 会把 `flying` 同步回来。
- **命令**：服务器线程 + `PlayerCommandOrigin` + `MinecraftCommands::executeCommand`，
  默认按带世界代次的本地 UUID 解析，可显式指定完整玩家名；身份未知或玩家不存在时
  明确拒绝，不再改用控制台 origin。保留 `@s` / `~` 语义。方块类命令是 deferred，
  `success=false` 只表示“已提交/延迟执行”。
- **快捷栏**：在 tick 之后调用 `PlayerInventory::selectSlot(slot, ContainerID::Inventory)`；
  放在 tick 之前会被原 tick 覆盖。用户请求使用独占的 `SlotCapture`，立即返回
  `select_slot_ok`，应用后固定快照交由 spool 格式化 `select_slot_done`，保留
  slot/applied/ok/item；同域在途拒绝 `select_slot_busy`，2 秒截止、退出取消、重连丢弃。
  应用/回读有局部 SEH；SDK 的物品类型名仍有 owning-string 分配，不能宣称零分配。
  闭环任务的内部槽切换仍走原 tick 时机，不再产生用户选择回执。

## 物品栏

2026-10-31：背包四组服务端写入、命令、权限和服务端物品使用已移除共享玩家名缓存。
`PlayerIdentityStore` 在 tick 只复制 UUID/epoch/tick，原子载荷 + 有界序号校验，局部
SEH/C++ 故障发布无效身份；旧有效身份不会因新采样失败而继续被返回。`ServerPlayerContext`
只在服务端回调内取得 Minecraft/Level/Player，先验证请求 epoch 与世界状态，再按 UUID
解析；显式命令玩家名只在冷侧查找。tick 不再为身份调用返回 owning string 的 getRealName
或取玩家名锁。`status.player_identity` 提供当前快照与累计故障计数；复用姿态的世界 epoch，
退出/卸载推进 epoch 即可阻止旧身份用于后续世界。重连同一世界不使当前身份失效。
已开始的游戏调用仍不能抢占；传送已共用该解析器，实体/飞行仍待统一。

身份专项 **41/41**、native 模型专项 **18/18**、离线 **315/315**、完整现场 **698/698**
（无跳过）。覆盖四读者/20 万次发布、零自有分配、三类故障下命令与背包写入拒绝及恢复，
真实重连/退出/重进和同 UUID 旧 epoch 拒绝。完整套件通过后，清理期间出现 world_left，
主菜单 ready 持续为 false；正常退出与带安全门的重进未恢复，原因未确定，最终姿态未恢复。
任务/两种录制/宏已停止，已请求解锁，113 个既有 crash 文件无新增/修改。
三份 DLL SHA-256 一致：`1B0BDDD6BE2CD44410D4D18E2B09A780FAD4E851DBFD540B4EA37D705DBD8468`。
复现入口和退出异常的证据边界见 `reliability-repairs.md`；不宣称退出问题或性能提升已验证。

- 读：客户端 `LocalPlayer` 的 `Inventory`（slots）、`getArmor(ArmorSlot)`、`getOffhandSlot()`，
  在 tick 之后采样到定长快照，spool 格式化。请求独占槽位，超时/取消和其余查询一致。
  容量为 256 个背包槽及固定盔甲/副手，约 1.1 MiB；保留真实 `size`，槽位或文字超出
  容量时显式 `truncated=true`。普通 36 格背包完整返回，增加 `sample_us`。
  状态/背包共用 `ItemSnapshot` 复制与冷侧 JSON 格式化；游戏对象不会交给消费者。
  快捷栏选择回执在后续批次接入独占固定快照，见上文。
- 背包后续验收：查询专项 **52/52**，完整现场 **305/305**（无跳过），离线 **283/283**；
  覆盖五类查询的 SEH/C++/超时、唯一终结、恢复，另有背包占用拒绝。原生模型验证
  256 个槽位中的首/尾和副手发布、下一请求清空旧物品及基础设施零分配。
  完整构建、ruff check/format、mypy、profile 一致性通过。安装与构建 DLL SHA-256 均为
  `5642ED2CEDBE5BBDD49A1958B7DAB153D515BA72FD77C907667A9FDA2159A8E2`。
  日志：`out/reliability-inventory-{unit,live,full-live,build,restart}.log`。
- 写：服务器线程拿到服务端 `Player`，用 `Inventory::setItem` / `Player::setArmor` /
  `setOffhandSlot` 修改，再 `sendInventory(true)` 同步回客户端。
- 物品解析：`Level::getItemRegistry().lookupByName(aux, name)` →
  `ItemStack::reinit(item, count, aux)`；名字找不到返回 `unknown_item`。
- `give` 用 `Inventory::add`；`clear_inventory` 逐个槽位写空 stack。

## 实体

- `getRuntimeActorList()` 归**集成服务端线程**所有；客户端 tick 线程不遍历它。所有
  `runtime_id` 查找与快照都在 `queueServerTask` 里执行。实体请求完全离开 tick：spool
  调度客户端平台队列复制玩家位置/头部/UUID（攻击才取名字），再调度服务端读取目标。
  `EntityCapture` 用独享定长快照跨阶段发布，任何 Actor* 都不跨阶段；三个读取阶段都有
  局部 SEH，SDK 返回的 owning string/vector 的持有对象在 raw SEH 帧外。
  最多 512 行，类型 255、实体名 4095 UTF-8 字节，截断报告 `truncated`，每个在途请求
  约 2.2 MiB；同域请求不合并，回 `entities_busy`。2 秒 wall-clock 截止、退出取消、
  重连丢弃，队列弱引用防止未运行的旧任务保留快照，消费只读取成功发布的数据。
  超时后的未开始阶段不再有副作用；已开始的游戏调用不能被超时强行中断。
  客户端/服务端任务投递或执行失败归属同一请求，spool 生成唯一结果。
- `attack_entity` 全程在服务端线程执行：解析服务端玩家（uuid→name 回退，同
  `requestServerTeleport`），用**服务端玩家的** `GameMode::attack(actor, headPos)`；
  Actor* 不跨线程传递（一帧旧的指针曾在 client 任务里 SEH）。
- `interact_entity` / `look_at_entity` 只需要目标的头部位置快照：服务端任务取
  `headPos` 后，瞄准与输入注入经平台主线程队列（`queueClientTask`）回到玩家上执行，
  转向/注入语义与之前一致（3 tick 使用按钮，best effort）。**不要**直接调用
  `Player::interact` / `GameMode::interact`：空 item 上下文会触发 `[rsi+0x1D0]`（RSI=0）
  崩溃。
- `look_at_entity` 用玩家头部到实体头部的向量算 yaw/pitch（`probe::detail::yawTo/
  pitchTo` 共享公式），再走 `_applyTurnDelta`。`look_at_done` 附带实际使用的 `eye/target`
  坐标（客户端/服务端两个阶段，非同 tick 原子快照）。自检用这些坐标重算方向，分别校验
  回执误差 <0.001°、玩家实际朝向误差 <0.1°，避免移动目标的后续位置造成误报。
- 2026-10-31 实体/快捷栏验收：离线 **289/289**，查询专项 **99/99**，真实生命周期
  **16/16**，完整现场 **377/377**（无跳过）。原生模型覆盖 500 轮跨线程阶段交接、
  超时运行中回调与新请求隔离、消费中重连丢弃及槽位快照；现场覆盖三个实体阶段的
  SEH/C++/超时唯一终结和恢复、busy、快捷栏空/非空/边界回读、断线与真实退出重进。
  安装/构建 DLL SHA-256 为 `B55D3DD56823A4C0CCAE438A4B8713054197E7465DFCD3AC53AD1C708F0063C5`，
  无新增/修改 crash 文件；实体攻击血量回读 9→8。日志 `out/reliability-entities-*`。

## JSON 录制与跑酷事件（2026-10-31）

`start/stop` 是仍被 `client.record()`、场景 DSL 与跑酷工具使用的 JSON 逐 tick 接口，
与 `record_start/record_stop` 的二进制环并存。`TickStreamModel.h` 将会话准入、定长队列与
终结状态独立为无游戏依赖的模型；`TickStreams.cpp` 负责局部 SEH 采样与后台格式化。

- 每次录制独立 `record_id`，样本 `tick` 从 1 连续计数；仍保留旧全局计数重置与所有样本字段。
  开始前验证非负整数，在途 `start` 返回 `recording_busy`，不会重置原录制。
  只在世界内且版本可用时接受开始，主菜单返回 `not_in_world`。
- tick 不取录制/传输锁，不构造自有 JSON/string；通过单次 CAS 接纳采样，写固定 256 行的
  单生产者队列。方块名从 SDK 返回的 const 引用直接拷到 255 字节 UTF-8 文本，截断显式报告。
  队列基础设施与 reducer 有原生零分配检查；不据此宣称整个游戏访问器或 mod tick 零分配。
- 命令/spool 通过冷侧锁串行消费与会话切换；停止关闭准入、等待已接纳读取退出、排出所有
  样本后再发送 `stopped`。自动到顶同样在最后一条之后唯一终结。回执增加 `ok/rows/record_id`，
  `capture_fault/capture_failed/buffer_full/world_left/disabled` 是失败；显式重复 stop 保留
  上一终结结果。正常队列约容纳 12.8 秒的 20 Hz 采样，满后明确失败而不覆盖旧行。
- Python 检查明确的 `ok=false`，兼容旧 mod 缺少 ok 的回执，并按 record_id 忽略旧会话事件。
  跑酷工具同时识别 `macro_done/macro_stopped`，主动停止采样并读到其终结回执；异常时
  保留已采样的部分数据，不把失败录制当成成功。
- 跑酷 landed/hurt/died 每 tick 只更新 POD reducer 和固定事件队列，spool 保留原事件字段并
  增加 tick。首次采样建立血量基线，避免带伤进入时误报 hurt；退出/开始进入时更新世界代次，
  故障后清空基线，后台丢弃旧世界尚未格式化的事件。满队列/采样失败报告 `parkour_diagnostic`。
- 受控故障入口 `selftest_tick_stream` 仅在 `allow_selftest_stress` 开启时可用。overflow 专项用
  每会话有效容量 3 和后台消费延迟验证生产分支，不阻塞游戏线程，也不声称测过 13 秒真实阻塞。
- 验收：专项 **39/39**（含真实退出/重进）、完整现场 **411/411**（无跳过）、离线
  **296/296**。原生模型跑 10 万次队列并发交接，覆盖已准入采样/停止竞争、失败终结优先、
  零分配和基线重建；现场 4 tick 宏排出 5 条连续采样，故障后落地距离 2.9216。
  构建/ruff check/format/mypy/profile 检查通过，无新增/修改 crash 文件。
  安装与构建 DLL SHA-256 均为
  `60756A2AB9FE4BD845C13F05C1AD9736B7B742670B99107DCD51D1EE29D19114`。
  复现 `driver/selftest.py --tick-stream-only` / `--tick-stream-world "超平坦测试"`；
  日志 `out/reliability-tick-stream-*`。宏、其余读取边界和 SDK 分配仍是后续范围。

## Macro

- 文件格式与 dmf-mpk 一致：每 tick 9 或 11 个字符串
  （`W,A,S,D,Sprint,Sneak,Jump,RMB,LMB,Yaw,Pitch`），保存时角度用 `%.9g`。
- 播放：每 tick 一行；按键按边沿；RMB/LMB 在行边沿直接走
  `traceRay + GameMode`（左键破坏、右键放置）；`event_level=true` 则生成鼠标事件，
  移动在两种模式下都默认走当前绑定的 Hardware HID 按键。
- `MacroPlayback` 独占帧与名字，冷侧会话锁串行 load/begin/stop 和完成消费。tick 仅一次
  有界 CAS 获取 lease，无会话锁、shared_ptr 复制或 JSON；换帧/启动先关闭准入，再等待
  已准入操作结束。索引和首次应用基线由 tick 推进；区间末帧后的下一 tick 才自然结束，
  循环仍回到 start。坏文件先解析失败，不破坏原播放；成功换帧先停止旧运行。
- 播放/释放分别有局部 SEH 和 C++ 边界；部分应用失败也请求清理，释放失败保留请求，
  下次 tick 重试，新一轮首帧前同样先清理。键位改变时旧绑定未释放成功则保留它供重试。
  run_id 关联唯一终结；macro_done 保留 name/reason/index，增加 ok/run_id。失败原因是
  playback_fault/playback_failed/release_fault/release_failed/world_left/disabled。
  普通 stop/load/task 的取消已有相应回执，不额外发正常 macro_done；手动停止回执后若
  释放失败，仍由 macro_release_diagnostic 的 faults/exceptions 计数报告。
  macro_stopped/status 暴露 release_pending，不把接受停止等同于按键已经释放。
- 直接点击与旧式宏点击只在 tick 内写 128 项定长 ClickResult 队列，后台生成原
  click_result 字段并增加 ok/reason；局部 SEH/C++ 故障分别为 click_fault/click_failed，
  溢出有 click_diagnostic/buffer_full/lost。游戏 API 是否接受交互仍需回读世界，
  ok=true 仅表示调用未触发捕获的异常。SDK 内部操作不在自有模型的零分配保证之内。
- Python 跑酷录制与多次尝试按 run_id 过滤旧终结；收到播放失败先停止状态采样并排完
  已有 tick，再抛出明确错误，单次录制仍保留部分 JSONL。
- 宏播放/点击验收：`--macro-playback-world "超平坦测试"` **56/56**，完整现场
  **536/536**（无跳过）、离线 **307/307**。native 覆盖 200000 帧零分配、范围/循环/
  首帧、在途停止保留帧存储、失败优先与 5000 次并发停止/换帧/重启；现场覆盖两种回放
  的 SEH/C++ 失败、唯一终结/按键释放/重试、短宏替换、任务接管、点击故障与真实退出/
  重进恢复。静态/profile 与构建通过；最终站稳、输入解锁且任务/两种状态录制/宏停止，
  无新增/修改 crash 文件。安装/构建 DLL SHA-256 均为
  `6E461B3FD8E1034F6C1A1C367C7B108F5B44A55FDDE047AB349AC1E253D1D1FC`。
  日志 `out/reliability-macro-playback-*`。卸载分支由 native 覆盖，未现场热卸载；
  未做性能提升对照，不能推广为整个 tick 零分配或第 12/13 项全部完成。
- 录制：WASD/Sprint/Sneak/Jump 来自输入状态，Yaw/Pitch 由 `ActorRotation` 差分；
  RMB/LMB 由 `MouseInputEvent` 记录。
- 录制和播放的冷侧命令共享 `mMacroSessionMutex` 管理准入。录制采样由
  `MacroRecordSession` 独享，tick 不取会话锁；停止先禁止新采样并等待已准入采样退出，再
  在冷侧复制完整快照、锁外保存。保存完成后按录制代次清理，避免并发新会话的数据被旧保存操作清除。
  达到 `max_ticks` 后也保留缓冲供 `macro_rec_stop` 保存。重复录制、
  播放时录制、录制时播放都会返回明确错误，避免两个会话同时改写按键状态。
- `MacroRecordSession` 统一限长策略：省略/0/超大 `max_ticks` 均归一到 200000，较小正数
  使用原值；启动时创建全部已初始化帧槽，tick 不增长 vector、不格式化、不等待消费者。
  到顶只报告一次 limit，保留完整帧。开始回执带实际 max_ticks/hard_limit/record_id，
  status 带 record_limit/record_id。开始前验证非负整数并完成分配，再替换旧会话；失败保留旧数据。
- 宏录制的输入/朝向读取使用局部 SEH，采样 C++ 异常和非有限角度同样明确终结；
  `macro_rec_stopped` 带 ok=false、record_id/ticks/reason，区分
  capture_fault/capture_failed/world_left/disabled。故障结果优先于并发的正常停止。
  首帧角度固定为零，新录制重建基线，yaw 差分有界归一到 ±180°。
  点击锁存无论按钮是否按住都消费，避免松开后补录旧点击。
- 故障后有帧时 `macro_rec_stop` 仍可保存部分数据，`macro_rec_saved` 带
  ok=false/partial=true/reason；Python 抛出含原因和部分文件路径的错误。
  保存先序列化再打开目标，检查 write/flush/close，失败保留帧供重试。
  此处未实现原子文件替换，也不宣称宏播放或整个游戏 tick 已无分配/无锁。
- 宏硬上限验收：`driver/selftest.py --macro-limit-only` **17/17**，完整现场 **322/322**
  （无跳过），离线 **284/284**。原生模型实际追加 200000 条，覆盖省略/0/上限/超上限/
  uint64 最大值、达到上限后不再追加、唯一终结、保留完整数据及预留后零分配；现场验证
  参数归一、3 tick 的真实唯一终结、保存和重复停止，负数/字符串/小数被拒绝且保留缓冲。
  现场未连续录制 200000 tick，硬上限由同一生产模板的原生回归证明。
  完整构建、静态检查和安装产物一致性通过，最终 DLL SHA-256：
  `D918884AE393D2CD9C070E193E930C5CD1AEB47D386DFA77B6A0569333AA0B4D`。
  日志：`out/reliability-macro-limit-{unit,live,full-live,build,restart}.log`。
- 事件级播放停止后在下一客户端 tick 释放按住的 HID 按键和鼠标；卸载路径关闭录制状态并
  提交同一释放请求。
- 宏录制采样验收：`--macro-capture-world "超平坦测试"` **29/29**，完整现场
  **435/435**（无跳过）、离线 **298/298**。生产模型原生回归覆盖 200000 帧零分配、
  并发状态读取、在途采样/停止竞争、故障优先、启动分配失败与跨会话旋转基线；
  现场验证 SEH/C++ 唯一终结、部分帧保存、真实目标打开失败保留 3 帧并重试成功、快速
  启停及退出/重进恢复。未模拟磁盘写满，未连续录制 200000 tick。
  构建/静态/profile 检查通过，无新增/修改 crash 文件；安装与构建 SHA-256 均为
  `D29E1160F03B5227D573316DE639113FF5C1F4476840374C01EC62FB7C9F1134`。
  日志 `out/reliability-macro-capture-*`；宏播放/其他读取与 SDK 分配仍待治理。

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

## 闭环任务（navigate / mine / place / use）

这些任务在 `LocalPlayer::$normalTick` 的前置钩子里推进，而不是由 Python 每 tick 发一条命令。
管道往返（约 1–5 ms）因此不进入反馈环，行为在 20 TPS 下是确定的。同一时间只允许一个任务；
启动新任务会清空手动输入、视角保持和 macro 播放。

- **navigate**：每 tick 计算目标方位角，`_applyTurnDelta` 限幅 35°/tick，`_updateClientMoveVector`
  给前进量；连续 5 tick 水平位移 < 0.02 且在地面时触发一次跳跃；到 `tolerance` 或
  `max_ticks` 结束，回报 `navigated`（`reason=arrived/timeout`）。
- **mine**：先用 `traceRay` 确定面（射线没命中时用视线主轴兜底），首 tick 调
  `GameMode::startDestroyBlock`，之后每 tick 调 `continueDestroyBlock`，并以方块变为空气
  作为完成条件。连续 40 tick 返回失败才判定 `cannot_continue`，因为部分方块不是每 tick
  都报告进度。
- **place**：分三阶段——瞄准、点击、回读。目标格必须是空气；支撑面优先选脚下，再选四周，
  最后选上方，并检查面法线与“眼睛→支撑块”向量同向。点击用真实的 `traceRay` 结果调用
  `GameMode::useItemOn`，然后等 2 tick 回读目标格，避免把预测放置当成成功。
- **use**：等待 `ticks` 后在**服务端玩家**上调用
  `Item::use(stack, player, hand)`（投掷/饮用等瞬时动作）；食物再调用
  `Item::useTimeDepleted` 并 `Player::useItem(method, consume=true)` 完成消耗。
  完成后 `sendInventory(true)` 把服务端层数推回客户端。客户端直接调用只会产生预测，
  背包和效果真正落库的是服务端玩家。这条路径不做事前可用性判断。

## 世界与设置

- `WorldSession` 集中持有世界状态、四个事件订阅及登录/自动进入计时器。
  `worlds` / `enter_world` / `leave_world` / `signin` 均经游戏平台主线程队列执行，
  管道线程只提交参数副本；主线程入口由 SEH 与 C++ 异常边界隔离。
- `worlds`：主线程调用 `IMinecraftGame::getLevelListCache().getLevelList(...)`。
- `enter_world`：按名字或 id 取 `LevelSummary`，用它构造 `LevelSettings`，再调用
  `IGameServerStartup::startLocalServerAsync(levelId, levelName, contentIdentity, settings, intent)`。
  `startLocalServerAsyncFromSummary` 没有被 LeviLamina 导出，直接链接会报 LNK2019；纯虚重载
  走 vtable。实测 `intent=0` 能从主菜单进"超平坦测试"。
- **登录门**：世界列表来自本地缓存，可能早于异步 Xbox Live 登录；这时启动本地服务器会弹出
  "你必须通过 Microsoft 服务身份验证"。进入前通过 `SceneProvider → FacetRegistryFactory →
  ScreenModelProvider::getMainMenuScreenModel() → isSignedInToXBL()` 查询，未登录则轮询等待
  `signin_wait_ms`（默认 15 s），仍未登录回 `signin_required`（`force=true` 可跳过）。
  轮询通过 wall-clock timer 延迟后重新排入主线程，禁止在主线程 Sleep。
  LL 默认 `ClientThreadExecutor` 只随世界 tick 执行，主菜单不能使用。
  join 结果用 `enter_world_settled` 通知，超时/取消回 `ok=false` 和原因。
- `leave_world`：`IGameServerShutdown::requestLeaveGame(true, true)`。
- **进入看门狗**：`WorldEntryModel` 为每次真实启动分配 `entry_id`，回执和
  `enter_world_settled` 均带同一编号。spool 每 25 ms 检查启动后的 30 秒截止，
  不依赖世界 tick/UI 回调；到期唯一终结为 `entry_timeout`，保留隔离准入。
  隔离期间 `enter_world` 回 `ok=false/reason=entry_recovering`，`worlds.ready=false`；
  起始阶段的重复请求仍回 `ok=true/already_starting`，并带原编号。
  平台队列每约 100 ms 探测一次，最多挂一个观察回调；旧编号/调度代次失效。
  玩家存在且 HUD 就绪时可补回缺失 join 事件的成功结果 `player_observed`；
  若已超时则只恢复实际 Playing 状态，不再发送第二次终结。
  只有无玩家、主菜单就绪、两侧 leave-done、can-start 且 ServerInstance 不存在，
  连续确认 500 ms 才解除隔离。加载卡住且未满足这些门时不会强行清标志或再开服务端。
  启动调用与观察使用局部 SEH，C++/SEH 失败仍保留状态并继续观察；
  `status.world_entry` / `worlds.entry` 暴露编号、阶段、剩余时间及观察错误。
  Python `enter_world_ready` 按回执编号匹配终结，兼容不带编号的旧 mod。
  测试专用 `_selftest` 字段受 `allow_selftest_stress` 门控；短超时测试使用 700 ms，
  并模拟 2 秒恢复阻挡，不把它称为真实服务端启动卡死测试。
- 看门狗验收：`driver/selftest.py --entry-lifecycle-world "超平坦测试"` **30/30**；
  离线 **287/287**，完整现场 **324/324**（无跳过），构建/ruff/mypy/profile 检查通过。
  native 覆盖 30 秒边界、恢复连续窗口、取消/迟到成功、旧编号无效及 1000 次加入/超时竞争。
  现场覆盖同编号重复请求、隔离拒绝、恢复、探测 SEH 后继续观察、启动 SEH 与真实缺失 join。
  安装与构建 DLL SHA-256 相同；最终受控故障无新增 crash 文件。
  首次完整回归暴露 `selftest_task_fault` 的局部 SEH 缺少归属标记，已补
  `guardedTaskTick` 与 `task_handled_fault_no_dump`。曾出现一次 `use_failed`，原坐标三次诊断
  未复现；放置自检改为导航后回位、连续站稳并重新避让实体，调整后三轮 **42/42**，
  再跑完整套件通过。不能据此宣称已解决所有 `use_failed` 或所有场景干扰。
  日志：`out/reliability-world-entry-{unit,live,full-live,build,restart}.log`，
  诊断与失败记录保留在同前缀的 `*-place-diagnostic*` / `*-before-fixture.log`。
- 世界事件：`ClientStartJoinLevelEvent` → `world_joining`，`ClientJoinLevelEvent` →
  `world_entered`，`ClientExitLevelEvent` → `world_left`，`ClientCancelJoinLevelEvent` →
  `world_join_cancelled`。
- `options_set`：`IOptionRegistry::loadOptionsFromString` 实时应用（gamma/FOV 立刻可见），
  然后自行更新 `options.txt`。游戏自带的 `saveIfNeeded` 在当前版本不会立刻写盘，所以
  mod 读改写同一个文件（保留 CRLF 和未修改的行），再 `MoveFileExW` 原子替换。
- `harness.json`：`auto_enter` / `options` / `delay_ms`。`WorldSession` 用可取消的延迟回调，
  在主线程等客户端存在、世界列表非空且游戏在前台，再应用选项并进世界。
  disable 会更新调度代次并取消计时器，已排队的旧代次回调不会再触碰游戏对象。

## 记录落盘与崩溃取证

- `enqueue()` 同时把同一行追加到内存缓冲；独立的 spool 线程每 25 ms 检查，按
  “距上次 ≥100 ms 或 ≥64 KB”把缓冲 swap 出来写进 `spool-<pid>.jsonl`。写文件时用
  `ofstream::flush()`，进程被 `taskkill /F` 杀死时 OS 缓冲仍然落盘。
- 连接建立时不再清空 `mQueue`：先强制刷 spool，再把积压行补发给新客户端；
  `hello.replay_rows` 告诉客户端有多少行。队列溢出仍丢最旧，但会往 spool 追加一条
  `{"event":"queue_dropped","count":N}` 标记。
- 崩溃取证走 VEH（`AddVectoredExceptionHandler`），因为 LeviLamina/游戏可能在 mod 加载后
  覆盖 `SetUnhandledExceptionFilter`。致命结构化异常（AV、非法指令、栈溢出等）且不属于
  `dispatchWithSeh` 时，写：
  - `crash-<pid>-<tick>.dmp`：`MiniDumpNormal | MiniDumpWithThreadInfo`；
  - `crash-<pid>.txt`：异常码/地址/线程/tick/最后命令/最后位置/`queue_dropped`/spool 路径。
  写 dump 用 `mCrashDumpInProgress` 防重入，全程只用固定缓冲和 Win32 调用。
- `selftest_crash` 用于验证这条路径，必须显式打开 `allow_crash_test`，因为它会结束进程。

## 内存原语（T2）

- 读写都走 `ReadProcessMemory` / `WriteProcessMemory`（当前进程）：非法地址返回失败而不是
  触发 AV，所以脚本传错地址不会带走游戏。外层还有 `VirtualQuery` 检查
  `MEM_COMMIT`、可读/可写和保护位（含 `PAGE_GUARD`）。
- `probe kind=block`：读 `Block+0x68` 得到 `BlockLegacy*`；`kind=legacy` 读
  `+0xE0`（哈希）、`+0xE8`（MSVC `std::string`）、`+0x17E`（u16 block id）、
  `+0x15C`/`+0x178`（float）。偏移集中在 `Memory.cpp` 的 `BlockLayout` /
  `BlockLegacyLayout`，命令处理里不出现裸数字。
- `blocks` 每个结果附带 `addr`（`Block*`），这样 `probe` 可以直接对活对象回读，
  名字与 `blocks` 交叉校验。
- `mem_write` 默认关闭；开启后每次写 1 KB 以内的十六进制摘要写进 `harness-boot.log`。

## 硬件观察点（T3）

- 每个线程 4 个 DR 槽；`watch_set` 全局分配槽位（所有线程共用同一个 slot 编号，
  DR6 的位号才能唯一映射回 watch）。安装前 `SuspendThread` + `GetThreadContext`，
  如果 DR7 的低 8 位里有不属于我们的 enable 位，回 `dr_busy`，不抢调试器。
- `watch_set` 默认 `thread="tick"`（`safeTickPost` 记录 `mTickThreadId`）；也接受具体
  tid。`thread="all"` 已被显式拒绝（`thread_all_unsupported`）——给 100+ 线程装 DR
  实测会搞崩游戏；VEH 读取 armed 线程表是无锁的定长数组（原子计数发布），异常路径
  不拿 mutex。挂起窗口内不做任何可能分配的操作（日志延迟到 Resume 之后）。
- 命中由 VEH 处理：清 DR6 对应位，把 `{watchId, tid, tick, rip, depth 帧栈}` 写进预分配的
  65536 条定长环；命中路径不做 JSON/字符串/加锁。`watch_drain` 在管道线程把环序列化成
  JSON，`since`/`next` 是绝对序号，`lost` 表示已被覆盖的条数。
- 清除时对每个安装过的线程恢复 DR7 并清零 DRn。

## 二进制 tick 环（T4）

- 记录是 64 字节 POD：`tick u64`、`x/y/z`、`vx/vy/vz`、`yaw/pitch`、`feet_y`、
  `flags u32`、三个方块名哈希、`health_milli`。`static_assert(sizeof == 64)`。
- 写者在 `safeTickPost`：`RecordBuffer` 关闭时一次原子 load 返回，开启时获取无等待的
  capture 准入；`period>1` 时按 tick 取模降采样。记录填完后原子发布到 slot 再推进 head。
  模型的采样准入/发布无 mutex、字符串、JSON 或分配；profile 偏移查询移到 start 的命令线程。
- start/stop 在命令侧串行：关闭准入、等待已经开始的 capture 完成，再重置 head、计数和缓存。
  旧采样不能进入新会话，stop 返回后的 head 稳定。tick 不等待命令锁，也不进行重置。
- 环固定 `1<<19` 条，64 B payload 共 32 MiB，另加约 4 MiB 的发布序号。第一次 start 分配，
  stop 不释放，便于 dump。slot 的 payload 使用原子 word，读前/读后检查发布序号，避免
  环绕覆写时的数据竞争与撕裂；并发覆写导致无法复制的行计入 `lost`，`count` 为实际返回数。
- `record_dump` 在管道线程解码：`jsonl` 直接给字段数组，`bin`/`hex` 给 64 字节记录的
  hex 块（受 JSONL 传输限制，用分块 hex 而不是裸字节帧）。`since/next/head/lost` 都是
  绝对序号；`lost` 表示已被环覆盖的条数。
- dump 结果本身也走 `enqueue`，因此会自动进 T1 的 spool。
- JSON `start` 的逐 tick 事件保留为兼容路径，两者可以同时开。
- 采样的游戏 API 调用放在独立 SEH 边界内；结构化异常先转成失败，再正常退出 capture，
  避免 `/EHs` 未展开析构导致下一次 start/stop 永久等待。SEH/C++ 异常分别停止本次会话，
  原因为 `capture_fault/capture_failed`；退出世界、卸载分别为 `world_left/disabled`。
  tick 只写固定结果，冷侧发一次 `record_failed`，带 `record_id/rows/ok=false/reason`。
  独立事件不会被 Python 当成其他在途命令的 `error`；新 start 前先排出上一代未报告失败。
- `record_started/record_stopped` 与 `status.record` 增加 `record_id/ok/reason/active`，原字段
  保留。正常停止为 `reason=client`；重复 stop 保留原失败，故障优先于并发正常停止。
  启动前后复核世界代次，避免准备缓冲期间退出后仍开启录制。初始化失败保留旧数据和结果。
- `record_dump` 增加 `record_id/ok/reason/partial`，失败后的完整已发布帧仍可读；故障帧不发布。
  Python `record_bin_stop()` 明确传播失败；`record_dump_all()` 默认拒绝失败或丢行，取证时
  可传 `allow_partial=True`。分页固定同一 `record_id`，显式允许部分数据也不能混入新会话。
  `RecordSession` 保存 record_id，退出时先保留部分 `entries` 再抛错；实验主体已有异常时
  将清理失败附为 note，保留原异常。
- 卸载先停止并等待在途采样，再在 spool 退出后补排尚未消费的失败，最后刷盘；
  避免后台线程先结束时漏掉 admitted capture 的终结。卸载分支由原生模型覆盖，
  现场未执行 DLL 热卸载。

- 2026-10-31 失败传播验收：最终专项 **33/33**（含真实退出/重进），完整现场 **484/484**
  （无跳过）、离线 **305/305**；最终生命周期模型调整后补跑 native 回归通过。原生覆盖
  在途采样/停止竞争、失败优先/唯一通知、初始化失败保留旧数据、退出与卸载取消，及
  既有 10 万帧并发环绕/2500 次重启。现场验证 JSON/hex 部分帧逐条一致，失败后 head
  不再增长，checked stop/dump 拒绝失败，新会话恢复；实验主体异常保留并附清理失败。
  日志 `out/reliability-bin-record-*`，复现 `--binary-capture-only` /
  `--binary-capture-world "超平坦测试"`。完整构建、ruff check/format、mypy 和 profile
  通过；安装/构建 SHA-256 均为
  `0C7599A84A80EDF5369391498062E74C43F87356C1D9EA7D3AA2EC52CA9C9540`。
  无新增/修改 crash 文件，最终站稳、输入解锁且任务/两类录制/宏停止。

## 跑酷辅助（P2）

- `scan`：管道线程提交 `ScanQuery`，tick 每次最多读 512 格，spool 格式化完成快照。
  体积上限 32768 格、结果上限 16384，默认跳过空气；生命周期及跨 tick 语义见“查询采样”。
- 存档点：tick 线程通过 `PoseStore` 发布一整帧位置/脚部坐标/朝向，载荷 word 与序号都是
  原子量。管道线程最多尝试 8 次一致读取，不能拼出跨 tick 的坐标；姿态读取有局部 SEH，
  故障/C++ 异常/非有限数字使本次姿态失效，计数由后台生成 `pose_diagnostic`。
  世界退出推进 pose epoch，主菜单或新世界还没有新姿态时 `checkpoint_save` 返回 no_pose。
  成功回执保留坐标字段并增加 tick/pose_epoch，文件使用 `checkpoints.json` 的
  `.tmp` + `MoveFileExW` 原子替换，文件 I/O 不进 tick。崩溃报告同样有界读取一致姿态，
  读取失败明确 pose_valid=0，不在异常处理器等待写者。
  `checkpoint_load` 复用 `resetPlayer`，和 `reset` 一样清任务/输入，但**不清药水效果**
  （`resetPlayer` 现在带 `clearEffects` 参数：只有 `reset` 传 true）。
  <!-- (2026-10-25) 之前 `resetPlayer` 固定 clearEffects=true，且 `teleport` 处理器不重置这个
       共享请求字段，导致**做过一次 reset 之后，之后每次 teleport 都会清空药水效果**
       （保护药水在做实验时"凭空消失"就是这个）；现在 teleport 显式写 false。 -->
- `state.effects` 直接读 `Actor::getAllEffects()`；`player_landed` 用起跳/落地的脚部高度差
  计算落差，`player_hurt`/`player_died` 由每 tick 的血量变化触发。

## 运行时装钩（T5）

- `hook_install` 支持两种目标：
  - `symbol`：编译期符号（`&MobJump::setJumpPendingScale`），使用 **typed detour**
    `void(EntityContext&, float)`，参数和返回值都按原签名传递；目前只登记了这一个符号。
  - `va`：来自 IDA 的运行时地址，使用通用 detour，只保存 rcx/rdx/r8/r9 四个整数寄存器；
    需要 `allow_hook_va=true` 才能真装，`dry_run` 只做地址校验。
- 地址校验：`VirtualQuery` 要求 `MEM_COMMIT`、非 `PAGE_GUARD`、`PAGE_EXECUTE*`；VA 的
  指令边界由调用方（IDA）负责，mod 只报 `instruction_boundary:"unverified"`。
- 安装/卸载走 `ll::memory::hook` / `unhook`（suspendThreads=true），原点指针由 LL 写回；
  失败返回 `hook_failed` + code，不会留下半装状态。
- 命中环固定 4096 条：每次命中只写 `{hookId,tid,tick,四个参数,返回地址,4 帧栈}`，
  按 `sample` 降采样；`hook_drain` 在管道线程序列化，`b_float` 把第二参数按 float 解释。
  记录由 `AtomicRing` 的原子 64 位载荷发布，多个 detour 线程不会写普通字段造成数据竞争；
  drain 遇到延迟提交会保留当前 `next`，遇到环绕覆写会累加 `lost`。
- `hook_call` 只是为了验证：tick 线程调用 `MobJump::setJumpPendingScale(ctx, scale)`，
  该调用会穿过 detour，因此能确定性地产生一条命中。请求使用独占 `SnapshotMailbox`，
  参数非有限时拒绝，在途重复请求返回 hook_call_busy，不覆盖前一参数。
  局部 SEH 隔离调用；JSON 与传输移到 spool，成功保留 hook_call_done/symbol/scale 并增加 ok。
  失败为 error，带 phase=hook_call 和 capture_fault/capture_failed/capture_timeout/world_left；
  2 秒截止，主菜单拒绝，退出/卸载取消，重连丢弃旧在途回执。
  已开始的游戏调用不能被超时抢占；钩子目标本身的行为与开销不因 wrapper 而消失。
- 钩子调用/姿态验收：`--tick-safety-world "超平坦测试"` **32/32**，完整现场
  **459/459**（无跳过）、离线 **299/299**。原生覆盖 200000 次姿态并发发布、无混帧/
  跨世界/非有限值、故障失效/零分配；钩子请求参数保护、超时/取消后拒绝迟到执行和异常。
  最终模型变更后两个 native 用例补验通过。现场覆盖真实重连/退出/重进和故障后恢复，
  无新增/修改 crash 文件；未主动崩溃验证报告格式，也未声称整体性能改善。
  最终安装/构建 SHA-256 均为
  `86AA34E86C7BBC9BC06D89A3A60072C9F562883376F6ECAE8E966AF8D2B0EA73`。
  日志 `out/reliability-tick-safety-*`；宏播放、ECS/输入等其余边界仍待治理。

## ECS 内省（T6）

- 请求由 `StagedCapture<EcsQuery, EcsSnapshot, EcsStage>` 独享，与实体请求共用生命周期
  模型。ECS 不进入 tick：spool 调度客户端平台队列或服务端队列，回调只读游戏对象并填
  快照，spool 统一生成 JSON。管道线程只验证/安装请求，不读取游戏对象。
- `ecs_registries` 在客户端队列枚举当前玩家的 registry；runtime-id 三类请求先确认客户端
  玩家存在，再在服务端队列查找目标和读取。`ecs_context` 在局部 SEH 下复制原始 handle 的
  registry 身份和 entity id；只有与当前客户端 registry 匹配，或在服务端找到对应 registry，
  才读取组件。跨阶段只传数字，不保存 Actor/registry 指针供后续回调直接使用；陌生 registry
  返回 context_not_found。原始地址可能复用，scope/registry 是本次核对结果，不是持久 handle。
- `EntityContext::getRegistry()` 返回 EnTT v4 的 `basic_registry<EntityId>`；
  `registry.storage()` 迭代 `(runtime component id, storage)`，`storage.contains(entity)` /
  `storage.index(entity)` / `storage.value(entity)` 给出该实体的组件指针。
- 每请求冷侧分配约 37 KiB 定长快照；最多保存 1024 条结果/失败 id，最多访问 4096 个 storage，
  超限标记 truncated。contains/index/value/stride 都在逐 storage 的局部 SEH/C++ 边界中；
  坏条目进 skipped/partial，其他条目保留。整体游戏读取故障为 ecs_fault，C++ 为 capture_failed。
- 组件字节用 T2 的 `readMemory` 读；显式长度优先，其次 profile 尺寸、stride、64，
  最终限制为 1..512。stride 由同一 storage 前两个元素的 `value()` 地址差推断，
  单元素/零地址/超 uint32 距离为 0。profile 已知组件带 size/name，未知 id 不猜类型。
- 共用 2 秒 wall-clock 截止；同域重叠请求返回 ecs_busy，参数错误保留原请求。
  退出/卸载取消，重连丢弃旧回执；排队回调只持弱引用，迟到回调不能操作新请求。
  已开始的游戏读取不能被超时抢占，超时消费者不读取仍在写入的快照。
  SDK 的实体列表/类型名访问仍可能分配，但都在所属队列中执行，未宣称整个 tick 零分配。
- ECS 验收：`--ecs-capture-world "超平坦测试"` **51/51**（真实重连/退出/重进），
  完整现场 **575/575**（无跳过）、离线 **308/308**。native 验证结果/失败上限、
  4096 个无匹配 storage 仍受预算限制、逐条故障与零分配、500 次跨线程交接、运行中
  超时不暴露快照、新请求隔离与重连丢弃；原实体模型同样通过。现场覆盖五接口、
  两侧 SEH/C++/超时、唯一终结/恢复、坏 storage 保留其余结果、busy/坏参数保留原请求，
  以及主菜单拒绝和新 context。另以可读的非 context 缓冲验证陌生 registry 被拒绝为
  context_not_found。临时标签自然消失不再误判为采样丢失。
  构建/静态/profile 通过；无新增/修改 crash 文件，最终站稳、输入解锁且任务/录制/宏停止。
  安装/构建 DLL SHA-256 均为
  `70C229A1EA2F11ABFB985327C71A2B213223589F39CD679416B3616B53F3A3C0`。
  日志 `out/reliability-ecs-*`。未现场热卸载、未做性能对照，第 12 项其余边界仍待治理。

## panic（T8）

- `panic {level:"hooks"|"tasks"|"all"}`：
  - `hooks`：`hookRemoveAll()` 逐个 `ll::memory::unhook`，再 `watchRemoveAll()` 清 DR 观察点；
    卸载失败的钩保留占用，便于重试。
  - `tasks`：`cancelTasks()` 递增 epoch 并清空所有闭环任务，`beginTask()` 停 macro、清移动输入。
  - `all`：两者都做。
- 回 `panic_done {ok, hooks_removed, watches_cleared, tasks_cancelled}`；进程和 tick 都不受影响，
  之后可以重新装钩/观察点。`unhook_all` 是 `panic hooks` 的别名。

## tick 开销测量与请求块（T9）

- 请求块：移动/跳跃/look 共用 `RequestBlock`。`RequestMailbox` 冷侧在写锁内构造私有副本，
  成功后按 64 位原子载荷发布；tick 每次 `readRequests()` 最多尝试 8 次，竞争时保留上次
  已验证快照，不等待写者。每条命令带自己的 `inputSeq/jumpSeq/lookSeq`，tick 侧消费一次
  并递减本地 ticks，`ticks<0` 仍表示直到 stop。传送已移出该请求块。
  `beginTask`/内部错误仍会写块（罕见），但只占写锁，不走读路径。
- 移动和 look 使用同一份有效快照；跨客户端/服务端的传送不宣称与它们同 tick 原子生效。
- `onPlayerTick` 进入时取一次 QPC，`safeTickPre` 结束后算 pre 耗时；`afterPlayerTick` 同理算
  post 耗时。`hook_us = pre + post`（不含 `origin()` 的游戏 tick），
  `tick_us` 是相邻两次 pre 开始的间隔。
- 1024 条定长样本环，现行实现复用 `AtomicRing` 原子载荷和逐槽发布序号；
  `overhead`/`status.overhead` 在管道线程验证完整窗口，再排序并给出
  p50/p99/max、`tick_hz`、`calls_per_sec`。
- `overhead.paths.{record,watch,hook}` 给出三条路径各自的 `calls_per_sec` 与 `calls_total`
  （窗口从首次调用起算），用于发现异常回调频率。
- `selftest_busy {us,ticks}` 在 `safeTickPost` 里忙等，需要 `allow_selftest_stress=true`
  （默认拒绝，避免误让 tick 卡近 1 秒）；关闭时回 `error/stress_disabled`。
- 历史实机（1.26.51.01，20 TPS，旧统计载荷尚非原子；保留证据，不用来论证性能收益）：
  seqlock 改后空闲 1024 样本 `tick_p50=50.006 ms`、
  `tick_p99=50.970 ms`、`hook_p50=10 µs`、`hook_p99=38 µs`；改前空闲为
  `tick_p99=54.596 ms`、`hook_p99=30 µs`；两轮不是受控对照，不能据此认定没有劣化。
  注入 1 ms ×20 tick 后 `hook_p99=1013 µs`、`hook_max=1040 µs`，证明指标可观测。

## hook_va 安装与生命周期（F2/F3）

- 槽位在 `gHookMutex` 内预留（`used=true`、`ready=false`）后**释放锁**再调
  `ll::memory::hook(..., suspend=true)`，避免持锁挂起线程造成堆/Loader 死锁。
- 补丁生效后、origin 发布前，detour 用 `ready` 有界自旋（200k 次 `YieldProcessor`），
  不再静默 `return 0`；origin 用原子发布，`ready` 最后置位。
- 失败路径：`status!=0` 直接释放槽位；`status==0 && origin==nullptr` 先 `unhook`，
  失败则保留槽位（`unhook_failed`，panic 可重试），成功才释放。同一地址重复安装回
  `hook_duplicate`。
- 主菜单（不在世界）安装回 `not_in_world`：LL 在该状态不挂起线程，直接改代码不安全。
- `hook_install` 的 `inject_fail`（仅 `allow_hook_va=true` 时生效）用于自检 F3 失败路径。

## offsets 表（F5）

- `Resolve.cpp` 内置一张 `struct -> field -> offset` 表（Block/BlockLegacy 共 6 项）和一个
  `components` 表（`id -> {name,size}`；StateVector 的 459100728/36 来自实机）。
  `<mods>/probe/offsets.json` 的 `structs`/`components` 会覆盖内置值。
- `structOffset("Block","LegacyPointer",...)` 是唯一查询入口；`Memory.cpp` 的
  `probe block/legacy` 与 `RecordRing.cpp` 的方块采样都走它，不再有魔法数字。
- JSON 里把某个字段设成 `null` 会移除该表项，`probe` 回 `{"event":"error","reason":"no_offset"}`，
  不会拿默认 0 去猜。未知字段只写 boot log，不生效。
- `diagnose.structs` 输出 `source/struct_fields/component_count/missing_lookups/last_missing`。
- 实测：把 `BlockLegacy.BlockId` 改成 `0x17C` 后 `probe legacy.block_id` 从 1 变 0；
  设 null 后 `probe legacy` 回 `no_offset`，`probe block` 仍正常。

## spool 顺序与轮转（F6）

- `flushSpool` 用 `mSpoolWriteMutex` 包住“swap 队列缓冲 + 写文件”，两个并发 flush 无法交错；
  `enqueue` 只取 `mQueueMutex`，tick 线程不会被文件 IO 阻塞。
- 启动时若 `spool-<pid>.jsonl` 已存在且非空，改名为 `spool-<pid>-<n>.jsonl`（PID 复用时
  不会把新会话追加到旧文件）；单文件写到 64 MiB 也会轮转。
- `spool_flush` 返回 `path`（当前文件名）。

## watch 细节（F7）

- foreign 检查只看目标槽的两个 DR7 位：`(Dr7 >> (2*slot)) & 3`；调试器占着别的槽不再
  阻断安装。
- 数据断点是执行后触发，`watch_drain` 的 `rip` 是**下一条**指令；每行附
  `code_hex = rip-16..rip`（32 位 hex），顶层 `rip_semantics="next_instruction"`。
- `allow_watch`（harness.json，默认 true）：关闭时 `watch_set` 回 `watch_disabled`；
  `watch_installed` 带 `suspends_tick_thread=true`。
- `watch_set` 的 `depth`（1..32，默认 8）控制每次命中抓多少返回地址；记录固定 32 槽，
  实际只填 `depth` 帧。深度越大，VEH 每次命中多一次更深回溯、环占用更大
  （65536 条 × (48 + 8×depth) B）。越 1..32 回 `bad_depth`。
  注意前几帧常是 VEH 处理器的栈，8 帧实际只剩 3 层真实调用者；
  取自证 pass/调用链时用 20 左右。

## 记录环采样（F8）

- 每 tick 的三次方块采样不再调用 `getTypeName()`：先经 F5 表读 `BlockLegacy::BlockId`，
  再用 256 项 `id -> FNV-1a-32` 缓存，稳态零字符串分配（每 tick 每个方块类型只在首次出现时分配一次）。
- y 超出 [-64, 319]（三种原版维度高度的并集）时不调用 `getBlock`，三个 block 字段写 0，
  并置 `RecordFlagBlockOob = 1<<11`。

## 开销语义（F9）

- `tick_p50/p99/max_us` 保留旧名，语义是**相邻 tick 开始的间隔**；新增同值的
  `tick_interval_*_us` 以免误读。`hook_*_us` 才是钩子体（pre+post）的耗时。
- `window_samples=1024`、`qpc_resolution_ns` 随响应返回。原 `gEpoch` 加普通 Sample
  的方案已移除：版本检查不能消除 C++ 数据竞争。`OverheadWindow` 复用 `AtomicRing`
  的原子载荷与逐槽发布序号，最多尝试 64 次；只接受所有槽有效且 head 未变化的窗口。
- `sample_first/sample_head` 是左闭右开的采样序号区间，`samples` 等于其长度。
  竞争超限时 `consistent=false/samples=0/reason=snapshot_busy`，不输出无效分位数；
  初始化失败回 `unavailable`。空环为 `consistent=true/samples=0`。
- 初始化在 enable 冷侧完成，失败时 tick 丢弃样本，禁止在 tick 重试初始化/分配/加锁。
  QPC 频率改为原子读写。路径调用率与调用总数共用一个单调计数，首次调用只设置起始时间，
  不再重置可能被其他生产者增加的窗口计数。
- 2026-10-31 验收：离线 **288/288**、`--overhead-only` **7/7**、完整现场
  **330/330**（无跳过），构建、ruff check/format、mypy 与 profile 一致性通过；
  安装/构建 DLL SHA-256 相同，无新增 crash 文件。真实游戏推进 **1100 tick**，
  **1741 次**窗口读取全部一致，录制路径调用计数增量 **9**。
  native 覆盖空环/未初始化/容量 1/最大 uint32/强制零次重试失败、20 万次并发写入与
  4 个并发读者的窗口顺序/载荷关联、记录及快照复制零分配、4 线程共 40000 次路径计数。
  内存从 8 KiB 样本载荷变为 16 KiB 原子槽；读取最多 64 次重试，无性能倍率结论。
  日志 `out/reliability-overhead-{unit,live,full-live,build,restart}.log`。

## 版本指纹与锚点（T7 / 版本 profile）

- 启动时用 `GetFileVersionInfoW` 读主模块版本，得到 `build_id`（当前 `1.26.51.1`）。
  **不再在 C++ 里写期望版本号**：`build_id` 去 `GeneratedProfiles.h` 里找 profile。
- profile 数据源是 `docs/offsets/*.json`（带 `"runtime": true` 的文件），由
  `driver/gen_profiles.py` 生成 `mod/probe/src/mod/GeneratedProfiles.h`。加一个游戏版本 =
  加/改 JSON + 重新生成 + 重编，不改任何 C++ 逻辑。
- 选择顺序：`build_id` 精确匹配 → `version_prefix`（如 `1.26.51.`，兼容小版本修订）→
  `version` 字符串 → 没有 profile。`profile_probe {build_id}` 可以离线验证某版本会选中谁。
- 4 个锚点仍是固定集合（`LocalPlayer::$normalTick`、`LocalPlayer::_applyTurnDelta`、
  `Actor::getHealth`、`Actor::isOnGround`），地址走符号；LL 导入桩可能是
  `jmp qword ptr [rip+0]` thunk，先跟随到真实函数再读 24 字节。
  签名来自 profile 的 `anchors.<name>.expected_hex`；**没配置的锚点不参与判定**。
- 结构体偏移和组件 id 也来自 profile（不再有编译期常量）。运行时
  `<mods>/probe/offsets.json` 可以只覆盖其中几项；也可以追加 profile 里没有的字段/
  组件（用于新版本临时试跑，不必重编）。
- 门禁语义：
  - `version_ok` = 找到 profile 且所有已配置锚点匹配；
  - `hooks_allowed` = `version_ok` 或 `allow_version_mismatch=true`；
  - tick 钩子、`watch_set`、`hook_install`、`record_start`、输入注入、封包 tap 都看
    `hooks_allowed`；
  - 没有 profile 时偏移表为空，`probe`/`record` 的方块字段回 `no_offset`，其余功能仍可用。
- `status`/`diagnose` 输出 `profile` 和 `capabilities`（hooks/input/watch/record/packets/
  offsets/components/profile/compat），新版本上先看这两个就知道哪些能力可用。
- `profile_dump` 从**当前运行的游戏**读 4 个锚点的真实字节、当前结构偏移和组件表，并给出
  一份可直接保存为 `docs/offsets/<version>.json` 的 `profile` 对象。移植流程见
  [`porting.md`](porting.md)。
- `diagnose` 输出每个锚点的地址、resolved 地址、配置状态、匹配结果和耗时；
  `signature_check {name,expected_hex}` 用来验证负路径（传错签名应回 `match=false`）。
- 只有带 `"runtime": true` 的 profile 参与生成；旧 schema 的历史数据没有这个标记，会被跳过。

## 封包解码注册表（H4）

- `driver/harness/packets.py`：`PACKET_NAMES`（由 `MinecraftPacketIds.h` 全量生成的
  256 项 id→名字表）、`decode_packet(id, bytes)`、`decode_row(packet_bytes_row)`。
  未知 id 返回 `{id,name:"id_N",hex}`，不抛异常。
- 已实现布局：
  - `77 CommandRequest`：命令字符串、origin 类型/名字/UUID、requestId、playerId、版本。
    **用实机抓到的 77 包验证**（`/give @s minecraft:stone 5`，origin=ClientAutomation）。
  - `36 PlayerAction`：runtimeId varint、action varint、x/y/z float、face varint。
  - `19 MovePlayer`：runtimeId、位置、pitch/yaw/headYaw、mode、onGround、ridden、tick。
    36 是客户端→服务端；19 需要另一个玩家才会在单机出现，所以两者仍然只有合成样例单测
    （本客户端移动走 PlayerAuthInput(144)）。H1-C 的接收侧只实测到 9/13/65/111，
    这些 id 暂无解码器，保留 hex。
- B3 存的是 payload 字节，不含包 id varint；解码器从第一个字段开始读。
- CLI：`py -3.11 -m driver.harness.cli packet-decode --id 77 <hex>`。
- `driver/packet_dump.py` 把身份环 + 字节环按 seq 拼起来，解码后写
  `out/packets-<ts>.jsonl`（行内带 `dir`）；`--directions send,recv` 时实测 5 秒
  105 行 send、54 行 recv。

## 命令包注入（H2）

- `CommandRequestPacketPayload` 的默认构造没有导出；通过真实 SDK 构造器重建 payload，
  只复用真实发送捕获到的来源值：
  1. 用 `/`（key 191）+ `text_input`（HID `onTextInput`）+ Enter 发一条真实命令；
  2. `sendToServer` 钩子在 id==77 时仅采 origin 数值字段和 requestId 字节，写入预初始化的
     AtomicRing；不复制 owning payload/command，不取模板锁，读取有局部 SEH/C++ 边界。
     origin UUID 不假定等于玩家 UUID，另附 tick 发布的玩家身份/世界代次与请求代次。
     最新捕获 pending/失败时明确不可用；requestId 超过 512 字节拒绝，embedded NUL 原样保留。
  3. `chat_send {text}` 冷侧安装独占请求，spool 调度平台客户端队列。临时
     ClientAutomationCommandOrigin → CommandContext → payload 均走 SDK 构造器，随后完整覆盖
     捕获的 type/UUID/requestId/playerId，发送的来源仍为原真实来源；设置
     `mVersion=Latest`/`mInternalSource=false`，构造 packet 并调用 `sendToServer()`。
     构造前、发送前都复核代次/截止/实际玩家 UUID；自身发送不再刷新模板，避免迟到发送污染新代次。
     owning 对象在 raw SEH frame 外持有，已完成构造的对象正常析构；SDK 构造中途故障不可回滚。
- 门禁：`allow_packet_inject`（默认 false），只允许这一个 id；`chat_send` 只入队，
  真正发送和结果通过 `chat_sent {ok,text,packet_id,partial,reason?}` 事件由后台回报。
  `chat_send_queued` 也带 packet_id，旧字段保留；Python 默认仍只等接受，wait=True 可等完成。
  请求忙时明确拒绝，2 秒截止；退出/panic/内部错误取消，重连丢弃旧请求，模板同时按代次失效。
  超时/取消不能读取还在运行的 snapshot，不重试可能已开始的发送，失败保守 partial=true。
- `op_self {level}` 在服务端玩家上 `setCommandPermissions`（测试世界用）；
  `commands_enabled` 读写 `LevelData::mCommandsEnabled`。客户端命令包仍受玩家权限约束，
  `commands_enabled=true` 不代表玩家是 Operator。
- 实测（1.26.51.01）：`/setblock` 回读 `gold_block`；`/tp @s ~1 ~ ~` 位移 1.000；
  `/fill`、`/give` 生效；注入前后 tick p99 不变，`chat_sent` 计数正确。
- 注意：`chat_sent ok=true` 只表示发送调用完成；命令是否执行必须用 `blocks`/位置回读。
  模板在每次真实 77 包发出时刷新，重启/重连/世界退出或取消后需要重新发一条命令。
  上面的 p99 数字是历史 H2 验收，本批未做性能对照；通用 PacketTap::write 的序列化分配和
  其余高频钩子 SDK 边界仍待处理，不能把命令包请求迁移等同于整个第 12 项完成。

## 封包字节与层选择（H1-B）

- 运行时符号解析：`symbol_resolve {name}` 先查编译期锚点（`&BatchedNetworkPeer::$sendPacket`、
  `&LoopbackPacketSender::$sendToServer`、`&NetworkConnection::receivePacket`），再跟随
  `E9 rel32` / `FF 25 [rip+disp]`；返回 `{anchor, real, thunk_hops, module, rva, executable}`。
  LL 的 `SymbolView::resolve()` 对游戏导入名返回 0，不能依赖它。
- 实测：`BatchedNetworkPeer::$sendPacket` = `Minecraft.Windows.exe+0x46EB60`（无 thunk）；
  `LoopbackPacketSender::$sendToServer` 锚点 `+0xD809E0`，跟随 3 跳到 `dmf.dll+0x8AC10`
  （已被扩展链式 hook）；`LocalNetworkPeer::$sendPacket` 只有导入声明，链接不到。
- 计数决策（`hook_va` 只计数，不读参数）：
  `sendToServer` 走/跳/命令都有流量；`BatchedNetworkPeer::sendPacket` 和
  `NetworkConnection::receivePacket` 在单机里都是 0 ⇒ 本地回环绕过序列化/peer 层，
  B1/B2 拿不到字节，转 B3。
- B3：`packet_capture {serialize_ids:[144,77]}` 时，仅对这些 id 在 `sendToServer` 钩子里
  用 `BinaryStream(std::string&)` 调 `Packet::write`，前 128 B 写进 4096 条字节环；
  `seq` 复用身份环的 order，`packet_bytes` 返回 `{seq,id,size,hex}`。
  代价：首次调用分配线程局部缓冲；关闭 capture 时零调用；denylist 的 id 不能进白名单。
- 实测 `PlayerAuthInput(144)` 序列化后 91–93 B，前几字节是位置浮点（可读），
  32 条字节记录与身份记录 100% 对上。
- 注意：序列化在客户端线程、发包钩子内进行；只对 1–2 个白名单 id 启用，不要扩成全量。

## 封包接收侧 tap（H1-C）

- 目标：补上服务端→客户端方向。单机里集成服务端同进程运行，回环投递不经过
  `NetworkConnection::receivePacket`（H1-B 实测 0），所以要挂投递层。
- 两层锚点，共 9 个钩子，`packet_capture {directions:["send","recv"]}` 才安装：
  1. `Packet::sendTo(Player)` / `sendTo(Actor, optional_ref)` / `sendTo(BlockPos, DimensionType, optional_ref)`、
     `Packet::sendToClient(NetworkIdentifier, SubClientId)` / `(NetworkIdentifierWithSubId)`、
     `Packet::sendToClients()` / `(exceptId, exceptSubId)`——服务端常用的发送辅助；
  2. `LoopbackPacketSender::$sendToClient(NetworkIdentifier, Packet const&, SubClientId)` 和
     `...(UserEntityIdentifierComponent const*, Packet const&)`——实际回环投递。
- 实测（1.26.51.01，超平坦，3.5 秒）：layer 1 **全部 0 hit**；layer 2
  `sendToClient` 66 hit、`sendToClient(user)` 4 hit。说明服务端代码直接走 sender 虚函数，
  不经过 `Packet::sendTo*` 辅助。`packet_stats.recv_anchors` 保留每个锚点的原始 hit 数。
- 接收方向主序列：`MoveDeltaActor(111)`（附近实体移动）、`AddActor(13)`、
  `LegacyTelemetryEvent(65)`、`Text(9)` 等。`/tp` 成功消息出现在 `Text(9)` payload 的
  JSON 原文里（`commands.tp.successVictim...`），可用来验证字节层。
- 去重：两层可能对同一包各触发一次；`onPacketReceived` 用
  “同 tick + 同 Packet 对象 + 同 id”折叠重复，广播到多个客户端只留一条身份记录。
  原始投递次数看 `recv_anchors`。
- 记录与字节环都新增 `dir` 字段（`"send"`/`"recv"`），`packet_drain` / `packet_bytes`
  原样返回；`selftest` 有 5 条 recv 检查（开启、记录、统计、字节、dir 字段）。
- 字节层：`serialize_ids` 对接收方向同样生效；`Packet::write` 是 const，在投递钩子里
  调用是安全的，白名单与 denylist 规则和发送方向一致。

## 封包身份 tap（H1 阶段 A）

- 状态（身份环、字节环、配置、统计）和 send/recv 共 9 个钩子都在
  `PacketTap`（`PacketTap.h/.cpp`）；`ProbeMod` 只保留 `onPacketSent` /
  `onPacketReceived` 转发，以及命令包模板注入。
- 锚点：`LoopbackPacketSender::$sendToServer(Packet&)`（LL 符号 hook，见
  `docs/offsets/1.26.51.01.json` 的 `packets.sendToServer`；24 B 签名待补）。
- 钩子体只做四件事：`packet.getId()`（虚函数，返回 `MinecraftPacketIds`，无分配）、
  `mPacketSeq.fetch_add`、`mTick`、`GetCurrentThreadId()`；写进 16384 条 MPSC 环，
  通过 `AtomicRing` 逐槽原子载荷发布。**不调用 `getName()`**（返回 `std::string_view`，但约定是
  身份层不碰字符串）。
- 身份环和字节环的 drain 都保持 reservation 顺序：未提交槽位不会被跳过，已覆写槽位会推进
  `next` 并计入 `lost`，避免消费者游标悄悄越过缺口。
- 停止捕获时先关闭回调入口并等待已进入的采集回调退出，再卸钩；共享门闩把“是否关闭”与
  在途计数放在同一原子值，防止停止线程漏掉刚开始的回调。
- 环记录：`{order, tick, id, tid, injected}`；`injected=false` 预留给 H2。
- denylist（硬挡，任何 `ids` 参数都无效）：KeepAlive 0、Login 1、PlayStatus 2、
  Handshake 3/4、Disconnect 5、ResourcePack 6/7/8/82/83/84、Transfer 85、
  SubclientLogin 94、NetworkSettings 143/193、ResourcePacksReady 340。
- 采样 `sample=1/N` 基于 order；`allow_packet_capture=false` 时不装钩、零记录；
  `panic`/`disable` 会 `packetCaptureStop()`。
- 实测：3.5 秒 122 条，`seq` 0..121 单调无丢失，tid 固定 11684；id 分布以
  `PlayerAuthInput(144)`、`SubChunkRequest(175)` 为主，攻击/放置产生
  `InventoryTransaction(30)`、`Interact(33)`。
- 字节层决策：`LocalNetworkPeer::$sendPacket` 是 dllimport-only（链接不到导出 thunk），
  所以 B1 的“NetworkPeer 字节 tap”无法按符号实现，`peer_sends` 恒为 0。
  如果要字节，走 B2（服务端接收侧，需按包类型/分派器定位）或 B3（白名单序列化）。
- H3 预演：B（标志）与 C（事件）各 3 秒的 id 序列几乎一致（144 占绝对多数），
  身份层无差异；G3 已证明速度也一致，所以不需要用封包解释疾跑。

## 能力可用性验收（H0）

- **StateVector 写者**：`state.runtime_id` / `state.context` 给出玩家标识；StateVector 的
  `VelY` 偏移来自 offsets 表（`0x1C`）。玩家位置/速度由**集成服务端线程**写入，所以观察点
  用 `watch_set(thread="both")`（tick + server）。实测命中指令为
  `mov qword ptr [rbx + 0x18], rax`（8 字节写覆盖 0x18–0x1F，包含 vy）。
- `thread="all"` 会给 100+ 个线程装 DR，实测导致游戏崩溃，mod 现在直接拒绝该模式；
  `status.server_thread_id` 在第一次 `queueServerTask` 后可用，`thread=server|both` 用它。
- **probe × blocks**：`blocks` 给出 `Block*`，`probe block` 跟到 legacy 指针，
  `probe legacy` 的 name/hash 与 blocks 一致。51 版实测 block id：air=0、grass_block=2、water=9
  （结构布局跨版本不变的假设目前成立）。
- **hook context × ECS**：`ecs_entity/ecs_components` 按 runtime id 解析到服务端 Actor；
  客户端玩家的 `EntityContext*` 从 `state.context` 取。`ecs_context {context,component_id}`
  在 tick 线程用这个指针直接查 registry，与 `state.context` 的查询结果（地址/count/size）
  一致。**不要**在 tick 线程枚举客户端 registry（会与游戏线程竞争并崩溃，已实测）。
- **落盘三连**：`driver/spool_forensics.py panic|taskkill|verify`。panic 路径 spool 单调连续
  且末事件为 `panic_done`；taskkill 最多丢 100 ms（实测 0 tick）；leave_world 需要桌面点
  “Save & Quit”，脚本明确返回不支持，不静默通过。
- selftest 对应检查：`h0_offsets_table` / `h0_statevector` / `h0_watch_*` / `h0_probe_*` /
  `h0_block_ids` / `h0_hook_*` / `h0_spool_*`。

## 输入事件层（G1–G6）

- **键盘（G1）**：`HIDController::onKeyDown(code, InputOrigin::Virtual|Hardware)` 是游戏自己的
  事件入口，会触发 LL `KeyInputEvent`、按键绑定和
  `Keyboard::_states()`。实例路径：
  `ll::service::getAppPlatform() → getPlatformShim() → Bedrock::Platform_GameCore::mHIDController`。
  另有对照路径 `Keyboard::_inputs().push_back(KeyboardAction)`：能改槽位/开背包，
  但**不会**发 LL 事件，所以事件计数必须以 HID 路径为准。
- `key_event {code,down,repeat,repeat_ms,via}` 先进入 128 项的 SPSC 环（管道线程写、tick 线程读），
  在 `safeTickPre` 的 `tickInputInjection()` 里 drain；`via=hid`（默认，Virtual）/
  `hardware`（Hardware 且带 `mInjectingInput`）/`raw`（Hardware 不带注入标记，等价真人按键）/
  `queue`（`Keyboard::_inputs` 直推）。双击空格必须走 Hardware origin（脚本用 `hardware`；
  锁关着时 `raw` 也行，锁一开就被丢），`raw` 平时只用于验证 `input_lock`（见「真实输入锁」）。
  强能力由 `allow_input_inject` 门禁（默认 false），每次注入写 boot log。
- 2026-10-31 输入队列加固：`InjectedEventQueue` 的 128 个槽同时持有事件与应用结果，
  后台消费结果前不复用槽；`input_id` 单调编号，`input_event_done` 明确应用结果。
  单事件局部 SEH/C++ 隔离，注入标记在边界外恢复；故障不重放整批旧事件。
  冷侧准入/消费/取消共用锁，tick 不取该锁、不创建自有字符串/JSON。
  重连/退出改变代次，下一次 tick 先释放跟踪的 held 输入；释放失败保留重试。
- `text_input` 不再覆盖共享字符串：独占 `TextCapture` 持有文本，重叠返回
  `text_input_busy`，2 秒截止；环内文本屏障先等待前序按键进入游戏，再由平台队列调用
  `onTextInput`。调用后跨两个 player tick 边界，后台报告 `text_input_done` 才释放后序键。
  超时/取消同样终结屏障，文本失败时更新输入代次、释放 held 并取消后序旧输入，
  同一连接仍回带编号的 input_cancelled；重连才丢弃旧代次结果。迟到回调不影响新请求。
  queued 回执保持兼容，Python 默认仍返回接受回执；`text_input(..., wait=True)` 按 `input_id` 等完成并传播失败，旧 mod 无
  编号时回退接受回执。完成不证明 UI 接受文字；现场以实际命令的聊天回显验证顺序。
- `screen/key_state` 走独立 `UiCapture`，在客户端平台队列读取，spool 格式化；主菜单
  `screen` 可响应。共享 UI 查询槽有 `ui_query_busy`、2 秒截止、取消和重连隔离，文本
  在途不占用该槽。`screen` 新增 `truncated`；旧返回字段保留。
- 本轮验收：离线 **313/313**，`--input-events-world "超平坦测试"` **51/51**，
  完整现场 **619/619**（无跳过）。native 覆盖 200000 次并发环绕、满队列、顺序屏障、
  异常后继续、代次取消和迟到请求隔离；Python 覆盖完成编号、失败和旧 mod 回退。
  现场验证两个故障后 raw 输入仍受锁拦截，文本 busy 不覆盖、2 秒截止、128 项满队列，
  文本失败后的 Enter 被取消且聊天仍打开，正常文本立即 Enter 有真实命令回显；
  重连释放 held、退出取消、主菜单查询和重进恢复通过。构建/静态/profile 全绿，
  安装/构建/打包 DLL SHA-256 一致：
  `34F743E7C41FD699DE6BFB96F66888DD8428DF7C861F7AB01B28A4205A81622E`。
  113 个既有 crash 文件无新增/修改。尚未现场注入 held 释放失败，也未热卸载或做性能
  对照；SDK 内部不在队列零分配保证内。证据 `out/reliability-input-*`。
- **鼠标（G2）**：`MouseDevice::feed(action, data, x, y, dx, dy, force)` 是 `MouseInputEvent`
  的发布入口；`mouse_event {action,...}` 同样在 tick 里 drain。
  `action=move`(0) + dx 能转向；`left/right` 只更新 `MouseInputEvent` 计数，在 1.26.51.01 上
  **不触发世界攻击**（攻击绑定不在这一层），攻击仍用 `click`/`attack_entity`。
- 后台守卫：`allowTurnDelta()` 放行 `mInjectingLook` 和注入鼠标转向后的 2 tick
  （`mInjectTurnUntilTick`）。注入标志只在调用期间短暂置位，避免整帧放行真实鼠标。
- **sprint 对照（G3，历史）**：`driver/sprint_compare.py` + `scenarios/sprint-compare.json`。
  旧对比里 B（直接 `set_input` 标志）0.2875、C（ctrl+W 事件）0.2806，基线 0.2805；
  说明事件层能复现疾跑。2026-10-28 起默认统一走 Hardware ctrl+W，B 只在 `force=true` 下保留。
- **macro（G4）**：`macro_run {event_level:true}` 按相邻 tick 的按钮边沿走 G1/G2 事件注入，
  不再调 GameMode；旋转仍走 `_applyTurnDelta`。旧路径（默认 `event_level=false`）完全保留，
  文件格式未改。结束/取消时 `releaseMacroInputs()` 释放仍按下的键和鼠标。
  实测三次回放落点误差 0.0000（无真实鼠标干扰时）。
- **世界内 UI（G6）**：`screen` 返回 `ClientInstance::getScreenName()`。实测
  `hud_screen --E--> inventory_screen --Esc--> hud_screen`，`/` 打开 `chat_screen`；
  暂停菜单不响应注入按键，退出暂停仍需桌面点击 Resume。
- 门禁与回归：`allow_input_inject=false` 时 `key_event`/`mouse_event`/`key_state` 回
  `input_inject_disabled`；selftest 覆盖槽位切换、100 次计数一致、鼠标计数/转向、
  背包开关、事件级 macro 确定性与旧路径。

## 健壮性

- tick 前置/后置钩子各自 `try/catch`；异常会清空输入和任务，并限制为每秒最多上报一次
  `internal_error`，不会把异常抛回游戏主循环。
- `handleLine` 外面有 `try/catch`，并且 `dispatchLine` 包在 `__try/__except` 里：
  坏 JSON/类型错误回 `bad_command`，请求级的 AV 回 `internal_error`，管道线程和 tick 都继续。
  C++ 异常通过 `__except` 过滤器放行到外层 `catch`，不会被误当成崩溃。
- 所有 `queueForServerThread` 回调统一走 `queueServerTask`，在服务端线程里捕获异常，
  避免异常穿过游戏的服务端 tick。
- 管道写入按 `WriteFile` 的实际返回值循环，防止字节模式下的半行写入破坏 JSON Lines；
  队列超过 20000 条时丢弃最旧的并计数（`status.queue_dropped`），同时往 spool 写一条
  `queue_dropped` 标记。
- 管道用 `PIPE_REJECT_REMOTE_CLIENTS`，只接受本机客户端。
- `PipeChannel` 独占 pipe/event 句柄，所有 pending I/O 在返回前取消并回收；connect/read/write
  等待每 25 ms 检查停止标志，`disable` 只需 join，不再存在一次性 CancelSynchronousIo 的窗口。
  发送合并成最多 64 KiB 的缓冲批次，大行复用其已有字符串；hello 同样走完整写入与错误处理。
  单次写入预算 5 s，超时断连并记录 `status.pipe_write_timeouts`；客户端可重新连接。
  `CommandFramer` 同时限制完整/分片行，不再每次重扫旧尾行或逐行 erase 前缀。
- 启动/管道状态写到 mod 目录下的 `harness-boot.log`（`load`、`enable`、`pipe: ...`），
  因为 LeviLauncher 的日志位置随启动方式变化。
- Python 侧读取用 `PeekNamedPipe` 轮询而不是阻塞 `read`：Windows 的同步管道句柄会把
  挂起的 `ReadFile` 和 `WriteFile` 串行化，阻塞读会让同一条连接上的发送永远等下去。
  每次请求都有超时（`ModTimeout`），连接关闭抛 `ModDisconnected`，协议错误抛
  `ModProtocolError`；`reconnect()` / `wait_ready()` 用于游戏重启后恢复。

## 单活动任务与会话所有权

任务类型、控制器与固定事件队列在 `TaskModel.h`，游戏计算在 `Tasks.cpp`，JSON 与 use
服务器调度在 `TaskEvents.cpp`。控制器统一拥有活动状态；替换/取消/tick/服务器副作用
共用同步边界。消费任务事件复用 spool 线程，tick 内不再分配任务 JSON/字符串。
终结事件带 task_id，取消后旧服务器回调直接跳过。任务 AV 在锁内被 SEH 转成失败，
`selftest_task_fault` 只在 allow_selftest_stress 开启时可用。

Python 每次连接创建 `_PipeSession`，旧读取线程不能访问新队列。`request` 将非目标事件
保留给 `readline/wait_event`，任务等待按 ID 关联。详细限制与迁移见 `architecture.md`
和 `refactoring.md` 的“行为重设计”。

`Chat.cpp` 将 `GuiData` 的聊天缓存作为只读快照开放。管道通过 `queueClientTask` 提交
一次平台主线程工作（主菜单也执行），客户端线程复制定长文字，spool 线程格式化 JSON。
`ChatModel.h` 为每次请求分配独享快照，Requested → Capturing → Ready 通过 acquire/release
发布；繁忙时明确拒绝。超过 2 秒由 spool 消费端回 `chat_history_timeout` 并释放槽，
退出世界取消请求，断连/重启取消并丢弃旧请求。迟到回调不读写新请求；已在采集中的旧回调
独享存储直到返回，超时消费端不读取其载荷。主菜单直接回 `ok=false/reason=no_player`。
不增加 tick 锁或在 tick 格式化聊天 JSON。消息优先使用过滤后的文字，返回最近最多 64 条，
每条 2047 UTF-8 字节并带 truncated；不返回账号标识。此接口读取游戏保留的缓存，
不保证永久历史，也不把 `command_done` 的即时结果当作 deferred 命令最终效果。

2026-10-31 聊天生命周期验收：native 模型覆盖排队/采集中超时、取消/重启、重复回调、
并发异常发布、迟到完成和弱引用释放；现场 `--chat-lifecycle-world "超平坦测试"` **9/9**，
验证超时终结、`world_left` 取消、无世界 tick 的主菜单两次请求与重进后的正常快照。
默认完整现场套件 **237/237**（无跳过），其中新增 `chat_timeout_terminal/recovery`。

## 实测数字速查（1.26.51.01 创作存档）

从 `mod/README.md` 迁入的逐项实测记录；与上文各专题小节互为补充（上文讲设计，这里记数字与复现要点）。

按主题归档的稳定结论；逐轮验收的执行记录（其中的通过数会随时间过时）见 git 历史
与 [`../docs/refactoring.md`](../docs/refactoring.md)。历史全覆盖基线：live selftest **992/992**
（输入、自检压力及两类封包能力开启）、离线单测 **346/346**。
本批最终姿态/能力/站稳与释放/解锁已回读恢复；此前退出异常根因仍待复核，未宣称已修复。

### 封包采集异常边界（2026-10-31）

- 采集只读取一次 Packet id；getId 与 write/字节复制各有局部 SEH/C++ 边界，
  CallbackGate guard 和 SDK 流的所有权留在 raw SEH frame 外。采集故障不跳过正常封包投递。
- id 读取失败只累加 id_faults；序列化失败保留身份行、累加 byte_faults，并发布相同
  seq/id/dir 的失败字节行：serialized=false、size=0、空 hex 和 reason。只有后台生成 JSON；
  字节复制也在读取边界内，失败不发布部分载荷。重入写入不会清除外层的 TLS 缓冲。
- 自检通过 selftest_packet_tap 在真实 send/recv 观察侧模拟 id/bytes 的 SEH/C++，
  后续正常采集及关闭用于验证错误和回调门已释放。命令需要 allow_selftest_stress 和
  allow_packet_capture；停止清除一次性故障，新采集清零统计。完整自检和
  `driver/selftest.py --packet-tap-only` 都会执行这些检查，不发送违规封包。
- Python decode_row 对失败记录保留 capture_error；断开现场报告同时保存 packet_stats，
  不因 state 探测失败而丢失采集计数。SDK BinaryStream 的可增长字符串分配仍未迁出钩子，
  本批不声称零分配或性能收益；其内部构造/写入中途故障也不能保证可回滚。

### 命令包来源/独占注入与自检同步（2026-10-31）

- 封包真实重连/退出/重进 **55/55**，传送 **45/45**、真实生命周期 **50/50**；完整现场
  **992/992**（无跳过）、离线 **346/346**，构建、ruff check/format、mypy（92 files）、
  profile 一致性通过。朝向检查先要求连续三 tick 匹配，再验证无角度传送；panic 保留
  先到达的领域取消事件，其他错误仍抛出。
- 已保存菜单下验证自检前提失败会停止并落盘报告；验证 recover 在世界中无 force 时拒绝，
  在已保存菜单显式 force 后可重启重进。`wait_for_menu` 使用 ready，缓存列表不代表可进入。
  真实错误页恢复没有现场样本，分类不证明磁盘保存完成，保存门禁失败不会自动强停。
- 临时配置逐字节恢复且重启加载，姿态/能力/背包/站稳、释放/解锁、113 个 crash 无新增/修改
  均回读确认。安装与打包 DLL SHA-256 相同：
  `85BFF73BFC2F82A1D92C8A7ACF6789B94B601463B8FC45CE175BFC5153395628`。
  证据 `out/reliability-rotation-final.json`，本轮日志同前缀；BadPacket 触发包仍 unresolved，
  通用 PacketTap 序列化及其余 SDK 边界继续保留在第 12 项，13–16 未完成。

### tick 内部错误恢复（2026-10-31）

- TickRecovery 使用定长 SPSC 错误队列与独立原子恢复状态；后台清理会话并发布 Prepared，
  tick 释放成功且获得有效 mailbox 快照后才转 Running。旧编号不可清除新故障，
  诊断溢出不影响恢复；消息 255 字节、带截断标记，后台容忍非 UTF-8 消息。
- 冷侧准入锁置于请求 raw SEH 边界之外，tick 不获取；恢复中的控制命令返回
  tick_recovering，status.tick_recovery 可观察。外层只处理 C++，SDK 局部 SEH 仍逐项治理。
- 原生覆盖 10 万轮并发、旧编号/释放/有效快照/溢出与零自有分配；离线 **318/318**，
  完整/最终构建与静态/profile 通过；`--tick-recovery-only` **51/51**、实际重连/退出/
  重进专项 **57/57**、完整现场 **919/919**（无跳过）。前/后 tick 各类 C++ 消息、
  释放重试、不重放、两类宏/任务终止与新请求、dispatch SEH 后释放准入锁均通过。
  最终姿态/能力/站稳、释放/解锁/恢复状态与三份 DLL 哈希一致，无新增/修改 crash。
  首轮 GBK 日志中断及完整哈希见 reliability-repairs.md；未做性能对照或现场热卸载。

### 移动/视角与传送（2026-10-31）

- `--controls-only` **126/126**；传送专项 **44/44**，`--teleport-world "超平坦测试"`
  **49/49**；完整 **868/868**（无跳过），离线 **317/317**。调用前/后 SEH/C++、
  不可用返回、实际绑定/游戏状态释放、停止后不重放、任务与两类宏失败及恢复通过。
  传送覆盖 busy/超时/队列失败/过期身份、部分副作用、效果与朝向、唯一回执和真实生命周期。
- 完整构建、ruff check/format、mypy（90 files）、profile、三份 DLL 哈希一致；113 个既有
  crash 文件无新增/修改。最终恢复通过，未做性能对照/现场热卸载；封包注入/内部错误
  上报及其余 SDK 边界仍属第 12 项。验收日志与哈希见 `reliability-repairs.md`。

### 玩家身份快照（2026-10-31）

- `--player-identity-world "超平坦测试"` **41/41**，完整现场 **698/698**（无跳过），
  离线 **315/315**；native 模型专项 **18/18**。设计、故障范围和退出收尾限制见“物品栏”。

### 飞行请求与重断言（2026-10-31）

- `--flight-world "超平坦测试"` **50/50**，完整现场 **663/663**（无跳过），离线
  **314/314**。覆盖空中飞行/关闭、MayFly 显式开关/反转及 12 tick 后保持，两阶段
  SEH/C++/超时的唯一失败与恢复，重断言故障、busy/坏类型保留请求、旁观拒绝，以及
  真实重连/退出/重进、主菜单拒绝与能力恢复。SDK 调用已开始后不能抢占或回滚。
- native 验证恰好 8 次预算、取消/替换后的迟到发布拒绝、旧失败不清新请求、失败队列
  满与零自有分配。首轮地面 Flying 前提失败，三次空中诊断通过；传送后先用 settle
  确认 on_ground=false 和高度，再验证开启。没有强制覆盖正常落地后的 Flying 状态。
- 最终构建/静态/profile 与安装哈希一致，113 个既有 crash 文件无新增/修改；玩家站稳、
  输入解锁、任务/两种状态录制/宏停止。日志 `out/reliability-flight-*`；尚未现场热卸载
  或做性能对照，不能把重断言基础设施的零自有分配推广为所有 SDK 调用零分配。

### Python 连接资源生命周期（2026-11-01）

`connection.py` 将请求订阅 `_PipeSession` 与后台 I/O `_PipeStream` 分开。
读线程和 `weakref.finalize` 仅持后者，避免订阅回调捕获 client 时形成后台线程可达的
永久循环。显式 close、GC、读线程 EOF/队列溢出共用资源释放；短锁只负责取走句柄/线程，
关句柄和有界 join 在锁外执行。启动/握手异常（含 KeyboardInterrupt、坏 protocol）
也释放当前代次。轮询 PeekNamedPipe、协议 4 和公开 Python API 保持不变。

离线 transport 回归 **46/46**，包括真实 Windows 管道的四代遗弃（交替有/无订阅循环）、
32 代竞争请求、EOF/溢出主动释放、并发关闭、迟到旧读者与启动/握手失败。
现场入口为 `py -3.11 driver/selftest.py --connection-only --require-complete`；
本批最终数字与日志见 `reliability-repairs.md`「Python 连接遗弃回收」。
显式 `with`/`finally close` 仍是首选；GC 不释放仍有业务引用的 client。
此处没有修改 C++/DLL，不以这项验收排除 mod 侧其他挂起或 BadPacket。

### 断开后导出路径与回执（2026-11-01 22:20 后续）

`capture_disconnect_report` 先 resolve 输出目录。飞行专项中的 inbound_dump 也用绝对路径，
并通过 request 等待专用回执，避免游戏在另一个 cwd 写相对 out 路径失败，或让导出 error
串到后面的 packet_drain。`--diagnostic-export-only --require-complete` 只导出现有环，
不武装钩子，现场 **3/3**；相关离线 **24/24**。
最终全量离线 **377/377**，ruff check/format、mypy（93文件）、profiles up to date、
roles56/63通过。日志 `out/diagnostic-export-{offline,tests,live}.log`。

同轮第14项候选现场 **28/29，1 fail**：packet187/size48/error104 后90/Block，
因此候选源码和安装均撤回，未提交。最终恢复回读 **11/11**，121个crash无新增/修改。
证据、未完成范围与逆向交接见 `notes/badpacket-187-20261101.md`，
不能把候选的376/376离线或构建通过表述为现场验收通过。

### 客户端 I/O（2026-10-28；离线）

- `protocol.LineDecoder` 只扫描新收到的字节，每次 feed 完成后统一压缩缓冲，
  移除逐行的额外 `bytes` 拷贝；UTF-8 替换、CRLF、空行及 pending 的行为不变。
- `RecordingCommands.record` 用现有 `wait_event` 保留异步通知，避免将同一条延后事件
  反复读回；移动发送或 sample 回调失败时 best-effort 发 stop 后保留原异常。
  整次录制/分页共用 `exchange`，防止其他请求交错消费响应。
- `record_dump_all` 按首个 head 限定后续分页大小；非空页游标不前进时抛
  `ModProtocolError`（仍属 `ModPipeError`），空页终止，不无限追赶正在增长的 head。
- `spool` 两个入口复用逐行解析；非法 dropped 计数、超长整数、坏 JSON 和过深 JSON
  计入 bad_lines 并继续处理。基于已打开文件的 fstat 限定读取窗口，成功读取后才提交
  offset/rotation；临时 I/O 失败保留状态供重试，轮转为空时清除 pending。
  `read_spool(max_bytes=0)` 返回空，负数报 ValueError；poll 单次仍最多读取 4 MiB。
- `py -3.11 driver/selftest.py --client-io-only`：**20/20**；此入口只跑本地回归夹具，
  不连接游戏。全量离线单测 **220/220**，ruff lint/format、mypy（79 文件）全绿。
  完整 live selftest 本轮未运行；没有构建/替换 mod 或执行逆向操作。

解码微基准：Windows / Python 3.11.9，对比提交 `27cb2d3`，9 轮中位数；随机种子
20261028 的 1000 组字节流输出与 pending 全部一致。以下仅表示 Python 解码吞吐，
不代表游戏 tick 性能提升。

| 输入 | 改前 | 改后 | 吞吐倍率 |
| --- | ---: | ---: | ---: |
| 1 MiB 长行，256 B 分片 | 52.091 ms | 2.003 ms | 26.00x |
| 20,000 行，一批输入 | 10.476 ms | 7.782 ms | 1.35x |
| 20,000 行，4 KiB 分片 | 10.406 ms | 7.951 ms | 1.31x |

本地基准脚本与日志：`out/bench-client-io.py`、`out/optimization-client-io-bench.log`
（忽略的运行产物）；可复现的功能边界已保留在上述 selftest 入口和 tests 中。

### Mod 核心（2026-10-28；离线与现场）

基于 `bc4e70d` 审阅和修改现有 C++ 源码，未执行逆向操作或打开 IDA；离线验收后保存退出世界、
备份旧 mod 与配置，安装新 DLL 并重启进入“超平坦测试”补做限定范围的现场自检。
新增 `PipeChannel`、`TransportModel`、`RecordModel` 三个不依赖 SDK 的内部头，分别负责
I/O 资源生命周期、线协议缓冲、录制并发模型；仅相应领域 TU 引用，`Internal.h` 保留公共入口。
wire 协议仍为 4，记录仍为 64 B；状态仅新增管道超时计数/预算。

复现入口：`py -3.11 driver/selftest.py --mod-core-only`，**3/3** native 集合：

- 分帧覆盖所有分块大小、UTF-8 字节和 CRLF、空行、恰好上限/超过上限、连续丢弃、
  同块恢复与连接重置；批量发送检查顺序、大行、空行、失败短路及复用缓冲清理。
- Windows 真命名管道传输 256 KiB；取消未连接等待、pending read、pending write；
  非读取客户端在 40 ms 测试预算下超时；取消/超时路径断言在 1.5 s 内返回。
  重连后可再次读写，100 次 open/析构前后进程句柄数一致。
- 录制检查降采样、period 边界、停止/初始化失败/异常展开、采样零分配；
  阻塞中的 start 等待旧 capture 退出后 head=0；100,000 次写入期间并发覆写读取，
  以及 **2500 次**会话重启均未读到撕裂字段或上一会话的数据。

LLVM22 / `xmake -j4 -y` 完整 mod 编译链接通过；离线单测 **223/223**，ruff lint/format、
mypy（80 文件）通过。此处的 native 测试使用生产模型和真实 Windows pipe API，
不连接 Minecraft；完整 live selftest **222/222** 仍是既有现场基线，本轮未重新验证。

现场复现入口：`py -3.11 driver/selftest.py --mod-core-live-only`，**33/33** 通过：

- 协议 4、加载的 5000 ms 写预算、状态查询、未知命令兼容；一批 64 个 ping、逐字节 UTF-8/CRLF，
  完整/分片超长行均只报一次 `line_too_long`，下一行 ping 正常。
- legacy JSON 录制 8 行；二进制 period=1/3 分别录到 19/7 行，JSON/hex 逐条相同，
  分页导出一致，停止后 head 不再变化；连续 12 次重启无上一会话样本。
- 74 行录制作为固定输出，原始管道客户端发送 64 次 dump 且不读响应，保持句柄打开 6.5 s；
  写超时计数 **0→1**，旧句柄尚未关闭时已可重连，tick 继续推进、世界状态可读。
- spool 无损坏 JSON 或 `internal_error`，另做 3 次重连均可 ping。没有调用内存/watch/hook、
  故障注入或移动命令，也未改方块、背包或生成实体；未启用额外 capability。

安装 DLL 与构建产物 SHA256 一致：
`5124d90c8e9a6c44fb140eae11f6c242b18e91256b2cbebcf8aeea898b75ee4f`。
备份在忽略的 `out/mod-before-live-20261028-213450/`；原 `harness.json` 保留。

传输微基准（Windows / clang++22 `-O2`，9 轮中位数，与原分帧算法比较）：
512 KiB 长行按 256 B 分片为 **8.838 → 0.537 ms（16.45x）**；
20,000 行按 4 KiB 分片为 **1.359 → 0.329 ms（4.13x）**。
同样 760,000 B / 20,000 行，批量写回调从 **20,000 次降至 12 次**，字节数与顺序一致。
回调次数用于衡量批量策略，不是实际系统调用或游戏帧率测量；部分写入可能增加实际调用次数。

验证日志在忽略的 `out/`：`mod-opt-native-selftest.log`、`mod-opt-tests-accepted.log`、
`mod-opt-build-accepted.log`、`mod-opt-bench.log`；微基准源码为 `out/mod-core-bench.cpp`。
后续现场日志为 `mod-core-live-selftest.log`，最终离线复核为 `mod-core-final-unittest.log`。
增加约 4 MiB 发布序号是为并发覆写安全付出的固定内存成本，未改变环容量或 wire 布局。

### 实验辅助（`driver/harness/lab.py`）

- `Builder.build`：命令 → 回读 → 不一致重试；`poke` 用邻块更新唤醒命令放置的液体。
- `RecordSession`：每个 session 从 `since=0` dump（`record_start` 会重置环形头）。
- `move_and_wait`：`set_input` 立即返回，等满输入窗口再返回。
- 纯函数 `fit_recursion` / `recursion` / `steady` / `segments` 有离线单测（`tests/test_lab.py`）。

### 起跳方式（2026-10-28 更新）

- 默认：`jump` / `set --jump` / 任务 / 宏回放都按当前 jump 绑定（默认 Space）发
  Hardware HID 按键；`jump_motion_matches_space` 逐位对比通过。
- `input_cfg jump_impulse` 默认 0；非零（1=jumpFromGround，2=setJumpPendingScale，
  3=两者）是 legacy 直接冲量，只在 `force=true` 下用于复现旧测量
  （selftest `jump_impulse_guard_*` / `legacy_input_force`）。

### 游泳/热键驱动（2026-10-24）

- `lab.swim_drive`：HID 前进+疾跑键 → 轮询 ADF bit57（`fnv1a("ActorDataFlagComponent")`，
  30 B 内位 57）；深水离底 4–5 tick 进入，松键即退出。
- `lab.jump_key`：拍空格（HID Space）。旧版曾与 legacy `jumpFromGround` 弧线比较；
  现行 `jump` 自身也走 Hardware HID，具体以「起跳方式」小节的当前契约为准。
- selftest `swim_drive_entered` / `swim_drive_dive`（俯冲 $v_y<-0.15$）/ `swim_stop_clears`。

### 真实输入锁（input_lock，2026-10-24）

- 三个拦截点：`HIDControllerGameCoreDesktop::$onKeyDown`（`InputOrigin::Hardware` 且
  **不带** `mInjectingInput` 时丢弃）、`MouseDevice::feed`（`mInjectingInput` 之外一律丢弃）、
  `allowTurnDelta`（锁定时非注入转向返回 false）。
- `key_event` 的四条注入路径与锁的关系：`hid`=Virtual（永远放行）、`hardware`=Hardware +
  注入标记（放行，双击空格必须用它）、`raw`=Hardware 无注入标记（被丢弃，等价真人按键，
  只用于验证锁）、`queue`=`Keyboard::_inputs` 直推（不经过这个钩子）。
- 配置 `lock_input`（默认 false）；命令 `input_lock {on}`；selftest 自动上锁并在 finally 解锁。
- 验证：`input_lock_on` / `input_lock_blocks_hardware`（`via=raw` 数字键不改槽位）/
  `input_lock_allows_injection`（Virtual 键改槽位）/ `input_lock_allows_mouse_injection`。
- （2026-10-24 已修）曾偶发在 tick 线程 `applyEcs` 枚举 registry 时崩溃，见下节「ECS 线程归属」。

### 双击空格飞行与 `/togglefly`（2026-10-25）

- 能力门是 `AbilitiesIndex::MayFly`；生存/创造/冒险都能被 `/togglefly` 或管道 `togglefly`
  打开（三模式实测），旁观模式拒绝（`spectator_not_supported` / `success=false`）。
- `CommandFlagValue::NotCheat` 生效：把世界的 `commands_enabled` 写成 false 后，聊天框输入
  `/togglefly` 仍然执行（`state.mayfly` 翻转，聊天回显正常），selftest `togglefly_not_cheat`。
- 开关能长期保持（创造关掉后 200 tick 仍为 false，生存打开后 200 tick 仍为 true），但
  **切换游戏模式会重置成该模式默认值**（生存/冒险 false、创造 true）：生存开启 →
  切创造（仍 true）→ 切回生存（变 false）。所以模式切换后要重新开。
- 切换只认 `InputOrigin::Hardware`：`via=hid`（Virtual）只起跳、从不翻转 `flying`；
  每次按下必须跨至少 1 tick（同 tick 内 down+up 被丢掉）。实测 hold=1/2 × gap=1/2/3 全部生效，
  hold=3 开始出现不生效样本，所以 `lab.double_space_fly` 默认 hold=1、gap=2、最多重试 3 次。
- 游戏内命令注册时机：客户端 mod 在 `enable()`（主界面阶段）时服务器命令表还不存在，
  直接 `getOrCreateCommand` 会 `0xC0000005` 崩在启动阶段（crash dump `tick=0`）。
  正确位置是 `ServerCommandRegisterEvent`；命令表按世界重建，用 `CommandRegistry*` 身份判重，
  避免同一张表重复挂 execute（重复执行会互相抵消）。
- 只注册服务端侧：能力是服务端权威，客户端侧命令会把客户端副本翻转后被同步覆盖；
  玩家在聊天框输入的 `/togglefly` 仍然会作为 `CommandRequestPacket` 到达服务端（实测命中）。
- 管道侧 `togglefly` 已在 2026-10-31 迁入 `FlightCapture`：客户端平台队列写 LocalPlayer，
  服务端队列按 UUID（完整玩家名兜底）镜像，两侧完成后由 spool 格式化原成功事件。
  仍保留 8 tick 重断言（模式切换后迟到的能力同步会覆盖单次写入，2026-10-25 实测）；
  tick 只做受局部 SEH/C++ 保护的能力写入和定长失败发布，不分配请求或生成 JSON。
  `fly/togglefly` 共用 2 秒截止的独占槽；忙时拒绝覆盖，退出取消，重连丢弃未入队旧结果。
  原子请求编号准入阻止已取消回调复活重断言，旧失败不能清除新请求；一次失败停止本次
  重断言。成功事件不等待完整 8 tick，之后的失败单独报告 `flight_assertion_failed`。
  已开始的调用不支持超时抢占；失败保守标记 partial，副作用不回滚。

### ECS 线程归属（2026-10-24 修复）

- 根因：`findActorByRuntimeId` 走 `bedrock::getLevel()` 的 actor 列表（单机里是服务端），
  而 `applyEcs` 在客户端 tick（`safeTickPost`→`runCaptureRequests`）里枚举该 registry；
  服务端线程并发增删组件/实体 ⇒ `storage()` 迭代时 AV（崩溃栈：`applyEcs+0x129C` [Ecs.cpp:182]）。
- 修法：runtime-id 的 `Entity`/`Components`/`Component` 三类请求改由 `queueServerTask` 在服务端线程
  执行（tick 之间 registry 静止）；`runEcsGuarded` 用无析构对象的 POD 包装 `__try/__except`
  兜底，结构化异常回 `internal_error, phase=ecs`。
- `Registries`/`Context` 仍走客户端 tick（客户端 context，同线程；Context 本就有 SEH）。
- 验证：`ecs_entity`（115 组件）/`ecs_components`（115，94 有尺寸）/`ecs_component`（StateVector 36B）
  全部通过；selftest 221/221。

### 行为契约与加固

- 任务生命周期：单活动槽、ID 关联、统一取消；`TaskModel.h` 定长状态、tick 只填
  `TaskStep`、spool 线程格式化事件。自检入口 `check_task_lifecycle`；受控故障入口
  `selftest_task_fault` 需要 `allow_selftest_stress`。
- `use` 服务端等待：进入 `server_pending` 时在任务锁内记录 5 秒截止时间；spool 线程按
  wall-clock 超时回 `item_used {ok:false,reason:"server_timeout",task_id}`，即使客户端 tick
  暂停也能释放任务槽。服务端回调取得锁后采样当前时间，禁止迟到的物品副作用；已经执行
  的游戏调用不被强行抢占。投递失败与回调异常通过拥有者失败回调终结同一任务，旧 ID 不能
  改写后继任务。`selftest_use_failure`（需 `allow_selftest_stress`）覆盖丢失回调、投递失败、
  外层 SEH/C++ 异常及锁内物品操作 SEH；专项入口 `driver/selftest.py --use-lifecycle-only`。
  请求/采样/客户端/服务端 SEH 边界共享嵌套安全的线程局部归属标记，first-chance VEH
  不为已捕获故障写 crash 文件。原生模型覆盖截止边界、锁等待、迟到拒绝、取消/替换/失败
  竞争的唯一终结和超时路径零分配。2026-10-31 专项现场 **16/16**、完整现场 **253/253**
  （无跳过）、离线全量 **282/282**；完整构建及安装 DLL SHA-256 一致性检查通过。
  原始日志在 `out/reliability-use-{unit,live,full-live,build,restart}.log`。
- Python 会话：旧读线程与新会话隔离；请求保留异步事件，缓冲有界并显式报溢出。
- `chat_history`：客户端线程定长快照（最多 64 条、每条 2047 UTF-8 字节、截断显式
  标记），后台格式化。不是永久日志，重启或游戏清理的历史无法追回。
- `navigate`/`route` 共用 `walkToward` 驱动（转向/接近减速/卡住检测/跳跃冷却），route
  的边沿前瞻可按 waypoint 配 `edge_lookahead`；小容差下不再绕圈。
- `state.effects` 仅报告实际效果，过滤游戏清除后留下的 NO_EFFECT 空槽。
- 数值入参统一有限性 + 范围校验：`look`/`teleport`/`reset`/`click`/`move`/`set` 越界回
  `bad_param`，`blocks`/`scan`/`place` 的坐标超界回 `bad_coord` 或跳过该条；
  `wrapDegrees` 另有非有限兜底。
- `ecs_context` 对外部指针先 `readableRange` 预检、访问链整体 SEH；坏地址回 `bad_addr`
  而不是崩游戏。
- 宏安全：回放中 `macro_load` 先停止并等待在途帧退出（回执带 `stopped`），再更换帧；
  `macro_stop` 关闭准入并请求下个 tick 释放输入。新一轮按新宏尺寸校验范围。
- Watch：VEH 无锁读 armed 线程表；挂起窗口内不做任何分配；`thread="all"` 回
  `thread_all_unsupported`；`rw` 观察点不要求目标页可写。
- spool 打开/写失败回灌重试并计数；spool 线程整体异常隔离；`hook_va` 安装留 bootLog
  审计；seqlock 读侧有重试上限（写者卡死时降级为旧快照）。
- 实体链路：查找/快照/攻击全部在服务端线程执行（攻击用服务端玩家的 GameMode），
  客户端 tick 线程不遍历 `getRuntimeActorList()`。

### 移动、跳跃与输入（历史 legacy 输入实测）

本节保留早期输入路径的原始数字，不是当前默认契约。默认现为 Hardware HID 按键沿；
直接冲量/标志路径仅在 input_cfg force=true 下可作旧结果对照。下述前 3 tick 不动与
补调 jumpFromGround 的结论不适用于现行 HID 路径，禁止据此恢复默认直接冲量。
- 步行注入峰值 **0.2159 格/tick**（原版 4.317 m/s）；`example.json` 60 tick 位移
  12.04 格、接地 100%
- 跳跃：起跳沿调 `LocalPlayer::jumpFromGround`，逐 tick 位移 0.4200、0.3332、0.2481、
  0.1648…，顶点 1.252 格、滞空 11 tick；按住且接地时补调 `jumpFromGround`，连续 4 跳
  与 HID 空格绑定的逐 tick 轨迹误差 < 0.0001 格（不补调会出现前 3 tick 不动、
  第 4 tick 一次性 ~0.99 格的“卡回弹”）。
- 潜行：20 tick 前进 4.316 格，潜行时 1.295 格（约 0.30x）
- `fly` 同时写服务端，传送后不会自己恢复飞行
- `click left` 破坏、`click right` 放置；`select_slot` 3→空手、0→草方块
- 服务端传送 2 秒后位置仍是目标值，`yaw/pitch` 生效
- `command` 的 `fill`/`setblock` 生效，`blocks` 能读回
- macro 播放/录制；真实鼠标左右键录进 macro（左键 4 tick、右键 3 tick 为 true）
- `driver/run_mod.py` 跑通 `setup-platform.json` 和 `example.json`

### 闭环任务、世界与场景

- `navigate`：约 5.2 格直线，27 tick 到达，误差 0.26 格
- `mine`：`setblock` 放石头后 1 tick 挖掉（创造模式），`blocks` 读回空气
- `place`：放回同一格，3 tick 完成，回读为石头
- `use`：末影珍珠服务端层数 2→1、客户端背包同步为 1，并触发约 10 格传送
- `worlds` 返回存档列表；`enter_world` 从主菜单直接进"超平坦测试"（intent 0），
  `leave_world` 回到主菜单
- `harness.json` 的 `auto_enter` 在直接启动 exe 后自动进世界
- `options_set` 修改 `gfx_gamma` 实时生效（画面亮度变化），并写回 `options.txt`
- `driver/dev.py run --world ... --build` 一条命令完成编译、重启、进世界
- tick 事件带 `jump_edge` / `in_air` / `block_below`；`reset` 清零速度并让两次重试落点
  误差 < 1e-4 格
- 后台鼠标守卫：游戏不在前台时 `yaw/pitch` 不被鼠标改变，光标保持可见；mod 自己的
  `look` 仍生效
- `driver/parkour.py`：跳跃分段、checkpoint、两次运行对比、`attempts` 2/2 成功
- spool：录制中 `taskkill /F` 掉游戏，spool 里仍有崩溃前最后一条 tick 记录（读取端
  最后收到 57 tick，spool 写到 62，未丢 tick）
- 崩溃取证：`selftest_crash`（需 `allow_crash_test`）生成 `crash-<pid>-<tick>.dmp` 和
  `crash-<pid>.txt`，后者含异常码/地址/线程/最后命令/tick/位置/spool 路径
- 请求隔离：`selftest_av` 回 `internal_error`，随后 tick 继续增长
- 二进制记录环：连续 **10 分钟**写入 **12,082** 条，`record_dump` 的 JSONL 与 hex 解码数
  都等于 `head`（`lost=0`），`queue_dropped=0`；环固定 32 MB，RSS 在系统 trim 后由
  316 MB 回填到 369 MB（游戏自身内存，环不增长）

### 观察点、hook、ECS 与版本 profile

- `hook_install` 按符号 `MobJump::setJumpPendingScale` 装钩成功；`hook_call` 触发后
  `hook_drain` 读到 `args[0]=EntityContext*`、`b_float=1.0`、返回地址和 4 帧栈；
  `hook_rm` 后 `hook_list` 为空。VA dry_run 对同一地址返回 `executable=true`，
  对 `0x10` 返回 `false`
- `diagnose`：4 个锚点（normalTick/_applyTurnDelta/getHealth/isOnGround）24 字节全部匹配，
  `build_id=1.26.51.1`、`version_ok=true`
- 版本 profile（移植）：`GeneratedProfiles.h` 由 `docs/offsets/*.json` 生成，C++ 里不再有
  版本号、签名、结构偏移和组件 id。实测 `profile.match=exact`、`anchors_matched=4/4`、
  `struct_fields=12`、`components=1`；`profile_probe 1.26.51.7` 选中同一 profile（`prefix`），
  `9.9.9.9` 回 `none`。`profile_dump` 导出的 `profile` 可直接保存为新的 offsets JSON。
  `allow_version_mismatch=true` 时 `hooks_allowed=true`（`compat=true`），默认关闭。
- 负路径：把其中一个内置签名字节改掉后重启 → `version_ok=false`，
  `version_failures=["LocalPlayer::normalTick"]`，`hook_install`/`record_start`/`watch_set`
  全部回 `version_mismatch`，游戏仍存活；改回后 4 个锚点恢复匹配
- `overhead`（空闲，1024 样本）：`hook_p50=10 µs`、`hook_p99=38 µs`、`tick_p50=50.006 ms`、
  `tick_p99=50.970 ms`、20.0 Hz；同一场景改前 `tick_p99=54.596 ms`、`hook_p99=30 µs`
  （tick p99 未劣化）。注入 1 ms busy ×20 tick 后 `hook_p99=1013 µs`、`hook_max=1040 µs`
- 请求块合并成 seqlock 后，tick 读路径不再取 `mInput/mLook/mTeleport` 三个 mutex；
  管道线程仍是单写者（`mRequestSeq` 奇偶校验）
- `line_too_long`：发 2 MiB 无换行 → 收到 `error/line_too_long`（1 次），补一个换行后
  `ping` 仍正常，进程不增长
- `panic all`：先装 1 个 hook + 1 个 watch，`panic` 后两者列表都为空、任务空闲、
  tick 继续增长
- `ecs_entity`/`ecs_components`：猪实体读到 **113** 个组件（77 个能推断 stride），其中一个
  `stride=36`（StateVector 大小），`ecs_component` 按 36 字节读出 hex；客户端 registry
  有 512 个组件 storage
- `state.effects`：`/effect @s speed 10 2` 后读到 `id=1, amplifier=2`
- 存档点：`checkpoint_save` → 传送走 → `checkpoint_load` 落点误差 **0.000** 格
- `scan` 3×3×4 区域返回 6 个非空气方块
- `player_landed`：抬升 5 格后落地，`fall_distance=4.92`，血量 20→18（摔落伤害）
- `probe kind=block/legacy` 对同一坐标返回的名字与 `blocks` 一致；`legacy.hash` 等于
  名字的 FNV-1-64
- `watch_set` 观察 tick 计数（size=8, mode=w, thread=tick）：20 tick 捕获 20 次，
  命中 tid 等于 tick 线程；`watch_drain` 每行带 `code_hex`（32 位 hex），`rip` 是下一条指令
- offsets 表（F5）：`diagnose.structs` 列出 6 个字段/1 个组件；把 `offsets.json` 里
  `BlockLegacy.BlockId` 改成 `0x17C` 后 `probe legacy.block_id` 从 1 变成 0，改回即恢复；
  设成 `null` 后 `probe legacy` 回 `error/no_offset`，`probe block` 不受影响
- spool（F6）：每次启动使用 `spool-<pid>.jsonl`；若同 PID 的旧文件非空则改名为
  `spool-<pid>-<n>.jsonl`，单文件超过 64 MiB 也会轮转；swap+write 由同一把锁串行化
- 记录环（F8）：方块采样走数字 id + 256 项哈希缓存，稳态零 `std::string` 分配；
  y 超出 [-64,319] 时不读方块、三个 block 字段为 0 并置 `flags & 0x800`
- `overhead`（F9）：新增 `tick_interval_p50/p99/max_us`（与旧 `tick_*` 同值）、
  `window_samples=1024`、`qpc_resolution_ns=100`、`consistent=true`

### 输入注入对照（G1–G4）

- G1：`key_event(2)` 走 HID `onKeyDown(Virtual)` → `selected_slot` 1→2，LL `KeyInputEvent` +1；
  100 次注入事件计数恰好 +100、按下状态可读回；E → `inventory_screen`，Esc → `hud_screen`
- G2：左键 down+up → `mouse.counts[1]` +2、`last_action=1`；`move dx=200` → yaw +25.99°；
  左键没有触发世界攻击（攻击绑定不在此层）
- G3：`driver/sprint_compare.py` 三组对照 B=0.2875、C=0.2806 格/tick（基线 0.2805），
  结论：事件层注入能复现疾跑，1.3x 差异不在输入事件层，G5 不启动
- G4：`macro_run event_level=true` 三次回放落点误差 **0.0000**；左右键事件各 +2；
  旧路径（`event_level=false`）仍走 GameMode，MouseInputEvent 计数为 0

### 封包层（H0–H4）

- H0.1：对 StateVector.vy（offsets 表 `VelY=0x1C`）设写观察点，原地单跳后采样 25 tick；
  唯一写者是**集成服务端线程**（`thread="both"`），`code_hex` 反汇编为
  `mov qword ptr [rbx + 0x18], rax`（覆盖 0x1C）
- H0.2：同一坐标 `blocks` / `probe block` / `probe legacy` 名字与 FNV-1-64 一致；
  51 版方块 id：air=0、grass_block=2、water=9
- H0.3：hook 捕获的 `EntityContext*` == `state.context`；`ecs_context` 从两个来源解析到
  同一个 StateVector 地址、count=2、size=36
- H0.4：`driver/spool_forensics.py panic|taskkill|verify`；panic 与 taskkill 都单调连续
  （taskkill 丢失 0 tick）；leave_world 需桌面点击，脚本明确返回不支持
- H1 阶段 A：开启后 3.5 秒抓到 **122** 条客户端→服务端包，`seq` 0..121 无丢失，
  tid 固定 11684；主序列 `PlayerAuthInput(144)` + `SubChunkRequest(175)`，
  另有 Interact(33)、InventoryTransaction(30)、PlayerSkin(93) 等。`denied` 计数 >0，
  `packet_capture(ids=[1])` 回 `denied_id`
- H1 阶段 A 的字节层结论：`LocalNetworkPeer::$sendPacket` 在本构建里是 dllimport-only，
  链接不到导出 thunk ⇒ 不能按符号在 NetworkPeer 层拿序列化字节（`peer_hooked=false`）；
  字节若要拿，只能走服务端接收侧（B2）或白名单序列化（B3）
- H3 预演：B（`set_input` sprint）与 C（ctrl+W 事件）各 3 秒的 id 序列几乎相同
  （B: 144×65/175×3，C: 144×66/175×5）⇒ 身份层没有差异；结合 G3 的速度结论，不需要 packet tap 定案
- H1-B 地址解析：`BatchedNetworkPeer::$sendPacket` = `Minecraft.Windows.exe+0x46EB60`
  （无 thunk）；`LoopbackPacketSender::$sendToServer` 锚点 `+0xD809E0`，跟随 3 跳落到
  `dmf.dll+0x8AC10`（说明已被 dmf/LL 链式 hook）。`LocalNetworkPeer` 仍只有导入声明。
- H1-B 决策表（走/跳/命令）：`sendToServer` 有流量；`BatchedNetworkPeer::sendPacket`
  与 `NetworkConnection::receivePacket` 均为 **0** ⇒ 单机回环绕过字节层，走 B3。
- H1-B B3：`packet_capture {serialize_ids:[144]}` 后 `packet_bytes` 拿到
  `PlayerAuthInput` 的 91–93 字节，`seq` 与身份记录一一对应（32/32 匹配）；tick 19.7–20.0，
  `dropped=0`。序列化只在白名单 id 上发生，关闭 capture 时零调用。
- H2（历史初验）：旧 `chat_send` 复制 `CommandRequestPacket(77)` payload；现改为定长来源模板
  和客户端队列独占请求，见「命令包注入（H2）」。历史实测 `/setblock` 回读为 `gold_block`、`/tp @s ~1 ~ ~`
  位移正好 1.000 格、`/fill`/`/give` 生效；注入前后 tick p99 不变（19.8 Hz）。
  捕获模板需要先用 `/` + `text_input` + Enter 发一条真实命令；客户端命令还要求
  `op_self`（世界 commands 已开启时仍可能是 Member 权限）。
- H1-C 接收侧 tap：`directions:["send","recv"]` 时额外装 9 个钩子。单机里流量全部来自
  `LoopbackPacketSender::$sendToClient`（3.5 秒 66 hit）和 `...sendToClient(user)`（4 hit），
  `Packet::sendTo*` 辅助层实测 **0**。接收侧主序列 `MoveDeltaActor(111)`、`AddActor(13)`、
  `LegacyTelemetryEvent(65)`；`/say`/`/tp` 的 `Text(9)` payload 里能直接读到 JSON 原文。
  同一 tick 内同一 `Packet` 对象的重复投递会去重，per-anchor 计数仍保留原始次数。
- H4：`driver/harness/packets.py` 提供 `decode_packet(id, hex)` / `decode_row(row)`；
  id→名字表由 `MinecraftPacketIds.h` 全量生成（256 项，1.26.51.01），已实现 77/36/19
  三种布局，未知 id 保留 hex 不报错；CLI `packet-decode --id 77 <hex>`。
  77 的布局用真实抓包验证，36/19 用合成样例单测。
  `driver/packet_dump.py --seconds 5 --walk [--directions send,recv]` 导出
  `out/packets-<ts>.jsonl`（实测 105 行 send、54 行 recv）。

### 违规守望（`ViolationWatch`，2026-10-31 后续）

- 位置：`ClientNetworkHandler::handlePacketViolation`（角色 `@client_handle_packet_violation`）。
  这是客户端里**唯一**以 `DisconnectFailReason::BadPacket(90)` 关闭连接的地方，且只在
  `response == PacketViolationResponse::TerminateConnection(3)` 时发生。
- **ABI 前提**：该函数 `this` + 9 参（4 个寄存器、6 个栈参数），所以不能用仓库的通用
  四寄存器 VA detour；`ViolationWatch.cpp` 用同签名 typed wrapper，
  记录后以原参数调用原实现，返回语义与异常传播保持不变。
- **地址重定位**：roles 里是 `image_base=0x140000000` 下的静态 VA；本机 exe 被 PeEditor
  改写首选基址并 ASLR，`OptionalHeader.ImageBase` 在内存里等于加载地址，按头部换算会
  `not_mapped`。运行期地址 = `diag.module_base + (role_va - image_base)`；
  实测 RVA `0x12FA0F0` 处为 `55 41 57 41 56 41 55 41 54 56 57 53 48 81 ec 38`（标准序言）。
- 记录：`seq/tick/tid/response/packet_id/packet_size/error_value/category/context`，
  256 行预分配环；`context` ≤47 B + `context_truncated`，输出时非可打印字符转 `.`；
  回调内零分配 / 零 JSON / 零新增 mutex。
- 后续新增屏幕路由观察：同一开关通过导出符号观察
  `OreUI::EntryPoints::DisconnectScreen::composeRoute`，统计增加 `screen_hooked`，记录
  增加 `source/detail/detail_truncated`。`source=callback` 的 `response` 是违规处理等级；
  `source=screen` 的 `response` 是路由原因码，`context` 是码字，不能混用两种语义。
  `detail` 同样最多 **47 字节**，非 ASCII 被替换为 `.`，不能视为完整的错误详情文本。
  历史 `disconnect_route` 专项 **16/16** 的人工 screen 行曾误标 `synthetic=false`；
  本批改为可嵌套的线程局部合成标记（局部 SEH 外持有），不影响其他线程的真实屏幕事件。
  人工路由与合成回调均计入 synthetic；`verdict` 按 source 区分，屏幕行另带 `disconnect_reason`。
- 生命周期：默认关闭，`allow_hook_va` + 世界内准入。SDK 会保留并动态更新 origin 输出槽
  （`pl/Hook.h`），本批将原先错误的栈临时槽改为静态存储，使用 LL typed hook 的相同契约，
  移除发布后有界自旋及跳过原调用/返回空路由的分支。不清空 origin 槽，卸载失败保留实际
  `callback_hooked/screen_hooked/active`，`panic_done.ok` 纳入守望卸载结果；disable 同样卸下。
  SDK 引用采样有局部 SEH/C++，失败只增加 `observation_faults`，仍调用原实现。
  环冷侧一次初始化并原子发布，进程内保持存续。未证明 SDK trampoline 的在途卸载安全，
  未做现场热卸载；不能把设施回归当成自然违规链或任意启停并发的证明。
- 既有现场记录：`--violation-only` **13/13**；默认自检 **934/934**，导出两行均为
  `synthetic`。这一计数不能替代开启两类封包能力时的 **992/992** 历史基线。
  该批守望只在默认入口最初武装，设施自测和 panic 后漏记，专项入口也未读取诊断变量。
  本批 `ViolationObservation` 统一所有 live 入口：先记起始 head，导出本会话增量及合成/
  真实行数、lost、最终状态和观察事件；关闭管道前导出，原本已武装的会话保留。
  设施自测恢复原有武装状态；panic 保持真实卸载语义，检查卸载结果后才重新武装，
  `gap_begin/gap_end/gap_uncovered` 明确覆盖空窗。落盘/采集失败独立报告，不遮蔽首错。
- 既有独立 `check_flight` **44/44**：该函数包含客户端/服务端 SEH、C++、超时及断言
  故障注入，不能记为“无故障注入的最小能力序列”。环中的三条历史 `synthetic` 也不能
  算成本轮新增记录；每组应保存起始 `next`，从该游标 drain，并检查 `lost`。
- 2026-10-31 22:32 新对照：同一进程计划先关闭、再开启守望，只执行正常
  gamemode/fly/togglefly/teleport/reset 操作，不发送 `selftest_flight` 或合成记录。
  第一组（守望关闭）在 reset 的服务端 `teleported(ok=true, teleport_id=103)` 后退档：
  等待起点 tick **73123**，最后 tick **73127**。**25 项通过后由 WorldLost 中止**，
  第二组尚未开始，不能记为 25/25 验收通过或已完成两组对照。本次错误页详情码待确认，
  不能只凭 `disconnect_error` 分类认定又是 90。
  原始 spool、提取的时间线、首错和解锁回执均已保存：
  `out/ability-watch-contrast-20261031-223225{,-timeline}.json`、
  `out/ability-watch-contrast-20261031-223225-spool.jsonl`、
  `out/ability-watch-disconnect-20261031-223232.json`。
- 本次 profile 一致性为 **up to date**，roles 校验 **50/57**。配置与 DLL 未作变更，
  没有执行新增逆向或安装新的观察边界。**设施验证与根因定位分开记账**：真实触发包、
  error code 和方向仍 unresolved；未武装期间没有记录不能用于排除 mod 的间接影响。
- 22:53–22:54 后续验证：在带屏幕路由观察的已安装 DLL 上，按**先开启、后关闭**守望
  执行相同正常能力序列，各 **33/33**。同一进程 **pid 14664**，相同配置、起始脚部位置
  与能力；DLL SHA-256 为 `6afc3aa41930e2bc4da8f58c715205bf33092223a8bb54f28cc796952c3ed6e8`。
  武装组 tick **9108–9218**，关闭组 **10796–10911**；各有 **11** 个采样点核对
  `active/screen_hooked` 与本组状态一致。两组起止游标均为 **3**、`lost=0`、新增记录 **0**；
  没有调用故障注入、合成记录或 `disconnect_route`，封包采集保持关闭。
  每组均回读恢复姿态、能力和站稳，最终解锁、held_mask=0、守望关闭；配置和 DLL 未改动，
  **113** 个 crash 文件无新增或修改。证据：
  `out/ability-watch-pair-20261031-225320.json`，两份原始报告为
  `out/ability-watch-contrast-20261031-225320.json`、
  `out/ability-watch-contrast-20261031-225445.json`；复现脚本
  `out/ability-watch-contrast.py --watch on|off` 使用现有 API 显式武装并回读。
  这只是一个固定顺序的同进程样本，未清除历史状态，也未出现自然断开，不能证明启停并发
  或真实违规回调链已通过验收。22:32 的失败使用另一 DLL/进程，不能归因于武装开关。

### 自检结果与首错保护（2026-10-31 后续）

`driver/selftest_support.py` 集中维护 pass/fail/skip 三态结果、清理首错保护与诊断会话。
`NOTE` 仅供信息，`SKIP` 含原因并进入总分母；`--require-complete` 遇必需 skip 返回 1，
普通模式保留可降级运行，均列出 fail/skip/required skip 数。实体 ECS 的 skipped 数据
仍是查询字段，不能混成测试 skip。无故障的空槽/权限前提缺失、外部鼠标干扰等不再算 PASS。
飞行、移动、传送、task_operations 清理统一用 preserve_failure：已有异常保持原对象和
类型，清理错误附到异常 notes 并输出；无主体错误时清理错误照常失败。直接回归覆盖
WorldLost、ModTimeout 及四组实际检查的 finally 路径。

本批设施专项 **21/21**（含统一观察会话），人工屏幕两行及合成回调一行均正确标记；
`real_count=0/synthetic_count=3/lost=0/observation_faults=0`，不是真实断开样本。
关闭封包注入后的 `--packet-injection-only --require-complete` 为 **2/3**：1 个必需 skip，
按预期 exit 1，并正常导出/关闭守望。完整套件验收另行记录，不能用这两项替代。

水流夹具后续：首轮完整 1053/1055（checkpoint_load/teleport 失败，0 skip），控制复现
确认开放水源泄漏并回灌；两类水源测试已迁共用固定封闭池。`--record-medium-only
--require-complete` 为 11/11，回读恢复干燥和站稳；存档点比较实际保存坐标，容差未放宽。
首轮失败及逐 tick 复现保留在 reliability-repairs 指向的证据中，完整重跑单独记录。

### 自然断开诊断链与恢复（2026-10-31 23:38 后续）

最新 DLL `9262737e…b6e0cbe` 的第二次完整自检 **465/466，1 fail、0 skip**，
最后通过 `flight_spectator_recovers`，finally 的恢复等待从 tick 859 开始，861 自然退档。
这不是全套通过；水流修复的新夹具尚未在完整套件中走到，专项 11/11 的范围不变。

同 tick 861、同 tid 30828 的两条非合成记录首次给出完整关联：
callback 的 `response=3/packet_id=39/packet_size=34/error_value=104`，
随后 screen 的 `response=90/disconnect_reason=90/context=Block`。
SDK 公开枚举确认 39 是 **SetActorData**，不是 UpdateAbilitiesPacket（187）。
回调 context=`BinaryStream read() incomplete.readNoHeader fai`，
屏幕 detail=`Minecraft Bedrock.Win,D,,GC-10.0.26100.4351 Err`；两者都截断到 47 字节。
`category` 仍是未解释的本进程值；size 参数边界、104 含义、方向、失败字段及生产链仍
unresolved，不能仅凭发生在能力恢复附近断定因果，也不能由 capture/chat 计数排除间接影响。

观察从入口武装到退档前无设施/panic 空窗，新增 real=2、synthetic=0、lost=0、
observation_faults=0。这验证 mod 自己读取自然错误页原因及其与违规回调的关联；
不证明 SDK 任意在途卸载安全。证据：`out/diagnostic-full-final-violation.json`、
`out/diagnostic-full-final-live.log`、`out/disconnect-20261031-233853.json`；原始 spool
等已归档到 `out/diagnostic-validation-20261031-231752/natural-disconnect-233853/`。

真实错误页 recover exit 0，原配置逐字节恢复并重启加载，最终回读 **16/16**：姿态、
能力、效果、背包/装备/选中槽、干燥站稳、输入释放/解锁、守望关闭、封包能力关闭、
配置与 DLL 哈希及 113 个 crash 文件无新增/修改。证据为 `out/diagnostic-natural-recover.log`
及 `out/diagnostic-validation-20261031-231752/final-restoration.json`。
最新离线 **362/362**、ruff/format/mypy、完整 C++ 构建通过；profiles up to date、roles 50/57。
完整现场失败保留，不覆盖历史全绿基线。后续仅转交
[`诊断提示词`](notes/prompts/badpacket-setactordata-20261031.md)，本批未执行新增逆向。
