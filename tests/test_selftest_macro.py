"""Behavior tests for the explicit full-length macro acceptance check."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from driver.harness.errors import ModPipeError
from driver.selftest import check_macro_capture, check_macro_record_limits
from driver.selftest_macro import (
    MACRO_RECORD_LIMIT,
    check_macro_long_recording,
    inspect_saved_macro,
)
from driver.selftest_support import Checks


class MacroFileTests(unittest.TestCase):
    def test_inspects_real_file_content_and_length(self) -> None:
        row = ["false"] * 9 + ["0", "0"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro.json"
            path.write_text(json.dumps([row]), encoding="utf-8")
            inspected = inspect_saved_macro(path, 1)
            self.assertTrue(inspected["valid"])
            self.assertEqual(inspected["ticks"], 1)
            self.assertEqual(inspected["first"], row)
            self.assertEqual(inspected["last"], row)
            self.assertEqual(len(inspected["sha256"]), 64)
            self.assertFalse(inspect_saved_macro(path, 2)["valid"])


class MacroLongRecordingTests(unittest.TestCase):
    @staticmethod
    def status(recording: bool, ticks: int, *, playing: bool = False) -> dict[str, object]:
        return {
            "recording": recording,
            "recorded_ticks": ticks,
            "record_limit": MACRO_RECORD_LIMIT,
            "playing": playing,
        }

    def test_natural_limit_saves_reloads_and_restores(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = Mock()
            client.state.return_value = {
                "in_world": True,
                "input": {"w": False, "jump": False},
            }
            client.status.return_value = {"spool": str(root / "spool.jsonl")}
            client.macro_status.side_effect = [
                self.status(False, 0),
                self.status(True, 0),
                self.status(False, MACRO_RECORD_LIMIT),
                {**self.status(False, 0), "ticks": MACRO_RECORD_LIMIT},
            ]
            client.macro_record_start.return_value = {
                "record_id": 7,
                "max_ticks": MACRO_RECORD_LIMIT,
                "hard_limit": MACRO_RECORD_LIMIT,
            }
            client.wait_event.return_value = {
                "event": "macro_rec_limit",
                "record_id": 7,
                "ticks": MACRO_RECORD_LIMIT,
                "ok": True,
                "reason": "max_ticks",
            }
            client.macro_record_stop.return_value = {
                "ok": True,
                "record_id": 7,
                "ticks": MACRO_RECORD_LIMIT,
            }
            client.macro_load.return_value = {"event": "macro_loaded"}
            report_path = root / "report.json"
            with (
                patch("driver.selftest_macro.inspect_saved_macro") as inspect,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                inspect.return_value = {"valid": True, "ticks": MACRO_RECORD_LIMIT}
                checks = Checks()
                check_macro_long_recording(client, checks, report_path=report_path)
            self.assertTrue(checks.results)
            self.assertTrue(all(row.outcome == "pass" for row in checks.results))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertNotIn("error", report)
            self.assertEqual(report["record_id"], 7)
            self.assertEqual(report["terminal"]["ticks"], MACRO_RECORD_LIMIT)
            self.assertEqual(report["reload"]["status"]["ticks"], MACRO_RECORD_LIMIT)
            predicate = client.wait_event.call_args.kwargs["predicate"]
            self.assertTrue(predicate({"record_id": 7}))
            self.assertFalse(predicate({"record_id": 8}))
            self.assertEqual(client.macro_status.call_count, 4)
            client.request.assert_not_called()

    def test_resume_keeps_the_existing_recording(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = Mock()
            final = {"in_world": True, "input": {"w": False, "jump": False}}
            client.state.side_effect = [{"in_world": False}, final]
            client.wait_in_world.return_value = final
            client.status.return_value = {"spool": str(root / "spool.jsonl")}
            client.macro_status.side_effect = [
                {**self.status(True, 123), "record_id": 7},
                self.status(False, MACRO_RECORD_LIMIT),
                {**self.status(False, 0), "ticks": MACRO_RECORD_LIMIT},
            ]
            client.wait_event.return_value = {
                "event": "macro_rec_limit",
                "record_id": 7,
                "ticks": MACRO_RECORD_LIMIT,
                "ok": True,
                "reason": "max_ticks",
            }
            client.macro_record_stop.return_value = {
                "ok": True,
                "record_id": 7,
                "ticks": MACRO_RECORD_LIMIT,
            }
            client.macro_load.return_value = {"event": "macro_loaded"}
            report_path = root / "report.json"
            with (
                patch("driver.selftest_macro.inspect_saved_macro") as inspect,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                inspect.return_value = {"valid": True, "ticks": MACRO_RECORD_LIMIT}
                checks = Checks()
                check_macro_long_recording(client, checks, report_path=report_path, resume=True)
            self.assertTrue(all(row.outcome == "pass" for row in checks.results))
            client.macro_record_start.assert_not_called()
            self.assertEqual(client.macro_status.call_count, 3)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["resumed_from_ticks"], 123)
            self.assertEqual(report["record_id"], 7)

    def test_disconnect_preserves_first_error_and_saves_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = Mock()
            client.state.return_value = {"in_world": True}
            client.status.return_value = {"spool": str(root / "spool.jsonl")}
            client.macro_status.side_effect = [
                self.status(False, 0),
                self.status(True, 0),
                self.status(True, 19),
            ]
            client.macro_record_start.return_value = {
                "record_id": 9,
                "max_ticks": MACRO_RECORD_LIMIT,
                "hard_limit": MACRO_RECORD_LIMIT,
            }
            client.wait_event.side_effect = ModPipeError("pipe lost")
            client.request.return_value = {"ok": False, "reason": "world_left", "ticks": 19}
            report_path = root / "report.json"
            with (
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(ModPipeError, "pipe lost"),
            ):
                check_macro_long_recording(client, Checks(), report_path=report_path)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["error"], "ModPipeError: pipe lost")
            self.assertEqual(report["partial_save"]["ticks"], 19)
            self.assertEqual(client.request.call_args.args[0]["cmd"], "macro_rec_stop")
            client.macro_record_stop.assert_not_called()


class MacroDomainTests(unittest.TestCase):
    def test_legacy_entry_points_delegate_to_the_domain_module(self) -> None:
        client = Mock()
        checks = Checks()
        wait = Mock()
        settle = Mock()
        with (
            patch("driver.selftest.run_macro_record_limits") as limits,
            patch("driver.selftest.run_macro_capture") as capture,
            patch("driver.selftest.wait_ticks", wait),
            patch("driver.selftest.settle", settle),
        ):
            check_macro_record_limits(client, checks)
            check_macro_capture(client, checks, "world")
        limits.assert_called_once_with(client, checks, wait)
        capture.assert_called_once_with(client, checks, "world", wait, settle)


if __name__ == "__main__":
    unittest.main()
