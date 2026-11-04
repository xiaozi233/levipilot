"""Shared world gates, waits and fixtures for the live selftest domains."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from driver.harness.lab import Builder, world_state
from driver.harness.record import RecordEntry
from driver.selftest_support import Checks
from driver.transport_mod import ModClient, ModPipeError, ModTimeout

ROOT = Path(__file__).resolve().parents[1]

# Fixed swim fixture: 3x3x10 water column inside a stone shell at (900, 900).
SWIM_X, SWIM_Y, SWIM_Z = 900, -55.5, 900


class WorldLost(ModPipeError):
    """The level disappeared mid-check.

    Everything a check would measure afterwards is derived from that loss, so
    the run stops instead of letting the next check overwrite the first failure
    (historically a ``feet_x`` KeyError hid the original disconnect).
    """


def require_world(client: ModClient, where: str) -> None:
    """Raise :class:`WorldLost` unless the client is still inside the level."""
    kind = world_state(client)
    if kind != "world":
        raise WorldLost(f"{where}: world state is {kind}")


def dump_packet_tap(client: ModClient, path: str | None) -> None:
    """Write the packet-identity ring to ``path`` (diagnostics; never fatal)."""
    if not path:
        return
    try:
        rows = client.packet_drain(0, 8192)
        Path(path).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    except (ModPipeError, OSError) as exc:
        print(f"NOTE  packet_tap_dump  (failed: {exc})")
        return
    print(f"NOTE  packet_tap_dump  ({rows.get('count', 0)} rows -> {path})")


def wait_still(client: ModClient, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.state()
        if state.get("in_world") is not True:
            raise WorldLost("wait_still: player left the world")
        if (
            state.get("on_ground")
            and abs(float(state.get("vx", 0.0))) < 0.01
            and abs(float(state.get("vz", 0.0))) < 0.01
        ):
            time.sleep(0.3)
            return
        time.sleep(0.05)


def wait_screen(client: ModClient, expected: str, timeout: float = 2.0) -> str:
    deadline = time.monotonic() + timeout
    name = ""
    while time.monotonic() < deadline:
        name = str(client.screen().get("name", ""))
        if name == expected:
            return name
        time.sleep(0.05)
    return name


def wait_ticks(client: ModClient, ticks: int, timeout: float = 6.0) -> int:
    status = client.status()
    if status.get("in_world") is not True:
        raise WorldLost("wait_ticks: player is not in a world")
    start = int(status.get("tick", 0))
    deadline = time.monotonic() + timeout
    while True:
        status = client.status()
        if status.get("in_world") is not True:
            raise WorldLost(f"wait_ticks: world lost after tick {start}")
        current = int(status.get("tick", 0))
        if current - start >= ticks:
            return current - start
        if time.monotonic() > deadline:
            return current - start
        time.sleep(0.01)


def wait_rotation(client: ModClient, yaw: float, pitch: float, timeout: float = 3.0) -> None:
    """Require the requested camera pose on three distinct consecutive ticks.

    A server teleport receipt precedes client synchronization. Starting the
    next teleport before this gate can preserve the old server rotation.
    """
    deadline = time.monotonic() + timeout
    last_tick: int | None = None
    streak = 0
    while True:
        state = client.state()
        if state.get("in_world") is not True:
            raise WorldLost("wait_rotation: player left the world")
        tick = int(state["tick"])
        if tick != last_tick:
            matches = (
                abs((float(state["yaw"]) - yaw + 180) % 360 - 180) < 0.1
                and abs(float(state["pitch"]) - pitch) < 0.1
            )
            consecutive = last_tick is not None and tick == last_tick + 1
            streak = (streak + 1 if consecutive else 1) if matches else 0
            last_tick = tick
            if streak >= 3:
                return
        if time.monotonic() >= deadline:
            raise ModTimeout(
                f"rotation did not settle: yaw={state.get('yaw')} want={yaw}, "
                f"pitch={state.get('pitch')} want={pitch}, matching_ticks={streak}/3"
            )
        # state() already waits for a fresh tick snapshot. An extra delay here
        # can cross the next capture boundary and manufacture a sampling gap.


def slot_item(snapshot: dict[str, Any], slot: int) -> dict[str, Any] | None:
    for row in snapshot.get("slots", []):
        if int(row.get("slot", -1)) == slot:
            return row.get("item")
    return None


def find_item(snapshot: dict[str, Any], item_type: str) -> tuple[int | None, dict[str, Any] | None]:
    for row in snapshot.get("slots", []):
        item = row.get("item")
        if item and item.get("type") == item_type:
            return int(row["slot"]), item
    return None, None


def jump_arcs(entries: list[RecordEntry]) -> list[list[float]]:
    """Extract complete, contiguous ground-to-ground arcs relative to takeoff."""
    arcs: list[list[float]] = []
    start: int | None = None
    for index in range(1, len(entries)):
        before, row = entries[index - 1], entries[index]
        if row.tick != before.tick + 1:
            start = None
            continue
        if before.on_ground and not row.on_ground and row.feet_y > before.feet_y + 0.05:
            start = index - 1
        if start is not None and row.on_ground:
            floor = entries[start].feet_y
            arcs.append([entry.feet_y - floor for entry in entries[start : index + 1]])
            start = None
    return arcs


def prepare_swim_fixture(client: ModClient, checks: Checks, name: str) -> bool:
    # Load the fixture's chunks before the deferred block commands. The
    # player starts above the shell while it is built, then enters its water.
    client.teleport(SWIM_X + 0.5, -51, SWIM_Z + 0.5)
    wait_ticks(client, 3)
    pool = Builder(client).build(
        [
            f"fill {SWIM_X - 2} -64 {SWIM_Z - 2} {SWIM_X + 2} -54 {SWIM_Z + 2} stone",
            f"fill {SWIM_X - 1} -63 {SWIM_Z - 1} {SWIM_X + 1} -54 {SWIM_Z + 1} water",
        ],
        {
            (SWIM_X, -58, SWIM_Z): "minecraft:water",
            (SWIM_X, -63, SWIM_Z): "minecraft:water",
            (SWIM_X - 2, -58, SWIM_Z): "minecraft:stone",
            (SWIM_X + 2, -58, SWIM_Z): "minecraft:stone",
        },
    )
    return checks.check(name, bool(pool) and all(row.ok for row in pool), str(pool))
