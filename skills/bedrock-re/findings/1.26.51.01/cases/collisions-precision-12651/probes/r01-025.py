"""collisions-precision-12651: float32 AABB precision drift, wall-stuck depenetration
and entity push.

Sections
--------
S1 baseline precision  : standing / walking / jumping at origin coords, per-tick
                          AABBShapeComponent min/max watch -> width/height ULP walk.
S2 depenetration       : shallow embed (MTD instant pop <= mMaxDepenetration) and
                          deep embed (all-axis exits > 1.0 -> slow push), with raw
                          DepenetrationComponent / ActorDataFlag / MoveRequest reads.
S3 high-coordinate ULP : x = 2^10 / 2^14 / 2^17 / 2^20 standing + walk, exact f32
                          width/height table.
S4 entity push         : player vs NoAI-cow overlap, per-tick mPosDelta increments
                          and cow displacement.

All fixtures are verified by blocks()/block_shapes() readback before any number is
published; raw rows go to out/collisions-precision-1.26.51.01.json.
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
from driver.harness.lab import LabSession, RecordSession, settle  # noqa: E402
from driver.harness.record import decode_watch_row  # noqa: E402

OUT = ROOT / "out" / "collisions-precision-1.26.51.01.json"

# ECS component ids (1.26.51.01, resolved via pe_map --ecs-hash).
STATE_VECTOR = 0x1B5D5238
AABB_SHAPE = 0xBAC1B3CF
ACTOR_DATA_FLAG = 0xC67426F3
DEPENETRATION = 0x256B41F6
MOVE_REQUEST = 0x4C8F7288

# Geometry: fresh arena, natural superflat ground surface y=-60 (layer block -61).
X0, X1 = 230, 246
Z0, Z1 = 230, 246
LAYER = -61
GROUND = -60.0
HOME = (242.5, GROUND, 233.5)

RESULTS: dict[str, Any] = {}


def cmd(client: ModClient, text: str, ticks: int = 4) -> None:
    client.command(text)
    client.wait_ticks(ticks)


def f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def ulp_at(value: float) -> float:
    """ULP of float32 at |value| (normal range only; our coords never hit subnormals)."""
    a = abs(float(value))
    if a == 0.0:
        return 2.0**-149
    exp = math.floor(math.log2(a))
    ulp = 2.0 ** (exp - 23)
    # Exactly representable powers of two sit at the low end of their binade, so
    # spacing below them is half: report the spacing of the value's own binade.
    return f32(ulp)


def aabb_watch() -> list[dict[str, Any]]:
    return [
        {"component_id": AABB_SHAPE, "offset": 0x00, "size": 4, "name": "box_min_x", "fmt": "f32"},
        {"component_id": AABB_SHAPE, "offset": 0x0C, "size": 4, "name": "box_max_x", "fmt": "f32"},
        {"component_id": AABB_SHAPE, "offset": 0x04, "size": 4, "name": "box_min_y", "fmt": "f32"},
        {"component_id": AABB_SHAPE, "offset": 0x10, "size": 4, "name": "box_max_y", "fmt": "f32"},
    ]


def mpd_watch() -> list[dict[str, Any]]:
    return [
        {"component_id": STATE_VECTOR, "offset": 0x18, "size": 4, "name": "mpd_x", "fmt": "f32"},
        {"component_id": STATE_VECTOR, "offset": 0x1C, "size": 4, "name": "mpd_y", "fmt": "f32"},
        {"component_id": STATE_VECTOR, "offset": 0x20, "size": 4, "name": "mpd_z", "fmt": "f32"},
        {"component_id": AABB_SHAPE, "offset": 0x04, "size": 4, "name": "box_min_y", "fmt": "f32"},
    ]


def decode_rows(rec: RecordSession) -> list[dict[str, Any]]:
    """Decode watch slots into named f32 fields; drop rows with invalid slots."""
    specs: list[dict[str, Any]] = list(rec.watch or [])
    out: list[dict[str, Any]] = []
    for sample in rec.watch_entries:
        row = decode_watch_row(sample, specs)
        if all(v is not None for v in row.values()):
            out.append(row)
    return out


def compact(rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row) if isinstance(row, dict) else {"raw": str(row)}
        out.append(item)
    return out


def read_player_components(client: ModClient) -> dict[str, Any]:
    rid = int(client.state()["runtime_id"])
    wanted = [
        ("aabb", AABB_SHAPE, 0, 32),
        ("dep", DEPENETRATION, 0, 56),
        ("req_0x30", MOVE_REQUEST, 0x30, 40),
        ("adf", ACTOR_DATA_FLAG, 0, 24),
        ("sv", STATE_VECTOR, 0, 36),
    ]
    hexes: dict[str, str] = {}
    for key, comp, _offset, length in wanted:
        try:
            reply = client.ecs_component(rid, comp, length=length)
        except Exception as error:  # component may be absent on this archetype
            hexes[key] = f"<missing: {error}>"
            continue
        hexes[key] = str(reply.get("hex", ""))
    req_hex = hexes.get("req_0x30", "")
    adf_hex = hexes.get("adf", "")
    sv_hex = hexes.get("sv", "")
    aabb_hex = hexes.get("aabb", "")
    dep_hex = hexes.get("dep", "")
    out: dict[str, Any] = {
        "runtime_id": rid,
        "raw_hex": {
            "aabb": aabb_hex,
            "dep": dep_hex,
            "req_0x30": req_hex,
            "adf": adf_hex,
            "sv": sv_hex,
        },
    }
    if not aabb_hex or aabb_hex.startswith("<missing"):
        out["aabb"] = None
    elif len(aabb_hex) >= 48:
        vals = struct.unpack("<6f", bytes.fromhex(aabb_hex)[:24])
        dims = struct.unpack("<2f", bytes.fromhex(aabb_hex)[24:32]) if len(aabb_hex) >= 64 else None
        out["aabb"] = {
            "min": list(vals[:3]),
            "max": list(vals[3:6]),
            "width_f32": f32(vals[3] - vals[0]),
            "height_f32": f32(vals[4] - vals[1]),
            "dim": list(dims) if dims else None,
        }
    if dep_hex and not dep_hex.startswith("<missing") and len(dep_hex) >= 8:
        bits = struct.unpack("<I", bytes.fromhex(dep_hex)[:4])[0]
        out["dep_bits"] = {
            "raw": bits,
            "is_always_one_way": bool(bits & 1),
            "was_penetrating_last_frame": bool(bits & 2),
            "is_stuck_in_collider": bool(bits & 4),
            "is_stuck_item": bool(bits & 8),
            "push_towards_closest_space": bool(bits & 0x10),
        }
        if len(dep_hex) >= 30:
            out["dep_min_depenetration"] = list(struct.unpack("<3f", bytes.fromhex(dep_hex)[4:16]))
        if len(dep_hex) >= 53:
            out["dep_override_engaged"] = bool(dep_hex[104:106] == "01")
    if req_hex and not req_hex.startswith("<missing") and len(req_hex) >= 40:
        req = struct.unpack("<10f", bytes.fromhex(req_hex)[:40])
        out["move_request"] = {
            "pre_collision_speed": list(req[:3]),
            "speed": list(req[3:6]),
            "max_depenetration": list(req[6:9]),
            "is_penetrating": bool(req_hex[96:98] == "01"),  # +0x54 - 0x30 = 0x24 -> byte 36
        }
    if adf_hex and not adf_hex.startswith("<missing") and len(adf_hex) >= 26:
        raw = bytes.fromhex(adf_hex)
        out["adf_flag109_push_towards_closest_space"] = bool(raw[13] & 0x20)
        out["adf_byte13"] = raw[13]
    if sv_hex and not sv_hex.startswith("<missing") and len(sv_hex) >= 36:
        sv = struct.unpack("<9f", bytes.fromhex(sv_hex)[:36])
        out["state_vector"] = {
            "mPos": list(sv[:3]),
            "mPosPrev": list(sv[3:6]),
            "mPosDelta": list(sv[6:9]),
        }
    return out


def blocks_map(
    client: ModClient, positions: list[tuple[int, int, int]]
) -> dict[tuple[int, int, int], str]:
    rows = client.blocks([list(p) for p in positions]).get("blocks", [])
    return {
        (int(r["x"]), int(r["y"]), int(r["z"])): str(r.get("name", ""))
        for r in rows
        if isinstance(r, dict)
    }


def verify_ground(client: ModClient, x: float, z: float, *, around: int = 2) -> list[str]:
    """Confirm solid natural ground under a (possibly far) position; returns block names."""
    px, pz = math.floor(x), math.floor(z)
    positions = [
        (px + dx, LAYER, pz + dz)
        for dx in range(-around, around + 1, 2)
        for dz in range(-around, around + 1, 2)
    ]
    blocks = blocks_map(client, positions)
    names: list[str] = []
    for pos in positions:
        name = blocks.get(pos, "")
        assert name and not name.endswith(":air"), f"ground missing at {pos}: {blocks}"
        names.append(name)
    return names


def fill_air(client: ModClient, y_from: int = LAYER + 1, y_to: int = LAYER + 8) -> None:
    cmd(client, f"fill {X0} {y_from} {Z0} {X1} {y_to} {Z1} air", 4)
    # self-heal the ground layer: earlier sections may have replaced/removed it
    cmd(client, f"fill {X0} {LAYER} {Z0} {X1} {LAYER} {Z1} grass_block", 4)
    blocks = blocks_map(
        client,
        [
            (X0, LAYER, Z0),
            (X1, LAYER, Z0),
            (X0, LAYER, Z1),
            (X1, LAYER, Z1),
            (X0, LAYER + 1, Z0),
            (X1, LAYER + 2, Z1),
        ],
    )
    for pos in [(X0, LAYER, Z0), (X1, LAYER, Z0), (X0, LAYER, Z1), (X1, LAYER, Z1)]:
        name = blocks.get(pos, "")
        assert name.endswith(":grass_block"), f"ground layer missing at {pos}: {name}"
    for pos in [(X0, LAYER + 1, Z0), (X1, LAYER + 2, Z1)]:
        name = blocks.get(pos, "")
        assert name.endswith(":air"), f"clear failed at {pos}: {name}"


def teleport_settle(client: ModClient, x: float, y: float, z: float, yaw: float = 0.0) -> None:
    """Teleport and settle; verifies the actual landing spot (a teleport into or
    over missing ground can leave the player parked/falling far below)."""
    settled: dict = {}
    for _attempt in range(3):
        client.teleport(x, y, z, yaw=yaw, pitch=0.0)
        client.wait_ticks(30)
        settled = settle(client, on_ground=True, input_sprint=False)
        if settled["ok"]:
            state = client.state()
            feet = float(state.get("y", 0.0)) - 1.6200101
            if abs(feet - y) < 0.75:
                return
        client.wait_ticks(10)
    raise AssertionError(
        f"teleport_settle at ({x},{y},{z}) failed 3 attempts: settled={settled}, state={client.state()}"
    )


# --------------------------------------------------------------------------- S1
def s1_baseline(client: ModClient, results: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    results["s1_baseline"] = out
    teleport_settle(client, *HOME)
    verify_ground(client, HOME[0], HOME[2])

    # standing: the sink-pop cycle keeps feet at the ground face; measure the
    # residual per-tick f32 rounding on min/max y and the width stability.
    with RecordSession(client, period=1, watch=aabb_watch()) as rec:
        client.wait_ticks(40)
    rows = decode_rows(rec)
    assert len(rows) >= 20, f"standing window too short: {len(rows)}"
    widths = [r["box_max_x"] - r["box_min_x"] for r in rows]
    heights = [r["box_max_y"] - r["box_min_y"] for r in rows]
    out["standing"] = {
        "rows": compact(rows),
        "width_first": f32(widths[0]),
        "width_last": f32(widths[-1]),
        "height_first": f32(heights[0]),
        "height_last": f32(heights[-1]),
        "width_set": sorted({f32(w) for w in widths}),
        "height_set": sorted({f32(h) for h in heights}),
        "min_y_set": sorted({f32(r["box_min_y"]) for r in rows}),
    }

    # walk east: yaw -90 (+x). Multi-phase safety: single phase, ticks then wait.
    before = f32(rows[-1]["box_min_x"])
    with RecordSession(client, period=1, watch=aabb_watch()) as rec:
        client.set_input(forward=1.0, ticks=24)
        client.wait_ticks(30)
    rows = decode_rows(rec)
    assert len(rows) >= 10, f"walk window too short: {len(rows)}"
    moved_x = max(abs(r["box_max_x"] - r["box_min_x"]) for r in rows)
    walk_entries = list(rec.entries)
    assert len(walk_entries) >= 10, "walk position rows missing"
    advance_x = f32(walk_entries[-1].x - walk_entries[0].x)
    advance_z = f32(walk_entries[-1].z - walk_entries[0].z)
    assert math.hypot(advance_x, advance_z) > 1.0, (
        f"walk did not move: dx={advance_x} dz={advance_z}"
    )
    drifts = []
    for prev, cur in itertools.pairwise(rows):
        drifts.append(
            {
                "d_width": f32(
                    (cur["box_max_x"] - cur["box_min_x"]) - (prev["box_max_x"] - prev["box_min_x"])
                ),
                "d_height": f32(
                    (cur["box_max_y"] - cur["box_min_y"]) - (prev["box_max_y"] - prev["box_min_y"])
                ),
            }
        )
    out["walk"] = {
        "rows": compact(rows),
        "min_x_before": before,
        "min_x_after": f32(rows[-1]["box_min_x"]),
        "width_first": f32(rows[0]["box_max_x"] - rows[0]["box_min_x"]),
        "width_last": f32(rows[-1]["box_max_x"] - rows[-1]["box_min_x"]),
        "height_first": f32(rows[0]["box_max_y"] - rows[0]["box_min_y"]),
        "height_last": f32(rows[-1]["box_max_y"] - rows[-1]["box_min_y"]),
        "drifts": drifts,
        "box_diagonal_max": f32(moved_x),
        "advance_x": advance_x,
        "advance_z": advance_z,
    }

    # jump arc: vertical collisions on takeoff/landing, watch height through it.
    with RecordSession(client, period=1, watch=aabb_watch()) as rec:
        client.set_input(jump=True, ticks=1)
        client.wait_ticks(40)
    rows = decode_rows(rec)
    assert len(rows) >= 20, f"jump window too short: {len(rows)}"
    heights = [f32(r["box_max_y"] - r["box_min_y"]) for r in rows]
    out["jump"] = {
        "rows": compact(rows),
        "height_set": sorted(set(heights)),
        "height_first": heights[0],
        "height_last": heights[-1],
        "min_height": min(heights),
        "max_height": max(heights),
    }
    return out


# --------------------------------------------------------------------------- S2
def s2_depenetration(client: ModClient, results: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    results["s2_depenetration"] = out
    gamemode = int(client.state().get("gamemode", 0))
    out["gamemode_before"] = gamemode
    if gamemode != 1:
        cmd(client, "gamemode creative @s", 6)
        assert int(client.state().get("gamemode", -1)) == 1, "gamemode switch to creative failed"

    # shallow embed: 1-thick wall column right at the player, all exits <= 1.0.
    # The eject can complete before the recording opens, so verify the overlap
    # first and capture the pending-delta decay whatever the phase.
    ex, ey, ez = 236.5, GROUND, 235.5
    shallow_positions = [(236, LAYER, 235), (236, LAYER + 2, 235)]
    for attempt in range(2):
        # leftover stone from a previous attempt/partial run makes the teleport
        # land inside blocks, which parks the player at y=-104; always clear first.
        cmd(client, f"fill 236 {LAYER} 235 236 {LAYER + 2} 235 air", 4)
        teleport_settle(client, ex, ey, ez)
        cmd(client, f"fill 236 {LAYER} 235 236 {LAYER + 2} 235 stone", 4)
        blocks = blocks_map(client, shallow_positions)
        for pos in shallow_positions:
            name = blocks.get(pos, "")
            assert name.endswith(":stone"), f"shallow wall missing at {pos}: {name}"
        pre = read_player_components(client)
        aabb = pre.get("aabb") or {}
        if (
            aabb.get("min")
            and abs(float(aabb["min"][1]) - GROUND) < 0.5
            and abs(float(aabb["max"][0]) - 236.8) < 0.6
        ):
            break
        if attempt == 0:
            cmd(client, f"fill 236 {LAYER} 235 236 {LAYER + 2} 235 air", 4)
            client.wait_ticks(20)
    else:
        raise AssertionError(f"shallow embed never engaged: aabb={aabb}")
    # recording pass: clear first (the verify pass left the wall), then open the
    # window before the fill so the whole eject is captured.
    cmd(client, f"fill 236 {LAYER} 235 236 {LAYER + 2} 235 air", 4)
    teleport_settle(client, ex, ey, ez)
    with RecordSession(client, period=1, watch=mpd_watch()) as rec:
        cmd(client, f"fill 236 {LAYER} 235 236 {LAYER + 2} 235 stone", 4)
        client.wait_ticks(28)
    rows = decode_rows(rec)
    assert len(rows) >= 10, f"shallow window too short: {len(rows)}"
    pre = read_player_components(client)
    post = read_player_components(client)
    displacements = [abs(float(r["mpd_z"])) + abs(float(r["mpd_x"])) for r in rows]
    out["shallow_embed"] = {
        "rows": compact(rows),
        "pre": pre,
        "post": post,
        "first_mpd": [float(rows[0]["mpd_x"]), float(rows[0]["mpd_y"]), float(rows[0]["mpd_z"])],
        "max_abs_mpd": max(displacements),
        "is_penetrating_pre": pre.get("move_request", {}).get("is_penetrating"),
        "is_penetrating_post": post.get("move_request", {}).get("is_penetrating"),
        "dep_bits_pre": pre.get("dep_bits"),
        "dep_bits_post": post.get("dep_bits"),
        "adf109_pre": pre.get("adf_flag109_push_towards_closest_space"),
        "adf109_post": post.get("adf_flag109_push_towards_closest_space"),
        "max_depenetration_post": post.get("move_request", {}).get("max_depenetration"),
        "min_depenetration_post": post.get("dep_min_depenetration"),
    }
    cmd(client, f"fill 236 {LAYER} 235 236 {LAYER + 2} 235 air", 4)
    teleport_settle(client, *HOME)

    # deep embed: solid 5x3x5 cube around the player; every axis exit > 1.0.
    # The fill is deferred and can race the settle, so verify the player really
    # is inside stone (min_y at the embed site) and retry once otherwise.
    dx_, dy_, dz_ = 232.5, GROUND, 235.5
    deep_positions = [(230, LAYER, 233), (234, LAYER + 2, 237), (232, LAYER, 235)]
    for attempt in range(2):
        cmd(client, f"fill 230 {LAYER} 233 234 {LAYER + 2} 237 air", 6)
        teleport_settle(client, dx_, dy_, dz_)
        cmd(client, f"fill 230 {LAYER} 233 234 {LAYER + 2} 237 stone", 6)
        blocks = blocks_map(client, deep_positions)
        for pos in deep_positions:
            name = blocks.get(pos, "")
            assert name.endswith(":stone"), f"deep cube missing at {pos}: {name}"
        pre = read_player_components(client)
        aabb = pre.get("aabb") or {}
        if (
            aabb.get("min")
            and abs(float(aabb["min"][1]) - GROUND) < 0.5
            and abs(float(aabb["min"][0]) - 232.2) < 0.5
        ):
            break
        if attempt == 0:
            cmd(client, f"fill 230 {LAYER} 233 234 {LAYER + 2} 237 air", 6)
            client.wait_ticks(20)
    else:
        raise AssertionError(f"deep embed never engaged: aabb={aabb}")
    # recording pass: clear first (the verify pass left the cube), then open the
    # window before the fill; every row must stay inside the cube.
    cmd(client, f"fill 230 {LAYER} 233 234 {LAYER + 2} 237 air", 6)
    client.wait_ticks(10)
    teleport_settle(client, dx_, dy_, dz_)
    with RecordSession(client, period=1, watch=mpd_watch()) as rec:
        cmd(client, f"fill 230 {LAYER} 233 234 {LAYER + 2} 237 stone", 6)
        client.wait_ticks(90)
    rows = decode_rows(rec)
    assert len(rows) >= 40, f"deep window too short: {len(rows)}"
    assert all(abs(r["box_min_y"] - GROUND) < 0.5 for r in rows), (
        f"deep embed broke mid-window: {[r['box_min_y'] for r in rows[:5]]}"
    )
    post = read_player_components(client)
    seq = [
        {
            "mpd_x": float(r["mpd_x"]),
            "mpd_y": float(r["mpd_y"]),
            "mpd_z": float(r["mpd_z"]),
            "min_y": float(r["box_min_y"]),
        }
        for r in rows
    ]
    out["deep_embed"] = {
        "rows": seq,
        "pre": pre,
        "post": post,
        "is_penetrating_pre": pre.get("move_request", {}).get("is_penetrating"),
        "is_penetrating_post": post.get("move_request", {}).get("is_penetrating"),
        "dep_bits_pre": pre.get("dep_bits"),
        "dep_bits_post": post.get("dep_bits"),
        "adf109_pre": pre.get("adf_flag109_push_towards_closest_space"),
        "adf109_post": post.get("adf_flag109_push_towards_closest_space"),
        "max_depenetration_post": post.get("move_request", {}).get("max_depenetration"),
        "min_depenetration_post": post.get("dep_min_depenetration"),
        "min_y_first": seq[0]["min_y"],
        "min_y_last": seq[-1]["min_y"],
    }
    cmd(client, f"fill 230 {LAYER} 233 234 {LAYER + 2} 237 air", 6)
    teleport_settle(client, *HOME)
    return out


# --------------------------------------------------------------------------- S3
def s3_high_coords(client: ModClient, results: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    results["s3_high_coords"] = out
    coords = [
        ("x2^10", 1024.0),
        ("x2^14", 16384.0),
        ("x2^17", 131072.0),
        ("x2^20", 1048576.0),
    ]
    for label, x in coords:
        entry: dict[str, Any] = {"x": x, "ulp_at_x": ulp_at(x), "ulp_at_width_mid": ulp_at(x + 0.3)}
        teleport_settle(client, x + 0.5, GROUND, HOME[2], yaw=-90.0)
        verify_ground(client, x + 0.5, HOME[2])
        pre = read_player_components(client)
        entry["standing_aabb"] = pre.get("aabb")

        with RecordSession(client, period=1, watch=aabb_watch()) as rec:
            client.wait_ticks(30)
        rows = decode_rows(rec)
        assert len(rows) >= 10, f"{label} standing window too short: {len(rows)}"
        widths = [f32(r["box_max_x"] - r["box_min_x"]) for r in rows]
        heights = [f32(r["box_max_y"] - r["box_min_y"]) for r in rows]
        entry["standing_width_set"] = sorted(set(widths))
        entry["standing_height_set"] = sorted(set(heights))
        entry["standing_width_first_last"] = [widths[0], widths[-1]]
        entry["standing_min_y_set"] = sorted({f32(r["box_min_y"]) for r in rows})

        with RecordSession(client, period=1, watch=aabb_watch()) as rec:
            client.set_input(forward=1.0, ticks=16)
            client.wait_ticks(24)
        rows = decode_rows(rec)
        assert len(rows) >= 8, f"{label} walk window too short: {len(rows)}"
        walk_widths = [f32(r["box_max_x"] - r["box_min_x"]) for r in rows]
        walk_heights = [f32(r["box_max_y"] - r["box_min_y"]) for r in rows]
        entry["walk_width_set"] = sorted(set(walk_widths))
        entry["walk_height_set"] = sorted(set(walk_heights))
        entry["walk_width_first_last"] = [walk_widths[0], walk_widths[-1]]
        entry["walk_rows_head"] = compact(rows[:10])
        moved = f32(rows[-1]["box_min_x"] - rows[0]["box_min_x"])
        entry["walk_min_x_advance"] = moved
        assert abs(moved) > 0.5, f"{label}: walk did not advance x ({moved})"
        post = read_player_components(client)
        entry["post_aabb"] = post.get("aabb")
        out[label] = entry
        teleport_settle(client, *HOME)
    return out


# --------------------------------------------------------------------------- S5
def s5_vertical_precision(client: ModClient, results: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    results["s5_vertical_precision"] = out
    teleport_settle(client, *HOME)

    # repeated jumps at origin: does the height random-walk across landings?
    heights: list[float] = []
    rows_all: list[dict[str, Any]] = []
    for _ in range(12):
        with RecordSession(client, period=1, watch=aabb_watch()) as rec:
            client.set_input(jump=True, ticks=1)
            client.wait_ticks(36)
        rows = decode_rows(rec)
        assert len(rows) >= 20, f"jump window too short: {len(rows)}"
        landed = [r for r in rows if f32(r["box_min_y"]) == -60.0]
        assert landed, "jump never returned to ground"
        rows_all.append(rows[-6:])
        heights.append(f32(landed[-1]["box_max_y"] - landed[-1]["box_min_y"]))
    out["jump_height_seq"] = heights
    out["jump_height_unique"] = sorted(set(heights))
    out["jump_tail_rows"] = compact([r for rows in rows_all for r in rows][:40])

    # high-Y platform: standing ULP walk at y=101 (ULP = 2^-17 * 4 = 7.6e-6? measure).
    cmd(client, "fill 228 100 230 234 100 236 stone", 6)
    blocks = blocks_map(client, [(230, 100, 232), (234, 100, 236)])
    for pos in [(230, 100, 232), (234, 100, 236)]:
        assert blocks.get(pos, "").endswith(":stone"), f"platform missing at {pos}"
    client.teleport(232.5, 101.0, 234.5, yaw=0.0, pitch=0.0)
    client.wait_ticks(30)
    settled = settle(client, on_ground=True, input_sprint=False)
    assert settled["ok"], f"high-Y settle failed: {settled}"
    with RecordSession(client, period=1, watch=aabb_watch()) as rec:
        client.wait_ticks(150)
    rows = decode_rows(rec)
    assert len(rows) >= 100, f"high-Y window too short: {len(rows)}"
    out["high_y"] = {
        "platform_y": 101.0,
        "min_y_unique": sorted({f32(r["box_min_y"]) for r in rows}),
        "max_y_unique": sorted({f32(r["box_max_y"]) for r in rows}),
        "height_unique": sorted({f32(r["box_max_y"] - r["box_min_y"]) for r in rows}),
        "rows_head": compact(rows[:20]),
        "aabb_final": read_player_components(client).get("aabb"),
    }
    cmd(client, "fill 228 100 230 234 100 236 air", 6)
    teleport_settle(client, *HOME)
    return out


# --------------------------------------------------------------------------- S4
def s4_entity_push(client: ModClient, results: dict[str, Any]) -> dict[str, Any]:
    """Confined cow (1x1 cell, no AI escape) + player teleport-overlap.

    The player side is measured per tick (mPosDelta watch); the cow side is
    polled before/after. Cow AI cannot wander inside a sealed cell.
    """
    out: dict[str, Any] = {}
    results["s4_entity_push"] = out
    # sealed cell at (238..238, -60..-58, 240): walls all around, interior one block.
    cmd(client, f"fill 237 {LAYER + 1} 239 239 {LAYER + 3} 241 stone", 4)
    cmd(client, f"fill 238 {LAYER + 1} 240 238 {LAYER + 3} 240 air", 4)
    wall_positions = [
        (237, LAYER + 1, 240),
        (239, LAYER + 1, 240),
        (238, LAYER + 1, 239),
        (238, LAYER + 1, 241),
        (237, LAYER + 3, 240),
        (239, LAYER + 3, 241),
    ]
    blocks = blocks_map(client, [*wall_positions, (238, LAYER + 1, 240)])
    for pos in wall_positions:
        assert blocks.get(pos, "").endswith(":stone"), f"cell wall missing at {pos}"
    assert blocks.get((238, LAYER + 1, 240), "").endswith(":air"), "cell interior not air"

    cmd(client, "kill @e[type=cow]", 4)
    cmd(client, "summon cow 238.5 -60 240.5", 4)
    cows = [
        e for e in client.entities().get("entities", []) if str(e.get("type", "")).endswith(":cow")
    ]
    assert len(cows) == 1, f"expected 1 cow, got {len(cows)}"
    cow_id = int(cows[0]["runtime_id"])
    cow0 = client.entity(cow_id)
    out["cow_initial"] = {
        "id": cow_id,
        "x": cow0.get("x"),
        "z": cow0.get("z"),
        "aabb": cow0.get("aabb"),
    }

    # player overlaps the confined cow, standing still (no input) -> the entity
    # push should push the player out of the cow while the cow stays cell-bound.
    teleport_settle(client, 238.5, GROUND, 240.5, yaw=0.0)
    with RecordSession(client, period=1, watch=mpd_watch()) as rec:
        client.wait_ticks(50)
    rows = decode_rows(rec)
    assert len(rows) >= 30, f"overlap window too short: {len(rows)}"
    out["player_overlap_rows"] = compact(rows)
    out["player_overlap_mpd_mag"] = [
        round(math.hypot(float(r["mpd_x"]), float(r["mpd_z"])), 6) for r in rows
    ]
    cow1 = client.entity(cow_id)
    out["cow_after_overlap"] = {"x": cow1.get("x"), "z": cow1.get("z"), "aabb": cow1.get("aabb")}
    player_after = read_player_components(client)
    out["player_after_aabb"] = player_after.get("aabb")
    teleport_settle(client, *HOME)

    # control: same measurement with the cell empty is already covered by
    # player_wall_contact below (plain wall contact, no entity).
    with RecordSession(client, period=1, watch=mpd_watch()) as rec:
        client.set_input(forward=1.0, ticks=18)
        client.wait_ticks(26)
    rows = decode_rows(rec)
    assert len(rows) >= 10, f"wall contact window too short: {len(rows)}"
    out["player_wall_contact"] = {
        "rows": compact(rows),
        "mpd_z_first_last": [float(rows[0]["mpd_z"]), float(rows[-1]["mpd_z"])],
    }

    # cow-vs-cow impulse: two heavily overlapped cows separate within a few
    # ticks (both are server movement-tick entities); the local player neither
    # pushes nor is pushed (walk-through test below).
    cmd(client, "kill @e[type=cow]", 4)
    client.teleport(244.5, GROUND, 240.5, yaw=0.0, pitch=0.0)
    client.wait_ticks(30)
    assert settle(client, on_ground=True, input_sprint=False)["ok"], "pair settle failed"
    cmd(client, "summon cow 244.5 -60 240.5", 1)
    cmd(client, "summon cow 244.55 -60 240.5", 1)
    cows = [
        e for e in client.entities().get("entities", []) if str(e.get("type", "")).endswith(":cow")
    ]
    assert len(cows) == 2, f"pair: expected 2 cows, got {len(cows)}"
    pair_ids = [int(e["runtime_id"]) for e in cows]
    pair_samples = []
    gaps = []
    for _ in range(18):
        client.wait_ticks(2)
        a = client.entity(pair_ids[0])
        b = client.entity(pair_ids[1])
        pair_samples.append([a.get("x"), b.get("x")])
        gaps = [abs(float(bx) - float(ax)) for ax, bx in pair_samples]
        if gaps[-1] > 1.0:
            break
    out["pair_impulse_samples"] = pair_samples
    out["pair_gap_seq"] = gaps
    assert gaps and gaps[-1] > 1.0, f"pair never separated: {gaps}"
    cmd(client, "tp @e[type=cow] 400 -100 400", 3)
    cmd(client, "kill @e[type=cow]", 3)

    # walk-through: a moving local player passes straight through a standing cow
    # (entity boxes do not block client-authoritative motion; no mutual push).
    client.teleport(246.5, GROUND, 240.5, yaw=0.0, pitch=0.0)
    client.wait_ticks(30)
    assert settle(client, on_ground=True, input_sprint=False)["ok"], "walkthrough settle failed"
    cmd(client, "summon cow 246.5 -60 240.5", 8)
    cows = [
        e for e in client.entities().get("entities", []) if str(e.get("type", "")).endswith(":cow")
    ]
    assert len(cows) == 1, f"walkthrough: expected 1 cow, got {len(cows)}"
    wid = int(cows[0]["runtime_id"])
    cow_walk0 = client.entity(wid)
    with RecordSession(client, period=1, watch=mpd_watch()) as rec:
        client.set_input(forward=1.0, ticks=20)
        client.wait_ticks(28)
    cow_walk1 = client.entity(wid)
    rows = decode_rows(rec)
    assert len(rows) >= 12, "walkthrough rows missing"
    entries = list(rec.entries)
    assert entries, "walkthrough position rows missing"
    cow_x = float(cow_walk0.get("x", 0.0))
    through = [e for e in entries if abs(e.x - cow_x) < 0.8]
    assert through, "walkthrough never crossed the cow position"
    out["walk_through"] = {
        "cow_x_before": cow_walk0.get("x"),
        "cow_x_after": cow_walk1.get("x"),
        "cow_moved": abs(float(cow_walk1.get("x", 0.0)) - float(cow_walk0.get("x", 0.0))) > 1e-4,
        "player_crossed": True,
    }
    cmd(client, "tp @e[type=cow] 400 -100 400", 3)
    cmd(client, "kill @e[type=cow]", 3)
    left = [
        e for e in client.entities().get("entities", []) if str(e.get("type", "")).endswith(":cow")
    ]
    assert not left, f"cows not cleaned up: {left}"
    return out


# --------------------------------------------------------------------------- S6
def s6_adf_writer(client: ModClient, results: dict[str, Any]) -> dict[str, Any]:
    """Catch the writer of ActorDataFlag byte13 (flag 109) during a stuck transition."""
    out: dict[str, Any] = {}
    results["s6_adf_writer"] = out
    gamemode = int(client.state().get("gamemode", 0))
    if gamemode != 1:
        cmd(client, "gamemode creative @s", 6)
    rid = int(client.state()["runtime_id"])
    adf = client.ecs_component(rid, ACTOR_DATA_FLAG, length=24)
    addr = int(adf.get("address", 0))
    assert addr, f"no ActorDataFlag address: {adf}"
    out["adf_addr"] = addr
    out["adf_before"] = adf.get("hex")
    watch = client.watch_set(addr + 13, size=1, mode="rw", thread="both", tag="adf109", depth=12)
    wid = int(watch.get("id"))
    try:
        # embed deep (all exits > 1.0) to drive the stuck transition.
        teleport_settle(client, 232.5, GROUND, 235.5)
        cmd(client, f"fill 230 {LAYER} 233 234 {LAYER + 2} 237 stone", 6)
        blocks = blocks_map(client, [(232, LAYER, 235)])
        assert blocks.get((232, LAYER, 235), "").endswith(":stone"), "cube missing"
        client.wait_ticks(60)
        out["adf_during"] = client.ecs_component(rid, ACTOR_DATA_FLAG, length=24).get("hex")
        out["dep_during"] = client.ecs_component(rid, DEPENETRATION, length=56).get("hex")
        drained = client.watch_drain(since=0, limit=200)
        hits = drained.get("hits") or drained.get("rows") or []
        out["watch_hits_head"] = hits[:20]
        out["watch_hit_count"] = len(hits)
        cmd(client, f"fill 230 {LAYER} 233 234 {LAYER + 2} 237 air", 6)
        teleport_settle(client, *HOME)
        client.wait_ticks(20)
        out["adf_after"] = client.ecs_component(rid, ACTOR_DATA_FLAG, length=24).get("hex")
    finally:
        client.watch_clear(watch_id=wid)
    return out


def main() -> int:
    results: dict[str, Any] = {}
    try:
        with ModClient() as client:
            client.wait_ticks(20)
            state = client.state()
            results["setup"] = {
                "tick": state.get("tick"),
                "pos": [state.get("x"), state.get("y"), state.get("z")],
                "gamemode": state.get("gamemode"),
            }
            with LabSession(client):
                fill_air(client)
                s1_baseline(client, results)
                s2_depenetration(client, results)
                s3_high_coords(client, results)
                s4_entity_push(client, results)
                s5_vertical_precision(client, results)
                s6_adf_writer(client, results)
                results["final_components"] = read_player_components(client)
                # cleanup: restore open arena, keep the world usable.
                cmd(client, f"fill {X0} {LAYER + 1} {Z0} {X1} {LAYER + 8} {Z1} air", 4)
                teleport_settle(client, *HOME)
    except Exception:
        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"partial results saved: {OUT}")
        raise
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    brief = {k: (v if k in ("setup",) else "...") for k, v in results.items()}
    print(json.dumps(brief, ensure_ascii=False))
    print(f"Saved: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
