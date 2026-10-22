"""Decoder for the mod's 64-byte binary tick ring (T4).

``record_dump`` returns either decoded JSON rows (``format=jsonl``) or a hex
blob of fixed 64-byte entries (``format=bin``/``hex``). Because the transport is
JSON Lines, binary payloads are hex-encoded and chunked by ``limit``; this
module keeps the struct layout and flag bits in one place.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

ENTRY_SIZE = 64
ENTRY_FORMAT = "<Q9f5I"

FLAG_ON_GROUND = 1 << 0
FLAG_IN_AIR = 1 << 1
FLAG_JUMP_EDGE = 1 << 2
FLAG_SPRINT = 1 << 3
FLAG_SNEAK = 1 << 4
FLAG_ACTION_SPRINT = 1 << 5
FLAG_INPUT_W = 1 << 6
FLAG_INPUT_A = 1 << 7
FLAG_INPUT_S = 1 << 8
FLAG_INPUT_D = 1 << 9
FLAG_INPUT_JUMP = 1 << 10

assert struct.calcsize(ENTRY_FORMAT) == ENTRY_SIZE


@dataclass(frozen=True)
class RecordEntry:
    tick: int
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    yaw: float
    pitch: float
    feet_y: float
    flags: int
    block_below: int
    block_feet: int
    block_head: int
    health_milli: int

    @classmethod
    def from_bytes(cls, data: bytes, offset: int = 0) -> RecordEntry:
        if offset + ENTRY_SIZE > len(data):
            raise ValueError("record entry is truncated")
        values = struct.unpack_from(ENTRY_FORMAT, data, offset)
        return cls(*values)

    @property
    def on_ground(self) -> bool:
        return bool(self.flags & FLAG_ON_GROUND)

    @property
    def in_air(self) -> bool:
        return bool(self.flags & FLAG_IN_AIR)

    @property
    def jump_edge(self) -> bool:
        return bool(self.flags & FLAG_JUMP_EDGE)

    @property
    def sprinting(self) -> bool:
        return bool(self.flags & FLAG_SPRINT)

    @property
    def sneaking(self) -> bool:
        return bool(self.flags & FLAG_SNEAK)

    @property
    def action_sprint(self) -> bool:
        return bool(self.flags & FLAG_ACTION_SPRINT)

    @property
    def input(self) -> dict[str, bool]:
        return {
            "w": bool(self.flags & FLAG_INPUT_W),
            "a": bool(self.flags & FLAG_INPUT_A),
            "s": bool(self.flags & FLAG_INPUT_S),
            "d": bool(self.flags & FLAG_INPUT_D),
            "jump": bool(self.flags & FLAG_INPUT_JUMP),
        }

    @property
    def health(self) -> float:
        return self.health_milli / 1000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "vx": self.vx,
            "vy": self.vy,
            "vz": self.vz,
            "yaw": self.yaw,
            "pitch": self.pitch,
            "feet_y": self.feet_y,
            "flags": self.flags,
            "on_ground": self.on_ground,
            "in_air": self.in_air,
            "jump_edge": self.jump_edge,
            "sprint": self.sprinting,
            "sneak": self.sneaking,
            "action_sprint": self.action_sprint,
            "input": self.input,
            "block_below": self.block_below,
            "block_feet": self.block_feet,
            "block_head": self.block_head,
            "health_milli": self.health_milli,
            "health": self.health,
        }


def decode_hex(text: str) -> list[RecordEntry]:
    """Decode one ``record_dump`` hex payload."""
    data = bytes.fromhex(text)
    if len(data) % ENTRY_SIZE:
        raise ValueError(f"payload is not a multiple of {ENTRY_SIZE} bytes")
    return [RecordEntry.from_bytes(data, offset) for offset in range(0, len(data), ENTRY_SIZE)]


def decode_dump(message: dict[str, Any]) -> list[RecordEntry]:
    """Decode a ``record_dump`` reply regardless of its encoding."""
    if message.get("event") != "record_dump":
        raise ValueError("not a record_dump message")
    if "data" in message:
        return decode_hex(str(message["data"]))
    rows = message.get("rows", [])
    return [RecordEntry.from_bytes(struct.pack(ENTRY_FORMAT, *(_row_values(row)))) for row in rows]


def _row_values(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(row.get("tick", 0)),
        float(row.get("x", 0.0)),
        float(row.get("y", 0.0)),
        float(row.get("z", 0.0)),
        float(row.get("vx", 0.0)),
        float(row.get("vy", 0.0)),
        float(row.get("vz", 0.0)),
        float(row.get("yaw", 0.0)),
        float(row.get("pitch", 0.0)),
        float(row.get("feet_y", row.get("y", 0.0))),
        int(row.get("flags", 0)),
        int(row.get("block_below", 0)),
        int(row.get("block_feet", 0)),
        int(row.get("block_head", 0)),
        int(row.get("health_milli", 0)),
    )


def block_hash(name: str) -> int:
    """FNV-1a 32 used by the mod for the three block fields."""
    value = 2166136261
    for byte in name.encode("utf-8"):
        value ^= byte
        value = (value * 16777619) & 0xFFFFFFFF
    return value


__all__ = [
    "ENTRY_SIZE",
    "FLAG_ACTION_SPRINT",
    "FLAG_INPUT_A",
    "FLAG_INPUT_D",
    "FLAG_INPUT_JUMP",
    "FLAG_INPUT_S",
    "FLAG_INPUT_W",
    "FLAG_IN_AIR",
    "FLAG_JUMP_EDGE",
    "FLAG_ON_GROUND",
    "FLAG_SNEAK",
    "FLAG_SPRINT",
    "RecordEntry",
    "block_hash",
    "decode_dump",
    "decode_hex",
]
