from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.blocks import (  # noqa: E402
    AIR_HASH,
    fnv1_64,
    format_block,
    name_for_hash,
    name_table,
)


class FnvTest(unittest.TestCase):
    def test_known_hashes(self) -> None:
        self.assertEqual(fnv1_64("minecraft:air"), AIR_HASH)
        self.assertEqual(fnv1_64("minecraft:cobblestone"), 0x3E53526998BB823E)
        self.assertEqual(fnv1_64("minecraft:stone"), 0x2727739C84AA7913)

    def test_hash_is_64_bit(self) -> None:
        self.assertLess(fnv1_64("minecraft:very_long_block_name_here"), 1 << 64)


class TableTest(unittest.TestCase):
    def test_table_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fake.bin"
            fake.write_bytes(b"\x00minecraft:stone\x00minecraft:oak_planks\x00")
            table = name_table(fake)
            self.assertEqual(table[fnv1_64("minecraft:stone")], "minecraft:stone")
            self.assertEqual(
                table[fnv1_64("minecraft:oak_planks")], "minecraft:oak_planks"
            )
            self.assertEqual(table[AIR_HASH], "minecraft:air")

    def test_name_for_hash_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fake.bin"
            fake.write_bytes(b"minecraft:stone")
            self.assertIsNone(name_for_hash(0x1234, fake))


class FormatTest(unittest.TestCase):
    def test_air(self) -> None:
        text = format_block(
            {"ok": True, "pos": [0, -63, 0], "air": True, "name_hash": str(AIR_HASH)}
        )
        self.assertIn("minecraft:air", text)

    def test_error(self) -> None:
        self.assertIn("failed", format_block({"ok": False, "error": "boom"}))


if __name__ == "__main__":
    unittest.main()
