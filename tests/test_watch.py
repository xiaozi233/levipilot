from __future__ import annotations

import unittest

from driver.harness.watch import HitCursor, decode_hits


def batch(next_seq: int, hits: list[tuple[int, int]], *, lost: int = 0) -> dict:
    return {
        "event": "watch_hits",
        "since": 0,
        "next": next_seq,
        "head": next_seq,
        "lost": lost,
        "hits": [
            {
                "seq": seq,
                "watch": watch,
                "tag": "vy",
                "tid": 42,
                "tick": seq,
                "rip": 0x140000000 + seq,
                "stack": [0x140000100, 0x140000200],
            }
            for seq, watch in hits
        ],
    }


class WatchDecodeTests(unittest.TestCase):
    def test_decode_hits(self) -> None:
        hits = decode_hits(
            {
                "event": "watch_hits",
                "hits": [
                    {
                        "seq": 3,
                        "watch": 1,
                        "tag": "vy",
                        "tid": 7,
                        "tick": 10,
                        "rip": 0x1234,
                        "stack": [1, 2],
                    }
                ],
            }
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].seq, 3)
        self.assertEqual(hits[0].rip, 0x1234)
        self.assertEqual(hits[0].stack, (1, 2))

    def test_cursor_resumes_from_next(self) -> None:
        cursor = HitCursor()
        first = cursor.consume(batch(3, [(1, 1), (2, 1)]))
        self.assertEqual([hit.seq for hit in first], [1, 2])
        self.assertEqual(cursor.since, 3)

        second = cursor.consume(batch(4, [(3, 1)], lost=2))
        self.assertEqual([hit.seq for hit in second], [3])
        self.assertEqual(cursor.since, 4)
        self.assertEqual(cursor.lost, 2)
        self.assertEqual(len(cursor.hits), 3)
        self.assertEqual(cursor.batches, 2)

    def test_cursor_ignores_unrelated_messages(self) -> None:
        cursor = HitCursor(since=5)
        self.assertEqual(cursor.consume({"event": "tick"}), [])
        self.assertEqual(cursor.since, 5)
        self.assertEqual(cursor.batches, 0)


if __name__ == "__main__":
    unittest.main()
