"""CLI diagnostics command execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..commands import ModClient

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


def run(args: argparse.Namespace, client: ModClient, parser: argparse.ArgumentParser) -> bool:
    if args.command == "ping":
        print(json.dumps(client.ping(), ensure_ascii=False))
        return True
    if args.command == "status":
        print(json.dumps(client.status(), ensure_ascii=False))
        return True
    if args.command == "diag":
        print(json.dumps(client.diag(), ensure_ascii=False))
        return True
    if args.command == "diagnose":
        print(json.dumps(client.diagnose(), ensure_ascii=False, indent=2))
        return True
    if args.command == "profile-dump":
        dump = client.profile_dump()
        if args.write:
            Path(args.write).write_text(
                json.dumps(dump["profile"], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"wrote {args.write}")
        else:
            print(json.dumps(dump, ensure_ascii=False, indent=2))
        return True
    if args.command == "profile-probe":
        print(json.dumps(client.profile_probe(args.build_id), ensure_ascii=False, indent=2))
        return True
    if args.command == "signature-check":
        print(
            json.dumps(
                client.signature_check(args.name, args.expected_hex),
                ensure_ascii=False,
            )
        )
        return True
    if args.command == "panic":
        print(json.dumps(client.panic(args.level), ensure_ascii=False))
        return True
    if args.command == "overhead":
        print(json.dumps(client.overhead(), ensure_ascii=False, indent=2))
        return True
    if args.command == "busy-test":
        print(json.dumps(client.selftest_busy(args.us, args.ticks), ensure_ascii=False))
        return True
    if args.command == "spool-flush":
        print(json.dumps(client.spool_flush(), ensure_ascii=False))
        return True
    if args.command == "selftest-av":
        print(json.dumps(client.selftest_av(), ensure_ascii=False))
        return True
    return False
