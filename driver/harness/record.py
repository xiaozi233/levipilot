"""Decoder for the mod's 64-byte binary tick ring (T4).

``record_dump`` returns either decoded JSON rows (``format=jsonl``) or a hex
blob of fixed 64-byte entries (``format=bin``/``hex``). Because the transport is
JSON Lines, binary payloads are hex-encoded and chunked by ``limit``; this
module keeps the struct layout and flag bits in one place.
"""

from __future__ import annotations

import base64
import math
import struct
from collections.abc import Mapping, Sequence
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
FLAG_BLOCK_OOB = 1 << 11
# Feet/head block is a liquid (flowing variants included).
FLAG_IN_WATER = 1 << 12
FLAG_IN_LAVA = 1 << 13
FLAG_SWIMMING = 1 << 14
FLAG_FLYING = 1 << 15
FLAG_GLIDING = 1 << 16

WATCH_MAX_SLOTS = 4
WATCH_ENTRY_FORMAT = "<4QII"
WATCH_ENTRY_SIZE = 40

if struct.calcsize(WATCH_ENTRY_FORMAT) != WATCH_ENTRY_SIZE:
    raise RuntimeError(
        f"WATCH_ENTRY_FORMAT size {struct.calcsize(WATCH_ENTRY_FORMAT)} != {WATCH_ENTRY_SIZE}"
    )

if struct.calcsize(ENTRY_FORMAT) != ENTRY_SIZE:
    raise RuntimeError(f"ENTRY_FORMAT size {struct.calcsize(ENTRY_FORMAT)} != {ENTRY_SIZE}")


@dataclass(frozen=True)
class RecordEntry:
    """One 64-byte tick sample from the mod's binary ring.

    ``vx, vy, vz`` are ``Actor::getVelocity()`` (completed per-tick displacement
    ``mPos - mPosPrev``), not pending ``StateVectorComponent::mPosDelta``
    (``state["pos_delta_x/y/z"]``).
    """

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
    def in_water(self) -> bool:
        return bool(self.flags & FLAG_IN_WATER)

    @property
    def in_lava(self) -> bool:
        return bool(self.flags & FLAG_IN_LAVA)

    @property
    def swimming(self) -> bool:
        return bool(self.flags & FLAG_SWIMMING)

    @property
    def flying(self) -> bool:
        return bool(self.flags & FLAG_FLYING)

    @property
    def gliding(self) -> bool:
        return bool(self.flags & FLAG_GLIDING)

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

    @property
    def completed_delta(self) -> tuple[float, float, float]:
        """Completed per-tick displacement ``(vx, vy, vz)`` from ``Actor::getVelocity()``.

        In Minecraft Bedrock, ``Actor::getVelocity()`` returns ``mPos - mPosPrev``
        (completed displacement), which is distinct from ``StateVectorComponent::mPosDelta``
        (pending pre-motion delta exposed as ``state["pos_delta_x/y/z"]``).
        """
        return (self.vx, self.vy, self.vz)

    @property
    def horizontal_speed(self) -> float:
        """Completed horizontal speed ``hypot(vx, vz)`` from ``mPos - mPosPrev``.

        Computed from ``vx`` and ``vz`` (``Actor::getVelocity()``), distinct from
        pending ``StateVectorComponent::mPosDelta`` (``state["pos_delta_x/y/z"]``).
        """
        return math.hypot(self.vx, self.vz)

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
            "in_water": self.in_water,
            "in_lava": self.in_lava,
            "swimming": self.swimming,
            "flying": self.flying,
            "gliding": self.gliding,
            "input": self.input,
            "block_below": self.block_below,
            "block_feet": self.block_feet,
            "block_head": self.block_head,
            "health_milli": self.health_milli,
            "health": self.health,
        }


@dataclass(frozen=True)
class WatchSample:
    """Custom per-tick scalar reads attached to a ``RecordSession``."""

    values: tuple[int, ...]
    valid_mask: int

    def valid(self, slot: int) -> bool:
        if slot < 0 or slot >= len(self.values):
            return False
        return bool(self.valid_mask & (1 << slot))

    def as_u32(self, slot: int) -> int:
        return self.values[slot] & 0xFFFFFFFF

    def as_u64(self, slot: int) -> int:
        return self.values[slot] & 0xFFFFFFFFFFFFFFFF

    def as_i32(self, slot: int) -> int:
        return struct.unpack("<i", struct.pack("<I", self.values[slot] & 0xFFFFFFFF))[0]

    def as_f32(self, slot: int) -> float:
        return float(struct.unpack("<f", struct.pack("<I", self.values[slot] & 0xFFFFFFFF))[0])

    def as_f64(self, slot: int) -> float:
        return float(
            struct.unpack("<d", struct.pack("<Q", self.values[slot] & 0xFFFFFFFFFFFFFFFF))[0]
        )

    def as_bit(self, slot: int, bit: int) -> bool:
        if bit < 0 or bit >= 64:
            raise ValueError(f"bit index out of range 0..63: {bit}")
        return bool((self.values[slot] >> bit) & 1)

    def decode(self, slot: int, fmt: str = "u64") -> int | float | bool | str | None:
        """Decode ``slot`` according to ``fmt`` (returns ``None`` when invalid)."""
        if not self.valid(slot):
            return None
        kind = fmt.strip().lower()
        if kind == "u64":
            return self.as_u64(slot)
        if kind == "u32":
            return self.as_u32(slot)
        if kind == "i32":
            return self.as_i32(slot)
        if kind == "f32":
            return self.as_f32(slot)
        if kind == "f64":
            return self.as_f64(slot)
        if kind == "hex":
            return f"{self.as_u64(slot):#x}"
        if kind.startswith("bit:"):
            return self.as_bit(slot, int(kind[4:], 0))
        raise ValueError(f"unknown watch format {fmt!r}")


def decode_watch_row(sample: WatchSample, specs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Decode one ``WatchSample`` into named fields using optional ``name``/``fmt``/``bits``."""
    out: dict[str, Any] = {}
    for idx, spec in enumerate(specs):
        name = str(spec.get("name") or f"watch_{idx}")
        bits = spec.get("bits")
        omit_raw = bool(spec.get("omit_raw", False))
        if not (omit_raw and isinstance(bits, Mapping)):
            out[name] = sample.decode(idx, str(spec.get("fmt", "u64")))
        if isinstance(bits, Mapping):
            valid = sample.valid(idx)
            for flag_name, bit_idx in bits.items():
                out[str(flag_name)] = sample.as_bit(idx, int(bit_idx)) if valid else None
    return out


def decode_watch_chunk(b64: str, count: int) -> list[WatchSample]:
    """Decode one ``record_dump`` base64 ``watch_b64`` payload."""
    if not b64:
        return []
    raw = base64.b64decode(b64.encode("ascii"))
    if len(raw) % WATCH_ENTRY_SIZE:
        raise ValueError(f"watch payload is not a multiple of {WATCH_ENTRY_SIZE} bytes")
    slots = max(0, min(int(count), WATCH_MAX_SLOTS))
    samples: list[WatchSample] = []
    for offset in range(0, len(raw), WATCH_ENTRY_SIZE):
        *vals, valid_mask, _pad = struct.unpack_from(WATCH_ENTRY_FORMAT, raw, offset)
        samples.append(
            WatchSample(values=tuple(int(v) for v in vals[:slots]), valid_mask=int(valid_mask))
        )
    return samples


def decode_watch_dump(message: dict[str, Any]) -> list[WatchSample]:
    """Decode watch samples from either binary (``watch_b64``) or JSON ``record_dump``."""
    if message.get("event") != "record_dump":
        raise ValueError("not a record_dump message")
    count = int(message.get("watch_count", 0))
    if count <= 0:
        return []
    if "watch_b64" in message:
        return decode_watch_chunk(str(message["watch_b64"]), count)
    rows = message.get("rows", [])
    return [
        WatchSample(
            values=tuple(int(v) for v in list(row.get("watch", ()))[:count]),
            valid_mask=int(row.get("watch_mask", 0)),
        )
        for row in rows
    ]


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
    "FLAG_FLYING",
    "FLAG_GLIDING",
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
    "FLAG_SWIMMING",
    "WATCH_ENTRY_SIZE",
    "WATCH_MAX_SLOTS",
    "RecordEntry",
    "WatchSample",
    "block_hash",
    "decode_dump",
    "decode_hex",
    "decode_watch_chunk",
    "decode_watch_dump",
]
