"""列出本机 CUA / node-repl 相关的命名管道，用来判断官方电脑操作路径是否已接上。

官方 in-session 路径（mcp__node_repl__js + agent.computerUse）依赖 node-repl 宿主拿到
ZCODE_CUA_PERMISSION_BROKER_SOCKET，此时宿主会建一条 zcode-node-repl-cua-<uuid> 管道。
没有这条管道（或它不属于当前会话）时，调用会抛
"Computer Use is unavailable for this node_repl session" —— 那就改走
driver/native/cua.js 的 helper 通道。

用法：py -3.11 driver/native/check_cua_pipes.py
"""

from __future__ import annotations

import os

PIPE_ROOT = "\\\\.\\pipe\\"
KEYS = ("cua", "zcode", "repl", "permission")


def main() -> int:
    try:
        pipes = os.listdir(PIPE_ROOT)
    except OSError as exc:
        print(f"列管道失败: {exc}")
        return 1
    hits = [p for p in pipes if any(k in p.lower() for k in KEYS)]
    print(f"{len(pipes)} 条管道，命中 {len(hits)} 条：")
    for name in hits:
        marker = "  <= CUA broker" if "cua" in name.lower() and "node-repl-cua" in name.lower() else ""
        print(f"  {name}{marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())