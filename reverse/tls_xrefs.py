import idautils
import ida_bytes
import ida_name
import idc

ea = ida_name.get_name_ea(idc.BADADDR, "TlsIndex")
print("TlsIndex", hex(ea))
for x in idautils.XrefsTo(ea):
    print(f"\nref {x.frm:X} type {x.type}")
    cur = x.frm
    start = cur
    for _ in range(10):
        start = idc.prev_head(start)
    p = start
    for _ in range(22):
        print(f"{p:X}: {idc.generate_disasm_line(p, 0)}")
        p = idc.next_head(p)
idc.qexit(0)