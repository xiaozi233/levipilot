"""Liquid blip probe: holding forward into a wall at the water surface.

1.8.9 / 26.2 ``jumpOutOfFluid`` forces vy = 0.3 when the entity has a horizontal
collision and the box moved by (vx, vy + 0.6 - dy, vz) is free of blocks and
liquid.  The Bedrock counterpart would show 0.3 spikes in ``record``.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
OUT = Path(__file__).resolve().parent

from driver.harness.facade import Harness  # noqa: E402
from driver.harness.lab import Builder  # noqa: E402

X0, X1 = 351, 357
Z0, Z1 = 300, 306
WALL_Y = (-48, -47)


def build_pool(h: Harness, b: Builder) -> None:
    h.command(f"tp @s {X0 + 3.5} -46 {Z0 + 3.5} 0 0")
    h.client.wait_ticks(20)
    x0, x1, z0, z1 = X0 - 1, X1 + 1, Z0 - 1, Z1 + 1
    y0, y1 = WALL_Y
    h.command(f"fill {x0 - 1} {y0 - 1} {z0 - 1} {x1 + 1} {y1 + 3} {z1 + 1} air")
    # Floor and a 1-thick wall ring, two blocks high.
    h.command(f"fill {x0} {y0 - 1} {z0} {x1} {y0 - 1} {z1} stone")
    h.command(f"fill {x0} {y0} {z0} {x1} {y1} {z0} stone")
    h.command(f"fill {x0} {y0} {z1} {x1} {y1} {z1} stone")
    h.command(f"fill {x0} {y0} {z0} {x0} {y1} {z1} stone")
    h.command(f"fill {x1} {y0} {z0} {x1} {y1} {z1} stone")
    h.command(f"fill {X0} {y0} {Z0} {X1} {y1} {Z1} water")
    h.client.wait_ticks(4)
    b.poke(X0, y0, Z0)


def run(h: Harness, label: str, tp: tuple[float, float, float], yaw: float, ticks: int, *, jump: bool = True) -> dict:
    x, y, z = tp
    h.command(f"tp @s {x} {y} {z} {yaw} 0")
    h.client.wait_ticks(3)
    t0 = int(h.state().get("tick", 0))
    h.record_bin_start(period=1)
    h.client.set_input(forward=1.0, jump=jump, ticks=ticks)
    h.client.wait_ticks(ticks + 2)
    h.record_bin_stop()
    rows = sorted((r for r in h.record_dump_all() if r.tick > t0), key=lambda r: r.tick)
    out = {
        "label": label,
        "ticks": len(rows),
        "vy": [round(r.vy, 4) for r in rows],
        "y": [round(r.feet_y, 3) for r in rows],
        "z": [round(r.z, 3) for r in rows],
        "max_vy": round(max((r.vy for r in rows), default=0.0), 4),
        "count_03": sum(1 for r in rows if abs(r.vy - 0.3) < 0.002),
        "in_water": sum(1 for r in rows if r.in_water),
    }
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return out


def main() -> int:
    with Harness() as h:
        b = Builder(h.client)
        h.client.input_lock(True)
        h.stop()
        build_pool(h, b)

        wall = run(h, "wall", (X0 + 3.5, -46.6, Z0 + 0.32), 180.0, 24)
        wall_nojump = run(h, "wall-nojump", (X0 + 3.5, -46.6, Z0 + 0.32), 180.0, 16, jump=False)
        # east wall: 1.8.9's float rounding bug made south-east towers fail
        east = run(h, "wall-east", (X1 - 0.32, -46.6, Z0 + 3.5), 270.0, 20)
        # no jump, but high enough that the +0.6 probe box clears the surface
        high = run(h, "wall-nojump-high", (X0 + 3.5, -46.3, Z0 + 0.32), 180.0, 14, jump=False)
        # deep: feet -47.8, the +0.6 offset still sits inside the water block
        deep = run(h, "wall-deep", (X0 + 3.5, -47.8, Z0 + 0.32), 180.0, 20, jump=False)
        # ceiling: a stone at y=-44 blocks the +0.6 offset box but not the player
        h.command(f"setblock {X0 + 3} -44 {Z0} stone")
        ceiling = run(h, "wall-ceiling", (X0 + 3.5, -46.2, Z0 + 0.32), 180.0, 24)
        h.command(f"setblock {X0 + 3} -44 {Z0} air")
        control = run(h, "no-wall", (X0 + 3.5, -46.6, Z0 + 5.5), 180.0, 14)

        all_runs = {"wall": wall, "wall-nojump": wall_nojump, "wall-nojump-high": high, "wall-east": east, "wall-deep": deep,
                    "wall-ceiling": ceiling, "no-wall": control}
        summary = {k: {"max_vy": v["max_vy"], "count_03": v["count_03"]} for k, v in all_runs.items()}
        print("SUMMARY " + json.dumps(summary))
        with open(OUT / "liquid-blip.json", "w", encoding="utf-8") as fh:
            json.dump(all_runs, fh, ensure_ascii=False, indent=1)

        h.command(f"fill {X0 - 3} -50 {Z0 - 3} {X1 + 3} -42 {Z1 + 3} air")
        h.client.input_lock(False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
