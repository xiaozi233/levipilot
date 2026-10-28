"""Vertical-flow push probe: still water vs walled waterfall vs open-air waterfall.

Measured passive sink inside three water columns at (300, -48..-24, 300).
The LiquidPhysics system adds the (-6) vertical bias to a flow vector; if it is
active here, vy converges to 0.8v - 0.005 - 0.014 = -0.095 instead of -0.025.
"""

from __future__ import annotations

import json
import math
import sys

sys.path.insert(0, r"<repo>")

from driver.harness.facade import Harness  # noqa: E402
from driver.harness.lab import Builder, fit_recursion, steady  # noqa: E402

X = 300
Z = 300
TOP = -24
GROUND = -49  # top solid block; air starts at y=-48


def build_walled_shaft(h: Harness) -> None:
    h.command(f"fill {X - 2} {GROUND + 1} {Z - 2} {X + 2} {TOP + 2} {Z + 2} air")
    for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        h.command(f"fill {X + dx} {GROUND + 1} {Z + dz} {X + dx} {TOP} {Z + dz} stone")


def record_sink(h: Harness, label: str, ticks: int = 16) -> dict:
    h.client.wait_ticks(4)
    pre = h.state()
    print(
        json.dumps({"label": label, "pre": {"in_water": pre.get("in_water"), "feet_y": pre.get("feet_y")}}),
        flush=True,
    )
    t0 = int(h.state().get("tick", 0))
    h.record_bin_start(period=1)
    h.client.wait_ticks(ticks)
    h.record_bin_stop()
    rows = sorted((r for r in h.record_dump_all() if r.tick > t0), key=lambda r: r.tick)
    body = rows[1:]
    pairs = [(body[i].vy, body[i + 1].vy) for i in range(len(body) - 1)]
    a, b, residual = fit_recursion(pairs)
    out = {
        "label": label,
        "ticks": len(rows),
        "in_water": sum(1 for r in body if r.in_water),
        "on_ground": sum(1 for r in body if r.on_ground),
        "vy": [round(r.vy, 6) for r in rows],
        "y": [round(r.feet_y, 4) for r in rows],
        "a": round(a, 6) if not math.isnan(a) else None,
        "b": round(b, 6) if not math.isnan(b) else None,
        "residual": round(residual, 8) if not math.isnan(residual) else None,
        "steady_tail": round(steady(body, "vy", 5), 6),
    }
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return out


def main() -> int:
    with Harness() as h:
        b = Builder(h.client)
        h.client.input_lock(True)
        h.stop()
        # Load the test chunks before any /fill: commands in unloaded chunks fail.
        h.command(f"tp @s {X + 2} -46 {Z + 2} 0 0")
        h.client.wait_ticks(20)

        results = {}

        # A. still water column (all source blocks)
        build_walled_shaft(h)
        h.command(f"fill {X} {GROUND + 1} {Z} {X} {TOP} {Z} water")
        h.client.wait_ticks(10)
        h.command(f"tp @s {X + 0.5} -30 {Z + 0.5} 0 0")
        results["still"] = record_sink(h, "still-water")

        # B. walled waterfall: one source on top, rest air, poked so it flows
        h.command(f"fill {X} {GROUND + 1} {Z} {X} {TOP} {Z} air")
        h.client.wait_ticks(4)
        h.command(f"setblock {X} {TOP} {Z} water")
        h.client.wait_ticks(2)
        b.poke(X, TOP - 1, Z)
        h.client.wait_ticks(60)
        h.command(f"tp @s {X + 0.5} -30 {Z + 0.5} 0 0")
        results["walled"] = record_sink(h, "walled-waterfall")

        # C. open-air waterfall: platform with a hole, no walls below
        h.command(f"fill {X - 2} {GROUND + 1} {Z - 2} {X + 2} {TOP + 2} {Z + 2} air")
        h.command(f"fill {X - 3} -22 {Z - 3} {X + 3} -18 {Z + 3} air")
        h.command(f"fill {X - 2} -20 {Z - 2} {X + 2} -20 {Z + 2} stone")
        h.command(f"setblock {X} -20 {Z} air")
        h.command(f"setblock {X} -20 {Z} water")
        h.client.wait_ticks(2)
        b.poke(X, -21, Z)
        h.client.wait_ticks(60)
        h.command(f"tp @s {X + 0.5} -30 {Z + 0.5} 0 0")
        results["open"] = record_sink(h, "open-air-waterfall")

        # D/E. 3x3 shaft: column in the middle (no adjacent wall) vs against one
        # wall.  Separates "any wall in the shaft" from "solid face next to the
        # fluid block".
        h.command(f"fill {X - 4} {GROUND + 1} {Z - 4} {X + 4} {TOP + 2} {Z + 4} air")
        h.command(f"fill {X - 2} {GROUND + 1} {Z - 2} {X - 2} {TOP} {Z + 2} stone")
        h.command(f"fill {X + 2} {GROUND + 1} {Z - 2} {X + 2} {TOP} {Z + 2} stone")
        h.command(f"fill {X - 1} {GROUND + 1} {Z - 2} {X + 1} {TOP} {Z - 2} stone")
        h.command(f"fill {X - 1} {GROUND + 1} {Z + 2} {X + 1} {TOP} {Z + 2} stone")
        h.client.wait_ticks(10)
        h.command(f"setblock {X} {TOP} {Z} water")
        h.client.wait_ticks(2)
        b.poke(X, TOP - 1, Z)
        h.client.wait_ticks(60)
        h.command(f"tp @s {X + 0.5} -30 {Z + 0.5} 0 0")
        results["wide_center"] = record_sink(h, "3x3-center-waterfall")

        h.command(f"fill {X - 1} {GROUND + 1} {Z - 1} {X + 1} {TOP} {Z + 1} air")
        h.client.wait_ticks(4)
        h.command(f"setblock {X - 1} {TOP} {Z} water")
        h.client.wait_ticks(2)
        b.poke(X - 1, TOP - 1, Z)
        h.client.wait_ticks(60)
        h.command(f"tp @s {X - 0.5} -30 {Z + 0.5} 0 0")
        results["wide_edge"] = record_sink(h, "3x3-edge-waterfall")

        # F. open-air column with a single stone block one level above the
        # neighbouring air block (Java checks the neighbour and the block above
        # it; static Bedrock does the same at 0x14345E178).
        h.command(f"fill {X - 4} {GROUND + 1} {Z - 4} {X + 4} {TOP + 2} {Z + 4} air")
        h.command(f"fill {X - 2} -22 {Z - 2} {X + 2} -18 {Z + 2} air")
        h.command(f"fill {X - 2} -20 {Z - 2} {X + 2} -20 {Z + 2} stone")
        h.command(f"setblock {X} -20 {Z} air")
        h.command(f"setblock {X} -20 {Z} water")
        h.command(f"setblock {X + 1} -29 {Z} stone")
        h.client.wait_ticks(2)
        b.poke(X, -21, Z)
        h.client.wait_ticks(60)
        h.command(f"tp @s {X + 0.5} -30 {Z + 0.5} 0 0")
        results["above_only"] = record_sink(h, "open-air-with-overhang")

        print(
            "SUMMARY "
            + json.dumps(
                {
                    k: {"a": v["a"], "b": v["b"], "steady": v["steady_tail"], "in_water": v["in_water"]}
                    for k, v in results.items()
                },
                ensure_ascii=False,
            )
        )
        with open(r"<repo>\out\flow-waterfall.json", "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=1)

        # Cleanup: remove water and the test structures.
        h.command(f"fill {X - 4} {GROUND + 1} {Z - 4} {X + 4} {TOP + 3} {Z + 4} air")
        h.command(f"fill {X - 3} -22 {Z - 3} {X + 3} -18 {Z + 3} air")
        h.command(f"fill {X - 4} {GROUND + 1} {Z - 4} {X + 4} {GROUND + 2} {Z + 4} air")
        h.client.input_lock(False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
