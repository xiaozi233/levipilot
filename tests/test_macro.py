from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.macro import (  # noqa: E402
    MacroFormatError,
    MacroTick,
    generate_ticks,
    load_macro,
    macro_to_json,
    parse_macro,
    save_macro,
)


class MacroParseTest(unittest.TestCase):
    def test_nine_strings_defaults_angles(self) -> None:
        ticks = parse_macro([["true", "false", "false", "false", "false", "false", "false", "false", "false"]])
        self.assertEqual(len(ticks), 1)
        self.assertTrue(ticks[0].w)
        self.assertEqual(ticks[0].yaw, 0.0)
        self.assertEqual(ticks[0].pitch, 0.0)

    def test_eleven_strings_and_bool_variants(self) -> None:
        ticks = parse_macro(
            [["1", "0", "true", "false", "1", "0", "true", "false", "true", "12.5", "-3"]]
        )
        tick = ticks[0]
        self.assertTrue(tick.w)
        self.assertFalse(tick.a)
        self.assertTrue(tick.s)
        self.assertTrue(tick.sprint)
        self.assertTrue(tick.jump)
        self.assertTrue(tick.lmb)
        self.assertAlmostEqual(tick.yaw, 12.5)
        self.assertAlmostEqual(tick.pitch, -3.0)

    def test_rejects_short_tick(self) -> None:
        with self.assertRaises(MacroFormatError):
            parse_macro([["false"] * 10])

    def test_rejects_non_array(self) -> None:
        with self.assertRaises(MacroFormatError):
            parse_macro({"ticks": []})

    def test_rejects_bad_button(self) -> None:
        with self.assertRaises(MacroFormatError):
            parse_macro([["yes"] + ["false"] * 8])

    def test_rejects_non_finite_angle(self) -> None:
        row = ["false"] * 9 + ["nan", "0"]
        with self.assertRaises(MacroFormatError):
            parse_macro([row])


class MacroRoundTripTest(unittest.TestCase):
    def test_round_trip_matches_dmf_shape(self) -> None:
        ticks = [
            MacroTick(w=True, sprint=True, yaw=1.25, pitch=-4.0),
            MacroTick(s=True, sneak=True, jump=True, rmb=True, lmb=True, yaw=0.0, pitch=9.5),
        ]
        rows = macro_to_json(ticks)
        self.assertEqual(rows[0][0], "true")
        self.assertEqual(rows[0][4], "true")
        self.assertEqual(rows[0][9], "1.25")
        self.assertEqual(len(rows[0]), 11)
        again = parse_macro(rows)
        self.assertEqual(again[0].w, True)
        self.assertEqual(again[1].rmb, True)
        self.assertAlmostEqual(again[1].pitch, 9.5)

    def test_file_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "macros" / "test.json"
            save_macro(path, [MacroTick(w=True, yaw=2.0)])
            self.assertTrue(path.exists())
            loaded = load_macro(path)
            self.assertEqual(len(loaded), 1)
            self.assertTrue(loaded[0].w)
            self.assertEqual(loaded[0].yaw, 2.0)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsInstance(raw, list)
            self.assertEqual(len(raw[0]), 11)

    def test_generate_ticks(self) -> None:
        ticks = generate_ticks(5, forward=True, sprint=True, yaw_per_tick=0.5)
        self.assertEqual(len(ticks), 5)
        self.assertTrue(all(t.w and t.sprint for t in ticks))
        self.assertTrue(all(t.yaw == 0.5 for t in ticks))


if __name__ == "__main__":
    unittest.main()