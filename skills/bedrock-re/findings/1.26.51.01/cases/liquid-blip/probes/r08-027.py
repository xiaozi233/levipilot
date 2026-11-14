"""Liquid blip probe: fall back into the water and cross the 0.6 band downwards.

r08-025 (teleport + sample) and r08-026 (rise then sink) both failed to reach the
band: the player never got above feet -46.6, so neither run could tell the models
apart.  This probe gets there without any teleport in the measured window:

1. levitation lifts the player until they are clearly out of the water;
2. the effect is cleared and the player falls back, so the feet cross the band
   (surface - 0.6 .. surface) *downwards* with a large negative vy.

The two readings of the formula now disagree by 0.4 blocks, which is easy to see:

* net lift 0.6 (the +VelY term cancels) -> the blip fires as soon as the feet reach
  -46.6, whatever the fall speed;
* lift 0.6 + VelY                        -> with vy about -0.4 the box bottom sits at
  feet + 0.2, so nothing fires until the fall has slowed down.

A no-wall run is the HorizontalCollisionFlag control.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
OUT = Path(__file__).resolve().parent

from driver.harness.facade import Harness  # noqa: E402
from driver.harness.lab import Builder, settle  # noqa: E402

X0, X1 = 351, 357
Z0, Z1 = 300, 306
WALL_Y = (-48, -47)
WALL_Z = Z0 + 0.28


def build_pool(h: Harness, b: Builder) -> None:
    h.command(f"tp @s {X0 + 3.5} -46 {Z0 + 3.5} 0 0")
    h.client.wait_ticks(20)
    x0, x1, z0, z1 = X0 - 1, X1 + 1, Z0 - 1, Z1 + 1
    y0, y1 = WALL_Y
    h.command(f"fill {x0 - 1} {y0 - 1} {z0 - 1} {x1 + 1} {y1 + 3} {z1 + 1} air")
    h.command(f"fill {x0} {y0 - 1} {z0} {x1} {y0 - 1} {z1} stone")
    h.command(f"fill {x0} {y0} {z0} {x1} {y1} {z0} stone")
    h.command(f"fill {x0} {y0} {z1} {x1} {y1} {z1} stone")
    h.command(f"fill {x0} {y0} {z0} {x0} {y1} {z1} stone")
    h.command(f"fill {x1} {y0} {z0} {x1} {y1} {z1} stone")
    h.command(f"fill {X0} {y0} {Z0} {X1} {y1} {Z1} water")
    h.client.wait_ticks(4)
    b.poke(X0, y0, Z0)


def run(
    h: Harness,
    label: str,
    tp: tuple[float, float, float],
    rise_ticks: int,
    sink_ticks: int,
) -> dict:
    x, y, z = tp
    h.command(f"tp @s {x} {y} {z} 180 0")
    h.client.wait_ticks(4)
    h.command("effect @s levitation 300 0 true")
    h.client.set_input(forward=1.0, jump=False, ticks=rise_ticks + sink_ticks + 6)
    t0 = int(h.state().get("tick", 0))
    h.record_bin_start(period=1)
    h.client.wait_ticks(rise_ticks)
    h.command("effect @s clear")
    h.client.wait_ticks(sink_ticks + 2)
    h.record_bin_stop()
    h.command("effect @s water_breathing 600 0 true")
    rows = sorted((r for r in h.record_dump_all() if r.tick > t0), key=lambda r: r.tick)
    out = {
        "label": label,
        "ticks": len(rows),
        "vy": [round(r.vy, 4) for r in rows],
        "y": [round(r.feet_y, 3) for r in rows],
        "z": [round(r.z, 3) for r in rows],
        "in_water": [bool(r.in_water) for r in rows],
        "rise_count_03": sum(
            1 for i, r in enumerate(rows) if i < rise_ticks and abs(r.vy - 0.3) < 0.002
        ),
        "fall_count_03": sum(
            1 for i, r in enumerate(rows) if i >= rise_ticks and abs(r.vy - 0.3) < 0.002
        ),
    }
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return out


def main() -> int:
    with Harness() as h:
        b = Builder(h.client)
        h.client.input_lock(True)
        h.stop()

        state = h.state()
        if state.get("flying") or state.get("mayfly"):
            h.command("gamemode creative")
            h.client.wait_ticks(5)
            h.command("gamemode survival")
            h.client.wait_ticks(10)
        h.command("effect @s water_breathing 600 0 true")
        print("settle:", json.dumps(settle(h.client, ticks=5, timeout=25.0).get("ok")), flush=True)

        build_pool(h, b)

        runs: dict[str, dict] = {}
        runs["fall-in"] = run(h, "fall-in", (X0 + 3.5, -47.2, WALL_Z), 42, 26)
        runs["fall-in-nowall"] = run(h, "fall-in-nowall", (X0 + 3.5, -47.2, Z0 + 5.5), 42, 26)

        h.command("effect @s clear")
        h.command(f"fill {X0 - 3} -50 {Z0 - 3} {X1 + 3} -42 {Z1 + 3} air")
        h.command("effect @s water_breathing 600 0 true")
        h.client.input_lock(False)

        summary = {
            k: {
                "rise_count_03": v["rise_count_03"],
                "fall_count_03": v["fall_count_03"],
                "y": v["y"],
                "vy": v["vy"],
            }
            for k, v in runs.items()
        }
        print("SUMMARY " + json.dumps(summary, ensure_ascii=False))
        with open(OUT / "liquid-blip-fallin.json", "w", encoding="utf-8") as fh:
            json.dump(runs, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
