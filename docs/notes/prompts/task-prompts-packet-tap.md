# 开发任务提示词 · 第四轮续（H1 封包 tap · H0 之后修订版）

> 独立成册，**取代** [`task-prompts-packets.md`](task-prompts-packets.md) 里 H1 的草图。
> **公共前置见 [`task-prompts.md`](task-prompts.md) §2**，派活时一并粘上。
>
> 为什么重写：H0 的实测改变了三个前提 ——
> ① 权威状态（`StateVector.vy`）由**集成服务端线程**写，不是 tick 线程 ⇒ 发送侧同样会跨线程；
> ② `thread="all"` 装 DR 会崩 ⇒ 任何"全线程"思路都要先算代价；
> ③ 按 runtime id 枚举客户端 registry 与服务端线程竞争会崩 ⇒ 观测点必须"只读已捕获指针/只读不可变数据"。

## 目标

看见客户端发出的玩法包（**身份、顺序、大小**），为 H3（疾跑定位）提供判据，为 H2（命令注入）提供模板字节。
分两阶段 —— **阶段 A 不碰字节**，先回答"该在哪一层拿字节"。

---

## 阶段 A（先做，约 2 小时）：只读身份，不做字节

```text
目标：用最小代价拿到 (tick, seq, runtimeId, 线程) 四元组，并能回答"字节该从哪拿"。

范围：新增 Packets.cpp/.hpp；Commands.cpp 加 packet_capture / packet_drain；
client/cli；harness.json 加 allow_packet_capture（默认 false）；offsets 表加 packets 段。

实现要点：
1) 锚点：LoopbackPacketSender::$sendToServer(Packet&)（LeviLamina 已导出该符号）。
   锚点与 24B 签名按 T7 的格式进 offsets 表，不要硬编码。
2) 钩子体只做三件事：读 Packet::getRuntimeId()、读全局 seq（fetch_add）、把
   {tick, seq, runtimeId, tid} 写进已有的二进制环。**零分配、无锁、不调虚函数以外的任何东西。**
3) 严禁在钩子里调 Packet::getName() —— 它返回 std::string，等于每次发送一次堆分配。
   id → 名字的映射交给 Python（H4 注册表）。
4) 记录里预留 injected=false 字段（H2 注入时置 true，H3 的对照实验要靠它区分来源）。
5) 环必须是 MPSC 安全（发送可能来自客户端线程也可能来自服务端线程）。
   直接复用 Watch/RecordRing 的 fetch_add + seq 发布范式，不要另写一套。
6) 过滤与降采样：id denylist（登录 / 握手 / 加密 / 资源包 / 子客户端登录）必须硬挡，
   即使调用方传了 ids 也回 denied_id；sample 默认 1，可 1/N。

★ 阶段 A 的唯一"科研任务"——先测这件事，再决定阶段 B 走哪条：
   额外加一个只计数、不写字节的探针：NetworkPeer::sendPacket(std::string const&) 的调用计数。
   然后各做一次：走 2 秒、跳一次、开一次门、攻击一次，打印两个计数的比值。
   - 若 NetworkPeer 计数 > 0 且与 sendToServer 同量级 ⇒ 走 B1；
   - 若恒为 0 ⇒ 本地回环绕过了序列化 ⇒ 走 B2；
   - 若只在某些动作上出现 ⇒ 记录是哪些动作，按 B1+B2 混用。

不要做：不要在这一阶段读/存字节；不要读 Packet 的任何字段（对象生命周期只在本调用内，
        读多了就是给自己埋悬垂指针）；不要 tap 接收侧；不要注入。
```

---

## 阶段 B（按阶段 A 的测量结果选一支，只做一支）

### B1 —— NetworkPeer 有流量：字节 tap + seq 关联

```text
做法：在 NetworkPeer::sendPacket(std::string const&) 上也装一个 tap，按 seq 与 A 关联。
      A 记 (seq, id)，B 记 (seq, size, 前 128B)；Python 侧按 seq 拼成 (id, size, bytes)。
断言：两者都触发的包，size 必须相等；不等就停下来报告，不要"取其一"。
注意：NetworkPeer 层可能含传输层帧（压缩/加密），先打印前 16 字节人工确认；
      若发现是压缩后的，改走 B2。
```

### B2 —— 本地回环不经过 NetworkPeer：改钩服务端接收侧

```text
做法：钩服务端处理入口（ServerNetworkHandler::handle 或等价，用 RTTI/符号定位），
      那里拿到的仍是带类型的 Packet&，字段按 offsets 表读。
优点：单机服务端同进程，这条路一定通；缺点：看到的是"服务端收到的"，
      与"客户端发出的"在极端情况下可能差一个 tick，记录里要带 threads 与 tick 以便对齐。
注意：此钩子在服务端线程，绝不能在里面碰客户端对象；只读包字段 + 写环。
```

### B3 —— 兜底：仅对白名单序列化

```text
做法：只对 1–2 个白名单 id，在 tap 里用预分配的 BinaryStream 调 Packet::write，
      把序列化结果拷进环。
代价必须显式记录：序列化有分配、个别包的 write 会改内部状态 ⇒ 白名单 + 关闭时零调用 + 事件里标注 serialized=true。
不要用这条做全量 tap。
```

---

## 三条硬约束（任何一支都适用）

1. **注入与观察必须可区分**：H2 之后注入的包也要经过同一个 tap，必须带 `injected=true`；
   否则 H3 的对照实验里分不清"服务端发的"和"我发的"。
2. **denylist 硬挡**：登录/握手/加密/资源包/子客户端登录类 id，任何参数都不能放行。
3. **钩子体纪律**：零分配、无锁、只写定长结构；一切格式化/解码在管道线程或 Python。

## 验收（阶段 A + 你选的那一支）

- 走 2 秒 / 跳 / 开门 / 攻击各一次 → drain 里出现对应包，`runtimeId` 稳定、`seq` 单调；
- 打印阶段 A 的两个计数器比值（这就是设计决策的证据，要留在提交信息或文档里）；
- `allow_packet_capture=false` 时：零记录、与未装钩基线逐 tick 一致；
- 开启后 tick 仍稳定 20 tps，`status` 里环 `dropped=0`；
- 给出 H3 需要的输出：**真按 ctrl+w 与 `set --sprint 1` 各 60 tick 的 id 序列对比**（有无差异都说清楚）；
- panic 之后 tap 关闭、游戏与真实输入正常。

## 不要做

- 不要两端同时 tap（先发送侧，接收侧最多按 B2 二选一）；
- 不要为了"看字节"而在钩子里序列化全量包；
- 不要在加载/菜单期 tap；
- 不要用 `thread="all"` 这类全线程操作来"确保不漏包" —— 发送是全局钩子，不需要按线程装 DR。
