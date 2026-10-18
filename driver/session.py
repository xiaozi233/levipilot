"""frida session wrapper for the MCBE harness."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Callable

import frida

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent" / "agent.js"


class HarnessError(RuntimeError):
    pass


class Session:
    """Attach to the game, load agent/agent.js, expose its RPC exports."""

    def __init__(self, target: int | str | None = None, *, agent_path: Path = AGENT) -> None:
        self.target = target if target is not None else "Minecraft.Windows.exe"
        self.agent_path = agent_path
        self.logs: list[dict[str, Any]] = []
        self._session = None
        self._script = None

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> "Session":
        try:
            self._session = frida.attach(self.target)
        except frida.ProcessNotFoundError as exc:
            raise HarnessError(
                f"找不到进程 {self.target!r}；请先启动 Minecraft 并进入世界"
            ) from exc
        except frida.PermissionDeniedError as exc:
            raise HarnessError(
                f"无法附加到 {self.target!r}：权限不足（尝试以管理员身份运行）"
            ) from exc

        source = self.agent_path.read_text(encoding="utf-8")
        self._script = self._session.create_script(source, name="mcbe-harness")
        self._script.on("message", self._on_message)
        self._script.load()

        init = self.call("init")
        if not init.get("ok"):
            raise HarnessError(f"agent init failed: {init.get('error')}")
        return self

    def close(self) -> None:
        if self._script is not None:
            try:
                self._script.unload()
            except Exception:
                pass
            self._script = None
        if self._session is not None:
            try:
                self._session.detach()
            except Exception:
                pass
            self._session = None

    def __enter__(self) -> "Session":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- RPC ---------------------------------------------------------------
    def call(self, name: str, *args: Any) -> Any:
        if self._script is None:
            raise HarnessError("session is not open")
        try:
            fn = getattr(self._script.exports_sync, name)
        except AttributeError as exc:
            raise HarnessError(f"agent has no export {name!r}") from exc
        try:
            return fn(*args)
        except Exception as exc:  # frida.InvalidOperationError etc.
            raise HarnessError(f"RPC {name} 失败: {exc}") from exc

    def status(self) -> dict[str, Any]:
        return self.call("status")

    def diagnose(self) -> dict[str, Any]:
        return self.call("diagnose")

    # -- waits -------------------------------------------------------------
    def wait_ticks(self, ticks: int, *, timeout: float | None = None) -> int:
        """Block until the game tick counter advances by `ticks`."""
        if ticks <= 0:
            return self.call("get_tick_count")
        start = self.call("get_tick_count")
        deadline = time.monotonic() + (
            timeout if timeout is not None else max(5.0, ticks / 20.0 * 6 + 5.0)
        )
        while True:
            current = self.call("get_tick_count")
            if current - start >= ticks:
                return current
            if time.monotonic() > deadline:
                raise HarnessError(
                    f"等待 {ticks} tick 超时（当前 {current - start}）；"
                    "游戏可能未聚焦/暂停，或已离开世界"
                )
            time.sleep(0.005)

    def wait_ready(self, *, timeout: float = 20.0, require_player: bool = True) -> dict[str, Any]:
        """Wait until the agent sees ticks (and optionally the local player)."""
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.status()
            ready = last.get("registry") is not None and last.get("tick", 0) > 0
            if require_player:
                ready = ready and last.get("player") is not None
            if ready:
                return last
            time.sleep(0.05)
        raise HarnessError(
            "agent 未就绪："
            f"registry={last.get('registry')} tick={last.get('tick')} "
            f"player={last.get('player')} last_error={last.get('last_error')}"
        )

    # -- messages ----------------------------------------------------------
    def _on_message(self, message: dict[str, Any], data: Any) -> None:
        if message.get("type") == "error":
            text = message.get("stack") or message.get("description") or str(message)
            print(f"[agent][error] {text}", file=sys.stderr)
            self.logs.append({"level": "error", "message": text})
            return
        if message.get("type") != "send":
            return
        payload = message.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "log":
            return
        level = payload.get("level", "info")
        text = payload.get("message", "")
        extra = payload.get("extra")
        line = f"[agent][{level}] {text}"
        if extra:
            line += f" {extra}"
        print(line, file=sys.stderr)
        self.logs.append({"level": level, "message": text, "extra": extra})


def attach(target: int | str | None = None) -> Session:
    return Session(target).open()
