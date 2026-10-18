"""Small wrapper around ZCode's native Windows CUA helper.

Only setup commands (``/tp``, ``/fill``) go through the game's chat UI, because
the position is server-authoritative and client-side StateVector writes are
corrected. Movement/look/sampling still use the frida RPC path.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parent / "native"
CUA_JS = NATIVE / "cua.js"
FOCUS_PS1 = NATIVE / "focus.ps1"

DEFAULT_COMMAND = r"D:\ZCode\ZCode.exe"
DEFAULT_ADDON = r"D:\ZCode\resources\tools\cua-helper\build\Release\ax_native.node"


class NativeCuaError(RuntimeError):
    pass


def _recipe() -> dict[str, Any]:
    raw = os.environ.get("ZCODE_CUA_WIN_HELPER_RECIPE")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


class NativeCua:
    def __init__(
        self,
        *,
        command: str | None = None,
        addon: str | None = None,
        timeout: float = 25.0,
    ) -> None:
        recipe = _recipe()
        self.command = command or recipe.get("command") or DEFAULT_COMMAND
        self.addon = addon or recipe.get("addonPath") or DEFAULT_ADDON
        self.timeout = timeout

    def available(self) -> tuple[bool, str]:
        if not Path(self.command).exists():
            return False, f"CUA helper command not found: {self.command}"
        if not Path(self.addon).exists():
            return False, f"native addon not found: {self.addon}"
        if not CUA_JS.exists():
            return False, f"missing {CUA_JS}"
        return True, ""

    def _run(self, *args: str) -> str:
        env = dict(os.environ)
        env["ELECTRON_RUN_AS_NODE"] = "1"
        try:
            proc = subprocess.run(
                [self.command, str(CUA_JS), self.addon, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NativeCuaError(str(exc)) from exc
        if proc.returncode != 0:
            message = (proc.stderr or proc.stdout or "").strip()
            raise NativeCuaError(message or f"CUA helper exited {proc.returncode}")
        return proc.stdout.strip()

    def info(self) -> dict[str, Any]:
        try:
            return json.loads(self._run("info"))
        except json.JSONDecodeError as exc:
            raise NativeCuaError(f"bad info output: {exc}") from exc

    def focus(self, pid: int) -> None:
        try:
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(FOCUS_PS1),
                    "-ProcessId",
                    str(pid),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NativeCuaError(f"focus failed: {exc}") from exc
        if proc.returncode != 0:
            message = (proc.stderr or proc.stdout or "").strip()
            raise NativeCuaError(message or "could not bring the game window to the foreground")

    def action(self, *args: str) -> dict[str, Any]:
        """Run one ``cua.js`` action and parse its JSON result.

        Example: ``action("hold", "ctrl+w", "2000")`` or
        ``action("point", "795", "480")``. Used by calibration runs that need
        real key input (see ``driver/probe_sprint.py``).
        """
        if not args:
            raise NativeCuaError("action needs at least an action name")
        out = self._run(*args)
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise NativeCuaError(f"bad {args[0]} output: {exc}") from exc

    def send_command(self, command: str) -> dict[str, Any]:
        """Bring the game forward and execute one command through chat."""
        command = command.strip().lstrip("/")
        if not command:
            raise NativeCuaError("empty command")
        info = self.info()
        self.focus(int(info["pid"]))
        try:
            return json.loads(self._run("chat", command))
        except json.JSONDecodeError as exc:
            raise NativeCuaError(f"bad chat output: {exc}") from exc
