# resume: input-joystick-strafe-12651

- version: `1.26.51.01`
- goal: 返工修正：静态+多点实测闭合 0x144761610 望远镜阻尼 5/D（OptionID 0x256）、撤回死区过度外推并追查前端输入链、补齐 0x1404B91F0 InputMode 分流与 0x187 选项归属、修复 S4 Pitch 折回与最终 JSON 完整性
- environment: -
- current round: 3
- last record: r02-028
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/input-joystick-strafe-12651/journal.jsonl`

## Confirmed (static closure required)

- r01-007: 移动链没有摇杆死区、轴向死区或重新映射（设备层的死区不在游戏代码内） [constant; 全库 --find-str deadzone/dead_zone/DeadZone 均 0 命中；无对应选项；RawMoveInputComponent(0x3613E513) 全部 8 处 .text 引用（0x1432973B5/0x143297415/0x1432979D4/0x14475ACF1/0x146694102/0x1466942E1/0x146695C8B/0x14669E423）均为描述符/诊断/拷贝，无死区或缩放。移动链上唯一的模拟变换是两处 “>1 才归一化”。]（见 r02-024 勘误）
- r01-008: 纯键盘多键在输入层精确相加后归一化：W+A+D 折叠为纯前进、W+S+A 折叠为纯左、A+D 与 W+S 归零，斜走与直行同速 [code; 0x140449655..0x140449695 对角位与正交位同一累加器；0x140449728 零向量短路；0x140449755 统一 sqrtss 归一化。r01-002 逐组回读 14 组 flags 与 mMove，稳态位移 W=+0.2139 / W+A+D=+0.2148 / W+A=(+0.1513,+0.1513)。]
- r01-009: 非 45° 角需要对角位与正交位同时置位（(1,2)→26.565°）；纯键盘到不了，只有触屏角键加键盘或多点触控才能触发 [code; 对角位 0x200/0x400/0x800/0x1000 与正交位 0x2000..0x10000 相加后统一归一化（0x140449655..0x140449728）；触屏侧存在 textures/gui/controls/{up_left,up_right,down_left,down_right} 与 button.look_* 控件族，键盘只注册正交方向。26.565° 数值为整数加法 + sqrtss 归一化推导，本机无触摸设备未实测。]
- r01-010: 灵敏度公共曲线 v = 0.81f × powf(1.1f × S, 0.6125f)，三端选项共用同一 OptionID 0x183 [code; 0x14217A339 读 [entry+0x14] → 0x14217A33E ×1.1f(0x14E7C4BFC) → 0x14217A34E powf(_,0.6125f@0x14E7C4C00) → 0x14217A388 ×0.81f(0x14E7C4C04) → 0x14091CEB0 写回。0.6125f/0.81f 全库唯一。三处注册 0x144E05675/0x144E06055/0x144E06B7E 都是 mov edx,0x183。实测 S=0.5→k=332.6099、S=0.2→115.4147，比值 0.346997 vs 模型 0.347132。]
- r01-011: 键鼠转角 Δ(deg) = dx × 9600f × (0.15f + 0.6f·v)³ × 0.3f ÷ W，W 是物理客户区宽度；横向与纵向系数相同 [code; 0x1404B9695..0x1404B96C3 乘 0.6f/加 0.15f/立方/乘 9600f(0x14E760F10)/分别乘 [this+0x34] 与 [this+0x38]；0x1404B9B72 乘 0.3f(0x14E758C38)。实测 dx 线性、dy 与 dx 同系数（差 1.5e-5°）、三种窗口尺寸下 slope×W 守恒 332.6095/332.6099（W=2559 与 1706），只改高度斜率不变。函数内无 divss，/W 的除法指令在原始增量写入点上游，unresolved。]
- r01-012: 望远镜开镜把视角速度压到基准的 1/10（FOV 比值阻尼），松手立即恢复 [code; 0x144761610：0x144761742 ucomiss fovB,fovA → 0x144761751 divss 得 fovA/fovB → 0x144761759 mulss 到 look 增量 → 0x144761778 进 0x1447617C0。实测两次独立运行比值 0.100001174 与 0.099997632，ItemInUseComponent 存在期间成立、松开后 Δyaw 回到 12.992554。]（见 r02-022 勘误）
- r01-013: Pitch 钳位 = 下限 −89.9f 精确、上限 90°−0.1° 表达（比 89.9f 低 1 ULP） [constant; 89.9f 全库唯一出现在 0x14E9A0D2C（相邻 0x14E9A0D28 为 −89.9f），唯一引用 0x14677EF11；0x14677EF1E maxss 下限、0x14677EF3D 的 0.00174534f(0.1°) 经 cmpltss/andps/andnps 取上限。实测终点 0x42B3CCCC=89.89999389648438 与 0xC2B3CCCD=−89.9000015258789。]
- r02-022: 0x144761610 取的是 Options::getSpyglassDamping(InputMode)（vtable+0x1A0 = 0x140FB5940，读 OptionID 0x256 的按端值），因子 = min(1, 0.05/D)；第 1 轮把它误读为 FOV 比值 [code; vtable 基址 0x14E79FF10（380 槽、无 RTTI），+0x1A0=0x140FB5940：0x140FB5963 getOption(0x256) → 0x140FB5993..0x140FB59D8 以调用方 int 的 4 字节做 FNV-1a 查表 → 0x140FB5A29 movss xmm0,[entry+0x14]，未命中回退 [opt+0x14]。0x144761610：分子 xmm6 取自 [rbx+8]->vtable[+0x228]()；0x144761742 ucomiss xmm0,xmm6 / 0x144761745 jbe → D<=xmm6 时 factor=1.0f；否则 0x144761751 divss xmm6,xmm0 → factor=xmm6/D。四点实测反解 xmm6=0.05：D=0.0→0.999996617、0.04→1.000000000、0.5→0.100001174、1.0→0.050000000。与 FOV 无关。]
- r02-023: Wiki 的望远镜阻尼 D ≤ 5 → 1 / D > 5 → 5/D 在 1.26.51.01 成立（D 取百分数；代码比较点是 0.05f），消费点是 Options::vtable[+0x1A0] [code; 同上：0x144761742..0x144761755 的 min(1, 0.05/D)；OptionID 0x256 由 0x144254987/0x140C0117D/0x147795131 三处注册（keyboardAndMouse.spyglassDamping、touch.spyglassDamping、controller.spyglassDamping 与 spyglass_mouse_dampening）。四点实测阈值两侧突跳（0.04→1.0、0.5→0.100001174），且 1.0→0.05 证明是 0.05/D 而非线性。]
- r02-024: 收窄：只有 ECS/服务端移动合成层（0x1404495E0、0x148B2D870、RawMoveInputComponent）可证不做死区裁剪；客户端手柄/触屏硬件前端的死区未定位，不能用字符串缺失推断代码缺失 [code; 0x1404495E0 全函数与 0x148B2D870 的拷贝段只有「len²>1.0f 才归一化」这一处模拟变换；RawMoveInputComponent(0x3613E513) 全部 8 处 .text 引用（0x1432973B5/0x143297415/0x1432979D4/0x14475ACF1/0x146694102/0x1466942E1/0x146695C8B/0x14669E423）均为描述符、诊断字符串与字段拷贝，无死区或缩放。前端：本版 --iat XInput/GameInput 均 0 命中，left_stick_{up,down,left,right} 只以方向动作名出现（0x144377402 等），摇杆轴数值常量未定位 ⇒ 前端 unresolved。]
- r02-025: 0x1404B91F0 里横向额外因子 0.7f 的门控是布尔选项 hotbar_only_touch（OptionID 0x1DB），不是 InputMode；edi = bool|2 只能是 2 或 3 [code; 0x1404B95A7 getOptions → 0x1404B95C1 mov rdx,[options_vtable+0x288]（=0x140FB63E0，读 OptionID 0x1DB 的 byte [opt+0x10]）→ 0x1404B95D4 movzx edi,al / or edi,2 → edi∈{2,3}；0x1404B9A80 cmp edi,3 / je → 0.7f；cmp edi,2 / jne → 1.0f。0x1DB 的注册名是 hotbar_only_touch（0x144E072C5/0x144E072DF）。默认关闭 ⇒ 实测 dy 与 dx 的 deg/px 相同（差 1.5e-5°）。]
- r02-026: 0x1404BF490（slot 14，0.4f·|accum|^1.2f/(n+1)）的分支门是 OptionID 0x187 = keyboard_show_full_keyboard_options，选项为假才走这支；鼠标不经过它 [code; 0x1404BFB16 getOption(0x187) → 0x1404BFB43 cmp byte [opt+0x10],0 / jne 0x1404BFDED。0x187 注册名 keyboard_show_full_keyboard_options（0x144E05B86 写字符串、0x144E05BB1 写 edx=0x187）。r02-018 的 dx=50/100/200 严格线性，若过 |x|^1.2 不可能线性 ⇒ 该分支对键鼠不生效；真实归属 unresolved。]

## Open / pending

- r01-014: 触屏每 tick 转角公式与手柄摇杆死区/曲线在 1.26.51.01 中仍可定位到具体分支 (inconclusive)
- r01-016: 1.26.51.01 live: MoveInputComponent synthesis (14 keyboard combos), analog pass-through/normalize (10 cases), mouse deg-per-pixel + sensitivity curve + pitch clamp + spyglass FOV damping (inconclusive)
- r02-017: r01-002（全量矩阵）与 r01-003（窗口缩放）的实测数据是本 case 全部 [实测] 数字的唯一来源 (live-only)
- r02-027: Pitch 上下钳位用 <180° 步进可稳定复现：+1000px 触顶 +89.89999389648438 后连加 +500px 两次仍停在上限；-1000px 触底 -89.9000015258789 后连加 -500px 两次仍停在下限 (live-only)
- r02-028: r02-018（全量）、r02-019（damp=0.0 + sens=0.2）、r02-020（damp=0.04）、r02-021（damp=1.0）的实测数据是返工轮全部 [实测] 数字的唯一来源，且窗口尺寸非零（2560×1494） (live-only)

## Retracted / errata

- r02-022 (erratum of r01-012): 0x144761610 取的是 Options::getSpyglassDamping(InputMode)（vtable+0x1A0 = 0x140FB5940，读 OptionID 0x256 的按端值），因子 = min(1, 0.05/D)；第 1 轮把它误读为 FOV 比值 [confirmed] — 原命题: 望远镜开镜把视角速度压到基准的 1/10（FOV 比值阻尼），松手立即恢复
- r02-023 (erratum of r01-015): Wiki 的望远镜阻尼 D ≤ 5 → 1 / D > 5 → 5/D 在 1.26.51.01 成立（D 取百分数；代码比较点是 0.05f），消费点是 Options::vtable[+0x1A0] [confirmed] — 原命题: spyglassDamping 选项按 D ≤ 5 → 1 / D > 5 → 5/D 缩放阻尼
- r02-024 (erratum of r01-007): 收窄：只有 ECS/服务端移动合成层（0x1404495E0、0x148B2D870、RawMoveInputComponent）可证不做死区裁剪；客户端手柄/触屏硬件前端的死区未定位，不能用字符串缺失推断代码缺失 [confirmed] — 原命题: 移动链没有摇杆死区、轴向死区或重新映射（设备层的死区不在游戏代码内）

## Next actions

- closed

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/input-joystick-strafe-12651/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/input-joystick-strafe-12651/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/input-joystick-strafe-12651/probes`
