"""CLI input command execution."""

from __future__ import annotations

import argparse
import json

from ..commands import ModClient
from ..lab import jump_key, swim_drive, swim_stop, swimming_state

COMMANDS = frozenset(
    {
        "mouse",
        "mouse-clear",
        "key-event",
        "key-state",
        "screen",
        "mouse-event",
        "click",
        "input-cfg",
        "fly",
        "set",
        "look",
        "jump",
        "jump-key",
        "input-lock",
        "togglefly",
        "swim",
        "stopmove",
    }
)


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> bool:
    if args.command == "mouse":
        print(json.dumps(client.mouse(), ensure_ascii=False))
        return True
    if args.command == "mouse-clear":
        print(json.dumps(client.mouse_clear(), ensure_ascii=False))
        return True
    if args.command == "key-event":
        print(
            json.dumps(
                client.key_event(
                    args.code,
                    not args.up,
                    repeat=args.repeat,
                    repeat_ms=args.repeat_ms,
                ),
                ensure_ascii=False,
            )
        )
        return True
    if args.command == "key-state":
        print(json.dumps(client.key_state(args.code), ensure_ascii=False))
        return True
    if args.command == "screen":
        print(json.dumps(client.screen(), ensure_ascii=False))
        return True
    if args.command == "mouse-event":
        down: bool | None = None
        if args.up:
            down = False
        elif args.down:
            down = True
        print(
            json.dumps(
                client.mouse_event(
                    args.action,
                    data=args.data,
                    down=down,
                    dx=args.dx,
                    dy=args.dy,
                    x=args.x,
                    y=args.y,
                ),
                ensure_ascii=False,
            )
        )
        return True
    if args.command == "click":
        print(json.dumps(client.click(args.button, args.reach)))
        return True
    if args.command == "input-cfg":
        print(
            json.dumps(
                client.input_cfg(
                    source=args.source,
                    controller=args.controller,
                    focus=args.focus,
                    mode=args.mode,
                    jump_impulse=args.jump_impulse,
                    force=args.force,
                ),
                ensure_ascii=False,
            )
        )
        return True
    if args.command == "fly":
        print(json.dumps(client.fly(not args.off)))
        return True
    if args.command == "set":
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
        return True
    if args.command == "look":
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
        return True
    if args.command == "jump":
        print(json.dumps(client.jump()))
        return True
    if args.command == "jump-key":
        print(json.dumps(jump_key(client)))
        return True
    if args.command == "input-lock":
        print(json.dumps(client.input_lock(not args.off)))
        return True
    if args.command == "togglefly":
        wanted = True if args.on else (False if args.off else None)
        print(json.dumps(client.toggle_fly(wanted), ensure_ascii=False))
        return True
    if args.command == "swim":
        if args.stop:
            result = swim_stop(client)
        elif args.drive:
            result = swim_drive(
                client,
                sprint=not args.no_sprint,
                timeout=args.timeout,
                hold=not args.release,
            )
        else:
            result = {"event": "swim_status", "swimming": swimming_state(client)}
        print(json.dumps(result, ensure_ascii=False))
        return True
    if args.command == "stopmove":
        print(json.dumps(client.stop_move()))
        return True
    return False
