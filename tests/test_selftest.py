"""Keep the live jump regression sensitive to incomplete/corrected recordings."""

from __future__ import annotations

import unittest

from driver.harness.record import FLAG_ON_GROUND, RecordEntry
from driver.selftest import jump_arcs


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


if __name__ == "__main__":
    unittest.main()
