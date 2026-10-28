# 坑与守卫

每条坑必须有归属：`guard: <可执行检查>` 或 `unverified-manual`。
优先处理“会静默给出错结论”的坑；人工项在 `close` 时逐条确认。

## A. 机制/数值（最贵）

| 坑 | 后果 | 守卫 / 归属 |
| --- | --- | --- |
| 把 `StateVectorComponent` 的原始字段当速度 | 地表只有真实位移的约 0.546×，站着还有残值 | guard: `checks/guards/check_statevector.py`（文档示例断言）+ 判速度用相邻 tick 位移 |
| `pos.y` 是脚部，StateVector 是眼睛（+1.6200101） | 高度/入水判定错一位 | guard: `checks/guards/check_eye_feet.py`（回读断言） |
| 把“记录位”当“阻力读的 ADF 位” | 疾跑延迟归因错两拍 | guard: `landing.py claim` 要求写清读/写角色；`pipeline.md` §2 |
| 把 `0.026/0.02` 当最终 ×1.3 | 漏掉 modifier 的 `(0.3+1)` 重算 | guard: `checks/guards/check_sprint_boost.py`（断言 0.3 + MULTIPLY_TOTAL） |
| 把 `jmp [rip+0]` thunk 的绝对地址当签名 | ASLR 后签名失效/指错函数 | guard: `checks/guards/check_roles.py` 拒绝 thunk 地址；解析器要求跟到真实函数 |
| 把观察点顺序当调度顺序 | 跨 pass/跨线程的“先后”不可靠 | guard: `landing.py claim`：顺序类结论必须有注册点/调用图证据 |
| 立即数写入扫不到 xref 就下“不存在” | 漏掉 `mov [mem], imm` 写入 | unverified-manual（读反编译上下文） |
| 大函数 `decompile` 截断 | 结论基于半段代码 | unverified-manual（先落文件再读） |
| 环缓冲带上一轮旧命中 | tick 号跳、归因污染 | unverified-manual（每轮先 drain 到 next） |
| 观察点命中任意写（脏标记/XOR） | 把“字节被写”当“目标位被写” | unverified-manual（按 rip + 位掩码区分） |
| 直接写 ADF/速度造状态 | 正常输入不可达，结论不可信 | guard: `experiments.md` 规定只读状态、用输入驱动 |

## B. 实验/输入

| 坑 | 后果 | 守卫 / 归属 |
| --- | --- | --- |
| 未加载区块执行方块命令 | `fill`/`setblock` 静默失败 | guard: `lab.Builder.build` 回读 + 重试 |
| 区块卸载回滚 | 场地“变回去” | guard: 现建现用 + 实验前 `blocks` 复核 |
| 液体不流动 | 源头旁全空气 | guard: `Builder.poke` 戳醒 |
| 直接传送进液体 | 游泳逻辑不启动、被动下沉 | guard: `experiments.md` 落到液面再按前进 |
| `set_input(sprint=True)` 旧实现隐藏跳跃 | 0.42 跳跃污染游泳/岩浆数据 | guard: `selftest.py::sprint_no_jump` |
| `state.input.sprint` 静止锁存 | 走路基线其实是疾跑 | guard: `lab.settle(..., input_sprint=False)` |
| 方块命令 `success=false` 当失败 | 误判没生效 | guard: 以 `blocks`/verify 回读为准 |
| `TeleportRequest.clearEffects` 共享字段 | reset 后每次 teleport 清空药水 | guard: `selftest.py` 药水效果回归 |
| 暂停菜单注入按键 | Esc/Enter 无效，卡住流程 | unverified-manual（桌面点击 Resume） |
| 真实鼠标/后台转向 | 宏回放漂移、落点不准 | guard: `client.input_lock(True)` + `background_guard` |
| 双击空格零长度按下 | 不翻转飞行 | guard: `lab.double_space_fly()`（跨 tick） |
| 创造飞行/游戏模式残留 | 物理完全改变 | guard: 实验前后断言 `gamemode`/`flying` |
| 岩浆/深水把玩家弄死 | 数据断档 | guard: `lab.ensure_protection` + 监听 `player_died` |
| 录制窗口不够 | 只录到加速段没有衰减段 | guard: `record_session` + 覆盖输入段和衰减段 |
| `record_start` 的 `since` 当 tick | 导错窗口 | guard: 用 `record_session(since=0)` |

## C. 平台/mod

| 坑 | 后果 | 守卫 / 归属 |
| --- | --- | --- |
| 同步管道挂起读阻塞写 | 客户端卡死 | guard: 轮询读线程（`connection.py` 已有） |
| 直接写客户端位置 | 服务端覆盖、橡皮筋 | guard: 传送/朝向走命令通道 |
| 只改朝向组件 | 相机每帧写回 | guard: `/tp @s ~ ~ ~ yaw pitch` |
| 移动输入只写一边 | 预测/校正互相拉扯 | guard: `Input.cpp` 双写（已实现） |
| 请求级 AV 不是 C++ 异常 | 崩溃穿透 tick | guard: `dispatchWithSeh`；新 handler 用 `__try/__except` |
| offsets 改了没生成头文件 | 运行时用旧偏移 | guard: `tests/test_profiles.py`（`gen_profiles.py --check`） |
| 旧 schema profile 当 runtime | 地址/字段读错 | guard: `tests/test_profiles.py::test_legacy_profile_is_not_runtime` |
| `allow_version_mismatch` 常开 | 无校验运行 | unverified-manual（日常配置关闭） |
| 按 runtime id 查客户端 ECS | 拿到服务端 Actor | guard: 用 `state.context` 再 `ecs_context` |
| 观察点只覆盖装过 DR 的线程 | 其他线程命中漏记 | guard: `watch_list.foreign_hits` 检查 |
| hook 槽耗尽 | `no_free_slot` | unverified-manual（先规划/`hook_rm`） |
| VA 钩子传静态地址 | `not_executable` | guard: `tools/ida.py` 不做运行时 hook；按模块基址换算 |
| 热路径分配/JSON/新锁 | tick 卡顿/崩溃 | guard: `AGENTS.md` 硬约束 + code review |
| `xmake` 吃满核心 | 机器卡死 | guard: 一律 `xmake -j4` |

## D. 交付/流程

| 坑 | 后果 | 守卫 / 归属 |
| --- | --- | --- |
| 只有 `[实测]` 就写 `confirmed` | 把假象当机制 | guard: `landing.py claim` 静态门 + `lint_report.py` |
| 负面结论没写 scope/limit | 无法复核，容易假否定 | guard: `probe.scope` 必填（`landing.py probe`） |
| 勘误静默改历史 | 旧结论被洗掉 | guard: `erratum_of` + `checks/lint_report.py` |
| 仓库写个人绝对路径 | 公开仓库泄露 | guard: `checks/lint_public.py` |
| 旧账不查就重做 | 浪费轮次、结论冲突 | guard: `landing.py begin` 查 `findings`/`docs`/`git log` |
| 一轮没落盘就开下一轮 | 压缩/崩溃后丢事实 | guard: `begin` 的 pending 门（`abandon` 可放行） |

## E. 停止条件

- 三种静态策略都定不到：写 `unresolved`，列已排除假设，停下来问。
- 静态与动态冲突：以静态为准，记录冲突现象与复现条件；不要为了让数字好看改结论。
- 需要猜地址才能继续：停；宁可让下游报“未知”，也不能给错地址。
