from __future__ import annotations

import struct
import unittest

from driver.harness.hooks import HookHit, decode_hook_hits, float_from_bits


def bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


class HookDecodeTests(unittest.TestCase):
    def test_float_from_bits(self) -> None:
        self.assertAlmostEqual(float_from_bits(bits(1.0)), 1.0)
        self.assertAlmostEqual(float_from_bits(bits(-0.5)), -0.5)

    def test_decode_hook_hits(self) -> None:
        hits = decode_hook_hits(
            {
                "event": "hook_hits",
                "hits": [
                    {
                        "seq": 3,
                        "hook": 7,
                        "tag": "jps",
                        "tid": 42,
                        "tick": 100,
                        "args": [0x1234, bits(1.0), 0, 0],
                        "b_float": 1.0,
                        "ret": 0x140000000,
                        "stack": [1, 2, 3],
                    }
                ],
            }
        )
        self.assertEqual(len(hits), 1)
        hit: HookHit = hits[0]
        self.assertEqual(hit.seq, 3)
        self.assertEqual(hit.hook, 7)
        self.assertEqual(hit.args[0], 0x1234)
        self.assertAlmostEqual(hit.b_float, 1.0)
        self.assertEqual(hit.stack, (1, 2, 3))

    def test_short_args_are_padded(self) -> None:
        hit = HookHit.from_json({"seq": 1, "args": [5]})
        self.assertEqual(hit.args, (5, 0, 0, 0))
        self.assertEqual(hit.b_float, 0.0)

    def test_mem_snapshot_decodes(self) -> None:
        raw = struct.pack("<6f", 1.5, -2.25, 3.0, 4.0, 5.0, 6.0)
        hit = HookHit.from_json(
            {
                "seq": 9,
                "args": [0x1000, 0, 0, 0],
                "mem": {
                    "arg": 0,
                    "offset": 4,
                    "len": len(raw),
                    "ok": True,
                    "addr": 0x1004,
                    "hex": raw.hex(),
                    "f32": list(struct.unpack("<6f", raw)),
                },
            }
        )
        self.assertTrue(hit.mem_ok)
        self.assertEqual(hit.mem_arg, 0)
        self.assertEqual(hit.mem_offset, 4)
        self.assertEqual(hit.mem_addr, 0x1004)
        self.assertEqual(hit.mem, raw)
        self.assertEqual(hit.mem_u32, struct.unpack("<6I", raw))
        self.assertAlmostEqual(hit.mem_f32[1], -2.25)

    def test_mem_snapshot_failure_keeps_metadata(self) -> None:
        hit = HookHit.from_json(
            {
                "seq": 10,
                "mem": {"arg": 2, "offset": 0, "len": 8, "ok": False, "addr": 0x40, "hex": ""},
            }
        )
        self.assertFalse(hit.mem_ok)
        self.assertEqual(hit.mem, b"")
        self.assertEqual(hit.mem_u32, ())
        self.assertEqual(hit.mem_addr, 0x40)

    def test_unrelated_message_returns_empty(self) -> None:
        self.assertEqual(decode_hook_hits({"event": "tick"}), [])


if __name__ == "__main__":
    unittest.main()
