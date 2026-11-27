# resume: sneaking-crawling-12651

- version: `1.26.51.01`
- goal: 独立复核静态补正：补齐 SneakingComponent 主消费点 TickServerMoveInputHandler 0x1404495e0（解释爬行加速与同拍 2 拍滞后），并更正 0x141A1C130 跳表为 ActorDataIDs [38,53,54]
- environment: -
- current round: 5
- last record: r05-073
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/sneaking-crawling-12651/journal.jsonl`

## Confirmed (static closure required)

- r02-024: 【勘误 r01-003】姿态判定静态闭合在 0x148FEA2E0：对 AABB 依次做站立 1.8f、潜行 sneakHeight(1.49f)、 爬行 0.6f 三个级联碰撞盒，每个盒六面各内缩 0.01f，命中即清 [rsi+0x0C]/[rsi+0x0D]/[rsi+0x0E]； 真实净空门槛是 1.79 / 1.48 / 0.59。 [code; pe_map --func 0x148FEA2E0：0x148FEA45C 载入 1.8f(0x14E6AE49C)、0x148FEA468/0x148FEA495 载入 +0.01f(0x14E7B9B80)/-0.01f(0x14E7D5AB8) 并对 min/max 六面内缩；0x148FEA5B3 mov byte [rsi+0xC],0； 0x148FEA5CB 起用 [rbp+0x110](sneakHeight) 组第二个盒，0x148FEA742 mov byte [rsi+0xD],0； 0x148FEA75A 载入 0.6f(0x14E6AE498) 组第三个盒，0x148FEA8B8 mov byte [rsi+0xE],0。 每级命中后继续降级，未命中直接跳到 0x148FEA8BC 收尾 ⇒ 级联短路。]
- r02-025: 【勘误 r01-006】SneakMovementSystem 的防跌落门控：玩家（有 MoveInputComponent，步长 0x64）读 MoveInput+0x00 bit0（潜行按键）；只有没有 MoveInput 的非玩家才退回 ADF bit1；全函数不读 bit114； ADF bit6 置位会整段跳过夹取。 [code; pe_map --func 0x14B18BB00：0x14B18BC11..0x14B18BC32 以 stride 0x64 取 MoveInput+0x00、and 1、 写 [MoveRequest+0x55]，0x14B18BC35 jne 进入夹取、0x14B18BC37 否则跳过； 0x14B18BC8E..0x14B18BCA1 是非玩家分支（无 MoveInput）读 ADF bit1 并同样写 +0x55； 0x14B18BCA7 test byte [rdx],0x40（ADF bit6）→ jne 0x14B18BE61 跳过整段。 0x14B18BCB6/0x14B18BCF0/0x14B18BD52/0x14B18BE47 分别为 +0.025 / -0.025 / 1.01 / 0.05。]
- r02-026: 【勘误 r01-008】0x14681DA00 注册组内每个系统的 vtable 逐块复核： SneakTriggerActionSystem = 0x14EAC9BB0（slot5 0x14B190C50 / slot6 0x14B190CF0）， SneakingChangedEventSystem = 0x14EAC9BF0（slot5 0x14B191C90 / slot6 0x14B192180）； v1 把两者与相邻块整体错位一格。 [constant; pe_map --disasm 0x1468204A2：名字后分配 0x60 字节，0x1468204F7 lea rcx,0x14EAC9BB0 并 mov [rax],rcx， 0x146820501 存 [rbp+0x2370]，0x14682052D 注册；pe_map --disasm 0x146820448： 0x146820467 lea rcx,0x14EAC9BF0（SneakingChangedEventSystem 的块，名字在 0x146820416）， 0x146820471 存 [rbp+0x22A0]。其余五个系统的 vtable 同法逐块复核一致。]
- r03-056: 【勘误 r02-027】在完整全绿探针中实证：低顶强制潜行（1.5 净空、sneaking=true）不按潜行键照样越过台沿坠落，按住潜行键停在 247.266022；强制潜行基础速度与 Swift Sneak III 同为 0.3× / 0.75×，矮顶下松键保持 1.49、走出后恢复 1.8。 [code; 0x14B18BC11..0x14B18BC37（MoveInputComponent+0x00 bit0 门控防跌落）与 SneakingSystem 0x14B192C50（ADF bit1 门控 0.3+0.15×SwiftSneak 移速）。]
- r03-058: 0.01f 六面内缩三档净空阈值（1.79 / 1.48 / 0.59）经 block_shapes 回读精细边界实测全线闭合：1.8175 站立、1.750/1.500 强制潜行、1.4375/0.625 强制爬行、0.5625/0.500 无法容纳被推出。 [code; 0x148FEA2E0（1.8f / sneakHeight=1.49f / 0.6f 级联盒六面各 ±0.01f 内缩，r02-024 已给出汇编依据）。]
- r04-061: 【闭合 §1.2/§7-1】把 0.6（以及 1.49/1.8）写进 ShouldUpdateBoundingBoxRequestComponent (0x752C8E04) 与 AABBShapeComponent 的是一条"姿态参数 → 尺寸组件 → 种类派发 → 写请求 → 消费请求 → 写 AABB"的表/组件驱动链，不是某个函数里的 0.6 字面量： (1) 1.8/1.49 只以字面量出现在 PlayerBoundingBoxStateUpdateSystem（0x148FE9B5A / 0x148FE9B9A），
    0.6 只出现在爬行判定盒（0x148FEA75A）；
(2) 三者落到 ActorDataBoundingBoxComponent (0x5ACD5A3E)：+0x00 scale=1.0、+0x04 width=0.6、
    +0x08 height（站立 1.8 / 潜行 1.49 / 爬行 0.6，实测回读）；
(3) 尺寸种类派发 0x141A1C130（edx=种类，跳表 0x14E7D0400）的分支 0x141A1C175 经 0x141E15010
    按实体取该组件 +0x04/+0x08 → xmm1/xmm2；
(4) 请求写点 = 0x1419F93A0：0x1419F9536 movss [req],xmm7 / 0x1419F953A movss [req+4],xmm6 /
    0x1419F9532 mov byte [req+8],1；
(5) 消费侧 0x14289AFB0 → thunk 0x14289AEE0 → 0x14289A870 写 AABBShapeComponent
    +0x18 width（0x14289A9C0）与 +0x1C height（0x14289A9C5），再用自身 min.y 重建六面；
(6) 0x14289A870 尾部 0x14289AA60 / 0x14289AA75 把同一 (w,h) 回写尺寸组件。 [code; 数据链：0x148FE9B5A mov edi,1.8f / 0x148FE9B9A mov edi,1.49f → 0x141A1C130 movsxd rax,[0x14E7D0400 + (edx-3)*4] 跳表 → 分支 0x141A1C175 lea rdx,[rcx+8] / call 0x141E15010（按实体读 ActorDataBoundingBoxComponent 0x5ACD5A3E，stride 0x20） / movss xmm1,[rsp+0x28] / movss xmm2,[rsp+0x2c] / call 0x1419F93A0； 0x1419F93A0 内 0x1419F9532-0x1419F953A 写请求三字段； 0x14289AFB0 取 0x752C8E04 的稠密表、0x14289AEE0 存参后 call 0x14289A870； 0x14289A870 内 0x14289A8ED/0x14289A8F2 读旧 w/h 比较、0x14289A9C0/0x14289A9C5 写 AABB w/h、 0x14289AA25..0x14289AA3E 写 min/max 六面、0x14289AA60/0x14289AA75 回写尺寸组件。 边界：能力字节 → 尺寸种类枚举值（3..0x82 跳表下标与站/蹲/爬的对应）无调试符号可读，标 unresolved。]（见 r05-073 勘误）
- r04-062: 【闭合 §4.1/§7-2】0x14B18BCA7 的 `test byte [rdx], 0x40` 是 ActorDataFlagComponent 的 bit 6，对应 LeviLamina 26.51.7 SDK 的 `ActorFlags::Tempted`（枚举：Onfire=0、Sneaking=1、 Riding=2、Sprinting=3、Usingitem=4、Invisible=5、Tempted=6 …、Crawling=114）。置位即 `jne 0x14B18BE61` 跳过整段边缘夹取。该检查位于"玩家按键路径（0x14B18BC35 jne）"与 "非玩家 ADF bit1 路径（0x14B18BCA1 je 之后同址）"汇合之后，对两类实体都生效； 玩家不会带 Tempted（该位由 minecraft:behavior.tempt 的被食物吸引行为写入；二进制里 `minecraft:behavior.tempt` @0x14EBCC0C5 与 `FloatTempt` @0x14EBCC0DE 存在）， 所以实际效果只是让被吸引的生物不被潜行机制拉回台沿。同段 `test byte [rbx+0xE], 4` = ActorFlags::Crawling（bit 114），属于移速乘数门控。 [constant; 枚举来源：本机 XMAKE_PKG_INSTALLDIR 下 levilamina 26.51.7 的 include/mc/world/actor/ActorFlags.h（Onfire=0 … Tempted=6 … Crawling=114，Count=131）； 位-字节映射：bit n → byte n>>3、mask 1<<(n&7)，故 bit6 → [rdx],0x40、bit114 → [rbx+0xE],4， 与 0x14B18BCA7 / 0x14B192F70 逐字节吻合； 门控位置：0x14B18BC11..0x14B18BC35（玩家 MoveInput+0x00 bit0）与 0x14B18BC8E..0x14B18BCA1 （非玩家 ADF bit1）都汇合到 0x14B18BCA7。]
- r04-063: 【闭合 §4.2/§7-3/§7-4】SneakMovementSystem（0x14B18BB00）是三段式：X 段、Z 段、 对角段，三段共用同一个探针盒与同一份 0.05 回退量子。对角段（0x14B18C101..0x14B18C2A6） 每轮把内缩盒按 (dx,dz) 整体平移后做一次六面重叠测试；无支撑时 dx 与 dz **各减一个 0.05** （符号感知、不跨 0，0x14B18C1F0..0x14B18C287），两轴都写回 MoveRequest 再回扫， dx==dz==0 时退出；探针仍用 0.025 水平内缩，竖直下探仍用同一次算出的 1.01×maxAutoStep （[rsp+0xC]，0x14B18BD52 计算、0x14B18C118 复用）。收尾把 |dx|/|dz| ≤ 1.19209e-07 的分量清零。 [code; 0x14B18BD52 mulss [0x14E7F8A04]=1.01f → [rsp+0xC]；0x14B18BCCD/0x14B18BCF0 ±0.025f （0x14E760F30/F34）；0x14B18BD00/0x14B18BD3F 中点回退（0.5f @0x14E5C6370）； 0x14B18BEB6/0x14B18BF91 X 段 0.05 回退与写回；0x14B18BF9B.. 同构 Z 段； 0x14B18C118 subss xmm3,[rsp+0xC]（对角段复用深度）；0x14B18C159..0x14B18C195 平移盒取序； 0x14B18C1F0..0x14B18C236 与 0x14B18C23B..0x14B18C287 两轴各减 0.05； 0x14B18C28D..0x14B18C2A6 两轴非零则回扫；0x14B18C2AC..0x14B18C2EE 清零残量。]（见 r05-071 勘误）
- r05-069: 【闭合 §3.3 静态消费点】`SneakingSystem`（0x14B192C50）在 0x14B192F9A 把
`min(0.3 + 0.15 × SwiftSneak, 1.0)` 写入 `SneakingComponent`（0x8918332C）的每实体浮点池；
唯一把该浮点读出来并乘进移动输入标量的代码是 0x144A26290（缺省 0.3f @0x14E758C38，
0x144A2642C `mulss xmm6, xmm0`）。调用点 0x144A281FF / 0x144A283F9 / 0x144A285DF 都在
玩家每 tick 移动更新 0x144A27D20 内（0x144A27670 → 0x144A27D20，经 vtable thunk
0x14B177510），结果写入 `LocalMoveVelocityComponent`（0x10257C61）。该 helper 只按
`ADF bit1 (Sneaking)` / `bit57 (Swimming)` 门控，没有 on_ground 分支，因此地面与空中
共享同一条乘数链。
 [code; 写点：0x14B192F6B test byte [rbx],2（ADF bit1）/ 0x14B192F70 test byte [rbx+0xE],4
（ADF bit114）→ 0x14B192F80 cvtsi2ss 读等级 → 0x14B192F86 mulss 0.15f（0x14E6AE4DC）
→ 0x14B192F8A addss 0.3f（0x14E758C38）→ 0x14B192F91 minss 1.0f（0x14E5A7060）
→ 0x14B192F9A movss [rcx+rdi*4],xmm0。
读点：0x144A2629B movaps xmm6,xmm1；0x144A262AD call 0x141E14790（edx=1，Sneaking）
→ 0x144A262B4 je 0x144A26430 返回原值；0x144A262C2 call 0x141E14790（edx=0x39=57，
Swimming）→ 0x144A262C9 jne 0x144A26430 返回原值；0x144A26388 movss xmm0,
[0x14E758C38]=0.3f；0x144A263B9..0x144A26427 查 0x8918332C 稀疏表并
movss xmm0,[rax+rdx*4]；0x144A2642C mulss xmm6,xmm0；0x144A26430 movaps xmm0,xmm6。
调用/落点：0x144A281F4 movss xmm1,[rsi+0xF10] / 0x144A281FF call；0x144A283EE
movss xmm1,[rsi+0xF20] / 0x144A283F9 call；0x144A285D4 movss xmm1,[rsi+0xF10] /
0x144A285DF call。0x144A28204 movd r9d,xmm0 → 0x144A2821C call 0x142833A80
（0x10257C61 的 setter；0x142833B76 / 0x142833B7F / 0x142833B6B 写 +0/+4/+8）；
0x144A283FE..0x144A28542 同。0x144A27670 → 0x144A2768A call 0x144A27D20；
0x14B177510 thunk `cmp byte [rcx+0x269],0; je 0x144A27670`。
其余 12 处 0x8918332C 命中：组件描述符 0x14023D604；视图要求 0x143284AF3 /
0x14669D90B / 0x14669E0B9 / 0x148B48306；事件分发 0x146698DCF / 0x148B455B6；
未发现第二个乘法点。
]（见 r05-072 勘误）
- r05-070: 【勘误 r04-064：Swift Sneak 首拍归因 / 空中算式 / 停点符号 / 静态消费点】
(1) `Swift Sneak` 的额外倍率不是客户端装备同步的 2 拍追赶：`SneakingSystem`
（0x14B192C50，组 0x14681DA00 第 3 个）先于 `SneakTriggerActionSystem`
（0x14B190C50，第 6 个）执行，后者才写 ADF bit1；同拍 W+Sneak 首拍 0.300，
先按住潜行 6 拍或矮顶强制爬行首拍 0.750（0.073502）。Round 4 的 pre_sneak
夹具在第 N 拍自动松键，所以 sneak+ss3_held 与 sneak+ss3 逐位相同。
(2) 空中加速度按 a_i = v[i+1] - 0.91·v[i]（a_0 = v[0]，0.91f @0x14E76D2D8）
精确为 0.450 / 0.600 / 0.750，不是未扣阻力的 0.398 / 0.548 / 0.698。
(3) 45° 斜向停点中心越过台沿 +0.275（实测 (208.271042, 258.274506)，单轴
208.272644 / 258.272644，重叠 0.028958 / 0.025494 / 0.027356 ∈ [0.025, 0.05)），
不是台沿 -0.275。
(4) 静态消费点见 r05-069。
 [code; §5.1 注册顺序（0x14681DA00）：PlayerBoundingBoxStateUpdateSystem 0x14681E6CA →
SneakTriggerIntentSystem 0x14681F8EF → SneakingSystem 0x14681F92B（slot5
0x14B192C50）→ JumpInputSystem 0x1468203DA → SneakingChangedEventSystem
0x146820416 → SneakTriggerActionSystem 0x1468204A2（slot5 0x14B190C50）→
UpdateHorizontalPoseSystem 0x146820595。SneakingSystem 在 0x14B192F6B 读 ADF bit1
时，SneakTriggerActionSystem 尚未执行；乘数写 0x14B192F9A，读/乘 0x144A26290
（见 r05-069）。0.91f @0x14E76D2D8。
]
- r05-072: 【独立复核勘误 r05-069】SneakingComponent (0x8918332C) 的真正主消费点是 TickServerMoveInputHandler（注册 0x14681dcf4，vt 0x14EA34B10，slot[5] 0x148B459F0 → 0x148B45510 → 0x148B2D870 → 消费叶 0x1404495e0），而不是 0x144A26290：0x1404495e0 完整检查 MoveInput & 0x40001 || ADF bit1 (Sneaking) || ADF bit114 (Crawling)，在 isFlying / WasInWater / ADF bit57 (Swimming) 时跳过，读 [SneakingComponent]（缺省 0.3f）直接乘入 MoveInputComponent +0x24/+0x28 mMoveVector（无 on_ground 分支）。0x144A26290 只是次级消费点（仅查 ADF bit1 与 !bit57）。由于 TickServerMoveInputHandler 在 0x14681DA00 组内排在 SneakingSystem 之前、后者又排在 SneakTriggerActionSystem 之前，同拍按下 W+Sneak 会滞后 2 拍（第 1、2 拍仍读旧 0.3，第 3 拍才读到 Swift Sneak 写入后的 0.75）。 [code; 0x14681dcf4 str 'TickServerMoveInputHandler' @0x14EC07660，vt 0x14EA34B10，slot[5]=0x148B459F0 → 0x148B45510 → lambda 0x148B2D870（0x148B2DD41 取 0x8918332C 指针 → 0x148B2DDB5 call 0x1404495e0）；0x140449782 test [rdx],0x40001 / 0x14044978d test cl,2 / 0x14044979a test r8b,r8b / 0x14044979f and [r9+8],0x4000000000000(bit114) / 0x1404497cb cmp [rsp+0x28],0 / 0x1404497d2 and rcx,0x200000000000000(bit57) / 0x1404497eb movss xmm2,[rcx] / 0x1404497f9 mulss / 0x140449801 写 MoveInputComponent+0x24/+0x28。]
- r05-073: 【独立复核勘误 r04-061】0x141A1C130 及其跳表 0x14E7D0400 并不是“无调试符号的站/蹲/爬尺寸种类派发”，而是 Actor::onSynchedDataUpdate(ActorDataIDs)：解码 0x14E7D0400 全部 128 项，落入 0x141A1C175（读 ActorDataBoundingBoxComponent 写 ShouldUpdateBoundingBoxRequestComponent）的下标恰好只有 [38, 53, 54]（SDK ActorDataIDs::Reserved038/053/054 = Scale/Width/Height）。真正按姿态写入 ActorDataBoundingBoxComponent 的上游写链为 0x142A3BBC0（0x142A1DBA0 → 0x143DFAF20 → 0x14204C3A0 → 0x1401E8570，unresolved）。 [code; 解码 0x14E7D0400 跳表（128 个 int32 相对偏移，base+i=edx-3）：唯一跳转至 0x141A1C175 的索引为 i∈{35,50,51} ⇒ edx ∈ {38, 53, 54}；对照 refs/levilamina-26.51.7/src/mc/world/actor/ActorDataIDs.h（Reserved038=38, Reserved053=53, Reserved054=54）。]

## Open / pending

- r03-051: verify_sneaking_crawling full Round 3 live run (inconclusive)
- r03-052: verify_sneaking_crawling full Round 3 live run (inconclusive)
- r03-053: verify_sneaking_crawling full Round 3 live run (inconclusive)
- r03-054: verify_sneaking_crawling full Round 3 live run (inconclusive)
- r03-055: 【勘误 r02-028】露天水池（无 y=-37 封顶）中游泳姿态 pose_swim 为 0.6×0.6、swimming=true，state.sneaking 与 input.sneak 均为 false；Round 1/2 录到的 sneaking=true 是 y=-37 石顶造成净空 1.56 < 1.79 触发强制潜行的夹具假象。 (live-only)
- r03-057: 【勘误 r02-029】在修正 drop=1.000 夹具（由误放 stone 改为 air 露出 y=-42）后的全绿复核：边缘下方落差 ≤0.5625（0.5 半砖 / 0.5625 营火）走下去站到下层；≥0.625（0.625 阳光探测器 / 1.0 整格落差）被拦在台沿 z=247.273529。 (live-only)
- r03-059: 实机进爬行方式与爬行上台阶（r03-048）：活板门压头当拍 1.8→0.6；露天水池游出进入干 1.0 通道保持 0.6 爬行；1.0 通道内爬行可自动走上 0.375 格高阳光探测器（上方净空 0.625 ≥ 0.59）。 (live-only)
- r04-065: 【补充 r04-064 的斜角停点符号】45° 斜向贴台角的停点中心是**越过台沿**约 0.27， 不是"落在台沿−0.275"：本轮实测斜向 (x=208.271, z=258.275)、单轴 x=208.273、单轴 z=258.273 （方块台角在 (208, 258)）；对应的箱体重叠为 0.0290 / 0.0255（斜向）与 0.0274（单轴）， 全部落在 [0.025, 0.05) 区间，与"0.025 内缩 + 0.05 回退量子"的模型一致。 不按潜行键的斜向对照坠落至 feet_y=-60。 (live-only)
- r04-067: Round 5 返工实机：pre_sneak 不松键的潜行/迅捷潜行首拍对照、0.91 空气阻力下的空中加速度、45° 斜角停点与单轴对照 (inconclusive)
- r05-071: 【勘误 r04-063 的停点符号】45° 斜向贴台角的停点中心是越过台沿约 +0.275，不是
台沿 -0.275；r04-063 的三段式静态结论（X/Z/对角共用 0.025 内缩与
1.01×maxAutoStep、每轮 dx/dz 各减 0.05）不变，只更正其实测摘要。实测 r05-068：
diag (208.271042, 258.274506)、axis_x 208.272644、axis_z 258.272644（台角
(208, 258)），重叠 0.028958 / 0.025494 / 0.027356 ∈ [0.025, 0.05)，
inset_limit (208.275, 258.275)；不按潜行斜向对照 feet_y=-60。
 (live-only)

## Retracted / errata

- r02-025 (erratum of r01-006): 【勘误 r01-006】SneakMovementSystem 的防跌落门控：玩家（有 MoveInputComponent，步长 0x64）读 MoveInput+0x00 bit0（潜行按键）；只有没有 MoveInput 的非玩家才退回 ADF bit1；全函数不读 bit114； ADF bit6 置位会整段跳过夹取。 [confirmed] — 原命题: SneakMovementSystem 的边缘防跌落用「AABB 水平四边各内缩 0.025、向下扩 1.01×maxAutoStep」的探针盒 判定支撑，无支撑时按 0.05 的量子回退 mSpeed；系统在移动组内夹在 MoveCollisionSystem 与 ActorMoveSystem 之间。
- r02-026 (erratum of r01-008): 【勘误 r01-008】0x14681DA00 注册组内每个系统的 vtable 逐块复核： SneakTriggerActionSystem = 0x14EAC9BB0（slot5 0x14B190C50 / slot6 0x14B190CF0）， SneakingChangedEventSystem = 0x14EAC9BF0（slot5 0x14B191C90 / slot6 0x14B192180）； v1 把两者与相邻块整体错位一格。 [confirmed] — 原命题: 姿态/潜行组（0x14681DA00）整组先于移动碰撞组（0x14681AC20）注册，故同 tick 内姿态与乘数先算、夹取先于 ActorMoveSystem。
- r02-027 (erratum of r01-007): 【勘误 r01-007】边缘防跌落只看「潜行按键」，不看姿态：爬行不按键会掉、按住键才停； 低顶强制潜行（sneaking=true 但按键为 false）同样会掉。 [live-only] — 原命题: 边缘防跌落只看潜行状态位，不看姿态；爬行自身不继承，按住潜行键才继承。
- r03-055 (erratum of r02-028): 【勘误 r02-028】露天水池（无 y=-37 封顶）中游泳姿态 pose_swim 为 0.6×0.6、swimming=true，state.sneaking 与 input.sneak 均为 false；Round 1/2 录到的 sneaking=true 是 y=-37 石顶造成净空 1.56 < 1.79 触发强制潜行的夹具假象。 [live-only] — 原命题: 游泳姿态的 AABB 是 0.6，引擎会把 state.sneaking 置真，但潜行按键 input.sneak 仍为 false； v1 文档写的 sneaking=false / head-feet=1.719335 与原始 JSON 矛盾。
- r03-056 (erratum of r02-027): 【勘误 r02-027】在完整全绿探针中实证：低顶强制潜行（1.5 净空、sneaking=true）不按潜行键照样越过台沿坠落，按住潜行键停在 247.266022；强制潜行基础速度与 Swift Sneak III 同为 0.3× / 0.75×，矮顶下松键保持 1.49、走出后恢复 1.8。 [confirmed] — 原命题: 【勘误 r01-007】边缘防跌落只看「潜行按键」，不看姿态：爬行不按键会掉、按住键才停； 低顶强制潜行（sneaking=true 但按键为 false）同样会掉。
- r03-057 (erratum of r02-029): 【勘误 r02-029】在修正 drop=1.000 夹具（由误放 stone 改为 air 露出 y=-42）后的全绿复核：边缘下方落差 ≤0.5625（0.5 半砖 / 0.5625 营火）走下去站到下层；≥0.625（0.625 阳光探测器 / 1.0 整格落差）被拦在台沿 z=247.273529。 [live-only] — 原命题: 边缘下方低矮落差的放行边界：落差 0.5 与 0.5625 会被夹取放行、走下去站到下层； 0.625 与 1.0 被拦在台沿（下探 1.01×maxAutoStep = 0.568125）。
- r05-070 (erratum of r04-064): 【勘误 r04-064：Swift Sneak 首拍归因 / 空中算式 / 停点符号 / 静态消费点】
(1) `Swift Sneak` 的额外倍率不是客户端装备同步的 2 拍追赶：`SneakingSystem`
（0x14B192C50，组 0x14681DA00 第 3 个）先于 `SneakTriggerActionSystem`
（0x14B190C50，第 6 个）执行，后者才写 ADF bit1；同拍 W+Sneak 首拍 0.300，
先按住潜行 6 拍或矮顶强制爬行首拍 0.750（0.073502）。Round 4 的 pre_sneak
夹具在第 N 拍自动松键，所以 sneak+ss3_held 与 sneak+ss3 逐位相同。
(2) 空中加速度按 a_i = v[i+1] - 0.91·v[i]（a_0 = v[0]，0.91f @0x14E76D2D8）
精确为 0.450 / 0.600 / 0.750，不是未扣阻力的 0.398 / 0.548 / 0.698。
(3) 45° 斜向停点中心越过台沿 +0.275（实测 (208.271042, 258.274506)，单轴
208.272644 / 258.272644，重叠 0.028958 / 0.025494 / 0.027356 ∈ [0.025, 0.05)），
不是台沿 -0.275。
(4) 静态消费点见 r05-069。
 [confirmed] — 原命题: 【闭合 §3.3/§7-5】潜行/爬行/强制潜行/迅捷潜行的移速乘数在**第一拍移动**就生效，没有 1-tick 延迟： 首拍水平位移比 = 1.00 / 0.30 / 0.30 / 0.30 / 0.75（走路 / 潜行 / 强制潜行 / 爬行 / 迅捷潜行 III）， 与 0.3+0.15×等级 一致；空中控制用同一个乘数缩放空中基础加速度（走路首拍 0.0196、潜行 0.3×；迅捷潜行的空中最大逐拍增量 实测 0.398/0.548/0.698，对应 0.45/0.6/0.75 的理论档）。Swift Sneak 的额外倍率有**客户端装备同步的 2 拍追赶**：刚 /enchant+equip 后的第一次移动前两拍仍是 0.3，稳定后（预热一次移动）才在首拍就 0.75。 斜向 45° 贴台角时两轴同时被夹取，停点中心落在 台沿−0.275 的一个 0.05 量子内，不按键则照常坠落。
- r05-071 (erratum of r04-063): 【勘误 r04-063 的停点符号】45° 斜向贴台角的停点中心是越过台沿约 +0.275，不是
台沿 -0.275；r04-063 的三段式静态结论（X/Z/对角共用 0.025 内缩与
1.01×maxAutoStep、每轮 dx/dz 各减 0.05）不变，只更正其实测摘要。实测 r05-068：
diag (208.271042, 258.274506)、axis_x 208.272644、axis_z 258.272644（台角
(208, 258)），重叠 0.028958 / 0.025494 / 0.027356 ∈ [0.025, 0.05)，
inset_limit (208.275, 258.275)；不按潜行斜向对照 feet_y=-60。
 [live-only] — 原命题: 【闭合 §4.2/§7-3/§7-4】SneakMovementSystem（0x14B18BB00）是三段式：X 段、Z 段、 对角段，三段共用同一个探针盒与同一份 0.05 回退量子。对角段（0x14B18C101..0x14B18C2A6） 每轮把内缩盒按 (dx,dz) 整体平移后做一次六面重叠测试；无支撑时 dx 与 dz **各减一个 0.05** （符号感知、不跨 0，0x14B18C1F0..0x14B18C287），两轴都写回 MoveRequest 再回扫， dx==dz==0 时退出；探针仍用 0.025 水平内缩，竖直下探仍用同一次算出的 1.01×maxAutoStep （[rsp+0xC]，0x14B18BD52 计算、0x14B18C118 复用）。收尾把 |dx|/|dz| ≤ 1.19209e-07 的分量清零。
- r05-072 (erratum of r05-069): 【独立复核勘误 r05-069】SneakingComponent (0x8918332C) 的真正主消费点是 TickServerMoveInputHandler（注册 0x14681dcf4，vt 0x14EA34B10，slot[5] 0x148B459F0 → 0x148B45510 → 0x148B2D870 → 消费叶 0x1404495e0），而不是 0x144A26290：0x1404495e0 完整检查 MoveInput & 0x40001 || ADF bit1 (Sneaking) || ADF bit114 (Crawling)，在 isFlying / WasInWater / ADF bit57 (Swimming) 时跳过，读 [SneakingComponent]（缺省 0.3f）直接乘入 MoveInputComponent +0x24/+0x28 mMoveVector（无 on_ground 分支）。0x144A26290 只是次级消费点（仅查 ADF bit1 与 !bit57）。由于 TickServerMoveInputHandler 在 0x14681DA00 组内排在 SneakingSystem 之前、后者又排在 SneakTriggerActionSystem 之前，同拍按下 W+Sneak 会滞后 2 拍（第 1、2 拍仍读旧 0.3，第 3 拍才读到 Swift Sneak 写入后的 0.75）。 [confirmed] — 原命题: 【闭合 §3.3 静态消费点】`SneakingSystem`（0x14B192C50）在 0x14B192F9A 把
`min(0.3 + 0.15 × SwiftSneak, 1.0)` 写入 `SneakingComponent`（0x8918332C）的每实体浮点池；
唯一把该浮点读出来并乘进移动输入标量的代码是 0x144A26290（缺省 0.3f @0x14E758C38，
0x144A2642C `mulss xmm6, xmm0`）。调用点 0x144A281FF / 0x144A283F9 / 0x144A285DF 都在
玩家每 tick 移动更新 0x144A27D20 内（0x144A27670 → 0x144A27D20，经 vtable thunk
0x14B177510），结果写入 `LocalMoveVelocityComponent`（0x10257C61）。该 helper 只按
`ADF bit1 (Sneaking)` / `bit57 (Swimming)` 门控，没有 on_ground 分支，因此地面与空中
共享同一条乘数链。

- r05-073 (erratum of r04-061): 【独立复核勘误 r04-061】0x141A1C130 及其跳表 0x14E7D0400 并不是“无调试符号的站/蹲/爬尺寸种类派发”，而是 Actor::onSynchedDataUpdate(ActorDataIDs)：解码 0x14E7D0400 全部 128 项，落入 0x141A1C175（读 ActorDataBoundingBoxComponent 写 ShouldUpdateBoundingBoxRequestComponent）的下标恰好只有 [38, 53, 54]（SDK ActorDataIDs::Reserved038/053/054 = Scale/Width/Height）。真正按姿态写入 ActorDataBoundingBoxComponent 的上游写链为 0x142A3BBC0（0x142A1DBA0 → 0x143DFAF20 → 0x14204C3A0 → 0x1401E8570，unresolved）。 [confirmed] — 原命题: 【闭合 §1.2/§7-1】把 0.6（以及 1.49/1.8）写进 ShouldUpdateBoundingBoxRequestComponent (0x752C8E04) 与 AABBShapeComponent 的是一条"姿态参数 → 尺寸组件 → 种类派发 → 写请求 → 消费请求 → 写 AABB"的表/组件驱动链，不是某个函数里的 0.6 字面量： (1) 1.8/1.49 只以字面量出现在 PlayerBoundingBoxStateUpdateSystem（0x148FE9B5A / 0x148FE9B9A），
    0.6 只出现在爬行判定盒（0x148FEA75A）；
(2) 三者落到 ActorDataBoundingBoxComponent (0x5ACD5A3E)：+0x00 scale=1.0、+0x04 width=0.6、
    +0x08 height（站立 1.8 / 潜行 1.49 / 爬行 0.6，实测回读）；
(3) 尺寸种类派发 0x141A1C130（edx=种类，跳表 0x14E7D0400）的分支 0x141A1C175 经 0x141E15010
    按实体取该组件 +0x04/+0x08 → xmm1/xmm2；
(4) 请求写点 = 0x1419F93A0：0x1419F9536 movss [req],xmm7 / 0x1419F953A movss [req+4],xmm6 /
    0x1419F9532 mov byte [req+8],1；
(5) 消费侧 0x14289AFB0 → thunk 0x14289AEE0 → 0x14289A870 写 AABBShapeComponent
    +0x18 width（0x14289A9C0）与 +0x1C height（0x14289A9C5），再用自身 min.y 重建六面；
(6) 0x14289A870 尾部 0x14289AA60 / 0x14289AA75 把同一 (w,h) 回写尺寸组件。

## Next actions

- closed

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/sneaking-crawling-12651/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/sneaking-crawling-12651/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/sneaking-crawling-12651/probes`
