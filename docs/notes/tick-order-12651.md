# tick 内调度排序调查（1.26.51.01 / LeviLamina 26.51.7）

> 目标：把“tick 内六步”的**执行先后**从纯实测升级为静态证据，回答
> “ActionSprintTriggerSystem 写 ADF bit3 与 WaterTravel 阻力读位/写 `vx` 在同一 tick 内谁先”。
> 计数约定沿用用户口径：**t0 = `v=0` 的那一 tick**。
> 标签：[静态] = 反编译直接读到；[实测] = 运行期观察点/序列；[推断] = 由前两者补出。

状态（2026-10-27，第二轮）：**调度规则已完全静态确定，矛盾已收窄到最后一点**。
- 集合实现已定位并反编译：**注册追加（push_back）、遍历正序、mode=1 只进 normal 列表**
  ⇒ **tick 顺序 = 注册顺序**（见 §8）。
- 于是静态上主 pass（E14/E1C）里 ActionSprintTriggerSystem（`0x14681FBB7`）必然早于
  阻力系统（`0x14682658B`）。
- 但时间线（+2）与同线程观察点都要求同一 tick 内“阻力在写位之前”。**两者不可能同时成立**，
  除非观察到的写位发生在主 pass 之后的另一个 pass（13584/1358C），即主 pass 里那次执行
  没有写位（过滤/状态未就绪），真正的写位在 catchup/correction pass。
- 未定位的只剩：`tickMovementCatchup` / `tickMovementCorrectionReplay` 的**调用点**
  （Level::tick / Level::_subTick / ServerLevel::_subTick 里没有对 `+0x18`/`+0x20` 的虚调用，
  见 §8.4）。

---

## 0. 结论摘要

已经确定（静态）：

1. 每 tick 的调度入口是 `EntitySystemsManager::tickEntitySystems`（`0x141177690`），
   带 profiling 注释记录（`0x14E7A7968`，路径串 `0x14EC11DD5` =
   `handheld/src/common/world/level/EntitySystemsManager.cpp`）。
2. 它按一个 mode 字节二选一，进入两条主 pass：
   - `sub_1463B0B00`：`foreachTickingSystem(filter{word_151DB9E14})`
   - `sub_1463B1070`：`foreachTickingSystem(filter{word_151DB9E1C})`
   两条都先遍历 `(manager+48)+48..56` 的 32 字节项表，调每项 `+16` 对象的虚函数 `+8`。
3. 注册侧存在三套类别跨度：
   - `sub_1466567E0` → `sub_146662E90`：`[E14, 13584, 1358C, E1C]`
   - `sub_1463B13E0`：`[E14, E1C, 13584, 1358C]`（几乎所有注册函数都调它）
   - `sub_142DEE020`：`[E14, E1C]`
   其中 `E14 = word_151DB9E14`、`13584 = word_151E13584`、`1358C = word_151E1358C`、
   `E1C = word_151DB9E1C`。
4. 写 ADF bit3 的是 **ActionSprintTriggerSystem**：注册点 `0x14681FBB7`（名字字面量
   `0x14681FB62`），vtable `0x14EACA020`，虚入口 `0x14B1A9FE0` / `0x14B1AA080`
   → `0x14B1A96C0` → `0x14B1A9F50` → `Actor::setSprinting`（`0x14B17C820`），
   同处把 `SPRINTING_BOOST` 压进属性队列。
5. 水阻力 `0x142DEE630`（读 `*adf & 8`）的调用者 `0x142DEF070` / `0x142DEFBD0` 属于
   vtable `0x14E85AF60` 的系统，构造于 `0x142DE0950`，在 `sub_146822800` 中注册于
   `0x14682658B` 一带。
6. 同一个注册函数 `sub_146822800(a1, a2)`（`a1` = 集合）里的先后：
   `sub_14681DA00(a1, v491)` 于 `0x1468230DA`（sprint/input 大块）→
   `MobTravelUpdateSpeedsSystem` `0x146826032` → `WaterTravelSystem` `0x1468261C6` →
   `LavaTravelSystem` `0x146826260` → `WaterMoveSystem` `0x14682629C` → 抓取系统的构造
   `sub_142DE0950` 于 `0x14682658B`。**sprint 块在先，travel/阻力在后** [静态]。
7. `EntitySystemTickingMode` = `{ bool mNormalTick; bool mSingleTick; }`；上述所有登记点的
   第三参都是 `1`（= normal tick）[静态]。

未确定：

- 如果 `foreachTickingSystem` 按插入序执行，写位应先于阻力；但 [实测] 同线程同 tick 的观察点
  显示阻力写 `vx`（`0x142DEE7BC`）早于写位（`0x14B17C870`）。
- 两种候选解释，均未证实：
  (a) `DefaultEntitySystemsCollection` 的 `foreach` 不是简单插入序（实现未定位）；
  (b) 两次写属于**不同 pass**：ActionSprintTriggerSystem 注册在 4 个类别，travel 组只在 2 个
      类别（`E14, E1C`）。若 `13584/1358C` 对应 movement catchup / correction pass，且它们
      晚于主 pass，则写位可能落在主 pass 之后。

---

## 1. 调度链（静态）

### 1.1 `EntitySystemsManager::tickEntitySystems`（`0x141177690`）

反编译结构（简化）：

```
profiling scope (PredeclaredAnnotation at 0x14E7A7968)
v1 = *(manager + 864)              // 持有 collection / 后处理对象的结构
if (v20 == 1)  sub_1463B1070(*v1, registry);   // 类别 E1C
else if (v20 == 2 || v20 == 0) sub_1463B0B00(*v1, registry);  // 类别 E14
// 其他 mode：不跑
```

`v20` 来自 `v18 = v1[4]; v20 = *(v18+2)`，字节字段，语义未命名。

### 1.2 两条主 pass

```
sub_1463B0B00(a1, registry):
    v6 = a1[6]; v7 = *(v6+48); i = *(v6+56);
    for (; v7 != i; v7 += 32)
        (*(*(v7+16) + 8))(*(v7+16));          // 32 字节项表，虚函数 +8，仅 this
    collection = a1[5];
    filter = { word_151DB9E14 };              // 类别 E14
    collection->foreachTickingSystem(filter, callback);  // vtable +32

sub_1463B1070(a1, registry):
    同样的 32 字节项表循环；
    filter = { word_151DB9E1C };              // 类别 E1C
    collection->foreachTickingSystem(filter, callback);  // vtable +32
```

callback 对象 vtable：`off_14E98AA80` / `off_14E98AB10`（两个不同的 lambda 包装）。

### 1.3 调用者

- `sub_1463B0B00` ← `0x141177910`（tickEntitySystems）与 `sub_1412F6310`
  （`0x145DA7B30` 调用；疑似 initialize/editor 路径，未命名）。
- `sub_1463B1070` ← `0x141177987`（tickEntitySystems）。
- `sub_1463B0DF0` 只有数据引用（`0x14E98A638` 等），疑似某个表里的回调/包装，未确认。

---

## 2. 注册侧（静态）

### 2.1 类别跨度

| helper | 跨度 | 备注 |
|---|---|---|
| `sub_146662E90`（由 `sub_1466567E0` 调用） | `[E14, 13584, 1358C, E1C]` | 构造 4 项 `span` 后调集合虚函数 `+?`（registerTickingSystem） |
| `sub_1463B13E0` | `[E14, E1C, 13584, 1358C]` | 注册函数几乎都调它；一次注册一个系统 |
| `sub_142DEE020` | `[E14, E1C]` | 2 项跨度（travel/water 一侧） |

`sub_1463B13E0` 的形态确认：读四个 TLS 初始化的 typeid 全局 → 填 `span{4, ptr}` →
`guard_dispatch_icall_nop(collection, &span)`。

### 2.2 `sub_146822800` 的注册顺序（行号来自反编译）

```
894   sub_14681DA00(a1, v491)        // sprint/input/attr 大块（含 ActionSprintTrigger 0x14681FBB7）
3144  "MobTravelUpdateSpeedsSystem"
3212  "WaterTravelSystem"
3236  "LavaTravelSystem"
3249  "WaterMoveSystem"
3385  sub_142DE0950(&v466)           // 抓取系统构造（vtable 0x14E85AF60）
...   sub_1466565E0(a1)              // 紧随其后的注册调用
```

注意：行号处 `sub_1466565E0` 实际注册的是 Geyser 相关系统（`"GeyserBoostSystem::MovementTick"`、
`"GeyserBoostSystem::FallingBlocks"`），说明“构造批次 → 注册”在反编译文本里不一定紧邻；
但 travel 的注册点（`0x1468261C6`/`0x146826260`）与抓取系统构造点（`0x14682658B`）都
**晚于** sprint 块（`0x1468230DA`），这一点由地址顺序确定。

---

## 3. 两个主角

### 3.1 ActionSprintTriggerSystem（写位）

- 名字字面量 `"ActionSprintTriggerSystem"` @ `0x14681FB62`；随后构造对象、写 vtable
  `0x14EACA020`、`sub_14B1A9380` 构造 SystemInfo、`0x14681FBED` 注册。
- vtable 布局（7 项，前 5 项与其它系统共用）：
  `0x140076BF0, 0x140076BC0, 0x140A1D4E0, 0x140A1D510, 0x140A1D540` +
  本系统 `0x14B1A9FE0, 0x14B1AA080`。
- 写位链：`0x14B1A9FE0` → `sub_14B1A96C0`（批量遍历实体，回调 `0x14B1A9F50`）→
  `Actor::setSprinting`（`0x14B17C820`，`*a3 = (*a3 & ~8) | 8*a5`，并压 `SPRINTING_BOOST`）。
- 另一入口 `0x14B1AA080` 直接按实体 id 处理（签名 `(a1, a2, int* entityId)`）。

### 3.2 水阻力系统（读位）

- `sub_142DEE630`：读 `*adf & 8`，`vy *= 0.8`，水平 `*= d`。
- 调用者 `0x142DEF070`（0xB5F）与 `0x142DEFBD0`（0x18E）；vtable `0x14E85AF60`
  （前 5 项与前文相同，本系统 `0x142DEF070, 0x142DEFBD0`）。
- 构造：`sub_142DE0950`（`lea rax, off_14E85AF60; mov [obj], rax` 在 `0x142DE09AC`）。
- 注册：`sub_146822800` 中 `0x14682658B` 一带（晚于 sprint 块）。

---

## 4. 矛盾与候选解释

- [实测] 同线程、同 tick：`vx` 写 `0x142DEE7BC`（阻力）早于 ADF bit3 写 `0x14B17C870`
  （`Actor::setSprinting`）。
- [静态] 注册先后：sprint 块早于 travel/阻力。
- 若集合按插入序 tick（append + 正序遍历），写位应先于阻力 ⇒ 与实测冲突。

候选：

1. **foreach 非插入序**：需要 `DefaultEntitySystemsCollection::registerTickingSystem` /
   `foreachTickingSystem` 的实现。该库无 RTTI、无符号、字符串表里也没有类名
   （`find type=string` 对 `DefaultEntitySystemsCollection` / `registerTickingSystem` /
   `foreachTickingSystem` 均为 0 命中），collection vtable 尚未定位。
2. **不同 pass**（更有希望）：sprint 系统在 4 个类别（含 `13584/1358C`），travel 组只在
   `E14/E1C`。若 `13584/1358C` 是 `tickMovementCatchup` / `tickMovementCorrectionReplay`
   使用的类别，且这两个 pass 在主 pass 之后被调用，则写位落在主 pass 之后完全可能。
   待证：这两条 pass 的调用点在 `Level::tick` / `ServerLevel::_subTick` 中的位置。

---

## 5. 证据文件

- `out/rev51\dec_targets.txt`（最近的 `sub_1463B1070` 反编译，会被后续运行覆盖）
- `out/rev51\ev_1431BD020.txt`、`ev_142859E10.txt`、`ev_14207F880.txt`、
  `ev_14B17C820.txt`、`ev_14207FB30.txt`、`ev_144763B20.txt`（SPRINTING_BOOST 相关）
- `out/rev51\ev_146822800.txt`（注册顺序，138 KB）
- `out/rev51\callsite.txt`、`callsites_all.txt`、`names_refs.txt`、
  `refs_step1.txt`、`regions.txt`、`qdump*.txt`、`bytescan.txt` 等扫描输出
- 本调查的 IDA 会话：只读 MCP（`%USERPROFILE%\reverse-skill\work\mc-bedrock-12651-order\`），
  会话名 `mcbr`，库 = `%APPDATA%\levilauncher.exe\versions\1.26.51.01\Minecraft.Windows.exe.i64`。

## 6. 下一步（可直接执行）

1. **Level tick 顺序**（绕开 collection）：
   - pretty-name 池里已有 `Level::tick`（`0x14EF8BBD8`）、`ServerLevel::_subTick`
     （`0x14EF8BC28`）、`Level::_subTick`（`0x14EF8BC59`）、`MinecraftGame::onTick`
     （`0x14EF8BBFF`）。
   - 做法（对 `tickEntitySystems` 已成功验证）：`xrefs_to` 名字串 → 取注释记录数据地址
     → 再 `xrefs_to` 该记录 → 得到函数体；然后读实体 tick / catchup / correction 的调用先后。
2. **collection vtable**：从 `EntitySystems` ctor 的 `mSystems` 初始化点回溯
   `EntitySystemsCollection::create()`；或按布局找 ctor（`+8/+0x20/+0x38/+0x50` 四个 vector、
   `+0x68` dense_map、`+0xa0` mutex）。
3. 确认 `13584/1358C` 的 pass 归属：decompile `tickMovementCatchup` /
   `tickMovementCorrectionReplay`（EntitySystems 的虚函数），看它们用的 filter 类别。

## 7. 操作纪律（本次踩坑）

- **不要复制 `.i64`**：每次打开会解包出 `.id0`（约 4.7 GB）+ `.id1`（约 1.2 GB）；
  多次复制/打开把 D 盘从 39 GB 可用压到 25 GB，C 盘压到 6.5 GB。
- 正确流程：只读 MCP 复用原库（`open_db.py`，`run_auto_analysis:false`）→ 合并查询 → 关闭
  → 删除解包文件（先 `taskkill` supervisor 与 idalib worker，否则 `Device or resource busy`）。
- 结论：静态调查继续用 MCP，不再产生任何 DB 副本。

---

## 8. 第二轮（2026-10-27）：集合实现已完全确定

### 8.1 DefaultEntitySystemsCollection

- 创建函数 `sub_1425C25A0`：`operator new(256)`，写 vtable **`off_14E8285E0`**，
  清零 +8..+120 的成员，`+176` 处 `Mtx_init_in_situ`（计时锁）。
- vtable `0x14E8285E0`（按 IEntitySystemsCollection 顺序）：
  - `+0x00` dtor `0x1425C5B50`
  - `+0x08` **registerTickingSystem `0x1425C5DD0`**
  - `+0x10` registerSystem `0x1425C6A00`
  - `+0x18` foreachSystem `0x1425C6CA0`
  - `+0x20` **foreachTickingSystem `0x1425C6CF0`** → `sub_1425C82D0(this, +0x38 begin, +0x38 end, cat, cb, capture)`
  - `+0x28` **foreachSingleTickingSystem `0x1425C6D20`** → 同样的 helper，用 `+0x50`
  - `+0x30` getLargestSystemIndex `0x1425C6D50`
  - `+0x58` hasSingleTickCategory `0x1425C6F30`
- 结构确认：`mAllSystems` @+0x8（shared_ptr 向量）、`mAllSystemsInfo` @+0x20、
  `mTickingSystemCategories` @+0x38、`mSingleTickingSystemCategories` @+0x50；
  类别项 0x38 字节：`{u16 typeid @+0, vector<uint> mSystems @+8..+16, vector<ECSTiming> @+0x20}`。

### 8.2 registerTickingSystem（`0x1425C5DD0`）——追加语义

```
index = mAllSystems.size();                 // v11 >> 4
mAllSystems.push_back(system);              // 末尾追加（*end = ptr; end += 16）
for (cat : categories_span) {               // a2 = span{count, ptr}
    if (a5 & 1) {                           // mNormalTick
        entry = findOrCreate(cat, this+0x38);
        *entry.mSystems.end = index;        // 末尾追加 uint
        entry.mSystems.end += 4;
    }
    if (a5 & 0x100) {                       // mSingleTick
        entry = findOrCreate(cat, this+0x50);
        *entry.mSystems.end = index; entry.mSystems.end += 4;
    }
}
```

**所有已见登记点的第五参 = 1 ⇒ 只进 normal 列表**（single-tick 列表为空）。

### 8.3 foreach 遍历（`0x1425C82D0`）——正序

```
entry = first category with *(u16*)entry == cat;   // 步长 0x38
if (!entry) return;
for (p = entry->mSystems.begin; p != entry->mSystems.end; ++p) {   // uint，正序
    idx = *p;
    system = mAllSystems[idx];                                      // *(this+8) + 16*idx
    callback(system, {idx});                                        // std::function
}
```

⇒ **tick 顺序 = 注册顺序（正序）**，不存在排序/反转。

### 8.4 EntitySystems 与三个 pass

- EntitySystems vtable **`0x14E98A620`**：
  - `+0x00` dtor `0x1463B8EE0`；`+0x28` dtor thunk `0x1463B8F10`
  - `+0x08` registerTickingSystem `0x1463B1320`：`collection = *(this+0x28)`，转发 span
  - `+0x10` _hasSingleTickCategory `0x1463B1C40`：调 collection `+0x58`（hasSingleTickCategory）
  - `+0x18` **tickMovementCatchup `0x1463B0DF0`**：`foreachTickingSystem(filter{word_151E13584})`
  - `+0x20` **tickMovementCorrectionReplay `0x1463B0F30`**：`foreachTickingSystem(filter{word_151E1358C})`
- ctor `0x1463B01E0`：把传入的 collection 存到 `this+0x28`；vtable 写入在 `0x1463B0277`。
- 构造链：`sub_141133190`（名字串 "Server ticking systems"，`0x141133308`）
  → `sub_1425C25A0`（create collection）→ `sub_1463B01E0`（EntitySystems ctor）。

### 8.5 由此得到的静态结论与最后一点

- 主 pass（E14 或 E1C）里，ActionSprintTriggerSystem（注册 `0x14681FBB7`）**必在**
  阻力系统（构造/注册 `0x14682658B`）**之前**。
- 时间线要求的“同一 tick 内阻力先于写位”与它冲突。
- 唯一自洽的解释：**写位发生在主 pass 之后的 catchup(13584)/correction(1358C) pass**，
  即主 pass 里 ActionSprintTrigger 对玩家没有产生写（状态未就绪/过滤），写位落在后一个 pass；
  而阻力只在 E14/E1C，所以“先看到阻力、后看到写位”。
- 侦察记录：`Level::tick`（`0x1411713D0`）、`Level::_subTick`（`0x1411718C0`）、
  `ServerLevel::_subTick`（`0x14290C070`）内**没有**对 `+0x18`/`+0x20` 的虚调用
  （逐条 call 扫描确认），所以 catchup/correction 的调用点不在 level tick，而在
  client/server 的 movement correction 流程里——这是最后一个待定位点。

### 8.6 复核命令

- 集合：`decompile 0x1425C5DD0`、`0x1425C82D0`、`0x1425C6CF0`、`0x1425C6D20`
- 集合 vtable：`get_bytes 0x14E8285E0`（14 个槽）
- EntitySystems：`decompile 0x1463B1320`、`0x1463B1C40`、`0x1463B0DF0`、`0x1463B0F30`、`0x1463B01E0`
- 创建链：`decompile 0x141133190`（"Server ticking systems"）、`0x1425C25A0`

---

## 9. 第三轮（2026-10-27）：pass 调用点搜索与时间线再核对

### 9.1 EntitySystems 的持有方式

- 工厂 `sub_140871910`：`operator new(64)` 控制块（vtable `off_14E775610`），
  `sub_141133190` 在 `ctl+16` 构造 EntitySystems，返回 `shared_ptr<EntitySystems>`
  （`*a1 = ctl+16`，`a1[1] = ctl`）。
- `sub_141133190` 的调用者：`sub_140871910`（0x140871A71）。
  工厂的引用：`sub_14086EEF0`（0x14086FAC2，取地址）、`sub_1428F3A10`（0x1428F96FE）
  —— 都是 owner 初始化路径，不是 per-tick。
- 结论：pass 的虚调用发生在**持有这个 shared_ptr 的 owner** 的代码里，不在 level tick。

### 9.2 虚调用点扫描结果（都不可用）

- `call qword ptr [reg+18h]/[reg+20h]` 的字节扫描：命中 53,510 条（绝大多数是普通成员函数）。
- 进一步要求“对象指针从成员加载 → 加载 vtable → 取 +0x18/+0x20 槽 → call”的四指令链：
  仍剩 **1,165** 条，无法从中唯一定位 EntitySystems。
- 对 `Level::tick`（0x1411713D0）、`Level::_subTick`（0x1411718C0）、
  `ServerLevel::_subTick`（0x14290C070）逐条 call 扫描：**没有** `+0x18`/`+0x20` 虚调用。
- 结论：这两条 pass 不在 level tick 里，位于 client/server movement correction 代码中；
  在当前工具（无符号、无 RTTI、无字段 xref）下未能在有限时间内唯一定位。

### 9.3 时间线再核对（关键）

- 地面：记录位 T → 属性生效 T+1（`SPRINTING_BOOST` 由 setSprinting 入队，属性系统下一 tick 处理）。
  这要求 **ADF 写位发生在 T 或 T+1 的主 pass**。
- 水面：记录位 T → 0.9 在 T+2。若写位在 T+1 主 pass，且主 pass 内 **写位先于阻力**（§8 已证），
  则 T+1 的阻力应已读到新位 → 应该 T+1 就见效，与 T+2 矛盾。
- 两个约束不能同时由“主 pass + 注册序”满足 ⇒ 必然有一项归属需要修正：
  (a) 观察到的阻力写与写位写不在同一个 pass/同一侧（client vs server）；
  (b) 写位的实际执行不在主 pass（而在 catchup/correction 等后置 pass），
      且主 pass 那次执行对玩家不写。
- 现有静态证据无法在 (a)/(b) 间二选一；需要运行期把**命中记录里的 tick/tid 与调用栈**
  持久化（旧探针 `out/probe_watch_sprint.py` 打印了 `frames`，但没有落盘），
  或在 pass 入口（`0x1463B0DF0` / `0x1463B0F30` / `0x141177690`）打 tick 戳。
  这一步是运行期取证，不属于本轮“静态”范围，但它是闭合该矛盾的唯一剩余手段。

### 9.4 本轮交付

- 调度规则（追加/正序/mode 位）与三条 pass 的类别、EntitySystems 的持有链：**静态确定**。
- “主 pass 内写位先于阻力”：**静态确定**。
- “观察到的 +2 时间线如何与主 pass 顺序共存”：**未定**，卡在 (9.3) 的 (a)/(b) 二选一。

---

## 10. 第四轮（2026-10-27）：动态取证 → 回到静态，pass 判定完成

> 手段：按用户授权启动游戏做**动态抓栈**，再回到静态解释。只为取调用链，不改世界。

### 10.1 实验

- 用 `dev.py run --world "超平坦测试" --allow allow_input_inject=1` 启动（mod DLL 已装入）。
- 硬件观察点：客户端玩家 `ActorDataFlagComponent` 的字节 0（bit3 = SPRINTING），
  `thread="both"`；把 `Watch.cpp` 的 `kMaxFrames` 临时从 8 提到 **20** 重建，拿到完整 20 帧。
- 过程：静止 → 注入前进+疾跑 → 松开；分别 drain 置位/清位窗口。
- 运行时模块基址：`0x7FF4FB3D0000`（由已知静态 RIP 反算，例如 `setSprinting` store
  `0x14B17C870` 对应运行时 `0x7FF64654C870`）。

### 10.2 结果（关键栈）

置位/清位都只由 `Actor::setSprinting`（`0x14B17C820`）的同一个 store 写出；tick 169 的清位栈：

```
f4  0x14B17C870  setSprinting store
f5  0x14B1A9FBC  sub_14B1A9F50（0x400 清位分支）
f6  0x14B190BC9  sub_14B190B20
f7  0x14B1A9F3F  sub_14B1A96C0（ActionSprintTrigger 批量遍历）
f8  0x14B1AA050  sub_14B1A9FE0（该系统的 tick 入口）
f9  0x140A1D4FB  std::function 调用点
f10 0x1463CC2E5  sub_1463CC280（foreach→system 调用回调）
f11 0x1425C846F  sub_1425C82D0（foreach helper）+0x19F
f12 0x1425C6D18  sub_1425C6CF0（foreachTickingSystem）
f13 0x1463B0C27  **sub_1463B0B00（E14 主 dispatcher）+0x127**
f14 0x141177915  **sub_141177690（EntitySystemsManager::tickEntitySystems）**
```

bit-34/脏标记写（`0x148B59CA5`、`0x142DEA943`）的栈也都在 `0x1463B0C27`（E14）之下。

### 10.3 结论（排序定案）

1. **写位发生在 E14 主 pass**（`sub_1463B0B00`），由 `tickEntitySystems` 直接调起；
   **不在** catchup（`0x1463B0DF0`，类别 13584）也不在 correction（`0x1463B0F30`，1358C）。
   补充：钩子实测客户端每 tick 只有 **E14 主 pass + 13584 catchup** 两条（E1C 与 correction 未触发），
   但写位栈只落在 E14。
2. 结合 §8 的集合实现（追加 + 正序）：主 pass 内 **ActionSprintTrigger（注册 0x14681FBB7）
   先于 travel/阻力系统（0x14682658B）** ⇒ **⑤写位先于②阻力**。
3. 因此“阻力先于写位”不是同一 pass 内的系统顺序；水面 +2 只能来自**状态链**：
   记录位 → 输入边沿/疾跑状态 → 下一（或下两）tick 的 ActionSprint 才看到变化并写位；
   写位与阻力同 tick 时，阻力读到的是**新位**。
4. 旧结论“阻力读上一 tick 的位”（来自 8 帧观察点里看到的 vx 写早于位写）**撤回**：
   那两次命中不是同一次 pass 内的可比顺序（或不同 tick），且与已证的注册序矛盾。
   新的可复现判据是 20 帧调用链：`setSprinting ← E14 dispatcher ← tickEntitySystems`。
5. `kMaxFrames` 已改回 8；本次动态只作取证，最终结论由静态（集合实现 + 注册序）与
   调用链证据共同支撑。
