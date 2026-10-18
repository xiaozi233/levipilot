"""Block name hashes: FNV-1-64 over ``minecraft:*`` names.

The game identifies a block type by a 64-bit hash of its name
(``FNV-1: hash = hash * prime; hash ^= byte``).  ``agent.block_at`` returns
that hash; this module maps it back to a name by scanning the names embedded
in the installed executable.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

FNV_BASIS_64 = 0xCBF29CE484222325
FNV_PRIME_64 = 0x100000001B3
AIR_HASH = 0xBD584BAF00003448  # FNV-1-64("minecraft:air")


def fnv1_64(text: str) -> int:
    value = FNV_BASIS_64
    for byte in text.encode("utf-8"):
        value = (value * FNV_PRIME_64) & 0xFFFFFFFFFFFFFFFF
        value ^= byte
    return value


@lru_cache(maxsize=4)
def _table_for(exe_path: str, mtime: float) -> dict[int, str]:
    data = Path(exe_path).read_bytes()
    names = {
        match.group(0).decode("ascii", "ignore")
        for match in re.finditer(rb"minecraft:[a-z0-9_]+", data)
    }
    names.add("minecraft:air")
    return {fnv1_64(name): name for name in names}


def name_table(exe: str | Path | None = None) -> dict[int, str]:
    """hash -> ``minecraft:name`` for every name found in the executable."""
    if exe is None:
        from driver.check_binary import default_exe

        exe = default_exe()
    path = Path(exe)
    return _table_for(str(path), path.stat().st_mtime)


def name_for_hash(value: int, exe: str | Path | None = None) -> str | None:
    return name_table(exe).get(value)


def format_block(result: dict, exe: str | Path | None = None) -> str:
    """Human-readable one-liner for an agent.block_at result."""
    pos = result.get("pos")
    if not result.get("ok"):
        return f"block_at failed: {result.get('error')}"
    if result.get("air"):
        return f"block at {pos}: minecraft:air"
    if result.get("block") is None:
        return f"block at {pos}: <null>"
    if result.get("name"):
        return f"block at {pos}: {result['name']}"
    raw = result.get("name_hash")
    if raw is None:
        return f"block at {pos}: unknown (no legacy hash)"
    value = int(raw)
    name = name_for_hash(value, exe)
    return f"block at {pos}: {name or 'unmapped'} (0x{value:016X})"
