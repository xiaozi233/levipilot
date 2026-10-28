# tick 管线心智模型（角色名）

> 这里只写“有哪些阶段、谁读谁写、顺序由什么决定”。具体地址查 `docs/roles/<version>.json`。
> 本文件不写裸地址；顺序结论的证据等级见 `methods.md` §0。

## 1. 两条 pass，不是一条

- `@tick_entity_systems` 每 tick 进入 `@entity_systems_main_pass`（E14）和
  `@entity_systems_secondary_pass`（E1C）；客户端实测通常跑主 pass + catchup，
  correction 不一定触发（`[实测]`，用入口钩子确认当前版本）。
- catchup / correction 是独立的类别向量，**不在主 pass 的调用链上**。
- 主 pass 用 `@systems_foreach` 按类别取系统索引，**追加 + 正序** ⇒
  **tick 顺序 = 注册顺序**。`Dependencies` 只有组件访问组，没有 before/after，
  所以不要指望“依赖关系”替你排序。

## 2. 疾跑/流体链

同一 tick 内，主 pass 的注册顺序（**顺序已静态定案**）：

1. 采样输入/疾跑状态（客户端 `Player::isSprinting()` 是更早的**记录位**）。
2. `@action_sprint_trigger` → `@sprint_tick_dispatch` → `@sprint_entity_visitor`
   → `@sprint_edge_handler` → `@set_sprinting`：
   - `@sprint_edge_handler` 按 `PlayerActionComponent` 的 bit `0x200/0x400` 调
     `@set_sprinting(1/0)`；
   - 写 `@sprint_bit_store`（ADF bit3 = SPRINTING）；
   - 把 `@sprint_boost_init` 定义的共享 modifier 压进属性队列。
3. `@water_drag` / 岩浆阻力：读 ADF bit3；写位在同 tick 之前，读的是**新位**。
4. `@move_attr_aggregate` → `@ground_accel_apply`：加速度模长来自移动速度属性；
   疾跑时 `SPRINTING_BOOST`（amount 0.3、MULTIPLY_TOTAL）重算为 ×1.3。
5. 移动。
6. tick 末状态可见。

**顺序的静态证明**：`@systems_register` 在 `mode & 1` 时把系统追加到每个类别向量的
`+0x38`，`@systems_foreach` 正序；`@cat_e14` 是主 pass 类别。
`@sprint_register_wrapper`/`@sprint_register_list` 把 ActionSprintTrigger 注册进
[E14, catchup, correction, E1C]；`@water_register_callback` →
`@water_register_wrapper` → `@water_register_list` 把水阻力注册进 [E14, E1C]，mode 都是 1。
两条注册链都在同一个 setup 函数里，ActionSprint 的调用点在前，水阻力 ctor 在后。
因此 E14 向量里 ActionSprint 索引在前，写位先于阻力；调用栈只是旁证。

仍然部分静态：

- `PlayerActionComponent` 的**写者**和它在 E14 向量里的位置还没定位
  （`@player_action_writer` 为 unresolved）——这是“记录位 → ADF 写位正好 2 tick”的最后一环。
- 地面 ×1.3 的可见延迟（+1 tick）来自属性聚合，已有静态来源；
  水面 +2 tick 的状态链在写者定位前只作为已观测相位，不写成纯静态结论。
- 旧结论“观察点显示阻力写早于写位 ⇒ 读上一 tick”已撤回；观察点顺序不等于调度顺序。

## 3. 竖直/流体分支

- 水/岩浆竖直：先阻力后重力（`v' = d(v+g)` 的离散形式），具体系数在 `docs/fluid-movement.md`。
- `@swim_vertical_branch` 管 0.028 / 0.04 / 0.15 / 0.2 的分支门；0.15/0.2 不是流体上浮
  （分别是脚手架、梯子/藤蔓），不要混。
- `@levitation` 是悬浮，不是游泳上浮。
- 入水首 tick 的 0.028 闩锁静态存在，但实测未命中；不要把它写成“已验证”。

## 4. 读状态时先问三个问题

1. 这个字段是**记录位**（客户端状态）还是**阻力/物理读的位**（ADF）？
2. 这个值是 `Actor::getVelocity()`（等于下一 tick 位移）还是 `StateVectorComponent`
   的原始字段（地表约 0.546×位移，站着 vy 还可能留残值）？
3. 这个“先后”是同一线程/同一 pass 内，还是跨线程/跨 pass 的观测？

答不上来就先回静态，不要用 tick 差值补机制。

## 5. 版本检查

换版本后先跑 `roles/resolve.py --check`；任何 `unresolved` 的角色都不准猜地址。
如果注册顺序或 pass 编号变了，先重做 §1 的集合/注册点静态复核，再谈数值。
