"""Transport-level tests that do not need a running game.

The interesting failure modes are all on the client side: a JSON line split
across pipe reads, a request that never gets an answer, and a pipe that closes
underneath a script.
"""

from __future__ import annotations

import queue
import unittest

from driver.transport_mod import (
    Harness,
    ModClient,
    ModDisconnected,
    ModProtocolError,
    ModTimeout,
    _LineDecoder,
)


class FakeClient(ModClient):
    def __init__(self) -> None:
        super().__init__(timeout=0.05)
        self.sent: list[dict] = []

    def send(self, message: dict) -> None:  # type: ignore[override]
        self.sent.append(message)


class LineDecoderTests(unittest.TestCase):
    def test_splits_partial_lines(self) -> None:
        decoder = _LineDecoder()
        self.assertEqual(decoder.feed(b'{"a":1}\n{"b"'), ['{"a":1}'])
        self.assertEqual(decoder.feed(b':2}\r\n'), ['{"b":2}'])
        self.assertEqual(decoder.pending(), b"")

    def test_handles_utf8_split_across_reads(self) -> None:
        payload = "中文".encode("utf-8") + b"\n"
        decoder = _LineDecoder()
        self.assertEqual(decoder.feed(payload[:2]), [])
        self.assertEqual(decoder.feed(payload[2:]), ["中文"])

    def test_ignores_blank_lines(self) -> None:
        decoder = _LineDecoder()
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