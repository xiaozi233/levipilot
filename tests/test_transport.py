"""Transport-level tests that do not need a running game.

The interesting failure modes are all on the client side: a JSON line split
across pipe reads, a request that never gets an answer, and a pipe that closes
underneath a script.
"""

from __future__ import annotations

import unittest

from driver.transport_mod import (
    Harness,
    LineDecoder,
    ModClient,
    ModDisconnected,
    ModProtocolError,
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


class RequestTests(unittest.TestCase):
    def test_skips_unrelated_events(self) -> None:
        client = FakeClient()
        client._lines.put({"event": "tick", "tick": 1})
        client._lines.put({"event": "moving"})
        client._lines.put({"event": "pong"})
        self.assertEqual(client.request({"cmd": "ping"}, "pong")["event"], "pong")

    def test_error_event_raises_protocol_error(self) -> None:
        client = FakeClient()
        client._lines.put({"event": "error", "reason": "not_in_world"})
        with self.assertRaises(ModProtocolError) as caught:
            client.request({"cmd": "state"}, "state")
        self.assertIn("not_in_world", str(caught.exception))

    def test_timeout_raises_and_closes(self) -> None:
        client = FakeClient()
        with self.assertRaises(ModTimeout):
            client.request({"cmd": "state"}, "state", timeout=0.01)

    def test_closed_pipe_raises_disconnected(self) -> None:
        client = FakeClient()
        client._lines.put({"event": "__closed__"})
        with self.assertRaises(ModDisconnected):
            client.request({"cmd": "ping"}, "pong")


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
                self._file = Handle()
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
        client._lines.put({"event": "tick", "tick": 1})
        client._lines.put({"event": "enter_world_settled", "ok": True})
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
