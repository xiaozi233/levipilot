"""Flight-check fixtures with one explicit effect lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from driver.selftest_support import Checks
from driver.transport_mod import ModClient

SENTINEL_EFFECTS = (
    ("night_vision", 16),
    ("water_breathing", 13),
    ("fire_resistance", 12),
    ("invisibility", 14),
    ("regeneration", 10),
    ("resistance", 11),
    ("absorption", 22),
)


def choose_sentinel_effect(existing: set[int]) -> tuple[str, int] | None:
    """Pick a supported long-duration effect absent from the player."""
    return next(
        ((name, effect_id) for name, effect_id in SENTINEL_EFFECTS if effect_id not in existing),
        None,
    )


def required_effect_ids(durations: dict[int, int], elapsed_ticks: int) -> set[int]:
    """Effects that must still exist after elapsed ticks; expired ids are exempt."""
    return {
        effect_id
        for effect_id, duration in durations.items()
        if duration < 0 or duration - elapsed_ticks > 0
    }


class EffectFixture:
    """Install, verify and retire a sentinel while preserving original effects."""

    def __init__(self, original: dict[str, Any]) -> None:
        self.durations = {
            int(row["id"]): int(row.get("duration", 0)) for row in original.get("effects", [])
        }
        self.tick = int(original.get("tick", 0))
        self.sentinel = choose_sentinel_effect(set(self.durations))

    def install(self, client: ModClient, checks: Checks, wait_ticks: Callable[..., int]) -> None:
        if self.sentinel is None:
            checks.note("flight_effect_sentinel", "all supported candidates already present")
            return
        name, effect_id = self.sentinel
        client.command(f"effect @s {name} 3600 0 true")
        wait_ticks(client, 2)
        effects = client.state().get("effects", [])
        checks.check(
            "flight_effect_fixture",
            any(int(row["id"]) == effect_id and int(row.get("duration", 0)) > 0 for row in effects),
            str(effects),
        )

    def verify(self, client: ModClient, checks: Checks, wait_ticks: Callable[..., int]) -> None:
        current = client.state()
        restored_ids = {int(row["id"]) for row in current.get("effects", [])}
        elapsed = int(current["tick"]) - self.tick
        required = required_effect_ids(self.durations, elapsed)
        if self.sentinel is not None:
            required.add(self.sentinel[1])
        checks.check(
            "flight_preserves_effects",
            required.issubset(restored_ids),
            f"required={sorted(required)} actual={sorted(restored_ids)} elapsed={elapsed}",
        )
        if self.sentinel is not None:
            client.command(f"effect @s clear {self.sentinel[0]}")
            wait_ticks(client, 2)

    def verify_restored(self, restored: dict[str, Any], checks: Checks) -> None:
        restored_ids = {int(row["id"]) for row in restored.get("effects", [])}
        required = required_effect_ids(self.durations, int(restored["tick"]) - self.tick)
        checks.check(
            "flight_restores_original_effects",
            required.issubset(restored_ids),
            f"required={sorted(required)} actual={sorted(restored_ids)}",
        )
