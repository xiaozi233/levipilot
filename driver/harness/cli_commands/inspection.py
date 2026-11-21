"""CLI inspection command execution."""

from __future__ import annotations

import argparse
from typing import Any

from ..commands import ModClient
from ._emit import emit_json

COMMANDS = frozenset(
    {
        "mem-read",
        "mem-write",
        "probe",
        "watch-set",
        "watch-clear",
        "watch-list",
        "watch-drain",
        "hook-install",
        "hook-rm",
        "hook-list",
        "hook-drain",
        "hook-call",
    }
)


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> int | None:
    if args.command == "mem-read":
        return emit_json(client.mem_read(args.addr, args.len), ensure_ascii=False, indent=2)
    if args.command == "mem-write":
        return emit_json(client.mem_write(args.addr, args.hex), ensure_ascii=False)
    if args.command == "probe":
        return emit_json(
            client.probe(args.addr, args.kind, length=args.len),
            ensure_ascii=False,
            indent=2,
        )
    if args.command == "watch-set":
        sample_options: dict[str, Any] = {"sample": True} if args.sample else {}
        return emit_json(
            client.watch_set(
                args.addr,
                size=args.size,
                mode=args.mode,
                thread=args.thread,
                tag=args.tag,
                **sample_options,
            ),
            ensure_ascii=False,
        )
    if args.command == "watch-clear":
        return emit_json(client.watch_clear(args.id, all=args.all), ensure_ascii=False)
    if args.command == "watch-list":
        return emit_json(client.watch_list(), ensure_ascii=False, indent=2)
    if args.command == "watch-drain":
        return emit_json(
            client.watch_drain(args.since, limit=args.limit),
            ensure_ascii=False,
            indent=2,
        )
    if args.command == "hook-install":
        symbol = None if args.va else args.symbol
        extra = {}
        if args.mem_size:
            extra = {
                "mem_arg": args.mem_arg,
                "mem_offset": args.mem_offset,
                "mem_size": args.mem_size,
                "mem_deref": args.mem_deref,
            }
        return emit_json(
            client.hook_install(
                symbol,
                va=args.va,
                tag=args.tag,
                sample=args.sample,
                dry_run=args.dry_run,
                **extra,
            ),
            ensure_ascii=False,
            indent=2,
        )
    if args.command == "hook-rm":
        return emit_json(client.hook_rm(args.id), ensure_ascii=False)
    if args.command == "hook-list":
        return emit_json(client.hook_list(), ensure_ascii=False, indent=2)
    if args.command == "hook-drain":
        return emit_json(
            client.hook_drain(args.since, limit=args.limit),
            ensure_ascii=False,
            indent=2,
        )
    if args.command == "hook-call":
        return emit_json(client.hook_call(args.symbol, args.scale), ensure_ascii=False)
    return None
