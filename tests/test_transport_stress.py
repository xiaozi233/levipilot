"""Exercise the production reader and request lock against real Windows pipes."""

from __future__ import annotations

import _winapi
import contextlib
import json
import msvcrt
import os
import queue
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from driver.harness.connection import PipeConnection, _PipeSession
from driver.harness.errors import ModDisconnected
from driver.harness.protocol import LineDecoder


class TransportStressTests(unittest.TestCase):
    def test_real_pipe_reconnects_and_serializes_competing_requests(self) -> None:
        name = rf"\\.\pipe\levipilot-test-{uuid.uuid4().hex}"
        ready = threading.Event()
        failures: queue.Queue[BaseException] = queue.Queue()
        generations = 32

        def serve() -> None:
            try:
                for generation in range(generations):
                    handle = _winapi.CreateNamedPipe(name, 3, 8, 1, 65536, 65536, 0, 0)
                    try:
                        ready.set()
                        try:
                            _winapi.ConnectNamedPipe(handle, overlapped=False)
                        except OSError as exc:
                            if exc.winerror != 535:  # Client connected before the accept call.
                                raise
                        fd = msvcrt.open_osfhandle(handle, os.O_BINARY)
                    except BaseException:
                        _winapi.CloseHandle(handle)
                        raise
                    with os.fdopen(fd, "r+b", buffering=0) as pipe:
                        pipe.write(b'{"event":"hello","protocol":4}\n')
                        decoder = LineDecoder()
                        for _ in range(16):
                            # Each request is one line, but reads may split it.
                            rows: list[str] = []
                            while not rows:
                                chunk = pipe.read(4096)
                                if not chunk:
                                    raise EOFError("client left before all requests completed")
                                rows = decoder.feed(chunk)
                            if len(rows) != 1:
                                raise AssertionError("requests escaped the exchange lock")
                            request = json.loads(rows[0])
                            for event in ("tick", "echo"):
                                reply = {
                                    "event": event,
                                    "generation": generation,
                                    "seq": request["seq"],
                                }
                                pipe.write((json.dumps(reply) + "\n").encode())
                        # Keep the connection alive until the client closes it.
                        with contextlib.suppress(OSError):
                            pipe.read(1)
            except BaseException as exc:
                failures.put(exc)
                ready.set()

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        client = PipeConnection(pipe_name=name, connect_timeout=5, timeout=5)
        sessions = []
        try:
            self.assertTrue(ready.wait(5))
            if not failures.empty():
                raise failures.get_nowait()
            with ThreadPoolExecutor(max_workers=2) as pool:
                for generation in range(generations):
                    client.reconnect()
                    sessions.append(client._session)
                    self.assertTrue(all(session.stopped.is_set() for session in sessions[:-1]))

                    def exchange(seq: int, expected: int = generation) -> None:
                        reply = client.request({"cmd": "echo", "seq": seq}, "echo")
                        self.assertEqual(
                            reply, {"event": "echo", "generation": expected, "seq": seq}
                        )

                    futures = [pool.submit(exchange, seq) for seq in range(16)]
                    for future in futures:
                        future.result(timeout=10)
                    ticks = [client.readline() for _ in range(16)]
                    self.assertEqual({row["seq"] for row in ticks}, set(range(16)))
                    self.assertTrue(
                        all(
                            row["event"] == "tick" and row["generation"] == generation
                            for row in ticks
                        )
                    )
                    self.assertFalse(client._session.pending)
        finally:
            client.close()
            server.join(10)
            if not failures.empty():
                raise failures.get_nowait()
        self.assertFalse(server.is_alive(), "named-pipe server did not finish")
        self.assertEqual(len({id(session) for session in sessions}), generations)
        self.assertTrue(
            all(session.reader is None and session.handle is None for session in sessions)
        )

    def test_reader_overflow_stops_only_the_old_session(self) -> None:
        payload = b'{"event":"tick"}\n' * 8193

        class Pipe:
            def fileno(self):
                return 1

            def read(self, _size):
                return payload

        def peek(_handle, _buffer, _size, _read, available, _left):
            available._obj.value = len(payload)
            return True

        old = _PipeSession(handle=Pipe())
        fresh = _PipeSession()
        client = PipeConnection()
        client._session = fresh
        with (
            patch("driver.harness.connection.msvcrt.get_osfhandle", return_value=1),
            patch("driver.harness.connection.kernel32.PeekNamedPipe", side_effect=peek),
        ):
            PipeConnection._read_loop(old)
        self.assertTrue(old.stopped.is_set())
        self.assertEqual(old.lines.qsize(), 8192)
        self.assertIn("溢出", old.failure)
        self.assertFalse(fresh.stopped.is_set())
        self.assertTrue(fresh.lines.empty())
        client._session = old
        while not old.lines.empty():
            client.readline(timeout=0)
        with self.assertRaisesRegex(ModDisconnected, "溢出"):
            client.readline(timeout=0)
