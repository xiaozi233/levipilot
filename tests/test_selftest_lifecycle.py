"""Behavior tests for the explicit hot-reload acceptance check."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from driver.harness.errors import ModPipeError, ModProtocolError, ModTimeout
from driver.selftest_lifecycle import check_hot_reload
from driver.selftest_support import Checks


class HotReloadTests(unittest.TestCase):
    @staticmethod
    def fixture(directory: Path) -> tuple[Mock, dict[str, object], dict[str, object]]:
        mod_dir = directory / "probe"
        mod_dir.mkdir()
        (mod_dir / "harness.json").write_text("{}", encoding="utf-8")
        (mod_dir / "probe.dll").write_bytes(b"probe")
        boot = mod_dir / "harness-boot.log"
        boot.write_text(
            "enable: begin lifecycle=4\n"
            "enable: done lifecycle=4\n"
            "disable: begin lifecycle=4 disable_id=1\n"
            "disable: done lifecycle=4 disable_id=1 ok=1\n",
            encoding="utf-8",
        )
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
        state: dict[str, object] = {"reloaded": False, "attempts": 0, "faults": 0}
        client.state_box = state

        def status() -> dict[str, object]:
            lifecycle = (
                {"generation": 5, "disable_id": 2, "running": True}
                if state["reloaded"]
                else {"generation": 4, "disable_id": 1, "running": True}
            )
            return {
                "spool": spool,
                "version_ok": True,
                "tick_recovery": {"active": False},
                "command_template_attempts": state["attempts"],
                "command_template_faults": state["faults"],
                "command_template": {
                    "ready": int(state["attempts"]) > 0
                    and int(state["faults"]) == 0
                    and not state.get("template_error"),
                    "reason": state.get("template_error")
                    or ("command_template_fault" if state["faults"] else ""),
                },
                "lifecycle": lifecycle,
            }

        client.status.side_effect = status

        def state_call() -> dict[str, object]:
            if state.get("quiesced"):
                raise ModProtocolError("state: quiescing")
            rows = state["rows"]
            return rows.pop(0) if rows else final  # type: ignore[union-attr]

        client.state.side_effect = state_call
        state["rows"] = [
            initial,
            {"in_world": True, "tick": 110},
            final,
            final,
            final,
            final,
            final,
            final,
        ]
        idle = {"playing": False, "recording": False, "release_pending": False}
        client.macro_status.side_effect = [idle, idle]
        screens = ["chat_screen"] + ["hud_screen"] * 32
        client.screen.side_effect = lambda: {"name": screens.pop(0) if screens else "hud_screen"}

        def text_input(text: str, **_: object) -> dict[str, object]:
            state["attempts"] = int(state["attempts"]) + 1
            return {"ok": True}

        client.text_input.side_effect = text_input
        client.packet_capture.return_value = {"event": "packet_capture", "active": True}
        client.chat_send.side_effect = lambda text, **_: {
            "event": "chat_sent",
            "ok": True,
            "packet_id": 7,
            "text": text,
        }
        client.command.return_value = {"success": False}

        def request(
            message: dict[str, object], expected_event: str, **_: object
        ) -> dict[str, object]:
            on = bool(message.get("on"))
            state["quiesced"] = not on
            return {
                "event": expected_event,
                "on": on,
                "ok": True,
                "quiescing": not on,
                "hooks": dict.fromkeys(("key", "mouse", "player", "turn"), on),
            }

        client.request.side_effect = request
        client.chat_history.return_value = {
            "messages": [{"text": "找不到模组 __levipilot_missing__"}]
        }

        def connect() -> Mock:
            state["reloaded"] = True
            state["quiesced"] = False  # a fresh instance starts un-quiesced
            with boot.open("a", encoding="utf-8") as stream:
                stream.write(
                    "disable: begin lifecycle=4 disable_id=2\n"
                    "disable: done lifecycle=4 disable_id=2 ok=1\n"
                    "enable: begin lifecycle=5\n"
                    "enable: done lifecycle=5\n"
                )
            return client

        client.connect.side_effect = connect
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
            self.assertIn("hot_reload_lifecycle_changed", {row.name for row in checks.results})
            self.assertIn("hot_reload_boot_records", {row.name for row in checks.results})
            self.assertIn("hot_reload_rejected_no_reload", {row.name for row in checks.results})
            client.chat_send.assert_called_once_with("/ll reload probe", wait=True, timeout=10)
            client.command.assert_called_once_with("ll reload __levipilot_missing__")
            client.connect.assert_called_once_with()
            self.assertEqual(client.close.call_count, 2)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertNotIn("error", report)
            self.assertEqual(report["before"], report["after"])
            self.assertEqual(report["lifecycle"]["before"]["generation"], 4)
            self.assertEqual(report["lifecycle"]["after"]["generation"], 5)
            self.assertEqual(report["lifecycle"]["after"]["disable_id"], 2)

    def test_no_lifecycle_change_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client, _, _ = self.fixture(root)
            client.connect.side_effect = lambda: client
            checks = Checks()
            with contextlib.redirect_stdout(io.StringIO()):
                check_hot_reload(client, checks, lifecycle_timeout=0)
            failed = {row.name: row for row in checks.results if row.outcome == "fail"}
            self.assertEqual(
                set(failed), {"hot_reload_lifecycle_changed", "hot_reload_boot_records"}
            )
            self.assertEqual(checks.finish(), 1)

    def test_unaccepted_rejection_stops_before_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client, _, _ = self.fixture(root)
            client.command.return_value = {"success": True}
            checks = Checks()
            with contextlib.redirect_stdout(io.StringIO()):
                check_hot_reload(client, checks)
            client.chat_send.assert_not_called()
            client.connect.assert_not_called()
            client.request.assert_not_called()
            self.assertEqual(checks.finish(), 1)
            failed = {row.name for row in checks.results if row.outcome == "fail"}
            self.assertEqual(failed, {"hot_reload_rejected_command"})

    def test_submit_pipe_drop_is_recorded_and_does_not_mask_missing_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client, _, _ = self.fixture(root)
            client.connect.side_effect = lambda: client
            client.chat_send.side_effect = ModPipeError("send failed")
            report_path = root / "report.json"
            checks = Checks()
            with contextlib.redirect_stdout(io.StringIO()):
                check_hot_reload(client, checks, report_path=report_path, lifecycle_timeout=0)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertIn(
                "send failed", report["submission"]["chat_send"]["disconnected_after_send"]
            )
            failed = {row.name for row in checks.results if row.outcome == "fail"}
            self.assertEqual(failed, {"hot_reload_lifecycle_changed", "hot_reload_boot_records"})
            self.assertEqual(checks.finish(), 1)

    def test_explicit_protocol_rejection_fails_the_send_step(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client, _, _ = self.fixture(root)
            client.connect.side_effect = lambda: client
            client.chat_send.side_effect = ModProtocolError("chat_send: packet_inject_disabled")
            report_path = root / "report.json"
            checks = Checks()
            with contextlib.redirect_stdout(io.StringIO()):
                check_hot_reload(client, checks, report_path=report_path, lifecycle_timeout=0)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            submission = report["submission"]["chat_send"]
            self.assertIn("packet_inject_disabled", submission["rejected"])
            self.assertNotIn("disconnected_after_send", submission)
            failed = {row.name for row in checks.results if row.outcome == "fail"}
            self.assertIn("hot_reload_command_sent", failed)
            client.connect.assert_not_called()

    def test_hook_removal_failure_blocks_the_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def request(
                message: dict[str, object], expected: str, **_: object
            ) -> dict[str, object]:
                on = bool(message.get("on"))
                return {
                    "event": expected,
                    "on": on,
                    "ok": on,  # the removal side reports failure
                    "hooks": {"key": True, "mouse": True, "player": True, "turn": True},
                }

            client, _, _ = self.fixture(root)
            client.request.side_effect = request
            checks = Checks()
            with contextlib.redirect_stdout(io.StringIO()):
                check_hot_reload(client, checks)
            client.chat_send.assert_not_called()
            failed = {row.name for row in checks.results if row.outcome == "fail"}
            self.assertEqual(failed, {"hot_reload_hook_prepared"})
            self.assertEqual(checks.finish(), 1)

    def test_template_capture_fault_fails_the_ready_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client, _, _ = self.fixture(root)

            def text_input(text: str, **_: object) -> dict[str, object]:
                client.state_box["attempts"] = 1
                client.state_box["faults"] = 1  # attempt recorded, but as a fault
                return {"ok": True}

            client.text_input.side_effect = text_input
            report_path = root / "report.json"
            checks = Checks()
            with contextlib.redirect_stdout(io.StringIO()):
                check_hot_reload(client, checks, report_path=report_path)
            client.chat_send.assert_not_called()
            client.request.assert_not_called()
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["template"]["attempts_after"], 1)
            failed = {row.name for row in checks.results if row.outcome == "fail"}
            self.assertEqual(failed, {"hot_reload_template_ready"})

    def test_invalid_template_without_faults_stops_before_unhooking(self) -> None:
        for reason in (
            "command_template_too_long",
            "command_template_owner_mismatch",
            "command_template_stale",
            "command_template_busy",
        ):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                client, _, _ = self.fixture(root)
                client.state_box["template_error"] = reason
                checks = Checks()
                report_path = root / "report.json"
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    patch("driver.selftest_lifecycle.time.sleep"),
                    patch("driver.selftest_lifecycle.time.monotonic", side_effect=range(100)),
                ):
                    check_hot_reload(client, checks, report_path=report_path)
                client.request.assert_not_called()
                client.chat_send.assert_not_called()
                client.connect.assert_not_called()
                failed = {row.name for row in checks.results if row.outcome == "fail"}
                self.assertEqual(failed, {"hot_reload_template_ready"})
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(report["template"]["faults_after"], 0)
                self.assertEqual(report["template"]["template"]["reason"], reason)

    def test_missing_template_readiness_stops_before_unhooking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client, _, _ = self.fixture(root)
            original_status = client.status.side_effect

            def status() -> dict[str, object]:
                result = original_status()
                result.pop("command_template")
                return result

            client.status.side_effect = status
            checks = Checks()
            with contextlib.redirect_stdout(io.StringIO()):
                check_hot_reload(client, checks, report_path=root / "report.json")
            client.request.assert_not_called()
            client.chat_send.assert_not_called()
            failed = {row.name for row in checks.results if row.outcome == "fail"}
            self.assertEqual(failed, {"hot_reload_template_ready"})

    def test_template_publication_can_finish_after_attempt_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client, _, _ = self.fixture(root)
            original_status = client.status.side_effect
            busy_seen = False

            def status() -> dict[str, object]:
                nonlocal busy_seen
                result = original_status()
                if client.state_box["attempts"] and not busy_seen:
                    busy_seen = True
                    result["command_template"] = {"ready": False, "reason": "command_template_busy"}
                return result

            client.status.side_effect = status
            checks = Checks()
            with (
                contextlib.redirect_stdout(io.StringIO()),
                patch("driver.selftest_lifecycle.time.sleep"),
            ):
                check_hot_reload(client, checks, report_path=root / "report.json")
            self.assertTrue(busy_seen)
            self.assertTrue(all(row.outcome == "pass" for row in checks.results))

    def test_restore_failure_still_closes_and_writes_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def request(
                message: dict[str, object], expected: str, **_: object
            ) -> dict[str, object]:
                if message.get("on") is True:
                    raise ModPipeError("restore failed")
                return {
                    "event": expected,
                    "on": False,
                    "ok": True,
                    "hooks": dict.fromkeys(("key", "mouse", "player", "turn"), False),
                }

            client, _, _ = self.fixture(root)
            client.request.side_effect = request
            report_path = root / "report.json"
            with (
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(ModPipeError, "restore failed"),
            ):
                check_hot_reload(client, Checks(), report_path=report_path)
            self.assertTrue(report_path.exists(), "restore failure must not skip the report")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertIn("restore failed", report["cleanup_error"])
            client.close.assert_called()

    def test_primary_and_restore_failures_are_both_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client, _, _ = self.fixture(root)
            original_request = client.request.side_effect

            def request(message: dict[str, object], expected: str, **kwargs: object) -> object:
                if message.get("on") is True:
                    raise ModPipeError("secondary restore failure")
                return original_request(message, expected, **kwargs)

            client.request.side_effect = request
            primary = ModTimeout("primary wait failure")
            client.wait_ready.side_effect = primary
            checks = Checks()
            report_path = root / "report.json"
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ModTimeout) as raised:
                check_hot_reload(client, checks, report_path=report_path)
            self.assertIs(raised.exception, primary)
            self.assertTrue(any("secondary restore failure" in note for note in primary.__notes__))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertIn("primary wait failure", report["error"])
            self.assertIn("secondary restore failure", report["cleanup_error"])
            self.assertEqual(client.close.call_count, 2)
            failed = {row.name for row in checks.results if row.outcome == "fail"}
            self.assertIn("hot_reload_hook_restored", failed)

    def test_failed_check_and_restore_error_survive_early_return(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client, _, _ = self.fixture(root)
            original_request = client.request.side_effect

            def request(message: dict[str, object], expected: str, **kwargs: object) -> object:
                if message.get("on") is True:
                    raise ModPipeError("restore after failed prepare")
                reply = original_request(message, expected, **kwargs)
                reply["ok"] = False
                return reply

            client.request.side_effect = request
            checks = Checks()
            report_path = root / "report.json"
            with (
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(ModPipeError, "restore after failed prepare"),
            ):
                check_hot_reload(client, checks, report_path=report_path)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            failed = {row["name"] for row in report["checks"] if row["outcome"] == "fail"}
            self.assertEqual(failed, {"hot_reload_hook_prepared", "hot_reload_hook_restored"})
            self.assertIn("restore after failed prepare", report["cleanup_error"])
            client.close.assert_called_once()
            client.chat_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
