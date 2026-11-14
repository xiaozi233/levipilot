"""Repeat missing wall gate, and lava controls without DR observation."""

import importlib.util
import json
import struct
from pathlib import Path

spec = importlib.util.spec_from_file_location("recheck", "out/liquid-blip-recheck.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.ROOT = Path("out/liquid-blip-recheck-repeat-20261110")
m.ROOT.mkdir(parents=True, exist_ok=True)


def plain(h, name, height, ticks):
    h.client.set_input()
    h.command("effect @s levitation 0")
    h.client.teleport(m.X, -48, m.OPEN_Z, yaw=180, pitch=0)
    assert m.settle(h.client, ticks=4, timeout=15, on_ground=True, input_sprint=False)["ok"]
    h.client.teleport(m.X, height, m.WALL_Z, yaw=180, pitch=0)
    h.client.wait_ticks(4)
    state = h.state()
    assert abs(state["z"] - m.WALL_Z) < .05 and not h.client.watch_list()["watches"]
    started = h.record_bin_start(period=1)
    try:
        h.client.set_input(forward=1, jump=True, ticks=ticks)
        h.client.wait_ticks(ticks + 3)
    finally:
        h.record_bin_stop()
        h.client.set_input()
    rows = [r.to_dict() for r in h.record_dump_all(record_id=started["record_id"])]
    result = {"name": name, "height": height, "ticks": ticks, "start": m.public(state), "record": rows,
              "record_blips": [r["tick"] for r in rows if abs(r["vy"] - .3) < .0001],
              "max_vy": max(r["vy"] for r in rows), "observers": "none; binary recording only"}
    m.save(name + ".json", result)
    print("PLAIN", json.dumps({k: v for k, v in result.items() if k not in ("record", "start")}), flush=True)
    return result


def main():
    summary = {}
    with m.Harness() as h:
        original = h.state()
        assert original["effects"] == [] and not original["flying"] and not original["mayfly"]
        assert not h.client.watch_list()["watches"]
        meta = json.loads(Path("out/liquid-blip-recheck-20261110/metadata.json").read_text())
        assert meta["original"]["context"] == original["context"]
        baseline = {"state": m.public(original)}
        for side in ("client", "server"):
            raw = h.client.mem_read(meta[side]["address"], 36)
            baseline[side] = {"reply": raw, "floats": list(struct.unpack("<9f", bytes.fromhex(raw["hex"])))}
        m.save("standing-raw.json", baseline)
        h.client.input_lock(True)
        try:
            assert all(b["name"] == "minecraft:air" for b in m.area(h))
            h.command("effect @s water_breathing 1200 0 true")
            h.command("effect @s fire_resistance 1200 0 true")
            summary["water_geometry"] = m.build(h, "water")
            summary["W-slow-a-repeat"] = m.run(h, meta, "W-slow-a-repeat", -47.2, 65, amp=0)
            summary["W-slow-b-repeat"] = m.run(h, meta, "W-slow-b-repeat", -47.05, 60, amp=0)
            summary["lava_geometry"] = m.build(h, "lava")
            for name, height, ticks in (("L-plain-060", -46.6, 65), ("L-plain-180", -47.8, 110)):
                r = plain(h, name, height, ticks)
                summary[name] = {k: v for k, v in r.items() if k not in ("record", "start")}
        finally:
            errors = []
            for w in h.client.watch_list()["watches"]:
                if w.get("tag", "").startswith("lb-"):
                    h.client.watch_clear(w["id"])
            for action in (
                lambda: h.client.set_input(),
                lambda: h.command("effect @s levitation 0"),
                lambda: h.command("effect @s fire_resistance 0"),
                lambda: h.command("effect @s water_breathing 0"),
                lambda: h.client.teleport(original["feet_x"], original["feet_y"], original["feet_z"],
                                         yaw=original["yaw"], pitch=original["pitch"]),
                lambda: h.command("fill 349 -49 298 359 -36 308 air"),
                lambda: h.client.input_lock(original["input"]["locked"]),
            ):
                try:
                    action()
                except Exception as exc:
                    errors.append(str(exc))
            h.client.wait_ticks(10)
            final = m.public(h.state())
            integrity = {"state": final, "cleanup_errors": errors, "watches": h.client.watch_list(),
                         "area_restored": all(b["name"] == "minecraft:air" for b in m.area(h))}
            m.save("integrity.json", integrity)
            m.save("summary.json", summary)
            print("RESTORED", json.dumps(integrity), flush=True)
            assert not errors and not final["effects"] and integrity["area_restored"] and not final["input"]["locked"]


if __name__ == "__main__":
    main()
