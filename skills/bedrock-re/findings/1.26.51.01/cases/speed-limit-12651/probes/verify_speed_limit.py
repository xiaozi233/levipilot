"""Live verification of 1.26.51.01 speed limits (rumoured 16 b/t cap).

Covers: free-fall recursion vs the 0.98/-0.08 model, the Speed-effect curve and
the effective MovementSpeed component, creative-flight speed, the tiny-speed
zeroing threshold, and an explosion impulse probe.

Run inside a world:

    py -3.11 driver/dev.py exec "py -3.11 <this file>"
"""

from __future__ import annotations

import itertools
import json
import math
import struct
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
    double_space_fly,
    fit_recursion,
    settle,
)
from driver.harness.record import FLAG_FLYING  # noqa: E402

OUT = ROOT / "out" / "speed-limit-1.26.51.01.json"
STATE_VECTOR = 0x1B5D5238
MOVEMENT_SPEED = 0x1268662E

MOVE_REQUEST = 0x4C8F7288

WATCH = [
    {"component_id": STATE_VECTOR, "offset": 0x18, "size": 4, "name": "mpd_x", "fmt": "f32"},
    {"component_id": STATE_VECTOR, "offset": 0x1C, "size": 4, "name": "mpd_y", "fmt": "f32"},
    {"component_id": STATE_VECTOR, "offset": 0x20, "size": 4, "name": "mpd_z", "fmt": "f32"},
]
WATCH_REQUEST = [
    {"component_id": MOVE_REQUEST, "offset": 0x3C, "size": 4, "name": "req_x", "fmt": "f32"},
    {"component_id": MOVE_REQUEST, "offset": 0x40, "size": 4, "name": "req_y", "fmt": "f32"},
    {"component_id": MOVE_REQUEST, "offset": 0x44, "size": 4, "name": "req_z", "fmt": "f32"},
]

RESULTS: dict[str, Any] = {}


def norm(*values: float) -> float:
    return math.sqrt(sum(value * value for value in values))


def read_movement_component(client: ModClient, runtime_id: int) -> dict[str, Any] | None:
    """Read the MovementSpeed float component, if the entity has it."""
    try:
        reply = client.ecs_component(runtime_id, MOVEMENT_SPEED, length=4)
    except Exception as exc:
        return {"error": str(exc)}
    if reply.get("ok") is False:
        return {"error": reply.get("reason")}
    raw = str(reply.get("hex") or "")
    if len(raw) < 8:
        return None
    return {"value": struct.unpack("<f", bytes.fromhex(raw[:8]))[0], "hex": raw[:16]}


def median(values: list[float]) -> float | None:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else None


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def summarize_run(
    rows: list[dict[str, Any]],
    *,
    axes: tuple[str, str, str] = ("vx", "vy", "vz"),
) -> dict[str, Any]:
    """3D norms of one run plus the per-tick series (completed displacement by default).

    Watch slots read ``None`` when the component is absent on the entity; those
    rows are counted in ``invalid_rows`` instead of poisoning the vector.
    """
    invalid = sum(
        1 for row in rows if not all(isinstance(row.get(key), (int, float)) for key in axes)
    )
    vectors = [[_number(row.get(key)) for key in axes] for row in rows]
    norms = [norm(*vector) for vector in vectors]
    return {
        "rows": len(rows),
        "invalid_rows": invalid,
        "watch_masks": sorted({int(row.get("watch_mask", 0)) for row in rows}),
        "max_norm": max(norms) if norms else None,
        "min_norm": min(norms) if norms else None,
        "at_cap": sum(1 for value in norms if value >= 15.99),
        "norms": [round(value, 5) for value in norms],
        "vectors": [[round(value, 5) for value in vector] for vector in vectors],
    }


def read_component(
    client: ModClient, runtime_id: int, component_id: int, length: int
) -> dict[str, Any]:
    """Read raw component bytes (hex) plus a few decoded floats."""
    try:
        reply = client.ecs_component(runtime_id, component_id, length=length)
    except Exception as exc:
        return {"error": str(exc)}
    if reply.get("ok") is False:
        return {"error": reply.get("reason")}
    raw = str(reply.get("hex") or "")
    blob = bytes.fromhex(raw) if raw else b""
    floats = [
        round(struct.unpack_from("<f", blob, offset)[0], 6) for offset in range(0, len(blob) - 3, 4)
    ]
    return {"hex": raw, "floats": floats}


def decode_player_settings(reply: dict[str, Any]) -> dict[str, Any]:
    """Decode PlayerMovementSettingsComponent (64 B) and ServerPlayerMovementComponent."""
    raw = reply.get("hex") or ""
    if len(raw) < 128:
        return {"error": "short component"}
    blob = bytes.fromhex(raw[:128])
    thresholds = struct.unpack_from("<3f", blob, 4)
    tick_batched, tick_credit = struct.unpack_from("<QQ", blob, 32)
    optional_value = struct.unpack_from("<f", blob, 8)[0]
    return {
        "action_direction_threshold": struct.unpack_from("<f", blob, 0)[0],
        "replay_thresholds": [round(value, 6) for value in thresholds],
        "replay_mode": blob[0x10],
        "min_correction_delay": blob[0x11],
        "max_correction_delay": blob[0x12],
        "history_size": struct.unpack_from("<H", blob, 0x14)[0],
        "tick_policy": blob[0x18],
        "max_batched_ticks": tick_batched,
        "max_credit_ticks": tick_credit,
        "strict_flags": list(blob[0x30:0x33]),
        "server_auth_block_breaking": blob[0x38],
        "pick_range_scalar": struct.unpack_from("<f", blob, 0x3C)[0],
        "raw_first_16": list(blob[:16]),
        "optional_at_8": round(optional_value, 6),
    }


PLATFORM_Y = 150


def build_platform(client: ModClient, px: int, pz: int) -> None:
    """Build an 11 x 82 stone runway at PLATFORM_Y in three loaded segments."""
    for z0, z1 in ((pz - 4, pz + 20), (pz + 20, pz + 45), (pz + 45, pz + 76)):
        client.command(f"fill {px - 5} {PLATFORM_Y} {z0} {px + 5} {PLATFORM_Y} {z1} stone")
        client.command(f"fill {px - 5} {PLATFORM_Y + 1} {z0} {px + 5} {PLATFORM_Y + 9} {z1} air")
        client.teleport(px + 0.5, PLATFORM_Y + 1, z1 - 1.5, yaw=0.0, pitch=0.0)
        client.wait_ticks(10)


def main() -> int:
    with ModClient(timeout=60.0).connect() as client:
        client.wait_ready(timeout=60.0, require_world=True)
        state = dict(client.state())
        feet = (float(state["feet_x"]), float(state["feet_y"]), float(state["feet_z"]))
        px, pz = int(feet[0]), int(feet[2])
        ground = PLATFORM_Y + 1
        RESULTS["setup"] = {
            "feet": feet,
            "platform_y": PLATFORM_Y,
            "runtime_id": state.get("runtime_id"),
        }

        with LabSession(client):
            try:
                client.command("gamerule falldamage false")
                client.wait_ticks(3)
                client.command("gamemode survival @s")
                client.command("effect @s clear")
                client.wait_ticks(4)

                # --- 1. free fall: recursion vs terminal velocity ----------
                client.teleport(
                    px + 0.5, 280.0, pz + 0.5, yaw=0.0, pitch=0.0
                )  # platform is +30 x away
                client.wait_ticks(3)
                with RecordSession(client, period=1) as rec:
                    client.wait_ticks(150)
                rows = rec.rows
                vys = [float(row["vy"]) for row in rows if row["in_air"]]
                pairs = list(itertools.pairwise(vys[3:-3]))
                residuals = [b - 0.98 * (a - 0.08) for a, b in pairs]
                RESULTS["free_fall"] = {
                    "air_rows": len(vys),
                    "min_vy": min(vys) if vys else None,
                    "last_vy": vys[-1] if vys else None,
                    "residual_max_abs": max((abs(r) for r in residuals), default=None),
                    "residual_median_abs": median([abs(r) for r in residuals]),
                    "residual_over_1e3": sum(1 for r in residuals if abs(r) > 1e-3),
                    "residual_pairs": len(residuals),
                    "terminal_model": -0.0784 / (1 - 0.98),
                    "tail_vy": [round(value, 5) for value in vys[-6:]],
                }
                client.wait_ticks(4)
                client.command("effect @s instant_health 1 10 true")
                client.wait_ticks(2)
                client.command("effect @s clear")
                client.wait_ticks(2)
                build_platform(client, px + 30, pz)
                ground = PLATFORM_Y + 1
                wx = px + 30

                # --- 2. Speed effect curve ---------------------------------
                runtime_id = int(state["runtime_id"])
                components: dict[str, Any] = {
                    "movement_speed": read_component(client, runtime_id, MOVEMENT_SPEED, 4),
                    "player_movement_settings": read_component(client, runtime_id, 0xB212EF87, 64),
                    "server_player_movement": read_component(client, runtime_id, 0x70CA8F01, 64),
                    "air_speed": read_component(client, runtime_id, 0x9B8F9061, 4),
                    "local_move_velocity": read_component(client, runtime_id, 0x10257C61, 12),
                }
                settings = components["player_movement_settings"]
                if "hex" in settings:
                    components["player_movement_settings_decoded"] = decode_player_settings(
                        settings
                    )
                RESULTS["components"] = components
                curve: dict[str, Any] = {}
                for amplifier, ticks in ((0, 30), (3, 24), (10, 20), (50, 14), (255, 6)):
                    client.command("effect @s clear")
                    client.wait_ticks(2)
                    if amplifier:
                        client.command(f"effect @s speed 400 {amplifier} true")
                        client.wait_ticks(2)
                    client.teleport(wx + 0.5, ground, pz - 2.5, yaw=0.0, pitch=0.0)
                    client.wait_ticks(6)
                    settle(client, on_ground=True, input_sprint=False)
                    component = read_movement_component(client, runtime_id)
                    start_tick = int(client.state()["tick"])
                    with RecordSession(client, period=1, watch=WATCH) as rec:
                        client.set_input(forward=1.0, ticks=ticks)
                        client.wait_ticks(ticks + 2)
                        client.set_input()
                        client.wait_ticks(10)
                    rows = rec.rows
                    moving = [
                        norm(float(row["vx"]), float(row["vz"]))
                        for row in rows
                        if not row["in_air"] and int(row["tick"]) - start_tick <= ticks
                    ]
                    speed_pairs = list(itertools.pairwise(moving))
                    fit_a, fit_b, fit_res = fit_recursion(speed_pairs)
                    curve[str(amplifier)] = {
                        "component": component,
                        "plateau": median(moving[-3:]),
                        "peak": max(moving) if moving else None,
                        "fit_a": fit_a,
                        "fit_b": fit_b,
                        "fit_residual": fit_res,
                        "steady_fit": fit_b / (1 - fit_a) if fit_a == fit_a and fit_a < 1 else None,
                        "series": [round(value, 6) for value in moving],
                        "ticks": ticks,
                    }
                    client.set_input()
                    client.wait_ticks(2)
                RESULTS["speed_effect"] = curve
                client.command("effect @s clear")
                client.wait_ticks(2)

                # --- 3. sprint at amplifier 255 ----------------------------
                client.command("effect @s speed 400 255 true")
                client.wait_ticks(2)
                client.teleport(wx + 0.5, ground, pz - 2.5, yaw=0.0, pitch=0.0)
                client.wait_ticks(6)
                settle(client, on_ground=True, input_sprint=False)
                start_tick = int(client.state()["tick"])
                with RecordSession(client, period=1, watch=WATCH) as rec:
                    client.set_input(forward=1.0, sprint=True, ticks=5)
                    client.wait_ticks(7)
                    client.set_input()
                    client.wait_ticks(8)
                rows = rec.rows
                moving = [
                    norm(float(row["vx"]), float(row["vz"]))
                    for row in rows
                    if not row["in_air"] and int(row["tick"]) - start_tick <= 5
                ]
                fit_a, fit_b, fit_res = fit_recursion(list(itertools.pairwise(moving)))
                RESULTS["sprint_255"] = {
                    "peak": max(moving) if moving else None,
                    "fit_a": fit_a,
                    "fit_b": fit_b,
                    "fit_residual": fit_res,
                    "steady_fit": fit_b / (1 - fit_a) if fit_a == fit_a and fit_a < 1 else None,
                    "series": [round(value, 6) for value in moving],
                    "component": read_movement_component(client, runtime_id),
                }
                client.command("effect @s clear")
                client.wait_ticks(2)

                # --- 4. tiny-speed decay to zero ---------------------------
                client.teleport(wx + 0.5, ground, pz - 2.5, yaw=0.0, pitch=0.0)
                client.wait_ticks(6)
                settle(client, on_ground=True, input_sprint=False)
                with RecordSession(client, period=1, watch=WATCH) as rec:
                    client.set_input(forward=1.0, ticks=2)
                    client.wait_ticks(3)
                    client.set_input()
                    client.wait_ticks(240)
                rows = rec.rows
                nonzero = [
                    (int(row["tick"]), norm(float(row["vx"]), float(row["vz"])))
                    for row in rows
                    if norm(float(row["vx"]), float(row["vz"])) > 0
                ]
                RESULTS["tiny_decay"] = {
                    "rows": len(rows),
                    "last_nonzero": nonzero[-1][1] if nonzero else None,
                    "last_nonzero_tick": nonzero[-1][0] if nonzero else None,
                    "smallest_nonzero": min((mag for _, mag in nonzero), default=None),
                    "tail_vx": [round(float(row["vx"]), 12) for row in rows[-10:]],
                    "tail_mpd_x": [round(float(row["mpd_x"]), 12) for row in rows[-10:]],
                }

                # --- 5. creative flight speed -----------------------------
                client.command("gamemode creative @s")
                client.wait_ticks(6)
                client.teleport(wx + 0.5, ground + 12.0, pz + 0.5, yaw=0.0, pitch=0.0)
                client.wait_ticks(8)
                client.toggle_fly(True)
                client.wait_ticks(2)
                flight = double_space_fly(client)
                RESULTS["creative_flight_flags"] = flight
                with RecordSession(client, period=1, watch=WATCH) as rec:
                    client.set_input(forward=1.0, ticks=30)
                    client.wait_ticks(34)
                    client.set_input()
                    client.wait_ticks(6)
                rows = rec.rows
                speeds = [norm(float(row["vx"]), float(row["vz"])) for row in rows]
                flying = [row for row in rows if int(row["flags"]) & FLAG_FLYING]
                fly_speeds = [norm(float(row["vx"]), float(row["vz"])) for row in flying]
                RESULTS["creative_flight"] = {
                    "peak": max(fly_speeds) if fly_speeds else None,
                    "plateau": median(fly_speeds[-6:]),
                    "flying_rows": len(flying),
                    "series": [round(value, 5) for value in speeds[:20]],
                }
                client.teleport(wx + 0.5, ground + 12.0, pz - 2.5, yaw=0.0, pitch=0.0)
                client.wait_ticks(8)
                client.toggle_fly(True)
                client.wait_ticks(2)
                double_space_fly(client)
                with RecordSession(client, period=1, watch=WATCH) as rec:
                    client.set_input(forward=1.0, sprint=True, ticks=60)
                    client.wait_ticks(64)
                    client.set_input()
                    client.wait_ticks(6)
                rows = rec.rows
                flying = [row for row in rows if int(row["flags"]) & FLAG_FLYING]
                fly_speeds = [norm(float(row["vx"]), float(row["vz"])) for row in flying]
                RESULTS["creative_sprint_flight"] = {
                    "peak": max(fly_speeds) if fly_speeds else None,
                    "plateau": median(fly_speeds[-6:]),
                    "flying_rows": len(flying),
                }
                client.toggle_fly(False)
                client.wait_ticks(2)

                # --- 6. explosion impulse ---------------------------------
                client.command("gamemode survival @s")
                client.command("gamerule mobgriefing false")
                client.command(
                    f"fill {wx - 3} {ground + 5} {pz - 3} {wx + 3} {ground + 5} {pz + 3} stone"
                )
                client.wait_ticks(6)
                top = ground + 6
                client.teleport(wx + 0.5, top, pz + 0.5, yaw=0.0, pitch=0.0)
                client.wait_ticks(10)
                client.command(f"summon minecraft:tnt {wx + 2.5} {top} {pz + 0.5}")
                client.wait_ticks(60)
                with RecordSession(client, period=1, watch=WATCH) as rec:
                    client.wait_ticks(60)
                after = dict(client.state())
                rows = rec.rows
                norms = [norm(float(row["vx"]), float(row["vy"]), float(row["vz"])) for row in rows]
                RESULTS["tnt_impulse"] = {
                    "max_norm": max(norms) if norms else None,
                    "max_vy": max((float(row["vy"]) for row in rows), default=None),
                    "max_vxz": max(
                        (norm(float(r["vx"]), float(r["vz"])) for r in rows), default=None
                    ),
                    "air_rows": sum(1 for row in rows if row["in_air"]),
                    "health_after": after.get("health"),
                }
                client.command("kill @e[type=minecraft:tnt]")
                client.command("gamerule mobgriefing true")
                client.wait_ticks(3)

                # --- 7. 16 b/t Euclidean clamp -----------------------------
                def runway(block: str) -> list[tuple[int, int, str]]:
                    """Land first, fill in loaded segments, then read the surface back."""
                    for z0, z1 in ((pz - 4, pz + 30), (pz + 30, pz + 56), (pz + 56, pz + 80)):
                        client.teleport(wx + 0.5, ground, z0 + 2.5, yaw=0.0, pitch=0.0)
                        client.wait_ticks(10)
                        client.command(
                            f"fill {wx - 5} {PLATFORM_Y} {z0} {wx + 5} {PLATFORM_Y} {z1} {block}"
                        )
                        client.wait_ticks(4)
                    return [
                        (int(row["x"]), int(row["z"]), str(row["name"]))
                        for row in client.blocks(
                            [
                                (wx, PLATFORM_Y, pz),
                                (wx, PLATFORM_Y, pz + 30),
                                (wx, PLATFORM_Y, pz + 58),
                            ]
                        ).get("blocks", [])
                    ]

                def run_pass(
                    *, ticks: int, sprint: bool, strafe: float, watch: list[dict[str, Any]]
                ) -> list[dict[str, Any]]:
                    client.teleport(wx + 0.5, ground, pz - 2.5, yaw=0.0, pitch=0.0)
                    client.wait_ticks(6)
                    settle(client, on_ground=True, input_sprint=False)
                    start_tick = int(client.state()["tick"])
                    with RecordSession(client, period=1, watch=watch) as rec:
                        client.set_input(forward=1.0, strafe=strafe, sprint=sprint, ticks=ticks)
                        client.wait_ticks(ticks + 2)
                        client.set_input()
                        client.wait_ticks(2)
                    return [row for row in rec.rows if int(row["tick"]) - start_tick <= ticks]

                def with_fit(result: dict[str, Any]) -> dict[str, Any]:
                    """Fit v' = a*v + b over the nonzero series and extrapolate the plateau."""
                    samples = [value for value in result["norms"] if value > 0]
                    fit_a, fit_b, fit_res = fit_recursion(list(itertools.pairwise(samples)))
                    return {
                        **result,
                        "fit_a": fit_a,
                        "fit_b": fit_b,
                        "fit_residual": fit_res,
                        "steady_fit": fit_b / (1 - fit_a) if fit_a == fit_a and fit_a < 1 else None,
                    }

                def measure(*, ticks: int, sprint: bool, strafe: float) -> dict[str, Any]:
                    moved = run_pass(ticks=ticks, sprint=sprint, strafe=strafe, watch=WATCH)
                    requested = run_pass(
                        ticks=ticks, sprint=sprint, strafe=strafe, watch=WATCH_REQUEST
                    )
                    return {
                        "completed": summarize_run(moved),
                        "mposdelta": summarize_run(moved, axes=("mpd_x", "mpd_y", "mpd_z")),
                        "request": summarize_run(requested, axes=("req_x", "req_y", "req_z")),
                    }

                # 7a. blue ice alone cannot reach 16: Bedrock scales the
                # acceleration by (0.6/slip)^3, so the 0.9 friction and the
                # smaller accel nearly cancel. Recorded as the counter-example.
                RESULTS["blue_ice_surface"] = runway("minecraft:blue_ice")
                client.command("gamemode survival @s")
                client.command("effect @s clear")
                client.command("effect @s speed 400 255 true")
                client.wait_ticks(3)
                RESULTS["blue_ice"] = {
                    "axis_walk": with_fit(measure(ticks=6, sprint=False, strafe=0.0)["completed"]),
                    "axis_sprint": with_fit(measure(ticks=6, sprint=True, strafe=0.0)["completed"]),
                }

                # 7b. soul sand + Soul Speed III + Speed 255 + sprint: the target
                # is far above 16 b/t, so fetchCollisionShapes must clamp.
                RESULTS["soul_sand_surface"] = runway("minecraft:soul_sand")
                client.command("gamemode creative @s")
                client.wait_ticks(4)
                client.set_item(0, "minecraft:diamond_boots", count=1)
                client.select_slot(0)
                client.wait_ticks(2)
                enchanted = client.command("enchant @s soul_speed 3")
                client.wait_ticks(2)
                client.equip("feet", slot=0)
                client.wait_ticks(4)
                feet_item = next(
                    (
                        slot.get("item")
                        for slot in client.inventory().get("armor", [])
                        if int(slot.get("slot", -1)) == 3
                    ),
                    None,
                )
                RESULTS["soul_speed_boots"] = {"enchant_reply": enchanted, "feet": feet_item}
                client.command("gamemode survival @s")
                client.command("effect @s clear")
                client.command("effect @s speed 400 255 true")
                client.wait_ticks(3)
                RESULTS["speed_clamp"] = {
                    "axis_sprint": measure(ticks=5, sprint=True, strafe=0.0),
                    "diagonal_sprint": measure(ticks=5, sprint=True, strafe=1.0),
                    "axis_walk": measure(ticks=7, sprint=False, strafe=0.0),
                }

                # Spectator bypass probe: movement without normal collision input.
                client.command("gamemode spectator @s")
                client.wait_ticks(6)
                client.command("effect @s clear")
                RESULTS["spectator_probe"] = summarize_run(
                    run_pass(ticks=8, sprint=True, strafe=0.0, watch=WATCH)
                )
                client.command("gamemode survival @s")
                client.wait_ticks(4)
            finally:
                client.set_input()
                client.command("effect @s clear")
                client.clear_inventory()
                client.toggle_fly(False)
                client.command("gamerule falldamage true")
                client.command("gamerule mobgriefing true")
                client.command(
                    f"fill {wx - 5} {PLATFORM_Y - 1} {pz - 4} {wx + 5} {PLATFORM_Y + 40} {pz + 76} air"
                )
                client.command("kill @e[type=minecraft:tnt]")
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
