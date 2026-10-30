from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from driver.pe_map import PdataIndex, PeImage, Section, default_exe, parse_headers

IMAGE_BASE = 0x140000000


def synthetic_pe() -> bytes:
    """Minimal PE32+ with a .text and a .pdata section (3 functions + zero padding)."""
    buffer = bytearray(0x2500)
    buffer[0:2] = b"MZ"
    struct.pack_into("<I", buffer, 0x3C, 0x80)
    buffer[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", buffer, 0x84, 0x8664)  # machine
    struct.pack_into("<H", buffer, 0x86, 2)  # section count
    struct.pack_into("<H", buffer, 0x94, 0xF0)  # optional header size
    optional = 0x98
    struct.pack_into("<H", buffer, optional, 0x20B)  # PE32+
    struct.pack_into("<Q", buffer, optional + 24, IMAGE_BASE)
    table = optional + 0xF0
    struct.pack_into("<8sIIII", buffer, table, b".text", 0x2000, 0x1000, 0x2000, 0x400)
    struct.pack_into("<8sIIII", buffer, table + 40, b".pdata", 0x30, 0x3000, 0x30, 0x2400)
    for index, entry in enumerate(((0x1000, 0x1100), (0x1100, 0x1200), (0x1210, 0x1300))):
        struct.pack_into("<III", buffer, 0x2400 + index * 12, entry[0], entry[1], 0)
    buffer[0x400:0x404] = b"\x48\x89\x5c\x24"
    return bytes(buffer)


class HeaderTests(unittest.TestCase):
    def test_parse_headers(self) -> None:
        base, sections = parse_headers(synthetic_pe())
        self.assertEqual(base, IMAGE_BASE)
        self.assertEqual([section.name for section in sections], [".text", ".pdata"])
        self.assertEqual(sections[0].va, 0x1000)
        self.assertEqual(sections[0].raw, 0x400)

    def test_section_mapping(self) -> None:
        section = Section(".text", 0x1000, 0x2000, 0x400, 0x2000)
        self.assertTrue(section.covers(0x1000))
        self.assertTrue(section.covers(0x2FFF))
        self.assertFalse(section.covers(0x3000))
        self.assertEqual(section.offset_of(0x1010), 0x410)
        self.assertIsNone(section.offset_of(0x3000))

    def test_rejects_non_pe(self) -> None:
        with self.assertRaises(ValueError):
            parse_headers(b"not a pe file at all")


class PdataTests(unittest.TestCase):
    def setUp(self) -> None:
        raw = synthetic_pe()[0x2400 : 0x2400 + 0x30]
        self.index = PdataIndex(raw)

    def test_finds_containing_function(self) -> None:
        self.assertEqual(self.index.function_at(0x1000), (0x1000, 0x1100))
        self.assertEqual(self.index.function_at(0x10FF), (0x1000, 0x1100))
        self.assertEqual(self.index.function_at(0x1210), (0x1210, 0x1300))

    def test_gap_and_tail_return_none(self) -> None:
        self.assertIsNone(self.index.function_at(0x1205))
        self.assertIsNone(self.index.function_at(0x4000))

    def test_empty_blob(self) -> None:
        self.assertIsNone(PdataIndex(b"").function_at(0x1000))


class PeImageTests(unittest.TestCase):
    def test_function_and_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fake.exe"
            path.write_bytes(synthetic_pe())
            with PeImage(path) as image:
                self.assertEqual(image.image_base, IMAGE_BASE)
                self.assertEqual(
                    image.function_at(IMAGE_BASE + 0x1050),
                    (IMAGE_BASE + 0x1000, IMAGE_BASE + 0x1100),
                )
                section = image.section_of(IMAGE_BASE + 0x1050)
                self.assertIsNotNone(section)
                assert section is not None
                self.assertEqual(section.name, ".text")
                self.assertEqual(image.read(IMAGE_BASE + 0x1000, 4), b"\x48\x89\x5c\x24")
                self.assertIsNone(image.function_at(IMAGE_BASE + 0x5000))

    def test_real_binary_shapes(self) -> None:
        """Property check against the installed build; skipped when it is absent."""
        try:
            path = default_exe()
        except FileNotFoundError:
            self.skipTest("Minecraft.Windows.exe not installed")
        with PeImage(path) as image:
            self.assertGreater(image.image_base, 0)
            self.assertGreater(len(image.functions), 1000)
            text = next((s for s in image.sections if s.name == ".text"), None)
            self.assertIsNotNone(text)
            assert text is not None
            for probe in (text.va + 0x10, text.va + 0x1_0000, text.va + text.vsize // 2):
                found = image.functions.function_at(probe)
                if found is not None:
                    self.assertLessEqual(found[0], probe)
                    self.assertLess(probe, found[1])


if __name__ == "__main__":
    unittest.main()
