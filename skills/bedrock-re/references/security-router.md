# 非 MCBE 的逆向/安全任务路由

本文件取代旧的 `reverse-security` 路由器，保留同样的职责，但不再硬编码个人绝对路径。

## 1. 本地 reverse-skill pack

完整技能包安装在用户级目录（不在本仓库）：

```
%USERPROFILE%\reverse-skill
```

可用环境变量 `REVERSE_SKILL_ROOT` 覆盖。**原地读取，不要复制出来**，否则相对引用和脚本会断。

触发顺序：

1. 读 `<root>\skills\SKILL.md`（路由入口），再读 `<root>\skills\routing.md` 选子技能；
2. 执行 `<root>\skills\<name>\SKILL.md` 的流程与脚本；
3. 工具索引在 `<root>\skills\tool-index.md` / `tool-index.json`；
   缺 jadx、radare2、Ghidra 等工具时按子技能说明按需 bootstrap，
   大下载先确认；
4. 只做**授权目标**：自有系统、CTF 靶场、明确授权的委托。越界请求直接拒绝。

## 2. 与 MCBE 的边界

- 玩法机制（方块/移动/流体/ECS/数值）→ 回 `SKILL.md` 的静态优先闭环。
- 崩溃、协议、反作弊绕过、客户端加固 → 走 pack 的 `ida-reverse` / `protocol-reverse` 等子技能；
  不要套玩法机制流程。
- 需要 IDA 会话时，优先用本 skill 的 `tools/ida.py`（只读、超时、会话默认）；
  pack 里的 per-case `mcp.py`/`open_db.py` 是历史路径，不再复制。

## 3. 安全边界

- 不协助未授权的入侵、破坏、批量攻击或规避检测。
- 双用途工具（C2、凭据测试、exploit 开发）只接受明确授权场景（渗透测试、CTF、
  安全研究、防御用途）。
- 动态分析恶意样本时先隔离环境；不要把样本或凭据写进公开仓库。
