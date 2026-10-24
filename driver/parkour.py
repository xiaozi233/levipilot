"""Parkour run recording, jump segmentation and attempt statistics.

The mod's tick recording is the input: each ``tick`` event is one 20 Hz sample
with position, velocity, rotation, ground state, jump edge and the blocks below
the feet. This module turns that stream into jumps and comparable run reports.

Examples::

    py -3.11 driver/parkour.py record --macro route1 --max-ticks 400 --out out/run1.jsonl
    py -3.11 driver/parkour.py analyze out/run1.jsonl --json out/run1.report.json
    py -3.11 driver/parkour.py compare out/run1.jsonl out/run2.jsonl
    py -3.11 driver/parkour.py attempts --macro route1 --start 0 64 0 \
        --target 12 0 --attempts 10 --out out/route1
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import sys
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver.harness import ModClient, ModPipeError

TICKS_PER_SECOND = 20.0


@dataclass(frozen=True)
class Tick:
    t: int
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    yaw: float
    pitch: float
    on_ground: bool
    jump: bool
    jump_edge: bool
    block_below: str
    block_feet: str
    block_head: str
    health: float

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> Tick:
        return cls(
            t=int(row.get("tick", row.get("t", 0))),
            x=float(row.get("x", 0.0)),
            y=float(row.get("y", 0.0)),
            z=float(row.get("z", 0.0)),
            vx=float(row.get("vx", 0.0)),
            vy=float(row.get("vy", 0.0)),
            vz=float(row.get("vz", 0.0)),
            yaw=float(row.get("yaw", 0.0)),
            pitch=float(row.get("pitch", 0.0)),
            on_ground=bool(row.get("on_ground", False)),
            jump=bool(row.get("jump", False)),
            jump_edge=bool(row.get("jump_edge", False)),
            block_below=str(row.get("block_below", "")),
            block_feet=str(row.get("block_feet", "")),
            block_head=str(row.get("block_head", "")),
            health=float(row.get("health", 20.0)),
        )

    @property
    def speed(self) -> float:
        return math.hypot(self.vx, self.vz)


@dataclass(frozen=True)
class Jump:
    index: int
    takeoff_tick: int
    landing_tick: int | None
    air_ticks: int
    distance: float
    apex: float
    takeoff_speed: float
    landing_speed: float | None
    takeoff_block: str
    landing_block: str | None
    jumped: bool
    landed: bool


def load_ticks(path: str | Path) -> list[Tick]:
    """Load a JSONL recording or a JSON array of tick rows."""
    source = Path(path)
    rows: list[dict[str, Any]] = []
    if source.suffix.lower() == ".json":
        data = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(data, list):
            rows = [row for row in data if isinstance(row, dict)]
        elif isinstance(data, dict):
            rows = [row for row in data.get("ticks", []) if isinstance(row, dict)]
    else:
        with source.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("event", "tick") == "tick":
                    rows.append(row)
    ticks = [Tick.from_mapping(row) for row in rows]
    ticks.sort(key=lambda tick: tick.t)
    return ticks


def _make_jump(
    ticks: list[Tick],
    index: int,
    takeoff_index: int,
    landing_index: int | None,
) -> Jump:
    takeoff = ticks[takeoff_index]
    landing = ticks[landing_index] if landing_index is not None else None
    air = ticks[takeoff_index + 1 : landing_index]
    end = landing if landing is not None else ticks[-1]
    apex = max((tick.y for tick in air), default=takeoff.y) - takeoff.y
    return Jump(
        index=index,
        takeoff_tick=takeoff.t,
        landing_tick=landing.t if landing is not None else None,
        air_ticks=end.t - takeoff.t,
        distance=math.hypot(end.x - takeoff.x, end.z - takeoff.z),
        apex=apex,
        takeoff_speed=takeoff.speed,
        landing_speed=landing.speed if landing is not None else None,
        takeoff_block=takeoff.block_below,
        landing_block=landing.block_below if landing is not None else None,
        # The press edge is processed before the physics tick, so it often lands
        # a few ticks before on_ground turns false.
        jumped=any(
            tick.jump_edge
            for tick in ticks[max(0, takeoff_index - 4) : (landing_index or len(ticks))]
        ),
        landed=landing is not None,
    )


def segment_jumps(ticks: Iterable[Tick]) -> list[Jump]:
    """Split a tick stream into jumps using ground transitions.

    A jump is the interval between the last grounded tick and the next grounded
    tick. Runs that start or end mid-air are handled: the leading partial air
    segment is ignored, the trailing one is reported with ``landed=False``.
    """
    ticks = list(ticks)
    jumps: list[Jump] = []
    ground_index: int | None = None

    for index, tick in enumerate(ticks):
        if not tick.on_ground:
            continue
        if ground_index is not None and index > ground_index + 1:
            jumps.append(_make_jump(ticks, len(jumps), ground_index, index))
        ground_index = index

    if (
        ticks
        and not ticks[-1].on_ground
        and ground_index is not None
        and ground_index < len(ticks) - 1
    ):
        jumps.append(_make_jump(ticks, len(jumps), ground_index, None))
    return jumps


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(jumps: Iterable[Jump]) -> dict[str, Any]:
    jumps = list(jumps)
    completed = [jump for jump in jumps if jump.landed]
    return {
        "jumps": len(jumps),
        "completed": len(completed),
        "jumped": sum(1 for jump in jumps if jump.jumped),
        "avg_distance": _mean([jump.distance for jump in completed]),
        "max_distance": max((jump.distance for jump in completed), default=None),
        "avg_air_ticks": _mean([float(jump.air_ticks) for jump in completed]),
        "max_air_ticks": max((jump.air_ticks for jump in completed), default=None),
        "avg_apex": _mean([jump.apex for jump in completed]),
        "max_apex": max((jump.apex for jump in completed), default=None),
        "avg_takeoff_speed": _mean([jump.takeoff_speed for jump in completed]),
    }


def splits(
    ticks: Iterable[Tick],
    checkpoints: Iterable[dict[str, Any] | tuple[float, float]],
) -> list[dict[str, Any]]:
    """First tick that enters each checkpoint radius, in order."""
    ticks = list(ticks)
    if not ticks:
        return []
    results: list[dict[str, Any]] = []
    search_from = 0
    for number, checkpoint in enumerate(checkpoints):
        if isinstance(checkpoint, dict):
            name = str(checkpoint.get("name", f"cp{number + 1}"))
            x = float(checkpoint["x"])
            z = float(checkpoint["z"])
            radius = float(checkpoint.get("radius", 0.75))
        else:
            name = f"cp{number + 1}"
            x, z = float(checkpoint[0]), float(checkpoint[1])
            radius = 0.75
        found: Tick | None = None
        for index in range(search_from, len(ticks)):
            tick = ticks[index]
            if math.hypot(tick.x - x, tick.z - z) <= radius:
                found = tick
                search_from = index
                break
        results.append(
            {
                "name": name,
                "x": x,
                "z": z,
                "radius": radius,
                "tick": found.t if found else None,
                "time_s": (found.t - ticks[0].t) / TICKS_PER_SECOND if found else None,
                "y": found.y if found else None,
            }
        )
    return results


def compare_runs(a: Iterable[Tick], b: Iterable[Tick]) -> dict[str, Any]:
    a_ticks, b_ticks = list(a), list(b)
    a_jumps, b_jumps = segment_jumps(a_ticks), segment_jumps(b_ticks)
    per_jump: list[dict[str, Any]] = []
    for index in range(max(len(a_jumps), len(b_jumps))):
        left = a_jumps[index] if index < len(a_jumps) else None
        right = b_jumps[index] if index < len(b_jumps) else None
        per_jump.append(
            {
                "index": index,
                "a_distance": left.distance if left else None,
                "b_distance": right.distance if right else None,
                "delta_distance": (right.distance - left.distance) if left and right else None,
                "a_air_ticks": left.air_ticks if left else None,
                "b_air_ticks": right.air_ticks if right else None,
                "delta_air_ticks": (right.air_ticks - left.air_ticks) if left and right else None,
                "a_landing_block": left.landing_block if left else None,
                "b_landing_block": right.landing_block if right else None,
            }
        )
    return {
        "a": _run_summary(a_ticks, a_jumps),
        "b": _run_summary(b_ticks, b_jumps),
        "jumps": per_jump,
        "delta_total_ticks": _total_ticks(b_ticks) - _total_ticks(a_ticks),
    }


def _total_ticks(ticks: list[Tick]) -> int:
    return ticks[-1].t - ticks[0].t if ticks else 0


def _run_summary(ticks: list[Tick], jumps: list[Jump]) -> dict[str, Any]:
    if not ticks:
        return {"ticks": 0, "time_s": 0.0, "jumps": 0}
    final = ticks[-1]
    return {
        "ticks": _total_ticks(ticks),
        "time_s": _total_ticks(ticks) / TICKS_PER_SECOND,
        "jumps": len(jumps),
        "completed_jumps": sum(1 for jump in jumps if jump.landed),
        "final": {"x": final.x, "y": final.y, "z": final.z, "on_ground": final.on_ground},
        "health": final.health,
    }


def _write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def read_until_run_end(client: ModClient, max_ticks: int) -> list[dict[str, Any]]:
    """Collect tick events until the macro or the recording stops."""
    timeout = max(5.0, max_ticks / TICKS_PER_SECOND + 10.0)
    ticks: list[dict[str, Any]] = []
    while True:
        message = client.readline(timeout=timeout)
        event = message.get("event")
        if event == "tick":
            ticks.append(message)
        elif event in {"macro_stopped", "stopped"}:
            return ticks
        elif event == "error":
            raise ModPipeError(str(message.get("reason", message)))


def record_macro(
    macro: str,
    max_ticks: int,
    out: str | Path,
    *,
    start: tuple[float, float, float] | None = None,
    yaw: float | None = None,
    pitch: float | None = None,
) -> list[Tick]:
    client = ModClient(connect_timeout=30.0).connect()
    rows: list[dict[str, Any]] = []
    try:
        client.macro_load(macro)
        if start is not None:
            client.reset(*start, yaw=yaw, pitch=pitch)
            time.sleep(0.2)
        client.start(max_ticks)
        client.send({"cmd": "macro_run", "loop": False})
        rows = read_until_run_end(client, max_ticks)
        with contextlib.suppress(ModPipeError):
            client.stop()
    finally:
        # Flush whatever was collected before closing: a disconnect or a mod
        # error mid-run must not discard the rows that already arrived.
        _write_jsonl(out, rows)
        client.close()
    return [Tick.from_mapping(row) for row in rows]


def _build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [result for result in results if result["success"]]
    return {
        "attempts": len(results),
        "successes": len(successes),
        "success_rate": len(successes) / len(results) if results else 0.0,
        "avg_success_ticks": _mean([float(result["ticks"]) for result in successes]),
        "failures": [
            {
                "attempt": result["attempt"],
                "reason": result["reason"],
                "distance": result["distance"],
            }
            for result in results
            if not result["success"]
        ],
        "results": results,
    }


def run_attempts(
    macro: str,
    start: tuple[float, float, float],
    target: tuple[float, float],
    attempts: int,
    tolerance: float,
    out_dir: str | Path,
    *,
    target_y: float | None = None,
    yaw: float | None = None,
    pitch: float | None = None,
    max_ticks: int = 400,
) -> dict[str, Any]:
    client = ModClient(connect_timeout=30.0).connect()
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    try:
        client.macro_load(macro)
        for attempt in range(1, attempts + 1):
            client.reset(*start, yaw=yaw, pitch=pitch)
            time.sleep(0.2)
            client.start(max_ticks)
            client.send({"cmd": "macro_run", "loop": False})
            rows = read_until_run_end(client, max_ticks)
            with contextlib.suppress(ModPipeError):
                client.stop()
            time.sleep(0.1)
            state = client.state()

            distance = math.hypot(
                state.get("feet_x", 0.0) - target[0], state.get("feet_z", 0.0) - target[1]
            )
            success = (
                bool(state.get("in_world"))
                and bool(state.get("on_ground"))
                and distance <= tolerance
                and float(state.get("health", 0)) > 0
            )
            if target_y is not None:
                success = success and abs(state.get("feet_y", 0.0) - target_y) <= 1.0
            reason = None
            if not success:
                if float(state.get("health", 0)) <= 0:
                    reason = "dead"
                elif target_y is not None and state.get("feet_y", 0.0) < target_y - 2.0:
                    reason = "fell"
                elif not state.get("on_ground"):
                    reason = "airborne"
                else:
                    reason = "off_target"

            name = f"attempt_{attempt:02d}.jsonl"
            _write_jsonl(out_path / name, rows)
            results.append(
                {
                    "attempt": attempt,
                    "success": success,
                    "reason": reason,
                    "ticks": len(rows),
                    "distance": distance,
                    "final": {
                        "x": state.get("feet_x"),
                        "y": state.get("feet_y"),
                        "z": state.get("feet_z"),
                        "health": state.get("health"),
                    },
                    "file": str(out_path / name),
                }
            )
            # Rewrite the summary after every attempt: a disconnect on attempt
            # N must not discard the conclusions of attempts 1..N-1.
            summary = _build_summary(results)
            (out_path / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    finally:
        client.close()

    if not results:
        summary = _build_summary(results)
        (out_path / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return summary


def _parse_start(values: list[float] | None) -> tuple[float, float, float] | None:
    if not values:
        return None
    if len(values) != 3:
        raise SystemExit("--start 需要 3 个坐标")
    return (values[0], values[1], values[2])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="segment jumps and print a report")
    analyze.add_argument("path")
    analyze.add_argument("--json", dest="json_out")
    analyze.add_argument(
        "--checkpoint",
        nargs=2,
        type=float,
        action="append",
        default=[],
        metavar=("X", "Z"),
    )

    compare = sub.add_parser("compare", help="compare two runs")
    compare.add_argument("a")
    compare.add_argument("b")
    compare.add_argument("--json", dest="json_out")

    record = sub.add_parser("record", help="record a macro run")
    record.add_argument("--macro", required=True)
    record.add_argument("--max-ticks", type=int, default=400)
    record.add_argument("--out", required=True)
    record.add_argument("--start", nargs=3, type=float)
    record.add_argument("--yaw", type=float)
    record.add_argument("--pitch", type=float)

    attempts = sub.add_parser("attempts", help="run a macro N times and score it")
    attempts.add_argument("--macro", required=True)
    attempts.add_argument("--start", nargs=3, type=float, required=True)
    attempts.add_argument("--target", nargs=2, type=float, required=True, metavar=("X", "Z"))
    attempts.add_argument("--target-y", type=float)
    attempts.add_argument("--attempts", type=int, default=10)
    attempts.add_argument("--tolerance", type=float, default=0.75)
    attempts.add_argument("--max-ticks", type=int, default=400)
    attempts.add_argument("--out", default="out/attempts")
    attempts.add_argument("--yaw", type=float)
    attempts.add_argument("--pitch", type=float)

    args = parser.parse_args(argv)

    if args.command == "analyze":
        ticks = load_ticks(args.path)
        jumps = segment_jumps(ticks)
        report = {
            "ticks": len(ticks),
            "summary": summarize(jumps),
            "jumps": [asdict(jump) for jump in jumps],
        }
        if args.checkpoint:
            report["splits"] = splits(ticks, args.checkpoint)
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.json_out:
            Path(args.json_out).write_text(text, encoding="utf-8")
        print(text)
        return 0

    if args.command == "compare":
        report = compare_runs(load_ticks(args.a), load_ticks(args.b))
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.json_out:
            Path(args.json_out).write_text(text, encoding="utf-8")
        print(text)
        return 0

    if args.command == "record":
        try:
            ticks = record_macro(
                args.macro,
                args.max_ticks,
                args.out,
                start=_parse_start(args.start),
                yaw=args.yaw,
                pitch=args.pitch,
            )
        except ModPipeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"ticks": len(ticks), "out": args.out}, ensure_ascii=False))
        return 0

    if args.command == "attempts":
        try:
            summary = run_attempts(
                args.macro,
                tuple(args.start),
                tuple(args.target),
                args.attempts,
                args.tolerance,
                args.out,
                target_y=args.target_y,
                yaw=args.yaw,
                pitch=args.pitch,
                max_ticks=args.max_ticks,
            )
        except ModPipeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
