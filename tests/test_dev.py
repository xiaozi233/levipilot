from __future__ import annotations

import contextlib
import ctypes
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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
        result, stop, restart = self.run_recovery("exiting", stop_error=SystemExit("not saved"))
        self.assertEqual(result, 2)
        stop.assert_called_once_with(Path("D:/custom/Minecraft.Windows.exe"))
        restart.assert_not_called()

    def test_error_page_stops_without_the_save_gate(self):
        # No level is loaded on the error page and the world list never becomes
        # ready, so waiting for that gate can only time out: recover stops it.
        result, stop, restart = self.run_recovery("disconnect_error")
        self.assertEqual(result, 0)
        stop.assert_called_once_with(Path("D:/custom/Minecraft.Windows.exe"), force=True)
        self.assertEqual(restart.call_count, 1)

    def test_error_page_stop_failure_is_reported(self):
        result, _stop, restart = self.run_recovery(
            "disconnect_error", stop_error=SystemExit("not stopped")
        )
        self.assertEqual(result, 2)
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


class FocusTests(unittest.TestCase):
    class _User32:
        def __init__(self, foreground: int) -> None:
            self.foreground = foreground
            self.attached: list[tuple[int, int, bool]] = []

        def BringWindowToTop(self, hwnd: int) -> None:
            pass

        def SetForegroundWindow(self, hwnd: int) -> int:
            return 1

        def GetForegroundWindow(self) -> int:
            return self.foreground

        def GetWindowThreadProcessId(self, hwnd: int, process_id) -> int:
            return 42

        def AttachThreadInput(self, first: int, second: int, attach: bool) -> int:
            self.attached.append((first, second, bool(attach)))
            return 1

    class _Kernel32:
        def GetCurrentThreadId(self) -> int:
            return 7

    def test_verified_foreground_does_not_call_the_shell(self) -> None:
        user32 = self._User32(111)
        with (
            patch.object(dev, "_appactivate") as appactivate,
            patch.object(dev.time, "sleep"),
        ):
            self.assertTrue(dev._focus_with_fallback(user32, 111, 9, self._Kernel32()))
        appactivate.assert_not_called()
        self.assertEqual(user32.attached, [(7, 42, True), (7, 42, False)])

    def test_shell_fallback_is_used_when_the_plain_call_is_refused(self) -> None:
        user32 = self._User32(0)

        def activate(pid: int) -> bool:
            user32.foreground = 111
            return True

        with (
            patch.object(dev, "_appactivate", side_effect=activate) as appactivate,
            patch.object(dev.time, "sleep"),
        ):
            self.assertTrue(dev._focus_with_fallback(user32, 111, 9, self._Kernel32()))
        appactivate.assert_called_once_with(9)

    def test_unverified_foreground_is_reported_as_failure(self) -> None:
        user32 = self._User32(0)
        with (
            patch.object(dev, "_appactivate", return_value=True),
            patch.object(dev.time, "sleep"),
        ):
            self.assertFalse(dev._focus_with_fallback(user32, 111, 9, self._Kernel32()))

    def test_appactivate_failure_is_not_silent_success(self) -> None:
        user32 = self._User32(0)
        with (
            patch.object(dev, "_appactivate", return_value=False),
            patch.object(dev.time, "sleep"),
        ):
            self.assertFalse(dev._focus_with_fallback(user32, 111, 9, self._Kernel32()))


class FocusCommandTests(unittest.TestCase):
    def test_no_running_game_is_an_error_and_does_not_focus(self) -> None:
        with (
            patch.object(dev, "game_pids", return_value=[]),
            patch.object(dev, "focus_game") as focus_game,
        ):
            self.assertEqual(dev.main(["focus"]), 1)
        focus_game.assert_not_called()

    def test_focus_reports_success_and_failure(self) -> None:
        with patch.object(dev, "game_pids", return_value=[12]):
            with patch.object(dev, "focus_game", return_value=True):
                self.assertEqual(dev.main(["focus"]), 0)
            with patch.object(dev, "focus_game", return_value=False):
                self.assertEqual(dev.main(["focus"]), 1)


class DesktopHintTests(unittest.TestCase):
    def test_current_desktop_name_reads_startup_info(self) -> None:
        buf = ctypes.create_unicode_buffer("exebox-TEST123")

        def fake_get_startup_info(ptr) -> None:
            info = ctypes.cast(ptr, ctypes.POINTER(dev._STARTUPINFOW)).contents
            info.lpDesktop = ctypes.cast(buf, dev.wintypes.LPWSTR)

        fake_kernel32 = SimpleNamespace(GetStartupInfoW=fake_get_startup_info)
        with patch.object(dev.ctypes, "windll", SimpleNamespace(kernel32=fake_kernel32)):
            self.assertEqual(dev.current_desktop_name(), "exebox-TEST123")

    def test_current_desktop_name_real_call_returns_optional_str(self) -> None:
        desktop = dev.current_desktop_name()
        self.assertTrue(desktop is None or (isinstance(desktop, str) and bool(desktop)))

    def test_current_desktop_name_falls_back_to_none_on_empty_or_error(self) -> None:
        buf = ctypes.create_unicode_buffer("")

        def empty_startup_info(ptr) -> None:
            info = ctypes.cast(ptr, ctypes.POINTER(dev._STARTUPINFOW)).contents
            info.lpDesktop = ctypes.cast(buf, dev.wintypes.LPWSTR)

        fake_kernel32 = SimpleNamespace(GetStartupInfoW=empty_startup_info)
        with patch.object(dev.ctypes, "windll", SimpleNamespace(kernel32=fake_kernel32)):
            self.assertIsNone(dev.current_desktop_name())

        broken_kernel32 = SimpleNamespace(GetStartupInfoW=MagicMock(side_effect=OSError("boom")))
        with patch.object(dev.ctypes, "windll", SimpleNamespace(kernel32=broken_kernel32)):
            self.assertIsNone(dev.current_desktop_name())

        with patch.object(dev.ctypes, "windll", None):
            self.assertIsNone(dev.current_desktop_name())

    def test_focus_failure_logs_attach_hint_only_for_isolated_desktop(self) -> None:
        buf = io.StringIO()
        with (
            patch.object(dev, "game_pids", return_value=[12]),
            patch.object(dev, "focus_game", return_value=False),
            patch.object(dev, "current_desktop_name", return_value="exebox-ISOLATED"),
            contextlib.redirect_stdout(buf),
        ):
            self.assertEqual(dev.main(["focus"]), 1)
        output = buf.getvalue()
        self.assertIn("exebox-ISOLATED", output)
        self.assertIn("isolated desktop", output)
        self.assertIn("py -3.11 driver/dev.py run --attach", output)

        for desktop in ("Default", "WinSta0\\Default", None):
            buf = io.StringIO()
            with (
                patch.object(dev, "game_pids", return_value=[12]),
                patch.object(dev, "focus_game", return_value=False),
                patch.object(dev, "current_desktop_name", return_value=desktop),
                contextlib.redirect_stdout(buf),
            ):
                self.assertEqual(dev.main(["focus"]), 1)
            self.assertNotIn("--attach", buf.getvalue())

    def test_run_focus_failure_logs_isolated_desktop_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            version_dir = Path(tmp)
            exe = version_dir / "Minecraft.Windows.exe"
            exe.write_bytes(b"exe")
            client = MagicMock()
            client.worlds.return_value = {"ready": True, "worlds": []}
            buf = io.StringIO()
            with (
                patch.object(dev, "probe_is_stale", return_value=(False, 0.0, 0.0)),
                patch.object(dev, "kill_game"),
                patch.object(dev.time, "sleep"),
                patch.object(dev, "install_mod"),
                patch.object(dev, "write_auto_config"),
                patch.object(dev.subprocess, "Popen", return_value=MagicMock(pid=99)),
                patch.object(dev, "focus_game", return_value=False),
                patch.object(dev, "current_desktop_name", return_value="exebox-SHELL"),
                patch.object(dev, "ModClient") as factory,
                contextlib.redirect_stdout(buf),
            ):
                factory.return_value.connect.return_value = client
                code = dev.main(["run", "--version-dir", str(version_dir), "--no-build"])
        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("exebox-SHELL", output)
        self.assertIn("py -3.11 driver/dev.py run --attach", output)


class RunAttachAndExecTests(unittest.TestCase):
    def test_attach_without_running_process_logs_and_returns_1(self) -> None:
        buf = io.StringIO()
        with (
            patch.object(dev, "game_pids", return_value=[]),
            patch.object(dev, "kill_game") as kill_game,
            patch.object(dev, "install_mod") as install_mod,
            patch.object(dev.subprocess, "Popen") as popen,
            patch.object(dev, "ModClient") as factory,
            contextlib.redirect_stdout(buf),
        ):
            code = dev.main(["run", "--attach", "--exe", "D:/game/Minecraft.Windows.exe"])
        self.assertEqual(code, 1)
        self.assertIn("no running Minecraft.Windows.exe to attach to", buf.getvalue())
        kill_game.assert_not_called()
        install_mod.assert_not_called()
        popen.assert_not_called()
        factory.assert_not_called()

    def test_attach_already_in_world_skips_menu_and_applies_options(self) -> None:
        for extra_args in ([], ["--world", "超平坦测试"]):
            client = MagicMock()
            client.status.return_value = {"in_world": True}
            with (
                patch.object(dev, "game_pids", return_value=[123]),
                patch.object(dev, "kill_game") as kill_game,
                patch.object(dev, "install_mod") as install_mod,
                patch.object(dev, "write_auto_config") as write_cfg,
                patch.object(dev.subprocess, "Popen") as popen,
                patch.object(dev, "ModClient") as factory,
            ):
                factory.return_value.connect.return_value = client
                code = dev.main(
                    [
                        "run",
                        "--attach",
                        "--exe",
                        "D:/game/Minecraft.Windows.exe",
                        "--options",
                        "gfx_gamma=0.8",
                        *extra_args,
                    ]
                )
            self.assertEqual(code, 0)
            kill_game.assert_not_called()
            install_mod.assert_not_called()
            write_cfg.assert_not_called()
            popen.assert_not_called()
            client.wait_ready.assert_called_once_with(timeout=90.0, require_world=False)
            client.status.assert_called_once_with()
            client.worlds.assert_not_called()
            client.enter_world_ready.assert_not_called()
            client.set_options.assert_called_once_with({"gfx_gamma": "0.8"})
            client.close.assert_called_once_with()

    def test_attach_at_menu_enters_requested_world(self) -> None:
        client = MagicMock()
        client.status.return_value = {"in_world": False}
        client.worlds.return_value = {"ready": True, "worlds": [{"name": "超平坦测试"}]}
        client.signin.return_value = {"known": True}
        client.enter_world_ready.return_value = {"ok": True}
        client.wait_in_world.return_value = {
            "in_world": True,
            "feet_x": 0.0,
            "feet_y": -60.0,
            "feet_z": 0.0,
            "health": 20.0,
        }
        with (
            patch.object(dev, "game_pids", return_value=[123]),
            patch.object(dev, "kill_game") as kill_game,
            patch.object(dev, "install_mod") as install_mod,
            patch.object(dev.subprocess, "Popen") as popen,
            patch.object(dev, "wait_settled", return_value=True) as wait_settled,
            patch.object(dev, "ModClient") as factory,
        ):
            factory.return_value.connect.return_value = client
            code = dev.main(
                [
                    "run",
                    "--attach",
                    "--exe",
                    "D:/game/Minecraft.Windows.exe",
                    "--world",
                    "超平坦测试",
                ]
            )
        self.assertEqual(code, 0)
        kill_game.assert_not_called()
        install_mod.assert_not_called()
        popen.assert_not_called()
        client.enter_world_ready.assert_called_once_with("超平坦测试", attempts=3)
        client.wait_in_world.assert_called_once()
        wait_settled.assert_called_once_with(client)
        client.close.assert_called_once_with()

    def test_exec_and_stop_after_closes_pipe_first_and_propagates_exit_code(self) -> None:
        events: list[str] = []
        exe = Path("D:/game/Minecraft.Windows.exe")
        client = MagicMock()
        client.status.return_value = {"in_world": True}
        client.close.side_effect = lambda: events.append("close")

        def fake_run(cmd, shell=False):
            events.append(f"exec:{cmd}:{shell}")
            return subprocess.CompletedProcess(cmd, 7)

        def fake_kill(target_exe):
            events.append(f"stop:{target_exe}")

        with (
            patch.object(dev, "game_pids", return_value=[123]),
            patch.object(dev, "ModClient") as factory,
            patch.object(dev.subprocess, "run", side_effect=fake_run),
            patch.object(dev, "kill_game", side_effect=fake_kill),
        ):
            factory.return_value.connect.return_value = client
            code = dev.main(
                [
                    "run",
                    "--attach",
                    "--exe",
                    str(exe),
                    "--exec",
                    "py -3.11 -V",
                    "--stop-after",
                ]
            )
        self.assertEqual(code, 7)
        self.assertEqual(events, ["close", "exec:py -3.11 -V:True", f"stop:{exe}"])

    def test_stop_after_still_stops_game_when_world_entry_fails(self) -> None:
        exe = Path("D:/game/Minecraft.Windows.exe")
        client = MagicMock()
        client.status.return_value = {"in_world": False}
        with (
            patch.object(dev, "game_pids", return_value=[123]),
            patch.object(dev, "wait_for_menu", side_effect=dev.ModTimeout("menu timeout")),
            patch.object(dev, "ModClient") as factory,
            patch.object(dev, "kill_game") as kill_game,
        ):
            factory.return_value.connect.return_value = client
            code = dev.main(
                [
                    "run",
                    "--attach",
                    "--exe",
                    str(exe),
                    "--world",
                    "超平坦测试",
                    "--stop-after",
                ]
            )
        self.assertEqual(code, 2)
        client.close.assert_called_once_with()
        kill_game.assert_called_once_with(exe)


if __name__ == "__main__":
    unittest.main()
