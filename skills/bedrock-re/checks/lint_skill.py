#!/usr/bin/env python3
"""Static checks for the skill text itself.

- ``methods.md`` must not contain version-specific addresses.
- Markdown links must resolve relative to the skill root.
- Every ``@role`` used in methods/pipeline must exist in a role profile
  (``@role`` and ``@version-exception`` are prose placeholders).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "skills" / "bedrock-re"
ADDRESS = re.compile(r"0x1[45][0-9A-Fa-f]{7}")
ROLE = re.compile(r"@[a-z][a-z0-9_-]*")
PLACEHOLDERS = {"@role", "@version-exception"}


def markdown_links(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text)


def frontmatter_errors(path: Path, expected_name: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return [f"{path.name}: missing YAML frontmatter"]
    end = text.find("\n---", 4)
    if end < 0:
        return [f"{path.name}: unterminated YAML frontmatter"]
    block = text[4:end]
    name = re.search(r"^name:\s*(\S.*?)\s*$", block, re.MULTILINE)
    description = re.search(r"^description:\s*(\S.*?)\s*$", block, re.MULTILINE)
    errors: list[str] = []
    if name is None or name.group(1) != expected_name:
        errors.append(f"{path.name}: name must be {expected_name!r}")
    if description is None or not description.group(1).strip():
        errors.append(f"{path.name}: description must be a non-empty single line")
    return errors


def known_roles() -> set[str]:
    roles: set[str] = set()
    for path in (ROOT / "docs" / "roles").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        roles.update((data.get("roles") or {}).keys())
    return roles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", type=Path, default=SKILL)
    args = parser.parse_args(argv)
    errors: list[str] = []
    skill_md = args.skill / "SKILL.md"
    if not skill_md.exists():
        errors.append(f"missing {skill_md}")
    else:
        errors.extend(frontmatter_errors(skill_md, args.skill.name))
    methods = args.skill / "references" / "methods.md"
    if not methods.exists():
        errors.append(f"missing {methods}")
    else:
        text = methods.read_text(encoding="utf-8")
        for match in ADDRESS.finditer(text):
            line = text[: match.start()].count("\n") + 1
            errors.append(f"methods.md:{line}: bare address {match.group(0)}")
    for path in [args.skill / "SKILL.md", *sorted((args.skill / "references").glob("*.md"))]:
        if not path.exists():
            errors.append(f"missing {path}")
            continue
        for target in markdown_links(path):
            if "://" in target or target.startswith("mailto:"):
                continue
            candidate = (path.parent / target).resolve()
            if not candidate.exists():
                errors.append(f"{path.relative_to(ROOT)}: broken link {target}")
    roles = known_roles()
    for rel in ("references/methods.md", "references/pipeline.md"):
        path = args.skill / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for name in sorted(set(ROLE.findall(text))):
            if name in PLACEHOLDERS:
                continue
            if name not in roles:
                errors.append(f"{rel}: unknown role {name}")
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("lint_skill: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
