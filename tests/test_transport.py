"""Transport-level tests that do not need a running game.

The interesting failure modes are all on the client side: a JSON line split
across pipe reads, a request that never gets an answer, and a pipe that closes
underneath a script.
"""

from __future__ import annotations

import os
import queue
import threading
import unittest
from unittest.mock import patch

import driver.harness as public_api
import driver.harness.client as client_api
import driver.transport_mod as legacy_api
from driver.harness.commands.session import SessionCommands
from driver.harness.connection import PipeConnection, _PipeSession, _PipeStream, pipe_error_hint
from driver.harness.protocol import MAX_LINE_BYTES, LineTooLongError
from driver.transport_mod import (
    Harness,
    LineDecoder,
    ModClient,
    ModDisconnected,
    ModProtocolError,
    ModTaskCancelled,
    ModTimeout,
)


class FakeClient(ModClient):
    def __init__(self) -> None:
        super().__init__(timeout=0.05)
        self.sent: list[dict] = []

    def send(self, message: dict) -> None:  # type: ignore[override]
        self.sent.append(message)


class LineDecoderTests(unittest.TestCase):
    def test_splits_partial_lines(self) -> None:
        decoder = LineDecoder()
        self.assertEqual(decoder.feed(b'{"a":1}\n{"b"'), ['{"a":1}'])
        self.assertEqual(decoder.feed(b":2}\r\n"), ['{"b":2}'])
        self.assertEqual(decoder.pending(), b"")

    def test_handles_utf8_split_across_reads(self) -> None:
        payload = "中文".encode() + b"\n"
        decoder = LineDecoder()
        self.assertEqual(decoder.feed(payload[:2]), [])
        self.assertEqual(decoder.feed(payload[2:]), ["中文"])

    def test_ignores_blank_lines(self) -> None:
        decoder = LineDecoder()
        self.assertEqual(decoder.feed(b"\n\r\n"), [])

    def test_many_lines_and_partial_tail_across_chunk_sizes(self) -> None:
        payload = b"\n\r\n" + ("中文\r\n".encode() + b"bad:\xff\n") * 100 + b"tail\r"
        expected = ["中文", "bad:\ufffd"] * 100
        for size in (1, 2, 7, 64, len(payload)):
            with self.subTest(chunk=size):
                decoder = LineDecoder()
                actual = []
                for offset in range(0, len(payload), size):
                    actual.extend(decoder.feed(payload[offset : offset + size]))
                self.assertEqual(actual, expected)
                self.assertEqual(decoder.pending(), b"tail\r")
                self.assertEqual(decoder.feed(b""), [])
                self.assertEqual(decoder.feed(b"\nnext\n"), ["tail", "next"])

    def test_long_fragmented_line_then_more_lines(self) -> None:
        decoder = LineDecoder()
        fragment = b"x" * 1024
        for _ in range(256):
            self.assertEqual(decoder.feed(fragment), [])
        self.assertEqual(decoder.feed(b"\nlast\n"), ["x" * (256 * 1024), "last"])
        self.assertEqual(decoder.pending(), b"")

    def test_line_limit_rejects_oversized_lines_and_resets_buffer(self) -> None:
        self.assertEqual(MAX_LINE_BYTES, 16 * 1024 * 1024)
        decoder = LineDecoder(max_line_bytes=8)
        self.assertEqual(decoder.feed(b"12345678\r"), [])
        self.assertEqual(decoder.feed(b"\nok\n"), ["12345678", "ok"])
        with self.assertRaises(LineTooLongError):
            decoder.feed(b"123456789\n")
        self.assertEqual(decoder.pending(), b"")
        with self.assertRaises(LineTooLongError):
            decoder.feed(b"12345")
            decoder.feed(b"6789")
        self.assertEqual(decoder.pending(), b"")
        self.assertEqual(decoder.feed(b"fresh\n"), ["fresh"])


class CompatibilityTests(unittest.TestCase):
    def test_legacy_exports_keep_the_same_objects(self) -> None:
        for name in legacy_api.__all__:
            if name != "main":
                with self.subTest(name=name):
                    self.assertIs(getattr(legacy_api, name), getattr(public_api, name))
        self.assertIs(client_api.ModClient, public_api.ModClient)
        self.assertIs(client_api.Harness, public_api.Harness)


class WriteTests(unittest.TestCase):
    class Pipe:
        def __init__(self) -> None:
            self.data = bytearray()
            self.flushed = False
            self.closed = False
            self.fail_write = False
            self.fail_flush = False

        def write(self, data: memoryview) -> int:
            if self.fail_write:
                return 0
            written = min(2, len(data))
            self.data.extend(data[:written])
            return written

        def flush(self) -> None:
            if self.fail_flush:
                raise OSError("flush failed")
            self.flushed = True

        def close(self) -> None:
            self.closed = True

    def test_short_writes_preserve_json_and_raw_bytes(self) -> None:
        for raw in (False, True):
            with self.subTest(raw=raw):
                client = ModClient()
                pipe = self.Pipe()
                client._session.stream.handle = pipe
                if raw:
                    client.send_raw(b"\xffpartial")
                    expected = b"\xffpartial"
                else:
                    client.send({"cmd": "ping", "text": "中"})
                    expected = b'{"cmd":"ping","text":"\\u4e2d"}\n'
                self.assertEqual(pipe.data, expected)
                self.assertTrue(pipe.flushed)
                client.close()

    def test_zero_write_and_flush_failure_close_both_write_paths(self) -> None:
        for method in ("send", "send_raw"):
            for failure in ("fail_write", "fail_flush"):
                with self.subTest(method=method, failure=failure):
                    client = ModClient()
                    pipe = self.Pipe()
                    setattr(pipe, failure, True)
                    client._session.stream.handle = pipe
                    payload = {"cmd": "ping"} if method == "send" else b"raw"
                    with self.assertRaisesRegex(ModDisconnected, "pipe 写入失败"):
                        getattr(client, method)(payload)
                    self.assertIsNone(client._session.stream.handle)
                    self.assertTrue(pipe.closed)

    def test_disconnection_precedes_serialization_but_encoding_errors_keep_connection(self) -> None:
        client = ModClient()
        message = {"unserializable": object()}
        with self.assertRaisesRegex(ModDisconnected, "尚未连接"):
            client.send(message)
        pipe = self.Pipe()
        client._session.stream.handle = pipe
        with self.assertRaises(TypeError):
            client.send(message)
        self.assertIs(client._session.stream.handle, pipe)
        self.assertEqual(pipe.data, b"")
        client.close()


class RequestTests(unittest.TestCase):
    def test_subscribed_events_do_not_fill_pending_or_hide_other_events(self) -> None:
        client = FakeClient()
        samples = []
        unsubscribe = client.subscribe("tick", samples.append)
        for tick in range(5000):
            client._session.stream.lines.put({"event": "tick", "tick": tick})
        client._session.stream.lines.put({"event": "notice"})
        client._session.stream.lines.put({"event": "pong"})
        self.assertEqual(client.ping()["event"], "pong")
        self.assertEqual(len(samples), 5000)
        self.assertEqual(client.readline(), {"event": "notice"})
        unsubscribe()
        unsubscribe()  # Cleanup is idempotent.
        client._session.stream.lines.put({"event": "tick", "tick": 5000})
        self.assertEqual(client.readline()["tick"], 5000)

    def test_subscriptions_belong_to_one_connection_generation(self) -> None:
        client = FakeClient()
        samples = []
        unsubscribe = client.subscribe("tick", samples.append)
        old_session = client._session
        client._session = _PipeSession()
        client._session.stream.lines.put({"event": "tick", "tick": 1})
        self.assertEqual(client.readline()["tick"], 1)
        self.assertFalse(samples)
        unsubscribe()
        self.assertFalse(old_session.consumers)

    def test_subscriber_exception_closes_the_incomplete_exchange(self) -> None:
        client = FakeClient()

        def fail(_event):
            raise RuntimeError("consumer failed")

        client.subscribe("tick", fail)
        client._session.stream.lines.put({"event": "tick"})
        with self.assertRaisesRegex(RuntimeError, "consumer failed"):
            client.ping()
        self.assertTrue(client._session.stream.stopped.is_set())

    def test_notifications_do_not_extend_request_deadline(self) -> None:
        client = FakeClient()
        now = 0.0

        def slow_consumer(_row):
            nonlocal now
            now = 2.0

        client.subscribe("tick", slow_consumer)
        client._session.stream.lines.put({"event": "tick"})
        client._session.stream.lines.put({"event": "pong"})
        with (
            patch("driver.harness.connection.time.monotonic", side_effect=lambda: now),
            self.assertRaises(ModTimeout),
        ):
            client.ping()
        self.assertTrue(client._session.stream.stopped.is_set())

    def test_preserves_unrelated_events(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "tick", "tick": 1})
        client._session.stream.lines.put({"event": "moving"})
        client._session.stream.lines.put({"event": "pong"})
        self.assertEqual(client.request({"cmd": "ping"}, "pong")["event"], "pong")
        self.assertEqual(client.readline(), {"event": "tick", "tick": 1})
        self.assertEqual(client.readline(), {"event": "moving"})

    def test_deferred_events_cannot_satisfy_a_new_request(self) -> None:
        client = FakeClient()
        client._session.pending.append({"event": "pong", "old": True})
        client._session.stream.lines.put({"event": "pong", "old": False})
        self.assertFalse(client.request({"cmd": "ping"}, "pong")["old"])
        self.assertTrue(client.readline()["old"])

    def test_wait_event_can_find_a_buffered_result_without_reordering_others(self) -> None:
        client = FakeClient()
        client._session.pending.extend(
            [
                {"event": "tick", "tick": 1},
                {"event": "joined", "ok": True},
                {"event": "tick", "tick": 2},
            ]
        )
        self.assertTrue(client.wait_event("joined")["ok"])
        self.assertEqual([client.readline()["tick"], client.readline()["tick"]], [1, 2])

    def test_pending_overflow_is_explicit_and_closes_connection(self) -> None:
        client = FakeClient()
        client._session.pending.extend({"event": "tick"} for _ in range(4096))
        with self.assertRaisesRegex(ModProtocolError, "4096"):
            client._defer({"event": "tick"})
        self.assertTrue(client._session.stream.stopped.is_set())

    def test_error_event_raises_protocol_error(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "error", "reason": "not_in_world"})
        with self.assertRaises(ModProtocolError) as caught:
            client.request({"cmd": "state"}, "state")
        self.assertIn("not_in_world", str(caught.exception))

    def test_req_id_ignores_unrelated_async_errors_and_stale_replies(self) -> None:
        client = FakeClient()
        client.hello = {"protocol": 4, "spool": "", "capabilities": ["req_id"]}
        outgoing = {"cmd": "ping"}
        client._session.stream.lines.put(
            {"event": "error", "phase": "tick_pre", "reason": "internal_error"}
        )
        client._session.stream.lines.put(
            {"event": "error", "phase": "teleport", "reason": "panic", "req_id": 99}
        )
        client._session.stream.lines.put({"event": "pong", "req_id": 99, "stale": True})
        client._session.stream.lines.put({"event": "pong", "req_id": 1, "stale": False})

        reply = client.request(outgoing, "pong")
        self.assertFalse(reply["stale"])
        self.assertEqual(outgoing, {"cmd": "ping"})
        self.assertEqual(client.sent, [{"cmd": "ping", "req_id": 1}])
        self.assertEqual(client.readline()["phase"], "tick_pre")
        self.assertEqual(client.readline()["req_id"], 99)
        self.assertTrue(client.readline()["stale"])

    def test_req_id_raises_on_matching_or_framing_errors(self) -> None:
        for error_row in (
            {"event": "error", "reason": "bad_param", "req_id": 1},
            {"event": "error", "reason": "bad_json"},
            {"event": "error", "reason": "line_too_long"},
        ):
            with self.subTest(error_row=error_row):
                client = FakeClient()
                client.hello = {"protocol": 4, "spool": "", "capabilities": ["req_id"]}
                client._session.stream.lines.put(error_row)
                with self.assertRaises(ModProtocolError):
                    client.request({"cmd": "state"}, "state")

    def test_wait_event_defers_background_errors_when_req_id_supported(self) -> None:
        client = FakeClient()
        client.hello = {"protocol": 4, "spool": "", "capabilities": ["req_id"]}
        client._session.stream.lines.put(
            {"event": "error", "phase": "tick_post", "reason": "internal_error"}
        )
        client._session.stream.lines.put(
            {"event": "error", "phase": "teleport", "reason": "cancelled", "req_id": 5}
        )
        client._session.stream.lines.put({"event": "navigated", "task_id": 7, "ok": True})
        done = client.wait_event("navigated", predicate=lambda r: r.get("task_id") == 7)
        self.assertTrue(done["ok"])
        self.assertEqual(client.readline()["phase"], "tick_post")
        self.assertEqual(client.readline()["req_id"], 5)

    def test_read_loop_records_line_too_long_failure(self) -> None:
        class Pipe:
            def close(self) -> None:
                pass

            def fileno(self) -> int:
                return 1

            def read(self, size: int) -> bytes:
                return b"x" * size

        stream = _PipeStream(handle=Pipe())
        with (
            patch("driver.harness.connection.msvcrt.get_osfhandle", return_value=1),
            patch(
                "driver.harness.connection.kernel32.PeekNamedPipe",
                side_effect=lambda _h, _b, _s, _r, avail, _l: (
                    setattr(avail._obj, "value", MAX_LINE_BYTES + 128) or True
                ),
            ),
        ):
            PipeConnection._read_loop(stream)
        self.assertIn("单行超过上限", stream.failure)
        self.assertTrue(stream.stopped.is_set())

    def test_timeout_raises_and_closes(self) -> None:
        client = FakeClient()
        with self.assertRaises(ModTimeout):
            client.request({"cmd": "state"}, "state", timeout=0.01)

    def test_closed_pipe_raises_disconnected(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "__closed__"})
        with self.assertRaises(ModDisconnected):
            client.request({"cmd": "ping"}, "pong")


class SessionTests(unittest.TestCase):
    def test_reader_eof_releases_handle_without_client_action(self) -> None:
        class Pipe(WriteTests.Pipe):
            def fileno(self):
                return 1

        pipe = Pipe()
        stream = _PipeStream(handle=pipe)
        with (
            patch("driver.harness.connection.msvcrt.get_osfhandle", return_value=1),
            patch("driver.harness.connection.kernel32.PeekNamedPipe", return_value=False),
        ):
            PipeConnection._read_loop(stream)
        self.assertTrue(pipe.closed)
        self.assertIsNone(stream.handle)
        self.assertTrue(stream.stopped.is_set())
        self.assertEqual(stream.lines.get_nowait(), {"event": "__closed__"})

    def test_concurrent_stream_close_transfers_handle_once(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        closes = []

        class Pipe:
            def close(self):
                closes.append(1)
                entered.set()
                release.wait(3)

        stream = _PipeStream(handle=Pipe())
        closer = threading.Thread(target=stream.close)
        closer.start()
        try:
            self.assertTrue(entered.wait(3))
            stream.close()
        finally:
            release.set()
            closer.join(3)
        self.assertFalse(closer.is_alive())
        self.assertEqual(closes, [1])
        self.assertIsNone(stream.handle)

    def test_failed_start_or_handshake_releases_the_open_handle(self) -> None:
        for failure in (
            RuntimeError("reader start failed"),
            KeyboardInterrupt(),
            ValueError("protocol"),
        ):
            with self.subTest(failure=type(failure).__name__):
                client = PipeConnection()
                pipe = WriteTests.Pipe()
                with (
                    patch("builtins.open", return_value=pipe),
                    patch("driver.harness.connection.threading.Thread.start") as start,
                    patch.object(client, "_next_line") as hello,
                ):
                    if isinstance(failure, RuntimeError):
                        start.side_effect = failure
                    elif isinstance(failure, ValueError):
                        hello.return_value = {"event": "hello", "protocol": "invalid"}
                    else:
                        hello.side_effect = failure
                    with self.assertRaises(type(failure)):
                        client.connect()
                self.assertTrue(pipe.closed)
                self.assertFalse(client.connected)
                assert client._session.finalizer is not None
                self.assertFalse(client._session.finalizer.alive)
                client.close()

    def test_late_old_reader_cannot_write_into_a_new_session(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class Pipe:
            def close(self):
                pass

            def fileno(self):
                return 1

            def read(self, size):
                entered.set()
                if not release.wait(3):
                    raise OSError("test gate timed out")
                return b'{"event":"old_data"}\n'

        client = FakeClient()
        old = _PipeSession(stream=_PipeStream(handle=Pipe()))
        fresh = _PipeSession()

        def peek(_handle, _buffer, _size, _read, available, _left):
            available._obj.value = 32
            return not release.is_set()

        with (
            patch("driver.harness.connection.msvcrt.get_osfhandle", return_value=1),
            patch("driver.harness.connection.kernel32.PeekNamedPipe", side_effect=peek),
        ):
            reader = threading.Thread(target=PipeConnection._read_loop, args=(old.stream,))
            reader.start()
            try:
                self.assertTrue(entered.wait(3))
                client._session = fresh
            finally:
                release.set()
                reader.join(3)
        self.assertFalse(reader.is_alive())
        self.assertEqual(old.stream.lines.get_nowait()["event"], "old_data")
        self.assertEqual(old.stream.lines.get_nowait()["event"], "__closed__")
        self.assertTrue(fresh.stream.lines.empty())
        self.assertFalse(fresh.stream.stopped.is_set())

    def test_close_wakes_a_blocked_consumer(self) -> None:
        client = FakeClient()
        errors = []

        def consume():
            try:
                client.readline(timeout=5)
            except ModDisconnected as exc:
                errors.append(exc)

        reader = threading.Thread(target=consume)
        reader.start()
        client.close()
        reader.join(2)
        self.assertFalse(reader.is_alive())
        self.assertEqual(len(errors), 1)


class TaskRequestTests(unittest.TestCase):
    def test_task_waiter_ignores_an_old_completion_with_the_same_event_name(self) -> None:
        client = FakeClient()
        for row in (
            {"event": "navigated", "task_id": 1},
            {"event": "navigate_started", "task_id": 2},
            {"event": "navigated", "task_id": 2, "ok": True},
        ):
            client._session.stream.lines.put(row)
        self.assertEqual(client.navigate(0, 0)["task_id"], 2)
        self.assertEqual(client.readline()["task_id"], 1)

    def test_cancellation_is_a_typed_error_without_closing_the_pipe(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "use_started", "task_id": 3})
        client._session.stream.lines.put(
            {"event": "task_cancelled", "task_id": 3, "reason": "replaced"}
        )
        with self.assertRaises(ModTaskCancelled) as caught:
            client.use_item()
        self.assertEqual(caught.exception.task_id, 3)
        self.assertEqual(caught.exception.reason, "replaced")
        self.assertFalse(client._session.stream.stopped.is_set())

    def test_task_rejects_an_old_mod_without_generation_ids(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "jump_when_started"})
        with self.assertRaisesRegex(ModProtocolError, "task_id"):
            client.jump_when()
        self.assertTrue(client._session.stream.stopped.is_set())


class WaitReadyTests(unittest.TestCase):
    def test_reconnects_until_world_is_loaded(self) -> None:
        class Handle:
            def close(self) -> None:
                pass

        class FlakyClient(ModClient):
            def __init__(self) -> None:
                super().__init__(timeout=0.05)
                self.connects = 0
                self.states = 0

            def connect(self):  # type: ignore[override]
                self.connects += 1
                self._session.stream.handle = Handle()
                return self

            def ping(self):  # type: ignore[override]
                return {"event": "pong"}

            def state(self):  # type: ignore[override]
                self.states += 1
                return {"event": "state", "in_world": self.states > 1}

        client = FlakyClient()
        state = client.wait_ready(timeout=5.0)
        self.assertTrue(state["in_world"])
        self.assertEqual(client.connects, 1)
        self.assertEqual(client.states, 2)


class EnterWorldRetryTests(unittest.TestCase):
    def test_waits_for_settled_and_retries_cancelled_join(self) -> None:
        class FlakyClient(ModClient):
            def __init__(self) -> None:
                super().__init__(timeout=0.05)
                self.calls = 0

            def enter_world(self, name=None, **kwargs):  # type: ignore[override]
                self.calls += 1
                return {"event": "enter_world_result", "ok": True}

            def wait_enter_settled(self, timeout=45.0):  # type: ignore[override]
                if self.calls == 1:
                    return {
                        "event": "enter_world_settled",
                        "ok": False,
                        "reason": "left_while_joining",
                    }
                return {"event": "enter_world_settled", "ok": True}

        client = FlakyClient()
        result = client.enter_world_ready("world", retry_delay=0)
        self.assertTrue(result["ok"])
        self.assertEqual(client.calls, 2)

    def test_signin_required_is_returned_without_waiting(self) -> None:
        class RefusingClient(ModClient):
            def __init__(self) -> None:
                super().__init__(timeout=0.05)
                self.calls = 0

            def enter_world(self, name=None, **kwargs):  # type: ignore[override]
                self.calls += 1
                return {
                    "event": "enter_world_result",
                    "ok": False,
                    "reason": "signin_required",
                }

            def wait_enter_settled(self, timeout=45.0):  # type: ignore[override]
                raise AssertionError("must not wait when enter_world already failed")

        client = RefusingClient()
        result = client.enter_world_ready("world", attempts=2, retry_delay=0)
        self.assertEqual(result["reason"], "signin_required")
        self.assertEqual(client.calls, 2)

    def test_wait_enter_settled_skips_ticks(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "tick", "tick": 1})
        client._session.stream.lines.put({"event": "enter_world_settled", "ok": True})
        self.assertTrue(client.wait_enter_settled(0.5)["ok"])

    def test_wait_enter_settled_filters_late_previous_entry(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put(
            {"event": "enter_world_settled", "entry_id": 1, "ok": True}
        )
        client._session.stream.lines.put(
            {"event": "enter_world_settled", "entry_id": 2, "ok": False}
        )
        self.assertFalse(client.wait_enter_settled(0.5, entry_id=2)["ok"])
        self.assertEqual(client.readline(0.5)["entry_id"], 1)

    def test_enter_ready_matches_acknowledged_entry(self) -> None:
        class TaggedClient(FakeClient):
            def enter_world(self, name=None, **kwargs):  # type: ignore[override]
                self._session.stream.lines.put(
                    {"event": "enter_world_settled", "entry_id": 10, "ok": False}
                )
                self._session.stream.lines.put(
                    {"event": "enter_world_settled", "entry_id": 11, "ok": True}
                )
                return {"event": "enter_world_result", "entry_id": 11, "ok": True}

        result = TaggedClient().enter_world_ready("world", attempts=1)
        self.assertEqual(result["entry_id"], 11)


class HarnessForwardingTests(unittest.TestCase):
    class Recorder:
        def __init__(self) -> None:
            self.calls = []

        def __getattr__(self, name):
            def record(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                return {"event": "ok"}

            return record

    def make_harness(self):
        harness = Harness.__new__(Harness)
        recorder = self.Recorder()
        harness.client = recorder  # type: ignore[assignment]
        return harness, recorder

    def test_movement_task_and_macro_arguments(self) -> None:
        harness, recorder = self.make_harness()
        harness.walk_to(1.5, -2.5, sprint=True, tolerance=0.2)
        harness.jump_when("distance", x=1.0, z=2.0, distance=0.5, forward=1.0)
        harness.route([{"x": 1, "z": 2, "jump": "edge"}], sprint=True)
        harness.mine(1, 2, 3, max_ticks=10)
        harness.place(4, 5, 6, expected="stone")
        harness.play_macro("demo", event_level=True)
        self.assertEqual(
            recorder.calls,
            [
                ("navigate", (1.5, -2.5), {"sprint": True, "tolerance": 0.2}),
                (
                    "jump_when",
                    ("distance",),
                    {"x": 1.0, "z": 2.0, "distance": 0.5, "forward": 1.0},
                ),
                ("route", ([{"x": 1, "z": 2, "jump": "edge"}],), {"sprint": True}),
                ("mine_block", (1, 2, 3), {"max_ticks": 10}),
                ("place_block", (4, 5, 6), {"expected": "stone"}),
                ("macro_load", ("demo",), {}),
                ("macro_run", (), {"loop": False, "event_level": True}),
            ],
        )

    def test_input_and_packet_arguments(self) -> None:
        harness, recorder = self.make_harness()
        harness.key_event(69, False, via="hardware")
        harness.key_state(69)
        harness.mouse_event("left", down=True, dx=0, dy=0)
        harness.screen()
        harness.packet_capture(True, ids=[144], serialize_ids=[144], sample=2)
        harness.packet_drain(5, 100)
        harness.packet_bytes(7, 8)
        harness.client.text_input("say hi")
        harness.client.chat_send("/say hi")
        harness.packet_stats()
        self.assertEqual(
            recorder.calls,
            [
                ("key_event", (69, False), {"via": "hardware"}),
                ("key_state", (69,), {}),
                ("mouse_event", ("left",), {"down": True, "dx": 0, "dy": 0}),
                ("screen", (), {}),
                (
                    "packet_capture",
                    (True,),
                    {"ids": [144], "serialize_ids": [144], "sample": 2},
                ),
                ("packet_drain", (5, 100), {}),
                ("packet_bytes", (7, 8), {}),
                ("text_input", ("say hi",), {}),
                ("chat_send", ("/say hi",), {}),
                ("packet_stats", (), {}),
            ],
        )

    def test_jump_when_distance_requires_target(self) -> None:
        with self.assertRaises(ValueError):
            ModClient(timeout=0.05).jump_when("distance")


class PipeHintTests(unittest.TestCase):
    def test_busy_and_wedged_map_to_actions(self) -> None:
        for winerror, needle in (
            (231, "管道忙"),
            (87, "重启游戏"),
            (2, "mod 没加载"),
            (5, "拒绝访问"),
        ):
            error = OSError(22, "Invalid argument")
            error.winerror = winerror  # type: ignore[attr-defined]
            self.assertIn(needle, pipe_error_hint(error))

    def test_unknown_and_timeout_fall_back(self) -> None:
        self.assertIn("连接超时", pipe_error_hint(None))
        error = OSError(22, "Invalid argument")
        error.winerror = 1234  # type: ignore[attr-defined]
        self.assertIn("已进入存档", pipe_error_hint(error))


class AuthAndBackpressureTests(unittest.TestCase):
    def test_auth_handshake_with_explicit_token_and_env(self) -> None:
        hello = {
            "event": "hello",
            "protocol": 4,
            "spool": "",
            "capabilities": ["req_id"],
            "auth_required": True,
        }
        for use_env in (False, True):
            with self.subTest(use_env=use_env):
                pipe = WriteTests.Pipe()
                env = {"LEVIPILOT_PIPE_TOKEN": "env-secret"} if use_env else {}
                replies = iter([hello, {"event": "auth_result", "ok": True, "req_id": 1}])
                next_line = lambda *_a, it=replies: next(it)  # noqa: E731
                with (
                    patch.dict(os.environ, env, clear=False),
                    patch("builtins.open", return_value=pipe),
                    patch("driver.harness.connection.threading.Thread.start"),
                ):
                    client = PipeConnection(token=None if use_env else "explicit-secret")
                    with patch.object(client, "_next_line", side_effect=next_line):
                        client.connect()
                self.assertIn(
                    b'"cmd":"auth"',
                    bytes(pipe.data),
                )
                expected_tok = b"env-secret" if use_env else b"explicit-secret"
                self.assertIn(expected_tok, bytes(pipe.data))
                client.close()

    def test_auth_handshake_rejects_missing_or_invalid_token(self) -> None:
        hello = {
            "event": "hello",
            "protocol": 4,
            "spool": "",
            "capabilities": ["req_id"],
            "auth_required": True,
        }
        pipe = WriteTests.Pipe()
        with patch.dict(os.environ, {"LEVIPILOT_PIPE_TOKEN": ""}, clear=False):
            client = PipeConnection(token="")
            with (
                patch("builtins.open", return_value=pipe),
                patch("driver.harness.connection.threading.Thread.start"),
                patch.object(client, "_next_line", return_value=hello),
                self.assertRaisesRegex(ModProtocolError, "auth_required"),
            ):
                client.connect()
        self.assertTrue(pipe.closed)

        pipe2 = WriteTests.Pipe()
        client2 = PipeConnection(token="wrong")
        replies = iter([hello, {"event": "error", "reason": "bad_token", "req_id": 1}])
        with (
            patch("builtins.open", return_value=pipe2),
            patch("driver.harness.connection.threading.Thread.start"),
            patch.object(client2, "_next_line", side_effect=lambda *_a: next(replies)),
            self.assertRaisesRegex(ModProtocolError, "bad_token"),
        ):
            client2.connect()
        self.assertTrue(pipe2.closed)

        pipe3 = WriteTests.Pipe()
        with patch.dict(os.environ, {"LEVIPILOT_PIPE_TOKEN": ""}, clear=False):
            raw_client = PipeConnection(token="", auto_auth=False)
            with (
                patch("builtins.open", return_value=pipe3),
                patch("driver.harness.connection.threading.Thread.start"),
                patch.object(raw_client, "_next_line", return_value=hello),
            ):
                raw_client.connect()
        self.assertEqual(bytes(pipe3.data), b"")
        self.assertIs(raw_client._session.stream.handle, pipe3)
        raw_client.close()

    def test_drop_events_opt_in_in_read_loop_and_defer(self) -> None:
        class FeedPipe:
            def __init__(self, payload: bytes) -> None:
                self.payload = payload
                self.pos = 0
                self.closed = False

            def fileno(self) -> int:
                return 1

            def read(self, n: int) -> bytes:
                chunk = self.payload[self.pos : self.pos + n]
                self.pos += len(chunk)
                return chunk

            def close(self) -> None:
                self.closed = True

        lines_payload = (b'{"event":"tick"}\n' * 6) + b'{"event":"pong"}\n'
        pipe = FeedPipe(lines_payload)
        stream = _PipeStream(
            handle=pipe,
            lines=queue.Queue(maxsize=4),
            drop_events=frozenset({"tick"}),
        )

        def fake_peek(_h, _b, _s, _r, avail, _l) -> bool:
            remaining = len(pipe.payload) - pipe.pos
            if remaining <= 0:
                return False
            avail._obj.value = remaining
            return True

        with (
            patch("driver.harness.connection.msvcrt.get_osfhandle", return_value=1),
            patch("driver.harness.connection.kernel32.PeekNamedPipe", side_effect=fake_peek),
        ):
            PipeConnection._read_loop(stream)
        queued = []
        while not stream.lines.empty():
            queued.append(stream.lines.get_nowait()["event"])
        self.assertIn("pong", queued)
        self.assertEqual(queued[-1], "pong")
        self.assertGreater(stream.dropped, 0)
        self.assertTrue(stream.stopped.is_set())

        client = FakeClient()
        self.assertEqual(client._session.stream.lines.maxsize, 20000)
        client.set_drop_events(("tick",))
        client._session.pending.extend({"event": "tick"} for _ in range(4096))
        client._defer({"event": "tick"})
        self.assertEqual(len(client._session.pending), 4096)
        self.assertEqual(client.dropped_events(), 1)
        client._defer({"event": "notice"})
        self.assertEqual(client._session.pending[-1]["event"], "notice")
        self.assertEqual(client.dropped_events(), 2)

        client.set_drop_events(())
        client._session.pending[-1] = {"event": "tick"}
        with self.assertRaisesRegex(ModProtocolError, "4096"):
            client._defer({"event": "tick"})

    def test_command_mixins_do_not_inherit_pipe_connection_and_harness_delegates(self) -> None:
        self.assertFalse(issubclass(SessionCommands, PipeConnection))
        for base in ModClient.__bases__:
            if base is not PipeConnection:
                self.assertFalse(issubclass(base, PipeConnection))

        uninitialized = Harness.__new__(Harness)
        with self.assertRaises(AttributeError):
            _ = uninitialized.ping

        real_harness = Harness.__new__(Harness)
        real_harness.client = ModClient()
        self.assertIn("ping", dir(real_harness))

        harness = Harness.__new__(Harness)
        recorder = HarnessForwardingTests.Recorder()
        harness.client = recorder  # type: ignore[assignment]
        harness.ping()
        self.assertEqual(recorder.calls, [("ping", (), {})])


if __name__ == "__main__":
    unittest.main()
