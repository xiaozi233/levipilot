"""Hardware watchpoints (T3)."""

from __future__ import annotations

from typing import Any

from ..connection import PipeConnection
from .hooks import remember_ring_head, ring_head


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
        sample: bool = False,
        snapshots: list[dict[str, Any]] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Install a debug-register watchpoint (mode w|rw|x; x requires size=1).

        ``depth`` is the number of return addresses captured per hit
        (1..32, default 8). Deeper stacks cost more per hit.
        ``sample`` adds a QPC timestamp; w/rw also reads watched bytes after
        each instruction. These reads are not atomic with concurrent writers.
        With x/sample, ``snapshots`` reads up to three argument registers'
        pointees before the instruction (reg rcx/rdx/r8/r9, size 1..64).
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
        if sample:
            message["sample"] = True
        if snapshots is not None:
            message["snapshots"] = snapshots
        reply = self.request(message, "watch_installed", timeout=timeout)
        if reply.get("id") is not None:
            remember_ring_head(self, "watch", int(reply["id"]))
        return reply

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

    def watch_drain(
        self,
        since: int | None = None,
        *,
        watch_id: int | None = None,
        limit: int = 4096,
    ) -> dict[str, Any]:
        """Drain watch hits starting at a cached head; rows are not ID-filtered by default.

        Same rules as ``hook_drain``: ``since=None`` starts at the install-time
        ring head read just after installation (or 0 without a cached head).
        ``watch_id`` filters rows after the server paginates the global ring;
        always advance using ``next``, even if the filtered page is empty.
        """
        resolved = ring_head(self, "watch", watch_id) if since is None else int(since)
        reply = self.request(
            {"cmd": "watch_drain", "since": resolved, "limit": int(limit)}, "watch_hits"
        )
        if watch_id is not None:
            reply["hits"] = [hit for hit in reply.get("hits", []) if hit.get("watch") == watch_id]
        return reply
