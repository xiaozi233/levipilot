"""World lifecycle: list/enter/leave, teleports, options, checkpoints."""

from __future__ import annotations

import time
from typing import Any

from ..connection import PipeConnection
from ..errors import ModTimeout


class WorldCommands(PipeConnection):
    def worlds(self, timeout: float | None = None) -> dict[str, Any]:
        """List cached worlds (id, name, last_saved, game_type)."""
        return self.request({"cmd": "worlds"}, "worlds", timeout=timeout)

    def signin(self) -> dict[str, Any]:
        """Xbox Live sign-in state from the main-menu model.

        ``known=false`` means the OreUI model is not available (for example
        in-world); call it from the main menu before entering a world.
        """
        return self.request({"cmd": "signin"}, "signin")

    def enter_world(
        self,
        name: str | None = None,
        *,
        world_id: str | None = None,
        intent: int = 0,
        force: bool = False,
        timeout: float = 40.0,
    ) -> dict[str, Any]:
        """Start an existing world without going through the menu UI.

        The mod waits for Xbox Live sign-in first (unless ``force``), replies
        ``enter_world_result``, then emits ``enter_world_settled`` once the join
        succeeds or is cancelled. The default timeout stays explicit: the
        sign-in wait alone can take tens of seconds.
        """
        if not name and not world_id:
            raise ValueError("name or world_id is required")
        message: dict[str, Any] = {"cmd": "enter_world", "intent": int(intent)}
        if name:
            message["name"] = name
        if world_id:
            message["id"] = world_id
        if force:
            message["force"] = True
        return self.request(message, "enter_world_result", timeout=timeout)

    def wait_enter_settled(self, timeout: float = 45.0) -> dict[str, Any]:
        """Wait for the join outcome of the most recent ``enter_world``."""
        return self.wait_event("enter_world_settled", timeout)

    def enter_world_ready(
        self,
        name: str | None = None,
        *,
        world_id: str | None = None,
        intent: int = 0,
        force: bool = False,
        attempts: int = 3,
        retry_delay: float = 4.0,
        settled_timeout: float = 45.0,
    ) -> dict[str, Any]:
        """Enter a world, retrying the async sign-in/join race.

        Returns the ``enter_world_settled`` message on success, or the last
        failure (``ok=false``) after all attempts.
        """
        last: dict[str, Any] = {"ok": False, "reason": "not_attempted"}
        for attempt in range(max(1, attempts)):
            # Drop a settled event left over from a previous attempt (for
            # example one that arrived after wait_enter_settled timed out):
            # request() defers it, and a later wait would match the stale row
            # as this attempt's outcome.
            while True:
                try:
                    self.wait_event("enter_world_settled", 0)
                except ModTimeout:
                    break
            result = self.enter_world(
                name,
                world_id=world_id,
                intent=intent,
                force=force,
                timeout=settled_timeout,
            )
            if not result.get("ok"):
                last = result
            else:
                try:
                    last = self.wait_enter_settled(timeout=settled_timeout)
                except ModTimeout:
                    last = {"ok": False, "reason": "settled_timeout"}
                if last.get("ok"):
                    return last
            if attempt + 1 < attempts:
                time.sleep(retry_delay)
        return last

    def leave_world(self, timeout: float = 20.0) -> dict[str, Any]:
        # Saving and leaving a large world can exceed the default timeout.
        return self.request({"cmd": "leave_world"}, "leave_world_result", timeout=timeout)

    def reset(
        self,
        x: float,
        y: float,
        z: float,
        *,
        yaw: float | None = None,
        pitch: float | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Clear input/tasks/macro, then teleport to a known feet pose.

        The server teleport lands on the next tick; callers that need a settled
        state should wait a few ticks before starting a run.
        """
        message: dict[str, Any] = {"cmd": "reset", "x": float(x), "y": float(y), "z": float(z)}
        if yaw is not None:
            message["yaw"] = float(yaw)
        if pitch is not None:
            message["pitch"] = float(pitch)
        return self.request(message, "reset_done", timeout=timeout)

    def teleport(
        self,
        x: float,
        y: float,
        z: float,
        *,
        yaw: float | None = None,
        pitch: float | None = None,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"cmd": "teleport", "x": float(x), "y": float(y), "z": float(z)}
        if yaw is not None:
            message["yaw"] = float(yaw)
        if pitch is not None:
            message["pitch"] = float(pitch)
        return self.request(message, "teleported")

    def set_options(
        self,
        values: dict[str, Any] | None = None,
        *,
        text: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Apply option save-tag values (``gfx_gamma``, ``gfx_viewdistance`` ...).

        ``loadOptionsFromString`` uses the same ``key:value`` format as
        ``options.txt``, so values passed here are applied through the game's
        normal option pipeline and then persisted with ``saveIfNeeded``.
        """
        message: dict[str, Any] = {"cmd": "options_set"}
        if values:
            message["values"] = values
        if text:
            message["text"] = text
        if "values" not in message and "text" not in message:
            raise ValueError("values or text is required")
        return self.request(message, "options_set", timeout=timeout)

    def save_options(self, timeout: float | None = None) -> dict[str, Any]:
        return self.request({"cmd": "options_save"}, "options_saved", timeout=timeout)
