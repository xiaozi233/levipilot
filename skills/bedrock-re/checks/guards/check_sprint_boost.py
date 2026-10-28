#!/usr/bin/env python3
"""Doc/arithmetic guard for the sprint ×1.3 attribution.

The constant in code is 0.3 with MULTIPLY_TOTAL, not a literal 1.3; the final
0.13 is recomputed as (0.3 + 1) × 0.1.  The old
``DEFAULT_SPRINT_SPEED_MULTIPLIER`` attribution must stay retracted.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
FILES = [
    ROOT / "skills" / "bedrock-re" / "references" / "experiments.md",
    ROOT / "docs" / "fluid-movement.md",
]
NEEDLES = ["MULTIPLY_TOTAL", "DEFAULT_SPRINT_SPEED_MULTIPLIER"]


def main() -> int:
    if abs((0.3 + 1.0) * 0.1 - 0.13) > 1e-12:
        print("check_sprint_boost: arithmetic mismatch", file=sys.stderr)
        return 1
    failures = 0
    for path in FILES:
        if not path.exists():
            print(f"check_sprint_boost: missing {path}", file=sys.stderr)
            failures += 1
            continue
        text = path.read_text(encoding="utf-8")
        for needle in NEEDLES:
            if needle not in text:
                print(f"check_sprint_boost: {path.relative_to(ROOT)} lacks {needle!r}", file=sys.stderr)
                failures += 1
    if failures:
        return 1
    print("check_sprint_boost: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
