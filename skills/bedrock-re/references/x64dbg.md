# x64dbg：何时用、怎么用（内化自 reverse-skill pack）

来源：本地 reverse-skill pack（`%USERPROFILE%\reverse-skill`，
`skills/reverse-engineering/tools-dynamic.md`、`references/re-agent-workflow.md`、
`skills/malware-analysis/references/anti-analysis-techniques.md`、`skills/routing.md`），
加上本仓库自己的实测补充（标 `[本仓库实测]`）。

## 0. 定位：动态验证，不替代静态

| 场景 | 工具 |
| --- | --- |
| 结构/控制流/写点/注册顺序 | IDA（本 skill 的 `tools/ida.py`） |
| 静态假设需要**跑起来对质**（哪个分支、真实参数、崩溃点） | **x64dbg** |
| 加壳/自解密/IAT 修复 | x64dbg + Scylla |
| 反调试/反虚拟机识别 | 先读 pack 的 `malware-analysis/references/anti-analysis-techniques.md` |

铁律不变：动态只用于**缩小范围 / 对质静态模型 / 证伪**；结论仍要能落回
`[代码]`/`[常量]`（见 `../SKILL.md` §2）。

## 1. 启动与基本操作（pack 原样）

```bash
x64dbg.exe binary.exe     # 64 位目标
x32dbg.exe binary.exe     # 32 位目标
```

快捷键：`F2` 断点 · `F7` 步进 · `F8` 步过 · `F9` 运行 · `Ctrl+G` 跳地址 · `Ctrl+F` 内存搜模式。

命令栏：

```
bp 0x401234                                   // 软件断点
bphws 0x<addr>, w, 8                          // 硬件写断点（8 字节）
SetBPX 0x401234, 0, "log {s:utf8@[rsp+8]}"    // 命中即记录（不中断）
run / StepOver / StepInto
```

`bphws` 是可用的硬件观察手段，**不受“不能改目标代码”限制**；配合
`SetBPXCondition` 只在满足条件时中断，避免逐包中断。

## 2. 断点起手式（补丁 7 + 10，顺序不变）

在用户态调试器启动样本前，按「四级火箭」预置：

1. **TLS 回调**断点（EP 之前就可能已执行）；
2. **入口点 EP**；
3. **敏感 API**（`CreateRemoteThread` / 网络 / 文件写）；
4. **保底**：`ExitProcess` / 进程退出路径。

> 若因反调试直接退出，**不要急着重启**：先 dump 内存、记录崩溃前镜像路径作为 Evidence，
> 再谈恢复。

## 3. 常见工作流（pack 原样 + 本仓库补充）

- GUI crackme：断 `GetWindowTextA` / `MessageBoxA`，从成功/失败提示回追；
- 加壳：`Scylla` 修 IAT；`Snowman` 出快速伪 C；`xAnalyzer` 自动标注函数参数；
- 单条写点定位：**硬件写断点 + 条件**比单步快得多（本仓库用它定位过
  “谁写了某个字段”，见 `docs/notes/badpacket-setactordata-20261031.md` §21）。

## 4. 反调试识别（pack 的 anti-analysis 摘要）

样本/沙箱会点名这些进程与窗口标题：`x64dbg.exe`、`ida64.exe`、`ollydbg.exe`、
`windbg.exe`、`Wireshark`、`Process Hacker`。分析前先看目标是否做这类枚举；
必要时改进程名/窗口标题或换机器，**不要**在授权环境外尝试绕过。

## 5. 本机集成（`[本仓库实测]`）

- 插件：`SetsunaYukiOvO/x64dbg-mcp` v1.0.12 → `D:\x64dbg\release\x64\plugins\x64dbg_mcp.dp64`；
  配置 `plugins/x64dbg-mcp/config.json` 里 `auto_start_mcp_on_plugin_load=true` 后
  **自动监听 `127.0.0.1:3000`**，端点 `POST /mcp`，默认无鉴权；
- 客户端：本仓库 `out/x64mcp.py`（Streamable HTTP + `tools/list` + `tools/call`）；
- 可用工具：`debug_init / debug_attach_pid / debug_run / breakpoint_set* / stack_get_trace /
  memory_read / module_list / script_execute`（79 个）；
- 旧插件 `x64dbg-MCP-Server.dp64`（duty1g 版）已改名 `.disabled`：它需要手动
  `StartMCPServer`、Bearer token（`mcp_config.json`）、且 attach 同样失败。

### 两个实测限制

1. **attach 到 Minecraft.Windows.exe 被操作系统拒绝**：`debug_attach_pid` 失败，
   直接 `DebugActiveProcess` 返回 **87 (ERROR_INVALID_PARAMETER)**；
   notepad 对照成功。排除项：完整性级别（都是 Medium 0x2000）、位数（原生 x64，非 WOW64）、
   管理员实例（仍失败）。可行替代是 `debug_init`（LoadBinary）让调试器自己启动目标。
2. **与本仓库 mod 的 DR 冲突**：mod 的 `watch_set` 占用 DR0–DR3，与调试器硬件断点同一组寄存器；
   接调试器时不要 arm `watch_set`（解析链上的普通 hook 不占 DR，可以保留）。

## 6. 授权边界

只做自有系统、CTF 靶场、明确授权的委托。对生产/第三方目标做动态调试前必须有书面授权；
本文件里的技巧不构成对该边界的放宽。
