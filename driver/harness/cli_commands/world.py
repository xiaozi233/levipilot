"""CLI world command execution."""

from __future__ import annotations

import argparse
import json

from ..commands import ModClient


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> bool:
    if args.command == "command":
        print(json.dumps(client.command(args.text), ensure_ascii=False))
        return True
    if args.command == "checkpoint":
        if args.checkpoint_command == "save":
            print(json.dumps(client.checkpoint_save(args.name), ensure_ascii=False))
        elif args.checkpoint_command == "load":
            print(json.dumps(client.checkpoint_load(args.name), ensure_ascii=False))
        elif args.checkpoint_command == "list":
            print(json.dumps(client.checkpoint_list(), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(client.checkpoint_delete(args.name), ensure_ascii=False))
        return True
    if args.command == "worlds":
        print(json.dumps(client.worlds(), ensure_ascii=False, indent=2))
        return True
    if args.command == "signin":
        print(json.dumps(client.signin(), ensure_ascii=False, indent=2))
        return True
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
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return True
    if args.command == "leave-world":
        print(json.dumps(client.leave_world(), ensure_ascii=False))
        return True
    if args.command == "options-set":
        values: dict[str, str] = {}
        for item in args.set:
            key, sep, value = item.partition("=")
            if not sep:
                raise SystemExit(f"--set 需要 KEY=VALUE：{item!r}")
            values[key] = value
        print(
            json.dumps(
                client.set_options(values or None, text=args.text),
                ensure_ascii=False,
            )
        )
        return True
    if args.command == "options-save":
        print(json.dumps(client.save_options(), ensure_ascii=False))
        return True
    if args.command == "teleport":
        print(json.dumps(client.teleport(args.x, args.y, args.z, yaw=args.yaw, pitch=args.pitch)))
        return True
    if args.command == "reset":
        print(json.dumps(client.reset(args.x, args.y, args.z, yaw=args.yaw, pitch=args.pitch)))
        return True
    return False
