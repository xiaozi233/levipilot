from __future__ import annotations

import struct
import unittest

from driver.harness.memory import (
    describe,
    fnv1_64,
    fnv1a_64,
    format_address,
    parse_address,
    parse_hex_bytes,
    to_hex,
)


class MemoryHelperTests(unittest.TestCase):
    def test_address_format_and_parse(self) -> None:
        self.assertEqual(format_address(0x1F2E3D4C), "0x1f2e3d4c")
        self.assertEqual(parse_address(0x1000), 0x1000)
        self.assertEqual(parse_address("0x1000"), 0x1000)
        self.assertEqual(parse_address("1000"), 1000)
        with self.assertRaises(ValueError):
            parse_address(-1)
        with self.assertRaises(ValueError):
            parse_address("")

    def test_hex_round_trip(self) -> None:
        data = bytes(range(16))
        self.assertEqual(to_hex(data), "000102030405060708090a0b0c0d0e0f")
        self.assertEqual(parse_hex_bytes("00 01 02"), b"\x00\x01\x02")
        with self.assertRaises(ValueError):
            parse_hex_bytes("abc")

    def test_fnv_vectors(self) -> None:
        self.assertEqual(fnv1_64(b""), 0xCBF29CE484222325)
        self.assertEqual(fnv1a_64(b""), 0xCBF29CE484222325)
        # Public FNV-1 / FNV-1a 64-bit vectors for "a".
        self.assertEqual(fnv1_64("a"), 0xAF63BD4C8601B7BE)
        self.assertEqual(fnv1a_64("a"), 0xAF63DC4C8601EC8C)

    def test_describe_matches_little_endian_views(self) -> None:
        data = struct.pack("<fI", 1.5, 0x11223344)
        views = describe(data)
        self.assertEqual(views["f32"][0], 1.5)
        self.assertEqual(views["u32"][1], 0x11223344)
        self.assertEqual(views["u16"][2], 0x3344)
        self.assertEqual(views["u8"][4], 0x44)


if __name__ == "__main__":
    unittest.main()
