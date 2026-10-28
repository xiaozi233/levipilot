#!/usr/bin/env python3
"""Validate role profiles against the resolver's rules and the offsets profile."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SKILL = ROOT / "skills" / "bedrock-re"


def main() -> int:
    resolver = SKILL / "roles" / "resolve.py"
    completed = subprocess.run(
        [sys.executable, str(resolver), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        print(completed.stdout, end="")
        print(completed.stderr, end="")
        return completed.returncode
    failures = 0
    for path in sorted((ROOT / "docs" / "roles").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for name, entry in (data.get("roles") or {}).items():
            if entry.get("status") == "unresolved" and entry.get("address") is not None:
                print(f"{path.name}: {name}: unresolved role carries an address")
                failures += 1
            if entry.get("status") == "resolved" and not entry.get("evidence"):
                print(f"{path.name}: {name}: resolved role has no evidence")
                failures += 1
    if failures:
        return 1
    print("check_roles: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
