from __future__ import annotations

import io
import json
import unittest
from unittest.mock import Mock

from driver.delta_watch import capture


class DeltaWatchTests(unittest.TestCase):
    def harness(self) -> Mock:
        h = Mock()
        h.client.status.return_value = {"version_ok": True, "compat": False}
        h.profile_dump.return_value = {
            "profile": {
                "components": {"StateVector": {"id": 99}},
                "structs": {"StateVector": {"VelX": "0x18", "VelY": "0x1c", "VelZ": "0x20"}},
            }
        }
        h.state.return_value = {"context": 123, "feet_y": -60, "tick": 800}
        h.client.ecs_context.return_value = {"address": 0x2000}
        h.client.watch_list.return_value = {"head": 70, "watches": [{"id": 100}]}
        h.watch_set.side_effect = [{"id": 7}, {"id": 8}, {"id": 9}]
        h.watch_drain.return_value = {"next": 70, "head": 70, "lost": 0, "hits": []}
        return h

    def test_separate_axes_and_global_paging_keep_unrelated_rows(self) -> None:
        h = self.harness()
        h.watch_drain.side_effect = [
            {"next": 71, "head": 74, "lost": 0, "hits": [{"seq": 71, "watch": 100}]},
            {
                "next": 74,
                "head": 74,
                "lost": 0,
                "hits": [{"seq": n, "watch": n - 65} for n in (72, 73, 74)],
            },
        ]
        output = io.StringIO()
        summary = capture(h, output, ticks=5)
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["counts"], {"x": 1, "y": 1, "z": 1})
        self.assertEqual(
            [call.args[0] for call in h.watch_set.call_args_list], [0x2018, 0x201C, 0x2020]
        )
        self.assertTrue(all(call.kwargs["size"] == 4 for call in h.watch_set.call_args_list))
        self.assertEqual([call.kwargs["since"] for call in h.watch_drain.call_args_list], [70, 71])
        self.assertEqual([call.args for call in h.watch_clear.call_args_list], [(7,), (8,), (9,)])
        pages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(
            any(row.get("reply", {}).get("hits") == [{"seq": 71, "watch": 100}] for row in pages)
        )
        h.client.set_input.assert_not_called()
        h.client.teleport.assert_not_called()

    def test_partial_install_does_not_clear_someone_elses_watch(self) -> None:
        h = self.harness()
        h.watch_set.side_effect = [{"id": 7}, RuntimeError("no slot")]
        output = io.StringIO()
        with self.assertRaisesRegex(RuntimeError, "no slot"):
            capture(h, output, ticks=5)
        h.watch_clear.assert_called_once_with(7)
        self.assertFalse(json.loads(output.getvalue().splitlines()[-1])["complete"])

    def test_context_change_aborts_and_releases_all_owned_watches(self) -> None:
        h = self.harness()
        h.state.side_effect = [{"context": 123}, {"context": 456}]
        with self.assertRaisesRegex(RuntimeError, "context changed"):
            capture(h, io.StringIO(), ticks=5)
        self.assertEqual(h.watch_clear.call_count, 3)
        h.watch_drain.assert_not_called()

    def test_drain_failure_keeps_first_error_and_attempts_every_cleanup(self) -> None:
        h = self.harness()
        h.watch_drain.side_effect = RuntimeError("first error")
        h.watch_clear.side_effect = [RuntimeError("clear failed"), {}, {}]
        output = io.StringIO()
        with self.assertRaisesRegex(RuntimeError, "first error") as caught:
            capture(h, output, ticks=5)
        self.assertEqual(h.watch_clear.call_count, 3)
        self.assertIn("clear failed", " ".join(caught.exception.__notes__))
        report = json.loads(output.getvalue().splitlines()[-1])
        self.assertIn("first error", report["error"])
        self.assertEqual(len(report["cleanup_errors"]), 1)

    def test_lost_rows_and_stalled_cursor_never_report_complete(self) -> None:
        h = self.harness()
        h.watch_drain.return_value = {"next": 80, "head": 80, "lost": 4, "hits": []}
        summary = capture(h, io.StringIO(), ticks=5)
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["lost"], 4)
        h = self.harness()
        h.watch_drain.return_value = {"next": 70, "head": 80, "lost": 0, "hits": []}
        with self.assertRaisesRegex(RuntimeError, "did not advance"):
            capture(h, io.StringIO(), ticks=5)
        self.assertEqual(h.watch_clear.call_count, 3)

    def test_cleanup_failure_alone_fails_and_persists_report(self) -> None:
        h = self.harness()
        h.watch_clear.side_effect = [None, RuntimeError("clear failed"), None]
        output = io.StringIO()
        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            capture(h, output, ticks=5)
        report = json.loads(output.getvalue().splitlines()[-1])
        self.assertFalse(report["complete"])
        self.assertEqual(h.watch_clear.call_count, 3)

    def test_preflight_rejection_never_installs_or_clears(self) -> None:
        h = self.harness()
        h.client.watch_list.return_value["watches"].append({"id": 101})
        with self.assertRaisesRegex(RuntimeError, "three free DR slots"):
            capture(h, io.StringIO(), ticks=5)
        h.watch_set.assert_not_called()
        h.watch_clear.assert_not_called()


if __name__ == "__main__":
    unittest.main()
