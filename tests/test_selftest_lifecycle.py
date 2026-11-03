"""Behavior tests for the explicit hot-reload acceptance check."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from driver.harness.errors import ModPipeError
from driver.selftest_lifecycle import check_hot_reload
from driver.selftest_support import Checks


class HotReloadTests(unittest.TestCase):
    @staticmethod
    def fixture(directory: Path) -> tuple[Mock, dict[str, object], dict[str, object]]:
        mod_dir = directory / "probe"
        mod_dir.mkdir()
        (mod_dir / "harness.json").write_text("{}", encoding="utf-8")
        (mod_dir / "probe.dll").write_bytes(b"probe")
        spool = str(mod_dir / "spool-42.jsonl")
        initial: dict[str, object] = {"in_world": True, "tick": 100}
        final = {
            "in_world": True,
            "tick": 111,
            "input": {
                "locked": False,
                "w": False,
                "a": False,
                "s": False,
                "d": False,
                "jump": False,
            },
        }
        client = Mock()
        client.status.side_effect = [
            {"spool": spool},
            {"spool": spool, "version_ok": True, "tick_recovery": {"active": False}},
        ]
        client.state.side_effect = [
            initial,
            {"in_world": True, "tick": 110},
            final,
            final,
            final,
            final,
        ]
        idle = {"playing": False, "recording": False, "release_pending": False}
        client.macro_status.side_effect = [idle, idle]
        client.screen.return_value = {"name": "chat_screen"}
        client.text_input.return_value = {"ok": True}
        client.key_event.return_value = {"input_id": 60}
        client.wait_event.return_value = {"ok": True, "input_id": 60}
        client.chat_history.return_value = {"messages": []}
        return client, initial, final

    def test_reload_reconnects_and_checks_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client, _, _ = self.fixture(root)
            checks = Checks()
            report_path = root / "report.json"
            with contextlib.redirect_stdout(io.StringIO()):
                check_hot_reload(client, checks, report_path=report_path)
            self.assertTrue(checks.results)
            self.assertTrue(all(row.outcome == "pass" for row in checks.results))
            self.assertIn("hot_reload_tick_alive", {row.name for row in checks.results})
            client.text_input.assert_called_once_with("ll mod reload probe", wait=True, timeout=10)
            self.assertEqual(client.key_event.call_count, 4)
            client.connect.assert_called_once_with()
            self.assertEqual(client.close.call_count, 2)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertNotIn("error", report)
            self.assertEqual(report["before"], report["after"])

    def test_submission_failure_preserves_first_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client, _, _ = self.fixture(root)
            client.text_input.side_effect = ModPipeError("text failed")
            report_path = root / "report.json"
            with (
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(ModPipeError, "text failed"),
            ):
                check_hot_reload(client, Checks(), report_path=report_path)
            client.connect.assert_not_called()
            client.close.assert_called_once_with()
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["error"], "ModPipeError: text failed")


if __name__ == "__main__":
    unittest.main()
