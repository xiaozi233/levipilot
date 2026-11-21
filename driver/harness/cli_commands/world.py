"""CLI world command execution."""

from __future__ import annotations

import argparse
from typing import Any

from ..commands import ModClient
from ._emit import emit_json

COMMANDS = frozenset(
    {
        "command",
        "checkpoint",
        "worlds",
        "signin",
        "enter-world",
        "leave-world",
        "options-set",
        "options-save",
        "teleport",
        "reset",
    }
)


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> int | None:
    if args.command == "command":
        return emit_json(client.command(args.text), ensure_ascii=False)
    if args.command == "checkpoint":
        if args.checkpoint_command == "save":
            return emit_json(client.checkpoint_save(args.name), ensure_ascii=False)
        if args.checkpoint_command == "load":
            return emit_json(client.checkpoint_load(args.name), ensure_ascii=False)
        if args.checkpoint_command == "list":
            return emit_json(client.checkpoint_list(), ensure_ascii=False, indent=2)
        return emit_json(client.checkpoint_delete(args.name), ensure_ascii=False)
    if args.command == "worlds":
        return emit_json(client.worlds(), ensure_ascii=False, indent=2)
    if args.command == "signin":
        return emit_json(client.signin(), ensure_ascii=False, indent=2)
    if args.command == "enter-world":
        if args.wait:
            result = client.enter_world_ready(
                args.name,
                world_id=args.id,
                intent=args.intent,
                force=args.force,
            )
        else:
            result = client.enter_world(
                args.name,
                world_id=args.id,
                intent=args.intent,
                force=args.force,
            )
        return emit_json(result, ensure_ascii=False, indent=2)
    if args.command == "leave-world":
        return emit_json(client.leave_world(), ensure_ascii=False)
    if args.command == "options-set":
        values: dict[str, str] = {}
        for item in args.set:
            key, sep, value = item.partition("=")
            if not sep:
                raise SystemExit(f"--set 需要 KEY=VALUE：{item!r}")
            values[key] = value
        return emit_json(
            client.set_options(values or None, text=args.text),
            ensure_ascii=False,
        )
    if args.command == "options-save":
        return emit_json(client.save_options(), ensure_ascii=False)
    if args.command == "teleport":
        return emit_json(client.teleport(args.x, args.y, args.z, yaw=args.yaw, pitch=args.pitch))
    if args.command == "reset":
        extra: dict[str, Any] = {"wait_teleport": False} if args.no_wait_teleport else {}
        return emit_json(
            client.reset(args.x, args.y, args.z, yaw=args.yaw, pitch=args.pitch, **extra)
        )
    return None
