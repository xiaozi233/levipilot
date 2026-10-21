"""Command-line entry point for the harness."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .client import ModClient
from .errors import ModPipeError
from .protocol import PIPE_NAME


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def _add_macro_parser(parent: argparse._SubParsersAction) -> None:
    macro = parent.add_parser("macro", help="macro file playback/recording")
    sub = macro.add_subparsers(dest="macro_command", required=True)

    load = sub.add_parser("load")
    load.add_argument("--name", default="default")
    load.add_argument("--path")

    run = sub.add_parser("run")
    run.add_argument("--start", type=int, default=0)
    run.add_argument("--end", type=int)
    run.add_argument("--loop", action="store_true")

    sub.add_parser("stop")
    sub.add_parser("status")
    sub.add_parser("list")

    rec_start = sub.add_parser("rec-start")
    rec_start.add_argument("--max-ticks", type=int, default=0)

    rec_stop = sub.add_parser("rec-stop")
    rec_stop.add_argument("--name", default="recorded")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipe", default=PIPE_NAME)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping")
    sub.add_parser("status")
    sub.add_parser("diag")
    sub.add_parser("state")

    command = sub.add_parser("command", help="run a server command (fill/tp/...)")
    command.add_argument("text")

    sub.add_parser("mouse")
    sub.add_parser("mouse-clear")

    entities = sub.add_parser("entities")
    entities.add_argument("--radius", type=float, default=16.0)
    entities.add_argument("--limit", type=int, default=64)

    blocks = sub.add_parser("blocks")
    blocks.add_argument("positions", nargs="+", type=float, help="x y z [x y z ...]")

    slot = sub.add_parser("slot")
    slot.add_argument("slot", type=int)

    sub.add_parser("inventory")

    give = sub.add_parser("give")
    give.add_argument("item")
    give.add_argument("--count", type=int, default=1)
    give.add_argument("--aux", type=int, default=0)

    set_item = sub.add_parser("set-item")
    set_item.add_argument("slot", type=int)
    set_item.add_argument("item")
    set_item.add_argument("--count", type=int, default=1)
    set_item.add_argument("--aux", type=int, default=0)
    set_item.add_argument(
        "--container", default="inventory", choices=["inventory", "armor", "offhand"]
    )

    clear_item = sub.add_parser("clear-item")
    clear_item.add_argument("slot", type=int)
    clear_item.add_argument(
        "--container", default="inventory", choices=["inventory", "armor", "offhand"]
    )

    sub.add_parser("clear-inventory")

    entity = sub.add_parser("entity")
    entity.add_argument("runtime_id", type=int)

    attack = sub.add_parser("attack")
    attack.add_argument("runtime_id", type=int)

    interact = sub.add_parser("interact")
    interact.add_argument("runtime_id", type=int)

    look_entity = sub.add_parser("look-entity")
    look_entity.add_argument("runtime_id", type=int)

    click = sub.add_parser("click")
    click.add_argument("button", choices=["left", "right"])
    click.add_argument("--reach", type=float, default=5.5)

    navigate = sub.add_parser("navigate", help="closed-loop walk to x,z")
    navigate.add_argument("x", type=float)
    navigate.add_argument("z", type=float)
    navigate.add_argument("--tolerance", type=float, default=0.35)
    navigate.add_argument("--sprint", action="store_true")
    navigate.add_argument("--max-ticks", type=int, default=600)

    mine = sub.add_parser("mine", help="break one block")
    mine.add_argument("x", type=int)
    mine.add_argument("y", type=int)
    mine.add_argument("z", type=int)
    mine.add_argument("--max-ticks", type=int, default=400)

    place = sub.add_parser("place", help="place the held block into x,y,z")
    place.add_argument("x", type=int)
    place.add_argument("y", type=int)
    place.add_argument("z", type=int)
    place.add_argument("--against", nargs=3, type=int, metavar=("AX", "AY", "AZ"))
    place.add_argument("--face", type=int, choices=range(6))
    place.add_argument("--expected")
    place.add_argument("--slot", type=int)
    place.add_argument("--max-ticks", type=int, default=30)

    use = sub.add_parser("use", help="hold and release the held item")
    use.add_argument("--ticks", type=int, default=32)

    sub.add_parser("task-status")
    sub.add_parser("task-stop")

    sub.add_parser("worlds")
    enter_world = sub.add_parser("enter-world", help="start an existing world without the menu UI")
    enter_world.add_argument("--name")
    enter_world.add_argument("--id")
    enter_world.add_argument("--intent", type=int, default=0)
    enter_world.add_argument("--wait", action="store_true", help="wait until state.in_world")
    sub.add_parser("leave-world")

    options_set = sub.add_parser("options-set", help="apply option save-tag values")
    options_set.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    options_set.add_argument("--text", help="raw options.txt-style key:value lines")
    sub.add_parser("options-save")

    fly = sub.add_parser("fly")
    fly.add_argument("--off", action="store_true")

    teleport = sub.add_parser("teleport")
    teleport.add_argument("x", type=float)
    teleport.add_argument("y", type=float)
    teleport.add_argument("z", type=float)
    teleport.add_argument("--yaw", type=float)
    teleport.add_argument("--pitch", type=float)

    set_input = sub.add_parser("set")
    set_input.add_argument("--forward", type=float, default=0.0)
    set_input.add_argument("--strafe", type=float, default=0.0)
    set_input.add_argument("--sprint", action="store_true")
    set_input.add_argument("--sneak", action="store_true")
    set_input.add_argument("--jump", action="store_true")
    set_input.add_argument("--rmb", action="store_true")
    set_input.add_argument("--lmb", action="store_true")
    set_input.add_argument("--ticks", type=int, default=1)

    look = sub.add_parser("look")
    look.add_argument("--yaw", type=float, required=True)
    look.add_argument("--pitch", type=float, default=0.0)
    look.add_argument("--absolute", action="store_true", help="treat yaw/pitch as absolute")
    look.add_argument("--ticks", type=int, default=1)

    sub.add_parser("jump")
    sub.add_parser("stopmove")

    record = sub.add_parser("record")
    record.add_argument("--ticks", type=int, default=40)
    record.add_argument("--out", type=Path, default=None)
    record.add_argument("--move-forward", type=float, default=0.0)
    record.add_argument("--move-strafe", type=float, default=0.0)
    record.add_argument("--move-ticks", type=int, default=0)

    _add_macro_parser(sub)

    args = parser.parse_args(argv)

    try:
        with ModClient(args.pipe) as client:
            if args.command == "ping":
                print(json.dumps(client.ping(), ensure_ascii=False))
            elif args.command == "status":
                print(json.dumps(client.status(), ensure_ascii=False))
            elif args.command == "diag":
                print(json.dumps(client.diag(), ensure_ascii=False))
            elif args.command == "command":
                print(json.dumps(client.command(args.text), ensure_ascii=False))
            elif args.command == "mouse":
                print(json.dumps(client.mouse(), ensure_ascii=False))
            elif args.command == "mouse-clear":
                print(json.dumps(client.mouse_clear(), ensure_ascii=False))
            elif args.command == "state":
                print(json.dumps(client.state(), ensure_ascii=False, indent=2))
            elif args.command == "entities":
                print(
                    json.dumps(
                        client.entities(args.radius, args.limit), ensure_ascii=False, indent=2
                    )
                )
            elif args.command == "blocks":
                if len(args.positions) % 3:
                    parser.error("blocks 需要 3 的倍数个坐标")
                positions = [args.positions[i : i + 3] for i in range(0, len(args.positions), 3)]
                print(json.dumps(client.blocks(positions), ensure_ascii=False, indent=2))
            elif args.command == "slot":
                print(json.dumps(client.select_slot(args.slot)))
            elif args.command == "inventory":
                print(json.dumps(client.inventory(), ensure_ascii=False, indent=2))
            elif args.command == "give":
                print(
                    json.dumps(
                        client.give(args.item, count=args.count, aux=args.aux),
                        ensure_ascii=False,
                    )
                )
            elif args.command == "set-item":
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
            elif args.command == "clear-item":
                print(
                    json.dumps(
                        client.clear_item(args.slot, container=args.container),
                        ensure_ascii=False,
                    )
                )
            elif args.command == "clear-inventory":
                print(json.dumps(client.clear_inventory(), ensure_ascii=False))
            elif args.command == "entity":
                print(json.dumps(client.entity(args.runtime_id), ensure_ascii=False, indent=2))
            elif args.command == "attack":
                print(json.dumps(client.attack_entity(args.runtime_id), ensure_ascii=False))
            elif args.command == "interact":
                print(json.dumps(client.interact_entity(args.runtime_id), ensure_ascii=False))
            elif args.command == "look-entity":
                print(json.dumps(client.look_at_entity(args.runtime_id), ensure_ascii=False))
            elif args.command == "click":
                print(json.dumps(client.click(args.button, args.reach)))
            elif args.command == "navigate":
                print(
                    json.dumps(
                        client.navigate(
                            args.x,
                            args.z,
                            tolerance=args.tolerance,
                            sprint=args.sprint,
                            max_ticks=args.max_ticks,
                        ),
                        ensure_ascii=False,
                    )
                )
            elif args.command == "mine":
                print(
                    json.dumps(
                        client.mine_block(args.x, args.y, args.z, max_ticks=args.max_ticks),
                        ensure_ascii=False,
                    )
                )
            elif args.command == "place":
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
            elif args.command == "use":
                print(json.dumps(client.use_item(args.ticks), ensure_ascii=False))
            elif args.command == "task-status":
                print(json.dumps(client.task_status(), ensure_ascii=False, indent=2))
            elif args.command == "task-stop":
                print(json.dumps(client.task_stop(), ensure_ascii=False))
            elif args.command == "worlds":
                print(json.dumps(client.worlds(), ensure_ascii=False, indent=2))
            elif args.command == "enter-world":
                result = client.enter_world(args.name, world_id=args.id, intent=args.intent)
                if args.wait:
                    result = client.wait_in_world()
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif args.command == "leave-world":
                print(json.dumps(client.leave_world(), ensure_ascii=False))
            elif args.command == "options-set":
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
            elif args.command == "options-save":
                print(json.dumps(client.save_options(), ensure_ascii=False))
            elif args.command == "fly":
                print(json.dumps(client.fly(not args.off)))
            elif args.command == "teleport":
                print(
                    json.dumps(
                        client.teleport(args.x, args.y, args.z, yaw=args.yaw, pitch=args.pitch)
                    )
                )
            elif args.command == "set":
                print(
                    json.dumps(
                        client.set_input(
                            args.forward,
                            args.strafe,
                            sprint=args.sprint,
                            sneak=args.sneak,
                            jump=args.jump,
                            rmb=args.rmb,
                            lmb=args.lmb,
                            ticks=args.ticks,
                        )
                    )
                )
            elif args.command == "look":
                print(
                    json.dumps(
                        client.look(
                            args.yaw,
                            args.pitch,
                            relative=not args.absolute,
                            ticks=args.ticks,
                        )
                    )
                )
            elif args.command == "jump":
                print(json.dumps(client.jump()))
            elif args.command == "stopmove":
                print(json.dumps(client.stop_move()))
            elif args.command == "record":
                samples = client.record(
                    args.ticks,
                    move_forward=args.move_forward,
                    move_strafe=args.move_strafe,
                    move_ticks=args.move_ticks,
                )
                if args.out is not None:
                    _write_jsonl(args.out, samples)
                print(f"samples={len(samples)}")
                if samples:
                    print(json.dumps(samples[0], ensure_ascii=False))
                    print(json.dumps(samples[-1], ensure_ascii=False))
            elif args.command == "macro":
                if args.macro_command == "load":
                    print(json.dumps(client.macro_load(args.name, args.path), ensure_ascii=False))
                elif args.macro_command == "run":
                    print(
                        json.dumps(client.macro_run(start=args.start, end=args.end, loop=args.loop))
                    )
                elif args.macro_command == "stop":
                    print(json.dumps(client.macro_stop()))
                elif args.macro_command == "status":
                    print(json.dumps(client.macro_status(), ensure_ascii=False, indent=2))
                elif args.macro_command == "list":
                    print(json.dumps(client.macro_list(), ensure_ascii=False, indent=2))
                elif args.macro_command == "rec-start":
                    print(json.dumps(client.macro_record_start(args.max_ticks)))
                elif args.macro_command == "rec-stop":
                    print(json.dumps(client.macro_record_stop(args.name)))
    except ModPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("提示：游戏内是否已加载 probe/harness mod，并且已经进入存档？", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
