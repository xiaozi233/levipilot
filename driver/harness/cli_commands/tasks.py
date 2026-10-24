"""CLI tasks command execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..commands import ModClient

COMMANDS = frozenset(
    {
        "navigate",
        "jump-when",
        "route",
        "mine",
        "place",
        "use",
        "task-status",
        "task-stop",
    }
)


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> bool:
    if args.command == "navigate":
        print(
            json.dumps(
                client.navigate(
                    args.x,
                    args.z,
                    tolerance=args.tolerance,
                    sprint=args.sprint,
                    max_ticks=args.max_ticks,
                    jump=args.jump,
                    jump_distance=args.jump_distance,
                    edge_lookahead=args.edge_lookahead,
                ),
                ensure_ascii=False,
            )
        )
        return True
    if args.command == "jump-when":
        print(
            json.dumps(
                client.jump_when(
                    args.mode,
                    x=args.x,
                    z=args.z,
                    distance=args.distance,
                    forward=args.forward,
                    sprint=args.sprint,
                    yaw=args.yaw,
                    after_ticks=args.after_ticks,
                    max_ticks=args.max_ticks,
                ),
                ensure_ascii=False,
            )
        )
        return True
    if args.command == "route":
        path = Path(args.waypoints)
        if path.is_file():
            waypoints = json.loads(path.read_text(encoding="utf-8"))
        else:
            waypoints = json.loads(args.waypoints)
        print(
            json.dumps(
                client.route(waypoints, sprint=args.sprint),
                ensure_ascii=False,
                indent=2,
            )
        )
        return True
    if args.command == "mine":
        print(
            json.dumps(
                client.mine_block(args.x, args.y, args.z, max_ticks=args.max_ticks),
                ensure_ascii=False,
            )
        )
        return True
    if args.command == "place":
        against = tuple(args.against) if args.against is not None else None
        print(
            json.dumps(
                client.place_block(
                    args.x,
                    args.y,
                    args.z,
                    against=against,
                    face=args.face,
                    expected=args.expected,
                    slot=args.slot,
                    max_ticks=args.max_ticks,
                ),
                ensure_ascii=False,
            )
        )
        return True
    if args.command == "use":
        print(json.dumps(client.use_item(args.ticks), ensure_ascii=False))
        return True
    if args.command == "task-status":
        print(json.dumps(client.task_status(), ensure_ascii=False, indent=2))
        return True
    if args.command == "task-stop":
        print(json.dumps(client.task_stop(), ensure_ascii=False))
        return True
    return False
