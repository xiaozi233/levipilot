"""Liquid blip probe: what exactly does the wall requirement ask for?

r05-011 showed vy>0 without jump triggers the 0.3 blip when the player is pressed
against a wall, and never when swimming in open water.  Both runs differed in two
ways at once (wall present *and* start z), so the requirement is still ambiguous.
This probe crosses {near wall, open water} x {forward held, forward released} with
levitation supplying vy>0, which separates the candidates:

* horizontal collision required  -> only wall+forward fires
* proximity to a wall required   -> wall+forward and wall+release fire
* horizontal velocity ~0 needed  -> wall+forward, wall+release, open+release fire
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
    forward: float,
    effect: str | None = None,
) -> dict:
    x, y, z = tp
    h.command(f"tp @s {x} {y} {z} 180 0")
    h.client.wait_ticks(3)
    t0 = int(h.state().get("tick", 0))
    h.record_bin_start(period=1)
    if effect:
        h.command(f"effect @s {effect} 120 0 true")
    h.client.set_input(forward=forward, jump=False, ticks=ticks)
    h.client.wait_ticks(ticks + 2)
    h.record_bin_stop()
    rows = sorted((r for r in h.record_dump_all() if r.tick > t0), key=lambda r: r.tick)
    out = {
        "label": label,
        "forward": forward,
        "effect": effect,
        "ticks": len(rows),
        "vy": [round(r.vy, 4) for r in rows],
        "y": [round(r.feet_y, 3) for r in rows],
        "z": [round(r.z, 3) for r in rows],
        "in_water": [bool(r.in_water) for r in rows],
        "max_vy": round(max((r.vy for r in rows), default=0.0), 4),
        "count_03": sum(1 for r in rows if abs(r.vy - 0.3) < 0.002),
        "first_03_tick": next((i for i, r in enumerate(rows) if abs(r.vy - 0.3) < 0.002), None),
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

        wall_tp = (X0 + 3.5, -46.6, Z0 + 0.32)
        near_tp = (X0 + 3.5, -46.6, Z0 + 0.6)
        open_tp = (X0 + 3.5, -46.6, Z0 + 5.5)
        runs: dict[str, dict] = {}
        runs["wall-fwd"] = run(h, "wall-fwd", wall_tp, 26, forward=1.0, effect="levitation")
        h.command("effect @s clear")
        h.command("effect @s water_breathing 600 0 true")
        runs["wall-nofwd"] = run(h, "wall-nofwd", near_tp, 26, forward=0.0, effect="levitation")
        h.command("effect @s clear")
        h.command("effect @s water_breathing 600 0 true")
        runs["open-fwd"] = run(h, "open-fwd", open_tp, 26, forward=1.0, effect="levitation")
        h.command("effect @s clear")
        h.command("effect @s water_breathing 600 0 true")
        runs["open-nofwd"] = run(h, "open-nofwd", open_tp, 26, forward=0.0, effect="levitation")
        # No levitation control: same four configurations must stay quiet.
        h.command("effect @s clear")
        runs["wall-fwd-dry"] = run(h, "wall-fwd-dry", wall_tp, 16, forward=1.0)

        h.command("effect @s clear")
        h.command(f"fill {X0 - 3} -50 {Z0 - 3} {X1 + 3} -42 {Z1 + 3} air")
        h.command("effect @s water_breathing 600 0 true")
        h.client.input_lock(False)

        summary = {
            k: {"max_vy": v["max_vy"], "count_03": v["count_03"], "first_03": v["first_03_tick"]}
            for k, v in runs.items()
        }
        print("SUMMARY " + json.dumps(summary, ensure_ascii=False))
        with open(OUT / "liquid-blip-wallreq.json", "w", encoding="utf-8") as fh:
            json.dump(runs, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
