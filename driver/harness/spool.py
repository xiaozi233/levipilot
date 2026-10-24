"""Reader for the mod's durable record spool (T1).

The mod appends every ``enqueue`` line to ``<modDir>/spool-<pid>.jsonl`` and
flushes at most 100 ms after the write, so a killed game still leaves a usable
tail. This module reads that file incrementally:

- a partial last line is kept and completed on the next poll;
- if the file shrinks (the mod restarted and recreated it) the reader treats it
  as a rotation and starts from byte 0;
- ``queue_dropped`` markers are folded into :attr:`SpoolStats.dropped`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SpoolStats:
    rows: int = 0
    dropped: int = 0
    bad_lines: int = 0
    rotations: int = 0
    offset: int = 0
    pending: str = ""


@dataclass
class SpoolReader:
    """Incremental, rotation-tolerant reader for one spool file."""

    path: Path
    stats: SpoolStats = field(default_factory=SpoolStats)
    # Bytes, not str: decoding per poll would split a multi-byte UTF-8
    # character across boundaries and turn it into U+FFFD.
    _pending: bytes = b""

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    def poll(self) -> list[dict[str, Any]]:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.stats.offset:
            # The file was recreated (new pid or truncated): read it from the
            # start and count the rotation for diagnostics.
            self.stats.offset = 0
            self.stats.rotations += 1
            self._pending = b""
        if size == self.stats.offset:
            return []

        # Bounded read so one poll after a long pause cannot balloon memory.
        with self.path.open("rb") as handle:
            handle.seek(self.stats.offset)
            chunk = handle.read(4 << 20)
        self.stats.offset += len(chunk)

        # Split on bytes and decode only complete lines: a multi-byte UTF-8
        # character split across poll boundaries stays intact for the next
        # poll instead of becoming U+FFFD.
        parts = (self._pending + chunk).split(b"\n")
        partial_bytes = parts.pop()
        self._pending = partial_bytes
        partial = partial_bytes.decode("utf-8", errors="replace")
        self.stats.pending = partial
        lines = [part.decode("utf-8", errors="replace") for part in parts]

        rows: list[dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                self.stats.bad_lines += 1
                continue
            if not isinstance(event, dict):
                self.stats.bad_lines += 1
                continue
            if event.get("event") == "queue_dropped":
                self.stats.dropped = int(event.get("count", self.stats.dropped))
                continue
            rows.append(event)
        self.stats.rows += len(rows)
        return rows


def read_spool(
    path: str | Path, *, max_bytes: int | None = 4_000_000
) -> tuple[list[dict[str, Any]], SpoolStats]:
    """Read a spool once, tolerating a truncated final line.

    Large files are read from a bounded tail so a long recording session does
    not have to fit in RAM. ``max_bytes=None`` reads everything.
    """
    target = Path(path)
    stats = SpoolStats()
    try:
        size = target.stat().st_size
    except OSError:
        return [], stats
    offset = 0
    if max_bytes is not None and size > max_bytes:
        offset = size - max_bytes
    try:
        with target.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except OSError:
        return [], stats
    if offset:
        boundary = data.find(b"\n")
        data = data[boundary + 1 :] if boundary >= 0 else b""

    rows: list[dict[str, Any]] = []
    for line in data.decode("utf-8", errors="replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            stats.bad_lines += 1
            continue
        if not isinstance(event, dict):
            stats.bad_lines += 1
            continue
        if event.get("event") == "queue_dropped":
            stats.dropped = int(event.get("count", stats.dropped))
            continue
        stats.rows += 1
        rows.append(event)
    return rows, stats


__all__ = ["SpoolReader", "SpoolStats", "read_spool"]
