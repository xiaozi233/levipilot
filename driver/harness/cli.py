"""Command-line entry point for the harness."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable

from .cli_commands import diagnostics, input, inspection, packets, query, recording, tasks, world
from .cli_parser import build_parser
from .commands import ModClient
from .errors import ModPipeError
from .packets import decode_packet
from .spool import read_spool

# One dispatch table instead of a parser list and a handler list that could
# drift apart: a command defined in cli_parser but missing here fails loudly
# (test_cli.test_every_parser_command_has_a_handler locks the two together).
_Handler = Callable[[argparse.Namespace, ModClient, argparse.ArgumentParser], bool]
_MODULES = (diagnostics, input, inspection, packets, query, world, tasks, recording)
REGISTRY: dict[str, _Handler] = {
    name: module.run for module in _MODULES for name in module.COMMANDS
}
assert len(REGISTRY) == sum(len(module.COMMANDS) for module in _MODULES), (
    "duplicate command names across CLI handler modules"
)
# Commands handled before connecting to the mod.
DIRECT = frozenset({"spool", "packet-decode"})


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
        try:
            payload = bytes.fromhex(args.hex)
        except ValueError as exc:
            print(f"error: --hex 不是合法十六进制：{exc}", file=sys.stderr)
            return 2
        print(json.dumps(decode_packet(args.id, payload), ensure_ascii=False))
        return 0

    handler = REGISTRY.get(args.command)
    if handler is None:
        # A command defined in cli_parser without a handler used to exit 0
        # silently, which scripts read as success.
        parser.error(f"unhandled command: {args.command}")

    try:
        with ModClient(args.pipe) as client:
            handler(args, client, parser)
    except ModPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("提示：游戏内是否已加载 probe/harness mod，并且已经进入存档？", file=sys.stderr)
        return 2
    except (ValueError, json.JSONDecodeError) as exc:
        # Bad numeric/JSON arguments (route waypoints, watch ids, ...) must be
        # a clean exit-2 error, not a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
