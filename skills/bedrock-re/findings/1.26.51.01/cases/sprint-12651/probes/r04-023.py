"""Round 3 tail closure for sprint-12651: fine f65 boundary sweep + Intent branches.

Round 4 correction: the fine sweep is classified **inside the wall-contact hold
window** (contact reached, forward+sprint still held).  Classifying on
whole-recording ``toggles`` conflated the start edge ``0->1`` and the release
edge ``1->0`` with the wall behaviour and mislabelled every stable angle as a
"single break".

Companion to ``verify_sprint.py``.  Runs inside a world:

    py -3.11 driver/dev.py exec "py -3.11 <this file>"

All reported numbers are written verbatim to ``out/sprint-tail-1.26.51.01.json``.
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
from driver.harness.lab import (  # noqa: E402
    LabSession,
    RecordSession,
    settle,
    swim_drive,
    swim_stop,
    swimming_state,
)

OUT = ROOT / "out" / "sprint-tail-1.26.51.01.json"
STATE_VECTOR = 0x1B5D5238
PLATFORM_Y = 150
PLAYER_HALF = 0.3

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


def series(rows: list[dict[str, Any]]) -> dict[str, Any]:
    flags = [bool(row["sprint"]) for row in rows]
    return {
        "sprint": [int(v) for v in flags],
        "toggles": sum(1 for a, b in itertools.pairwise(flags) if a != b),
        "ticks": len(rows),
    }


def held_window(
    rows: list[dict[str, Any]], contact: int, release_tick: int
) -> list[dict[str, Any]]:
    """Rows where the wall has been reached AND forward+sprint is still held.

    ``contact + 2`` mirrors the settle margin used by :func:`contact_check`;
    ``release_tick`` is read from the game right before the key release, so the
    trailing rows of the recording (input released -> sprint legitimately
    drops) never enter the classification.  Classifying on whole-recording
    toggles mixes the start edge and the release edge with the wall behaviour.
    """
    return [
        row
        for index, row in enumerate(rows)
        if index >= contact + 2 and int(row["tick"]) <= release_tick
    ]


def classify(rows: list[dict[str, Any]], contact: int, release_tick: int) -> dict[str, Any]:
    """Classify sprint behaviour *inside* the wall-contact hold window."""
    window = held_window(rows, contact, release_tick)
    flags = [int(bool(row["sprint"])) for row in window]
    zeros = sum(1 for flag in flags if flag == 0)
    breaks = sum(1 for a, b in itertools.pairwise(flags) if a == 1 and b == 0)
    if not flags:
        kind = "empty"
    elif zeros == 0:
        kind = "stable"
    elif zeros >= 2:
        kind = "flicker"
    elif breaks == 1 and flags[-1] == 0:
        kind = "single_break"
    else:
        kind = "other"
    return {
        "kind": kind,
        "held_rows": len(flags),
        "held_zeros": zeros,
        "held_breaks": breaks,
        "held_sprint": flags,
    }


def contact_check(
    rows: list[dict[str, Any]], *, axis: str, target: float, label: str
) -> dict[str, Any]:
    contact = next(
        (i for i, row in enumerate(rows) if abs(float(row[axis]) - target) <= 0.02), None
    )
    if contact is None:
        closest = min((abs(float(row[axis]) - target) for row in rows), default=float("nan"))
        raise AssertionError(
            f"{label}: never reached {axis}={target:.3f} (closest={closest:.4f}, rows={len(rows)})"
        )
    pressed = rows[contact + 2 :]
    assert pressed, f"{label}: contact window too short"
    airborne = [i for i, row in enumerate(pressed) if not row["on_ground"]]
    assert not airborne, f"{label}: airborne during contact at {airborne[:5]}"
    max_err = max(abs(float(row[axis]) - target) for row in pressed)
    v_axis = "vx" if axis == "x" else "vz"
    max_v = max(abs(float(row[v_axis])) for row in pressed)
    assert max_err <= 0.05, f"{label}: drifted off wall ({max_err:.4f})"
    assert max_v <= 0.02, f"{label}: {v_axis} not blocked ({max_v:.4f})"
    return {
        "contact_index": contact,
        "contact_rows": len(pressed),
        "max_axis_error": round(max_err, 5),
        "max_axis_speed": round(max_v, 5),
        "all_on_ground": True,
    }


def dtap(client: ModClient, kb: int, spr: int, gap: int, *, air: bool) -> dict[str, Any]:
    """Double-tap forward with `gap` idle ticks between the two W edges."""
    client.set_input()
    for code in (kb, spr, 32):
        client.key_event(code, False, via="hardware")
    client.wait_ticks(14)
    # A prior air trial leaves the player airborne; always wait for a clean
    # grounded pose (and a cleared sprint latch) before the next trial.
    settle(client, on_ground=True, input_sprint=False, sprinting=False)
    with RecordSession(client) as rec:
        if air:
            client.key_event(32, True, via="hardware")
            client.wait_ticks(1)
            client.key_event(32, False, via="hardware")
            client.wait_ticks(2)
        airborne = not bool(client.state().get("on_ground"))
        client.key_event(kb, True, via="hardware")
        client.wait_ticks(1)
        client.key_event(kb, False, via="hardware")
        client.wait_ticks(gap)
        client.key_event(kb, True, via="hardware")
        client.wait_ticks(6)
        client.key_event(kb, False, via="hardware")
        client.wait_ticks(2)
    rows = rec.rows
    # For air cases only judge the airborne window: after landing a fresh
    # double-tap window can legitimately open and start sprint.
    # Space is a 1-tick edge; the player lands again ~8-10 ticks later.  The
    # sprint-trigger timer counts ticks, not on-ground state, so a legal
    # post-landing start can happen while the trigger is still live.  Judge the
    # strict in-air window (first 5 airborne rows) and keep the full series.
    airborne_rows = [r for r in rows if not r.get("on_ground")]
    judged = rows if not air else airborne_rows[:5]
    flags = [int(bool(r["sprint"])) for r in judged]
    full_air = [int(bool(r["sprint"])) for r in airborne_rows]
    return {
        "gap": gap,
        "airborne_at_input": airborne,
        "judged": "first_5_airborne" if air else "all",
        "judged_rows": len(judged),
        "series": flags,
        "full_airborne_series": full_air,
        "first_on": next((i for i, v in enumerate(flags) if v), None),
        "any_sprint": any(flags),
    }


def main() -> int:
    with ModClient(timeout=60.0).connect() as client:
        client.wait_ready(timeout=60.0, require_world=True)
        state = dict(client.state())
        feet = (float(state["feet_x"]), float(state["feet_y"]), float(state["feet_z"]))
        px, pz = int(feet[0]), int(feet[2])
        wx = px + 30
        ground = PLATFORM_Y + 1
        x0, x1 = wx - 30, wx + 30
        z0, z1 = pz - 10, pz + 65
        wall_z = pz + 40
        wall_x = x0 + 5
        kb = int(state.get("bindings", {}).get("forward", 87))
        spr = int(state.get("bindings", {}).get("sprint", 82))
        theory = math.degrees(math.atan(1 - 0.546))
        RESULTS["setup"] = {
            "feet": feet,
            "platform": {"x": [x0, x1], "z": [z0, z1], "y": PLATFORM_Y},
            "wall_z": wall_z,
            "wall_x": wall_x,
            "bindings": {"forward": kb, "sprint": spr},
            "theory_deg": round(theory, 6),
            "theory_complement_deg": round(90 - theory, 6),
        }

        with LabSession(client):
            try:
                client.command("gamerule falldamage false")
                client.wait_ticks(3)
                client.command("gamemode survival @s")
                client.command("effect @s clear")
                client.wait_ticks(4)

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

                def build_wall(axis: str, wall: int) -> None:
                    names: list[str] = []
                    for _attempt in range(3):
                        if axis == "z":
                            client.teleport(wx + 0.5, ground, wall - 3, yaw=0.0, pitch=0.0)
                            client.wait_ticks(8)
                            client.command(
                                f"fill {x0} {ground} {wall} {x1} {ground + 3} {wall} stone"
                            )
                            checks = [
                                (wx, ground, wall),
                                (x0 + 2, ground, wall),
                                (x1 - 2, ground, wall),
                            ]
                        else:
                            client.teleport(wall + 3, ground, pz + 30, yaw=0.0, pitch=0.0)
                            client.wait_ticks(8)
                            client.command(
                                f"fill {wall} {ground} {z0} {wall} {ground + 3} {z1} stone"
                            )
                            checks = [
                                (wall, ground, z0 + 2),
                                (wall, ground, z1 - 2),
                                (wall, ground, pz + 30),
                            ]
                        client.wait_ticks(4)
                        names = [str(r["name"]) for r in client.blocks(checks).get("blocks", [])]
                        if names and all(n == "minecraft:stone" for n in names):
                            return
                    raise AssertionError(f"{axis} wall at {wall} failed: {names}")

                build_wall("z", int(wall_z))
                build_wall("x", int(wall_x))
                RESULTS["walls"] = {"z": wall_z, "x": wall_x}

                def reset(*, z_off: float = -2.5, x_off: float = 0.5, yaw: float = 0.0) -> None:
                    client.set_input()
                    client.teleport(wx + x_off, ground, pz + z_off, yaw=yaw, pitch=0.0)
                    client.wait_ticks(6)
                    assert settle(client, on_ground=True, input_sprint=False)["ok"]

                def run(*, ticks: int, yaw: float, axis: str) -> dict[str, Any]:
                    if axis == "z":
                        reset(z_off=wall_z - PLAYER_HALF - pz - 5, yaw=yaw)
                    else:
                        reset(z_off=2.5, x_off=(wall_x + 5.0) - wx, yaw=yaw)
                    start = int(client.state()["tick"])
                    with RecordSession(client, period=1, watch=WATCH) as rec:
                        client.set_input(forward=1.0, sprint=True, ticks=ticks)
                        client.wait_ticks(ticks + 1)
                        release_tick = int(client.state()["tick"])
                        client.set_input()
                        client.wait_ticks(2)
                    rows = [r for r in rec.rows if int(r["tick"]) - start <= ticks + 3]
                    return {"rows": rows, "release_tick": release_tick}

                # ---- fine f65 sweep: 0.01 deg steps ------------------------
                fine: dict[str, Any] = {"z": {}, "x": {}}
                for hundredths in range(2438, 2451):  # 24.38 .. 24.50
                    yaw = hundredths / 100.0
                    trial = run(ticks=45, yaw=yaw, axis="z")
                    rows, release_tick = trial["rows"], trial["release_tick"]
                    contact = contact_check(
                        rows, axis="z", target=wall_z - PLAYER_HALF, label=f"z@{yaw}"
                    )
                    fine["z"][f"{yaw:.2f}"] = {
                        **series(rows),
                        **contact,
                        **classify(rows, contact["contact_index"], release_tick),
                    }

                for hundredths in range(6550, 6563):  # 65.50 .. 65.62
                    yaw = hundredths / 100.0
                    trial = run(ticks=45, yaw=yaw, axis="x")
                    rows, release_tick = trial["rows"], trial["release_tick"]
                    contact = contact_check(
                        rows, axis="x", target=wall_x + 1.0 + PLAYER_HALF, label=f"x@{yaw}"
                    )
                    fine["x"][f"{yaw:.2f}"] = {
                        **series(rows),
                        **contact,
                        **classify(rows, contact["contact_index"], release_tick),
                    }
                RESULTS["fine_sweep"] = fine

                def band(table: dict[str, Any]) -> dict[str, Any]:
                    entries = [(float(k), v) for k, v in table.items()]
                    buckets: dict[str, list[float]] = {
                        "stable_angles": [],
                        "flicker_angles": [],
                        "single_break_angles": [],
                        "other_angles": [],
                    }
                    for angle, value in entries:
                        bucket = {
                            "stable": "stable_angles",
                            "flicker": "flicker_angles",
                            "single_break": "single_break_angles",
                        }.get(value["kind"], "other_angles")
                        buckets[bucket].append(angle)
                    for angles in buckets.values():
                        angles.sort()
                    stable = buckets["stable_angles"]
                    flicker = buckets["flicker_angles"]
                    return {
                        **buckets,
                        "stable_min": min(stable) if stable else None,
                        "stable_max": max(stable) if stable else None,
                        "flicker_min": min(flicker) if flicker else None,
                        "flicker_max": max(flicker) if flicker else None,
                    }

                RESULTS["fine_bands"] = {"z": band(fine["z"]), "x": band(fine["x"])}
                print("fine_bands:", json.dumps(RESULTS["fine_bands"], ensure_ascii=False))
                # The claim is *where* the transition sits: the closed-form
                # critical angle must fall between the highest flickering step
                # and the lowest stable step.  Asserting exact 0.01 deg bins
                # would make the probe fail on a one-bin shift caused by float
                # rounding; the bracket is the actual prediction.
                z_bands = RESULTS["fine_bands"]["z"]
                x_bands = RESULTS["fine_bands"]["x"]
                assert z_bands["flicker_angles"] and z_bands["stable_angles"], z_bands
                assert x_bands["flicker_angles"] and x_bands["stable_angles"], x_bands
                assert z_bands["flicker_max"] < z_bands["stable_min"], z_bands
                assert x_bands["stable_max"] < x_bands["flicker_min"], x_bands
                assert z_bands["flicker_max"] < theory < z_bands["stable_min"], z_bands
                assert x_bands["stable_max"] < 90.0 - theory < x_bands["flicker_min"], x_bands
                # A steady hold against the wall never produces a single
                # terminal break in this sweep: either it keeps sprinting or it
                # alternates.  Guard the misclassification that round 3 shipped.
                assert not z_bands["single_break_angles"], z_bands
                assert not x_bands["single_break_angles"], x_bands

                # ---- Intent: blindness ------------------------------------
                reset()
                client.command("effect @s blindness 20 0 true")
                client.wait_ticks(4)
                blind_effects = list(client.state().get("effects") or [])
                assert any(e.get("id") == 15 for e in blind_effects), blind_effects
                with RecordSession(client) as rec:
                    client.set_input(forward=1.0, sprint=True, ticks=14)
                    client.wait_ticks(16)
                    client.set_input()
                    client.wait_ticks(3)
                blind_on = [int(bool(r["sprint"])) for r in rec.rows]
                assert not any(blind_on), blind_on
                client.command("effect @s clear")
                client.wait_ticks(6)
                reset()
                with RecordSession(client) as rec2:
                    client.set_input(forward=1.0, sprint=True, ticks=14)
                    client.wait_ticks(16)
                    client.set_input()
                    client.wait_ticks(3)
                clear_on = [int(bool(r["sprint"])) for r in rec2.rows]
                assert any(clear_on), clear_on
                RESULTS["intent_blindness"] = {
                    "effect_id_15": blind_effects,
                    "blindness_sprint": blind_on,
                    "after_clear_sprint": clear_on,
                }

                # ---- Intent: low hunger (effect-only bound) ----------------
                reset()
                client.command("effect @s hunger 255 20 true")
                client.wait_ticks(4)
                hun0 = float(client.state().get("hunger") or 0)
                samples: list[dict[str, Any]] = []
                for _ in range(6):
                    client.set_input(forward=1.0, ticks=8)
                    client.wait_ticks(9)
                    st = client.state()
                    samples.append(
                        {
                            "tick": st.get("tick"),
                            "hunger": st.get("hunger"),
                            "exhaustion": round(float(st.get("exhaustion") or 0), 4),
                        }
                    )
                client.set_input()
                client.wait_ticks(4)
                hun1 = float(client.state().get("hunger") or 0)
                with RecordSession(client) as rec:
                    client.set_input(forward=1.0, sprint=True, ticks=14)
                    client.wait_ticks(16)
                    client.set_input()
                    client.wait_ticks(3)
                lowh_sprint = [int(bool(r["sprint"])) for r in rec.rows]
                RESULTS["intent_low_hunger"] = {
                    "hunger_before": hun0,
                    "hunger_after": hun1,
                    "effect_reached_le6": hun1 <= 6,
                    "samples": samples,
                    "sprint_series_under_effect": lowh_sprint,
                    "note": (
                        "Windows 1.26.51.01: /effect hunger raises exhaustion but hunger "
                        "stays 20 in-window; food<=6 gate not reachable by effect alone. "
                        "Static branch (PlayerInputRequest +0x0F) remains closed."
                    ),
                }
                client.command("effect @s clear")
                client.wait_ticks(5)

                # ---- Intent: item-use slowdown -----------------------------
                reset()
                client.command("give @s bread 8")
                client.wait_ticks(3)
                filled = [s["slot"] for s in client.inventory().get("slots", []) if s.get("item")]
                assert filled, "no bread slot"
                client.select_slot(filled[0])
                client.wait_ticks(10)
                item_state = client.state().get("item")
                assert item_state and "bread" in str(item_state.get("type")), item_state
                with RecordSession(client) as rec:
                    client.set_input(rmb=True, ticks=16)
                    client.wait_ticks(2)
                    client.set_input(forward=1.0, sprint=True, rmb=True, ticks=14)
                    client.wait_ticks(16)
                    client.set_input()
                    client.wait_ticks(3)
                item_sprint = [int(bool(r["sprint"])) for r in rec.rows]
                assert not any(item_sprint), item_sprint
                RESULTS["intent_item_slowdown"] = {
                    "item": item_state,
                    "sprint_series": item_sprint,
                }
                client.set_input()
                client.wait_ticks(4)

                # ---- Intent: water non-swimming jump cancels sprint -------
                pool_floor = ground - 6
                cpx, cpz = wx - 2, pz - 20
                client.teleport(cpx + 0.5, pool_floor + 6, cpz + 2.5, yaw=0.0, pitch=0.0)
                client.wait_ticks(8)
                client.command(
                    f"fill {cpx - 3} {pool_floor} {cpz - 3} {cpx + 3} {pool_floor} {cpz + 6} stone"
                )
                client.command(
                    f"fill {cpx - 3} {pool_floor + 1} {cpz - 3} {cpx + 3} {pool_floor + 2} {cpz + 6} water"
                )
                client.wait_ticks(10)
                client.teleport(cpx + 0.5, pool_floor + 1, cpz + 0.5, yaw=0.0, pitch=0.0)
                client.wait_ticks(8)
                st = client.state()
                assert st.get("in_water") is True, st.get("in_water")
                assert swimming_state(client) is False, swimming_state(client)
                with RecordSession(client) as rec:
                    client.set_input(forward=1.0, sprint=True, ticks=8)
                    client.wait_ticks(9)
                    pre_sprint = bool(client.state().get("sprint"))
                    client.set_input(jump=True, forward=1.0, sprint=True, ticks=2)
                    client.wait_ticks(4)
                    client.set_input()
                    client.wait_ticks(4)
                wet_series = [int(bool(r["sprint"])) for r in rec.rows]
                assert any(wet_series[:9]), wet_series
                assert not any(wet_series[-4:]), wet_series
                RESULTS["intent_water_jump"] = {
                    "in_water": True,
                    "swimming": False,
                    "pre_sprint": pre_sprint,
                    "sprint_series": wet_series,
                }

                # ---- Intent: double-tap W 7-tick window --------------------
                ground_dt: dict[str, Any] = {}
                for gap in (1, 2, 5, 9):
                    ground_dt[str(gap)] = dtap(client, kb, spr, gap, air=False)
                assert ground_dt["1"]["any_sprint"] and ground_dt["2"]["any_sprint"]
                assert not ground_dt["5"]["any_sprint"] and not ground_dt["9"]["any_sprint"]
                RESULTS["intent_double_tap_ground"] = ground_dt

                air_dt: dict[str, Any] = {}
                for gap in (1, 2, 5):
                    air_dt[str(gap)] = dtap(client, kb, spr, gap, air=True)
                    assert air_dt[str(gap)]["airborne_at_input"]
                    assert not air_dt[str(gap)]["any_sprint"], air_dt[str(gap)]["series"]
                client.set_input()
                for code in (kb, spr, 32):
                    client.key_event(code, False, via="hardware")
                client.wait_ticks(12)
                settle(client, on_ground=True, input_sprint=False, sprinting=False)
                with RecordSession(client) as rec:
                    client.key_event(32, True, via="hardware")
                    client.wait_ticks(1)
                    client.key_event(32, False, via="hardware")
                    client.wait_ticks(2)
                    air0 = not bool(client.state().get("on_ground"))
                    client.set_input(forward=1.0, sprint=True, ticks=8)
                    client.wait_ticks(9)
                    client.set_input()
                    client.wait_ticks(3)
                air_key_series = [int(bool(r["sprint"])) for r in rec.rows]
                assert air0 and any(air_key_series), (air0, air_key_series)
                RESULTS["intent_double_tap_air"] = {
                    "double_tap": air_dt,
                    "independent_key": {
                        "airborne_at_input": air0,
                        "sprint_series": air_key_series,
                    },
                }

                # ---- Intent: swimming sprint survives wall contact --------
                # Verified geometry from dolphins-grace-12651: 5-wide, 8-deep
                # channel with stone on all four sides, minecraft: prefix on
                # every fill, and settle(in_water=True) before swim_drive.
                sx, sy = wx - 14, ground - 12
                sz = pz - 50
                y_bot, y_top = sy + 1, sy + 8
                client.teleport(sx + 3.5, y_bot + 3, sz + 6, yaw=0.0, pitch=0.0)
                client.wait_ticks(12)
                client.command(
                    f"fill {sx - 1} {sy - 1} {sz - 1} {sx + 7} {y_top + 2} {sz + 32} minecraft:air"
                )
                client.wait_ticks(4)
                client.command(f"fill {sx} {sy} {sz} {sx + 6} {sy} {sz + 30} minecraft:stone")
                client.command(f"fill {sx} {y_bot} {sz} {sx} {y_top} {sz + 30} minecraft:stone")
                client.command(
                    f"fill {sx + 6} {y_bot} {sz} {sx + 6} {y_top} {sz + 30} minecraft:stone"
                )
                client.command(f"fill {sx} {y_bot} {sz} {sx + 6} {y_top} {sz} minecraft:stone")
                client.command(
                    f"fill {sx} {y_bot} {sz + 30} {sx + 6} {y_top} {sz + 30} minecraft:stone"
                )
                client.command(
                    f"fill {sx + 1} {y_bot} {sz + 1} {sx + 5} {y_top} {sz + 29} minecraft:water"
                )
                client.wait_ticks(12)
                blocks = client.blocks([[sx + 3, y_top - 2, sz + 4]]).get("blocks", [])
                assert blocks and str(blocks[0].get("name", "")).endswith("water"), blocks
                client.teleport(sx + 3.5, sy + 4.0, sz + 3, yaw=0.0, pitch=0.0)
                client.wait_ticks(6)
                settle(
                    client,
                    ticks=4,
                    on_ground=None,
                    include_vertical=False,
                    in_water=True,
                    sprinting=False,
                    input_sprint=False,
                )
                drive = swim_drive(client, sprint=True, timeout=2.0, hold=True, poll_ticks=2)
                sw = swimming_state(client)
                if sw and drive.get("entered"):
                    client.look(0.0, 0.0, ticks=2)
                    with RecordSession(client) as rec:
                        client.set_input(forward=1.0, sprint=True, ticks=55)
                        client.wait_ticks(57)
                        client.set_input()
                        client.wait_ticks(3)
                    wet_flags = [int(bool(r["sprint"])) for r in rec.rows if r.get("in_water")]
                    RESULTS["intent_swim_wall"] = {
                        "drive": drive,
                        "swimming": sw,
                        "channel": {"x": [sx, sx + 6], "y": [y_bot, y_top], "z": [sz, sz + 30]},
                        "wet_sprint_series": wet_flags,
                        "wet_toggles": sum(1 for a, b in itertools.pairwise(wet_flags) if a != b),
                    }
                    assert len(wet_flags) >= 30, len(wet_flags)
                    assert RESULTS["intent_swim_wall"]["wet_toggles"] <= 2
                else:
                    RESULTS["intent_swim_wall"] = {
                        "drive": drive,
                        "swimming": sw,
                        "status": "swimming_not_reached",
                        "note": "SWIMMING gate not entered with this channel; static branch retained.",
                    }
                swim_stop(client, settle=6)
            finally:
                client.set_input()
                client.command("effect @s clear")
                client.command("gamerule falldamage true")
                # Keep whatever was collected even when a hard assertion below
                # fails: a failed run must still leave the raw series on disk
                # so the classification can be audited instead of re-running.
                OUT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")

        OUT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = {
            "setup": RESULTS["setup"],
            "fine_bands": RESULTS["fine_bands"],
            "blindness": "ok",
            "low_hunger": {
                "before": RESULTS["intent_low_hunger"]["hunger_before"],
                "after": RESULTS["intent_low_hunger"]["hunger_after"],
            },
            "item_slowdown": "ok",
            "water_jump": "ok",
            "double_tap_ground": {
                k: v["any_sprint"] for k, v in RESULTS["intent_double_tap_ground"].items()
            },
            "double_tap_air": {
                k: v["any_sprint"]
                for k, v in RESULTS["intent_double_tap_air"]["double_tap"].items()
            },
            "swim_wall": RESULTS["intent_swim_wall"].get("status", "ok"),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"Saved: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
