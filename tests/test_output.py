from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.output import summarize, write_run  # noqa: E402
from driver.runner import Mark, RunResult  # noqa: E402
from driver.scenario import normalize  # noqa: E402


def make_result() -> RunResult:
    scen = normalize(
        {
            "name": "unit",
            "steps": [{"type": "input", "forward": 1, "ticks": 2}],
            "sample": ["pos", "vel", "on_ground", "flags"],
        }
    )
    samples = [
        {
            "t": 0,
            "ok": True,
            "pos": [0.0, 10.0, 0.0],
            "vel": [0.0, 0.0, 0.1],
            "on_ground": True,
            "yaw": 0.0,
            "pitch": 0.0,
            "flags_lo": "0",
            "flags_mid": "0",
            "flags_hi": "0",
            "input": {"x": 0.0, "y": 1.0, "flags": 0},
        },
        {
            "t": 1,
            "ok": True,
            "pos": [0.0, 10.0, 0.1],
            "vel": [0.0, 0.0, 0.2],
            "on_ground": True,
            "yaw": 0.0,
            "pitch": 0.0,
            "flags_lo": "2",
            "flags_mid": "0",
            "flags_hi": "0",
            "input": {"x": 0.0, "y": 1.0, "flags": 0},
        },
    ]
    return RunResult(
        scenario=scen,
        marks=[Mark(0, "input", 0, 2, {"ok": True})],
        recording={"samples": samples, "dropped": 0, "start_tick": 0, "end_tick": 2},
        start_state={"ok": True, "pos": [0, 10, 0]},
        end_state={"ok": True, "pos": [0, 10, 0.1]},
        started_at="2026-10-18T00:00:00",
        finished_at="2026-10-18T00:00:01",
    )


class SummaryTest(unittest.TestCase):
    def test_summary_math(self) -> None:
        summary = summarize(make_result())
        self.assertEqual(summary["ticks_recorded"], 2)
        self.assertAlmostEqual(summary["displacement"][2], 0.1)
        self.assertAlmostEqual(summary["distance"], 0.1)
        # speed comes from consecutive positions, not from StateVector.vel
        self.assertAlmostEqual(summary["max_h_speed"], 0.1)
        self.assertAlmostEqual(summary["mean_h_speed"], 0.1)
        self.assertAlmostEqual(summary["max_vel_h_speed"], 0.2)
        self.assertEqual(summary["speed_basis"], "pos_delta")
        self.assertEqual(summary["on_ground_ratio"], 1.0)
        row = summary["steps"][0]
        self.assertEqual(row["type"], "input")
        self.assertAlmostEqual(row["distance"], 0.1)
        self.assertAlmostEqual(row["max_h_speed"], 0.1)

    def test_speed_skips_tick_gaps(self) -> None:
        result = make_result()
        samples = result.recording["samples"]
        samples[1] = dict(samples[1], t=5, pos=[0.0, 10.0, 3.0])
        summary = summarize(result)
        # a 4-tick gap must not be reported as a single-tick speed of 3.0
        self.assertIsNone(summary["max_h_speed"])

    def test_freeze_in_the_middle_is_flagged(self) -> None:
        result = make_result()
        # move, then stand still for 9 ticks, then move again
        samples = []
        z = 0.0
        for t in range(24):
            if 6 <= t < 15:
                pass  # frozen
            else:
                z += 0.2
            samples.append(
                {"t": t, "ok": True, "pos": [0.0, 10.0, z], "vel": [0.0, 0.0, 0.1],
                 "on_ground": True, "input": {"x": 0.0, "y": 1.0}}
            )
        result.recording["samples"] = samples
        result.marks = [Mark(0, "input", 0, 24, {"ok": True})]
        row = summarize(result)["steps"][0]
        self.assertTrue(row["stalled"])
        self.assertEqual(row["freeze_ticks"], 9)

    def test_wall_stop_is_not_a_freeze(self) -> None:
        result = make_result()
        # move, then stay blocked at a wall until the step ends: not a freeze
        samples = []
        z = 0.0
        for t in range(24):
            if t < 6:
                z += 0.2
            samples.append(
                {"t": t, "ok": True, "pos": [0.0, 10.0, z], "vel": [0.0, 0.0, 0.1],
                 "on_ground": True, "input": {"x": 0.0, "y": 1.0}}
            )
        result.recording["samples"] = samples
        result.marks = [Mark(0, "input", 0, 24, {"ok": True})]
        row = summarize(result)["steps"][0]
        self.assertFalse(row["stalled"])

    def test_summary_handles_empty_recording(self) -> None:
        result = make_result()
        result.recording["samples"] = []
        summary = summarize(result)
        self.assertIsNone(summary["distance"])
        self.assertIsNone(summary["max_h_speed"])


class WriteRunTest(unittest.TestCase):
    def test_jsonl_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jsonl, summary_path = write_run(
                make_result(), tmp, sample_fields=["pos", "vel", "flags"]
            )
            self.assertTrue(jsonl.exists())
            self.assertTrue(summary_path.exists())

            lines = [
                json.loads(line)
                for line in jsonl.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(lines[0]["type"], "meta")
            self.assertEqual(lines[1]["type"], "sample")
            self.assertEqual(lines[1]["step"], 0)
            self.assertIn("pos", lines[1])
            self.assertNotIn("input", lines[1])
            self.assertEqual(lines[1]["flags"]["lo"], "0")
            self.assertEqual(lines[-1]["type"], "summary")

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["scenario"], "unit")


if __name__ == "__main__":
    unittest.main()
