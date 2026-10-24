"""Connection lifecycle and the generic server-command channel."""

from __future__ import annotations

import time
from typing import Any, cast

from ..connection import PipeConnection
from ..errors import ModPipeError, ModTimeout
from ..protocol import StateEvent


class SessionCommands(PipeConnection):
    # wait_ready needs a long default: it polls through game boot.
    def wait_ready(self, timeout: float = 60.0, require_world: bool = True) -> StateEvent:
        """Reconnect until the mod answers and (optionally) a world is loaded."""
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.ensure_connected()
                self.ping()
                state = self.state()
                if state.get("in_world") or not require_world:
                    return state
            except ModPipeError as exc:
                last = exc
                self.close()
            time.sleep(0.5)
        raise ModTimeout(f"等待游戏就绪超时（{timeout:.0f}s）：{last}")

    def ping(self) -> dict[str, Any]:
        return self.request({"cmd": "ping"}, "pong")

    def status(self) -> dict[str, Any]:
        return self.request({"cmd": "status"}, "status")

    def state(self) -> StateEvent:
        return cast(StateEvent, self.request({"cmd": "state"}, "state"))

    def wait_in_world(
        self, timeout: float = 90.0, poll: float = 0.5, settle: float = 1.5
    ) -> StateEvent:
        """Wait until ``state.in_world`` is true, then let the join settle."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.state()
            if state.get("in_world"):
                if settle > 0:
                    time.sleep(settle)
                    return self.state()
                return state
            time.sleep(poll)
        raise ModTimeout(f"等待进入世界超时（{timeout:.0f}s）")

    def wait_ticks(self, ticks: int, *, timeout: float | None = None, poll: float = 0.02) -> int:
        """Wait until the game tick advanced by ``ticks``; returns the delta.

        The default budget is 3x realtime plus a 2 s margin for server hitches;
        raises :class:`ModTimeout` when it runs out.
        """
        if ticks <= 0:
            return 0
        start = int(self.status().get("tick", 0))
        if timeout is None:
            timeout = max(3.0, ticks / 20.0 * 3.0 + 2.0)
        deadline = time.monotonic() + timeout
        current = start
        while current - start < ticks:
            if time.monotonic() > deadline:
                raise ModTimeout(f"wait_ticks({ticks}) 超时：tick 只前进了 {current - start}")
            time.sleep(poll)
            current = int(self.status().get("tick", 0))
        return current - start

    def command(self, command: str, player: str | None = None) -> dict[str, Any]:
        """Run a server command (``/fill``, ``/tp`` ...) on the integrated server."""
        message: dict[str, Any] = {"cmd": "command", "command": command}
        if player:
            message["player"] = player
        return self.request(message, "command_done")
