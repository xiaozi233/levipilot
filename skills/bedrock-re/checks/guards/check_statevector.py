#!/usr/bin/env python3
"""Doc-drift guard: StateVector+0x18 is not the velocity the harness reports."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
FILES = [
    ROOT / "skills" / "bedrock-re" / "references" / "experiments.md",
    ROOT / "AGENTS.md",
]
NEEDLES = ["0.546", "StateVectorComponent+0x18"]


def main() -> int:
    failures = 0
    for path in FILES:
        if not path.exists():
            print(f"check_statevector: missing {path}", file=sys.stderr)
            failures += 1
            continue
        text = path.read_text(encoding="utf-8")
        for needle in NEEDLES:
            if needle not in text:
                print(f"check_statevector: {path.relative_to(ROOT)} lacks {needle!r}", file=sys.stderr)
                failures += 1
    if failures:
        return 1
    print("check_statevector: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
