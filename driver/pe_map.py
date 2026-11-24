"""Map a runtime VA to the PE section and function that contains it.

Watchpoints and hooks report absolute addresses; this turns "rip=+0x82a4890"
into "function 0x1482a4890..0x1482a4bc4" without starting IDA. Offline: it reads
the game binary from disk, never the running process.

    py -3.11 driver/pe_map.py 0x1431C35FF
    py -3.11 driver/pe_map.py --sections
    py -3.11 driver/pe_map.py --xrefs 0x151d6973c
    py -3.11 driver/pe_map.py --iat sinf
    py -3.11 driver/pe_map.py --import-xrefs sinf
    py -3.11 driver/pe_map.py --disasm 0x146667b10 -n 15
    py -3.11 driver/pe_map.py --rtti StyledWriter

The binary is discovered from `MCBE_EXE`, `--exe`, or the newest
`%APPDATA%\\levilauncher.exe\\versions\\*\\Minecraft.Windows.exe`.
"""

from __future__ import annotations

import argparse
import mmap
import os
import re
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from capstone import CS_ARCH_X86, CS_GRP_CALL, CS_GRP_JUMP, CS_MODE_64, Cs, CsInsn
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_REG_RIP

RUNTIME_FUNCTION = struct.Struct("<III")  # begin RVA, end RVA, unwind RVA
IMPORT_DESCRIPTOR = struct.Struct("<IIIII")  # OFT, TimeDateStamp, Forwarder, Name, FT
RTTI_COL = struct.Struct("<IIIIII")  # signature, offset, cdOffset, pTD, pCD, pSelf

_IMAGE_SCN_MEM_EXECUTE = 0x20000000
_RIP_TAILS = (0, 1, 2, 4)
_SCAN_CHUNK_U32 = 4 * 1024 * 1024  # 16 MiB per slice


@dataclass(frozen=True)
class Section:
    name: str
    va: int
    vsize: int
    raw: int
    raw_size: int
    characteristics: int = 0

    @property
    def is_executable(self) -> bool:
        return self.name == ".text" or bool(self.characteristics & _IMAGE_SCN_MEM_EXECUTE)

    def covers(self, rva: int) -> bool:
        return self.va <= rva < self.va + self.vsize

    def offset_of(self, rva: int) -> int | None:
        if not self.covers(rva):
            return None
        offset = self.raw + (rva - self.va)
        return offset if rva - self.va < self.raw_size else None


def _format_func_label(func_begin: int | None, func_end: int | None) -> str:
    if func_begin is None or func_end is None:
        return "<leaf>"
    return f"func {func_begin:#x}..{func_end:#x}"


@dataclass(frozen=True)
class RipXref:
    insn_va: int
    func_begin: int | None
    func_end: int | None
    text: str

    @property
    def function(self) -> tuple[int, int] | None:
        if self.func_begin is None or self.func_end is None:
            return None
        return (self.func_begin, self.func_end)

    @property
    def func_label(self) -> str:
        return _format_func_label(self.func_begin, self.func_end)


@dataclass(frozen=True)
class ImportEntry:
    dll: str
    symbol: str
    slot_va: int
    ordinal: int | None = None


@dataclass(frozen=True)
class ImportXref:
    entry: ImportEntry
    insn_va: int
    func_begin: int | None
    func_end: int | None
    text: str

    @property
    def function(self) -> tuple[int, int] | None:
        if self.func_begin is None or self.func_end is None:
            return None
        return (self.func_begin, self.func_end)

    @property
    def func_label(self) -> str:
        return _format_func_label(self.func_begin, self.func_end)


@dataclass(frozen=True)
class RttiResult:
    name: str
    mangled: str
    vtable_va: int
    col_va: int
    type_desc_va: int
    methods: tuple[int, ...]
    xrefs: tuple[RipXref, ...]


@dataclass(frozen=True)
class EcsComponentInfo:
    hash_id: int
    name: str
    desc_va: int
    func_span: tuple[int, int] | None

    @property
    def func_label(self) -> str:
        return _format_func_label(*self.func_span) if self.func_span is not None else ".text"


@dataclass(frozen=True)
class _PeLayout:
    image_base: int
    sections: tuple[Section, ...]
    is_pe32_plus: bool
    import_dir_rva: int
    import_dir_size: int


def _parse_pe_layout(data: bytes | mmap.mmap) -> _PeLayout:
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
        is_pe32_plus = True
        image_base = struct.unpack_from("<Q", data, optional + 24)[0]
        num_rva_off, imp_dir_off = 108, 120
    elif magic == 0x10B:  # PE32
        is_pe32_plus = False
        image_base = struct.unpack_from("<I", data, optional + 28)[0]
        num_rva_off, imp_dir_off = 92, 104
    else:
        raise ValueError(f"unknown optional-header magic {magic:#x}")

    import_dir_rva = 0
    import_dir_size = 0
    if optional_size >= imp_dir_off + 8:
        num_rva = struct.unpack_from("<I", data, optional + num_rva_off)[0]
        if num_rva >= 2:
            import_dir_rva, import_dir_size = struct.unpack_from(
                "<II", data, optional + imp_dir_off
            )

    table = optional + optional_size
    sections: list[Section] = []
    for index in range(section_count):
        entry = table + index * 40
        name = data[entry : entry + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, va, raw_size, raw = struct.unpack_from("<IIII", data, entry + 8)
        chars = struct.unpack_from("<I", data, entry + 36)[0]
        sections.append(Section(name, va, vsize, raw, raw_size, chars))
    return _PeLayout(image_base, tuple(sections), is_pe32_plus, import_dir_rva, import_dir_size)


def parse_headers(data: bytes | mmap.mmap) -> tuple[int, tuple[Section, ...]]:
    """Return ``(image_base, sections)`` from the PE headers."""
    layout = _parse_pe_layout(data)
    return layout.image_base, layout.sections


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

    def prev_function_end(self, rva: int) -> int:
        """Return the ``end`` RVA of the nearest preceding ``.pdata`` function (or 0)."""
        low, high = 0, self._count - 1
        best = 0
        while low <= high:
            middle = (low + high) // 2
            begin, end, _ = RUNTIME_FUNCTION.unpack_from(self._blob, middle * 12)
            if begin == 0 or rva < begin:
                high = middle - 1
            elif rva >= end:
                if end > best:
                    best = end
                low = middle + 1
            else:
                return begin
        return best


_MSVC_PRIMITIVES = {
    "C": "signed char",
    "D": "char",
    "E": "unsigned char",
    "F": "short",
    "G": "unsigned short",
    "H": "int",
    "I": "unsigned int",
    "J": "long",
    "K": "unsigned long",
    "M": "float",
    "N": "double",
    "O": "long double",
    "X": "void",
    "_N": "bool",
    "_J": "int64_t",
    "_K": "uint64_t",
    "_W": "wchar_t",
    "_S": "char16_t",
    "_U": "char32_t",
    "_Q": "char8_t",
}


def demangle_rtti_name(mangled: str) -> str:
    """Decode an MSVC RTTI TypeDescriptor name (``.?AV...@@`` / ``.?AU...@@``) to C++ syntax."""
    if not mangled.startswith(".?A"):
        return mangled
    if mangled.startswith(".?AW4"):
        pos = 5
    elif len(mangled) >= 4 and mangled[3] in ("V", "U", "T"):
        pos = 4
    else:
        return mangled

    n = len(mangled)
    backrefs: list[str] = []

    def read_name_part(i: int, br: list[str]) -> tuple[str, int]:
        if i < n and mangled[i].isdigit():
            idx = int(mangled[i])
            return (br[idx] if idx < len(br) else mangled[i]), i + 1
        if mangled.startswith("?$", i):
            t_name, j = parse_template(i + 2)
            if len(br) < 10:
                br.append(t_name)
            return t_name, j
        if mangled.startswith("?A", i):
            end = mangled.find("@", i)
            if end == -1:
                return mangled[i:], n
            anon = "'anonymous namespace'"
            if len(br) < 10:
                br.append(anon)
            return anon, end + 1
        end = mangled.find("@", i)
        if end == -1:
            return mangled[i:], n
        part = mangled[i:end]
        if len(br) < 10:
            br.append(part)
        return part, end + 1

    def parse_qual_name(i: int, br: list[str]) -> tuple[str, int]:
        parts: list[str] = []
        while i < n and mangled[i] != "@":
            part, i = read_name_part(i, br)
            parts.append(part)
        if i < n and mangled[i] == "@":
            i += 1
        return "::".join(reversed(parts)), i

    def parse_template(i: int) -> tuple[str, int]:
        tbr: list[str] = []
        end = mangled.find("@", i)
        if end == -1:
            return mangled[i:], n
        tname = mangled[i:end]
        tbr.append(tname)
        i = end + 1
        args: list[str] = []
        while i < n and mangled[i] != "@":
            arg, i = parse_type(i, tbr)
            args.append(arg)
        if i < n and mangled[i] == "@":
            i += 1
        return f"{tname}<{', '.join(args)}>", i

    def parse_type(i: int, br: list[str]) -> tuple[str, int]:
        if i >= n:
            return "", i
        if mangled[i] in ("V", "U", "T"):
            return parse_qual_name(i + 1, br)
        if mangled.startswith("W4", i):
            return parse_qual_name(i + 2, br)
        if mangled.startswith(("$$A6", "$$A8"), i):
            end = mangled.find("@Z", i)
            if end != -1:
                return "func(...)", end + 2
        for prefix, fmt in (
            ("PEAV", "{0}*"),
            ("PEAU", "{0}*"),
            ("PEBV", "const {0}*"),
            ("PEBU", "const {0}*"),
            ("AEAV", "{0}&"),
            ("AEAU", "{0}&"),
            ("AEBV", "const {0}&"),
            ("AEBU", "const {0}&"),
        ):
            if mangled.startswith(prefix, i):
                inner, j = parse_qual_name(i + 4, br)
                return fmt.format(inner), j
        if mangled[i] == "_" and i + 1 < n and mangled[i : i + 2] in _MSVC_PRIMITIVES:
            return _MSVC_PRIMITIVES[mangled[i : i + 2]], i + 2
        if mangled[i] in _MSVC_PRIMITIVES:
            return _MSVC_PRIMITIVES[mangled[i]], i + 1
        if mangled.startswith("$0", i):
            end = mangled.find("@", i)
            if end != -1:
                return mangled[i + 2 : end], end + 1
        if mangled[i].isdigit():
            idx = int(mangled[i])
            return (br[idx] if idx < len(br) else mangled[i]), i + 1
        end = mangled.find("@", i)
        if end == -1:
            return mangled[i:], n
        return mangled[i:end], end + 1

    try:
        decoded, _ = parse_qual_name(pos, backrefs)
        return decoded or mangled
    except Exception:
        return mangled


def _make_disassembler(*, detail: bool) -> Cs:
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = detail
    return md


def _ascii_preview(raw: bytes, max_chars: int = 48) -> str | None:
    """Return printable ASCII preview if ``raw`` starts with a C-string or ASCII run."""
    nul = raw.find(0)
    slice_bytes = raw[:nul] if nul != -1 else raw
    min_len = 3 if nul != -1 else 8
    if len(slice_bytes) < min_len:
        return None
    if not all(0x20 <= b <= 0x7E or b in (9, 10, 13) for b in slice_bytes):
        return None
    text = slice_bytes.decode("ascii")
    if len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def _format_insn(address: int, mnemonic: str, op_str: str) -> str:
    if op_str:
        return f"{address:#x}: {mnemonic} {op_str}"
    return f"{address:#x}: {mnemonic}"


class PeImage:
    """A mapped PE file: sections, image base, functions, imports, xrefs, and RTTI."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = path.open("rb")
        try:
            self._data = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        except Exception:
            self._file.close()
            raise
        layout = _parse_pe_layout(self._data)
        self.image_base = layout.image_base
        self.sections = layout.sections
        self._is_pe32_plus = layout.is_pe32_plus
        self._import_dir_rva = layout.import_dir_rva
        self._import_dir_size = layout.import_dir_size
        pdata = next((s for s in self.sections if s.name == ".pdata"), None)
        blob: bytes | mmap.mmap = b""
        if pdata is not None and pdata.raw_size:
            blob = self._data[pdata.raw : pdata.raw + pdata.raw_size]
        self.functions = PdataIndex(blob)
        self._imports_cache: tuple[ImportEntry, ...] | None = None
        self._iat_map_cache: dict[int, ImportEntry] | None = None
        self._ecs_cache: list[EcsComponentInfo] | None = None
        self._md_fast: Cs | None = None
        self._md_detail: Cs | None = None

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self._data.close()
        self._file.close()

    def __enter__(self) -> PeImage:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- basic queries -----------------------------------------------------
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

    def find_func(self, va: int) -> tuple[int, int] | None:
        """Alias for ``function_at(va)``."""
        return self.function_at(va)

    def section_va(self, section: Section) -> int:
        """Return the virtual address of ``section`` (``image_base + section.va``)."""
        return self.image_base + section.va

    def read(self, va: int, length: int = 16) -> bytes:
        rva = self.rva(va)
        for section in self.sections:
            offset = section.offset_of(rva)
            if offset is not None:
                avail = (section.raw + section.raw_size) - offset
                return self._data[offset : offset + min(length, avail)]
        return b""

    def _read_cstr_rva(self, rva: int, max_len: int = 4096) -> str:
        offset = self.offset_of(self.image_base + rva)
        if offset is None:
            return ""
        end = self._data.find(b"\0", offset, min(len(self._data), offset + max_len))
        if end == -1:
            return ""
        return self._data[offset:end].decode("ascii", "replace")

    def _disasm_fast(self) -> Cs:
        if self._md_fast is None:
            self._md_fast = _make_disassembler(detail=False)
        return self._md_fast

    def _disasm_detail(self) -> Cs:
        if self._md_detail is None:
            self._md_detail = _make_disassembler(detail=True)
        return self._md_detail

    # -- imports & IAT -----------------------------------------------------
    def parse_imports(self) -> list[ImportEntry]:
        """Parse PE ``IMAGE_DIRECTORY_ENTRY_IMPORT`` into a list of :class:`ImportEntry`."""
        if self._imports_cache is not None:
            return list(self._imports_cache)
        if self._import_dir_rva == 0:
            self._imports_cache = ()
            self._iat_map_cache = {}
            return []
        dir_off = self.offset_of(self.image_base + self._import_dir_rva)
        if dir_off is None:
            self._imports_cache = ()
            self._iat_map_cache = {}
            return []

        ptr_size = 8 if self._is_pe32_plus else 4
        ptr_fmt = "<Q" if self._is_pe32_plus else "<I"
        ordinal_flag = 1 << (63 if self._is_pe32_plus else 31)
        data_len = len(self._data)
        entries: list[ImportEntry] = []

        desc_index = 0
        while dir_off + (desc_index + 1) * IMPORT_DESCRIPTOR.size <= data_len:
            orig_ft, time_stamp, forwarder, name_rva, first_thunk = IMPORT_DESCRIPTOR.unpack_from(
                self._data, dir_off + desc_index * IMPORT_DESCRIPTOR.size
            )
            desc_index += 1
            if not (orig_ft or time_stamp or forwarder or name_rva or first_thunk):
                break
            if name_rva == 0 or first_thunk == 0:
                continue
            dll = self._read_cstr_rva(name_rva)
            lookup_rva = orig_ft if orig_ft != 0 else first_thunk
            if self.offset_of(self.image_base + lookup_rva) is None:
                lookup_rva = first_thunk
            slot_index = 0
            while True:
                thunk_off = self.offset_of(self.image_base + lookup_rva + slot_index * ptr_size)
                if thunk_off is None or thunk_off + ptr_size > data_len:
                    break
                val = struct.unpack_from(ptr_fmt, self._data, thunk_off)[0]
                if val == 0:
                    break
                slot_va = self.image_base + first_thunk + slot_index * ptr_size
                if val & ordinal_flag:
                    ord_num = val & 0xFFFF
                    entries.append(ImportEntry(dll, f"ordinal#{ord_num}", slot_va, ord_num))
                else:
                    sym = self._read_cstr_rva((val & 0x7FFFFFFF) + 2)
                    entries.append(ImportEntry(dll, sym, slot_va, None))
                slot_index += 1

        self._imports_cache = tuple(entries)
        self._iat_map_cache = {entry.slot_va: entry for entry in entries}
        return entries

    def _iat_map(self) -> dict[int, ImportEntry]:
        if self._iat_map_cache is None:
            self.parse_imports()
        assert self._iat_map_cache is not None
        return self._iat_map_cache

    # -- RIP-relative xref scanner -----------------------------------------
    def _insn_matches_targets(self, insn: CsInsn, targets: set[int]) -> int | None:
        """If ``insn`` has a RIP-relative or relative-branch operand targeting ``targets``, return it."""
        is_branch = insn.group(CS_GRP_CALL) or insn.group(CS_GRP_JUMP)
        for op in insn.operands:
            if op.type == X86_OP_MEM and op.mem.base == X86_REG_RIP:
                target = insn.address + insn.size + op.mem.disp
                if target in targets:
                    return int(target)
            elif is_branch and op.type == X86_OP_IMM:
                target = int(op.imm)
                if target in targets:
                    return target
        return None

    def _validate_rip_candidate(
        self,
        blob: memoryview,
        text_va: int,
        scan_len: int,
        off: int,
        tail: int,
        targets: set[int],
    ) -> tuple[int, RipXref] | None:
        disp_va = text_va + off
        expected_end = disp_va + 4 + tail
        fn = self.function_at(disp_va)
        md_fast = self._disasm_fast()
        md_detail = self._disasm_detail()

        def try_linear_sweep(start_off: int) -> tuple[bool, int | None, int | None]:
            """Sweep from ``start_off``; return ``(decided, insn_off, insn_size)``."""
            code = bytes(blob[start_off : min(scan_len, off + 4 + tail)])
            last_end = text_va + start_off
            for addr, size, _mnem, _op in md_fast.disasm_lite(code, text_va + start_off):
                last_end = addr + size
                if addr <= disp_va < last_end:
                    if last_end == expected_end:
                        return True, addr - text_va, size
                    return True, None, None
            return False, None, None

        decided = False
        insn_off: int | None = None
        insn_size: int | None = None

        if fn is not None and (disp_va - fn[0]) <= 0x40000:
            decided, insn_off, insn_size = try_linear_sweep(fn[0] - text_va)
        elif fn is None:
            sync_rva = (
                self.functions.prev_function_end(self.rva(disp_va)) if len(self.functions) else 0
            )
            sync_va = self.image_base + sync_rva if sync_rva else text_va
            if sync_va < text_va:
                sync_va = text_va
            if 0 <= (disp_va - sync_va) <= 0x10000:
                decided, insn_off, insn_size = try_linear_sweep(sync_va - text_va)

        if not decided:
            # Try syncing from a nearby 0xCC padding run, else longest valid decode in window.
            win_lo = max(0, off - 2048)
            raw_win = bytes(blob[win_lo:off])
            cc_pos = raw_win.rfind(b"\xcc\xcc")
            if cc_pos != -1:
                decided, insn_off, insn_size = try_linear_sweep(win_lo + cc_pos + 2)

        if insn_off is not None and insn_size is not None:
            single = next(
                md_detail.disasm(
                    bytes(blob[insn_off : insn_off + insn_size]), text_va + insn_off, count=1
                ),
                None,
            )
            if single is not None:
                matched_target = self._insn_matches_targets(single, targets)
                if matched_target is not None:
                    text_str = (
                        f"{single.mnemonic} {single.op_str}" if single.op_str else single.mnemonic
                    )
                    fn_begin, fn_end = fn if fn is not None else (None, None)
                    return matched_target, RipXref(single.address, fn_begin, fn_end, text_str)
            return None

        if decided:
            return None

        # Last-resort local window fallback (handles binaries without .pdata or embedded data).
        for start_off in range(max(0, off - 11), off + 1):
            code = bytes(blob[start_off : min(scan_len, off + 4 + tail + 1)])
            single = next(md_detail.disasm(code, text_va + start_off, count=1), None)
            if single is None or single.address + single.size != expected_end:
                continue
            matched_target = self._insn_matches_targets(single, targets)
            if matched_target is not None:
                text_str = (
                    f"{single.mnemonic} {single.op_str}" if single.op_str else single.mnemonic
                )
                fn_begin, fn_end = fn if fn is not None else (None, None)
                return matched_target, RipXref(single.address, fn_begin, fn_end, text_str)
        return None

    def _scan_rip_xrefs(self, target_vas: Sequence[int]) -> dict[int, list[RipXref]]:
        """Scan ``.text`` once for RIP-relative / relative-branch xrefs to ``target_vas``."""
        unique_targets = sorted(set(target_vas))
        results: dict[int, list[RipXref]] = {t: [] for t in unique_targets}
        if not unique_targets:
            return results

        text = next((s for s in self.sections if s.name == ".text"), None)
        if text is None or text.raw_size < 4:
            return results
        scan_len = min(text.raw_size, text.vsize) if text.vsize > 0 else text.raw_size
        if scan_len < 4:
            return results

        text_va = self.image_base + text.va
        min_va = text_va - 0x80000000
        max_va = text_va + scan_len + 0x7FFFFFFF
        reachable = [t for t in unique_targets if min_va <= t <= max_va]
        if not reachable:
            return results

        k_list = [t - text_va - 4 for t in reachable]
        k_lookup = set(k_list)
        target_set = set(reachable)
        candidates: list[tuple[int, int]] = []

        idx_buf = np.arange(0, _SCAN_CHUNK_U32 * 4, 4, dtype=np.int32)
        work = np.empty(_SCAN_CHUNK_U32, dtype=np.int32)
        uwork = work.view(np.uint32)
        scratch = np.empty(_SCAN_CHUNK_U32, dtype=np.uint32) if len(k_list) > 1 else None
        mask = np.empty(_SCAN_CHUNK_U32, dtype=np.bool_)

        def record_hits(arr: np.ndarray, base_off: int, align: int, indices: Sequence[int]) -> None:
            for h in indices:
                off = base_off + align + h * 4
                val_sum = int(arr[h]) + off
                for tail in _RIP_TAILS:
                    if (val_sum + tail) in k_lookup:
                        candidates.append((off, tail))

        blob = memoryview(self._data)[text.raw : text.raw + scan_len]
        try:
            for base_off in range(0, scan_len - 3, _SCAN_CHUNK_U32 * 4):
                end_off = min(scan_len, base_off + _SCAN_CHUNK_U32 * 4 + 3)
                seg = blob[base_off:end_off]
                try:
                    for align in range(4):
                        cnt = (len(seg) - align) // 4
                        if cnt <= 0:
                            continue
                        arr = np.frombuffer(seg, dtype="<i4", count=cnt, offset=align)
                        try:
                            w = work[:cnt]
                            uw = uwork[:cnt]
                            m = mask[:cnt]
                            np.add(arr, idx_buf[:cnt], out=w)

                            if len(k_list) == 1:
                                k0 = k_list[0]
                                uw -= np.uint32((k0 - base_off - align - 4) & 0xFFFFFFFF)
                                np.less_equal(uw, 4, out=m)
                                for h in np.flatnonzero(m):
                                    off = base_off + align + int(h) * 4
                                    tail = k0 - (int(arr[h]) + off)
                                    if tail in _RIP_TAILS:
                                        candidates.append((off, tail))
                            elif len(k_list) <= 16:
                                assert scratch is not None
                                sc = scratch[:cnt]
                                hit_indices: set[int] = set()
                                for k_val in k_list:
                                    np.subtract(
                                        uw,
                                        np.uint32((k_val - base_off - align - 4) & 0xFFFFFFFF),
                                        out=sc,
                                    )
                                    np.less_equal(sc, 4, out=m)
                                    if np.any(m):
                                        hit_indices.update(int(h) for h in np.flatnonzero(m))
                                if hit_indices:
                                    record_hits(arr, base_off, align, sorted(hit_indices))
                            else:
                                needed_i4 = np.array(
                                    [
                                        ((kv - base_off - align - tail) & 0xFFFFFFFF)
                                        for kv in k_list
                                        for tail in _RIP_TAILS
                                    ],
                                    dtype=np.uint32,
                                ).view(np.int32)
                                hit_mask = np.isin(w, needed_i4)
                                if np.any(hit_mask):
                                    record_hits(
                                        arr,
                                        base_off,
                                        align,
                                        [int(h) for h in np.flatnonzero(hit_mask)],
                                    )
                        finally:
                            del arr
                finally:
                    seg.release()

            seen_insns: set[tuple[int, int]] = set()
            for off, tail in sorted(candidates):
                validated = self._validate_rip_candidate(
                    blob, text_va, scan_len, off, tail, target_set
                )
                if validated is not None:
                    matched_target, xref = validated
                    key = (matched_target, xref.insn_va)
                    if key not in seen_insns:
                        seen_insns.add(key)
                        results[matched_target].append(xref)
        finally:
            blob.release()
            del idx_buf, work, uwork, scratch, mask

        for target in results:
            results[target].sort(key=lambda x: x.insn_va)
        return results

    def find_rip_xrefs(self, target_va: int) -> list[RipXref]:
        """Scan ``.text`` for RIP-relative and branch xrefs targeting ``target_va``."""
        return self._scan_rip_xrefs([target_va]).get(target_va, [])

    def find_import_xrefs(self, symbol: str) -> list[ImportXref]:
        """Find ``.text`` xrefs to IAT slot(s) matching ``symbol`` (exact, then substring)."""
        if not symbol:
            return []
        imports = self.parse_imports()
        matched = [entry for entry in imports if entry.symbol == symbol]
        if not matched:
            sym_lower = symbol.lower()
            matched = [entry for entry in imports if entry.symbol.lower() == sym_lower]
        if not matched:
            sym_lower = symbol.lower()
            matched = [entry for entry in imports if sym_lower in entry.symbol.lower()]
        if not matched:
            return []

        slot_to_entry = {entry.slot_va: entry for entry in matched}
        by_slot = self._scan_rip_xrefs(list(slot_to_entry))
        out: list[ImportXref] = []
        for slot_va, xrefs in by_slot.items():
            entry = slot_to_entry[slot_va]
            for xref in xrefs:
                out.append(
                    ImportXref(
                        entry=entry,
                        insn_va=xref.insn_va,
                        func_begin=xref.func_begin,
                        func_end=xref.func_end,
                        text=xref.text,
                    )
                )
        out.sort(key=lambda item: item.insn_va)
        return out

    # -- disassembly with annotation ---------------------------------------
    def _annotate_target(self, target: int) -> str:
        iat = self._iat_map().get(target)
        if iat is not None:
            return f"{target:#x} IAT {iat.dll}!{iat.symbol}"

        section = self.section_of(target)
        if section is None:
            return f"{target:#x} (outside image)"

        if section.is_executable:
            found = self.function_at(target)
            if found is not None:
                begin, end = found
                if target == begin:
                    return f"{target:#x} func {begin:#x}..{end:#x}"
                return f"{target:#x} func {begin:#x}..{end:#x} (+{target - begin:#x})"
            return f"{target:#x} {section.name} <leaf> (+{self.rva(target):#x})"

        raw = self.read(target, 64)
        if not raw:
            return f"{target:#x} {section.name} (+{self.rva(target):#x}, bss)"

        parts = [section.name]
        if len(raw) >= 4:
            u32_val = struct.unpack_from("<I", raw, 0)[0]
            f32_val = struct.unpack_from("<f", raw, 0)[0]
            parts.append(f"f32={f32_val:.6g}")
        if len(raw) >= 8:
            f64_val = struct.unpack_from("<d", raw, 0)[0]
            parts.append(f"f64={f64_val:.6g}")
        if len(raw) >= 4:
            parts.append(f"u32={u32_val:#010x}")
        else:
            parts.append(f"bytes={raw.hex()}")
        preview = _ascii_preview(raw)
        if preview is not None:
            parts.append(f"str={preview!r}")
        return f"{target:#x} " + " ".join(parts)

    def _format_disasm_insn(self, insn: CsInsn) -> str:
        target: int | None = None
        for op in insn.operands:
            if op.type == X86_OP_MEM and op.mem.base == X86_REG_RIP:
                target = insn.address + insn.size + op.mem.disp
                break
            if op.type == X86_OP_IMM and insn.mnemonic in ("call", "jmp"):
                target = int(op.imm)
                break
        line = _format_insn(insn.address, insn.mnemonic, insn.op_str)
        if target is not None:
            line = f"{line}  ; {self._annotate_target(target)}"
        return line

    def disassemble(self, va: int, count: int = 40) -> list[str]:
        """Disassemble ``count`` instructions from ``va`` with target annotations."""
        if count <= 0:
            return []
        code = self.read(va, count * 15)
        if not code:
            return []

        md = self._disasm_detail()
        lines: list[str] = []
        for idx, insn in enumerate(md.disasm(code, va)):
            if idx >= count:
                break
            lines.append(self._format_disasm_insn(insn))
        if not lines:
            span = self.function_at(va)
            if span is not None and span[0] < va < span[1]:
                return self.disassemble_function(va, max_insns=count, from_va=va)
        return lines

    def disassemble_function(
        self,
        va: int,
        *,
        max_insns: int = 2000,
        from_va: int | None = None,
    ) -> list[str]:
        """Disassemble the ``.pdata`` function containing ``va`` (optionally from ``from_va``)."""
        if max_insns <= 0:
            return []
        span = self.function_at(va)
        if span is None:
            return []
        begin, end = span
        code = self.read(begin, max(0, end - begin))
        if not code:
            return []

        md = self._disasm_detail()
        lines: list[str] = []
        for insn in md.disasm(code, begin):
            if insn.address >= end or len(lines) >= max_insns:
                break
            if from_va is not None and insn.address + insn.size <= from_va:
                continue
            lines.append(self._format_disasm_insn(insn))
        return lines

    def find_bytes(
        self,
        pattern: bytes,
        *,
        section: str | None = None,
        limit: int = 64,
    ) -> list[tuple[int, tuple[int, int] | None]]:
        """Scan sections (default ``.text`` and ``.rdata``) for byte ``pattern``."""
        if not pattern or limit <= 0:
            return []
        sec_names = (section,) if section is not None else (".text", ".rdata")
        hits: list[tuple[int, tuple[int, int] | None]] = []
        for sec in self.sections:
            if sec.name not in sec_names or sec.raw_size < len(pattern):
                continue
            start = sec.raw
            end = sec.raw + sec.raw_size
            pos = start
            while pos < end and len(hits) < limit:
                idx = self._data.find(pattern, pos, end)
                if idx == -1:
                    break
                va = self.image_base + sec.va + (idx - start)
                hits.append((va, self.function_at(va)))
                pos = idx + 1
            if len(hits) >= limit:
                break
        return hits

    def find_u32(
        self,
        value: int,
        *,
        section: str | None = None,
        limit: int = 64,
    ) -> list[tuple[int, tuple[int, int] | None]]:
        """Scan for little-endian 32-bit unsigned integer ``value``."""
        return self.find_bytes(struct.pack("<I", value & 0xFFFFFFFF), section=section, limit=limit)

    def find_f32(
        self,
        value: float,
        *,
        section: str | None = None,
        limit: int = 64,
    ) -> list[tuple[int, tuple[int, int] | None]]:
        """Scan for IEEE-754 little-endian float32 ``value``."""
        return self.find_bytes(struct.pack("<f", float(value)), section=section, limit=limit)

    def find_string(
        self,
        substring: str,
        *,
        section: str = ".rdata",
        ignore_case: bool = False,
        limit: int = 64,
    ) -> list[tuple[int, str]]:
        """Find printable ASCII/UTF-8 strings containing ``substring`` in ``section``."""
        if not substring or limit <= 0:
            return []
        needle = substring.encode("utf-8")
        needle_lower = needle.lower() if ignore_case else needle
        sec = next((s for s in self.sections if s.name == section), None)
        if sec is None or sec.raw_size == 0:
            return []
        start = sec.raw
        end = sec.raw + sec.raw_size
        haystack = bytes(self._data[start:end]).lower() if ignore_case else self._data[start:end]
        results: list[tuple[int, str]] = []
        seen_starts: set[int] = set()
        pos = 0
        while pos < len(haystack) and len(results) < limit:
            rel = haystack.find(needle_lower, pos)
            if rel == -1:
                break
            abs_pos = start + rel
            s = abs_pos
            while s > start and 0x20 <= self._data[s - 1] < 0x7F:
                s -= 1
            e = abs_pos + len(needle)
            while e < end and 0x20 <= self._data[e] < 0x7F:
                e += 1
            if s not in seen_starts:
                seen_starts.add(s)
                va = self.image_base + sec.va + (s - start)
                results.append((va, self._data[s:e].decode("utf-8", "replace")))
            pos = rel + len(needle_lower)
        return results

    # -- MSVC RTTI ---------------------------------------------------------
    def _read_vtable_methods(self, vtable_va: int, limit: int = 6) -> tuple[int, ...]:
        methods: list[int] = []
        for index in range(limit):
            raw = self.read(vtable_va + index * 8, 8)
            if len(raw) < 8:
                break
            ptr = struct.unpack("<Q", raw)[0]
            if ptr == 0:
                break
            section = self.section_of(ptr)
            if section is None or not section.is_executable:
                break
            methods.append(ptr)
        return tuple(methods)

    def _find_rtti_by_vtable(self, vtable_va: int) -> list[RttiResult]:
        col_ptr_bytes = self.read(vtable_va - 8, 8)
        if len(col_ptr_bytes) < 8:
            return []
        col_va = struct.unpack("<Q", col_ptr_bytes)[0]
        col_bytes = self.read(col_va, RTTI_COL.size)
        if len(col_bytes) < RTTI_COL.size:
            return []
        sig, _off, _cd_off, p_type_desc, _p_class_desc, p_self = RTTI_COL.unpack(col_bytes)
        if sig != 1 or p_type_desc == 0:
            return []
        if p_self != 0 and p_self != self.rva(col_va):
            return []

        type_desc_va = self.image_base + p_type_desc
        mangled = self._read_cstr_rva(p_type_desc + 16)
        if not mangled.startswith(".?A"):
            return []
        name = demangle_rtti_name(mangled)
        methods = self._read_vtable_methods(vtable_va, limit=6)
        xrefs = tuple(self.find_rip_xrefs(vtable_va))
        return [
            RttiResult(
                name=name,
                mangled=mangled,
                vtable_va=vtable_va,
                col_va=col_va,
                type_desc_va=type_desc_va,
                methods=methods,
                xrefs=xrefs,
            )
        ]

    def _find_rtti_by_name(
        self,
        substring: str,
        *,
        allow_empty: bool = False,
        include_xrefs: bool = True,
    ) -> list[RttiResult]:
        if not substring and not allow_empty:
            return []
        q_lower = substring.lower()
        td_matches: dict[int, tuple[str, str]] = {}

        for sec_name in (".data", ".rdata"):
            sec = next((s for s in self.sections if s.name == sec_name), None)
            if sec is None or sec.raw_size <= 16:
                continue
            start = sec.raw
            end = sec.raw + sec.raw_size
            pos = start + 16
            while pos < end:
                hit = self._data.find(b".?A", pos, end)
                if hit == -1:
                    break
                if self._data[hit - 1] == 0:
                    nul = self._data.find(b"\0", hit, min(end, hit + 4096))
                    if nul != -1:
                        mangled = self._data[hit:nul].decode("ascii", "replace")
                        demangled = demangle_rtti_name(mangled)
                        if (
                            not q_lower
                            or q_lower in mangled.lower()
                            or q_lower in demangled.lower()
                        ):
                            td_rva = sec.va + (hit - sec.raw) - 16
                            td_matches[td_rva] = (mangled, demangled)
                pos = hit + 3

        if not td_matches:
            return []

        rdata = next((s for s in self.sections if s.name == ".rdata"), None)
        if rdata is None or rdata.raw_size < RTTI_COL.size:
            return []

        col_to_td: dict[int, int] = {}
        mv = memoryview(self._data)[rdata.raw : rdata.raw + rdata.raw_size]
        try:
            u4_count = rdata.raw_size // 4
            arr4 = np.frombuffer(mv, dtype="<u4", count=u4_count)
            try:
                sig_hits = np.flatnonzero(arr4[:-5] == 1)
                td_rva_set = set(td_matches)
                for idx_np in sig_hits:
                    i = int(idx_np)
                    ptd = int(arr4[i + 3])
                    if ptd not in td_rva_set:
                        continue
                    col_rva = rdata.va + i * 4
                    pself = int(arr4[i + 5])
                    if pself != 0 and pself != col_rva:
                        continue
                    col_to_td[self.image_base + col_rva] = ptd
            finally:
                del arr4

            if not col_to_td:
                return []

            vtable_to_col: dict[int, int] = {}
            u8_count = rdata.raw_size // 8
            arr8 = np.frombuffer(mv, dtype="<u8", count=u8_count)
            try:
                col_arr = np.array(sorted(col_to_td), dtype=np.uint64)
                hits8 = np.flatnonzero(np.isin(arr8, col_arr))
                for idx8 in hits8:
                    j = int(idx8)
                    col_va = int(arr8[j])
                    vt_va = self.image_base + rdata.va + (j + 1) * 8
                    vtable_to_col[vt_va] = col_va
            finally:
                del arr8
        finally:
            mv.release()

        if not vtable_to_col:
            return []

        xrefs_by_vt = self._scan_rip_xrefs(sorted(vtable_to_col)) if include_xrefs else {}
        results: list[RttiResult] = []
        for vt_va in sorted(vtable_to_col):
            col_va = vtable_to_col[vt_va]
            td_rva = col_to_td[col_va]
            mangled, demangled = td_matches[td_rva]
            results.append(
                RttiResult(
                    name=demangled,
                    mangled=mangled,
                    vtable_va=vt_va,
                    col_va=col_va,
                    type_desc_va=self.image_base + td_rva,
                    methods=self._read_vtable_methods(vt_va, limit=6),
                    xrefs=tuple(xrefs_by_vt.get(vt_va, ())),
                )
            )
        return results

    def find_vtable_slot_overrides(
        self, slot: int, *, filter_substr: str = ""
    ) -> dict[int, list[str]]:
        """Group RTTI classes matching ``filter_substr`` by the method VA at ``vtable[slot]``."""
        if slot < 0:
            return {}
        rtti_list = self._find_rtti_by_name(filter_substr, allow_empty=True, include_xrefs=False)
        grouped: dict[int, list[str]] = {}
        for item in rtti_list:
            raw = self.read(item.vtable_va + slot * 8, 8)
            if len(raw) < 8:
                continue
            method_va = struct.unpack("<Q", raw)[0]
            sec = self.section_of(method_va)
            if sec is None or not sec.is_executable:
                continue
            grouped.setdefault(method_va, []).append(item.name)
        return {va: sorted(names) for va, names in grouped.items()}

    def find_rtti(self, query: str | int) -> list[RttiResult]:
        """Resolve MSVC x64 RTTI by vtable VA (``int`` or ``0x...``) or class name substring."""
        if isinstance(query, int):
            return self._find_rtti_by_vtable(query)
        stripped = query.strip()
        if stripped.lower().startswith("0x") or stripped.isdigit():
            return self._find_rtti_by_vtable(int(stripped, 0))
        return self._find_rtti_by_name(stripped)

    _ENTT_DESC_RE = re.compile(
        rb"\xc7\x40\x04(.{4})\x48\x8d\x0d(.{4})\x48\x89\x48\x08",
        re.DOTALL,
    )

    def scan_ecs_components(self) -> list[EcsComponentInfo]:
        """Scan ``.text`` for EnTT type descriptor emitters (hash_id <-> component name)."""
        if self._ecs_cache is not None:
            return self._ecs_cache
        by_hash: dict[int, EcsComponentInfo] = {}
        for sec in self.sections:
            if not sec.is_executable or sec.raw_size < 18:
                continue
            raw = self._data[sec.raw : sec.raw + sec.raw_size]
            for match in self._ENTT_DESC_RE.finditer(raw):
                off = match.start()
                hash_id = struct.unpack("<I", match.group(1))[0]
                if hash_id in by_hash:
                    continue
                disp = struct.unpack("<i", match.group(2))[0]
                desc_va = self.image_base + sec.va + off
                str_va = desc_va + 14 + disp
                slen = 256
                if raw[off + 18 : off + 22] == b"\x48\xc7\x40\x10" and off + 26 <= len(raw):
                    explicit = struct.unpack_from("<I", raw, off + 22)[0]
                    if 0 < explicit <= 256:
                        slen = explicit
                sraw = self.read(str_va, slen)
                if not sraw:
                    continue
                head = sraw.split(b"\x00", 1)[0].split(b"]", 1)[0]
                if not head or not all(0x20 <= b < 0x7F for b in head):
                    continue
                name = head.decode("ascii")
                by_hash[hash_id] = EcsComponentInfo(
                    hash_id=hash_id,
                    name=name,
                    desc_va=desc_va,
                    func_span=self.function_at(desc_va),
                )
        self._ecs_cache = sorted(by_hash.values(), key=lambda c: (c.name.lower(), c.hash_id))
        return self._ecs_cache

    def find_ecs_by_hash(self, hash_id: int) -> EcsComponentInfo | None:
        """Resolve one EnTT component hash (masked to uint32)."""
        target = hash_id & 0xFFFFFFFF
        for item in self.scan_ecs_components():
            if item.hash_id == target:
                return item
        return None

    def find_ecs_by_name(self, query: str) -> list[EcsComponentInfo]:
        """Find EnTT components whose decoded name contains ``query`` (case-insensitive)."""
        needle = query.strip().lower()
        if not needle:
            return []
        return [item for item in self.scan_ecs_components() if needle in item.name.lower()]


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


def _print_xrefs(image: PeImage, target_va: int) -> int:
    hits = image.find_rip_xrefs(target_va)
    print(f"xrefs to {target_va:#x}: {len(hits)} hit(s)")
    for hit in hits:
        print(f"  {hit.insn_va:#x}  {hit.func_label:34s}  {hit.text}")
    return 0


def _print_iat(image: PeImage, filt: str) -> int:
    entries = image.parse_imports()
    filt_lower = filt.lower()
    if filt_lower:
        if "." in filt_lower or "!" in filt_lower:
            entries = [e for e in entries if filt_lower in f"{e.dll}!{e.symbol}".lower()]
        else:
            sym_hits = [e for e in entries if filt_lower in e.symbol.lower()]
            entries = sym_hits or [e for e in entries if filt_lower in e.dll.lower()]
    label = f"IAT entries (filter={filt!r})" if filt else "IAT entries"
    print(f"{label}: {len(entries)} match(es)")
    for entry in entries:
        print(f"  {entry.slot_va:#x}  {entry.dll}!{entry.symbol}")
    return 0


def _print_import_xrefs(image: PeImage, symbol: str) -> int:
    hits = image.find_import_xrefs(symbol)
    slots = len({hit.entry.slot_va for hit in hits})
    print(f"import xrefs for {symbol!r} ({slots} IAT slot(s), {len(hits)} hit(s)):")
    for hit in hits:
        print(
            f"  {hit.insn_va:#x}  {hit.func_label:34s}  "
            f"{hit.entry.dll}!{hit.entry.symbol} ({hit.entry.slot_va:#x})  {hit.text}"
        )
    return 0


def _print_disasm(image: PeImage, va: int, count: int) -> int:
    lines = image.disassemble(va, count=count)
    if not lines:
        print(f"no readable code at {va:#x}")
        return 1
    print("\n".join(lines))
    return 0


def _print_func(image: PeImage, va: int, max_insns: int, *, from_va: int | None = None) -> int:
    span = image.function_at(va)
    if span is None:
        print(f"no .pdata function covering {va:#x}")
        return 1
    begin, end = span
    lines = image.disassemble_function(va, max_insns=max_insns, from_va=from_va)
    suffix = f" from {from_va:#x}" if from_va is not None else ""
    print(f"function {begin:#x}..{end:#x} (size {end - begin:#x}, {len(lines)} insn(s){suffix}):")
    if lines:
        print("\n".join(lines))
    return 0


def _in_span(va: int, span: tuple[int, int]) -> bool:
    return span[0] <= va < span[1]


def _print_find_hits(
    image: PeImage,
    label: str,
    hits: list[tuple[int, tuple[int, int] | None]],
    *,
    with_xrefs: bool = False,
    in_func_span: tuple[int, int] | None = None,
    limit: int = 64,
) -> int:
    xrefs_map = (
        image._scan_rip_xrefs(sorted({va for va, _ in hits})) if (with_xrefs and hits) else {}
    )
    filtered: list[tuple[int, tuple[int, int] | None, list[RipXref]]] = []
    for va, span in hits:
        xrefs = xrefs_map.get(va, [])
        if in_func_span is not None:
            if with_xrefs:
                xrefs = [x for x in xrefs if _in_span(x.insn_va, in_func_span)]
                if not _in_span(va, in_func_span) and not xrefs:
                    continue
            elif not _in_span(va, in_func_span):
                continue
        filtered.append((va, span, xrefs))
        if len(filtered) >= limit:
            break

    print(f"{label}: {len(filtered)} hit(s)")
    for va, span, xrefs in filtered:
        sec = image.section_of(va)
        sec_name = sec.name if sec is not None else "?"
        func_label = _format_func_label(span[0], span[1]) if span is not None else sec_name
        print(f"  {va:#x}  [{sec_name}]  {func_label}")
        if with_xrefs:
            for xref in xrefs[:10]:
                print(f"    xref {xref.insn_va:#x}  {xref.func_label:34s}  {xref.text}")
    return 0


def _print_find_strings(
    image: PeImage,
    query: str,
    *,
    ignore_case: bool = False,
    limit: int = 64,
    with_xrefs: bool = False,
    in_func_span: tuple[int, int] | None = None,
) -> int:
    scan_limit = max(limit, 1024) if (in_func_span is not None and with_xrefs) else limit
    hits = image.find_string(query, ignore_case=ignore_case, limit=scan_limit)
    xrefs_map = (
        image._scan_rip_xrefs(sorted({va for va, _ in hits})) if (with_xrefs and hits) else {}
    )
    filtered: list[tuple[int, str, list[RipXref]]] = []
    for va, text in hits:
        xrefs = xrefs_map.get(va, [])
        if in_func_span is not None and with_xrefs:
            xrefs = [x for x in xrefs if _in_span(x.insn_va, in_func_span)]
            if not xrefs:
                continue
        filtered.append((va, text, xrefs))
        if len(filtered) >= limit:
            break

    print(f"strings matching {query!r}: {len(filtered)} hit(s)")
    for va, text, xrefs in filtered:
        print(f"  {va:#x}  {text!r}")
        if with_xrefs:
            for xref in xrefs[:10]:
                print(f"    xref {xref.insn_va:#x}  {xref.func_label:34s}  {xref.text}")
    return 0


def _print_vtable_slot(image: PeImage, slot: int, filt: str) -> int:
    grouped = image.find_vtable_slot_overrides(slot, filter_substr=filt)
    print(f"vtable slot[{slot}] (filter={filt!r}): {len(grouped)} unique target(s)")
    for method_va, names in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        span = image.function_at(method_va)
        func_label = _format_func_label(span[0], span[1]) if span is not None else "<no .pdata>"
        sample = ", ".join(names[:5])
        suffix = f", ... (+{len(names) - 5} more)" if len(names) > 5 else ""
        print(f"  {method_va:#x}  {func_label:34s}  ({len(names)}): {sample}{suffix}")
    return 0


def _print_rtti(image: PeImage, query: str) -> int:
    results = image.find_rtti(query)
    if not results:
        print(f"no RTTI matches for {query!r}")
        stripped = query.strip()
        if stripped and not stripped.lower().startswith("0x") and not stripped.isdigit():
            ecs_hits = image.find_ecs_by_name(stripped)[:5]
            if ecs_hits:
                print(
                    "hint: matching ECS component(s): "
                    + ", ".join(f"{c.name} ({c.hash_id:#010x})" for c in ecs_hits)
                )
            str_hits = image.find_string(stripped, ignore_case=True, limit=5)
            if str_hits:
                print(
                    "hint: matching string(s): "
                    + ", ".join(f"{va:#x} {text!r}" for va, text in str_hits)
                )
        return 0
    print(f"RTTI matches for {query!r}: {len(results)}")
    for item in results:
        print(f"  {item.name} ({item.mangled})")
        print(
            f"    vtable {item.vtable_va:#x}  col {item.col_va:#x}  "
            f"type_desc {item.type_desc_va:#x}"
        )
        methods_str = ", ".join(f"{m:#x}" for m in item.methods) if item.methods else "<none>"
        print(f"    methods[{len(item.methods)}]: {methods_str}")
        print(f"    xrefs[{len(item.xrefs)}]:")
        for xref in item.xrefs[:20]:
            print(f"      {xref.insn_va:#x}  {xref.func_label:34s}  {xref.text}")
        if len(item.xrefs) > 20:
            print(f"      ... (+{len(item.xrefs) - 20} more)")
    return 0


def _print_ecs_hash(
    image: PeImage,
    hash_id: int,
    *,
    limit: int = 64,
    in_func_span: tuple[int, int] | None = None,
) -> int:
    val = hash_id & 0xFFFFFFFF
    info = image.find_ecs_by_hash(val)
    if info is None:
        print(f"ECS hash {val:#010x}: <descriptor not found>")
    else:
        print(
            f"ECS hash {val:#010x}: {info.name}  (descriptor {info.desc_va:#x}, {info.func_label})"
        )
    scan_limit = max(limit, 1024) if in_func_span is not None else limit
    hits = image.find_u32(val, section=".text", limit=scan_limit)
    return _print_find_hits(
        image,
        f".text u32 {val:#010x}",
        hits,
        with_xrefs=False,
        in_func_span=in_func_span,
        limit=limit,
    )


def _print_ecs_find(
    image: PeImage,
    query: str,
    *,
    limit: int = 64,
    with_xrefs: bool = False,
) -> int:
    matches = image.find_ecs_by_name(query)
    shown = matches[: max(0, limit)]
    print(f"ECS components matching {query!r}: {len(matches)} match(es)")
    for idx, info in enumerate(shown):
        print(f"  {info.hash_id:#010x}  {info.name:42s}  desc {info.desc_va:#x}  {info.func_label}")
        if with_xrefs and idx < 10:
            for va, span in image.find_u32(info.hash_id, section=".text", limit=10):
                func_label = _format_func_label(*span) if span is not None else ".text"
                print(f"    use  {va:#x}  {func_label}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("address", nargs="?", help="VA in hex (0x...) or decimal")
    parser.add_argument("--exe", type=Path, default=None, help="path to Minecraft.Windows.exe")
    parser.add_argument(
        "-n", "--count", type=int, default=None, help="instruction count for --disasm/--func"
    )
    parser.add_argument(
        "--from", dest="from_va", metavar="VA", default=None, help="start VA for --func"
    )
    parser.add_argument("--section", default=None, help="section filter for --find-u32/--find-f32")
    parser.add_argument("--limit", type=int, default=64, help="max results for --find-* scans")
    parser.add_argument("--ignore-case", action="store_true", help="case-insensitive --find-str")
    parser.add_argument(
        "--with-xrefs", action="store_true", help="resolve .text RIP xrefs for --find-* hits"
    )
    parser.add_argument(
        "--in-func",
        metavar="VA",
        default=None,
        help="restrict --find-* / --ecs-hash hits or xrefs to .pdata function covering VA",
    )
    parser.add_argument(
        "--filter", default="", help="demangled RTTI class substring for --vtable-slot"
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sections", action="store_true", help="print the section table")
    mode.add_argument("--xrefs", metavar="VA", help="find .text RIP-relative xrefs to VA")
    mode.add_argument(
        "--iat", nargs="?", const="", default=None, metavar="FILTER", help="list IAT imports"
    )
    mode.add_argument("--import-xrefs", metavar="SYMBOL", help="find .text xrefs to IAT import")
    mode.add_argument("--disasm", metavar="VA", help="disassemble with annotations from VA")
    mode.add_argument("--func", metavar="VA", help="disassemble entire .pdata function covering VA")
    mode.add_argument("--find-u32", metavar="INT", help="find little-endian uint32 constant")
    mode.add_argument("--find-f32", metavar="FLOAT", help="find little-endian float32 constant")
    mode.add_argument("--find-str", metavar="TEXT", help="find printable UTF-8/ASCII strings")
    mode.add_argument("--ecs-hash", metavar="HASH", help="resolve EnTT ECS component hash and uses")
    mode.add_argument("--ecs-find", metavar="NAME", help="search EnTT ECS components by name")
    mode.add_argument(
        "--vtable-slot", type=int, metavar="SLOT", help="group RTTI classes by vtable[SLOT]"
    )
    mode.add_argument("--rtti", metavar="VA_OR_NAME", help="inspect MSVC RTTI by vtable VA or name")
    args = parser.parse_args()

    has_mode = any(
        [
            args.sections,
            args.xrefs is not None,
            args.iat is not None,
            args.import_xrefs is not None,
            args.disasm is not None,
            args.func is not None,
            args.find_u32 is not None,
            args.find_f32 is not None,
            args.find_str is not None,
            args.ecs_hash is not None,
            args.ecs_find is not None,
            args.vtable_slot is not None,
            args.rtti is not None,
        ]
    )
    if has_mode and args.address is not None:
        parser.error("positional address cannot be combined with a mode flag")

    path = args.exe if args.exe is not None else default_exe()
    with PeImage(path) as image:
        in_func_span: tuple[int, int] | None = None
        if args.in_func is not None:
            in_func_va = int(args.in_func, 0)
            in_func_span = image.function_at(in_func_va)
            if in_func_span is None:
                print(f"no .pdata function covering {in_func_va:#x}")
                return 1
        if args.sections:
            print(f"{path}  base {image.image_base:#x}  functions {len(image.functions)}")
            for section in image.sections:
                print(
                    f"  {section.name:9s} va {section.va:#010x} vsize {section.vsize:#010x} "
                    f"raw {section.raw:#010x} rawsize {section.raw_size:#010x}"
                )
            return 0
        if args.xrefs is not None:
            return _print_xrefs(image, int(args.xrefs, 0))
        if args.iat is not None:
            return _print_iat(image, args.iat)
        if args.import_xrefs is not None:
            return _print_import_xrefs(image, args.import_xrefs)
        if args.disasm is not None:
            return _print_disasm(image, int(args.disasm, 0), args.count or 40)
        if args.func is not None:
            from_va = int(args.from_va, 0) if args.from_va is not None else None
            return _print_func(image, int(args.func, 0), args.count or 2000, from_va=from_va)
        if args.find_u32 is not None:
            val = int(args.find_u32, 0) & 0xFFFFFFFF
            scan_limit = max(args.limit, 1024) if in_func_span is not None else args.limit
            hits = image.find_u32(val, section=args.section, limit=scan_limit)
            return _print_find_hits(
                image,
                f"u32 {val:#010x}",
                hits,
                with_xrefs=args.with_xrefs,
                in_func_span=in_func_span,
                limit=args.limit,
            )
        if args.find_f32 is not None:
            val_f = float(args.find_f32)
            bits = struct.unpack("<I", struct.pack("<f", val_f))[0]
            scan_limit = max(args.limit, 1024) if in_func_span is not None else args.limit
            hits = image.find_f32(val_f, section=args.section, limit=scan_limit)
            return _print_find_hits(
                image,
                f"f32 {val_f} ({bits:#010x})",
                hits,
                with_xrefs=args.with_xrefs,
                in_func_span=in_func_span,
                limit=args.limit,
            )
        if args.find_str is not None:
            return _print_find_strings(
                image,
                args.find_str,
                ignore_case=args.ignore_case,
                limit=args.limit,
                with_xrefs=args.with_xrefs,
                in_func_span=in_func_span,
            )
        if args.ecs_hash is not None:
            return _print_ecs_hash(
                image,
                int(args.ecs_hash, 0),
                limit=args.limit,
                in_func_span=in_func_span,
            )
        if args.ecs_find is not None:
            return _print_ecs_find(
                image,
                args.ecs_find,
                limit=args.limit,
                with_xrefs=args.with_xrefs,
            )
        if args.vtable_slot is not None:
            return _print_vtable_slot(image, args.vtable_slot, args.filter)
        if args.rtti is not None:
            return _print_rtti(image, args.rtti)
        if args.address is None:
            parser.error("address is required unless a mode flag is given")
        address = int(args.address, 0)
        print("\n".join(describe(image, address)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
