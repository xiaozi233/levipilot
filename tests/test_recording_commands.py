"""Recording workflows must advance without consuming unrelated notifications."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from driver.harness import ModProtocolError
from tests.test_transport import FakeClient


class RecordingCommandsTests(unittest.TestCase):
    def test_binary_stop_propagates_failed_recording(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put(
            {"event": "record_stopped", "ok": False, "reason": "capture_fault"}
        )
        with self.assertRaisesRegex(ModProtocolError, "capture_fault"):
            client.record_bin_stop()

    def test_binary_dump_failure_requires_explicit_partial(self) -> None:
        client = FakeClient()
        message = {
            "event": "record_dump",
            "ok": False,
            "reason": "capture_failed",
            "head": 1,
            "next": 1,
            "data": "00" * 64,
        }
        with patch.object(client, "record_dump", return_value=message):
            with self.assertRaisesRegex(ModProtocolError, "capture_failed"):
                client.record_dump_all()
            self.assertEqual(len(client.record_dump_all(allow_partial=True)), 1)

    def test_binary_dump_rejects_lost_rows(self) -> None:
        client = FakeClient()
        message = {"event": "record_dump", "lost": 1, "head": 2, "next": 2, "data": "00" * 64}
        with patch.object(client, "record_dump", return_value=message):
            with self.assertRaisesRegex(ModProtocolError, "lost rows"):
                client.record_dump_all()
            self.assertEqual(len(client.record_dump_all(allow_partial=True)), 1)

    def test_binary_dump_never_combines_recordings(self) -> None:
        client = FakeClient()
        messages = [
            {"event": "record_dump", "record_id": 1, "head": 2, "next": 1, "data": "00" * 64},
            {"event": "record_dump", "record_id": 2, "head": 2, "next": 2, "data": "00" * 64},
        ]
        for partial in (False, True):
            with (
                patch.object(client, "record_dump", side_effect=messages),
                self.assertRaisesRegex(ModProtocolError, "recording changed"),
            ):
                client.record_dump_all(allow_partial=partial)

    def test_macro_partial_save_is_reported_as_failed_recording(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put(
            {
                "event": "macro_rec_saved",
                "ok": False,
                "reason": "capture_fault",
                "path": "partial.json",
            }
        )
        with self.assertRaisesRegex(ModProtocolError, "capture_fault.*partial.json"):
            client.macro_record_stop()

    def test_macro_save_accepts_legacy_receipt_without_ok(self) -> None:
        client = FakeClient()
        receipt = {"event": "macro_rec_saved", "ticks": 3}
        client._session.stream.lines.put(receipt)
        self.assertEqual(client.macro_record_stop(), receipt)

    def test_recording_terminal_failure_is_not_success(self) -> None:
        for reason in ("capture_fault", "capture_failed", "buffer_full", "world_left"):
            with self.subTest(reason=reason):
                client = FakeClient()
                client._session.stream.lines.put({"event": "started"})
                client._session.stream.lines.put({"event": "tick", "tick": 1})
                client._session.stream.lines.put(
                    {"event": "stopped", "ok": False, "reason": reason}
                )
                with self.assertRaisesRegex(ModProtocolError, reason):
                    client.record(3)

    def test_stop_propagates_failed_auto_stop(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "stopped", "ok": False, "reason": "buffer_full"})
        with self.assertRaisesRegex(ModProtocolError, "buffer_full"):
            client.stop()

    def test_record_ignores_old_session_rows_and_terminals(self) -> None:
        client = FakeClient()
        stale = {"event": "stopped", "record_id": 1, "ok": False, "reason": "capture_fault"}
        client._defer(stale)
        for event in (
            {"event": "started", "record_id": 2},
            {"event": "tick", "record_id": 1, "tick": 99},
            {"event": "tick", "record_id": 2, "tick": 1},
            {"event": "stopped", "record_id": 2, "ok": True},
        ):
            client._session.stream.lines.put(event)
        self.assertEqual([row["tick"] for row in client.record(1)], [1])
        self.assertEqual(client.readline(), stale)

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
            client._session.stream.lines.put(event)
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
                client._session.stream.lines.put({"event": "started"})
                client._session.stream.lines.put({"event": "tick", "tick": 1})
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

    def test_record_bin_start_and_dump_all_with_watch(self) -> None:
        import base64
        import struct

        client = FakeClient()
        client._session.stream.lines.put(
            {"event": "record_started", "watch_count": 1, "watch_entry_size": 40}
        )
        started = client.record_bin_start(period=1, watch=[{"component_id": 0x60FC5A9A, "size": 4}])
        self.assertEqual(started["watch_count"], 1)
        self.assertEqual(
            client.sent[-1],
            {
                "cmd": "record_start",
                "period": 1,
                "watch": [{"component_id": 0x60FC5A9A, "offset": 0, "size": 4}],
            },
        )

        watch_blob = struct.pack("<4QII", 0x40000000, 0, 0, 0, 1, 0)
        message = {
            "event": "record_dump",
            "head": 1,
            "next": 1,
            "count": 1,
            "watch_count": 1,
            "watch_entry_size": 40,
            "data": "00" * 64,
            "watch_b64": base64.b64encode(watch_blob).decode("ascii"),
        }
        with patch.object(client, "record_dump", return_value=message):
            entries, watch_entries = client.record_dump_all(format="hex", include_watch=True)
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(watch_entries), 1)
        self.assertTrue(watch_entries[0].valid(0))
        self.assertAlmostEqual(watch_entries[0].as_f32(0), 2.0)


if __name__ == "__main__":
    unittest.main()
