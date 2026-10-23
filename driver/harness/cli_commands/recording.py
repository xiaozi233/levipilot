"""CLI recording command execution."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..commands import ModClient
from ..record import decode_dump


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> bool:
    if args.command == "record":
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
        return True
    if args.command == "record-start":
        print(json.dumps(client.record_bin_start(args.period), ensure_ascii=False))
        return True
    if args.command == "record-stop":
        print(json.dumps(client.record_bin_stop(), ensure_ascii=False))
        return True
    if args.command == "record-dump":
        if args.all:
            entries = client.record_dump_all(since=args.since, chunk=args.limit, format=args.format)
        else:
            entries = decode_dump(
                client.record_dump(args.since, limit=args.limit, format=args.format)
            )
        rows = [entry.to_dict() for entry in entries]
        if args.out is not None:
            _write_jsonl(args.out, rows)
        print(f"entries={len(rows)}")
        if rows:
            print(json.dumps(rows[0], ensure_ascii=False))
            print(json.dumps(rows[-1], ensure_ascii=False))
        return True
    if args.command == "macro":
        if args.macro_command == "load":
            print(json.dumps(client.macro_load(args.name, args.path), ensure_ascii=False))
        elif args.macro_command == "run":
            print(json.dumps(client.macro_run(start=args.start, end=args.end, loop=args.loop)))
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
        return True
    return False
