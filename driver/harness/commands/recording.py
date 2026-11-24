"""Tick recording: the legacy JSON stream and the fixed-size binary ring (T4)."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, overload

from ..errors import ModProtocolError
from ..record import RecordEntry, WatchSample, decode_dump, decode_watch_dump
from ._protocol import PipeMixinBase


class RecordingCommands(PipeMixinBase):
    def start(self, max_ticks: int = 0) -> dict[str, Any]:
        return self.request({"cmd": "start", "max_ticks": int(max_ticks)}, "started")

    def stop(self) -> dict[str, Any]:
        reply = self.request({"cmd": "stop"}, "stopped")
        self.check_recording_result(reply)
        return reply

    @staticmethod
    def check_recording_result(reply: dict[str, Any]) -> None:
        """Old mods omit ok; an explicit failure must not look like a complete run."""
        if reply.get("ok") is False:
            raise ModProtocolError(f"recording: {reply.get('reason', 'failed')}")

    def record_bin_start(
        self,
        period: int = 1,
        *,
        watch: Sequence[Mapping[str, Any]] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Start the fixed-size binary tick ring (T4)."""
        message: dict[str, Any] = {"cmd": "record_start", "period": int(period)}
        if watch:
            payload_watch: list[dict[str, Any]] = []
            for spec in watch:
                item: dict[str, Any] = {
                    "offset": int(spec.get("offset", 0)),
                    "size": int(spec.get("size", 4)),
                }
                if "component_id" in spec:
                    item["component_id"] = int(spec["component_id"])
                elif "addr" in spec:
                    item["addr"] = spec["addr"]
                else:
                    raise ValueError("watch spec requires component_id or addr")
                payload_watch.append(item)
            message["watch"] = payload_watch
        return self.request(message, "record_started", timeout=timeout)

    def record_bin_stop(self, timeout: float | None = None) -> dict[str, Any]:
        reply = self.request({"cmd": "record_stop"}, "record_stopped", timeout=timeout)
        self.check_recording_result(reply)
        return reply

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

    @overload
    def record_dump_all(
        self,
        *,
        since: int = 0,
        chunk: int = 1024,
        format: str = "jsonl",
        timeout: float | None = None,
        allow_partial: bool = False,
        record_id: int | None = None,
        include_watch: Literal[False] = False,
    ) -> list[RecordEntry]: ...

    @overload
    def record_dump_all(
        self,
        *,
        since: int = 0,
        chunk: int = 1024,
        format: str = "jsonl",
        timeout: float | None = None,
        allow_partial: bool = False,
        record_id: int | None = None,
        include_watch: Literal[True],
    ) -> tuple[list[RecordEntry], list[WatchSample]]: ...

    def record_dump_all(
        self,
        *,
        since: int = 0,
        chunk: int = 1024,
        format: str = "jsonl",
        timeout: float | None = None,
        allow_partial: bool = False,
        record_id: int | None = None,
        include_watch: bool = False,
    ) -> list[RecordEntry] | tuple[list[RecordEntry], list[WatchSample]]:
        """Drain one recording up to its initial head; reject failure/loss by default."""
        entries: list[RecordEntry] = []
        watch_entries: list[WatchSample] = []
        stop_at: int | None = None
        cursor = since
        with self.exchange():
            while True:
                limit = chunk if stop_at is None else min(chunk, stop_at - cursor)
                message = self.record_dump(cursor, limit=limit, format=format, timeout=timeout)
                observed_id = message.get("record_id")
                if record_id is None:
                    record_id = observed_id
                elif observed_id is not None and observed_id != record_id:
                    raise ModProtocolError("record_dump recording changed during collection")
                if not allow_partial:
                    self.check_recording_result(message)
                    if int(message.get("lost", 0)):
                        raise ModProtocolError(
                            "record_dump lost rows; use allow_partial=True to inspect"
                        )
                batch = decode_dump(message)
                next_cursor = int(message.get("next", cursor))
                if batch and next_cursor <= cursor:
                    raise ModProtocolError("record_dump cursor did not advance")
                entries.extend(batch)
                if include_watch:
                    watch_entries.extend(decode_watch_dump(message))
                cursor = next_cursor
                if stop_at is None:
                    stop_at = int(message.get("head", cursor))
                if cursor >= stop_at or not batch:
                    break
        if include_watch:
            return entries, watch_entries
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
            started = self.start(max_ticks)
            record_id = started.get("record_id")
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
                    message = self.wait_event(
                        ("tick", "stopped", "moving", "stopping"),
                        predicate=lambda row: (
                            record_id is None or row.get("record_id", record_id) == record_id
                        ),
                    )
                    event = message.get("event")
                    if event == "tick":
                        samples.append(message)
                        if on_sample is not None:
                            on_sample(message)
                    elif event == "stopped":
                        self.check_recording_result(message)
                        break
            except BaseException:
                # Leaving the mod recording (max_ticks=0 is unbounded) would
                # poison every later workflow on this connection.
                with contextlib.suppress(Exception):
                    self.send({"cmd": "stop"})
                raise
        return samples
