"""Lava blip, round 2: start the player close to the surface.

Round 1 (liquid-blip-lava.py) built the pool correctly and the water controls
reproduced the blip, but in lava the player never rose far enough for the probe
box to clear the surface (jump gave ~0.09 of rise, levitation ~0.003/tick net),
so the trigger condition was never reachable and the result was inconclusive.

This round teleports the player to just under the surface so the box clears it
within a few ticks, and keeps the water controls at the same heights.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
OUT = Path(__file__).resolve().parent

from driver import dev  # noqa: E402
from driver.harness.facade import Harness  # noqa: E402
from driver.harness.lab import Builder, settle  # noqa: E402

X0, X1 = 351, 357
Z0, Z1 = 300, 306
WALL_Y = (-48, -47)
WALL_TOP = -38
SURFACE = -46.0
WALL_Z = Z0 + 0.28
OPEN_Z = Z0 + 5.5
CENTER = (X0 + 3.5, Z0 + 3.5)


def preflight(h: Harness, liquid: str) -> dict:
    state = h.state()
    if state.get("flying") or state.get("mayfly"):
        print("PREFLIGHT: resetting creative flight", flush=True)
        h.command("gamemode creative")
        h.client.wait_ticks(5)
        h.command("gamemode survival")
        h.client.wait_ticks(10)
        state = h.state()
    h.command("effect @s clear")
    h.command("effect @s fire_resistance 900 0 true")
    h.client.wait_ticks(2)
    state = h.state()
    pids = dev.game_pids(dev.DEFAULT_VERSION / "Minecraft.Windows.exe")
    focused = bool(pids) and dev.focus_game(pids[0])
    settled = settle(h.client, ticks=5, timeout=25.0)
    info = {
        "liquid": liquid,
        "flying": state.get("flying"),
        "mayfly": state.get("mayfly"),
        "gamemode": state.get("gamemode"),
        "effects": state.get("effects"),
        "focus": focused,
        "settle_ok": settled.get("ok"),
        "health": state.get("health"),
    }
    print("PREFLIGHT " + json.dumps(info, ensure_ascii=False), flush=True)
    return info


def check_geometry(h: Harness) -> dict:
    cells = h.client.blocks([[354, y, 303] for y in (-49, -48, -47, -46, -45)])
    depth = h.client.liquid_depth([354, -47, 303])
    info = {"blocks": cells, "liquid_depth_at_-47": depth}
    print("GEOMETRY " + json.dumps(info, ensure_ascii=False)[:400], flush=True)
    return info


def probe_box(r) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    lo = (r.x - 0.3 + r.vx, r.feet_y + 0.6 + r.vy, r.z - 0.3 + r.vz)
    hi = (r.x + 0.3 + r.vx, r.feet_y + 1.8 + 0.6 + r.vy, r.z + 0.3 + r.vz)
    return lo, hi


def build_pool(h: Harness, b: Builder, liquid: str) -> None:
    h.command(f"tp @s {CENTER[0]} -46 {CENTER[1]} 0 0")
    h.client.wait_ticks(20)
    x0, x1, z0, z1 = X0 - 1, X1 + 1, Z0 - 1, Z1 + 1
    y0, y1 = WALL_Y
    h.command(f"fill {x0 - 1} {y0 - 1} {z0 - 1} {x1 + 1} {WALL_TOP + 2} {z1 + 1} air")
    h.command(f"fill {x0} {y0 - 1} {z0} {x1} {y0 - 1} {z1} stone")
    h.command(f"fill {x0} {y0} {z0} {x1} {WALL_TOP} {z0} stone")
    h.command(f"fill {x0} {y0} {z1} {x1} {y1} {z1} stone")
    h.command(f"fill {x0} {y0} {z0} {x0} {y1} {z1} stone")
    h.command(f"fill {x1} {y0} {z0} {x1} {y1} {z1} stone")
    h.command(f"fill {X0} {y0} {Z0} {X1} {y1} {Z1} {liquid}")
    h.client.wait_ticks(6)
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
    amp: int = 0,
) -> dict:
    x, y, z = tp
    h.command(f"tp @s {x} {y} {z} 180 0")
    h.client.wait_ticks(4)
    if effect:
        h.command(f"effect @s {effect} 400 {amp} true")
        h.client.wait_ticks(2)
    h.client.set_input(forward=forward, jump=jump, ticks=ticks + 6)
    t0 = int(h.state().get("tick", 0))
    h.record_bin_start(period=1)
    h.client.wait_ticks(ticks + 2)
    h.record_bin_stop()
    if effect:
        h.command("effect @s clear")
    h.command("effect @s fire_resistance 900 0 true")
    rows = sorted((r for r in h.record_dump_all() if r.tick > t0), key=lambda r: r.tick)

    print(f"\n===== {label} =====", flush=True)
    print(
        "  tick      vx      vy      vz   feet_y       z    w  |  box_bottom   bottom-surface  blip",
        flush=True,
    )
    table = []
    for i, r in enumerate(rows):
        lo, _hi = probe_box(r)
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
                "box_bottom": round(lo[1], 3),
                "blip": blip,
            }
        )
        print(
            f"  {i:4d} {r.vx:7.4f} {r.vy:7.4f} {r.vz:7.4f} {r.feet_y:8.3f} {r.z:7.2f} "
            f"{int(bool(r.in_water))}  | {lo[1]:10.3f} {lo[1] - SURFACE:14.3f}  "
            f"{'<== 0.3' if blip else ''}",
            flush=True,
        )
    out = {
        "label": label,
        "tp": tp,
        "jump": jump,
        "effect": effect,
        "amp": amp,
        "ticks": len(rows),
        "count_03": sum(1 for r in rows if abs(r.vy - 0.3) < 0.002),
        "max_vy": max((r.vy for r in rows), default=0.0),
        "min_feet": min((r.feet_y for r in rows), default=0.0),
        "max_feet": max((r.feet_y for r in rows), default=0.0),
        "table": table,
    }
    print(
        f"  => count_03 = {out['count_03']}  max vy = {out['max_vy']:.4f}  "
        f"feet {out['min_feet']:.3f}..{out['max_feet']:.3f}",
        flush=True,
    )
    return out


def main() -> int:
    runs: dict[str, dict] = {}
    with Harness() as h:
        b = Builder(h.client)
        h.client.input_lock(True)
        h.stop()

        preflight(h, "lava")
        build_pool(h, b, "lava")
        runs["_geometry_lava"] = {"check": check_geometry(h)}

        # near-surface starts so the probe box clears the surface quickly
        runs["L2-jump-near"] = run(
            h, "L2 lava jump near surface", (X0 + 3.5, -46.35, WALL_Z), 26, jump=True
        )
        runs["L2-jump-lower"] = run(
            h, "L2 lava jump 0.5 deep", (X0 + 3.5, -46.5, WALL_Z), 26, jump=True
        )
        runs["L2-lev-near"] = run(
            h,
            "L2 lava levitation near surface",
            (X0 + 3.5, -46.45, WALL_Z),
            70,
            effect="levitation",
        )
        runs["L2-lev2-near"] = run(
            h,
            "L2 lava levitation II near surface",
            (X0 + 3.5, -47.0, WALL_Z),
            70,
            effect="levitation",
            amp=1,
        )
        runs["L2-jump-nowall"] = run(
            h, "L2 lava jump no wall (control)", (X0 + 3.5, -46.35, OPEN_Z), 26, jump=True
        )

        # water controls at the same heights
        build_pool(h, b, "water")
        runs["_geometry_water"] = {"check": check_geometry(h)}
        runs["W2-jump-near"] = run(
            h, "W2 water jump near surface (control)", (X0 + 3.5, -46.35, WALL_Z), 26, jump=True
        )
        runs["W2-lev-near"] = run(
            h,
            "W2 water levitation near surface (control)",
            (X0 + 3.5, -46.45, WALL_Z),
            40,
            effect="levitation",
        )

        h.command("effect @s clear")
        h.command(f"fill {X0 - 3} -52 {Z0 - 3} {X1 + 3} {WALL_TOP + 3} {Z1 + 3} air")
        h.command("effect @s fire_resistance 900 0 true")
        h.client.input_lock(False)

        summary = {
            k: {
                "count_03": v.get("count_03"),
                "ticks": v.get("ticks"),
                "max_vy": v.get("max_vy"),
                "feet": [v.get("min_feet"), v.get("max_feet")],
            }
            for k, v in runs.items()
            if "table" in v
        }
        print("\nSUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
        with (OUT / "liquid-blip-lava2.json").open("w", encoding="utf-8") as fh:
            json.dump(runs, fh, ensure_ascii=False, indent=1)
        print(f"wrote {OUT / 'liquid-blip-lava2.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
