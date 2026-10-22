from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from driver.harness.spool import SpoolReader, read_spool


def write_line(handle, event: dict) -> None:
    handle.write(json.dumps(event) + "\n")
    handle.flush()


class SpoolReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "spool.jsonl"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_incremental_poll_returns_only_new_rows(self) -> None:
        reader = SpoolReader(self.path)
        with self.path.open("w", encoding="utf-8") as handle:
            write_line(handle, {"event": "tick", "tick": 1})
            write_line(handle, {"event": "tick", "tick": 2})
        rows = reader.poll()
        self.assertEqual([row["tick"] for row in rows], [1, 2])

        with self.path.open("a", encoding="utf-8") as handle:
            write_line(handle, {"event": "tick", "tick": 3})
        rows = reader.poll()
        self.assertEqual([row["tick"] for row in rows], [3])
        self.assertEqual(reader.stats.rows, 3)

    def test_partial_line_is_completed_on_next_poll(self) -> None:
        reader = SpoolReader(self.path)
        raw = json.dumps({"event": "tick", "tick": 7})
        with self.path.open("w", encoding="utf-8") as handle:
            handle.write(raw[: len(raw) // 2])
            handle.flush()
        self.assertEqual(reader.poll(), [])
        self.assertTrue(reader.stats.pending)

        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(raw[len(raw) // 2 :] + "\n")
        rows = reader.poll()
        self.assertEqual([row["tick"] for row in rows], [7])
        self.assertEqual(reader.stats.pending, "")

    def test_shrinking_file_counts_as_rotation(self) -> None:
        reader = SpoolReader(self.path)
        with self.path.open("w", encoding="utf-8") as handle:
            for tick in range(10):
                write_line(handle, {"event": "tick", "tick": tick})
        self.assertEqual(len(reader.poll()), 10)

        # Simulate the mod restarting and recreating the file with less data
        # than our previous offset.
        with self.path.open("w", encoding="utf-8") as handle:
            write_line(handle, {"event": "tick", "tick": 99})
        rows = reader.poll()
        self.assertEqual([row["tick"] for row in rows], [99])
        self.assertEqual(reader.stats.rotations, 1)

    def test_queue_overflow_marker_is_not_returned_as_a_row(self) -> None:
        reader = SpoolReader(self.path)
        with self.path.open("w", encoding="utf-8") as handle:
            write_line(handle, {"event": "tick", "tick": 1})
            write_line(handle, {"event": "queue_dropped", "count": 12})
            write_line(handle, {"event": "tick", "tick": 2})
        rows = reader.poll()
        self.assertEqual([row["tick"] for row in rows], [1, 2])
        self.assertEqual(reader.stats.dropped, 12)

    def test_read_spool_tolerates_truncated_final_line(self) -> None:
        with self.path.open("w", encoding="utf-8") as handle:
            write_line(handle, {"event": "tick", "tick": 1})
            handle.write('{"event":"tick","tick":2')
            handle.flush()
        rows, stats = read_spool(self.path)
        self.assertEqual([row["tick"] for row in rows], [1])
        self.assertEqual(stats.rows, 1)


if __name__ == "__main__":
    unittest.main()
