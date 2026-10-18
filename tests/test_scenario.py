from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.scenario import (  # noqa: E402
    DEFAULT_SAMPLE,
    ScenarioError,
    load,
    normalize,
    project_sample,
)


class LegacyShapeTest(unittest.TestCase):
    def test_legacy_input_becomes_step(self) -> None:
        scen = normalize(
            {
                "name": "flat-walk",
                "input": {"forward": 1.0, "strafe": 0.0, "jump": False},
                "ticks": 60,
                "sample": ["pos", "vel"],
            }
        )
        self.assertEqual(scen.name, "flat-walk")
        self.assertEqual(len(scen.steps), 2)
        self.assertEqual(scen.steps[0].type, "input")
        self.assertEqual(scen.steps[0].ticks, 60)
        self.assertEqual(scen.steps[0].params["forward"], 1.0)
        self.assertEqual(scen.steps[1].type, "stop")
        self.assertEqual(scen.total_ticks, 60)

    def test_legacy_defaults(self) -> None:
        scen = normalize({"input": {}, "ticks": 3})
        self.assertEqual(scen.sample, DEFAULT_SAMPLE)
        self.assertEqual(scen.steps[0].ticks, 3)


class ModernShapeTest(unittest.TestCase):
    def test_steps_and_settle_ticks(self) -> None:
        scen = normalize(
            {
                "name": "mixed",
                "steps": [
                    {"type": "tp", "pos": [0, -60, 0]},
                    {"type": "look", "yaw": 90, "pitch": -10},
                    {"type": "input", "forward": 0.5, "strafe": -1, "ticks": 20},
                    {"type": "wait", "ticks": 2},
                ],
            }
        )
        self.assertEqual([s.type for s in scen.steps], ["tp", "look", "input", "wait"])
        self.assertEqual(scen.steps[0].params["pos"], [0.0, -60.0, 0.0])
        self.assertEqual(scen.steps[0].params["mode"], "server")
        self.assertEqual(scen.steps[0].ticks, 5)
        self.assertEqual(scen.steps[1].ticks, 1)
        self.assertEqual(scen.total_ticks, 5 + 1 + 20 + 2)

    def test_tp_direct_mode(self) -> None:
        scen = normalize({"steps": [{"type": "tp", "pos": [1, 2, 3], "mode": "direct"}]})
        self.assertEqual(scen.steps[0].params["mode"], "direct")

    def test_bad_mode_rejected(self) -> None:
        with self.assertRaises(ScenarioError):
            normalize({"steps": [{"type": "tp", "pos": [1, 2, 3], "mode": "teleport"}]})

    def test_bad_vec3_rejected(self) -> None:
        with self.assertRaises(ScenarioError):
            normalize({"steps": [{"type": "tp", "pos": [1, 2]}]})

    def test_bad_step_type_rejected(self) -> None:
        with self.assertRaises(ScenarioError):
            normalize({"steps": [{"type": "fly"}]})

    def test_unknown_sample_field_rejected(self) -> None:
        with self.assertRaises(ScenarioError):
            normalize({"steps": [{"type": "wait"}], "sample": ["position"]})

    def test_nan_rejected(self) -> None:
        with self.assertRaises(ScenarioError):
            normalize({"steps": [{"type": "vel", "v": [float("nan"), 0, 0]}]})

    def test_world_direction(self) -> None:
        scen = normalize({"steps": [{"type": "input", "world": [0, 1], "ticks": 2}]})
        self.assertEqual(scen.steps[0].params["world"], [0.0, 1.0])

    def test_world_direction_must_be_pair(self) -> None:
        with self.assertRaises(ScenarioError):
            normalize({"steps": [{"type": "input", "world": [1]}]})


class SetupTest(unittest.TestCase):
    def test_commands_marked_not_executed(self) -> None:
        scen = normalize(
            {
                "steps": [{"type": "wait", "ticks": 1}],
                "setup": {"commands": ["tp @s 0 -60 0"]},
            }
        )
        self.assertFalse(scen.setup["commands_executed"])

    def test_strict_commands_rejected(self) -> None:
        with self.assertRaises(ScenarioError):
            normalize(
                {
                    "steps": [{"type": "wait", "ticks": 1}],
                    "setup": {"commands": ["tp @s 0 -60 0"], "strict": True},
                }
            )


class LoadTest(unittest.TestCase):
    def test_loads_example(self) -> None:
        path = ROOT / "scenarios" / "example.json"
        scen = load(path)
        self.assertEqual(scen.name, "example-flat-walk")
        self.assertGreater(scen.total_ticks, 0)

    def test_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{nope", encoding="utf-8")
            with self.assertRaises(ScenarioError):
                load(path)


class ProjectSampleTest(unittest.TestCase):
    def test_projection_and_flags(self) -> None:
        sample = {
            "t": 12,
            "ok": True,
            "pos": [1, 2, 3],
            "vel": [0, 0, 1],
            "on_ground": True,
            "yaw": 0,
            "pitch": 0,
            "flags_lo": "2",
            "flags_mid": "0",
            "flags_hi": "0",
            "input": {"x": 0, "y": 1, "flags": 0},
        }
        row = project_sample(sample, ["pos", "flags", "input"])
        self.assertEqual(row["t"], 12)
        self.assertEqual(row["pos"], [1, 2, 3])
        self.assertEqual(row["flags"], {"lo": "2", "mid": "0", "hi": "0"})
        self.assertEqual(row["input"]["y"], 1)
        self.assertNotIn("vel", row)

    def test_projection_error_sample(self) -> None:
        row = project_sample({"t": 3, "ok": False, "error": "boom"}, ["pos"])
        self.assertEqual(row["error"], "boom")
        self.assertNotIn("pos", row)


class JsonRoundTripTest(unittest.TestCase):
    def test_normalize_loaded_document(self) -> None:
        doc = {
            "name": "roundtrip",
            "steps": [{"type": "input", "forward": 1, "ticks": 5}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            scen = load(path)
        self.assertEqual(scen.source, path)
        self.assertEqual(scen.total_ticks, 5)


if __name__ == "__main__":
    unittest.main()
