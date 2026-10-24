"""Closed-loop tasks bound to accepted task ids (navigate/route/mine/place/use)."""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

from ..connection import PipeConnection
from ..errors import ModProtocolError, ModTaskCancelled, ModTimeout


class TaskCommands(PipeConnection):
    def _task_request(
        self, message: dict[str, Any], started: str, terminal: str | tuple[str, ...], timeout: float
    ) -> dict[str, Any]:
        """Bind completion to the task accepted by this command, not its type."""
        events = (terminal,) if isinstance(terminal, str) else terminal
        with self.exchange():
            deadline = time.monotonic() + timeout
            accepted = self.request(message, started, timeout=timeout)
            task_id = accepted.get("task_id")
            if not isinstance(task_id, int) or task_id <= 0:
                self.close()
                raise ModProtocolError("任务响应缺少 task_id；请更新 probe mod")
            try:
                result = self.wait_event(
                    (*events, "task_cancelled"),
                    max(0.0, deadline - time.monotonic()),
                    predicate=lambda row: row.get("task_id") == task_id,
                )
            except ModTimeout:
                self.close()
                raise
            if result["event"] == "task_cancelled":
                raise ModTaskCancelled(task_id, str(result.get("reason", "cancelled")))
            return result

    def navigate(
        self,
        x: float,
        z: float,
        *,
        tolerance: float = 0.35,
        sprint: bool = False,
        max_ticks: int = 600,
        jump: str = "auto",
        jump_distance: float = 1.0,
        edge_lookahead: float = 0.55,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Walk to ``(x, z)`` in a closed loop on the client tick.

        ``jump`` selects the takeoff policy: ``auto`` (when stuck), ``distance``
        (within ``jump_distance`` of the target), ``edge`` (support ahead is
        missing) or ``never``.
        """
        if timeout is None:
            timeout = max_ticks / 20.0 + 15.0
        return self._task_request(
            {
                "cmd": "navigate",
                "x": float(x),
                "z": float(z),
                "tolerance": float(tolerance),
                "sprint": bool(sprint),
                "max_ticks": int(max_ticks),
                "jump": jump,
                "jump_distance": float(jump_distance),
                "edge_lookahead": float(edge_lookahead),
            },
            "navigate_started",
            "navigated",
            timeout=timeout,
        )

    def jump_when(
        self,
        mode: str = "now",
        *,
        x: float | None = None,
        z: float | None = None,
        distance: float = 0.6,
        forward: float = 0.0,
        sprint: bool = False,
        yaw: float | None = None,
        after_ticks: int = 1,
        max_ticks: int = 60,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Jump at a controlled moment.

        ``mode``: ``now``, ``tick`` (after ``after_ticks``), ``distance``
        (horizontal distance to x,z <= ``distance``) or ``edge`` (the block
        ahead has no support). ``forward``/``sprint`` keep the player moving
        while waiting, so one command can run and jump.
        """
        if mode == "distance" and (x is None or z is None):
            raise ValueError("distance mode requires x and z")
        message: dict[str, Any] = {
            "cmd": "jump_when",
            "mode": mode,
            "distance": float(distance),
            "forward": float(forward),
            "sprint": bool(sprint),
            "after_ticks": int(after_ticks),
            "max_ticks": int(max_ticks),
        }
        if x is not None:
            message["x"] = float(x)
        if z is not None:
            message["z"] = float(z)
        if yaw is not None:
            message["yaw"] = float(yaw)
        if timeout is None:
            timeout = max_ticks / 20.0 + 10.0
        return self._task_request(message, "jump_when_started", "jumped", timeout=timeout)

    def route(
        self,
        waypoints: Iterable[dict[str, Any]],
        *,
        sprint: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Run a waypoint route in the mod and return ``route_done``/``route_failed``.

        Each waypoint accepts ``x, z, tolerance, max_ticks, jump, jump_distance,
        edge_lookahead`` where ``jump`` is ``auto`` (default), ``distance``,
        ``edge`` or ``never``.
        """
        points = [dict(point) for point in waypoints]
        if not points:
            raise ValueError("at least one waypoint is required")
        total_ticks = sum(int(point.get("max_ticks", 600)) for point in points)
        if timeout is None:
            timeout = total_ticks / 20.0 + 20.0
        return self._task_request(
            {"cmd": "route", "waypoints": points, "sprint": bool(sprint)},
            "route_started",
            ("route_done", "route_failed"),
            timeout,
        )

    def mine_block(
        self,
        x: int,
        y: int,
        z: int,
        *,
        max_ticks: int = 400,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Break one block; the mod drives start/continueDestroyBlock per tick."""
        if timeout is None:
            timeout = max_ticks / 20.0 + 15.0
        return self._task_request(
            {"cmd": "mine", "x": int(x), "y": int(y), "z": int(z), "max_ticks": int(max_ticks)},
            "mine_started",
            "block_mined",
            timeout=timeout,
        )

    def place_block(
        self,
        x: int,
        y: int,
        z: int,
        *,
        against: tuple[int, int, int] | None = None,
        face: int | None = None,
        expected: str | None = None,
        slot: int | None = None,
        max_ticks: int = 30,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Place the held block into ``(x, y, z)`` and verify the result.

        ``against`` is the block that gets clicked; when omitted the mod picks a
        visible neighbour. ``expected`` checks the resulting block name.
        """
        if timeout is None:
            timeout = max_ticks / 20.0 + 10.0
        message: dict[str, Any] = {
            "cmd": "place",
            "x": int(x),
            "y": int(y),
            "z": int(z),
            "max_ticks": int(max_ticks),
        }
        if against is not None:
            message["against"] = [int(v) for v in against]
        if face is not None:
            message["face"] = int(face)
        if expected is not None:
            message["expected"] = expected
        if slot is not None:
            message["slot"] = int(slot)
        return self._task_request(message, "place_started", "block_placed", timeout=timeout)

    def use_item(self, ticks: int = 32, *, timeout: float | None = None) -> dict[str, Any]:
        """Hold the main-hand item for ``ticks`` and then release it (eat/bow)."""
        if timeout is None:
            timeout = ticks / 20.0 + 10.0
        return self._task_request(
            {"cmd": "use", "ticks": int(ticks)}, "use_started", "item_used", timeout=timeout
        )

    def task_status(self) -> dict[str, Any]:
        return self.request({"cmd": "task_status"}, "task_status")

    def task_stop(self) -> dict[str, Any]:
        return self.request({"cmd": "task_stop"}, "task_stopped")
