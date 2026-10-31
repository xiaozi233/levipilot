"""Keep the live jump regression sensitive to incomplete/corrected recordings."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from driver.harness.record import FLAG_ON_GROUND, RecordEntry
from driver.selftest import (
    ModPipeError,
    ModTimeout,
    WorldLost,
    dump_packet_tap,
    jump_arcs,
    main,
    wait_rotation,
    wait_ticks,
)


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


if __name__ == "__main__":
    unittest.main()
