# SetActorData 反序列化失败（BadPacket 90）：静态结论

日期 2026-11-01。对象：2026-10-31 23:38:53（北京时间）自然断开时的第一条真实违规记录。
证据文件：`out/diagnostic-full-final-violation.json`、`out/disconnect-20261031-233853.json`。

## 1. 现场（`[实测]`，两条同 tick 861 / 同 tid 30828）

```json
{"source":"callback","synthetic":false,"response":3,"packet_id":39,"packet_size":34,
 "error_value":104,"category":140697646054272,
 "context":"BinaryStream read() incomplete.readNoHeader fai"}
{"source":"screen","synthetic":false,"response":90,"context":"Block",
 "detail":"Minecraft Bedrock.Win,D,,GC-10.0.26100.4351 Err"}
```

## 2. 链条（每一环的证据等级）

| 环节 | 结论 | 证据 |
| --- | --- | --- |
| 包 id | **39 = SetActorData**（不是能力包） | `[常量]` `refs/levilamina-26.51.7/src/mc/network/MinecraftPacketIds.h` |
| 失败性质 | 包体读取未完成：`BinaryStream read() incomplete` + `readNoHeader failed! packetId: {}`（后者格式串在 `sub_1417260E0`，33 字节，用 packetId 填充） | `[代码]` 反编译 `0x1417260E0`；`[实测]` context 前缀与两者拼接一致 |
| 判定 | `response = 3 = TerminateConnection` | `[实测]` callback 记录 |
| 断开 | 该分支用 `BadPacket(90)` 关闭连接，错误页 codeword `Block` | `[代码]` `@client_handle_packet_violation` `0x1412FA0F0`（`0x1412FA243`）；`[实测]` screen 记录 |
| 方向 | 客户端**接收**侧（单机即集成服务端发出） | `[代码]` 该函数是 `ClientNetworkHandler` 的入站违规回调；`[实测]` 与 90 的设置点一致 |

## 3. `error_value = 104` 的语义（本轮新闭合）

**`104 = std::errc::bad_message`（generic 类别，即 UCRT `EBADMSG`）**，意思是“数据不是有效的”。

推导链（可复用，全部静态）：

1. 记录里的 `category` 是 `&std::error_code::category()`，即类别对象地址。用当轮
   `module_base`（`out/diagnostic-full-final-violation.json` 的
   `observation[].reply.module_base = 0x7FF6AACC0000`）换算：
   `rva = category - module_base = 0xE6ABB80`，落在 `.rdata`。
2. 该地址第一个 qword = `0x14E6ABB90`（静态 VA），即真正的类别对象在 `rva 0xE6ABB90`；
   其后是 vtable：`0x1400FB550`（dtor）、`0x140108400`（`name()`）、
   `0x140108410`（`message(int)`）。
3. `name()` 直接返回字符串 **`"generic"`**；`message(int)` 走 `std::_Syserror_map(a3)`
   —— 正是 MSVC 的 `std::generic_category()`。
4. 因此 104 是 **POSIX/`std::errc` 域**的值，不是 Win32 错误码。
   Windows SDK `10.0.26100.0/ucrt/errno.h`：`#define EBADMSG 104`，
   对应 `std::errc::bad_message`。

> 注意：类别指针是**进程内地址**，换了进程/基址就没有意义；必须和当轮 `module_base`
> 一起记录才能换算。文档里不要写成可移植枚举。

## 4. 仍然 unresolved（不猜）

- **34 字节在读侧哪一步不够**：需要 `SetActorDataPacketPayload` 的读取实现
  （`mId` → `mPackedItems`（type id + 值） → `mSynchedProperties`（int/float 条目）
  → `mTick`）。当前只知道它读到 EOF/bad_message。
- **写侧是谁**：`SetActorDataPacketPayload(ActorRuntimeID, SynchedActorDataEntityWrapper&,
  PropertyComponent*, uint64 tick, bool packAll)` 的调用点，`packAll` 取值，
  以及是否由 `Player::setAbility` 间接触发（我们调用的是公开 API）。
- **能力变更与它的关系**：时间相邻已被证据否定为“依据”，仍需调用链才能定因果。
- 已知的自有代码面（`[代码]` 复核）：`Flight.cpp` 只在 `queueServerTask` /
  `queueClientTask` 内写 `setAbility`，断言路径在 tick 线程写 LocalPlayer；
  我们从不写 `SynchedActorData` / `DataItem` / `PropertyComponent` / `SetActorDataPacket`；
  事发 `packet_capture=false`，tap 未调用 `packet.write`。

## 5. 下一步（一条命令级的入口）

`0x1416AB0E0` 是入站分发（其中 `cmp r13d, 0x9c` 特殊处理 `PacketViolationWarning(156)`，
`r13d` = 包 id）。包体读取是一次间接调用（`0x1416ABD8B`），失败结果在 `rbp+0x210`，
成功搬到 `rbp+0x380`；失败分支 `0x1416ABEE4 → 0x1416ACBDF` 用 `r13d` 填
`readNoHeader failed! packetId: {}`。

要钉住读侧，需要：从分发里的 `createPacket(id)` 拿到 id 39 的具体类 → 其 vtable 的
`read` 槽 → 逐字段核对宽度（尤其 `DataItem` 的 type→宽度表、`PropertySyncData` 的计数）。
这条路径尚未执行完，本轮不猜。

已排除的做法（别重复）：

- `"BinaryStream read() incomplete"`（file `0xec577b0` / VA `0x14EC587B0`）在 IDA 里**没有
  xref**，本地 rip-relative 扫描（`48/4C 8D/8B` 全 `.text`）也**没有**任何引用，
  `.rdata` 里也没有指向它的 8 字节指针 ⇒ 该字面量在本构建里查不到生产点。
  不要再用“找这条串的引用”当入口。
- 反过来，`"readNoHeader failed! packetId: {}"`（`0x14EBAE980`）有唯一引用：
  `sub_1417260E0`（33 字节格式串 → `fmt`），它由入站分发 `sub_1416AB0E0` 在失败分支
  `0x1416ABEE4 → 0x1416ACBDF` 用 `r13d`（包 id）调用。

可复用的取证技巧：记录的 `category` 是进程内地址，配合当轮 `module_base` 求 RVA，
再按 `std::error_category` 的 vtable（`[dtor, name, message, default_error_condition]`）
读 `name()`/`message()`，就能把任何 `std::error_code` 的域和取值离线解释出来。

## 6. 读侧已闭合到函数级（2026-11-01 追加）

`[代码]` `SetActorDataPacketPayload` 的虚表与实现（静态 VA，`image_base=0x140000000`）：

| 槽 | 成员 | 地址 | 说明 |
| --- | --- | --- | --- |
| +0x08 | `getId()` | `0x143DF6E20` | 返回 **39** |
| +0x10 | `getName()` | `0x143DF6E30` | `"SetActorDataPacket"` |
| +0x18 | `getMaxSize()` | `0x140C44940` | 10 MiB |
| +0x20 | `checkSize()` | `0x1416C20F0` | 超限报 115（同类别） |
| +0x28 | `writeWithSerializationMode` | `0x143DF6E50` | `if (mode == 5)` 走 cereal |
| +0x38 | `write(stream)` | `0x140C43F60` | |
| +0x48 | `read(stream)` | `0x143DF7060` | 调 `_read`，失败时按流状态改成 104 |
| +0x60/+0x68 | get/setSerializationMode | `0x143788CD0`/`0x143788CE0` | 读写 payload`+136` |

- `read(stream)` 的失败分支（`0x143DF7362`）就是 `v28 = 104`：
  条件等价于 SDK 公开头的
  `ReadOnlyBinaryStream::ensureReadCompleted()` —— **`mHasOverflowed || mView.size() != mReadPointer`**
  ⇒ `std::errc::bad_message`。所以 104 的语义是“**读越界或有剩余字节**”，不是普通的 EOF。
- `_read(stream)`（`0x143DF7CC0`）开头 `if (getSerializationMode() != 5)` 会用**同一条** 104
  但配另一条消息（“Packet defined using SerializationMode::CerealOnly tried to serialize
  (read) using a different mode: {}”）。现场 context 是 `BinaryStream read() incomplete…`，
  说明**模式门禁在读取侧通过了**，失败发生在随后的字段读取里。

### 构造函数把模式钉成 CerealOnly

`[代码]` 构造 `SetActorDataPacket`（`0x1416B4320`）时 `mov dword ptr [rdi+98h], 5`：
`0x98 = payload(+0x10) + 0x88`，正是 `getSerializationMode()` 读的 `payload+136`。
即**新建实例默认 5 = CerealOnly**，读写都会走 cereal，表面上是自洽的。

### 由此得到的候选机制（按当前证据排序，均未定案）

1. **写入用了另一种模式**：`writeWithSerializationMode(stream, ctx, override)` 的第四参带
   “可选 override”（`a4 & 0x100000000`），上层可以按一次发送覆盖模式，而**不改对象自己的字段**；
   读取端用的是新建实例的默认 5。若发送侧被 override 成经典二进制，接收侧按 cereal 解析
   同一段字节 ⇒ 计数读成垃圾 ⇒ 越界 ⇒ 104。`setSerializationMode` 只有虚调用点（无直接 caller），
   与“上层按需设置”一致。
2. **载荷被截短**：写入侧实际写出少于读取侧 schema 所需的字节（34 字节里字段没写完）。
   单机走 loopback，仍经过长度前缀的传输层，因此不能仅凭“同进程”排除。

**判别方法（下一次复发）**：发送侧 tap 的 B3 白名单能序列化指定 id 的字节
（`packet_capture(True, ids=[39], serialize_ids=[39], directions=["send","recv"])` +
`packet_bytes`）。抓到的 39 号载荷：前 8 字节若是小的 runtime id（如 `01 00 00 00 …`）
⇒ 经典二进制（指向机制 1）；若是哈希/指纹形态 ⇒ cereal，再比对长度与 34（指向机制 2）。

**接线建议（未落地，避免与另一会话正在重写的 `check_flight`/`ViolationObservation` 冲突）**：
在 `LEVIPILOT_VIOLATION_DUMP` 生效时，于 `check_flight` 开头 arm 上面这条 capture，
在它的 `finally` 里 `packet_bytes(0, 256)` 落盘到 `<dump>.bytes.json`，再 `packet_capture(False)`。
能力本身（序列化 39 号、导出字节）已验证可用，缺的只是这两处接线。

本轮未做：不对 39 号抓包（未复发），不构造畸形包。

## 7. 载荷字节捕获落地 + 判别结果（2026-11-01 00:5x）

### 接线（已落地并现场验证）

`driver/selftest.py::check_flight`：`LEVIPILOT_VIOLATION_DUMP` 生效时，
进入检查前 arm `packet_capture(True, serialize_ids=[39,111,187,188],
directions=["send","recv"])`（身份环不过滤，记全部包），`finally` 里导出
`<dump>.bytes.json`（B3 序列化字节）与 `<dump>.drain.json`（`seq → tick/id` 索引）。
需要 `allow_packet_capture=true`（否则 arm 失败，会在检查开头打
`flight_bytes_capture unavailable`）。实测 5 次飞行专项各捕获 200–256 行，无异常。

### 成功运行的基线（同一进程、同一世界，62–132 个样本/包）

| id | 包 | 序列化长度 | 稳定性 |
| --- | --- | --- | --- |
| 111 | MoveDeltaActor | 13–22 | 随位移变化 |
| 187 | UpdateAbilitiesPacket | **33** | 恒定 |
| 188 | UpdateAdventureSettingsPacket | **5** | 恒定 |

### 与失败样本对照

| 样本 | id | 记录里的 `packet_size` | 发送侧序列化 | 读侧报错 |
| --- | --- | --- | --- | --- |
| A | 39 SetActorData | 34 | 未捕获（该窗口没发） | `incomplete` / 104 |
| B | 111 MoveDeltaActor | 28 | 13–22（含包头则接近） | `incomplete` / 104 |
| C | 188 UpdateAdventureSettings | **2** | **5** | `overflow checkedNumber = 3` / 29 |

C 是最干净的一组：**发送侧对 188 恒定写 5 字节，而到达读侧的 `packet_size` 只有 2**，
读侧要 3 字节即溢出。B 的 28 若含包头（~4–6 字节）也与“到达长度 ≥ 发送 body”并不矛盾，
但不足以单独定案；A 没有同期字节。

### 结论（证据等级）

- `[实测]` **入站缓冲比发送侧序列化结果短**（样本 C：5 → 2），且三次失败横跨三个不同包
  （39/111/188）——这把“SetActorData 的 schema 问题”和“序列化模式 override”都排除了：
  模式不一致会让**固定**的包稳定失败，不会随机换包。
- `[推断]` 机制收敛为**入站分帧/长度与实际字节不一致**（发送侧长度前缀、压缩路径或传输层），
  读侧只是受害者；`read() incomplete`/`overflow` 与 `ensureReadCompleted` 的 104 都是同一后果。
- `[未定]` 谁把 5 字节变成 2：需要**接收侧**的字节（不是发送侧序列化），即挂钩
  `NetworkConnection` 的解压/收包边界，或记录 `packet_size` 的来源（头里的声明长度 vs 实收长度）。
  这是下一步的“拓展能力”，本轮未做。

### 复现状态

本轮 5 次飞行专项武装运行未再触发（历史频率约 1/3–1/4），捕获设施与基线已就位；
下次触发时 `<dump>.bytes.json` + `.drain.json` 会给出失败 tick 前后的完整包序列。

## 8. 接收侧钩子尝试（失败并回退）+ `packet_size` 语义（2026-11-01 01:1x）

### 尝试与结果

目标：在解析入口记录每个入站包的切片长度。选了 `Packet::readNoHeader`（每个客户端入站包都过它）。

- `[代码]` 静态确认目标地址：分发函数 `sub_1416AB0E0` 在 `0x1416ABE4F` 调
  **`sub_1416C2310` = `Packet::readNoHeader`**。反编译签名是
  `(__int64 sret, _BYTE* this, stream, ctx, subid)` —— 即 `Bedrock::Result<void>` 用
  **隐藏返回指针（sret）**，`this` 在第 2 个寄存器。
- 两次现场都崩（新增 `crash-36456`、`crash-21624`，异常 `0xC0000005`，`last_command=violation_watch`），
  第一次在加“有界自旋等 origin”后仍崩 ⇒ 不是安装窗口问题，是 **wrapper 与真实 ABI 不符**
  （`Bedrock::Result<void>` 在本仓库 SDK 头里的返回约定与真实二进制不同）。
- 处置：**整段入站钩子已回退**（`inbound_drain` 现在明确回
  `{"supported": false, "reason": "read_no_header_hook_retired"}`，不再假装有数据）。
  回归：安装/停止守望恢复正常（`callback_hooked/screen_hooked` 都对，`hits=0`），无新崩溃。

### `packet_size` 语义不必再赌

样本 C 里 188 的发送侧序列化恒为 **5** 字节，而违规回调的 `packet_size = 2`。两种可能语义都指向同一结论：

- 若 `packet_size` 是**包体长度** ⇒ 2 < 5，包体被截短；
- 若 `packet_size` 是**含包头的整包长度** ⇒ 整包 2，包体更短，同样被截短。

包头只会**增加**长度，不可能把 5 变 2。所以"**到达读侧的帧比发送侧序列化结果短**"这一结论
不依赖 `packet_size` 的精确口径。

### 下一步（不再做新钩子）

用已落地的 **发送侧全量身份环**（`packet_capture(directions=["send","recv"])` 不过滤 id，
导出 `<dump>.drain.json`）检查失败 tick 附近的**包序列**：如果失败的 39/111/188 之前紧跟
187（UpdateAbilities，发送侧恒定 33 字节），就说明是**能力包把帧边界读歪**，后续包在错误偏移上
被解析——这也解释了为什么失败包 id 每次不同。这条只用已有设施，不需要新钩子。

## 9. 入站切片长度：截短层已定位（2026-11-01 02:0x）

### 能力（已落地、已验证稳定）

`violation_watch on` 同一开关还装第三只只读钩子：`Packet::readNoHeader` 的**原始机器形态**
（5 个指针参数，无寄存器返回）。关键实现要点：

- 早期两次崩溃的原因：按 `(Packet*, stream, ctx, subid)` 写的 typed wrapper 与真实 ABI 不符；
  改成**五个 `void*` 原样透传**后才稳定。
- 不假设流在哪个参数：对前 4 个候选逐个用 `readMemory` 探测
  （`+0x28 view / +0x30 size / +0x38 readPtr / +0x40 overflow`），
  `0 < size <= 1MiB && readPtr <= size` 才算命中。实测 **流恒在 r8（第 3 个参数）**。
- 采样 1/4，环形 1024 行；`inbound_dump {"path": ...}` 由 **mod 直接落盘**
  （多客户端共用管道时回复会被另一个连接截走，所以结论不依赖谁在听）。
- 试图再读包 id 时（对校验过的候选调 `getId()`）又崩一次（`crash-11356`），
  已移除该读取——id 由违规回调那边给出，不需要在热路径里虚调用。

### 实测对照（同一次 `--flight-only` 运行，4 份 dump 同时产出）

| 项 | 值 |
| --- | --- |
| 发送侧 B3 序列化 | 187 = **33**、188 = **5**、111 = 13–26 |
| 入站切片长度 | 187 → **35**（39 次）、188 → **7**（23 次） |
| 结论 | **入站切片 = 发送侧 payload + 2**（这 2 字节是 187/188 的 id varint；见 §12 更正） |
| 失败样本 C | 188 的 `packet_size` = **2**，同模型应为 **7** ⇒ 少了 5 字节 |

### 定位结果

`[实测]` 失败包到达**解析入口时切片已经只有 2 字节**，而写侧输出 5（+2 头 = 7）。
所以截短发生在**解析器上游的帧切分/解压/传输层**，不在 payload schema、不在 cereal 反序列化、
也不在本次钩子所在的边界之后。这与此前"三次失败横跨 39/111/188"一致：
读侧只是受害者，受损的是帧边界。

### 仍未定（下一层）

帧头里**声明长度**与**实收字节**谁先错：若声明长度本身是 2（写侧头错），则是发送侧分帧 bug；
若声明 7 而只到 2（传输截断），则是传输层。判别需要在**解包/解压边界**再取一次
（缓冲区长度 vs 头声明长度），又是一次全新钩子；本轮已因同类钩子产生 3 个 crash 文件
（`crash-36456`、`crash-21624`、`crash-11356`），未继续。

## 10. 子问题收束：声明长度 vs 实收字节（2026-11-01 02:1x）

### 已知（`[实测]`，同一运行对齐）

- 入站切片 = 发送侧 payload **+2**：187 33→35、188 5→7（各 39/23 次）。
- 失败样本 C：188 的入站 `packet_size` = **2**（同模型应为 7）。

### 两个剩余子情形

| 子情形 | 含义 | 需要的观测 |
| --- | --- | --- |
| A | 帧头里**声明长度就是 2** | 发送侧分帧 bug（写侧头与 body 不一致） |
| B | 声明 7，但传输只到 2 | 传输/解压层截断（批尾被剪） |

### 现有证据更偏向 A（`[推断]`，不是定案）

1. 读侧是在**逐包解析**阶段失败的（`read() incomplete` / `overflow`），而不是批处理层的
   长度校验失败。若整批字节比声明少，连接层会先报批级错误。
2. 失败前同一批里的更早包都解析成功（我们的身份环在失败 tick 前有连续的入站切片记录），
   说明这批字节按“每包声明长度”切分是自洽的，只是这一个包的声明长度与 body 不符。
3. 反证：若属 B（批尾被剪），被剪的应当是批中**最后一个**包。三次失败横跨 39/111/188，
   若都为批尾巧合，概率不低但无法排除；这一点没有证据。

### 要变成定案，只需一处仪器

在**解包/解压之后、逐包切分之前**记录 `(批总长, 首个声明长度, 剩余字节)` 三元组，
与本次的入站切片（187→35、188→7）对齐即可二分。实现要点（沿用本轮已验证的形态）：

- 用**五个 `void*` 透传**的原始机器形态钩子，不做 typed 包装（typed 版崩过两次）；
- 不对候选做虚调用（读包 id 的那次崩了第三次，已移除）；
- 所有读取走 `readMemory` + 范围校验（`0 < size <= 1MiB`）；
- 结果由 mod 直接写文件（多客户端时管道回复会被另一连接截走）。

本轮未再装这只钩子：同类钩子已产生 3 个 crash 文件（`crash-36456/21624/11356`），
在无法离线验证其 ABI 前继续加钩子的风险高于收益。

## 11. 决策：子问题不阻塞任何自有代码动作（2026-11-01 02:2x）

再往下钉“声明 2 还是实收 2”，需要在**游戏自己的**批处理/解压路径里再插一只钩子：

- 该函数族（`sub_1419D7D90` → `sub_1416AA7F0` → 分发 `sub_1416AB0E0`）属于 Bedrock 网络层，
  **不是本仓库的 mod 代码**；无论子问题是 A（发送侧分帧）还是 B（传输剪尾），
  mod 侧都没有可修的写入点——我们不写 `SetActorData` / `MoveDeltaActor` / `UpdateAdventureSettings`，
  也不碰批缓冲。
- 已定位的事实足以支撑这个判断：入站切片 = 发送侧 payload +2（187 33→35、188 5→7 各数十次），
  失败样本的 188 切片为 2；读侧报的是**逐包解析越界**而不是批级长度错误。
- 三次加钩子的尝试已产生 3 个 crash（`crash-36456/21624/11356`）。继续在未验证 ABI 的游戏内
  函数上加钩子，收益（一个不影响行动的子情形）低于风险。

因此本调查的结论停在：**根因层 = 游戏网络栈的入站分帧/解压/传输，早于包解析；mod 侧无缺陷、无补丁**。
需要彻底定案时的仪器与步骤见 §10，可作为后续独立任务执行。

## 12. 更正：批入口钩子装错了函数；入站切片以包 id 开头（2026-11-01 03:0x）

### 更正（`[代码]`，本轮静态复核）

上一轮登记的 `@client_packet_batch_entry = 0x1416AA7F0` **不是逐包入口**：

- `0x1416AA7F0` 反编译里出现 `NetworkSystem::runEvents task` 字符串、`NonOwnerPointer<NetworkSessionOwner>`
  的 `mControlBlock` 断言、以及任务提交 `sub_1400A2400` —— 它是 **runEvents 任务体**，签名
  `(void*, char)`，第二参是"哪一侧"的标志位，不是包下标。
- 它的调用者 `sub_1419D7D90` 也不是批循环：函数体是 `Query_perf_frequency/counter` 计时 +
  profiler + 每次迭代一次 runEvents，是**网络运行循环**。
- 那一轮快照里稳定的 5 个 qword（`…4ae40 / …4ae30 / 0x7ff6b941e2f0…`）因此是 NetworkSystem
  对象与 vtable 指针，不是"批视图"，更不能当声明长度用。

已更正：角色改为 `@client_packet_parse_loop = 0x1416AB0E0`（真正的逐包解析循环，
函数内只调用一次 `Packet::readNoHeader`，地址 `0x1416abe4f`；runEvents 以
`sub_1416AB0E0(&v49,&v43)` 进入），旧的 batch 钩子与 `batch_addr` 参数已从 mod/驱动中移除。

### 新测量：切片以包 id 开头，长度前缀不在切片里（`[实测]`）

`out/inbound-live.json`（1024 条，2026-11-01 03:0x，`inbound_dump` 落盘）解码：

| 切片首字节 | 解出的 id | 包名（`refs/levilamina-26.51.7/.../MinecraftPacketIds.h`） | 切片长 | 载荷 |
| --- | --- | --- | --- | --- |
| `27` | 39 | `SetActorData` | 17 | 16 |
| `6f` | 111 | `MoveDeltaActor` | 21/22/23… | 20/21/22… |
| `90 01` | 144 | `PlayerAuthInputPacket` | 95 | 93 |

1. 首字节解出的 id 与载荷结构自洽（39 行：`27 0d 01 00 07 07 …` = runtime id 13 + 数据项）。
2. 187/188 的 id varint 是 **2 字节**（`bb 01` / `bc 01`），所以切片 = payload + **2**；
   这个 +2 不是"固定包头"，而是这两个 id 的 varint 宽度。
3. 长度前缀在切片**之前**，已由切分器消费 —— 这也解释了为什么切片大小精确等于 payload+id 宽度。
4. `144 PlayerAuthInput` 是客户端上行包：本地世界是 loopback，集成服务端在**同一进程**里解析它，
   所以本钩子看到的是"本进程解析的所有包"，不是"服务端下发的包"。

由此，失败样本 C（188 的切片只有 2 字节）在 id-first 模型下 = **只剩 id varint、payload 为 0**。

### 一并修掉的工具缺陷（`[实测]`）

`inbound_drain` 一直超时，早前归因为"两个客户端抢管道"是**错的**：`drainRing()` 把事件名
硬编码成 `"violation_drain"`，客户端等 `inbound_drain` 永远等不到。已改为按调用方传入
（`violation_drain` / `inbound_drain`）。`inbound_dump` 落盘路线不受影响。

## 13. 声明长度**不在**切片前（实测否定）+ 判定方法保留（2026-11-01 03:2x）

### 实测：切片是拷贝，前面的字节不是长度前缀

`out/inbound-slices-before.json`（57 条，重装后同一进程）：

```
id=111 size=23  before 0000000000000000 8000cc811c020000   slice 6f15 01fea1a4...
id=144 size=24  before 10e668941b020000 10d678941b020000   slice 9001 006808bfc0...
id=39  size=17  before 0000000000000000 8000cc811c020000   slice 2706 010007078080...
```

- 切片前 16 字节是**堆指针 / 零**（`0x21b…` 两枚指针、`0x21c81cc0080` 一类），
  末尾字节恒为 `00`；若是帧头，长度 varint 的末字节必须正好结束在切片起点，
  且值等于 body（这里 16/20/22），实际不是。
- 结论（`[实测]`）：解析入口拿到的是**独立拷贝**（或指向另一对象内部），
  帧的 `length` 前缀既不在切片里，也不在切片前可读的位置。`before_hex` 保留为
  “现状证据”，但**不能**用来恢复声明长度；`slice_verdict()` 因此把 `declared==0`
  一律判 `unknown`，自检遇到“声明不可读”时 skip 而不是误判。

### 仍然可用的判定事实（不依赖声明长度）

1. `[实测]` 发送侧 `packet.write()` 的 body：187=33、188=5（白名单序列化，多次运行恒定）。
2. `[实测]` 解析入口切片：187 = 35 = 33 + 2、188 = 7 = 5 + 2（+2 是两个 id 的 varint 宽度）。
3. `[实测]` 失败样本 C：188 的切片 = **2 字节**，即只有 id varint、body 为 0。
4. `[代码]` 本地世界是 loopback：`BatchedNetworkPeer::sendPacket` 实测 0 调用，
   `LocalNetworkPeer::$sendPacket` 仅 dllimport —— 发送侧没有可观测的“帧字节”，
   帧是在进程内由另一层写出的。

由 1+3 可直接得：**写侧明明有 5 字节 body，到达解析入口时 body 为 0**。
这已经是“帧长度与 body 不一致”，不需要再猜；剩下只差“谁写的 0”。

### 要定案还缺的一步（可选、独立）

需要钩住**写帧/切分**的那一层（不是 readNoHeader）才能看到“声明长度”这个局部值。
本轮已排除的候选与理由：

- `0x1416AA7F0`（runEvents 任务）/ `sub_1419D7D90`（网络运行循环）：不是逐包函数。
- `sub_1416AB0E0`（逐包解析循环）：入口处批还没解析，取不到每包的声明长度。
- 切片前 16 字节：实测为无关堆数据（见上）。

进一步静态追查需要完整反编译 `sub_1416AB0E0`（0x1e9f 字节，MCP 单次返回被截断），
以及 `off_14E75BD20`（ReadOnlyBinaryStream vtable）的写入者（`xrefs_to` 本轮两次超时）。
这属于“定案子情形”，不影响已经成立的行动结论（§11）：
**根因层在游戏网络栈的帧写出/切分，早于包解析；mod 侧无写入点、无补丁。**

### 现场验收项（`driver/selftest.py`）

`check_inbound_slices()`：切片行数 ≥4、id 落在合理范围、至少出现一个已知 id
（39/111/144/187/188）、以及“声明==实收”占比（≥90% 才通过；声明不可读即 skip）。
它验证的是**仪器已接通**（切片字节 + 前导字节都能读到），不是“某次没有违规”。

## 14. 两次自然复现（2026-11-01 03:12 与 12:47）+ 页面文本可读性（2026-11-01 12:5x）

### 复现 A（p0b8，03:12，tick 454）

| 字段 | 值 |
| --- | --- |
| 来源 | `source=callback`，`response=3` |
| 包 | **187 UpdateAbilities** |
| 解析入口切片 | `packet_size = 36`（同运行 63 条 187 全为 **35** = id 2 + body 33） |
| 错误 | `error_value = 29`，context 截断为 `BinaryStream read() overflow checkedNumber = 37` |
| 发送侧 | `packet.write()` body 恒 **33** 字节（含事发前最后一条） |
| 后续 | 同 tick 出现 `source=screen` 的 `response=90 / context="Block"` |

### 复现 B（pb14-3，12:47，tick 1389）

| 字段 | 值 |
| --- | --- |
| 包 | **111 MoveDeltaActor**，`packet_size = 20` |
| 错误 | `error_value = 104`（bad_message），context `invalid numeric value
readNoHeader failed! pack…` |
| 后续 | 同 tick `screen` 行 `response=90`；错误页详情文本见用户截图（`Date: 2026-11-01T04:47:17Z`，`ClientDisconnection-90`） |

### 共同点（`[实测]`）

- 两次都发生在**刚进世界不久**：tick 454 / 1389（新会话早期），不是长时间运行后。
- 触发操作都是飞行能力切换（`check_flight` 的 fly / togglefly / reset 序列），
  但同样的序列在多数运行里不触发——是概率事件，约 1/5。
- 截面都在**解析入口**：`packet_size` 与发送侧 body 不一致（187：36 vs 35；111：20 vs 与发送侧对照待补）。

### 页面文本：能拿到多少

`source=screen` 行的 `detail` 就是错误页右栏那段文本（`composeRoute` 的 `errorDetails`），
旧上限 47 字节只能留下第一行。已放宽：

- `context`（违规消息）→ 192 字节，足以装下
  `BinaryStream read() overflow checkedNumber = {}, mReadPointer = {}, buffer Length = {}`
  的完整一句（这句的三个数正是判定“声明 vs 实收”的关键）；
- `detail`（错误页文本）→ 640 字节，足以装下整页（约 570 字符，含
  `Date/Version/Application/Transport/NetworkType/Messaging/Connection/WorldSession/Client/WorldName`）。

页面里的 GUID 是会话标识，用于和日志对齐；**包 id 与 error_code 只在 callback 行**，
页面文本本身不含。两者在同一次 drain 里按 tick/tid 对齐。

### 追加：长度上限是我们的记录结构，不是游戏的

游戏传进来的是 `std::string`（`handlePacketViolation` 的 context、`composeRoute` 的
`errorDetails`），本身**没有固定上限**。之前的 47 字节只是定长环记录的前缀上限。
现在两条通道并存：

| 通道 | 上限 | 用途 |
| --- | --- | --- |
| 记录行 `context` / `detail` | 191 / 639 B | 与 seq/tick/tid/包 id 对齐，够放整页约 570 字符 |
| `violation_text` | 4 KB/槽 × 8 槽 | 整段原文，回调里只 memcpy，drain 时取回 |

`[实测]` 现场：605 字符的 details 原样返回（`len=605`、`truncated=false`），合成违规 context 完整。

## 15. 闭合证据：失败切片里装着**别的包的 payload**（2026-11-01 13:06，tick 1881）

这是第一次同时拿到「失败切片字节 + 同 tick 发送侧 body + 完整错误文本」的一次复现。

### 现场

```
seq 3153  tick 1881  size 35  id 187  bb01 01000000ffffffff0204010100ffff0f00ff00...
seq 3154  tick 1881  size 23  id 188  bc01 0000000001 0e 01 00 07 07 80 80 c0 84 80 83 80 03 00 00
```

- 回调行：`packet_id=188`、`packet_size=23`、`error_value=104`、
  `context="BinaryStream read() incomplete
readNoHeader failed! packetId: 188"`（完整，未截断）。
- 同 run 的 188 正常切片：**61 条，全部 7 字节**（`bc01` + body `0000000001`）。
- 发送侧 `packet.write()` 的 188 body：**63 条，全部 5 字节**。

### 失败切片的构成（`[实测]`）

| 段 | 字节 | 长度 |
| --- | --- | --- |
| 包 id（188 的 varint） | `bc 01` | 2 |
| 188 自己的 body | `00 00 00 00 01` | 5 |
| **外来 16 字节** | `0e 01 00 07 07 80 80 c0 84 80 83 80 03 00 00 00` | 16 |

外来 16 字节与同 run 的 SetActorData(39) 切片逐字节同构：

```
正常 39 切片 (17B)：27 0d 01 00 07 07 80 80 c0 84 80 83 80 03 00 00 00
外来 16 字节      ：   0e 01 00 07 07 80 80 c0 84 80 83 80 03 00 00 00
```

即：**一个 39 包的 payload（演员 runtime id 0x0e + 15 字节），不带它自己的 id 字节 0x27**，
被塞进了 188 的帧里。

### 结论（子情形闭合）

1. `[实测]` 交付到解析入口的切片**长 23 字节且内容完整**：前缀是合法的 188 头 + body，
   尾巴是一段连贯的 39 payload。**没有“被剪掉”的迹象**——被剪的切片不会留下完整的邻居 payload。
2. `[实测]` 切片长度（23）与回调的 `packet_size`（23）一致，说明游戏自己也知道这个帧是 23。
3. ⇒ **不是传输/解压剪尾，而是帧写出侧把邻居包的 body 并进了这个帧**（长度也随之变成 23）。
   结合 §14 的 187（36 vs 35）与更早样本 C（188 只有 2），同一层的表现是“帧长度与 body 不一致”，
   只是方向不同。
4. `[实测+代码]` 该层的后果：解析入口 `readPtr = 2`（调用方已读掉 2 字节 id），
   188 的 body 只有 5 字节，读完还剩 **16 字节未消费**；
   `ReadOnlyBinaryStream::ensureReadCompleted()` 的判据是
   `mHasOverflowed || mView.size() != mReadPointer`，两种方向（少读/多读）都给
   `bad_message(104)`、文本同为 `BinaryStream read() incomplete.` —— 这解释了为什么
   样本 C（切片 2 字节，短）和本次（切片 23 字节，长）报的是**同一个** error 104。
   之后 `handlePacketViolation(response=3)` → `BadPacket(90)` → 错误页
   `ClientDisconnection-90` / codeword `Block`。

### 仍是推断的部分

1. 帧头长度字段本身的值：入站侧只能间接推断（§17 的分布对比给出**倾向 23**的旁证，
   长度 varint 未被直读，未定案）。
2. 责任归属：`[实测]` 已证否“传输剪尾”（剪尾不会留下连贯的邻居 payload），
   也证否“本仓库直接写这些包”；但**未排除**对象共享、时序、以及自有 hook ABI 的间接影响。
   结论只能写到：疑点集中在组帧路径，具体写入点与责任归属未闭合。

### 复现配方（可重复）

1. `dev.py run --world "超平坦测试" --allow allow_hook_va=1 --allow allow_packet_capture=1`
2. `selftest.py --flight-only`（或 `--violation-only` 后再跑 flight），每轮 20–60 秒；
3. 约 **1/6 概率**命中；命中后 `inbound_dump` + 违规 dump 同 tick 对齐。
   今天 03:12（187/36）、12:47（111/20）、13:06（188/23）三次都发生在**进世界后 400–2000 tick** 内。

## 16. 收束（2026-11-01 13:2x）

### 已定案

| 问题 | 答案 | 证据等级 |
| --- | --- | --- |
| 谁断开 | 客户端的协议违规处理器，`TerminateConnection(3)` → `BadPacket(90)` | `[代码]` |
| 报什么 | 错误页 `ClientDisconnection-90`、codeword `Block` | `[代码]+[实测]` |
| 在哪一层 | **帧写出侧**：188 的帧里含 16 字节邻居 SetActorData payload | `[实测]` |
| 是不是传输剪尾 | **不是**：被剪的切片不会留下连贯的邻居 payload | `[实测]` |
| 帧声明值 | 倾向 23（切片 23 = 7 + 16），但长度字段未直读 | `[推断]` |
| 为什么是 104 | 读完 5 字节 body 剩 16 字节，`ensureReadCompleted` 判 `size != readPtr` | `[实测+代码]` |
| mod 侧是否参与 | 本仓库**不直接写**这些包、不碰帧缓冲（`[代码]`）；但**未排除**对象共享、时序、以及我们自己的 hook ABI 带来的间接影响 | `[代码]+未决` |

### 帧声明值（§17，仍是 `[推断]`）

多包 tick 里首包切片长度恒等于该包自身长度（187 恒 35、188 恒 7），说明切分独立于后续包；
若切分器直接用声明长度，则失败切片的 23 即声明值 23。**这依赖一个未证实的前提**——
长度字段本身没有被直接读到。定案仍需在切分函数里读出长度解码点，或拿到批缓冲解析帧头。

### 可重复性

三次自然复现（187/36、111/20、188/23）都在进世界后 400–2000 tick 内、飞行能力切换之后，
约 1/6 概率；`out/pb8.json`、`out/pb14-3.json`、`out/pb15-6.json` + 对应 `.inbound.json`
是可复核的原始产物（`pb15-6` 同时含完整 context 与 416 字符错误页文本）。

## 17. 旁证：帧声明值**倾向** 23（2026-11-01 13:5x；未定案）

### 判别原理

切片大小来自帧自己的长度字段（切分器按每包长度切），所以
**只要证明“切片 == 该包自己的长度、而不是“批里剩下的字节””，失败切片的 23 就直接等于帧声明值。**

### 证据：多包 tick 里第一个包的切片长度不变（`[实测]`）

把 1662 条入站切片按 tick 分组（386 个 tick 有 ≥2 个包），比较“多包 tick 的首包”和
“单包 tick”的尺寸分布：

| 包 | 多包 tick 首包尺寸 | 单包 tick 尺寸 |
| --- | --- | --- |
| 187 | 35 ×45 | —（未单独出现） |
| 188 | 7 ×29 | — |
| 111 | 21×58, 22×50, 23×26, 14×21 | 21×37, 22×23, 23×16, 14×15 |
| 144 | 95×87, 94×20 | 95×226, 94×68 |
| 39 | 17×29, 18×3 | 17×2 |

若切分器用“批里剩余字节”，多包 tick 的首包必然比自己的包大（187 会变成 35+后续包，
188 会变成 7+后续包）。实测完全相同 ⇒ **切片长度 = 该帧声明的长度**。

### 结论（`[推断]`，待直接读取长度前缀）

> **前提**：这条推断依赖“切分器直接使用帧声明长度作为切片长度”这一个假设。
> 多包 tick 的分布只证明**切片长度独立于后续包**，没有直接读到长度字段本身；
> 因此下面第 1 条是旁证，不是定案。

1. `[推断]` 失败 188 的切片是 23 字节，若切分器按声明长度切，则声明值 = 23（不是 7）。
2. `[实测]` 23 = 7（188 自己的 id+body）+ 16（一段完整的 SetActorData payload，演员 0x0e）。
3. ⇒ 不是“长度 7、传输多塞了 16 字节（未被计入）”；而是**写出侧把这 16 字节算进了帧长度**。
   两种描述里，可观测的事实是：**帧声明并携带了邻居包的 payload**。
4. `[实测]` 这 16 字节不是缓冲区残留：事发前一个包是 187（abilities payload），
   若为复用缓冲区的旧内容，这里应当出现 187 的字节而不是一条自洽的 39 payload。

### 负结果（有界搜索）

尝试直接读“声明字节”的两种途径都失败了，记录以免重复：

- 解析入口的四个指针参数里没有父流（父流 `view+readPtr` 应等于切片起点，1662 条中 0 命中）；
- 以内容匹配扫描调用者栈 ±0x600/0x800 字节，0 命中 ⇒ 逐包流确实是**独立拷贝**，
  批流不在该栈窗口内（大概率在连接对象堆上）。

因此“声明值”目前只能由尺寸对比推出。要定案仍需**直接读到长度 varint**，两条可行路径：
(a) 在切分函数里定位长度解码点并读出该局部值；(b) 拿到批缓冲（父流）并解析帧头。
两条都还没做成。

## 18. 追声明字节：三条新路径全部为负（2026-11-01 14:2x）

上一轮把“声明长度 = 23”留作旁证，这一轮专门去拿那个字节，三条路都走不通：

| 路径 | 做法 | 结果 |
| --- | --- | --- |
| 流对象里的源指针 | 逐包流若是拷贝，源指针可能作为普通字段留在对象前 0x60 字节；按内容匹配找“以切片首 4 字节开头的指针” | 801 条采样 **0 命中** |
| 调用者栈窗口 | 以返回地址槽为基准扫 ±0x600/0x800 字节，同样按内容匹配 | **0 命中** ⇒ 逐包流是独立拷贝，批流不在该窗口 |
| 第四个参数 `r9 = [rsi+0x208]` | 直接取该指针处 24 字节 | 是**带 vptr 的对象**（`0x7ff6c993bbd0` + 两个相差 0x10 的堆指针），不是原始批缓冲 |

另确认：**NetherNet 是静态链接进 exe 的**（版本目录与进程模块表都没有独立模块），
所以组帧代码确实在这份 IDB 里；但承载它的 `sub_1416AB0E0` 有 353 个基本块，
MCP 的 `decompile` 对 >64KB 输出截断到 1KB，而该函数内既无 `shl` 也无 `and 7Fh`
常量（`func_profile` 的 293 个常量里没有 127/128），说明**长度解码不在这个函数里**。

### 由此得到两个互斥模型（都未排除）

| 模型 | 含义 | 现状 |
| --- | --- | --- |
| A. 有长度前缀且算成 23 | 组帧时长度字段覆盖了 16 字节邻居 payload | 与“多包 tick 首包尺寸 = 自身长度”相符 |
| B. 没有长度前缀，23 就是缓冲长度 | 缓冲区里残留 16 字节旧内容，长度按缓冲算，所以“长度正确但多写了” | 与“切片是独立拷贝、前邻字节是堆数据”相符 |

两个模型的差别是“有没有那个前缀”，而这需要拿到**批缓冲**或**组帧函数**；
入站侧拿不到（上表三条路径），发送侧的 tap 又只看到 `packet.write()` 的 body。
**因此“声明 23 vs 7”这一问在本轮无法闭合**，只能写到“疑点在组帧路径，且两种形态都属于写侧”。

### 一条与此相关的旁证

`packet_capture` 的 tap 在同一 run 里记录了 111/187/188，却**没有任何 39**——
说明 SetActorData 的下发不走 `LoopbackPacketSender::sendToClient` 那条被 tap 的路径。
若模型 A 成立（188 帧里并入了 39 的 payload），两者必然共享了某段序列化缓冲，
而它们走的又不是同一条发送路径，这一点值得后续验证。

## 19. 找到切片流的构造点：`[代码]` 逐条读到（2026-11-01 14:4x）

之前一直缺的“流从哪来”，在解析循环 `sub_1416AB0E0` 里找到了（`0x1416ab789`–`0x1416ab808`）：

```
1416ab789  cmp qword ptr [rsi+140h], 10h     ; capacity == SSO?
1416ab791  mov rax, [rbp+450h+var_2A0]
1416ab798  jb  short loc_1416AB7A1
1416ab79a  mov rax, [rsi+128h]               ; heap data pointer
1416ab7a1  mov rdx, [rsi+138h]               ; size
1416ab7a8  lea rcx, off_14E75BD20            ; ReadOnlyBinaryStream vtable
1416ab7af  mov [rbp+450h+var_318], rcx
1416ab7b6  lea rcx, [rbp+450h+var_310]
1416ab7bd  movups xmmword ptr [rcx], xmm6    ; SSO buffer 清零
1416ab7c0  mov qword ptr [rcx+10h], 0
1416ab7c8  mov [rbp+450h+var_2F8], 0Fh       ; capacity = 15
1416ab7d3  mov [rbp+450h+var_2F0], rax       ; +0x28 = data pointer
1416ab7da  mov [rbp+450h+var_D8], rdx
1416ab7e1  mov [rbp+450h+var_2E8], rdx       ; +0x30 = size
1416ab7e8  mov [rbp+450h+var_2E0], 0         ; +0x38 = readPtr
1416ab7f3  mov [rbp+450h+var_2D8], 0         ; +0x40 = overflow
1416ab7fa  lea rcx, [rbp+450h+var_318]
1416ab801  lea rdx, [rbp+450h+SRWLock]
1416ab808  call sub_140670C40
```

读出来的结论：

1. `[代码]` 流对象布局被这条构造逐字段确认：`+0x28` 数据指针、`+0x30` **size**、`+0x38` readPtr、
   `+0x40` overflow —— 与我们一直探的位置一致。
2. `[代码]` **size 不是本函数从字节里解出来的 varint**，而是直接取自
   `rsi+0x138`（`rsi = *a1`，即 NetworkSystem 侧对象的成员 `std::string`：
   buffer `+0x128`、size `+0x138`、capacity `+0x140`，capacity==0x10 时用 SSO 内联缓冲）。
3. `[代码]` readPtr 构造时为 0；到 `readNoHeader` 入口变成 2，说明**包 id 是在这两点之间从同一流里读掉的**。
4. `[实测]` 因此失败样本的 23，就是那个成员 `std::string` **当时的 size**。
5. `[代码]` 这个流是**只读视图**（`+0x28` 直接指向那个 string 的缓冲，没有二次拷贝），
   所以切片起点＝该 string 的起点：string 里存的就是 `[id][payload]`，
   **长度前缀在更上游就被剥掉了**（若存在）。这也解释了为什么切片前的 16 字节是堆数据
   —— 那是这段 string 缓冲的邻居，不是帧头。

### 这一步把问题推进到哪

- 可以确定：解析层没有“先读长度前缀再切包”的动作，它只是把 NetworkSystem 里某个 `std::string`
  的 `{ptr,size}` 包成流。**所以 §18 的模型 B（无前缀、长度即缓冲长度）在解析层成立。**
- 仍未定：那个 `std::string` 是谁写的、写的时候 size 为何是 23。
  上游若按 `[len][payload]` 从批里拷贝，则前缀确实存在（模型 A），只是不在这层读；
  若它是复用缓冲且只写了 7 字节却把 size 设成 23，则是模型 B 的“长度按缓冲算”。
- 下一个可执行步骤：找 `NetworkSystem + 0x128` 这个 `std::string` 的写入点（`sub_140670C40`
  之后到解析之间的调用链，或网络接收线程里的 `assign/append`）。

## 20. 模型判据翻转：切片缓冲是**复用**的（2026-11-01 14:5x）

对已有三份 dump 用切片数据指针（`category`）统计：

| dump | 行数 | 唯一缓冲地址 | 相邻同址 |
| --- | --- | --- | --- |
| `pb15-6.inbound.json` | 1024 | **2** | 805 / 1023 |
| `auxprobe.json` | 817 | **2** | 569 / 816 |
| `sourceprobe.json` | 801 | **2** | 545 / 800 |

结论：逐包流不是“每包一份新缓冲”，而是**同一个 `std::string` 缓冲反复复用**，
每次只改 size/内容。这与 §19 的构造点一致（流直接指向 NetworkSystem 成员 string 的缓冲）。

### 这条数据推翻了什么

§15/§17 曾用“事发前一个包是 187，若是残留就该出现 187 的字节”来排除缓冲残留。
**该论证不成立**：缓冲区由**生产者**写入、由解析器读取，两者之间存在交接；
对同一个复用缓冲而言，`188` 写入前最后落进去的不一定是解析器刚处理过的那个 `187`，
而可能是生产者更早放入的一段 SetActorData 内容。因此“16 字节是残留”重新成为**同等级候选**。

### 两个模型的最新状态

| 模型 | 支持证据 | 反对/未决 |
| --- | --- | --- |
| A. 长度前缀算成 23 | 多包 tick 首包尺寸恒等于自身长度（切分按每包长度） | 解析层不读前缀（§19），前缀若存在只能在上游，未见 |
| B. 复用缓冲残留 16 字节 | 缓冲只有 2 个、约 80% 相邻复用（本节） | 残留内容与“解析器前一个包=187”不符；但真正的上一写入者是生产者，不可见 |

两者都归到同一个待定位对象：**`NetworkSystem + 0x128` 这个 string 的生产者写入点**。
下一步只有两条：把这个类型在 IDA 里建起来后追写入者，或在生产者线程钩住该 string 的 `assign/append`。

### 缓冲按方向分开（同一份 dump 的另一条切分）

按缓冲地址再切一刀：

| 缓冲 | 出现的包 | 线程 |
| --- | --- | --- |
| `0x23e7037a020` | **144 PlayerAuthInput** ×129 | 37408 |
| `0x23e90922020` | **111 MoveDeltaActor** ×640、**39 SetActorData** ×46、314 ×2 | 36796 |

即：上下行各有自己的复用缓冲，且 **39 与 111/188 共用同一个下行缓冲**（线程 36796）。
这让“失败 188 的尾部 16 字节原本是缓冲里一段 SetActorData”在**机制上成立**
——同一缓冲、同一线程、同一方向，39 确实写进这里过。

仍未解释的细节：若 39 以 `[id][payload]` 从偏移 0 写入，偏移 7 处应是 payload[6]=`80`，
而实测尾部以 `0e` 开头（payload[0]）。也就是说，残留说还需要“写入偏移不等于 0”
或“某次写入只落了 payload”这样的额外条件，尚未找到证据。

## 21. 用 DR 写断点抓到写入指令与调用链（2026-11-01 15:0x）

### 做法

1. 给解析循环 `sub_1416AB0E0` 装入口钩子，取 `*a1` 作为“网络上下文”，经
   `violation_stats.net_ctx` 导出；
2. 现场核对布局：`ns+0x128` = 数据指针、**`ns+0x138` = size**、`ns+0x140`。
   静止时读到 `size=0x0e`，缓冲内容 `6f 02 00 ...`（111 包）——确认就是逐包缓冲；
3. `watch_set(ns+0x138, size=8, mode=w, thread=both, depth=8)` 装硬件写断点。

### 命中结果（`[实测]`）

```
rip   = 0x140d7d70b   （运行时换算回静态 VA）
stack = … 0x1416c608d, 0x140d937ea, 0x1416ab781
```

`pe_map` 定位：

| 静态 VA | 所属 | 说明 |
| --- | --- | --- |
| `0x140d7d470`(+0x29b) | 写指令所在 | 陷阱 RIP 前一条是 `0x140d7d707: mov [rdi+10h], rbx`（写 std::string 的 size），紧跟 `memmove(dst, r9, rbx)` 与补 NUL |
| `0x1416c6070` | 中间函数 | `mov rcx,[rcx+8]; rax=[rax+40h]; call` → 经 vtable 调一次虚函数 |
| `0x140d935e0` | 解析链上游 | 解析循环在 `0x1416ab77c` 调它 |
| `0x1416ab781` | 解析循环 | `call sub_140D935E0` 的下一条 |

### 这条链说明什么

- `[实测]` 逐包缓冲的 size 是在**解析循环自己的调用链里**被写的（`0x1416ab77c` → … → `0x140d7d707`），
  不是另起一个生产者线程先填好再交给解析器。这修正了 §20 的“生产者/解析器交接”假设：
  两者的边界比想象的更近。
- `[代码]` 写入点是一个通用 `std::string` 赋/拷助手（写 size → memmove → 补 NUL），
  真正的长度 `rbx` 与源指针 `r9` 由它的调用者（经 vtable 的一次虚调用）算出。
- `[未决]` 该虚调用内部如何得出 23：仍在虚函数实现里，未定位。

### 下一步（明确）

DR 命中只记录了 RIP/栈，没有寄存器快照。要拿“写入的 size 与源”，有两条：
(a) 给 watch 命中补寄存器快照（`rbx`/`r9`），再在失败复现时读；
(b) 直接钩 `0x140d7d707` 的调用者（经 vtable 的那次虚调用目标），按调用者身份分辨。

## 22. 抓到填充那次 memmove：源不是帧头（2026-11-01 15:2x）

### 工具

新增 `memmove_probe`：钩 `memmove`，只记录**目标等于逐包缓冲**（`ns+0x128` 的 data 指针）的那些调用，
把 `(源地址, 拷贝长度, 源前 16 字节, 源起 16 字节)` 写进原文环（kind=`memmove`）。
每次调用只多一次地址比较，其余原样转发。

### 实测（现场 5 条，`inbound_sample=1`）

```
len 22  src 0x224fc69c9c0  before …24170dbf242e0090  after 6f0f01540441410001b077fb41…
len 21  src 0x224fc69c330  before …a117babf240b0090  after 6f0e01e5f8174100013d6ca841…
len 21  src 0x224fc69cba0  before …2a1723bf24380090  after 6f070103aa13c1000114f0f8bf…
len 22  src 0x224f57d97e0  before …ee52d0b900420290  after 6f1201c488a4c000018bd36341…
len 21  src 0x224f57da0b0  before …995165b841710290  after 6f10011436524000016d141641…
```

结论：

1. `[实测]` `after` 全部以包 id 开头（`6f`=111），长度 21/22 与入站切片一致 ⇒ 拷贝源就是
   “该包自己的 `[id][payload]`”。
2. `[实测]` 源地址每次都不同（`0x224fc6xxxx` / `0x224f57xxxx` 两个区），**不是**同一个批缓冲里的
   不同偏移；而**目的**（逐包 string）只有 2 个 —— 即：`每包一个源缓冲 → 复用目的 string`。
3. `[实测]` 源前最后一个字节恒为 **`0x90`**（带继续位的字节），**不可能**是长度 varint 的结尾；
   源前 16 字节是堆样数据。⇒ 拷贝源**不是**“批缓冲里紧跟长度前缀之后的位置”。
4. 因此“长度前缀”若存在，是在**更上游**产生这个 per-packet 源缓冲时就被消费掉了；
   本次 memmove 只是把它整段搬进复用的解析缓冲。

### 对两个模型的影响

- 模型 A（前缀算成 23）依然可能：上游按 `[len]` 切成 per-packet 缓冲时，len 可能是 23。
- 模型 B（复用缓冲残留）**被削弱**：这次拷贝的**长度是显式传入的 `n`**（21/22 与切片一致），
  不是“目的缓冲的容量”。若要出现 23，必须上游把 per-packet 缓冲做成了 23 字节，
  而不是解析缓冲残留 —— 也就是残留发生在上游缓冲，不在这里。
- 仍未定：谁创建了那个 per-packet 源缓冲、它的长度从哪来。

### 下一步（有明确钩点）

per-packet 源缓冲的分配/填充是下一个可观测点：源地址每次不同且成簇，像是从 arena/池里取。
可以在 `memmove` 命中里再记录 `src-0x20..src` 的更多字节判池结构，或钩住返回这些源缓冲的
上层函数（`sub_1416C6070` 经 vtable 的那次虚调用）。

## 23. 第四次复现：失败 size（8）与拷贝长度（7）不一致（2026-11-01 15:0x）

### 现场

`[实测]` 同一进程、`inbound_sample=1`、`memmove_probe(id=188)` 打开：

```
callback: tick 252  packet_id 188  packet_size 8  error 104  response 3
screen  : tick 252  reason 90 / Block
memmove(188) 记录：tick 168/169/183 …，copy_len 全为 7
```

- `[实测]` 这次失败切片的 `packet_size` 是 **8**（188 的 id 2 + body 5 = 7，多 1 字节）。
- `[实测]` 同期 188 的 memmove 拷贝长度**全是 7**，与失败帧的 8 不符。
- `[实测]` 拷贝源缓冲是**复用的**（同址反复出现），且拷贝区之后紧跟着**上一个包**的字节
  （例：`bc01 0000000001 | 00 ff ff 02 04 01 01 00 ff` —— 7 字节之后是上一包 187 的残段）。

### 这条数据改变了什么

此前默认“解析流的 size 就是那次 memmove 写入的 size”。这次不成立：
**拷贝长度 7，解析看到的却是 8** ⇒ 在 `assign`（写 size）与构造流（读 size）之间，
size 字段还不是刚刚写入的值，或者被别的东西改过。

把它与历史样本放在一起：

| 样本 | 失败 size | 该包正常大小 | 差值 |
| --- | --- | --- | --- |
| 03:12 | 36 | 35（187） | +1 |
| 12:47 | 20 | —（111） | ? |
| 13:06 | 23 | 7（188） | +16 |
| 15:0x | **8** | 7（188） | **+1** |

差值不固定（+1/+16/−5），而且都围绕“该包正常大小”。这与**复用缓冲在写入与解析之间被并发访存**
（另一个线程写、解析线程读）最吻合：解析线程可能读到“新的几个字节 + 旧的尾巴”，
或读到上一包遗留的 size。

### 相应修正的结论

- 模型 A（帧声明 23）**进一步走弱**：若真有一个前缀声明，四次应当读到四个不同的合法值，
  而实测多是“正常大小 + 小偏移/大偏移”的混合形态。
- 模型 B（复用缓冲残留）**得到直接支持**：源缓冲确实在拷贝区之后保留着上一个包的字节（本次实测）。
- 新的候选根因：**解析缓冲 `ns+0x128` 这个 string 的写入与解析之间缺少同步**（竞态）。
  这能同时解释 size 与内容的不一致，也解释了“只偶发、约 1/6”。

### 工具事故与修复（记录）

钩 `memmove` 后连续两次把管道弄死（进程仍在、无 crash dump）。原因是 detour 内部
`readMemory`/环发布可能再次进入 memmove，形成递归。已加线程级重入保护，
复测同一场景 **管道存活**、检查 37/39（两次失败是 “fly: not_in_world”，即该 run 里世界已丢失）。

### 线程归属（同一次运行内）

同一次运行里按包分 tid：

| 包 | tid |
| --- | --- |
| 111 / 187 / 188 / 39（下行） | **33788**（同一个） |
| 144（上行） | 36128 |

即**下行所有包由同一线程解析**。早前 DR 命中记录到的 size 写入 tid 是 **36148**，但那是
另一个进程实例，跨实例不能直接比较。要判定竞态，必须在**同一次运行**里同时拿到
“写 size 的 tid”与“解析该包的 tid”——这是下一步唯一需要的测量（现有仪器已够：
DR 命中带 tid，入站行带 tid）。

## 24. 同线程否掉竞态；新样本显示缓冲在包中途用尽（2026-11-01 15:1x）

### 同一次运行内的 tid 配对（`[实测]`）

同一进程、`violation_watch(loop_addr)` + `inbound_sample=1` + `watch_set(net_ctx+0x138, w, 8)`：

| 观察 | tid |
| --- | --- |
| 写 `ns+0x138`（DR 命中，2000/2000 条） | **23848** |
| 解析下行包 111/187/188/39 | **23848** |
| 解析上行 144/72 | 30616 |

⇒ **写者与解析器同一个线程**。§23 的“并发写/读竞态”候选**被否**。
“size 与拷贝长度不一致”必须在**程序顺序**内解释，不是时序竞争。

### 新样本：tick 397，111 包 `packet_size = 5`（`[实测]`）

同 tick 的入站环尾部（`out/pair.json`）：

```
397 size 35 id 187  bb010100…
397 size 7  id 188  bc010000000001
397 size 14 id 111  6f160000000000011a0100000002
397 size 21 id 111  6f1401faa5c2c10001f859e5410000000100000004
397 size 23 id 111  6fbb01013d3e1dc10001b3063ec10001e10001000000
397 size 5  id 111  6f10000000        ← 失败帧
397 size 1  id 72   48
```

两点：

1. `[实测]` 失败帧只有 **5 字节**（`6f 10 00 00 00`），而 111 正常要 14–23 字节 ⇒ 流在**包中途用尽**；
2. `[实测]` 紧接着出现一个 **1 字节**的“包”（`48`，解出 id 72）⇒ 游标已经越过有效数据，
   读到了缓冲里的残余字节。

### 修正后的候选机制

把五次样本按方向排：

| 样本 | 包 | 失败 size | 该包正常 | 形态 |
| --- | --- | --- | --- | --- |
| 03:12 | 187 | 36 | 35 | 多 1 |
| 13:06 | 188 | 23 | 7 | 多 16（邻居 payload） |
| 15:0x | 188 | 8 | 7 | 多 1 |
| 15:1x | 111 | **5** | 14–23 | **少**（中途用尽） |

同一线程、同一缓冲、尺寸既能偏大也能偏小 ⇒ 不是“前缀声明错了”，也不是残留，
而是**批循环里“本包长度/游标”的算法与缓冲实际内容不一致**：
某些情况下它按上一包/下一包的长度推进，导致本包被截短或被拉长。

下一步最直接的验证：在批循环里记录**每包的起始游标与长度来源**（不是流本身），
即 `sub_1416AB0E0` 里 `0x1416ab789` 之前那段（`sub_140D935E0` 的入参与返回值），
那里应能看出长度是从哪里取的。

### 追加仪器：每次解析前的“缓冲写入次数”（有缺陷，待修）

在入站行里加了一个 `copies` 字段：自上次解析以来，目标为逐包缓冲的 `memmove` 次数
（`detourMemmove` 里计数，解析入口 `exchange(0)`）。

首轮结果（952 行）：`copies=1` 712 行、`copies=0` 240 行。但**这个计数目前不可用**：
上下行共用同一个 `exchange(0)`，中间夹一个上行（144）解析就会把下行刚累积的计数清掉，
所以 `copies=0` 的行里既有“上行包”，也有“被上行清掉”的下行包 —— 无法据此判断
“失败包是否根本没有被填充”。

修法（下一轮第一步，两行）：只在**同一个缓冲地址**的连续解析之间清零；
即记住上次的 `begin`，地址变化时不清零。
