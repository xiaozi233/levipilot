# 结构重构记录（2026-10-23）

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

静态逐项对照：191 个公开方法的名称、参数和默认值相同；除合并的两个写入方法外，
191 个 Python 方法体的 AST 相同；96 个 C++ 分支命令体与原文一致，仅把处理器的提前返回
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
后续优先考虑 `ProbeMod` 状态封装与 CLI 分域；逐个改动需继续保持同样的接口和线程边界。
