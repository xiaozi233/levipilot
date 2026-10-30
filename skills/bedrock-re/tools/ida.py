#!/usr/bin/env python3
"""Skill-side entry point for the IDA MCP client.

The implementation lives in [`driver/ida.py`](../../../driver/ida.py): endpoint
discovery, the supervisor spawn, retrying a lost ``idb_open`` reply, the session
cache under ``out/`` and the log tail in errors. This file only keeps the
command names the skill's references use:

    py -3.11 skills/bedrock-re/tools/ida.py health
    py -3.11 skills/bedrock-re/tools/ida.py tools
    py -3.11 skills/bedrock-re/tools/ida.py serve --tool-timeout 1800
    py -3.11 skills/bedrock-re/tools/ida.py open --version 1.26.51.01
    py -3.11 skills/bedrock-re/tools/ida.py call decompile --args '{"addr": "0x1431C2F50"}'
    py -3.11 skills/bedrock-re/tools/ida.py decompile 0x1431C2F50

``open --session <name>`` is accepted for compatibility: the backend assigns the
session id and the client caches it, so the name itself is not used.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from driver import ida  # noqa: E402


def database_for(version: str | None) -> Path:
    """``<APPDATA>/levilauncher.exe/versions/<version>/Minecraft.Windows.exe.i64``."""
    if version is None:
        return ida.default_i64()
    appdata = Path(os.environ.get("APPDATA", ""))
    return appdata / "levilauncher.exe" / "versions" / version / "Minecraft.Windows.exe.i64"


def health() -> int:
    url = ida.discover_url()
    alive = ida.endpoint_alive(url)
    print(json.dumps({"event": "status", "url": url, "alive": alive}))
    if not alive:
        return 1
    client = ida.Mcp(url)
    client.initialize(timeout=30.0)
    for session in client.list_sessions():
        print(json.dumps({"event": "session", **session}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="print the request instead of sending it"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("health", help="report the endpoint and its sessions")
    commands.add_parser("tools", help="list the backend's tools")

    serve = commands.add_parser("serve", help="start the supervisor and wait for it")
    serve.add_argument("--tool-timeout", type=float, default=1800.0)
    serve.add_argument("--port", type=int, default=ida.PORT)
    serve.add_argument("--wait", type=float, default=600.0)
    serve.add_argument("--log", type=Path, default=None)
    serve.add_argument("--version", default=None, help="game version directory name")

    open_cmd = commands.add_parser("open", help="open (or reuse) a session")
    open_cmd.add_argument("--version", default=None, help="game version directory name")
    open_cmd.add_argument("--session", default=None, help="accepted for compatibility")
    open_cmd.add_argument("--wait", type=float, default=900.0)

    call = commands.add_parser("call", help="call a backend tool directly")
    call.add_argument("tool")
    call.add_argument("--args", default="{}")

    for name in ("decompile", "disasm", "xrefs"):
        single = commands.add_parser(name, help=f"{name} one address")
        single.add_argument("address")

    args = parser.parse_args()

    if args.command == "health":
        if args.dry_run:
            print(json.dumps({"method": "tools/list", "note": "dry run"}))
            return 0
        return health()

    if args.command == "tools":
        if args.dry_run:
            print(json.dumps({"method": "tools/list"}))
            return 0
        client = ida.Mcp(ida.discover_url())
        client.initialize(timeout=30.0)
        print("\n".join(client.tools()))
        return 0

    if args.command == "serve":
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "method": "idalib_supervisor",
                        "input_path": str(database_for(args.version)),
                        "tool_timeout": args.tool_timeout,
                    }
                )
            )
            return 0
        # The supervisor reads this at startup; the worker's first open runs
        # under it, and a multi-GB database needs minutes.
        ida.CALL_TIMEOUT = args.tool_timeout
        ida.PORT = args.port
        ida.start_supervisor(database_for(args.version), log=args.log, wait=args.wait)
        url = ida.discover_url()
        ok = ida.endpoint_alive(url)
        print(json.dumps({"event": "served" if ok else "pending", "url": url}))
        return 0 if ok else 1

    if args.command == "open":
        if args.dry_run:
            print(json.dumps({"method": "idb_open", "input_path": str(database_for(args.version))}))
            return 0
        client = ida.Mcp(ida.discover_url())
        if not ida.endpoint_alive(client.url):
            raise SystemExit("start the supervisor first: ida.py serve")
        client.initialize(timeout=30.0)
        session = ida.open_session(client, database_for(args.version), wait=args.wait)
        print(json.dumps({"event": "opened", "database": session}))
        return 0

    if args.command == "call":
        arguments = json.loads(args.args)
        # The older skill text used `address`; the MCP tools want `addr`
        # (`addrs` for xrefs_to).
        if "address" in arguments:
            key = "addrs" if args.tool == "xrefs_to" else "addr"
            arguments.setdefault(key, arguments.pop("address"))
        if args.dry_run:
            print(json.dumps({"tool": args.tool, "args": arguments}, ensure_ascii=False))
            return 0
        result = ida.call(args.tool, **arguments)
        print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False))
        return 0

    address = args.address if args.address.startswith("0x") else hex(int(args.address, 0))
    if args.dry_run:
        print(json.dumps({"tool": args.command, "addr": address}))
        return 0
    if args.command == "decompile":
        result = ida.call("decompile", addr=address, include_addresses=False)
    elif args.command == "disasm":
        result = ida.call("disasm", addr=address)
    else:
        result = ida.call("xrefs_to", addrs=[address])
    text = result.get("code", result) if isinstance(result, dict) else result
    print(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
