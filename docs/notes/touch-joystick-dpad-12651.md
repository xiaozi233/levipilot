# 触屏虚拟摇杆前端、经典 D-pad 几何与鼠标 `/W` 生产者（1.26.51.01）

> 版本：`Minecraft.Windows.exe` `1.26.51.01`（LeviLamina `26.51.7`，`image_base = 0x140000000`）。
> 证据约定：`[代码]` 反汇编/反编译的控制流与数据流；`[常量]` vtable、注册表、枚举、SDK 布局；`[实测]` LeviPilot 运行期 VA hook（`allow_hook_va=1`）与 `mouse_event` 注入；`[推断]` 由直接证据补出的环节；`unresolved` 为未定位。
> 复现脚本：[`verify_touch_joystick_dpad.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/touch-joystick-dpad-12651/probes/verify_touch_joystick_dpad.py)。原始数据：gitignored `out/touch-joystick-dpad-1.26.51.01.json`，本文所有 `[实测]` 数字逐位取自该文件。
> 前置：[`input-joystick-strafe-12651.md`](input-joystick-strafe-12651.md)、[`touch-input-turn-12651.md`](touch-input-turn-12651.md)。本文 §9 含对两者的勘误，冲突处以本文为准。
> **硬件边界**：本机无触摸屏/手柄。触屏摇杆与经典 D-pad **全部为静态闭合**（`[代码]`/`[常量]`），没有任何触屏运行期数据；`[实测]` 只覆盖键鼠路径。

---

## 0. 结论摘要

| # | 结论 | 证据 |
| --- | --- | --- |
| 1 | 鼠标视角的 `/W` 在 **`MouseMapper::$tick`（`0x140362490`）**：`0x140362AE4 divss xmm1,xmm8`（y）与 `0x140362AE9 divss xmm0,xmm8`（x），`xmm8 = (float)mScreenWidth`（`this+0x08`，`0x140362A47`）。实测该字段等于物理客户区宽度 `2560` | `[代码]` `[实测]` |
| 2 | 事件为 `{Direction(3), DirectionId::Turn(1), x = dx/W, y = dy×f/W}`，`f = mYAxisInversionFactor`（`this+0x50`，`0x140362A3E imul`）。前两轮看到的「上游已取负」就是这个乘子：**默认 `−1`** | `[代码]` `[实测]` |
| 3 | `f` 由 `MouseMapper::$setMapping`（`0x140361D50`）在 `0x140362270..27C` 写入：`(MouseInputMapping::invertYAxis − 1) \| 1`，即关闭 `−1`、开启 `+1`。存档 `ctrl_invertmouse_mouse:1` 重启后实测 `f = +1`，事件 `y` 与 pitch 同时反号，yaw 不变 | `[代码]` `[实测]` |
| 4 | OptionID `0x185` 是**一个按输入模式分值的选项**，注册名三个：`keyboard_mouse_invert_y_axis`/`controller_invert_y_axis`/`touch_invert_y_axis`；按模式取值的 getter 是 `Options::vtable[+0x1D0] = 0x140FB5CB0`。把选项值写进 `InputMapping` 的函数 `unresolved` | `[常量]` `[代码]` |
| 5 | **派发链更正**：`0x145DC7B50` 是 `ClientInstance::forEachVisibleScreen`（`IClientInstance` 槽 `+0x7F0`），不是事件转发点。视角事件走 `InputHandler` 中按 `DirectionId` 注册的 5 个 `std::function`（`_Do_call` = `0x1442E4210` 族）→ `forEachScreen`（`+0x7F8`）→ `0x144B22290/0x144B22580` → 适配器 → 处理器 | `[常量]` `[代码]` |
| 6 | 处理器 slot 27 的「action 1 / action 8」就是 **`DirectionId::Turn` / `TurnAlt`**（常量烘焙在 5 个 lambda 里）；`0x144761610` 的 `this` 是 `client->getLocalPlayer()`（槽 `+0xF8`），即 `LocalPlayer::turn` | `[常量]` `[代码]` |
| 7 | **slot 15 重读**：灵敏度键 = `MinecraftInputHandler::getInputMode()`（`0x144353910`），仅当 `InputMode == Touch` 才换成 `hotbar_only_touch \| 2`；`+0x850` 那个虚调用在 `IClientInstance` 上，是 **`isTouchGameplayAllowed`**；触屏分支里的 `c` 就是 slot 15 的**浮点参数**（适配器 `0x140889540` 传入），不是 `[screen->vtable[+0xE0]()+0x3C]` | `[代码]` `[实测]` |
| 8 | **slot 14 重读**：`^1.2` 那段只受 `edx == r8d` 门控；OptionID `0x187` 的存档键是 **`ctrl_fullkeyboardgameplay`**，`0x1404BFB47` 为真直接跳到尾声，为假才进入一段「取屏上一点 → 相机反投影 → 射线检测 → 写转头角」的逻辑 | `[代码]` `[常量]` |
| 9 | `ctrl_hotbarOnlyTouch` ↔ OptionID **`0x1DB`**（`0x1420C9E64 mov edx, 0x1DB`） | `[常量]` |
| 10 | **触屏摇杆前端** = `TouchMoveAndTurnInteractControl::$tick`（vtable `0x14E76D010` 槽 5，`0x140708A60`）：半径 `R = (区域.x1 − 区域.x0)/3`；死区 **25**；幅度 `m = (min(dist, R) − 25)/(R − 25)`（线性、到 `R` 封顶）；疾跑区 **`dist > 1.05·R`** 且 `mSprintOnMovement()` 为真 | `[代码]` |
| 11 | **经典 D-pad** 是外框上的 **3×3 网格**：四个角格 `button.upleft/upright/downleft/downright` 是完整格，四个边格 `button.up/down/left/right` 是中间格并朝中心内缩 `S/22×1.01`；命中测试是闭矩形 | `[代码]` `[常量]` |
| 12 | D-pad 按钮的按压规则：每个按钮是独立控件、各记各的指针；触点带「本拍未被消费」标志，被第一个命中的非 `passThrough` 控件清零；D-pad 按钮 `promiscuous=1`、`extend=0`。⇒ 单指在格内只会按下一个按钮；两指分别在角格与边格可同时按下。角键/边格按钮名到 `MoveInputState` 位的注册点 `unresolved` | `[代码]` |
| 13 | 摇杆疾跑的计时阈值、`calibrateMoveDelta` 的完整形状、摇杆原点（固定/动态）的写入点、望远镜阻尼分子 `0.05f` 的虚函数实现：**未定位** | `unresolved` |

---

## 1. 输入架构：Mapper 家族与 SDK 对照

LeviLamina SDK 头（`mc/deps/input/`）给出了完整的类结构，二进制与之一致：

| 类 | vtable | `setMapping`（槽 1） | `tick`（槽 5） | 其它覆写 `[常量]` |
| --- | --- | --- | --- | --- |
| `MouseMapper` | `0x14E759000` | `0x140361D50` | **`0x140362490`** | `clearInputDeviceQueue 0x140362D60`、`getCursorPos 0x140362DA0`、`changeControllerId 0x140362DD0` |
| `TouchMapper` | `0x14E76CFA0` | `0x1406FDF90` | **`0x140707990`** | `clearInputDeviceQueue 0x140707CA0`、`render 0x140707CE0` |
| 手柄 Mapper（两个 vtable 共用实现） | `0x14E758E20` / `0x14E758EA0` | `0x140346910` | `0x140349430` | `changeControllerId 0x14034CE60` |

`[常量]` 这三个 vtable 的槽 10 都指向基类 `InputDeviceMapper::setWindowSize`（`0x140352BB0`）、槽 4 指向 `clearInputDeviceQueueForFrame`（`0x1401C5E40`），槽 6 的 `getInputMode` 分别返回 `1`（Mouse）/`2`（Touch）/`3`（GamePad），与 SDK `InputMode { Undefined=0, Mouse=1, Touch=2, GamePad=3 }` 一致。`[代码]` 把 `0x14E759000` 认成 `MouseMapper` 的直接依据：它的 `tick` 遍历 `MouseDevice::_inputs`（单例 `0x151D29E40`，`_inputs` 在 `+0x18`，元素步长 20 = `sizeof(MouseAction)`），`setMapping` 写 `mMouseButtonToButtonIdMap`（`+0x58`）、滚轮向量（`+0x98/+0xB0`）、`mPrimaryGameControllerId`（`+0xC8`），偏移全部与 SDK 布局吻合。

`MouseMapper`/`TouchMapper` 的字段偏移（SDK 布局，二进制已对上）：

| 字段 | `MouseMapper` | `TouchMapper` |
| --- | --- | --- |
| `mScreenWidth` / `mScreenHeight` | `+0x08` / `+0x0C` | `+0x08` / `+0x0C` |
| `mYAxisInversionFactor`（int） | `+0x50` | `+0x58` |
| 其它 | `mMouseButtonToButtonIdMap +0x58`、滚轮上/下 `+0x98/+0xB0`、`mPrimaryGameControllerId +0xC8`、`mIsClientUsingMouseInputMode +0xD0` | `mTouchControlSet +0x50`、`touchIdsWithFlags +0x60`、`mAreaFunc +0x78` |

`TouchMapper::$tick`（`0x140707990`）`[代码]`：把 `TouchControlSet` 里的触点复制进 `touchIdsWithFlags`，重建触点结果列表，然后对 `TouchControlSet` 中的每个控件调用 `control->vtable[5](control, eventQueue, set, mYAxisInversionFactor)`，即 `TouchControl::tick(InputEventQueue&, TouchPointResults&, int yAxisInversionFactor)`。

---

## 2. `/W` 生产者：`MouseMapper::$tick`

### 2.1 指令 `[代码]`

```asm
; MouseMapper::$tick  0x140362490..0x140362D5F    r14 = this, rdi = 当前 MouseAction*
0x1403627F4  movzx ebx, word ptr [rdi + 4]        ; dx = MouseAction::dx   (+0x04)
0x1403627F8  movsx ebp, word ptr [rdi + 6]        ; dy = MouseAction::dy   (+0x06)
0x1403627FC  test bx, bx                          ; dx == 0 → 0x140362A31 再看 dy；
0x1403627FF  je 0x140362A31                       ;   两者皆 0 → 0x140362B1E 发 PointerLocation 事件
0x140362A3E  imul ebp, dword ptr [r14 + 0x50]     ; dy *= mYAxisInversionFactor
0x140362A43  xorps xmm8, xmm8
0x140362A47  cvtsi2ss xmm8, dword ptr [r14 + 8]   ; W = (float)mScreenWidth
; …（环形队列取空槽，略）
0x140362AD3  movsx ecx, bx
0x140362AD6  xorps xmm0, xmm0
0x140362AD9  cvtsi2ss xmm0, ecx                   ; (float)(short)dx
0x140362ADD  xorps xmm1, xmm1
0x140362AE0  cvtsi2ss xmm1, ebp                   ; (float)(dy * f)
0x140362AE4  divss xmm1, xmm8                     ; y = dy * f / W
0x140362AE9  divss xmm0, xmm8                     ; x = dx / W
0x140362AEE  mov word ptr [rax], 0x203            ; 事件头：InputEventType::Direction (3)
0x140362AF3  mov dword ptr [rax + 4], 1           ; DirectionId::Turn (1)
0x140362AFA  movss dword ptr [rax + 8], xmm0      ; DirectionEventData::x
0x140362AFF  movss dword ptr [rax + 0xC], xmm1    ; DirectionEventData::y
```

要点：

- 走这条路的是 **`MouseAction::ActionMove (0)`** 且 `dx ≠ 0 || dy ≠ 0`；`dx == dy == 0` 时改发 `PointerLocation` 事件（光标位置）。`ActionMoveRelative (7)` 在这个函数里被跳过（`0x1403627E0 cmp ecx,ecx / jne` 之前有 `action > 6 → LABEL_7`）。
- 分母是 `mScreenWidth`，由基类 `setWindowSize(w, h)` 维护；没有除以高度、DPI 或 GUI 缩放的痕迹。
- 事件布局 `{u16 头=0x203, u32 DirectionId, float x, float y, int source, …}`，`source = mPrimaryGameControllerId`（`0x140362A4D`）。

### 2.2 `−dy` 的来源：反转乘子 `[代码]`

```asm
; MouseMapper::$setMapping  0x140361D50..0x140362331    rbx = InputMapping const&, rsi = this
0x140362270  movzx eax, byte ptr [rbx + 0xA0]     ; inputMapping.mouseMapping.invertYAxis
0x140362277  dec eax
0x140362279  or eax, 1                            ; (b − 1) | 1 ：0 → −1，1 → +1
0x14036227C  mov dword ptr [rsi + 0x50], eax      ; mYAxisInversionFactor
```

`InputMapping` 布局（SDK）：`keyboardMapping +0x00`（88 B）、`mouseMapping +0x58`（80 B），`MouseInputMapping::invertYAxis` 在其自身 `+0x48` ⇒ `InputMapping+0xA0`，与指令吻合 `[常量]`。

### 2.3 实测对质 `[实测]`

见 §7.1：两轮会话里 `MouseMapper::$tick` 入口快照的 `mScreenWidth` 都等于 `GetClientRect` 的物理宽度，默认 `f = −1`，事件 `y = −dy/W`；把存档改成 `ctrl_invertmouse_mouse:1` 重启后 `f = +1`、`y = +dy/W`。

---

## 3. `invert_y_axis`（OptionID `0x185`）链

| 环节 | 位置 | 内容 | 证据 |
| --- | --- | --- | --- |
| 注册 ×3 | `0x144E05909` / `0x144E061C2` / `0x144E06F19` | 字符串 `keyboard_mouse_invert_y_axis` / `controller_invert_y_axis` / `touch_invert_y_axis`，各自随后 `mov edx, 0x185` 调 `0x144E0B8A0` | `[常量]` |
| 存档键 | `0x1420C258D` | `ctrl_invertmouse` ↔ `options.invertYAxis`，`mov edx, 0x185`；本机存档里是按模式的三行 `ctrl_invertmouse_mouse/touch/gamepad` | `[常量]` |
| 按模式 getter | `0x140FB5CB0`（`Options::vtable[+0x1D0]`，槽 58） | `getOption(0x185)` → 对 4 字节键（`InputMode`）做 FNV-1a → 查按端表，命中用 `[entry+0x14]`，否则用选项基值 | `[代码]` |
| 消费（鼠标） | `0x140362270..27C` | 见 §2.2 | `[代码]` `[实测]` |
| 消费（触屏） | `TouchMapper::$setMapping`（`0x1406FDF90`） | 读 `TouchInputMapping::invertYAxis` 写 `mYAxisInversionFactor(+0x58)`，反汇编未展开 | `[推断]` |
| 选项 → `InputMapping` | — | 把 `Options::vtable[+0x1D0](InputMode)` 的结果写进 `MouseInputMapping::invertYAxis` / `TouchInputMapping::invertYAxis` 的函数**未定位** | `unresolved` `[常量]` |

`Options::vtable` 全表（380 槽）的「槽位 ↔ OptionID」对照见下表节选；`+0x1D0 → 0x185`、`+0x288 → 0x1DB`、`+0x1A0 → 0x256`、`+0x1B8 → 0x18D` 四个已知点全部与上一轮独立得到的结论一致。

| 槽 | 函数 | OptionID | 备注 |
| --- | --- | --- | --- |
| `+0x1A0` | `0x140FB5940` | `0x256` | 望远镜阻尼（按模式） |
| `+0x1B8` | `0x140FB5BA0` | `0x18D` | 灵敏度派生值（按模式） |
| `+0x1D0` | `0x140FB5CB0` | `0x185` | 反转 Y 轴（按模式） |
| `+0x288` | `0x140FB63E0` | `0x1DB` | `hotbar_only_touch`（布尔） |

---

## 4. 视角处理器 slot 27 / 15 / 14 再读

### 4.1 派发链 `[代码]` `[常量]`

```
MouseMapper::$tick → InputEventQueue::{Direction, Turn, x, y}
  → InputHandler：按 DirectionId 注册的 std::function（5 个 lambda，_Do_call 见下）
      0x1442E4210 (Move=0)  0x1442E42E0 (Turn=1)  0x1442E4480 (Scroll=2)
      0x1442E43B0 (TurnRaw=3)  0x1442E4550 (TurnAlt=8)
      每个 _Do_call(this=client, float const& x, float const& y, uint8 const& focus, …)
        把 {常量 DirectionId, &x, &y, &focus} 作为捕获、以适配器 0x145DDCA90 构造 std::function，
        调 client->vtable[+0x7F8]（forEachScreen，0x145DC8130）
  → 0x144B22290 → 0x144B22580：遍历场景栈上的处理器记录，0x144B225CE 调 entry->fn
  → 适配器 0x145DDCA90：edx = 捕获里的 DirectionId、xmm2 = x、xmm3 = y、cl = focus
  → 处理器 slot 27（vtable[+0xD8]）
```

`DirectionId`（SDK）：`Move=0, Turn=1, Scroll=2, TurnRaw=3, TurnUp=4, TurnDown=5, TurnLeft=6, TurnRight=7, TurnAlt=8`。5 个 lambda 的常量分别是 `0/1/8/2/3`，与之一一对应。

**slot 27**（`0x1404C02B0`）`[代码]`：`dl != 8 && dl != 1` 直接返回；`dl == 1`（`Turn`）且「本地玩家的 `vtable[243]()` 返回对象存在、其 `vtable[5]()` 非空、再 `vtable[7]() == false`」时累加 `[this+0x38] += x`、`[this+0x34] += y`；`dl == 8`（`TurnAlt`）取 `(x, −y)` 直接调 `0x144761610`。`0x144761610` 的第一参来自 `client->vtable[+0xF8] = getLocalPlayer()`，所以它是 `LocalPlayer::turn`，不是「screen」。

### 4.2 slot 15（`0x1404B91F0`，签名 `(this, float alpha)`）`[代码]`

```c
game   = client->getInput();                        // IClientInstance 槽 +0x978
player = client->getLocalPlayer();                  // 槽 +0xF8
key    = game->getInputMode();                      // 0x144353910
if (key == InputMode::Touch)                        // 0x1404B9598..：仅 Touch 进入
    key = getOptions().getOption(0x1DB) | 2;        // 0x1404B95C1..D7：hotbar_only_touch ? 3 : 2
v   = getOptions().vtable[+0x1B8](key);             // 按模式灵敏度
k   = 9600f * (0.15f + 0.6f*v)^3;
p   = [this+0x34] * k;  y = [this+0x38] * k;        // 俯仰轴 / 偏航轴事件
[this+0x34..0x3B] = 0;
if (!client->isShowingMenu() || client->isTouchGameplayAllowed()) {   // 槽 +0x4B0 / +0x850
    [this+0x84] += p;  [this+0x88] += y;
    if (key == GamePad) f = 0.7f;
    else if (key == Touch) {
        f = getOptions().getOption(0x1DB) ? 0.7f : 1.0f;
        vec = {p*0.3f*f, y*0.3f};
        if (client->isTouchGameplayAllowed()) {                        // 0x1404B9EF1..F0A
            g = min(3.0f, (alpha + (float)[this+0x98]) - [this+0x94]);  // 0x1404B9F10..F32
            [this+0x94] = alpha + [this+0x98];
            turn = {g*[this+0x8C]*0.15f, g*[this+0x90]*0.15f};         // 0x1404B9F3E..F5E
            if (!isinf(turn)) { player->turn(turn); goto end; }         // 0x1404BA011..02C
        }
        f = 1.0f;
    } else f = 1.0f;
    player->turn({p*0.3f*f, y*0.3f});
}
```

与上一轮的三处出入（均以原始指令为准）：

| 上一轮写法 | 原始指令 |
| --- | --- |
| 「`edi = hotbar_only_touch \| 2 ∈ {2,3}`」（键鼠路径也如此） | 该三行在 `InputMode == 2` 分支内；键鼠路径 `key = 1`，`0.7f` 分支不可达 |
| 「`screen = getScreen(); screen->vtable[+0x850]()`」 | `0x1404B9EF1: mov rcx,[rsi+0x1B8]` → `IClientInstance` 上的 `isTouchGameplayAllowed` |
| 「`c = [screen->vtable[+0xE0]()+0x3C]`」 | `0x1404B921C: movaps xmm6, xmm1` ⇒ `c` = 浮点参数；适配器 `0x140889540` 用 `movss xmm1,[rax+8]`（`rax = *(*(ctx)+0x1268)`）填入 |

`[实测]` 该浮点见 §7.1：两轮共 16 次采样都落在 `0.046669..0.886675`，相邻采样（间隔约 2 tick 加一次管道往返）相差 `0.12..0.18` 或其回绕值；它所在对象的 `+0x00` 是 `20.0`（每秒 tick 数）、`+0x0C` 是 `1.0`、`+0x10` 是另一个落在 `(0,1)` 的浮点、`+0x14` 是 `0.0666667`。与「逐拍变化、不足一个单位的时间量（tick 小数或帧间隔）」相符 `[推断]`；该结构体的类型名静态上未定 `unresolved`，所以它不能作为「渲染插值 alpha」的定案。

### 4.3 slot 14（`0x1404BF490`，签名 `(this, int a2, int a3)`）`[代码]`

```c
if (a2 == a3) {                                     // 唯一的门；与任何选项无关
    n = 1.0f/(float)(a2 + 1);
    [this+0x8C] = copysign(powf(fabs([this+0x84]), 1.2f)*0.4f*n, [this+0x84]);
    [this+0x90] = copysign(powf(fabs([this+0x88]), 1.2f)*0.4f*n, [this+0x88]);
    [this+0x84..0x8B] = 0;
}
++[this+0x98];
// 以下与 a2 == a3 无关，每次调用都执行：
if (client->isInWorldAndNotShowingAnyMenuScreens()      // 槽 +0x4F0
 && client->allowPicking()                              // 槽 +0x4A8
 && (player = client->getLocalPlayer()) != nullptr
 && player->vtable[243]() 的对象有效 /* 略去 shared_ptr 记账 */
 && ... /* 见下 */) {
    v23 = *ECS(player, 0x02C628C0);                    // 玩家实体某组件的首字节
    if (v23 == 4 || v23 == 2) {
        if (game->getInputMode() == InputMode::Mouse) {
            opt = getOptions().vtable[27](&out, 0x187);  // 0x1404BFB13..1F
            if (*(bool*)(opt + 0x10) == 0) {             // 0x1404BFB43 / 0x1404BFB47 jne → 尾声
                … 取屏上一点（宽 int、高 float，两个 short）→ 屏幕点 {x, 高 − y}
                … 0x1404C2CF0 经相机反投影、0x14026E620 射线检测
                … 命中：**(int**)(player+0x228) = 0；*(float*)(*(player+0x228)+4) = (6.2831855f − atan2f(…))·57.295776f；
                   0x141A29FA0(player)
            }
        }
    }
}
```

要点与勘误：

- **`^1.2` 段与 `0x187` 无关**。上一轮「`0x187` 为假时才走 `^1.2` 这一支」「开关是 `keyboard_show_full_keyboard_options`」两句都不成立：存档键是 `ctrl_fullkeyboardgameplay`（`0x1420BB150` 注册表：`ctrl_fullkeyboardgameplay` / `options.fullKeyboardGameplay` → `mov edx, 0x187`）`[常量]`，且 `0x187 == true` 时跳过整段尾部逻辑。
- 尾部那段是「按屏上一点把玩家转头对准射线命中点」的代码 `[推断]`（写入点 `{int 0, float 角度}` 在 `player+0x228` 所指对象 `+0/+4`，随后调 `0x141A29FA0`）。它的触发前提之一是 `InputMode == Mouse`，所以**不是触屏路径**；组件 `0x02C628C0` 的名字与该段的完整语义 `unresolved`。
- `a2/a3` 来自适配器（未定位）；鼠标路径上实测恒 `0/0`（上一轮 `r01-017`）。

---

## 5. 触屏虚拟摇杆前端 `TouchMoveAndTurnInteractControl::$tick`

### 5.1 定位 `[常量]`

`TouchMapper::$setMapping`（`0x1406FDF90`）里写入的控件 vtable：

| 控件类（SDK） | vtable | `render`（槽 4） | `tick`（槽 5） |
| --- | --- | --- | --- |
| `TouchMoveAndTurnInteractControl` | `0x14E76D010` | `0x140708410`（画摇杆，调用 `0x140707DE0` 取 `joystick_frame/knob` 纹理） | **`0x140708A60`**（`0x3664` B） |
| `TouchTurnInteractControl` | `0x14E76CE80` | 基类空实现 | `0x1406EDA60` |
| `TouchGlyphButtonControl` | `0x14E76CDC0` | `0x1406EC830` | `0x1406ECCE0` |
| `TouchTextButtonControl` | `0x14E76CE40` | `0x1406ED6B0` | 同 `TouchGlyphButtonControl` |
| `TouchTapOrHoldGlyphButtonControl` | `0x14E76CE00` | `0x1406ED020` | `0x1406ED3B0` |

`TouchMoveAndTurnInteractControl` 的成员（基类 `TouchControl` 占 `0x70`）：`mStaticJoystick +0x1F0`、`mDefaultMoveStickArea +0x270`、`mSprintOnMovement +0x3F0`（`std::function` 的可调用对象指针在 `+0x428`）、`mSprintButtonId +0x53C`、`mState +0x540`；`mState` 内 `mMoveState +0x54C`、`mActiveMovePointerId +0x558`、`mStartMoveTime +0x578`、`mStartTimeMovingOutsideMaxZone +0x580`、`mMoveTouchX/Y +0x594/+0x598`、`mMoveTouchX0/Y0 +0x5AC/+0x5B0`。

### 5.2 传输函数 `[代码]`

`tick` 太大（13.9 KB），IDA 反编译被截断；以下来自 `mMoveState == JoystickState::Move` 分支的反汇编：

```asm
0x140708E7D  movss xmm7, [rbp-0x10]
0x140708E82  subss xmm7, [rbp-0x14]                 ; 区域宽 = moveArea.x1 − x0
0x140708E87  divss xmm7, [3.0f]                     ; R = 区域宽 / 3
0x140708FCF  movaps xmm2, xmm13 / subss xmm2, [rsi+0x5AC]   ; dx = 触点x − mMoveTouchX0
0x140708FDB  movaps xmm3, xmm14 / subss xmm3, [rsi+0x5B0]   ; dy = 触点y − mMoveTouchY0
0x140709000  movss xmm1, [25.0f]
0x140709008  ucomiss xmm1, xmm0 / jae 0x14070B6AB             ; dist ≤ 25 → 死区分支
0x140709013  divss xmm2, xmm0 / divss xmm3, xmm0             ; 单位方向 (dx, dy)/dist
0x14070901B  movss xmm4, [-25.0f] / addss xmm4, xmm7           ; 可用行程 = R − 25
0x140709061  addss xmm5, [-25.0f]                             ; min(dist, R) − 25
0x140709069  mulss xmm2, xmm5 / divss xmm2, xmm4               ; x = dx/dist · m
0x140709071  mulss xmm3, xmm5 / divss xmm3, xmm4               ; y = dy/dist · m
0x140709079  movss xmm4, [1.05f] / mulss xmm4, xmm7            ; 1.05·R
0x140709085  ucomiss xmm0, xmm4 / seta al                       ; outsideMax = dist > 1.05·R
0x14070908B  and bl, al                                        ; 与 mSprintOnMovement() 的结果相与
```

即：

```
R      = (moveArea.x1 − moveArea.x0) / 3
dist   = hypot(触点 − 原点)                   原点 = mMoveTouchX0/Y0
dist ≤ 25        → 死区（不输出移动）
m                = (min(dist, R) − 25) / (R − 25)      线性，R 处封顶为 1
输出             = (dx, dy) / dist · m
疾跑候选         = mSprintOnMovement() && (dist > 1.05·R)
```

`[代码]` 没有平方/立方响应曲线，也没有对角归一化以外的整形；`calibrateMoveDelta(float&, float&)`（SDK 静态成员）在 `0x14070BDD9..0x14070BE99` 一带被内联，发射前还有一步 `−0.3·x/√(x²+1)` 形状的修正，**其完整形状与适用分支 `unresolved`**。发射走 `0x14034DF90(queue, DirectionId=Move(0), …, x, y)`。

### 5.3 未闭合

- 摇杆原点 `mMoveTouchX0/Y0` 的写入点（`mStaticJoystick` 为真/假时分别怎么取）未展开。
- 疾跑：只读到「候选 = `mSprintOnMovement()` 且出界 5%」与离开 `Move` 状态时松开 `mSprintButtonId`（`0x1407095BF`）；`mStartTimeMovingOutsideMaxZone` 的计时阈值未读到。
- 触点坐标是否已按 GUI 缩放换算（死区 `25` 的物理单位）未核对。

---

## 6. 经典 D-pad

### 6.1 绑定与几何 `[常量]` `[代码]`

8 个方向各是一个 `TouchGlyphButtonBinding`（构造函数 `0x146931A00`），调用者给出按钮名、区域/条件/透明度哈希：

| 按钮 | 绑定构造 | 区域哈希 | 区域 lambda `_Do_call` |
| --- | --- | --- | --- |
| `button.upleft` | `0x1469415F0` | `0xB2342D20` | `0x14436E490` |
| `button.upright` | `0x146941840` | `0xD9BD30D5` | `0x14436E570` |
| `button.downleft` | `0x146941A90` | `0xCD6D2D03` | `0x14436E650` |
| `button.downright` | `0x146941CE0` | `0x7355D00C` | `0x14436E730` |
| `button.left` | `0x146941FC0` | `0xA1199A88` | `0x14436D110` |
| `button.right` | `0x146942200` | `0x0C42EF1D` | `0x14436D230` |
| `button.up` | `0x146937850` | `0x7CA2A528` | `0x14436D360` |
| `button.down` | `0x1469427A0` | `0x0E5292E5D` | `0x14436D580` |

区域哈希 → lambda 的登记在 `ClientBindingFactory` 构造函数（`0x144343EB0`）的 `mAreaBindings` 表里；公共参数：UV `22×22`、`glyphScale = 1.0`、`promiscuous = 1`、`extendButtonPressOutsideAreaUntilReleased = 0`、透明度哈希 `0x20AE315B`、左右上下与上/下的条件哈希 `0xD7DC7161`。

**外框**（8 个 lambda 共同调用 `0x14434E660(client, &rect)`）`[代码]`：

```
W, H  = GuiData 的宽、高（+72/+76）
a = opt[0x0FE]  b = opt[0x101]  c = opt[0x2A4]  d = opt[0x2A5]  e = opt[0x2A6]     （选项名未定位）
cx    = W · d
top   = (1 − a)·H/2        bottom = b·H/2
span  = ((H − top) + bottom) − (bottom + top) = a·H
cy    = c·span + (bottom + top)
half  = (e · 0.414f) · span / 2
rect  = {cx − half, cx + half, cy − half, cy + half}      → 再经 0x14434EAF0 修正
```

**网格**（`S = (x1 − x0)/3`，`i = S/22 · 1.01`，`cy = y0 + 1.5·S`，`cx = x0 + 1.5·S`）：

| 区域 | x 范围 | y 范围 |
| --- | --- | --- |
| `up_left` / `up_right` | `[x0, x0+S]` / `[x1−S, x1]` | `[y0, y0+S]` |
| `down_left` / `down_right` | `[x0, x0+S]` / `[x1−S, x1]` | `[y1−S, y1]` |
| `left` / `right` | `[x0+i, x0+S+i]` / `[x1−S−i, x1−i]` | `[cy−S/2, cy+S/2]` |
| `up` / `down` | `[cx−S/2, cx+S/2]` | `[y0+i, y0+S+i]` / `[y1−S−i, y1−i]` |

### 6.2 命中与按压规则（`TouchGlyphButtonControl::tick`，`0x1406ECCE0`）`[代码]`

```c
if (!mCondition()) return;
wasPressed = mButtonState; mButtonState = 0;
area = mArea();                                  // 即上面的 RectangleArea
newPointer = -1;
for (TouchPoint& p : results) {
    if (p.captured /*+16*/) continue;
    if (mActivePointerId != -1 && mActivePointerId != p.id) continue;
    inside = area.x0 <= p.x && p.x <= area.x1 && area.y0 <= p.y && p.y <= area.y1;   // 闭矩形
    if (mTouchStateRequirement == 4 /*DoublePressed*/) { …; goto next; }
    if (!inside) continue;
    newPointer = p.id;
    if ((mTouchStateRequirement & (p.state == Triggered ? 2 : 1))
        && (p.fresh /*+20*/ || (mPromiscuous && wasPressed)))
        mButtonState = 1;
next:
    if (!mPassThrough) p.fresh = 0;               // 本控件消费掉这个触点
}
if (mExtendButtonPressOutsideAreaUntilReleased && mActivePointerId != -1 && 该指针仍在)
    mButtonState = 1;
if (mButtonState != wasPressed) {
    mActivePointerId = mButtonState ? newPointer : -1;
    enqueue(Button{mButtonId, mButtonState, exclusive=0, repeatInterval=-1.0f, source=2});   // 0x14033F020
}
```

`p.fresh`（`TouchPoint+0x20`）在 `TouchMapper::$tick` 重建触点时对每个触点置 `1`。据此：

1. **单指**：格与格内部不相交，一根手指在某一格内只会让该格的按钮按下。相邻格只在共享边上相交（闭矩形，边上两点同属两格）；此时控件表中靠前的那个先消费 `fresh`，靠后的只有「上一拍已是按下且 `promiscuous`」才保持——所以从一格滑到另一格、恰好停在共享边的那一拍可能短暂两键同按，进入新格内部后旧键即松开（`extend = 0`）。
2. **边格之间的小重叠**：四个边格都朝中心内缩 `i`，于是相邻两个边格在「十字交角」处重叠出一个 `i×i` 的小方块（四个交角各一个）。单指停在这个小方块里时同样适用上一条规则。
3. **多点**：每个按钮各记各的 `mActivePointerId`，两根手指分别在角格与边格 ⇒ 两个按钮同时为按下，各自发出 `Down`。合成层的算术见 [`input-joystick-strafe-12651.md`](input-joystick-strafe-12651.md) §2.1（`Up + UpLeft = (1,2) → 26.565°`）。

**未闭合**：`button.upleft` 等按钮名到 `MoveInputState::UpLeft(0x200)` 等位的注册点（`ClientMoveInputHandler::_registerButtonStateTracking` 的调用处）没有定位到——`.rdata` 里不存在 `upleft` 字符串，这些名字只在上述构造函数与触屏映射模板里以栈上字符串出现；所以「角键置对角位」目前仍是 [`input-joystick-strafe-12651.md`](input-joystick-strafe-12651.md) 的 `[推断]`，本文没有把它升级。

---

## 7. 实机对质汇总

### 7.1 两轮会话（`r01-003`）`[实测]`

条件：`W = 2560×1494`，`ctrl_sensitivity2_mouse = 0.5`，`dx = 200, dy = 100`，键鼠路径。

| 量 | `invert_off`（`ctrl_invertmouse_mouse:0`） | `invert_on`（`ctrl_invertmouse_mouse:1`） |
| --- | --- | --- |
| `MouseMapper::$tick` 命中（窗口内） | `70` | `71` |
| `mScreenWidth` / `mScreenHeight` | `2560` / `1494` | `2560` / `1494` |
| `mYAxisInversionFactor` | `−1` | `+1` |
| slot 15 消费的事件 `[+0x34, +0x38]` | `(−0.0390625, 0.078125)` | `(0.0390625, 0.078125)` |
| 其后累加拍 `[+0x84, +0x88]` | `(−43.308548, 86.617096)` | `(43.308548, 86.617096)` |
| `LocalPlayer::turn` 入参 | `(−12.992565, 25.98513)` | `(12.992565, 25.98513)` |
| 状态 `Δyaw` / `Δpitch` | `25.985107421875` / `12.9925537109375` | `25.985107421875` / `−12.9925537109375` |
| `deg/px` 横向 / 纵向（比值） | `0.129925537109375` / `0.12992561340332032`（`0.9999994127874918`） | 同左 |
| `dy=+100` 单独一拍的 `Δpitch` | `12.992568969726562` | `−12.992568969726562` |
| 适配器浮点 `c` 的 8 次采样 | `0.706669, 0.526669, 0.366669, 0.526669, 0.526669, 0.206669, 0.046669, 0.526669` | `0.886675, 0.046675, 0.766675, 0.886675, 0.726675, 0.386675, 0.226675, 0.386675` |
| `lost` | `0` | `0` |

命中顺序每拍相同：`mapper_tick → adapter → slot15 → apply`（`hit_order_head`）。`static_model`（纯 Python，对本文公式的自检，不涉及游戏）输出：`R(306 宽区域) = 102`、`m(25.5) = 0.00649`、`m(63.5) = 0.5`、`m(102) = m(140) = 1`、疾跑阈值 `107.1`、D-pad 边格内缩 `4.6827`、四个交角重叠面积各 `21.9279`、反转因子 `[-1, 1]`。

### 7.2 没有做的

没有触屏/手柄运行期数据；没有改窗口尺寸复测 `/W`（分母守恒见上一轮）；没有验证 `ctrl_invertmouse_touch`。

---

## 8. 未定位（`unresolved`）

| 项 | 已确认 | 缺什么 |
| --- | --- | --- |
| 选项 `0x185` → `InputMapping::invertYAxis` 的写入者 | 注册、getter、`setMapping` 的 `(b−1)\|1`、运行期翻转运态 | 把 `Options::vtable[+0x1D0](InputMode)` 写进 `MouseInputMapping`/`TouchInputMapping` 的函数；`TouchMapper::$setMapping` 对应段未展开 |
| D-pad 按钮名 → `MoveInputState` 位 | 按钮名、绑定、区域、按压规则 | `_registerButtonStateTracking("button.upleft", UpLeft)` 一类调用点；角键置对角位仍为 `[推断]` |
| 摇杆疾跑计时 | 候选条件 `mSprintOnMovement() && dist > 1.05R`、离开 `Move` 时松键 | `mStartTimeMovingOutsideMaxZone` 的阈值与按下 `mSprintButtonId` 的路径 |
| `calibrateMoveDelta` 完整形状 | 发射前存在 `−0.3·x/√(x²+1)` 形状的修正 | 适用分支与入参含义 |
| 摇杆原点（固定/动态） | `mMoveTouchX0/Y0` 是原点 | `mStaticJoystick` 决定原点的写入点 |
| slot 14 尾部段 | 门控链、`0x187` 为假才进入、写 `player+0x228` 所指对象 | 组件 `0x02C628C0` 的名字、`player->vtable[243]` 的语义、整段意图 |
| `c` 的结构体身份 | 是 slot 15 的浮点参数，实测 `0.046669..0.886675` 逐拍变化 | `*(*(ctx)+0x1268)` 的类型名 |
| D-pad 外框的 5 个选项 | `0x0FE/0x101/0x2A4/0x2A5/0x2A6` 的用法 | 选项名（不在 `0x1420BB150` 注册表里） |
| 望远镜阻尼分子 `0.05f` | `LocalPlayer::turn`：`(*(player->vtable[77]() + 8))->vtable[+0x228]()` 返回值的低 32 位 | 该虚函数的实现；全库唯一一对 `{0.05f,0.05f}`（`0x142045A97` → `0x151D5E1BC`）所属对象的 vtable `0x14E76DDB0` 只有 11 槽，不是它 |
| 派发单锚点更正 | `0x145018E70` 是资源包界面（`resourcePack.suggestedContent.title` 等）；`button.look_up_left` 所在的 `0x1469265A0`/`0x146917EF0` 是按键→动作意图表；`0x1443765D0` 是手柄映射布局；`0x144D89B40` 是控件图标/名称表 | —（它们都不是触控移动或摇杆的数学本体） |

---

## 9. 勘误（对前两轮）

| 被更正的说法 | 出处 | 现结论 | 依据 |
| --- | --- | --- | --- |
| `0x145DC7B50`「只把 16 字节 event 原样转发」，`/W` 在其调用者里 | `touch-input-turn-12651` §1.1/§3 | 它是 `ClientInstance::forEachVisibleScreen`；那 16 字节是 `std::function`。视角事件由 `InputHandler` 的 `DirectionId` lambda 发出，经 `forEachScreen`（`+0x7F8`）派发；`/W` 在 `MouseMapper::$tick` | §1、§2、§4.1 |
| slot 27 的「action 1 / action 8」 | 同上 §2 | `DirectionId::Turn` / `TurnAlt`；「screen」实为 `getLocalPlayer()` | §4.1 |
| slot 15「`screen->vtable[+0x850]()`」「`c = [screen->vtable[+0xE0]()+0x3C]`」「键鼠路径 `edi ∈ {2,3}`」 | 同上 §4.2/§4.3 | `IClientInstance::isTouchGameplayAllowed`；`c` = 浮点参数；键鼠路径键为 `1` | §4.2 |
| slot 14「`0x187 = keyboard_show_full_keyboard_options`，为假才走 `^1.2`」 | `input-joystick-strafe-12651` §4.6、`touch-input-turn-12651` §5 | `0x187 = ctrl_fullkeyboardgameplay`；`^1.2` 只看 `edx == r8d`；`0x187` 为假进入的是尾部另一段逻辑 | §4.3 |
| 「`ctrl_hotbarOnlyTouch` 是否映射到 `0x1DB` 未定」 | `touch-input-turn-12651` §8 | 映射成立：`0x1420C9E64 mov edx, 0x1DB` | §3 表后文、§0 #9 |
| 「`touch_invert_y_axis` 消费点未定位」「`dy` 在进入 handler 链前已取负（来源未定）」 | 同上 §8 | 取负 = `MouseMapper::mYAxisInversionFactor`（默认 `−1`）；按模式 getter = `Options::vtable[+0x1D0]` | §2.2、§3 |

---

## 10. 边界

- 静态结论来自 `pe_map.py` 离线反汇编与 IDA 反编译；13.9 KB 的 `TouchMoveAndTurnInteractControl::$tick` 只读了 `Move` 状态分支，其余状态（`Inactive/Deciding/Tapping/Holding`）与副摇杆分支未读。
- 运行期只 hook 了参数全为整型的三个函数（`MouseMapper::$tick`、slot 15、适配器 `0x140889540`）与 `LocalPlayer::turn`；slot 27 仍因 XMM 传参不可 hook。
- 探针改写过存档里的 `ctrl_invertmouse_mouse`（`0 → 1 → 恢复`），结束时已按字节恢复原文件；`r01-003` 的 `options.restored` 记录了恢复后的值（`0`，与原值一致）。
- 本轮未改 `mod/`、`driver/`、`tests/`。
