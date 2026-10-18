# Minecraft Bedrock 1.26.45.01 偏移与地址

所有 VA 以 image base `0x140000000` 为基准；agent 运行时按模块实际基址重定位。

证据标签：

- `[代码]`：IDA 反编译/反汇编直接读出。
- `[互证]`：两条独立路径（FNV 复算、多个调用点、布局自洽）一致。
- `[实测]`：2026-10-18 在运行中的 1.26.45.01 客户端上验证。

## 1. 版本指纹

agent 启动时校验以下函数入口的前 24 字节；任一不符就禁用对应 hook（`status().signatures`）。

| 名称 | VA | 作用 |
| --- | --- | --- |
| `enttStorageGet` | `0x141953860` | `(registry, typeId) -> storage`，用于捕获 registry |
| `enttEmplace` | `0x1401FC7B0` | `(storage, out[2], entityPtr, force)`，确保实体有组件元素 |
| `localPlayerPostMoveTick` | `0x1477705B0` | `LocalPlayerPostMoveSystem_Tick`，每 tick 一次，作 tick 时钟 |
| `moveInputResolve` | `0x145A5F910` | `MoveInput_ResolveDirectionAndSpeedFactor`，输入注入点 |
| `moveInputPipeline` | `0x1465D5A50` | `MoveInput_RawToResolvedPipeline`，仅诊断计数 |

指纹原文见 `agent/agent.js` 的 `SIG`。`driver/check_binary.py` 可离线对照安装目录的 EXE。

## 2. ECS 访问

### 2.1 registry 哈希表 `[代码]`

`0x141953860` 的反编译：

```
bucket_begin = registry[+0x48]
bucket_end   = registry[+0x50]     // count = (end - begin) / 8
pool         = registry[+0x68]     // 节点池
pool_end     = registry[+0x70]
bucket       = bucket_begin + 8 * (typeId & (count - 1))
while (*bucket != 0xFFFFFFFF):
    node = pool + 32 * *bucket
    if node.typeId (+0x08) == typeId:
        return node.storage (+0x10)   // 若是 pool_end 则视为 miss
    bucket = node                     // next 在 node +0x00
```

> storage 未命中时该函数会 `new(120)` 并初始化；agent 只做纯内存遍历，不调用它，
> 避免在 JS 线程上修改 registry。

### 2.2 双 registry `[实测]`

本地世界里同一个进程有**两个** registry，`0x141953860` 会被两者调用：

| registry | 识别特征 | 用途 |
| --- | --- | --- |
| 客户端 | 有 `LocalPlayerComponent`（`0xACB47B38`） | 读位置/输入，客户端预测 |
| 服务端 | 没有 `LocalPlayerComponent`，有 `PlayerComponent`（`0xF95D258F`） | 权威位置、权威移动 |

两者的实体 id 可能不同（实测客户端 `0xB`，服务端 `0x1`），组件 id 相同。
agent 捕获所有候选 registry；客户端选带 `LocalPlayerComponent` 的那个，
服务端选另一个带 `PlayerComponent` 的。

### 2.3 storage 布局 `[代码][互证]`

返回的 storage 指针 = 120 B 分配块 + `0x10`。

| 偏移 | 内容 |
| --- | --- |
| `+0x00` | vtable：`0x14E7B7F10`（通用）或 `0x14E762A60`（12 B 专用路径） |
| `+0x08` | sparse 页指针表 begin（end `+0x10`） |
| `+0x18` | sparse 页指针表 capacity |
| `+0x20` | packed 实体表 begin（end `+0x28`，cap `+0x30`），元素为 u32 |
| `+0x38` | type info |
| `+0x40` | mode |
| `+0x48` | max entity id（`0x3FFFF`） |
| `+0x50` | 载荷 chunk 指针表 begin（end `+0x58`） |

### 2.4 元素寻址 `[代码][互证]`

```
page_offset = (entity & 0x3F800) >> 8          // = (entity >> 11) * 8
page        = *(sparse_begin + page_offset)
entry       = *(u32 *)(page + 4 * (entity & 0x7FF))
if ((entry ^ (entity & 0xFFFC0000)) > 0x3FFFE) -> 元素不存在
dense       = entry & 0x3FFFF
element     = *(chunk_table + 8 * (dense >> 7)) + stride * (dense & 0x7F)
```

- 实体 id 低 18 位有效；高 14 位是版本字。
- 页大小 2048 个实体；chunk 大小 128 个元素。
- 与 `MoveInput_ResolveOneEntity_B/C` 里的
  `*(storage+80) + ((dense & 0x3FF80)>>4)` 逐位等价。

## 3. 组件表

| 组件 | id | 元素步长 | 用途 | 证据 |
| --- | --- | --- | --- | --- |
| `StateVectorComponent` | `0x1B5D5238` | 36 | 位置 + 速度 | `[代码][互证]` |
| `ActorRotationComponent` | `0x75DF36B7` | 16 | pitch / yaw / prev | `[代码]` |
| `ActorDataFlagComponent` | `0xC67426F3` | 24 | 3×u64 标志位 | `[代码]` |
| `MoveInputComponent` | `0x018B1887` | 100 | 解析后输入 | `[代码]` |
| `RawMoveInputComponent` | `0x3613E513` | — | 原始输入（未使用） | `[代码]` |
| `PlayerInputRequestComponent` | `0x6B04A058` | 40 | 每 tick 请求快照 | `[代码]` |
| `OnGroundFlagComponent` | `0xC29078A0` | tag | 存在 = 接地 | `[代码]` |
| `LocalPlayerComponent` | `0xACB47B38` | tag | 本地玩家（仅客户端 registry） | `[代码]` |
| `PlayerComponent` | `0xF95D258F` | tag | 玩家（客户端/服务端都有） | `[代码]` |
| `JumpFromGroundRequestComponent` | `0x4EA8C276` | tag | 起跳请求 | `[代码]` |
| `LocalPlayerJumpRequestComponent` | `0xDA6293F1` | tag | 本地玩家起跳请求 | `[代码]` |
| `TriggerJumpRequestComponent` | `0xF37EFB0F` | tag | 低跳标志 | `[代码]` |
| `ActorSetPositionRequestComponent` | `0x5E3CA211` | 12 | 引擎位置提交请求 | `[代码]` |
| `MovementAbilitiesComponent` | `0x6DE78DC5` | 16 | 能力位（飞行等） | `[代码]` |

所有 id 均用 FNV-1a-32（basis `0x811C9DC5`，prime `0x01000193`）复算过。

## 4. 字段偏移

### StateVector（36 B）

| 偏移 | 字段 |
| --- | --- |
| `+0x00` | x |
| `+0x04` | y（**眼睛位置** = 脚部 + `1.6200101`） |
| `+0x08` | z |
| `+0x18` | vx |
| `+0x1C` | vy |
| `+0x20` | vz |

`getState().pos` 和 `tp()` 对外统一使用**脚部坐标**（与 HUD / 命令一致）；
`getState().eye_pos` 返回 StateVector 的原始眼睛坐标 `[实测]`。

### ActorRotation（16 B）

`{pitch, yaw, prevPitch, prevYaw}`，四个 float 依次位于 `+0x00/+0x04/+0x08/+0x0C`。
证据：`SwimControl_PerEntity_ApplyPitchDrive`（`0x14CC562D0`）用
`a4[1]-a4[3]` 算 yaw 增量、`a4[0]-a4[2]` 算 pitch 增量 `[代码]`。

### ActorDataFlag（24 B）

| 偏移 | 内容 |
| --- | --- |
| `+0x00` | flags 0..63（bit1 = SNEAKING） |
| `+0x08` | flags 64..127（bit50 对应 flag 114 = CRAWLING） |
| `+0x10` | flags 128..191 |

### MoveInput（100 B）

| 偏移 | 字段 |
| --- | --- |
| `+0x00` | 原始 dword 标志（bit0 被 worker 读取） |
| `+0x04` | 原始 x |
| `+0x08` | 原始 y |
| `+0x0C` | word |
| `+0x0E` | byte |
| `+0x24` | 解析后的最终输入 **strafe**（本地 x） |
| `+0x28` | 解析后的最终输入 **forward**（本地 z） |
| `+0x54` | 状态位：滞空时 bit1 由游戏置起（实测 `0x34222168` → `0x3422216a`） |
| `+0x58` | 滞空时清零（`0x22800000` → `0`） |
| `+0x5C` | float，输入模长：地面 `0x3f7ffffe`(≈1.0) → 滞空 `0x3f800000`(1.0) |
| `+0x60` | 第二组 16 位标志 |

滞空/地面 100 字节逐字段 diff 实测只有上面三项变化，**坐标轴字段不变**：
所以滞空推进偏慢不是"我们少写了输入轴"，而是 Bedrock 本身的低空中控制
（起跳不保留步行速度，见 §5.1）。

`0x145A5F910` 返回的 `{resolvedX, resolvedY}` 会被三个
`MoveInput_ResolveOneEntity_A/B/C` 变体写到 `+0x24/+0x28` `[代码]`。

轴序证据：`PostMove_AutoJumpSweepCheck`（`0x142FCD8D0`）把
`(a7, a8)` 按 `x' = a7·cos − a8·sin`、`z' = a7·sin + a8·cos` 旋转；
yaw=0 时 forward 必须落在 +Z，因此 `a7 = strafe`、`a8 = forward`。
调用点传入的正是 `MoveInput+0x24/+0x28` `[代码]`。

## 5. 写路径与实测结论

### 5.1 输入注入（客户端 + 服务端） `[实测]`

- 在 `0x145A5F910` 的 `onLeave` 覆盖返回 buffer，三个变体都会把它写进 `MoveInput+0x24/+0x28`。
- 同一 tick 还会直接写 **客户端和服务端两份** `MoveInput`；只写客户端会导致
  客户端预测 vs 服务端校正来回拉扯（实测位置在 0.5↔1.46 之间跳动）。
- `stopInput()` 会把两份都写 0；否则服务端会保留上一次的输入，下次运行玩家会自己走。
- **注入的输入能复现原版步行速度**：四种组合（local/world × strafe/forward）逐 tick
  位移都是首 tick **0.098** → 收敛到 **0.2158 格/tick（4.317 m/s）**，
  符合 `v_{n+1} = 0.546·v_n + 0.098`（0.546 = 地面滑度 0.6 × 0.91）；
  两轴对称、两轮重复一致，客户端与服务端位置吻合。
- ⚠️ **`getState().vel` 不是逐 tick 位移**：`StateVector+0x18` 存的是**摩擦前**速度，
  正常地面上恒为真实位移的 **0.546 倍**（实测比例稳定 1.832 = 1/0.546）。
  判速度必须用相邻 tick 的位置差；`output.py` 的 `max_h_speed`/`mean_h_speed`
  已按位置差计算，`max_vel_h_speed` 保留原字段便于对照。
  早期把它当速度读，才会误以为"注入只有 55% 加速度"。
- 实测（撞墙场景，测试平台上 x=6 砌 3 格墙）：从平台中心以世界 +X 前进，
  位移 **(+5.200, 0, 0)**，最大速度 **0.2159 格/tick**（= 步行），接地 100%，
  撞墙后停在 5.7（墙面 6 − 玩家半宽 0.3），速度立即归零；
  teardown 在记录窗口之外执行，不会污染位移/速度。
- **滞空推进偏慢（符合原版，不是注入缺陷）**：地面能精确复现步行速度，但滞空时只有
  0.13-0.15 格/tick，整段跳跃弧线水平只推进 **0.630 格**，峰高 **+1.166 格**
  （实测 `jump-distance`，滞空 4-5 tick）。逐字段对比滞空/地面的 `MoveInput`
  100 字节，只有 `+0x54` bit1、`+0x58`、`+0x5C` 三个字段被游戏改动，
  **坐标轴字段一致**——说明这是 Bedrock 本身很低的空中控制（Walk-jump 在基岩版
  比走路还慢），不是我们漏写了输入。要复现"跑跳上一格台阶"需要 sprint 状态，
  而 sprint 目前未注入（见 §8）。

### 5.2 朝向与世界方向移动 `[实测]`

- 直接写 `ActorRotation+0x00/+0x04`（含 prev）**只能短暂生效**：客户端相机每帧把它写回。
  更关键的是服务端按**真实相机朝向**解释移动输入，于是两侧世界方向分叉：
  实测把 yaw 写成 0 后，客户端朝 +X 走，服务端却沿 58.97°（旧相机 yaw）方向走，
  每前进约 0.83 格就被服务端校正一次（位置跳变，表现为橡皮筋）。
- **正解：用服务端命令 `/tp @s ~ ~ ~ <yaw> <pitch>` 设朝向**（`look` 步骤的 `command` 模式）。
  实测之后客户端与服务端 yaw 都稳定、不再被回写，且 forward=1 → 两侧同步 +Z、
  strafe=1 → 两侧同步 +X、yaw=90 → 两侧同步 −X，逐 tick 位置吻合。
- 需要指定世界方向时仍可用 `moveWorld(dx, dz)`：读服务端 `ActorRotation.yaw`，
  反解 `strafe = dx·cos + dz·sin`、`forward = −dx·sin + dz·cos`，写入两份 MoveInput；
  前提是朝向已用命令对齐，否则两侧又会对不上。
- 命令通道见 §6.1。

### 5.3 速度

直接写 `StateVector+0x18/0x1C/0x20`。
水平速度可生效；写位置/竖直速度会被服务端权威位置覆盖。
注意 `+0x18` 读出来的是**摩擦前**速度（见 §5.1），不是逐 tick 位移：
写 0 不影响下一次输入注入的速度曲线（实测带/不带 `set_vel(0,0,0)` 都是 0.2158），
所以重置场景时不要依赖它来"刹停"，用 `stopInput()` 即可。

### 5.4 传送：必须走服务端 registry `[实测]`

客户端侧的三条路径全部**无效**（写完后位置不变）：

- 写客户端 `StateVector` 位置；
- 在客户端 registry `emplace` 并写 `ActorSetPositionRequestComponent`；
- 同样的请求在碰撞系统生成请求的 tick 阶段写。

有效路径：

1. 找到服务端 registry（另一个候选 registry，带 `PlayerComponent`）和它的玩家实体；
2. 在该实体上 `emplace` `ActorSetPositionRequestComponent`（12 B）；
3. 写 `{x, y + 1.6200101, z}`（组件与 StateVector 同为眼睛坐标）；
4. 服务端在后续 tick 应用并同步回客户端。

实测：从 (0.5,-63,0.5) 传送到 (2.5,-63,2.5)，客户端和服务端位置都变为目标值，
`on_ground=true`。这是 `tp` 的默认路径（`mode="server"`）。

`TeleportToRequest`（FNV `0xF8BDF31C`）在客户端 registry 中不存在存储，未使用。

### 5.5 跳跃 `[实测]`

对玩家 emplac `JumpFromGroundRequestComponent`（`0x4EA8C276`）。
`JumpFromGroundSystem` 每 tick 遍历带该组件的实体，再按 `LocalPlayerComponent` 分派到
`LocalPlayerJumpRequestComponent` `[代码]`。

- `jump()`：单次请求（下一 tick 消费）。
- `setInput({jump:true})`：按住跳，每 tick 重新确保组件存在。
- tag 组件可能没有载荷 chunk，成功判据是“稀疏集里存在”，不是元素指针非空。

实测（y=-63 平台）：脚部 y 依次为 -63 → -62.580 → -62.247 → -61.999 → -61.834，
随后落地；峰值约 **+1.17 格**，滞空约 5 tick。

### 5.6 方块读取（`block_at` / `--scan`） `[实测]`

链路：

1. hook 方块查询包装 `0x144767E30`（AABB 查询）。它的第 4 个参数是查询上下文；
   从 `ctx+456` 取 holder，调 `holder 虚表+96` 得到 block accessor，缓存。
2. `accessor 虚表+16(BlockPos{int x,y,z})` 返回 `Block*`。
3. `Block*+0x68` = `BlockLegacy*`。
4. `BlockLegacy+0xE0` = 8 字节名字哈希（**FNV-1-64**("minecraft:xxx")，
   与 HashedString 算法一致）；`BlockLegacy+0xE8` = 完整名字 `std::string`
   （MSVC 布局 `{buf/ptr, size, capacity}`，size ≤ 15 时内联，否则首字段是指针）。

实测：

- `minecraft:air` 哈希 `0xBD584BAF00003448`，`minecraft:stone` 哈希 `0x2727739C84AA7913`，
  `minecraft:cobblestone` 哈希 `0x3E53526998BB823E`；`block_at` 返回的名字与游戏内一致。
- `--scan -4 4 -4 4`：平台表面以 `stone`（y=-64）为主，混有 `grass_block`、`slime`、
  `sweet_berry_bush`、`rose_bush`、`poppy`、`hopper`、`bed`、`bamboo`、`acacia_fence_gate`，
  另有 2 列 void ⇒ 平台高度确实不一。

限制：accessor 需要至少一次已发生的方块查询（移动/碰撞时游戏会查询）；
driver 首次调用会轻推玩家一下，之后可对任意坐标查询。

## 6. 后台运行（关闭失焦暂停） `[实测]`

`options.txt` 中的开关：

```
pause_menu_on_focus_lost:0
```

位置（两个都要改）：

- `...\Minecraft Bedrock\Users\<id>\games\com.mojang\minecraftpe\options.txt`
- `...\Minecraft Bedrock\Users\Shared\games\com.mojang\minecraftpe\options.txt`

验证：让 ZCode 占据前台、游戏失焦 3 秒，`LocalPlayerPostMoveSystem_Tick` 的计数器
前进 **65 tick**（满速 20 tps 下约 60 tick）。改完需重启游戏生效。

## 6.1 命令通道（场地准备） `[实测]`
`fill` / `setblock` / `tp … <yaw> <pitch>` 这类只能由服务端执行的命令，走游戏自己的
聊天框提交。**不需要抢前台窗口**：

- `driver/postmsg.py` 用 `PostMessageW` 把 `WM_KEYDOWN` / `WM_CHAR` 投递给游戏窗口；
- `lParam` 必须带上 `MapVirtualKeyW(vk, 0)` 得到的扫描码（SDL 用它映射按键），
  否则按键事件被丢掉；
- 打开聊天框用 `/` 键（游戏会自动填入 `/`，所以正文的 `/` 不能重复投递）；
  投递前先发一批退格清空残留文本（聊天框没开时无副作用）；
- 回车提交只投 `WM_KEYDOWN` + `WM_KEYUP`。**不要再投 `WM_CHAR(0x0D)`**：
  Bedrock 里回车也是"打开聊天"的绑定键，聊天框关掉之后多出来的那一次会把聊天框
  重新打开，玩家就停在那里（表现为"输入在动、人不动"）。

验证：窗口 `foreground: False` 时 `/setblock 2 -60 2 stone` 生效，
`block_at(2,-60,2)` 返回 `minecraft:stone`；随后 `air` 恢复成功。
这条通道同时解决了 §5.2 的朝向问题——它是唯一能改服务端权威朝向的办法。

### 6.2 命令之后的进程卡顿 `[实测]`

聊天命令执行后约 **1.5 s**，整个进程（客户端 + 集成服务端）会卡住 **~1.2 s**，
然后一次性补跑约 **24 个 tick**：期间 tick 计数不动或跳号，玩家位置不被积分
（在走就是"停下"，在空中就"悬停在顶点"）。确定性复现（`step-up` 连跑 3 次都在
走出 **+2.115 格**处停住），与是否有录制无关；不用命令通道时 8 次全过。
怀疑是命令把世界标脏后触发的存档，未证实。

规避：**朝向命令放进 `setup`，setup 之后空转 60 tick 再开始限时步骤**，
把卡顿吸收在测量窗口之外（`run_scenario` 现在自动这么做）。
`output.py` 另外会给"中途零位移后又恢复移动"的 step 打 `stalled`/`freeze_ticks`，
`run.py` 遇到就返回退出码 4，避免脏数据被当成结果。

## 7. 运行时验证记录（2026-10-18）

| 项目 | 结果 |
| --- | --- |
| `getState` vs HUD | X/Z 精确一致；HUD y=-63 对应 `pos.y=-63`，StateVector eye y=-61.38 |
| 朝向 | HUD `P/F` 与 ActorRotation 的 pitch/yaw 精确一致 |
| 命令通道（未聚焦） | `foreground: False` 下 `/setblock` 生效并被 `blockAt` 读到 |
| 朝向改法 | 命令 `/tp @s ~ ~ ~ 0 0` 后两侧 yaw 稳定；forward→+Z、strafe→+X、yaw90→−X 逐 tick 吻合 |
| 撞墙（wall-stop 场景） | 位移 (+5.200, 0, 0)，停在 x=5.7，接地 100% |
| 移动速度 | 逐 tick 位移 0.098 → 0.2158 格/tick（= 原版步行 4.317 m/s），两轴对称、可重复 |
| `vel` 语义 | 摩擦前速度 = 真实位移 × 0.546（实测比例 1.832），不能当速度用 |
| 跳跃 | 峰值 +1.17 格，约 5 tick 滞空 |
| 服务端传送 | (0.5,-63,0.5) → (2.5,-63,2.5)，双端同步 |
| 方块读取 | `--scan -4 4 -4 4`：stone/grass_block/slime/浆果/玫瑰/虞美人/漏斗/床/竹子/栅栏门 + 2 列 void |
| 后台 tick | 失焦 3 秒 +65 tick |
| 客户端写位置 | 无效（服务端覆盖） |
| 合成鼠标 | `moveTo` 光标准确落在按钮上，`clickAtPoint` 返回成功但按钮无反应（Raw Input 忽略点击） |
| 坐标契约 | verified 截图 1591×962；`bounds[0..1] + 光栅坐标` → 点击落在目标按钮上（实测命中） |
| 菜单键盘导航 | 焦点环可见；主菜单→进存档 5 步按键连续 3 次复现（含冷进/退档后再进） |
| 世界内走路（CUA） | `hold w 1500` 前进 +6.69 格 = 4.46 格/秒，与原版走速一致 |
| 合成鼠标移动副作用 | 暂停菜单里 `moveTo` 后重进世界，yaw 0.0000 → -5.4492（移动漂视角、点击无效） |

测试平台：世界底部是 y=-64（1.18+ 地板），原生地面高度不一、宽度不够跑长距离；
现已用 `/fill` 在 y=-50 铺 65×65 石台（地表 -49），见
`scenarios/setup-platform.json` / `teardown-platform.json`。

## 8. 已知边界

- **不要与 x64dbg 同时挂**：attach 会打死游戏进程。
- **sneak/sprint 未注入**：它们由服务端状态位控制，当前只记录请求并返回 warning。
- **chat 通道不抢焦点但依赖窗口**：游戏窗口必须存在且不是全屏独占；
  `--command-channel none` 时场地准备与 `look mode=command` 都不可用。
- **合成鼠标点击不可用**：Minecraft 的 Raw Input 忽略合成鼠标**点击**。实测 `moveTo` 能把光标
  精确落在"设置"按钮上（截图里光标就停在按钮上），紧接着的 `clickAtPoint` 返回成功但按钮
  毫无反应。PowerShell 的 `GetWindowRect` 还受 DPI 虚拟化影响，别用它算坐标。
  UI 操作只走**键盘**（窗口消息，或 helper 的 `pressKeyGlobal`/`holdKeyGlobal`），见 §9。
- **多人服务器**：服务端 registry 可能在远端，`serverTp` 只适用于本地/集成服务器。
- **方块读取的前置**：`block_at` 依赖先捕获一次方块查询上下文；新会话第一次调用时
  driver 会轻推玩家触发查询，之后 accessor 被缓存。

## 9. 桌面操作：菜单 / 进存档 / 世界内走路 `[实测]`

`driver/native/cua.js`（ZCode 电脑操作 helper 的封装）动作：`info / shot / click / key / hold / type`。
完整流程、调用契约和排查顺序见 skill **`mcbe-desktop-control`**
（`~/.agents/skills/mcbe-desktop-control/SKILL.md`）。核心事实：

- 截图是窗口内容的物理像素（1591×962）；`info` 的 `bounds = [left, top, width, height]`，
  其中 width/height 含窗口边框（比截图大），**只有原点是可信的**：
  屏幕坐标 = `(bounds[0] + raster_x, bounds[1] + raster_y)`。
- 菜单有**焦点环**，导航就是"按键 → 截图看焦点环 → 再按 Return"。
- **主菜单 → 进存档**（连续 3 次复现，含冷进和退档后再进）：
  `Return`（进"游戏"屏）→ `Down`（世界标签 → 创建新世界）→ `Left`（→ 排序按钮 ⣿）
  → `Down`（→ 第一张存档卡的"游玩"区）→ `Return`（加载 20–30 s）。
  在"游戏"屏上 `Return` 对标签是**空操作**（实测），但落在"创建新世界"上会真的新建世界，
  所以每步都要确认焦点环。
- 卡片行内顺序是 `[卡片1游玩][卡片1铅笔][卡片2游玩][卡片2铅笔]…`：从卡片 1 游玩区
  `Right` 到的是**同一张卡的铅笔**（世界设置），再 `Right` 才到卡片 2。
- **游戏内 → 主菜单**：`Escape`（暂停菜单，焦点在"继续游戏"）→ `Down×3`（保存并退出）→ `Return`。
- **世界内走路**（CUA 兜底，不是实验手段）：`hold w 1500` 实测前进 **+6.69 格 / 1.5 s**
  ≈ 4.46 格/秒，与原版走速一致。
- 每次世界切换都会销毁 frida script，进世界后必须重新 attach
  （`py -3.11 driver/run.py --wait 30`，看到 `local player resolved` 才算好）。
- **合成鼠标"移动"会漂视角**：点击无效，但移动有副作用——实测在暂停菜单里 `moveTo` 之后
  重进世界，HUD yaw 从 `0.0000` 变成 `-5.4492`。游戏内别发合成鼠标移动，实验前一律用
  `/tp @s ~ ~ ~ <yaw> <pitch>` 重置朝向。

