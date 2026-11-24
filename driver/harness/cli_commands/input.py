"""CLI input command execution."""

from __future__ import annotations

import argparse

from ..commands import ModClient
from ..lab import jump_key, swim_drive, swim_stop, swimming_state
from ._emit import emit_json

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
        "focus-cfg",
        "togglefly",
        "swim",
        "stopmove",
    }
)


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> int | None:
    if args.command == "mouse":
        return emit_json(client.mouse(), ensure_ascii=False)
    if args.command == "mouse-clear":
        return emit_json(client.mouse_clear(), ensure_ascii=False)
    if args.command == "key-event":
        return emit_json(
            client.key_event(
                args.code,
                not args.up,
                repeat=args.repeat,
                repeat_ms=args.repeat_ms,
            ),
            ensure_ascii=False,
        )
    if args.command == "key-state":
        return emit_json(client.key_state(args.code), ensure_ascii=False)
    if args.command == "screen":
        return emit_json(client.screen(), ensure_ascii=False)
    if args.command == "mouse-event":
        down: bool | None = None
        if args.up:
            down = False
        elif args.down:
            down = True
        return emit_json(
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
    if args.command == "click":
        return emit_json(client.click(args.button, args.reach))
    if args.command == "input-cfg":
        return emit_json(
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
    if args.command == "fly":
        return emit_json(client.fly(not args.off))
    if args.command == "set":
        return emit_json(
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
    if args.command == "look":
        return emit_json(
            client.look(
                args.yaw,
                args.pitch,
                relative=not args.absolute,
                ticks=args.ticks,
            )
        )
    if args.command == "jump":
        return emit_json(client.jump())
    if args.command == "jump-key":
        return emit_json(jump_key(client))
    if args.command == "input-lock":
        return emit_json(client.input_lock(not args.off))
    if args.command == "focus-cfg":
        return emit_json(
            client.focus_cfg(
                background_guard=args.background_guard,
                suppress_cursor_grab=args.suppress_cursor_grab,
                suppress_focus_pause=args.suppress_focus_pause,
            ),
            ensure_ascii=False,
        )
    if args.command == "togglefly":
        wanted = True if args.on else (False if args.off else None)
        return emit_json(client.toggle_fly(wanted), ensure_ascii=False)
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
        return emit_json(result, ensure_ascii=False)
    if args.command == "stopmove":
        return emit_json(client.stop_move())
    return None
