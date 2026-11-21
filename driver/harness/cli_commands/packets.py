"""CLI packets command execution."""

from __future__ import annotations

import argparse

from ..commands import ModClient
from ._emit import emit_json

COMMANDS = frozenset(
    {
        "packet-capture",
        "packet-drain",
        "packet-bytes",
        "packet-stats",
    }
)


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> int | None:
    if args.command == "packet-capture":
        return emit_json(
            client.packet_capture(
                not args.off,
                ids=args.ids,
                sample=args.sample,
                directions=args.directions,
                serialize_ids=args.serialize_ids,
            ),
            ensure_ascii=False,
        )
    if args.command == "packet-drain":
        return emit_json(client.packet_drain(args.since, args.limit), ensure_ascii=False)
    if args.command == "packet-bytes":
        return emit_json(client.packet_bytes(args.since, args.limit), ensure_ascii=False)
    if args.command == "packet-stats":
        return emit_json(client.packet_stats(), ensure_ascii=False)
    return None
