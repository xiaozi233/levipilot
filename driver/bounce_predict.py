"""Predict whether a slime-block landing bounces, from the cover height and the drop.

Two models, both from `docs/notes/bounce-12651.md`:

* the **gather box** that decides whether the slime block enters the tick's
  candidate list (`sub_1431C2F50`, §3.2), and
* the **resolver** that picks a block by `shape centre <= probe` (`0x141E144A0`, §1).

The fall integration (`v <- 0.98 v + 0.0784`) is a fit that reproduces the measured
per-tick deltas bit for bit; it is not read out of the binary. `validate()` replays
the recorded sweeps to keep it honest.

Usage:
    py -3.11 driver/bounce_predict.py --layers 3 --drops 0.5,1.25
    py -3.11 driver/bounce_predict.py --table
    py -3.11 driver/bounce_predict.py --validate
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

# Runtime constants of 1.26.51.01; a new version needs them re-resolved rather than
# reused (see docs/ports and docs/roles).
DRAG = 0.98
GRAVITY_STEP = 0.0784  # 0.08 * DRAG
AUTO_STEP = 0.5625
STEP_DOWN = AUTO_STEP * 1.01
PROBE_OFFSET = 0.2  # resolver probe = AABB.minY - 0.2

# Defaults for the test rig: 3x3 slime at y=-60 (top -59), snow_layer stack on top.
SLIME_TOP = -59.0
SLIME_RESTITUTION = 0.9  # unused by the decision; every covering block tested has 0

PROBES = Path("skills/bedrock-re/findings/1.26.51.01/cases/slime-bounce-coverage/probes")


@dataclass(frozen=True)
class Prediction:
    cover_top: float
    drop: float
    pre_y: float  # feet before the landing tick's move
    dy: float  # requested vertical displacement at that tick
    box_lo: float  # gather box bottom, decides list membership
    slime_in_list: bool
    picked: str  # "nothing" | "cover" | "slime"
    bounce: bool


def fall_landing(cover_top: float, drop: float) -> tuple[float, float]:
    """Return ``(pre_y, dy)`` for the tick whose move reaches ``cover_top``."""
    y = cover_top + drop
    velocity = 0.0
    while True:
        velocity = DRAG * velocity + GRAVITY_STEP
        if y - velocity <= cover_top:
            return y, -velocity
        y -= velocity


def predict(cover_top: float, drop: float, *, slime_top: float = SLIME_TOP) -> Prediction:
    """Predict the landing outcome for a drop onto a cover sitting on a slime block."""
    pre_y, dy = fall_landing(cover_top, drop)
    height = cover_top - slime_top
    box_lo = min(pre_y - STEP_DOWN, pre_y + dy - PROBE_OFFSET)
    probe = cover_top - PROBE_OFFSET
    slime_in_list = box_lo < slime_top

    cover_center = cover_top - height / 2.0
    slime_center = slime_top - 0.5
    # The resolver keeps shapes whose centre is at or below the probe and takes the
    # highest one, so a cover that qualifies always wins over the slime underneath.
    if cover_center <= probe:
        picked = "cover"
    elif slime_in_list and slime_center <= probe:
        picked = "slime"
    else:
        picked = "nothing"
    return Prediction(
        cover_top=cover_top,
        drop=drop,
        pre_y=pre_y,
        dy=dy,
        box_lo=box_lo,
        slime_in_list=slime_in_list,
        picked=picked,
        bounce=picked == "slime",
    )


def bounce_drops(
    layers: int,
    *,
    lo: float = 0.25,
    hi: float = 6.0,
    step: float = 1 / 16,
    slime_top: float = SLIME_TOP,
) -> list[float]:
    """Drop heights (measured from the cover top) that do NOT bounce."""
    cover_top = slime_top + layers * 0.125
    out: list[float] = []
    value = lo
    while value <= hi + 1e-9:
        if not predict(cover_top, round(value, 5), slime_top=slime_top).bounce:
            out.append(round(value, 4))
        value += step
    return out


def validate() -> int:
    """Replay the recorded sweeps; return the number of disagreements."""
    files = sorted(PROBES.glob("live_sweep_*.json"))
    if not files:
        print(f"no sweeps under {PROBES}")
        return 1
    rows = total = 0
    for path in files:
        for record in json.loads(path.read_text(encoding="utf-8")):
            layers = int(record["layers"])
            cover_top = SLIME_TOP + layers * 0.125
            result = predict(cover_top, float(record["drop"]))
            total += 1
            ok = (
                abs(result.pre_y - float(record["preY"])) < 1e-3
                and abs(result.dy - float(record["dyVel"])) < 1e-3
                and abs(result.box_lo - float(record["boxLo"])) < 1e-3
                and result.bounce == bool(record["bounce"])
                and result.slime_in_list == (int(record["n"]) == 2)
            )
            if not ok:
                rows += 1
                print(
                    f"MISMATCH {path.name} layers={layers} drop={record['drop']} "
                    f"sim=({result.pre_y:+.4f},{result.dy:+.5f},"
                    f"{result.slime_in_list},{result.bounce}) "
                    f"live=({record['preY']:+.4f},{record['dyVel']:+.5f},"
                    f"{int(record['n']) == 2},{record['bounce']})"
                )
    print(f"validate: {total - rows}/{total} rows reproduced")
    return 0 if rows == 0 else 1


def table(*, lo: float = 0.25, hi: float = 6.0, step: float = 1 / 16) -> None:
    for layers in range(0, 6):
        gaps = bounce_drops(layers, lo=lo, hi=hi, step=step)
        if layers >= 4:
            print(f"{layers} layers: never bounces")
        elif not gaps:
            print(f"{layers} layers: always bounces in {lo:g}-{hi:g} @ {step:g}")
        else:
            print(f"{layers} layers: {len(gaps)} gap(s) -> {gaps}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slime-top", type=float, default=SLIME_TOP)
    parser.add_argument("--layers", type=int, default=3, help="snow_layer count (0.125 each)")
    parser.add_argument("--drops", default="0.5,1,2,3", help="comma separated drop heights")
    parser.add_argument("--table", action="store_true", help="print the gap table per layer")
    parser.add_argument("--validate", action="store_true", help="replay the recorded sweeps")
    args = parser.parse_args()

    if args.validate:
        return validate()
    if args.table:
        table()
        return 0
    cover_top = args.slime_top + args.layers * 0.125
    for text in args.drops.split(","):
        drop = float(text)
        result = predict(cover_top, drop, slime_top=args.slime_top)
        print(
            f"layers={args.layers} drop={drop:<6g} preY={result.pre_y:+.4f} "
            f"dy={result.dy:+.5f} boxLo={result.box_lo:+.4f} "
            f"slimeInList={result.slime_in_list} picked={result.picked} "
            f"-> {'BOUNCE' if result.bounce else 'flat'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
