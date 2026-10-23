"""Behavior snapshots captured before extracting the monolithic CLI.

The fixture records the original entry point's calls, output, files and errors;
it must not be regenerated to make a changed CLI pass.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from driver.harness import cli
from tests.cli_support import invoke_cli

BASELINE = Path(__file__).parent / "fixtures" / "cli-baseline.jsonl"


class CliCompatibilityTests(unittest.TestCase):
    def test_original_command_behaviors(self) -> None:
        for line in BASELINE.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            with self.subTest(argv=case["argv"], failure=case.get("failure")):
                self.assertEqual(
                    invoke_cli(cli, case["argv"], case.get("failure")),
                    case["expected"],
                )


if __name__ == "__main__":
    unittest.main()
