# 接手提示词：飞行专项再次出现 BadPacket(90)，packet 187 / size 48

请在 `D:\mcbe-harness` 接续调查，先读 `AGENTS.md`、
`docs/notes/badpacket-setactordata-20261031.md` §36–§43 及
`docs/notes/badpacket-187-20261101.md`。使用 `py -3.11`，不要启动子 agent，
不要自行推送，不修改游戏校验结果或吞掉断开。保留其他会话改动。

## 本次任务

先判断现有原始数据是否足以定位 2026-11-01 22:20:40（北京时间）的失败包；
不够就明确缺什么，再设计最小采集。新增逆向由接手会话完成，原会话已停止逆向推进。
不要把时间相邻或一次失败归因为此次重构。

## 已完成，不要重复

- Python 管道遗弃资源回收已提交 `8f55472`：离线 376/376、真实重连专项 11/11。
- 旧 BadPacket 结论以证据册 §36–§43 为准：队列深拷贝、源 string size、
  无帧长度前缀；「声明 23」「邻居 payload 混入」已撤回。
- 实体/飞行统一 `ServerPlayerContext` 的候选完成了编译、相关回归 25/25 和全量
  离线 376/376，但现场失败；**未提交，已从工作树撤回并恢复原 DLL**。
  补丁在 `out/server-context-candidate.patch`，候选 DLL/PDB/manifest 在
  `out/server-context-candidate/`。不要默认应用候选补丁。

## 新现场事实

命令：设置 `LEVIPILOT_VIOLATION_DUMP=out/server-context-flight-violation.json` 后运行
`py -3.11 driver/selftest.py --flight-world "超平坦测试" --require-complete`。

- 候选 DLL SHA256：`B4E3BECE70BBED3473BAE7D3A08549B3E31C36A620C797A6C5D70A0F91BDC916`。
- 游戏 pid 11320；错误页时间 `2026-11-01T14:20:40Z`。
- 同 tick 1341 / tid 39772：callback `response=3, packet_id=187, packet_size=48,
  error_value=104`，随后 screen `disconnect_reason=90, context=Block`。
- 完整回调原文：`BinaryStream read() incomplete\nreadNoHeader failed! packetId: 187`。
- 专项结果 **28/29，1 fail、0 skip**；最后通过 `flight_server_cpp_unique`，
  后续 `fly: not_in_world`，清理错误保留为 note。未走到真实退出/重进部分。
- 观察 2 条真实、0 synthetic、lost=0、observation_faults=0；入站 sample=4，
  1296 次入口命中、324 条采样，不能声称一定有失败原帧。
- 常规导出起初因给游戏相对路径产生 `open_failed`，错误回执又被下一次 packet_drain
  消费。后来通过绝对路径及 request/expected-event 直接补取全部现有环；不要把这个
  导出错误当 BadPacket 的原因。自有导出路径已修，专项入口为 `--diagnostic-export-only`。

## 原始文件（均在本机 out，未纳入 Git）

- `out/server-context-flight-live.log`
- `out/server-context-flight-violation.json`（回调、页面、完整文本、观察统计）
- `out/server-context-disconnect-20261101-222214.json`
- `out/server-context-failure-inbound.json`（前 256 条）
- `out/server-context-failure-inbound-tail.json`（后 68 条）
- `out/server-context-failure-inbound-all.json`（游戏写的环快照，先检查覆盖范围）
- `out/server-context-failure-packet-bytes.json`（241 条）
- `out/server-context-failure-packet-index.json`（385 条，按 seq 关联 tick）
- `out/server-context-failure-violation-text.json`
- `out/server-context-failure-stats.json`
- `out/server-context-before/`（测试前状态、背包、配置、DLL 与 crash 清单）
- `out/server-context-evidence-manifest.json`（这些现场文件的 SHA256）

## 顺序与判定标准

1. 先用已有 Python 解码辅助检查保存文件；不要只读前 256 条就说原帧不存在。
   按 tick/tid/id/size/seq 区分原始解析输入、SDK 重序列化 body 和错误上下文文本。
   入边行里的 packet_id=2 与 context_hex 头的 varint id 不一致；先校验字段语义，
   不能把错误字符串或 teardown 后的切片误认作 packet187 的 48 字节原帧。
2. 有精确原帧才逐字段对照；若没有，报告 sample=4 的缺口。下一轮使用既有
   `inbound_sample(1)`，但先核对当前 parse hook 签名、参数生命期和采集点正确性。
3. 静态优先确认 `UpdateAbilities(187)` 在当前版本的读写路径及 104 的具体触发条件，
   不把 SetActorData 的 CerealOnly 路径无条件套用到 187。若需动态探针，先给出
   目标签名、typed wrapper、固定环载荷与可验证的预期，不用通用四寄存器 hook
   代替复杂签名，不允许热路径分配/锁/JSON。
4. 比较基线 DLL 与候选补丁的身份解析差异，设计一次只改变该因素的对照。
   当前没有这样的对照，不能据此排除 mod 间接影响，也不能定为候选重构引入。
5. 保留首错与 raw evidence，不重复已有 20/7 进世界窗口排除工作。只有证据闭合后
   再给自有代码的最小修复；需要更深逆向时继续按项目账本记录。

每个结论区分 `[代码]`、`[实测]`、`[推断]` 和 unresolved；没有真实样本就明确未解决。
现场验证后回读姿态/能力/背包/输入并恢复原配置。探针用 `with ModClient()` 或 finally close。
不要强停 IDA；游戏只在确认 disconnect_error 或显式 force 时放弃保存门禁。
