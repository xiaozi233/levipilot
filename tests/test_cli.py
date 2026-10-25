"""Behavior snapshots captured before extracting the monolithic CLI.

The fixture records the original entry point's calls, output, files and errors;
it must not be regenerated to make a changed CLI pass.
"""

from __future__ import annotations

import argparse
import json
import re
import unittest
from pathlib import Path

from driver.harness import cli
from driver.harness.cli_parser import build_parser
from driver.harness.client import ModClient
from driver.harness.errors import ModPipeError
from tests.cli_support import invoke_cli

BASELINE = Path(__file__).parent / "fixtures" / "cli-baseline.jsonl"


def baseline_commands() -> set[str]:
    """Command names the fixture was captured with (parsed from its --help)."""
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        if case["argv"] == ["--help"]:
            match = re.search(r"\{([^}]*)\}", case["expected"]["stdout"])
            assert match is not None
            return {token.strip() for token in match.group(1).split(",") if token.strip()}
    raise AssertionError("baseline --help case missing")


BASELINE_COMMANDS = baseline_commands()


class CliCompatibilityTests(unittest.TestCase):
    def test_original_command_behaviors(self) -> None:
        for line in BASELINE.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            with self.subTest(argv=case["argv"], failure=case.get("failure")):
                self.assertEqual(
                    self.without_new_commands(invoke_cli(cli, case["argv"], case.get("failure"))),
                    self.without_new_commands(case["expected"]),
                )

    @staticmethod
    def without_new_commands(result: dict) -> dict:
        # Keep the fixed baseline. The only intended help/error changes are
        # commands added after the fixture was captured; their execution is
        # tested independently below. argparse re-wraps the usage list and the
        # invalid-choice suggestions as commands are added, so both the live
        # output and the fixture expectation are reduced to the fixture's own
        # command set before comparing.
        def keep(token: str) -> bool:
            return token in BASELINE_COMMANDS

        def drop_help_lines(text: str) -> str:
            return re.sub(
                r"^    ([a-z0-9-]+) +[^\n]*\n",
                lambda match: match.group(0) if keep(match.group(1)) else "",
                text,
                flags=re.MULTILINE,
            )

        def collapse_usage(match: re.Match[str]) -> str:
            tokens = [token.strip() for token in match.group(1).strip("{}").split(",")]
            return (
                "usage: harness [-h] [--pipe PIPE] {"
                + ",".join(token for token in tokens if keep(token))
                + "}"
            )

        def clean_choices(match: re.Match[str]) -> str:
            tokens = re.findall(r"'([^']*)'", match.group(2))
            return match.group(1) + ", ".join(f"'{token}'" for token in tokens if keep(token))

        def collapse_positional(match: re.Match[str]) -> str:
            tokens = [token.strip() for token in match.group(1).strip("{}").split(",")]
            return "  {" + ",".join(token for token in tokens if keep(token)) + "}"

        for stream in ("stdout", "stderr"):
            text = drop_help_lines(result[stream])
            text = re.sub(
                r"usage: harness \[-h\] \[--pipe PIPE\]\s+(\{[^}]*\})",
                collapse_usage,
                text,
            )
            # argparse repeats the command list under "positional arguments:".
            text = re.sub(r"^  (\{[^}]*\})$", collapse_positional, text, flags=re.MULTILINE)
            text = re.sub(r"(choose from )((?:'[^']*'(?:, )?)+)", clean_choices, text)
            result[stream] = text
        return result

    def test_chat_history_extension(self) -> None:
        result = invoke_cli(cli, ["chat-history", "--limit", "5"])
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["calls"][1], ["chat_history", [5], {}])
        help_result = invoke_cli(cli, ["--help"])
        self.assertIn("read retained client chat messages", help_result["stdout"])

    def test_input_cfg_jump_impulse_guard(self) -> None:
        ok = invoke_cli(cli, ["input-cfg", "--jump-impulse", "1"])
        self.assertEqual(ok["code"], 0)
        self.assertEqual(ok["calls"][1][0], "input_cfg")
        self.assertEqual(ok["calls"][1][2].get("jump_impulse"), 1)

        forced = invoke_cli(cli, ["input-cfg", "--jump-impulse", "0", "--force"])
        self.assertEqual(forced["code"], 0)
        self.assertEqual(forced["calls"][1][2].get("jump_impulse"), 0)
        self.assertIs(forced["calls"][1][2].get("force"), True)

        passthrough = invoke_cli(cli, ["input-cfg", "--mode", "7"])
        self.assertEqual(passthrough["code"], 0)
        self.assertEqual(passthrough["calls"][1][2].get("mode"), 7)

        # The guard lives in the real client method; the CLI fixture replaces
        # ModClient with a recorder, so validation is checked here directly.
        # Valid modes pass validation and only fail on the unconnected pipe.
        client = ModClient()
        for bad in (0, 2):
            with self.assertRaises(ValueError):
                client.input_cfg(jump_impulse=bad)
        with self.assertRaises(ModPipeError):
            client.input_cfg(jump_impulse=1)
        with self.assertRaises(ModPipeError):
            client.input_cfg(jump_impulse=2, force=True)

    def test_hotkey_command_wiring(self) -> None:
        jump_key = invoke_cli(cli, ["jump-key"])
        self.assertEqual(jump_key["code"], 0)
        self.assertIn("jump_ok", jump_key["stdout"])
        pressed = [
            call for call in jump_key["calls"] if call[0] == "key_event" and call[1][0] == 32
        ]
        self.assertEqual([call[1][1] for call in pressed], [True, False])

        status = invoke_cli(cli, ["swim", "--status"])
        self.assertEqual(status["code"], 0)
        self.assertIn("swim_status", status["stdout"])

        drive = invoke_cli(cli, ["swim", "--drive", "--no-sprint", "--timeout", "0.1"])
        self.assertEqual(drive["code"], 0)
        self.assertIn("swim_drive", drive["stdout"])

    def test_input_lock_wiring(self) -> None:
        locked = invoke_cli(cli, ["input-lock"])
        self.assertEqual(locked["code"], 0)
        self.assertEqual(locked["calls"][1], ["input_lock", [True], {}])

        unlocked = invoke_cli(cli, ["input-lock", "--off"])
        self.assertEqual(unlocked["code"], 0)
        self.assertEqual(unlocked["calls"][1], ["input_lock", [False], {}])

    def test_togglefly_wiring(self) -> None:
        flipped = invoke_cli(cli, ["togglefly"])
        self.assertEqual(flipped["code"], 0)
        self.assertEqual(flipped["calls"][1], ["toggle_fly", [None], {}])

        on = invoke_cli(cli, ["togglefly", "--on"])
        self.assertEqual(on["calls"][1], ["toggle_fly", [True], {}])
        off = invoke_cli(cli, ["togglefly", "--off"])
        self.assertEqual(off["calls"][1], ["toggle_fly", [False], {}])

    def test_equip_wiring(self) -> None:
        result = invoke_cli(cli, ["equip", "feet"])
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["calls"][1], ["equip", ["feet"], {"slot": -1}])

        with_slot = invoke_cli(cli, ["equip", "head", "--slot", "5"])
        self.assertEqual(with_slot["code"], 0)
        self.assertEqual(with_slot["calls"][1], ["equip", ["head"], {"slot": 5}])

    def test_every_parser_command_has_a_handler(self) -> None:
        # The parser definition and the dispatch registry must stay in sync;
        # before the registry existed a missing handler exited 0 silently.
        parser = build_parser()
        assert parser._subparsers is not None
        subparsers = next(
            action
            for action in parser._subparsers._group_actions
            if isinstance(action, argparse._SubParsersAction)
        )
        defined = set(subparsers.choices)
        registered = set(cli.REGISTRY) | set(cli.DIRECT)
        self.assertEqual(defined, registered, "parser/registry drift")


if __name__ == "__main__":
    unittest.main()
