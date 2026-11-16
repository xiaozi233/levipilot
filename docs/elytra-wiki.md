# 鞘翅滑翔与烟花助推

在《我的世界》基岩版（`1.26.51.01`）中，展开鞘翅后的滑翔运动遵循一套独立的空气动力学递推规则：游戏每刻先按俯仰角抵消一部分重力，再将下坠速度按比例转化为向前推进的水平速度，并允许通过抬头消耗水平速度来换取垂直抬升。

> **单位与符号约定**：全文速度单位均为 **格/刻**（$1\text{ 秒} = 20\text{ 刻}$），角度以**度（$^\circ$）**为单位。规定视角俯仰角 $\theta$ **低头俯冲为正、抬头爬升为负**（玩家视角输入范围被钳位在 $[-89.9^\circ, +89.9^\circ]$）。下标 $t$ 表示当前刻最终值，$0$ 表示本刻初始输入值（即上一刻结束时的速度）。
>
> 若需 `1.26.51.01` 逆向地址（VA）、反编译符号、精确单精度（`float32`）字面量与组件字段对照、分级证据标签及逐 tick 实测误差账本，见 [`elytra-movement.md`](elytra-movement.md)。

---

## 碰撞箱与起停条件

玩家进入滑翔状态时，碰撞箱会从站立时的 `[0.6, 1.8, 0.6]`（宽 `0.6`、高 `1.8`）立即收缩为 **`[0.6, 0.6, 0.6]`** 的正方体碰撞箱。

与滑翔起停相关的判定具有以下四条特性：

1. **无头顶空间阻挡检查**：进入滑翔与维持滑翔时，游戏**不会检查周围是否有容纳站立姿态的空间**。因此，即使在头顶净空不足 `1.8` 格的矮顶下起跳（例如净空仅 `0.834` 格），也能直接展开鞘翅将碰撞箱缩为 `0.6 × 0.6`，并顺畅钻入 **`1×1`（1 格高）** 的狭窄通道内持续滑翔。
2. **缓降效果的“起飞门控”**：如果玩家在空中下落时**身上已经带有缓降（Slow Falling）效果**，按下跳跃键将**无法展开鞘翅**进入滑翔；但如果**先在无缓降状态下展开鞘翅进入滑翔，再获得缓降效果**，则滑翔不会中断，而是切换为极低重力的缓降滑翔状态。
3. **生存模式二段按空格收翅**：在生存模式下，滑翔途中再次按下跳跃键会立即收起鞘翅、退出滑翔，切回普通自由落体。
4. **创造模式长按空格微升**：在创造模式下（与是否开启 `mayfly` 飞行权限无关；生存模式即使通过命令开启 `mayfly` 也不会触发），当滑翔已持续满 **`11` 刻**且玩家按住跳跃键时，每刻会在进入滑翔物理计算**之前**，先为垂直速度额外增加 `0.1`：
   $$V_{y,0} \mathrel{+}= 0.1$$

---

## 滑翔运动

### 视线方向与初始量采样

记进入本刻滑翔物理时的三维速度为 $\vec{V}_0 = (V_{x,0}, V_{y,0}, V_{z,0})$。若此时垂直速度 $V_{y,0} > -0.5$，游戏会先将玩家的**累计摔落距离重置为 `1.0` 格**，防止平飞或缓降滑翔过程无限累积摔落伤害。

在修改任何速度分量之前，游戏会**先行采样记录本刻的初始水平速率 $V_{H,0}$**：

$$V_{H,0} = \sqrt{V_{x,0}^2 + V_{z,0}^2}$$

需要注意，后续**抬头爬升**与**水平转向对齐**两步中用到的都是这个**修改前的初始值 $V_{H,0}$**。

记玩家的单位视线向量为 $\hat{l} = (l_x, l_y, l_z)$，其水平投影模长为 $L_H = \sqrt{l_x^2 + l_z^2}$，水平单位视线方向为：

$$\hat{l}_H = \left(\frac{l_x}{L_H},\; \frac{l_z}{L_H}\right)$$

*（注：底层在计算视线向量 $\hat{l}$ 时，会对上一刻与当前刻的朝向角做 $360^\circ$ 插值后查正弦表；而后续公式中直接出现的俯仰角 $\theta$，则直接取当前刻未插值的俯仰角。此外，源码中的水平姿态系数写为 $\min\!\left(1,\; \frac{\lVert\hat{l}\rVert}{0.4}\right)\cos^2\theta$，由于单位视线向量模长 $\lVert\hat{l}\rVert \approx 1 > 0.4$，该系数恒等于 $\cos^2\theta$。）*

随后，游戏每刻按以下 **六步** 依次更新速度：

### 1. 重力与机翼升力抵消

滑翔时的基础重力加速度 $g$ 由玩家是否带有**缓降**效果决定：

$$g = \begin{cases}
-0.01 & \text{带有缓降效果}, \\
-0.08 & \text{无缓降效果}.
\end{cases}$$

机翼会根据水平姿态因子 $\cos^2\theta$ 抵消最多 $75\%$ 的重力，更新后的垂直速度为：

$$V_{y,1} = V_{y,0} + \bigl(1 - 0.75\cos^2\theta\bigr)\,g$$

- **平视（$\theta = 0^\circ$）**：$\cos^2\theta = 1$，每刻只承受 $25\%$ 的重力，净下沉加速度为 **$-0.02$**（带有缓降时仅为 **$-0.0025$**）。
- **垂直俯仰（$\theta \to \pm 90^\circ$）**：$\cos^2\theta \to 0$，升力抵消消失，承受全额重力 **$-0.08$**。

### 2. 下坠速度转化为水平推进

当水平视线投影非零（$L_H > 0$）且经第 1 步后玩家仍处于**下落状态（$V_{y,1} < 0$）**时，游戏会抽取 $0.1\cos^2\theta$ 比例的下坠速度，将其等量转化为沿水平视线方向 $\hat{l}_H$ 的推力，同时抵消同等数值的下坠速度：

$$\Delta v_{\text{lift}} = -0.1 \, V_{y,1} \cos^2\theta > 0$$

$$
\begin{aligned}
\vec{V}_{H,1} &= \vec{V}_{H,0} + \Delta v_{\text{lift}} \cdot \hat{l}_H, \\
V_{y,2} &= V_{y,1} + \Delta v_{\text{lift}}.
\end{aligned}
$$

这正是鞘翅在不使用烟花时依然能保持高速前飞的原因：下坠越快、视角越接近水平，每刻从竖直方向抽入水平方向的加速量 $\Delta v_{\text{lift}}$ 就越大。若此时玩家正在向上运动（$V_{y,1} \ge 0$），则跳过此步（$\Delta v_{\text{lift}} = 0$）。

### 3. 抬头爬升：消耗水平速度换取 $3.2$ 倍抬升

当玩家处于**抬头状态（$\theta < 0$）**时，会按本刻最开始记录的**初始水平速率 $V_{H,0}$** 与仰角正弦值抽取水平速度：

$$\Delta v_{\text{climb}} = V_{H,0} \cdot (-\sin\theta) \cdot 0.04 > 0$$

随后从水平速度中扣除 $\Delta v_{\text{climb}}$，并将其**放大 $3.2$ 倍**叠加到垂直速度上：

$$
\begin{aligned}
\vec{V}_{H,2} &= \vec{V}_{H,1} - \Delta v_{\text{climb}} \cdot \hat{l}_H, \\
V_{y,3} &= V_{y,2} + 3.2\,\Delta v_{\text{climb}}.
\end{aligned}
$$

若玩家未抬头（$\theta \ge 0$），则跳过此步（$\Delta v_{\text{climb}} = 0$）。因此，先低头俯冲积攒较大的水平速率 $V_{H,0}$，再迅速抬头拉起，即可利用 $3.2$ 倍的放大系数获得显著的短时爬升速度。

### 4. 水平转向对齐

当 $L_H > 0$ 时，游戏会以**初始水平速率 $V_{H,0}$** 作为目标速率大小，将当前水平速度向量按 $10\%$ 的比例拉向当前水平视线方向 $\hat{l}_H$：

$$\vec{V}_{H,3} = \vec{V}_{H,2} + 0.1\,\bigl(V_{H,0}\,\hat{l}_H - \vec{V}_{H,2}\bigr) = 0.9\,\vec{V}_{H,2} + 0.1\,V_{H,0}\,\hat{l}_H$$

这一步同时带来两个效果：

- **转向惯性**：空中急转弯时，水平移动方向不会瞬间锁定准星，而是每刻向新视线方向偏转 $10\%$。
- **单刻水平增量折算**：由于括号内的目标速率使用的是修改前的 $V_{H,0}$，第 2 步刚转入的水平推进 $\Delta v_{\text{lift}}$ 与第 3 步刚扣除的爬升量 $\Delta v_{\text{climb}}$ 在经过本步加权后，实际保留 $90\%$。

### 5. 烟花火箭助推（仅助推生效期间）

若当前处于烟花助推生效窗口内，三维速度向量 $\vec{V}_3 = (V_{x,3}, V_{y,3}, V_{z,3})$ 会直接朝三维视线方向 $\hat{l}$ 强力收敛并叠加推力：

$$
\begin{aligned}
\vec{V}_4 &= \vec{V}_3 + 0.5\,\bigl(1.5\,\hat{l} - \vec{V}_3\bigr) + 0.1\,\hat{l} \\
&= 0.5\,\vec{V}_3 + 0.85\,\hat{l}
\end{aligned}
$$

若当前未处于烟花助推状态，则 $\vec{V}_4 = \vec{V}_3$。

可以看到，助推更新的不动点为 $1.7\,\hat{l}$：当玩家速度慢于 $1.7$ 格/刻时，烟花会大幅拉高速度（从静止平视点火，首刻末速率即可达 **$0.8424$ 格/刻**）；但若玩家刚从极高速俯冲拉起、当前速率高于 $1.7$ 格/刻，点火瞬间反而会被 $0.5$ 的权重拉慢。

### 6. 空气阻力

最后，水平方向乘以阻力系数 **`0.99`**，垂直方向乘以阻力系数 **`0.98`**，得到本刻最终速度：

$$
\begin{aligned}
V_{x,t} &= 0.99 \, V_{x,4}, \\
V_{y,t} &= 0.98 \, V_{y,4}, \\
V_{z,t} &= 0.99 \, V_{z,4}.
\end{aligned}
$$

### 直线滑翔汇总公式与典型稳态速度

在不转弯且无烟花助推的直线滑翔中（$\vec{V}_{H,0} = V_{H,0}\,\hat{l}_H$），将上述步骤合并后，单刻水平速率 $V_{H,t}$ 与垂直速度 $V_{y,t}$ 的递推式可简写为：

$$
\begin{aligned}
V_{y,1} &= V_{y,0} + \bigl(1 - 0.75\cos^2\theta\bigr)\,g, \\
\Delta v_{\text{lift}} &= \begin{cases} -0.1\,V_{y,1}\cos^2\theta & V_{y,1} < 0, \\ 0 & V_{y,1} \ge 0, \end{cases} \\
\Delta v_{\text{climb}} &= \begin{cases} -0.04\,V_{H,0}\sin\theta & \theta < 0, \\ 0 & \theta \ge 0, \end{cases} \\[4pt]
V_{H,t} &= 0.99 \times \bigl(V_{H,0} + 0.9\,(\Delta v_{\text{lift}} - \Delta v_{\text{climb}})\bigr), \\
V_{y,t} &= 0.98 \times \bigl(V_{y,1} + \Delta v_{\text{lift}} + 3.2\,\Delta v_{\text{climb}}\bigr).
\end{aligned}
$$

以平飞（$\theta = 0^\circ$）为例，$\Delta v_{\text{climb}} = 0$，$V_{y,t} = 0.98 \times 0.9\,(V_{y,0} + 0.25\,g) = 0.882\,(V_{y,0} + 0.25\,g)$，令 $V_{y,t} = V_{y,0}$ 即可直接解出平飞的终端下沉速度：

$$V_{y,\infty} = \frac{0.2205\,g}{1 - 0.882} = \frac{0.2205\,g}{0.118} \approx \begin{cases}
-0.1495 & \text{正常（}g = -0.08\text{）}, \\
-0.0187 & \text{缓降（}g = -0.01\text{）}.
\end{cases}$$

不同固定俯仰角与状态下的终端稳定速度如下表所示：

| 滑翔状态 / 俯仰角 $\theta$ | 终端垂直速度 $V_{y,\infty}$（格/刻） | 终端总速率 $\lvert \vec{V}_\infty \rvert$（格/刻） | 折合每秒速率（格/秒） | 运动特征 |
| --- | --- | --- | --- | --- |
| **$-45^\circ$（抬头）** | `-0.2259` | `0.4158` | `8.3` | 耗尽初始水平速度后失速下坠 |
| **$-20^\circ$（微抬头）** | `-0.0774` | `0.3784` | `7.6` | 低速缓降，总速率极低 |
| **$0^\circ$（平视巡航）** | `-0.1495` | `1.5175` | `30.4` | 每刻 `-0.02` 净重力持续转入前向，滑翔比约 `10.1 : 1` |
| **$+20^\circ$（缓俯冲）** | `-0.2266` | `2.0081` | `40.2` | 兼顾较高水平推进与适中掉高 |
| **$+45^\circ$（中俯冲）** | `-0.6746` | `3.2980` | `66.0` | 高速滑降，用来快速积攒水平速率 |
| **$+60^\circ$（陡俯冲）** | `-1.3957` | `3.5404` | `70.8` | 总速率接近峰值，下坠明显加快 |
| **$+75^\circ$（急俯冲）** | `-2.7842` | `3.2659` | `65.3` | $\cos^2\theta$ 很小，向水平方向转化的比例大幅衰减 |
| **$+90^\circ$（垂直俯冲）** | `-3.9200` | `3.9200` | `78.4` | 纯 `-0.08` 重力与 `0.98` 阻力平衡（$-0.08 \times 0.98 / 0.02 = -3.92$） |
| **$0^\circ$ + 缓降效果** | `-0.0187` | `0.1897` | `3.8` | 重力缩为 `1/8`，稳态各轴速度同步精确缩为无缓降时的 `1/8` |
| **$0^\circ$ + 持续烟花助推** | `-0.0158` | `1.6696` | `33.4` | 速度被锚定在近 `1.67` 格/刻，同时下沉被压制到极小 |

---

## 烟花火箭助推

仅当玩家已处于滑翔状态时，使用烟花火箭才会触发助推效果。

### 助推持续时间

每次燃放烟花时，真实的助推持续时间（单位：刻）由服务端根据烟花物品的**飞行时间等级（`Flight`）**随机生成，并每刻递减 `1` 直至归零：

$$T_{\text{boost}} = 10 \times (\text{Flight} + 1) + U(0, 5) + U(0, 6)$$

其中 `Flight` 为合成烟花时加入的火药数量（未含 `Flight` 标签时按 `0` 计），$U(0, 5)$ 为 `0 ~ 5` 的均匀随机整数，$U(0, 6)$ 为 `0 ~ 6` 的均匀随机整数（两者之和分布在 `0 ~ 11` 之间，平均为 `5.5` 刻）：

- **无 `Flight` 标签（默认烟花）**：持续 **`10 ~ 21` 刻**（平均 `15.5` 刻）
- **一级烟花（`Flight = 1`）**：持续 **`20 ~ 31` 刻**（平均 `25.5` 刻）
- **二级烟花（`Flight = 2`）**：持续 **`30 ~ 41` 刻**（平均 `35.5` 刻）
- **三级烟花（`Flight = 3`）**：持续 **`40 ~ 51` 刻**（平均 `45.5` 刻）

### 本地预测与连续燃放的“防短覆盖”

1. **客户端 `20` 刻本地预测**：按下使用键的瞬间，客户端会先在本地填入固定的 **`20` 刻**预测助推，随后由服务端下发按上式掷出的真实随机时长 `T_boost` 完成同步，最终实际加速帧数严格等于服务端生成的 `T_boost`。
2. **短时长防降级保护**：如果在当前助推尚未结束（剩余时长记为 $T_{\text{remain}}$）时再次燃放烟花：
   - 当新时长 $T_{\text{new}} \ge T_{\text{remain}}$ 时，正常刷新为 $T_{\text{new}}$（实现续期）；
   - 当新时长 $T_{\text{new}} < T_{\text{remain}}$（或当前处于无限时长 `-1`）时，游戏会**直接忽略这次覆盖**，保留原本更长的剩余时间，避免短时长烟花意外截断正在进行的更长助推。
3. **火箭实体不产生推力**：生成并依附在玩家身上的烟花火箭实体每刻只是将自身坐标与速度同步为玩家的位置与速度，**不会对玩家施加任何物理推力**；全部加速效果均来自前文滑翔运动第 5 步的助推公式。

---

## 撞墙伤害与耐久消耗

### 高速撞墙伤害

滑翔途中在水平方向撞击方块时，游戏按碰撞前后的**水平速率减少量**计算撞击伤害：

$$\text{Damage} = \bigl(V_{H,\text{pre}} - V_{H,\text{post}}\bigr) \times 10 - 3$$

其中 $V_{H,\text{pre}}$ 为撞墙前的水平速率，$V_{H,\text{post}}$ 为碰撞阻挡后的剩余水平速率（正面垂直撞墙停下时 $V_{H,\text{post}} = 0$）。

- 当 $\text{Damage} > 0$（正面撞墙对应 $V_{H,\text{pre}} > 0.3$ 格/刻）时触发撞墙受伤判定；
- 生存模式下实际扣除的生命值为**向下取整**后的整数点血量：
  $$\Delta \text{HP} = \lfloor \text{Damage} \rfloor$$
  例如计算值为 `6.74` 时实际扣 `6` 点生命（$3$ 颗心），计算值为 `13.10` 或 `13.63` 时实际均扣 `13` 点生命（$6.5$ 颗心）。

### 耐久度与耐久附魔

- **耐久上限与破损阈值**：鞘翅的最大耐久度为 **`432`**。当已损耗值达到 **`431`**（即仅剩最后 `1` 点耐久、物品变为破损外观）时，鞘翅将无法继续使用，但不会像普通工具那样直接碎裂消失。
- **每 `20` 刻扣减一次**：滑翔期间飞行计时器从 `0` 开始每刻加 `1`（离开滑翔立即清零）；每当计时器满足 `FallFlyTicks % 20 == 19`（即滑翔满第 `20, 40, 60, ...` 刻，恰好每秒一次）时，触发 `1` 点耐久损耗判定。
- **耐久（Unbreaking）附魔**：穿戴附有等级 $L$ 耐久附魔的鞘翅时，每次触发损耗判定实际扣除耐久的概率为：
  $$P = \frac{1}{L + 1}$$
  即无附魔与耐久 I、II、III 的单次扣点概率依次为 **$100\%$、$50\%$、$33.3\%$、$25\%$**，无附魔满耐久鞘翅可持续滑翔 $431 \times 20 = 8620$ 刻（`431` 秒），耐久 III 下数学期望滑翔时间约为 `1724` 秒（约 `28.7` 分钟）。

---

## 与 Java 版的主要差别（对照 `JE 1.12.2` 与 `JE 26.1.2`）

基岩版（`1.26.51.01`）的滑翔核心公式沿自早期的 Java 版（如 `JE 1.12.2`），但在**浮点精度、缓降、起停操作、烟花实现、摔落重置与撞墙伤害结算**上，与现代 Java 版（如 `JE 26.1.2`）存在多处关键差异：

| 机制维度 | 基岩版（`1.26.51.01`） | Java 版（`JE 1.12.2` / `JE 26.1.2`） |
| --- | --- | --- |
| **浮点精度与查表** | 全程 32 位 `float`；`tableSin`/`tableCos` 用 `float32` 系数 `10430.378f`（65536 项）；保留 `min(1, \|look\| / 0.4)` 乘子 | 速度全程 64 位 `double`；`JE 1.12.2` 查表同为 `10430.378f` 且保留 `/ 0.4`；`JE 26.1.2` 升力因子改用原生双精度 `Math.cos(leanAngle)` 并移除 `/ 0.4`，其余查表用双精度系数 `10430.378350470453` |
| **视角采样与除零保护** | 视线 `look` 对 `(yaw, prevYaw)` / `(pitch, prevPitch)` 做 `wrapAngle360` 插值（实测有 1-tick 观测延迟），而 `cosPitch`/`pitch < 0`/`sinPitch` 读未插值 `pitchDeg`；`pitch < 0` 分支不检查 `horizLookSq > 0`（靠 `[-89.9°, +89.9°]` 钳位） | 视线与俯仰角均直接读取当前角度，无历史插值与未插值混用；`JE 1.12.2` 爬升分支同样无 `lookHorLength > 0` 检查，**`JE 26.1.2` 已补上 `lookHorLength > 0.0` 防护** |
| **重力来源与缓降** | 滑翔重力写死 `-0.08f` / 缓降 `-0.01f`；**无论上升还是下落全程取 `-0.01f`**；空中已带缓降时无法按跳跃起飞（须先滑翔后再施缓降） | `JE 26.1.2` 调用 `getEffectiveGravity()` 读重力属性（默认 `0.08`），**仅 `vel.y <= 0` 下落时**带缓降才夹到 `<= 0.01`（抬头上升期仍承受全额重力）；空中带缓降仍可起飞 |
| **起停条件与创造微升** | 进入与收缩碰撞箱不校验几何适应空间（`0.834` 格净空可起飞，可穿 `1` 格高通道）；**生存模式空中再按跳跃会主动收翅**；创造模式相关能力位（`0x04`，不读 `MayFly`）满 `11` 刻起按住跳跃每刻在物理前 `vel.y += 0.1f` | `JE 26.1.2` 在 `Player.updatePlayerPose()` 中调用 `canPlayerFitWithinBlocksAndEntitiesWhen` 按姿态碰撞箱（`FALL_FLYING` / `SWIMMING` 均为 `0.6 × 0.6`）校验空间；空中再按跳跃不收翅（仅落地、骑乘、可攀爬方块、飘浮或滑翔器损坏时停止）；无创造 `+0.1` 微升 |
| **烟花火箭助推** | 烟花实体仅做视觉跟随、不施加推力；助推在 `GlideMoveSystem` 中依 `MovementEffects` 倒计时结算（客户端先 `20` 刻预测，再同步服务端随机时长），**连放不会叠加多发推力**，且拒绝短时长覆盖 | `FireworkRocketEntity.tick()` 直接给绑定玩家叠加助推速度；若同时绑定多枚烟花，**多枚火箭会各自施加推力形成叠加**，也没有“短时长防降级”概念 |
| **摔落距离重置** | 当 `vel.y > -0.5f` 时**无条件**写 `fallDistance = 1.0f`（与 `JE 1.12.2` 一致） | `JE 26.1.2`（`Entity.checkFallDistanceAccumulation`）仅当 `vel.y > -0.5` **且 `fallDistance > 1.0`** 时才压回 `1.0` |
| **撞墙伤害结算** | `(V_H_pre - V_H_post) * 10 - 3` 后**按 `floorf` 向下取整**扣整数点生命值 | `JE 1.12.2` 与 `JE 26.1.2` 直接将浮点 `dmg` 传入 `hurt` 结算，**保留小数生命值伤害** |
| **耐久消耗与槽位** | 固定检查胸甲槽（slot 1），在 `FallFlyTicks % 20 == 19` 时判定 1 次 `1 / (L + 1)` 扣点 | `JE 26.1.2` 支持任意携带 `GLIDER` 组件的有效槽位，在 `(fallFlyTicks + 1) % 20 == 0` 时随机抽一个槽位扣 `1` 耐久，并每 `10` tick 触发 `ELYTRA_GLIDE` 事件 |

---

## 等价伪代码参考（基岩版 `1.26.51.01`）

下面将基岩版 `1.26.51.01` 的底层实现整理为易读的 C++ 风格伪代码，编号严格对应前文**“滑翔运动”第 1～6 步**。其中略去了底层内存偏移、汇编寄存器与冗长的 IEEE-754 尾数（如把 `-0.0799999982f` 简记为 `-0.08f`）；若需用于逐比特浮点对质或查证 VA 地址，请参阅 [`elytra-movement.md`](elytra-movement.md) 第八节。

### 1. 状态结构与正弦查找表

```cpp
struct Vec3 {
    float x, y, z;
};

struct ElytraState {
    // 核心物理量（全流程以 32 位单精度 float 计算）
    Vec3 vel;            // 对应 StateVectorComponent::mPosDelta（本刻待应用位移，非上刻 mPos - mPosPrev）
    float fallDistance;  // 累计摔落距离
    float yawDeg,   prevYawDeg;    // 当前与上一刻偏航角（度）
    float pitchDeg, prevPitchDeg;  // 当前与上一刻俯仰角（度：低头 > 0，抬头 < 0）

    // 状态与计时
    bool isGliding;
    bool hasSlowFalling;          // 是否激活缓降效果
    bool hasCreativeLiftAbility;  // 创造模式相关能力位（与 MayFly 无关）
    uint32_t fallFlyTicks;        // 连续滑翔刻数（退出滑翔立即归零）
    int32_t boostTicksLeft;       // 烟花助推剩余刻数（0 = 未生效，-1 = 无限时长）
};

// 基岩版正弦查找表：启动时以 sinf(float(i) / 10430.378f) 填充 65536 项
extern float SIN_TABLE[65536];

constexpr float PI_F       = 3.1415927f;
constexpr float DEG_TO_RAD = 0.017453292f;
constexpr float LUT_SCALE  = 10430.378f;

inline float tableSin(float rad) {
    return SIN_TABLE[static_cast<uint16_t>(static_cast<int32_t>(rad * LUT_SCALE))];
}

inline float tableCos(float rad) {
    // 在 float32 下先 +16384.0f 再截断；极大角度时舍入进位会产生“大半角”偏差
    return SIN_TABLE[static_cast<uint16_t>(static_cast<int32_t>(rad * LUT_SCALE + 16384.0f))];
}

// 360° 最短包角（仅用于抑制跨 ±180° 跳变；当前位置插值权重 α = 1）
inline float wrapAngle360(float deg, float prevDeg) {
    float r = std::fmodf((deg - prevDeg) + 180.0f, 360.0f);
    if (r < 0.0f) r += 360.0f;
    return (r - 180.0f) + prevDeg;
}

// 视线向量公式沿自早期方块逻辑：走 (-π - yaw) 与 (-pitch) 查表，且不做三维模长归一化
inline Vec3 calcLookVectorLUT(float yawDeg, float pitchDeg) {
    float yawArg   = -PI_F - yawDeg * DEG_TO_RAD;
    float pitchArg = -pitchDeg * DEG_TO_RAD;
    float negCosP  = -tableCos(pitchArg);
    return {
        tableSin(yawArg) * negCosP,
        tableSin(pitchArg),
        tableCos(yawArg) * negCosP
    };
}
```

### 2. 起滑前输入注入（创造模式按住空格微升）

```cpp
// 在每刻“滑翔物理 stepGlidePhysics”之前执行
void stepGlideInput(ElytraState& s, bool holdingJump) {
    if (!s.isGliding) {
        s.fallFlyTicks = 0;
        return;
    }

    // 仅创造能力位（不读 MayFly）且连续滑翔满 11 刻后，按住跳跃每刻预叠 +0.1 升力
    if (holdingJump && s.hasCreativeLiftAbility && s.fallFlyTicks >= 11) {
        s.vel.y += 0.1f;
    }
    s.fallFlyTicks += 1;
}
```

### 3. 每刻滑翔物理主流程（`1`～`6` 步）

```cpp
void stepGlidePhysics(ElytraState& s) {
    // [0] 摔落距离重置与视角采样
    if (s.vel.y > -0.5f) {
        s.fallDistance = 1.0f; // 只要垂速大于 -0.5 就无条件写 1.0（与 Java 1.21+ 不同）
    }

    // 三维视线对历史角度做 360° 包角插值，而俯仰因子直接读未插值的当前 pitchDeg
    float yawInterp   = wrapAngle360(s.yawDeg,   s.prevYawDeg);
    float pitchInterp = wrapAngle360(s.pitchDeg, s.prevPitchDeg);
    Vec3 look = calcLookVectorLUT(yawInterp, pitchInterp);

    float pitchRad  = s.pitchDeg * DEG_TO_RAD;
    float cosPitch  = tableCos(pitchRad);
    float lookLen3d = std::sqrtf(look.x * look.x + look.y * look.y + look.z * look.z);
    float d3        = std::fminf(1.0f, lookLen3d / 0.4f) * cosPitch * cosPitch;

    // 提前采样修改前的初始水平速率：供第 [3] 步爬升消耗与第 [4] 步转向对齐复用
    float initialHorizSpeed = std::sqrtf(s.vel.x * s.vel.x + s.vel.z * s.vel.z);

    float horizLookSq  = look.x * look.x + look.z * look.z;
    float horizLookLen = std::sqrtf(horizLookSq);

    // [1] 重力与机翼升力抵消（带有缓降时全程 g = -0.01f，不区分升降方向）
    float gravity = s.hasSlowFalling ? -0.01f : -0.08f;
    s.vel.y += (1.0f - 0.75f * d3) * gravity;

    // [2] 下坠速度转化为水平推进
    if (horizLookSq > 0.0f && s.vel.y < 0.0f) {
        float lift = d3 * (-0.1f * s.vel.y);
        s.vel.x += (look.x / horizLookLen) * lift;
        s.vel.y += lift;
        s.vel.z += (look.z / horizLookLen) * lift;
    }

    // [3] 抬头爬升（pitch < 0）：消耗初始水平速率，换取 3.2 倍垂直抬升
    // 注：与早期 Java 版相同，此处未再检查 horizLookSq > 0，靠视角钳位 [-89.9°, +89.9°] 避开除零
    if (pitchRad < 0.0f) {
        float sinPitch = tableSin(pitchRad);
        float climb    = (-sinPitch) * initialHorizSpeed * 0.04f;
        s.vel.x -= (look.x / horizLookLen) * climb;
        s.vel.y += 3.2f * climb;
        s.vel.z -= (look.z / horizLookLen) * climb;
    }

    // [4] 水平转向对齐：按 10% 将当前水平速度拉向“以 initialHorizSpeed 为速率的视线水平方向”
    if (horizLookSq > 0.0f) {
        float targetVx = (look.x / horizLookLen) * initialHorizSpeed;
        float targetVz = (look.z / horizLookLen) * initialHorizSpeed;
        s.vel.x += (targetVx - s.vel.x) * 0.1f;
        s.vel.z += (targetVz - s.vel.z) * 0.1f;
    }

    // [5] 烟花助推（若助推有效，直接以未归一化的 LUT 视线 look 推向不动点 1.7 * look）
    // 注：基岩版烟花火箭实体本身只做位置/速度跟随，不负责施加速度；助推在空气阻力之前完成
    if (s.boostTicksLeft != 0) {
        s.vel.x += 0.1f * look.x + 0.5f * (1.5f * look.x - s.vel.x);
        s.vel.y += 0.1f * look.y + 0.5f * (1.5f * look.y - s.vel.y);
        s.vel.z += 0.1f * look.z + 0.5f * (1.5f * look.z - s.vel.z);
    }

    // [6] 空气阻力衰减并写回本刻最终速度
    s.vel.x *= 0.99f;
    s.vel.y *= 0.98f;
    s.vel.z *= 0.99f;
}
```

### 4. 烟花助推时长、防短覆盖、撞墙伤害与耐久

```cpp
// 服务端生成烟花助推时长（客户端按下瞬间先在本地写固定 20 tick 预测，随后被服务端覆盖）
int32_t calcFireworkBoostDuration(bool hasFlightTag, int flightLevel, Random& rng) {
    int base = hasFlightTag ? 10 * (flightLevel + 1) : 10;
    return base + rng.nextInt(6) + rng.nextInt(7); // 含两个随机项 U(0,5) + U(0,6)
}

// 短时长防降级保护：避免连放烟花时较短的新时长截断正在生效的更长助推
void applyBoostDuration(int32_t& curTicksLeft, int32_t newTicks) {
    if (newTicks != curTicksLeft) {
        if (curTicksLeft == -1) return;                     // 当前为无限时长：跳过
        if (newTicks != -1 && newTicks < curTicksLeft) return; // 新时长更短：跳过
    }
    curTicksLeft = newTicks;
}

// 撞墙动能伤害：与 Java 版保留小数伤害不同，基岩版实测按 floor 向下取整扣整数血量
int calcGlidingCrashDamage(float preHorizSpeed, float postHorizSpeed, bool isInvulnerable) {
    if (isInvulnerable) return 0;
    float dmg = (preHorizSpeed - postHorizSpeed) * 10.0f - 3.0f;
    return (dmg > 0.0f) ? static_cast<int>(std::floorf(dmg)) : 0; // dmg > 4 播大摔落音效
}

// 鞘翅耐久可用阈值与每 20 tick 消耗规则
constexpr int ELYTRA_MAX_DAMAGE = 432;

inline bool canElytraGlide(int damage) {
    return damage < ELYTRA_MAX_DAMAGE - 1; // damage >= 431（仅剩 1 点）不可再滑翔
}

inline bool shouldDamageElytraThisTick(uint32_t fallFlyTicks, int unbreakingLevel, Random& rng) {
    if (fallFlyTicks % 20 != 19) return false;       // 滑翔满第 20, 40, 60... tick 判定一次
    return rng.nextInt(unbreakingLevel + 1) == 0;    // 扣点概率 = 1 / (L + 1)
}
```
