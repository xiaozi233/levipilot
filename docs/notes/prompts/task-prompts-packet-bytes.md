# 开发任务提示词 · 第四轮续二（H1-B 封包字节：先解导入桩，再决定层）

> 独立成册，**取代** [`task-prompts-packet-tap.md`](task-prompts-packet-tap.md) 里「阶段 B」的三分支选择。
> **公共前置见 [`task-prompts.md`](task-prompts.md) §2**，派活时一并粘上。

## 背景：H1-A 的结论只是"符号层不可达"，不是"函数不可达"

H1-A 的探针报回 `peer_hooked=false / peer_reason=local_network_peer_symbol_unavailable`，
代码注释写着 `LocalNetworkPeer::$sendPacket is dllimport-only in this build`。
**dllimport 意味着它有一个导入桩（`FF 25` 跳到 IAT 槽），真函数在别的模块里** ——
符号解析不到，不等于地址拿不到。而仓库里已经有两个现成零件可以解开它：
`Resolve.cpp` 的 `followThunk()` 和 `hook_va`。

所以本册的顺序是：**先试着把 B1 救活（拿线缆级字节），救不活才走 B2（服务端接收侧）**。

## 目标

1. 解析出 `LocalNetworkPeer::sendPacket(std::string const&)` 的**真实函数地址**（含所属模块）；
2. 用只计数探针确认它在单机里**是否真有流量**；
3. 按结果落地 B1 或 B2，产出 H2/H3 需要的东西：`(seq, id, size, bytes 前 N 字节)` 的配对，
   以及"真按 ctrl+w vs `set --sprint 1`"的 id 序列对比。

---

## 步骤 1：解析导入桩（三种手段，按顺序试，全部记录下来）

```text
手段 a（首选）：LeviLamina 给的那个地址 → followThunk()
  anchor = ll::memory::toFuncPtr(&LocalNetworkPeer::$sendPacket)   // 若可用
  real   = followThunk(anchor)
  若 real != anchor ⇒ 已经拿到真地址；若相等，说明 toFuncPtr 给的就是桩本身，继续手段 b。

手段 b：读桩的 IAT 槽
  桩形态是 FF 25 <disp32>；real = *(anchor + 6)（注意 64 位）
  再把 real 交给 followThunk 二次确认（可能还是中间桩）。

手段 c：走对象
  在已装的 LoopbackPacketSender::sendToServer 钩子里，取出 sender 持有的 connection/peer 指针，
  读它的 vtable，用 IDA 给出的 sendPacket 槽位下标取出真函数地址。
  这条不依赖任何符号，是最"物理"的方式；缺点是每次发版要重新确认槽位。

校验（三条都必须过，否则中止并报告）：
  - VirtualQuery(real) 是已提交、可执行；
  - 记录 real 所属模块（模块名 + RVA，因为真函数可能在别的 DLL 里，ASLR 下不能存绝对地址）；
  - real 不等于 anchor（否则说明还停在桩上）。
```

## 步骤 2：只计数探针（这一步才是决策点）

```text
用 hook_va 在 real 上装一个**只计数、不读参数**的探针（sample=1）。
然后依次做：走 2 秒 / 跳一次 / 开一次门 / 攻击一次 / 发一条命令，
对每个动作单独清零计数，打印：
  {action, sendToServer_calls, sendPacket_calls}

决策（把这张表写进提交信息）：
  sendPacket 与 sendToServer 同量级           ⇒ 走步骤 3a（B1，线缆级字节）
  恒为 0                                      ⇒ 走步骤 3b（B2，服务端接收侧）
  只在部分动作出现                            ⇒ 记录哪些动作，B1+B2 混用并说明覆盖范围
```

## 步骤 3a（B1）字节采集

```text
在 real 的钩子里读 sendPacket 的参数（const std::string&），把前 min(size, max_bytes) 字节
拷进环，与 H1-A 的 seq 关联。

必须处理的坑：
1) std::string 的 SSO：布局是 {ptr@0, size@0x10, cap@0x18}；cap <= 0xF 时数据在对象内联区（+0），
   此时 data != ptr。不要假设它总是堆指针，读之前先看 cap。
2) 缓冲区可能被复用/COW：只在本次调用内拷贝，绝不持有指针。
3) 先人工看一眼前 16 字节：如果能解析成 "varint packet id + payload"，说明是原始包；
   如果看起来是压缩/加帧的，立刻停，改走 3b（别在错误层次上继续建东西）。
4) 两个钩子的 seq 必须来自同一个全局计数器，否则关联会错位。
```

## 步骤 3b（B2）服务端接收侧

```text
钩服务端处理入口（ServerNetworkHandler::handle 或等价的包分发函数，用 RTTI/符号定位），
那里拿到的是带类型的 Packet&：用 offsets 表读字段，别按字节猜。

注意：
- 此钩子在服务端线程（H0.1 已经证明权威状态由它写），钩子里绝不能碰客户端对象；
- 看到的是"服务端收到的"，与客户端发出的可能差一个 tick，记录里带 tid + tick 以便对齐；
- 只读包字段 + 写环，零分配无锁。
```

## 步骤 4（仅当 3a/3b 都不可行）：B3 兜底

```text
只对 1–2 个白名单 id，在钩子里用预分配 BinaryStream 调 Packet::write 序列化。
代价必须显式记录：有分配、个别包的 write 会改内部状态 ⇒ 关闭时零调用、事件里标 serialized=true。
不要用这条做全量 tap。
```

---

## 纪律（与前三册一致，不重复解释）

- 钩子体：零分配、无锁、只写定长结构；格式化/解码留给管道线程或 Python。
- `injected` 字段：H2 之后注入的包会经过同一个 tap，必须能区分（H3 的对照依赖它）。
- denylist 硬挡：登录/握手/加密/资源包/子客户端登录。
- 只在 in_world 生效；`allow_packet_capture` 关闭时零开销。
- 环 MPSC（发送可能来自客户端线程也可能来自服务端线程），复用现有 seq 发布范式。

## 验收

1. **地址解析报告**：`{anchor, real, module, rva, 用了哪种手段, 三条校验结果}` 一行一包，写进提交信息；
2. **决策表**：五个动作的 `{sendToServer, sendPacket}` 计数（这是本册最重要的产出，比代码更重要）；
3. 若走 B1：`out/packets-<ts>.jsonl` 里 20 条 `{seq, id, size, hex(≤128)}`，且 id 与 H1-A 的同 seq 记录一致；
4. 若走 B2：同样 20 条，另标注"与 A 侧 tick 差"（0 或 1）；
5. **H3 的输入**：真按 ctrl+w 与 `set --sprint 1` 各 60 tick 的 id 序列对比（有无差异都写清楚）；
6. tick 稳定 20 tps，环 `dropped=0`；panic 后 tap 关闭、游戏正常。

## 不要做

- **不要直接 hook 导入桩**：桩被所有调用者共享且极短，钩它既不准也不安全 —— 钩真函数。
- 不要在探针阶段读参数（先把"有没有流量"测清楚，再谈读什么）。
- 不要因为"符号不可用"就跳到 B2 之前不做步骤 1 —— 那正是本册要纠正的默认反应。
- 不要把绝对地址写进代码或 offsets 表；只存 `模块名 + RVA`。
