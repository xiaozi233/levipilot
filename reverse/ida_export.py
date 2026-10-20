"""Export useful facts from an IDA .i64 database (run with idat -A -S)."""
import json
import os
import sys

import ida_auto
import ida_bytes
import ida_entry
import ida_funcs
import ida_hexrays
import ida_nalt
import ida_segment
import ida_ua
import idautils
import idc

OUT = os.environ.get("IDA_EXPORT_DIR", r".\reverse\ida_out")


def main():
    os.makedirs(OUT, exist_ok=True)
    ida_auto.auto_wait()

    with open(os.path.join(OUT, "segments.tsv"), "w", encoding="utf-8") as f:
        f.write("name\tstart\tend\tperm\tclass\n")
        for s in idautils.Segments():
            seg = ida_segment.getseg(s)
            if not seg:
                continue
            f.write(
                f"{ida_segment.get_segm_name(seg)}\t{seg.start_ea:X}\t{seg.end_ea:X}\t"
                f"{seg.perm}\t{ida_segment.get_segm_class(seg)}\n"
            )

    with open(os.path.join(OUT, "entries.tsv"), "w", encoding="utf-8") as f:
        f.write("ordinal\taddress\tname\tforwarder\n")
        for i in range(ida_entry.get_entry_qty()):
            ord_ = ida_entry.get_entry_ordinal(i)
            ea = ida_entry.get_entry(ord_)
            f.write(
                f"{ord_}\t{ea:X}\t{ida_entry.get_entry_name(ord_)}\t"
                f"{ida_entry.get_entry_forwarder(ord_) or ''}\n"
            )

    funcs = []
    for ea in idautils.Functions():
        fn = ida_funcs.get_func(ea)
        if not fn:
            continue
        funcs.append((fn.start_ea, fn.end_ea - fn.start_ea, idc.get_func_name(fn.start_ea)))
    funcs.sort()
    with open(os.path.join(OUT, "functions.tsv"), "w", encoding="utf-8") as f:
        f.write("address\tsize\tname\n")
        for a, sz, name in funcs:
            f.write(f"{a:X}\t{sz}\t{name}\n")

    with open(os.path.join(OUT, "imports.tsv"), "w", encoding="utf-8") as f:
        f.write("address\tmodule\tname\n")
        for i in range(ida_nalt.get_import_module_qty()):
            mod = ida_nalt.get_import_module_name(i) or ""

            def cb(ea, name, ordinal, _mod=mod):
                f.write(f"{ea:X}\t{_mod}\t{name or '#' + str(ordinal)}\n")
                return True

            ida_nalt.enum_import_names(i, cb)

    with open(os.path.join(OUT, "exports.tsv"), "w", encoding="utf-8") as f:
        f.write("address\tname\n")
        for i in range(ida_entry.get_entry_qty()):
            ord_ = ida_entry.get_entry_ordinal(i)
            f.write(f"{ida_entry.get_entry(ord_):X}\t{ida_entry.get_entry_name(ord_)}\n")

    # Strings with xrefs, packed as JSONL so we can grep and then resolve addresses.
    n = 0
    with open(os.path.join(OUT, "strings.jsonl"), "w", encoding="utf-8") as f:
        for s in idautils.Strings():
            try:
                val = str(s)
            except Exception:
                continue
            if len(val) < 2:
                continue
            refs = []
            for x in idautils.XrefsTo(s.ea, 0):
                refs.append({"from": f"{x.frm:X}", "type": int(x.type)})
            f.write(
                json.dumps(
                    {"ea": f"{s.ea:X}", "type": int(s.strtype), "len": s.length,
                     "value": val, "xrefs": refs},
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1

    # Basic instruction survey: count, useful for sanity.
    print(f"segments={len(list(idautils.Segments()))} funcs={len(funcs)} strings={n}")

    # Decompile everything only if asked; the first pass is cheap.
    if os.environ.get("IDA_DECOMPILE_ALL") == "1":
        decompile_all(funcs)


def decompile_all(funcs):
    ida_hexrays.init_hexrays_plugin()
    path = os.path.join(OUT, "pseudocode.c")
    with open(path, "w", encoding="utf-8") as f:
        for a, sz, name in funcs:
            try:
                cf = ida_hexrays.decompile(a)
            except Exception as exc:
                f.write(f"\n/* {name} @ {a:X}: decompile failed: {exc} */\n")
                continue
            if cf is None:
                f.write(f"\n/* {name} @ {a:X}: null */\n")
                continue
            f.write(f"\n/* ===== {name} @ {a:X} size={sz} ===== */\n")
            f.write(str(cf))
            f.write("\n")
            f.flush()


if __name__ == "__main__":
    main()
    idc.qexit(0)