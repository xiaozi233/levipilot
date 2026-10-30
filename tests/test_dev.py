from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from driver import dev


class ProbeStalenessTests(unittest.TestCase):
    def _with_root(self, root: Path):
        original = dev.MOD_BUILD
        dev.MOD_BUILD = root
        self.addCleanup(setattr, dev, "MOD_BUILD", original)

    def test_missing_dll_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._with_root(Path(tmp))
            self.assertTrue(dev.probe_is_stale()[0])

    def test_newer_source_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dll = root / "bin" / "probe" / "probe.dll"
            dll.parent.mkdir(parents=True)
            dll.write_bytes(b"dll")
            source = root / "src"
            source.mkdir()
            cpp = source / "a.cpp"
            cpp.write_text("x", encoding="utf-8")
            os.utime(dll, (1000, 1000))
            os.utime(cpp, (2000, 2000))
            self._with_root(root)
            stale, newest, dll_mtime = dev.probe_is_stale()
            self.assertTrue(stale)
            self.assertGreater(newest, dll_mtime)

    def test_newer_dll_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dll = root / "bin" / "probe" / "probe.dll"
            dll.parent.mkdir(parents=True)
            dll.write_bytes(b"dll")
            source = root / "src"
            source.mkdir()
            cpp = source / "a.cpp"
            cpp.write_text("x", encoding="utf-8")
            os.utime(cpp, (1000, 1000))
            os.utime(dll, (2000, 2000))
            self._with_root(root)
            self.assertFalse(dev.probe_is_stale()[0])


class IsolatedWorldHintTests(unittest.TestCase):
    def test_finds_level_name_under_isolated_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            version = Path(tmp) / "1.26.51.01"
            world = (
                version
                / "Minecraft Bedrock"
                / "Users"
                / "123"
                / "games"
                / "com.mojang"
                / "minecraftWorlds"
                / "abc="
            )
            world.mkdir(parents=True)
            (world / "levelname.txt").write_text("超平坦测试\n", encoding="utf-8")
            found = dev.find_isolated_worlds(version, "超平坦测试")
            self.assertEqual(found, [world])
            self.assertEqual(dev.find_isolated_worlds(version, "missing"), [])


if __name__ == "__main__":
    unittest.main()
