# resume: spyglass-item-use-12651

- version: `1.26.51.01`
- goal: Round 4 返工：修复 hotbar_switch 错位与无缝切换自动松键、补齐持续开镜起跳与盾牌潜行格挡实测，并修正组件 sizeof=4 / Item::isComponentBased / VanillaClientGameplayComponent::mSprintTriggerTime 静态闭合
- environment: -
- current round: 5
- last record: r04-027
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/spyglass-item-use-12651/journal.jsonl`

## Confirmed (static closure required)

- r01-003: ItemUseSlowdownSystem 默认写 0.35f，物品声明 use_modifiers 时改写为该组件的 movement_modifier，取到 1.0 时整条写入被跳过（组件不创建） [code; 视图由 0x14c60ce20 取 ItemInUseComponent(0x5b90aef)/ActorMovementTickNeededComponent(0x94154cce)/PlayerInputRequestComponent(0x6b04a058) 三个池并按最小池迭代；写入前唯一分支是 |m-1|<=2^-23f，命中即 return，因此 1.0 与“无组件”等价]（见 r04-019 勘误）
- r01-004: 每物品乘数由 minecraft:use_modifiers 组件 +0x48(float) 与 +0x4C(标记) 决定；无该组件回落 0.35f，标记为 0 时返回 1.0f [code; 该函数是 ItemUseSlowdownSystem 唯一的乘数来源（0x14c5f5e44 调用点），全库没有按物品 id 分支的减速代码；二进制中没有 'movement_modifier' 字面量（只有旧的 use_duration），说明内置物品的组件值是代码/编译期数据而非 JSON 键]（见 r04-020 勘误）
- r01-005: ItemUseSlowdownApplySystem 把乘数平方一次后乘进 MoveInputComponent +0x24/+0x28（mMoveVector.x/z） [code; 系统视图由 0x14c60fea0 描述为迭代 ActorMovementTickNeededComponent、读 ItemUseSlowdownModifierComponent、写 MoveInputComponent(0x018b1887)+VanillaClientGameplayComponent(0xa406686e)；平方是 0x14c60f084 的 mulss xmm0,xmm0，全库没有第二个乘 0.35 的点]
- r01-006: ItemUseSlowdownClearSystem 是 RemoveFromAllEntitiesSystem<ItemUseSlowdownModifierComponent> 的实例，每拍末清空整张表，组件是单 tick 瞬态、任何管道查询都读不到 [constant; 注册序 Slowdown(19) -> Apply(20) -> ... -> Clear(34)，清空发生在同一 pass 的末尾；管道查询只在 tick 边界服务，因此必然落在清空之后，只有施加后的 MoveInput 倍率可观测]
- r01-007: 三系统与相邻输入/疾跑/潜行系统同注册于 0x14681da00，顺序为 TickServerMoveInputHandler(3) -> ItemUseSlowdownSystem(19) -> Apply(20) -> IntentSprintTrigger(24) -> SneakingSystem(33) -> Clear(34) -> ActionSprintTrigger(35) [constant; 同一注册函数、同一 pass 内的追加顺序决定执行序：输入处理器先算 mMoveVector（含潜行乘数），Slowdown 写组件，Apply 平方后乘同一个向量，Intent 之后才判起跑，Clear 收尾；实测开镜首拍即为 0.1225、松手首拍即回 1.0，与该序一致]
- r01-008: wiki 流传的 0.35^2=0.1225 是“一次 0.35 + Apply 的一次平方”，不是乘了两次 0.35 [code; 写入组件的是 m（不是 m^2），平方只发生在 Apply 的 mulss xmm0,xmm0；若未来去掉该平方，同一 0.35 将给出 0.35 而不是 0.1225]
- r01-010: 使用物品减速与潜行/迅捷潜行在同一 mMoveVector 上相乘：0.1225*0.3=0.03675，0.1225*0.75=0.091875 [code; 两次乘法作用于同一组 +0x24/+0x28 且分属注册序 3 与 20，中间没有覆盖写；乘积关系由共享写入点决定，不可能是取最小或覆盖]（见 r04-022 勘误）
- r01-011: 使用物品期间无法起跑、疾跑中开镜当拍取消疾跑、松手后下一拍即可恢复疾跑 [code; Clear 在注册序 34、Intent 在 24，因此只要当拍在用物品，起跑判定必然看到组件存在；疾跑被取消后松手，下一拍组件已不存在，起跑放行]
- r01-012: 空中使用物品同样按 m^2 减速，但跳跃初速不受乘数影响 [code; 主消费点 0x1404495e0 与 Apply 的 0x14c60f080 都没有 on_ground 分支，乘数只作用于 mMoveVector；跳跃冲量由 JumpFromGroundSystem 单独写入 mPosDelta，不读该倍率]
- r01-013: 45 度斜走在使用物品时先归一化再乘减速，倍率为 0.1225*(1/sqrt2)=0.086621 [code; 倍率等于“归一化后再乘 m^2”的乘积；若先乘再归一化，斜走倍率会等于 m^2 本身（0.1225），与实测不符]
- r04-024: 方法决策：所有相位切换改为「超发窗口 + 到期前覆写」（ticks + HOLD_SLACK，到期前重新 set_input），并对每段断言 input.w 无内部空档；不再依赖 set_input(ticks=N) 的自然到期 [code; 每个窗口发 ticks+HOLD_SLACK（SLACK=20）并在消费 ticks 拍后立刻覆写，旧窗口仍有 20 拍余量，注入的按键沿不会释放；探针用记录里的 input.w 位独立复核，不依赖时序假设]
- r04-026: 持续按住右键开镜起跳：起跳拍仍在开镜状态（mi_z=0.1225、ItemInUseComponent 为真），竖直初速 0.419998 与普通跳一致，且不进入疾跑 [code; 减速只乘进 MoveInputComponent+0x24/+0x28（0x14c60f080），跳跃冲量由 JumpFromGroundSystem 写 mPosDelta，两者不共享字段；主消费点 0x1404495e0 与 Apply 都没有 on_ground 分支，因此空中同一乘数]

## Open / pending

- r04-021: 勘误 r01-009：山羊角在持续按住右键时也进入使用并减速 0.1225（约 8 拍后使用结束）；盾牌主手/副手永不进入 ItemInUseComponent（潜行格挡，乘数 0.3）；刷子仍未测 (live-only)
- r04-023: 勘误 r01-014：Round 1 的 select_slot 在窗口到期后才执行，四个切槽标签整体错位一格；真实行为是切到可继续使用的物品（面包、山羊角）保持 0.1225，切到不能维持使用的物品（盾牌、石头）使用结束并回到 1.0 (live-only)
- r04-025: 盾牌格挡是潜行触发，不走物品使用管线：主手/副手盾牌都不进入 ItemInUseComponent，行走乘数就是纯潜行 0.3，加按右键也不会再降 (live-only)
- r04-027: 高频点按叠加疾跑键（W+Sprint+rmb 交替 2 拍开关）同样不能擦出超曲线加速：疾跑只在不按右键的拍生效，逐拍速度不超过 0.546·v + 0.1274 (live-only)

## Retracted / errata

- r01-015 (erratum of -): 1.26.51.01 中“望远镜加速（Spyglass Glitch）”作为可复现的速度增益并不存在：没有乘数>1 的写入路径，也没有残留拍、免检拍、疾跑叠加或切槽逃逸 [refuted]
- r02-016 (erratum of r01-015): 勘误 r01-015 的证据数值：点按段全段最高速度是 0.154419（不是 0.10767），且 57 拍中没有任何一拍超过 0.546·v+0.097982；“望远镜加速作为速度增益不存在”的结论不变且更强 [refuted] — 原命题: 1.26.51.01 中“望远镜加速（Spyglass Glitch）”作为可复现的速度增益并不存在：没有乘数>1 的写入路径，也没有残留拍、免检拍、疾跑叠加或切槽逃逸
- r04-018 (erratum of r01-002): 勘误 r01-002：ItemUseSlowdownModifierComponent 的真实大小是 4 字节（只有 +0x00 一个 float）；描述符里的 0x20 是 std::string_view 的类型名长度（32 字符），不是 sizeof [refuted] — 原命题: ItemUseSlowdownModifierComponent (0x867fc92c) 只有 +0x00 一个 float 字段被三系统读写，描述符声明大小 0x20
- r04-019 (erratum of r01-003): 勘误 r01-003：取物品乘数的入参是 Item* 而不是 ItemStack*；vtable[9] 是 Item::isComponentBased()，vtable[24] 是 Item::getComponent(HashedString const&)；0x14c5f5e3c 的 je 只跳过取物品乘数，非组件化物品保留 0.35f 并照常写入组件 [refuted] — 原命题: ItemUseSlowdownSystem 默认写 0.35f，物品声明 use_modifiers 时改写为该组件的 movement_modifier，取到 1.0 时整条写入被跳过（组件不创建）
- r04-020 (erratum of r01-004): 勘误 r01-004：ItemUseSlowdownApplySystem 在 0x14c60f0a4 清零的是 VanillaClientGameplayComponent+0x00 = int mSprintTriggerTime [refuted] — 原命题: 每物品乘数由 minecraft:use_modifiers 组件 +0x48(float) 与 +0x4C(标记) 决定；无该组件回落 0.35f，标记为 0 时返回 1.0f
- r04-021 (erratum of r01-009): 勘误 r01-009：山羊角在持续按住右键时也进入使用并减速 0.1225（约 8 拍后使用结束）；盾牌主手/副手永不进入 ItemInUseComponent（潜行格挡，乘数 0.3）；刷子仍未测 [live-only] — 原命题: 所有进入 ItemInUse 窗口的可使用物品实测施加倍率均为 0.1225（即 movement_modifier=0.35），望远镜没有专属数值
- r04-022 (erratum of r01-010): 勘误 r01-010：疾跑中开镜不是当拍取消，而是 1 拍过渡——开镜那一拍仍 sprint=1、mi_z=1.0、速度维持 0.28064，下一拍起 sprint=0 且 mi_z=0.1225；松手后下一拍恢复疾跑 [refuted] — 原命题: 使用物品减速与潜行/迅捷潜行在同一 mMoveVector 上相乘：0.1225*0.3=0.03675，0.1225*0.75=0.091875
- r04-023 (erratum of r01-014): 勘误 r01-014：Round 1 的 select_slot 在窗口到期后才执行，四个切槽标签整体错位一格；真实行为是切到可继续使用的物品（面包、山羊角）保持 0.1225，切到不能维持使用的物品（盾牌、石头）使用结束并回到 1.0 [live-only] — 原命题: 开镜中切快捷栏不会打断使用窗口，减速乘数继续保持 0.1225；切到无法延续使用的物品（山羊角）后使用结束、倍率回到 1.0

## Next actions

- closed

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/spyglass-item-use-12651/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/spyglass-item-use-12651/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/spyglass-item-use-12651/probes`
