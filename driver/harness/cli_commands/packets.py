"""CLI packets command execution."""

from __future__ import annotations

import argparse
import json

from ..commands import ModClient


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> bool:
    if args.command == "packet-capture":
        print(
            json.dumps(
                client.packet_capture(
                    not args.off,
                    ids=args.ids,
                    sample=args.sample,
                    directions=args.directions,
                    serialize_ids=args.serialize_ids,
                ),
                ensure_ascii=False,
            )
        )
        return True
    if args.command == "packet-drain":
        print(json.dumps(client.packet_drain(args.since, args.limit), ensure_ascii=False))
        return True
    if args.command == "packet-bytes":
        print(json.dumps(client.packet_bytes(args.since, args.limit), ensure_ascii=False))
        return True
    if args.command == "packet-stats":
        print(json.dumps(client.packet_stats(), ensure_ascii=False))
        return True
    return False
