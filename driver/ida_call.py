"""直连 IDA supervisor 的 MCP HTTP 端点（ZCode 只暴露了少数工具时的兜底）。

ZCode 注入的 IDA 工具子集不含 `find_bytes` / `py_eval` / `xrefs_to` 等，
而 `find_regex` 在 34 万条字符串上会超过工具 30 秒上限。这里直接按 MCP
streamable-HTTP 协议调 supervisor（默认 127.0.0.1:13337），可以只做**限定范围**
的查询，避免全表扫描。

用法：
  py -3.11 driver/ida_call.py server_health
  py -3.11 driver/ida_call.py find_bytes --arg patterns='["73 65 74 62 6C 6F 63 6B 00"]' --arg limit=20
  py -3.11 driver/ida_call.py xrefs_to --arg address=0x141234567
  py -3.11 driver/ida_call.py --list

环境变量：IDA_MCP_URL（默认 http://127.0.0.1:13337/mcp）、IDA_MCP_DB（默认 mcbr）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

URL = os.environ.get("IDA_MCP_URL", "http://127.0.0.1:13337/mcp")
DB = os.environ.get("IDA_MCP_DB", "mcbr")


class McpError(RuntimeError):
    pass


def _post(payload: dict, session: str | None) -> tuple[dict | None, str | None]:
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(URL, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise McpError(f"HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise McpError(f"连接失败（{URL}）: {exc}") from exc

    # Streamable HTTP 可能返回 SSE 帧；取最后一个 data: 行。
    text = raw.strip()
    if text.startswith("event:") or text.startswith("data:"):
        payloads = [
            line[len("data:"):].strip()
            for line in text.splitlines()
            if line.startswith("data:")
        ]
        text = payloads[-1] if payloads else ""
    if not text:
        return None, sid
    return json.loads(text), sid


def connect() -> str:
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mcbe-harness", "version": "1"},
        },
    }
    reply, sid = _post(init, None)
    if sid is None:
        sid = str(uuid.uuid4())
    # 通知（不需要响应）
    try:
        _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    except McpError:
        pass
    if not reply:
        return sid
    return sid


def call_tool(name: str, arguments: dict, session: str | None = None) -> dict:
    sid = session or connect()
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    reply, _ = _post(payload, sid)
    if not reply:
        raise McpError("空响应")
    if "error" in reply:
        raise McpError(json.dumps(reply["error"], ensure_ascii=False))
    result = reply.get("result") or {}
    if result.get("isError"):
        raise McpError(json.dumps(result.get("content"), ensure_ascii=False))
    return result.get("structuredContent") or result


def parse_args(argv: list[str]) -> tuple[str, dict]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="列出可用工具")
    parser.add_argument("tool", nargs="?")
    parser.add_argument("--arg", action="append", default=[], metavar="KEY=JSON")
    ns = parser.parse_args(argv)
    if ns.list:
        return "", {}
    if not ns.tool:
        parser.error("需要工具名")
    args: dict = {}
    for item in ns.arg:
        key, _, raw = item.partition("=")
        try:
            args[key] = json.loads(raw)
        except json.JSONDecodeError:
            args[key] = raw
    return ns.tool, args


def main(argv: list[str]) -> int:
    tool, args = parse_args(argv)
    try:
        sid = connect()
        if not tool:
            reply, _ = _post(
                {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, sid
            )
            names = [t.get("name") for t in (reply or {}).get("result", {}).get("tools", [])]
            print("\n".join(sorted(n for n in names if n)))
            return 0
        if tool not in ("idb_open", "idb_list"):
            args.setdefault("database", DB)
        print(json.dumps(call_tool(tool, args, sid), ensure_ascii=False, indent=2))
        return 0
    except McpError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))