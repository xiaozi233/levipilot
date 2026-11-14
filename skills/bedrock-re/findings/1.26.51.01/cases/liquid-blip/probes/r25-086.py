"""Independent re-check of the Liquid Blip audit (static scan + dynamic data).

Read-only: scans the on-disk game image for the 0.3f store sites and the float
constants, dumps the two block-source vtable slots, then recomputes the whole
`liquid-blip-recheck.json` model from raw snapshots (no stored counts).

    py -3.11 out/liquid_blip_audit_check.py
"""

from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "driver"))
import pe_map  # noqa: E402

RECHECK = ROOT / "skills/bedrock-re/findings/1.26.51.01/cases/liquid-blip/probes/liquid-blip-recheck.json"
SURFACE = -46.0


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def fl(hexs: str, i: int) -> float:
    return struct.unpack_from("<f", bytes.fromhex(hexs), i * 4)[0]


def static_part() -> None:
    exe = pe_map.default_exe()
    img = pe_map.PeImage(Path(exe))
    text = next(s for s in img.sections if s.name == ".text")
    data = img.read(img.image_base + text.va, text.vsize)
    base = img.image_base + text.va
    print("IMAGE", Path(exe).name, "base", hex(img.image_base), "text", hex(text.vsize))

    # 1) every immediate store of 0.3f into [reg+0x1C]
    hits = [hex(base + m.start()) for m in re.finditer(rb"\xc7([\x40-\x7f])\x1c\x9a\x99\x99\x3e", data)]
    print("STORE_0P3_INTO_1C", hits)

    # 2) float constants
    for name, va in (
        ("mHeightOffset(1.62001f)", 0x14E6AE444),
        ("lift(0.6f)", 0x14E6AE498),
        ("half(0.5f)", 0x14E5C6370),
        ("sleep_k(0.4f)", 0x14E602290),
        ("pose_k(0.2f)", 0x14E6AE454),
    ):
        raw = img.read(va, 4)
        print("CONST", name, hex(va), raw.hex(" "), struct.unpack("<f", raw)[0])

    # 3) block-source vtable slots used by the worker
    for slot in (0x38, 0xA0):
        raw = img.read(0x14E8554E0 + slot, 8)
        print("VTABLE", hex(0x14E8554E0 + slot), "->", hex(struct.unpack("<Q", raw)[0]))
    img.close()

    for va in (0x142D8A050, 0x1482A8E46):
        found = pe_map.PeImage(Path(exe))
        print("FUNC", hex(va), [hex(x) for x in (found.function_at(va) or ())])
        found.close()


def dynamic_part() -> None:
    doc = json.loads(RECHECK.read_text(encoding="utf-8"))
    counts = {
        "entries": 0,
        "client": 0,
        "server": 0,
        "dy_mismatch": 0,
        "travel_ne_prev": 0,
        "gap_mismatch": 0,
        "overwritten": 0,
        "overwrite_mismatch": 0,
        "trigger_neg_gap": 0,
        "silent_pos_gap": 0,
        "zero_gap_trigger": 0,
        "ivcv_vs_record": 0,
    }
    gaps: list[tuple[float, bool, str]] = []
    raws_at_trigger: list[float] = []
    for name, run in doc["runs"].items():
        if "analysis" not in run:
            continue
        tags = {w["id"]: w["tag"] for w in run["watches"]}
        entries = run["analysis"]["entries"]
        stores = [h for h in run["watch"]["hits"] if tags.get(h["watch"]) == "lb-store"]
        for i, entry in enumerate(entries):
            counts["entries"] += 1
            counts["client" if entry["scope"] == "client" else "server"] += 1
            snap = {s["reg"]: s["hex"] for s in entry["snapshots"]}
            feet = fl(snap["rdx"], 1)
            travel = fl(snap["r8"], 5)
            pos = fl(snap["r9"], 1)
            prev = fl(snap["r9"], 4)
            raw = fl(snap["r9"], 7)
            dy = f32(f32(f32(travel - pos) + f32(0.6)) + raw)
            gap = f32(f32(feet + dy) - SURFACE)
            if abs(dy - entry["dy"]) > 1e-6:
                counts["dy_mismatch"] += 1
            if travel != prev:
                counts["travel_ne_prev"] += 1
            if abs(gap - entry["gap"]) > 1e-5:
                counts["gap_mismatch"] += 1
            gaps.append((gap, entry["overwritten"], name))
            # independent write attribution: same thread, between this entry and the next
            nxt = [e for e in entries[i + 1 :] if e["tid"] == entry["tid"]]
            end = nxt[0]["qpc"] if nxt else float("inf")
            pred = any(
                s["tid"] == entry["tid"] and entry["qpc"] < s["qpc"] < end for s in stores
            )
            if entry["overwritten"]:
                counts["overwritten"] += 1
                raws_at_trigger.append(raw)
            if pred != entry["overwritten"]:
                counts["overwrite_mismatch"] += 1
            if entry["overwritten"] and gap < 0:
                counts["trigger_neg_gap"] += 1
            if (not entry["overwritten"]) and gap >= 0:
                counts["silent_pos_gap"] += 1
            if gap == 0.0 and entry["overwritten"]:
                counts["zero_gap_trigger"] += 1
            rec = entry.get("record")
            if rec is not None and abs(f32(pos - travel) - rec["vy"]) > 1e-9:
                counts["ivcv_vs_record"] += 1
    neg = sorted(g for g in gaps if not g[1])
    pos = sorted(g for g in gaps if g[1])
    print("COUNTS", json.dumps(counts, sort_keys=True))
    print("NEAREST_NEGATIVE", neg[-3:])
    print("SMALLEST_POSITIVE", pos[:3])
    print("MIN_RAW_AT_TRIGGER", min(raws_at_trigger))
    print("BOUNDARY_BAND"
          f" |gap|<0.02:{sum(1 for g in gaps if abs(g[0]) < 0.02)}"
          f" |gap|<0.05:{sum(1 for g in gaps if abs(g[0]) < 0.05)}")

    # 0.34 = 0.3 + 0.04 and the two air recurrences
    for name in ("W-jump-a", "L-jump-035", "W-jump-b", "L-plain-060", "L-levII-100-a"):
        rows = doc["runs"][name]["record"]
        seq = [(r["tick"], r["vy"]) for r in rows]
        for (t0, v0), (t1, v1) in zip(seq, seq[1:]):
            if abs(v1 - 0.3400001525878906) < 1e-9:
                print("PAIR_0P34", name, t0, v0, "->", t1, v1)
            if abs(v0 - 0.2999992370605469) < 1e-9:
                pred = f32(f32(0.98) * f32(v0 - f32(0.08)))
                pred_lev2 = f32(f32(0.98) * f32(f32(0.8) * v0 + f32(0.02)))
                print("AFTER_0P3", name, t0, "next", repr(v1),
                      "air_res", v1 - pred, "lev2_res", v1 - pred_lev2)


def main() -> int:
    static_part()
    dynamic_part()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())