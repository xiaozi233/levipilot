"""Decoding helpers for hardware watchpoint hits (T3).

The mod keeps hits in a fixed ring and returns them in batches via
``watch_drain``. ``since``/``next`` are absolute sequence numbers (1-based), so
a consumer can resume without re-reading; ``lost`` reports records the ring had
already overwritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WatchHit:
    seq: int
    watch: int
    tag: str
    tid: int
    tick: int
    rip: int
    stack: tuple[int, ...] = ()

    @classmethod
    def from_json(cls, row: dict[str, Any]) -> WatchHit:
        return cls(
            seq=int(row.get("seq", 0)),
            watch=int(row.get("watch", 0)),
            tag=str(row.get("tag", "")),
            tid=int(row.get("tid", 0)),
            tick=int(row.get("tick", 0)),
            rip=int(row.get("rip", 0)),
            stack=tuple(int(frame) for frame in row.get("stack", [])),
        )


@dataclass
class HitCursor:
    """Resumable drain cursor: feed it ``watch_hits`` replies."""

    since: int = 0
    hits: list[WatchHit] = field(default_factory=list)
    lost: int = 0
    batches: int = 0

    def consume(self, message: dict[str, Any]) -> list[WatchHit]:
        if message.get("event") != "watch_hits":
            return []
        batch = [WatchHit.from_json(row) for row in message.get("hits", [])]
        self.hits.extend(batch)
        self.lost += int(message.get("lost", 0))
        self.since = int(message.get("next", self.since))
        self.batches += 1
        return batch


def decode_hits(message: dict[str, Any]) -> list[WatchHit]:
    return [WatchHit.from_json(row) for row in message.get("hits", [])]


__all__ = ["HitCursor", "WatchHit", "decode_hits"]
