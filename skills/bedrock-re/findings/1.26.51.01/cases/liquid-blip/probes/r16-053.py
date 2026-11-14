"""Dynamic component availability check during liquid movement; no static RE."""
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from driver.harness.facade import Harness

spec = importlib.util.spec_from_file_location("liquid_measure", "out/liquid_blip_dynamic_review.py")
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)


def main():
    rows = []
    with Harness() as h:
        original = h.state()
        assert not original["effects"] and not original["flying"] and not original["mayfly"]
        h.client.input_lock(True)
        try:
            measure.build(h)
            h.command("effect @s water_breathing 120 0 true")
            h.client.teleport(measure.X, -47.5, measure.Z, yaw=180, pitch=0)
            h.client.wait_ticks(8)
            h.client.set_input(forward=1, ticks=80)
            for i in range(6):
                state = h.state()
                row = {"tick": state["tick"], "feet_y": state["feet_y"], "in_water": state["in_water"]}
                for side in ("client", "server"):
                    try:
                        if side == "client":
                            reply = h.client.ecs_context(state["context"], 0x39CF8D64)
                            reply["raw"] = h.client.mem_read(reply["address"], 40)
                        else:
                            reply = h.client.ecs_component(state["runtime_id"], 0x39CF8D64, length=40)
                        row[side] = reply
                    except Exception as exc:
                        row[side] = {"error": str(exc)}
                rows.append(row)
                print(json.dumps(row), flush=True)
        finally:
            for action in (
                lambda: h.client.set_input(),
                lambda: h.command("effect @s water_breathing 0"),
                lambda: h.client.teleport(original["feet_x"], original["feet_y"], original["feet_z"], yaw=original["yaw"], pitch=original["pitch"]),
                lambda: h.command("fill 349 -49 298 359 -36 308 air"),
                lambda: h.client.input_lock(original["input"]["locked"]),
            ):
                action()
    Path("out/liquid-blip-dynamic-review/travel-preflight.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
