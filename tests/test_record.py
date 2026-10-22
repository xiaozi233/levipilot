from __future__ import annotations

import struct
import unittest

from driver.harness.record import (
    ENTRY_FORMAT,
    ENTRY_SIZE,
    FLAG_IN_AIR,
    FLAG_INPUT_W,
    FLAG_JUMP_EDGE,
    FLAG_ON_GROUND,
    RecordEntry,
    block_hash,
    decode_dump,
    decode_hex,
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


if __name__ == "__main__":
    unittest.main()
