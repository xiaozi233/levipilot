"""Summarise the static position-y-offset findings as a reproducible probe.

Re-derives, from the exe alone:
  * the single float32 1.62001f cell and every movss site that loads it,
  * which of those sites feed the OffsetsComponent height-offset writer,
  * the .pdata function boundaries for the roles added this round,
  * the float32 model of the measured 1.6200103759765625 tail.
"""

from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "out")
import posy_func as pf  # noqa: E402

CONST_1_62001 = 0x14E6AE444
SET_HEIGHT_OFFSET = 0x14253CFD0
OFFSETS_COMPONENT = 0xDE21C316


def main() -> int:
    pf.load()
    data = pf._DATA
    text_va = 0x140001000
    raddr, rsize = 0x400, 0xE5A5C00
    blob = data[raddr : raddr + rsize]

    out: dict[str, object] = {}

    # 1. the constant cell
    o = pf.va_to_off(CONST_1_62001)
    out["const_1_62001"] = {
        "va": hex(CONST_1_62001),
        "bytes": data[o : o + 4].hex(" "),
        "f32": struct.unpack("<f", data[o : o + 4])[0],
    }

    # 2. every movss that loads it
    sites = []
    for op in (0x10, 0x11):
        pat = re.compile(bytes([0xF3, 0x0F, op]) + rb"[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]")
        for m in pat.finditer(blob):
            i = m.start()
            if i + 8 > len(blob):
                continue
            disp = struct.unpack_from("<i", blob, i + 4)[0]
            if text_va + i + 8 + disp == CONST_1_62001:
                sites.append(text_va + i)
    out["movss_sites"] = [hex(s) for s in sites]

    # 3. direct callers of the height-offset writer
    callers = []
    s = 0
    while True:
        i = blob.find(b"\xe8", s)
        if i < 0:
            break
        if i + 5 <= len(blob):
            disp = struct.unpack_from("<i", blob, i + 1)[0]
            if text_va + i + 5 + disp == SET_HEIGHT_OFFSET:
                callers.append(text_va + i)
        s = i + 1
    out["set_height_offset_callers"] = [hex(c) for c in callers]
    out["set_height_offset_with_1_62001"] = [
        hex(c) for c in callers if any(abs(c - s) <= 16 for s in sites)
    ]

    # 4. function boundaries for the roles of this round
    roles = {
        "@actor_offset_set_height_offset": 0x14253CFD0,
        "@actor_offset_set_aabb_relative_scaling": 0x14253D810,
        "@vanilla_offset_system_tick": 0x1428A4D30,
        "@vanilla_offset_system_worker": 0x1428A4AB0,
        "@update_bounding_box_system_register": 0x1428A1610,
        "@update_bounding_box_system_single_tick": 0x1428B4E20,
        "@actor_set_pos_system_tick": 0x14901DB10,
        "@actor_set_pos_assert": 0x14901D080,
        "@get_attach_position_views": 0x1428CCDC0,
    }
    out["functions"] = {
        name: {"start": hex(a), "size": pf.func_of(a)[1] - pf.func_of(a)[0]}
        for name, a in roles.items()
    }

    # 5. float32 model of the measured tail
    f = lambda x: struct.unpack("f", struct.pack("f", x))[0]  # noqa: E731
    feet = -60.0
    out["float32_model"] = {
        "feet": feet,
        "mHeightOffset_f32": f(1.62001),
        "position_y_f32": f(f(feet) + f(1.62001)),
        "difference_double": f(f(feet) + f(1.62001)) - feet,
        "matches_measurement": f(f(feet) + f(1.62001)) - feet == 1.6200103759765625,
        "if_constant_were_1_62f": f(f(feet) + f(1.62)) - feet,
        "if_computed_1p8x0p9_plus_1e-5": f(f(1.8) * f(0.9) + f(1e-5)),
    }

    print(json.dumps(out, indent=1))
    Path("out/posy-summary.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
