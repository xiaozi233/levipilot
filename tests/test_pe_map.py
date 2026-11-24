from __future__ import annotations

import contextlib
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from driver import pe_map
from driver.pe_map import (
    ImportEntry,
    PdataIndex,
    PeImage,
    Section,
    default_exe,
    demangle_rtti_name,
    parse_headers,
)

IMAGE_BASE = 0x140000000


def synthetic_pe() -> bytes:
    """Minimal PE32+ with a .text and a .pdata section (3 functions + zero padding)."""
    buffer = bytearray(0x2500)
    buffer[0:2] = b"MZ"
    struct.pack_into("<I", buffer, 0x3C, 0x80)
    buffer[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", buffer, 0x84, 0x8664)  # machine
    struct.pack_into("<H", buffer, 0x86, 2)  # section count
    struct.pack_into("<H", buffer, 0x94, 0xF0)  # optional header size
    optional = 0x98
    struct.pack_into("<H", buffer, optional, 0x20B)  # PE32+
    struct.pack_into("<Q", buffer, optional + 24, IMAGE_BASE)
    table = optional + 0xF0
    struct.pack_into("<8sIIII", buffer, table, b".text", 0x2000, 0x1000, 0x2000, 0x400)
    struct.pack_into("<8sIIII", buffer, table + 40, b".pdata", 0x30, 0x3000, 0x30, 0x2400)
    for index, entry in enumerate(((0x1000, 0x1100), (0x1100, 0x1200), (0x1210, 0x1300))):
        struct.pack_into("<III", buffer, 0x2400 + index * 12, entry[0], entry[1], 0)
    buffer[0x400:0x404] = b"\x48\x89\x5c\x24"
    return bytes(buffer)


def _disp32(insn_end_rva: int, target_rva: int) -> bytes:
    return struct.pack("<i", target_rva - insn_end_rva)


def synthetic_rich_pe() -> tuple[bytes, dict[str, int]]:
    """Build a synthetic PE32+ image exercising xrefs, IAT, disasm, BSS, and RTTI."""
    text_va, text_raw, text_size = 0x1000, 0x400, 0x400
    rdata_va, rdata_raw, rdata_size = 0x2000, 0x800, 0x800
    data_va, data_raw, data_rsize, data_vsize = 0x4000, 0x1000, 0x100, 0x400
    pdata_va, pdata_raw, pdata_size = 0x5000, 0x1100, 0x30

    buf = bytearray(pdata_raw + pdata_size)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, 0x80)
    buf[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", buf, 0x84, 0x8664)  # AMD64
    struct.pack_into("<H", buf, 0x86, 4)  # 4 sections
    struct.pack_into("<H", buf, 0x94, 0xF0)  # optional header size
    opt = 0x98
    struct.pack_into("<H", buf, opt, 0x20B)  # PE32+
    struct.pack_into("<Q", buf, opt + 24, IMAGE_BASE)
    struct.pack_into("<I", buf, opt + 108, 16)  # NumberOfRvaAndSizes
    struct.pack_into("<II", buf, opt + 120, rdata_va, 60)  # IMAGE_DIRECTORY_ENTRY_IMPORT

    table = opt + 0xF0
    sections = [
        (b".text", text_size, text_va, text_size, text_raw, 0x60000020),
        (b".rdata", rdata_size, rdata_va, rdata_size, rdata_raw, 0x40000040),
        (b".data", data_vsize, data_va, data_rsize, data_raw, 0xC0000040),
        (b".pdata", pdata_size, pdata_va, pdata_size, pdata_raw, 0x40000040),
    ]
    for idx, (name, vsz, va, rsz, raw, chars) in enumerate(sections):
        off = table + idx * 40
        struct.pack_into("<8sIIII", buf, off, name, vsz, va, rsz, raw)
        struct.pack_into("<I", buf, off + 36, chars)

    def put_rdata(rva: int, payload: bytes) -> None:
        off = rdata_raw + (rva - rdata_va)
        buf[off : off + len(payload)] = payload

    def put_data(rva: int, payload: bytes) -> None:
        off = data_raw + (rva - data_va)
        buf[off : off + len(payload)] = payload

    def put_text(rva: int, payload: bytes) -> None:
        off = text_raw + (rva - text_va)
        buf[off : off + len(payload)] = payload

    # --- Import Table in .rdata (at 0x2000) ---
    # Descriptor 0: math.dll (OFT=0x2060, Name=0x2100, FT=0x2080)
    # Descriptor 1: helper.dll (OFT=0, Name=0x2110, FT=0x20B0)
    struct.pack_into("<IIIII", buf, rdata_raw, 0x2060, 0, 0, 0x2100, 0x2080)
    struct.pack_into("<IIIII", buf, rdata_raw + 20, 0, 0, 0, 0x2110, 0x20B0)
    put_rdata(0x2100, b"math.dll\0")
    put_rdata(0x2110, b"helper.dll\0")

    # Hint/Names
    put_rdata(0x2120, struct.pack("<H", 0) + b"sinf\0")
    put_rdata(0x2130, struct.pack("<H", 1) + b"cosf\0")
    put_rdata(0x2140, struct.pack("<H", 0) + b"helper_fn\0")

    # math.dll thunks: sinf, cosf, ordinal#7
    for off_base in (0x2060, 0x2080):
        put_rdata(off_base + 0, struct.pack("<Q", 0x2120))
        put_rdata(off_base + 8, struct.pack("<Q", 0x2130))
        put_rdata(off_base + 16, struct.pack("<Q", (1 << 63) | 7))
    # helper.dll FT-only thunk
    put_rdata(0x20B0, struct.pack("<Q", 0x2140))

    sinf_slot = IMAGE_BASE + 0x2080
    cosf_slot = IMAGE_BASE + 0x2088
    helper_slot = IMAGE_BASE + 0x20B0

    # --- Constants & strings in .rdata ---
    hello_rva = 0x2200
    put_rdata(hello_rva, b"HelloPE\0")
    float_rva = 0x2210
    put_rdata(float_rva, struct.pack("<ff", 0.6, 1.5))

    # --- Data globals & BSS ---
    global_rva = 0x4000
    put_data(global_rva, struct.pack("<I", 0x11223344))
    bss_rva = 0x4200  # beyond data_rsize (0x100)

    # --- RTTI ---
    # 1) Demo::Widget in .data (pSelf == col_rva)
    widget_td_rva = 0x4010
    put_data(widget_td_rva, struct.pack("<QQ", 0, 0) + b".?AVWidget@Demo@@\0")
    widget_col_rva = 0x2260
    put_rdata(
        widget_col_rva,
        struct.pack("<IIIIII", 1, 0, 0, widget_td_rva, 0, widget_col_rva),
    )
    widget_vt_rva = 0x2308
    put_rdata(widget_vt_rva - 8, struct.pack("<Q", IMAGE_BASE + widget_col_rva))
    put_rdata(
        widget_vt_rva,
        struct.pack("<QQQ", IMAGE_BASE + 0x1000, IMAGE_BASE + 0x1050, 0x3F800000),
    )

    # 2) PlainStruct in .rdata (pSelf == 0 synthetic case)
    plain_td_rva = 0x2400
    put_rdata(plain_td_rva, struct.pack("<QQ", 0, 0) + b".?AUPlainStruct@@\0")
    plain_col_rva = 0x2440
    put_rdata(plain_col_rva, struct.pack("<IIIIII", 1, 0, 0, plain_td_rva, 0, 0))
    plain_vt_rva = 0x2488
    put_rdata(plain_vt_rva - 8, struct.pack("<Q", IMAGE_BASE + plain_col_rva))
    put_rdata(plain_vt_rva, struct.pack("<QQ", IMAGE_BASE + 0x1050, 0))

    # 3) Template Demo::Box<Demo::Widget> in .data
    box_td_rva = 0x4050
    put_data(box_td_rva, struct.pack("<QQ", 0, 0) + b".?AV?$Box@VWidget@Demo@@@Demo@@\0")
    box_col_rva = 0x2500
    put_rdata(box_col_rva, struct.pack("<IIIIII", 1, 0, 0, box_td_rva, 0, box_col_rva))
    box_vt_rva = 0x2548
    put_rdata(box_vt_rva - 8, struct.pack("<Q", IMAGE_BASE + box_col_rva))
    put_rdata(box_vt_rva, struct.pack("<QQ", IMAGE_BASE + 0x1000, 0))

    # --- .pdata: fn1 [0x1000, 0x1048), fn2 [0x1050, 0x1070); leaf at 0x1072..0x1090 ---
    struct.pack_into("<III", buf, pdata_raw + 0, 0x1000, 0x1048, 0)
    struct.pack_into("<III", buf, pdata_raw + 12, 0x1050, 0x1070, 0)

    # --- .text instructions ---
    # fn1 (0x1000..0x1048)
    # 0x1000: lea rcx, [rip + disp] -> hello_rva (7B)
    put_text(0x1000, b"\x48\x8d\x0d" + _disp32(0x1007, hello_rva))
    # 0x1007: movss xmm0, dword ptr [rip + disp] -> float_rva (8B)
    put_text(0x1007, b"\xf3\x0f\x10\x05" + _disp32(0x100F, float_rva))
    # 0x100F: call qword ptr [rip + disp] -> sinf IAT (6B)
    put_text(0x100F, b"\xff\x15" + _disp32(0x1015, 0x2080))
    # 0x1015: mov dword ptr [rip + disp], 0x12345678 -> global_rva (10B, tail=4)
    put_text(0x1015, b"\xc7\x05" + _disp32(0x101F, global_rva) + struct.pack("<I", 0x12345678))
    # 0x101F: call 0x1050 -> fn2 (5B)
    put_text(0x101F, b"\xe8" + _disp32(0x1024, 0x1050))
    # 0x1024: lea rax, [rip + disp] -> widget_vt_rva (7B)
    put_text(0x1024, b"\x48\x8d\x05" + _disp32(0x102B, widget_vt_rva))
    # 0x102B: lea rdx, [rip + disp] -> bss_rva (7B)
    put_text(0x102B, b"\x48\x8d\x15" + _disp32(0x1032, bss_rva))
    # 0x1032: mov eax, <false disp32 targeting float_rva from 0x1037> (5B, imm32 not rip-rel!)
    put_text(0x1032, b"\xb8" + _disp32(0x1037, float_rva))
    # 0x1037: ret
    put_text(0x1037, b"\xc3")

    # fn2 (0x1050..0x1070)
    # 0x1050: jmp qword ptr [rip + disp] -> cosf IAT (6B)
    put_text(0x1050, b"\xff\x25" + _disp32(0x1056, 0x2088))
    # 0x1056: mov rax, qword ptr [rip + disp] -> sinf IAT (7B)
    put_text(0x1056, b"\x48\x8b\x05" + _disp32(0x105D, 0x2080))
    # 0x105D: call qword ptr [rip + disp] -> helper_fn IAT (6B)
    put_text(0x105D, b"\xff\x15" + _disp32(0x1063, 0x20B0))
    # 0x1063: ret
    put_text(0x1063, b"\xc3")

    # <leaf> at 0x1072 (preceded by int3 padding at 0x1070..0x1072)
    put_text(0x1070, b"\xcc\xcc")
    # 0x1072: lea r9, [rip + disp] -> float_rva (7B, tests REX prefix disambiguation!)
    put_text(0x1072, b"\x4c\x8d\x0d" + _disp32(0x1079, float_rva))
    # 0x1079: lea rax, [rip + disp] -> plain_vt_rva (7B)
    put_text(0x1079, b"\x48\x8d\x05" + _disp32(0x1080, plain_vt_rva))
    # 0x1080: jmp 0x1050 (5B)
    put_text(0x1080, b"\xe9" + _disp32(0x1085, 0x1050))

    # Synthetic EnTT component descriptor inside .text (0x1090..0x10B0, inside pdata fn3 [0x1090, 0x10B8))
    ecs_name_rva = 0x25A0
    put_rdata(ecs_name_rva, b"DemoFooComponent]")
    struct.pack_into("<III", buf, pdata_raw + 24, 0x1090, 0x10B8, 0)
    # 0x1090: mov dword ptr [rax+4], 0xCAFEBABE (7B)
    # 0x1097: lea rcx, [rip + disp] -> ecs_name_rva (7B)
    # 0x109E: mov qword ptr [rax+8], rcx (4B)
    # 0x10A2: mov qword ptr [rax+0x10], 16 (8B)
    # 0x10AA: ret (1B)
    put_text(
        0x1090,
        b"\xc7\x40\x04"
        + struct.pack("<I", 0xCAFEBABE)
        + b"\x48\x8d\x0d"
        + _disp32(0x109E, ecs_name_rva)
        + b"\x48\x89\x48\x08\x48\xc7\x40\x10"
        + struct.pack("<I", 16)
        + b"\xc3",
    )

    addrs = {
        "hello": IMAGE_BASE + hello_rva,
        "float": IMAGE_BASE + float_rva,
        "global": IMAGE_BASE + global_rva,
        "bss": IMAGE_BASE + bss_rva,
        "sinf_slot": sinf_slot,
        "cosf_slot": cosf_slot,
        "helper_slot": helper_slot,
        "fn1": IMAGE_BASE + 0x1000,
        "fn2": IMAGE_BASE + 0x1050,
        "fn3": IMAGE_BASE + 0x1090,
        "leaf": IMAGE_BASE + 0x1072,
        "widget_vt": IMAGE_BASE + widget_vt_rva,
        "widget_col": IMAGE_BASE + widget_col_rva,
        "widget_td": IMAGE_BASE + widget_td_rva,
        "plain_vt": IMAGE_BASE + plain_vt_rva,
        "box_vt": IMAGE_BASE + box_vt_rva,
        "ecs_desc": IMAGE_BASE + 0x1090,
    }
    return bytes(buf), addrs


class HeaderTests(unittest.TestCase):
    def test_parse_headers(self) -> None:
        base, sections = parse_headers(synthetic_pe())
        self.assertEqual(base, IMAGE_BASE)
        self.assertEqual([section.name for section in sections], [".text", ".pdata"])
        self.assertEqual(sections[0].va, 0x1000)
        self.assertEqual(sections[0].raw, 0x400)

    def test_section_mapping(self) -> None:
        section = Section(".text", 0x1000, 0x2000, 0x400, 0x2000)
        self.assertTrue(section.covers(0x1000))
        self.assertTrue(section.covers(0x2FFF))
        self.assertFalse(section.covers(0x3000))
        self.assertEqual(section.offset_of(0x1010), 0x410)
        self.assertIsNone(section.offset_of(0x3000))
        self.assertTrue(section.is_executable)

    def test_rejects_non_pe(self) -> None:
        with self.assertRaises(ValueError):
            parse_headers(b"not a pe file at all")


class PdataTests(unittest.TestCase):
    def setUp(self) -> None:
        raw = synthetic_pe()[0x2400 : 0x2400 + 0x30]
        self.index = PdataIndex(raw)

    def test_finds_containing_function(self) -> None:
        self.assertEqual(self.index.function_at(0x1000), (0x1000, 0x1100))
        self.assertEqual(self.index.function_at(0x10FF), (0x1000, 0x1100))
        self.assertEqual(self.index.function_at(0x1210), (0x1210, 0x1300))

    def test_gap_and_tail_return_none(self) -> None:
        self.assertIsNone(self.index.function_at(0x1205))
        self.assertIsNone(self.index.function_at(0x4000))
        self.assertEqual(self.index.prev_function_end(0x1205), 0x1200)
        self.assertEqual(self.index.prev_function_end(0x0500), 0)

    def test_empty_blob(self) -> None:
        self.assertIsNone(PdataIndex(b"").function_at(0x1000))
        self.assertEqual(PdataIndex(b"").prev_function_end(0x1000), 0)


class DemangleTests(unittest.TestCase):
    def test_demangles_common_rtti_patterns(self) -> None:
        self.assertEqual(demangle_rtti_name(".?AVSimple@@"), "Simple")
        self.assertEqual(demangle_rtti_name(".?AUPlainStruct@@"), "PlainStruct")
        self.assertEqual(demangle_rtti_name(".?AVStyledWriter@Json@@"), "Json::StyledWriter")
        self.assertEqual(
            demangle_rtti_name(".?AV?$Box@VWidget@Demo@@@Demo@@"),
            "Demo::Box<Demo::Widget>",
        )
        self.assertEqual(
            demangle_rtti_name(".?AV?$numpunct@D@std@@"),
            "std::numpunct<char>",
        )
        self.assertEqual(demangle_rtti_name("not_mangled"), "not_mangled")


class PeImageTests(unittest.TestCase):
    def test_function_and_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fake.exe"
            path.write_bytes(synthetic_pe())
            with PeImage(path) as image:
                self.assertEqual(image.image_base, IMAGE_BASE)
                self.assertEqual(
                    image.function_at(IMAGE_BASE + 0x1050),
                    (IMAGE_BASE + 0x1000, IMAGE_BASE + 0x1100),
                )
                section = image.section_of(IMAGE_BASE + 0x1050)
                self.assertIsNotNone(section)
                assert section is not None
                self.assertEqual(section.name, ".text")
                self.assertEqual(image.read(IMAGE_BASE + 0x1000, 4), b"\x48\x89\x5c\x24")
                self.assertIsNone(image.function_at(IMAGE_BASE + 0x5000))
                self.assertEqual(image.parse_imports(), [])
                self.assertEqual(image.find_rip_xrefs(IMAGE_BASE + 0x2000), [])

    def test_find_rip_xrefs_and_leaf_and_tail4(self) -> None:
        blob, addrs = synthetic_rich_pe()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rich.exe"
            path.write_bytes(blob)
            with PeImage(path) as image:
                # float_rva has 2 true xrefs (fn1 movss + leaf lea r9) and 1 false imm32 in fn1
                float_hits = image.find_rip_xrefs(addrs["float"])
                self.assertEqual(
                    [(h.insn_va, h.function) for h in float_hits],
                    [
                        (IMAGE_BASE + 0x1007, (addrs["fn1"], IMAGE_BASE + 0x1048)),
                        (addrs["leaf"], None),
                    ],
                )
                self.assertTrue(float_hits[0].text.startswith("movss"))
                self.assertEqual(float_hits[1].func_label, "<leaf>")
                self.assertTrue(float_hits[1].text.startswith("lea r9"))

                # tail=4 instruction: mov dword ptr [rip + disp], 0x12345678
                global_hits = image.find_rip_xrefs(addrs["global"])
                self.assertEqual(len(global_hits), 1)
                self.assertEqual(global_hits[0].insn_va, IMAGE_BASE + 0x1015)

                # direct call + leaf jmp to fn2
                fn2_hits = image.find_rip_xrefs(addrs["fn2"])
                self.assertEqual(
                    [(h.insn_va, h.function) for h in fn2_hits],
                    [
                        (IMAGE_BASE + 0x101F, (addrs["fn1"], IMAGE_BASE + 0x1048)),
                        (IMAGE_BASE + 0x1080, None),
                    ],
                )

    def test_imports_and_import_xrefs(self) -> None:
        blob, addrs = synthetic_rich_pe()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rich.exe"
            path.write_bytes(blob)
            with PeImage(path) as image:
                imports = image.parse_imports()
                self.assertEqual(
                    imports,
                    [
                        ImportEntry("math.dll", "sinf", addrs["sinf_slot"], None),
                        ImportEntry("math.dll", "cosf", addrs["cosf_slot"], None),
                        ImportEntry("math.dll", "ordinal#7", IMAGE_BASE + 0x2090, 7),
                        ImportEntry("helper.dll", "helper_fn", addrs["helper_slot"], None),
                    ],
                )

                sinf_xrefs = image.find_import_xrefs("sinf")
                self.assertEqual(
                    [(x.insn_va, x.entry.symbol, x.function) for x in sinf_xrefs],
                    [
                        (IMAGE_BASE + 0x100F, "sinf", (addrs["fn1"], IMAGE_BASE + 0x1048)),
                        (IMAGE_BASE + 0x1056, "sinf", (addrs["fn2"], IMAGE_BASE + 0x1070)),
                    ],
                )

                # Substring match when no exact symbol exists
                helper_xrefs = image.find_import_xrefs("helper")
                self.assertEqual(len(helper_xrefs), 1)
                self.assertEqual(helper_xrefs[0].insn_va, IMAGE_BASE + 0x105D)
                self.assertEqual(image.find_import_xrefs("missing"), [])
                self.assertEqual(image.find_import_xrefs(""), [])

    def test_disassemble_annotations(self) -> None:
        blob, addrs = synthetic_rich_pe()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rich.exe"
            path.write_bytes(blob)
            with PeImage(path) as image:
                lines = image.disassemble(addrs["fn1"], count=9)
                self.assertEqual(len(lines), 9)
                self.assertIn(".rdata", lines[0])
                self.assertIn("str='HelloPE'", lines[0])
                self.assertIn("f32=0.6", lines[1])
                self.assertIn("f64=", lines[1])
                self.assertIn("u32=0x3f19999a", lines[1])
                self.assertIn("IAT math.dll!sinf", lines[2])
                self.assertIn(".data", lines[3])
                self.assertIn("u32=0x11223344", lines[3])
                self.assertIn(f"func {addrs['fn2']:#x}..{IMAGE_BASE + 0x1070:#x}", lines[4])
                self.assertIn(", bss)", lines[6])
                self.assertEqual(image.disassemble(addrs["fn1"], count=0), [])
                self.assertEqual(image.disassemble(IMAGE_BASE + 0x99999, count=5), [])

    def test_rtti_by_vtable_and_name(self) -> None:
        blob, addrs = synthetic_rich_pe()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rich.exe"
            path.write_bytes(blob)
            with PeImage(path) as image:
                by_va = image.find_rtti(addrs["widget_vt"])
                self.assertEqual(len(by_va), 1)
                res = by_va[0]
                self.assertEqual(res.name, "Demo::Widget")
                self.assertEqual(res.mangled, ".?AVWidget@Demo@@")
                self.assertEqual(res.vtable_va, addrs["widget_vt"])
                self.assertEqual(res.col_va, addrs["widget_col"])
                self.assertEqual(res.type_desc_va, addrs["widget_td"])
                self.assertEqual(res.methods, (addrs["fn1"], addrs["fn2"]))
                self.assertEqual([x.insn_va for x in res.xrefs], [IMAGE_BASE + 0x1024])

                # Hex string query
                self.assertEqual(image.find_rtti(hex(addrs["widget_vt"])), by_va)

                # Substring matching both Widget and Box<Widget>
                by_name = image.find_rtti("Widget")
                self.assertEqual(
                    [r.name for r in by_name], ["Demo::Widget", "Demo::Box<Demo::Widget>"]
                )

                # Demangled '::' substring query + struct in .rdata with pSelf=0
                self.assertEqual(len(image.find_rtti("Demo::Widget")), 2)
                plain = image.find_rtti("PlainStruct")
                self.assertEqual(len(plain), 1)
                self.assertEqual(plain[0].methods, (addrs["fn2"],))
                self.assertEqual([x.insn_va for x in plain[0].xrefs], [IMAGE_BASE + 0x1079])

                self.assertEqual(image.find_rtti("NoSuchClass"), [])

    def test_cli_flags(self) -> None:
        blob, addrs = synthetic_rich_pe()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rich.exe"
            path.write_bytes(blob)

            def run_cli(*argv: str) -> tuple[int, str]:
                buf = io.StringIO()
                with (
                    mock.patch.object(sys, "argv", ["pe_map.py", "--exe", str(path), *argv]),
                    contextlib.redirect_stdout(buf),
                ):
                    rc = pe_map.main()
                return rc, buf.getvalue()

            rc, out = run_cli("--sections")
            self.assertEqual(rc, 0)
            self.assertIn(".text", out)

            rc, out = run_cli("--xrefs", hex(addrs["float"]))
            self.assertEqual(rc, 0)
            self.assertIn("2 hit(s)", out)
            self.assertIn("<leaf>", out)

            rc, out = run_cli("--iat")
            self.assertEqual(rc, 0)
            self.assertIn("helper.dll!helper_fn", out)

            rc, out = run_cli("--iat", "sinf")
            self.assertEqual(rc, 0)
            self.assertIn("math.dll!sinf", out)
            self.assertNotIn("cosf", out)

            rc, out = run_cli("--import-xrefs", "cosf")
            self.assertEqual(rc, 0)
            self.assertIn("1 hit(s)", out)
            self.assertIn("math.dll!cosf", out)

            rc, out = run_cli("--disasm", hex(addrs["fn1"]), "-n", "4")
            self.assertEqual(rc, 0)
            self.assertEqual(len(out.strip().splitlines()), 4)
            self.assertIn("IAT math.dll!sinf", out)

            rc, out = run_cli("--rtti", "PlainStruct")
            self.assertEqual(rc, 0)
            self.assertIn("PlainStruct (.?AUPlainStruct@@)", out)

            rc, out = run_cli("--find-u32", "0x12345678")
            self.assertEqual(rc, 0)
            self.assertIn("1 hit(s)", out)

            rc, out = run_cli("--find-f32", "1.5", "--with-xrefs")
            self.assertEqual(rc, 0)
            self.assertIn(hex(addrs["float"] + 4), out)

            rc, out = run_cli("--find-f32", "0.6", "--with-xrefs")
            self.assertEqual(rc, 0)
            self.assertIn(hex(addrs["float"]), out)
            self.assertIn("xref ", out)

            rc, out = run_cli("--find-str", "hello", "--ignore-case", "--with-xrefs")
            self.assertEqual(rc, 0)
            self.assertIn("'HelloPE'", out)
            self.assertIn("xref ", out)

            rc, out = run_cli("--func", hex(addrs["fn1"] + 7))
            self.assertEqual(rc, 0)
            self.assertIn("function ", out)
            self.assertIn("call ", out)

            rc, out = run_cli("--vtable-slot", "0", "--filter", "PlainStruct")
            self.assertEqual(rc, 0)
            self.assertIn("PlainStruct", out)

    def test_new_pe_image_helpers(self) -> None:
        blob, addrs = synthetic_rich_pe()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.exe"
            path.write_bytes(blob)
            with PeImage(path) as image:
                self.assertEqual(image.find_func(addrs["fn1"] + 3), image.function_at(addrs["fn1"]))
                text_sec = next(s for s in image.sections if s.name == ".text")
                self.assertEqual(image.section_va(text_sec), image.image_base + text_sec.va)
                u_hits = image.find_u32(0x12345678)
                self.assertTrue(any(va > addrs["fn1"] and span is not None for va, span in u_hits))
                f_hits = image.find_f32(1.5, section=".rdata")
                self.assertEqual([va for va, _ in f_hits], [addrs["float"] + 4])
                s_hits = image.find_string("HELLO", ignore_case=True)
                self.assertEqual(s_hits, [(addrs["hello"], "HelloPE")])
                fn_lines = image.disassemble_function(addrs["fn1"] + 7)
                self.assertGreaterEqual(len(fn_lines), 5)
                slots = image.find_vtable_slot_overrides(0, filter_substr="PlainStruct")
                self.assertEqual(slots, {addrs["fn2"]: ["PlainStruct"]})

                ecs_all = image.scan_ecs_components()
                self.assertEqual(len(ecs_all), 1)
                self.assertEqual(ecs_all[0].hash_id, 0xCAFEBABE)
                self.assertEqual(ecs_all[0].name, "DemoFooComponent")
                self.assertEqual(image.find_ecs_by_hash(0xCAFEBABE), ecs_all[0])
                self.assertEqual(image.find_ecs_by_name("demofoo"), ecs_all)

                from_lines = image.disassemble_function(addrs["fn1"], from_va=addrs["fn1"] + 0x10)
                self.assertTrue(from_lines[0].startswith(f"{addrs['fn1'] + 0xF:#x}:"))

    def test_ecs_from_infunc_and_rtti_hints_cli(self) -> None:
        blob, addrs = synthetic_rich_pe()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.exe"
            path.write_bytes(blob)

            def run_cli(*argv: str) -> tuple[int, str]:
                buf = io.StringIO()
                with (
                    mock.patch.object(sys, "argv", ["pe_map.py", "--exe", str(path), *argv]),
                    contextlib.redirect_stdout(buf),
                ):
                    rc = pe_map.main()
                return rc, buf.getvalue()

            rc, out = run_cli("--ecs-hash", "0xCAFEBABE")
            self.assertEqual(rc, 0)
            self.assertIn("DemoFooComponent", out)

            rc, out = run_cli("--ecs-find", "DemoFoo", "--with-xrefs")
            self.assertEqual(rc, 0)
            self.assertIn("0xcafebabe", out)
            self.assertIn("use ", out)

            rc, out = run_cli("--func", hex(addrs["fn1"]), "--from", hex(addrs["fn1"] + 0x10))
            self.assertEqual(rc, 0)
            self.assertIn(f"from {addrs['fn1'] + 0x10:#x}", out)

            rc, out = run_cli("--find-f32", "0.6", "--with-xrefs", "--in-func", hex(addrs["fn1"]))
            self.assertEqual(rc, 0)
            self.assertIn(hex(addrs["fn1"]), out)
            self.assertNotIn(hex(addrs["leaf"]), out)

            rc, out = run_cli("--rtti", "DemoFoo")
            self.assertEqual(rc, 0)
            self.assertIn("no RTTI matches for 'DemoFoo'", out)
            self.assertIn("hint: matching ECS component(s): DemoFooComponent (0xcafebabe)", out)
            self.assertIn("hint: matching string(s):", out)

    def test_real_binary_shapes(self) -> None:
        """Property check against the installed build; skipped when it is absent."""
        try:
            path = default_exe()
        except FileNotFoundError:
            self.skipTest("Minecraft.Windows.exe not installed")
        with PeImage(path) as image:
            self.assertGreater(image.image_base, 0)
            self.assertGreater(len(image.functions), 1000)
            text = next((s for s in image.sections if s.name == ".text"), None)
            self.assertIsNotNone(text)
            assert text is not None
            for probe in (text.va + 0x10, text.va + 0x1_0000, text.va + text.vsize // 2):
                found = image.functions.function_at(probe)
                if found is not None:
                    self.assertLessEqual(found[0], probe)
                    self.assertLess(probe, found[1])

            imports = image.parse_imports()
            self.assertGreater(len(imports), 100)
            self.assertTrue(any(e.symbol == "sinf" for e in imports))

            sinf_xrefs = image.find_import_xrefs("sinf")
            self.assertGreaterEqual(len(sinf_xrefs), 100)

            sin_table_xrefs = image.find_rip_xrefs(0x151D6973C)
            self.assertGreaterEqual(len(sin_table_xrefs), 200)
            self.assertTrue(any(x.function is None for x in sin_table_xrefs))

            disasm_lines = image.disassemble(0x146667B10, count=15)
            self.assertEqual(len(disasm_lines), 15)

            rtti = image.find_rtti("StyledWriter")
            self.assertGreaterEqual(len(rtti), 1)
            first = rtti[0]
            self.assertEqual(first.name, "Json::StyledWriter")
            self.assertGreater(len(first.methods), 0)
            self.assertGreater(len(first.xrefs), 0)
            self.assertEqual(image.find_rtti(first.vtable_va), [first])


if __name__ == "__main__":
    unittest.main()
