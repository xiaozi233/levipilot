"""Callback race fixtures must release controls even when a stage fails."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from driver.harness.errors import ModProtocolError, ModTimeout
from driver.selftest_callbacks import _check_external_refusal, check_callback_drain
from driver.selftest_support import Checks


class CallbackCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = {
            "in_world": True,
            "bindings": {"forward": 87},
            "input": {"locked": False, "w": False},
            "feet_x": 0.5,
            "feet_y": -49.0,
            "feet_z": 4.0,
            "yaw": 10.0,
            "pitch": 0.0,
            "mayfly": False,
            "flying": False,
        }
        self.client = Mock()
        self.client.state.return_value = self.state
        self.checks = Checks()

    def test_interrupted_race_clears_delays_and_restores_pose_and_lock(self) -> None:
        original = ModTimeout("race interrupted")
        with (
            patch("driver.selftest_callbacks._check_callback_drain", side_effect=original),
            patch("driver.selftest_callbacks.wait_ticks"),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(ModTimeout) as raised,
        ):
            check_callback_drain(self.client, self.checks)
        self.assertIs(raised.exception, original)
        self.client.request.assert_called_once_with(
            {"cmd": "selftest_callback", "action": "clear"}, "callback_test_cleared"
        )
        self.client.key_event.assert_called_once_with(87, False, via="hardware")
        self.client.teleport.assert_called_once_with(0.5, -49.0, 4.0, yaw=10.0, pitch=0.0)
        self.client.input_lock.assert_called_once_with(False)

    def test_cleanup_failure_keeps_first_error_and_continues_release(self) -> None:
        original = ModTimeout("first failure")
        self.client.request.side_effect = ModProtocolError("clear rejected")
        self.client.teleport.side_effect = ModTimeout("pose failed")
        with (
            patch("driver.selftest_callbacks._check_callback_drain", side_effect=original),
            patch("driver.selftest_callbacks.wait_ticks"),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(ModTimeout) as raised,
        ):
            check_callback_drain(self.client, self.checks)
        self.assertIs(raised.exception, original)
        self.assertEqual(len(original.__notes__), 2)
        self.client.key_event.assert_called_once_with(87, False, via="hardware")
        self.client.input_lock.assert_called_once_with(False)
        self.assertEqual(sum(row.outcome == "fail" for row in self.checks.results), 2)

    def test_cleanup_own_error_is_reported_after_restoring_original_lock(self) -> None:
        self.state["input"]["locked"] = True
        self.client.request.side_effect = ModProtocolError("clear failed")
        with (
            patch("driver.selftest_callbacks._check_callback_drain"),
            patch("driver.selftest_callbacks.wait_ticks"),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(ModProtocolError, "clear failed"),
        ):
            check_callback_drain(self.client, self.checks)
        self.client.input_lock.assert_called_once_with(True)

    def test_external_busy_check_rejects_self_or_stale_refusal_evidence(self) -> None:
        for reason, boot_delta, valid in (
            ("busy", "disable: refused reason=busy\n", True),
            ("callback_self", "disable: refused reason=callback_self\n", False),
            ("busy", "", False),
        ):
            with (
                self.subTest(reason=reason, boot_delta=boot_delta),
                tempfile.TemporaryDirectory() as d,
            ):
                directory = Path(d)
                (directory / "harness-boot.log").write_text("old log", encoding="utf-8")
                lifecycle = {
                    "generation": 2,
                    "disable_id": 1,
                    "running": True,
                    "hooks": dict.fromkeys(("key", "mouse", "player", "turn"), True),
                    "callbacks": {"closed": False, "refusal": reason},
                }
                status = {"spool": str(directory / "spool-1.jsonl"), "lifecycle": lifecycle}
                client = Mock()
                client.status.return_value = status
                client.chat_send.return_value = {"ok": True}
                checks = Checks()
                with (
                    patch(
                        "driver.selftest_callbacks._wait_lifecycle",
                        return_value=(status, {"boot_delta": boot_delta}),
                    ),
                    contextlib.chdir(directory),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    _check_external_refusal(client, checks, "held", "busy")
                self.assertEqual(checks.results[-1].outcome, "pass" if valid else "fail")
                client.chat_send.assert_called_once_with("/ll reload probe", wait=True, timeout=10)
                client.command.assert_not_called()
