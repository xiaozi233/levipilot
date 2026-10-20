"""Compare a patched Minecraft.Windows.exe with its .bak and describe the PE changes.

Read-only: mmaps both files, parses the PE headers, and reports which bytes differ
plus the entry point / import / TLS tables of each file.

Usage:
    py -3.11 driver/inspect_pe_patch.py --dir "C:\\...\\versions\\1.26.51.01"
"""

from __future__ import annotations

import argparse
import mmap
import struct
from pathlib import Path


def parse_pe(path: Path) -> dict:
    with path.open("rb") as f:
        data = f.read(0x2000)
    if data[:2] != b"MZ":
        raise SystemExit(f"{path}: not MZ")
    e = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e : e + 4] != b"PE\0\0":
        raise SystemExit(f"{path}: no PE signature")
    coff = e + 4
    machine, nsec, timestamp, _, _, optsize, chars = struct.unpack_from(
        "<HHIIIHH", data, coff
    )
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    is64 = magic == 0x20B
    entry = struct.unpack_from("<I", data, opt + 16)[0]
    image_base = struct.unpack_from("<Q" if is64 else "<I", data, opt + 24 if is64 else 28)[0]
    size_image = struct.unpack_from("<I", data, opt + 56)[0]
    checksum = struct.unpack_from("<I", data, opt + 64)[0]
    nrva = struct.unpack_from("<I", data, opt + 108)[0]
    dirs = []
    for i in range(min(nrva, 16)):
        rva, size = struct.unpack_from("<II", data, opt + 112 + i * 8)
        dirs.append((rva, size))
    sections = []
    sec = opt + optsize
    for i in range(nsec):
        off = sec + i * 40
        name = data[off : off + 8].rstrip(b"\0").decode("latin1")
        vsize, va, rawsize, raw = struct.unpack_from("<IIII", data, off + 8)
        schar = struct.unpack_from("<I", data, off + 36)[0]
        sections.append((name, va, vsize, raw, rawsize, schar))
    return {
        "machine": machine,
        "timestamp": timestamp,
        "characteristics": chars,
        "entry_rva": entry,
        "image_base": image_base,
        "size_image": size_image,
        "checksum": checksum,
        "dirs": dirs,
        "sections": sections,
        "is64": is64,
    }


def rva_to_off(rva: int, pe: dict) -> int | None:
    for name, va, vsize, raw, rawsize, _ in pe["sections"]:
        if va <= rva < va + max(vsize, rawsize):
            d = rva - va
            return raw + d if d < rawsize else None
    if rva < pe["sections"][0][1]:
        return rva
    return None


def cstr(mm, off: int, limit: int = 256) -> str:
    end = mm.find(b"\0", off, off + limit)
    if end < 0:
        return ""
    return bytes(mm[off:end]).decode("latin1", "replace")


def imports(path: Path, pe: dict) -> list[str]:
    if len(pe["dirs"]) < 2 or not pe["dirs"][1][0]:
        return []
    names = []
    with path.open("rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            off = rva_to_off(pe["dirs"][1][0], pe)
            if off is None:
                return ["<unmapped>"]
            for i in range(512):
                desc = off + i * 20
                ilt, ts, fc, name_rva, iat = struct.unpack_from("<IIIII", mm, desc)
                if not (ilt or ts or fc or name_rva or iat):
                    break
                no = rva_to_off(name_rva, pe)
                names.append(cstr(mm, no) if no is not None else f"<rva 0x{name_rva:X}>")
    return names


def tls_callbacks(path: Path, pe: dict) -> list[int]:
    if len(pe["dirs"]) < 10 or not pe["dirs"][9][0]:
        return []
    off = rva_to_off(pe["dirs"][9][0], pe)
    if off is None:
        return []
    with path.open("rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            start, end, index, callbacks = struct.unpack_from("<QQQQ", mm, off)
            if not callbacks:
                return []
            co = rva_to_off(callbacks - pe["image_base"], pe)
            out = []
            if co is not None:
                for i in range(64):
                    ptr = struct.unpack_from("<Q", mm, co + i * 8)[0]
                    if not ptr:
                        break
                    out.append(ptr)
            return out


def diff_runs(a: Path, b: Path, max_runs: int = 40, chunk: int = 1 << 20):
    """Yield (offset, length, sample_a, sample_b) for differing byte runs."""
    size_a, size_b = a.stat().st_size, b.stat().st_size
    n = min(size_a, size_b)
    runs = []
    run_start = None
    with a.open("rb") as fa, b.open("rb") as fb:
        ma = mmap.mmap(fa.fileno(), 0, access=mmap.ACCESS_READ)
        mb = mmap.mmap(fb.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            pos = 0
            while pos < n:
                step = min(chunk, n - pos)
                ba, bb = ma[pos : pos + step], mb[pos : pos + step]
                if ba == bb:
                    if run_start is not None:
                        runs.append((run_start, pos - run_start))
                        run_start = None
                        if len(runs) >= max_runs:
                            break
                    pos += step
                    continue
                for i in range(step):
                    if ba[i] != bb[i]:
                        if run_start is None:
                            run_start = pos + i
                    elif run_start is not None:
                        runs.append((run_start, pos + i - run_start))
                        run_start = None
                        if len(runs) >= max_runs:
                            break
                if len(runs) >= max_runs:
                    break
                pos += step
            if run_start is not None and len(runs) < max_runs:
                runs.append((run_start, n - run_start))
            tail = size_b - size_a
            if tail > 0 and len(runs) < max_runs:
                runs.append((n, tail))
            elif tail < 0:
                runs.append((n, tail))
        finally:
            ma.close()
            mb.close()
    return runs, size_a, size_b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="version directory containing exe and .bak")
    ap.add_argument("--exe", default="Minecraft.Windows.exe")
    ap.add_argument("--bak", default="Minecraft.Windows.exe.bak")
    ap.add_argument("--max-runs", type=int, default=40)
    args = ap.parse_args()

    d = Path(args.dir)
    exe, bak = d / args.exe, d / args.bak
    for p in (exe, bak):
        if not p.exists():
            raise SystemExit(f"missing: {p}")

    pe_new, pe_old = parse_pe(exe), parse_pe(bak)
    for label, pe in (("ORIGINAL (.bak)", pe_old), ("PATCHED (exe)", pe_new)):
        print(f"=== {label} ===")
        print(
            f"image_base=0x{pe['image_base']:X} size_of_image=0x{pe['size_image']:X} "
            f"entry_rva=0x{pe['entry_rva']:X} checksum=0x{pe['checksum']:08X}"
        )
        print(f"machine=0x{pe['machine']:04X} timestamp=0x{pe['timestamp']:08X}")
        for name, va, vsize, raw, rawsize, schar in pe["sections"]:
            print(
                f"  {name:8s} va=0x{va:08X} vsize=0x{vsize:08X} "
                f"raw=0x{raw:08X} rawsize=0x{rawsize:08X} flags=0x{schar:08X}"
            )
        dir_names = {
            0: "export", 1: "import", 2: "resource", 3: "exception", 4: "security",
            5: "reloc", 6: "debug", 9: "tls", 10: "load_config", 14: "clr",
        }
        for i, (rva, size) in enumerate(pe["dirs"]):
            if size:
                print(f"  dir[{i}] {dir_names.get(i, '?'):10s} rva=0x{rva:08X} size=0x{size:X}")
        imp = imports(exe if label.startswith("PATCHED") else bak, pe)
        print(f"  imports ({len(imp)}): {imp}")
        cb = tls_callbacks(exe if label.startswith("PATCHED") else bak, pe)
        if cb:
            print(f"  tls callbacks: {[hex(x) for x in cb]}")
        print()

    runs, sa, sb = diff_runs(bak, exe, args.max_runs)
    print(f"=== diff: .bak {sa:,} bytes -> exe {sb:,} bytes ({sb - sa:+,}) ===")
    with exe.open("rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            for off, length in runs[: args.max_runs]:
                tag = "APPENDED" if off >= sa else "CHANGED"
                chunk = bytes(mm[off : off + min(length, 64)])
                asc = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
                print(f"  {tag:8s} off=0x{off:08X} len={length:,}  {asc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())