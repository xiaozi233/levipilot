"""Length heuristics must not turn unclassified traffic into a bad-packet verdict."""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from driver.selftest_diagnostics import check_send_probe
from driver.selftest_support import Checks


class SendProbeAccountingTests(unittest.TestCase):
    def run_check(self, before: int, after: int) -> Checks:
        client = Mock()
        client.state.return_value = {"in_world": True}
        client.ping.return_value = {"event": "pong"}
        client.send_probe.side_effect = [
            {
                "active": True,
                "serialize_hooked": True,
                "path": "suspect.jsonl",
                "dumps": before,
                "history_records": 0,
            },
            {"active": False, "hits": 10, "dumps": after, "history_records": 10},
            {"active": False},
        ]
        checks = Checks()
        with (
            patch("driver.selftest_diagnostics.violation_watch_address", return_value=1234),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            check_send_probe(client, checks, Mock())
        return checks

    def test_candidates_are_preserved_without_claiming_a_bad_packet(self) -> None:
        checks = self.run_check(0, 1)
        self.assertTrue(all(row.outcome == "pass" for row in checks.results))

    def test_invalid_counter_is_still_a_failure(self) -> None:
        checks = self.run_check(2, 1)
        failed = [row.name for row in checks.results if row.outcome == "fail"]
        self.assertEqual(failed, ["send_probe_dump_accounting"])
