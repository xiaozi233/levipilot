#!/usr/bin/env python3
"""Doc-drift guard: pos.y is feet; the recorded y is eyes (feet + 1.6200101)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
FILES = [
    ROOT / "skills" / "bedrock-re" / "references" / "experiments.md",
    ROOT / "AGENTS.md",
]
NEEDLE = "1.6200101"


def main() -> int:
    failures = 0
    for path in FILES:
        if not path.exists() or NEEDLE not in path.read_text(encoding="utf-8"):
            print(f"check_eye_feet: {path} lacks {NEEDLE!r}", file=sys.stderr)
            failures += 1
    if failures:
        return 1
    print("check_eye_feet: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
