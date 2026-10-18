"""Attach to Minecraft and run scenarios / probes.

Usage:
    py -3.11 driver/run.py                        # 状态快照
    py -3.11 driver/run.py --diagnose             # 组件/registry 诊断
    py -3.11 driver/run.py --probe                # 前进 40 tick，验证输入注入
    py -3.11 driver/run.py --scenario scenarios/example.json
    py -3.11 driver/run.py 12345 --scenario ...   # 按 PID
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver import scenario as scenario_mod  # noqa: E402
from driver.blocks import format_block, name_for_hash  # noqa: E402
from driver.native_cua import NativeCua  # noqa: E402
from driver.output import write_run  # noqa: E402
from driver.runner import RunError, run_scenario  # noqa: E402
from driver.scenario import ScenarioError  # noqa: E402
from driver.session import HarnessError, attach  # noqa: E402


def _target(value: str | None) -> int | str | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _probe_scenario(ticks: int) -> scenario_mod.Scenario:
    return scenario_mod.normalize(
        {
            "name": "probe-forward",
            "description": "传送到默认点 (0.5, -63, 0.5)，朝向 0/0，前进 N tick。",
            "steps": [
                {"type": "tp", "pos": [0.5, -63, 0.5]},
                {"type": "look", "yaw": 0, "pitch": 0, "settle_ticks": 2},
                {"type": "wait", "ticks": 5},
                {"type": "input", "world": [0, 1], "ticks": ticks},
                {"type": "stop"},
            ],
            "sample": ["pos", "vel", "on_ground", "yaw", "pitch", "input"],
            "output": "probe-forward",
        }
    )


def _print_summary(summary: dict) -> None:
    delta = summary.get("displacement")
    delta_text = (
        "(" + ", ".join(f"{v:+.4f}" for v in delta) + ")" if delta else "n/a"
    )
    print("--- summary ---")
    print(
        f"ticks recorded : {summary.get('ticks_recorded')} "
        f"(dropped {summary.get('ticks_dropped')})"
    )
    print(f"displacement   : {delta_text}")
    distance = summary.get("distance")
    print(f"distance       : {distance:.4f}" if distance is not None else "distance       : n/a")
    max_speed = summary.get("max_h_speed")
    mean_speed = summary.get("mean_h_speed")
    if max_speed is not None:
        print(f"h speed        : max {max_speed:.4f} / mean {mean_speed:.4f} blocks/tick")
    ground = summary.get("on_ground_ratio")
    if ground is not None:
        print(f"on ground      : {ground * 100:.1f}%")
    for row in summary.get("steps", []):
        d = row.get("distance")
        d_text = f"{d:.4f}" if d is not None else "n/a"
        print(
            f"  step {row['index']:>2} {row['type']:<6} "
            f"ticks={row['step_ticks']:<3} samples={row['samples']:<3} dist={d_text}"
        )
    for warning in summary.get("warnings", []):
        print(f"  warning: {warning}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", help="进程名或 PID（默认 Minecraft.Windows.exe）")
    parser.add_argument("--scenario", help="场景 JSON 路径")
    parser.add_argument("--probe", action="store_true", help="运行内置前进探针")
    parser.add_argument("--probe-ticks", type=int, default=40, help="探针前进 tick 数")
    parser.add_argument("--diagnose", action="store_true", help="打印组件/registry 诊断")
    parser.add_argument(
        "--block",
        nargs=3,
        metavar=("X", "Y", "Z"),
        help="读取指定方块并打印方块名（需要先有一次移动/碰撞以捕获 accessor）",
    )
    parser.add_argument(
        "--scan",
        nargs=4,
        metavar=("X0", "X1", "Z0", "Z1"),
        help="扫描区域，输出每列的表面方块（顶层非空气方块）",
    )
    parser.add_argument("--scan-y-min", type=int, default=None, help="扫描最低 y（默认脚部-8）")
    parser.add_argument("--scan-y-max", type=int, default=None, help="扫描最高 y（默认脚部+2）")
    parser.add_argument("--wait", type=float, default=20.0, help="等待进入世界的秒数")
    parser.add_argument("--out-dir", default=str(ROOT / "out"), help="输出目录")
    parser.add_argument("--json", action="store_true", help="以 JSON 打印诊断/状态")
    parser.add_argument(
        "--no-wait-player",
        action="store_true",
        help="只要求 tick 在跑，不要求已解析到本地玩家",
    )
    parser.add_argument(
        "--no-chat",
        action="store_true",
        help="禁用聊天命令通道（setup.commands / tp 将退回 frida 客户端写入，可能无效）",
    )
    args = parser.parse_args(argv)

    if args.scenario and args.probe:
        parser.error("--scenario 与 --probe 只能选一个")

    try:
        session = attach(_target(args.target))
        try:
            if args.diagnose:
                report = session.diagnose()
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 0 if report.get("ok") else 2

            session.wait_ready(timeout=args.wait, require_player=not args.no_wait_player)

            if args.block:
                bx, by, bz = (float(value) for value in args.block)
                block = session.call("block_at", bx, by, bz)
                if not block.get("ok") and "no block query" in str(block.get("error", "")):
                    # 轻推一下，让游戏产生一次方块查询并缓存 accessor。
                    session.call("move_world", 0.0, 1.0)
                    session.wait_ticks(3)
                    session.call("stop_input")
                    session.wait_ticks(1)
                    block = session.call("block_at", bx, by, bz)
                if args.json:
                    print(json.dumps(block, ensure_ascii=False, indent=2))
                else:
                    print(format_block(block))
                return 0 if block.get("ok") else 2

            if args.scan:
                x0, x1, z0, z1 = (int(value) for value in args.scan)
                if x1 < x0:
                    x0, x1 = x1, x0
                if z1 < z0:
                    z0, z1 = z1, z0
                state_now = session.call("get_state")
                feet_y = int(math.floor(float(state_now["pos"][1])))
                y_max = args.scan_y_max if args.scan_y_max is not None else feet_y + 2
                y_min = args.scan_y_min if args.scan_y_min is not None else feet_y - 8

                # 保证 accessor 已捕获
                probe = session.call("block_at", x0, y_max, z0)
                if not probe.get("ok") and "no block query" in str(probe.get("error", "")):
                    session.call("move_world", 0.0, 1.0)
                    session.wait_ticks(3)
                    session.call("stop_input")
                    session.wait_ticks(1)

                rows = []
                legend: dict[str, str] = {}

                def label_for(result: dict) -> str:
                    if result.get("air"):
                        return "air"
                    name = result.get("name")
                    if not name:
                        raw = result.get("name_hash")
                        name = name_for_hash(int(raw)) if raw else None
                    if not name:
                        raw = result.get("name_hash")
                        if raw is None:
                            return "?"
                        value = int(raw)
                        label = f"h{value & 0xFFFFFFFF:08X}"
                        legend[label] = f"unmapped 0x{value:016X}"
                        return label
                    legend[name] = name
                    return name

                for z in range(z0, z1 + 1):
                    cells = []
                    for x in range(x0, x1 + 1):
                        surface = None
                        for y in range(y_max, y_min - 1, -1):
                            result = session.call("block_at", x, y, z)
                            if not result.get("ok"):
                                raise HarnessError(f"block_at 失败: {result.get('error')}")
                            if not result.get("air"):
                                surface = (y, label_for(result))
                                break
                        cells.append(surface)
                    rows.append((z, cells))

                print(f"scan x=[{x0},{x1}] z=[{z0},{z1}] y=[{y_min},{y_max}]")
                print("  z\\x " + " ".join(f"{x:>16}" for x in range(x0, x1 + 1)))
                for z, cells in rows:
                    parts = []
                    for surface in cells:
                        if surface is None:
                            parts.append(f"{'void':>16}")
                        else:
                            y, label = surface
                            parts.append(f"{y:>6} {label[-9:]:>9}")
                    print(f"{z:>5} " + " ".join(parts))
                if legend:
                    print("legend:")
                    for label in sorted(legend):
                        print(f"  {label:<16} {legend[label]}")
                return 0

            if args.probe:
                scen = _probe_scenario(max(1, args.probe_ticks))
            elif args.scenario:
                scen = scenario_mod.load(args.scenario)
            else:
                print(json.dumps(session.status(), ensure_ascii=False, indent=2))
                return 0

            if not args.json:
                print(f"scenario: {scen.name} ({scen.total_ticks} ticks, {len(scen.steps)} steps)")
                if scen.setup.get("commands"):
                    print(
                        "note: setup.commands 未执行（当前没有命令派发 RPC）；"
                        "请用行为包或手动准备场地",
                        file=sys.stderr,
                    )

            status = session.status()
            if not all(s.get("ok") for s in status.get("signatures", {}).values()):
                bad = [
                    name
                    for name, sig in status.get("signatures", {}).items()
                    if not sig.get("ok")
                ]
                raise HarnessError(f"版本指纹不匹配: {bad}；不要在该版本上写入")

            command_sender = None
            if not args.no_chat:
                try:
                    cua = NativeCua()
                    usable, why = cua.available()
                    if usable:
                        command_sender = cua.send_command
                    else:
                        print(f"note: 未启用聊天命令通道: {why}", file=sys.stderr)
                except Exception as exc:
                    print(f"note: 未启用聊天命令通道: {exc}", file=sys.stderr)

            result = run_scenario(
                session,
                scen,
                log=(lambda *a: None)
                if args.json
                else (lambda *a: print(" ".join(str(x) for x in a[1:]))),
                command_sender=command_sender,
            )
            jsonl_path, summary_path = write_run(
                result,
                args.out_dir,
                sample_fields=scen.sample,
                agent_status=status,
            )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, indent=2))
            else:
                _print_summary(summary)
                print(f"jsonl   : {jsonl_path}")
                print(f"summary : {summary_path}")

            if args.probe and (summary.get("distance") or 0.0) < 0.05:
                print(
                    "probe: 位移几乎为 0 —— 输入注入未生效"
                    "（检查 MoveInput 解析器 hook / 游戏是否聚焦）",
                    file=sys.stderr,
                )
                return 3
            return 0
        finally:
            session.close()
    except (HarnessError, RunError, ScenarioError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
