"""SDK boundary acceptance must detect duplicate receipts and leaked owners."""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from driver.selftest_sdk import check_sdk_boundaries
from driver.selftest_support import Checks
from driver.transport_mod import ModTimeout


class SdkBoundaryChecksTests(unittest.TestCase):
    def run_checks(
        self, *, duplicate: bool = False, retired: bool = True, wrong_reason: bool = False
    ) -> Checks:
        client = Mock()
        client.state.side_effect = [
            {"mayfly": False, "flying": False},
            {"mayfly": True, "flying": False},
            {"mayfly": True, "flying": False},
            {"mayfly": False, "flying": False},
        ]
        client.command.return_value = {"success": True}
        client.ping.return_value = {"event": "pong"}
        client.wait_event.side_effect = None if duplicate else ModTimeout("no duplicate")

        def reply(request: dict, *_args: object) -> dict:
            ok = request["mode"] == "none"
            target, phase = request["target"], request["phase"]
            reason = f"{target}_task_{'failed' if phase == 'run' else 'queue_failed'}"
            return {
                "event": "sdk_test_result",
                "ok": ok,
                "reason": "wrong" if wrong_reason else reason,
            }

        client.request.side_effect = reply
        checks = Checks()
        with (
            contextlib.redirect_stdout(io.StringIO()),
            patch("driver.selftest_sdk.wait_ticks"),
            patch("driver.selftest_sdk._task_retired", return_value=retired),
        ):
            check_sdk_boundaries(client, checks)
        self.assertEqual(client.command.call_count, 2)
        return checks

    def test_recovers_after_each_fault(self) -> None:
        self.assertTrue(all(row.outcome == "pass" for row in self.run_checks().results))

    def test_duplicate_terminal_fails(self) -> None:
        checks = self.run_checks(duplicate=True)
        self.assertEqual(sum(row.outcome == "fail" for row in checks.results), 12)

    def test_leaked_task_ownership_fails(self) -> None:
        checks = self.run_checks(retired=False)
        self.assertEqual(sum(row.outcome == "fail" for row in checks.results), 24)

    def test_wrong_fault_reason_fails(self) -> None:
        checks = self.run_checks(wrong_reason=True)
        self.assertEqual(sum(row.outcome == "fail" for row in checks.results), 12)
