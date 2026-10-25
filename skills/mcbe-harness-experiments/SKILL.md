---
name: mcbe-harness-experiments
description: 用 LeviPilot harness（probe mod + 命名管道 + Python driver）在真实 Minecraft Bedrock 里做逐 tick 实验 —— 建场回读验证、液体/方块怪癖、二进制录制环、输入注入、离线拟合分析。凡是需要从运行中的基岩版测出数值（移动/物理/方块机制）、填充或传送"没生效"、或者要给仓库加一个新的闭环实验时使用。
---

# MCBE harness 实验手册（LeviPilot）

目标：把"游戏里到底怎么动"变成**可复现的逐 tick 数据 + 拟合系数**，而不是靠手感描述。

## 0. 开工前

1. **确认版本与目录**：游戏在 `%APPDATA%\levilauncher.exe\versions\<版本>\`；
   本仓库的 mod 按 `docs/offsets/<版本>.json` 匹配（`allow_version_mismatch` 只用于移植）。
2. **起游戏（带能力开关）**：
   ```bash
   py -3.11 driver/dev.py run --world "超平坦测试" \
     --allow allow_input_inject=1 --allow allow_selftest_stress=1
   ```
   `--allow` 写的是 `harness.json` **顶层**开关（mod 启动时读一次，改完必须重启游戏）；
   `--options` 写的是**游戏选项**，两者不要混。需要 hook/写内存时再加
   `--allow allow_hook_va=1` / `--allow allow_write=1`。
3. **sanity**：`client.wait_ticks(20)` 能推进说明世界在 tick（失焦暂停由
   `pause_menu_on_focus_lost` 决定）。**每次重启后先确认进程是新的** —— 上一轮中断的
   selftest/实验可能留下已装 hook（`hook_duplicate`）或残留方块。
4. 常用常量：`超平坦测试` 自然地表 **y=-60**（草方块 Y=-61，下面两层泥土+基岩）；
   出生点平台在 -49，**不要拿出生点高度当地面**。

## 1. 实验闭环

```python
import sys; sys.path.insert(0, "driver")
from harness.client import ModClient
from harness.lab import Builder, record_session, move_and_wait, fit_recursion, recursion, steady

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

- **`set_input`/`move` 立即返回**，输入在后台跑 N tick；录制窗口要等够：
  `move_and_wait(client, ticks=70, settle=2)`，并把衰减段一起录（等 `70+55`）。
- **分段靠记录字段**：`on_ground` / `sprint` / 输入位 / `in_water` / `in_lava` /
  `block_feet` 等方块名哈希（`lab.fnv1a("minecraft:water")` == 记录里的哈希）。
- 拟合优先看**递推**（`a, b`）再看稳态：`a` 是阻力、`b/(1-a)` 是平衡值，
  比"手挑稳态窗口"抗污染。三个介质统一形如 `v' = d*(v+g)`（先重力后阻力）。

## 2. 硬坑速查（都是实测踩出来的）

| 坑 | 现象 | 正确处理 |
|---|---|---|
| 未加载区块的方块命令 | `fill`/`setblock` 静默 `success=false` | 先 `teleport` 到场地附近再建；用 `Builder.build` 回读+重试 |
| 区块卸载回滚 | 已建的方块"变回去" | **现建现用**；实验前 `blocks` 复核；跨分钟的实验别指望场地还在 |
| `/setblock` 液体不流动 | 源头旁边全是空气 | `Builder.poke(x,y,z)` 放/拆一个邻块"戳醒"液体 |
| 直接传送进液体 | 俯仰驱动/游泳逻辑不起作用，按被动递推缓沉 | 要游泳态请"落到液面"（从上方落下）+ 按住移动键 |
| 合成输入触发不了 SWIMMING 意图 | 水下俯仰完全无效 | 用**真实按键**：深水里按住前进+疾跑键（`swim --drive`）；松键即退出 |
| 放置水的池子封顶 | 玩家"浮"在石盖上 | 石壳别封住内部顶层；`fill` 到 Y=-40 会把开口也填上 |
| 满速前进自动疾跑 | 干地"走路"基线其实是疾跑 | 检查 `record` 行的 `sprint` 位，走路基线要排除 |
| 游戏模式/飞行状态被改动 | 崩溃的 selftest 可能留下创造模式；创造飞行会完全改变物理 | 实验前后读 `state.gamemode`（0 生存/1 创造/2 冒险/6 旁观）与 `flying` 并断言；需要时先 `gamemode survival @s` |
| 岩浆/深水实验把玩家弄死 | 中途死亡、数据断档、掉落物消失 | 先 `lab.ensure_protection(client)`（抗火 + 水下呼吸）；盯 `player_died` 事件 |
| 双击空格没反应 | 连按空格只跳不飞 | `MayFly` 没开（`/togglefly` 或管道 `togglefly`）；`via=hid` 只起跳；每次按下要跨 ≥1 tick |
| 真人在机器旁动鼠标/按键 | 结果漂移、按键计数多出来（如 `key_event_count` 多 5 次）、落点不准 | 脚本开头 `client.input_lock(True)` 锁真实输入（dev.py `--lock-input`；selftest 已自动锁），结束 `input_lock(False)` |
| 附魔装备 | `replaceitem` 不支持组件、`/enchant` 只作用手持、右键不穿甲 | `set_item` 手持 → `/enchant` → **`equip feet`**（会连附魔一起搬到护甲槽） |
| `key_event` 被拒 | `input_inject_disabled` | `dev.py run --allow allow_input_inject=1` 后重启 |
| `record_start` 游标 | `since` 是环位置不是 tick | 每个 session 用 `record_session`（内部 `since=0`） |
| 起跳 | 只调输入标志/队列/handler 会 3 tick 橡皮筋；`setJumpPendingScale` 单独不起跳 | 用 `jump` / `set --jump`（内部 `jumpFromGround`）；空中发 `jump` 会丢弃，等落地用 `set --jump --ticks N` |
| 暂停菜单 | 注入按键无效 | 退出暂停只能桌面点击（见 skill `mcbe-desktop-control`） |

## 3.5 玩家忠实度：热键优先（重要）

运动状态一律用**玩家会按的键**复现，不写状态位、不直接改速度：

- **游泳**：深水（≥2–3 格、离底）里 `set_input(forward=1, sprint=True)` 4–8 tick 置 ADF bit57；
  `swim --drive` 封装（内部就是这条输入路径，走 `_toggleClientSprint`）。关键是**sprint 状态**：
  HID 的 W+Ctrl 在浸没状态不会把 `state.sprinting` 置位（双击 W 也不行），纯按键事件进不去。
- **上浮**：按住空格就升——水 +0.175 格/tick、岩浆 +0.04、游泳态 +0.2；**抬头不会上浮**
  （游泳门把 vy 压成 0），贴到水面会"探出又落回"地上下浮。
- **跳跃**：`jump-key` 拍空格（HID Space），与 `jump` 的 jumpFromGround 弧线 selftest 逐位一致。
- **疾跑**：`swim --drive` 与宏回放都走 HID 疾跑键；`set_input(sprint=True)` 调的是客户端
  sprint 切换函数（等价动作，但不是按键事件）。
- **双击空格飞行**：`lab.double_space_fly()`。两条实测约束：只有 `InputOrigin::Hardware`
  会翻转 `flying`（`via=hid` 只起跳、不切换），每次按下必须跨至少 1 tick（同 tick down+up 被丢）。
  能力门是 `MayFly`，先 `/togglefly`（本地世界的聊天框）或管道 `togglefly` 打开；旁观模式无效，
  **切游戏模式会把能力重置回该模式默认值**（生存/冒险 false、创造 true），切完要重新开。
- **药水保护**：碰岩浆/深水的实验前 `lab.ensure_protection(client)`（抗火 id 12 + 水下呼吸 id 13，
  默认 900 s），否则会把玩家烧死/淹死；它会往 `state.effects` 写两条，跑"空效果"断言的
  selftest 前后记得清理。`teleport`/`checkpoint_load` **不会**清效果，只有 `reset` 会
  （2026-10-25 修过"一次 reset 之后每个 teleport 都清效果"的 bug，见 AGENTS 已知坑）。
- 直接写 ADF/速度只能造出正常输入不可达的状态，结论不可信；状态只用来**读**（如 bit57 判定）。

## 3. 记录字段与常用判据

- `RecordEntry`：`tick/x/y/z`（y 是眼睛；`feet_y` 是脚）、`vx/vy/vz`、
  `yaw/pitch`、`flags`、`block_below/feet/head`（方块名 FNV-1a 32）。
- 位：`on_ground/in_air/jump_edge/sprint/sneak/action_sprint/输入 W A S D Jump/
  block_oob/in_water/in_lava`。
- `vx/vy/vz` 来自 `Actor::getVelocity()`，等于**下一 tick 的位移**（液体里 `Δfeet_y ≈ vy`，
  地面也实测比值 1.0）。别把它和 `mem_read` 读到的 `StateVectorComponent+0x18` 混：后者是另一个
  内部字段，地面约是位移的 0.546 倍（位移 0.2159 → 字段 0.1179，且站着时 vy 仍留着 −0.0784）。
- `pitch` 正 = 低头。

## 4. 结论落地

- 原始数据与脚本放 `out/<case>/`（git 忽略），结论文档放 `docs/`；
  范例：`docs/fluid-movement.md`（三介质递推 + 流推 + 泳姿）。
- 改了 mod 命令/行为：按 AGENTS 流程补 `tests/` 单测 + `driver/selftest.py` 检查 +
  `mod/README.md` 的「实测/限制」与基线数字（当前 **215/215** live、**193** 离线）。
- 跨版本复核静态结论用
  `reverse-skill/.../bedrock-client-re/scripts/pe_code_diff.py OLD.exe NEW.exe --values ...`。

## 5. 收尾检查清单

- [ ] 场地用前回读过；液体是"戳醒"过的
- [ ] 录制窗口覆盖输入段 + 衰减段；`record_start` 之后从 `since=0` dump
- [ ] 拟合带了残差；稳态窗口没有被碰撞/墙污染的单点
- [ ] 新增代码跑过 `unittest`、`ruff check`、`ruff format --check`、`mypy`
- [ ] live 侧补了 selftest 检查；基线数字同步到 `mod/README.md`
