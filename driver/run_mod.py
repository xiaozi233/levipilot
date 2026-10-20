"""Run the scenario DSL against the in-game LeviLamina probe mod.

This is the mod-transport counterpart of ``driver/run.py`` (which drives the
retired frida agent). Scenarios, the runner and the JSONL/summary writer are
shared; only the transport differs.

Usage::

    py -3.11 driver/run_mod.py --state
    py -3.11 driver/run_mod.py --command "/fill -4 -60 -4 4 -60 4 stone"
    py -3.11 driver/run_mod.py --block 0 -60 0
    py -3.11 driver/run_mod.py --scenario scenarios/setup-platform.json
    py -3.11 driver/run_mod.py --scenario scenarios/example.json
    py -3.11 driver/run_mod.py --probe --probe-ticks 40
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver import scenario as scenario_mod  # noqa: E402
from driver.mod_agent import AgentError, ModAgent  # noqa: E402
from driver.output import write_run  # noqa: E402
from driver.runner import RunError, run_scenario  # noqa: E402
from driver.scenario import ScenarioError  # noqa: E402
from driver.transport_mod import ModPipeError  # noqa: E402


def _probe_scenario(ticks: int) -> scenario_mod.Scenario:
    return scenario_mod.normalize(
        {
            "name": "probe-forward",
            "description": "向前走 N tick，验证 mod 输入注入。",
            "steps": [
                {"type": "wait", "ticks": 5},
                {"type": "input", "forward": 1.0, "ticks": ticks},
                {"type": "stop"},
            ],
            "sample": ["pos", "vel", "on_ground", "yaw", "pitch", "input"],
            "output": "probe-forward",
        }
    )


def _print_summary(summary: dict[str, Any]) -> None:
    delta = summary.get("displacement")
    delta_text = "(" + ", ".join(f"{v:+.4f}" for v in delta) + ")" if delta else "n/a"
    print("--- summary ---")
    print(f"ticks recorded : {summary.get('ticks_recorded')}")
    print(f"displacement   : {delta_text}")
    distance = summary.get("distance")
    print(f"distance       : {distance:.4f}" if distance is not None else "distance       : n/a")
    if summary.get("max_h_speed") is not None:
        print(
            f"h speed        : max {summary['max_h_speed']:.4f} / "
            f"mean {summary['mean_h_speed']:.4f} blocks/tick"
        )
    if summary.get("on_ground_ratio") is not None:
        print(f"on ground      : {summary['on_ground_ratio'] * 100:.1f}%")
    for row in summary.get("steps", []):
        distance = row.get("distance")
        d_text = f"{distance:.4f}" if distance is not None else "n/a"
        print(
            f"  step {row['index']:>2} {row['type']:<6} "
            f"ticks={row['step_ticks']:<3} samples={row['samples']:<3} dist={d_text}"
        )
    for warning in summary.get("warnings", []):
        print(f"  warning: {warning}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipe", default=None, help="named pipe path")
    parser.add_argument("--scenario", help="scenario JSON path")
    parser.add_argument("--probe", action="store_true", help="run the built-in forward probe")
    parser.add_argument("--probe-ticks", type=int, default=40)
    parser.add_argument("--out-dir", default=str(ROOT / "out"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--state", action="store_true", help="print the current player state")
    parser.add_argument("--command", help="run one server command and exit")
    parser.add_argument("--block", nargs=3, type=float, metavar=("X", "Y", "Z"))
    parser.add_argument("--list-scenarios", action="store_true")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        for path in sorted((ROOT / "scenarios").glob("*.json")):
            print(path.relative_to(ROOT))
        return 0

    if args.scenario and args.probe:
        parser.error("--scenario 与 --probe 只能选一个")

    try:
        with ModAgent(args.pipe) as agent:
            status = agent.client.status()
            if not status.get("in_world"):
                print("error: mod 已连接，但玩家不在世界中", file=sys.stderr)
                return 2

            if args.state:
                print(json.dumps(agent.get_state(), ensure_ascii=False, indent=2))
                return 0

            if args.command:
                print(json.dumps(agent.command(args.command), ensure_ascii=False))
                return 0

            if args.block:
                print(json.dumps(agent.block_at(*args.block), ensure_ascii=False))
                return 0

            if args.probe:
                scen = _probe_scenario(max(1, args.probe_ticks))
            elif args.scenario:
                scen = scenario_mod.load(args.scenario)
            else:
                print(json.dumps(status, ensure_ascii=False, indent=2))
                return 0

            if not args.json:
                print(f"scenario: {scen.name} ({scen.total_ticks} ticks, {len(scen.steps)} steps)")

            result = run_scenario(
                agent,
                scen,
                log=(lambda *_: None) if args.json else (lambda *a: print(" ".join(str(x) for x in a[1:]))),
                command_sender=agent.command,
                block_reader=agent.block_at,
            )
            jsonl_path, summary_path = write_run(
                result,
                args.out_dir,
                sample_fields=scen.sample,
                agent_status={"transport": "mod", "status": status},
            )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, indent=2))
            else:
                _print_summary(summary)
                print(f"jsonl   : {jsonl_path}")
                print(f"summary : {summary_path}")
            return 0
    except (ModPipeError, AgentError, RunError, ScenarioError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("提示：游戏内是否已加载 probe mod，并且已经进入存档？", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())