"""Keep the live jump regression sensitive to incomplete/corrected recordings."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from driver.harness.record import FLAG_ON_GROUND, RecordEntry
from driver.selftest import (
    ModPipeError,
    ModProtocolError,
    ModTimeout,
    WorldLost,
    check_controls,
    check_flight,
    check_input_mode_contract,
    check_record_medium,
    check_task_operations,
    check_teleport,
    choose_sentinel_effect,
    dump_packet_tap,
    jump_arcs,
    main,
    required_effect_ids,
    wait_rotation,
    wait_ticks,
)
from driver.selftest_flight import EffectFixture
from driver.selftest_support import Checks, ViolationObservation, preserve_failure


def row(tick: int, height: float, *, ground: bool = False) -> RecordEntry:
    return RecordEntry(
        tick=tick,
        x=0.5,
        y=height + 1.62,
        z=0.5,
        vx=0,
        vy=0,
        vz=0,
        yaw=0,
        pitch=0,
        feet_y=height,
        flags=FLAG_ON_GROUND if ground else 0,
        block_below=0,
        block_feet=0,
        block_head=0,
        health_milli=20000,
    )


class JumpArcsTests(unittest.TestCase):
    def test_held_jump_has_separate_arcs_without_new_button_edges(self) -> None:
        heights = (0.0, 0.42, 0.7532, 0.3, 0.0, 0.42, 0.7532, 0.3, 0.0)
        entries = [row(tick, y - 49, ground=y == 0) for tick, y in enumerate(heights)]
        arcs = jump_arcs(entries)
        self.assertEqual(len(arcs), 2)
        self.assertEqual(arcs[0], arcs[1])
        self.assertAlmostEqual(arcs[0][1], 0.42)

    def test_server_snap_is_preserved_for_comparison(self) -> None:
        heights = (0.0, 0.0, 0.0, 0.99143, 1.1514, 0.0)
        entries = [row(tick, y, ground=y == 0) for tick, y in enumerate(heights)]
        self.assertAlmostEqual(jump_arcs(entries)[0][1], 0.99143)

    def test_incomplete_arc_is_not_evidence_of_a_complete_jump(self) -> None:
        self.assertEqual(jump_arcs([row(1, 0, ground=True), row(2, 0.42)]), [])
        self.assertEqual(jump_arcs([row(1, 0.42), row(2, 0, ground=True)]), [])

    def test_missing_tick_discards_arc_and_recovers_on_next_takeoff(self) -> None:
        entries = [
            row(1, 0, ground=True),
            row(2, 0.42),
            row(4, 0, ground=True),
            row(5, 0.42),
            row(6, 0, ground=True),
        ]
        self.assertEqual(jump_arcs(entries), [[0, 0.42, 0]])


class RotationGateTests(unittest.TestCase):
    @staticmethod
    def state(tick, yaw=55.0, pitch=15.0):
        return {"in_world": True, "tick": tick, "yaw": yaw, "pitch": pitch}

    def test_waits_for_sync_and_three_distinct_consecutive_ticks(self):
        client = Mock()
        client.state.side_effect = [
            self.state(1, 30, 0),
            self.state(2),
            self.state(2),
            self.state(3, 30, 0),
            self.state(4),
            self.state(6),
            self.state(7),
            self.state(8),
        ]
        with patch("driver.selftest.time.sleep"):
            wait_rotation(client, 55, 15)
        self.assertEqual(client.state.call_count, 8)

    def test_accepts_wrapped_yaw_with_original_tolerance(self):
        client = Mock()
        client.state.side_effect = [self.state(tick, -175.01) for tick in (1, 2, 3)]
        with patch("driver.selftest.time.sleep"):
            wait_rotation(client, 185, 15)

    def test_never_accepts_a_stale_pose(self):
        client = Mock()
        client.state.return_value = self.state(1, 30, 0)
        with self.assertRaisesRegex(ModTimeout, "yaw=30 want=55"):
            wait_rotation(client, 55, 15, timeout=0)

    def test_world_loss_stops_the_next_operation(self):
        client = Mock()
        client.state.return_value = {"in_world": False}
        with self.assertRaises(WorldLost):
            wait_rotation(client, 55, 15)
            client.teleport()
        client.teleport.assert_not_called()

    def test_tick_wait_aborts_before_stall_timeout(self):
        client = Mock()
        client.status.side_effect = [{"in_world": True, "tick": 842}, {"in_world": False}]
        with self.assertRaisesRegex(WorldLost, "842"), patch("driver.selftest.time.sleep") as sleep:
            wait_ticks(client, 10)
        sleep.assert_not_called()


class EffectExpiryTests(unittest.TestCase):
    def test_short_effect_may_expire_naturally(self):
        durations = {16: 3, 1: 100}
        self.assertEqual(required_effect_ids(durations, 2), {16, 1})
        self.assertEqual(required_effect_ids(durations, 3), {1})
        self.assertEqual(required_effect_ids(durations, 1000), set())

    def test_long_effect_remains_required(self):
        self.assertEqual(required_effect_ids({16: 3598}, 272), {16})
        self.assertEqual(required_effect_ids({16: 3600}, 3599), {16})

    def test_unexpected_early_clear_is_not_accepted(self):
        required = required_effect_ids({16: 3}, 1)
        self.assertEqual(required, {16})
        self.assertFalse(required.issubset(set()))

    def test_infinite_effect_never_expires(self):
        self.assertEqual(required_effect_ids({16: -1}, 1000000), {16})

    def test_sentinel_uses_supported_fallbacks_when_candidates_are_occupied(self):
        self.assertEqual(choose_sentinel_effect(set()), ("night_vision", 16))
        self.assertEqual(choose_sentinel_effect({16, 13, 12, 14}), ("regeneration", 10))
        self.assertEqual(choose_sentinel_effect({16, 13, 12, 14, 10, 11}), ("absorption", 22))
        self.assertIsNone(choose_sentinel_effect({16, 13, 12, 14, 10, 11, 22}))

    def test_fixture_uses_fallback_and_permits_natural_expiry(self):
        original = {
            "tick": 100,
            "effects": [{"id": effect_id, "duration": 3600} for effect_id in (16, 13, 12, 14)]
            + [{"id": 1, "duration": 20}],
        }
        long_effects = original["effects"][:-1]
        fixture = EffectFixture(original)
        client = Mock()
        client.state.return_value = {
            "tick": 105,
            "effects": [*original["effects"], {"id": 10, "duration": 71995}],
        }
        checks = Checks()
        waits = Mock(return_value=105)
        fixture.install(client, checks, waits)
        client.command.assert_called_once_with("effect @s regeneration 3600 0 true")
        self.assertEqual(checks.results[-1].outcome, "pass")

        client.state.return_value = {
            "tick": 130,
            "effects": [*long_effects, {"id": 10, "duration": 1}],
        }
        fixture.verify(client, checks, waits)
        client.command.assert_called_with("effect @s clear regeneration")
        fixture.verify_restored({"tick": 140, "effects": long_effects}, checks)
        self.assertTrue(all(row.outcome == "pass" for row in checks.results))

    def test_fixture_rejects_unexpected_effect_loss(self):
        fixture = EffectFixture({"tick": 100, "effects": [{"id": 1, "duration": 20}]})
        client = Mock()
        client.state.return_value = {"tick": 101, "effects": [{"id": 16, "duration": 71999}]}
        checks = Checks()
        fixture.verify(client, checks, Mock(return_value=101))
        self.assertEqual(checks.results[0].name, "flight_preserves_effects")
        self.assertEqual(checks.results[0].outcome, "fail")


class InterruptedSelftestTests(unittest.TestCase):
    def test_no_world_stops_before_modifications_and_preserves_the_failure(self):
        client = Mock()
        client.state.return_value = {"in_world": False}
        client.worlds.return_value = {"ready": True}
        client.input_lock.side_effect = ModPipeError("cleanup disconnected")
        output = io.StringIO()
        with (
            patch("driver.selftest.ModClient") as factory,
            patch("driver.selftest.capture_disconnect_report", side_effect=OSError("disk full")),
            patch("driver.selftest.dump_packet_tap"),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
        ):
            factory.return_value.connect.return_value = client
            self.assertEqual(main([]), 1)
        client.command.assert_not_called()
        client.input_lock.assert_called_once_with(False)
        client.close.assert_called_once()
        self.assertIn("selftest preflight: world state is menu", output.getvalue())
        self.assertIn("disk full", output.getvalue())
        self.assertIn("0/1 checks passed", output.getvalue())

    def test_unwritable_packet_dump_remains_nonfatal(self):
        client = Mock()
        client.packet_drain.return_value = {"count": 0}
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            dump_packet_tap(client, str(Path(directory)))

    def test_flight_cleanup_uses_effect_preserving_teleport(self):
        source = Path("driver/selftest.py").read_text(encoding="utf-8")
        body = source.split("def check_flight", 1)[1].split("def check_input_events", 1)[0]
        self.assertNotIn("client.reset(", body)
        self.assertIn("client.teleport(", body)
        self.assertIn("effect_fixture.verify(client, checks, wait_ticks)", body)
        self.assertIn("effect_fixture.verify_restored(restored, checks)", body)

    def test_flight_cleanup_preserves_first_error_and_type(self):
        for cause in (WorldLost("level vanished at tick 763"), ModTimeout("first request timeout")):
            with (
                self.subTest(cause=type(cause).__name__),
                tempfile.TemporaryDirectory() as directory,
            ):
                client = Mock()
                client.state.return_value = {"in_world": True, "gamemode": 0}
                client.status.return_value = {"spool": str(Path(directory) / "spool.jsonl")}
                client.command.side_effect = [cause, ModPipeError("cleanup player unknown")]
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    self.assertRaises(type(cause)) as caught,
                ):
                    check_flight(client, Checks())
                self.assertIs(caught.exception, cause)
                self.assertIn("cleanup player unknown", cause.__notes__[0])

    def test_flight_dump_uses_absolute_path_and_consumes_its_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            client.state.return_value = {"in_world": True, "gamemode": 0}
            client.status.return_value = {"spool": str(Path(directory) / "spool.jsonl")}
            cause = ModPipeError("first flight failure")
            client.command.side_effect = cause
            client.packet_bytes.return_value = {"hits": [], "count": 0}
            client.packet_drain.return_value = {"hits": [], "count": 0}
            target = str(Path(directory) / "violation.json")
            with (
                patch.dict("os.environ", {"LEVIPILOT_VIOLATION_DUMP": target}),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(ModPipeError) as caught,
            ):
                check_flight(client, Checks())
            self.assertIs(caught.exception, cause)
            client.request.assert_any_call(
                {"cmd": "inbound_dump", "path": str(Path(f"{target}.inbound.json").resolve())},
                "inbound_dump",
            )

    def test_cleanup_failure_without_primary_still_fails(self):
        with (
            self.assertRaisesRegex(ModPipeError, "cleanup alone"),
            preserve_failure(Checks(), "fixture"),
        ):
            raise ModPipeError("cleanup alone")

    def test_input_contract_always_restores_hardware_path_and_keeps_first_error(self):
        for cleanup in (None, ModPipeError("restore failed")):
            with self.subTest(cleanup=cleanup):
                client = Mock()
                cause = ModTimeout("force acknowledgement lost")
                client.request.side_effect = [
                    ModProtocolError("legacy_jump_impulse_requires_force"),
                    ModProtocolError("legacy_jump_impulse_requires_force"),
                    ModProtocolError("legacy_input_mode_requires_force"),
                    cause,
                ]
                client.input_cfg.side_effect = cleanup
                client.input_cfg.return_value = {"event": "input_cfg_ok"}
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    self.assertRaises(ModTimeout) as caught,
                ):
                    check_input_mode_contract(client, Checks())
                self.assertIs(caught.exception, cause)
                client.input_cfg.assert_called_once_with(jump_impulse=0, mode=0)
                if cleanup:
                    self.assertIn("restore failed", cause.__notes__[0])

    def test_controls_teleport_and_tasks_keep_the_first_failure(self):
        for check in (check_controls, check_teleport, check_task_operations):
            with self.subTest(check=check.__name__), tempfile.TemporaryDirectory() as directory:
                cause = WorldLost("initial level loss")
                cleanup = ModPipeError("cleanup cannot find player")
                client = Mock()
                client.state.return_value = {
                    "in_world": True,
                    "feet_x": 0.5,
                    "feet_y": -49,
                    "feet_z": 0.5,
                    "yaw": 0,
                    "pitch": 0,
                    "selected_slot": 0,
                    "gamemode": 0,
                }
                client.status.return_value = {"spool": str(Path(directory) / "spool.jsonl")}
                if check is check_controls:
                    client.look.side_effect = cause
                    client.request.side_effect = cleanup
                elif check is check_teleport:
                    client.command.side_effect = [cause, cleanup]
                else:
                    client.inventory.return_value = {"slots": [{"slot": 0, "item": None}]}
                    client.entities.return_value = {"entities": []}
                    client.blocks.return_value = {
                        "blocks": [{"name": "minecraft:air"}, {"name": "minecraft:stone"}]
                    }
                    client.command.side_effect = cause
                    client.task_stop.side_effect = cleanup
                with (
                    patch("driver.selftest.settle"),
                    contextlib.redirect_stdout(io.StringIO()),
                    self.assertRaises(WorldLost) as caught,
                ):
                    check(client, Checks())
                self.assertIs(caught.exception, cause)
                self.assertIn(str(cleanup), cause.__notes__[0])

    def test_long_recording_has_an_explicit_cli_mode(self):
        client = self.observation_client()
        with (
            patch("driver.selftest.ModClient") as factory,
            patch("driver.selftest.check_macro_long_recording") as check,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            factory.return_value.connect.return_value = client
            self.assertEqual(main(["--macro-long-recording-only"]), 0)
        check.assert_called_once_with(client, unittest.mock.ANY)
        self.assertEqual(
            [call.args[0] for call in client.input_lock.call_args_list], [True, False, False]
        )
        client.close.assert_called_once()

    def test_hot_reload_has_an_explicit_cli_mode(self):
        client = self.observation_client()
        with (
            patch("driver.selftest.ModClient") as factory,
            patch("driver.selftest.check_hot_reload") as check,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            factory.return_value.connect.return_value = client
            self.assertEqual(main(["--hot-reload-only"]), 0)
        check.assert_called_once_with(client, unittest.mock.ANY)
        client.close.assert_called_once()

    def test_specialized_entry_exports_before_closing_and_reports_world_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watch.json"
            client = self.observation_client()
            sequence = []
            client.close.side_effect = lambda: sequence.append("close")
            client.violation_drain.side_effect = lambda *_: (
                sequence.append("drain") or {"head": 0, "hits": [], "count": 0, "next": 0}
            )
            with (
                patch("driver.selftest.ModClient") as factory,
                patch.dict("os.environ", {"LEVIPILOT_VIOLATION_DUMP": str(path)}),
                patch("driver.selftest.violation_watch_address", return_value=1234),
                patch(
                    "driver.selftest.check_flight", side_effect=WorldLost("first flight failure")
                ),
                patch("driver.selftest.capture_disconnect_report", return_value="report.json"),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                factory.return_value.connect.return_value = client
                self.assertEqual(main(["--flight-only"]), 1)
            self.assertTrue(path.is_file())
            self.assertEqual(sequence[-2:], ["drain", "close"])
            client.close.assert_called_once()

    @staticmethod
    def observation_client():
        client = Mock()
        client.violation_stats.return_value = {"active": False}
        client.violation_watch.side_effect = lambda on, **_: {"active": on, "screen_hooked": on}
        client.violation_drain.return_value = {"head": 7, "next": 7, "hits": [], "count": 0}
        return client


class CheckReportingTests(unittest.TestCase):
    def test_skip_has_a_distinct_result_and_denominator(self):
        checks = Checks()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertTrue(checks.check("one", True))
            checks.skip("two", "permission disabled")
            checks.note("info", "not a check")
            self.assertEqual(checks.finish(), 0)
        self.assertEqual([r.outcome for r in checks.results], ["pass", "skip"])
        self.assertIn("1/2 checks passed (0 failed, 1 skipped", output.getvalue())

    def test_complete_mode_rejects_required_skip_but_accepts_optional_skip(self):
        for required in (True, False):
            with self.subTest(required=required):
                checks = Checks(require_complete=True)
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    checks.skip("fixture", "unavailable", required=required)
                    self.assertEqual(checks.finish(), int(required))


class RecordMediumTests(unittest.TestCase):
    def test_fixture_writes_stay_away_from_original_pose_and_restore_after_read_failure(self):
        client = Mock()
        original = {
            "feet_x": 5.1,
            "feet_y": -49,
            "feet_z": 20.4,
            "yaw": 35,
            "pitch": 10,
            "on_ground": True,
            "in_water": False,
        }
        client.state.return_value = original
        cause = ModPipeError("record download failed")
        client.record_dump_all.side_effect = cause
        with (
            patch("driver.selftest.require_world"),
            patch("driver.selftest.Builder") as builder,
            patch("driver.selftest.wait_ticks"),
            patch("driver.selftest.settle", return_value={"ok": True, "state": original}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            builder.return_value.build.return_value = [Mock(ok=True)]
            with self.assertRaises(ModPipeError) as caught:
                check_record_medium(client, Checks())
            self.assertIs(caught.exception, cause)
            commands, expected = builder.return_value.build.call_args.args
            self.assertTrue(all(command.startswith("fill 89") for command in commands))
            self.assertTrue(all(position[0] >= 898 and position[2] >= 898 for position in expected))
            client.teleport.assert_called_with(5.1, -49.0, 20.4, yaw=35.0, pitch=10.0)
            self.assertEqual(client.record_bin_stop.call_count, 1)


class ViolationObservationTests(unittest.TestCase):
    def test_incremental_dump_separates_synthetic_rows_and_records_gaps(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            client = InterruptedSelftestTests.observation_client()
            path = Path(directory) / "nested" / "report.json"
            observation = ViolationObservation(client, Checks(), str(path), lambda: 1234)
            observation.start()
            with observation.gap("panic"):
                client.panic("all")
            client.violation_drain.return_value = {
                "hits": [{"synthetic": True}, {"synthetic": False}],
                "count": 2,
                "since": 7,
                "head": 9,
                "next": 9,
                "lost": 0,
            }
            observation.finish()
            report = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(report["real_count"], 1)
            self.assertEqual(report["synthetic_count"], 1)
            client.violation_drain.assert_called_with(7, 256)
            self.assertEqual(
                [e["kind"] for e in report["observation"]["events"]],
                ["start", "armed", "gap_begin", "armed", "gap_end", "stop"],
            )
            self.assertEqual(client.violation_watch.call_args.args, (False,))

    def test_preexisting_watch_is_not_taken_down(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            client = InterruptedSelftestTests.observation_client()
            client.violation_stats.return_value = {"active": True, "screen_hooked": True}
            observation = ViolationObservation(
                client, Checks(), str(Path(directory) / "r.json"), lambda: 1
            )
            observation.start()
            observation.finish()
            self.assertTrue(all(call.args[0] for call in client.violation_watch.call_args_list))

    def test_drain_and_dump_failure_do_not_prevent_stop_or_replace_body_failure(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            client = InterruptedSelftestTests.observation_client()
            checks = Checks()
            observation = ViolationObservation(client, checks, directory, lambda: 1)
            observation.start()
            client.violation_drain.side_effect = ModPipeError("pipe lost")
            cause = WorldLost("primary")
            with self.assertRaises(WorldLost) as caught:
                try:
                    raise cause
                finally:
                    observation.finish()
            self.assertIs(caught.exception, cause)
            self.assertEqual(client.violation_watch.call_args.args, (False,))
            failures = [row.name for row in checks.results if row.outcome == "fail"]
            self.assertEqual(failures, ["violation_session_drain", "violation_session_dump"])


if __name__ == "__main__":
    unittest.main()
