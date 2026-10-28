"""Transport-level tests that do not need a running game.

The interesting failure modes are all on the client side: a JSON line split
across pipe reads, a request that never gets an answer, and a pipe that closes
underneath a script.
"""

from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

import driver.harness as public_api
import driver.harness.client as client_api
import driver.transport_mod as legacy_api
from driver.harness.connection import PipeConnection, _PipeSession
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
                client._session.handle = pipe
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
                    client._session.handle = pipe
                    payload = {"cmd": "ping"} if method == "send" else b"raw"
                    with self.assertRaisesRegex(ModDisconnected, "pipe 写入失败"):
                        getattr(client, method)(payload)
                    self.assertIsNone(client._session.handle)
                    self.assertTrue(pipe.closed)

    def test_disconnection_precedes_serialization_but_encoding_errors_keep_connection(self) -> None:
        client = ModClient()
        message = {"unserializable": object()}
        with self.assertRaisesRegex(ModDisconnected, "尚未连接"):
            client.send(message)
        pipe = self.Pipe()
        client._session.handle = pipe
        with self.assertRaises(TypeError):
            client.send(message)
        self.assertIs(client._session.handle, pipe)
        self.assertEqual(pipe.data, b"")
        client.close()


class RequestTests(unittest.TestCase):
    def test_preserves_unrelated_events(self) -> None:
        client = FakeClient()
        client._session.lines.put({"event": "tick", "tick": 1})
        client._session.lines.put({"event": "moving"})
        client._session.lines.put({"event": "pong"})
        self.assertEqual(client.request({"cmd": "ping"}, "pong")["event"], "pong")
        self.assertEqual(client.readline(), {"event": "tick", "tick": 1})
        self.assertEqual(client.readline(), {"event": "moving"})

    def test_deferred_events_cannot_satisfy_a_new_request(self) -> None:
        client = FakeClient()
        client._session.pending.append({"event": "pong", "old": True})
        client._session.lines.put({"event": "pong", "old": False})
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
        self.assertTrue(client._session.stopped.is_set())

    def test_error_event_raises_protocol_error(self) -> None:
        client = FakeClient()
        client._session.lines.put({"event": "error", "reason": "not_in_world"})
        with self.assertRaises(ModProtocolError) as caught:
            client.request({"cmd": "state"}, "state")
        self.assertIn("not_in_world", str(caught.exception))

    def test_timeout_raises_and_closes(self) -> None:
        client = FakeClient()
        with self.assertRaises(ModTimeout):
            client.request({"cmd": "state"}, "state", timeout=0.01)

    def test_closed_pipe_raises_disconnected(self) -> None:
        client = FakeClient()
        client._session.lines.put({"event": "__closed__"})
        with self.assertRaises(ModDisconnected):
            client.request({"cmd": "ping"}, "pong")


class SessionTests(unittest.TestCase):
    def test_late_old_reader_cannot_write_into_a_new_session(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class Pipe:
            def fileno(self):
                return 1

            def read(self, size):
                entered.set()
                if not release.wait(3):
                    raise OSError("test gate timed out")
                return b'{"event":"old_data"}\n'

        client = FakeClient()
        old = _PipeSession(handle=Pipe())
        fresh = _PipeSession()

        def peek(_handle, _buffer, _size, _read, available, _left):
            available._obj.value = 32
            return not release.is_set()

        with (
            patch("driver.harness.connection.msvcrt.get_osfhandle", return_value=1),
            patch("driver.harness.connection.kernel32.PeekNamedPipe", side_effect=peek),
        ):
            reader = threading.Thread(target=PipeConnection._read_loop, args=(old,))
            reader.start()
            try:
                self.assertTrue(entered.wait(3))
                client._session = fresh
            finally:
                release.set()
                reader.join(3)
        self.assertFalse(reader.is_alive())
        self.assertEqual(old.lines.get_nowait()["event"], "old_data")
        self.assertEqual(old.lines.get_nowait()["event"], "__closed__")
        self.assertTrue(fresh.lines.empty())
        self.assertFalse(fresh.stopped.is_set())

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
            client._session.lines.put(row)
        self.assertEqual(client.navigate(0, 0)["task_id"], 2)
        self.assertEqual(client.readline()["task_id"], 1)

    def test_cancellation_is_a_typed_error_without_closing_the_pipe(self) -> None:
        client = FakeClient()
        client._session.lines.put({"event": "use_started", "task_id": 3})
        client._session.lines.put({"event": "task_cancelled", "task_id": 3, "reason": "replaced"})
        with self.assertRaises(ModTaskCancelled) as caught:
            client.use_item()
        self.assertEqual(caught.exception.task_id, 3)
        self.assertEqual(caught.exception.reason, "replaced")
        self.assertFalse(client._session.stopped.is_set())

    def test_task_rejects_an_old_mod_without_generation_ids(self) -> None:
        client = FakeClient()
        client._session.lines.put({"event": "jump_when_started"})
        with self.assertRaisesRegex(ModProtocolError, "task_id"):
            client.jump_when()
        self.assertTrue(client._session.stopped.is_set())


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
                self._session.handle = Handle()
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
        client._session.lines.put({"event": "tick", "tick": 1})
        client._session.lines.put({"event": "enter_world_settled", "ok": True})
        self.assertTrue(client.wait_enter_settled(0.5)["ok"])


class HarnessForwardingTests(unittest.TestCase):
    def test_walk_to_forwards_keyword_arguments(self) -> None:
        class Recorder:
            def __init__(self) -> None:
                self.call = None

            def navigate(self, x, z, **kwargs):
                self.call = (x, z, kwargs)
                return {"event": "navigated"}

        harness = Harness.__new__(Harness)
        harness.client = Recorder()  # type: ignore[assignment]
        harness.walk_to(1.5, -2.5, sprint=True, tolerance=0.2)
        self.assertEqual(harness.client.call, (1.5, -2.5, {"sprint": True, "tolerance": 0.2}))

    def test_jump_and_route_forward_arguments(self) -> None:
        class Recorder:
            def __init__(self) -> None:
                self.calls = []

            def jump_when(self, mode, **kwargs):
                self.calls.append(("jump", mode, kwargs))
                return {"event": "jumped"}

            def route(self, waypoints, **kwargs):
                self.calls.append(("route", list(waypoints), kwargs))
                return {"event": "route_done"}

        harness = Harness.__new__(Harness)
        harness.client = Recorder()  # type: ignore[assignment]
        harness.jump_when("distance", x=1.0, z=2.0, distance=0.5, forward=1.0)
        harness.route([{"x": 1, "z": 2, "jump": "edge"}], sprint=True)
        self.assertEqual(
            harness.client.calls,
            [
                ("jump", "distance", {"x": 1.0, "z": 2.0, "distance": 0.5, "forward": 1.0}),
                ("route", [{"x": 1, "z": 2, "jump": "edge"}], {"sprint": True}),
            ],
        )

    def test_input_event_forwarding(self) -> None:
        class Recorder:
            def __init__(self) -> None:
                self.calls = []

            def key_event(self, code, down=True, **kwargs):
                self.calls.append(("key", code, down, kwargs))
                return {"event": "key_event_queued"}

            def key_state(self, code):
                self.calls.append(("state", code))
                return {"event": "key_state"}

            def mouse_event(self, action, **kwargs):
                self.calls.append(("mouse", action, kwargs))
                return {"event": "mouse_event_queued"}

            def screen(self):
                self.calls.append(("screen",))
                return {"event": "screen"}

        harness = Harness.__new__(Harness)
        harness.client = Recorder()  # type: ignore[assignment]
        harness.key_event(69, False, via="hardware")
        harness.key_state(69)
        harness.mouse_event("left", down=True, dx=0, dy=0)
        harness.screen()
        self.assertEqual(
            harness.client.calls,
            [
                ("key", 69, False, {"via": "hardware"}),
                ("state", 69),
                ("mouse", "left", {"down": True, "dx": 0, "dy": 0}),
                ("screen",),
            ],
        )

    def test_packet_commands_forward(self) -> None:
        class Recorder:
            def __init__(self) -> None:
                self.calls = []

            def packet_capture(self, on=True, **kwargs):
                self.calls.append(("capture", on, kwargs))
                return {"event": "packet_capture"}

            def packet_drain(self, since=0, limit=1024):
                self.calls.append(("drain", since, limit))
                return {"event": "packet_drain"}

            def text_input(self, text):
                self.calls.append(("text", text))
                return {"event": "text_input_queued"}

            def chat_send(self, text):
                self.calls.append(("chat", text))
                return {"event": "chat_send_queued"}

            def packet_bytes(self, since=0, limit=256):
                self.calls.append(("bytes", since, limit))
                return {"event": "packet_bytes"}

            def packet_stats(self):
                self.calls.append(("stats",))
                return {"event": "packet_stats"}

        harness = Harness.__new__(Harness)
        harness.client = Recorder()  # type: ignore[assignment]
        harness.packet_capture(True, ids=[144], serialize_ids=[144], sample=2)
        harness.packet_drain(5, 100)
        harness.packet_bytes(7, 8)
        harness.client.text_input("say hi")
        harness.client.chat_send("/say hi")
        harness.packet_stats()
        self.assertEqual(
            harness.client.calls,
            [
                ("capture", True, {"ids": [144], "serialize_ids": [144], "sample": 2}),
                ("drain", 5, 100),
                ("bytes", 7, 8),
                ("text", "say hi"),
                ("chat", "/say hi"),
                ("stats",),
            ],
        )

    def test_play_macro_forwards_event_level(self) -> None:
        class Recorder:
            def __init__(self) -> None:
                self.calls = []

            def macro_load(self, name):
                self.calls.append(("load", name))
                return {"event": "macro_loaded"}

            def macro_run(self, **kwargs):
                self.calls.append(("run", kwargs))
                return {"event": "macro_started"}

        harness = Harness.__new__(Harness)
        harness.client = Recorder()  # type: ignore[assignment]
        harness.play_macro("demo", event_level=True)
        self.assertEqual(
            harness.client.calls,
            [("load", "demo"), ("run", {"loop": False, "event_level": True})],
        )

    def test_jump_when_distance_requires_target(self) -> None:
        with self.assertRaises(ValueError):
            ModClient(timeout=0.05).jump_when("distance")

    def test_mine_and_place_forward_task_arguments(self) -> None:
        class Recorder:
            def __init__(self) -> None:
                self.calls = []

            def mine_block(self, x, y, z, **kwargs):
                self.calls.append(("mine", x, y, z, kwargs))
                return {"event": "block_mined"}

            def place_block(self, x, y, z, **kwargs):
                self.calls.append(("place", x, y, z, kwargs))
                return {"event": "block_placed"}

        harness = Harness.__new__(Harness)
        harness.client = Recorder()  # type: ignore[assignment]
        harness.mine(1, 2, 3, max_ticks=10)
        harness.place(4, 5, 6, expected="stone")
        self.assertEqual(
            harness.client.calls,
            [
                ("mine", 1, 2, 3, {"max_ticks": 10}),
                ("place", 4, 5, 6, {"expected": "stone"}),
            ],
        )


if __name__ == "__main__":
    unittest.main()
