# 使用物品减速与望远镜加速（1.26.51.01）

> 版本：`Minecraft.Windows.exe` `1.26.51.01`（LeviLamina `26.51.7`，`image_base = 0x140000000`）。
> 证据约定：`[代码]` 反汇编/控制流；`[常量]` `.rdata` 浮点、组件哈希、注册点；`[实测]` LeviPilot 逐 tick 录制与 ECS 回读；`[推断]` 由直接证据补出的环节。
> 复现脚本：[`verify_spyglass_item_use.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/spyglass-item-use-12651/probes/verify_spyglass_item_use.py)（Round 4 账本 `r04-017`，`exit: 0`；Round 1 为 `r01-001`）。原始数据：gitignored `out/spyglass-item-use-1.26.51.01.json`。
> **Round 4 返工**：Round 1 的 `set_input(ticks=N)` 到点自动松键在段间留下 3~9 拍空档，切槽段还整体错位一格。本轮改成"超发窗口 + 到期前覆写"（`HOLD_SLACK`，脚本对每段断言 `input.w` 无内部空档）与"单窗口内切槽"（逐槽断言 `selected_slot`），并修正了组件 `sizeof`、`Item::isComponentBased` 与 `VanillaClientGameplayComponent::mSprintTriggerTime` 三处静态误读。受影响的结论已在 §10 逐条勘误。
> 相位口径：录制 `vx/vy/vz` 是 `Actor::getVelocity()`（**已完成位移**）；`mi_z` 是 watch 直读的 `MoveInputComponent+0x28`（**本拍施加后的 `mMoveVector.z`**）；`slow_m` 是 `ItemUseSlowdownModifierComponent+0x00` 的直读结果。本文所有数字取自最终 `exit: 0` 的 JSON，没有手算改写。

---

## 0. 结论摘要

| # | 结论 | 证据 |
| --- | --- | --- |
| 1 | `ItemUseSlowdownModifierComponent`（`0x867fc92c`）**只有 `+0x00` 一个 float 字段**被读写，真实大小 **`4` 字节**（稠密数组步长 `shl eax, 2`）；描述符里的 `0x20` 是**类型名长度**，不是 `sizeof` | `[代码]` `[常量]` |
| 2 | 减速值由 `ItemUseSlowdownSystem` 写入：默认 **`0.35f`**；仅当 `Item::isComponentBased()` 为真才去查 `minecraft:use_modifiers` 的 `movement_modifier`（否则保留默认值**照常写入**）；取到 **`1.0`** 时**整条写入被跳过**（不创建组件） | `[代码]` `[常量]` |
| 3 | `ItemUseSlowdownApplySystem` 把该值**平方一次**（`mulss xmm0, xmm0`）后乘进 `MoveInputComponent+0x24/+0x28`（`mMoveVector.x/z`） | `[代码]` |
| 4 | 因此 wiki 流传的 `0.35² = 0.1225` **不是乘了两次 `0.35`**：`0.35` 只出现一次（物品乘数或默认值），平方来自 Apply 系统自身的 `m²` 变换 | `[代码]` `[实测]` |
| 5 | `ItemUseSlowdownClearSystem` 是 `RemoveFromAllEntitiesSystem<ItemUseSlowdownModifierComponent>` 的实例，**每拍末清空整张表** ⇒ 该组件是**单 tick 瞬态**，任何管道查询都读不到它 | `[代码]` `[常量]` `[实测]` |
| 6 | 地面稳态：开镜 `0.026489` / 行走 `0.215820` = **`0.1227`**；首拍加速度 `0.012026` / `0.097982` = **`0.1227`** | `[实测]` |
| 7 | 空中同样减速：开镜 `0.002401` / 空中行走 `0.019601` = **`0.1225`**，但**跳跃初速仍是 `0.42`**，不被乘数缩放 | `[实测]` |
| 8 | 与潜行**相乘**：潜行 `0.03675`（= `0.3 × 0.1225`）、迅捷潜行 III `0.091875`（= `0.75 × 0.1225`） | `[代码]` `[实测]` |
| 9 | 45° 斜走**先归一化再乘**：`0.086621` = `0.1225 × 1/√2` | `[实测]` |
| 10 | 使用物品期间**无法起跑**；疾跑中开镜有 **1 拍过渡**——开镜那一拍仍按未开镜结算（`sprint=1`、`mi_z=1.0`、速度保持 `0.28064`），**下一拍**才 `sprint=0` 且 `mi_z=0.1225`；松手后**下一拍**即可恢复疾跑（`first_sprint_offset = 1`） | `[代码]` `[实测]` |
| 11 | 开镜中切快捷栏：切到**可继续使用的物品**（面包、山羊角）使用窗口与 `0.1225` 都保持；切到**不能维持使用的物品**（盾牌、石头）使用窗口**结束**、乘数回到 `1.0` | `[实测]` |
| 11b | 山羊角在"持续按住右键 + 前进"下**保持使用并减速 `0.1225`**（与其它物品同为 `m = 0.35`）；Round 1 的"一按前进就结束"是窗口到期假象 | `[实测]` |
| 11c | 盾牌举盾是**潜行**触发：主手/副手都**不进入 `ItemInUseComponent`**，乘数就是纯潜行 **`0.3`**（没有额外的 `0.1225`，也没有格挡专属减速） | `[实测]` |
| 12 | **「望远镜加速」作为速度增益在 `1.26.51.01` 不存在**：没有乘数 >1 的写入路径，没有残留拍、没有免检拍、不能与疾跑叠加；逐拍点按的最高速度 `0.154419` 仍低于行走稳态 `0.215820`，且 57 拍里没有任何一拍超过 `0.546·v + 0.097982` | `[代码]` `[实测]` |

---

## 1. 组件与三系统

### 1.1 `ItemUseSlowdownModifierComponent`

描述符 `0x143292861`（函数 `0x143292830..0x1432928c2`）写入 hash `0x867fc92c` 与**名称长度 `0x20`** `[常量]`：

```
0x143292861  mov dword ptr [rax + 4], 0x867fc92c     ; EnTT component hash
0x14329286f  mov qword ptr [rax + 8], <name ptr>
0x143292873  mov qword ptr [rax + 0x10], 0x20        ; std::string_view 的长度 = 32 个字符
```

**`0x20` 不是 `sizeof`**：`ItemUseSlowdownModifierComponent` 恰好 32 个字符，`[rax+8]/[rax+0x10]`
是描述符里的 `std::string_view{name, length}`。真实大小由稀疏表查询处的步长给出——
`0x14c5f5ee2: shl eax, 2` 把实体行号乘 **4** 取组件地址，所以 `sizeof == 4`（只有一个 `float`）`[代码]` `[常量]`。

全库 20 处 `.text` 引用中，真正读写该组件的只有三处（其余是系统上下文构造、视图描述和
"列出视图组件"的调试函数）：

| 角色 | 地址 | 作用 |
| --- | --- | --- |
| 写入 | `0x14c5f5efc`（体内 `ItemUseSlowdownSystem`） | `movss [rax], xmm6`，写 `+0x00` |
| 读取 | `0x14c60f080`（体内 `ItemUseSlowdownApplySystem`） | `movss xmm0, [rdx]`，读 `+0x00` |
| 存在性 | `0x14b1a4d11`（`IntentSprintTriggerSystem`） | `test bpl, bpl`，只判存在（见 [`sprint-12651.md`](sprint-12651.md) §2.1） |

`+0x04` 以后在三系统里没有任何访问，所以"只有 `+0x00` 一个 float"与 4 字节步长互相印证。
运行期也印证了这一点：watch 直读 `+0x00` 的值始终不可得（§1.5），而 `mMoveVector` 的变化完整解释了观测到的倍率。

### 1.2 `ItemUseSlowdownSystem`：谁写、写什么

系统对象 vtable `0x14eb40730`（客户端变体 `0x14eb40770`），`slot[5] = 0x14c60cf70` 是视图迭代，
逐实体回调是 `0x14c60ce10` → `jmp 0x14c5f5e00`。视图由 `0x14c60ce20` 取池构造，包含
`ItemInUseComponent (0x05b90aef)`、`ActorMovementTickNeededComponent (0x94154cce)`、
`PlayerInputRequestComponent (0x6b04a058)`（`PassengerComponent (0x98e40c0e)` 作为附加观察）`[代码]` `[常量]`。

主体 `0x14c5f5e00..0x14c5f5f0d` 只有 75 条指令 `[代码]`：

```
0x14c5f5e12  rax = [rdx]                   ; ItemInUseComponent*（rdx = 组件指针）
0x14c5f5e22  rcx = [rax]                   ; ItemStack*（组件首个字段）
0x14c5f5e15  movss xmm6, [0x14e7f8a98]     ; 0.35f  ← 默认乘数
0x14c5f5e2d  rax = [rcx]                   ; Item*（ItemStack 首个字段）
0x14c5f5e30  rax = [rax + 0x48]            ; Item vtable[9] = Item::isComponentBased()
0x14c5f5e34  call rax
0x14c5f5e3c  je  0x14c5f5e4c              ; false 只跳过取物品乘数，xmm6 仍是 0.35f
0x14c5f5e3e  rax = [rbx]                   ; rbx = rdx → ItemInUseComponent*
0x14c5f5e41  rcx = [rax]                   ; Item*
0x14c5f5e44  call 0x143fd7e10              ; xmm6 = Item 的 use_modifiers 乘数
0x14c5f5e4c  xmm0 = |xmm6 - 1.0|
0x14c5f5e67  if (1.19209e-07f >= xmm0) skip   ; 乘数 == 1.0 时连组件都不创建
0x14c5f5efc  movss [rax], xmm6              ; 写 ItemUseSlowdownModifierComponent+0x00
```

要点：

- **默认就是 `0.35f`**（`0x14e7f8a98`）。没有 `use_modifiers` 的物品、以及**非组件化物品**
  （`isComponentBased() == false`）都吃这一档——注意 `0x14c5f5e3c` 是 `je` 到 `0x14c5f5e4c`，
  只跳过"取物品乘数"这一句，**仍然会带着 `0.35f` 写组件**，不是跳过整条写入。
- **`1.0` 等于"不减速"**：比较用 `fabs(m - 1.0) <= 1.19209e-07f`（f32 的 `2^-23`），命中就
  **连组件都不创建**。所以"没有该组件"和"乘数为 1"是同一回事，下游只能靠存在性判断。
- `rcx` 在调用点是 **`Item*`**（`[ItemStack] → Item*`），`vtable[9]`（`+0x48`）由 SDK 头
  `refs/levilamina-26.51.7/src/mc/world/item/Item.h:159` 确认为 **`Item::isComponentBased()`** `[常量]`：
  非组件化物品不查 `use_modifiers`，直接沿用默认 `0.35f`。

### 1.3 物品乘数从哪来：`0x143fd7e10`

```
0x143fd7e10  rax = item->vtable[0xc0]                      ; Item vtable[24] = Item::getComponent
0x143fd7e51  call rax(item, &HashedString("minecraft:use_modifiers"))
0x143fd7e61  if (!comp)                return 0.35f
0x143fd7e66  if (comp[+0x4C] == 0)     return 1.0f
0x143fd7e80  else                      return *(f32*)(comp + 0x48)
```

入参 `rcx` 是 **`Item*`**（不是 `ItemStack*`）：`vtable[24]`（`+0xC0`）由
`refs/levilamina-26.51.7/src/mc/world/item/Item.h:189` 确认为
**`Item::getComponent(HashedString const&)`**，第二参数就是 `minecraft:use_modifiers` 的
`HashedString`（类型全局 `0x151db1070`）`[代码]` `[常量]`。

`[代码]` `[常量]` 结论：**每个物品的减速系数存放在物品的 `minecraft:use_modifiers` 数据里**，
字段在组件 `+0x48`（float），`+0x4C` 是"该字段是否有值"的标记；没有这个物品组件时回落到
`0.35f`。

这个数值在 `1.26.51.01` 的二进制里**不是字符串键**：全库 `--find-str "movement_modifier"` 只命中
`backwards_movement_modifier` 一处，`items.brarchive` 里也没有该键 ⇒ 内置物品的组件值由代码
（或编译进二进制的定义）直接构造，因此本文的**逐物品数值以 `[实测]` 为准**（§4）。
跨版本核对时不要拿 Java 的 `Item.Properties` 值直接套用。

### 1.4 `ItemUseSlowdownApplySystem`：平方并乘进输入向量

系统 vtable `0x14eb407b0`，`slot[5] = 0x14c60f0b0` 迭代，逐实体回调 `0x14c60f080`，
**只有 8 条指令** `[代码]`：

```
0x14c60f080  movss xmm0, [rdx]        ; m = ItemUseSlowdownModifierComponent+0x00
0x14c60f084  mulss xmm0, xmm0         ; xmm0 = m * m
0x14c60f088  movss xmm1, [r8 + 0x24]  ; MoveInputComponent.mMoveVector.x
0x14c60f08e  mulss xmm1, xmm0
0x14c60f092  movss [r8 + 0x24], xmm1
0x14c60f098  mulss xmm0, [r8 + 0x28]  ; .z
0x14c60f09e  movss [r8 + 0x28], xmm0
0x14c60f0a4  mov dword ptr [r9], 0    ; VanillaClientGameplayComponent+0x00 清零
```

- 视图：迭代 `ActorMovementTickNeededComponent`，读取 `ItemUseSlowdownModifierComponent`，
  写 `MoveInputComponent (0x018b1887)` 与 `VanillaClientGameplayComponent (0xa406686e)` `[常量]`。
- **平方是这里做的，且只做一次**。`0.35` 只在 §1.2/§1.3 出现一次。
- `r9` 指向的 `VanillaClientGameplayComponent+0x00` 被清 `0`：该字段由
  `refs/levilamina-26.51.7/src/mc/deps/vanilla_components/VanillaClientGameplayComponent.h`
  确认为 **`::ll::TypedStorage<4, 4, int> mSprintTriggerTime`**（结构体首个成员，`+0x00`）`[常量]`。
  也就是说**每拍施加一次物品减速就会把双击前进的疾跑计时器清零**；这与 §5.2 的
  "使用物品期间起跑被拒、松手后下一拍才能起跑"互为补充，也解释了为什么"开镜前攒的疾跑计时"
  不会在松手后立刻生效。
- 该写入不改变 `mMoveVector` 或速度，只是清零计时器；本课题的倍率结论不依赖它。

### 1.5 `ItemUseSlowdownClearSystem`：整表清空，组件是单拍瞬态

注册处的名字字面量是 `ItemUseSlowdownClearSystem`（`0x14ec3411f`），而它内部对象的类型名是
`const char *entt::internal::pretty_function() [Type = RemoveFromAllEntitiesSystem<ItemUseSlowdownModifierComponent>]`
（`0x14eddf67a`）`[常量]`。运行路径 `0x14c610370` 取到 `ItemUseSlowdownModifierComponent` 的
storage 后调用其 `[vtable+0x18]`（整表操作），随后复位 `[rsi+0x48]` / `[rsi+0x28]` 的版本标记 `[代码]`。

**这解释了实测里最反直觉的一点**：watch 挂 `ItemUseSlowdownModifierComponent+0x00` 时，**所有 11 个
物品段落的 `slow_m_values` 都是空数组**，`ecs_component` 查询也一律
`component_not_found` `[实测]`。原因不是"组件没被创建"，而是同一拍内
`ItemUseSlowdownSystem`(19) 写入 → `ItemUseSlowdownApplySystem`(20) 消费 →
`ItemUseSlowdownClearSystem`(34) 清空，**管道查询只会在 tick 边界服务，永远落在清空之后**。

因此本课题的可观测口径只能是"施加后的 `MoveInput.mMoveVector`"（`mi_z`），它等价于
`原始输入 × 潜行乘数 × m²`；探针用 `ItemInUseComponent` 的出现/消失独立硬断言"物品确实举起来了"，
避免"没举起来当成 1.0×"的假阴性。

---

## 2. 注册顺序与同 tick 相位

三系统与相邻输入/疾跑/潜行系统全部注册在同一个函数 `0x14681da00..0x1468210b6` 内，按出现顺序
（节选，完整 51 项见账本 `r01-001` 的静态记录）`[常量]`：

| 序 | 系统 | 注册点 |
| --- | --- | --- |
| 3 | `TickServerMoveInputHandler`（写 `mMoveVector`，含潜行乘数） | `0x14681dd0c` |
| 9 | `PlayerBoundingBoxStateUpdateSystem` | `0x14681e6ca` |
| 18 | `ItemInUseComponentRemoveSystem` | `0x14681ef21` |
| **19** | **`ItemUseSlowdownSystem`**（写减速组件） | `0x14681f04c` |
| **20** | **`ItemUseSlowdownApplySystem`**（平方并乘进 `mMoveVector`） | `0x14681f21c` |
| 21 | `SetMoveSystemCommon` | `0x14681f2b0` |
| 22 | `SprintTimerSystem` | `0x14681f3d6` |
| 23 | `SetRequestSprintTriggerSystem` | `0x14681f462` |
| 24 | `IntentSprintTriggerSystem`（读减速组件存在性） | `0x14681f4fa` |
| 32 | `SneakTriggerIntentSystem` | `0x14681f8ef` |
| 33 | `SneakingSystem`（写 `SneakingComponent` 乘数） | `0x14681f92b` |
| **34** | **`ItemUseSlowdownClearSystem`**（清空组件） | `0x14681fa63` |
| 35 | `ActionSprintTriggerSystem` | `0x14681fb62` |

同 tick 的读写相位（同一 pass、同线程，靠注册顺序定序）`[代码]` `[常量]`：

1. `TickServerMoveInputHandler` 先用**上一拍的** `SneakingComponent` 算 `mMoveVector`
   （潜行乘数在 `0x1404495e0` 乘进 `+0x24/+0x28`，见 [`sneaking-crawling-12651.md`](sneaking-crawling-12651.md) §3.3）；
2. `ItemUseSlowdownSystem` 读当拍 `ItemInUseComponent`，写减速值；
3. `ItemUseSlowdownApplySystem` 读刚写的值，`m²` 乘进同一个 `mMoveVector`；
4. `IntentSprintTriggerSystem` 在之后才跑，看到组件存在 ⇒ 起跑被拒（§5.2）；
5. `ItemUseSlowdownClearSystem` 收尾清空，`ActionSprintTriggerSystem` 最后跑。

**这解释了两条实测事实**：开镜当拍 `mi_z` 就已经是 `0.1225`（不是"下一拍才生效"），
以及松手当拍 `mi_z` 立刻回到 `1.0`（上一拍的组件已被清空，不存在残留）。

---

## 3. `0.35` 与 `0.1225` 的来源核实

Wiki 与社区长期写"使用物品/格挡减速 `0.35² = 0.1225`"，容易被理解成"乘了两次 `0.35`"。
`1.26.51.01` 的静态与实测给出的答案是**一次 `0.35` 加一次平方**：

| 环节 | 值 | 位置 | 证据 |
| --- | --- | --- | --- |
| 物品乘数（或默认） | `0.35f` | `0x14e7f8a98`（默认）/ 物品 `use_modifiers` | `[常量]` `[代码]` |
| 写入组件 | `m`（实测侧反解 `√0.1225 = 0.35`） | `0x14c5f5efc` | `[代码]` |
| 施加到输入 | `m² = 0.1225` | `0x14c60f084` | `[代码]` `[实测]` |

实测三重印证（同一 `r04-017`）：

- `mMoveVector.z` 在开镜期间恒为 `0.1224999949336052`（`0.1225` 的 f32 值）；
- 地面首拍加速度 `0.012026` / 行走基线 `0.097982` = `0.122737`；
- 地面稳态 `0.026489` / 行走稳态 `0.215820` = `0.122737`。

三者都与"一次 `0.35`、一次平方"一致，与"两次 `0.35` 相乘于输入"数值上也相同，
但**代码路径上只有一个 `0.35` 常量和一个 `mulss xmm0, xmm0`**，不存在第二个乘 `0.35` 的点。
如果未来版本把 Apply 的平方去掉，同样的 `0.35` 会给出 `0.35` 而不是 `0.1225` —— 这正是需要
盯住的回归点。

---

## 4. 物品种类差异（`[实测]`，`r04-017`）

11 个物品/持法，干净跑道（脚本先 `fill air` 并回读 9 个点确认无残留方块）、同一 `settle` 前置、
同一 `ItemInUseComponent` 断言、段间无 `input.w` 空档。`factor` 是行走段 `mMoveVector.z` 的众数
（即施加倍率），`m` 由 `√factor` 反解。

| 物品 | 持法 | 使用窗口（举/走/松后） | factor | m | 行走段首拍加速度 |
| --- | --- | --- | --- | --- | --- |
| `spyglass` | 主手 | 是 / 是 / 否 | `0.1225` | `0.35` | `0.012026` |
| `bow`（带箭） | 主手 | 是 / 是 / 否 | `0.1225` | `0.35` | `0.012025` |
| `crossbow`（带箭） | 主手 | 是 / 是 / 否 | `0.1225` | `0.35` | `0.011984` |
| `trident` | 主手 | 是 / 是 / 否 | `0.1225` | `0.35` | `0.012023` |
| `golden_apple` | 主手 | 是 / 是 / 否 | `0.1225` | `0.35` | `0.012025` |
| `potion` | 主手 | 是 / 是 / 否 | `0.1225` | `0.35` | `0.012025` |
| `milk_bucket` | 主手 | 是 / 是 / 否 | `0.1225` | `0.35` | `0.012020` |
| `bread` | 主手 | 是 / 是 / 否 | `0.1225` | `0.35` | `0.012025` |
| `goat_horn` | 主手 | 是 / 是（前 8 拍后结束）/ 否 | 见下 | 见下 | `0.097989`（整段） |
| `shield` | 主手 | 否 / 否 / 否 | `1.0` | — | `0.097982` |
| `shield` | 副手 | 否 / 否 / 否 | `1.0` | — | `0.097982` |

- **八类物品完全同值**：望远镜、弓、弩、三叉戟、金苹果、药水、奶桶、面包的施加倍率都是
  `0.1225`，即 `movement_modifier = 0.35`。**望远镜没有任何专属数值**。
- `crossbow`：装填完成（约 25 拍）后使用窗口结束，所以整段稳态速度被拉高到 `0.159424`；
  变化点之前的加速度仍是 `0.011984`，与 `0.1225` 一致。
- `goat_horn`：**使用窗口内同样减速到 `0.1225`**（`24767..24774` 共 8 拍 `mi_z = 0.1225`），
  之后使用窗口结束（`24775` 起 `mi_z = 1.0`），玩家按 `0.546·v + 0.097982` 加速度回到行走稳态
  `0.215454`。因此"山羊角的 `movement_modifier`"与其它物品同档，只是**移动会让它的使用提前结束**；
  Round 1 的"没测到减速"是窗口到期 + 相位错位叠加的假象。
- `shield` 主手/副手用**右键**都不进入 `ItemInUseComponent` 窗口，所以 `factor = 1.0` 只代表
  "没有减速被施加"，**不能**据此说"盾牌的 `movement_modifier` 是 `1.0`"。盾牌的获取方式是潜行，
  见 §4.2。

### 4.2 盾牌：潜行格挡，不叠加物品减速（`[实测]`，`r04-017`）

| 持法 | 潜行帧数 | 使用窗口 | 行走段 factor | 加按右键后 factor |
| --- | --- | --- | --- | --- |
| 主手盾牌 | 22 | **否** | `0.3` | `0.3` |
| 副手盾牌 | 23 | **否** | `0.3` | `0.3` |

- 盾牌格挡走的是**潜行**（不是右键）：主手、副手都不产生 `ItemInUseComponent`，因此也不会有
  `0.1225`；行走乘数就是纯潜行的 `0.3`（稳态 `0.064697`）。
- 再叠加右键**不会**再降：`factor` 仍是 `0.3`，即"格挡没有专属减速，也不与物品减速相乘"。
- **没闭合的一点**：本版没有名为 `Blocking` 的 ECS 组件（`--ecs-find` 只找到
  `ShieldFlickerComponent`），而 SDK 的 `ActorFlags::Blocking = 72` 在本版本**无盾潜行时也为真**，
  所以不能用它证明"格挡状态确实建立"。本轮记录的是原始位集（有盾/无盾两组的 `adf_bits` 见 JSON），
  差异只出现在 bit 73，映射关系未定，列为边界。

> 物品数值属于**物品数据**而不是这三套系统的代码，所以本表是 `[实测]` 权威；代码侧只证明
> "读哪个字段、缺省多少、什么时候跳过"。

---

## 5. 与潜行、疾跑、空中、斜走的组合

### 5.1 潜行与迅捷潜行：**相乘**

| 配置 | factor（`mMoveVector.z`） | 地面首拍加速度 | 与 `0.1225 × 潜行乘数` 对照 |
| --- | --- | --- | --- |
| 开镜行走 | `0.1225` | `0.012025` | — |
| 开镜 + 潜行 | `0.03675` | `0.003602` | `0.1225 × 0.3 = 0.03675` ✅ |
| 开镜 + 潜行 + 迅捷潜行 III | `0.091875` | `0.008978` | `0.1225 × 0.75 = 0.091875` ✅ |

三档比值 `0.03675/0.1225 = 0.30000`、`0.091875/0.1225 = 0.75000`，**是乘积不是取最小、也不是覆盖**。
机制上也必然如此：`TickServerMoveInputHandler`(3) 先把 `0.3/0.75` 乘进 `mMoveVector`，
`ItemUseSlowdownApplySystem`(20) 在同一向量上再乘 `m²`，两次乘法作用在同一个字段上。
（迅捷潜行 III 的 `0.75` 由 `enchant @s swift_sneak 3` + 皮裤实测回读 `SwiftSneakComponent = 3` 保证。）

### 5.2 疾跑：不能起跑 / 开镜有 1 拍过渡 / 松手下一拍恢复

段间无 `input.w` 空档（本轮 `input_w_gaps = 0`），所以下表描述的是**真正的无缝转换**：

| 场景 | `sprint` 位 | factor |
| --- | --- | --- |
| 疾跑 14 拍（未开镜） | 14 拍全 `1`，速度爬到 `0.28052` | `1.0` |
| 同拍追加 `rmb`（开镜第一拍） | **仍为 `1`**，速度保持 `0.28064` | `1.0` |
| 开镜第二拍起 | `0` | `0.1225` |
| 继续 `W + rmb` | 0 | `0.1225` |
| 松手只按 `W` | 0 | `1.0` |
| 松手后 `W + Sprint` | 第 **1** 个记录拍即为 `1`（`first_sprint_offset = 1`） | `1.0` |

- 起跑被拒是静态门控：`IntentSprintTriggerSystem` 里 `test bpl, bpl`（`0x14b1a4d11`）判
  `ItemUseSlowdownModifierComponent` 存在即拒绝（[`sprint-12651.md`](sprint-12651.md) §2.1）。
  因为 Clear 在注册序 34、Intent 在 24，**只要当拍在用物品，起跑判定一定看到组件**。
- **开镜不是"当拍"取消疾跑，而是 1 拍过渡**：实测开镜那一拍 `sprint=1`、`mi_z=1.0`、
  速度维持在 `0.28064`，从下一拍起才 `sprint=0` 且 `mi_z=0.1225`（速度 `0.28064 → 0.16516`）。
  这和"右键按下在 `TickServerMoveInputHandler` 之后才被使用系统看到"是同一类相位关系；
  Round 1 的"当拍取消"是段间松键造成的假象（W 先断了，疾跑自然结束）。
- 过渡拍**没有任何超调**：速度没有超过前一段的 `0.28064`，只是把已有的疾跑速度多维持了一拍。
- 恢复：松手后**下一拍**就能重新起跑（`first_sprint_offset = 1`），
  速度序列 `0.02649 → 0.02649 → 0.14185 → 0.20483 → 0.23926 → …`，
  首个疾跑加速拍 `0.14185` 与正常疾跑起步同量级（`0.1274`）。

### 5.3 空中：同样减速，跳跃不减速

| 配置 | factor | 空中加速度 `a=v'−0.91v` | 与空中行走比 |
| --- | --- | --- | --- |
| 空中行走（基线） | `1.0` | `0.019601` | `1.000` |
| 开镜 + 空中前进 | `0.1225` | `0.002401` | `0.1225` |
| 开镜 + 空中 + 疾跑键 | `0.1225` | `0.002401` | `0.1225` |

- 空中减速**与地面同倍率**，说明 `mMoveVector` 的消费点确实没有 `on_ground` 分支
  （与 [`sneaking-crawling-12651.md`](sneaking-crawling-12651.md) §3.3 的结论一致）。
- **持续开镜起跳**（本轮重点复核）：`rmb` 全程不松，起跳拍的 `mi_z` 仍是 `0.1225`、
  `vy` 首拍 `0.419998`、`sprint=False`；`in_use_on_jump = True`。
  也就是说"开镜不影响跳跃初速"是在**真正开镜**的状态下测到的，不是 Round 1 那种
  "起跳后才补按右键"的假象。
- 空中再按疾跑键无效：`sprint` 位全程 `0`（两个变体都是 0）。

### 5.4 45° 斜走：先归一化，再乘

- `W + A` 同时按住（`forward=1.0, strafe=1.0`）：factor `0.086621`，
  `0.086621 / 0.1225 = 0.707110`，即 `1/√2 = 0.707107`；稳态 `0.026413`。
- 说明顺序是"输入向量归一化 → 潜行/物品乘数"，不是"先乘再归一化"（后者会让倍率仍等于 `m²`）。

### 5.5 开镜中切快捷栏：取决于新物品能否维持使用

**单个 96+ 拍窗口**内完成 5 个槽位切换（中途不调用 `set_input`），逐槽断言
`inventory().selected_slot == 目标槽`，段间无 `input.w` 空档：

| 采样槽位 | 持有物 | 使用窗口 | factor |
| --- | --- | --- | --- |
| 0 | `spyglass` | 是 | `0.1225` |
| 1 | `bread` | 是 | `0.1225` |
| 2 | `shield` | **否** | `1.0` |
| 3 | `goat_horn` | **是** | `0.1225` |
| 4 | `stone` | **否** | `1.0` |

- 切到**能维持使用的物品**（面包、山羊角）时，使用窗口与 `0.1225` 都保持；
- 切到**不能维持使用的物品**（盾牌、石头）时，使用窗口**结束**、乘数回到 `1.0`；
  再切回可使用的物品会重新起用并恢复 `0.1225`（`mi_z` 序列里 `1.0 → 0.1225` 的跳变可见）。
- 因此"靠切槽甩掉减速"只在切到**不能使用的物品**时成立，而那时你也已经不在使用任何物品了；
  想"边保持开镜边全速"做不到。
- Round 1 的"切槽不打断、盾牌被接手"是**槽位整体错位一格**导致的误读（`select_slot` 在窗口到期后
  才执行，标签全部后移），已在本轮修正；详见 §10 勘误。

---

## 6. 「望远镜加速」（Spyglass Glitch）存废结论

**结论：`1.26.51.01` 中不存在可复现的"望远镜加速"。** 逐条排除（每条都有对应的代码路径或逐拍数据）：

| 可能的加速来源 | 检查结果 | 证据 |
| --- | --- | --- |
| 望远镜有专属的乘数（`0.1` / `>1`） | 实测 `√0.1225 = 0.35`，与弓/面包完全相同 | `[实测]` |
| 乘数被反向应用（`>1`） | 游戏侧只有 `0.35` 默认值、物品值和 `1.0` 跳过三支；`m²` 对 `m ∈ {0.35, 1}` 单调不增 | `[代码]` |
| 松手后乘数残留 | 松手段 `ItemInUseComponent` 为假，`mi_z` 下一拍即 `1.0`；Clear(34) 每拍清表 | `[代码]` `[实测]` |
| 起用当拍"免检"（先加速再减速） | 开镜首拍 `mi_z` 就是 `0.1225`（Apply 在输入之后） | `[代码]` `[实测]` |
| 与疾跑叠加出 `1.3×` | 开镜那一拍仍是 `sprint=1 / mi_z=1.0`，但速度不增（维持 `0.28064`），下一拍起 `sprint=0`；起跑被门控拒绝 | `[代码]` `[实测]` |
| 只在空中不减速 | 空中加速度同为地面倍率；**持续开镜起跳**的起跳拍 `mi_z=0.1225`、`vy=0.419998` | `[实测]` |
| 切槽/切物品绕过 | 只有切到"不能维持使用"的物品才结束减速（那时已不在使用）；切到可继续使用的物品仍是 `0.1225` | `[实测]` |
| 高频点按（`rmb` 抖动） | 逐拍在 `0.1225` / `1.0` 间切换；55 拍里**没有一拍**超过 `0.546·v + 0.097982`，最高 `0.200439` < 行走稳态 `0.215820` | `[实测]` |
| 点按 + 疾跑键（`W+Sprint+rmb` 交替） | 58 拍、`sprint` 25 拍（只在不按右键的拍生效），速度**没有一拍**超过 `0.546·v + 0.1274` | `[实测]` |

点按段的关键逐拍数据（`tap_spam`，6 组 `开2拍/关2拍`，段间无空档）：

| tick | `mi_z` | 速度 |
| --- | --- | --- |
| `25656` | `0.1225` | `0.0` |
| `25657` | `0.1225` | `0.01196` |
| `25658` | `0.1225` | `0.01855` |
| `25659` | `0.1225` | `0.02209` |
| `25660` | `1.0` | `0.02405` |
| `25661` | `1.0` | `0.11121` |
| `25662` | `1.0` | `0.15869` |
| `25663` | `1.0` | `0.18469` |

每个 `1.0` 拍只是"从当前速度按行走加速"（`0.02405 → 0.11121 ≈ 0.546 × 0.02405 + 0.097982`）。
脚本对两个点按变体的每一对相邻拍都做了 `v' ≤ 0.546·v + a` 的扫描（普通版 `a=0.097982`、
疾跑版 `a=0.1274`），**违反数都是 0** —— 这就是"没有免检拍、没有额外加速源"的数值证明。
注意普通版的最高速度 `0.200439` 已经接近行走稳态 `0.215820`：因为"关镜"的拍数足够长时，
玩家本来就能按正常行走速度加速到接近稳态，这不是加速而是"没在用物品"。

**那社区看到的"望远镜 glitch"可能是什么？** 本课题只能给出可证伪的边界：

- 望远镜**确实**有独立的镜头 FOV 缩放与"开镜期间手部动作"，但那是相机/渲染路径，不是移动倍率；
  本课题没有测 FOV（不在移动管线内）。
- 开镜**不影响跳跃初速**（`vy = 0.42` 逐位相同），也不影响台阶/碰撞解算，所以"开镜跳"在几何上
  与普通跳一致，只是水平操控被压到 `12.25%`。
- 换言之：如果历史版本里存在过真正的望远镜加速，`1.26.51.01` 的三系统 + `m²` 施加点已经把
  "乘数 >1"和"残留"两条通道都堵死了。任何声称的加速都能用本文的 `mi_z` 口径逐拍证伪。

---

## 7. 未闭合与如实边界

1. **组件本身不可在线观测**：`ItemUseSlowdownModifierComponent` 是单拍瞬态（Clear 在查询前清空），
   watch 与 `ecs_component` 都读不到。本文的"施加倍率"来自 `MoveInputComponent+0x28`，
   **不是**直接读组件；两者的一致性由静态代码（`movss [rdx] → m²→ +0x28`）保证。
2. **物品数值在数据侧**：二进制里没有 `movement_modifier` 字样（只有旧的 `use_duration` 键），
   `items.brarchive` 也不含内置物品。逐物品数值只能是 `[实测]`；代码侧只闭合"读哪、缺省多少、何时跳过"。
3. **盾牌的"格挡状态"无法从 ECS 证明**：本版没有名为 `Blocking` 的组件，SDK 的
   `ActorFlags::Blocking = 72` 在无盾潜行时同样为真（有盾/无盾的原始位集见 JSON，差异只在 bit 73）。
   因此 §4.2 的结论限于"**不进入 `ItemInUseComponent`、乘数就是纯潜行 `0.3`**"，
   不宣称"格挡已建立"或"格挡无专属减速"之外的机制。
4. **山羊角/弩的使用窗口会提前结束**：山羊角在前进约 8 拍后结束使用（`mi_z` 由 `0.1225` 跳回 `1.0`），
   弩在装填完成后结束。它们"为什么结束"没有静态闭环，只记录了逐拍事实。
5. **刷子未纳入 Round 4 矩阵**：Round 1 曾对 `minecraft:suspicious_sand`（方块回读一致）尝试，
   没有取得使用窗口；本轮矩阵不含刷子，因此它的 `movement_modifier` 仍是未测。
6. **只测了迅捷潜行 III**（`0.75`）；I/II 的 `0.45/0.60` 由 [`sneaking-crawling-12651.md`](sneaking-crawling-12651.md)
   的独立结论支撑，本课题未重测。
7. **未覆盖**：创造飞行、骑乘/载具、游泳、鞘翅、梯子/攀爬、FOV/相机、多人服务端权威移动。
8. 疾跑恢复的 `first_sprint_offset = 1` 与"开镜 1 拍过渡"都是按记录拍表述；`use_state()` 查询
   本身有几十毫秒的壁钟开销（所以每个窗口都超发 `HOLD_SLACK=20` 拍），不细分亚拍时序。

---

## 8. 复现

```bash
# 1. 起游戏并进世界（本机实例路径见 docs/mod-build-environment.md）
py -3.11 driver/dev.py run --world "超平坦测试" --allow allow_input_inject=1

# 2. 让窗口在前台；未聚焦时世界仍 tick，但注入的移动会被丢弃
py -3.11 driver/dev.py focus

# 3. 跑探针（原始 JSON 写到 out/spyglass-item-use-1.26.51.01.json）
py -3.11 skills/bedrock-re/findings/1.26.51.01/cases/spyglass-item-use-12651/probes/verify_spyglass_item_use.py

# 4. 账本口径（probe / claim / close 见 skills/bedrock-re/SKILL.md）
py -3.11 driver/landing.py probe --case spyglass-item-use-12651 --scope "..." -- \
  py -3.11 skills/bedrock-re/findings/1.26.51.01/cases/spyglass-item-use-12651/probes/verify_spyglass_item_use.py
```

探针的硬断言（失败即 `exit != 0`，不出数）：

- 场地：先 `fill air` 再回读 9 个点，全部必须是 `minecraft:air`（Round 1 的残留沙块曾堵住跑道）；
- 每段前置 `settle(on_ground=True, input_sprint=False)` 且 `ItemInUseComponent` 必须为假；
- 任何 `factor < 1` 的行必须有 `ItemInUseComponent` 窗口，且行走段 `input_w_gaps == 0`；
- `spyglass` 与 `golden_apple` 必须在**行走段**仍处于使用窗口；
- `spyglass` 的施加倍率必须是 `0.1225 ± 1e-4`，且与 `bread` 一致；
- 疾跑段至少 10 拍 `sprint=1`，开镜段的 `sprint` 存活拍数 ≤ 3（防止"根本没取消"）；
- 切槽段逐槽 `selected_slot` 必须等于目标槽，且整段 `input_w_gaps == 0`；
- 起跳拍必须 `in_use_on_jump=True`（右键未丢）、`input_w_gaps == 0`；
- `swift_sneak` 必须回读到 `3`。

---

## 9. 与 Java 版对照

| 维度 | Java（1.21.x `UseModifiers`） | 基岩 `1.26.51.01` |
| --- | --- | --- |
| 数据来源 | 物品组件 `use_modifiers.movement_modifier` | 同名物品组件，`+0x48` float + `+0x4C` 标记 |
| 缺省 | `1.0`（不减速） | **`0.35`**（缺组件时也减速） |
| 施加方式 | 直接乘水平输入 | **先平方再乘**（`m²`），所以 `0.35 → 0.1225` |
| 生效范围 | 移动输入 | 地面与空中同一乘数；跳跃冲量不受影响 |
| 清除时机 | 停止使用时 | 每拍末整表清空（单拍瞬态） |

> 上表的 Java 列只用于对照阅读；本文没有对 Java 做逆向，不作为证据引用。

---

## 10. 勘误

| 编号 | 原记录 | 现结论 | 依据 |
| --- | --- | --- | --- |
| `erratum_of: r01-015`（`r02-016`） | 点按段最高速度 `0.10767` | 全段最高 `0.154419`（Round 4 复测为 `0.200439`）；"无越界拍"的结论不变并加强为逐拍扫描 | 全段统计 vs 中途裁切 |
| `erratum_of: r01-002`（本轮） | "描述符声明的大小是 `0x20`" | 组件真实大小 **`4` 字节**（`shl eax, 2` 步长）；`0x20` 是 `std::string_view` 的**类型名长度**（32 字符） | `0x143292868..0x143292873` 与 `0x14c5f5ee2` |
| `erratum_of: r01-003`（本轮） | "`itemStack->vtable[0xc0]`"、"`0x14c5f5e3c` 跳过写入" | 入参是 **`Item*`**；`vtable[9] = Item::isComponentBased()`、`vtable[24] = Item::getComponent(HashedString const&)`；`0x14c5f5e3c` 是 `je` 到 `0x14c5f5e4c`，**只跳过取物品乘数**，非组件化物品仍带 `0.35f` 写入组件 | SDK `Item.h` L159/L189 + 反汇编 |
| `erratum_of: r01-004`（本轮） | "`VanillaClientGameplayComponent+0x00` 语义未定" | 该字段是 **`int mSprintTriggerTime`**（结构体首成员）：每拍施加物品减速都会清零双击前进的疾跑计时器 | `VanillaClientGameplayComponent.h` |
| `erratum_of: r01-009`（本轮） | "山羊角/盾牌/刷子未取得使用窗口" | 山羊角在持续按住右键时**会**进入使用并减速 `0.1225`（前 8 拍），随后移动结束使用；盾牌确认**永不**进入 `ItemInUseComponent`（潜行格挡，乘数 `0.3`）；刷子仍未测 | `r04-017` |
| `erratum_of: r01-010`（本轮） | "疾跑中开镜**当拍**取消疾跑" | 开镜那一拍仍 `sprint=1 / mi_z=1.0`（速度维持 `0.28064`），**下一拍**才取消并施加 `0.1225`；Round 1 的 0 拍是段间松键造成的 | 无缝窗口 + `input_w_gaps=0` |
| `erratum_of: r01-014`（本轮） | "开镜中切槽不打断使用窗口，盾牌被接手（`0.1225`）" | Round 1 的 `select_slot` 在窗口到期后才执行，**四槽标签整体错位一格**。真实行为：切到可继续使用的物品（面包、山羊角）保持 `0.1225`；切到不能维持使用的物品（盾牌、石头）使用结束、回 `1.0` | 单窗口 5 槽 + 逐槽 `selected_slot` 断言 |
| `erratum_of: r01-001`（本轮，方法） | 段间靠窗口到期切换相位 | 改为"超发窗口 + 到期前覆写"（`HOLD_SLACK`）并断言 `input.w` 无内部空档；Round 1 在 `sprint_then_scope`/`air`/`sneak_ss3`/`hotbar` 都有 3~9 拍空档 | `r04-017` 全部段落 `input_w_gaps=0` |
