"""CLI query command execution."""

from __future__ import annotations

import argparse
import json

from ..commands import ModClient

COMMANDS = frozenset(
    {
        "chat-history",
        "state",
        "entities",
        "blocks",
        "scan",
        "slot",
        "inventory",
        "give",
        "set-item",
        "clear-item",
        "clear-inventory",
        "entity",
        "attack",
        "interact",
        "look-entity",
        "ecs-registries",
        "ecs-entity",
        "ecs-components",
        "ecs-component",
    }
)


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> bool:
    if args.command == "chat-history":
        print(json.dumps(client.chat_history(args.limit), ensure_ascii=False, indent=2))
        return True
    if args.command == "state":
        print(json.dumps(client.state(), ensure_ascii=False, indent=2))
        return True
    if args.command == "entities":
        print(json.dumps(client.entities(args.radius, args.limit), ensure_ascii=False, indent=2))
        return True
    if args.command == "blocks":
        if len(args.positions) % 3:
            parser.error("blocks 需要 3 的倍数个坐标")
        positions = [args.positions[i : i + 3] for i in range(0, len(args.positions), 3)]
        print(json.dumps(client.blocks(positions), ensure_ascii=False, indent=2))
        return True
    if args.command == "scan":
        print(
            json.dumps(
                client.scan(
                    args.coords[:3],
                    args.coords[3:],
                    include_air=args.include_air,
                    limit=args.limit,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return True
    if args.command == "slot":
        print(json.dumps(client.select_slot(args.slot)))
        return True
    if args.command == "inventory":
        print(json.dumps(client.inventory(), ensure_ascii=False, indent=2))
        return True
    if args.command == "give":
        print(
            json.dumps(
                client.give(args.item, count=args.count, aux=args.aux),
                ensure_ascii=False,
            )
        )
        return True
    if args.command == "set-item":
        print(
            json.dumps(
                client.set_item(
                    args.slot,
                    args.item,
                    count=args.count,
                    aux=args.aux,
                    container=args.container,
                ),
                ensure_ascii=False,
            )
        )
        return True
    if args.command == "clear-item":
        print(
            json.dumps(
                client.clear_item(args.slot, container=args.container),
                ensure_ascii=False,
            )
        )
        return True
    if args.command == "clear-inventory":
        print(json.dumps(client.clear_inventory(), ensure_ascii=False))
        return True
    if args.command == "entity":
        print(json.dumps(client.entity(args.runtime_id), ensure_ascii=False, indent=2))
        return True
    if args.command == "attack":
        print(json.dumps(client.attack_entity(args.runtime_id), ensure_ascii=False))
        return True
    if args.command == "interact":
        print(json.dumps(client.interact_entity(args.runtime_id), ensure_ascii=False))
        return True
    if args.command == "look-entity":
        print(json.dumps(client.look_at_entity(args.runtime_id), ensure_ascii=False))
        return True
    if args.command == "ecs-registries":
        print(json.dumps(client.ecs_registries(), ensure_ascii=False, indent=2))
        return True
    if args.command == "ecs-entity":
        print(json.dumps(client.ecs_entity(args.runtime_id), ensure_ascii=False, indent=2))
        return True
    if args.command == "ecs-components":
        print(json.dumps(client.ecs_components(args.runtime_id), ensure_ascii=False, indent=2))
        return True
    if args.command == "ecs-component":
        print(
            json.dumps(
                client.ecs_component(args.runtime_id, args.component_id, length=args.len),
                ensure_ascii=False,
                indent=2,
            )
        )
        return True
    return False
