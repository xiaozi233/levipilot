"""Liquid blip measurement: probe box numbers, player velocities, and the full
trigger process under six different conditions, with the state verified first.

Method
------
1.  State gate before anything else: creative flight (`flying`/`mayfly`) persists
    across launches and silently destroys every vertical measurement, so it is
    reset (creative -> survival) and re-read; the effect list, the game window
    focus and the pool geometry are logged too.
2.  The pool is a 2-deep water box (water at y=-48 and y=-47, so the surface is
    y=-46.0) with a wall tall enough (up to y=-38) that the player cannot drift
    over it while rising out of the water.  The water level is confirmed with
    `blocks` + `liquid_depth` instead of trusting the build command.
3.  Every condition records vx/vy/vz, feet position, in_water and the block
    flags per tick, and derives the probe box from R-F16:
        box = player AABB [0.6 x 1.8 x 0.6] + (vx, 0.6 + vy, vz)
    (the MobTravel term measured ~0).  The box bottom is printed next to the
    water surface so the trigger condition can be checked tick by tick.
4.  Six conditions cross {jump, levitation, nothing} x {wall, no wall} plus a
    deep-water control and a rise-then-fall run.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
OUT = Path(__file__).resolve().parent

from driver import dev  # noqa: E402
from driver.harness.facade import Harness  # noqa: E402
from driver.harness.lab import Builder, settle  # noqa: E402

X0, X1 = 351, 357
Z0, Z1 = 300, 306
WALL_Y = (-48, -47)
WALL_TOP = -38
SURFACE = -46.0  # top of the water block at y=-47
WALL_Z = Z0 + 0.28
OPEN_Z = Z0 + 5.5


def preflight(h: Harness) -> dict:
    state = h.state()
    if state.get("flying") or state.get("mayfly"):
        print("PREFLIGHT: resetting creative flight", flush=True)
        h.command("gamemode creative")
        h.client.wait_ticks(5)
        h.command("gamemode survival")
        h.client.wait_ticks(10)
        state = h.state()
    h.command("effect @s water_breathing 600 0 true")
    h.command("effect @s clear")
    h.command("effect @s water_breathing 600 0 true")
    h.client.wait_ticks(2)
    state = h.state()
    pids = dev.game_pids(dev.DEFAULT_VERSION / "Minecraft.Windows.exe")
    focused = bool(pids) and dev.focus_game(pids[0])
    settled = settle(h.client, ticks=5, timeout=25.0)
    info = {
        "flying": state.get("flying"),
        "mayfly": state.get("mayfly"),
        "gamemode": state.get("gamemode"),
        "effects": state.get("effects"),
        "focus": focused,
        "settle_ok": settled.get("ok"),
        "dimension": state.get("dimension"),
    }
    print("PREFLIGHT " + json.dumps(info, ensure_ascii=False), flush=True)
    return info


def check_geometry(h: Harness) -> dict:
    cells = h.client.blocks([[354, y, 303] for y in (-49, -48, -47, -46, -45)])
    depth = h.client.liquid_depth([354, -47, 303])
    info = {"blocks": cells, "liquid_depth_at_-47": depth}
    print("GEOMETRY " + json.dumps(info, ensure_ascii=False), flush=True)
    return info


def probe_box(r) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """R-F16: the player AABB displaced by (vx, 0.6 + vy, vz)."""
    lo = (r.x - 0.3 + r.vx, r.feet_y + 0.6 + r.vy, r.z - 0.3 + r.vz)
    hi = (r.x + 0.3 + r.vx, r.feet_y + 1.8 + 0.6 + r.vy, r.z + 0.3 + r.vz)
    return lo, hi


def build_pool(h: Harness, b: Builder) -> None:
    h.command(f"tp @s {X0 + 3.5} -46 {Z0 + 3.5} 0 0")
    h.client.wait_ticks(20)
    x0, x1, z0, z1 = X0 - 1, X1 + 1, Z0 - 1, Z1 + 1
    y0, y1 = WALL_Y
    h.command(f"fill {x0 - 1} {y0 - 1} {z0 - 1} {x1 + 1} {WALL_TOP + 2} {z1 + 1} air")
    h.command(f"fill {x0} {y0 - 1} {z0} {x1} {y0 - 1} {z1} stone")
    h.command(f"fill {x0} {y0} {z0} {x1} {WALL_TOP} {z0} stone")  # tall north wall
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
    jump: bool = False,
    effect: str | None = None,
    clear_after: int | None = None,
) -> dict:
    x, y, z = tp
    h.command(f"tp @s {x} {y} {z} 180 0")
    h.client.wait_ticks(4)
    if effect:
        h.command(f"effect @s {effect} 300 0 true")
        h.client.wait_ticks(2)
    h.client.set_input(forward=forward, jump=jump, ticks=ticks + (clear_after or 0) + 6)
    t0 = int(h.state().get("tick", 0))
    h.record_bin_start(period=1)
    if clear_after is None:
        h.client.wait_ticks(ticks + 2)
    else:
        h.client.wait_ticks(clear_after)
        h.command("effect @s clear")
        h.client.wait_ticks(ticks + 2)
    h.record_bin_stop()
    if effect:
        h.command("effect @s clear")
    h.command("effect @s water_breathing 600 0 true")
    rows = sorted((r for r in h.record_dump_all() if r.tick > t0), key=lambda r: r.tick)

    print(f"\n===== {label} =====", flush=True)
    print(
        "  tick      vx      vy      vz   feet_y       z    w  |  box_bottom  box_top   "
        "bottom-surface  blip",
        flush=True,
    )
    table = []
    for i, r in enumerate(rows):
        lo, hi = probe_box(r)
        blip = abs(r.vy - 0.3) < 0.002
        table.append(
            {
                "tick": i,
                "vx": round(r.vx, 4),
                "vy": round(r.vy, 4),
                "vz": round(r.vz, 4),
                "feet_y": round(r.feet_y, 3),
                "z": round(r.z, 3),
                "in_water": bool(r.in_water),
                "box": [round(v, 3) for v in lo + hi],
                "blip": blip,
            }
        )
        print(
            f"  {i:4d} {r.vx:7.4f} {r.vy:7.4f} {r.vz:7.4f} {r.feet_y:8.3f} {r.z:7.2f} "
            f"{int(bool(r.in_water))}  | {lo[1]:10.3f} {hi[1]:8.3f} {lo[1] - SURFACE:14.3f}  "
            f"{'<== 0.3' if blip else ''}",
            flush=True,
        )
    out = {
        "label": label,
        "tp": tp,
        "forward": forward,
        "jump": jump,
        "effect": effect,
        "ticks": len(rows),
        "count_03": sum(1 for r in rows if abs(r.vy - 0.3) < 0.002),
        "table": table,
    }
    print(f"  => count_03 = {out['count_03']}", flush=True)
    return out


def main() -> int:
    with Harness() as h:
        b = Builder(h.client)
        h.client.input_lock(True)
        h.stop()
        preflight(h)
        build_pool(h, b)
        check_geometry(h)

        wall_tp = (X0 + 3.5, -46.6, WALL_Z)
        runs: dict[str, dict] = {}
        # A: the classic - wall + forward + jump
        runs["A-jump-wall"] = run(h, "A jump+wall", wall_tp, 24, jump=True)
        # B: wall + forward + levitation (no jump)
        runs["B-lev-wall"] = run(h, "B levitation+wall", (X0 + 3.5, -47.2, WALL_Z), 26, effect="levitation")
        # C: wall + forward, nothing else (vy < 0)
        runs["C-sink-wall"] = run(h, "C sink+wall", (X0 + 3.5, -46.5, WALL_Z), 18)
        # D: rise out of the water with levitation, then fall back in
        runs["D-fall-wall"] = run(
            h, "D rise-then-fall+wall", (X0 + 3.5, -47.2, WALL_Z), 30,
            effect="levitation", clear_after=30,
        )
        # E: no wall + forward + levitation
        runs["E-lev-nowall"] = run(h, "E levitation+no-wall", (X0 + 3.5, -47.2, OPEN_Z), 22, effect="levitation")
        # F: deep water + wall + jump (the box never clears)
        runs["F-jump-deep"] = run(h, "F jump+deep", (X0 + 3.5, -47.9, WALL_Z), 16, jump=True)

        h.command("effect @s clear")
        h.command(f"fill {X0 - 3} -52 {Z0 - 3} {X1 + 3} {WALL_TOP + 3} {Z1 + 3} air")
        h.command("effect @s water_breathing 600 0 true")
        h.client.input_lock(False)

        summary = {k: {"count_03": v["count_03"], "ticks": v["ticks"]} for k, v in runs.items()}
        print("\nSUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
        with open(OUT / "liquid-blip-measure.json", "w", encoding="utf-8") as fh:
            json.dump(runs, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
