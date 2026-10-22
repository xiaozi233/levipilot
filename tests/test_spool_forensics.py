from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driver.spool_forensics import _runs, verify


class RunsTests(unittest.TestCase):
    def test_splits_on_restart(self) -> None:
        self.assertEqual(_runs([1, 2, 3, 1, 2]), [[1, 2, 3], [1, 2]])

    def test_gap_stays_in_one_run(self) -> None:
        self.assertEqual(_runs([1, 2, 4, 5]), [[1, 2, 4, 5]])

    def test_empty(self) -> None:
        self.assertEqual(_runs([]), [])


class VerifyTests(unittest.TestCase):
    def test_contiguous_last_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spool.jsonl"
            path.write_text(
                '{"event":"tick","tick":1}\n'
                '{"event":"tick","tick":2}\n'
                '{"event":"world_left"}\n'
                '{"event":"tick","tick":1}\n'
                '{"event":"tick","tick":2}\n'
                '{"event":"panic_done"}\n',
                encoding="utf-8",
            )
            result = verify(path)
        self.assertTrue(result["contiguous"])
        self.assertEqual(result["runs"], 2)
        self.assertEqual(result["last_run"], 2)
        self.assertEqual(result["last_event"], "panic_done")


if __name__ == "__main__":
    unittest.main()
