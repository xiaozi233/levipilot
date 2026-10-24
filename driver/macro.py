"""Read, write and generate dmf-mpk compatible macro files.

The format is a top-level JSON array. Each element is a tick with 9 or 11
strings:

    W, A, S, D, Sprint, Sneak, Jump, RMB, LMB, Yaw, Pitch

``Yaw`` and ``Pitch`` are per-tick rotation deltas in degrees. A 9-string tick
means both angles are zero.

This module is independent of the game so macros can be generated or inspected
offline, then loaded by the harness mod with ``macro_load``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

BUTTON_NAMES = ("w", "a", "s", "d", "sprint", "sneak", "jump", "rmb", "lmb")


class MacroFormatError(ValueError):
    pass


@dataclass
class MacroTick:
    w: bool = False
    a: bool = False
    s: bool = False
    d: bool = False
    sprint: bool = False
    sneak: bool = False
    jump: bool = False
    rmb: bool = False
    lmb: bool = False
    yaw: float = 0.0
    pitch: float = 0.0

    def to_row(self) -> list[str]:
        row = ["true" if getattr(self, name) else "false" for name in BUTTON_NAMES]
        row.extend([_format_float(self.yaw), _format_float(self.pitch)])
        return row


def _format_float(value: float) -> str:
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise MacroFormatError("angle must be finite")
    return f"{value:.9g}"


def _parse_bool(value: Any, index: int) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        if value == "true" or value == "1":
            return True
        if value == "false" or value == "0":
            return False
    raise MacroFormatError(f"tick[{index}]: invalid button value {value!r}")


def _parse_angle(value: Any, index: int, column: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MacroFormatError(f"tick[{index}].{column}: invalid number {value!r}") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise MacroFormatError(f"tick[{index}].{column}: angle must be finite")
    return number


def parse_macro(doc: Any) -> list[MacroTick]:
    if not isinstance(doc, list):
        raise MacroFormatError("macro must be a top-level array")
    ticks: list[MacroTick] = []
    for index, row in enumerate(doc):
        if not isinstance(row, list):
            raise MacroFormatError(f"tick[{index}] must be an array")
        if len(row) not in (9, 11):
            raise MacroFormatError(f"tick[{index}] must contain 9 or 11 strings")
        buttons = [_parse_bool(row[i], index) for i in range(9)]
        yaw = _parse_angle(row[9], index, "yaw") if len(row) == 11 else 0.0
        pitch = _parse_angle(row[10], index, "pitch") if len(row) == 11 else 0.0
        w, a, s, d, sprint, sneak, jump, rmb, lmb = buttons
        ticks.append(MacroTick(w, a, s, d, sprint, sneak, jump, rmb, lmb, yaw=yaw, pitch=pitch))
    return ticks


def load_macro(path: str | Path) -> list[MacroTick]:
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MacroFormatError(f"cannot read {path}: {exc}") from exc
    return parse_macro(doc)


def macro_to_json(ticks: Iterable[MacroTick]) -> list[list[str]]:
    return [tick.to_row() for tick in ticks]


def save_macro(path: str | Path, ticks: Iterable[MacroTick]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(macro_to_json(ticks), indent=2), encoding="utf-8")
    return path


def tick_from_dict(value: dict[str, Any]) -> MacroTick:
    """Build a tick from a friendly dict; unknown keys are ignored."""
    known = {k: v for k, v in value.items() if k in MacroTick.__dataclass_fields__}
    return MacroTick(**known)


def generate_ticks(
    ticks: int,
    *,
    forward: bool = False,
    strafe: bool = False,
    sprint: bool = False,
    sneak: bool = False,
    jump: bool = False,
    yaw_per_tick: float = 0.0,
    pitch_per_tick: float = 0.0,
) -> list[MacroTick]:
    """Generate a constant per-tick macro.

    ``strafe`` sets D when true, A when false is not meaningful; use ``d``/``a``
    through :class:`MacroTick` for precise control.
    """
    return [
        MacroTick(
            w=forward,
            d=strafe,
            sprint=sprint,
            sneak=sneak,
            jump=jump,
            yaw=yaw_per_tick,
            pitch=pitch_per_tick,
        )
        for _ in range(ticks)
    ]


def dump_tick(tick: MacroTick) -> dict[str, Any]:
    return asdict(tick)
