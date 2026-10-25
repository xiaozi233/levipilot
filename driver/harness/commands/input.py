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

    def input_lock(self, on: bool = True) -> dict[str, Any]:
        """Drop real hardware keys/mouse while scripts own the session.

        Our own injections (``key_event``/``set_input``/``mouse_event``/macros)
        still pass; the lock applies at the HID key-down (Hardware origin) and
        MouseDevice feed entries, plus real camera turns.
        """
        return self.request({"cmd": "input_lock", "on": bool(on)}, "input_lock_set")

    def jump(self) -> dict[str, Any]:
        return self.request({"cmd": "jump"}, "jump_ok")

    def toggle_fly(self, on: bool | None = None) -> dict[str, Any]:
        """Grant/remove double-space flight (the MayFly ability).

        Works in any mode except spectator; without ``on`` each call flips it.
        The reply is emitted after the tick applies it and carries the
        resulting ``mayfly``/``flying``/``gamemode``.
        """
        message: dict[str, Any] = {"cmd": "togglefly"}
        if on is not None:
            message["on"] = bool(on)
        return self.request(message, "togglefly_ok")

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
        """Queue a key edge.

        ``via`` selects the entry point: ``hid`` (Virtual origin, default),
        ``hardware`` (Hardware origin carrying the injection guard — the only
        path that flips double-space flight, and it still works while
        ``input_lock`` is on), ``raw`` (Hardware origin with no guard, i.e. what
        a real key looks like: dropped by ``input_lock``, for lock tests only),
        or ``queue`` (``Keyboard::_inputs`` push — no Levilamina events).
        """
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

    def input_cfg(
        self,
        *,
        source: int | None = None,
        controller: int | None = None,
        focus: int | None = None,
        mode: int | None = None,
        jump_impulse: int | None = None,
        force: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Configure synthetic input routing.

        ``jump_impulse``: 1 = ``LocalPlayer::jumpFromGround`` (default, the
        measured-correct takeoff), 3 = additionally ``setJumpPendingScale``.
        Modes 0 (input pipeline only, three-tick ground stall then a ~0.99
        block snap) and 2 (``setJumpPendingScale`` alone, no takeoff) are
        rejected unless ``force=True``.

        The mod applies ``source``/``controller``/``focus`` on every call and
        resets the ones omitted to their defaults (-1/0/0).
        """
        if jump_impulse is not None and jump_impulse not in (1, 3) and not force:
            raise ValueError(
                f"jump_impulse={jump_impulse} 复现实测无法正常起跳的路径"
                "（0 = 输入管线三拍橡皮筋；2 = setJumpPendingScale 单独不起跳）；"
                "用 1（jumpFromGround）或 3，或 force=True 显式复现"
            )
        message: dict[str, Any] = {"cmd": "input_cfg"}
        if source is not None:
            message["source"] = int(source)
        if controller is not None:
            message["controller"] = int(controller)
        if focus is not None:
            message["focus"] = int(focus)
        if mode is not None:
            message["mode"] = int(mode)
        if jump_impulse is not None:
            message["jump_impulse"] = int(jump_impulse)
            if force:
                message["force"] = True
        return self.request(message, "input_cfg_ok", timeout=timeout)

    def screen(self) -> dict[str, Any]:
        return self.request({"cmd": "screen"}, "screen")
