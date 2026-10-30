"""Map a runtime VA to the PE section and function that contains it.

Watchpoints and hooks report absolute addresses; this turns "rip=+0x82a4890"
into "function 0x1482a4890..0x1482a4bc4" without starting IDA. Offline: it reads
the game binary from disk, never the running process.

    py -3.11 driver/pe_map.py 0x1431C35FF
    py -3.11 driver/pe_map.py --sections
    py -3.11 driver/pe_map.py 0x1482A4890 --exe <path>

The binary is discovered from `MCBE_EXE`, `--exe`, or the newest
`%APPDATA%\\levilauncher.exe\\versions\\*\\Minecraft.Windows.exe`.
"""

from __future__ import annotations

import argparse
import mmap
import os
import struct
from dataclasses import dataclass
from pathlib import Path

RUNTIME_FUNCTION = struct.Struct("<III")  # begin RVA, end RVA, unwind RVA


@dataclass(frozen=True)
class Section:
    name: str
    va: int
    vsize: int
    raw: int
    raw_size: int

    def covers(self, rva: int) -> bool:
        return self.va <= rva < self.va + self.vsize

    def offset_of(self, rva: int) -> int | None:
        if not self.covers(rva):
            return None
        offset = self.raw + (rva - self.va)
        return offset if rva - self.va < self.raw_size else None


def parse_headers(data: bytes | mmap.mmap) -> tuple[int, tuple[Section, ...]]:
    """Return ``(image_base, sections)`` from the PE headers."""
    if data[:2] != b"MZ":
        raise ValueError("not a PE file (missing MZ)")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        raise ValueError("not a PE file (missing PE signature)")
    section_count = struct.unpack_from("<H", data, e_lfanew + 6)[0]
    optional_size = struct.unpack_from("<H", data, e_lfanew + 20)[0]
    optional = e_lfanew + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic == 0x20B:  # PE32+
        image_base = struct.unpack_from("<Q", data, optional + 24)[0]
    elif magic == 0x10B:  # PE32
        image_base = struct.unpack_from("<I", data, optional + 28)[0]
    else:
        raise ValueError(f"unknown optional-header magic {magic:#x}")
    table = optional + optional_size
    sections = []
    for index in range(section_count):
        entry = table + index * 40
        name = data[entry : entry + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, va, raw_size, raw = struct.unpack_from("<IIII", data, entry + 8)
        sections.append(Section(name, va, vsize, raw, raw_size))
    return image_base, tuple(sections)


class PdataIndex:
    """Binary search over the ``.pdata`` RUNTIME_FUNCTION array (RVAs, sorted)."""

    def __init__(self, blob: bytes | mmap.mmap) -> None:
        self._blob = blob
        self._count = len(blob) // RUNTIME_FUNCTION.size

    def __len__(self) -> int:
        return self._count

    def function_at(self, rva: int) -> tuple[int, int] | None:
        low, high = 0, self._count - 1
        while low <= high:
            middle = (low + high) // 2
            begin, end, _ = RUNTIME_FUNCTION.unpack_from(self._blob, middle * 12)
            if begin == 0 or rva < begin:  # zero padding at the tail
                high = middle - 1
            elif rva >= end:
                low = middle + 1
            else:
                return begin, end
        return None


class PeImage:
    """A mapped PE file: sections, image base and the function table."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = path.open("rb")
        try:
            self._data = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        except Exception:
            self._file.close()
            raise
        self.image_base, self.sections = parse_headers(self._data)
        pdata = next((s for s in self.sections if s.name == ".pdata"), None)
        blob: bytes | mmap.mmap = b""
        if pdata is not None and pdata.raw_size:
            blob = self._data[pdata.raw : pdata.raw + pdata.raw_size]
        self.functions = PdataIndex(blob)

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self._data.close()
        self._file.close()

    def __enter__(self) -> PeImage:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- queries -----------------------------------------------------------
    def rva(self, va: int) -> int:
        return va - self.image_base

    def section_of(self, va: int) -> Section | None:
        rva = self.rva(va)
        return next((s for s in self.sections if s.covers(rva)), None)

    def offset_of(self, va: int) -> int | None:
        rva = self.rva(va)
        for section in self.sections:
            offset = section.offset_of(rva)
            if offset is not None:
                return offset
        return None

    def function_at(self, va: int) -> tuple[int, int] | None:
        found = self.functions.function_at(self.rva(va))
        if found is None:
            return None
        return self.image_base + found[0], self.image_base + found[1]

    def read(self, va: int, length: int = 16) -> bytes:
        offset = self.offset_of(va)
        if offset is None:
            return b""
        return self._data[offset : offset + length]


def default_exe() -> Path:
    """Newest ``Minecraft.Windows.exe`` under the launcher's versions directory."""
    explicit = os.environ.get("MCBE_EXE")
    if explicit:
        return Path(explicit)
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) / "levilauncher.exe" / "versions" if appdata else None
    candidates = sorted(root.glob("*/Minecraft.Windows.exe")) if root else []
    if not candidates:
        raise FileNotFoundError("no Minecraft.Windows.exe found; pass --exe or set MCBE_EXE")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def describe(image: PeImage, address: int) -> list[str]:
    rva = image.rva(address)
    section = image.section_of(address)
    lines = [f"{address:#x}"]
    if section is None:
        lines.append(f"  module base {image.image_base:#x}  (address outside the image)")
        return lines
    lines.append(f"  module  {image.image_base:#x}  section {section.name} (+{rva:#x})")
    found = image.function_at(address)
    if found is None:
        lines.append("  function <none in .pdata>")
    else:
        begin, end = found
        lines.append(
            f"  function {begin:#x}..{end:#x}  (size {end - begin:#x}, offset +{address - begin:#x})"
        )
    head = image.read(address, 8)
    if head:
        lines.append("  bytes   " + " ".join(f"{byte:02x}" for byte in head))
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("address", nargs="?", help="VA in hex (0x...) or decimal")
    parser.add_argument("--exe", type=Path, default=None, help="path to Minecraft.Windows.exe")
    parser.add_argument("--sections", action="store_true", help="print the section table")
    args = parser.parse_args()

    path = args.exe if args.exe is not None else default_exe()
    with PeImage(path) as image:
        if args.sections:
            print(f"{path}  base {image.image_base:#x}  functions {len(image.functions)}")
            for section in image.sections:
                print(
                    f"  {section.name:9s} va {section.va:#010x} vsize {section.vsize:#010x} "
                    f"raw {section.raw:#010x} rawsize {section.raw_size:#010x}"
                )
            return 0
        if args.address is None:
            parser.error("address is required unless --sections is given")
        address = int(args.address, 0)
        print("\n".join(describe(image, address)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
