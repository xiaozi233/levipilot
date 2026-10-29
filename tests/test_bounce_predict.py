from __future__ import annotations

import contextlib
import io
import unittest

from driver.bounce_predict import PROBES, SLIME_TOP, bounce_drops, predict, validate


class BouncePredictTests(unittest.TestCase):
    def test_recorded_sweeps_reproduce(self) -> None:
        # 60 rows of hook-captured MoveRequest state paired with the recorded outcome.
        self.assertTrue(list(PROBES.glob("live_sweep_*.json")), f"missing sweeps in {PROBES}")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            mismatches = validate()
        self.assertEqual(mismatches, 0, buffer.getvalue())

    def test_known_drops(self) -> None:
        cases = [
            (3, 0.75, False),
            (3, 1.0, False),
            (3, 1.25, True),
            (3, 2.5, True),
            (3, 3.0, True),
            (3, 3.25, False),
            (2, 1.125, False),
            (2, 1.5625, False),
            (2, 1.0, True),
            (1, 0.75, True),
            (0, 0.75, True),
            (4, 0.25, False),
            (4, 6.0, False),
        ]
        for layers, drop, expected in cases:
            cover_top = SLIME_TOP + layers * 0.125
            with self.subTest(layers=layers, drop=drop):
                self.assertEqual(predict(cover_top, drop).bounce, expected)

    def test_gather_box_matches_captured_state(self) -> None:
        result = predict(SLIME_TOP + 0.375, 3.0)
        self.assertAlmostEqual(result.pre_y, -58.3195610, places=4)
        self.assertAlmostEqual(result.dy, -0.6517090, places=4)
        self.assertAlmostEqual(result.box_lo, -59.1712685, places=4)

    def test_two_layer_gaps(self) -> None:
        self.assertEqual(
            bounce_drops(2),
            [1.125, 1.5625, 2.0625, 2.6875, 3.3125, 4.0625, 4.8125, 5.6875],
        )

    def test_thin_covers_never_gap(self) -> None:
        for layers in (0, 1):
            with self.subTest(layers=layers):
                self.assertEqual(bounce_drops(layers, hi=12.0), [])

    def test_thick_covers_pick_the_cover(self) -> None:
        for layers in (4, 5):
            cover_top = SLIME_TOP + layers * 0.125
            with self.subTest(layers=layers):
                self.assertEqual(predict(cover_top, 0.25).picked, "cover")


if __name__ == "__main__":
    unittest.main()
