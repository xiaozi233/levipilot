# 触屏视角转角、输入处理器家族与 `/W` 归一化位置（1.26.51.01）

> 版本：`Minecraft.Windows.exe` `1.26.51.01`（LeviLamina `26.51.7`，`image_base = 0x140000000`）。
> 证据约定：`[代码]` 反汇编/控制流；`[常量]` `.rdata` 浮点、vtable、注册点；`[实测]` LeviPilot 运行期 VA hook（`allow_hook_va=1`）；`[推断]` 由直接证据补出的环节；`unresolved` 为未定位。
> 复现脚本：[`verify_touch_input_turn.py`](../../skills/bedrock-re/findings/1.26.51.01/cases/touch-input-turn-12651/probes/verify_touch_input_turn.py)（`r01-017` 链路与默认系数、`r01-009` `hotbarOnlyTouch=1`）。原始数据：gitignored `out/touch-input-turn-1.26.51.01.json`，本文所有 `[实测]` 数字逐位取自该文件。
> 前置：移动输入合成与键鼠公式见 [`input-joystick-strafe-12651.md`](input-joystick-strafe-12651.md)。
> **硬件边界**：本机无触摸屏/手柄，触屏分支全部为静态闭合；`mouse_event` 注入只覆盖键鼠路径。

---

## 0. 结论摘要

| # | 结论 | 证据 |
| --- | --- | --- |
| 1 | 输入处理器是**单一类**：vtable `0x14E760FA0` 共 **107 槽**，只有约 25 个有实体代码，没有按设备分的子类；Mouse/Touch/GamePad 的区别在**参数与注册的回调**，不在 vtable 分发 | `[常量]` `[代码]` |
| 2 | 视角事件走 **`std::function` 回调链**：`0x144B22290`（每个事件两遍派发）→ `0x144B22580`（遍历已注册回调，调用点 `0x144B225CE`）→ adapter → `slot 27 / 15 / 14` | `[代码]` `[实测]` |
| 3 | **slot 27（`0x1404C02B0`）是视角动作分发器**：`action 1` 把 `(dx, −dy)` 累加进 `[this+0x34/+0x38]`；`action 8` 用 `(dx, −dy)` **直接**调 `0x144761610`，绕过整条灵敏度曲线 | `[代码]` |
| 4 | **`/W` 发生在 handler 链上游**：进入 slot 15 时 `[this+0x38] = dx/W = 0.078125`、`[this+0x34] = −dy/W = −0.0390625`（`dx=200, dy=100, W=2560` 精确）。链上 `0x145DC7B50`、`0x144B2AC10`、`0x144B22290`、`0x144B22230` 都只做 16 字节转发，除法指令本身 `unresolved` | `[实测]` `[代码]` |
| 5 | slot 15 把事件乘 `k = 9600·(0.15+0.6v)³` 累进 `[this+0x84/0x88]`，再 `×0.3` 交给相机：实测 `[0x84,0x88] = (−43.308548, 86.617096)` → `(−12.992565, 25.985130)` 度 | `[代码]` `[实测]` |
| 6 | **slot 14（`0x1404BF490`）是触屏视角的非线性级**：输入 slot 15 的线性累加器，输出 `[this+0x8C/0x90] = sign·0.4·|A|^1.2/(n+1)`，`n = [this+0x98]` 每拍 `+1`；其输出**只被** slot 15 的 `+0x850` 触屏分支消费。它在鼠标路径上每 tick 也跑（`edx == r8d == 0`），结果无人读取 | `[代码]` `[实测]` |
| 7 | **触屏每 tick 转角**：`Δ = 0.15·g·(T.x, T.y)`，`T = 0.4·|A|^1.2/(n+1)`，`A = Σ(d/W)·k`，`g = min(3, (n+c) − last)`。合并后 `(0.15+0.6v)^(3×1.2) = (0.15+0.6v)^3.6` —— Wiki 的 `(1.6+6.4v)^3.6` 与 `(0.15+0.6v)^3.6` 对 `v` 的依赖完全等价（`1.6+6.4v ≡ (32/3)(0.15+0.6v)`，只是把常数因子提进括号，指数 `3.6 = 3×1.2` 来自本链）；真正差异是像素增量非线性 `|d/W|^1.2`、逐拍增幅 `g` 与物理宽度分母 `W` | `[代码]` `[常量]` |
| 8 | 竖直（pitch）分量额外因子 `0.7f` 的门控是 `hotbar_only_touch`（OptionID `0x1DB`）；默认 `deg/px(dx) / deg/px(dy) = 0.999999`。把 `ctrl_hotbarOnlyTouch` 改 `1` 重启后仍为 `1.0`（本机无触摸设备，`0x1DB` 未生效），`0.7` 分支**只有静态证据**。**更正前置案例**：该因子乘的是 `xmm8 = [this+0x34]·k`，按本轮实测的轴序这是 **dy/俯仰** 分量，不是「横向」 | `[代码]` `[实测]` |
| 9 | **通用 VA detour 不能 hook slot 27**：它只保留 `rcx/rdx/r8/r9`，slot 27 的两个浮点在 `xmm2/xmm3`，hook 后累加值变 0、相机不转（`d_yaw = 0.0`） | `[实测]` |
| 10 | 触屏虚拟摇杆前端（像素拖动 → `mAnalogMoveVector`）、D-pad 角键命中区、`touch_invert_y_axis`、`/W` 指令本身：**未定位** | `unresolved` |

---

## 1. 视角事件的派发架构

### 1.1 派发链 `[代码]`

```
事件源（鼠标/触屏事件生产者，未定位）
  → 0x145DC7B50  vtable 0x14E9731B0 slot +0x7F0：只把 16 字节 event 转发给 ↓（0x145DC7DA3..DB5）
  → 0x144B2AC10  同样 16 字节转发（0x144B2AD7B..D93）
  → 0x144B22290  fire：对注册的 handler 先跑 +0x178 / +0x108 两遍前置，再把 event 拷到
                   [rbp+0x40] / [rbp+0x50]，**以 flag=0 与 flag=1 各调一次** 0x144B22580
  → 0x144B22580  遍历 [registry+0x38] 的回调列表：对每个 entry 查 +0x158 / +0x290 / +0x258 /
                   +0x280 / +0x278 / +0x168 / +0x138 / +0x170 等门控布尔，通过则
                   0x144B225CE: call entry->fn(owner->m8, entry)
```

`0x144B225CE` 是 slot 14 与 slot 15 **共同**的调用点（返回地址 `0x144B225D4`），区别只在 entry 里装的 `fn`：

| entry 的 `fn` | 作用 | 运行期证据 |
| --- | --- | --- |
| `0x140889540` | 先取 `[ctx->vtable[0x1268]()+8]` 一个 float 放进 `xmm1`，再调 **slot 15**，尾调 slot 16（默认空实现） | slot 15 的栈帧 `[..., 0x14088956B, 0x144B225D4]` |
| 尾跳 adapter（未定位） | 直接 `jmp` 到 **slot 14**，`edx = r8d = 0` | slot 14 的栈帧 `[..., 0x144B225D4, 0x144B223CF]` |
| `0x145DDCA90` | 把 event 的 `(action, f@+8, f@+0x10, byte@+0x18)` 转给 **slot 27** | slot 27 的栈帧 `[..., 0x145DDCAEB, 0x144B2285F]` |

### 1.2 adapter `0x145DDCA90` `[代码]`

```asm
0x145DDCA90  rsi = rdx (handler) ; rdi = rcx (event: {float* @0, float* @8, float* @0x10, ptr @0x18})
0x145DDCA9F  if (handler->vtable[+0x290]()) return true          ; 捕获输入的 UI 短路
0x145DDCAB7  cl = *(byte*)event[0x18]
0x145DDCABE  xmm3 = *event[0x10]                                  ; 第二个浮点
0x145DDCAC6  rdx = event[8] ; xmm2 = *rdx                         ; 第一个浮点
0x145DDCAC6  edx = *(byte*)event[0]                               ; action id
0x145DDCAD7  handler->vtable[+0xD8](rcx=handler, xmm2, xmm3, dl, [rsp+0x20]=cl)   ; slot 27
```

它只转发，**不做任何算术**；进入 handler 链时的两个浮点已经是 `dx/W`、`−dy/W`（§3）。

---

## 2. slot 27：视角动作分发器 `0x1404C02B0..0x1404C0804` `[代码]`

```
f(this /*rsi*/, xmm2 /*dy*/, xmm3 /*dx*/, dl /*action*/, cl /*[rsp+0x20]*/)
  if (dl == 8) goto DIRECT ; if (dl != 1) return
  ; ---- action 1：累加 ----
  screen = client->getScreen()
  if (screen && screen[+0x798] 存在 && 正在捕获输入) → 0x1404C0756：跳过累加（原值不动）
  else 0x1404C0662: [this+0x38] += dy ; [this+0x34] += dx        ; 注意轴序
  ; ---- action 8：直转 ----
  DIRECT: screen = client->getScreen(); if (!screen) return
          [rbp-0x40] = dx ; [rbp-0x3C] = -dy                      ; dy 取负
          0x144761610(screen, &[rbp-0x40])                       ; 直接进相机，绕过 slot 15/14
```

要点：

- **action 1 是鼠标移动事件的落点**：`mouse_event(dx=200,dy=100)` 恰好产生 **1 次** slot 27 命中、`rdx` 低字节 = `1`（`r01-003` 前身的探索运行）。
- **action 8 是一条完全绕过灵敏度曲线的直转**（过场、脚本或某些触屏手势的候选）；它对 `dy` 取负后再交给 `0x144761610`。
- 轴序与符号：`[this+0x38]` 是 **yaw 轴**（`dx/W`），`[this+0x34]` 是 **pitch 轴**，且**上游已取负**（`dy=+100` → `−0.0390625`，低头为正 pitch 由这个负号产生）。

**不能 hook slot 27** `[实测]`：装上通用 VA detour 后 `d_yaw = 0.0`、`[this+0x34]` 在 slot 15 入口恒为 0——detour 不保留 `xmm2/xmm3`，累加的是被清零的浮点。这是 `AGENTS.md`「通用 VA detour 只保留 rcx/rdx/r8/r9；XMM 必须 typed detour」的直接复现。

---

## 3. `/W`：进入 handler 链前已完成 `[实测]` `[代码]`

`r01-017`（`W = 2560×1494`，`dx=200, dy=100`，slot 15 入口快照 `[this+0x34..0x8B]`）：

| 量 | 实测 | 期望 |
| --- | --- | --- |
| `[this+0x38]`（消费拍） | `0.078125` | `200/2560 = 0.078125` |
| `[this+0x34]`（消费拍） | `−0.0390625` | `−100/2560 = −0.0390625` |
| `[this+0x84, 0x88]`（其后累加拍） | `(−43.308548, 86.617096)` | `k·(−dy/W, dx/W)`，`k = 1108.698853` |
| `0x144761610` 入参 | `(−12.992565, 25.985130)` | `0.3 ×` 上行 |
| 状态 `Δyaw / Δpitch` | `25.985107 / 12.992554` | 与入参一致 |

**`/W` 的位置**：事件在 `0x145DDCA90` 处已是 `dx/W`，而它上游的 `0x145DC7B50`（`0x145DC7DA3..DB5`）、`0x144B2AC10`（`0x144B2AD7B..D93`）、`0x144B22290`、`0x144B22230` 都只是 `movups/movaps` 16 字节转发，**没有除法**。所以 `divss`（或乘以 `1/W`）在**鼠标事件生产者**里，即 `0x145DC7B50` 的调用者（vtable `0x14E9731B0` slot `+0x7F0`，全库 63 处调用）中。该生产者 `unresolved`。

已证实的边界：分母是**物理客户区宽度**（`W=2560` 精确命中；上一轮 `slope×W` 在三种窗口尺寸下守恒），`dy` 在同一处被取负。

---

## 4. slot 15：线性级、公共缩放与触屏分支

### 4.1 线性累加（键鼠与触屏共用）`[代码]` `[常量]`

```asm
0x1404B96B8  movss xmm8, [rsi+0x34]      ; pitch 轴事件
0x1404B96BE  mulss xmm8, xmm7            ; × k,  k = 9600f·(0.15f+0.6f·v)³
0x1404B96C3  mulss xmm7, [rsi+0x38]      ; yaw 轴事件 × k
0x1404B984D  mov   qword [rsi+0x34], 0   ; 清事件
0x1404B9A57  movss xmm0,[rsi+0x84] ; addss xmm0,xmm8 ; movss [rsi+0x84],xmm0
0x1404B9A6C  movss xmm0,[rsi+0x88] ; addss xmm0,xmm7 ; movss [rsi+0x88],xmm0
```

`v` 来自 `Options::vtable[+0x1B8](edi)`（`0x140FB5BA0`，读 OptionID `0x18D`），`edi = hotbar_only_touch | 2 ∈ {2,3}`。

### 4.2 公共缩放（`0x1404B9E3C..0x1404B9E60`）`[代码]`

```
xmm7 = [0x88]·k ; xmm8 = [0x84]·k
xmm7 *= 0.3f ; xmm8 *= 0.3f          ; 0x14E758C38
xmm8 *= factor                      ; factor = 0.7f（0x14E760F14）当 hotbar_only_touch，否则 1.0f
```

**轴序更正**：`xmm8` 来自 `[this+0x34]`，本轮实测该字段是 **`−dy/W`（俯仰轴）**；`xmm7` 来自 `[this+0x38] = dx/W`（偏航轴）。所以 `0.7f` 乘的是**竖直/俯仰**分量，`0x1404B9B83` 的 `mulss xmm8, xmm0` 不是上一轮文档所写的「横向因子」。若 `0x1DB` 生效，应观察到 `deg/px(dx) / deg/px(dy) = 1/0.7 ≈ 1.4286`。实测默认比值为 `0.999999`。

### 4.3 触屏分支（`0x1404B9EF1..0x1404B9F78`）`[代码]` `[常量]`

```asm
0x1404B9EF1  screen = getScreen(); if (!screen->vtable[+0x850]()) → 直接用 4.2 的结果
0x1404B9F10  n = (float)[rsi+0x98]            ; slot 14 的计数器
0x1404B9F1B  xmm6 = c + n                     ; c = [screen->vtable[+0xE0]()+0x3C]
0x1404B9F1F  g = min(3.0f, xmm6 − [rsi+0x94]) ; 0x14E6022AC = 3.0f
0x1404B9F36  [rsi+0x94] = xmm6
0x1404B9F3E  xmm0 = [rsi+0x90] · g · 0.15f    ; 0x14E6AE4DC = 0.15f
0x1404B9F56  xmm1 = g · [rsi+0x8C] · 0.15f
0x1404B9F62  → 0x144761610( xmm1, xmm0 )
```

`screen->vtable[+0x850]()` 为真即「触屏拖动视角」`[推断]`；此时**不用** 4.2 的线性结果，改用 slot 14 的输出 `[this+0x8C/0x90]`。

---

## 5. slot 14：非线性级 `0x1404BF490..0x1404BFEF6` `[代码]` `[常量]`

```asm
0x1404BF4CD  rbx = rcx ; if (edx != r8d) skip         ; 门控
0x1404BF4D9  xmm6 = 1.0f / (float)(edx + 1)           ; 1/(n+1)
0x1404BF4EB  x = |[rbx+0x84]| ; powf(x, 1.2f) ; ×0.4f ; ×xmm6 ; 带符号 → [rbx+0x8C]   ; 1.2f@0x14E6AE4F4, 0.4f@0x14E602290
0x1404BF537  y = |[rbx+0x88]| ; powf(y, 1.2f) ; ×0.4f ; ×xmm6 ; 带符号 → [rbx+0x90]
0x1404BF580  [rbx+0x84] = 0 ; [rbx+0x88] = 0          ; 清输入
0x1404BF58B  ++[rbx+0x98]                             ; n
0x1404BFB16  getOption(0x187) ; 若为真 → 0x1404BFDED（另一条未展开路径）
```

- **`edx == r8d` 门控**：鼠标路径上 adapter 传 `edx = r8d = 0`，恒成立；`[实测]` 一次 `mouse_event` 窗口内 slot 14 命中 **11 次**、`edx == r8d` 全部为真，且装 detour 不影响转角（`d_yaw = 25.985123`）。`edx` 的语义（触屏时是否为拖动帧计数）`[推断]`，未定。
- **归属**：它的输入是 slot 15 写的线性累加器，输出只被 §4.3 消费 ⇒ 它是**触屏视角的加速/平滑级**，不是手柄摇杆、也不是全键盘视角；`0x187` 为真时走 `0x1404BFDED` 的另一支未展开。

### 触屏每 tick 公式（合并 §4.1、§5、§4.3）

```
A  = Σ (d/W) · k ,          k = 9600 · (0.15 + 0.6·v)³            ; slot 15
T  = sign(A) · 0.4 · |A|^1.2 / (n + 1)                             ; slot 14
g  = min(3.0, (n + c) − last)                                      ; slot 15
Δ  = (T.pitch, T.yaw) · 0.15 · g          (度)                      ; slot 15 → 0x144761610
```

单拍（`n=0`）展开：`Δ = 0.06·g·9600^1.2·|d/W|^1.2·(0.15+0.6v)^3.6 ≈ 3605·g·|d/W|^1.2·(0.15+0.6v)^3.6`。
`(0.15+0.6v)^3.6` 的指数 `3.6 = 3 × 1.2` 完全来自「线性级立方 × 非线性级 1.2 次幂」。

**与 Wiki 旧式 `dx × (32/93275) × (1.6+6.4v)^3.6` 对质**：

| Wiki 项 | 本版 | 判定 |
| --- | --- | --- |
| 指数 `3.6` | `(0.15+0.6v)^(3×1.2)` | **真实代码结构**（同一条链） |
| `1.6 + 6.4v` | `0.15 + 0.6v` | **代数等价**（`1.6+6.4v ≡ (32/3)(0.15+0.6v)`，对 `v` 曲线比例完全一致；`6.4f` 在二进制中未出现） |
| 线性 `dx` | `|d/W|^1.2` | 本版对像素增量取 `1.2` 次幂，非线性 |
| `32/93275`（含 `/W`） | `0.06·9600^1.2 / W^1.2` | 分母是**物理客户区宽度**；常量随版本变 |
| 无 `g` | `g = min(3, (n+c)−last)` | 本版多出的逐拍增幅上限 |

`6.4f` 全库 0 命中（`--find-f32 6.4`）。

---

## 6. 输入处理器家族：vtable `0x14E760FA0`（107 槽）`[常量]` `[代码]`

构造函数 `0x1405306D0..0x140530D95` 在 `0x140530B29` 装入该 vtable。有实体代码的槽：

| slot | VA | 大小 | 特征 |
| --- | --- | --- | --- |
| 0 | `0x140538830` | 0x2B | 析构/空 |
| 1 | `0x1404B7AA0` | 0xD98 | 注册/初始化（引用 `NonOwnerPointer<IClientInstance>` 断言串） |
| 14 | `0x1404BF490` | 0xA66 | **非线性级** `0.4·|x|^1.2/(n+1)`，2×`powf`，`0x187` 门 |
| 15 | `0x1404B91F0` | 0xE41 | **线性级** `9600·(0.15+0.6v)³`，`0.3/0.7`，触屏分支 |
| 18 | `0x1404BA090` | 0x5D5 | — |
| 19 | `0x1404BDFB0` | 0x313 | — |
| 26 | `0x1404C0B00` | 0x1CD | — |
| 27 | `0x1404C02B0` | 0x554 | **视角动作分发**（action 1 累加 / action 8 直转） |
| 40 | `0x1404C0140` | 0x16F | — |
| 55 / 57 | `0x1404C5780` / `0x1404C5840` | 0x75 / 0x52 | — |
| 58 / 60 | `0x144634620` | 0x21 | — |
| 59 | `0x144634650` | 0x18C | — |
| 77 | `0x144634840` | 0x49 | — |
| 91 | `0x1446340D0` | 0x5C | — |
| 92 | `0x1404BA6B0` | 0x188 | — |
| 93 | `0x1404BA840` | 0x2C33 | 准星/交互 tick：`ClientMoveInputHandler::getMoveInput`（`0x1404BC48F` 查 `0x018B1887`）、`−0.7f` 比较、raycast |
| 94 | `0x1404BE360` | 0xF6E | 读 `getOption(6)` 等 |
| 97 | `0x1404C1950` | 0x327 | — |
| 98 | `0x1404C2EB0` | 0x1109 | 屏幕→NDC（`short/[r15+0x48]`）、四元数、`6.7/12/44.89/144` |
| 99 | `0x1404C0DA0` | 0x75D | — |
| 100 | `0x1404C1C80` | 0xA8A | `0.2f`、调用 slot 101 |
| 101 | `0x1404C4400` | 0x1029 | `0.0174533`（deg→rad）、`−0.025/−42/−50/−180` |
| 102 | `0x1404C1500` | 0x3E8 | — |
| 104 | `0x1404C2810` | 0xB8 | — |

其余槽指向默认空实现 `0x140076BC0`。`ClientMoveInputHandler::getMoveInput` 的独立实现是 `0x14267A440`（唯一调用者 `0x1426795CB`，读 `+0x34` 的 `mInteractDir`，不是摇杆写入者）。

---

## 7. 实机对质汇总 `[实测]`

| 探针 | 条件 | 结果 |
| --- | --- | --- |
| `r01-017` | `hotbarOnlyTouch=0`，`W=2560×1494`，最终脚本 | `dx/W = 0.078125`、`−dy/W = −0.0390625`；`[0x84,0x88] = (−43.308548, 86.617096)`；apply `(−12.992565, 25.985130)`；`Δyaw/Δpitch = 25.985107/12.992554`；slot 14 命中 11 次且 `edx==r8d`；`deg/px` dx/dy 比 `0.999999`；`lost = 0` |
| `r01-009` | `ctrl_hotbarOnlyTouch=1` 重启 | `deg/px` dx/dy 比仍 `1.0`，`hotbar_factor_applied = false`（脚本的后续改动只放宽了该点的断言集合，数据未变） |

`r01-001..006`、`r01-008` 为探针自身迭代失败（hook 槽上限、轴序语义、断言拍序），已 `abandon`，数据未被引用。

---

## 8. 未定位（`unresolved`）

| 项 | 已确认 | 缺什么 |
| --- | --- | --- |
| `/W` 的具体除法指令 | 进入 `0x145DDCA90` 前已是 `dx/W`（`W=2560` 精确），链上 4 个转发点无算术 | 鼠标事件生产者（`0x145DC7B50` 的调用者，vtable `0x14E9731B0` slot `+0x7F0`，63 处候选）未定位 |
| 触屏虚拟摇杆前端 | UI 控件 `textures/ui/joystick_frame`/`joystick_knob`（`0x140707DE0`）与 `:touch_virtual_joystick_*:`（`0x144D89B40`）存在；合成层只做 `>1 归一化` | 像素拖动 → `mAnalogMoveVector(+0x04/+0x08)` 的写入者、死区半径、最大半径、固定/动态摇杆中心、疾跑区均未定位；`ClientMoveInputHandler::_updateClientMoveVector` 的静态地址未解析（`0x018B1887` 的全部引用均为依赖声明、准星 tick 与移动系统） |
| 经典 D-pad 角键 | `dpad_{up,down,left,right}` 注册于 `0x1443765D0`；对角位 `0x200..0x1000` 由触屏角键置位（前置案例 `[推断]`） | 角区命中几何、同一手指滑入角区时是否与正交位重叠、多点触控下正交+对角并存的真实触发：未定位 |
| `hotbar_only_touch` 存档映射 | 门控 `getOption(0x1DB)`；注册名 `hotbar_only_touch`（`0x144E072C5`/`0x144E072DF`，registrar `0x144CDFC30`） | `ctrl_hotbarOnlyTouch:1` 重启后 `0x1DB` 仍为 false：是存档键不映射到 `0x1DB`，还是无触摸设备时被运行期覆盖，未定 |
| slot 14 的 `edx` 语义 | 鼠标路径恒 `0`；`0x187` 为真走 `0x1404BFDED` | 触屏时是否传拖动帧计数；`0x1404BFDED` 分支未展开 |
| `touch_invert_y_axis`（`0x185`） | 选项注册于 `0x185` | 消费点未定位（`dy` 在进入 handler 链前已取负，反转可能在生产者里） |
| `c = [screen->vtable[+0xE0]()+0x3C]` | 触屏分支的增幅基数 | 该字段的来源与单位未定位 |

---

## 9. 边界

- 静态结论全部来自 `pe_map.py` 离线反汇编；运行期只用了 VA enter hook 与 `mouse_event` 注入，**没有**触屏/手柄实测。
- `hook_install` 只有 4 个槽，且通用 detour 不保留 XMM：slot 27 不可 hook，slot 14/15 可 hook（整型参数）。
- 本轮未改 `mod/`、`driver/`、`tests/`；`options.txt` 的 `ctrl_hotbarOnlyTouch`、`ctrl_sensitivity2_mouse`、`ctrl_spyglassdamp_mouse` 已恢复 `0 / 0.5 / 0.5`。
