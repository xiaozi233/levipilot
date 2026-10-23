"""Behavior snapshots captured before extracting the monolithic CLI.

The fixture records the original entry point's calls, output, files and errors;
it must not be regenerated to make a changed CLI pass.
"""

from __future__ import annotations

import json
import re
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
                    self.without_chat_extension(invoke_cli(cli, case["argv"], case.get("failure"))),
                    case["expected"],
                )

    @staticmethod
    def without_chat_extension(result: dict) -> dict:
        # Keep the fixed baseline. The only intended help/error change is the
        # newly added command; its execution is tested independently below.
        for stream in ("stdout", "stderr"):
            result[stream] = re.sub(
                r"^    chat-history +read retained client chat messages\n",
                "",
                result[stream].replace(",chat-history", "").replace(", 'chat-history'", ""),
                flags=re.MULTILINE,
            )
        return result

    def test_chat_history_extension(self) -> None:
        result = invoke_cli(cli, ["chat-history", "--limit", "5"])
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["calls"][1], ["chat_history", [5], {}])
        help_result = invoke_cli(cli, ["--help"])
        self.assertIn("read retained client chat messages", help_result["stdout"])


if __name__ == "__main__":
    unittest.main()
