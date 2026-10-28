#!/usr/bin/env python3
"""Fail when a tracked text file contains a personal absolute path.

The repository is public.  Generic templates such as ``%APPDATA%`` and
``%USERPROFILE%`` are allowed; machine-specific paths are not.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SKIP_DIRS = {".git", "out", "build", "bin", "local-repo", "__pycache__", ".zcode", "refs"}
TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".txt",
    ".cpp",
    ".h",
    ".hpp",
    ".ps1",
    ".out",
    ".cmd",
}
# Build the patterns from parts so this file does not match itself.
PATTERNS = [
    "C:" + "\\Users\\",
    "C:" + "/Users/",
    "D:" + "\\mcbe-harness",
    "D:" + "/mcbe-harness",
    "AppData" + "\\Roaming",
]


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    failures = 0
    for path in iter_files(args.root):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError as exc:
            print(f"{path}: cannot read: {exc}", file=sys.stderr)
            failures += 1
            continue
        for line_no, line in enumerate(lines, 1):
            for pattern in PATTERNS:
                if pattern in line:
                    print(f"{path.relative_to(args.root)}:{line_no}: personal path pattern {pattern!r}")
                    failures += 1
    if failures:
        print(f"lint_public: {failures} personal path occurrence(s)", file=sys.stderr)
        return 1
    print("lint_public: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
