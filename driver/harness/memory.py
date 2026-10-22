"""Helpers for the mod's memory primitives (T2).

The mod returns hex strings and addresses as JSON integers. These helpers keep
parsing/formatting in one place so the CLI, selftest and scripts agree.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable

FNV1_64_OFFSET = 0xCBF29CE484222325
FNV1_64_PRIME = 0x100000001B3


def format_address(value: int) -> str:
    """Format an address the way the CLI reports it."""
    return f"0x{int(value):x}"


def parse_address(value: int | str) -> int:
    """Accept ``int``, ``"0x1234"`` or ``"1234"`` (hex without prefix)."""
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"address must be non-negative: {value}")
        return value
    text = value.strip()
    if not text:
        raise ValueError("empty address")
    base = 16 if text.lower().startswith("0x") else 10
    return int(text, base)


def parse_hex_bytes(text: str) -> bytes:
    """Decode an even-length hex string; raises on malformed input."""
    cleaned = "".join(text.split())
    if len(cleaned) % 2:
        raise ValueError("hex string must have an even number of digits")
    return bytes.fromhex(cleaned)


def to_hex(data: bytes | bytearray | memoryview) -> str:
    return bytes(data).hex()


def fnv1_64(text: str | bytes) -> int:
    """FNV-1 64-bit (multiply, then XOR), as named in the T2 acceptance."""
    data = text.encode("utf-8") if isinstance(text, str) else text
    value = FNV1_64_OFFSET
    for byte in data:
        value = (value * FNV1_64_PRIME) & 0xFFFFFFFFFFFFFFFF
        value ^= byte
    return value


def fnv1a_64(text: str | bytes) -> int:
    """FNV-1a 64-bit; reported alongside ``fnv1_64`` for hash identification."""
    data = text.encode("utf-8") if isinstance(text, str) else text
    value = FNV1_64_OFFSET
    for byte in data:
        value ^= byte
        value = (value * FNV1_64_PRIME) & 0xFFFFFFFFFFFFFFFF
    return value


def u16_values(data: bytes) -> list[int]:
    return [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data) - 1, 2)]


def u32_values(data: bytes) -> list[int]:
    return [int.from_bytes(data[i : i + 4], "little") for i in range(0, len(data) - 3, 4)]


def u64_values(data: bytes) -> list[int]:
    return [int.from_bytes(data[i : i + 8], "little") for i in range(0, len(data) - 7, 8)]


def f32_values(data: bytes) -> list[float]:
    return [struct.unpack_from("<f", data, i)[0] for i in range(0, len(data) - 3, 4)]


def describe(data: Iterable[int]) -> dict[str, list[int] | list[float]]:
    """Decode a byte sequence into the same views the mod returns for raw probes."""
    raw = bytes(data)
    return {
        "u8": list(raw),
        "u16": u16_values(raw),
        "u32": u32_values(raw),
        "u64": u64_values(raw),
        "f32": f32_values(raw),
    }


__all__ = [
    "describe",
    "f32_values",
    "fnv1_64",
    "fnv1a_64",
    "format_address",
    "parse_address",
    "parse_hex_bytes",
    "to_hex",
    "u16_values",
    "u32_values",
    "u64_values",
]
