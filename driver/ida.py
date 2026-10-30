"""IDA MCP client that survives the backend's real behaviour.

The idalib supervisor opens an 892 MB `.i64`, which takes minutes and **drops
the HTTP connection** while it does. A naive `idb_open` therefore fails with
`WinError 10054` even though the worker was spawned fine. This wrapper:

* starts the supervisor detached and waits until the endpoint answers;
* opens (or reuses) a session, polling `idb_list` instead of trusting the
  `idb_open` reply, and caches the session id under `out/`;
* retries a call once if the session turns out to be unreachable;
* exposes `decompile` / `disasm` / `xrefs` / `callers` with the right params.

    py -3.11 driver/ida.py status
    py -3.11 driver/ida.py start          # supervisor + wait until ready
    py -3.11 driver/ida.py open           # cache a session (minutes on cold DB)
    py -3.11 driver/ida.py decompile 0x1431C2F50
    py -3.11 driver/ida.py xrefs 0x1431C2F50
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PORT = int(os.environ.get("IDA_MCP_PORT", "8745"))
DEFAULT_URL = os.environ.get("IDA_MCP_URL", f"http://127.0.0.1:{PORT}/mcp")
CALL_TIMEOUT = float(os.environ.get("IDA_MCP_TOOL_TIMEOUT_SEC", "1800"))
CACHE = Path("out") / "ida-session.json"
LOG = Path("out") / "ida-supervisor.log"
# The supervisor does not always bind the port we pass (its config may win), so
# the client discovers the endpoint instead of assuming one.
KNOWN_PORTS = (PORT, 13337, 8745)


class IdaError(RuntimeError):
    """Raised when the backend cannot be reached or refuses the request."""


def default_i64() -> Path:
    explicit = os.environ.get("IDA_I64")
    if explicit:
        return Path(explicit)
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) / "levilauncher.exe" / "versions" if appdata else None
    candidates = sorted(root.glob("*/*.exe.i64")) if root else []
    if not candidates:
        raise IdaError("no *.exe.i64 found; pass --i64 or set IDA_I64")
    return max(candidates, key=lambda path: path.stat().st_mtime)


class Mcp:
    """Minimal streamable-HTTP MCP client (just what the idalib tools need)."""

    def __init__(self, url: str = DEFAULT_URL, timeout: float = 120.0) -> None:
        self.url = url
        self.timeout = timeout
        self.session = ""

    # -- transport ---------------------------------------------------------
    def _post(self, payload: dict[str, Any], timeout: float | None = None) -> str:
        request = urllib.request.Request(self.url, data=json.dumps(payload).encode(), method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json, text/event-stream")
        if self.session:
            request.add_header("mcp-session-id", self.session)
        with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
            self.session = response.headers.get("mcp-session-id") or self.session
            return response.read().decode()

    @staticmethod
    def _unpack(raw: str) -> Any:
        chunk = raw.split("data: ")[-1].strip().splitlines()[0]
        payload = json.loads(chunk)
        if "error" in payload:
            raise IdaError(str(payload["error"]))
        result = payload.get("result", {})
        if result.get("isError"):
            content = result.get("content") or [{}]
            raise IdaError(content[0].get("text", "tool error"))
        content = result.get("content") or []
        text = content[0].get("text", "") if content else json.dumps(result)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def initialize(self, timeout: float = 10.0) -> None:
        self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "levipilot", "version": "0"},
                },
            },
            timeout=timeout,
        )

    def call(self, name: str, timeout: float | None = None, **arguments: Any) -> Any:
        if not self.session:
            self.initialize(timeout=30.0)
        raw = self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            timeout=timeout,
        )
        return self._unpack(raw)

    # -- convenience -------------------------------------------------------
    def list_sessions(self) -> list[dict[str, Any]]:
        reply = self.call("idb_list")
        return reply.get("sessions", []) if isinstance(reply, dict) else []

    def session_healthy(self, database: str) -> bool:
        try:
            self.call("server_health", database=database, timeout=30.0)
            return True
        except Exception:
            return False

    def tools(self) -> list[str]:
        raw = self._post({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
        payload = json.loads(raw.split("data: ")[-1].strip().splitlines()[0])
        return [tool["name"] for tool in payload["result"]["tools"]]


def endpoint_alive(url: str = DEFAULT_URL, timeout: float = 3.0) -> bool:
    host, _, port = url.split("//", 1)[1].split("/", 1)[0].partition(":")
    try:
        with socket.create_connection((host, int(port or 80)), timeout=timeout):
            return True
    except OSError:
        return False


def log_tail(log: Path | None = None, lines: int = 6) -> str:
    """Last lines of the supervisor log, for error messages."""
    try:
        text = (log or LOG).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "  (no log)"
    return "\n".join("  " + line for line in text[-lines:])


def urls_from_log(log: Path | None = None) -> list[str]:
    """Endpoints the supervisor announced, newest last (we own this log)."""
    try:
        text = (log or LOG).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    import re

    found = re.findall(r"http://127\.0\.0\.1:(\d+)/mcp", text)
    return [f"http://127.0.0.1:{port}/mcp" for port in dict.fromkeys(found)]


def discover_url() -> str:
    """First endpoint that answers: env override, our supervisor log, known ports."""
    explicit = os.environ.get("IDA_MCP_URL")
    if explicit and endpoint_alive(explicit):
        return explicit
    for url in urls_from_log():
        if endpoint_alive(url):
            return url
    for port in KNOWN_PORTS:
        url = f"http://127.0.0.1:{port}/mcp"
        if endpoint_alive(url):
            return url
    return DEFAULT_URL


def start_supervisor(i64: Path, *, log: Path | None = None, wait: float = 0.0) -> None:
    """Spawn the supervisor detached; optionally wait until the port answers."""
    if endpoint_alive(discover_url()):
        print(f"supervisor already listening on {discover_url()}", file=sys.stderr)
        return
    if not i64.exists():
        raise IdaError(f"database not found: {i64}")
    log = log or LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = log.open("ab")
    creation = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ida_pro_mcp.idalib_supervisor",
            "--verbose",
            "--port",
            str(PORT),
            str(i64),
        ],
        stdout=handle,
        stderr=handle,
        stdin=subprocess.DEVNULL,
        creationflags=creation,
        close_fds=True,
    )
    print(f"supervisor spawned; log -> {log}", file=sys.stderr)
    deadline = time.monotonic() + wait
    while wait and time.monotonic() < deadline:
        url = discover_url()
        if endpoint_alive(url):
            print(f"endpoint is up: {url}", file=sys.stderr)
            return
        time.sleep(3.0)


def load_cache(url: str) -> str:
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("database", "")) if data.get("url") == url else ""


def store_cache(url: str, database: str, path: Path) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(
        json.dumps({"url": url, "database": database, "path": str(path)}, indent=1),
        encoding="utf-8",
    )


def open_session(mcp: Mcp, i64: Path, *, wait: float = 900.0) -> str:
    """Open the database and return the session id, tolerating a dropped reply."""
    cached = load_cache(mcp.url)
    for session in mcp.list_sessions():
        if session.get("session_id") and session.get("session_id") == cached:
            print(f"reusing session {cached}", file=sys.stderr)
            return cached
    try:
        mcp.call("idb_open", input_path=str(i64), timeout=60.0)
    except (IdaError, urllib.error.URLError, TimeoutError, OSError) as exc:
        # Expected on a cold database: the worker keeps loading after the reply dies.
        print(f"idb_open reply lost ({exc}); polling for the session", file=sys.stderr)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        sessions = [s for s in mcp.list_sessions() if s.get("session_id")]
        if sessions:
            database = str(sessions[0]["session_id"])
            store_cache(mcp.url, database, i64)
            print(f"session {database} ready", file=sys.stderr)
            return database
        time.sleep(5.0)
    raise IdaError(f"no session appeared within {wait:.0f}s. Supervisor log tail:\n{log_tail()}")


def connect(*, open_if_needed: bool = True, wait: float = 900.0) -> tuple[Mcp, str]:
    """Return a client plus a live session id (opening one when needed)."""
    mcp = Mcp(discover_url())
    if not endpoint_alive(mcp.url):
        raise IdaError("IDA MCP endpoint is down; run `py -3.11 driver/ida.py start` first")
    mcp.initialize(timeout=30.0)
    cached = load_cache(mcp.url)
    sessions = {s.get("session_id") for s in mcp.list_sessions()}
    if cached and cached in sessions and mcp.session_healthy(cached):
        return mcp, cached
    if not open_if_needed:
        raise IdaError("no live session; run `py -3.11 driver/ida.py open`")
    return mcp, open_session(mcp, default_i64(), wait=wait)


def call(tool: str, **arguments: Any) -> Any:
    """Call a tool, re-opening the session once if it turns out to be stale."""
    mcp, session = connect()
    try:
        return mcp.call(tool, database=session, **arguments)
    except IdaError as exc:
        if "not reachable" not in str(exc):
            raise
        print("session went stale; re-opening", file=sys.stderr)
        session = open_session(mcp, default_i64(), wait=900.0)
        return mcp.call(tool, database=session, **arguments)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "action",
        choices=[
            "status",
            "start",
            "open",
            "tools",
            "decompile",
            "disasm",
            "xrefs",
            "callers",
            "call",
        ],
    )
    parser.add_argument("target", nargs="?", help="address or tool name")
    parser.add_argument("--i64", type=Path, default=None)
    parser.add_argument("--args", default="{}", help="JSON arguments for `call`")
    parser.add_argument("--wait", type=float, default=900.0, help="seconds to wait for a session")
    parser.add_argument("--out", type=Path, default=None, help="write the result to a file")
    args = parser.parse_args()

    if args.action == "status":
        url = discover_url()
        alive = endpoint_alive(url)
        print(f"endpoint {url}: {'up' if alive else 'down'}")
        if alive:
            mcp = Mcp(url)
            mcp.initialize(timeout=30.0)
            for session in mcp.list_sessions():
                print(
                    f"  session {session.get('session_id')}  "
                    f"pid {session.get('worker_pid')}  busy {session.get('busy')}  "
                    f"{session.get('filename')}"
                )
            print(f"  cached: {load_cache(mcp.url) or '(none)'}")
        return 0

    if args.action == "start":
        start_supervisor(args.i64 or default_i64(), wait=max(args.wait, 1.0))
        url = discover_url()
        print(f"supervisor {'ready on ' + url if endpoint_alive(url) else 'not answering yet'}")
        return 0

    if args.action == "open":
        mcp = Mcp(discover_url())
        if not endpoint_alive(mcp.url):
            raise IdaError("start the supervisor first")
        mcp.initialize(timeout=30.0)
        database = open_session(mcp, args.i64 or default_i64(), wait=args.wait)
        print(database)
        return 0

    if args.action == "tools":
        mcp = Mcp(discover_url())
        mcp.initialize(timeout=30.0)
        print("\n".join(mcp.tools()))
        return 0

    if args.action == "call":
        if not args.target:
            parser.error("call needs a tool name")
        result = call(args.target, **json.loads(args.args))
    else:
        if not args.target:
            parser.error(f"{args.action} needs an address")
        address = args.target if args.target.startswith("0x") else hex(int(args.target, 0))
        if args.action == "decompile":
            result = call("decompile", addr=address, include_addresses=False)
        elif args.action == "disasm":
            result = call("disasm", addr=address)
        elif args.action == "xrefs":
            result = call("xrefs_to", addrs=[address])
        else:
            result = call("xrefs_to", addrs=[address])
            rows = result[0].get("xrefs", []) if isinstance(result, list) and result else []
            result = [row for row in rows if row.get("type") == "code"]

    text = result.get("code", result) if isinstance(result, dict) else result
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=1)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(text)} chars)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
