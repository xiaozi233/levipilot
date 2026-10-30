from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path
from typing import Any

from driver import ida


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LogDiscoveryTests(unittest.TestCase):
    def test_urls_from_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "sup.log"
            log.write_text(
                "INFO:zeromcp.mcp:  Streamable HTTP: http://127.0.0.1:8745/mcp\n"
                "INFO:zeromcp.mcp:  SSE: http://127.0.0.1:8745/sse\n"
                "INFO:zeromcp.mcp:  Streamable HTTP: http://127.0.0.1:13337/mcp\n",
                encoding="utf-8",
            )
            self.assertEqual(
                ida.urls_from_log(log),
                ["http://127.0.0.1:8745/mcp", "http://127.0.0.1:13337/mcp"],
            )

    def test_missing_log_is_empty(self) -> None:
        self.assertEqual(ida.urls_from_log(Path("does-not-exist.log")), [])
        self.assertIn("no log", ida.log_tail(Path("does-not-exist.log")))

    def test_log_tail_keeps_last_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "sup.log"
            log.write_text("a\nb\nc\nd\n", encoding="utf-8")
            self.assertEqual(ida.log_tail(log, lines=2), "  c\n  d")


class EndpointTests(unittest.TestCase):
    def test_endpoint_alive(self) -> None:
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            self.assertTrue(ida.endpoint_alive(f"http://127.0.0.1:{port}/mcp"))
        closed = free_port()
        self.assertFalse(ida.endpoint_alive(f"http://127.0.0.1:{closed}/mcp"))


class CacheTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            original = ida.CACHE
            ida.CACHE = Path(directory) / "ida-session.json"
            try:
                self.assertEqual(ida.load_cache("http://a/mcp"), "")
                ida.store_cache("http://a/mcp", "abc123", Path("db.i64"))
                self.assertEqual(ida.load_cache("http://a/mcp"), "abc123")
                # A different endpoint must not reuse the cached session.
                self.assertEqual(ida.load_cache("http://b/mcp"), "")
                data = json.loads(ida.CACHE.read_text(encoding="utf-8"))
                self.assertEqual(data["database"], "abc123")
            finally:
                ida.CACHE = original


class _StubMcp:
    """Stands in for Mcp: one failed open, then a session appears."""

    url = "http://stub/mcp"

    def __init__(self, sessions_after: int = 2) -> None:
        self.calls: list[str] = []
        self._polls = 0
        self._sessions_after = sessions_after

    def call(self, name: str, timeout: float | None = None, **arguments: Any) -> Any:
        self.calls.append(name)
        if name == "idb_open":
            raise ida.IdaError("WinError 10054")
        return {}

    def list_sessions(self) -> list[dict[str, Any]]:
        self._polls += 1
        if self._polls < self._sessions_after:
            return []
        return [{"session_id": "s1", "worker_pid": 1}]


class OpenSessionTests(unittest.TestCase):
    def test_survives_a_lost_open_reply(self) -> None:
        stub = _StubMcp()
        with tempfile.TemporaryDirectory() as directory:
            original = ida.CACHE
            ida.CACHE = Path(directory) / "session.json"
            try:
                database = ida.open_session(stub, Path("db.i64"), wait=30.0)  # type: ignore[arg-type]
                self.assertEqual(database, "s1")
                self.assertIn("idb_open", stub.calls)
                self.assertEqual(ida.load_cache(stub.url), "s1")
            finally:
                ida.CACHE = original

    def test_reports_log_when_no_session_appears(self) -> None:
        stub = _StubMcp(sessions_after=10**9)
        with tempfile.TemporaryDirectory() as directory:
            original_cache, original_log = ida.CACHE, ida.LOG
            ida.CACHE = Path(directory) / "session.json"
            ida.LOG = Path(directory) / "sup.log"
            ida.LOG.write_text("Failed to open initial binary\n", encoding="utf-8")
            try:
                with self.assertRaises(ida.IdaError) as caught:
                    ida.open_session(stub, Path("db.i64"), wait=0.0)  # type: ignore[arg-type]
                self.assertIn("Failed to open initial binary", str(caught.exception))
            finally:
                ida.CACHE, ida.LOG = original_cache, original_log


if __name__ == "__main__":
    unittest.main()
