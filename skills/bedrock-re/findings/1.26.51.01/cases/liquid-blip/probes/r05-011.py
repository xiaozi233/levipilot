"""Liquid blip probe: is "must hold jump" an input gate or the probe-box geometry?

R-F16 located the writer: ``sub_1482A8D00`` sets ``StateVector.VelY = 0.3`` when the box
``AABB + (VelX, comp40[+0x14] - PositionY + 0.6f + VelY, VelZ)`` holds neither a collision
(``queryIface`` vtable+0x38) nor any liquid (vtable+0xA0).  Neither that function nor its
callers (``sub_1482EC8D0`` -> ``sub_1482EC770``) read a single input bit, so the live
"no blip without jump" result (r02-006) should follow from the ``+VelY`` term: with no jump
vy stays negative, the probe box never clears the surface, and the liquid gate rejects it.

This probe keeps the r02-005 wall position (feet 0.6 below the surface, so *any* vy > 0
clears the box) and gives the player a positive vy **without** pressing jump, using
levitation.  The two hypotheses predict different vy traces:

* geometry: vy snaps to exactly 0.3 within a tick or two of the effect landing (the blip
  overwrites the 0.05/tick levitation ramp), often alternating 0.3 / 0.35 afterwards;
* input/flag gate: a clean monotonic ramp 0.05, 0.10, 0.15, ... and no 0.3 at all.
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
SURFACE = -46.0  # top of the water block at y=-47


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
    yaw: float,
    ticks: int,
    *,
    jump: bool = False,
    effect: str | None = None,
) -> dict:
    x, y, z = tp
    h.command(f"tp @s {x} {y} {z} {yaw} 0")
    h.client.wait_ticks(3)
    t0 = int(h.state().get("tick", 0))
    h.record_bin_start(period=1)
    if effect:
        # Levitation amplifier 0 ramps vy by ~0.05/tick, so a clean ramp is the
        # "gate" signature and an early exact 0.3 is the blip signature.
        h.command(f"effect @s {effect} 120 0 true")
    h.client.set_input(forward=1.0, jump=jump, ticks=ticks)
    h.client.wait_ticks(ticks + 2)
    h.record_bin_stop()
    rows = sorted((r for r in h.record_dump_all() if r.tick > t0), key=lambda r: r.tick)
    out = {
        "label": label,
        "feet": y,
        "jump": jump,
        "effect": effect,
        "ticks": len(rows),
        "vy": [round(r.vy, 4) for r in rows],
        "y": [round(r.feet_y, 3) for r in rows],
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
            print("resetting creative flight before the experiment", flush=True)
            h.command("gamemode creative")
            h.client.wait_ticks(5)
            h.command("gamemode survival")
            h.client.wait_ticks(10)
            state = h.state()
        print(
            "preflight:",
            json.dumps(
                {
                    "flying": state.get("flying"),
                    "mayfly": state.get("mayfly"),
                    "gamemode": state.get("gamemode"),
                    "feet": [state.get("x"), state.get("y"), state.get("z")],
                }
            ),
            flush=True,
        )
        h.command("effect @s water_breathing 600 0 true")

        settled = settle(h.client, ticks=5, timeout=25.0)
        print("settle:", json.dumps(settled.get("ok")), flush=True)

        build_pool(h, b)

        wall_tp = (X0 + 3.5, -46.6, Z0 + 0.32)
        runs: dict[str, dict] = {}
        runs["nojump"] = run(h, "nojump", wall_tp, 180.0, 16)
        runs["nojump-lev"] = run(h, "nojump-lev", wall_tp, 180.0, 26, effect="levitation")
        h.command("effect @s clear")
        h.command("effect @s water_breathing 600 0 true")
        # Deeper start: levitation lifts the player through the surface zone, so one
        # run sweeps every box height and the tick where (if) 0.3 appears reads off
        # the trigger height directly.
        runs["nojump-lev-deep"] = run(
            h, "nojump-lev-deep", (X0 + 3.5, -47.6, Z0 + 0.32), 180.0, 26, effect="levitation"
        )
        h.command("effect @s clear")
        h.command("effect @s water_breathing 600 0 true")
        runs["nojump-lev-nowall"] = run(
            h, "nojump-lev-nowall", (X0 + 3.5, -46.6, Z0 + 5.5), 180.0, 26, effect="levitation"
        )
        h.command("effect @s clear")
        h.command("effect @s water_breathing 600 0 true")
        runs["jump"] = run(h, "jump", wall_tp, 180.0, 24, jump=True)

        h.command("effect @s clear")
        h.command(f"fill {X0 - 3} -50 {Z0 - 3} {X1 + 3} -42 {Z1 + 3} air")
        h.command("effect @s water_breathing 600 0 true")
        h.client.input_lock(False)

        summary = {
            k: {
                "max_vy": v["max_vy"],
                "count_03": v["count_03"],
                "first_03_tick": v["first_03_tick"],
                "vy_head": v["vy"][:10],
            }
            for k, v in runs.items()
        }
        print("SUMMARY " + json.dumps(summary, ensure_ascii=False))
        with open(OUT / "liquid-blip-jumpgate.json", "w", encoding="utf-8") as fh:
            json.dump(runs, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
