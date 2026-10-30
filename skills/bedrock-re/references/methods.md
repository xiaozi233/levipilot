# L1 方法层：静态定位与证明

> 本文件**不写裸地址、不写具体版本号、不写组件 id 数值**。地址在 `docs/roles/<version>.json`，
> 版本数据在 `docs/offsets/<version>.json`；这里只写可跨版本复用的手法。

## 0. 证据分级

| 标签 | 含义 | 用途 |
| --- | --- | --- |
| `[代码]` | 反编译的控制流、数据流、写入点 | 支撑结论 |
| `[常量]` | 常量、结构布局、表、注册点 | 支撑结论 |
| `[实测]` | 运行期数值、调用栈、观察点、线程 | 现象/旁证；必须由静态解释 |
| `[推断]` | 补出来的中间环节 | 只能标推断 |

`confirmed` 至少要有 `[代码]` 或 `[常量]` 的静态闭环；`[实测]` 不能单独定案。
顺序类结论的强度：**能落到 dispatcher/调用者的调用栈（定位用） + 静态注册顺序/集合实现**；
同 tick 同线程观察点次之；跨线程、跨 pass、跨 tick 的先后不算数。

## 1. 分诊

先判断问题类型，再选手法：

- **数值/归属**（系数多少、谁写的、为什么这个值）→ 常量/数据流。
- **顺序**（谁先谁后、哪条 pass）→ 注册顺序/集合实现/调用图。
- **状态机/分支**（什么条件下走这条）→ 条件链与字段语义。
- **崩溃/协议/反作弊** → 走 `security-router.md`，不要套玩法机制流程。

## 2. 无符号、无 RTTI 时怎么定位（按成功率排序）

IDA 会话用本 skill 的薄封装（参数兼容层，实现在 `driver/ida.py`）。`serve` 会替你
拉起 supervisor 并代传超时；`open` 会复用缓存会话，后端不在时才需要先 `serve`：

```bash
py -3.11 skills/bedrock-re/tools/ida.py open --version <version> --session mcbr
py -3.11 skills/bedrock-re/tools/ida.py call decompile --args '{"address":"0x..."}'
py -3.11 skills/bedrock-re/tools/ida.py call xrefs_to --args '{"address":"0x..."}'
```

大函数先落文件再读；`py_eval` 全内联，不引用外层局部变量。

### A. 美化函数名 + 注释记录 → 代码引用（最有效）

exe 里有 `__FUNCSIG__` 风格的名字和 profiler 源路径。每个名字有一条 `{path, name, line}` 记录，
代码里对这条记录的引用就在函数的静态初始化处：

1. 找名字字符串；
2. `xrefs_to` 名字地址 → 数据记录（名字槽）；
3. 记录起点通常是名字槽 − 8（前面是 path 槽）；
4. `xrefs_to` 记录起点 → 函数里的 `lea`，即目标函数。

`@tick_entity_systems` 和 `@entity_systems_main_pass` 都是这样认出来的。
**先给函数改名、在注释里留下“名字记录 → 引用点”的证据链**，后续所有结论都引用这个角色。

### B. vtable / 构造函数

类方法表在只读段。先找 create/ctor 里写 vtable 的位置（`lea rax, off_XXX; mov [obj], rax`），
再按 SDK 的虚函数顺序给槽位命名。`@systems_register` / `@systems_foreach` 由
collection 的 vtable 与 create 定位；vtable 形状也能区分同名类。

### C. SDK 头文件当“类型模板”

把反编译字段偏移、枚举值、虚表顺序对回 SDK 头文件。例：
`EntitySystemTickingMode { bool mNormalTick; bool mSingleTick; }` ⇒ 登记点第三参 `1`
= `{normal=true, single=false}`；`Dependencies` 只有组件访问组，没有 before/after，
所以**系统顺序只能来自注册顺序**。

### D. 常量扫描 + xref

- 罕见 float/double 先全库扫，再对地址 `xrefs_to`。
- **立即数写入不会产生 xref**（`mov dword ptr [rax], 0BC23D70Ah`）；
  “扫不到引用”不等于“不存在”，要看反编译上下文。
- 反例：`0.026/0.02` 看起来像 1.3 倍，实际是基础加速度字段；最终乘数要沿数据流追到写入点。

### E. 调用者/被调用者

`callees` 只列直接调用；虚调用只出现 `__guard_dispatch_icall_fptr`，没有目标。
需要找虚调用目标时，先找 vtable 槽位，再按槽位反查实现。

### F. 函数内指令扫描（比全段字节扫描可靠）

按基本块遍历 `call` / `mov`，只在目标函数范围内扫。全段 `get_bytes` / `find_bytes`
会把 IDA worker 搞崩；必须全段时用 4 MB 分块，并用 `get_item_head(ea) == ea` 校验指令边界。

### G. 组件 id / 哈希认名

ECS 的运行时 id 和类型名哈希是两套东西。优先用二进制自带的 `[Type = X]` 串、
`__FUNCSIG__` / RTTI mangled name 与 FNV-1a 对撞认名；不要凭 id 数值猜组件。
认名后把“名字 → id → 结构偏移”分别记在 `docs/roles` 与 `docs/offsets`，不要混写。

## 3. 顺序/时序

1. **先画数据流**：谁写字段、谁读字段。数据流错了，顺序一定错。
2. 顺序结论必须落在**同一个执行体**（同线程、同 pass、同函数链）。
3. 调用栈是首选定位证据，但要能落到 dispatcher/调用者；栈被截断时先加深
   （`watch_set(depth=...)`，1..32），再回静态。
4. 频率类结论（每 tick 跑几次、哪些 pass 存在）用入口钩子 + tick 戳，不靠猜。
5. 集合类调度：确认容器是**追加**还是插入、遍历是**正序**还是反序；`register` 的类别位
   决定进入哪条 pass。注册顺序 + 追加 + 正序 = tick 顺序。
6. **注册链要追到 collection 方法**：包装函数 → 类别 id 列表 → collection 的 register
   （`mode & 1` 追加到 `+0x38`）。只看“注册点地址大小”不够，必须证明两个系统进入同一个
   类别向量，并确认 setup 调用点的先后。
7. **反例**：观察点显示“阻力写早于写位”，但 8 帧栈只有 VEH 噪声；加深到 20 帧后看到主 pass，
   集合实现又证明 ActionSprintTrigger 注册在先 ⇒ 写位在前。旧结论撤回。

## 4. 动态取证的边界

允许动态只取：调用栈、线程 id、命中 tick、某函数是否被调用、入口频率。
抓到的**运行时地址必须换算回静态**（模块基址 + RVA）；动态发现必须能回到静态解释，
否则只写“线索/未解释现象”，不写结论。

## 5. 验证与交付

- **float32 逐位模型**：把候选公式写成脚本，用实测序列逐位比对；残差不为零就不是定案。
- **回读验证**：写入类操作后回读；`success=false` 不算判据（方块命令是 deferred）。
- **可复核路径**：函数名/角色、反编译片段、探针原文、`journal` 记录 id。
- **勘误**：旧结论被推翻时写 `erratum_of`，并在 `docs/` 的“已撤回”表同步。
- **可读化**：改局部变量名、建结构体、写注释；不要把 `sub_XXXX` 当名字写进结论。

## 6. 版本边界

可以跨版本复用：数据流画法、注册顺序/集合语义、属性聚合、modifier 乘法、
tick 管线分层、验证与落盘流程。

必须允许版本特例：glitch/兼容行为、输入时序怪癖、数据驱动的实体乘数、版本常量、
某些版本独有的隐藏跳跃/闩锁。`methods.md` 只用角色名；具体版本差异写
`@version-exception` 块（版本范围 + 角色指针），不写裸地址。
