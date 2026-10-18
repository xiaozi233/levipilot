"""Verify agent/agent.js signatures against the installed Minecraft.Windows.exe.

This does not attach to the game and does not need IDA. It parses the PE file
section table to map VAs to file offsets, then compares the first bytes of each
hooked function with the ``SIG`` table in the agent.

Usage:
    py -3.11 driver/check_binary.py
    py -3.11 driver/check_binary.py --exe "C:\\path\\Minecraft.Windows.exe"
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent" / "agent.js"
DEFAULT_VERSION = "1.26.45.01"


class PeError(RuntimeError):
    pass


def default_exe() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise PeError("APPDATA 未设置，请用 --exe 指定 Minecraft.Windows.exe")
    versions = Path(appdata) / "levilauncher.exe" / "versions"
    preferred = versions / DEFAULT_VERSION / "Minecraft.Windows.exe"
    if preferred.exists():
        return preferred
    candidates = sorted(versions.glob("*/Minecraft.Windows.exe"))
    if not candidates:
        raise PeError(f"{versions} 下找不到 Minecraft.Windows.exe")
    return candidates[-1]


def parse_pe(path: Path) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    """Return (image_base, size_of_image, sections[(va, vsize, raw, rawsize)])."""
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise PeError("不是 MZ 文件")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        raise PeError("PE 签名缺失")
    coff = e_lfanew + 4
    number_of_sections = struct.unpack_from("<H", data, coff + 2)[0]
    size_of_optional = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x20B:
        image_base = struct.unpack_from("<Q", data, opt + 24)[0]
    elif magic == 0x10B:
        image_base = struct.unpack_from("<I", data, opt + 28)[0]
    else:
        raise PeError(f"未知 optional header magic 0x{magic:04X}")
    size_of_image = struct.unpack_from("<I", data, opt + 56)[0]

    sections = []
    sec = opt + size_of_optional
    for i in range(number_of_sections):
        off = sec + i * 40
        vsize, va, rawsize, raw = struct.unpack_from("<IIII", data, off + 8)
        sections.append((va, vsize, raw, rawsize))
    return image_base, size_of_image, sections


def va_to_offset(va: int, image_base: int, size_of_image: int, sections) -> int:
    rva = va - image_base
    if rva < 0 or rva >= size_of_image:
        raise PeError(f"VA 0x{va:X} 超出 SizeOfImage")
    for va0, vsize, raw, rawsize in sections:
        span = max(vsize, rawsize)
        if va0 <= rva < va0 + span:
            delta = rva - va0
            if delta >= rawsize:
                raise PeError(f"VA 0x{va:X} 落在未初始化尾部（无文件后备）")
            return raw + delta
    # headers
    if rva < sections[0][0]:
        return rva
    raise PeError(f"VA 0x{va:X} 不属于任何 section")


def parse_js_tables(source: str) -> tuple[dict[str, int], dict[str, str]]:
    va_match = re.search(r"const VA = \{(.*?)\};", source, re.S)
    sig_match = re.search(r"const SIG = \{(.*?)\};", source, re.S)
    if not va_match or not sig_match:
        raise PeError("agent.js 里找不到 VA / SIG 表")
    va_table = {
        name: int(value, 0)
        for name, value in re.findall(r"(\w+):\s*(0x[0-9A-Fa-f]+)", va_match.group(1))
    }
    sig_table = {
        name: value
        for name, value in re.findall(r"(\w+):\s*'([0-9A-Fa-f ]+)'", sig_match.group(1))
    }
    return va_table, sig_table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", help="Minecraft.Windows.exe 路径")
    parser.add_argument("--agent", default=str(AGENT), help="agent/agent.js 路径")
    args = parser.parse_args(argv)

    exe = Path(args.exe) if args.exe else default_exe()
    try:
        data = exe.read_bytes()
    except OSError as exc:
        print(f"error: 无法读取 {exe}: {exc}", file=sys.stderr)
        print(
            "提示：IDA 打开 .i64 时可能锁住 EXE；先关掉数据库再跑本检查。",
            file=sys.stderr,
        )
        return 2

    try:
        image_base, size_of_image, sections = parse_pe(exe)
    except PeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    source = Path(args.agent).read_text(encoding="utf-8")
    try:
        va_table, sig_table = parse_js_tables(source)
    except PeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"exe          : {exe}")
    print(f"image base   : 0x{image_base:X}")
    print(f"size of image: 0x{size_of_image:X}")
    print(f"sections     : {len(sections)}")

    ok_count = 0
    for name, expected_hex in sig_table.items():
        expected = bytes(int(x, 16) for x in expected_hex.split())
        va = va_table.get(name)
        if va is None:
            print(f"  {name:<26} MISSING in VA table")
            continue
        try:
            offset = va_to_offset(va, image_base, size_of_image, sections)
        except PeError as exc:
            print(f"  {name:<26} 0x{va:X} ERROR {exc}")
            continue
        actual = data[offset : offset + len(expected)]
        ok = actual == expected
        ok_count += int(ok)
        print(
            f"  {name:<26} 0x{va:X} {'OK' if ok else 'MISMATCH'}"
            f"  file+0x{offset:X}  {actual.hex(' ')}"
        )

    total = len(sig_table)
    print(f"\nresult: {ok_count}/{total} signatures match")
    return 0 if ok_count == total and total > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
