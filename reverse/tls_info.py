import ida_bytes
import ida_name
import ida_segment
import idc

for name in ["TlsIndex", "dword_18010F380"]:
    ea = ida_name.get_name_ea(idc.BADADDR, name)
    print(name, hex(ea), "seg", ida_segment.get_segm_name(ida_segment.getseg(ea)) if ea != idc.BADADDR else "?")
    if ea != idc.BADADDR:
        print("  bytes", ida_bytes.get_bytes(ea, 8).hex())
        print("  dword", hex(ida_bytes.get_dword(ea)))
idc.qexit(0)