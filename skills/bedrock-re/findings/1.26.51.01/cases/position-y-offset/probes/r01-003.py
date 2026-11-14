"""Resolve ECS component ids to their names via the TLS cache-init site.

Each component id has exactly one `mov dword ptr [reg+4], imm32` site whose
function also loads the component name string and its length.

    py -3.11 out/comp_name.py 0xDE21C316 1965854212 45880112
"""

from __future__ import annotations

import struct
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "out")
import posy_func as pf  # noqa: E402


def main() -> int:
    pf.load()
    data = pf._DATA
    text_va = 0x140001000
    raddr, rsize = 0x400, 0xE5A5C00
    blob = data[raddr : raddr + rsize]
    md_rd = None
    import capstone

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    for arg in sys.argv[1:]:
        cid = int(arg, 0) & 0xFFFFFFFF
        pat = struct.pack("<I", cid)
        site = None
        for modrm in range(0x40, 0x48):
            p = bytes([0xC7, modrm, 0x04]) + pat
            j = blob.find(p)
            if j >= 0:
                site = text_va + j
                break
        if site is None:
            print(f"0x{cid:08X} ({cid}): no cache-init site")
            continue
        f = pf.func_of(site)
        name = ""
        length = None
        if f:
            code = pf.read_at(f[0], f[1] - f[0])
            for ins in md.disasm(code, f[0]):
                if ins.mnemonic == "lea":
                    for op in ins.operands:
                        if (
                            op.type == capstone.x86.X86_OP_MEM
                            and op.mem.base == capstone.x86.X86_REG_RIP
                        ):
                            target = ins.address + ins.size + op.mem.disp
                            o = pf.va_to_off(target)
                            if o is None:
                                continue
                            j = data.find(b"\x00", o, o + 200)
                            if j > o:
                                chunk = data[o:j]
                                if 3 <= len(chunk) <= 160 and all(32 <= c < 127 for c in chunk):
                                    name = chunk.decode()
                if ins.mnemonic == "mov" and "qword ptr [rax + 0x10]" in ins.op_str:
                    try:
                        length = int(ins.op_str.split(", ")[1], 0)
                    except (IndexError, ValueError):
                        pass
        print(f"0x{cid:08X} ({cid}): {name!r} len={length} site=0x{site:X}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
