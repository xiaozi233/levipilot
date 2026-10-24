"""Movement, look and event-level input injection (G1/G2)."""

from __future__ import annotations

from typing import Any

from ..connection import PipeConnection


class InputCommands(PipeConnection):
    def move(self, forward: float = 0.0, strafe: float = 0.0, ticks: int = 1) -> dict[str, Any]:
        return self.request(
            {
                "cmd": "move",
                "forward": float(forward),
                "strafe": float(strafe),
                "ticks": int(ticks),
            },
            "moving",
        )

    def stop_move(self) -> dict[str, Any]:
        return self.request({"cmd": "stopmove"}, "stopping")

    def set_input(
        self,
        forward: float = 0.0,
        strafe: float = 0.0,
        *,
        sprint: bool = False,
        sneak: bool = False,
        jump: bool = False,
        rmb: bool = False,
        lmb: bool = False,
        ticks: int = 1,
    ) -> dict[str, Any]:
        return self.request(
            {
                "cmd": "set",
                "forward": float(forward),
                "strafe": float(strafe),
                "sprint": bool(sprint),
                "sneak": bool(sneak),
                "jump": bool(jump),
                "rmb": bool(rmb),
                "lmb": bool(lmb),
                "ticks": int(ticks),
            },
            "set_ok",
        )

    def jump(self) -> dict[str, Any]:
        return self.request({"cmd": "jump"}, "jump_ok")

    def look(
        self,
        yaw: float,
        pitch: float = 0.0,
        *,
        relative: bool = False,
        ticks: int = 1,
    ) -> dict[str, Any]:
        return self.request(
            {
                "cmd": "look",
                "yaw": float(yaw),
                "pitch": float(pitch),
                "relative": bool(relative),
                "ticks": int(ticks),
            },
            "look_ok",
        )

    def click(self, button: str = "left", reach: float = 5.5) -> dict[str, Any]:
        return self.request(
            {"cmd": "click", "button": button, "reach": float(reach)}, "click_result"
        )

    def fly(self, enabled: bool = True) -> dict[str, Any]:
        return self.request({"cmd": "fly", "enabled": bool(enabled)}, "fly_ok")

    def mouse(self) -> dict[str, Any]:
        """Mouse input diagnostics (event counts per MouseAction id)."""
        return self.request({"cmd": "mouse"}, "mouse")

    def mouse_clear(self) -> dict[str, Any]:
        return self.request({"cmd": "mouse_clear"}, "mouse_cleared")

    def mouse_event(
        self,
        action: int | str,
        *,
        data: int | str | None = None,
        down: bool | None = None,
        dx: int = 0,
        dy: int = 0,
        x: int | None = None,
        y: int | None = None,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "cmd": "mouse_event",
            "action": action,
            "dx": int(dx),
            "dy": int(dy),
        }
        if x is not None:
            message["x"] = int(x)
        if y is not None:
            message["y"] = int(y)
        if data is not None:
            message["data"] = data
        if down is not None:
            message["down"] = bool(down)
        return self.request(message, "mouse_event_queued")

    # -- G1/G2: event-level input injection ----------------------------------
    def key_event(
        self,
        code: int,
        down: bool = True,
        *,
        repeat: bool = False,
        repeat_ms: int = 0,
        via: str = "hid",
    ) -> dict[str, Any]:
        return self.request(
            {
                "cmd": "key_event",
                "code": int(code),
                "down": bool(down),
                "repeat": bool(repeat),
                "repeat_ms": int(repeat_ms),
                "via": via,
            },
            "key_event_queued",
        )

    def key_state(self, code: int) -> dict[str, Any]:
        return self.request({"cmd": "key_state", "code": int(code)}, "key_state")

    def screen(self) -> dict[str, Any]:
        return self.request({"cmd": "screen"}, "screen")
