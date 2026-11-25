"""Live verification of the 1.26.51.01 sprint state machine and wall cancellation.

Rework v2: wide platform + long wall, hard contact assertions, critical-angle
sweep around arctan(1-0.546)=24.42 deg, X-wall vs Z-wall, adjacent-start vs
run-in, and split jump-after-wall / jump-wall-slide probes.

Run inside a world:

    py -3.11 driver/dev.py exec "py -3.11 <this file>"
"""

from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "driver").is_dir())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.harness.client import ModClient  # noqa: E402
from driver.harness.lab import LabSession, RecordSession, settle  # noqa: E402

OUT = ROOT / "out" / "sprint-1.26.51.01.json"
STATE_VECTOR = 0x1B5D5238
PLATFORM_Y = 150
PLAYER_HALF = 0.3
FRICTION_GROUND = 0.546

WATCH = [
    {"component_id": STATE_VECTOR, "offset": 0x18, "size": 4, "name": "mpd_x", "fmt": "f32"},
    {"component_id": STATE_VECTOR, "offset": 0x1C, "size": 4, "name": "mpd_y", "fmt": "f32"},
    {"component_id": STATE_VECTOR, "offset": 0x20, "size": 4, "name": "mpd_z", "fmt": "f32"},
]

RESULTS: dict[str, Any] = {}


def norm(*values: float) -> float:
    return math.sqrt(sum(value * value for value in values))


def speed(row: dict[str, Any]) -> float:
    return norm(float(row["vx"]), float(row["vz"]))


def sprint_series(rows: list[dict[str, Any]]) -> dict[str, Any]:
    flags = [bool(row["sprint"]) for row in rows]
    return {
        "sprint": [int(value) for value in flags],
        "toggles": sum(1 for a, b in itertools.pairwise(flags) if a != b),
        "ticks": len(rows),
    }


def contact_check(
    rows: list[dict[str, Any]],
    *,
    axis: str,
    target: float,
    label: str,
) -> dict[str, Any]:
    """Assert the player is pressed against the wall for the whole sampled window.

    ``target`` is the expected player-center coordinate: the wall block occupies
    ``[wall, wall + 1]`` and the AABB half-width is 0.3, so a +axis approach
    stops at ``wall - 0.3`` and a -axis approach at ``wall + 1.3``. Raises
    AssertionError (fails the run) when the player is airborne or not touching.
    """
    contact = next(
        (index for index, row in enumerate(rows) if abs(float(row[axis]) - target) <= 0.02),
        None,
    )
    if contact is None:
        closest = min((abs(float(row[axis]) - target) for row in rows), default=float("nan"))
        last = rows[-1] if rows else {}
        raise AssertionError(
            f"{label}: never reached {axis} wall at {target:.3f} "
            f"(closest={closest:.4f}, last_{axis}={float(last.get(axis, float('nan'))):.3f}, "
            f"rows={len(rows)})"
        )
    # Skip the two approach ticks: the collision solver zeroes the axis velocity
    # within a tick, then the player stays pressed for the rest of the window.
    pressed = rows[contact + 2 :]
    assert pressed, f"{label}: contact window too short"
    airborne = [index for index, row in enumerate(pressed) if not row["on_ground"]]
    assert not airborne, f"{label}: left the ground during contact at {airborne[:5]}"
    max_err = max(abs(float(row[axis]) - target) for row in pressed)
    v_axis = "vx" if axis == "x" else "vz"
    max_v = max(abs(float(row[v_axis])) for row in pressed)
    assert max_err <= 0.05, f"{label}: drifted off the wall ({max_err:.4f})"
    assert max_v <= 0.02, f"{label}: {v_axis} not blocked at wall ({max_v:.4f})"
    return {
        "contact_index": contact,
        "contact_rows": len(pressed),
        "max_axis_error": round(max_err, 5),
        "max_axis_speed": round(max_v, 5),
        "all_on_ground": True,
    }


def impulse_vector(rows: list[dict[str, Any]], takeoff: int) -> dict[str, Any]:
    """Horizontal vector added on the jump tick over the ground-friction prediction.

    The +0.2 is written into ``mPosDelta`` on the jump tick, which is still
    reported as ``on_ground``; the first ``not on_ground`` row is the *next*
    tick. So the impulse row is ``takeoff - 1`` and the baseline is the row
    before it scaled by the ground friction (0.546).
    """
    impulse_tick = takeoff - 1
    if impulse_tick <= 0:
        return {"takeoff": takeoff, "impulse_tick": impulse_tick, "added": None}
    prev = rows[impulse_tick - 1]
    row = rows[impulse_tick]
    pred_x = FRICTION_GROUND * float(prev["mpd_x"])
    pred_z = FRICTION_GROUND * float(prev["mpd_z"])
    dx = float(row["mpd_x"]) - pred_x
    dz = float(row["mpd_z"]) - pred_z
    return {
        "takeoff": takeoff,
        "impulse_tick": impulse_tick,
        "prev_mpd": [round(float(prev["mpd_x"]), 5), round(float(prev["mpd_z"]), 5)],
        "impulse_mpd": [round(float(row["mpd_x"]), 5), round(float(row["mpd_z"]), 5)],
        "added": [round(dx, 5), round(dz, 5)],
        "added_norm": round(norm(dx, dz), 5),
    }


def takeoff_index(rows: list[dict[str, Any]]) -> int | None:
    return next((index for index, row in enumerate(rows) if not row["on_ground"]), None)


def main() -> int:
    with ModClient(timeout=60.0).connect() as client:
        client.wait_ready(timeout=60.0, require_world=True)
        state = dict(client.state())
        feet = (float(state["feet_x"]), float(state["feet_y"]), float(state["feet_z"]))
        px, pz = int(feet[0]), int(feet[2])
        wx = px + 30
        ground = PLATFORM_Y + 1
        # 61 wide x 75 long platform; Z wall 5 blocks ahead of the run start.
        x0, x1 = wx - 30, wx + 30
        z0, z1 = pz - 10, pz + 65
        wall_z = pz + 40
        wall_x = x0 + 5  # X wall kept 25 blocks from the Z-run column
        RESULTS["setup"] = {
            "feet": feet,
            "platform": {"x": [x0, x1], "z": [z0, z1], "y": PLATFORM_Y},
            "wall_z": wall_z,
            "wall_x": wall_x,
        }

        with LabSession(client):
            try:
                client.command("gamerule falldamage false")
                client.wait_ticks(3)
                client.command("gamemode survival @s")
                client.command("effect @s clear")
                client.wait_ticks(4)

                # Build in segments after landing so every target chunk is loaded.
                for seg_x0, seg_x1 in ((x0, x0 + 20), (x0 + 20, x0 + 40), (x0 + 40, x1 + 16)):
                    client.teleport(
                        (seg_x0 + seg_x1) / 2 + 0.5, ground, pz + 2.5, yaw=0.0, pitch=0.0
                    )
                    client.wait_ticks(10)
                    client.command(
                        f"fill {seg_x0} {PLATFORM_Y} {z0} {seg_x1} {PLATFORM_Y} {z1} stone"
                    )
                    client.command(
                        f"fill {seg_x0} {PLATFORM_Y + 1} {z0} {seg_x1} {PLATFORM_Y + 10} {z1} air"
                    )
                    client.wait_ticks(4)

                def build_wall(axis: str, wall: float) -> None:
                    """Fill near the wall first, then read back and retry.

                    A single long fill aborts entirely when any target chunk is
                    not simulated, which silently produced a missing X wall.
                    """
                    names: list[str] = []
                    for _attempt in range(3):
                        if axis == "z":
                            client.teleport(wx + 0.5, ground, wall - 3, yaw=0.0, pitch=0.0)
                            client.wait_ticks(8)
                            client.command(
                                f"fill {x0} {ground} {wall} {x1} {ground + 3} {wall} stone"
                            )
                            checks = [
                                (int(wx), ground, int(wall)),
                                (int(x0 + 2), ground, int(wall)),
                                (int(x1 - 2), ground, int(wall)),
                            ]
                        else:
                            client.teleport(wall + 3, ground, pz + 30, yaw=0.0, pitch=0.0)
                            client.wait_ticks(8)
                            client.command(
                                f"fill {wall} {ground} {z0} {wall} {ground + 3} {z1} stone"
                            )
                            checks = [
                                (int(wall), ground, int(z0 + 2)),
                                (int(wall), ground, int(z1 - 2)),
                                (int(wall), ground, int(pz + 30)),
                            ]
                        client.wait_ticks(4)
                        names = [
                            str(row["name"]) for row in client.blocks(checks).get("blocks", [])
                        ]
                        if names and all(name == "minecraft:stone" for name in names):
                            return
                    raise AssertionError(f"{axis} wall at {wall} failed to build: {names}")

                build_wall("z", wall_z)
                build_wall("x", wall_x)
                RESULTS["walls"] = {"z": wall_z, "x": wall_x}
                RESULTS["surface"] = [
                    (int(row["x"]), int(row["y"]), int(row["z"]), str(row["name"]))
                    for row in client.blocks(
                        [
                            (wx, PLATFORM_Y, pz),
                            (wx, PLATFORM_Y, wall_z),
                            (wall_x, PLATFORM_Y, pz + 30),
                        ]
                    ).get("blocks", [])
                ]

                def reset_pose(
                    *, z_offset: float = -2.5, x_offset: float = 0.5, yaw: float = 0.0
                ) -> None:
                    client.teleport(wx + x_offset, ground, pz + z_offset, yaw=yaw, pitch=0.0)
                    client.wait_ticks(6)
                    settle(client, on_ground=True, input_sprint=False)

                def run(
                    *,
                    ticks: int,
                    forward: float = 0.0,
                    strafe: float = 0.0,
                    sprint: bool = False,
                    sneak: bool = False,
                    jump: bool = False,
                    tail: int = 2,
                    start_tick: int | None = None,
                ) -> list[dict[str, Any]]:
                    if start_tick is None:
                        start_tick = int(client.state()["tick"])
                    with RecordSession(client, period=1, watch=WATCH) as rec:
                        client.set_input(
                            forward=forward,
                            strafe=strafe,
                            sprint=sprint,
                            sneak=sneak,
                            jump=jump,
                            ticks=ticks,
                        )
                        client.wait_ticks(ticks + 1)
                        client.set_input()
                        client.wait_ticks(tail)
                    return [
                        row for row in rec.rows if int(row["tick"]) - start_tick <= ticks + tail
                    ]

                # --- 1. start conditions -----------------------------------
                for label, forward, strafe in (
                    ("start_forward", 1.0, 0.0),
                    ("start_backward", -1.0, 0.0),
                    ("start_strafe", 0.0, 1.0),
                    ("start_back_strafe", -1.0, 1.0),
                ):
                    reset_pose()
                    RESULTS[label] = sprint_series(
                        run(ticks=4, forward=forward, strafe=strafe, sprint=True)
                    )

                # --- 2. hold + input switch --------------------------------
                for label, forward, strafe, sneak in (
                    ("hold_back", -1.0, 0.0, False),
                    ("hold_strafe", 0.0, 1.0, False),
                    ("hold_sneak", 1.0, 0.0, True),
                ):
                    reset_pose()
                    with RecordSession(client, period=1, watch=WATCH) as rec:
                        client.set_input(forward=1.0, sprint=True, ticks=8)
                        client.wait_ticks(9)
                        client.set_input(
                            forward=forward, strafe=strafe, sprint=True, sneak=sneak, ticks=10
                        )
                        client.wait_ticks(11)
                        client.set_input()
                        client.wait_ticks(2)
                    RESULTS[label] = sprint_series(rec.rows)

                # --- 3. wall cancellation sweeps --------------------------
                def z_wall_probe(
                    *, yaw: float, ticks: int = 45, adjacent: bool = False
                ) -> dict[str, Any]:
                    z_off = wall_z - PLAYER_HALF - pz if adjacent else pz + 35 - pz
                    reset_pose(z_offset=z_off, yaw=yaw)
                    rows = run(ticks=ticks, forward=1.0, sprint=True, tail=1)
                    contact = contact_check(
                        rows, axis="z", target=wall_z - PLAYER_HALF, label=f"z@{yaw}"
                    )
                    series = sprint_series(rows)
                    return {
                        **series,
                        **contact,
                        "first_off": next(
                            (index for index, row in enumerate(rows) if not row["sprint"]), None
                        ),
                        "speeds": [round(speed(row), 5) for row in rows],
                    }

                z_sweep: dict[str, Any] = {}
                for yaw in (
                    0.0,
                    1.0,
                    5.0,
                    10.0,
                    15.0,
                    20.0,
                    23.5,
                    24.0,
                    24.3,
                    24.4,
                    24.5,
                    24.7,
                    25.0,
                    30.0,
                    45.0,
                    65.0,
                ):
                    z_sweep[f"{yaw:g}"] = z_wall_probe(yaw=yaw)
                RESULTS["wall_z_sweep"] = z_sweep

                # X wall: yaw 90 walks -x into wall_x.
                def x_wall_probe(*, yaw: float) -> dict[str, Any]:
                    reset_pose(z_offset=2.5, x_offset=(wall_x + 5.0) - wx, yaw=yaw)
                    rows = run(ticks=45, forward=1.0, sprint=True, tail=1)
                    contact = contact_check(
                        rows, axis="x", target=wall_x + 1.0 + PLAYER_HALF, label=f"x@{yaw}"
                    )
                    series = sprint_series(rows)
                    return {
                        **series,
                        **contact,
                        "first_off": next(
                            (index for index, row in enumerate(rows) if not row["sprint"]), None
                        ),
                        "speeds": [round(speed(row), 5) for row in rows],
                    }

                x_sweep: dict[str, Any] = {}
                for yaw in (90.0, 89.0, 65.6, 65.58, 65.5, 65.0, 45.0):
                    x_sweep[f"{yaw:g}"] = x_wall_probe(yaw=yaw)
                RESULTS["wall_x_sweep"] = x_sweep

                # Adjacent start (already touching the wall).
                adjacent: dict[str, Any] = {}
                for yaw in (0.0, 24.4, 25.0, 45.0, 65.0):
                    adjacent[f"{yaw:g}"] = z_wall_probe(yaw=yaw, ticks=25, adjacent=True)
                RESULTS["wall_adjacent"] = adjacent

                # --- 4. ground vs air delay --------------------------------
                def first_move(*, sprinting: bool) -> dict[str, Any]:
                    reset_pose()
                    rows = run(ticks=3, forward=1.0, sprint=sprinting, tail=0)
                    speeds = [round(speed(row), 5) for row in rows]
                    return {
                        "speeds": speeds,
                        "first_nonzero": next((value for value in speeds if value), None),
                    }

                RESULTS["delay_ground"] = {
                    "walk": first_move(sprinting=False),
                    "sprint": first_move(sprinting=True),
                }

                def air_probe(*, sprinting: bool) -> dict[str, Any]:
                    reset_pose()
                    with RecordSession(client, period=1, watch=WATCH) as rec:
                        client.set_input(jump=True, ticks=1)
                        client.wait_ticks(2)
                        client.set_input(forward=1.0, sprint=sprinting, ticks=2)
                        client.wait_ticks(3)
                        client.set_input()
                        client.wait_ticks(2)
                    air_rows = [row for row in rec.rows if row["in_air"]]
                    return {
                        "air_rows": len(air_rows),
                        "speeds": [round(speed(row), 5) for row in air_rows[:8]],
                        "mpd_z": [round(float(row["mpd_z"]), 5) for row in air_rows[:8]],
                    }

                RESULTS["delay_air"] = {
                    "walk": air_probe(sprinting=False),
                    "sprint": air_probe(sprinting=True),
                }

                # --- 5a. normal sprint jump --------------------------------
                def jump_probe(*, sprinting: bool) -> dict[str, Any]:
                    reset_pose()
                    start_tick = int(client.state()["tick"])
                    with RecordSession(client, period=1, watch=WATCH) as rec:
                        client.set_input(forward=1.0, sprint=sprinting, ticks=6)
                        client.wait_ticks(6)
                        client.set_input(jump=True, forward=1.0, sprint=sprinting, ticks=1)
                        client.wait_ticks(4)
                        client.set_input()
                        client.wait_ticks(2)
                    rows = [row for row in rec.rows if int(row["tick"]) - start_tick <= 12]
                    takeoff = takeoff_index(rows)
                    return {
                        "rows": len(rows),
                        "takeoff_index": takeoff,
                        "speeds": [round(speed(row), 5) for row in rows],
                        "mpd": [
                            [round(float(row["mpd_x"]), 5), round(float(row["mpd_z"]), 5)]
                            for row in rows
                        ],
                        "impulse": None if takeoff is None else impulse_vector(rows, takeoff),
                    }

                RESULTS["jump_walk"] = jump_probe(sprinting=False)
                RESULTS["jump_sprint"] = jump_probe(sprinting=True)

                # --- 5b. jump after the wall cancelled sprint --------------
                reset_pose(z_offset=pz + 35 - pz, yaw=0.0)
                with RecordSession(client, period=1, watch=WATCH) as rec:
                    client.set_input(forward=1.0, sprint=True, ticks=30)
                    client.wait_ticks(30)
                    client.set_input(ticks=2)
                    client.wait_ticks(3)
                    client.set_input(jump=True, sprint=True, ticks=1)
                    client.wait_ticks(4)
                    client.set_input()
                    client.wait_ticks(2)
                rows = rec.rows
                takeoff = takeoff_index(rows)
                pre = rows[:takeoff] if takeoff else rows
                assert pre[-1]["sprint"] is False, "sprint still on after the wall"
                assert speed(pre[-1]) < 0.03, f"not stopped at the wall: {speed(pre[-1]):.4f}"
                RESULTS["jump_after_wall"] = {
                    "rows": len(rows),
                    "takeoff_index": takeoff,
                    "pre_sprint": [int(bool(row["sprint"])) for row in pre[-6:]],
                    "pre_speeds": [round(speed(row), 5) for row in pre[-6:]],
                    "speeds": [round(speed(row), 5) for row in rows],
                    "contact": contact_check(
                        pre, axis="z", target=wall_z - PLAYER_HALF, label="after_wall"
                    ),
                    "impulse": None if takeoff is None else impulse_vector(rows, takeoff),
                }

                # --- 5c. jump while sliding along the wall -----------------
                # High angles slide fastest; retry until the pre-jump state is
                # genuinely pressed + sprinting + below 0.20 b/t.
                slide_attempts: list[dict[str, Any]] = []
                slide_result: dict[str, Any] | None = None
                for yaw in (25.0, 30.0, 20.0, 55.0, 60.0, 65.0):
                    reset_pose(z_offset=pz + 35 - pz, yaw=yaw)
                    with RecordSession(client, period=1, watch=WATCH) as rec:
                        client.set_input(forward=1.0, sprint=True, ticks=55)
                        client.wait_ticks(55)
                        pre_state = dict(client.state())
                        eligible = bool(pre_state.get("sprinting")) and speed(pre_state) < 0.20
                        slide_attempts.append(
                            {
                                "yaw": yaw,
                                "pre_sprinting": pre_state.get("sprinting"),
                                "pre_speed": round(speed(pre_state), 5),
                                "jumped": eligible,
                            }
                        )
                        if eligible:
                            client.set_input(jump=True, forward=1.0, sprint=True, ticks=1)
                            client.wait_ticks(4)
                        client.set_input()
                        client.wait_ticks(2)
                    if not eligible:
                        continue
                    rows = rec.rows
                    takeoff = takeoff_index(rows)
                    pre = rows[:takeoff] if takeoff else rows
                    slide_result = {
                        "rows": len(rows),
                        "takeoff_index": takeoff,
                        "pre_sprint_state": pre_state.get("sprinting"),
                        "pre_speed": round(speed(pre_state), 5),
                        "pre_sprint": [int(bool(row["sprint"])) for row in pre[-6:]],
                        "contact": contact_check(
                            pre,
                            axis="z",
                            target=wall_z - PLAYER_HALF,
                            label="wall_slide",
                        ),
                        "impulse": None if takeoff is None else impulse_vector(rows, takeoff),
                    }
                    break
                RESULTS["jump_wall_slide_attempts"] = slide_attempts
                assert slide_result is not None, f"no stable wall-slide state: {slide_attempts}"
                RESULTS["jump_wall_slide"] = slide_result
            finally:
                client.set_input()
                client.command("effect @s clear")
                client.clear_inventory()
                client.command("gamerule falldamage true")
                client.command(
                    f"fill {x0 - 1} {PLATFORM_Y - 1} {z0 - 1} {x1 + 1} {PLATFORM_Y + 40} {z1 + 1} air"
                )
                client.teleport(
                    *feet, yaw=float(state.get("yaw", 0.0)), pitch=float(state.get("pitch", 0.0))
                )
                client.wait_ticks(6)

        OUT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(RESULTS, ensure_ascii=False, indent=2))
        print(f"Saved: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
