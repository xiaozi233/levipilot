# LeviPilot 热重载验收交接（2026-11-03，停止点）

> **状态更新（同日收尾）**：本文件描述的停止点已被处理，下面的「当前」描述全部过时。
> 结论：命令已改为 `/ll reload probe` 并通过服务端；`unload()`、4 个静态钩子的
> prepare/restore（以 `unhook()` 真实返回值判定状态）、异常过滤器注销、persistent
> 代次/disable_id 与负向回归均已落地，离线 **402/402**、
> ruff/format/mypy/profiles/roles/lint_public 全绿。现场仍**未通过**：`/ll unload probe`
> 的对照里卸载成功且 `probe.dll` 已释放，但 reload 可复现挂起；**具体阻塞点与归属
> unresolved**（`unload: disable returned 1` 在 `return` 之前写出，只证明内部 `disable()`
> 返回）。现行结论见 `docs/reliability-repairs.md` 的 2026-11-03 小节。

本文交给下一位 agent。**先读现状，不要继续发送输入、执行重载、安装 DLL 或提交。**
当前工作包含未验收候选，不能把已有构建或旧验收数字当作完成依据。

## 1. 当前停止点与禁止事项

- 游戏进程为 **PID 24420**，最近读取 `Responding=true`，不是进程无响应；
  但 `screen()` 仍为 **`chat_screen`**。
- 最后一次 `key_event(27, via="hardware")` 没有关闭聊天；随后聚焦成功，但桌面
  `SendInput(Escape)` 返回 **0**，未发出输入。**没有记录 `GetLastError()`，原因未定位，
  不要继续盲发按键。** 停止后未再操作游戏。
- 最近 `status.lifecycle` 为 `generation=337, disable_id=2, running=true`。
  这是 `reactivate` 的结果，**不是一次完整 DLL unload → load**。
- 原 4 键配置 SHA-256 仍为
  `fc33faf25181fc0c0c154e036437792ca981d048880096b34c4b128a3065bc37`。
  最近回读位置为 `(0.5, -49, 4.2753)`、生存模式、无效果、背包为空、宏空闲。
- 强制恢复时 crash 文件为 124 个，未新增；其后不要未经复核就继续宣称数量不变。
- 上一次 `--force` 是用户对那一次卡死的明确授权，**不是后续可随意强停的常驻授权**。
  如果再次需要强停，先取得新的确认，或确认已经正常保存退出。
- 不使用 `reverse-skill`，不新增逆向，不对 IDA/idalib 进程 `taskkill /F`。

## 2. 当前 HEAD、安装产物与源码不一致

HEAD 为 **`18d58c6`**，此轮后续修改尚未提交，未推送。

| 项目 | 当前值 / 含义 |
| --- | --- |
| 安装中的 `mods/probe/probe.dll` | SHA-256 `7541ef5f1663794201ae831f16a211c0dede358f418962b22c90847f93f4163c`；含 `ProbeMod::unload()` 和生命周期计数，**不含后续 prepare 候选** |
| `mod/probe/bin/probe/probe.dll` | SHA-256 `5da041a33018305c0dd7b5309f5e7a0b9e08ef1e1c7103e96716ecc98bdfce77`；只含较早的 `ProbeHardwareKeyHook` prepare 候选，**不是当前完整源码** |
| 当前源码 | 又加入 `ProbeLocalPlayerTickHook` prepare/restore 和桌面 Enter 逻辑，**未做最终构建、安装或端到端验证** |

不要因为 `dev.py --no-build` 能运行，就误以为安装产物等于当前源码。下一次构建前应先审阅
未提交差异；安装前重新核对构建/安装哈希。

当前未提交文件：

- `driver/selftest_lifecycle.py`、`tests/test_selftest_lifecycle.py`、`tests/test_mod_models.py`
- `mod/probe/src/mod/CommandDiagnostics.cpp`、`InputEvents.cpp`、`Internal.h`
- `mod/probe/src/mod/ProbeMod.cpp`、`ProbeMod.h`
- 新增 `mod/probe/src/mod/LifecycleModel.h`、`tests/native/lifecycle_model.cpp`
- `AGENTS.md`、`docs/code-quality-review.md`、`docs/mod-internals.md`
- `docs/reliability-repairs.md`、`mod/README.md`

`out/` 中的脚本、报告和日志是 ignored 证据，不是受跟踪的完成代码。

## 3. 已确认的事实与证据

### 3.1 旧 9/9 是误报，已撤回

原报告 `out/hot-reload-13638828000000.json` 的聊天回读为：

> `§c语法错误：意外的“mod”：出现在“/ll >>mod<< reload pr”`

旧 `check_hot_reload()` 只验证输入成功、管道重连和 tick 推进，没有验证实例变化。
因此旧 **9/9 不能证明热重载**。原始报告、日志均保留，没有改写。

LeviLamina 注册代码 `refs/levilamina-26.51.7/src/ll/core/command/ModManage.cpp`
显示 `reload` 是 `/ll` 的操作参数，正确形式为：

```text
/ll reload probe
```

`/ll mod reload probe` 多了一层不存在的 `mod` 字面量。

### 3.2 拒绝路径有真实回执

`ll reload __levipilot_missing__` 返回：

- `command_done.success=false`
- 聊天：`找不到模组 __levipilot_missing__`
- lifecycle 不变，boot 日志没有 disable/enable。

证据：`out/review-20261103-reload-command-baseline.json`。

正确命令进入重载流程后，曾返回：

> `模组 probe 重载失败` / `该模组未注册卸载函数`

这暴露了第二个独立问题：`ProbeMod` 原来只有 `load/enable/disable`，没有 `unload()`。
`refs/levilamina-26.51.7/src/ll/api/mod/RegisterHelper.h` 的 `Unloadable<T>`
要求 `bool unload()`。已在当前安装 DLL 中实现 `ProbeMod::unload()`，直接返回 `disable()`；
第二次 disable 保持幂等，不重复生成 disable 序号。

### 3.3 `reactivate` 证明 disable → enable 正常

`out/review-20261103-reactivate.json`：

- `disable_id: 1 → 2`
- `generation: 336 → 337`
- `disable: begin/done lifecycle=336 disable_id=2`
- `enable: begin/done lifecycle=337`
- 管道恢复，世界和输入仍正常。

这证明 enable 和持久计数可以工作，**但 `reactivate` 不释放 DLL，不能替代真正的 reload 验收**。

### 3.4 真正 reload 曾卡死，已按用户授权强制恢复

另一次 `/ll reload probe` 的旧进程 PID 34004：

- `disable: begin lifecycle=335 disable_id=1`
- `disable: done lifecycle=335 disable_id=1 ok=1`
- 之后没有新的 `load/enable`，管道消失，`Responding=false`。

`out/review-20261103-reload-stuck-recover.log` 显示非强制恢复因无法确认保存状态而拒绝。
用户随后明确回复“允许 --force”，才执行
`out/review-20261103-reload-stuck-force-recover.log` 中的强停和重启。
**没有证据证明具体卡死函数，不能写成已经确认是 loader lock 或某个 hook 的定论。**

### 3.5 输入路径现象

- `Keyboard::_inputs`（`via="queue"`）能打开聊天，但 Enter 没有提交，最后停在聊天界面。
- 普通聊天提交仍应使用现有 Hardware HID 路径；不要用 queue 失败来构造成功证据。
- `ProbeLocalPlayerTickHook` 在 `mod/probe/src/mod/ProbeMod.cpp` 中调用
  `onPlayerTick → origin() → afterPlayerTick`，所以整个原始 tick 都可能处在 probe detour 内。
- `ProbeHardwareKeyHook` 也包裹 `onKeyDown`。
- 这些是候选的嵌套回调风险，**仍需验证，不能把假设写成根因**。

### 3.6 持久生命周期实现和离线结果

新增 `LifecycleModel.h` 从追加式 `harness-boot.log` 恢复计数：

- `enable: begin/done lifecycle=N`
- `disable: begin/done lifecycle=N disable_id=M ok=1`

`status.lifecycle` 暴露 `generation/disable_id/running`。
`tests/native/lifecycle_model.cpp` 已通过解析回归。

在该阶段完整离线为 **402/402**，见 `out/review-20261103-reload-lifecycle-offline.log`。
后续 prepare、桌面 Enter、PlayerTick 管理加入后，**尚未重跑完整离线和最终 C++ 构建**。
不得用 402/402 或更早的 399/399、1097/1097 证明当前候选通过。

200000 tick 长录制已有独立完整记录，**无需因热重载问题重跑**。

## 4. 当前未验收候选的内容

`driver/selftest_lifecycle.py` 当前包含：

- 正确命令 `RELOAD_COMMAND = "ll reload probe"`。
- 先跑不存在的模组，要求 `success=false` 且代次不变。
- 要求 `hot_reload_lifecycle_changed`，不能仅凭管道重连或 tick 通过。
- 要求 boot 中同时存在旧 disable 和新 enable 记录。
- `lifecycle_prepare` 准备与恢复钩子，使用 `preserve_failure` 保留首错。
- `_send_enter()` 通过桌面 `SendInput` 提交，支持 `send_enter` 替身参数供离线测试。

`CommandDiagnostics.cpp` 当前有自测门控的 `lifecycle_prepare`，默认受
`allow_selftest_stress` 限制；`ProbeMod` 有 `setLifecycleKeyHook()` 和
`setLifecyclePlayerHook()`。**这些 prepare 修改未安装、未做端到端验证。**

`_send_enter()` 的结构体及 `SendInput` 返回值尚未在真实重载中验证。
另一次 Escape 返回 0，下一步应先记录 `ctypes.get_last_error()`、结构体大小和窗口/权限状态；
优先考虑可用的官方桌面只读/输入工具，不要连续发送随机按键。

## 5. 下一位 agent 的建议顺序

1. **先处理聊天状态**：用官方桌面工具或用户手动方式关闭聊天，回读
   `screen == "hud_screen"`、`state.in_world == true`。不要先执行 reload。
   如果必须强停，重新取得明确授权。
2. 审阅未提交差异和构建/安装哈希，确认候选是否值得保留。不要把 `out/` 中脚本当作验收通过。
3. 先修 prepare/restore 及离线替身的正确性：
   - 未发生 reload 时，lifecycle 和 boot 检查必须失败。
   - 拒绝命令未返回失败时，不进入 prepare，也不触发 reload。
   - prepare 失败时不发送 Enter；恢复失败不能覆盖首错。
   - 不要把 prepare 描述成“已证明生产环境任意在途卸载安全”。
4. 在聊天恢复正常、输入空闲、宏空闲后，重新构建并安装，核对哈希，再运行：

   ```bash
   py -3.11 driver/selftest.py --hot-reload-only --require-complete
   ```

   正向必须同时取得：命令实际进入重载的证据、同进程 generation/disable_id 增长、
   旧 disable 和新 enable 日志、恢复后的状态与 crash/config/DLL 完整性。
   `reactivate` 不能代替该检查。
5. 若仍卡死，保留现场并停止，按 recover 门禁处理；不要放宽检查、改计数或把重连当成 reload。
6. 只有真实专项通过后才运行完整离线、静态检查和适用的现场回归，再恢复配置/姿态并更新文档。
   当前 prepare 候选没有本地提交，不应作为完成项提交或推送。

## 6. 继续保留的后续边界

- HBUI 异常退档的形成根因未闭合，已有诊断和正常恢复不等于解决。
- 持键或请求在途时的卸载未验收；不准备支持时应明确禁止并检查前置状态。
- Session 封装、自检拆分和 PacketTap 显式诊断开销属于持续维护项。
- 游戏侧共享序列化缓冲竞态仍是外部限制，不能由本验收推断已根治。
- 本轮没有新增逆向。现有证据主要来自 SDK 源码、boot/chat 回读和有限现场；
  具体卡死根因仍待静态调用栈/线程证据支持，不要靠猜测补齐。
