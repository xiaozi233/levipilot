"""Sprint A/B/C comparison (G3).

Runs the same straight line three ways and reports steady-state per-tick speed
from recorded positions:

  B  set_input(forward=1, sprint=true)          (action layer)
  C  key_event(Control) + key_event(W)          (HID/event layer)

A (a human holding ctrl+w) is optional: measure it once and pass ``--a-speed``.
The script reads ``scenarios/sprint-compare.json`` for geometry and repeats.

    py -3.11 driver/sprint_compare.py
    py -3.11 driver/sprint_compare.py --a-speed 0.2805
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.harness import ModClient  # noqa: E402
from driver.harness.record import decode_dump  # noqa: E402

SCENARIO = ROOT / "scenarios" / "sprint-compare.json"


def wait_ticks(client: ModClient, ticks: int, timeout: float = 15.0) -> int:
    start = int(client.state().get("tick", 0))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        tick = int(client.state().get("tick", 0))
        if tick - start >= ticks:
            return tick - start
        time.sleep(0.02)
    raise TimeoutError(f"等待 {ticks} tick 超时")


def steady_speed(rows: list[Any], skip: int = 15) -> tuple[float, float, int]:
    distances = [
        math.hypot(rows[i].x - rows[i - 1].x, rows[i].z - rows[i - 1].z)
        for i in range(1, len(rows))
    ]
    core = distances[skip:-5] if len(distances) > skip + 10 else distances[skip:]
    core = [value for value in core if value > 1e-6]
    if not core:
        return 0.0, 0.0, 0
    return statistics.median(core), statistics.pstdev(core), len(core)


def run_group(
    client: ModClient, group: str, scenario: dict[str, Any], repeats: int
) -> list[tuple[float, float, int]]:
    results: list[tuple[float, float, int]] = []
    ticks = int(scenario["ticks"])
    for _ in range(repeats):
        start = scenario["start"]
        client.reset(
            float(start[0]),
            float(start[1]),
            float(start[2]),
            yaw=float(scenario["yaw"]),
            pitch=float(scenario["pitch"]),
        )
        time.sleep(0.8)
        client.record_bin_start(period=1)
        if group == "B":
            client.set_input(forward=1.0, sprint=True, ticks=-1)
        else:
            client.key_event(17, True, via="hid")  # Control
            client.key_event(87, True, via="hid")  # W
        wait_ticks(client, ticks)
        client.stop_move()
        if group == "C":
            client.key_event(87, False, via="hid")
            client.key_event(17, False, via="hid")
        time.sleep(0.2)
        rows = decode_dump(client.record_dump(0, limit=8192, format="jsonl"))
        client.record_bin_stop()
        results.append(steady_speed(rows))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=0, help="override repeats")
    parser.add_argument("--a-speed", type=float, default=None, help="human group A speed")
    parser.add_argument("--scenario", default=str(SCENARIO))
    args = parser.parse_args(argv)

    scenario = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
    repeats = args.repeats or int(scenario.get("repeats", 3))
    client = ModClient(connect_timeout=30.0, timeout=20.0).connect()
    try:
        measured: dict[str, list[tuple[float, float, int]]] = {}
        for group in ("B", "C"):
            measured[group] = run_group(client, group, scenario, repeats)
    finally:
        client.close()

    print(f"{'group':<6}{'mean':>10}{'sd':>10}{'n':>6}")
    summary: dict[str, float] = {}
    if args.a_speed is not None:
        summary["A"] = args.a_speed
        print(f"{'A':<6}{args.a_speed:>10.4f}{'-':>10}{'-':>6}")
    for group, runs in measured.items():
        mean = sum(run[0] for run in runs) / len(runs)
        spread = max(run[0] for run in runs) - min(run[0] for run in runs)
        summary[group] = mean
        print(f"{group:<6}{mean:>10.4f}{spread:>10.4f}{runs[0][2]:>6}")

    expected = float(scenario.get("expect", {}).get("sprint_per_tick", 0.2805))
    b, c = summary["B"], summary["C"]
    same = abs(c - b) <= max(0.01, 0.03 * max(b, 1e-6))
    if same and abs(c - expected) <= 0.01:
        print(
            f"\n结论：C ≈ B ≈ 疾跑基线 {expected:.4f} —— 事件层注入能复现疾跑，"
            "1.3x 差异不在输入事件层，不需要做 packet tap。"
        )
    elif same:
        print(
            f"\n结论：C ≈ B，但都偏离疾跑基线 {expected:.4f}；下一步检查移动输入解析或服务端请求。"
        )
    else:
        print(f"\n结论：C 与 B 不一致（{c:.4f} vs {b:.4f}）——差异仍在输入层，先修 G1/G2 注入路径。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
