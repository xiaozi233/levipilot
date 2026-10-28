"""Recording workflows must advance without consuming unrelated notifications."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from driver.harness import ModProtocolError
from tests.test_transport import FakeClient


class RecordingCommandsTests(unittest.TestCase):
    def test_record_preserves_buffered_and_live_notifications_in_order(self) -> None:
        client = FakeClient()
        buffered = {"event": "notice", "seq": 1}
        live = {"event": "notice", "seq": 2}
        client._defer(buffered)
        for event in (
            {"event": "started"},
            live,
            {"event": "tick", "tick": 1},
            {"event": "stopped"},
        ):
            client._session.lines.put(event)
        deferred = client._defer
        seen = set()

        def defer_once(event):
            # Turn an accidental notification reread loop into a bounded failure.
            if id(event) in seen:
                raise AssertionError("recording reread a deferred notification")
            seen.add(id(event))
            deferred(event)

        samples = []
        with patch.object(client, "_defer", side_effect=defer_once):
            self.assertEqual(
                client.record(1, on_sample=samples.append), [{"event": "tick", "tick": 1}]
            )
        self.assertEqual(samples, [{"event": "tick", "tick": 1}])
        self.assertEqual(client.readline(), buffered)
        self.assertEqual(client.readline(), live)

    def test_record_dump_rejects_stalled_cursor(self) -> None:
        client = FakeClient()
        message = {"event": "record_dump", "next": 0, "head": 1, "count": 1, "data": "00" * 64}
        with (
            patch.object(
                client, "record_dump", side_effect=[message, AssertionError("repeated dump")]
            ),
            self.assertRaisesRegex(ModProtocolError, "cursor"),
        ):
            client.record_dump_all()

    def test_record_dump_caps_following_pages_at_initial_head(self) -> None:
        client = FakeClient()
        messages = [
            {"event": "record_dump", "next": 2, "head": 3, "data": "00" * 128},
            {"event": "record_dump", "next": 3, "head": 9, "data": "00" * 64},
        ]
        with patch.object(client, "record_dump", side_effect=messages) as dump:
            self.assertEqual(len(client.record_dump_all(chunk=2)), 3)
        self.assertEqual([call.kwargs["limit"] for call in dump.call_args_list], [2, 1])

    def test_record_stops_if_callback_or_move_fails(self) -> None:
        for failure in ("callback", "move"):
            with self.subTest(failure=failure):
                client = FakeClient()
                client._session.lines.put({"event": "started"})
                client._session.lines.put({"event": "tick", "tick": 1})
                send = client.send

                def send_or_fail(message, *, mode=failure, sender=send):
                    if mode == "move" and message["cmd"] == "move":
                        raise RuntimeError("move failed")
                    sender(message)

                def callback(_row):
                    raise RuntimeError("callback failed")

                with (
                    patch.object(client, "send", side_effect=send_or_fail),
                    self.assertRaisesRegex(RuntimeError, failure),
                ):
                    client.record(1, on_sample=callback, move_ticks=1)
                self.assertEqual(client.sent[-1], {"cmd": "stop"})


if __name__ == "__main__":
    unittest.main()
