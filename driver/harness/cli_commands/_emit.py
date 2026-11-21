"""Shared JSON printing and exit-code mapping for CLI command handlers."""

from __future__ import annotations

import json
from typing import Any


def emit_json(payload: Any, *, ensure_ascii: bool = True, indent: int | None = None) -> int:
    """Print JSON payload to stdout and return 1 when ``ok`` is explicitly False."""
    print(json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent))
    if isinstance(payload, dict) and payload.get("ok") is False:
        return 1
    return 0
