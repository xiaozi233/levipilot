"""Live verification of MCBE 1.26.51.01 climbable block mechanics."""

from __future__ import annotations

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
from driver.harness.lab import RecordSession, settle  # noqa: E402

OUT_DIR = ROOT / "out" / "climb-blocks-1.26.51.01"

ACTOR_DATA_FLAG_ID = 0xC67426F3
STATE_VECTOR_ID = 0x1B5D5238
BLOCK_SLOWDOWN_ID = 0xF6091F58

# ActorDataFlag bit indices (see mc/world/actor/ActorFlags.h)
FLAG_WALLCLIMBING = 18
FLAG_CANCLIMB = 19
FLAG_IN_SCAFFOLDING = 69
FLAG_OVER_SCAFFOLDING = 70
FLAG_DESCEND_THROUGH = 71
FLAG_IN_ASCENDABLE = 99
FLAG_OVER_DESCENDABLE = 100

WATCH_SLOTS = [
    {"component_id": ACTOR_DATA_FLAG_ID, "offset": 0, "size": 8},
    {"component_id": ACTOR_DATA_FLAG_ID, "offset": 8, "size": 8},
    {"component_id": STATE_VECTOR_ID, "offset": 0x1C, "size": 4},
    {"component_id": BLOCK_SLOWDOWN_ID, "offset": 4, "size": 4},
]


def _u32_to_f32(val: int) -> float:
    return float(struct.unpack("<f", struct.pack("<I", val & 0xFFFFFFFF))[0])


def _flag_bit(lo: int, hi: int, bit: int) -> bool:
    word = lo if bit < 64 else hi
    return bool((word >> (bit % 64)) & 1)


def _decode_rows(rec: RecordSession) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for e, w in zip(rec.entries, rec.watch_entries, strict=True):
        lo = w.values[0] if w.valid(0) else 0
        hi = w.values[1] if w.valid(1) else 0
        mpd_y = _u32_to_f32(w.values[2]) if w.valid(2) else math.nan
        slow_y = round(_u32_to_f32(w.values[3]), 6) if w.valid(3) else None
        rows.append(
            {
                "tick": e.tick,
                "x": round(e.x, 6),
                "feet_y": round(e.feet_y, 6),
                "z": round(e.z, 6),
                "vx": round(e.vx, 6),
                "vy": round(e.vy, 6),
                "vz": round(e.vz, 6),
                "horiz": round(math.hypot(e.vx, e.vz), 6),
                "mpd_y": round(mpd_y, 6),
                "slow_y": slow_y,
                "on_ground": e.on_ground,
                "sneak": e.sneaking,
                "sprint": e.sprinting,
                "in_scaf": _flag_bit(lo, hi, FLAG_IN_SCAFFOLDING),
                "over_scaf": _flag_bit(lo, hi, FLAG_OVER_SCAFFOLDING),
                "descend_thru": _flag_bit(lo, hi, FLAG_DESCEND_THROUGH),
                "in_asc": _flag_bit(lo, hi, FLAG_IN_ASCENDABLE),
                "over_desc": _flag_bit(lo, hi, FLAG_OVER_DESCENDABLE),
            }
        )
    return rows


def read_flags_batch(client: ModClient) -> dict[str, Any]:
    res = client.ecs_read_batch(
        [
            {"component_id": ACTOR_DATA_FLAG_ID, "offset": 0, "len": 16},
            {"component_id": STATE_VECTOR_ID, "offset": 0x18, "len": 12},
        ]
    )
    items = res["items"]
    adf = bytes.fromhex(items[0]["hex"])
    sv = bytes.fromhex(items[1]["hex"])
    lo, hi = struct.unpack("<QQ", adf)
    dx, dy, dz = struct.unpack("<fff", sv)
    return {
        "tick": res["tick"],
        "wallclimbing": _flag_bit(lo, hi, FLAG_WALLCLIMBING),
        "canclimb": _flag_bit(lo, hi, FLAG_CANCLIMB),
        "in_scaffolding": _flag_bit(lo, hi, FLAG_IN_SCAFFOLDING),
        "over_scaffolding": _flag_bit(lo, hi, FLAG_OVER_SCAFFOLDING),
        "descend_through_block": _flag_bit(lo, hi, FLAG_DESCEND_THROUGH),
        "in_ascendable_block": _flag_bit(lo, hi, FLAG_IN_ASCENDABLE),
        "over_descendable_block": _flag_bit(lo, hi, FLAG_OVER_DESCENDABLE),
        "mpd": [round(dx, 6), round(dy, 6), round(dz, 6)],
    }


def cmd_wait(client: ModClient, command: str, ticks: int = 3) -> None:
    client.command(command)
    client.wait_ticks(ticks)


def prep_air_and_tp(
    client: ModClient, x: float, y: float, z: float, *, yaw: float = 0.0, pitch: float = 0.0
) -> None:
    client.set_input()
    client.teleport(x, y, z, yaw=yaw, pitch=pitch)
    client.wait_ticks(4)


def run_all(client: ModClient) -> dict[str, Any]:
    results: dict[str, Any] = {}

    # Clear test arena: stone floor at y=-57, air at y=-56..-38 across x=420..478, z=420..445
    cmd_wait(client, "fill 420 -57 420 478 -57 445 stone", ticks=4)
    cmd_wait(client, "fill 420 -56 420 478 -36 445 air", ticks=4)
    client.clear_inventory()
    client.wait_ticks(2)

    # Set up columns (walls at z=426, climbable at z=425, y=-56..-44)
    # 1. Ladder at x=424 (attached to z=426, faces -Z: facing_direction=2)
    cmd_wait(client, "fill 424 -56 426 424 -43 426 stone", ticks=2)
    cmd_wait(client, 'fill 424 -56 425 424 -44 425 ladder ["facing_direction"=2]', ticks=3)

    # 2. Vine at x=436 (against stone pillar behind at z=426; check surviving vine_direction_bits)
    cmd_wait(client, "fill 436 -56 426 436 -43 426 stone", ticks=2)
    vine_dir_used = None
    for bits in (1, 4, 2, 8, 15):
        cmd_wait(client, f'setblock 436 -55 425 vine ["vine_direction_bits"={bits}]', ticks=4)
        blk = str(client.blocks([[436, -55, 425]])["blocks"][0].get("name", ""))
        if "vine" in blk:
            vine_dir_used = bits
            break
    if vine_dir_used is None:
        vine_dir_used = 1
    cmd_wait(
        client,
        f'fill 436 -56 425 436 -44 425 vine ["vine_direction_bits"={vine_dir_used}]',
        ticks=3,
    )

    # 3. Twisting vines at x=440 (self-supporting upward from stone floor y=-57)
    cmd_wait(client, "fill 440 -56 425 440 -44 425 twisting_vines", ticks=3)

    # 4. Weeping vines at x=443 (hangs downward from stone ceiling at y=-43)
    cmd_wait(client, "setblock 443 -43 425 stone", ticks=2)
    cmd_wait(client, "fill 443 -56 425 443 -44 425 weeping_vines", ticks=3)

    # 5. Cave vines at x=446 (hangs downward from stone ceiling at y=-43)
    cmd_wait(client, "setblock 446 -43 425 stone", ticks=2)
    cmd_wait(client, "fill 446 -56 425 446 -44 425 cave_vines", ticks=3)

    # 6. Open trapdoor above 3-block ladder at x=430
    cmd_wait(client, "fill 430 -56 426 430 -51 426 stone", ticks=2)
    cmd_wait(client, 'fill 430 -56 425 430 -54 425 ladder ["facing_direction"=2]', ticks=2)
    trap_dir_used = 2
    for d in (0, 1, 2, 3):
        cmd_wait(
            client,
            f'setblock 430 -53 425 trapdoor ["direction"={d},"open_bit"=true,"upside_down_bit"=false]',
            ticks=2,
        )
        shapes = client.block_shapes([(430, -53, 425)], entity_mode="none")["blocks"][0].get(
            "shapes", []
        )
        if shapes and shapes[0]["min"][2] > 0.7:
            trap_dir_used = d
            break
    cmd_wait(
        client,
        f'fill 430 -53 425 430 -51 425 trapdoor ["direction"={trap_dir_used},"open_bit"=true,"upside_down_bit"=false]',
        ticks=3,
    )

    # 7. Scaffolding: supported tower at x=452, y=-56..-49 + overhanging block at x=453, y=-49 (air below)
    cmd_wait(client, "fill 452 -56 425 452 -49 425 scaffolding", ticks=3)
    cmd_wait(client, "setblock 453 -49 425 scaffolding", ticks=3)

    # 8. Powder snow column at x=460, y=-56..-49 + stone wall behind at z=426
    cmd_wait(client, "fill 460 -56 426 460 -47 426 stone", ticks=2)
    cmd_wait(client, "fill 460 -56 425 460 -49 425 powder_snow", ticks=3)

    results["setup"] = {
        "ladder": client.blocks([[424, -55, 425]])["blocks"][0].get("name"),
        "vine": client.blocks([[436, -55, 425]])["blocks"][0].get("name"),
        "vine_dir_used": vine_dir_used,
        "twisting_vines": client.blocks([[440, -55, 425]])["blocks"][0].get("name"),
        "weeping_vines": client.blocks([[443, -55, 425]])["blocks"][0].get("name"),
        "cave_vines": client.blocks([[446, -55, 425]])["blocks"][0].get("name"),
        "trapdoor": client.blocks([[430, -53, 425]])["blocks"][0].get("name"),
        "trap_dir_used": trap_dir_used,
        "trapdoor_shape": client.block_shapes([(430, -53, 425)], entity_mode="none")["blocks"][0],
        "scaffolding_top": client.blocks([[452, -49, 425]])["blocks"][0].get("name"),
        "scaffolding_overhang": client.blocks([[453, -49, 425]])["blocks"][0].get("name"),
        "powder_snow": client.blocks([[460, -55, 425]])["blocks"][0].get("name"),
    }

    # --- Test 1: Ladder Space-climb + release ---
    prep_air_and_tp(client, 424.5, -55.0, 425.3, yaw=0.0, pitch=0.0)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.wait_ticks(2)
        client.set_input(jump=True, ticks=16)
        client.wait_ticks(16)
        client.set_input()
        client.wait_ticks(6)
    rows = _decode_rows(rec)
    results["ladder_space_climb"] = rows

    # --- Test 2: Ladder Auto-climb (W into wall, no Space) + release ---
    prep_air_and_tp(client, 424.5, -55.0, 425.3, yaw=0.0, pitch=0.0)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.wait_ticks(2)
        client.set_input(forward=1.0, ticks=18)
        client.wait_ticks(18)
        client.set_input()
        client.wait_ticks(6)
    results["ladder_auto_climb"] = _decode_rows(rec)

    # --- Test 3: Ladder passive slide down (no keys) ---
    prep_air_and_tp(client, 424.5, -47.0, 425.3, yaw=0.0, pitch=0.0)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.wait_ticks(16)
    results["ladder_passive_slide"] = _decode_rows(rec)

    # --- Test 4: Ladder sneak-halt & sneak while rising ---
    prep_air_and_tp(client, 424.5, -52.0, 425.3, yaw=0.0, pitch=0.0)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.set_input(sneak=True, ticks=10)
        client.wait_ticks(12)
        client.set_input()
    results["ladder_sneak_halt"] = _decode_rows(rec)

    prep_air_and_tp(client, 424.5, -55.0, 425.3, yaw=0.0, pitch=0.0)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.set_input(jump=True, ticks=8)
        client.wait_ticks(8)
        # Hold sneak right after climbing upward (test halt only after mPosDelta.y < 0)
        client.set_input(sneak=True, ticks=10)
        client.wait_ticks(10)
        client.set_input()
    results["ladder_sneak_after_rise"] = _decode_rows(rec)

    # --- Test 5: Vine sprint-through (horizontal clamp test) + all 4 vines ---
    # 5a: Sprint along +X (yaw=-90) at y=-56 through vine at (436, -56/-55, 425)
    prep_air_and_tp(client, 433.5, -56.0, 425.4, yaw=-90.0, pitch=0.0)
    st_settle = settle(client, on_ground=True, input_sprint=False)
    assert st_settle["ok"], st_settle
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.set_input(forward=1.0, sprint=True, ticks=22)
        client.wait_ticks(24)
        client.set_input()
    results["vine_sprint_through"] = _decode_rows(rec)

    # Clear sprint latch before normal tests
    settle(client, on_ground=None, still=None, input_sprint=False, clear_input=True)

    vine_variants: dict[str, Any] = {}
    for label, vx in (
        ("vine", 436.5),
        ("twisting_vines", 440.5),
        ("weeping_vines", 443.5),
        ("cave_vines", 446.5),
    ):
        prep_air_and_tp(client, vx, -54.0, 425.4, yaw=0.0, pitch=0.0)
        with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
            client.set_input(jump=True, ticks=8)
            client.wait_ticks(9)
            client.set_input(ticks=8)  # passive slide
            client.wait_ticks(9)
            client.set_input(sneak=True, ticks=6)
            client.wait_ticks(7)
            client.set_input()
        vine_variants[label] = _decode_rows(rec)
    results["vine_variants"] = vine_variants

    # --- Test 6: Ladder single-point check (center outside at x=423.8 vs inside at x=424.1) ---
    # Ladder is at x=[424..425]. Player width=0.6 (half=0.3).
    # At x=423.85: AABB x=[423.55..424.15] OVERLAPS x=424, but center floor(423.85)=423 (OUTSIDE).
    # At x=424.10: AABB x=[423.80..424.40] OVERLAPS x=424, and center floor(424.10)=424 (INSIDE).
    prep_air_and_tp(client, 423.85, -48.0, 425.4, yaw=0.0, pitch=0.0)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.set_input(jump=True, ticks=10)
        client.wait_ticks(11)
        client.set_input()
    results["ladder_center_outside"] = _decode_rows(rec)

    prep_air_and_tp(client, 424.10, -48.0, 425.4, yaw=0.0, pitch=0.0)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.set_input(jump=True, ticks=10)
        client.wait_ticks(11)
        client.set_input()
    results["ladder_center_inside"] = _decode_rows(rec)

    # --- Test 7: Open trapdoor above ladder ---
    # Center at z=425.5 so AABB z=[425.2..425.8] clears open trapdoors on both -Z and +Z faces.
    client.set_input()
    client.teleport(430.5, -52.8, 425.5, yaw=0.0, pitch=0.0)
    client.wait_ticks(1)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.set_input(jump=True, ticks=10)
        client.wait_ticks(11)
        client.set_input()
    results["open_trapdoor_above_ladder"] = _decode_rows(rec)

    # Also test climbing up ladder from -55 into open trapdoor at -53
    prep_air_and_tp(client, 430.5, -55.0, 425.5, yaw=0.0, pitch=0.0)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.set_input(jump=True, ticks=16)
        client.wait_ticks(17)
        client.set_input()
    results["open_trapdoor_climb_from_ladder"] = _decode_rows(rec)

    # --- Test 8: Supported Scaffolding ---
    # 8a: Top stand
    prep_air_and_tp(client, 452.5, -48.0, 425.5, yaw=0.0, pitch=0.0)
    st_top = settle(client, on_ground=True)
    fl_top = read_flags_batch(client)

    # 8b: Sneak descend through supported scaffolding (top y=-48.0 down to -55)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.set_input(sneak=True, ticks=20)
        client.wait_ticks(22)
        client.set_input()
    rows_desc = _decode_rows(rec)

    # 8c: Space ascend inside supported scaffolding + 1st release tick
    prep_air_and_tp(client, 452.5, -55.0, 425.5, yaw=0.0, pitch=0.0)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.wait_ticks(2)
        client.set_input(jump=True, ticks=18)
        client.wait_ticks(18)
        client.set_input()
        client.wait_ticks(6)
    rows_asc = _decode_rows(rec)

    # 8d: Scaffolding AABB footprint check: center outside (x=451.80 -> floor=451, AABB max.x=452.10 overlaps x=452!)
    # Put stone under 451 at -57 so floor is solid? Better: stand in scaffolding edge in mid-air at x=451.80, y=-54.0!
    prep_air_and_tp(client, 451.80, -54.0, 425.5, yaw=0.0, pitch=0.0)
    fl_edge = read_flags_batch(client)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.set_input(jump=True, ticks=10)
        client.wait_ticks(11)
        client.set_input()
    rows_edge = _decode_rows(rec)

    results["scaffolding_supported"] = {
        "top_state": {
            "feet_y": st_top["state"]["feet_y"],
            "on_ground": st_top["state"]["on_ground"],
            "flags": fl_top,
        },
        "sneak_descend": rows_desc,
        "space_ascend": rows_asc,
        "edge_footprint_flags": fl_edge,
        "edge_footprint_space": rows_edge,
    }

    # --- Test 9: Overhanging Scaffolding (air below at (453, -50, 425)) ---
    # 9a: Sneak on top of overhanging scaffolding (top is y=-48.0, block at y=-49)
    prep_air_and_tp(client, 453.5, -48.0, 425.5, yaw=0.0, pitch=0.0)
    fl_oh_top = read_flags_batch(client)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.set_input(sneak=True, ticks=12)
        client.wait_ticks(13)
        client.set_input()
    rows_oh_top = _decode_rows(rec)

    # 9b: Space inside overhanging scaffolding (y=-48.6 inside y=-49 block; do not wait 4t before Space!)
    client.set_input()
    client.teleport(453.5, -48.6, 425.5, yaw=0.0, pitch=0.0)
    client.wait_ticks(1)
    fl_oh_in = read_flags_batch(client)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.set_input(jump=True, ticks=10)
        client.wait_ticks(11)
        client.set_input()
    rows_oh_in = _decode_rows(rec)

    results["scaffolding_overhanging"] = {
        "top_flags": fl_oh_top,
        "top_sneak": rows_oh_top,
        "inside_flags": fl_oh_in,
        "inside_space": rows_oh_in,
    }

    # --- Test 10: Powder Snow with Leather Boots vs Bare Feet ---
    client.set_item(0, "minecraft:leather_boots", count=1)
    client.equip("feet", slot=0)
    client.wait_ticks(4)
    armor_after = client.inventory().get("armor", [])

    # 10a: Powder snow top stand + sneak descend (y=-48.0 down through powder snow at -49..-56)
    prep_air_and_tp(client, 460.5, -48.0, 425.4, yaw=0.0, pitch=0.0)
    st_ps_top = settle(client, on_ground=True)
    fl_ps_top = read_flags_batch(client)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.set_input(sneak=True, ticks=18)
        client.wait_ticks(20)
        client.set_input()
    rows_ps_desc = _decode_rows(rec)

    # 10b: Inside powder snow + Space (no wall push) -> check +0.15 vs +0.20!
    prep_air_and_tp(client, 460.5, -55.0, 425.3, yaw=0.0, pitch=0.0)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.wait_ticks(2)
        client.set_input(jump=True, ticks=16)
        client.wait_ticks(17)
        client.set_input()
        client.wait_ticks(5)
    rows_ps_space = _decode_rows(rec)

    # 10c: Inside powder snow + W into wall z=426 (no Space) -> check AutoClimb +0.20!
    prep_air_and_tp(client, 460.5, -55.0, 425.3, yaw=0.0, pitch=0.0)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.wait_ticks(2)
        client.set_input(forward=1.0, ticks=18)
        client.wait_ticks(19)
        client.set_input()
        client.wait_ticks(5)
    rows_ps_auto = _decode_rows(rec)

    # 10d: Bare feet inside powder snow (wait 8t after clear_inventory + verify armor empty)
    client.clear_inventory()
    client.wait_ticks(8)
    armor_cleared = client.inventory().get("armor", [])
    prep_air_and_tp(client, 460.5, -51.0, 425.4, yaw=0.0, pitch=0.0)
    fl_ps_bare = read_flags_batch(client)
    with RecordSession(client, period=1, watch=WATCH_SLOTS) as rec:
        client.set_input(jump=True, ticks=14)
        client.wait_ticks(16)
        client.set_input()
    rows_ps_bare = _decode_rows(rec)

    results["powder_snow"] = {
        "armor_equipped": armor_after,
        "armor_cleared": armor_cleared,
        "top_state": {
            "feet_y": st_ps_top["state"]["feet_y"],
            "on_ground": st_ps_top["state"]["on_ground"],
            "flags": fl_ps_top,
        },
        "sneak_descend": rows_ps_desc,
        "space_ascend": rows_ps_space,
        "auto_climb": rows_ps_auto,
        "bare_feet_flags": fl_ps_bare,
        "bare_feet_drop": rows_ps_bare,
    }

    # --- Test 11: Fall damage reset (survival mode, ensure fallDamage=true during test) ---
    def heal_and_clear() -> None:
        cmd_wait(client, "effect @s instant_health 1 10 true", ticks=2)
        cmd_wait(client, "effect @s clear", ticks=2)

    cmd_wait(client, "gamerule fallDamage true", ticks=3)
    cmd_wait(client, "gamemode survival @s", ticks=4)
    gm_during_t11 = int(client.state().get("gamemode", -1))

    # 11_ctrl: Control drop 16 blocks onto solid stone floor (y=-41.0 -> y=-57.0)
    heal_and_clear()
    prep_air_and_tp(client, 466.5, -41.0, 425.5, yaw=0.0, pitch=0.0)
    client.wait_ticks(28)
    st_stone = client.state()

    # 11a: Fall onto ladder column (x=424, y=-48 -> ladder top is -44)
    heal_and_clear()
    prep_air_and_tp(client, 424.5, -41.0, 425.3, yaw=0.0, pitch=0.0)
    client.wait_ticks(28)
    st_ladder = client.state()

    # 11b: Fall 9 blocks onto scaffolding WITHOUT sneak vs WITH sneak
    # Place 6-tall tower at 468, -56..-51 (top=-50)
    cmd_wait(client, "fill 468 -56 425 468 -51 425 scaffolding", ticks=2)
    heal_and_clear()
    prep_air_and_tp(client, 468.5, -41.0, 425.5, yaw=0.0, pitch=0.0)
    client.wait_ticks(28)
    st_scaf_nosneak = client.state()

    heal_and_clear()
    prep_air_and_tp(client, 468.5, -41.0, 425.5, yaw=0.0, pitch=0.0)
    client.set_input(sneak=True, ticks=30)
    client.wait_ticks(32)
    client.set_input()
    st_scaf_sneak = client.state()

    # 11c: Fall 9 blocks onto powder snow WITHOUT boots vs WITH leather boots
    heal_and_clear()
    prep_air_and_tp(client, 460.5, -39.0, 425.4, yaw=0.0, pitch=0.0)
    client.wait_ticks(30)
    st_ps_bare = client.state()

    client.set_item(0, "minecraft:leather_boots", count=1)
    client.equip("feet", slot=0)
    client.wait_ticks(4)
    heal_and_clear()
    prep_air_and_tp(client, 460.5, -39.0, 425.4, yaw=0.0, pitch=0.0)
    client.wait_ticks(28)
    st_ps_boots = client.state()

    cmd_wait(client, "gamemode creative @s", ticks=2)
    results["fall_damage"] = {
        "gamemode_in_test": gm_during_t11,
        "stone_control_drop_health": st_stone["health"],
        "stone_control_drop_y": st_stone["feet_y"],
        "ladder_drop_health": st_ladder["health"],
        "ladder_drop_y": st_ladder["feet_y"],
        "scaffolding_drop_no_sneak_health": st_scaf_nosneak["health"],
        "scaffolding_drop_no_sneak_y": st_scaf_nosneak["feet_y"],
        "scaffolding_drop_sneak_health": st_scaf_sneak["health"],
        "scaffolding_drop_sneak_y": st_scaf_sneak["feet_y"],
        "powder_snow_bare_drop_health": st_ps_bare["health"],
        "powder_snow_bare_drop_y": st_ps_bare["feet_y"],
        "powder_snow_boots_drop_health": st_ps_boots["health"],
        "powder_snow_boots_drop_y": st_ps_boots["feet_y"],
    }

    return results


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with ModClient(timeout=30.0).connect() as client:
        client.wait_ready(timeout=30.0, require_world=True)
        orig_fc = client.focus_cfg()
        orig = client.state()
        orig_gm = int(orig.get("gamemode", 1))
        fd_query = client.command("gamerule falldamage")
        client.wait_ticks(3)
        _chat_before = client.chat_history(limit=4)
        restore_fd_false = "false" in str(_chat_before).lower()
        client.focus_cfg(suppress_cursor_grab=True, suppress_focus_pause=True)
        try:
            results = run_all(client)
            results["fall_damage_gamerule_orig"] = {
                "cmd_resp": fd_query,
                "chat_before": _chat_before,
            }
        finally:
            client.set_input()
            client.clear_inventory()
            if restore_fd_false:
                cmd_wait(client, "gamerule falldamage false", ticks=2)
            cmd_wait(client, f"gamemode {orig_gm} @s", ticks=2)
            cmd_wait(client, "fill 420 -57 420 478 -36 445 air", ticks=3)
            client.teleport(
                orig["feet_x"],
                orig["feet_y"],
                orig["feet_z"],
                yaw=orig["yaw"],
                pitch=orig["pitch"],
            )
            client.focus_cfg(
                background_guard=bool(orig_fc.get("background_guard", True)),
                suppress_cursor_grab=bool(orig_fc.get("suppress_cursor_grab", False)),
                suppress_focus_pause=bool(orig_fc.get("suppress_focus_pause", False)),
            )

    out_path = OUT_DIR / "live-results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved live results to {out_path}")
    print("Setup:", json.dumps(results["setup"], ensure_ascii=False))
    print("Fall damage:", json.dumps(results["fall_damage"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
