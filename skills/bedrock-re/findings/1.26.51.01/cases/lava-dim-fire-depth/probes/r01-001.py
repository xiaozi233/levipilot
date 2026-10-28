"""Consolidated lava dimension / depth / fire-resistance verification.

Produces, per dimension: deep and near-surface sink recursion, fire-resistance
on/off, single-source lava spread, and the entity drift in a flowing channel.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
OUT = Path(__file__).resolve().parent

from driver.harness.facade import Harness  # noqa: E402
from driver.harness.lab import Builder, fit_recursion, steady  # noqa: E402


def fit(rows) -> dict:
    body = rows[1:]
    pairs = [(body[i].vy, body[i + 1].vy) for i in range(len(body) - 1)]
    a, b, residual = fit_recursion(pairs)
    d50 = [v1 - 0.5 * v0 for v0, v1 in pairs]
    return {
        "a": round(a, 5) if not math.isnan(a) else None,
        "b": round(b, 6) if not math.isnan(b) else None,
        "b50": round(statistics.median(d50), 6) if d50 else None,
        "residual": round(residual, 7) if not math.isnan(residual) else None,
        "steady": round(steady(body, "vy", 5), 6),
        "in_lava": sum(1 for r in body if r.in_lava),
        "vy": [round(r.vy, 5) for r in rows],
    }


def sink(h: Harness, label: str, x: float, y: float, z: float, *, ticks: int = 14) -> dict:
    h.command(f"tp @s {x} {y} {z} 0 0")
    h.client.wait_ticks(4)
    pre = h.state()
    t0 = int(h.state().get("tick", 0))
    h.record_bin_start(period=1)
    h.client.wait_ticks(ticks)
    h.record_bin_stop()
    rows = sorted((r for r in h.record_dump_all() if r.tick > t0), key=lambda r: r.tick)
    out = {"label": label, "feet_block": pre.get("block_feet"), "health": pre.get("health")}
    out.update(fit(rows))
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return out


def pool(h: Harness, ox: int, oz: int, base: int) -> None:
    h.command(f"fill {ox - 2} {base - 2} {oz - 2} {ox + 4} {base + 8} {oz + 4} air")
    h.command(f"fill {ox - 1} {base - 1} {oz - 1} {ox + 3} {base - 1} {oz + 3} stone")
    h.command(f"fill {ox - 1} {base} {oz - 1} {ox + 3} {base + 5} {oz - 1} stone")
    h.command(f"fill {ox - 1} {base} {oz + 3} {ox + 3} {base + 5} {oz + 3} stone")
    h.command(f"fill {ox - 1} {base} {oz - 1} {ox - 1} {base + 5} {oz + 3} stone")
    h.command(f"fill {ox + 3} {base} {oz - 1} {ox + 3} {base + 5} {oz + 3} stone")
    h.command(f"fill {ox} {base} {oz} {ox + 2} {base + 4} {oz + 2} lava")
    h.client.wait_ticks(20)


def spread(h: Harness, ox: int, oz: int, base: int, label: str) -> dict:
    b = Builder(h.client)
    h.command(f"fill {ox - 5} {base - 2} {oz - 5} {ox + 20} {base + 5} {oz + 5} air")
    h.command(f"fill {ox - 5} {base - 1} {oz - 5} {ox + 20} {base - 1} {oz + 5} stone")
    h.command(f"setblock {ox} {base} {oz} lava")
    h.client.wait_ticks(2)
    b.poke(ox + 1, base, oz, settle_ticks=8)
    h.client.wait_ticks(80)
    reply = h.client.blocks([[ox + dx, base, oz] for dx in range(-2, 19)])
    row = {int(x["x"]) - ox: str(x["name"]).replace("minecraft:", "") for x in reply.get("blocks", [])}
    print(json.dumps({"spread": label, "row": row}, ensure_ascii=False), flush=True)
    return row


def channel(h: Harness, ox: int, oz: int, floor_y: int) -> None:
    h.command(f"fill {ox - 3} {floor_y - 1} {oz - 3} {ox + 16} {floor_y + 5} {oz + 3} air")
    h.command(f"fill {ox - 3} {floor_y - 1} {oz - 3} {ox + 16} {floor_y - 1} {oz + 3} stone")
    h.command(f"fill {ox - 1} {floor_y} {oz - 1} {ox + 15} {floor_y + 4} {oz - 1} stone")
    h.command(f"fill {ox - 1} {floor_y} {oz + 1} {ox + 15} {floor_y + 4} {oz + 1} stone")
    h.command(f"fill {ox - 1} {floor_y} {oz - 1} {ox - 1} {floor_y + 4} {oz + 1} stone")
    h.command(f"setblock {ox} {floor_y} {oz} lava")
    h.client.wait_ticks(2)
    Builder(h.client).poke(ox + 1, floor_y, oz, settle_ticks=10)
    h.client.wait_ticks(260)


def drift(h: Harness, ox: int, oz: int, floor_y: int, offset: int, label: str) -> dict:
    h.command(f"tp @s {ox + offset + 0.5} {floor_y} {oz + 0.5} 270 0")
    h.client.wait_ticks(5)
    t0 = int(h.state().get("tick", 0))
    h.record_bin_start(period=1)
    h.client.wait_ticks(20)
    h.record_bin_stop()
    rows = sorted((r for r in h.record_dump_all() if r.tick > t0), key=lambda r: r.tick)
    vx = [round(r.vx, 5) for r in rows]
    out = {"label": label, "vx": vx, "steady": round(sum(vx[-5:]) / 5, 6)}
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return out


def main() -> int:
    with Harness() as h:
        h.client.input_lock(True)
        h.stop()
        h.command("effect @s fire_resistance 900 0 true")
        h.command("effect @s water_breathing 900 0 true")
        res = {}

        # Nether
        h.command("execute in nether run tp @s 0 100 0")
        h.client.wait_ticks(20)
        pool(h, 0, 0, 101)
        res["nether_deep"] = sink(h, "nether-deep", 1.5, 103.0, 1.5)
        res["nether_shallow"] = sink(h, "nether-shallow", 1.5, 105.45, 1.5)
        h.command("effect @s fire_resistance 0")
        res["nether_deep_nofr"] = sink(h, "nether-deep-nofr", 1.5, 103.0, 1.5)
        h.command("effect @s fire_resistance 900 0 true")
        res["nether_spread"] = spread(h, 0, 0, 101, "nether")
        channel(h, 40, 0, 100)
        res["nether_drift"] = drift(h, 40, 0, 100, 2, "nether-channel")

        # Overworld
        h.command("execute in overworld run tp @s 600 -46 600")
        h.client.wait_ticks(20)
        pool(h, 600, 600, -48)
        res["overworld_deep"] = sink(h, "overworld-deep", 601.5, -46.0, 601.5)
        res["overworld_shallow"] = sink(h, "overworld-shallow", 601.5, -43.55, 601.5)
        h.command("effect @s fire_resistance 0")
        res["overworld_deep_nofr"] = sink(h, "overworld-deep-nofr", 601.5, -46.0, 601.5)
        h.command("effect @s fire_resistance 900 0 true")
        res["overworld_spread"] = spread(h, 600, 600, -47, "overworld")
        channel(h, 640, 600, -47)
        res["overworld_drift"] = drift(h, 640, 600, -47, 2, "overworld-channel")

        summary = {
            k: {kk: v[kk] for kk in ("a", "b", "b50", "steady", "in_lava", "health") if kk in v}
            for k, v in res.items() if isinstance(v, dict) and "a" in v
        }
        print("SUMMARY " + json.dumps(summary, ensure_ascii=False))
        with open(OUT / "lava-verify.json", "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
        h.client.input_lock(False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
