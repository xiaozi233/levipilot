"""CLI inspection command execution."""

from __future__ import annotations

import argparse
import json

from ..commands import ModClient

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


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> bool:
    if args.command == "mem-read":
        print(json.dumps(client.mem_read(args.addr, args.len), ensure_ascii=False, indent=2))
        return True
    if args.command == "mem-write":
        print(json.dumps(client.mem_write(args.addr, args.hex), ensure_ascii=False))
        return True
    if args.command == "probe":
        print(
            json.dumps(
                client.probe(args.addr, args.kind, length=args.len),
                ensure_ascii=False,
                indent=2,
            )
        )
        return True
    if args.command == "watch-set":
        print(
            json.dumps(
                client.watch_set(
                    args.addr,
                    size=args.size,
                    mode=args.mode,
                    thread=args.thread,
                    tag=args.tag,
                ),
                ensure_ascii=False,
            )
        )
        return True
    if args.command == "watch-clear":
        print(json.dumps(client.watch_clear(args.id, all=args.all), ensure_ascii=False))
        return True
    if args.command == "watch-list":
        print(json.dumps(client.watch_list(), ensure_ascii=False, indent=2))
        return True
    if args.command == "watch-drain":
        print(
            json.dumps(
                client.watch_drain(args.since, limit=args.limit),
                ensure_ascii=False,
                indent=2,
            )
        )
        return True
    if args.command == "hook-install":
        symbol = None if args.va else args.symbol
        extra = {}
        if args.mem_size:
            extra = {
                "mem_arg": args.mem_arg,
                "mem_offset": args.mem_offset,
                "mem_size": args.mem_size,
            }
        print(
            json.dumps(
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
        )
        return True
    if args.command == "hook-rm":
        print(json.dumps(client.hook_rm(args.id), ensure_ascii=False))
        return True
    if args.command == "hook-list":
        print(json.dumps(client.hook_list(), ensure_ascii=False, indent=2))
        return True
    if args.command == "hook-drain":
        print(
            json.dumps(
                client.hook_drain(args.since, limit=args.limit),
                ensure_ascii=False,
                indent=2,
            )
        )
        return True
    if args.command == "hook-call":
        print(json.dumps(client.hook_call(args.symbol, args.scale), ensure_ascii=False))
        return True
    return False
