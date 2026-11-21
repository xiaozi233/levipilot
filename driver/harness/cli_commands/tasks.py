"""CLI tasks command execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..commands import ModClient
from ._emit import emit_json

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


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> int | None:
    if args.command == "navigate":
        return emit_json(
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
    if args.command == "jump-when":
        return emit_json(
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
    if args.command == "route":
        path = Path(args.waypoints)
        if path.is_file():
            waypoints = json.loads(path.read_text(encoding="utf-8"))
        else:
            waypoints = json.loads(args.waypoints)
        return emit_json(
            client.route(waypoints, sprint=args.sprint),
            ensure_ascii=False,
            indent=2,
        )
    if args.command == "mine":
        return emit_json(
            client.mine_block(args.x, args.y, args.z, max_ticks=args.max_ticks),
            ensure_ascii=False,
        )
    if args.command == "place":
        against = tuple(args.against) if args.against is not None else None
        return emit_json(
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
    if args.command == "use":
        return emit_json(client.use_item(args.ticks), ensure_ascii=False)
    if args.command == "task-status":
        return emit_json(client.task_status(), ensure_ascii=False, indent=2)
    if args.command == "task-stop":
        return emit_json(client.task_stop(), ensure_ascii=False)
    return None
