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
| `+0x60` | 第二组 16 位标志 |

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
- 实测：从 (0.5, -63, 0.5) 世界 +Z 方向 20 tick 前进 **2.2 格**，
  max 水平速度 **0.1177 格/tick**，停止后位移 0。

### 5.2 朝向与世界方向移动 `[实测]`

- 直接写 `ActorRotation+0x00/+0x04`（含 prev）只能短暂生效：客户端每 tick 被相机系统回写，
  服务端另有自己的朝向。所以“改 yaw 再前进”不可靠。
- 需要指定世界方向时用 `moveWorld(dx, dz)`：
  - 读服务端 `ActorRotation.yaw`；
  - 反解 `strafe = dx·cos + dz·sin`、`forward = −dx·sin + dz·cos`；
  - 写入两份 MoveInput。
- 实测 `moveWorld(0,1)` → +Z，`moveWorld(0,-1)` → −Z，`moveWorld(-1,0)` → −X，
  `moveWorld(1,0)` → +X（距离受测试平台边界限制）。

### 5.3 速度

直接写 `StateVector+0x18/0x1C/0x20`。
水平速度可生效；写位置/竖直速度会被服务端权威位置覆盖。

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

## 7. 运行时验证记录（2026-10-18）

| 项目 | 结果 |
| --- | --- |
| `getState` vs HUD | X/Z 精确一致；HUD y=-63 对应 `pos.y=-63`，StateVector eye y=-61.38 |
| 朝向 | HUD `P/F` 与 ActorRotation 的 pitch/yaw 精确一致 |
| 直走 20 tick | +Z 2.2 格，max 0.1177 格/tick，on_ground 100% |
| 跳跃 | 峰值 +1.17 格，约 5 tick 滞空 |
| 服务端传送 | (0.5,-63,0.5) → (2.5,-63,2.5)，双端同步 |
| 后台 tick | 失焦 3 秒 +65 tick |
| 客户端写位置 | 无效（服务端覆盖） |

默认测试平台约 2.6×4.8 格（x≈[-1.6,1.0]，z≈[-2.1,2.7]），长距离直走会撞墙。

## 8. 已知边界

- **不要与 x64dbg 同时挂**：attach 会打死游戏进程。
- **sneak/sprint 未注入**：它们由服务端状态位控制，当前只记录请求并返回 warning。
- **chat 通道只用于 setup**：`setup.commands`（如 `/fill`）通过 ZCode 原生 CUA helper
  的键盘注入执行；传送/移动/观测都不依赖它。
- **合成鼠标不可用**：Minecraft 的 Raw Input 基本忽略合成鼠标事件；PowerShell 的
  `GetWindowRect` 还受 DPI 虚拟化影响。UI 操作应使用原生 helper 的键盘路径。
- **多人服务器**：服务端 registry 可能在远端，`serverTp` 只适用于本地/集成服务器。
