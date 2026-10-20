import ida_bytes
import ida_funcs
import ida_ua
import idautils
import idc

TARGET = 0x18010EE68
lo, hi = 0x180001000, 0x1800CD000

print("global dword_18010F380 =", hex(ida_bytes.get_dword(0x18010F380)))
print("global byte_18010F510 =", hex(ida_bytes.get_byte(0x18010F510)))
for head in idautils.Heads(lo, hi):
    if idc.print_insn_mnem(head) != "call":
        continue
    if idc.get_operand_value(head, 0) != TARGET:
        continue
    print(f"\n=== call at {head:X} func {idc.get_func_name(head)} ===")
    ea = head
    prev = []
    for _ in range(24):
        ea = idc.prev_head(ea)
        if ea == idc.BADADDR:
            break
        prev.append(ea)
    for x in reversed(prev):
        print(f"{x:X}: {idc.generate_disasm_line(x, 0)}")
    print(f"{head:X}: {idc.generate_disasm_line(head, 0)}")

idc.qexit(0)