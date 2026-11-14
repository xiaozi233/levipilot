"""Liquid blip probe: the decisive test between the positional and the vy model.

Static closure (R-F16) says the probe box is the player AABB lifted by a *net* 0.6
(the +VelY term is cancelled by MobTravel's position delta), so the real condition
is positional: the feet must sit within 0.6 of the surface.  The older reading said
"vy > 0".  The two models disagree exactly here: a player who is *sinking* (vy < 0)
but starts above the threshold.

* positional model -> the blip fires immediately and keeps firing while the feet
  stay inside the 0.6 band, so the player hovers/climbs out instead of sinking;
* vy > 0 model     -> nothing happens, the player keeps sinking.

The probe also scans the feet height around the predicted threshold
(surface - 0.6 = -46.6, shifted by the first-tick vy) and runs no-wall controls.
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
SURFACE = -46.0
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
    ticks: int,
    *,
    forward: float = 1.0,
) -> dict:
    x, y, z = tp
    h.command(f"tp @s {x} {y} {z} 180 0")
    h.client.wait_ticks(3)
    t0 = int(h.state().get("tick", 0))
    h.record_bin_start(period=1)
    h.client.set_input(forward=forward, jump=False, ticks=ticks)
    h.client.wait_ticks(ticks + 2)
    h.record_bin_stop()
    rows = sorted((r for r in h.record_dump_all() if r.tick > t0), key=lambda r: r.tick)
    out = {
        "label": label,
        "tp_y": y,
        "ticks": len(rows),
        "vy": [round(r.vy, 4) for r in rows],
        "y": [round(r.feet_y, 3) for r in rows],
        "in_water": [bool(r.in_water) for r in rows],
        "count_03": sum(1 for r in rows if abs(r.vy - 0.3) < 0.002),
        "first_03_tick": next((i for i, r in enumerate(rows) if abs(r.vy - 0.3) < 0.002), None),
        "first_y": round(rows[0].feet_y, 3) if rows else None,
        "first_vy": round(rows[0].vy, 4) if rows else None,
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
        # 1. Decisive: sinking player that starts 0.3 above the threshold, no push at all.
        runs["sink-high"] = run(h, "sink-high", (X0 + 3.5, -46.3, WALL_Z), 18)
        # 2. Same but no wall -> the HorizontalCollisionFlag filter must keep it quiet.
        runs["sink-high-nowall"] = run(h, "sink-high-nowall", (X0 + 3.5, -46.3, Z0 + 5.5), 18)
        # 3. Height scan around the predicted threshold (surface - 0.6, plus the
        #    first-tick vy of about -0.013 that the record shows after a teleport).
        for y in (-46.40, -46.50, -46.55, -46.60, -46.65, -46.70, -46.80):
            key = f"scan{int(round((y + 46.0) * 100)):03d}"
            runs[key] = run(h, f"scan-{y}", (X0 + 3.5, y, WALL_Z), 5)
        # 4. Deep control: the box never clears the water.
        runs["deep"] = run(h, "deep", (X0 + 3.5, -47.6, WALL_Z), 8)

        h.command("effect @s clear")
        h.command(f"fill {X0 - 3} -50 {Z0 - 3} {X1 + 3} -42 {Z1 + 3} air")
        h.command("effect @s water_breathing 600 0 true")
        h.client.input_lock(False)

        summary = {
            k: {
                "count_03": v["count_03"],
                "first_03_tick": v["first_03_tick"],
                "first_y": v["first_y"],
                "first_vy": v["first_vy"],
                "max_vy": round(max(v["vy"], default=0.0), 4),
            }
            for k, v in runs.items()
        }
        print("SUMMARY " + json.dumps(summary, ensure_ascii=False))
        with open(OUT / "liquid-blip-positional.json", "w", encoding="utf-8") as fh:
            json.dump(runs, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
