"""CLI diagnostics command execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..commands import ModClient
from ._emit import emit_json

COMMANDS = frozenset(
    {
        "ping",
        "status",
        "diag",
        "diagnose",
        "profile-dump",
        "profile-probe",
        "signature-check",
        "panic",
        "overhead",
        "busy-test",
        "spool-flush",
        "selftest-av",
    }
)


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> int | None:
    if args.command == "ping":
        return emit_json(client.ping(), ensure_ascii=False)
    if args.command == "status":
        return emit_json(client.status(), ensure_ascii=False)
    if args.command == "diag":
        return emit_json(client.diag(), ensure_ascii=False)
    if args.command == "diagnose":
        return emit_json(client.diagnose(), ensure_ascii=False, indent=2)
    if args.command == "profile-dump":
        dump = client.profile_dump()
        if args.write:
            Path(args.write).write_text(
                json.dumps(dump["profile"], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"wrote {args.write}")
            return 1 if isinstance(dump, dict) and dump.get("ok") is False else 0
        return emit_json(dump, ensure_ascii=False, indent=2)
    if args.command == "profile-probe":
        return emit_json(client.profile_probe(args.build_id), ensure_ascii=False, indent=2)
    if args.command == "signature-check":
        return emit_json(
            client.signature_check(args.name, args.expected_hex),
            ensure_ascii=False,
        )
    if args.command == "panic":
        return emit_json(client.panic(args.level), ensure_ascii=False)
    if args.command == "overhead":
        return emit_json(client.overhead(), ensure_ascii=False, indent=2)
    if args.command == "busy-test":
        return emit_json(client.selftest_busy(args.us, args.ticks), ensure_ascii=False)
    if args.command == "spool-flush":
        return emit_json(client.spool_flush(), ensure_ascii=False)
    if args.command == "selftest-av":
        return emit_json(client.selftest_av(), ensure_ascii=False)
    return None
