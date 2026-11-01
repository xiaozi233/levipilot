# SetActorData / BadPacket 自然断开：后续诊断提示词

将以下内容交给接手模型。它是一次有限范围的诊断任务，不是授权重写整个项目。

请接续本仓库中 LeviPilot 的自然断开调查。先阅读 `AGENTS.md`、
`docs/reliability-repairs.md` 最新现场小节和 `docs/mod-internals.md`，保留其他会话的未提交改动。
Python 必须用 `py -3.11`，构建限制 `xmake -j4 -y`。不要启动子 agent，不自行推送。

## 已知现场与证据等级

1. **[实测]** 2026-10-31 北京时间 **23:38:53**，完整自检在 `check_flight` 的 finally
   恢复阶段自然退档。最后通过 `flight_spectator_recovers`；清理中的等待起点 tick 859，
   861 退档。结果 **465/466，1 fail、0 skip**，失败 `aborted_world_left`。
   该检查先前包含受控 SEH/C++/timeout 自测，不能称为无故障注入的最小能力序列。
2. **[实测]** 两条新增观察均非合成、同 tick **861**、同 tid **30828**：

   ```json
   {"source":"callback","synthetic":false,"response":3,"packet_id":39,"packet_size":34,"error_value":104,"category":140697646054272,"context":"BinaryStream read() incomplete.readNoHeader fai","context_truncated":true}
   {"source":"screen","synthetic":false,"response":90,"disconnect_reason":90,"context":"Block","detail":"Minecraft Bedrock.Win,D,,GC-10.0.26100.4351 Err","detail_truncated":true}
   ```

   从入口成功武装到退档前没有设施自测/panic 空窗。
   `real_count=2/synthetic_count=0/lost=0/observation_faults=0`。
   context/detail 都最多 47 字节，不能补写被截断的后半句。
3. **[常量]** SDK 公开头文件
   `refs/levilamina-26.51.7/src/mc/network/MinecraftPacketIds.h`：
   **SetActorData=39**、UpdateAbilitiesPacket=187。不要将 39 写成能力包。
4. **[已有代码证据，须核对报告]** `@client_handle_packet_violation` 的
   `response=TerminateConnection(3)` 分支最终使用 BadPacket(90) 关闭连接。
   `Block` 是 Packet 主题的码字，不表示放置方块错误。此次 callback→screen 的现场链
   已补齐，但不完整读取的具体成因尚未闭合。
5. **仍未知**：size 参数 34 所指的边界、错误值 104 的语义、category 的含义、实际方向、
   哪个字段读取失败、封包生产链，以及是否由自有 mod 的状态更新/生命周期间接影响。
   不要把进程内 category 观测值当成可移植枚举，不要由时间相邻推出因果。
   事发 `packet_capture=false/chat_sent=0`，这些计数不能排除原生同步的间接影响。

原始文件：

- `out/diagnostic-full-final-live.log`
- `out/diagnostic-full-final-violation.json`
- `out/disconnect-20261031-233853.json`
- `out/diagnostic-validation-20261031-231752/natural-disconnect-233853/`
  （日志、dump、原始 spool、事发配置、`integrity.json`）

最新现场 DLL SHA-256：
`9262737efdf5a658e0aa51a6b6c1e85f36bd0e93419823104ebad21aab6e0cbe`。
原始文件在本机 out 中且未入库；不外传 spool 中的玩家标识、用户名和本机路径。

## 本轮诊断顺序

1. 先检查自有代码与公开 SDK 声明：`Flight.cpp` / `FlightModel.h`、
   `Server.cpp` / `TeleportModel.h`、`driver/selftest.py::check_flight`、
   `ViolationWatch.cpp` / `ViolationWatchModel.h`。
   检查状态修改的线程归属、请求/世界代次、对象存续和清理顺序；分别列出确定缺陷和假设。
   不把“先改能力后断开”直接当作静态闭环。
2. 给出已知证据能支持的结论，以及每项未知需要什么额外证据；size/error/category 的解释
   必须注明来源。若自有代码和公开声明不足，只列静态调查问题、候选类型与证据缺口，
   **另行输出逆向提示词，不在本任务执行反编译、地址定位或新增逆向探针**。
3. 只有证实自有代码存在缺陷后才做最小修复；保持外部协议兼容、强能力默认关闭、热路径
   无自有分配/JSON/新增锁及局部异常边界。不要同时重构无关子系统。
4. 回归优先使用独立 native/Python 模型及正常 SDK 操作。若需要现场比较，先明确单一变量、
   起始状态、正常操作序列和终止条件；使用已有只读诊断 API 并保存起始游标、增量与 lost。
   不重复跑完整套件碰运气，不把“没再触发”写成故障已解决。

## 不可跨越的边界

- 不构造、重放或发送畸形网络包，不主动制造协议违规或崩溃；不对第三方服务器测试。
- 不修改校验结果、断开决策、权限或反作弊路径，不通过吞掉 90/104 来掩盖问题。
- 不放宽恢复安全门：只有确认 disconnect_error 或显式 --force 才强停；exiting 不自动强停。
- 不热卸载 DLL，不声称 SDK 任意在途 hook 卸载安全。观察设施已有静态 origin 槽修复，
  不要恢复栈临时输出槽或通用四寄存器 detour 来包装十参数函数。
- 已有失败必须保留；不能降低容差、把 skip 算 pass 或去掉出错测试来取得全绿。

## 已完成的恢复与验证，避免重做

已归档错误现场，恢复原配置并执行 `recover --world "超平坦测试"`，exit 0。
最终回读 **16/16**，含原姿态/能力/效果、背包/装备/选中槽、干燥站稳、输入释放/解锁、
守望关闭、封包能力关闭、配置/DLL 哈希、113 个 crash 无新增/修改。
证据：`out/diagnostic-natural-recover.log` 和
`out/diagnostic-validation-20261031-231752/final-restoration.json`。

最新离线 **362/362**、ruff check/format、mypy、完整 C++ 构建通过；
profiles up to date、roles 50/57。诊断专项 **21/21**，必需 skip 对照 **2/3，预期 exit 1**。
首轮完整 **1053/1055** 的位置失败已定位到自检开放水源泄漏，固定封闭水池专项 **11/11**；
第二次完整在更早的 flight 中止，水池修复尚未在完整套件覆盖到。历史 **992/992**
不能代表当前全绿。当前尚未提交或推送。

## 交付要求

输出证据分级的结论、仍未知项、自有代码最小改动（如有）及回归结果；需要新增逆向时只交付
后续提示词。同步 `mod/README.md`、`docs/mod-internals.md` 和 `docs/reliability-repairs.md`。
未定位成因或完整现场仍失败时如实标记，不提交为全绿，不自行推送。
