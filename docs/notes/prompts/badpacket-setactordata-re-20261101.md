# 逆向提示词：入站 SetActorData(39) 反序列化失败（error 104）

用途：交给接手逆向的模型/会话。**本任务只做静态定位**，不改代码、不发包、不装探针。
工作区：本仓库根目录，目标版本 Minecraft Bedrock 1.26.51.01（build 1.26.51.1），
LeviLamina 26.51.7。先读 `README.md`、`AGENTS.md`、`docs/reverse-engineering.md`、
`skills/bedrock-re/SKILL.md`（含 `references/methods.md` 的 A–F 手法）。

## 已有证据（不要重复调查）

`[实测]` 2026-10-31 23:38:53（北京时间）自然断开，守望同时给出两条记录，**同 tick 861、
同 tid 30828**（见 `out/diagnostic-full-final-violation.json`）：

```json
{"source":"callback","synthetic":false,"response":3,"packet_id":39,"packet_size":34,
 "error_value":104,"category":<进程内指针>,"context":"BinaryStream read() incomplete.readNoHeader fai"}
{"source":"screen","synthetic":false,"response":90,"context":"Block",
 "detail":"Minecraft Bedrock.Win,D,,GC-10.0.26100.4351 Err"}
```

`[已有代码证据]` `@client_handle_packet_violation`（角色已在 `docs/roles/1.26.51.01.json`，
静态 VA `0x1412FA0F0`，运行期 = `diag.module_base + (role_va - image_base)`）在
`response == TerminateConnection(3)` 时以 `BadPacket(90)` 关闭连接；`Block` 是 Packet 主题码字。
`[常量]` `MinecraftPacketIds.h`：**SetActorData = 39**（不是能力包；UpdateAbilitiesPacket = 187）。
`[实测]` 事发时 `packet_capture=false`、`chat_sent=0`、模板尝试 0。

## 要回答的问题（按优先级）

1. **读侧**：`SetActorDataPacket` 的 payload 读取路径在哪（`Packet::read` /
   `SetActorDataPacketPayload` 反序列化，或 `SetActorDataPacket::$read` thunk）？
   逐字段列出读取顺序与每个字段的宽度：`mId`(ActorRuntimeID)、`mPackedItems`（`DataItem` 列表，
   含 type id + 值）、`mSynchedProperties`（`PropertySyncData::mIntEntries/mFloatEntries`）、
   `mTick`(PlayerInputTick)。
   **目标**：给出一次“读到 EOF”的精确位置，并说明**34 字节**载荷在哪一步缺多少字节。
2. **error 104**：`context` 是 `BinaryStream read() incomplete / readNoHeader failed` 一类的
   读失败串。找到产生它的 `std::error_code` 枚举/分类，说明 104 对应哪一条
   （是“字节不足”还是“类型不支持”）。**不要**把进程内 category 指针当成可移植值。
3. **写侧**：谁构造并广播 `SetActorDataPacketPayload(ActorRuntimeID, SynchedActorDataEntityWrapper&,
   PropertyComponent*, uint64 tick, bool packAll)`？特别关注：
   - `packAll=true` 的调用点；
   - `Player::setAbility(AbilitiesIndex, bool)`（LLAPI，实现在游戏里）是否会改
     `SynchedActorData`（PLAYER_FLAGS/ABILITIES 一类 data id）并**立即广播** SetActorData；
   - 该调用在 `ServerPlayer` 上的合法线程/时机（是否必须在玩家自己的 tick 内）。
4. **类型宽度**：`SynchedActorData::DataList`（`DataItem`）的序列化表——type id → 写入宽度。
   找出“写侧少写、读侧多读”的候选（例如某个 type 在写入时被降级或跳过）。
5. **属性计数**：`PropertySyncData` 的两个条目数组是否有**计数或索引**可能前后不一致
   （例如属性注册表在会话中途变化、或 `PropertyComponent` 与客户端不同步）。
6. **关联性**：确认 `SetActorData(39)` 与“能力变更（fly/togglefly/reset 恢复）”是同一链路
   还是仅时间相邻——只在能给出调用链/写点时才算成立，时间相邻不算。

## 方法提示

- 先按 `references/methods.md` 的 A（`__FUNCSIG__`/profiler 名 → 记录 → 引用）与 B（vtable/ctor）
  定位类方法；包工厂按 id 39 反查注册点。
- 立即数无 xref：`packAll` 的 `true` 要看反编译上下文。
- 记录角色时地址只进 `docs/roles/1.26.51.01.json`（`image_base=0x140000000` 下的静态 VA，
  `status/confidence/evidence` 按现有格式），不要写进 `.cpp`。
- 反编译大函数先落文件再读；`py -3.11 skills/bedrock-re/tools/ida.py ...`。

## 边界

- 不构造、重放、发送畸形包；不改校验结果/断开决策；不通过吞掉 90/104 掩盖问题。
- 不动态装探针；只读静态结论。若定位不到，写 `unresolved` 并列出已排除的假设。
- 结论分级：`[代码]`/`[常量]` 才可用于定案；`[实测]` 只作旁证；`[推断]` 必须标注。

## 交付

一份带证据等级的结论：读侧失败点（含 34 字节为何不够）、error 104 的语义、
写侧调用链（谁在什么时机广播哪个 payload）、能力变更与它的关系（确认/否定/unresolved）、
以及若要修需要改哪一层（自有 mod 的状态写入 vs 游戏自身路径）。
