from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_invalid_drop_counts_do_not_discard_valid_rows(self) -> None:
        invalid = [None, {}, [], "bad", -1, 1.5, float("inf"), float("nan"), True]
        with self.path.open("w", encoding="utf-8") as handle:
            write_line(handle, {"event": "queue_dropped", "count": 12})
            for count in invalid:
                write_line(handle, {"event": "queue_dropped", "count": count})
            write_line(handle, {"event": "tick", "tick": 7})
        reader = SpoolReader(self.path)
        for rows, stats in ((reader.poll(), reader.stats), read_spool(self.path)):
            self.assertEqual(rows, [{"event": "tick", "tick": 7}])
            self.assertEqual(stats.dropped, 12)
            self.assertEqual(stats.bad_lines, len(invalid))
            self.assertEqual(stats.rows, 1)

    def test_temporary_open_failure_preserves_partial_line_and_offset(self) -> None:
        raw = '{"event":"tick","text":"中文"}\n'.encode()
        self.path.write_bytes(raw[:-4])
        reader = SpoolReader(self.path)
        self.assertEqual(reader.poll(), [])
        offset = reader.stats.offset
        self.path.write_bytes(raw)
        with patch.object(Path, "open", side_effect=PermissionError("temporarily unavailable")):
            self.assertEqual(reader.poll(), [])
        self.assertEqual(reader.stats.offset, offset)
        self.assertEqual(reader.poll(), [{"event": "tick", "text": "中文"}])
        self.assertEqual(reader.stats.pending, "")

    def test_oversized_integer_does_not_discard_following_rows(self) -> None:
        raw = b'{"event":"queue_dropped","count":' + b"9" * 10000 + b"}\n"
        self.path.write_bytes(raw + b'{"event":"tick","tick":8}\n')
        reader = SpoolReader(self.path)
        for rows, stats in ((reader.poll(), reader.stats), read_spool(self.path)):
            self.assertEqual(rows, [{"event": "tick", "tick": 8}])
            self.assertEqual(stats.bad_lines, 1)

    def test_temporary_read_failure_preserves_rotation_state_for_retry(self) -> None:
        self.path.write_bytes(b'{"partial":')
        reader = SpoolReader(self.path)
        reader.poll()
        offset, pending = reader.stats.offset, reader.stats.pending
        self.path.write_bytes(b"{}\n")
        with (
            self.path.open("rb") as handle,
            patch.object(Path, "open", return_value=handle),
            patch.object(handle, "read", side_effect=OSError("temporary failure")),
        ):
            self.assertEqual(reader.poll(), [])
        self.assertEqual(reader.stats.offset, offset)
        self.assertEqual(reader.stats.pending, pending)
        self.assertEqual(reader.stats.rotations, 0)
        self.assertEqual(reader.poll(), [{}])
        self.assertEqual(reader.stats.rotations, 1)
        self.assertEqual(reader.stats.pending, "")

    def test_rotation_to_empty_file_clears_pending_diagnostics(self) -> None:
        self.path.write_bytes(b'{"partial":')
        reader = SpoolReader(self.path)
        self.assertEqual(reader.poll(), [])
        self.path.write_bytes(b"")
        self.assertEqual(reader.poll(), [])
        self.assertEqual(reader.stats.pending, "")
        self.assertEqual(reader.stats.offset, 0)
        self.assertEqual(reader.stats.rotations, 1)

    def test_tail_limit_is_nonnegative_and_zero_reads_nothing(self) -> None:
        self.path.write_text('{"event":"tick"}\n', encoding="utf-8")
        self.assertEqual(read_spool(self.path, max_bytes=0)[0], [])
        with self.assertRaisesRegex(ValueError, "max_bytes"):
            read_spool(self.path, max_bytes=-1)


if __name__ == "__main__":
    unittest.main()
