"""stepping-12651 round 3: tail closures for the step-up system.

Covers the remaining live items of docs/notes/stepping-12651.md §10:

* fine height scan: 0.5 / 0.5625 / 0.59375 (horizontal chain) / 0.625, all
  heights read back from real collision AABBs;
* low ceiling over the approach + a 0.5 step at the ceiling edge, with and
  without a one-block gap, to test the "pre-deleted ceiling box lets you step
  out from under a low roof" inference (the endpoint AABB check still sees the
  unfiltered list);
* tap 1/1 under a 0.2-clearance ceiling, asserting the 3-tick landing /
  re-trigger sequence row by row;
* combined geometry (0.5 step -> ground gap -> 2-block ceiling front) vs pure
  stairs and pure ceiling edges, all with the same 1/2 tap pattern;
* sneak and creative-flight step comparisons (0.5625 accepted / 0.625 refused).

Every fixture asserts its block states, real collision height, contact with the
front face, absence of accidental jump impulses and the expected outcome of the
scenario it is designed to discriminate.  Raw rows go to the gitignored
out/stepping-tail-1.26.51.01.json.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path
from typing import Any

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "driver").is_dir())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.harness.client import ModClient  # noqa: E402
from driver.harness.lab import (  # noqa: E402
    KEY_SPACE,
    LabSession,
    RecordSession,
    double_space_fly,
    settle,
)

OUT = ROOT / "out" / "stepping-tail-1.26.51.01.json"
STATE_VECTOR = 0x1B5D5238
PLAYER_HALF = 0.3

WATCH = [
    {"component_id": STATE_VECTOR, "offset": 0x18, "size": 4, "name": "mpd_x", "fmt": "f32"},
    {"component_id": STATE_VECTOR, "offset": 0x1C, "size": 4, "name": "mpd_y", "fmt": "f32"},
    {"component_id": STATE_VECTOR, "offset": 0x20, "size": 4, "name": "mpd_z", "fmt": "f32"},
]

# Fixed arena origin: the flat world's natural surface around (1400, 1400) is
# -62 (dirt at -63, bedrock at -64); the arena layer is -61 so the walking
# surface is exactly -60.0.  This area was probed with small stone fills up to
# +59 blocks before the round; the first candidate at (1000, 1000) had a chunk
# boundary the server refused to fill past, which is why the arena lives here.
ORIGIN = (1400.5, -60.0, 1400.5)
LANES = {
    "fine": int(ORIGIN[0]) + 4,
    "edge": int(ORIGIN[0]) + 12,
    "gap": int(ORIGIN[0]) + 20,
    "combo": int(ORIGIN[0]) + 28,
    "tap": int(ORIGIN[0]) + 36,
    "sneak": int(ORIGIN[0]) + 44,
    "flight": int(ORIGIN[0]) + 52,
}

FINE_CASES = [
    {
        "label": "top0.500",
        "block": "snow_layer",
        "states": '["height"=4]',
        "state_key": "height",
        "state_value": 4,
        "top": 0.5,
        "expect": "step",
    },
    {
        "label": "top0.5625",
        "block": "stonecutter_block",
        "states": "",
        "state_key": None,
        "state_value": None,
        "top": 0.5625,
        "expect": "step",
    },
    {
        "label": "top0.59375",
        "block": "iron_chain",
        "states": '["pillar_axis"="x"]',
        "state_key": "pillar_axis",
        "state_value": "x",
        "top": 0.59375,
        "expect": "block",
        "x_extent": 1.0,
        "z_extent": 0.1875,
    },
    {
        "label": "top0.625",
        "block": "snow_layer",
        "states": '["height"=5]',
        "state_key": "height",
        "state_value": 5,
        "top": 0.625,
        "expect": "block",
    },
]

RESULTS: dict[str, Any] = {}
GEO: dict[str, Any] = {}


def cmd(client: ModClient, text: str, ticks: int = 4) -> dict[str, Any]:
    reply = client.command(text)
    client.wait_ticks(ticks)
    return reply


def shape_of(client: ModClient, pos: tuple[int, int, int]) -> dict[str, Any]:
    rows = client.block_shapes([pos], entity_mode="none").get("blocks", [])
    assert rows, f"block_shapes returned no row for {pos}"
    return rows[0]


def assert_shape(
    client: ModClient,
    pos: tuple[int, int, int],
    *,
    name_contains: str,
    top: float | None = None,
    state_key: str | None = None,
    state_value: Any = None,
    x_extent: float | None = None,
    z_extent: float | None = None,
) -> dict[str, Any]:
    """Read a real collision AABB back and check the fixture's own geometry."""
    row = shape_of(client, pos)
    name = str(row.get("name", ""))
    assert name_contains in name, f"{pos}: expected {name_contains}, got {name}"
    collision = row.get("collision", {})
    assert not collision.get("empty", True), f"{pos}: empty collision box: {row}"
    assert bool(row.get("add_ret")), f"{pos}: collision shapes not collected: {row}"
    # block_shapes reports world-space collision boxes: the step height above
    # the block's own base is max.y - block_y (one block above the floor).
    got_top = float(collision["max"][1]) - float(pos[1])
    if top is not None:
        assert abs(got_top - top) <= 1e-4, f"{pos}: collision top {got_top} != {top}: {row}"
    if state_key is not None:
        states = row.get("states", {})
        assert states.get(state_key) == state_value, (
            f"{pos}: state {state_key}={states.get(state_key)!r} != {state_value!r}: {row}"
        )
    if x_extent is not None:
        got = float(collision["max"][0]) - float(collision["min"][0])
        assert abs(got - x_extent) <= 1e-4, f"{pos}: x extent {got} != {x_extent}: {row}"
    if z_extent is not None:
        got = float(collision["max"][2]) - float(collision["min"][2])
        assert abs(got - z_extent) <= 1e-4, f"{pos}: z extent {got} != {z_extent}: {row}"
    return {
        "pos": list(pos),
        "name": name,
        "states": row.get("states", {}),
        "top": round(got_top, 6),
        "x_extent": round(float(collision["max"][0]) - float(collision["min"][0]), 6),
        "z_extent": round(float(collision["max"][2]) - float(collision["min"][2]), 6),
        "collision": collision,
    }


def assert_air(client: ModClient, pos: tuple[int, int, int]) -> None:
    row = shape_of(client, pos)
    assert bool(row.get("air")) or str(row.get("name", "")).endswith(":air"), (
        f"{pos}: expected air, got {row.get('name')}"
    )


def assert_watch(rows: list[dict[str, Any]], label: str) -> None:
    assert all(
        row.get("mpd_x") is not None
        and row.get("mpd_y") is not None
        and row.get("mpd_z") is not None
        for row in rows
    ), f"{label}: mPosDelta watch decode missing"


def compact(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = (
        "tick",
        "x",
        "z",
        "feet_y",
        "vx",
        "vy",
        "vz",
        "on_ground",
        "sprint",
        "sneak",
        "mpd_x",
        "mpd_y",
        "mpd_z",
    )
    return [{key: row.get(key) for key in keys} for row in rows]


def build_arena(client: ModClient) -> None:
    """Use the natural flat surface (grass at -61, feet at -60) as the floor.

    No platform is built: the surface is already flat, and a large ``fill``
    would abort as soon as its volume touches a server-side chunk that is still
    loading.  Every lane below clears only its own 5x6x~40 box after the player
    has been teleported next to it.
    """
    px = int(ORIGIN[0])
    pz = int(ORIGIN[2])
    ground = float(ORIGIN[1])
    layer = int(ground) - 1
    x0, x1 = px - 4, px + 56
    z0, z1 = pz - 4, pz + 50

    client.teleport(px + 0.5, ground, pz + 0.5, yaw=0.0, pitch=0.0)
    client.wait_ticks(24)
    cmd(client, "gamerule falldamage false", 2)
    cmd(client, "gamemode survival @s", 2)
    cmd(client, "effect @s clear", 2)

    probes = [(px, layer, pz), (px, layer + 1, pz)]
    rows = client.blocks(probes).get("blocks", [])
    names = [str(row.get("name", "")) for row in rows]
    assert len(names) == 2 and any(word in names[0] for word in ("grass", "dirt", "stone")), (
        f"arena: natural floor is not solid: {names}"
    )
    assert "air" in names[1], f"arena: spawn cell is not clear: {names}"
    surface = shape_of(client, (px, layer, pz))
    assert not surface.get("collision", {}).get("empty", True), (
        f"arena: floor collision missing: {surface}"
    )
    node_height = float(surface["collision"]["max"][1]) - float(surface["collision"]["min"][1])
    assert abs(node_height - 1.0) <= 1e-4, (
        f"arena: floor is not a full block: {surface['collision']}"
    )
    original = names[0]

    client.teleport(px + 0.5, ground, pz + 0.5, yaw=0.0, pitch=0.0)
    client.wait_ticks(8)

    GEO.update(
        {
            "layer": layer,
            "ground": ground,
            "x0": x0,
            "x1": x1,
            "z0": z0,
            "z1": z1,
            "original_block": original,
            "obstacle_z": pz + 14,
            "start_z": pz + 8.5,
            "lanes": dict(LANES),
        }
    )
    RESULTS["setup"] = {
        "origin": list(ORIGIN),
        "layer": layer,
        "ground": ground,
        "platform": {"x": [x0, x1], "z": [z0, z1]},
        "original_block": original,
        "lanes": dict(LANES),
        "obstacle_z": GEO["obstacle_z"],
        "start_z": GEO["start_z"],
    }


def prepare_lane(client: ModClient, lane: int, z: float) -> None:
    """Stand next to the lane before issuing its (local) block commands.

    A fill that reaches a chunk which the server has not finished loading only
    applies to the loaded prefix; keeping the player at the lane start makes
    every lane command a local, loaded-chunk operation.
    """
    client.teleport(lane + 0.5, GEO["ground"], float(z), yaw=0.0, pitch=0.0)
    client.wait_ticks(10)


def clear_lane(client: ModClient, lane: int, z_from: int, z_to: int) -> None:
    cmd(
        client,
        f"fill {lane - 2} {GEO['layer'] + 1} {z_from} {lane + 2} {GEO['layer'] + 6} {z_to} air",
        3,
    )


def fill_ceiling(
    client: ModClient, lane: int, z_from: int, z_to: int, y: int
) -> list[dict[str, Any]]:
    cmd(client, f"fill {lane - 1} {y} {z_from} {lane + 1} {y} {z_to} stone", 3)
    return [assert_shape(client, (lane, y, z), name_contains="stone") for z in (z_from, z_to)]


def place_row(
    client: ModClient, lane: int, z_positions: list[int], case: dict[str, Any]
) -> list[dict[str, Any]]:
    shapes = []
    for z in z_positions:
        text = f"setblock {lane} {GEO['layer'] + 1} {z} {case['block']} {case['states']}".strip()
        cmd(client, text, 2)
        shapes.append(
            assert_shape(
                client,
                (lane, GEO["layer"] + 1, z),
                name_contains=case["block"],
                top=case["top"],
                state_key=case["state_key"],
                state_value=case["state_value"],
                x_extent=case.get("x_extent"),
                z_extent=case.get("z_extent"),
            )
        )
    return shapes


def pose(client: ModClient, lane: int, z: float) -> int:
    client.teleport(lane + 0.5, GEO["ground"], float(z), yaw=0.0, pitch=0.0)
    client.wait_ticks(6)
    settled = settle(client, on_ground=True, input_sprint=False)
    assert settled["ok"], f"pose lane={lane} z={z}: settle failed: {settled}"
    return int(client.state()["tick"])


def window(rec: RecordSession, start_tick: int, size: int) -> list[dict[str, Any]]:
    return [row for row in rec.rows if int(row["tick"]) - start_tick <= size]


def walk_probe(
    client: ModClient,
    case: dict[str, Any],
    *,
    lane_key: str = "fine",
    sprint: bool = False,
    ticks: int = 52,
) -> dict[str, Any]:
    """Walk into a full-width (or chain-strip) obstacle at walking speed."""
    lane = GEO["lanes"][lane_key]
    z0 = GEO["obstacle_z"]
    prepare_lane(client, lane, z0 + 1)
    clear_lane(client, lane, z0 - 7, z0 + 9)
    shapes = place_row(client, lane, [z0, z0 + 1, z0 + 2], case)
    box = shapes[0]["collision"]
    front = float(box["min"][2]) - PLAYER_HALF
    start_z = front - 1.4
    start_tick = pose(client, lane, start_z)
    with RecordSession(client, period=1, watch=WATCH) as rec:
        client.set_input(forward=1.0, sprint=sprint, ticks=ticks)
        client.wait_ticks(ticks + 1)
        client.set_input()
        client.wait_ticks(4)
    rows = window(rec, start_tick, ticks + 5)
    assert len(rows) >= ticks - 4, f"{case['label']} sprint={sprint}: only {len(rows)} rows"
    assert_watch(rows, f"{case['label']} sprint={sprint}")
    contact = next((i for i, row in enumerate(rows) if float(row["z"]) >= front - 0.05), None)
    assert contact is not None, (
        f"{case['label']} sprint={sprint}: never reached the front face "
        f"(max z={max(float(row['z']) for row in rows):.3f}, front={front:.3f})"
    )
    assert all(bool(row["on_ground"]) for row in rows[: contact + 2]), (
        f"{case['label']} sprint={sprint}: left the ground before contact"
    )
    assert max(float(row["mpd_y"]) for row in rows) < 0.30, (
        f"{case['label']} sprint={sprint}: a jump impulse appeared (walk-only fixture)"
    )
    max_feet = max(float(row["feet_y"]) for row in rows)
    max_z = max(float(row["z"]) for row in rows)
    if case["expect"] == "step":
        assert abs(max_feet - (GEO["ground"] + case["top"])) <= 0.03, (
            f"{case['label']} sprint={sprint}: no clean step-up "
            f"(max feet_y={max_feet:.5f}, expected={GEO['ground'] + case['top']:.5f})"
        )
        assert max_z >= z0 + 0.2, (
            f"{case['label']} sprint={sprint}: never crossed the obstacle (max z={max_z:.3f})"
        )
    else:
        assert max_feet <= GEO["ground"] + 0.03, (
            f"{case['label']} sprint={sprint}: unexpectedly climbed (max feet_y={max_feet:.5f})"
        )
        assert max_z <= front + 0.06, (
            f"{case['label']} sprint={sprint}: passed through the wall (max z={max_z:.3f})"
        )
    step_index = next(
        (i for i, row in enumerate(rows) if float(row["feet_y"]) > GEO["ground"] + 0.02), None
    )
    return {
        "label": case["label"],
        "sprint": sprint,
        "expected": case["expect"],
        "top": case["top"],
        "shapes": shapes,
        "front_face_z": front,
        "contact_index": contact,
        "max_feet_y": round(max_feet, 6),
        "max_z": round(max_z, 6),
        "step_index": step_index,
        "step_tick": None if step_index is None else rows[step_index]["tick"],
        "mpd_y_at_step": None if step_index is None else rows[step_index]["mpd_y"],
        "mpd_z_at_step": None if step_index is None else rows[step_index]["mpd_z"],
        "rows": compact(rows),
    }


def low_roof_probe(client: ModClient, *, with_gap: bool) -> dict[str, Any]:
    """0.5 step at the leading edge of a 0.2-clearance ceiling.

    ``with_gap=False`` puts the ceiling's last block directly over the player's
    AABB when the step front is touched; ``with_gap=True`` ends the ceiling one
    block earlier so the same AABB is already clear.  The endpoint AABB check
    iterates the *unfiltered* collision list, so only the gap variant should
    step up.
    """
    lane = GEO["lanes"]["gap" if with_gap else "edge"]
    z0 = GEO["obstacle_z"]
    prepare_lane(client, lane, z0 - 4)
    clear_lane(client, lane, z0 - 11, z0 + 9)
    ceiling_to = z0 - 2 if with_gap else z0 - 1
    ceiling = fill_ceiling(client, lane, z0 - 9, ceiling_to, GEO["layer"] + 3)
    for z in (z0, z0 + 2):
        assert_air(client, (lane, GEO["layer"] + 2, z))
    assert_air(client, (lane, GEO["layer"] + 3, z0))
    shapes = place_row(client, lane, [z0, z0 + 1, z0 + 2], FINE_CASES[0])
    start_tick = pose(client, lane, z0 - 4.5)
    with RecordSession(client, period=1, watch=WATCH) as rec:
        client.set_input(forward=1.0, ticks=64)
        client.wait_ticks(65)
        client.set_input()
        client.wait_ticks(4)
    rows = window(rec, start_tick, 69)
    assert len(rows) >= 60, f"low roof gap={with_gap}: only {len(rows)} rows"
    assert_watch(rows, f"low roof gap={with_gap}")
    front = z0 - PLAYER_HALF
    contact = next((i for i, row in enumerate(rows) if float(row["z"]) >= front - 0.05), None)
    assert contact is not None, f"low roof gap={with_gap}: never reached the step front face"
    assert max(float(row["mpd_y"]) for row in rows) < 0.30, (
        f"low roof gap={with_gap}: unexpected jump impulse (walk-only fixture)"
    )
    max_feet = max(float(row["feet_y"]) for row in rows)
    max_z = max(float(row["z"]) for row in rows)
    stepped = max_feet > GEO["ground"] + 0.45
    assert stepped is with_gap, (
        f"low roof gap={with_gap}: stepped={stepped}, max_feet_y={max_feet:.6f}, max_z={max_z:.6f}"
    )
    return {
        "with_gap": with_gap,
        "ceiling_y": GEO["layer"] + 3,
        "ceiling_z": [z0 - 9, ceiling_to],
        "ceiling": ceiling,
        "shapes": shapes,
        "front_face_z": front,
        "contact_index": contact,
        "max_feet_y": round(max_feet, 6),
        "max_z": round(max_z, 6),
        "stepped": stepped,
        "rows": compact(rows),
    }


def build_tap_geometry(
    client: ModClient, lane: int, geometry: str, start_z: float
) -> list[dict[str, Any]]:
    z_from = int(start_z) - 3
    z_to = int(start_z) + 36
    prepare_lane(client, lane, start_z)
    clear_lane(client, lane, z_from - 2, z_to + 2)
    shapes: list[dict[str, Any]] = []
    if geometry == "ceiling_solid":
        shapes = fill_ceiling(client, lane, z_from, z_to, GEO["layer"] + 3)
        for z in (z_from, z_to):
            assert_air(client, (lane, GEO["layer"] + 2, z))
    elif geometry == "ceiling_edges":
        for start in range(z_from, z_to, 3):
            shapes.extend(fill_ceiling(client, lane, start, start + 1, GEO["layer"] + 3))
        assert_air(client, (lane, GEO["layer"] + 3, z_from + 2))
    elif geometry == "stairs":
        z_positions: list[int] = []
        for i in range(5):
            z = GEO["obstacle_z"] + 4 * i
            z_positions.extend((z, z + 1))
        shapes = place_row(client, lane, z_positions, FINE_CASES[0])
    elif geometry == "combo":
        for i in range(4):
            z = GEO["obstacle_z"] + 6 * i
            shapes.extend(place_row(client, lane, [z, z + 1], FINE_CASES[0]))
            shapes.extend(fill_ceiling(client, lane, z + 3, z + 4, GEO["layer"] + 3))
            assert_air(client, (lane, GEO["layer"] + 2, z + 2))
            assert_air(client, (lane, GEO["layer"] + 3, z + 5))
    else:
        raise AssertionError(f"unknown tap geometry {geometry}")
    return shapes


def tap_probe(
    client: ModClient,
    label: str,
    *,
    lane_key: str,
    geometry: str,
    hold: int | None = 1,
    release: int = 1,
    total_ticks: int = 40,
    repeats: int = 1,
) -> dict[str, Any]:
    """Tap-jump run with a long-held movement state and Space key edges."""
    lane = GEO["lanes"][lane_key]
    shapes = build_tap_geometry(client, lane, geometry, GEO["start_z"])
    runs: list[dict[str, Any]] = []
    for index in range(repeats):
        start_tick = pose(client, lane, GEO["start_z"])
        with RecordSession(client, period=1, watch=WATCH) as rec:
            if hold is None:
                client.set_input(forward=1.0, sprint=True, jump=True, ticks=total_ticks)
                client.wait_ticks(total_ticks)
            else:
                client.set_input(forward=1.0, sprint=True, ticks=total_ticks + 6)
                tick = 0
                while tick < total_ticks:
                    client.key_event(KEY_SPACE, True)
                    client.wait_ticks(hold)
                    tick += hold
                    client.key_event(KEY_SPACE, False)
                    rest = min(release, total_ticks - tick)
                    if rest > 0:
                        client.wait_ticks(rest)
                        tick += rest
            client.set_input()
            client.wait_ticks(4)
        rows = window(rec, start_tick, total_ticks + 5)
        assert len(rows) >= total_ticks, f"{label} run {index}: only {len(rows)} rows"
        assert_watch(rows, label)
        impulses = [i for i, row in enumerate(rows) if float(row["mpd_y"]) >= 0.40]
        assert impulses, f"{label} run {index}: tap pattern produced no impulse"
        if geometry in ("ceiling_solid", "ceiling_edges"):
            assert max(float(row["feet_y"]) for row in rows) <= GEO["ground"] + 0.85, (
                f"{label} run {index}: escaped the low ceiling"
            )
        sprint = [int(bool(row["sprint"])) for row in rows[:total_ticks]]
        sprint_drops = [i for i, value in enumerate(sprint) if not value]
        runs.append(
            {
                "impulse_indices": impulses,
                "impulse_gaps": [b - a for a, b in itertools.pairwise(impulses)],
                "impulse_count": len(impulses),
                "max_mpd_z": round(max(float(row["mpd_z"]) for row in rows), 6),
                "max_vz": round(max(float(row["vz"]) for row in rows), 6),
                "max_feet_y": round(max(float(row["feet_y"]) for row in rows), 6),
                "max_z": round(max(float(row["z"]) for row in rows), 6),
                "sprint_drops": sprint_drops,
                "on_ground_series": [int(bool(row["on_ground"])) for row in rows],
                "rows": compact(rows),
            }
        )
    gaps = [gap for run in runs for gap in run["impulse_gaps"]]
    return {
        "label": label,
        "lane": lane,
        "geometry": geometry,
        "hold": hold,
        "release": release,
        "repeats": repeats,
        "shapes": shapes[:6],
        "impulse_indices": runs[-1]["impulse_indices"],
        "impulse_gaps": gaps,
        "min_impulse_gap": min(gaps) if gaps else None,
        "max_impulse_gap": max(gaps) if gaps else None,
        "impulse_count": sum(run["impulse_count"] for run in runs),
        "max_mpd_z": max(run["max_mpd_z"] for run in runs),
        "max_vz": max(run["max_vz"] for run in runs),
        "max_feet_y": max(run["max_feet_y"] for run in runs),
        "max_z": max(run["max_z"] for run in runs),
        "sprint_drops": runs[-1]["sprint_drops"],
        "runs": runs,
    }


def three_tick_probe(client: ModClient, result: dict[str, Any]) -> dict[str, Any]:
    """Pick the fastest 3-tick pair and assert the landing sequence row by row."""
    assert result["min_impulse_gap"] == 3, (
        f"three tick: fastest tap gap is {result['min_impulse_gap']}, not the 3-tick floor"
    )
    ground = GEO["ground"]
    chosen: dict[str, Any] | None = None
    for run in result["runs"]:
        for a, b in itertools.pairwise(run["impulse_indices"]):
            if b - a == 3:
                rows = run["rows"]
                impulse, rise, fall, landing = rows[a], rows[a + 1], rows[a + 2], rows[a + 3]
                assert abs(float(impulse["mpd_y"]) - 0.42) <= 1e-6
                assert bool(impulse["on_ground"])
                assert abs((float(rise["feet_y"]) - ground) - 0.2) <= 0.02, (
                    f"three tick: rise row {rise['feet_y']} is not +0.2"
                )
                assert not bool(rise["on_ground"])
                assert abs((float(fall["feet_y"]) - ground) - 0.1216) <= 0.02, (
                    f"three tick: first fall row {fall['feet_y']} is not +0.1216"
                )
                assert not bool(fall["on_ground"])
                assert abs(float(landing["feet_y"]) - ground) <= 0.02, (
                    f"three tick: landing row {landing['feet_y']} is not back on the ground"
                )
                assert bool(landing["on_ground"])
                assert abs(float(landing["mpd_y"]) - 0.42) <= 1e-6, (
                    "three tick: landing row did not re-trigger the impulse"
                )
                chosen = {
                    "impulse_index": a,
                    "gap": b - a,
                    "impulse": impulse,
                    "rise": rise,
                    "fall": fall,
                    "landing": landing,
                }
                break
        if chosen is not None:
            break
    assert chosen is not None, "three tick: no adjacent 3-tick impulse pair found"
    # The two fall ticks must add up to the +0.2 rise.
    rise_dy = float(chosen["rise"]["feet_y"]) - ground
    fall1 = float(chosen["fall"]["feet_y"]) - ground
    assert abs((rise_dy - fall1) - 0.0784) <= 0.005, "three tick: first fall is not -0.0784"
    assert abs(fall1 - 0.1216) <= 0.02, "three tick: residual fall is not 0.1216"
    return chosen


def sneak_probe(client: ModClient, case: dict[str, Any]) -> dict[str, Any]:
    lane = GEO["lanes"]["sneak"]
    z0 = GEO["obstacle_z"]
    prepare_lane(client, lane, z0 + 1)
    clear_lane(client, lane, z0 - 7, z0 + 9)
    shapes = place_row(client, lane, [z0, z0 + 1, z0 + 2], case)
    front = z0 - PLAYER_HALF
    start_tick = pose(client, lane, front - 1.2)
    with RecordSession(client, period=1, watch=WATCH) as rec:
        client.set_input(forward=1.0, sneak=True, ticks=-1)
        client.wait_ticks(4)
        held = dict(client.state())
        assert held["input"]["sneak"] or held.get("sneaking"), (
            f"sneak {case['label']}: sneak input did not stick: {held.get('input')}"
        )
        crouch = {
            "head_offset": float(held["head_y"]) - float(held["feet_y"]),
            "sneaking": held.get("sneaking"),
            "input_sneak": held["input"]["sneak"],
        }
        client.wait_ticks(64)
        client.set_input()
        client.wait_ticks(4)
    rows = window(rec, start_tick, 73)
    assert len(rows) >= 60, f"sneak {case['label']}: only {len(rows)} rows"
    assert_watch(rows, f"sneak {case['label']}")
    contact = next((i for i, row in enumerate(rows) if float(row["z"]) >= front - 0.05), None)
    assert contact is not None, f"sneak {case['label']}: never reached the front face"
    assert max(float(row["mpd_y"]) for row in rows) < 0.30, (
        f"sneak {case['label']}: jump impulse appeared (sneak-only fixture)"
    )
    max_feet = max(float(row["feet_y"]) for row in rows)
    max_z = max(float(row["z"]) for row in rows)
    stepped = max_feet > GEO["ground"] + 0.45
    assert stepped is (case["expect"] == "step"), (
        f"sneak {case['label']}: stepped={stepped}, max_feet_y={max_feet:.6f}"
    )
    return {
        "label": case["label"],
        "top": case["top"],
        "expected": case["expect"],
        "crouch": crouch,
        "shapes": shapes,
        "front_face_z": front,
        "contact_index": contact,
        "max_feet_y": round(max_feet, 6),
        "max_z": round(max_z, 6),
        "stepped": stepped,
        "rows": compact(rows),
    }


def flight_probe(client: ModClient, case: dict[str, Any] | None) -> dict[str, Any]:
    """Creative flight at ground level, optional 0.5625 step in the lane."""
    lane = GEO["lanes"]["flight"]
    z0 = GEO["obstacle_z"]
    prepare_lane(client, lane, z0 + 1)
    clear_lane(client, lane, z0 - 7, z0 + 9)
    shapes: list[dict[str, Any]] = []
    front = z0 - PLAYER_HALF
    if case is not None:
        shapes = place_row(client, lane, [z0, z0 + 1, z0 + 2], case)
    start_z = front - 1.6
    pose(client, lane, start_z)
    cmd(client, "gamemode creative @s", 4)
    reply = client.toggle_fly(on=True)
    assert reply.get("mayfly"), f"flight: mayfly not granted: {reply}"
    client.wait_ticks(4)
    flown = double_space_fly(client, attempts=4)
    assert flown.get("flying"), f"flight: double-space did not engage flight: {flown}"
    client.teleport(lane + 0.5, GEO["ground"], start_z, yaw=0.0, pitch=0.0)
    client.wait_ticks(4)
    before = dict(client.state())
    assert before.get("flying"), "flight: teleport dropped the flying flag"
    start_tick = int(before["tick"])
    with RecordSession(client, period=1, watch=WATCH) as rec:
        client.set_input(forward=1.0, ticks=40)
        client.wait_ticks(42)
        mid = dict(client.state())
        client.set_input()
        client.wait_ticks(4)
    rows = window(rec, start_tick, 45)
    assert len(rows) >= 36, f"flight {case and case['label']}: only {len(rows)} rows"
    assert_watch(rows, "flight")
    max_feet = max(float(row["feet_y"]) for row in rows)
    max_z = max(float(row["z"]) for row in rows)
    contact = next((i for i, row in enumerate(rows) if float(row["z"]) >= front - 0.05), None)
    if case is not None:
        assert contact is not None, f"flight {case['label']}: never reached the step front face"
        stepped = max_feet > GEO["ground"] + 0.45
    else:
        stepped = None
    client.set_input()
    off = client.toggle_fly(on=False)
    client.wait_ticks(4)
    cmd(client, "gamemode survival @s", 4)
    settle(client, on_ground=True, input_sprint=False)
    return {
        "label": case["label"] if case else "flight_control",
        "shapes": shapes,
        "flying_before": before.get("flying"),
        "flying_mid": mid.get("flying"),
        "mayfly": before.get("mayfly"),
        "front_face_z": None if case is None else front,
        "contact_index": contact,
        "max_feet_y": round(max_feet, 6),
        "max_z": round(max_z, 6),
        "stepped": stepped,
        "flight_off": off,
        "rows": compact(rows),
    }


def main() -> int:
    with ModClient(timeout=60.0).connect() as client:
        client.wait_ready(timeout=60.0, require_world=True)
        build_arena(client)

        with LabSession(client):
            try:
                for case in FINE_CASES:
                    RESULTS[f"fine_{case['label']}_walk"] = walk_probe(client, case)
                RESULTS["fine_top0.59375_sprint"] = walk_probe(client, FINE_CASES[2], sprint=True)

                RESULTS["low_roof_no_gap"] = low_roof_probe(client, with_gap=False)
                RESULTS["low_roof_with_gap"] = low_roof_probe(client, with_gap=True)

                RESULTS["tap_three_tick"] = tap_probe(
                    client,
                    "tap_three_tick",
                    lane_key="tap",
                    geometry="ceiling_solid",
                    hold=1,
                    release=1,
                    total_ticks=40,
                    repeats=3,
                )
                RESULTS["tap_three_tick"]["fastest_pair"] = three_tick_probe(
                    client, RESULTS["tap_three_tick"]
                )

                RESULTS["tap_edges_control"] = tap_probe(
                    client,
                    "tap_edges_control",
                    lane_key="tap",
                    geometry="ceiling_edges",
                    hold=1,
                    release=2,
                )
                RESULTS["tap_stairs_control"] = tap_probe(
                    client,
                    "tap_stairs_control",
                    lane_key="tap",
                    geometry="stairs",
                    hold=1,
                    release=2,
                )
                RESULTS["tap_combo"] = tap_probe(
                    client,
                    "tap_combo",
                    lane_key="combo",
                    geometry="combo",
                    hold=1,
                    release=2,
                    total_ticks=90,
                )

                RESULTS["sneak_top0.5625"] = sneak_probe(client, FINE_CASES[1])
                RESULTS["sneak_top0.625"] = sneak_probe(client, FINE_CASES[3])

                RESULTS["flight_control"] = flight_probe(client, None)
                RESULTS["flight_top0.5625"] = flight_probe(client, FINE_CASES[1])
            except Exception:
                OUT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"partial results saved: {OUT}")
                raise
            finally:
                client.set_input()
                client.command("effect @s clear")
                client.command("gamemode survival @s")
                client.command("gamerule falldamage true")
                for lane in GEO["lanes"].values():
                    prepare_lane(client, lane, GEO["start_z"] + 4)
                    cmd(
                        client,
                        f"fill {lane - 3} {GEO['layer'] + 1} {GEO['obstacle_z'] - 12} "
                        f"{lane + 3} {GEO['layer'] + 6} {GEO['obstacle_z'] + 36} air",
                        3,
                    )
                client.teleport(*ORIGIN, yaw=0.0, pitch=0.0)
                client.wait_ticks(6)

    OUT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in RESULTS.items() if k != "setup"}, ensure_ascii=False)[:2000])
    print(f"Saved: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
