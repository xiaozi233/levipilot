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
import os
from collections.abc import Iterable
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


def _parse_lines(lines: Iterable[bytes], stats: SpoolStats) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            # json.loads also raises ValueError for oversized integer tokens.
            stats.bad_lines += 1
            continue
        if not isinstance(event, dict):
            stats.bad_lines += 1
            continue
        if event.get("event") == "queue_dropped":
            count = event.get("count", stats.dropped)
            try:
                if isinstance(count, bool) or not isinstance(count, (int, str)):
                    raise ValueError("count must be an integer")
                dropped = int(count)
                if dropped < 0:
                    raise ValueError("count must be nonnegative")
            except ValueError:
                stats.bad_lines += 1
                continue
            stats.dropped = dropped
            continue
        rows.append(event)
    stats.rows += len(rows)
    return rows


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
            with self.path.open("rb") as handle:
                # Inspect the opened file, not a path which may be rotated
                # between stat() and open(). Commit state only after read().
                size = os.fstat(handle.fileno()).st_size
                rotated = size < self.stats.offset
                offset = 0 if rotated else self.stats.offset
                handle.seek(offset)
                chunk = handle.read(min(4 << 20, size - offset))
        except OSError:
            return []
        if rotated:
            # The file was recreated (new pid or truncated): read it from the
            # start and count the rotation for diagnostics.
            self.stats.rotations += 1
            self._pending = b""
            self.stats.pending = ""
        self.stats.offset = offset + len(chunk)
        if not chunk:
            return []

        # Split on bytes and decode only complete lines: a multi-byte UTF-8
        # character split across poll boundaries stays intact for the next
        # poll instead of becoming U+FFFD.
        parts = (self._pending + chunk).split(b"\n")
        partial_bytes = parts.pop()
        self._pending = partial_bytes
        self.stats.pending = partial_bytes.decode("utf-8", errors="replace")
        return _parse_lines(parts, self.stats)


def read_spool(
    path: str | Path, *, max_bytes: int | None = 4_000_000
) -> tuple[list[dict[str, Any]], SpoolStats]:
    """Read a spool once, tolerating a truncated final line.

    Large files are read from a bounded tail so a long recording session does
    not have to fit in RAM. ``max_bytes=None`` reads everything.
    """
    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes must be nonnegative or None")
    target = Path(path)
    stats = SpoolStats()
    try:
        with target.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            offset = max(0, size - max_bytes) if max_bytes is not None else 0
            handle.seek(offset)
            data = handle.read(size - offset)
    except OSError:
        return [], stats
    if offset:
        boundary = data.find(b"\n")
        data = data[boundary + 1 :] if boundary >= 0 else b""

    return _parse_lines(data.split(b"\n"), stats), stats


__all__ = ["SpoolReader", "SpoolStats", "read_spool"]
