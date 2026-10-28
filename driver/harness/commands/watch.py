"""Hardware watchpoints (T3)."""

from __future__ import annotations

from typing import Any

from ..connection import PipeConnection


class WatchCommands(PipeConnection):
    def watch_set(
        self,
        addr: int | str,
        *,
        size: int = 4,
        mode: str = "w",
        thread: str | int = "tick",
        tag: str = "",
        depth: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Install a debug-register watchpoint (``size`` 1/2/4/8, mode w|rw).

        ``depth`` is the number of return addresses captured per hit
        (1..32, default 8). Deeper stacks cost more per hit.
        """
        message: dict[str, Any] = {
            "cmd": "watch_set",
            "addr": addr,
            "size": int(size),
            "mode": mode,
            "thread": thread,
            "tag": tag,
        }
        if depth is not None:
            message["depth"] = int(depth)
        return self.request(message, "watch_installed", timeout=timeout)

    def watch_clear(
        self, watch_id: int | None = None, *, all: bool = False, timeout: float | None = None
    ) -> dict[str, Any]:
        if not all and watch_id is None:
            raise ValueError("watch_id or all=True is required")
        message: dict[str, Any] = {"cmd": "watch_clear"}
        if all:
            message["all"] = True
        else:
            message["id"] = int(watch_id)  # type: ignore[arg-type]
        return self.request(message, "watch_cleared", timeout=timeout)

    def watch_list(self) -> dict[str, Any]:
        return self.request({"cmd": "watch_list"}, "watch_list")

    def watch_drain(self, since: int = 0, *, limit: int = 4096) -> dict[str, Any]:
        return self.request(
            {"cmd": "watch_drain", "since": int(since), "limit": int(limit)}, "watch_hits"
        )
