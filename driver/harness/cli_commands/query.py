"""CLI query command execution."""

from __future__ import annotations

import argparse
from typing import Any

from ..commands import ModClient
from ._emit import emit_json

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
        "equip",
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


def _item_options(args: argparse.Namespace) -> dict[str, Any]:
    """Forward optional item flags only when passed so baseline calls stay intact."""
    extra: dict[str, Any] = {}
    if args.damage is not None:
        extra["damage"] = args.damage
    if args.flight is not None:
        extra["flight"] = args.flight
    return extra


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> int | None:
    if args.command == "chat-history":
        return emit_json(client.chat_history(args.limit), ensure_ascii=False, indent=2)
    if args.command == "state":
        return emit_json(client.state(), ensure_ascii=False, indent=2)
    if args.command == "entities":
        return emit_json(client.entities(args.radius, args.limit), ensure_ascii=False, indent=2)
    if args.command == "blocks":
        if len(args.positions) % 3:
            parser.error("blocks 需要 3 的倍数个坐标")
        positions = [args.positions[i : i + 3] for i in range(0, len(args.positions), 3)]
        return emit_json(client.blocks(positions), ensure_ascii=False, indent=2)
    if args.command == "scan":
        return emit_json(
            client.scan(
                args.coords[:3],
                args.coords[3:],
                include_air=args.include_air,
                limit=args.limit,
            ),
            ensure_ascii=False,
            indent=2,
        )
    if args.command == "slot":
        return emit_json(client.select_slot(args.slot))
    if args.command == "inventory":
        return emit_json(client.inventory(), ensure_ascii=False, indent=2)
    if args.command == "give":
        return emit_json(
            client.give(args.item, count=args.count, aux=args.aux, **_item_options(args)),
            ensure_ascii=False,
        )
    if args.command == "equip":
        return emit_json(
            client.equip(args.target, slot=args.slot),
            ensure_ascii=False,
        )
    if args.command == "set-item":
        return emit_json(
            client.set_item(
                args.slot,
                args.item,
                count=args.count,
                aux=args.aux,
                container=args.container,
                **_item_options(args),
            ),
            ensure_ascii=False,
        )
    if args.command == "clear-item":
        return emit_json(
            client.clear_item(args.slot, container=args.container),
            ensure_ascii=False,
        )
    if args.command == "clear-inventory":
        return emit_json(client.clear_inventory(), ensure_ascii=False)
    if args.command == "entity":
        return emit_json(client.entity(args.runtime_id), ensure_ascii=False, indent=2)
    if args.command == "attack":
        return emit_json(client.attack_entity(args.runtime_id), ensure_ascii=False)
    if args.command == "interact":
        return emit_json(client.interact_entity(args.runtime_id), ensure_ascii=False)
    if args.command == "look-entity":
        return emit_json(client.look_at_entity(args.runtime_id), ensure_ascii=False)
    if args.command == "ecs-registries":
        extra = {"scope": args.scope} if args.scope is not None else {}
        return emit_json(client.ecs_registries(**extra), ensure_ascii=False, indent=2)
    if args.command == "ecs-entity":
        return emit_json(client.ecs_entity(args.runtime_id), ensure_ascii=False, indent=2)
    if args.command == "ecs-components":
        return emit_json(client.ecs_components(args.runtime_id), ensure_ascii=False, indent=2)
    if args.command == "ecs-component":
        return emit_json(
            client.ecs_component(args.runtime_id, args.component_id, length=args.len),
            ensure_ascii=False,
            indent=2,
        )
    return None
