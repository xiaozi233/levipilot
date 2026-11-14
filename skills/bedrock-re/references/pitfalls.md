# 坑与守卫

每条坑必须有归属：`guard: <可执行检查>` 或 `unverified-manual`。
优先处理“会静默给出错结论”的坑；人工项在 `close` 时逐条确认。

## A. 机制/数值（最贵）

| 坑 | 后果 | 守卫 / 归属 |
| --- | --- | --- |
| 把 `StateVectorComponent` 的原始字段当速度 | 地表只有真实位移的约 0.546×，站着还有残值 | guard: `checks/guards/check_statevector.py`（文档示例断言）+ 判速度用相邻 tick 位移 |
| 传送 `pos.y` 是脚部，StateVector Y 是位置基准（本轮站蹲均约 +1.6200101），不是当前头部/镜头高度 | 高度/入水判定错一位，蹲姿时混淆位置与头部 | guard: `checks/guards/check_eye_feet.py`（文档标记）；原地站蹲实测 `liquid-blip/r20-066` |
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
| `set_input(sprint=True)` 旧实现隐藏跳跃（legacy 直接标志） | 0.42 跳跃污染游泳/岩浆数据 | guard: `selftest.py::sprint_no_jump`；默认路径已改为真实 HID |
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
| claim 的 `probe_ref` 指向还没入账的探针 | `lint_report` 永久报 `unknown probe_ref`（探针 id 必须出现在账本**更早**位置），而账本只追加不改，只能再补一条勘误 | guard: 先 `landing.py probe` 入账、后 `claim` 引用；确需补链接就新写一条带正确 `probe_ref` 的记录（本轮 r21-071→r21-074 就是这么补的） |

## E. 停止条件

- 三种静态策略都定不到：写 `unresolved`，列已排除假设，停下来问。
- 静态与动态冲突：以静态为准，记录冲突现象与复现条件；不要为了让数字好看改结论。
- 需要猜地址才能继续：停；宁可让下游报“未知”，也不能给错地址。

## F. 工作区/工具链（2026-10-28）

| 坑 | 后果 | 守卫 / 归属 |
| --- | --- | --- |
| `dev.py run` 没带 `--build` 却装了旧 `probe.dll` | 新字段/输入路径缺失，selftest 在 `key_event_*` / `state` 上失败 | guard: `dev.py` 比较源码与 DLL mtime，自动重建；`--no-build` 可跳过 |
| 冷世界进入后立刻跑 selftest | 第一批 `teleport`/`scan`/`jump` 因玩家还在下落/区块未热而失败 | guard: `dev.py` 进入后等 `on_ground` 且三轴速度≈0 连续 5 tick；冷世界失败先重跑一遍 |
| 直接启动 exe 与 LeviLauncher 隔离的数据根不同 | 世界列表里找不到测试世界 | guard: `dev.py` 找不到世界时扫描版本隔离目录并给出复制/启动提示 |
| IDA MCP 默认 60 s per-tool 超时 | 大库冷开被杀（本机重建后 5.9 GB，要 2 分钟），worker 回收 | guard: `tools/ida.py serve --tool-timeout 1800`（代传 `IDA_MCP_TOOL_TIMEOUT_SEC`） |
| **对正在打开的 IDA/idalib 进程 `taskkill /F`** | `.i64` 被写坏，之后无论 idalib 还是 `idat -A` 都报 `internal error 1228`，只能重建 | guard: 先 `idb_close(save=true)`；坏库改名 `.i64.corrupt`，用 `idat -A <exe>` 重建（本机约 1 小时） |
| 在整库跑 `search_text` | 单 worker 队列被 listing 扫描堵死，后续全部超时 | guard: 用 `find_bytes`/`insn_query` 限 `segment/start/end/func`；堵了先 `idb_close` 再重开，别杀进程 |
| **把开库锁冲突当成 `.i64` 损坏** | 2~5 s 的 `Failed to open database` 只是数据库被另一个 worker 占着（成功开库要 3~12 分钟）；据此删库重建白烧 1 小时 | guard: 判库坏之前先 `py -3.11 driver/ida.py status` 看有没有活会话；`open_session`/`connect` 先用 `live_session` adopt 同库活会话，不重复 `idb_open` |
| supervisor 启动时 `Failed to open initial binary`（WinError 10054） | 误判库坏；实际是启动那次开库失败后 supervisor 自己重试，重试的 worker 照常开库 | guard: 看 `out/ida-supervisor.log` 里随后有没有 `idb_open ... success`；用 `status` 确认 |
| 管道高频批量查询约 600 次后“连不上” | 只能重启游戏；跨线程 `CloseHandle` 会与阻塞 I/O 竞争 | guard: 分批 + 结束前 `close()`；**不做**看门狗，等待稳定复现 |

## G. 流体取点与窗口焦点（2026-11-04）

| 坑 | 后果 | 守卫 / 归属 |
| --- | --- | --- |
| 用 `/testforblock x y z water ["liquid_depth"=N]` 判液位 | 对 `liquid_depth` 的匹配不可靠：不同液位的格子都报 `=0`，据此得出“浅水无物理”的错误结论 | 读方块状态字：`blocks` 取 `addr`，`probe --addr <addr+288> --kind raw --len 2`，低字节即 `liquid_depth` |
| `dev.py` 报 `could not focus the game window` | 世界 tick 可能暂停：`setblock`/`teleport`/方块回读都正常，但水不流、沙不落，容易误判“基岩不流动体” | guard: 实验前跑 `py -3.11 driver/dev.py focus`（已校验前台窗口，失败会返回非零） |
| 只按 1 tick 的跳跃 + 轮询 `vy` 取峰值 | 采样错过顶点，同一格时而 0.42 时而 0.09，结论飘 | 按 5 tick 并取 1 s 内 `feet_y` 最大值：干地顶点约 1.25、水中约 0.6 |
| 按视觉液深推液体判定 | 基岩只看格子里有没有流体，液位 0~7 结果一致 | 先读 `+288` 状态字定点，再用跳跃/加速对质；见 `docs/fluid-movement.md` §5 |
| 竖直实验前没查 `state.flying` | 飞行能力会随存档保留：跳跃=升空、潜行=下降，竖直数据全假，且不会自行落地 | 先确认 `flying=false`；必要时 `/gamemode creative` → `/gamemode survival` 重置能力 |
| 贴墙类实验的传送点离墙不足半个身位 | 解挤把玩家推开并留下横向速度，之后再也贴不到墙，水平碰撞门不成立，整组数据作废（实测 z=300.28 废、300.32 可行） | 传送点与墙面至少留 0.3；跑完先回读 `vz≈0` 且 `z` 稳定贴墙 |

## H. 静态取数与常量归属（2026-11-10）

| 坑 | 后果 | 守卫 / 归属 |
| --- | --- | --- |
| **把 IDA 反编译里的 float 常量当准** | 常量落在 16 字节对齐块中部时，反编译可能打印块首值：`sub_1401EEFD0` 的 `movss xmm1,[rip+..]`（目标 0x14E6AE444=1.62001f）被反编译成 `sub_14253CFD0(a1+8, 20.0)`（20.0 是块首 0x14E6AE430 附近的 0x14E6AE434） | 反编译数值一律用 `disasm` 的 `refs` 或 capstone rip 相对解算核对；同类函数 `sub_1401E58B0` 的反编译又是对的，不能凭一次巧合信任 |
| **块首假象会渗进账本 claim** | 同一个坑连栽两次：`@vanilla_offset_system_worker`(0x1428A4AB0) 的 K 被反编译记成 `{0.22222199, 20.0, 20.0−alpha}`——`0.22222199` 是 `0.4f`(0x14E602290) 所在块的首元素 0x14E602280（全库唯一、无任何 movss 引用），`20.0` 来自块内 0x14E6AE434。真实值是 `0.4f/0.2f/1.62001f`（r13-029 勘误 r06-018） | 落 claim 前对每个浮点常量做一次「capstone rip 解算 + 直接读文件字节」双向核对；块首值还有个特征——扫全库 movss 引用时为 0 次 |
| **只信反编译、不信 disasm 的 refs** | 同一 16 字节块内的相邻常量互相串：0x1401E5BAA 实际取 0x14E6AE454（0.2f），指令文本被渲染成块基标签 `dword_14E6AE444`，反编译随之打印 1.62001，一度多算一个“玩家用 1.62001”的调用点 | 反编译文本 / disasm 文本 / disasm refs 三处不一致时以 **refs 的绝对地址**为准，再用字节扫描复核该地址的值 |
| IDA MCP `decompile` 返回 `null` 或只给变量声明 | 大函数（>2 KB）被服务端截断，误以为“没这个函数” | 用 `disasm`（`max_instructions` ≤ 500 分页，≥1000 会被压成 10 行）或 capstone；函数边界用 `.pdata` 的 RUNTIME_FUNCTION |
| **把 `call [全局指针]` 当成普通间接调用** | 本 exe 把虚调用藏进 `0x14F0FBAA8`：该指针指向 `0x14E585660` 的 5 字节 `jmp rax` 桩，真正目标由调用方先装进 `rax`（如 `mov rax,[rsi]; mov rax,[rax+0x28]; call [0x14F0FBAA8]` 就是 `this->vtable[0x28]`）。不跟这一跳就会把虚函数调用当成“某个固定 helper”，进而读错参数表 | 见到 `call qword ptr [rip+…]` 先解出指针值再解一次；`0x14E585660` 这种 5 字节 `jmp rax` 桩是全库通用的虚调用转发器，认出来就能直接按 MS ABI 读 rcx/rdx/r8/r9 |
| 组件 id 只靠“名字像”对应 | 同名/近名组件（`ShouldUpdateBoundingBoxRequestComponent` vs `AABBRelativeSizeUpdateComponent`）串台，字段偏移全错 | 每个 id 只有一处 `mov [reg+4], imm32`（TLS 缓存初始化），反编译该函数即得 `{id, 名字, 长度}`；脚本 `out/comp_name.py` |
| 常量只在 `.rdata` 就断言“没人用” | 漏掉全部 `movss xmm,[rip+disp]` 消费者：1.62001f 无 `lea`/指针引用，但有 7 处 movss | 扫 `F3 0F 10/11 <modrm rip>` 两类，别只扫 `lea`；表基址被引用不等于表内每个格子被引用 |
| 用 `1.8×0.9+1e-5` 解释 1.62001 | 该式 float32 结果低 1 ULP（1.6200098991394043），与实测常量 1.6200100183486938 不同 | 先做全 exe 字节扫描（`7d 5c cf 3f` 唯一命中即字面量），再用 float32 加法模型复现实测尾差 |
| 把实测差 `1.6200103759765625` 当成“第二个容差” | 把 float32 加法量化误判成引擎阈值 | `float32(feet)+float32(1.62001)` 再双精度相减即得该值；114/114 客户端样本 bit-exact |
| **LL 解析出的符号地址是只读映射区里的混淆跳板，直接 `mem_read` 会落到 FREE 页** | LeviLamina.dll 的符号槽（`base+0x425698` 一类）指向 `MEM_MAPPED` 只读区的一段 `jmp [rip]` 跳板；跳板里的目标 qword 少读一位就会探到 FREE 地址，误判“地址无效” | 目标地址按 **qword 完整 8 字节**解（例：`00 a0 0f e9 55 02 00 00` = `0x255E90FA000`，不是 `0x255E90FA0`）；运行期是否可达用 harness `watch_set(mode='x')` 验证（装得上=可执行） |
| **以为 LL 用签名/PDB 解析游戏符号** | 全 DLL 无签名串、无烘焙游戏地址、游戏 exe 只导出 7 个构建戳；实际是内嵌符号表 + 运行期生成的跳板农场（`mov rax,<off>; mov rbx,base; lea; jmp`） | 定位真身：槽 → 跳板 → `48 c7 c0 <off32>` 偏移链 → 链尾 `xchg [rsp],rax; ret` 的计算式跳转（xor/rol 常量混淆）→ 得到游戏 exe 运行期地址 → 减模块基址得 RVA → 回 exe 静态字节核对。脚本 `out/trace_chain.py` |
| **拿不到地址就放弃静态定位** | IDA 冷开失败（可用内存 < worker 需求）时容易退回纯动态 | 先试 harness：`mem_read` 任意地址 + `watch_set(mode='x', depth=N, snapshots=[rcx/rdx/r8])` 可在运行期直接确认签名与调用方，再回静态 exe 反汇编 |
| **`.i64` 被存成「unpacked」状态后 idalib 一律拒绝打开** | worker 起得来、`idb_open` 一调就 `ConnectionResetError 10054` 且 worker 直接退出；worker 输出里是 `FATAL ERROR: internal error 1228` + "IDA has found an unpacked version of database on the disk. It appears IDA did not close properly" | 先看版本目录有没有 `.id0/.id1/.nam/.til`（本库解包后约 6.7 GB）：**移走备份后仍报 1228 就说明 `.i64` 本身就是未打包状态**，只能进 IDA GUI 重新存盘（packed）或用 `idat -A <exe>` 重建（本机约 1 小时，且会丢掉库里的人工标注） |
| **supervisor 启动时带 `input_path` 会因首开失败直接退出** | `idalib_supervisor.main()` 里 `open_session` 失败就 `raise SystemExit("Failed to open initial binary: ...")`，**不重试**；日志里连续多条 `Spawning idalib worker` 其实是多次 `driver/ida.py start`，不是单次重试 | 需要稳定起后端时先不带路径起 supervisor（`py -3.11 -m ida_pro_mcp.idalib_supervisor --port 8745`），再用 `driver/ida.py open`（它自己轮询 `idb_list`，能容忍首开掉线）；worker 的 stdout/stderr 被 supervisor 丢到 DEVNULL，要抓崩因得自己前台起 `idalib_server` 再手工 `idb_open` |

## I. 字段语义与账本摘要（2026-11-11）

| 坑 | 后果 | 守卫 / 归属 |
| --- | --- | --- |
| **把 `StateVectorComponent+0x18/+0x1C/+0x20` 当 `VelX/VelY/VelZ`** | 这三个字段是 SDK 的 `mPosDelta`（等待后续运动应用阶段使用的位移量，不保证是下一个世界 tick），不是速度，也不是 `getVelocity()` 的返回值。直接当速度会把“待执行位移”和“已完成位移”混成一个量，`0.6+mPosDelta.y` 与 `0.6+录制 vy` 的差就被误读成采样误差 | 三个量分开写：`mPosDelta`（+0x18，blip/水跳写它）、`mPos−mPosPrev`（已完成位移 = 录制 `vy`）、录制流本身。`docs/offsets` 的 `Vel*` 键名只是兼容别名 |
| **只看 `floor()` 的一个轴就推“阈值”** | 预过滤对 x/y/z 都按 `[floor(min), ceil(max))` 整格枚举；盒底恰在液格顶面时不枚举液格——`gap=0` 通过是枚举规则的必然，不是拟合出的“固定 0.7 格余量” | 用盒子三轴同时算，并写明“整格枚举 + 半开区间”；实测边界要给出最近阴性/阳性两条原始 gap |
| **写点只按“同线程下一个入口之前”归属** | 单实体的实验里没问题，多实体/乘客场景会把别的实体的 0.3 记到当前入口头上 | 写点观察能拿到组件地址时就把实体身份一起记；账本里写明“本场景只有玩家一个候选实体” |
| **自动生成的 `resume.md` 把已撤回命题留在 `Open/Confirmed`** | 追加一条勘误不足以消除现行摘要的自相矛盾（r23-077 的“深度禁区”被 r24-082 撤回后仍出现在 Open） | `generate_resume` 现在把 `erratum_of` 的目标从 Open 移除、在 Confirmed 加标注、并在 Retracted 打印“原命题”；改生成器时同步 `tests/test_landing.py` |
| **把过滤后的空页当作没命中** | 原始协议默认 `since=0`；Python 客户端与 Harness 默认使用安装后缓存的 head（无缓存仍为 0）。历史 facade 曾错误地强传 0。全局分页后才过滤 ID，旧行/其他 ID 会占满一页 | 要覆盖安装最初命中，安装前取 head 并显式传 `since`；无论过滤后是否为空，都用 `next` 翻页，保留 `lost`。`watch_list` 的计数与 drain 缺采要分开看 |
| **拿 `ecs_context` 失败解释组件生命周期** | r26 陆地与水中查询失败，r24 入口快照成功，不能由不同窗口推出组件只在 tick 内存在或位于另一存储 | 原因 unresolved；组件 id、查询路径、registry 与生命周期均需对质。若用入口地址，必须在同窗口重新核对实体/侧别/组件，不能复用旧会话指针 |
| **把 `+0x18,size=8` 标作 x/z** | 连续 8 字节实际是 x/y，与 `+0x1C,size=4` 重叠；没有观察 z，不能给出完整三轴写者集合（r26-097） | 逐轴用 `+0x18/+0x1C/+0x20` 各 4 字节；旧命中数保留并关联部分勘误，不伪造更正后的实测 |
| **探针用 `watch_clear(all=True)` 收尾** | 会移除其他会话的观察点；中途抛错还可能泄漏自己已安装的资源 | 记录并仅清理自己的 ID，逐个尝试；保留首错和清理失败。新 `driver.delta_watch` 被动采集，不注入输入或移动玩家 |
