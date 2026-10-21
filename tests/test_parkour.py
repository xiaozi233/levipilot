"""Pure-Python tests for parkour run analysis (no game required)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from driver.parkour import (
    Tick,
    compare_runs,
    load_ticks,
    segment_jumps,
    splits,
    summarize,
)


def tick(
    t: int,
    x: float,
    y: float,
    z: float = 0.0,
    *,
    on_ground: bool = True,
    jump_edge: bool = False,
    block_below: str = "minecraft:stone",
    health: float = 20.0,
) -> Tick:
    return Tick.from_mapping(
        {
            "tick": t,
            "x": x,
            "y": y,
            "z": z,
            "vx": 0.0,
            "vy": 0.0,
            "vz": 0.0,
            "on_ground": on_ground,
            "jump_edge": jump_edge,
            "block_below": block_below,
            "health": health,
        }
    )


class JumpSegmentationTests(unittest.TestCase):
    def test_single_jump(self) -> None:
        ticks = [
            tick(0, 0.0, 64.0),
            tick(1, 1.0, 65.0, on_ground=False, jump_edge=True),
            tick(2, 2.0, 65.5, on_ground=False),
            tick(3, 3.0, 64.0),
        ]
        jumps = segment_jumps(ticks)
        self.assertEqual(len(jumps), 1)
        jump = jumps[0]
        self.assertTrue(jump.landed)
        self.assertTrue(jump.jumped)
        self.assertEqual(jump.takeoff_tick, 0)
        self.assertEqual(jump.landing_tick, 3)
        self.assertEqual(jump.air_ticks, 3)
        self.assertAlmostEqual(jump.distance, 3.0)
        self.assertAlmostEqual(jump.apex, 1.5)
        self.assertEqual(jump.landing_block, "minecraft:stone")

    def test_multiple_jumps(self) -> None:
        ticks = [
            tick(0, 0.0, 64.0),
            tick(1, 1.0, 65.0, on_ground=False),
            tick(2, 2.0, 64.0),
            tick(3, 3.0, 64.0),
            tick(4, 4.0, 65.0, on_ground=False),
            tick(5, 6.0, 64.0, block_below="minecraft:grass_block"),
        ]
        jumps = segment_jumps(ticks)
        self.assertEqual(len(jumps), 2)
        self.assertAlmostEqual(jumps[0].distance, 2.0)
        self.assertAlmostEqual(jumps[1].distance, 3.0)
        self.assertEqual(jumps[1].landing_block, "minecraft:grass_block")

    def test_midair_start_ignored(self) -> None:
        ticks = [
            tick(0, 0.0, 65.0, on_ground=False),
            tick(1, 1.0, 64.0),
            tick(2, 2.0, 64.0),
        ]
        self.assertEqual(segment_jumps(ticks), [])

    def test_unfinished_jump(self) -> None:
        ticks = [
            tick(0, 0.0, 64.0),
            tick(1, 1.0, 65.0, on_ground=False),
            tick(2, 2.0, 65.0, on_ground=False),
        ]
        jumps = segment_jumps(ticks)
        self.assertEqual(len(jumps), 1)
        self.assertFalse(jumps[0].landed)
        self.assertIsNone(jumps[0].landing_tick)
        self.assertEqual(jumps[0].air_ticks, 2)

    def test_jump_edge_before_takeoff(self) -> None:
        ticks = [
            tick(0, 0.0, 64.0),
            tick(1, 0.0, 64.0, jump_edge=True),
            tick(2, 0.0, 64.0),
            tick(3, 1.0, 65.0, on_ground=False),
            tick(4, 2.0, 64.0),
        ]
        jumps = segment_jumps(ticks)
        self.assertEqual(len(jumps), 1)
        self.assertTrue(jumps[0].jumped)


class SummaryTests(unittest.TestCase):
    def test_summary_and_splits(self) -> None:
        ticks = [
            tick(0, 0.0, 64.0),
            tick(1, 1.0, 65.0, on_ground=False),
            tick(2, 2.0, 65.0, on_ground=False),
            tick(3, 3.0, 64.0),
        ]
        jumps = segment_jumps(ticks)
        summary = summarize(jumps)
        self.assertEqual(summary["jumps"], 1)
        self.assertEqual(summary["completed"], 1)
        self.assertAlmostEqual(summary["avg_distance"], 3.0)
        self.assertAlmostEqual(summary["avg_apex"], 1.0)
        self.assertAlmostEqual(summary["avg_air_ticks"], 3.0)

        marks = splits(ticks, [{"name": "land", "x": 3.0, "z": 0.0, "radius": 0.5}])
        self.assertEqual(marks[0]["tick"], 3)
        self.assertAlmostEqual(marks[0]["time_s"], 0.15)

    def test_compare_runs(self) -> None:
        a = [
            tick(0, 0.0, 64.0),
            tick(1, 1.0, 65.0, on_ground=False),
            tick(2, 2.0, 64.0),
        ]
        b = [
            tick(0, 0.0, 64.0),
            tick(1, 1.0, 65.0, on_ground=False),
            tick(2, 2.0, 65.0, on_ground=False),
            tick(3, 4.0, 64.0),
        ]
        report = compare_runs(a, b)
        self.assertEqual(report["a"]["jumps"], 1)
        self.assertEqual(report["b"]["jumps"], 1)
        self.assertAlmostEqual(report["jumps"][0]["delta_distance"], 2.0)
        self.assertEqual(report["delta_total_ticks"], 1)

    def test_load_ticks_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            path.write_text(
                json.dumps({"event": "tick", "tick": 1, "x": 1.0, "y": 64.0, "z": 2.0})
                + "\n"
                + json.dumps({"event": "started"})
                + "\n",
                encoding="utf-8",
            )
            ticks = load_ticks(path)
            self.assertEqual(len(ticks), 1)
            self.assertEqual(ticks[0].x, 1.0)


if __name__ == "__main__":
    unittest.main()
