# LeviPilot 实验手册（动态辅助）

> 动态实验只回答“是不是这条路径/这个值”。机制结论按 `SKILL.md` §2 回到静态；
> 只有 `[实测]` 的数字不能写进 `facts.md` 的结论区。

## 0. 开工前

1. **版本与目录**：游戏在 `%APPDATA%\levilauncher.exe\versions\<版本>\`；
   mod 按 `docs/offsets/<版本>.json` 匹配（`allow_version_mismatch` 只用于移植）。
2. **起游戏（带能力开关）**：
   ```bash
   py -3.11 driver/dev.py run --world "超平坦测试" \
     --allow allow_input_inject=1 --allow allow_selftest_stress=1
   ```
   `--allow` 写 `harness.json` 顶层开关（启动时读一次，改完必须重启）；
   `--options` 写游戏选项，两者不要混。需要 hook/写内存时再加
   `--allow allow_hook_va=1` / `--allow allow_write=1`。
3. **sanity**：`client.wait_ticks(20)` 能推进说明世界在 tick（失焦暂停由
   `pause_menu_on_focus_lost` 决定）。每次重启后先确认进程是新的——上一轮可能留下
   hook（`hook_duplicate`）或残留方块。
4. 常用常量：`超平坦测试` 自然地表 **y=-60**（草方块 Y=-61，下面两层泥土+基岩）；
   出生点平台在 -49，不要拿出生点高度当地面。

## 1. 实验闭环

```python
import sys; sys.path.insert(0, "driver")
from harness.client import ModClient
from harness.lab import Builder, record_session, move_and_wait, fit_recursion, recursion

with ModClient() as client:
    client.wait_ticks(20)
    builder = Builder(client)
    builder.ensure_loaded(205, 205)          # 先传送，让目标区块加载
    checks = builder.build(                  # 命令 → 回读 → 不一致自动重试
        ["fill 200 -52 200 206 -40 206 stone",
         "fill 201 -51 201 205 -41 205 water"],
        {(203, -45, 203): "minecraft:water"},
    )
    assert all(c.ok for c in checks), checks

    with record_session(client) as session:  # record_start 会重置环形游标
        client.teleport(203.5, -45.5, 203.5)
        client.wait_ticks(120)
    rows = session.entries                   # list[RecordEntry]（pos/vel/flags/方块哈希）

    free = [r for r in rows if not r.on_ground and r.in_water]
    a, b, resid = fit_recursion(recursion(free, "vy"))
    print(f"vy' = {a:.4f}*vy + {b:+.4f}  v_inf={b/(1-a):+.4f}  resid={resid:.1e}")
```

要点：

- `set_input`/`move` 立即返回，输入在后台跑 N tick；录制窗口要等够：
  `move_and_wait(client, ticks=70, settle=2)`，并把衰减段一起录（等 `70+55`）。
- 分段靠记录字段：`on_ground` / `sprint` / 输入位 / `in_water` / `in_lava` /
  `block_feet` 等方块名哈希（`lab.fnv1a("minecraft:water")` == 记录里的哈希）。
- 拟合优先看递推 `(a, b)` 再看稳态；`a` 是阻力、`b/(1-a)` 是平衡值。

## 2. 硬坑速查

完整的坑与守卫见 `pitfalls.md`；这里只列最容易污染数据的行为：

| 坑 | 正确处理 |
| --- | --- |
| 未加载区块的方块命令静默失败 | 先传送到场地附近，`Builder.build` 回读+重试 |
| 区块卸载回滚 | 现建现用；实验前 `blocks` 复核 |
| 液体不流动 | `Builder.poke(x,y,z)` 戳醒 |
| 直接传送进液体 | 落到液面 + 按住前进；不要指望俯仰驱动游泳 |
| 合成输入触发不了 SWIMMING | 深水按住前进+疾跑键（`swim --drive`），松键即退出 |
| 封顶水池 | 石壳别封住内部顶层 |
| 满速前进自动疾跑 | 走路基线要 `lab.settle(input_sprint=False)` |
| 游戏模式/飞行残留 | 实验前后断言 `gamemode` / `flying` |
| 岩浆/深水死亡 | `lab.ensure_protection` + 监听 `player_died` |
| 真实鼠标/按键 | `client.input_lock(True)`，结束解锁 |
| 附魔装备穿不上 | `set_item` → `/enchant` → `equip feet` |
| `key_event` 被拒 | `dev.py run --allow allow_input_inject=1` 后重启 |
| `record_start` 游标 | 用 `record_session`（内部 `since=0`） |
| 暂停菜单 | 注入按键无效，桌面点击 Resume |

## 3. 玩家忠实度：热键优先

运动状态一律用**玩家会按的键**复现，不写状态位、不直接改速度：

- **游泳**：深水（≥2–3 格、离底）里 `set_input(forward=1, sprint=True)` 4–8 tick 置
  ADF bit57；`swim --drive` 封装。关键是 sprint 状态：HID 的 W+Ctrl 在浸没状态不会把
  `state.sprinting` 置位（双击 W 也不行），纯按键事件进不去。
- **上浮**：按住空格就升——水/游泳态都是 +0.175 格/tick，岩浆 +0.04；贴到水面会
  “探出又落回”。游泳态平视是真正 vy=0（踩水）；水下抬头按泳姿上升（pitch −30° ≈ +0.121）；
  头露出水面后抬头才会被置零。旧测的“游泳态 +0.2 / 平视也 +0.16~0.2”是 sprint 注入的
  隐藏 jump（已修）。
- **前提检查**：每个实验步骤先 `lab.settle(...)`（连续 N tick 验证 on_ground / 静止 /
  在水中 / 非疾跑），再注入；不要用裸 sleep 当前提。`swim_start_still` / `sprint_no_jump`
  是回归检查。
- **跳跃**：`jump-key` 拍空格（HID Space），与 `jump` 的 jumpFromGround 弧线逐位一致。
- **疾跑**：`set_input(sprint=True)` 只走 MoveInput 标志路径（队列/处理器不再重发 sprint 按钮）。
  旧实现会触发 0.42 跳跃，已修并有 `sprint_no_jump` 回归；用旧 sprint 注入得出的
  “疾跑触发上浮”结论一律作废。
  - 平地疾跑一般无延迟；偶发 1 tick 是 sprint 标志更新与 movement 的相位竞争。
  - 水里（封闭池）：`record` 的 sprint 位（客户端状态）出现后 **2 tick** 才看到 0.9。
    正常起步 `0.01959/0.03528/0.05133`（第 3 tick 生效）；输入晚一拍
    `0.01959/0.03528/0.04782/0.06262`（第 4 tick）。静止时输入已锁存的 harness 变体会提前到
    第 2 tick（`0.03723`），不是正常路径。
  - 2 tick 来自**状态链**（记录位 → 输入/疾跑状态 → ADF 写位），不是“阻力读上一 tick”。
    同一 tick 内 E14 主 pass 的注册序 ActionSprintTrigger 早于 travel，阻力在写位之后读**新位**。
  - 地面 ×1.3 来自移动速度属性：`Actor::setSprinting` 把 `SPRINTING_BOOST`（amount 0.3、
    `MULTIPLY_TOTAL`）压进属性队列，重算 `(0.3+1)×0.1=0.13`；代码里没有 1.3 常数。
    `DEFAULT_SPRINT_SPEED_MULTIPLIER` 是 AI goal 的 JSON 字段，不是这里。
  - **站着按疾跑键不会预置 sprint**（实测 client_sprint=0、ADF bit3=0）；状态从第一次前进
    移动才开始，所以“提前按住”也消不掉水里的起步延迟。
  - 当前世界静止时 `state.input.sprint` 可能就是 true，只按 W 也会直接疾跑；做走/跑对照前
    必须让 `lab.settle(..., input_sprint=False)` 通过。
- **双击空格飞行**：`lab.double_space_fly()`。只有 `InputOrigin::Hardware` 会翻转 `flying`
  （`via=hid` 只起跳），每次按下必须跨至少 1 tick。能力门是 `MayFly`，先 `/togglefly`
  或管道 `togglefly`；切游戏模式会重置，切完要重新开。
- **药水保护**：碰岩浆/深水前 `lab.ensure_protection(client)`（抗火 + 水下呼吸，默认 900 s）；
  `teleport`/`checkpoint_load` 不会清效果，只有 `reset` 会。
- 直接写 ADF/速度只能造出正常输入不可达的状态，结论不可信；状态只用来**读**。

## 4. 记录字段与常用判据

- `RecordEntry`：`tick/x/y/z`（y 是眼睛；`feet_y` 是脚）、`vx/vy/vz`、`yaw/pitch`、`flags`、
  `block_below/feet/head`（方块名 FNV-1a 32）。
- 位：`on_ground/in_air/jump_edge/sprint/sneak/action_sprint/输入 W A S D Jump/
  block_oob/in_water/in_lava`。
- `vx/vy/vz` 来自 `Actor::getVelocity()`，等于**下一 tick 的位移**（液体里 `Δfeet_y ≈ vy`，
  地面也实测比值 1.0）。别把它和 `mem_read` 读到的 `StateVectorComponent+0x18` 混：
  后者是另一个内部字段，地面约是位移的 0.546 倍（位移 0.2159 → 字段 0.1179，
  且站着时 vy 仍留着 −0.0784）。
- `pos.y` 是**脚部**；`RecordEntry` 的 `y` 是眼睛，关系是脚部 + 1.6200101。
- `pitch` 正 = 低头。

## 5. 结论落地

- 原始数据与脚本放 `out/<case>/`（git 忽略），结论文档放 `docs/`；账本放
  `skills/bedrock-re/findings/<version>/cases/<case>/`。
- 每轮用 `driver/landing.py` 落盘：`begin` → `probe` → `claim` → `end`；
  结案跑 `close`（lint + profile 检查）。
- 改了 mod 命令/行为：按 `AGENTS.md` 流程补 `tests/` 单测 + `driver/selftest.py` 检查 +
  `mod/README.md` 的「实测/限制」与基线数字（当前 **221/221** live、**204** 离线）。
- 跨版本复核静态结论用 `tools/ida.py` + 本地证据册；不要复制/重开 `.i64`。

## 6. 收尾检查清单

- [ ] 场地用前回读过；液体是“戳醒”过的
- [ ] 录制窗口覆盖输入段 + 衰减段；`record_start` 之后从 `since=0` dump
- [ ] 拟合带了残差；稳态窗口没有被碰撞/墙污染的单点
- [ ] 每步前 `settle`，走/跑对照 `input_sprint=False`
- [ ] 脚本上锁并在异常路径解锁
- [ ] 新增代码跑过 `unittest`、`ruff check`、`ruff format --check`、`mypy`
- [ ] live 侧补了 selftest 检查；基线数字同步到 `mod/README.md`
- [ ] 本轮结论已 `claim`（confirmed 有静态闭环）并 `end`
