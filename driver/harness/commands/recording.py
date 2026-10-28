"""Tick recording: the legacy JSON stream and the fixed-size binary ring (T4)."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from ..connection import PipeConnection
from ..errors import ModProtocolError
from ..record import RecordEntry, decode_dump


class RecordingCommands(PipeConnection):
    def start(self, max_ticks: int = 0) -> dict[str, Any]:
        return self.request({"cmd": "start", "max_ticks": int(max_ticks)}, "started")

    def stop(self) -> dict[str, Any]:
        return self.request({"cmd": "stop"}, "stopped")

    def record_bin_start(self, period: int = 1, timeout: float | None = None) -> dict[str, Any]:
        """Start the fixed-size binary tick ring (T4)."""
        return self.request(
            {"cmd": "record_start", "period": int(period)},
            "record_started",
            timeout=timeout,
        )

    def record_bin_stop(self, timeout: float | None = None) -> dict[str, Any]:
        return self.request({"cmd": "record_stop"}, "record_stopped", timeout=timeout)

    def record_dump(
        self,
        since: int = 0,
        *,
        limit: int = 1024,
        format: str = "jsonl",  # mirrors the wire field name
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self.request(
            {
                "cmd": "record_dump",
                "since": int(since),
                "limit": int(limit),
                "format": format,
            },
            "record_dump",
            timeout=timeout,
        )

    def record_dump_all(
        self,
        *,
        since: int = 0,
        chunk: int = 1024,
        format: str = "jsonl",
        timeout: float | None = None,
    ) -> list[RecordEntry]:
        """Drain the ring up to the head observed on the first reply."""
        entries: list[RecordEntry] = []
        stop_at: int | None = None
        cursor = since
        with self.exchange():
            while True:
                limit = chunk if stop_at is None else min(chunk, stop_at - cursor)
                message = self.record_dump(cursor, limit=limit, format=format, timeout=timeout)
                batch = decode_dump(message)
                next_cursor = int(message.get("next", cursor))
                if batch and next_cursor <= cursor:
                    raise ModProtocolError("record_dump cursor did not advance")
                entries.extend(batch)
                cursor = next_cursor
                if stop_at is None:
                    stop_at = int(message.get("head", cursor))
                if cursor >= stop_at or not batch:
                    break
        return entries

    def record(
        self,
        max_ticks: int,
        on_sample: Callable[[dict[str, Any]], None] | None = None,
        move_forward: float = 0.0,
        move_strafe: float = 0.0,
        move_ticks: int = 0,
    ) -> list[dict[str, Any]]:
        samples: list[dict[str, Any]] = []
        with self.exchange():
            self.start(max_ticks)
            try:
                if move_ticks:
                    self.send(
                        {
                            "cmd": "move",
                            "forward": move_forward,
                            "strafe": move_strafe,
                            "ticks": move_ticks,
                        }
                    )
                while True:
                    # wait_event preserves unrelated notifications without
                    # immediately rereading the same deferred notification.
                    message = self.wait_event(("tick", "stopped", "moving", "stopping"))
                    event = message.get("event")
                    if event == "tick":
                        samples.append(message)
                        if on_sample is not None:
                            on_sample(message)
                    elif event == "stopped":
                        break
            except BaseException:
                # Leaving the mod recording (max_ticks=0 is unbounded) would
                # poison every later workflow on this connection.
                with contextlib.suppress(Exception):
                    self.send({"cmd": "stop"})
                raise
        return samples
