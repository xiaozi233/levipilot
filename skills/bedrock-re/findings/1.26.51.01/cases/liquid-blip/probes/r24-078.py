"""Live-only Liquid Blip recheck. Reuses published entry/store addresses.

No instruction decoding, memory writes, velocity injection or static analysis.
The DR snapshots are observation-only; input uses the normal HID path.
"""

from __future__ import annotations

import json
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from driver.harness.facade import Harness
from driver.harness.lab import Builder, settle

ROOT = Path("out/liquid-blip-recheck-20261110")
ROOT.mkdir(parents=True, exist_ok=True)
X, WALL_Z, OPEN_Z, SURFACE = 354.5, 300.32, 303.5, -46.0


def save(name, data):
    (ROOT / name).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def public(state):
    return {k: v for k, v in state.items() if k not in ("name", "uuid", "item")}


def f32(v):
    return struct.unpack("<f", struct.pack("<f", v))[0]


def floats(snap):
    assert snap["ok"], snap
    return list(struct.unpack(f"<{snap['size'] // 4}f", bytes.fromhex(snap["hex"])))


def area(h):
    positions = [[x, y, z] for x in range(349, 360) for y in range(-49, -35) for z in range(298, 309)]
    rows = []
    for start in range(0, len(positions), 100):
        rows.extend(h.client.blocks(positions[start : start + 100])["blocks"])
    assert len(rows) == len(positions)
    return rows


def build(h, liquid):
    h.client.teleport(X, -60, 312.5, yaw=180, pitch=0)
    h.client.wait_ticks(8)
    checks = Builder(h.client).build(
        [
            "fill 349 -49 298 359 -36 308 air",
            "fill 350 -49 299 358 -49 307 stone",
            "fill 350 -48 299 358 -38 299 stone",
            "fill 350 -48 307 358 -47 307 stone",
            "fill 350 -48 299 350 -47 307 stone",
            "fill 358 -48 299 358 -47 307 stone",
            f"fill 351 -48 300 357 -47 306 {liquid}",
        ],
        {(354, -49, 303): "minecraft:stone", (354, -48, 303): f"minecraft:{liquid}",
         (354, -47, 303): f"minecraft:{liquid}", (354, -46, 303): "minecraft:air",
         (354, -38, 299): "minecraft:stone"},
    )
    assert all(c.ok for c in checks), checks
    h.client.wait_ticks(10)
    geometry = h.client.blocks([[354, y, z] for z in (299, 300, 303) for y in (-49, -48, -47, -46, -38)])
    depths = {str(y): h.client.liquid_depth([354, y, 300]) for y in (-48, -47)}
    print("GEOMETRY", liquid, json.dumps(depths), flush=True)
    return {"blocks": geometry, "depths": depths}


def analyze(data, meta):
    hits = data["watch"]["hits"]
    tags = {w["id"]: w["tag"] for w in data["watches"]}
    entries = [hit for hit in hits if tags[hit["watch"]] == "lb-entry"]
    writes = [hit for hit in hits if tags[hit["watch"]] == "lb-store"]
    values = []
    for hit in entries:
        snaps = {s["reg"]: s for s in hit["snapshots"]}
        scope = next((side for side in ("client", "server") if snaps["r9"]["address"] == meta[side]["address"]), "other")
        box, travel, state = [floats(snaps[r]) for r in ("rdx", "r8", "r9")]
        dy = f32(f32(f32(travel[5] - state[1]) + f32(0.6)) + state[7])
        gap = f32(box[1] + dy) - SURFACE
        next_qpc = min((e["qpc"] for e in entries if e["tid"] == hit["tid"] and e["qpc"] > hit["qpc"]), default=float("inf"))
        overwrites = [w for w in writes if w["tid"] == hit["tid"] and hit["qpc"] < w["qpc"] < next_qpc]
        candidates = [r for r in data["record"] if max(abs(state[i] - r[k]) for i, k in enumerate(("x", "y", "z"))) < 1e-5]
        candidates.sort(key=lambda r: abs(r["tick"] - hit["tick"] - 1))
        record = candidates[0] if candidates and scope == "client" else None
        values.append({"scope": scope, "tick": hit["tick"], "tid": hit["tid"], "qpc": hit["qpc"],
                       "feet_y": box[1], "position_y": state[1], "travel_y": travel[5],
                       "previous_y": state[4], "raw_vy": state[7], "v_move": state[1] - travel[5],
                       "dy": dy, "gap": gap, "overwritten": bool(overwrites), "record": record,
                       "snapshots": hit["snapshots"]})
    assert data["watch"]["lost"] == 0, data["watch"]["lost"]
    bad = [v for v in values if (v["gap"] > 0) != v["overwritten"]]
    return {"entries": values, "writes": len(writes), "gap_mismatches": len(bad), "mismatches": bad,
            "client_entries": sum(v["scope"] == "client" for v in values),
            "server_entries": sum(v["scope"] == "server" for v in values),
            "record_blips": [r["tick"] for r in data["record"] if abs(r["vy"] - .3) < .0001],
            "max_vy": max(r["vy"] for r in data["record"]),
            "max_feet": max(r["feet_y"] for r in data["record"])}


def run(h, meta, name, height, ticks, *, jump=False, amp=None, wall=True, dry=False):
    h.client.set_input()
    h.command("effect @s levitation 0")
    h.client.teleport(X, -48, OPEN_Z, yaw=180, pitch=0)
    gate = settle(h.client, ticks=4, timeout=15, on_ground=True, input_sprint=False, include_vertical=True)
    assert gate["ok"], gate
    z = 312.5 if dry else WALL_Z if wall else OPEN_Z
    h.client.teleport(X, height, z, yaw=180, pitch=0)
    h.client.wait_ticks(4)
    if amp is not None:
        h.command(f"effect @s levitation 120 {amp} true")
        h.client.wait_ticks(2)
    state = h.state()
    assert not state["flying"] and not state["mayfly"] and abs(state["z"] - z) < .05, state
    watches = []
    t0 = h.client.watch_list()["head"]
    started = None
    data = {"name": name, "height": height, "ticks": ticks, "jump": jump, "amp": amp,
            "wall": wall, "start": public(state)}
    try:
        watches.append(h.client.watch_set(meta["entry_address"], size=1, mode="x", thread="both", depth=1,
                                         sample=True, tag="lb-entry", snapshots=[{"reg": "rdx", "size": 32},
                                         {"reg": "r8", "size": 40}, {"reg": "r9", "size": 36}]))
        # Store offset was already established in R-F16/r19-058; no new decoding.
        for tid in watches[0]["threads"]:
            watches.append(h.client.watch_set(meta["entry_address"] + 0x146, size=1, mode="x", thread=tid,
                                             depth=1, sample=True, tag="lb-store"))
        started = h.record_bin_start(period=1)
        h.client.set_input(forward=0 if dry else 1, jump=jump, sprint=False, ticks=ticks)
        h.client.wait_ticks(ticks + 3)
    finally:
        for w in watches:
            h.client.watch_clear(w["id"])
        if started:
            data["stopped"] = h.record_bin_stop()
            data["record"] = [r.to_dict() for r in h.record_dump_all(record_id=started["record_id"])]
        data["watches"] = watches
        data["watch"] = h.client.watch_drain(since=t0, limit=16384)
        for hit in data["watch"]["hits"]:
            hit.pop("code_hex", None)
        h.client.set_input()
        h.command("effect @s levitation 0")
        data["end"] = public(h.state())
        save(name + ".json", data)
    analysis = analyze(data, meta)
    save(name + "-analysis.json", analysis)
    result = {"name": name, "record_rows": len(data["record"]), "entries": len(analysis["entries"]),
              **{k: v for k, v in analysis.items() if k not in ("entries", "mismatches")}}
    print("RUN", json.dumps(result), flush=True)
    return result


def main():
    summary, cleanup = {}, []
    with Harness() as h:
        original = h.state()
        assert original["in_world"] and original["dimension"] == 0 and original["gamemode"] == 0
        assert not original["effects"] and not original["flying"] and not original["mayfly"]
        assert not any(original["input"][k] for k in ("w", "a", "s", "d", "jump", "sneak", "locked"))
        status = h.client.status()
        assert not any(status[k] for k in ("record_active", "recording", "macro_recording", "macro_playing", "watch_count"))
        before = original["tick"]
        time.sleep(.7)
        after = h.state()["tick"]
        assert after - before >= 8, "background tick is paused"
        roles = json.loads(Path("docs/roles/1.26.51.01.json").read_text(encoding="utf-8"))
        module_base = h.client.diag()["module_base"]
        meta = {"original": public(original), "background_tick_delta": after - before,
                "entry_address": module_base + int(roles["roles"]["@liquid_exit_blip"]["address"], 16) - int(roles["image_base"], 16),
                "client": h.client.ecs_context(original["context"], 0x1B5D5238),
                "server": h.client.ecs_component(original["runtime_id"], 0x1B5D5238, length=36),
                "scope": "dynamic only; reused published R-F16/r19 observation points; no static analysis"}
        save("metadata.json", meta)
        h.client.input_lock(True)
        built = False
        try:
            before_area = area(h)
            save("area-before.json", before_area)
            assert all(b["name"] == "minecraft:air" for b in before_area), "test volume is not empty"
            h.command("effect @s water_breathing 1200 0 true")
            h.command("effect @s fire_resistance 1200 0 true")
            built = True
            summary["water_geometry"] = build(h, "water")
            choices = [
                ("W-standing", -60, 18, {"dry": True}),
                ("W-slow-a", -47.2, 65, {"amp": 0}),
                ("W-slow-b", -47.05, 60, {"amp": 0}),
                ("W-jump-a", -47.7, 50, {"jump": True}),
                ("W-jump-b", -47.45, 50, {"jump": True}),
                ("W-sink", -46.85, 24, {}),
                ("W-lev-nowall", -47.2, 28, {"amp": 0, "wall": False}),
            ]
            for name, height, ticks, args in choices:
                summary[name] = run(h, meta, name, height, ticks, **args)
                save("summary.json", summary)
            summary["lava_geometry"] = build(h, "lava")
            choices = [
                ("L-jump-035", -46.35, 60, {"jump": True}),
                ("L-jump-060-a", -46.6, 65, {"jump": True}),
                ("L-jump-080", -46.8, 75, {"jump": True}),
                ("L-jump-100", -47.0, 85, {"jump": True}),
                ("L-jump-180", -47.8, 110, {"jump": True}),
                ("L-levII-100-a", -47.0, 130, {"amp": 1}),
                ("L-jump-nowall", -46.6, 25, {"jump": True, "wall": False}),
                ("L-sink", -46.5, 24, {}),
                ("L-jump-060-b", -46.6, 65, {"jump": True}),
                ("L-levII-100-b", -47.0, 130, {"amp": 1}),
            ]
            for name, height, ticks, args in choices:
                summary[name] = run(h, meta, name, height, ticks, **args)
                save("summary.json", summary)
        finally:
            for w in h.client.watch_list()["watches"]:
                if w.get("tag", "").startswith("lb-"):
                    try:
                        h.client.watch_clear(w["id"])
                    except Exception as exc:
                        cleanup.append(str(exc))
            actions = [lambda: h.client.set_input(),
                       lambda: h.command("effect @s levitation 0"),
                       lambda: h.command("effect @s water_breathing 0"),
                       lambda: h.command("effect @s fire_resistance 0"),
                       lambda: h.client.teleport(original["feet_x"], original["feet_y"], original["feet_z"],
                                                yaw=original["yaw"], pitch=original["pitch"])]
            if built:
                actions.append(lambda: h.command("fill 349 -49 298 359 -36 308 air"))
            actions.append(lambda: h.client.input_lock(original["input"]["locked"]))
            for action in actions:
                try:
                    action()
                except Exception as exc:
                    cleanup.append(str(exc))
            h.client.wait_ticks(10)
            final = public(h.state())
            restored_area = area(h)
            integrity = {"cleanup_errors": cleanup, "state": final, "watches": h.client.watch_list(),
                         "area_restored": all(b["name"] == "minecraft:air" for b in restored_area),
                         "background_tick_delta": after - before}
            save("integrity.json", integrity)
            save("summary.json", summary)
            print("RESTORED", json.dumps(integrity), flush=True)
            assert not cleanup and integrity["area_restored"] and not final["effects"]
            assert not final["input"]["locked"] and final["on_ground"] and final["health"] == original["health"]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
