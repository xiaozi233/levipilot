#!/usr/bin/env python3
"""Validate the append-only landing ledger.

Catches the failure mode the skill cares about: a ``confirmed`` claim with only
dynamic evidence, a probe without its raw output, or a pending probe that was
never claimed/abandoned.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
KINDS = {"probe", "claim", "erratum", "dead-end", "decision", "abandon"}
RESULTS = {"confirmed", "refuted", "inconclusive", "live-only"}
TIERS = {"code", "constant", "live", "inference"}
HEX_ADDRESS = re.compile(r"^0x[0-9A-Fa-f]{6,16}$")


class ReportError(ValueError):
    """Malformed ledger data."""


def read_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReportError(f"{path}:{line_no}: {exc}") from exc
        if not isinstance(record, dict):
            raise ReportError(f"{path}:{line_no}: record must be an object")
        records.append(record)
    return records


def probe_refs(record: dict[str, Any]) -> set[str]:
    ref = record.get("probe_ref")
    if isinstance(ref, str):
        return {ref}
    if isinstance(ref, list):
        return {item for item in ref if isinstance(item, str)}
    return set()


def pending(records: list[dict[str, Any]]) -> list[str]:
    probe_ids = {
        str(item.get("id")) for item in records if item.get("kind") == "probe" and item.get("id")
    }
    closed: set[str] = set()
    for record in records:
        if record.get("kind") in {"claim", "erratum", "abandon", "dead-end"}:
            closed.update(probe_refs(record))
    return sorted(probe_ids - closed)


def validate_records(
    records: list[dict[str, Any]], case_dir: Path, root: Path
) -> list[str]:
    errors: list[str] = []
    probe_ids: set[str] = set()
    for record in records:
        ident = record.get("id")
        if not isinstance(ident, str) or not ident:
            errors.append("record without id")
            continue
        kind = record.get("kind")
        if kind not in KINDS:
            errors.append(f"{ident}: bad kind {kind!r}")
            continue
        for field in ("round", "seq", "version", "case"):
            if field not in record:
                errors.append(f"{ident}: missing {field}")
        if kind == "probe":
            probe_ids.add(ident)
            probe = record.get("probe")
            if not isinstance(probe, dict):
                errors.append(f"{ident}: probe must be an object")
                continue
            scope = probe.get("scope")
            if not isinstance(scope, str) or not scope.strip():
                errors.append(f"{ident}: probe.scope must be non-empty")
            for field in ("script", "output"):
                value = probe.get(field)
                if not isinstance(value, str) or not value:
                    errors.append(f"{ident}: probe.{field} missing")
                    continue
                target = root / value
                if not target.exists():
                    errors.append(f"{ident}: probe.{field} does not exist: {value}")
                elif field == "output" and target.stat().st_size == 0:
                    errors.append(f"{ident}: probe.output is empty: {value}")
            continue
        if kind == "abandon":
            if not isinstance(record.get("reason"), str) or not record["reason"].strip():
                errors.append(f"{ident}: abandon needs reason")
            continue
        if kind in {"claim", "erratum"}:
            result = record.get("result")
            if result not in RESULTS:
                errors.append(f"{ident}: bad result {result!r}")
            tier = record.get("evidence_tier")
            if tier not in TIERS:
                errors.append(f"{ident}: bad evidence_tier {tier!r}")
            if not isinstance(record.get("evidence"), str) or not record["evidence"].strip():
                errors.append(f"{ident}: evidence must be non-empty")
            closure = record.get("static_closure")
            if result == "confirmed":
                if tier not in {"code", "constant"}:
                    errors.append(f"{ident}: confirmed needs code/constant tier")
                if not isinstance(closure, str) or not closure.strip():
                    errors.append(f"{ident}: confirmed needs static_closure")
            if tier in {"code", "constant"} and not isinstance(closure, str):
                errors.append(f"{ident}: {tier} claim needs static_closure")
            refs = probe_refs(record)
            missing = refs - probe_ids
            if missing:
                errors.append(f"{ident}: unknown probe_ref {', '.join(sorted(missing))}")
            roles = record.get("roles") or {}
            if not isinstance(roles, dict):
                errors.append(f"{ident}: roles must be an object")
            else:
                for name, address in roles.items():
                    if not isinstance(name, str) or not name.startswith("@"):
                        errors.append(f"{ident}: role must start with @: {name!r}")
                    if address is not None and (
                        not isinstance(address, str) or not HEX_ADDRESS.match(address)
                    ):
                        errors.append(f"{ident}: bad role address {address!r}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--findings-root", type=Path, default=ROOT / "skills" / "bedrock-re" / "findings")
    parser.add_argument("--version")
    parser.add_argument("--case")
    parser.add_argument("--allow-pending", action="store_true")
    args = parser.parse_args(argv)
    paths: list[Path] = []
    if args.case:
        if not args.version:
            print("lint_report: --case needs --version", file=sys.stderr)
            return 2
        paths.append(args.findings_root / args.version / "cases" / args.case / "journal.jsonl")
    else:
        paths.extend(sorted(args.findings_root.glob("*/cases/*/journal.jsonl")))
    if not paths:
        print("lint_report: no journals found", file=sys.stderr)
        return 2
    failures = 0
    for path in paths:
        if not path.exists():
            print(f"MISSING {path.parent.name}: journal does not exist", file=sys.stderr)
            failures += 1
            continue
        try:
            records = read_journal(path)
        except ReportError as exc:
            print(f"MISSING {exc}", file=sys.stderr)
            failures += 1
            continue
        if not records:
            print(f"MISSING {path.parent.name}: journal is empty", file=sys.stderr)
            failures += 1
            continue
        errors = validate_records(records, path.parent, ROOT)
        for error in errors:
            print(f"MISSING {path.parent.name}: {error}", file=sys.stderr)
            failures += len(errors)
        if not args.allow_pending:
            open_ids = pending(records)
            if open_ids:
                print(
                    f"MISSING {path.parent.name}: pending probes {', '.join(open_ids)}",
                    file=sys.stderr,
                )
                failures += 1
    if failures:
        return 1
    print("lint_report: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
