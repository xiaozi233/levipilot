"""dev.py harness.json handling: merge instead of clobber, plus a backup."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_empty_update_clears_stale_launch_actions_and_can_restore(self) -> None:
        previous = {"auto_enter": "旧世界", "options": {"gfx_gamma": "1.2"}, "allow_write": True}
        self.path.write_text(json.dumps(previous), encoding="utf-8")
        dev.write_auto_config(self.dir, None, {})
        self.assertNotIn("auto_enter", self.read())
        self.assertNotIn("options", self.read())
        self.assertTrue(self.read()["allow_write"])
        self.assertTrue(dev.restore_config(self.dir))
        self.assertEqual(self.read(), previous)

    def test_write_config_does_not_build_stop_install_or_launch(self) -> None:
        with (
            patch.object(dev, "probe_is_stale", side_effect=AssertionError("build inspection")),
            patch.object(dev, "build_mod", side_effect=AssertionError("build")),
            patch.object(dev, "kill_game", side_effect=AssertionError("stop")),
            patch.object(dev, "install_mod", side_effect=AssertionError("install")),
            patch.object(dev.subprocess, "Popen", side_effect=AssertionError("launch")),
        ):
            # The exe deliberately does not exist: writing configuration needs none.
            code = dev.main(
                [
                    "run",
                    "--write-config",
                    "--build",
                    "--version-dir",
                    str(self.dir),
                    "--world",
                    "测试",
                ]
            )
        self.assertEqual(code, 0)
        config = json.loads((self.dir / "mods/probe/harness.json").read_text(encoding="utf-8"))
        self.assertEqual(config["auto_enter"], "测试")


if __name__ == "__main__":
    unittest.main()
