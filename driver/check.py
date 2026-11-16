"""Unified pre-commit verification CLI for LeviPilot.

Usage::

    py -3.11 driver/check.py               # full suite (lint, types, docs, guards, unit tests)
    py -3.11 driver/check.py --fast        # skip the full unittest suite
    py -3.11 driver/check.py --docs-only   # run doc, guard, link, and diff whitespace checks
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

# ``python driver/check.py`` puts driver/ on sys.path, which hides the package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent

_FENCE_OPEN_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(`+)[^\n]*?\1")
_LINK_RE = re.compile(r"!?\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(((?:[^()\n]|\([^()\n]*\))+)\)")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]+:")
_DRIVE_RE = re.compile(r"^[a-zA-Z]:")


@dataclass(frozen=True)
class CheckSpec:
    """One verification step: either an external command or an in-process check."""

    name: str
    command: list[str] | None = None
    fn: Callable[[Path], tuple[bool, str]] | None = None


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single verification step."""

    name: str
    passed: bool
    duration: float
    output: str = ""


def _print_safe(text: str) -> None:
    """Print a line, falling back to UTF-8 bytes under restricted consoles."""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        buffer = getattr(getattr(sys, "stdout", None), "buffer", None)
        if buffer is None:
            raise
        buffer.write((text + "\n").encode("utf-8", "replace"))
        buffer.flush()


def iter_markdown_files(root: Path = REPO_ROOT) -> list[Path]:
    """Return the Markdown files checked for relative link targets."""
    files: list[Path] = []
    for rel in ("README.md", "AGENTS.md", "mod/README.md"):
        candidate = root / rel
        if candidate.exists():
            files.append(candidate)
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        files.extend(sorted(docs_dir.rglob("*.md")))
    return files


def _strip_code_preserving_lines(text: str) -> list[str]:
    """Strip fenced code blocks and inline code spans while preserving line indices."""
    lines = text.splitlines()
    cleaned: list[str] = []
    in_fence: tuple[str, int] | None = None
    for line in lines:
        if in_fence is not None:
            fence_char, fence_len = in_fence
            stripped = line.strip()
            if (
                len(stripped) >= fence_len
                and set(stripped) == {fence_char}
                and line.lstrip().startswith(fence_char * fence_len)
            ):
                in_fence = None
            cleaned.append("")
            continue

        match = _FENCE_OPEN_RE.match(line)
        if match:
            fence = match.group(1)
            rest = line[match.end() :]
            if fence[0] == "~" or "`" not in rest:
                in_fence = (fence[0], len(fence))
                cleaned.append("")
                continue

        cleaned.append(_INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line))
    return cleaned


def _extract_link_target(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith("<"):
        end = raw.find(">")
        if end != -1:
            raw = raw[1:end].strip()
    else:
        raw = raw.split(None, 1)[0]
    return raw.split("#", 1)[0].split("?", 1)[0].strip()


def find_broken_markdown_links(root: Path = REPO_ROOT) -> list[str]:
    """Return human-readable errors for missing relative Markdown link targets."""
    errors: list[str] = []
    for path in iter_markdown_files(root):
        rel_path = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"{rel_path}: cannot read ({exc})")
            continue

        for line_no, line in enumerate(_strip_code_preserving_lines(text), 1):
            for match in _LINK_RE.finditer(line):
                target = _extract_link_target(match.group(1))
                if not target or target.startswith("//") or _SCHEME_RE.match(target):
                    continue
                if target.startswith(("/", "\\")) or _DRIVE_RE.match(target):
                    errors.append(f"{rel_path}:{line_no}: non-relative link {target}")
                    continue
                candidate = (path.parent / target).resolve()
                if candidate.exists():
                    continue
                unquoted = unquote(target)
                if unquoted != target and (path.parent / unquoted).resolve().exists():
                    continue
                errors.append(f"{rel_path}:{line_no}: broken link {target}")
    return errors


def check_markdown_links(root: Path = REPO_ROOT) -> tuple[bool, str]:
    """Verify every relative Markdown link in top-level and ``docs/`` docs resolves."""
    files = iter_markdown_files(root)
    errors = find_broken_markdown_links(root)
    if errors:
        return False, "\n".join(errors)
    return True, f"markdown links: ok ({len(files)} files checked)"


def build_checks(
    *,
    docs_only: bool = False,
    fast: bool = False,
    root: Path = REPO_ROOT,
) -> list[CheckSpec]:
    """Build the ordered list of checks for the selected mode."""
    py = sys.executable
    checks: list[CheckSpec] = []
    if not docs_only:
        checks.extend(
            [
                CheckSpec("ruff check", command=[py, "-m", "ruff", "check", "driver", "tests"]),
                CheckSpec(
                    "ruff format",
                    command=[py, "-m", "ruff", "format", "--check", "driver", "tests"],
                ),
                CheckSpec("mypy", command=[py, "-m", "mypy"]),
            ]
        )

    checks.extend(
        [
            CheckSpec(
                "lint_public",
                command=[py, str(root / "skills" / "bedrock-re" / "checks" / "lint_public.py")],
            ),
            CheckSpec(
                "lint_skill",
                command=[py, str(root / "skills" / "bedrock-re" / "checks" / "lint_skill.py")],
            ),
        ]
    )

    guards_dir = root / "skills" / "bedrock-re" / "checks" / "guards"
    if guards_dir.is_dir():
        for guard in sorted(guards_dir.glob("check_*.py")):
            checks.append(CheckSpec(guard.stem, command=[py, str(guard)]))

    if not docs_only:
        checks.append(
            CheckSpec(
                "gen_profiles",
                command=[py, str(root / "driver" / "gen_profiles.py"), "--check"],
            )
        )

    checks.extend(
        [
            CheckSpec("markdown links", fn=check_markdown_links),
            CheckSpec("git diff --check", command=["git", "diff", "--check"]),
        ]
    )

    if not docs_only and not fast:
        checks.append(
            CheckSpec(
                "unittest",
                command=[py, "-m", "unittest", "discover", "-s", "tests", "-v"],
            )
        )
    return checks


def _run_spec(spec: CheckSpec, root: Path) -> CheckResult:
    started = time.monotonic()
    if spec.fn is not None:
        passed, output = spec.fn(root)
        return CheckResult(
            name=spec.name,
            passed=passed,
            duration=time.monotonic() - started,
            output=output,
        )
    if spec.command is None:
        raise ValueError(f"check {spec.name!r} has neither command nor callable")
    try:
        completed = subprocess.run(
            spec.command,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return CheckResult(
            name=spec.name,
            passed=False,
            duration=time.monotonic() - started,
            output=str(exc),
        )
    combined = "\n".join(
        part.rstrip() for part in (completed.stdout, completed.stderr) if part and part.strip()
    )
    return CheckResult(
        name=spec.name,
        passed=completed.returncode == 0,
        duration=time.monotonic() - started,
        output=combined,
    )


def run_checks(
    *,
    docs_only: bool = False,
    fast: bool = False,
    root: Path = REPO_ROOT,
) -> tuple[int, list[CheckResult]]:
    """Run all selected checks, report per-check status, and return `(exit_code, results)`."""
    specs = build_checks(docs_only=docs_only, fast=fast, root=root)
    results: list[CheckResult] = []
    for spec in specs:
        _print_safe(f"[RUN ] {spec.name}")
        result = _run_spec(spec, root)
        results.append(result)
        tag = "PASS" if result.passed else "FAIL"
        _print_safe(f"[{tag}] {result.name} ({result.duration:.2f}s)")
        if not result.passed and result.output:
            for line in result.output.splitlines():
                _print_safe(f"  {line}")

    passed_count = sum(1 for item in results if item.passed)
    failed = [item.name for item in results if not item.passed]
    if failed:
        _print_safe(f"{passed_count}/{len(results)} checks passed (failed: {', '.join(failed)})")
        return 1, results
    _print_safe(f"{passed_count}/{len(results)} checks passed")
    return 0, results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--docs-only",
        action="store_true",
        help="run only doc, skill, guard, markdown link, and git whitespace checks",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="skip the full unittest suite",
    )
    args = parser.parse_args(argv)
    exit_code, _results = run_checks(docs_only=args.docs_only, fast=args.fast, root=REPO_ROOT)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
