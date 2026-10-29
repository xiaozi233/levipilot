"""dev.py harness.json handling: merge instead of clobber, plus a backup."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from driver import dev


class AutoConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "harness.json"
        self.backup = self.dir / "harness.json.prev"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_merge_keeps_hand_written_switches(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "delay_ms": 100,
                    "allow_input_inject": True,
                    "allow_selftest_stress": True,
                    "events": {"spool": False},
                    "auto_enter": "旧世界",
                }
            ),
            encoding="utf-8",
        )
        dev.write_auto_config(self.dir, None, {}, {"allow_hook_va": True})

        config = self.read()
        self.assertEqual(config["delay_ms"], 100)  # not reset to the default
        self.assertTrue(config["allow_input_inject"])
        self.assertTrue(config["allow_selftest_stress"])
        self.assertEqual(config["events"], {"spool": False})
        self.assertTrue(config["allow_hook_va"])
        # auto_enter is owned by dev.py: a stale one would race enter_world.
        self.assertNotIn("auto_enter", config)

    def test_options_are_merged_not_replaced(self) -> None:
        self.path.write_text(json.dumps({"options": {"gfx_viewdistance": "8"}}), encoding="utf-8")
        dev.write_auto_config(self.dir, None, {"gfx_gamma": "1.2"}, {})
        config = self.read()
        self.assertEqual(config["options"], {"gfx_viewdistance": "8", "gfx_gamma": "1.2"})

    def test_backup_is_the_pre_tool_state_and_survives_later_writes(self) -> None:
        self.path.write_text(json.dumps({"allow_input_inject": True}), encoding="utf-8")
        dev.write_auto_config(self.dir, "世界", {}, {"allow_hook_va": True})
        self.assertTrue(self.backup.exists())
        self.assertEqual(
            json.loads(self.backup.read_text(encoding="utf-8")), {"allow_input_inject": True}
        )

        dev.write_auto_config(self.dir, None, {}, {"allow_write": True})
        self.assertEqual(
            json.loads(self.backup.read_text(encoding="utf-8")), {"allow_input_inject": True}
        )
        self.assertTrue(self.read()["allow_write"])

    def test_restore_config_puts_the_backup_back(self) -> None:
        self.path.write_text(json.dumps({"allow_input_inject": True}), encoding="utf-8")
        dev.write_auto_config(self.dir, "世界", {}, {"allow_hook_va": True})
        self.assertTrue(dev.restore_config(self.dir))
        self.assertEqual(self.read(), {"allow_input_inject": True})
        self.assertFalse(self.backup.exists())
        self.assertFalse(dev.restore_config(self.dir))

    def test_nothing_to_write_leaves_the_file_alone(self) -> None:
        self.path.write_text(json.dumps({"allow_input_inject": True}), encoding="utf-8")
        dev.write_auto_config(self.dir, None, {}, None)
        self.assertEqual(self.read(), {"allow_input_inject": True})


if __name__ == "__main__":
    unittest.main()
