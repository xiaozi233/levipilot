"""Fast development loop: build, restart the game directly, enter a world.

This replaces the LeviLauncher + menu + CUA path for day-to-day iteration:

    py -3.11 driver/dev.py run --world "超平坦测试" --build

The game is started from ``Minecraft.Windows.exe`` directly, the mod's
``enter_world`` command starts the named world, and ``wait_in_world`` blocks
until the player is actually in game. No window automation is involved.

``run`` rebuilds automatically when the built DLL is older than ``mod/probe``
sources (``--no-build`` keeps the current binary), waits until the player is
on the ground and still, and explains the version-isolated data root when the
requested world is not in the world list.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from ctypes import wintypes
from pathlib import Path

# ``python driver/dev.py`` puts driver/ on sys.path, which hides the package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver.harness import ModClient, ModPipeError, ModTimeout
from driver.harness.lab import capture_disconnect_report, world_state


def _default_version_dir() -> Path:
    override = os.environ.get("LEVIPILOT_VERSION_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    version = os.environ.get("LEVIPILOT_VERSION", "").strip() or "1.26.51.01"
    return Path(os.environ.get("APPDATA", "")) / "levilauncher.exe" / "versions" / version


DEFAULT_VERSION = _default_version_dir()
REPO_ROOT = Path(__file__).resolve().parent.parent
MOD_BUILD = REPO_ROOT / "mod" / "probe"

XMAKE_ENV = {
    "XMAKE_GLOBALDIR": r"D:\Tools\xmake-home",
    "XMAKE_PKG_INSTALLDIR": r"D:\Tools\xmake-packages",
}
XMAKE_PATH = r"D:\Tools\LLVM22\bin;D:\Tools\xmake"


def _build_xmake_env(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    for key, fallback in XMAKE_ENV.items():
        if not env.get(key) and Path(fallback).exists():
            env[key] = fallback

    candidates: list[Path] = []
    llvm_root = env.get("LEVIPILOT_LLVM_ROOT", "").strip()
    if llvm_root:
        candidates.append(Path(llvm_root) / "bin")
    xmake_dir = env.get("LEVIPILOT_XMAKE_DIR", "").strip()
    if xmake_dir:
        candidates.append(Path(xmake_dir))
    for entry in XMAKE_PATH.split(";"):
        if entry:
            candidates.append(Path(entry))

    existing_parts = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    seen = {os.path.normcase(os.path.normpath(part)) for part in existing_parts}
    prepend: list[str] = []
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        text = str(candidate)
        norm = os.path.normcase(os.path.normpath(text))
        if norm not in seen:
            seen.add(norm)
            prepend.append(text)
    if prepend:
        env["PATH"] = os.pathsep.join([*prepend, *existing_parts])
    return env


def log(message: str) -> None:
    """Print a progress line, tolerating a console that cannot encode it.

    Windows CI runs the unit tests under a cp1252 console while these messages
    are Chinese, so a bare ``print`` raises UnicodeEncodeError and fails the
    run. Fall back to UTF-8 bytes on the underlying buffer.
    """
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        buffer = getattr(getattr(sys, "stdout", None), "buffer", None)
        if buffer is None:
            raise
        buffer.write((message + "\n").encode("utf-8", "replace"))
        buffer.flush()


def game_pids(exe: Path) -> list[int]:
    """Match the full executable path so another installed version is untouched."""
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
            "Get-CimInstance Win32_Process -Filter \"Name='Minecraft.Windows.exe'\" | "
            "Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=15,
    )
    rows = json.loads(result.stdout) if result.stdout.strip() else []
    if isinstance(rows, dict):
        rows = [rows]
    pids = []
    for row in rows:
        path = row.get("ExecutablePath")
        if not path:
            raise SystemExit("无法读取游戏进程路径；请以相同用户运行游戏与开发工具")
        if Path(path).resolve() == exe.resolve():
            pids.append(int(row["ProcessId"]))
    return pids


def kill_game(exe: Path | None = None, *, force: bool = False) -> None:
    exe = exe or DEFAULT_VERSION / "Minecraft.Windows.exe"
    pids = game_pids(exe)
    if not pids:
        return
    if not force:
        if len(pids) != 1:
            raise SystemExit("目标 exe 有多个实例，无法确定保存退出的会话；请先关闭多余实例")
        try:
            with ModClient(connect_timeout=3, timeout=30) as client:
                status = client.status()
                spool = Path(str(status.get("spool", "")))
                if spool.name != f"spool-{pids[0]}.jsonl":
                    raise SystemExit("管道属于其他游戏实例，已保留目标进程")
                if status.get("in_world"):
                    log(f"saving and leaving world (pid={pids[0]})")
                    # Remove replayed exit events before starting this exit.
                    while True:
                        try:
                            client.wait_event("world_left", 0)
                        except ModTimeout:
                            break
                    if not client.leave_world().get("ok"):
                        raise SystemExit("保存退出请求失败，已保留游戏进程")
                    client.wait_event("world_left", 45)
                deadline = time.monotonic() + 45
                while True:
                    worlds = client.worlds()
                    ready = worlds.get("ready")
                    if ready is None:  # Compatibility with the previously installed mod.
                        screen = str(client.screen().get("name", ""))
                        ready = (
                            "progress_screen" not in screen
                            and ("start_screen" in screen or screen.startswith("ui://"))
                            and client.signin().get("known")
                        )
                    if ready:
                        break
                    if time.monotonic() >= deadline:
                        # The disconnect error page is HBUI too but never
                        # reports a ready world list; with no level loaded there
                        # is nothing left to save, so stop instead of refusing.
                        if world_state(client) == "disconnect_error":
                            log("disconnect error page: no level loaded, skipping the save gate")
                            break
                        raise ModTimeout("等待世界保存退出完成超时")
                    time.sleep(0.1)
        except ModPipeError as exc:
            raise SystemExit(
                f"未能确认保存退出，游戏已保留：{exc}；故障恢复可显式加 --force-stop"
            ) from exc
    # Recheck the executable identity immediately before stopping the selected PID.
    remaining = set(game_pids(exe))
    for pid in pids:
        if pid in remaining:
            log(f"stopping target game pid={pid}" + (" (forced)" if force else " after save"))
            result = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False
            )
            if result.returncode and pid in game_pids(exe):
                raise SystemExit(f"无法停止目标游戏进程 {pid}")


def _appactivate(pid: int) -> bool:
    """Ask the shell to activate the process window; Win32 foreground calls fail silently."""
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"$w=New-Object -ComObject WScript.Shell; if(-not $w.AppActivate({pid})){{exit 1}}",
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _wait_foreground(user32, hwnd: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if int(user32.GetForegroundWindow() or 0) == int(hwnd):
            return True
        time.sleep(0.1)
    return False


def _force_foreground(user32, hwnd: int, kernel32=None) -> None:
    """SetForegroundWindow from the foreground thread's input queue.

    Plain ``SetForegroundWindow`` from a background process is refused by the
    foreground lock (and still returns a value that looks like success), so
    attach to the current foreground thread first, which is the documented
    workaround.
    """
    kernel32 = kernel32 or ctypes.WinDLL("kernel32")
    foreground = user32.GetForegroundWindow()
    foreground_thread = 0
    if foreground:
        foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
    current = kernel32.GetCurrentThreadId()
    attached = False
    if foreground_thread and foreground_thread != current:
        attached = bool(user32.AttachThreadInput(current, foreground_thread, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(current, foreground_thread, False)


def _focus_with_fallback(user32, hwnd: int, pid: int, kernel32=None) -> bool:
    """Foreground the window and report whether it *actually* became foreground."""
    _force_foreground(user32, hwnd, kernel32)
    if _wait_foreground(user32, hwnd, 0.6):
        return True
    # The shell's AppActivate path is the last resort when the input-queue
    # attach is refused as well.
    if not _appactivate(pid):
        return False
    return _wait_foreground(user32, hwnd, 1.5)


def focus_game(pid: int, timeout: float = 15.0) -> bool:
    """Bring the game window to the foreground and verify it took effect.

    A background-launched Minecraft keeps raw mouse capture; entering a world
    while it is unfocused is what makes the cursor feel stuck, and the paused
    world stops block ticks (fluids and falling sand look frozen while commands
    and block reads still work). Only a verified foreground window counts: the
    plain ``SetForegroundWindow`` call returns success even when Windows ignores
    it.
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found: list[int] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False
        return True

    callback_ref = enum_proc(callback)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found.clear()
        user32.EnumWindows(callback_ref, 0)
        if found:
            user32.ShowWindow(found[0], 9)  # SW_RESTORE
            if _focus_with_fallback(user32, found[0], pid):
                return True
        time.sleep(0.3)
    return False


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", wintypes.LPBYTE),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


def current_desktop_name() -> str | None:
    """Return the startup desktop name (``lpDesktop``) for this process, or ``None``."""
    windll = getattr(ctypes, "windll", None)
    kernel32 = getattr(windll, "kernel32", None)
    get_startup_info = getattr(kernel32, "GetStartupInfoW", None)
    if get_startup_info is None:
        return None
    try:
        info = _STARTUPINFOW()
        info.cb = ctypes.sizeof(_STARTUPINFOW)
        get_startup_info(ctypes.byref(info))
        desktop = info.lpDesktop
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if not desktop:
        return None
    name = str(desktop).strip()
    return name or None


def _is_isolated_desktop(desktop: str | None) -> bool:
    if not desktop:
        return False
    leaf = desktop.rsplit("\\", 1)[-1].strip()
    return bool(leaf) and leaf.lower() != "default"


def _log_isolated_desktop_hint() -> None:
    desktop = current_desktop_name()
    if not _is_isolated_desktop(desktop):
        return
    log(
        f"hint: current shell is running in an isolated desktop ({desktop}), "
        "where launched windows cannot appear on the physical screen or take foreground focus; "
        "start the game on the user desktop and run: py -3.11 driver/dev.py run --attach"
    )


def build_mod() -> None:
    env = _build_xmake_env()
    log(f"building {MOD_BUILD} ...")
    result = subprocess.run(["xmake", "-j4", "-y"], cwd=MOD_BUILD, env=env)
    if result.returncode != 0:
        raise SystemExit(f"xmake failed with exit code {result.returncode}")


def probe_is_stale() -> tuple[bool, float, float]:
    """Return (stale, newest_source_mtime, dll_mtime).

    ``dev.py run`` installs the *built* DLL, so a source edit without a rebuild
    silently ships the previous binary.  A plain ``in_world``/selftest run then
    fails in confusing ways (missing fields, stale input path).
    """
    dll = MOD_BUILD / "bin" / "probe" / "probe.dll"
    if not dll.exists():
        return (True, 0.0, 0.0)
    newest = dll.stat().st_mtime
    candidates = [MOD_BUILD / "xmake.lua", MOD_BUILD / "src"]
    for candidate in candidates:
        if candidate.is_file():
            newest = max(newest, candidate.stat().st_mtime)
        elif candidate.is_dir():
            for path in candidate.rglob("*"):
                if path.is_file():
                    newest = max(newest, path.stat().st_mtime)
    dll_mtime = dll.stat().st_mtime
    return (newest > dll_mtime + 1.0, newest, dll_mtime)


def find_isolated_worlds(version_dir: Path, name: str) -> list[Path]:
    """Worlds with ``name`` under the version-isolated data root."""
    root = version_dir / "Minecraft Bedrock" / "Users"
    found: list[Path] = []
    if not root.is_dir():
        return found
    for levelname in root.glob("*/games/com.mojang/minecraftWorlds/*/levelname.txt"):
        try:
            if levelname.read_text(encoding="utf-8", errors="replace").strip() == name:
                found.append(levelname.parent)
        except OSError:
            continue
    return found


def wait_settled(client: ModClient, timeout: float = 15.0) -> bool:
    """Wait until the player is in a world, on the ground and still.

    The first selftest run right after entering a cold world used to fail a
    cluster of teleport/scan/jump checks because the baseline feet were taken
    while the player was still falling.  Dev runs now return only once the
    player is playable.
    """
    deadline = time.monotonic() + timeout
    stable = 0
    last_tick: int | None = None
    while time.monotonic() < deadline:
        try:
            state = client.state()
        except (ModPipeError, ModTimeout):
            time.sleep(0.1)
            continue
        if not state.get("in_world"):
            time.sleep(0.2)
            continue
        tick = int(state.get("tick", -1))
        if tick == last_tick:
            time.sleep(0.02)
            continue
        last_tick = tick
        still = (
            bool(state.get("on_ground"))
            and abs(float(state.get("vx", 0.0))) < 1e-3
            and abs(float(state.get("vz", 0.0))) < 1e-3
            and abs(float(state.get("vy", 0.0))) < 1e-3
        )
        stable = stable + 1 if still else 0
        if stable >= 5:
            return True
        time.sleep(0.05)
    return False


def install_mod(version_dir: Path) -> None:
    src = MOD_BUILD / "bin" / "probe"
    dst = version_dir / "mods" / "probe"
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("probe.dll", "probe.pdb", "manifest.json"):
        shutil.copy2(src / name, dst / name)
    log(f"installed mod -> {dst}")


def write_auto_config(
    mod_dir: Path,
    world: str | None,
    options: dict[str, str],
    allow: dict[str, object] | None = None,
) -> None:
    """Optional hands-free config for a bare exe launch.

    ``options`` land under the config's ``options`` object (game options);
    ``allow`` keys are written at the top level where the mod reads its
    capability switches (``allow_input_inject``, ``allow_selftest_stress`` …).

    The file is *merged*, not replaced: a previous experiment's
    ``allow_hook_va`` or a hand-written ``events`` switch must not vanish just
    because this run only passed ``--allow allow_input_inject=1``. The first
    version of the file is kept as ``harness.json.prev`` so
    ``dev.py restore-config`` can put it back; ``auto_enter``/``options`` are
    owned by this function and are dropped/updated explicitly (a stale
    auto_enter races with the explicit ``enter_world`` below).
    """
    mod_dir.mkdir(parents=True, exist_ok=True)
    path = mod_dir / "harness.json"
    allow = allow or {}

    previous: dict[str, object] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous = loaded
        except (OSError, ValueError):
            log(f"warning: {path.name} 不是合法 JSON，按空配置处理")
        backup = mod_dir / "harness.json.prev"
        if not backup.exists():
            shutil.copy2(path, backup)

    if not world and not options and not allow and not {"auto_enter", "options"} & previous.keys():
        return

    config: dict[str, object] = {
        key: value for key, value in previous.items() if key not in {"auto_enter", "options"}
    }
    config.setdefault("delay_ms", 2000)
    if world:
        config["auto_enter"] = world
    if options:
        existing_options = previous.get("options")
        merged_options: dict[str, object] = (
            dict(existing_options) if isinstance(existing_options, dict) else {}
        )
        merged_options.update(options)
        config["options"] = merged_options
    config.update(allow)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"harness.json: {path}")


def restore_config(mod_dir: Path) -> bool:
    """Put back the config that existed before this tool first wrote one."""
    path = mod_dir / "harness.json"
    backup = mod_dir / "harness.json.prev"
    if not backup.exists():
        log(f"没有备份可恢复：{backup}")
        return False
    shutil.copy2(backup, path)
    backup.unlink()
    log(f"harness.json 已恢复：{path}")
    return True


def wait_for_menu(client: ModClient, timeout: float = 90.0) -> list[dict]:
    """Wait for the mod's safe-menu gate; cached world names are insufficient."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            reply = client.worlds()
            if reply.get("ready") is True:
                return list(reply.get("worlds", []))
        except (ModPipeError, ModTimeout):
            pass
        time.sleep(1.0)
    raise ModTimeout("等待主菜单/世界列表超时")


def _parse_kv_options(items: list[str]) -> dict[str, str]:
    options: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            log(f"--options 需要 KEY=VALUE 形式，已忽略 {item!r}")
            continue
        options[key] = value
    return options


def _parse_kv_allow(items: list[str], *, lock_input: bool) -> dict[str, object]:
    allow: dict[str, object] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            log(f"--allow 需要 KEY=VALUE 形式，已忽略 {item!r}")
            continue
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            allow[key] = True
        elif lowered in ("0", "false", "no", "off"):
            allow[key] = False
        else:
            allow[key] = value
    if lock_input:
        allow["lock_input"] = True
    return allow


def _prepare_and_enter_world(
    client: ModClient,
    *,
    args: argparse.Namespace,
    version_dir: Path,
    options: dict[str, str],
    pid: int,
    attach: bool,
) -> None:
    log("pipe ready; waiting for menu")
    worlds = wait_for_menu(client)
    log("worlds: " + ", ".join(w.get("name", "?") for w in worlds))

    if options:
        result = client.set_options(options)
        log(f"options: {result}")

    if not args.world:
        return

    names = {w.get("name") for w in worlds}
    if args.world not in names:
        hint = ""
        isolated = find_isolated_worlds(version_dir, args.world)
        if isolated:
            hint = (
                "\n提示：这个版本开着数据隔离，世界在 "
                f"{isolated[0].parents[5]}；直接启动 exe 用的是全局数据根。"
                " 用 LeviLauncher 启动该实例，或把世界目录复制到 "
                "%APPDATA%\\Minecraft Bedrock\\Users\\<id>\\games\\com.mojang\\minecraftWorlds。"
            )
        raise SystemExit(
            f"找不到世界 {args.world!r}；可用：{', '.join(sorted(n for n in names if n))}{hint}"
        )
    if not attach and not args.no_focus and not focus_game(pid, timeout=3.0):
        log("warning: game window is not foreground; block ticks may be paused")
        _log_isolated_desktop_hint()
    try:
        log(f"signin: {client.signin()}")
    except (ModPipeError, ModTimeout) as exc:
        log(f"signin query unavailable: {exc}")
    log(f"entering {args.world!r}")
    settled = client.enter_world_ready(args.world, attempts=args.attempts)
    if not settled.get("ok"):
        raise SystemExit(f"进入世界失败（可能在等 Microsoft 登录）：{settled}")
    state = client.wait_in_world(timeout=args.timeout)
    if all(key in state for key in ("feet_x", "feet_y", "feet_z", "health")):
        log(
            "in world: feet=({feet_x:.2f}, {feet_y:.2f}, {feet_z:.2f}) health={health}".format(
                **state
            )
        )
    else:
        log("in world")
    if wait_settled(client):
        log("player settled (on ground, still)")
    else:
        log("warning: player did not settle before the timeout; selftest may be flaky")


def run(args: argparse.Namespace) -> int:
    version_dir = Path(args.version_dir).expanduser()
    options = _parse_kv_options(args.options)
    allow = _parse_kv_allow(args.allow, lock_input=args.lock_input)
    if args.write_config:
        write_auto_config(version_dir / "mods" / "probe", args.world, options, allow)
        log("wrote harness.json; the mod will auto-enter on launch")
        return 0

    attach = bool(getattr(args, "attach", False))
    exec_cmd = getattr(args, "exec", None)
    stop_after = bool(getattr(args, "stop_after", False))

    exe = Path(args.exe).expanduser() if args.exe else version_dir / "Minecraft.Windows.exe"
    pid: int
    if attach:
        pids = game_pids(exe)
        if not pids:
            log(f"no running {exe.name} to attach to")
            return 1
        pid = pids[0]
        log(f"attaching to {exe.name} (pid={pid})")
    else:
        if not exe.exists():
            raise SystemExit(f"找不到游戏 exe：{exe}")
        stale, newest, dll_mtime = probe_is_stale()
        if args.build:
            build_mod()
        elif stale and not args.no_build:
            log(
                "probe.dll is older than the mod sources "
                f"({time.strftime('%H:%M:%S', time.localtime(dll_mtime))} < "
                f"{time.strftime('%H:%M:%S', time.localtime(newest))}); rebuilding"
            )
            build_mod()
        elif stale:
            log(
                "warning: probe.dll is older than the mod sources; run without --no-build to rebuild"
            )
        kill_game(exe, force=args.force_stop)
        time.sleep(1.0)
        install_mod(version_dir)
        # A stale auto_enter would race with the explicit enter_world below; the
        # capability switches must still be present when the mod loads its config.
        write_auto_config(version_dir / "mods" / "probe", None, {}, allow)

        log(f"starting {exe}")
        process = subprocess.Popen([str(exe)], cwd=str(exe.parent))
        pid = process.pid
        if not args.no_focus:
            # The window can take tens of seconds to appear behind the splash; the
            # wait ends as soon as a visible window shows up.
            if focus_game(pid, timeout=60.0):
                log("game focused")
            else:
                log(
                    "warning: could not focus the game window; a paused world stops block ticks "
                    "(water/lava/sand look frozen while commands still work)"
                )
                _log_isolated_desktop_hint()

    exit_code = 0
    try:
        client = ModClient(connect_timeout=90.0, timeout=20.0).connect()
        try:
            client.wait_ready(timeout=90.0, require_world=False)
            already_in_world = False
            if attach:
                status = client.status()
                already_in_world = bool(status.get("in_world"))

            if already_in_world:
                log("already in world; skipping menu/world entry")
                if options:
                    result = client.set_options(options)
                    log(f"options: {result}")
            else:
                _prepare_and_enter_world(
                    client,
                    args=args,
                    version_dir=version_dir,
                    options=options,
                    pid=pid,
                    attach=attach,
                )
        finally:
            client.close()

        if exec_cmd:
            log(f"exec: {exec_cmd}")
            exec_result = subprocess.run(exec_cmd, shell=True)
            exit_code = int(exec_result.returncode)
    finally:
        if stop_after:
            kill_game(exe)
    return exit_code


def recover(args: argparse.Namespace) -> int:
    """Record a stuck session, then restart; force-stop requires --force."""
    version_dir = Path(args.version_dir).expanduser()
    exe = Path(args.exe).expanduser() if args.exe else version_dir / "Minecraft.Windows.exe"
    kind = "exiting"
    if game_pids(exe):
        try:
            with ModClient(connect_timeout=5, timeout=20) as client:
                kind = world_state(client)
                log(f"state: {kind}")
                if kind == "world" and not args.force:
                    print(
                        "error: 仍在世界里（state=world）；先正常离开世界，或加 --force",
                        file=sys.stderr,
                    )
                    return 2
                if kind == "menu" and not args.force:
                    log("menu: 已经是可用菜单，无需恢复")
                    return 0
                report = capture_disconnect_report(client, REPO_ROOT / "out", tag="disconnect")
                log(f"recorded: {report}")
        except (ModPipeError, ModTimeout, OSError) as exc:
            log(f"pipe unavailable ({exc}); no live state to record")
    if game_pids(exe):
        if args.force or kind == "disconnect_error":
            # A confirmed error page has no loaded level: the save gate below can
            # only spin for 45 s and then refuse. Leaving it is the whole point
            # of `recover`, so this is the one case that may stop itself.
            if kind == "disconnect_error":
                log("disconnect error page: no level loaded, stopping without the save gate")
            try:
                kill_game(exe, force=True)
            except SystemExit as exc:
                log(f"stop failed ({exc}); process preserved")
                return 2
        else:
            try:
                kill_game(exe)
            except SystemExit as exc:
                log(f"graceful stop refused ({exc}); process preserved, use --force to abandon it")
                return 2
        time.sleep(1.0)
    argv = [sys.executable, str(Path(__file__).resolve()), "run", "--version-dir", str(version_dir)]
    if args.exe:
        argv += ["--exe", str(exe)]
    argv.append("--build" if args.build else "--no-build")
    if args.world:
        argv += ["--world", args.world]
    if args.no_focus:
        argv += ["--no-focus"]
    return subprocess.call(argv)


def focus(args: argparse.Namespace) -> int:
    """Focus the already-running game so block ticks (fluids, sand) keep running."""
    exe = (
        Path(args.exe).expanduser()
        if args.exe
        else Path(args.version_dir).expanduser() / "Minecraft.Windows.exe"
    )
    pids = game_pids(exe)
    if not pids:
        log(f"no running {exe.name}")
        return 1
    if focus_game(pids[0], timeout=args.timeout):
        log(f"game focused (pid={pids[0]})")
        return 0
    log("could not focus the game window; the world may stay paused (no block ticks)")
    _log_isolated_desktop_hint()
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="build (optional), restart, enter a world")
    run_parser.add_argument("--world", help="world name to enter")
    run_parser.add_argument("--build", action="store_true", help="run xmake before installing")
    run_parser.add_argument(
        "--no-build",
        action="store_true",
        help="never rebuild, even if the built DLL is older than the mod sources",
    )
    run_parser.add_argument("--options", action="append", default=[], metavar="KEY=VALUE")
    run_parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="top-level harness.json switch, e.g. allow_input_inject=1 (repeatable)",
    )
    run_parser.add_argument(
        "--lock-input",
        action="store_true",
        help="harness.json lock_input=true: ignore real hardware keys/mouse at startup",
    )
    run_parser.add_argument("--version-dir", default=str(DEFAULT_VERSION))
    run_parser.add_argument("--exe", help="override the game executable path")
    run_parser.add_argument(
        "--force-stop",
        action="store_true",
        help="stop the target PID without saving (recovery only)",
    )
    run_parser.add_argument("--timeout", type=float, default=90.0)
    run_parser.add_argument(
        "--attempts",
        type=int,
        default=3,
        help="enter_world retries while Microsoft sign-in settles",
    )
    run_parser.add_argument(
        "--no-focus",
        action="store_true",
        help="do not bring the game window to the foreground before entering",
    )
    run_parser.add_argument(
        "--write-config",
        action="store_true",
        help="only write harness.json (the mod auto-enters on launch)",
    )
    run_parser.add_argument(
        "--attach",
        action="store_true",
        help="attach to an already-running game instead of rebuilding/restarting",
    )
    run_parser.add_argument(
        "--exec",
        help="shell command to execute once the world is ready",
    )
    run_parser.add_argument(
        "--stop-after",
        action="store_true",
        help="save and stop the game after --exec or world entry finishes",
    )

    stop_parser = sub.add_parser("stop", help="save, leave and stop the selected game executable")
    stop_parser.add_argument("--version-dir", default=str(DEFAULT_VERSION))
    stop_parser.add_argument("--exe")
    stop_parser.add_argument("--force-stop", action="store_true")
    focus_parser = sub.add_parser(
        "focus", help="bring the running game window to the foreground (keeps block ticks alive)"
    )
    focus_parser.add_argument("--version-dir", default=str(DEFAULT_VERSION))
    focus_parser.add_argument("--exe")
    focus_parser.add_argument("--timeout", type=float, default=15.0)
    worlds_parser = sub.add_parser("worlds", help="list cached worlds")
    worlds_parser.add_argument("--version-dir", default=str(DEFAULT_VERSION))
    restore_parser = sub.add_parser(
        "restore-config",
        help="restore harness.json to the state before dev.py first rewrote it",
    )
    restore_parser.add_argument("--version-dir", default=str(DEFAULT_VERSION))

    recover_parser = sub.add_parser(
        "recover",
        help="record a dead session (disconnect error page), restart to the menu, re-enter",
    )
    recover_parser.add_argument("--version-dir", default=str(DEFAULT_VERSION))
    recover_parser.add_argument("--exe")
    recover_parser.add_argument("--world", help="world to enter after the restart")
    recover_parser.add_argument(
        "--force",
        action="store_true",
        help="explicitly allow force-stop when saving cannot be confirmed (may lose unsaved changes)",
    )
    recover_parser.add_argument("--build", action="store_true", help="run xmake after the restart")
    recover_parser.add_argument("--no-focus", action="store_true")

    args = parser.parse_args(argv)

    try:
        if args.command == "stop":
            exe = (
                Path(args.exe).expanduser()
                if args.exe
                else Path(args.version_dir).expanduser() / "Minecraft.Windows.exe"
            )
            kill_game(exe, force=args.force_stop)
            log("stopped")
            return 0
        if args.command == "worlds":
            with ModClient(timeout=20.0) as client:
                for world in client.worlds().get("worlds", []):
                    print(f"{world.get('name')}\t{world.get('id')}\t{world.get('last_saved')}")
            return 0
        if args.command == "restore-config":
            ok = restore_config(Path(args.version_dir).expanduser() / "mods" / "probe")
            return 0 if ok else 1
        if args.command == "focus":
            return focus(args)
        if args.command == "recover":
            return recover(args)
        return run(args)
    except (ModPipeError, ModTimeout, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
