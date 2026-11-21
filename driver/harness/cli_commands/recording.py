"""CLI recording command execution."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..commands import ModClient
from ..record import decode_dump
from ._emit import emit_json


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


COMMANDS = frozenset(
    {
        "record",
        "record-start",
        "record-stop",
        "record-dump",
        "macro",
    }
)


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> int | None:
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
        return 0
    if args.command == "record-start":
        return emit_json(client.record_bin_start(args.period), ensure_ascii=False)
    if args.command == "record-stop":
        return emit_json(client.record_bin_stop(), ensure_ascii=False)
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
        return 0
    if args.command == "macro":
        if args.macro_command == "load":
            return emit_json(client.macro_load(args.name, args.path), ensure_ascii=False)
        if args.macro_command == "run":
            return emit_json(client.macro_run(start=args.start, end=args.end, loop=args.loop))
        if args.macro_command == "stop":
            return emit_json(client.macro_stop())
        if args.macro_command == "status":
            return emit_json(client.macro_status(), ensure_ascii=False, indent=2)
        if args.macro_command == "list":
            return emit_json(client.macro_list(), ensure_ascii=False, indent=2)
        if args.macro_command == "rec-start":
            return emit_json(client.macro_record_start(args.max_ticks))
        if args.macro_command == "rec-stop":
            return emit_json(client.macro_record_stop(args.name))
        return 0
    return None
