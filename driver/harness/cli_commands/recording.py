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
        watch_specs: list[dict[str, int]] = []
        for raw in args.watch:
            spec_str = str(raw).strip()
            parts = spec_str.split(":")
            if not 1 <= len(parts) <= 3:
                parser.error(
                    f"invalid --watch {raw!r}; expected COMP[:OFFSET[:SIZE]] or @ADDR[:SIZE]"
                )
            if parts[0].startswith("@"):
                if len(parts) > 2:
                    parser.error(f"invalid --watch {raw!r}; @ADDR takes @ADDR[:SIZE]")
                spec: dict[str, int] = {"addr": int(parts[0][1:], 0)}
                if len(parts) == 2 and parts[1] != "":
                    spec["size"] = int(parts[1], 0)
                watch_specs.append(spec)
            else:
                spec = {"component_id": int(parts[0], 0)}
                if len(parts) >= 2 and parts[1] != "":
                    spec["offset"] = int(parts[1], 0)
                if len(parts) == 3 and parts[2] != "":
                    spec["size"] = int(parts[2], 0)
                watch_specs.append(spec)
        if watch_specs:
            return emit_json(
                client.record_bin_start(args.period, watch=watch_specs), ensure_ascii=False
            )
        return emit_json(client.record_bin_start(args.period), ensure_ascii=False)
    if args.command == "record-stop":
        return emit_json(client.record_bin_stop(), ensure_ascii=False)
    if args.command == "record-dump":
        watch_entries = []
        if args.all:
            entries = client.record_dump_all(since=args.since, chunk=args.limit, format=args.format)
        else:
            raw_dump = client.record_dump(args.since, limit=args.limit, format=args.format)
            entries = decode_dump(raw_dump)
            if raw_dump.get("watch_count"):
                from ..record import decode_watch_dump

                watch_entries = decode_watch_dump(raw_dump)
        rows = [entry.to_dict() for entry in entries]
        if watch_entries and len(watch_entries) == len(rows):
            for row, w in zip(rows, watch_entries, strict=True):
                row["watch"] = list(w.values)
                row["watch_mask"] = w.valid_mask
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
