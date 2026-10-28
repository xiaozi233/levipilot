"""Lava spread front over time, Nether vs Overworld (repeatability check)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
OUT = Path(__file__).resolve().parent

from driver.harness.facade import Harness  # noqa: E402
from driver.harness.lab import Builder  # noqa: E402


def read_row(h: Harness, ox: int, oz: int, y: int) -> dict:
    reply = h.client.blocks([[ox + dx, y, oz] for dx in range(-3, 26)])
    return {int(b["x"]) - ox: str(b["name"]).replace("minecraft:", "") for b in reply.get("blocks", [])}


def front(row: dict) -> int:
    xs = [x for x, name in row.items() if name == "lava"]
    return max(xs) if xs else -999


def run_dim(h: Harness, ox: int, oz: int, base: int, label: str) -> dict:
    b = Builder(h.client)
    h.command(f"fill {ox - 5} {base - 3} {oz - 5} {ox + 30} {base + 6} {oz + 5} air")
    h.command(f"fill {ox - 5} {base - 1} {oz - 5} {ox + 30} {base - 1} {oz + 5} stone")
    h.command(f"setblock {ox} {base} {oz} lava")
    h.client.wait_ticks(2)
    b.poke(ox + 1, base, oz, settle_ticks=6)
    samples = {}
    t = 0
    for step in (40, 40, 40, 40):
        h.client.wait_ticks(step)
        t += step
        row = read_row(h, ox, oz, base)
        samples[str(t)] = {"front": front(row), "row": row}
    print(json.dumps({"dim": label, "samples": samples}, ensure_ascii=False), flush=True)
    return {"samples": samples}


def main() -> int:
    with Harness() as h:
        h.client.input_lock(True)
        h.stop()
        h.command("effect @s fire_resistance 900 0 true")
        res = {}
        h.command("execute in nether run tp @s 0 100 0")
        h.client.wait_ticks(20)
        res["nether"] = run_dim(h, 0, 0, 100, "nether")
        h.command("execute in overworld run tp @s 700 -46 700")
        h.client.wait_ticks(20)
        res["overworld"] = run_dim(h, 700, 700, -47, "overworld")
        print("SUMMARY " + json.dumps({k: {t: v["front"] for t, v in r["samples"].items()} for k, r in res.items()}))
        with open(OUT / "lava-spread2.json", "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
        h.client.input_lock(False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
