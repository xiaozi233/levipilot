# LeviPilot 剩余工程任务交接提示词（2026-11-02）

下面正文可完整交给一个全新的 agent。所有相对路径均以本仓库根目录为基准。
本提示词交接工程收尾；历史调查提示词只作背景资料。新 agent **可以**在工程需要时
开展有边界的新逆向（工具链已在本机装好，见「工作区准备」与「逆向工作约定」）。

## 目标与执行边界

你接手的是 LeviPilot 工作区：Minecraft Bedrock 1.26.51.01 / LeviLamina 26.51.7，
C++ 客户端 mod、JSONL 命名管道协议及 Python 客户端。

请完成现有审阅清单的所有剩余工程工作，并给出代码、回归、现场验收、文档与本地提交。
不要只输出建议或计划；先核对现状，再按依赖顺序逐批实施并验收。
内部结构和有问题的内部行为可以调整，但必须遵守当前 AGENTS.md 的协议兼容、
线程归属、热路径、版本数据与强能力约束，不能为了抽象而整体重写。

- 不使用子 agent，不自动推送，不擅自启动旧聊天中暂停的 goal 或创建替代 goal。
  本次以用户在新聊天中发出的任务为执行依据。
- **允许新增逆向**：本工作区自带完整的逆向工具链（见「工作区准备」与「逆向工作约定」），
  当工程子项必须有新的游戏侧事实才能安全推进时，可以自行定位函数、结构布局、
  调用时序，并把结论按证据分级落盘。但红线不变：不修改游戏网络栈、不绕过或吞掉
  坏包校验、不猜地址/布局、不用补丁去掩盖游戏侧缺陷。
- 逆向结论只允许来自静态（`[代码]`/`[常量]`）；动态只用于定位、消歧与证伪。
  地址、偏移、角色一律先落 `docs/roles/<version>.json` / `docs/offsets/<version>.json`
  再使用，运行期地址按 `module_base + (role_va - image_base)` 换算。
- 如果某个子项即使新增逆向也无法得出可靠结论（例如需要外部资料或不可复现的现场），
  明确记录所缺资料、依赖与阻塞原因，保持该项未完成，继续不依赖它的工程工作。
- 可以按已经授权的开发流程保存退出、备份测试世界和配置、安装测试 DLL、重启并在
  “超平坦测试”验收。仍须遵守保存门禁与 recover 的强停边界。
- 做完且适用检查通过后，按独立工程主题本地提交。保留失败证据，提交说明如实写验收
  范围；未通过的候选不要作为完成项提交，更不要提交成“全绿”。不要自动 push。

## 工作区准备（新 agent 第一步，先验证再动手）

### 环境
- Python 一律 `py -3.11`（默认解释器可能缺依赖）。
- 构建：`mod/probe` 下 `xmake -j4 -y`；工具链见 `docs/mod-build-environment.md`
  （`XMAKE_GLOBALDIR=D:\Tools\xmake-home`、`XMAKE_PKG_INSTALLDIR=D:\Tools\xmake-packages`、
  `PATH` 追加 `D:\Tools\LLVM22\bin;D:\Tools\xmake`）。`py -3.11 driver/dev.py run --build`
  会自动带这些环境；手工构建必须自己设。
- 游戏与配置：`py -3.11 driver/dev.py run --world "超平坦测试"`；
  `harness.json` 在 `%APPDATA%\levilauncher.exe\versions\1.26.51.01\mods\probe\`。
- 运行产物、证据、探针输出都放 `out/`（已 gitignore）。

### skill（已装好，先验证，缺了再重建）
- **真身**：`skills/bedrock-re/`，随仓库提交，是唯一权威副本。
- 被发现的两处联接：`~/.agents/skills/bedrock-re`、`.zcode/skills/bedrock-re`
  （Windows 目录联接，指向真身）。校验：`ls skills/bedrock-re/SKILL.md` 且
  `ls -la ~/.agents/skills | grep bedrock`；联接丢了用 `mklink /J` 重建。
- 非 MCBE 的逆向/安全任务路由到本机 reverse-skill pack：
  `%USERPROFILE%\reverse-skill`（`skills/` 是包根，路由文件 `skills/SKILL.md`），
  同样靠 `~/.agents/skills` 的联接被发现；工具索引脚本
  `scripts/master-route.ps1`、`scripts/refresh-tool-index.ps1`。该包的工作产物写在
  它自己的 `work/` 下（本仓库 `.gitignore` 已忽略 `work/`）。
  包丢失时：`git clone https://github.com/zhaoxuya520/reverse-skill` 到
  `%USERPROFILE%\reverse-skill` 再建联接。

### IDA（静态主力）
- 客户端：`py -3.11 driver/ida.py {status|start|open|decompile|disasm|xrefs|callers|call}`；
  端点自动发现（supervisor 日志 / 已知端口），数据库为
  `%APPDATA%\levilauncher.exe\versions\1.26.51.01\Minecraft.Windows.exe.i64`。
- 首次打开大库要数分钟；`IDA_MCP_TOOL_TIMEOUT_SEC=900` 必须带（客户端会代传）。
- **绝不要对正在打开的 IDA/idalib 进程 `taskkill /F`**：会把 `.i64` 写坏，之后只能
  从 exe 重建（本机约一小时）。停后端用 `idb_close(save=true)`；坏库先留
  `.i64.corrupt` 再重建。地址→函数可用 `py -3.11 driver/pe_map.py <va>`（离线，不需要 IDA）。

### x64dbg（动态对质）
- 路径：`D:\x64dbg\release\x64\x64dbg.exe`。
- 插件：`SetsunaYukiOvO/x64dbg-mcp` v1.0.12 →
  `D:\x64dbg\release\x64\plugins\x64dbg_mcp.dp64`；配置
  `plugins/x64dbg-mcp/config.json` 的 `auto_start_mcp_on_plugin_load=true`。
- 端点：`http://127.0.0.1:3000/mcp`（`POST`，默认无鉴权）；客户端 `out/x64mcp.py`。
- **两个已实测限制**：
  1. attach 到 `Minecraft.Windows.exe` 会被系统拒绝（`DebugActiveProcess` 返回 87）；
     改用 `debug_init`（LoadBinary）让调试器自己启动目标；
  2. mod 的 `watch_set` 占用 DR0–DR3，与调试器硬件断点冲突：接了调试器就别 arm `watch_set`。
- 旧插件 `x64dbg-MCP-Server.dp64`（duty1g 版，9094 + Bearer）已改名 `.disabled`。
- 用法细节见 `skills/bedrock-re/references/x64dbg.md`。

### 管道与探针纪律（踩过两次）
- 命名管道同一时刻只服务一个客户端；探针脚本必须 `try/finally: client.close()`。
  不带 `close()` 丢弃客户端会让 mod 侧管道卡到 `Errno 22`：游戏进程还活着但不再服务，
  只能重启游戏。所有临时脚本按此写法。

## 逆向工作约定（本仓库允许且需要时执行）

1. **证据分级**：`[代码]`（静态反汇编/常量）、`[实测]`（现场或探针）、`[推断]`、
   `unresolved`。旁证不等于定案；未闭合项单列，不写进结论段。
2. **地址与布局的落盘**：
   - 角色（函数入口、vtable 槽、全局）写 `docs/roles/<version>.json`；`unresolved` 不填地址。
   - 运行时偏移（结构字段、锚点）写 `docs/offsets/<version>.json` 且带 `"runtime": true`；
     改完跑 `py -3.11 driver/gen_profiles.py`（`GeneratedProfiles.h` 是提交物）。
   - `.cpp` 里禁止出现版本常量；没有 profile 时降级而不是崩溃。
3. **运行期地址换算**：`runtime = module_base + (role_va - image_base)`。本机 exe 的
   ImageBase 被 PeEditor 改写并 ASLR，按文件头换算会 `not_mapped`。
4. **hook ABI**：通用 VA detour 只保留 `rcx/rdx/r8/r9` 并转发返回值；float/xmm 参数、
   向量返回、多于 4 个参数或隐藏返回指针，必须写 typed wrapper，否则 origin 收错参数。
5. **热路径纪律**：detour 内禁止 `std::string` / `nlohmann::json` / 动态分配 / 新增 mutex /
   `readMemory`（会在持锁帧里死锁，实测把管道挂死过）；只写预分配定长结构，格式化放冷侧。
6. **结论要落盘**：新发现写 `docs/notes/`（地址 + 原始字节 + 证据分级），角色同步进 roles，
   影响使用契约的同步进 `mod/README.md` / `docs/mod-internals.md`。
7. **优先复用现成诊断设施**：`hook_install`（VA/符号，支持 `mem_arg` 快照）、
   `send_probe`（发包路径异常帧连栈落 `suspect-<pid>.jsonl`）、`violation_watch`（含首错冻结）、
   `watch_set`（DR 观察点）、`packet_capture`、`inbound_drain`、`diagnose`。
   新写探针只在现有设施覆盖不到时进行，并遵守上面 4/5 两条。
8. **已闭合结论可直接作为工程输入**，不要重新调查；只有出现与结论矛盾的新证据时才重开，
   并在证据册里写清“为什么重开”。

## 先读什么、如何识别现状

先读仓库的 AGENTS.md、README.md、docs/architecture.md，然后按需要阅读：

1. docs/code-quality-review.md：问题、严重程度和具体位置。
2. docs/reliability-repairs.md：原始 16 项清单及逐批验收。
3. mod/README.md：使用契约、实测与限制。
4. docs/mod-internals.md：实现和逐项数字。
5. docs/refactoring.md、docs/mod-build-environment.md。
6. docs/notes/badpacket-setactordata-20261031.md 的最新结论与撤回标注：
   把 §§36–49 的已证实结论作为**工程设计输入**直接使用，不重新调查已闭合项；
   若某个工程子项需要新的游戏侧事实（例如 PacketTap 的真实写入成本、结构字段布局），
   按「逆向工作约定」自行开展**有边界的新逆向**，并把新证据落盘。

开始时记录 git status、HEAD、已有改动、当前游戏 PID/世界状态、安装 DLL 与构建产物
哈希、配置原文、crash 文件清单。不要把下列历史数字当作新执行的结果。
若工作区已有他人改动，先分清归属，保护其文件与 staging，不能整目录回滚。

交接时已核实 HEAD 为 b95a970，工作树干净；新 agent 必须重新核实。
原审阅第 1–11、16 项已经完成原定范围，第 12–15 项仍有未结部分。
docs/code-quality-review.md 的局部编号与原始 16 项不同，不要混用。

已完成内容不要重做：

- 请求与采集环的并发模型、世界进入看门狗、UseTask/聊天截止、Overhead 原子载荷。
- 查询/ECS/录制/宏播放等已落地的定长快照、局部异常隔离及分阶段生命周期。
- PlacePhase、ControlInputMode、JumpImpulse 已在 658d90a 实现。
- 8f55472 已修 Python 遗弃连接生命周期；读线程/finalizer 只持 _PipeStream，
  不经订阅 session 保活 client，现场连接专项 11/11。
- 45b5402 已修诊断导出绝对路径及等待回执，避免旧错误污染下一条请求；
  --diagnostic-export-only 已存在，历史专项 3/3。
- ViolationWatch、首错冻结、send_probe、断开页读取、首错保护和三态统计已存在。
  有些历史文档仍写“待实现”，须核代码后校准，不重复造设施。
- BadPacket(90) 已于 2026-11-01/02 闭合（证据册 §36–§49），结论可直接作为工程设计输入：
  队列/解析层忠实（入队深拷贝、长度取源 string 的 `_Mysize`、路径上没有帧长度前缀），
  根因是**游戏侧 `NetworkSystem` 的序列化缓冲被两个线程并发使用**（同 tick 两个 tid
  交替、`before/after` 链条自相矛盾，产出粘连帧 → 104 → BadPacket）。
  harness 不打补丁，只做时序规避。取证设施：首错冻结、`send_probe`（异常帧连栈落盘）、
  整帧上下文（191 字节）。已有 **1090/1090**（`--require-complete`，0 fail、0 skip）
  覆盖这些新代码，新增检查为 `inbound_freeze_*`、`send_probe_*`。

最近已有验收记录为离线 379/379、完整现场 **1090/1090**（`--require-complete`，0 fail、
0 skip），以及 ruff check/format、mypy、profiles、lint_public 通过。roles 最近记录为
56/63；未解析角色不要求补齐。这些均是既有记录，不是本次复验。

## 实施顺序与具体交付

### 第一阶段：校准当前契约与验收缺口

1. 建立一个有限的剩余工作表：问题、源码位置、优先级、修改方案、依赖、验收条件、
   当前状态。逐项对应原第 12–15 项，以及下面的工程补充项。
2. 核实并修正 driver/selftest.py::check_inbound_slices：当前函数说明和判断仍假设
   解析切片前存在长度 varint，与已证实的“该本地路径无长度前缀”冲突。
   关联核对 driver/harness/lab.py 的 decode_inbound_slice、slice_verdict、
   summarize_inbound_slices 及对应测试、帮助文本和报告。
3. 按实际采集路径建立适用的验证：包头解码、完整/截断标记、实际样本边界、序号关联、
   首错冻结等。单独保留有真实适用依据的 framing 判断；不得对前方堆字节猜长度。
   修正契约须保留兼容字段或明确增加路径/适用性信息，不能静默改坏已有消费者。
4. 解决这个必需 skip 的方式必须是纠正测试适用范围并提供有效替代覆盖，不能把 skip
   直接变成 PASS、降低 --require-complete 门槛或删掉所有边界验证来凑全绿。
5. 校准现行文档摘要中的过期结论：BadPacket 已闭合，原“尚缺失败帧”“声明长度 23”
   等不再是当前状态。历史轮次保留并标明历史/撤回，不覆盖原始证据。

### 第二阶段：自有调用时序的风险规避

已证实游戏共享序列化缓冲存在跨线程竞态；本仓库不修游戏实现。
但不能从“探针只读”“capture 关闭”“没有 chat_send”推断 harness 调用时序无关。
工程目标是减少自有代码在不合适的线程/时点触发原生状态同步，不承诺根治游戏竞态。

重点审阅 Flight.cpp / FlightModel.h、CommandInput.cpp、ProbeMod.cpp 的相关调用：

- fly/togglefly 客户端阶段、服务端阶段、8 tick 重断言及 finally 中的能力恢复。
- setAbility 等可能触发状态同步的调用所在队列、发生时机、同请求内的先后关系。
- reset/模式切换/恢复流程中是否存在可避免的重复应用、交错和过期副作用。

在已有源码与公开契约足够支持时，把自有请求变成明确的所属线程操作和有界顺序，
由游戏原生输入/同步管线传播状态；优先消除无必要的双侧反复写入。
不要把“放到一个队列”直接当成已经证明与另一线程不并发；说明实际保证和剩余风险。
如现有接口无法证明安全调用时点，保留限制并标阻塞，不用新的内部地址或补丁兜底。

保留或明确补充：请求编号、世界 epoch、busy/截止、取消、迟到回调拒绝、唯一终结、
partial、错误传播与重连隔离。fly_ok/togglefly_ok 的既有可观察契约不能悄悄削弱。
若调整 8 tick 重断言，解释替代机制与回读证据，不能单纯删掉回归检查。

至少完成一次“最小能力序列 + 已有守望/探针”的受控对照，再运行相关完整检查。
记录版本/DLL、世界、输入前提、序列、tick/tid、观察启停窗口与异常落盘路径。
设施 synthetic 自测与真实事件分开；有限次数没复现只能写“本次未复现”。

### 第三阶段：完成原第 12 项——热路径与 SDK 边界

对 safeTickPre/safeTickPost 及高频 hook 做剩余调用图/线程/所有权盘点，
只处理真实未治理的路径，不把历史列表中已经完成的迁移再做一遍。

重点为 PacketTap.cpp::serializePacket：它仍在 hook 内使用 thread_local std::string、
BinaryStream 和 packet.write(stream)，会有 SDK 分配与无界执行成本。
getId/write 已有局部 SEH/C++、失败计数、按序号关联的失败记录；这些应保留。

- 优先利用已存在且生命周期可靠的字节采集能力，在原调用上下文复制有界数据，
  冷侧格式化/导出；任何替代都必须交代数据来源、采样语义与有效期。
- 不把短命 Packet 指针丢到后台；不跨线程调用未证明可安全调用的 SDK；
  不因 reserve 一次或复制后截断就声称 packet.write 零分配/有预算。
- 如果公开 SDK 无法在保留全部能力的同时满足热路径纪律，明确拆分常规采集与显式
  高成本诊断的契约、权限和适用范围，再以兼容方式实施；无法安全完成的部分保留未结。
  需要确认某个 SDK 调用是否可安全离开 hook 时，允许做**有边界的新逆向**（例如确认
  写入点、内存所有权、是否加锁），但不要为此改动游戏实现。
- 排查其余高频 owning-string 访问、JSON/格式化、动态分配、已有热路径锁和未保护
  的 SDK 读取；逐一给出修改或可核查的保留理由，不能新增 mutex 掩盖热路径问题。
- 原生回归验证固定存储、边界、并发/重入、环绕/溢出、取消与唯一失败；现场回读验证
  数据有效性和生命周期，不能只证明“收到了成功事件”。
- 这一项往往需要**新的游戏侧事实**才能说清成本与线程归属：例如 `Packet::write` 在
  实际调用点是否分配、`Packet`/`BinaryStream` 的真实生命周期、hook 命中线程。
  允许按「逆向工作约定」用 IDA 静态定位 + `hook_install`/`send_probe`/`watch_set` 实测，
  但结论必须落回 `[代码]`/`[实测]`，并把新角色写进 `docs/roles/<version>.json`。
  拿不到可靠证据时不要用“reserve 一次就够了”代替结论，明确标阻塞。

### 第四阶段：完成原第 14 项——统一服务端玩家解析

Server.cpp 的 serverPlayerContext / ServerPlayerContext 已用于背包写、命令、权限、
物品使用和传送；Entities.cpp、Flight.cpp 仍有独立查找及隐式名字兜底。

统一到服务端所属队列内的共享策略：UUID + 世界 epoch 为默认身份；显式 player 名称
参数保持原有支持，缺省身份失效不得静默换成名字、控制台 origin 或另一玩家。
保留各领域清楚的错误原因，不跨线程保存 Actor/Level/ServerPlayer 指针。
验证无玩家、换世界、旧请求、同名前缀、取消、busy、SEH/C++ 与恢复场景。

已有候选 out/server-context-candidate.patch 与 out/server-context-candidate/ 仅供参考：
候选曾编译及离线通过，但真实飞行生命周期 28/29 失败，已经撤回，未落地。
必须对照当前代码重新设计/审阅，不能直接 apply 后标完成；失败原因不能未经对照归给候选。
先完成或明确第二阶段的时序规避边界，再验收这一项，避免把两个变化混成一个结论。

### 第五阶段：完成原第 13 项——领域状态与所有权

ProbeMod.h 中 MacroRecording/Input 整体会话及其余松散字段仍需封装。
按已存在 ChatCapture、PacketTap、MacroPlayback 等范式划分职责；类名由实际职责决定，
不要求机械套用某种设计模式，也不要新建一个同样耦合的总控容器。

至少覆盖：宏录制/停止/保存快照、输入会话、held/latch/按键状态、任务接管、
播放/录制互斥、输入锁、重连/退出/panic/disable 与错误恢复。
为每组状态明确：唯一拥有者、读写线程、代次/编号、释放协议、冷侧锁与锁顺序。
tick-owned 字段继续在游戏线程修改，取消后的释放失败允许有界重试且不能重放旧输入。
消除跨 TU 手写 reset 列表和重复生命周期分支，保留现有已验收的模型。
不能在 tick 引入 shared_ptr 生命周期回收、分配、JSON 或新的阻塞锁。

### 第六阶段：完成原第 15 项——夹具、交互效果与验收真实性

三态 Checks、--require-complete、WorldLost 首错保护和四组 finally 保护已实现，
本阶段完善剩余覆盖，不能只把 selftest.py 按行数拆文件。

- 按领域拆分检查和可复用 fixture，保留 selftest.py 入口、现有专项参数、返回码和
  可追踪的检查名；测试初始化、动作、观察、恢复各有明确边界。
- 盘点 setup/teardown 污染：朝向、脚部位置、能力/模式、药水、背包/快捷栏、水池、
  实体、输入与锁状态。用 lab.settle 和状态回读建立前提，少用裸 sleep。
- 攻击/交互/放置/使用应验证真实可观察效果或明确 best effort，而非仅断言 queued/ok。
  对 interact_entity 的已知能力限制如实写明；不为补覆盖而调用已知会崩的空物品路径。
- 加强受控生命周期回归：请求中重连、退档、取消、故障恢复、清理自身失败；
  主体首错不可被清理/导出错误覆盖，意外 world_left 后终止当前套件并保留现场。
- 已知 HBUI worlds.ready=false/退出未完成保留为独立限制；补状态/事件关联回归和诊断，
  若再次出现先落盘再按 recover 规则恢复，不放宽菜单安全门。
- 默认移动/跳跃仍走当前键位绑定的 Hardware HID；不恢复直接组件写、冲量注入、
  客户端/服务端手工双写，也不以 legacy 长按跳跃实验代替普通玩家跳跃检查。

### 第七阶段：明确补充债务并收尾

下列已知欠项也要给出明确结论，不能在最终交接中失踪：

1. Macro.cpp::saveMacroFile 仍是直接截断目标文件。完善同目录临时写入、检查写入结果、
   Windows 上可靠替换与失败清理，保证失败不破坏旧文件；保留停止后录制数据可重试保存。
   加有意义的故障回归与现场保存/再加载回读，遵守现有格式及错误传播。
2. Overhead/热路径改造的性能收益尚缺受控对照。用同 DLL/配置说明基线及实验变量，
   比较适当的开关组或可重复基线，报告采样窗口、p50/p99/max、tick_hz/开销及限制。
   不把文件变短、锁变少或一次分位数变化当作已经证明性能提高。
3. 历史提示词索引 docs/notes/prompts/README.md 仍有过期“待做/当前实质缺口”。
   校准其现行入口，历史需求与条件项保留历史身份，不把旧 H3/G5 等重新变成新功能任务。
4. 现场热卸载、超长录制等未执行的历史验证要继续标明“未验收”，不为凑完成度新增
   未授权的崩溃/破坏性实验；用现有 native 生命周期覆盖解释边界。

## 不可破坏的工程约束

- py -3.11；xmake -j4 -y，按 docs/mod-build-environment.md 使用 LLVM22 工具链。
- Internal.h 是统一共享入口，新共享工具放 probe::detail。
- 管道/spool 不碰游戏对象；分别使用 queueServerTask、queueClientTask 与所属 tick。
- 热路径只发布预分配定长载荷。局部 SEH 与 C++ 异常隔离不能穿过持锁帧跳析构。
- protocol 4 兼容、字段增量、旧 Python 导出/CLI 可用，未知命令行为保留。
- 版本数据进 profile，生成物同步；不猜测新的偏移或角色。逆向新结论必须先落
  `docs/roles` / `docs/offsets` 再在代码里用，不许在 `.cpp` 里硬编码 VA 或字段偏移。
- 强能力默认关闭，诊断配置最小化且可恢复；日志/仓库文档不写本机个人绝对路径。
- 动态调试只用于定位/消歧/证伪：结论必须能落回 `[代码]`/`[常量]`；不允许用
  “调试器里看到过”当作定案，也不允许为了省事 patch 游戏代码绕过问题。
- Python 探针用 with ModClient() 或 finally close；导出绝对路径并消费对应回执。
- 错误页用已有 mod 进程内诊断，不要求用户反复点击“显示详情”。
- recover 只对确认的 disconnect_error 自动强停；world/menu/exiting 保留保存门禁，
  只有显式 --force 才放弃会话。不能把“HBUI URL”单独当作安全退出依据。

## 验证与提交门禁

先记录当前基线；每批跑对应的有意义回归，再跑适用的总门禁，避免没有新依据的重复长测。
以下命令在仓库根目录执行，构建命令除外：

```powershell
py -3.11 -m unittest discover -s tests -v
py -3.11 -m ruff check driver tests
py -3.11 -m ruff format --check driver tests
py -3.11 -m mypy
py -3.11 driver/gen_profiles.py --check
py -3.11 skills/bedrock-re/roles/resolve.py --check
py -3.11 skills/bedrock-re/checks/lint_public.py
```

```powershell
# 在 mod/probe 内，先按构建环境文档配置工具链
xmake -j4 -y
```

安装新 DLL 前备份并受控退出；记录构建/打包/安装 DLL 哈希，确认加载了本批产物。
先运行相关现有专项，再跑完整 selftest --require-complete。权限与空槽前提先检查；
确需清空背包时先保存并恢复物品，不能以检查通过为由丢弃用户数据。

```powershell
py -3.11 driver/selftest.py --connection-only --require-complete
py -3.11 driver/selftest.py --diagnostic-export-only --require-complete
py -3.11 driver/selftest.py --flight-only --require-complete
py -3.11 driver/selftest.py --flight-world "超平坦测试" --require-complete
py -3.11 driver/selftest.py --packet-tap-only --require-complete
py -3.11 driver/selftest.py --macro-capture-only --require-complete
py -3.11 driver/selftest.py --macro-playback-only --require-complete
py -3.11 driver/selftest.py --input-events-only --require-complete
py -3.11 driver/selftest.py --controls-only --require-complete
```

专项按本批改动选择，不要求每次都重复全部；新增检查仍须从 selftest.py 可复现进入。
完整现场启用已有观察路径（按当前命令配置；不为此添加新逆向设施）：

```powershell
$env:LEVIPILOT_VIOLATION_DUMP = "out/remaining-refactor-full-violation.json"
py -3.11 driver/selftest.py --require-complete
```

每轮用独立文件名，结束恢复该环境变量原值。若 fail/required skip，保留首错、日志、
观察环和相关诊断，按原因修复或明确阻塞，不改统计凑通过。
最终逐字节恢复配置并在必要时重启使恢复生效；回读姿态/能力/背包/快捷栏、
输入释放/解锁、任务/录制/宏空闲，核对新增或修改的 crash 文件并解释差异。

每个完成项同步 docs/reliability-repairs.md、docs/code-quality-review.md 的状态，
使用契约/基线同步 mod/README.md，实现与实测数字同步 docs/mod-internals.md，
架构变化更新 docs/architecture.md，必要时更新 AGENTS.md。
历史证据不篡改，当前摘要以本次真实验收为准。

最终交付一份按优先级排列的结果：

1. 每个剩余项完成了什么、关键文件、设计理由与兼容影响。
2. 每项回归、构建、专项、完整现场的真实 pass/fail/skip、退出码与证据路径。
3. 时序规避与游戏根因的边界，未复现不等于根治；任何阻塞和未验收范围。
4. 配置/世界/输入恢复、DLL 一致性、crash 核对结果。
5. 本地提交哈希、工作树状态；明确未推送。
6. **本轮新增逆向的清单**（如有）：每条给角色名、静态地址、证据分级、落盘位置
   （`docs/roles` / `docs/notes` / `offsets`），以及它支撑了哪个工程结论；
   没有新增逆向就写“本轮未新增逆向”，不要留空。

只有范围内工作和验收全部完成才能称工程收尾完成；若有必须外部资料才能解决的子项，
交付可独立使用的已完成部分与具体阻塞清单，不将阻塞项标成已完成。
