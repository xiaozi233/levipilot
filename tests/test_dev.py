from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class GameShutdownTests(unittest.TestCase):
    def test_process_selection_matches_full_path(self) -> None:
        exe = Path("D:/versions/current/Minecraft.Windows.exe")
        rows = [
            {"ProcessId": 12, "ExecutablePath": str(exe)},
            {"ProcessId": 34, "ExecutablePath": "D:/versions/other/Minecraft.Windows.exe"},
        ]
        with patch.object(dev.subprocess, "run", return_value=MagicMock(stdout=json.dumps(rows))):
            self.assertEqual(dev.game_pids(exe), [12])

    def test_no_target_does_not_connect_or_terminate(self) -> None:
        with (
            patch.object(dev, "game_pids", return_value=[]),
            patch.object(dev, "ModClient") as client,
            patch.object(dev.subprocess, "run") as run,
        ):
            dev.kill_game(Path("D:/game/Minecraft.Windows.exe"))
        client.assert_not_called()
        run.assert_not_called()

    def test_saves_and_waits_before_stopping_selected_pid(self) -> None:
        client = MagicMock()
        client.status.return_value = {
            "in_world": True,
            "spool": "D:/game/mods/probe/spool-12.jsonl",
        }
        client.wait_event.side_effect = [dev.ModTimeout("no replay"), {"event": "world_left"}]
        client.leave_world.return_value = {"ok": True}
        client.worlds.side_effect = [{"ready": False}, {"ready": True}]
        calls = []
        client.leave_world.side_effect = lambda: calls.append("save") or {"ok": True}
        with (
            patch.object(dev, "game_pids", return_value=[12]),
            patch.object(dev, "ModClient") as factory,
            patch.object(dev.time, "sleep"),
            patch.object(dev.subprocess, "run") as terminate,
        ):
            factory.return_value.__enter__.return_value = client
            terminate.side_effect = lambda *a, **k: (
                calls.append("stop") or subprocess.CompletedProcess(a[0], 0)
            )
            dev.kill_game(Path("D:/game/Minecraft.Windows.exe"))
        self.assertEqual(calls, ["save", "stop"])
        self.assertEqual(client.worlds.call_count, 2)
        self.assertEqual(terminate.call_args.args[0], ["taskkill", "/F", "/PID", "12"])

    def test_failed_save_preserves_process(self) -> None:
        client = MagicMock()
        client.status.return_value = {"in_world": True, "spool": "spool-12.jsonl"}
        client.wait_event.side_effect = dev.ModTimeout("no replay")
        client.leave_world.return_value = {"ok": False}
        with (
            patch.object(dev, "game_pids", return_value=[12]),
            patch.object(dev, "ModClient") as factory,
            patch.object(dev.subprocess, "run") as terminate,
            self.assertRaises(SystemExit),
        ):
            factory.return_value.__enter__.return_value = client
            dev.kill_game(Path("D:/game/Minecraft.Windows.exe"))
        terminate.assert_not_called()

    def test_wrong_pipe_instance_is_never_stopped(self) -> None:
        client = MagicMock()
        client.status.return_value = {"in_world": True, "spool": "spool-34.jsonl"}
        with (
            patch.object(dev, "game_pids", return_value=[12]),
            patch.object(dev, "ModClient") as factory,
            patch.object(dev.subprocess, "run") as terminate,
            self.assertRaises(SystemExit),
        ):
            factory.return_value.__enter__.return_value = client
            dev.kill_game(Path("D:/game/Minecraft.Windows.exe"))
        terminate.assert_not_called()
        client.leave_world.assert_not_called()

    def test_old_mod_accepts_hbui_menu_but_waits_for_progress_screen(self) -> None:
        client = MagicMock()
        client.status.return_value = {"in_world": False, "spool": "spool-12.jsonl"}
        client.worlds.return_value = {"worlds": []}
        client.screen.side_effect = [
            {"name": "ui://world_saving_progress_screen"},
            {"name": "ui://./hbui/index.html"},
        ]
        client.signin.return_value = {"known": True}
        with (
            patch.object(dev, "game_pids", return_value=[12]),
            patch.object(dev, "ModClient") as factory,
            patch.object(dev.time, "sleep") as pause,
            patch.object(dev.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)),
        ):
            factory.return_value.__enter__.return_value = client
            dev.kill_game(Path("D:/game/Minecraft.Windows.exe"))
        pause.assert_called_once()
        self.assertEqual(client.screen.call_count, 2)

    def test_force_stop_still_selects_pid_and_rechecks_identity(self) -> None:
        with (
            patch.object(dev, "game_pids", side_effect=[[12], []]),
            patch.object(dev, "ModClient") as client,
            patch.object(dev.subprocess, "run") as terminate,
        ):
            dev.kill_game(Path("D:/game/Minecraft.Windows.exe"), force=True)
        client.assert_not_called()
        terminate.assert_not_called()


class MenuReadinessTests(unittest.TestCase):
    def test_cached_names_do_not_bypass_the_save_gate(self):
        client = MagicMock()
        worlds = [{"name": "test"}]
        client.worlds.side_effect = [
            {"ready": False, "worlds": worlds},
            {"ready": True, "worlds": worlds},
        ]
        with patch.object(dev.time, "sleep") as sleep:
            self.assertEqual(dev.wait_for_menu(client), worlds)
        sleep.assert_called_once()
        self.assertEqual(client.worlds.call_count, 2)

    def test_safe_empty_menu_is_ready(self):
        client = MagicMock()
        client.worlds.return_value = {"ready": True, "worlds": []}
        self.assertEqual(dev.wait_for_menu(client), [])

    def test_missing_ready_flag_does_not_prove_a_safe_menu(self):
        client = MagicMock()
        client.worlds.return_value = {"worlds": [{"name": "cached"}]}
        with (
            patch.object(dev.time, "monotonic", side_effect=[0, 0, 2]),
            patch.object(dev.time, "sleep"),
            self.assertRaises(dev.ModTimeout),
        ):
            dev.wait_for_menu(client, timeout=1)


class RecoveryTests(unittest.TestCase):
    def run_recovery(self, kind, *, force=False, stop_error=None):
        with (
            patch.object(dev, "game_pids", return_value=[12]),
            patch.object(dev, "ModClient"),
            patch.object(dev, "world_state", return_value=kind),
            patch.object(dev, "capture_disconnect_report", return_value=Path("report.json")),
            patch.object(dev, "kill_game", side_effect=stop_error) as stop,
            patch.object(dev.time, "sleep"),
            patch.object(dev.subprocess, "call", return_value=0) as restart,
        ):
            args = ["recover", "--exe", "D:/custom/Minecraft.Windows.exe", "--world", "test"]
            result = dev.main([*args, "--force"] if force else args)
        return result, stop, restart

    def test_world_refuses_without_force(self):
        result, stop, restart = self.run_recovery("world")
        self.assertEqual(result, 2)
        stop.assert_not_called()
        restart.assert_not_called()

    def test_ready_menu_needs_no_recovery(self):
        result, stop, restart = self.run_recovery("menu")
        self.assertEqual(result, 0)
        stop.assert_not_called()
        restart.assert_not_called()

    def test_unconfirmed_exit_never_falls_back_to_force(self):
        for kind in ("exiting", "disconnect_error"):
            result, stop, restart = self.run_recovery(kind, stop_error=SystemExit("not saved"))
            self.assertEqual(result, 2)
            stop.assert_called_once_with(Path("D:/custom/Minecraft.Windows.exe"))
            restart.assert_not_called()

    def test_explicit_force_preserves_custom_executable_on_restart(self):
        result, stop, restart = self.run_recovery("disconnect_error", force=True)
        self.assertEqual(result, 0)
        stop.assert_called_once_with(Path("D:/custom/Minecraft.Windows.exe"), force=True)
        argv = restart.call_args.args[0]
        self.assertEqual(
            argv[argv.index("--exe") + 1], str(Path("D:/custom/Minecraft.Windows.exe"))
        )
        self.assertEqual(argv[argv.index("--world") + 1], "test")


if __name__ == "__main__":
    unittest.main()
