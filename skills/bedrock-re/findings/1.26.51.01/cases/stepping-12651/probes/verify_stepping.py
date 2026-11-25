"""stepping-12651 round 2: step-up, step-assist jump cancel, solid blip and tap-grind.

All geometry is verified by reading real collision AABBs back from the game; every
fixture asserts its block states, contact/crossing, landing tick alignment and
mPosDelta watch decode before any number is published. Raw rows go to
out/stepping-1.26.51.01.json.
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
from driver.harness.lab import KEY_SPACE, LabSession, RecordSession, settle  # noqa: E402

OUT = ROOT / "out" / "stepping-1.26.51.01.json"
STATE_VECTOR = 0x1B5D5238
PLAYER_HALF = 0.3

WATCH = [
    {"component_id": STATE_VECTOR, "offset": 0x18, "size": 4, "name": "mpd_x", "fmt": "f32"},
    {"component_id": STATE_VECTOR, "offset": 0x1C, "size": 4, "name": "mpd_y", "fmt": "f32"},
    {"component_id": STATE_VECTOR, "offset": 0x20, "size": 4, "name": "mpd_z", "fmt": "f32"},
]

# Full-width single-box heights from the 1.26.51.01 collision-shape dump.
STEP_CASES = [
    {
        "label": "h0.500",
        "block": "snow_layer",
        "states": '["height"=4]',
        "state_key": "height",
        "state_value": 4,
        "height": 0.5,
        "expect": "step",
    },
    {
        "label": "h0.5625",
        "block": "stonecutter_block",
        "states": "",
        "state_key": None,
        "state_value": None,
        "height": 0.5625,
        "expect": "step",
    },
    {
        "label": "h0.625",
        "block": "snow_layer",
        "states": '["height"=5]',
        "state_key": "height",
        "state_value": 5,
        "height": 0.625,
        "expect": "block",
    },
]

RESULTS: dict[str, Any] = {}
GEO: dict[str, Any] = {}


def cmd(client: ModClient, text: str, ticks: int = 4) -> None:
    client.command(text)
    client.wait_ticks(ticks)


def shape_of(client: ModClient, pos: tuple[int, int, int]) -> dict[str, Any]:
    rows = client.block_shapes([pos], entity_mode="none").get("blocks", [])
    assert rows, f"block_shapes returned no row for {pos}"
    return rows[0]


def assert_shape(
    client: ModClient,
    pos: tuple[int, int, int],
    *,
    name_contains: str,
    height: float | None = None,
    state_key: str | None = None,
    state_value: Any = None,
) -> dict[str, Any]:
    row = shape_of(client, pos)
    name = str(row.get("name", ""))
    assert name_contains in name, f"{pos}: expected {name_contains}, got {name}"
    collision = row.get("collision", {})
    assert not collision.get("empty", True), f"{pos}: empty collision box: {row}"
    got_height = float(collision["max"][1]) - float(collision["min"][1])
    if height is not None:
        assert abs(got_height - height) <= 1e-4, (
            f"{pos}: collision height {got_height} != {height}: {row}"
        )
    if state_key is not None:
        states = row.get("states", {})
        assert states.get(state_key) == state_value, (
            f"{pos}: state {state_key}={states.get(state_key)!r} != {state_value!r}: {row}"
        )
    return {
        "pos": list(pos),
        "name": name,
        "states": row.get("states", {}),
        "height": round(got_height, 6),
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
        "mpd_x",
        "mpd_y",
        "mpd_z",
    )
    return [{key: row.get(key) for key in keys} for row in rows]


def build_arena(client: ModClient, feet: tuple[float, float, float]) -> None:
    """Refill the natural superflat ground layer with stone and clear the air above."""
    px, pz = int(feet[0]), int(feet[2])
    feet_int = round(feet[1])
    layer = feet_int - 1
    ground = float(feet_int)
    x0, x1 = px - 8, px + 52
    z0, z1 = pz - 8, pz + 67
    original = str(
        client.blocks([(px, layer, pz)]).get("blocks", [{}])[0].get("name", "minecraft:stone")
    )

    cmd(client, "gamerule falldamage false", 2)
    cmd(client, "gamemode survival @s", 2)
    cmd(client, "effect @s clear", 2)
    for seg_x0, seg_x1 in ((x0, x0 + 20), (x0 + 20, x0 + 40), (x0 + 40, x1)):
        client.teleport((seg_x0 + seg_x1) / 2 + 0.5, ground, pz + 2.5, yaw=0.0, pitch=0.0)
        client.wait_ticks(8)
        cmd(client, f"fill {seg_x0} {layer} {z0} {seg_x1} {layer} {z1} stone", 4)
        cmd(client, f"fill {seg_x0} {layer + 1} {z0} {seg_x1} {layer + 8} {z1} air", 4)
    checks = [
        (x0 + 2, layer, pz + 2),
        (x1 - 2, layer, pz + 60),
        (px + 4, layer + 1, pz + 14),
    ]
    names = [str(row.get("name", "")) for row in client.blocks(checks).get("blocks", [])]
    assert len(names) == 3 and all("stone" in name for name in names[:2]), names
    assert "air" in names[2], names

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
            "lanes": {
                "step": px + 4,
                "sprint": px + 12,
                "blip": px + 20,
                "jump": px + 28,
                "grind": px + 36,
                "ceiling": px + 44,
            },
        }
    )
    RESULTS["setup"] = {
        "feet": list(feet),
        "layer": layer,
        "ground": ground,
        "platform": {"x": [x0, x1], "z": [z0, z1]},
        "original_block": original,
        "lanes": GEO["lanes"],
        "obstacle_z": GEO["obstacle_z"],
        "start_z": GEO["start_z"],
    }


def clear_lane(client: ModClient, lane: int, z_from: int, z_to: int) -> None:
    cmd(
        client,
        f"fill {lane - 2} {GEO['layer'] + 1} {z_from} {lane + 2} {GEO['layer'] + 5} {z_to} air",
        3,
    )


def fill_ceiling(
    client: ModClient, lane: int, z_from: int, z_to: int, y: int
) -> list[dict[str, Any]]:
    cmd(client, f"fill {lane - 1} {y} {z_from} {lane + 1} {y} {z_to} stone", 3)
    return [assert_shape(client, (lane, y, z), name_contains="stone") for z in (z_from, z_to)]


def place_steps_at(
    client: ModClient,
    lane: int,
    z_positions: list[int],
    case: dict[str, Any],
) -> list[dict[str, Any]]:
    shapes = []
    for z in z_positions:
        cmd(
            client,
            f"setblock {lane} {GEO['layer'] + 1} {z} {case['block']} {case['states']}".strip(),
            2,
        )
        shapes.append(
            assert_shape(
                client,
                (lane, GEO["layer"] + 1, z),
                name_contains=case["block"],
                height=case["height"],
                state_key=case["state_key"],
                state_value=case["state_value"],
            )
        )
    return shapes


def place_step(client: ModClient, lane: int, case: dict[str, Any], z0: int) -> list[dict[str, Any]]:
    clear_lane(client, lane, z0 - 3, z0 + 5)
    return place_steps_at(client, lane, [z0, z0 + 1, z0 + 2], case)


def pose(client: ModClient, lane: int, z: float) -> int:
    client.teleport(lane + 0.5, GEO["ground"], float(z), yaw=0.0, pitch=0.0)
    client.wait_ticks(6)
    settled = settle(client, on_ground=True, input_sprint=False)
    assert settled["ok"], f"pose z={z}: settle failed: {settled}"
    return int(client.state()["tick"])


def window(rec: RecordSession, start_tick: int, size: int) -> list[dict[str, Any]]:
    return [row for row in rec.rows if int(row["tick"]) - start_tick <= size]


def step_probe(client: ModClient, case: dict[str, Any], *, sprint: bool) -> dict[str, Any]:
    """Reused from round 1 (r01-005 / r01-007): plain walk into a full-width step."""
    lane = GEO["lanes"]["sprint" if sprint else "step"]
    z0 = GEO["obstacle_z"]
    shapes = place_step(client, lane, case, z0)
    start_tick = pose(client, lane, GEO["start_z"])
    with RecordSession(client, period=1, watch=WATCH) as rec:
        client.set_input(forward=1.0, sprint=sprint, ticks=52)
        client.wait_ticks(53)
        client.set_input()
        client.wait_ticks(4)
    rows = window(rec, start_tick, 57)
    assert len(rows) >= 40, f"{case['label']} sprint={sprint}: only {len(rows)} rows"
    assert_watch(rows, f"{case['label']} sprint={sprint}")
    front = z0 - PLAYER_HALF
    contact = next((i for i, row in enumerate(rows) if float(row["z"]) >= front - 0.05), None)
    assert contact is not None, (
        f"{case['label']} sprint={sprint}: never reached the step "
        f"(max z={max(float(row['z']) for row in rows):.3f})"
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
        assert abs(max_feet - (GEO["ground"] + case["height"])) <= 0.03, (
            f"{case['label']} sprint={sprint}: no clean step-up "
            f"(max feet_y={max_feet:.5f}, expected={GEO['ground'] + case['height']:.5f})"
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
        "height": case["height"],
        "shapes": shapes,
        "max_feet_y": round(max_feet, 6),
        "max_z": round(max_z, 6),
        "front_face_z": front,
        "step_index": step_index,
        "step_tick": None if step_index is None else rows[step_index]["tick"],
        "mpd_y_at_step": None if step_index is None else rows[step_index]["mpd_y"],
        "mpd_z_at_step": None if step_index is None else rows[step_index]["mpd_z"],
        "sprint_series": [int(bool(row["sprint"])) for row in rows],
        "rows": compact(rows),
    }


def blip_drop_probe(client: ModClient, *, with_step: bool) -> dict[str, Any]:
    """Free fall from 1.63 blocks: lands first, then normal step-up (round 1 control)."""
    lane = GEO["lanes"]["blip"]
    z0 = GEO["obstacle_z"]
    clear_lane(client, lane, z0 - 5, z0 + 5)
    shape = None
    if with_step:
        cmd(client, f"setblock {lane} {GEO['layer'] + 1} {z0} stonecutter_block", 3)
        shape = assert_shape(
            client, (lane, GEO["layer"] + 1, z0), name_contains="stonecutter", height=0.5625
        )
    else:
        assert_air(client, (lane, GEO["layer"] + 1, z0))
    client.teleport(lane + 0.5, GEO["ground"] + 1.63, z0 - 1.3, yaw=0.0, pitch=0.0)
    client.wait_ticks(2)
    state = dict(client.state())
    assert float(state["feet_y"]) > GEO["ground"] + 0.5, "blip drop: teleport did not leave ground"
    start_tick = int(state["tick"])
    with RecordSession(client, period=1, watch=WATCH) as rec:
        client.set_input(forward=1.0, sprint=True, ticks=14)
        client.wait_ticks(15)
        client.set_input()
        client.wait_ticks(3)
    rows = window(rec, start_tick, 18)
    assert len(rows) >= 10 and all(row.get("mpd_y") is not None for row in rows), (
        "blip drop: short/incomplete recording"
    )
    contact = next(
        (i for i, row in enumerate(rows) if float(row["z"]) >= z0 - PLAYER_HALF - 0.02), None
    )
    assert contact is not None, "blip drop: never reached the step edge"
    after = rows[contact:]
    return {
        "with_step": with_step,
        "shape": shape,
        "contact_index": contact,
        "max_feet_y_after": round(max(float(row["feet_y"]) for row in after), 6),
        "max_vy_after": round(max(float(row["vy"]) for row in after), 6),
        "step_top": GEO["ground"] + 0.5625,
        "rows": compact(rows),
    }


def run_jump_arc(
    client: ModClient,
    lane: int,
    *,
    start_z: float,
    with_step: bool,
    z0: int,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Sprint-jump from ground; optionally place one 0.5625 step at z0."""
    clear_lane(client, lane, z0 - 7, z0 + 8)
    shape = None
    if with_step:
        cmd(client, f"setblock {lane} {GEO['layer'] + 1} {z0} stonecutter_block", 3)
        shape = assert_shape(
            client, (lane, GEO["layer"] + 1, z0), name_contains="stonecutter", height=0.5625
        )
    else:
        assert_air(client, (lane, GEO["layer"] + 1, z0))
    start_tick = pose(client, lane, start_z)
    with RecordSession(client, period=1, watch=WATCH) as rec:
        client.set_input(forward=1.0, sprint=True, jump=True, ticks=18)
        client.wait_ticks(19)
        client.set_input()
        client.wait_ticks(3)
    rows = window(rec, start_tick, 23)
    assert len(rows) >= 20, f"jump arc: only {len(rows)} rows"
    assert_watch(rows, "jump arc")
    return rows, shape


def arc_landing_index(rows: list[dict[str, Any]]) -> int | None:
    for i, row in enumerate(rows):
        if i >= 3 and bool(row["on_ground"]) and float(row["feet_y"]) >= GEO["ground"] - 0.01:
            return i
    return None


def arc_contact_index(rows: list[dict[str, Any]], front: float) -> int | None:
    return next((i for i, row in enumerate(rows) if float(row["z"]) >= front - 0.02), None)


def solid_blip_aligned(client: ModClient) -> dict[str, Any]:
    """Align the FIRST horizontal step contact with the jump-arc landing tick.

    Round 1 hit the step side two ticks early (airborne, no landing gate) and
    wrongly concluded blips lose speed. Here a control arc fixes the landing z,
    then a fine z-offset scan places the step front face at that exact tick.
    """
    lane = GEO["lanes"]["blip"]
    z0 = int(GEO["start_z"]) + 4  # front face = z0 - 0.3
    front = z0 - PLAYER_HALF
    base_rows, _ = run_jump_arc(client, lane, start_z=GEO["start_z"], with_step=False, z0=z0)
    landing = arc_landing_index(base_rows)
    assert landing is not None, "aligned blip: control arc never landed"
    landing_z = float(base_rows[landing]["z"])
    needed = front + 0.03 - landing_z
    attempts: list[dict[str, Any]] = []
    chosen: tuple[float, list[dict[str, Any]], dict[str, Any] | None] | None = None
    for delta in (needed, needed + 0.02, needed - 0.02, needed + 0.05, needed + 0.10):
        offset = round(delta, 5)
        rows, shape = run_jump_arc(
            client, lane, start_z=GEO["start_z"] + offset, with_step=True, z0=z0
        )
        landing_i = arc_landing_index(rows)
        contact_i = arc_contact_index(rows, front)
        attempts.append(
            {
                "offset": offset,
                "landing_index": landing_i,
                "contact_index": contact_i,
                "contact_z": None if contact_i is None else round(float(rows[contact_i]["z"]), 6),
                "contact_feet_y": None
                if contact_i is None
                else round(float(rows[contact_i]["feet_y"]), 6),
            }
        )
        if landing_i is not None and contact_i == landing_i:
            chosen = (offset, rows, shape)
            break
    assert chosen is not None, f"aligned blip: no offset aligned contact with landing: {attempts}"
    offset, rows, shape = chosen
    landing_i = arc_landing_index(rows)
    contact_i = arc_contact_index(rows, front)
    assert landing_i is not None and contact_i == landing_i
    at = rows[contact_i]
    before = rows[contact_i - 1]
    assert not bool(before["on_ground"]) and float(before["feet_y"]) > GEO["ground"] + 0.02, (
        "aligned blip: the tick before contact was already grounded (fixture drift)"
    )
    assert float(at["z"]) <= front + 0.35, "aligned blip: tunnelled through the step block"
    control_rows, _ = run_jump_arc(
        client, lane, start_z=GEO["start_z"] + offset, with_step=False, z0=z0
    )
    control_landing = arc_landing_index(control_rows)
    assert control_landing is not None, "aligned blip control: never landed"
    ctl = control_rows[control_landing]
    return {
        "z0": z0,
        "front_face_z": front,
        "offset": offset,
        "attempts": attempts,
        "shape": shape,
        "landing_index": landing_i,
        "contact_index": contact_i,
        "contact_tick": at["tick"],
        "contact_feet_y": round(float(at["feet_y"]), 6),
        "contact_vy": round(float(at["vy"]), 6),
        "contact_vz": round(float(at["vz"]), 6),
        "contact_mpd_y": round(float(at["mpd_y"]), 6),
        "contact_mpd_z": round(float(at["mpd_z"]), 6),
        "contact_on_ground": bool(at["on_ground"]),
        "control_landing_vz": round(float(ctl["vz"]), 6),
        "control_landing_feet_y": round(float(ctl["feet_y"]), 6),
        "step_top": GEO["ground"] + 0.5625,
        "rows": compact(rows),
        "control_rows": compact(control_rows),
    }


def solid_blip_early(client: ModClient) -> dict[str, Any]:
    """Round-1 false-negative fixture: step side hit 2 ticks before landing."""
    lane = GEO["lanes"]["blip"]
    z0 = int(GEO["start_z"]) + 3
    rows, shape = run_jump_arc(client, lane, start_z=GEO["start_z"], with_step=True, z0=z0)
    front = z0 - PLAYER_HALF
    contact = arc_contact_index(rows, front)
    landing = arc_landing_index(rows)
    assert contact is not None and landing is not None, "early blip: no contact/landing"
    assert contact < landing, "early blip: expected contact BEFORE landing (round 1 fixture)"
    at = rows[contact]
    return {
        "front_face_z": front,
        "shape": shape,
        "contact_index": contact,
        "landing_index": landing,
        "ticks_early": landing - contact,
        "contact_feet_y": round(float(at["feet_y"]), 6),
        "contact_vz": round(float(at["vz"]), 6),
        "rows": compact(rows),
    }


def low_ceiling_step_probe(client: ModClient, *, high: bool) -> dict[str, Any]:
    """Ceiling box filter (0x149049826) + endpoint intersection (0x149049a8b).

    A 0.5 step under a ceiling 0.2 above the head must be REJECTED (final AABB
    would intersect); with a 1.2-clearance ceiling the same step is accepted.
    """
    lane = GEO["lanes"]["jump"]
    z0 = GEO["obstacle_z"]
    y = GEO["layer"] + (4 if high else 3)  # ground+3 (1.2 clearance) / ground+2 (0.2)
    clear_lane(client, lane, z0 - 8, z0 + 8)
    ceiling = fill_ceiling(client, lane, z0 - 6, z0 + 6, y)
    for z in (z0 - 2, z0, z0 + 2):
        assert_air(client, (lane, GEO["layer"] + 2, z))
    shapes = place_steps_at(client, lane, [z0, z0 + 1, z0 + 2], STEP_CASES[0])
    start_tick = pose(client, lane, GEO["start_z"])
    with RecordSession(client, period=1, watch=WATCH) as rec:
        client.set_input(forward=1.0, ticks=52)
        client.wait_ticks(53)
        client.set_input()
        client.wait_ticks(4)
    rows = window(rec, start_tick, 57)
    assert len(rows) >= 40, f"low ceiling step: only {len(rows)} rows"
    assert_watch(rows, "low ceiling step")
    front = z0 - PLAYER_HALF
    contact = next((i for i, row in enumerate(rows) if float(row["z"]) >= front - 0.05), None)
    assert contact is not None, "low ceiling step: never reached the step front face"
    assert max(float(row["mpd_y"]) for row in rows) < 0.30, (
        "low ceiling step: unexpected jump impulse (walk-only fixture)"
    )
    assert all(bool(row["on_ground"]) for row in rows[: contact + 2]), (
        "low ceiling step: left the ground before contact"
    )
    max_feet = max(float(row["feet_y"]) for row in rows)
    max_z = max(float(row["z"]) for row in rows)
    stepped = max_feet > GEO["ground"] + 0.45
    assert stepped is high, (
        f"low ceiling step high={high}: stepped={stepped}, max_feet_y={max_feet:.6f}"
    )
    return {
        "high": high,
        "ceiling_y": y,
        "head_clearance": 1.2 if high else 0.2,
        "ceiling": ceiling,
        "shapes": shapes,
        "front_face_z": front,
        "contact_index": contact,
        "max_feet_y": round(max_feet, 6),
        "max_z": round(max_z, 6),
        "stepped": stepped,
        "rows": compact(rows),
    }


def step_assist_jump_cancel(client: ModClient, case: dict[str, Any]) -> dict[str, Any]:
    """Jump with the AABB already touching a 0.42 < h <= 0.5625 step front face.

    The first jump tick keeps the tick-start OnGroundFlagComponent, so
    AutoStepFilterSystem admits and AutoStepSystem eats the +0.42 while lifting
    the player onto the step (Step-Assist Jump Cancel), then we re-tap jump.
    """
    lane = GEO["lanes"]["ceiling"]
    z0 = GEO["obstacle_z"]
    front = z0 - PLAYER_HALF
    clear_lane(client, lane, z0 - 4, z0 + 6)
    shapes = place_steps_at(client, lane, [z0, z0 + 1, z0 + 2], case)
    start_z = front - 0.1
    start_tick = pose(client, lane, start_z)
    with RecordSession(client, period=1, watch=WATCH) as rec:
        client.set_input(forward=1.0, sprint=True, jump=True, ticks=1)
        client.wait_ticks(1)
        client.set_input(forward=1.0, sprint=True, ticks=1)
        client.wait_ticks(1)
        client.set_input(forward=1.0, sprint=True, jump=True, ticks=1)
        client.wait_ticks(1)
        client.set_input(forward=1.0, sprint=True, ticks=8)
        client.wait_ticks(9)
        client.set_input()
        client.wait_ticks(3)
    rows = window(rec, start_tick, 15)
    assert len(rows) >= 12, f"step jump cancel: only {len(rows)} rows"
    assert_watch(rows, "step jump cancel")
    impulses = [i for i, row in enumerate(rows) if float(row["mpd_y"]) >= 0.40]
    assert impulses, "step jump cancel: no +0.42 jump impulse"
    first = impulses[0]
    # The +0.42 impulse is written on the last grounded row; the horizontal
    # crossing / step-assist outcome shows up on the NEXT tick (takeoff row).
    takeoff_index = first + 1
    assert takeoff_index < len(rows), "step jump cancel: no takeoff row after impulse"
    at = rows[takeoff_index]
    assert float(at["z"]) >= front - 0.02, (
        f"step jump cancel: takeoff tick never crossed the front face "
        f"(z={at['z']:.6f}, front={front})"
    )
    assert float(at["z"]) <= front + 0.45, "step jump cancel: tunnelled through the step"
    assert float(at["feet_y"]) <= GEO["ground"] + case["height"] + 0.05, (
        "step jump cancel: player above the step top on takeoff tick"
    )
    return {
        "label": case["label"],
        "height": case["height"],
        "shapes": shapes,
        "start_z": start_z,
        "front_face_z": front,
        "step_top": GEO["ground"] + case["height"],
        "impulse_index": first,
        "takeoff_index": takeoff_index,
        "takeoff_z": round(float(at["z"]), 6),
        "takeoff_feet_y": round(float(at["feet_y"]), 6),
        "takeoff_vy": round(float(at["vy"]), 6),
        "takeoff_vz": round(float(at["vz"]), 6),
        "takeoff_mpd_y": round(float(at["mpd_y"]), 6),
        "takeoff_mpd_z": round(float(at["mpd_z"]), 6),
        "takeoff_on_ground": bool(at["on_ground"]),
        "impulse_indices": impulses,
        "retrigger_on_next_tap": len(impulses) >= 2,
        "max_feet_y": round(max(float(row["feet_y"]) for row in rows), 6),
        "max_vy": round(max(float(row["vy"]) for row in rows), 6),
        "rows": compact(rows),
    }


def ceiling_probe(
    client: ModClient,
    label: str,
    *,
    lane_key: str,
    z_from: int,
    z_to: int,
    jump_ticks: int,
    forward: float,
    sprint: bool,
    total_ticks: int,
    clip_assert: bool = True,
) -> dict[str, Any]:
    """Held-jump run under a 0.2-clearance ceiling (round 1 baseline)."""
    lane = GEO["lanes"][lane_key]
    clear_lane(client, lane, z_from - 3, z_to + 3)
    shapes = fill_ceiling(client, lane, z_from, z_to, GEO["layer"] + 3)
    for z in (z_from, z_to):
        assert_air(client, (lane, GEO["layer"] + 2, z))
    start_tick = pose(client, lane, GEO["start_z"])
    with RecordSession(client, period=1, watch=WATCH) as rec:
        client.set_input(forward=forward, sprint=sprint, jump=True, ticks=jump_ticks)
        client.wait_ticks(total_ticks)
        client.set_input()
        client.wait_ticks(4)
    rows = window(rec, start_tick, total_ticks + 4)
    assert len(rows) >= total_ticks, f"{label}: only {len(rows)} rows"
    assert_watch(rows, label)
    if clip_assert:
        assert max(float(row["feet_y"]) for row in rows) <= GEO["ground"] + 0.25, (
            f"{label}: escaped the ceiling (max feet_y={max(float(r['feet_y']) for r in rows):.5f})"
        )
    assert max(float(row["mpd_y"]) for row in rows) >= 0.40, (
        f"{label}: jump impulse never reached mPosDelta.y"
    )
    impulses = [i for i, row in enumerate(rows) if float(row["mpd_y"]) >= 0.40]
    return {
        "label": label,
        "lane": lane,
        "ceiling_y": GEO["layer"] + 3,
        "z_range": [z_from, z_to],
        "shapes": shapes,
        "jump_ticks": jump_ticks,
        "impulse_indices": impulses,
        "impulse_gaps": [b - a for a, b in itertools.pairwise(impulses)],
        "max_mpd_y": round(max(float(row["mpd_y"]) for row in rows), 6),
        "max_mpd_z": round(max(float(row["mpd_z"]) for row in rows), 6),
        "max_vz": round(max(float(row["vz"]) for row in rows), 6),
        "max_feet_y": round(max(float(row["feet_y"]) for row in rows), 6),
        "sprint_series": [int(bool(row["sprint"])) for row in rows],
        "on_ground_series": [int(bool(row["on_ground"])) for row in rows],
        "rows": compact(rows),
    }


def tap_jump_probe(
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
    """Tap-jump Grind: `hold` ticks pressed, `release` ticks released, repeated.

    Round 1 only held the key down (jump_ticks=24) and measured the engine's
    10-tick auto-repeat. Movement is held with ONE long `set_input` (a per-tick
    set loop drops ticks when the pipe round trip slips, which silently clears
    sprint) and Space is tapped with key edges, which merge on top of the active
    movement state. The press edge lands on a tick with about +/-1 tick of
    injection jitter, so the headline pattern is measured `repeats` times and
    reported as an aggregate; `hold=None` is the never-release baseline.
    """
    lane = GEO["lanes"][lane_key]
    z_from = int(GEO["start_z"]) - 3
    z_to = int(GEO["start_z"]) + 34
    clear_lane(client, lane, z_from, z_to)
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
        shapes = place_steps_at(client, lane, z_positions, STEP_CASES[0])
    else:
        raise AssertionError(f"unknown tap geometry {geometry}")

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
        assert len(rows) >= total_ticks, f"{label}: only {len(rows)} rows"
        assert_watch(rows, label)
        impulses = [i for i, row in enumerate(rows) if float(row["mpd_y"]) >= 0.40]
        assert len(impulses) >= 2, (
            f"{label} run {index}: tap pattern produced only {len(impulses)} impulse(s)"
        )
        if geometry in ("ceiling_solid", "ceiling_edges"):
            assert max(float(row["feet_y"]) for row in rows) <= GEO["ground"] + 0.85, (
                f"{label} run {index}: escaped the low ceiling"
            )
        sprint = [int(bool(row["sprint"])) for row in rows[:total_ticks]]
        sprint_drops = [i for i, value in enumerate(sprint) if not value]
        if geometry != "stairs":
            assert len(sprint_drops) <= 2, f"{label} run {index}: sprint dropped at {sprint_drops}"
        runs.append(
            {
                "impulse_indices": impulses,
                "impulse_gaps": [b - a for a, b in itertools.pairwise(impulses)],
                "impulse_count": len(impulses),
                "max_mpd_z": round(max(float(row["mpd_z"]) for row in rows), 6),
                "max_vz": round(max(float(row["vz"]) for row in rows), 6),
                "max_feet_y": round(max(float(row["feet_y"]) for row in rows), 6),
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
        "shapes": shapes[:4],
        "impulse_indices": runs[-1]["impulse_indices"],
        "impulse_gaps": gaps,
        "min_impulse_gap": min(gaps) if gaps else None,
        "max_impulse_gap": max(gaps) if gaps else None,
        "impulse_count": sum(run["impulse_count"] for run in runs),
        "max_mpd_z": max(run["max_mpd_z"] for run in runs),
        "max_vz": max(run["max_vz"] for run in runs),
        "max_feet_y": max(run["max_feet_y"] for run in runs),
        "sprint_drops": runs[-1]["sprint_drops"],
        "runs": runs,
    }


def free_sprint_probe(client: ModClient) -> dict[str, Any]:
    lane = GEO["lanes"]["grind"]
    clear_lane(client, lane, GEO["obstacle_z"] - 5, GEO["obstacle_z"] + 30)
    start_tick = pose(client, lane, GEO["start_z"])
    with RecordSession(client, period=1, watch=WATCH) as rec:
        client.set_input(forward=1.0, sprint=True, ticks=24)
        client.wait_ticks(25)
        client.set_input()
        client.wait_ticks(3)
    rows = window(rec, start_tick, 28)
    assert len(rows) >= 24, f"free_sprint: only {len(rows)} rows"
    assert_watch(rows, "free_sprint")
    return {
        "label": "free_sprint",
        "max_mpd_z": round(max(float(row["mpd_z"]) for row in rows), 6),
        "max_vz": round(max(float(row["vz"]) for row in rows), 6),
        "rows": compact(rows),
    }


def main() -> int:
    with ModClient(timeout=60.0).connect() as client:
        client.wait_ready(timeout=60.0, require_world=True)
        state = dict(client.state())
        feet = (float(state["feet_x"]), float(state["feet_y"]), float(state["feet_z"]))
        build_arena(client, feet)

        with LabSession(client):
            try:
                for case in STEP_CASES:
                    RESULTS[f"step_{case['label']}_walk"] = step_probe(client, case, sprint=False)
                    RESULTS[f"step_{case['label']}_sprint"] = step_probe(client, case, sprint=True)

                RESULTS["blip_drop_control"] = blip_drop_probe(client, with_step=False)
                RESULTS["blip_drop_step"] = blip_drop_probe(client, with_step=True)
                RESULTS["solid_blip_early"] = solid_blip_early(client)
                RESULTS["solid_blip_aligned"] = solid_blip_aligned(client)

                RESULTS["low_ceiling_step_high"] = low_ceiling_step_probe(client, high=True)
                RESULTS["low_ceiling_step_low"] = low_ceiling_step_probe(client, high=False)

                RESULTS["step_jump_cancel_h0.500"] = step_assist_jump_cancel(client, STEP_CASES[0])
                RESULTS["step_jump_cancel_h0.5625"] = step_assist_jump_cancel(client, STEP_CASES[1])

                RESULTS["overhead_jump_cancel"] = ceiling_probe(
                    client,
                    "overhead_jump_cancel",
                    lane_key="jump",
                    z_from=int(GEO["start_z"]) - 3,
                    z_to=int(GEO["start_z"]) + 9,
                    jump_ticks=1,
                    forward=0.0,
                    sprint=False,
                    total_ticks=12,
                )
                RESULTS["free_sprint"] = free_sprint_probe(client)
                RESULTS["held_jump_grind"] = ceiling_probe(
                    client,
                    "held_jump_grind",
                    lane_key="grind",
                    z_from=int(GEO["start_z"]) - 3,
                    z_to=int(GEO["start_z"]) + 30,
                    jump_ticks=24,
                    forward=1.0,
                    sprint=True,
                    total_ticks=25,
                )
                for hold, release in ((1, 1), (1, 2), (1, 3), (1, 4), (1, 9)):
                    RESULTS[f"tap_grind_ceiling_h{hold}r{release}"] = tap_jump_probe(
                        client,
                        f"tap_grind_ceiling_h{hold}r{release}",
                        lane_key="grind",
                        geometry="ceiling_solid",
                        hold=hold,
                        release=release,
                        repeats=3 if (hold, release) == (1, 1) else 1,
                    )
                RESULTS["tap_grind_edges"] = tap_jump_probe(
                    client,
                    "tap_grind_edges",
                    lane_key="grind",
                    geometry="ceiling_edges",
                    hold=1,
                    release=2,
                )
                RESULTS["tap_grind_stairs"] = tap_jump_probe(
                    client,
                    "tap_grind_stairs",
                    lane_key="grind",
                    geometry="stairs",
                    hold=1,
                    release=2,
                )

                edge_z = int(GEO["start_z"]) + 1
                front = ceiling_probe(
                    client,
                    "ceiling_front_step_assist",
                    lane_key="ceiling",
                    z_from=edge_z,
                    z_to=int(GEO["start_z"]) + 22,
                    jump_ticks=18,
                    forward=1.0,
                    sprint=True,
                    total_ticks=19,
                    clip_assert=False,
                )
                rows = front["rows"]
                assert max(float(row["z"]) for row in rows) >= edge_z - PLAYER_HALF - 0.05, (
                    "ceiling_front_step_assist: never reached the ceiling front edge"
                )
                impulse = front["impulse_indices"][0]
                after = rows[impulse + 1]
                assert abs(float(after["feet_y"]) - GEO["ground"]) <= 0.02, (
                    "ceiling_front_step_assist: +0.42 was not eaten back to ground"
                )
                assert abs(float(after["vy"])) <= 0.02 and float(after["vz"]) >= 0.30, (
                    "ceiling_front_step_assist: vertical not cancelled / speed not kept"
                )
                assert float(after["mpd_y"]) < 0.30, (
                    "ceiling_front_step_assist: +0.42 lingered in mPosDelta.y"
                )
                RESULTS["ceiling_front_step_assist"] = front
            except Exception:
                OUT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"partial results saved: {OUT}")
                raise
            finally:
                client.set_input()
                client.command("effect @s clear")
                client.command("gamerule falldamage true")
                cmd(
                    client,
                    f"fill {GEO['x0'] - 1} {GEO['layer']} {GEO['z0'] - 1} "
                    f"{GEO['x1'] + 1} {GEO['layer'] + 8} {GEO['z1'] + 1} air",
                    3,
                )
                cmd(
                    client,
                    f"fill {GEO['x0'] - 1} {GEO['layer']} {GEO['z0'] - 1} "
                    f"{GEO['x1'] + 1} {GEO['layer']} {GEO['z1'] + 1} {GEO['original_block']}",
                    3,
                )
                client.teleport(*feet, yaw=float(state.get("yaw", 0.0)), pitch=0.0)
                client.wait_ticks(6)

    OUT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in RESULTS.items() if k != "setup"}, ensure_ascii=False)[:2000])
    print(f"Saved: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
