"""Liquid blip probe: rise through the 0.6 band vs sink through it, with no teleport.

r08-025 teleported the player and then sampled, which turned out to be unusable: a
player teleported to feet -46.526 (0.474 above the surface, sinking at vy -0.0122)
did NOT fire, while the earlier levitation runs fired at feet -46.586 while rising.
Both the "net lift 0.6" model and the "lift 0.6 + vy" model predict a fire for the
teleported case, so the components (AABB / flags) must lag the teleport by a few
ticks and the first samples were stale.

This probe removes the teleport from the measured window: levitation lifts the
player up through the band (vy > 0), then the effect is cleared and the same player
sinks back down through the same band (vy < 0).  Only the second half is compared
against the models:

* net lift 0.6 (positional) -> fires while sinking too, as long as the feet stay
  within 0.6 of the surface;
* lift 0.6 + vy             -> never fires while sinking.

The rise half is the positive control (it must fire), and a no-wall run is the
HorizontalCollisionFlag control.
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
    h.command("effect @s levitation 200 0 true")
    h.client.set_input(forward=1.0, jump=False, ticks=rise_ticks + sink_ticks + 4)
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
        "sink_count_03": sum(
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
        runs["rise-sink"] = run(h, "rise-sink", (X0 + 3.5, -47.2, WALL_Z), 16, 18)
        runs["rise-sink-nowall"] = run(
            h, "rise-sink-nowall", (X0 + 3.5, -47.2, Z0 + 5.5), 16, 18
        )

        h.command("effect @s clear")
        h.command(f"fill {X0 - 3} -50 {Z0 - 3} {X1 + 3} -42 {Z1 + 3} air")
        h.command("effect @s water_breathing 600 0 true")
        h.client.input_lock(False)

        summary = {
            k: {
                "rise_count_03": v["rise_count_03"],
                "sink_count_03": v["sink_count_03"],
                "y": v["y"],
            }
            for k, v in runs.items()
        }
        print("SUMMARY " + json.dumps(summary, ensure_ascii=False))
        with open(OUT / "liquid-blip-risesink.json", "w", encoding="utf-8") as fh:
            json.dump(runs, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
