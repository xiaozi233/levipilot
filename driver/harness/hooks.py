"""Decoding helpers for runtime hook hits (T5).

The mod stores hits in a fixed ring: each row has the four integer argument
registers, the return address and four stack frames. ``b_float`` is supplied by
the mod for typed hooks whose second argument is a float.

``mem`` carries the optional in-detour snapshot requested with
``hook_install(mem_arg=..., mem_offset=..., mem_size=...)``: a short-lived
buffer behind an argument pointer is copied while the detour runs, so the
bytes survive until ``hook_drain``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any


def float_from_bits(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", int(bits) & 0xFFFFFFFF))[0]


@dataclass(frozen=True)
class HookHit:
    seq: int
    hook: int
    tag: str
    tid: int
    tick: int
    args: tuple[int, int, int, int]
    b_float: float
    ret: int
    stack: tuple[int, ...] = ()
    mem_ok: bool = False
    mem_arg: int = 0
    mem_offset: int = 0
    mem_addr: int = 0
    mem: bytes = b""
    mem_f32: tuple[float, ...] = ()

    @property
    def mem_u32(self) -> tuple[int, ...]:
        if not self.mem:
            return ()
        return struct.unpack(f"<{len(self.mem) // 4}I", self.mem)

    @classmethod
    def from_json(cls, row: dict[str, Any]) -> HookHit:
        args = row.get("args", [])
        padded = [int(value) for value in args[:4]] + [0] * (4 - len(args[:4]))
        mem = row.get("mem") or {}
        ok = bool(mem.get("ok", False))
        raw = bytes.fromhex(mem.get("hex", "")) if ok else b""
        return cls(
            seq=int(row.get("seq", 0)),
            hook=int(row.get("hook", 0)),
            tag=str(row.get("tag", "")),
            tid=int(row.get("tid", 0)),
            tick=int(row.get("tick", 0)),
            args=(padded[0], padded[1], padded[2], padded[3]),
            b_float=float(row.get("b_float", float_from_bits(padded[1]))),
            ret=int(row.get("ret", 0)),
            stack=tuple(int(frame) for frame in row.get("stack", [])),
            mem_ok=ok,
            mem_arg=int(mem.get("arg", 0)),
            mem_offset=int(mem.get("offset", 0)),
            mem_addr=int(mem.get("addr", 0)),
            mem=raw,
            mem_f32=tuple(float(value) for value in mem.get("f32", [])),
        )


def decode_hook_hits(message: dict[str, Any]) -> list[HookHit]:
    if message.get("event") != "hook_hits":
        return []
    return [HookHit.from_json(row) for row in message.get("hits", [])]


__all__ = ["HookHit", "decode_hook_hits", "float_from_bits"]
