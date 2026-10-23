"""Command-line entry point for the harness."""

from __future__ import annotations

import json
import sys

from .cli_commands import diagnostics, input, inspection, packets, query, recording, tasks, world
from .cli_parser import build_parser
from .commands import ModClient
from .errors import ModPipeError
from .packets import decode_packet
from .spool import read_spool


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(description=__doc__)
    args = parser.parse_args(argv)

    if args.command == "spool":
        rows, stats = read_spool(args.path)
        print(
            json.dumps(
                {
                    "path": str(args.path),
                    "rows": stats.rows,
                    "dropped": stats.dropped,
                    "bad_lines": stats.bad_lines,
                    "rotations": stats.rotations,
                },
                ensure_ascii=False,
            )
        )
        selected = rows if args.all else rows[-args.tail :] if args.tail > 0 else []
        for row in selected:
            print(json.dumps(row, ensure_ascii=False))
        return 0

    if args.command == "packet-decode":
        print(json.dumps(decode_packet(args.id, bytes.fromhex(args.hex)), ensure_ascii=False))
        return 0

    try:
        with ModClient(args.pipe) as client:
            for handler in (
                diagnostics.run,
                input.run,
                inspection.run,
                packets.run,
                query.run,
                world.run,
                tasks.run,
                recording.run,
            ):
                if handler(args, client, parser):
                    break
    except ModPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("提示：游戏内是否已加载 probe/harness mod，并且已经进入存档？", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
