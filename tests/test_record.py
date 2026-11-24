from __future__ import annotations

import math
import struct
import unittest

from driver.harness.record import (
    ENTRY_FORMAT,
    ENTRY_SIZE,
    FLAG_ACTION_SPRINT,
    FLAG_FLYING,
    FLAG_GLIDING,
    FLAG_IN_AIR,
    FLAG_INPUT_W,
    FLAG_JUMP_EDGE,
    FLAG_ON_GROUND,
    FLAG_SWIMMING,
    RecordEntry,
    WatchSample,
    block_hash,
    decode_dump,
    decode_hex,
    decode_watch_chunk,
    decode_watch_dump,
    decode_watch_row,
)


def pack_entry(**overrides: object) -> bytes:
    values: dict[str, object] = {
        "tick": 7,
        "x": 1.5,
        "y": 64.25,
        "z": -3.0,
        "vx": 0.1,
        "vy": 0.42,
        "vz": -0.2,
        "yaw": 90.0,
        "pitch": -10.0,
        "feet_y": 63.0,
        "flags": FLAG_ON_GROUND | FLAG_JUMP_EDGE | FLAG_INPUT_W,
        "block_below": block_hash("minecraft:stone"),
        "block_feet": block_hash("minecraft:air"),
        "block_head": block_hash("minecraft:air"),
        "health_milli": 20000,
    }
    values.update(overrides)
    return struct.pack(ENTRY_FORMAT, *values.values())


class RecordDecodeTests(unittest.TestCase):
    def test_entry_size_matches_mod(self) -> None:
        self.assertEqual(ENTRY_SIZE, 64)
        self.assertEqual(struct.calcsize(ENTRY_FORMAT), 64)

    def test_decode_hex_fields_and_flags(self) -> None:
        data = pack_entry() + pack_entry(tick=8, flags=FLAG_IN_AIR)
        entries = decode_hex(data.hex())
        self.assertEqual(len(entries), 2)
        first, second = entries
        self.assertEqual(first.tick, 7)
        self.assertAlmostEqual(first.y, 64.25)
        self.assertTrue(first.on_ground)
        self.assertTrue(first.jump_edge)
        self.assertEqual(first.input["w"], True)
        self.assertFalse(first.input["jump"])
        self.assertEqual(first.health, 20.0)
        self.assertTrue(second.in_air)
        self.assertFalse(second.on_ground)

    def test_decode_dump_json_rows(self) -> None:
        entry = decode_hex(pack_entry().hex())[0]
        message = {
            "event": "record_dump",
            "count": 1,
            "rows": [entry.to_dict()],
        }
        decoded = decode_dump(message)
        self.assertEqual(decoded[0].tick, 7)
        self.assertEqual(decoded[0].block_below, block_hash("minecraft:stone"))

    def test_truncated_payload_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            decode_hex("00" * (ENTRY_SIZE - 1))

    def test_block_hash_matches_fnv1a32(self) -> None:
        # FNV-1a 32 of "minecraft:stone", computed independently.
        self.assertEqual(block_hash("minecraft:stone"), 0xB8BAD8AB)
        self.assertEqual(block_hash(""), 0x811C9DC5)


class RecordEntryTests(unittest.TestCase):
    def test_from_bytes_offset(self) -> None:
        data = b"\x00" * 4 + pack_entry()
        entry = RecordEntry.from_bytes(data, 4)
        self.assertEqual(entry.tick, 7)
        with self.assertRaises(ValueError):
            RecordEntry.from_bytes(data, 8)

    def test_completed_delta_and_horizontal_speed(self) -> None:
        packed = decode_hex(pack_entry(vx=0.3, vy=0.42, vz=-0.4).hex())[0]
        self.assertEqual(packed.completed_delta, (packed.vx, packed.vy, packed.vz))
        self.assertAlmostEqual(packed.vx, 0.3, places=6)
        self.assertAlmostEqual(packed.vy, 0.42, places=6)
        self.assertAlmostEqual(packed.vz, -0.4, places=6)
        self.assertAlmostEqual(packed.horizontal_speed, 0.5, places=6)

        exact = RecordEntry(1, 0.0, 0.0, 0.0, 0.3, -0.1, 0.4, 0.0, 0.0, 0.0, 0, 0, 0, 0, 20000)
        self.assertEqual(exact.completed_delta, (0.3, -0.1, 0.4))
        self.assertEqual(exact.horizontal_speed, math.hypot(0.3, 0.4))

    def test_pose_flags_and_watch_decoding(self) -> None:
        import base64

        packed = decode_hex(
            pack_entry(flags=FLAG_SWIMMING | FLAG_FLYING | FLAG_GLIDING | FLAG_ACTION_SPRINT).hex()
        )[0]
        self.assertTrue(packed.swimming)
        self.assertTrue(packed.flying)
        self.assertTrue(packed.gliding)
        self.assertTrue(packed.action_sprint)

        raw = struct.pack("<4QII", 0x40000000, 0xFFFFFFFE, 0, 0, 0b11, 0)
        b64 = base64.b64encode(raw).decode("ascii")
        chunk = decode_watch_chunk(b64, 2)
        self.assertEqual(len(chunk), 1)
        s = chunk[0]
        self.assertIsInstance(s, WatchSample)
        self.assertTrue(s.valid(0))
        self.assertTrue(s.valid(1))
        self.assertFalse(s.valid(2))
        self.assertAlmostEqual(s.as_f32(0), 2.0)
        self.assertEqual(s.as_i32(1), -2)
        self.assertEqual(s.as_u32(1), 0xFFFFFFFE)
        self.assertEqual(
            decode_watch_dump({"event": "record_dump", "watch_count": 2, "watch_b64": b64}),
            chunk,
        )
        json_dump = decode_watch_dump(
            {"event": "record_dump", "watch_count": 1, "rows": [{"watch": [60], "watch_mask": 1}]}
        )
        self.assertEqual(json_dump, [WatchSample((60,), 1)])

    def test_watch_typed_decode_and_named_rows(self) -> None:
        f64_bits = struct.unpack("<Q", struct.pack("<d", 2.25))[0]
        sample = WatchSample((0x3FC00000, (1 << 5) | (1 << 60), f64_bits), 0b111)
        self.assertEqual(sample.as_u64(1), (1 << 5) | (1 << 60))
        self.assertAlmostEqual(sample.as_f64(2), 2.25, places=12)
        self.assertTrue(sample.as_bit(1, 5))
        self.assertTrue(sample.as_bit(1, 60))
        self.assertFalse(sample.as_bit(1, 6))
        self.assertAlmostEqual(float(sample.decode(0, "f32")), 1.5)
        self.assertAlmostEqual(float(sample.decode(2, "f64")), 2.25)
        self.assertEqual(sample.decode(1, "hex"), hex((1 << 5) | (1 << 60)))
        self.assertTrue(sample.decode(1, "bit:60"))
        self.assertIsNone(sample.decode(3, "u32"))
        with self.assertRaises(ValueError):
            sample.decode(0, "badfmt")

        specs = [
            {"name": "slow_y", "fmt": "f32"},
            {
                "name": "adf_hi",
                "fmt": "u64",
                "omit_raw": True,
                "bits": {"in_scaf": 5, "over_desc": 36},
            },
            {"name": "dbl", "fmt": "f64"},
            {"name": "missing", "fmt": "u32"},
        ]
        decoded = decode_watch_row(sample, specs)
        self.assertEqual(decoded["slow_y"], 1.5)
        self.assertEqual(decoded["dbl"], 2.25)
        self.assertNotIn("adf_hi", decoded)
        self.assertTrue(decoded["in_scaf"])
        self.assertFalse(decoded["over_desc"])
        self.assertIsNone(decoded["missing"])


if __name__ == "__main__":
    unittest.main()
