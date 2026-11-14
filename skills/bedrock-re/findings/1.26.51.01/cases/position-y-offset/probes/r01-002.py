"""Offline byte/string scan of Minecraft.Windows.exe for the 1.62 offset sources.

Read-only. Writes a JSON summary to out/posy-scan.json.
"""

from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, ".")
from driver import dev  # noqa: E402

EXE = dev.DEFAULT_VERSION / "Minecraft.Windows.exe"
OUT = Path("out/posy-scan.json")

PATTERNS = {
    "1.62001f": bytes.fromhex("7d5ccf3f"),
    "1.62f": bytes.fromhex("295ccf3f"),
    "0.9f": bytes.fromhex("6666663f"),
    "1e-5f": bytes.fromhex("acc52737"),
    "1.8f": bytes.fromhex("6666e63f"),
    "1.49f": bytes.fromhex("52b8be3f"),
    "0.31f": bytes.fromhex("52b89e3e"),
    "0.6f": bytes.fromhex("9a99193f"),
    "1.5f": bytes.fromhex("0000c03f"),
    "0.1f": bytes.fromhex("cdcccc3d"),
}

NEEDLES = [
    "ActorSetPosSystem",
    "UpdateBoundingBox",
    "VanillaOffsetSystem",
    "FinalizeMoveSystem",
    "StateVectorComponent",
    "OffsetsComponent",
    "ActorOffset",
    "getFeetPos",
    "getHeadPos",
    "mHeightOffset",
    "mHeadOffset",
    "mEyeOffset",
    "ClimbOutOfLiquidSystem",
    "PlayerBoundingBoxStateUpdate",
    "VanillaOffset",
    "ActorSetPositionRequest",
    "ShouldUpdateBoundingBox",
]


def sections(data: bytes) -> list[tuple[str, int, int, int]]:
    """PE sections as (name, va, raw offset, raw size)."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe : pe + 4] == b"PE\0\0", "not a PE"
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    opt = struct.unpack_from("<H", data, pe + 20)[0]
    image_base = struct.unpack_from("<Q", data, pe + 24 + 24)[0]
    out = []
    off = pe + 24 + opt
    for i in range(nsec):
        base = off + i * 40
        name = data[base : base + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", data, base + 8)
        out.append((name, vaddr, raddr, rsize))
    return out, image_base


def to_va(secs, image_base, off: int) -> int | None:
    for _name, vaddr, raddr, rsize in secs:
        if raddr <= off < raddr + rsize:
            return image_base + vaddr + (off - raddr)
    return None


def main() -> int:
    data = EXE.read_bytes()
    secs, image_base = sections(data)
    print(f"exe {len(data)} bytes, image_base 0x{image_base:X}")
    for name, vaddr, raddr, rsize in secs:
        print(f"  {name:8s} va=0x{vaddr:X} raw=0x{raddr:X} size=0x{rsize:X}")

    report: dict[str, object] = {"image_base": hex(image_base), "sections": []}

    consts = {}
    for label, pat in PATTERNS.items():
        hits = []
        start = 0
        while True:
            i = data.find(pat, start)
            if i < 0:
                break
            va = to_va(secs, image_base, i)
            hits.append({"off": hex(i), "va": hex(va) if va else None})
            start = i + 1
            if len(hits) >= 64:
                break
        consts[label] = {"count": len(hits), "first": hits[:24]}
        print(f"{label:9s} hits={len(hits)} first={[h['va'] for h in hits[:6]]}")
    report["constants"] = consts

    strings = {}
    for needle in NEEDLES:
        pat = needle.encode()
        hits = []
        start = 0
        while True:
            i = data.find(pat, start)
            if i < 0:
                break
            # keep only plausible standalone strings (ASCII boundary)
            before = data[i - 1] if i else 0
            after = data[i + len(pat)] if i + len(pat) < len(data) else 0
            if not (32 <= before < 127) and not (32 <= after < 127):
                va = to_va(secs, image_base, i)
                hits.append({"off": hex(i), "va": hex(va) if va else None})
            start = i + 1
            if len(hits) >= 40:
                break
        strings[needle] = hits
        print(f"{needle:32s} {len(hits)} {[h['va'] for h in hits[:6]]}")
    report["strings"] = strings

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
