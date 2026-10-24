# 结构重构记录（2026-10-23 起）

> **历史记录，不是现行规范。** 本文按轮次保存 2026-10-23/24 的结构重构、任务与会话
> 重设计、世界进入崩溃修复的决策与验收证据；其中的模块行数、通过数等数字会随时间过时。
> 现行结构看 [`architecture.md`](architecture.md)，实现细节看
> [`mod-internals.md`](mod-internals.md)，使用限制看 [`../mod/README.md`](../mod/README.md)。

## 基线与依赖

重构前工作区干净；Python 146 项单测、ruff lint/format、mypy 全部通过。
现有结构已经按游戏能力划分实现模块，问题集中在入口层，而非所有模块都需要重写。

```text
CLI / 开发脚本 / 场景 runner
  → transport_mod（兼容门面）→ harness.client（连接 + 命令 + Harness）
  → UTF-8 JSONL 命名管道
  → Transport → Commands（解析 + 所有命令处理 + 异常隔离）
  → 请求块 / 请求队列 → 游戏 tick 或集成服务端线程
  → State / Input / Tasks / Inventory / Entities / Macro / World 等

docs/offsets → gen_profiles → GeneratedProfiles.h → Resolve / Memory / ECS
tick / hook / VEH → 定长记录环 → 管道格式化 / spool → Python 解码与分析
```

| 问题 | 证据与影响 | 优先级 |
| --- | --- | --- |
| 分发入口职责过多 | `Commands.cpp` 1770 行；新增命令和异常边界混在一起，难以定位能力所属模块 | P1 |
| Python 连接和业务耦合 | `client.py` 1540 行；管道句柄、100 多个命令/工作流、脚本门面同文件 | P1 |
| 重复的写入循环 | `send` / `send_raw` 重复处理短写、flush、关闭与错误转换，修复容易遗漏一处 | P1 |
| 内部代码反向依赖兼容入口 | `mod_agent.py` 经 `transport_mod.py` 间接加载 CLI；业务不应依赖历史入口 | P2 |
| 文档与实现有偏差 | 架构图仍写 protocol 3，输入请求已使用 seqlock；扩展步骤未反映模块边界 | P2 |
| 共享状态集中 | `ProbeMod.h` 739 行，跨模块状态仍由单个对象持有；共享头变化触发全量构建 | 后续 |
| 任务安装存在近似重复 | 各任务重置其他任务的范围并不完全相同，直接统一会改变既有行为 | 后续，先定义行为 |
| CLI / live selftest 较大 | argparse 定义、执行分支、现场检查集中；可在各能力继续扩展时按领域提取 | 后续 |

命名整体遵循 C++ camelCase / Python snake_case；`diag` / `diagnose`、
`record_start` / `macro_rec_start` 等虽然相似，却是已经发布的不同协议能力，不在本轮改名。
已有局部名保留，领域处理器用明确的职责名称，避免机械改名掩盖行为差异。

## 范围、顺序与兼容约束

1. **固定基线**：运行现有检查，补充覆盖短写、失败、未连接时序和旧入口的行为检查。
2. **Python 分层**：提取连接与脚本门面，保留 `client`、包根和 `transport_mod` 导出；
   合并写入实现，业务代码直接导入包接口。
3. **C++ 按领域提取**：诊断、内存/观察点/钩子、输入、封包、记录/macro、查询、世界、任务。
   入口只解析一次 JSON，再调用领域处理器；领域处理器只在识别并处理命令后返回 true。
   保留所有参数、默认值、响应、权限门、审计和请求级 SEH；不改 tick / hook / VEH 热路径。
4. **同步扩展约定**：更新架构、模块地图、live selftest 和 mod 限制说明。
5. **验收**：单测、ruff lint/format、mypy、profile 生成一致性、旧 CLI 入口、C++ `xmake -j4 -y`；
   可用游戏环境内运行 live selftest，实际执行结果另记，未运行的检查不能当作通过。

本轮保持协议 4、命令名称、错误内容、Python 参数及默认值、兼容导入路径。
不改变版本数据、线程归属、任务生命周期、能力默认开关，不引入动态注册表或热路径分配。
`Internal.h` 继续是各 C++ TU 的统一内部入口，不新增共享内部头。

后续状态封装应按输入、录制、诊断等领域逐个进行，先覆盖跨线程行为，再搬移成员；
任务安装的去重须先确定 `place/use` 是否应该取消 jump/route，再以独立行为变更处理。

## 实施结果

| 层 | 重构后职责 |
| --- | --- |
| `harness/connection.py`（212 行） | 只管理连接、读线程、请求等待、完整写入与 I/O 错误 |
| `harness/commands.py`（1076 行） | `ModClient` 命令 payload 与工作流，继承连接实现 |
| `harness/facade.py`（262 行） | `Harness` 组合客户端，提供脚本命名与集合解包 |
| `harness/client.py`（10 行） | 保留旧路径，重新导出同一组公开类 |
| `Commands.cpp`（94 行） | 解析、异常隔离、领域分发、未知命令 |
| 8 个 `Command<领域>.cpp`（各 129–310 行） | 原有 96 个条件分支、103 个命令名称按领域归属 |

领域处理器共用一个已解析的 JSON 对象和既有 `ProbeMod` 状态，不创建新的请求队列、锁或线程。
输入三命令的共享权限检查与后续分支一起搬移；服务端 lambda 内的返回语义保持不变。
共享头只增加私有处理器声明，仍由 `Internal.h` 统一引入。

`send` 与 `send_raw` 共用 `_write`，保留 JSON 紧凑格式、原始字节、短写循环和 flush。
未连接时仍先报 `ModDisconnected`；连接存在时 JSON 编码错误仍直接抛出且不关闭连接。
新增四项单测覆盖这些边界，并核对旧包入口对象身份。

静态逐项对照：193 个方法（包含内部及上下文方法）的名称、参数和默认值相同；
其中除合并的两个写入方法外，191 个 Python 方法体的 AST 相同；96 个 C++ 分支命令体与原文一致，仅把处理器的提前返回
改为“已消费=true”，保留三个服务端 lambda 的 void 返回。检查材料保存在本地 `out/`。

## 验收记录

- Python 150/150 单测通过；ruff lint、format、mypy（15 个文件）通过。
- `driver/gen_profiles.py --check` 通过，版本 profile 和生成头未修改。
- 旧 CLI 脚本入口和模块入口的 `--help` 输出一致；公开导入对象一致。
- C++ release：`xmake -j4 -y` 构建成功，约 176 秒。打包器提示无法解析版本 tag，使用 `0.0.0`；
  编译链接成功，游戏版本 profile 不依赖此打包标签。
- 经用户同意，保存退出当前世界，备份已安装 mod 后重启进入“超平坦测试”；
  保留 `harness.json`，安装 DLL 与构建 DLL 的 SHA256 一致。
- 原自检首轮新 DLL 138/144、备份旧 DLL 139/144：两者均复现 4 项命令包失败和
  `jump_distance` 失败，新 DLL 的首轮还发生一次 route 超时；该次结束时界面停在聊天框。
- 定位到自检前提错误：距离起跳目标在 +X，测试却用 yaw=0 沿 +Z 前进；聊天步骤未等待
  `chat_screen`，输入文本到 Enter 仅留 2 tick。修正自检朝向、显式检查聊天打开/关闭并给文本
  10 tick 消费时间；若提交失败则关闭聊天，保留失败断言，避免污染后续移动测试。
- 新 DLL 独立复测 18/18 通过（含 8 领域、距离起跳、路线及坐标回读、SEH 后 tick 恢复）；
  在同次复测中聊天回到 HUD，成功抓到 1 个真实 77 模板。产品代码未为这些失败作行为修改。
- 修正自检前提后，新 DLL 完整 live selftest **146/146 通过**：包括 12 个新增分发检查、
  聊天开关/77 模板、封包注入 setblock 回读金块与相对传送 1 格、距离起跳、两段 route、
  watch/hook、SEH 隔离、profile、记录解码、背包、实体、macro 回放。
  未降低原有断言；新增的等待和正确朝向让测试符合其目标。
- 本地验收日志：`out/refactor-unittest.log`、`out/refactor-selftest-final.log`；
  首轮与旧 DLL 对照分别在 `out/refactor-selftest.log`、`out/refactor-baseline-selftest.log`。
  `out/` 为忽略的运行产物，不随源码提交；上述命令和 selftest 可重复执行。

验收针对本机 1.26.51.01 与现有单人测试世界；没有据此宣称其他游戏版本或所有并发场景均已覆盖。
后续状态封装需继续保持同样的接口和线程边界；CLI 分域已在下述续轮完成。

## 续轮：CLI 参数与领域执行分离

继续处理入口层的剩余耦合：原 `cli.py` 的 `main` 同时定义全部参数、执行 81 个在线命令
分支、处理两个离线命令，并负责连接和错误输出。按以下顺序实施：

1. 固定旧实现的行为：先在旧入口采集并运行 236 组特征检查，覆盖全部叶命令的默认参数、
   显式参数、帮助、文件写入、离线解码、参数错误、连接失败及 Ctrl+C。
2. 提取 `cli_parser.py`：只定义 argparse 参数；保留命令顺序、帮助文本、类型和默认值。
3. 提取 `cli_commands/` 的诊断、输入、内省、封包、查询、世界、任务、录制八个执行模块。
   每个模块处理后返回 true，未识别返回 false；入口统一管理连接、异常和退出码。
4. 在 live selftest 加入真实 CLI 子进程检查，覆盖八个领域及旧模块入口。命名管道只接受
   一个客户端，因此子进程执行前释放当前连接，结束后在 finally 中重连。
5. 更新架构与扩展约定，运行兼容性、类型、静态和游戏检查。

`cli.py` 从 887 行降为 67 行，`cli_parser.py` 为 368 行，八个执行模块各 35–107 行。
离线 `spool` / `packet-decode` 仍在连接前执行，`blocks` 参数错误仍由同一个 parser 输出，
管道错误仍返回 2、Ctrl+C 仍返回 130。`transport_mod.py` 的脚本与模块入口均继续使用同一
`main`；没有更改 Python 客户端公开接口、协议、C++ 状态、线程、热路径或版本数据。

### 续轮验证

- `tests/fixtures/cli-baseline.jsonl` 是重构前采集的固定基线，不根据新实现更新期望；
  `tests/test_cli.py` 对照 236 组调用、标准输出/错误、退出结果和文件内容，全部一致。
- AST 逐项核对：81 个执行分支的原有语句完全相同，只增加“已处理”的返回；参数定义和
  macro 子命令定义也相同，仅将 parser 的 description 改为由入口传入。
- 完成前重新对照重构前的 Git 源码：193 个客户端/门面方法的参数一致，191 个方法体一致；
  两个写入方法已合并并有短写及异常测试。提取连接层后的四个链式方法用 `Self` 返回类型
  替代 `ModClient`，仍返回自身；旧导出对象身份有单测覆盖。96 个 C++ 命令分支也逐一核对。
- Python **155/155** 单测通过（CLI 的一项测试包含上述 236 组子检查）；ruff lint / format、
  mypy（25 个文件）和 `gen_profiles.py --check` 通过。
- 真实管道的八个领域 CLI 命令及 `python -m driver.transport_mod ping` 共 **9/9** 通过；
  完整 live selftest **163/163** 通过，包含前轮的连续起跳回归。
- 完整自检的宏确定性项因真实鼠标移动按原逻辑跳过；随后独立复测 **2/2** 通过，
  两次视角漂移均为 0、回放落点误差 0.000000 格，没有降低原断言。
- 本轮没有 C++ 改动，复用已构建且验证的 DLL；安装文件与构建文件 SHA256 相同。
  游戏验证结束后恢复玩家原位置、朝向和选中槽位，输入、macro、记录、观察点均已停止。
- 本地日志：`out/refactor-cli-unittest.log`、`out/refactor-cli-parity.log`、
  `out/refactor-cli-selftest.log`、`out/refactor-cli-macro-check.log`；静态对照脚本与原 CLI
  副本同样保存在忽略的 `out/` 中。固定基线和可复现检查随源码提交。

### 对原始要求的完成核对

| 原始要求 | 交付与验证证据 |
| --- | --- |
| 梳理结构、模块和依赖 | 本文的基线依赖图、问题表，以及更新后的 `architecture.md` |
| 说明主要问题与优先级 | 入口职责过多、连接耦合、重复写入、反向依赖和文档偏差均有位置及影响说明 |
| 明确范围、步骤和顺序 | 前轮五步方案与本轮五步方案；优先处理入口与边界，逐步提取 |
| 实施重构、接口兼容 | C++ 八领域、Python 三层与兼容导出、CLI 八领域；公开方法/分支 AST 与 CLI 行为对照 |
| 职责清晰、易于扩展 | 解析、领域执行、连接及脚本门面各有单独模块；扩展步骤已同步到架构和 AGENTS |
| 验证稳定可运行 | 单测、静态检查、profile 一致性、前轮 C++ 构建及本轮真实管道/完整游戏自检 |
| 说明改动与验证限制 | 本文逐轮保留结果、复现命令和干扰处理；验证限于当前版本与单人测试存档 |

已完成所定范围内的重构与验收。`ProbeMod` 状态进一步封装、live selftest 按能力拆分，以及
`place/use` 与 jump/route 的任务替换语义仍是独立的后续设计议题；当前重构保留现有行为，
没有把需要先定义行为的任务清理策略顺便改掉。

## 行为重设计：任务与连接的所有权

本轮用户明确允许破坏性改动。基线为 `f74a1cf`，工作区干净、155 项无游戏测试通过。
前三轮解决了入口耦合，本轮按状态不变量重设计两个核心组件，保留已经分域的命令与 CLI。

| 问题 | 实际后果 | 优先级与实施顺序 |
| --- | --- | --- |
| 六个 optional + 六份 reset 清单 | `place/use` 漏清 jump/route，隐藏任务可继续运行 | P0：先建立单活动任务模型 |
| epoch 仅防写回，终结事件在校验后无条件发送 | 已替换任务仍可能发完成事件；旧 use 服务端回调仍能消费物品 | P0：副作用、状态变换、结果提交使用同一同步边界 |
| 读线程持有 self 上会被重连替换的队列/停止信号 | 迟到的旧线程能向新连接投递数据或关闭通知 | P0：每次连接独占 session |
| 请求循环丢弃所有其他事件 | 录制、世界事件、任务结果会被其他请求吃掉 | P1：响应等待与延后事件消费分开 |
| task tick 构造 JSON/复制字符串 | 热路径分配，任务状态与传输格式耦合 | P1：定长类型和事件，后台格式化 |
| 非法模式回退、路线静默截断/跳过坏行 | 调用成功却执行了另一种任务 | P1：先完整验证，再改变活动任务 |

### 实施设计

- `TaskModel.h` 是不依赖 SDK 的领域模型。`TaskState` 是封闭的 variant，`TaskController`
  封装活动槽、ID、同步和固定容量事件队列。它替代原任务锁和 epoch，没有新增第二把任务锁。
- `Tasks.cpp` 只计算一个 tick 并写 `TaskStep`；不构造任务 JSON、字符串或服务端闭包，
  不复制整条 route 再写回。固定文字缓冲按 UTF-8 字符边界截断。
- `TaskEvents.cpp` 在已有 spool 线程消费记录，负责 wire 格式和服务器使用物品的调度。
  use 从 waiting 转到 server_pending 后仍占有任务，服务器回调核对 ID 后才能产生副作用。
- tick 与取消串行化，完成/取消只能提交一次；请求解析失败、背压拒绝不会取消当前任务。
  128 槽事件队列为取消预留空间；背压时暂缓任务并释放脚本移动输入，排空后再推进。
- 任务接管清理输入，宏的按键释放从管道线程移回游戏线程。退出世界/reset/panic/disable
  统一调用取消入口。已完成的游戏副作用不做事务回滚，取消作用于之后的推进/回调。
- 游戏调用的 AV 由锁内 SEH 边界转成任务失败，避免 `/EHsc` 下 AV 把任务锁永久占住。
  服务器使用也有独立 SEH 边界；C++ 异常走正常栈展开及错误结果。
- `_PipeSession` 独占句柄、读线程、停止信号及两个消息缓冲。连接对象负责串行工作流和
  会话切换，读线程只写自己的 session。关闭连接可唤醒消费者。
- `request` 等待新的响应并保存其他事件；`wait_event` 可按谓词取出缓冲事件；`readline`
  按顺序消费剩余消息。接收队列 8192 行、pending 4096 行，超限明确断线而不无限增长。
  `exchange` 串行化整个工作流，协议 4 仍为单路请求，并未伪装成多路并发 RPC。

### 有意改变的行为与迁移

1. 任意新任务替换旧任务，包括 `place/use` 对 jump/route；没有任务队列或隐式恢复。
   正常结果事件保留，新加 `task_cancelled`；通过其 reason 区分 replaced、stopped、panic 等。
2. 所有 started、terminal、status 带 `task_id`；ID 在当前 mod 生命周期递增，空闲 status 为 0。
   事件转换异步，可能与其他命令回复交错，调用者必须按 ID 匹配。
3. Python 阻塞任务方法先等 started 再按 ID 等终结；取消抛 `ModTaskCancelled`，包含
   `task_id/reason`。新客户端的任务方法需要同步更新 mod；旧 mod 缺少 ID 时明确报错。
   普通命令、旧包入口与 CLI 参数继续可用，协议号保持 4，hello 新增 `task_lifecycle` capability。
4. 非法 jump mode、超过 64 点的 route、非对象 waypoint、超过 255 UTF-8 字节的 expected
   现在报错，不再容错为另一个任务。超长路线请由调用者明确拆成多次 route。
5. 等待请求不再吞掉异步事件。因此 `readline` 可能读到过去被丢掉的 tick/世界通知，
   调用者要按 event 筛选；持续录制要及时消费，或用二进制记录环/落盘接口。
6. 同一连接的阻塞工作流串行执行。低层多步 send/readline 使用 `with client.exchange()`；
   `close()` 可从另一个线程中断等待。若需要后台启动任务，使用 request 等 started，
   之后可顺序调用 task_stop，再 wait_event 读取对应 ID 的取消通知。

```python
from driver.harness import ModClient, ModTaskCancelled

with ModClient() as client:
    started = client.request({"cmd": "use", "ticks": 2000}, "use_started")
    client.task_stop()
    event = client.wait_event(
        "task_cancelled", predicate=lambda row: row.get("task_id") == started["task_id"]
    )
    assert event["reason"] == "stopped"
```

领域状态仍逐个封装：本轮没有把内存、封包、世界模块全部塞进一个通用框架，也没有改动
版本 profile、低层观察点/钩子权限或 CLI 固定基线。`ProbeMod` 保留生命周期编排和其余领域
组件；需要继续拆分的模块应先定义自己的线程/所有权不变量。

### 可复现验证

- `py -3.11 -m unittest discover -s tests -v`：包含生产 C++ 模型的独立编译/运行，
  验证 36 组替换、唯一终结、服务器旧回调无副作用、异常终结、取消与完成竞态、背压恢复、
  UTF-8 截断，以及 1000 轮控制器操作零 C++ 堆分配。CI 要求 clang++，本地缺失时明确 skipped。
- Python 定向检查覆盖迟到旧线程、关闭唤醒、异步事件保留及顺序、缓冲溢出、旧结果 ID 隔离、
  typed cancellation 和缺 ID 的旧 mod。CLI 的 236 组固定行为检查仍保留。
- `driver/selftest.py::check_task_lifecycle` 验证 jump/route 到 place/use、非法请求保留当前任务、
  服务端空手结果及连续三次重连。`selftest_task_fault` 在 `allow_selftest_stress` 下制造一次
  任务锁内 AV，检查失败结果、任务空闲与 tick 继续；开关关闭时只检查拒绝并注明跳过。
- 运行 lint、format、mypy、profile 生成检查，构建并安装 DLL 后执行完整 live selftest。

最终验收数据见下方，运行日志保存在忽略的 `out/redesign-*` 中。

### 验收中发现的状态输出缺陷

第二轮连续完整自检中 `jump_motion_ready` 失败。回读发现 effects 里是七个
`id=0,duration=0` 的空槽；`Actor::getAllEffects()` 在 clear/重置后保留了 NO_EFFECT
占位。这不是活跃状态，却让调用者误以为仍有药水效果。
`State.cpp` 现在通过游戏提供的 `MobEffectInstance::NO_EFFECT()` 标识过滤空槽，
没有修改实际效果、持续时间或跳跃算法。speed 的正向检查保留，并新增 clear/reset
后 effects 为空的两条回归；无限效果不通过 duration 数值判断，避免误删。

正常操作另有 `check_task_operations`：在先回读为空的临时单元上真实放置、挖掘，
并验证导航落点、任务 ID 和 block 回读；最终恢复单元、空槽、模式、位置与选中槽。

### 验收中的导航与诊断修正

初始朝向 -53.17°、目标向北 0.7 格、容差 0.15 格时，旧导航稳定绕圈并在 80 tick
超时，终点误差约 0.2965 格；相同位置从 180° 出发能成功，方块回读排除了障碍。
原因是限制转角为每 tick 35°，却一直满速前进。`navigate/route` 现在共用转向策略：
尚未对准时不前进，接近目标逐渐减速，低速区关闭 sprint，原地转向不累计卡住起跳。
自检从四种初始朝向分别验证两种入口；未降低容差或跳过失败项。

用户看到“该方块无法放置”的提示时，日志中的放置与挖掘均已成功，随后冗余的
`setblock ... air` 返回 false。清理现在先回读，仅当方块仍存在时执行，并保留最终验证。
原接口未记录聊天文字，因此新增 `chat_history` / `chat-history --limit 32`：客户端线程
读取游戏保留的缓存并复制到定长 mailbox，spool 线程格式化；不使用内存偏移或新 hook。
最多 64 条、每条 2047 UTF-8 字节，保留原语言并标记截断；这不是永久日志，也不能恢复
游戏已经清除的历史。live selftest 用仅自己可见的 tellraw 标记验证实际文字读取及 CLI。
CLI 固定基线未重采集；对帮助/错误输出只排除这一条有意增加的命令，再对照原样本，
并单独检查新命令的调用及帮助。

新接口实际读到了中文测试消息，并通过封包通道向本来就是空气的单元再次 setblock air，
复现并捕获了 `§c该方块无法放置`。普通 `command` 使用 suppressOutput，故不能凭它的
false 结果断言原聊天提示一定来自清理。进一步检查发现旧 H2 封包自检反复在固定位置
放置金块且不恢复：后续运行可能因目标本来就是金块而报错，最终类型检查却仍通过。
H2 现在选择预先回读为空气的单元，验证空气 → 金块，再在 finally 清理并验证恢复空气；
不会删除旧测试或用户已有的金块。`packet_inject_setblock_precondition/restored` 保证这项
检查必须发生实际状态变化，排除了原来的重复运行假阳性。

宏回放自检在 reset 后显式等 10 tick，再等静止。`reset_done` 只表示请求已接受，
服务端传送和客户端同步尚未落定；旧位置静止不能当作新起点就绪。
独立严格复测保留了视角漂移 < 0.1°、两次落点差 < 0.0001 格的断言。

### 本轮最终验收

- Python 单测 **165/165**，包含生产任务控制器的 native 编译/运行与 236 组 CLI 固定基线。
  ruff lint / format、mypy（25 个文件）、profile 生成一致性和 Git whitespace 检查通过。
- `xmake -j4 -y` 全量构建成功；已备份旧 mod，保存退出后安装并重启进入“超平坦测试”。
  构建和安装的 DLL SHA256 一致：
  `62f1960f37c6fc119f12f286aaff41a70e00824f14a8fe7f022bd9e5aa4e970f`。
- 导航/路线的四种朝向及真实放置/挖掘定向检查 **13/13**；聊天接口实际捕获了中文
  tellraw 与重复 setblock 的错误提示，CLI 可读取同样的客户端缓存。
- 完整 live selftest **209/209**。HID 连跳 4 次、脚本连跳 4 次和单次 jump_when 的完整弧线
  比较通过，误差 < 0.0001 格，没有重新引入此前的起跳补位移。
- 一轮完整自检的宏确定性断言按原规则因外部视角变化跳过；独立严格复测 **2/2**，
  两次视角漂移均为 0°、落点误差 0.000000 格。未放宽精度断言。
  加入 reset 后等待后再次完整复跑 **209/209**，宏精度断言未跳过，落点误差仍为 0。
- 运行结束恢复原位置、朝向和选中槽位，停止任务/移动/宏/录制，确认 HUD、观察点清空。
  备份与日志保存在忽略的 `out/redesign-*`；验证针对本机 1.26.51.01 单人测试世界，
  不据此宣称其他版本或所有并发场景都已覆盖。

CI 工作流显式检查 Windows runner 的 LLVM 编译器版本，并将其安装目录加入 PATH。
native 测试也支持从 Windows 标准 LLVM 安装目录或本机工具目录寻找 clang++；
CI 找不到编译器时失败，不默默跳过。

最终日志：`out/redesign-unittest-accepted.log`、`out/redesign-chat-build.log`、
`out/redesign-selftest-accepted.log`、`out/redesign-macro-verified.log`、
`out/redesign-chat-error-repro.log`；安装备份为 `out/redesign-backup-32084/`。

## 快速退出/重进崩溃（world session 续轮）

### 现象与证据

压力测试在 `leave_world` 后立即 `enter_world` 时崩溃：`0xC0000005`，`last_command` 是
`enter_world`，三次崩溃都落在 `Minecraft.Windows.exe+0x8140139`
（`crash-35000-2400.dmp`、`crash-36764-147.dmp`、`crash-28396-192.dmp`）。

dump 的异常参数是 `READ at 0x498`（`NullPointer + 0x498`），崩溃指令为：

```text
movq 0x230(%rcx), %rax   ; rcx = 全局单例；rax = null
movq 0x498(%rax), %rcx   ; 访问违例
```

崩溃栈全部在游戏模块内（没有 probe/LeviLamina 帧），是一条 UI 调度/回调链
（`MC+0x8148530 → 0x8147bc0 → 0x8141700 → 0x8141150 → … → 0x8140070`），
说明不是直接的 `startLocalServerAsync` 入口，而是它破坏 UI 状态后在后续过渡里爆掉。

启动日志给出两个直接原因：

```text
22:29:32.928 enter_world: starting 超平坦测试 signed_in=1
22:29:34.627 auto: entering 超平坦测试
22:29:34.627 enter_world: starting 超平坦测试 signed_in=1     # 同一进程第二次 start
```

以及修复前最后一次重进的进入条件：

```text
enter_world: start phase=menu screen=world_saving_progress_screen - world_loading_progress_screen
               leave_done=1 client_leave_done=1 can_start=1
```

即 `isLeaveGameDone()` / `ClientInstance::isLeaveGameDone()` / `canStartLocalServer()` 三者都已为
true，但 UI 仍停在旧世界的 saving/loading 过渡屏。此时 `startLocalServerAsync` 会留下不完整的
screen 栈，约 10 秒后（tick 147–192）在 UI 回调里空指针崩溃。

### 修复

- `WorldSession::mEntryInFlight`：同一时刻只允许一个本地服务端启动。显式 `enter` 会先取消
  `auto_enter` 的延迟回调；已在加载中时第二次进入返回
  `{"event":"enter_world_result","ok":true,"reason":"already_starting"}`，由 join 事件收尾。
  `autoEnterOnClient` 发现进入在飞时直接停止自己的重试链，不再启动第二个服务端。
- 主菜单 screen 门：`isMenuScreen()` 只接受 `start_screen` 或 HBUI 主菜单
  （`ui://…`），并显式拒绝任何 `progress_screen`。离开世界后必须等 UI 回到菜单，
  即使 leave-done 标志已经为 true。等待仍以 100 ms 的状态轮询进行，不是固定 Sleep；
  超时（`signin_wait_ms`）返回 `world_busy` 并记录 blocked 日志。
- 进入前记录一行状态（phase/screen/leave_done/client_leave_done/can_start），
  以后换版本能直接从日志判断 gating 是否仍然成立。
- 压力测试 `finally`：恢复失败不再覆盖原始异常；正常结束时清理移动、任务、宏、
  观察点并回读确认（`stress_cleanup`）。

### 验证

- 定向复现 `out/followup-stress.py`（30 cycles，第 9/19/29 cycle 真实退出并立即重进）：
  **123/123 通过**，日志 `out/followup-stress-run3.log`。
- 重复 enter 探针：第二次返回 `already_starting`，只产生一次 `enter_world: start` 且正常 join。
- 完整 live selftest + 30-cycle stress：**332/332 通过**（`out/final-selftest.log`）。
- 离线：单测 **167/167**，ruff lint/format、mypy（25 文件）、`gen_profiles.py --check` 通过。
- 结束后恢复玩家位置/朝向/槽位/模式，任务、移动、宏、记录、观察点均为空；
  安装 DLL 与构建 DLL SHA256 一致。

### CI 的 mod-build

`ci: build the complete client mod with a portable LLVM toolchain`（run 39969952524，两次 attempt）
的 unit job 通过，mod-build 在**从源码安装 LeviLamina 包**时失败。工作流先加入失败时
打印/上传 `installdir.failed/logs/install.txt`，随后定位到两个上游头文件问题
（本机干净缓存安装可以过，干净 runner 暴露了它们）：

1. `RakPeer.h` 的内联默认析构函数持有 `unique_ptr<ShadowBanList>`，而
   `ShadowBanList.h` 反向包含 `RakPeer.h`（为使用嵌套的 `RemoteSystemStruct`），
   两个包含顺序都无法让析构函数看到完整类型；没有 PCH 的编译单元会触发
   `std::default_delete` 的 incomplete-type 断言。配方把析构函数改成声明，并生成
   `RakPeerDtorPatch.cpp` 在两个类型都完整后定义它。
2. `optional_ref::and_then` 在空值分支返回 `std::remove_cvref_t<Ret>{}`；
   `Packet.cpp` 传入返回 void 的 lambda 时，干净 runner 报
   `illegal initializer type ... aka 'void'`。配方给该分支加
   `if constexpr (std::is_void_v<Ret>)`。

修复后 run **39987591253**（`bf642f1`）的 unit 与 mod-build 均成功；artifact
`probe-client-bf642f1…` 包含 `probe.dll`、`probe.pdb`、`manifest.json`。

## 继续拆分：PacketTap（封包 tap）

世界生命周期拆完之后，下一个选的是封包 tap：它有真实的跨线程状态（send/recv 钩子在
客户端/服务端线程，start/stop/drain 在管道线程），同时和命令包注入有清晰的缝。

### 设计

- `PacketTap.h/.cpp` 独占 H1 的全部状态：身份环、字节环、ids/serialize_ids/sample、
  9 个 send/recv 钩子、逐锚点命中数、drain/status 的 wire 格式。
- `ProbeMod` 只保留：
  - `onPacketSent` / `onPacketReceived` / `packetPeerTick` / `packetCaptureStop` 四个薄转发，
    文件作用域的 LL 钩子不需要改；
  - 命令包模板捕获（`captureCommandTemplate`）和 `chat_send` 注入。
- 线程与热路径规则不变：钩子体仍只做 `getId`、原子计数、定长环写入；start/stop 与
  drain 仍在管道线程；反序列化白名单仍只在 capture 开启时调用 `Packet::write`。
- `allow_packet_capture` 的权限门移到 `CommandPackets.cpp`，返回同样的
  `packet_capture_disabled`；`PacketTap` 不再知道配置开关。
- 公开协议不变：`packet_capture` / `packet_drain` / `packet_bytes` / `packet_stats`
  的字段、错误和默认值完全一致。

### 规模与验证

- `Packets.cpp` 从 768 行降到 91 行；新增 `PacketTap.cpp` 689 行、`PacketTap.h` 116 行；
  `ProbeMod.h` 从 660 行降到 581 行（减少 79 行成员/声明）。
- 构建：`xmake -j4 -y` 成功，构建/安装 DLL SHA256 一致。
- 直接接口检查：`packet_capture {directions:["send","recv"]}` 回
  `send_hooked=true / recv_hooked=true`；2 秒内 77 条身份记录（send 41、recv 38），
  `recv_records=37`、`dropped=0`，`loopback_sendToClient` 命中 289，
  接收字节含 `MoveDeltaActor(111)`；关闭后 `active=false`。
- 完整 live selftest **209/209** 通过（包含 H1-A 身份/序列化、H1-C 接收侧、H2 注入）。
- 离线：单测 **167/167**、ruff lint/format、mypy（25 文件）、`gen_profiles --check` 通过。
- 一次自检在关闭背包时按 Esc 后世界进入暂停菜单，排队命令 20 s 超时；这是已知的
  “暂停菜单不响应注入按键”限制，重启并聚焦后同一 DLL 复跑 **209/209**。该失败与
  本次拆分无关（封包相关检查在失败前已全部通过），未通过放宽断言规避。
