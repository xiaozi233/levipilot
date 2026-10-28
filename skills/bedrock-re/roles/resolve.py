#!/usr/bin/env python3
"""Resolve RE roles for a Bedrock version.

Roles live in ``docs/roles/<version>.json`` and are deliberately separate from
``docs/offsets/<version>.json`` (mod runtime offsets).  A role is either
``resolved`` with a static address and evidence, or ``unresolved`` with no
address at all.  Guessing is a validation error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
ROLE_DIR = ROOT / "docs" / "roles"
ADDRESS = re.compile(r"^0x[0-9A-Fa-f]{6,16}$")
CONFIDENCE = {"code", "code+live", "constant", "live", "inference", "none"}


class RoleError(ValueError):
    """The role profile is malformed."""


class RoleNotFound(KeyError):
    """The requested role is not in the profile."""


class RoleUnresolved(RoleError):
    """The role exists but has no static address in this version."""


def profile_path(version: str, root: Path = ROOT) -> Path:
    return root / "docs" / "roles" / f"{version}.json"


def load_profile(version: str, root: Path = ROOT) -> dict[str, Any]:
    path = profile_path(version, root)
    if not path.exists():
        raise RoleError(f"missing role profile: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RoleError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RoleError(f"{path}: top level must be an object")
    return data


def resolve(role: str, version: str, root: Path = ROOT) -> str:
    """Return the static address for ``role`` or raise."""
    data = load_profile(version, root)
    roles = data.get("roles")
    if not isinstance(roles, dict):
        raise RoleError(f"{version}: roles must be an object")
    entry = roles.get(role)
    if not isinstance(entry, dict):
        raise RoleNotFound(role)
    if entry.get("status") != "resolved":
        raise RoleUnresolved(f"{role}: unresolved in {version}")
    address = entry.get("address")
    if not isinstance(address, str) or not ADDRESS.match(address):
        raise RoleError(f"{role}: resolved role has no valid address")
    return address


def validate(data: dict[str, Any], root: Path = ROOT) -> list[str]:
    """Return a list of human-readable errors; empty means valid."""
    errors: list[str] = []
    version = data.get("version")
    if not isinstance(version, str) or not version:
        errors.append("version must be a non-empty string")
        version = ""
    roles = data.get("roles")
    if not isinstance(roles, dict):
        errors.append("roles must be an object")
        return errors

    offsets_ref = data.get("offsets_ref")
    if isinstance(offsets_ref, str) and not (root / offsets_ref).exists():
        errors.append(f"offsets_ref does not exist: {offsets_ref}")
    offsets_path = root / "docs" / "offsets" / f"{version}.json"
    if version and offsets_path.exists():
        try:
            offsets = json.loads(offsets_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"offsets profile is not JSON: {exc}")
            offsets = {}
        if isinstance(offsets, dict):
            if offsets.get("version") not in (None, version):
                errors.append(
                    f"version mismatch: roles={version} offsets={offsets.get('version')}"
                )
            build_id = data.get("build_id")
            if build_id and offsets.get("build_id") not in (None, build_id):
                errors.append(
                    f"build_id mismatch: roles={build_id} offsets={offsets.get('build_id')}"
                )

    seen: set[str] = set()
    for name, entry in roles.items():
        if not isinstance(name, str) or not name.startswith("@"):
            errors.append(f"role name must start with @: {name!r}")
            continue
        if name in seen:
            errors.append(f"duplicate role: {name}")
        seen.add(name)
        if not isinstance(entry, dict):
            errors.append(f"{name}: entry must be an object")
            continue
        strategy = entry.get("strategy")
        if not isinstance(strategy, list) or not strategy or not all(
            isinstance(item, str) and item for item in strategy
        ):
            errors.append(f"{name}: strategy must be a non-empty string list")
        status = entry.get("status")
        if status not in ("resolved", "unresolved"):
            errors.append(f"{name}: bad status {status!r}")
            continue
        confidence = entry.get("confidence")
        if confidence not in CONFIDENCE:
            errors.append(f"{name}: bad confidence {confidence!r}")
        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
            errors.append(f"{name}: evidence must be a string list")
            evidence = []
        address = entry.get("address")
        if status == "resolved":
            if not isinstance(address, str) or not ADDRESS.match(address):
                errors.append(f"{name}: resolved role needs a 0x address")
            if confidence not in ("code", "code+live", "constant"):
                errors.append(f"{name}: resolved role needs static confidence")
            if not evidence:
                errors.append(f"{name}: resolved role needs evidence")
        else:
            if address is not None:
                errors.append(f"{name}: unresolved role must not carry an address")
            if confidence != "none":
                errors.append(f"{name}: unresolved role must use confidence=none")

    if not errors:
        resolved = sum(1 for entry in roles.values() if entry.get("status") == "resolved")
        if resolved == 0:
            errors.append("profile has no resolved roles")
    return errors


def matching_profiles(root: Path = ROOT) -> list[str]:
    return sorted(path.stem for path in (root / "docs" / "roles").glob("*.json"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="game version, e.g. 1.26.51.01")
    parser.add_argument("--role", help="role name, e.g. @set_sprinting")
    parser.add_argument("--list", action="store_true", help="list roles and status")
    parser.add_argument("--check", action="store_true", help="validate the profile")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    versions = [args.version] if args.version else matching_profiles(args.root)
    if not versions:
        print("no role profiles under docs/roles", file=sys.stderr)
        return 2
    exit_code = 0
    for version in versions:
        try:
            data = load_profile(version, args.root)
        except RoleError as exc:
            print(str(exc), file=sys.stderr)
            exit_code = 1
            continue
        if args.check:
            errors = validate(data, args.root)
            if errors:
                exit_code = 1
                for error in errors:
                    print(f"{version}: {error}", file=sys.stderr)
            else:
                roles = data.get("roles", {})
                resolved = sum(
                    1 for entry in roles.values() if entry.get("status") == "resolved"
                )
                print(f"{version}: {resolved}/{len(roles)} roles resolved")
            continue
        if args.list:
            roles = data.get("roles", {})
            for name, entry in sorted(roles.items()):
                print(
                    f"{name}\t{entry.get('status')}\t{entry.get('address') or '-'}"
                )
            continue
        if args.role:
            try:
                address = resolve(args.role, version, args.root)
            except RoleUnresolved:
                print(f"{args.role}: unresolved in {version}", file=sys.stderr)
                exit_code = 1
            except RoleNotFound:
                print(f"{args.role}: not found in {version}", file=sys.stderr)
                exit_code = 1
            else:
                if args.json:
                    print(json.dumps({"version": version, "role": args.role, "address": address}))
                else:
                    print(address)
            continue
        parser.print_help()
        return 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
