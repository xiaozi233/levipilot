"""Fast development loop: build, restart the game directly, enter a world.

This replaces the LeviLauncher + menu + CUA path for day-to-day iteration:

    py -3.11 driver/dev.py run --world "超平坦测试" --build

The game is started from ``Minecraft.Windows.exe`` directly, the mod's
``enter_world`` command starts the named world, and ``wait_in_world`` blocks
until the player is actually in game. No window automation is involved.
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
from ctypes import wintypes
from pathlib import Path

# ``python driver/dev.py`` puts driver/ on sys.path, which hides the package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver.harness import ModClient, ModPipeError, ModTimeout

DEFAULT_VERSION = (
    Path(os.environ.get("APPDATA", "")) / "levilauncher.exe" / "versions" / "1.26.51.01"
)
REPO_ROOT = Path(__file__).resolve().parent.parent
MOD_BUILD = REPO_ROOT / "mod" / "probe"

XMAKE_ENV = {
    "XMAKE_GLOBALDIR": r"D:\Tools\xmake-home",
    "XMAKE_PKG_INSTALLDIR": r"D:\Tools\xmake-packages",
}
XMAKE_PATH = r"D:\Tools\LLVM22\bin;D:\Tools\xmake"


def log(message: str) -> None:
    print(message, flush=True)


def kill_game() -> None:
    subprocess.run(
        ["taskkill", "/F", "/IM", "Minecraft.Windows.exe"],
        capture_output=True,
        check=False,
    )


def focus_game(pid: int, timeout: float = 15.0) -> bool:
    """Bring the game window to the foreground.

    A background-launched Minecraft keeps raw mouse capture; entering a world
    while it is unfocused is what makes the cursor feel stuck. Focusing the
    window before entering avoids that state.
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
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
            hwnd = found[0]
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            return True
        time.sleep(0.3)
    return False


def build_mod() -> None:
    env = os.environ.copy()
    env["PATH"] = XMAKE_PATH + ";" + env.get("PATH", "")
    env.update(XMAKE_ENV)
    log(f"building {MOD_BUILD} ...")
    result = subprocess.run(["xmake", "-y"], cwd=MOD_BUILD, env=env)
    if result.returncode != 0:
        raise SystemExit(f"xmake failed with exit code {result.returncode}")


def install_mod(version_dir: Path) -> None:
    src = MOD_BUILD / "bin" / "probe"
    dst = version_dir / "mods" / "probe"
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("probe.dll", "probe.pdb", "manifest.json"):
        shutil.copy2(src / name, dst / name)
    log(f"installed mod -> {dst}")


def write_auto_config(mod_dir: Path, world: str | None, options: dict[str, str]) -> None:
    """Optional hands-free config for a bare exe launch."""
    mod_dir.mkdir(parents=True, exist_ok=True)
    path = mod_dir / "harness.json"
    if not world and not options:
        if path.exists():
            path.unlink()
        return
    config: dict[str, object] = {"delay_ms": 2000}
    if world:
        config["auto_enter"] = world
    if options:
        config["options"] = options
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def wait_for_menu(client: ModClient, timeout: float = 90.0) -> list[dict]:
    """Wait until the world list is readable (main menu is up)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            worlds = client.worlds().get("worlds", [])
            if worlds:
                return list(worlds)
        except (ModPipeError, ModTimeout):
            pass
        time.sleep(1.0)
    raise ModTimeout("等待主菜单/世界列表超时")


def run(args: argparse.Namespace) -> int:
    version_dir = Path(args.version_dir).expanduser()
    exe = Path(args.exe).expanduser() if args.exe else version_dir / "Minecraft.Windows.exe"
    if not exe.exists():
        raise SystemExit(f"找不到游戏 exe：{exe}")

    if args.build:
        build_mod()
    kill_game()
    time.sleep(1.0)
    install_mod(version_dir)

    options = dict(item.split("=", 1) for item in args.options)
    if args.write_config:
        write_auto_config(version_dir / "mods" / "probe", args.world, options)
        log("wrote harness.json; the mod will auto-enter on launch")
        return 0
    # A stale auto_enter would race with the explicit enter_world below.
    write_auto_config(version_dir / "mods" / "probe", None, {})

    log(f"starting {exe}")
    process = subprocess.Popen([str(exe)], cwd=str(exe.parent))
    if not args.no_focus:
        if focus_game(process.pid):
            log("game focused")
        else:
            log("warning: could not focus the game window")

    client = ModClient(connect_timeout=90.0, timeout=20.0).connect()
    try:
        client.wait_ready(timeout=90.0, require_world=False)
        log("pipe ready; waiting for menu")
        worlds = wait_for_menu(client)
        log("worlds: " + ", ".join(w.get("name", "?") for w in worlds))

        if options:
            result = client.set_options(options)
            log(f"options: {result}")

        if args.world:
            names = {w.get("name") for w in worlds}
            if args.world not in names:
                raise SystemExit(
                    f"找不到世界 {args.world!r}；可用：{', '.join(sorted(n for n in names if n))}"
                )
            if not args.no_focus:
                focus_game(process.pid, timeout=3.0)
            try:
                log(f"signin: {client.signin()}")
            except (ModPipeError, ModTimeout) as exc:
                log(f"signin query unavailable: {exc}")
            log(f"entering {args.world!r}")
            settled = client.enter_world_ready(args.world, attempts=args.attempts)
            if not settled.get("ok"):
                raise SystemExit(f"进入世界失败（可能在等 Microsoft 登录）：{settled}")
            state = client.wait_in_world(timeout=args.timeout)
            log(
                "in world: feet=({feet_x:.2f}, {feet_y:.2f}, {feet_z:.2f}) health={health}".format(
                    **state
                )
            )
        return 0
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="build (optional), restart, enter a world")
    run_parser.add_argument("--world", help="world name to enter")
    run_parser.add_argument("--build", action="store_true", help="run xmake before installing")
    run_parser.add_argument("--options", action="append", default=[], metavar="KEY=VALUE")
    run_parser.add_argument("--version-dir", default=str(DEFAULT_VERSION))
    run_parser.add_argument("--exe", help="override the game executable path")
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

    sub.add_parser("stop", help="kill Minecraft.Windows.exe")
    worlds_parser = sub.add_parser("worlds", help="list cached worlds")
    worlds_parser.add_argument("--version-dir", default=str(DEFAULT_VERSION))

    args = parser.parse_args(argv)

    try:
        if args.command == "stop":
            kill_game()
            log("stopped")
            return 0
        if args.command == "worlds":
            with ModClient(timeout=20.0) as client:
                for world in client.worlds().get("worlds", []):
                    print(f"{world.get('name')}\t{world.get('id')}\t{world.get('last_saved')}")
            return 0
        return run(args)
    except (ModPipeError, ModTimeout) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
