# 开发任务提示词 · 第四轮（封包层 H0–H4）

> 独立成册。**公共前置（仓库约定 / 架构硬约束 / 脚本纪律）见 [`task-prompts.md`](task-prompts.md) §2**，
> 派活时一并粘上。
> 本册补的是当前唯一没有实现的逆向能力：**封包观察与注入**。它同时是两条遗留未解项的钥匙
> （疾跑 1.3× 的来源、命令派发的 RPC）。强能力一律默认关闭、显式开启、留审计日志。

## 为什么先做 H0

前三轮留下的能力（watch / probe / hook_va / ECS / offsets）**代码里都有，但"可用性"只在
selftest 的常规路径上验过**。封包层要建立在这些原语之上，所以先把这四项固化成 selftest，
免得后面排查时分不清是"封包没抓到"还是"底层原语本身没生效"。

## 总览

| ID | 优先级 | 任务 | 依赖 |
| --- | --- | --- | --- |
| H0 | P1 | 能力可用性四项验收（落成 selftest） | 无 |
| H1 | P1 | 被动封包 tap（只观察，不解码） | H0、T4 记录环 |
| H2 | P2 | 封包注入：`CommandRequestPacket`（替代 WM_CHAR 命令通道） | H1 |
| H3 | P2（条件） | `PlayerActionPacket` 注入（疾跑收尾） | H1 + G3 的结论 |
| H4 | P3 | Python 侧封包注册表（id → 名字/字段） | H1 |

> **不可越的线（本册所有任务共用）**：只碰游戏玩法包。登录/握手/加密/资源包/子客户端登录这类
> 包**一律禁止** tap 与注入，用显式 denylist 挡住，不靠"我们不会传"这种自觉。

---

## H0（P1）能力可用性四项验收

```text
目标：把前几轮"代码里有"的能力，变成四条可重复的 selftest 断言。

背景：selftest 92/92 覆盖的是常规路径；而我关心的四条恰好是"出错/边界路径"，
之前一次都没被验证过。封包层要建在这些原语上，先确认它们真能用。

范围：driver/selftest.py 增加四个检查；mod 侧只在必要时补只读事件字段，不改行为。

四项（每条都要打印一行可粘贴的证据）：
1) watch 抓写者：对玩家 StateVector 的 vy 字段（偏移从 offsets 表读，不要写死）设 write 观察点，
   走/跳 30 秒，要求 ≥1 条命中；用 code_hex 反汇编定位那条 store，并打印"命中指令 == 已知写者"。
2) probe × blocks：对同一坐标读方块，probe 的名字/哈希与 blocks 一致；额外打印 mBlockId，
   记录 water / air / stone 三者在 51 版的值（用于验证结构布局跨版本不变这个假设）。
3) ECS × hook_va 交叉：ecs_component 读出的条目（数量、stride）与 hook_va 在某入口抓到的
   条目一致；stride 断言与 offsets 表一致。
4) 落盘三连：record_start → 10 分钟 → 分别用 leave_world / panic / taskkill /F 收尾，
   断言 spool 里 tick 序号连续且单调；三种收尾各打印"最后一条 tick 与收尾事件"。

不要做：不要为了让断言通过而放宽判据（比如把"≥1 条命中"改成"允许 0"）；
不要把四项写成 skip-on-failure（失败必须红）。

验收：selftest 全绿且四项各输出一行证据；任一项失败时给出明确原因（不是 traceback）。
```

---

## H1（P1）被动封包 tap —— 只观察，不解码

```text
目标：能看到客户端发出的玩法包（id、大小、前 N 字节、tick），且不拖慢 tick。

背景：单机是集成服务端同进程，客户端的包经 LoopbackPacketSender 交给本进程的服务端，
所以 tap 一个点就能看全；这也是我们一直缺的那块观测面。
注意：绝不要在钩子里解码——原始字节进环，解码放 Python（沿用"热路径只写定长数据"的纪律）。

范围：新增 Packets.cpp/.hpp（或并入 RecordRing）；Commands.cpp 加命令；client/cli；
harness.json 加 allow_packet_capture（默认 false）；offsets 表加 packets 段。

实现要点：
1) 锚点：从 ClientInstance/PacketSender 取 LoopbackPacketSender，或用 RTTI
   （.?AVLoopbackPacketSender@@）定位；再找 sendToServer(Packet&) 的 VA。
   锚点写进 offsets 表（anchors.packets.send）、签名按 T7 的 24 B 格式补。
2) 钩子体：取 Packet::getId()（vtable 索引由 IDA 给出，写进 offsets），把
   {tick, id, size, first 128 B} 写进已有的二进制环（或一条并行环），零分配、无锁。
3) 命令：packet_capture {on|off, ids?, max_bytes?, sample?}、packet_drain {since}、
   计数进 status。sample 默认 1（全采），可降到 1/N。
4) 过滤：默认只允许"玩法包白名单"；登录取向的 id 走 denylist（Login/
   ClientToServerHandshake/NetworkSettings/ResourcePack*/SubClientLogin 等），
   即使调用方传了 ids 也必须被 denylist 拦下并回 denied_id。
5) 只在 in_world 生效；管道线程只读队列，不碰游戏对象。

不要做：不要在钩子里做 JSON/字符串/分配；不要 tap 收发两端（先只 send，recv 留给下一册）；
不要默认开启。

验收：
- 走动/跳/开门/攻击各做一次 → drain 里出现对应的包，id 与大小稳定；
- 按 ctrl+w 与 `set --sprint 1` 各跑 60 tick → 打印两次的 id 序列，能看出差异（或明确"无差异"）；
- allow_packet_capture=false 时零开销、零记录；
- 开启后 tick 仍稳定 20 tps，环无丢失（status 里 dropped=0）。
```

---

## H2（P2）封包注入：`CommandRequestPacket`

```text
目标：在后台、不靠窗口消息，把服务端命令发出去；替换现在 WM_CHAR 那条通道。

背景：命令通道现在是 postmsg 投递按键+字符，结果是"不抢焦点但会引入约 1.2 s 卡顿，
且期间位置不被积分"（旧文档已记录）。封包层做好后可以直接发 CommandRequestPacket。

范围：Packets.cpp 的注入路径；Commands.cpp 加 chat_send；client/cli；配置门。

实现要点（推荐"模板复用"，不要从零构造对象）：
1) 先用现有 WM_CHAR 通道发一条 /say test，在 H1 的 tap 里抓到该 CommandRequestPacket 的原始字节；
2) 把这份字节当模板：复制一份，改命令字符串字段与长度字段（长度字段偏移从 IDA 写入 offsets），
   其余字段原样保留；
3) 调用 sendToServer 发送；对象生命周期只在本 tick 内，不跨 tick 持有指针；
4) 门：allow_packet_inject 默认 false；本任务只允许 CommandRequestPacket 这一个 id，
   其它 id 一律 denied_id；
5) 旧的 WM_CHAR 通道保留为回退开关，不要删。

不要做：不要构造复杂对象（vtable/ctor 有风险）；不要发非命令类包；
不要在 tick 线程里做字符串拼接（命令字符串在管道线程准备好，tick 只做一次拷贝发送）。

验收：
- 后台（游戏失焦）发 /say hi → 服务端回显/日志可见，且没有 1.2 s 卡顿（对比旧通道的 tick 间隔）；
- /fill、/tp 带 @s 与 ~ 语义正确；
- 注入后 tick 间隔 p99 与不发命令时一致；
- panic 之后注入通道关闭、游戏正常。
```

---

## H3（P2，条件项）`PlayerActionPacket` 注入 —— 疾跑收尾

```text
启动条件（必须同时满足）：
  1) G3 的三组对照结论是"差异在服务端/输入请求层"（C ≈ B < A）；
  2) H1 的 tap 里能看到"真人 ctrl+w"与"注入 sprint 标志"在 PlayerActionPacket 序列上不同。

目标：把疾跑起停作为动作包发出去，复现 0.2805 格/tick。

范围：Packets.cpp 注入路径（复用 H2 的模板机制）；scenarios/sprint-compare.json 增加
一组 D（事件 + 动作包）。

实现要点：
- 起/停疾跑各是一个 action id（从 H1 的 tap 与真人按键对照得到，不要猜）；
- 包内字段（runtime id / 位置 / face）一律从模板拷贝，只改 action 与必要的坐标；
- 与 G1/G2 的按键注入组合成 D 组；仍保持 20 tps；
- 单包发送一次，不重复刷。

不要做：不要每 tick 重发（会触发服务端限流/校验）；不要绕过 H2 的配置门与 denylist。

验收：
- D 组稳态逐 tick 位移 ≥ 0.2805 且与 A 组（真人）差异 < 1%；
- 停止疾跑后回落到 0.2158；
- 复跑 3 次稳定；若仍不达标，打印"服务端拒绝了什么"（用 H1 的 tap 看回来的包）。
```

---

## H4（P3）Python 侧封包注册表

```text
目标：把 drain 出来的原始字节变成人能读的字段。

范围：driver/harness/packets.py；offsets 表加 packets 段的字段布局；docs 补一页。

实现要点：
- 注册表：id → {name, fields:[{name, offset, type, size}]}；每项标注"来源：IDA / 实测"；
- 先只解三个：CommandRequestPacket、PlayerActionPacket、MovePlayerPacket；
- 未知 id 打印 id/size/hex，不报错；
- 解码与游戏无关（纯 Python），单测覆盖：给定 hex → 期望字段。

验收：
- 用 H1 抓到的真实 hex 解码，字段值与内存侧观察一致（至少 runtime id、动作类型、坐标）；
- 未知 id 稳定降级；单测通过。
```

---

## 第四轮回归门禁

沿用前三轮，另加：

1) **denylist 生效**：即使显式请求，登录/握手/资源包类 id 必须被拒并回 denied_id；
2) **零开销**：`allow_packet_capture=false` 时与未装钩的基线逐 tick 一致；
3) **可恢复**：panic 之后 tap 关闭、注入关闭、tick 与真实输入均正常；
4) **只 send 不 recv**：本册不 hook 接收路径（留到下一册，避免一次改两端）。

改完在 `mod/README.md` 的「实测 / 限制」补一行（尤其是"命令通道是否还需要 WM_CHAR"这一条）。
