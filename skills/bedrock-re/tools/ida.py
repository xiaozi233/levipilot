#!/usr/bin/env python3
"""Thin, read-only IDA MCP client with sane defaults.

This is the replacement for the per-case ``mcp.py`` / ``open_db.py`` copies.
It never copies or re-analyzes a database; ``open`` reuses an existing ``.i64``.

Examples::

    py -3.11 skills/bedrock-re/tools/ida.py health
    py -3.11 skills/bedrock-re/tools/ida.py tools
    py -3.11 skills/bedrock-re/tools/ida.py open --version 1.26.51.01 --session mcbr
    py -3.11 skills/bedrock-re/tools/ida.py call decompile --args '{"address":"0x..."}'
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_URL = "http://127.0.0.1:13337/mcp"
DEFAULT_TIMEOUT = 900.0
OPEN_TIMEOUT = 3500.0


def appdata() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))


def default_db(version: str) -> Path:
    return (
        appdata()
        / "levilauncher.exe"
        / "versions"
        / version
        / "Minecraft.Windows.exe.i64"
    )


def rpc(url: str, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", "replace")
    if raw.startswith("event:") or "\ndata:" in raw:
        for line in raw.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                break
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("MCP reply is not a JSON object")
    return data


def call_tool(
    url: str, name: str, arguments: dict[str, Any], timeout: float
) -> Any:
    reply = rpc(url, "tools/call", {"name": name, "arguments": arguments}, timeout)
    if "error" in reply:
        raise RuntimeError(json.dumps(reply["error"], ensure_ascii=False))
    result = reply.get("result", {})
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    texts = [
        item.get("text", "")
        for item in result.get("content", [])
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    joined = "\n".join(texts)
    if texts:
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return joined
    return result


def print_result(value: Any) -> None:
    if isinstance(value, str):
        print(value)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=1))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("IDA_MCP_URL", DEFAULT_URL))
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--dry-run", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_health = sub.add_parser("health", help="call server_health")
    p_health.add_argument("--session", default="mcbr")

    sub.add_parser("tools", help="list MCP tools")

    p_open = sub.add_parser("open", help="open an existing .i64 read-only")
    p_open.add_argument("--version", default="1.26.51.01")
    p_open.add_argument("--db", type=Path)
    p_open.add_argument("--session", default="mcbr")

    p_call = sub.add_parser("call", help="call a tool by name")
    p_call.add_argument("tool")
    p_call.add_argument("--args", default="{}")
    args = parser.parse_args(argv)

    try:
        if args.command == "health":
            if args.dry_run:
                print(json.dumps({"tool": "server_health", "args": {"database": args.session}}))
                return 0
            print_result(call_tool(args.url, "server_health", {"database": args.session}, args.timeout))
            return 0
        if args.command == "tools":
            if args.dry_run:
                print(json.dumps({"method": "tools/list"}))
                return 0
            reply = rpc(args.url, "tools/list", {}, args.timeout)
            for tool in reply.get("result", {}).get("tools", []):
                print(tool.get("name"))
            return 0
        if args.command == "open":
            db = args.db or default_db(args.version)
            arguments = {
                "input_path": str(db),
                "run_auto_analysis": False,
                "preferred_session_id": args.session,
                "mode": "prefer_headless",
            }
            if args.dry_run:
                print(json.dumps({"tool": "idb_open", "args": arguments}, ensure_ascii=False))
                return 0
            if not db.exists():
                print(f"ida: database not found: {db}", file=sys.stderr)
                return 1
            print_result(call_tool(args.url, "idb_open", arguments, OPEN_TIMEOUT))
            return 0
        arguments = json.loads(args.args)
        if not isinstance(arguments, dict):
            print("ida: --args must be a JSON object", file=sys.stderr)
            return 2
        if args.dry_run:
            print(json.dumps({"tool": args.tool, "args": arguments}, ensure_ascii=False))
            return 0
        print_result(call_tool(args.url, args.tool, arguments, args.timeout))
        return 0
    except urllib.error.URLError as exc:
        print(f"ida: cannot reach {args.url}: {exc}", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ida: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
