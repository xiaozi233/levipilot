"""Live verification of MCBE 1.26.51.01 cobweb & slowing block mechanics."""

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
from driver.harness.errors import ModPipeError  # noqa: E402
from driver.harness.lab import (  # noqa: E402
    Builder,
    LabSession,
    RecordSession,
    clear_armor_and_wait,
    double_space_fly,
    heal_clean,
    settle,
)

OUT_PATH = ROOT / "out" / "slowing-blocks-1.26.51.01.json"

STATE_VECTOR_ID = 0x1B5D5238
BLOCK_SLOWDOWN_ID = 0xF6091F58
FREEZING_ID = 0x47E62980

WATCH_SLOW_MPD = [
    {"component_id": BLOCK_SLOWDOWN_ID, "offset": 0, "size": 4, "name": "slow_x", "fmt": "f32"},
    {"component_id": BLOCK_SLOWDOWN_ID, "offset": 4, "size": 4, "name": "slow_y", "fmt": "f32"},
    {"component_id": STATE_VECTOR_ID, "offset": 0x18, "size": 4, "name": "mpd_x", "fmt": "f32"},
    {"component_id": STATE_VECTOR_ID, "offset": 0x1C, "size": 4, "name": "mpd_y", "fmt": "f32"},
]

WATCH_FREEZE = [
    {"component_id": FREEZING_ID, "offset": 0, "size": 4, "name": "freeze_p", "fmt": "f32"},
    {"component_id": BLOCK_SLOWDOWN_ID, "offset": 0, "size": 4, "name": "slow_x", "fmt": "f32"},
    {"component_id": STATE_VECTOR_ID, "offset": 0x18, "size": 4, "name": "mpd_x", "fmt": "f32"},
    {"component_id": STATE_VECTOR_ID, "offset": 0x1C, "size": 4, "name": "mpd_y", "fmt": "f32"},
]

BASE_X = 520
BASE_Y = -60
BASE_Z = 520


def cmd_wait(client: ModClient, command: str, ticks: int = 3) -> None:
    client.command(command)
    client.wait_ticks(ticks)


def prep_tp(
    client: ModClient,
    x: float,
    y: float,
    z: float,
    *,
    yaw: float = -90.0,
    pitch: float = 0.0,
    ticks: int = 4,
) -> None:
    client.set_input()
    client.teleport(x, y, z, yaw=yaw, pitch=pitch)
    client.wait_ticks(ticks)


def equip_leather_boots(client: ModClient) -> None:
    client.set_item(0, "minecraft:leather_boots", count=1)
    client.equip("feet", slot=0)
    client.wait_ticks(6)


def read_server_freezing(client: ModClient, runtime_id: int) -> float | None:
    try:
        res = client.ecs_component(runtime_id, FREEZING_ID, length=4)
    except ModPipeError:
        return None
    if not res.get("ok") or not res.get("hex"):
        return None
    return round(float(struct.unpack("<f", bytes.fromhex(str(res["hex"])))[0]), 6)


def compact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        item: dict[str, Any] = {
            "tick": r["tick"],
            "x": round(r["x"], 6),
            "feet_y": round(r["feet_y"], 6),
            "z": round(r["z"], 6),
            "vx": round(r["vx"], 6),
            "vy": round(r["vy"], 6),
            "vz": round(r["vz"], 6),
            "horiz": round(math.hypot(r["vx"], r["vz"]), 6),
            "on_ground": r["on_ground"],
            "sprint": r["sprint"],
            "health": r["health"],
        }
        for key in ("slow_x", "slow_y", "freeze_p", "mpd_x", "mpd_y"):
            if key in r:
                val = r[key]
                item[key] = round(val, 6) if isinstance(val, float) else val
        out.append(item)
    return out


def walk_strip(
    client: ModClient,
    watch: list[dict[str, Any]],
    *,
    ticks: int = 20,
    sprint: bool = False,
) -> list[dict[str, Any]]:
    with RecordSession(client, period=1, watch=watch) as rec:
        client.wait_ticks(2)
        client.set_input(forward=1.0, sprint=sprint, ticks=ticks)
        client.wait_ticks(ticks + 2)
    return compact_rows(rec.rows)


def build_arena(builder: Builder) -> dict[str, Any]:
    builder.ensure_loaded(BASE_X + 20, BASE_Z + 20, y=float(BASE_Y + 4))
    cmds = [
        # Clean volume from y=-60 to y=-40
        f"fill {BASE_X} {BASE_Y} {BASE_Z} {BASE_X + 45} {BASE_Y + 20} {BASE_Z + 45} air",
        # Solid stone floor at y=-61 (surface at y=-60)
        f"fill {BASE_X} {BASE_Y - 1} {BASE_Z} {BASE_X + 45} {BASE_Y - 1} {BASE_Z + 45} stone",
        # T1: Cobweb strip at z=BASE_Z+2, x=BASE_X+4..BASE_X+12, plus fall column at x=BASE_X+14
        f"fill {BASE_X + 4} {BASE_Y} {BASE_Z + 2} {BASE_X + 12} {BASE_Y} {BASE_Z + 2} web",
        f"setblock {BASE_X + 14} {BASE_Y} {BASE_Z + 2} web",
        # T2: Sweet Berry Bush strips (grass beneath! bedrock uses dirt/grass_block)
        # z=BASE_Z+6: growth=0; z=BASE_Z+9: growth=3
        f"fill {BASE_X + 3} {BASE_Y - 1} {BASE_Z + 6} {BASE_X + 14} {BASE_Y - 1} {BASE_Z + 6} grass_block",
        f"fill {BASE_X + 3} {BASE_Y - 1} {BASE_Z + 9} {BASE_X + 14} {BASE_Y - 1} {BASE_Z + 9} grass_block",
        f'fill {BASE_X + 4} {BASE_Y} {BASE_Z + 6} {BASE_X + 12} {BASE_Y} {BASE_Z + 6} sweet_berry_bush ["growth"=0]',
        f'fill {BASE_X + 4} {BASE_Y} {BASE_Z + 9} {BASE_X + 12} {BASE_Y} {BASE_Z + 9} sweet_berry_bush ["growth"=3]',
        # T3: Powder Snow strip at z=BASE_Z+13, plus 2-block high platform for 1.5 vs 3.5 drop at x=BASE_X+16
        f"fill {BASE_X + 4} {BASE_Y} {BASE_Z + 13} {BASE_X + 12} {BASE_Y} {BASE_Z + 13} powder_snow",
        f"setblock {BASE_X + 16} {BASE_Y} {BASE_Z + 13} powder_snow",
        f"setblock {BASE_X + 16} {BASE_Y + 1} {BASE_Z + 13} powder_snow",
        # T4: Overlap pairs at z=BASE_Z+17 (web + powder_snow) and z=BASE_Z+20 (berry + powder_snow)
        f"setblock {BASE_X + 6} {BASE_Y} {BASE_Z + 17} web",
        f"setblock {BASE_X + 7} {BASE_Y} {BASE_Z + 17} powder_snow",
        f"setblock {BASE_X + 6} {BASE_Y - 1} {BASE_Z + 20} grass_block",
        f'setblock {BASE_X + 6} {BASE_Y} {BASE_Z + 20} sweet_berry_bush ["growth"=0]',
        f"setblock {BASE_X + 7} {BASE_Y} {BASE_Z + 20} powder_snow",
        # T5: Effect box cobweb at (BASE_X+22, BASE_Y, BASE_Z+17) surrounded by stone floor
        f"setblock {BASE_X + 22} {BASE_Y} {BASE_Z + 17} web",
        # T7: 3-block-high cobweb strip at z=BASE_Z+14 for creative flying directly inside cobweb
        f"fill {BASE_X + 22} {BASE_Y} {BASE_Z + 14} {BASE_X + 25} {BASE_Y + 2} {BASE_Z + 14} web",
        # T6: Powder snow cell at (BASE_X+26, BASE_Y, BASE_Z+17) and long dry stone strip at z=BASE_Z+24
        f"setblock {BASE_X + 26} {BASE_Y} {BASE_Z + 17} powder_snow",
        # T8: Soul sand strip at z=BASE_Z+28, x=BASE_X+4..BASE_X+24 (y=-61 so feet at y=-60; bedrock soul_sand is 0.875? Wait: test collision!)
        # Actually put soul sand flush in floor at y=-61 (Wait: if soul_sand is y=-61, walking on it tests grounded!)
        f"fill {BASE_X + 4} {BASE_Y - 1} {BASE_Z + 28} {BASE_X + 24} {BASE_Y - 1} {BASE_Z + 28} soul_sand",
        # Also carpet on soul sand strip at z=BASE_Z+32 and bottom stone_slab on soul sand at z=BASE_Z+36
        f"fill {BASE_X + 4} {BASE_Y - 1} {BASE_Z + 32} {BASE_X + 18} {BASE_Y - 1} {BASE_Z + 32} soul_sand",
        f"fill {BASE_X + 4} {BASE_Y} {BASE_Z + 32} {BASE_X + 18} {BASE_Y} {BASE_Z + 32} white_carpet",
        f"fill {BASE_X + 4} {BASE_Y - 1} {BASE_Z + 36} {BASE_X + 18} {BASE_Y - 1} {BASE_Z + 36} soul_sand",
        f"fill {BASE_X + 4} {BASE_Y} {BASE_Z + 36} {BASE_X + 18} {BASE_Y} {BASE_Z + 36} stone_block_slab",
    ]
    expect = {
        (BASE_X + 4, BASE_Y, BASE_Z + 2): "minecraft:web",
        (BASE_X + 4, BASE_Y, BASE_Z + 6): "minecraft:sweet_berry_bush",
        (BASE_X + 4, BASE_Y, BASE_Z + 9): "minecraft:sweet_berry_bush",
        (BASE_X + 4, BASE_Y, BASE_Z + 13): "minecraft:powder_snow",
        (BASE_X + 6, BASE_Y, BASE_Z + 17): "minecraft:web",
        (BASE_X + 7, BASE_Y, BASE_Z + 17): "minecraft:powder_snow",
        (BASE_X + 22, BASE_Y, BASE_Z + 17): "minecraft:web",
        (BASE_X + 4, BASE_Y - 1, BASE_Z + 28): "minecraft:soul_sand",
        (BASE_X + 4, BASE_Y, BASE_Z + 32): "minecraft:white_carpet",
        (BASE_X + 4, BASE_Y, BASE_Z + 36): "minecraft:smooth_stone_slab",
    }
    checks = builder.build(cmds, expect, retries=3)
    return {
        "all_ok": all(c.ok for c in checks),
        "checks": [{"pos": c.position, "expected": c.expected, "actual": c.actual} for c in checks],
    }


def run_all(client: ModClient) -> dict[str, Any]:
    builder = Builder(client)
    results: dict[str, Any] = {}
    results["setup"] = build_arena(builder)

    # Clear armor + survival + falldamage true + heal
    clear_armor_and_wait(client)
    cmd_wait(client, "gamemode survival @s", ticks=2)
    cmd_wait(client, "gamerule falldamage true", ticks=2)
    heal_clean(client)

    # --- T0: Stone ground baseline (walk & sprint & release drag & air jump) ---
    prep_tp(client, BASE_X + 2.5, float(BASE_Y), BASE_Z + 24.5, yaw=-90.0)
    assert settle(client, input_sprint=False)["ok"]
    with RecordSession(client, period=1, watch=WATCH_SLOW_MPD) as rec:
        client.wait_ticks(2)
        client.set_input(forward=1.0, sprint=False, ticks=16)
        client.wait_ticks(22)
    stone_walk = compact_rows(rec.rows)

    prep_tp(client, BASE_X + 2.5, float(BASE_Y), BASE_Z + 24.5, yaw=-90.0)
    assert settle(client, input_sprint=False)["ok"]
    stone_sprint = walk_strip(client, WATCH_SLOW_MPD, ticks=16, sprint=True)

    prep_tp(client, BASE_X + 2.5, float(BASE_Y), BASE_Z + 24.5, yaw=-90.0)
    assert settle(client, input_sprint=False)["ok"]
    with RecordSession(client, period=1, watch=WATCH_SLOW_MPD) as rec:
        client.set_input(forward=1.0, jump=True, ticks=12)
        client.wait_ticks(14)
    stone_jump_air = compact_rows(rec.rows)

    results["stone_baseline"] = {
        "walk_and_release": stone_walk,
        "sprint": stone_sprint,
        "jump_air": stone_jump_air,
    }

    # --- T1: Cobweb ---
    # Walk inside cobweb strip
    prep_tp(client, BASE_X + 4.5, float(BASE_Y), BASE_Z + 2.5, yaw=-90.0)
    assert settle(client, input_sprint=False)["ok"]
    web_walk = walk_strip(client, WATCH_SLOW_MPD, ticks=16, sprint=False)

    # Jump & fall inside cobweb (2-high cobweb column or jump from y=-60 into cobweb)
    prep_tp(client, BASE_X + 4.5, float(BASE_Y), BASE_Z + 2.5, yaw=-90.0)
    assert settle(client, input_sprint=False)["ok"]
    with RecordSession(client, period=1, watch=WATCH_SLOW_MPD) as rec:
        client.wait_ticks(2)
        client.set_input(jump=True, ticks=6)
        client.wait_ticks(16)
    web_jump = compact_rows(rec.rows)

    # 10-block survival fall into cobweb at (BASE_X+14, BASE_Y, BASE_Z+2)
    heal_clean(client)
    prep_tp(client, BASE_X + 14.5, float(BASE_Y + 10), BASE_Z + 2.5, yaw=-90.0)
    client.wait_ticks(35)
    web_fall_state = client.state()

    results["cobweb"] = {
        "walk": web_walk,
        "jump": web_jump,
        "fall_10_health": web_fall_state["health"],
        "fall_10_y": web_fall_state["feet_y"],
    }

    # --- T2: Sweet Berry Bush (growth=0 vs growth=3) ---
    heal_clean(client)
    prep_tp(client, BASE_X + 4.5, float(BASE_Y), BASE_Z + 6.5, yaw=-90.0)
    assert settle(client, input_sprint=False)["ok"]
    berry_g0_walk = walk_strip(client, WATCH_SLOW_MPD, ticks=18, sprint=False)

    # Fall 10 blocks onto growth=0 bush
    heal_clean(client)
    prep_tp(client, BASE_X + 10.5, float(BASE_Y + 10), BASE_Z + 6.5, yaw=-90.0)
    client.wait_ticks(28)
    berry_fall_state = client.state()

    # Walk in growth=3 bush (tests damage!)
    heal_clean(client)
    prep_tp(client, BASE_X + 4.5, float(BASE_Y), BASE_Z + 9.5, yaw=-90.0)
    assert settle(client, input_sprint=False)["ok"]
    berry_g3_walk = walk_strip(client, WATCH_SLOW_MPD, ticks=25, sprint=False)

    results["sweet_berry_bush"] = {
        "growth0_walk": berry_g0_walk,
        "growth0_fall_10_health": berry_fall_state["health"],
        "growth0_fall_10_y": berry_fall_state["feet_y"],
        "growth3_walk": berry_g3_walk,
        "growth3_end_health": client.state()["health"],
    }

    # --- T3: Powder Snow + Leather Boots slowdown & 2.5f fall-distance sink ---
    heal_clean(client)
    equip_leather_boots(client)
    prep_tp(client, BASE_X + 4.5, float(BASE_Y), BASE_Z + 13.5, yaw=-90.0)
    assert settle(client, input_sprint=False)["ok"]
    ps_boots_walk = walk_strip(client, WATCH_SLOW_MPD, ticks=16, sprint=False)

    # Top of 2-high tower at BASE_X+16, z=BASE_Z+13 is y = BASE_Y + 2.0 (-58.0)
    # Drop 1.5 blocks -> stands on top (-58.0)
    prep_tp(client, BASE_X + 16.5, float(BASE_Y + 3.5), BASE_Z + 13.5, yaw=-90.0)
    client.wait_ticks(18)
    ps_drop_1p5 = client.state()

    # Drop 3.5 blocks -> fallDistance > 2.5f -> sinks into top block (-58.1 / continues down!)
    prep_tp(client, BASE_X + 16.5, float(BASE_Y + 5.5), BASE_Z + 13.5, yaw=-90.0)
    with RecordSession(client, period=1, watch=WATCH_SLOW_MPD) as rec:
        client.wait_ticks(22)
    ps_drop_3p5_rows = compact_rows(rec.rows)
    ps_drop_3p5 = client.state()

    results["powder_snow_boots"] = {
        "walk": ps_boots_walk,
        "drop_1p5": {
            "feet_y": ps_drop_1p5["feet_y"],
            "on_ground": ps_drop_1p5["on_ground"],
            "health": ps_drop_1p5["health"],
        },
        "drop_3p5": {
            "feet_y": ps_drop_3p5["feet_y"],
            "on_ground": ps_drop_3p5["on_ground"],
            "health": ps_drop_3p5["health"],
            "rows": ps_drop_3p5_rows,
        },
    }

    # --- T4: Multi-block overlap (minss rule) ---
    # Leather boots on -> PowderSnow active + Cobweb / SweetBerry active simultaneously
    prep_tp(client, BASE_X + 7.0, float(BASE_Y), BASE_Z + 17.5, yaw=-90.0)
    with RecordSession(client, period=1, watch=WATCH_SLOW_MPD) as rec:
        client.wait_ticks(4)
    overlap_web_ps = compact_rows(rec.rows)

    prep_tp(client, BASE_X + 7.0, float(BASE_Y), BASE_Z + 20.5, yaw=-90.0)
    with RecordSession(client, period=1, watch=WATCH_SLOW_MPD) as rec:
        client.wait_ticks(4)
    overlap_berry_ps = compact_rows(rec.rows)

    results["overlap_minss"] = {
        "web_plus_powder_snow": overlap_web_ps,
        "berry_plus_powder_snow": overlap_berry_ps,
    }

    # --- T5: 0.998x0.998x0.998 Effect Box (+-0.001f shrink) ---
    clear_armor_and_wait(client)
    bx, by, bz = BASE_X + 22, BASE_Y, BASE_Z + 17
    effect_box: dict[str, Any] = {}
    for tag, px, py, pz, wait_tp in [
        ("horiz_outside_0005", bx - 0.2995, float(by), bz + 0.5, 2),
        ("horiz_inside_0020", bx - 0.2980, float(by), bz + 0.5, 2),
        ("top_outside_0005", bx + 0.5, float(by) + 0.9995, bz + 0.5, 0),
        ("top_inside_0020", bx + 0.5, float(by) + 0.9980, bz + 0.5, 0),
    ]:
        client.set_input()
        client.teleport(px, py, pz, yaw=-90.0, pitch=0.0)
        if wait_tp > 0:
            client.wait_ticks(wait_tp)
        with RecordSession(client, period=1, watch=WATCH_SLOW_MPD) as rec:
            client.wait_ticks(4)
        effect_box[tag] = compact_rows(rec.rows)
    results["effect_box_0998"] = effect_box

    # --- T6: Freezing ramp (+1/140), thaw (-2/140), ground vs air slowdown, leather immunity ---
    heal_clean(client)
    runtime_id = int(client.state()["runtime_id"])
    cmd_wait(client, "gamerule naturalregeneration false", ticks=2)
    freeze_samples: list[dict[str, Any]] = []
    # Barefoot stand in powder snow at (BASE_X+26, BASE_Y, BASE_Z+17): sample server FreezingComponent + 40t damage
    prep_tp(client, BASE_X + 26.5, float(BASE_Y), BASE_Z + 17.5, yaw=-90.0, ticks=1)
    freeze_samples.append({"phase": "enter", "p": read_server_freezing(client, runtime_id)})
    with RecordSession(client, period=1, watch=WATCH_FREEZE) as rec:
        client.wait_ticks(35)
        freeze_samples.append({"phase": "snow_35t", "p": read_server_freezing(client, runtime_id)})
        client.wait_ticks(35)
        freeze_samples.append({"phase": "snow_70t", "p": read_server_freezing(client, runtime_id)})
        client.wait_ticks(70)
        freeze_samples.append({"phase": "snow_140t", "p": read_server_freezing(client, runtime_id)})
        client.wait_ticks(125)
    freeze_ramp = compact_rows(rec.rows)
    freeze_samples.append({"phase": "snow_265t", "p": read_server_freezing(client, runtime_id)})

    # Walk immediately on dry stone strip while freeze_p == 1.0 -> thaw (-2/140 per tick)
    prep_tp(client, BASE_X + 4.5, float(BASE_Y), BASE_Z + 24.5, yaw=-90.0, ticks=1)
    freeze_samples.append({"phase": "thaw_start", "p": read_server_freezing(client, runtime_id)})
    with RecordSession(client, period=1, watch=WATCH_FREEZE) as rec:
        client.set_input(forward=1.0, sprint=False, ticks=25)
        client.wait_ticks(27)
    freeze_ground_thaw = compact_rows(rec.rows)
    freeze_samples.append(
        {"phase": "thaw_after_27t", "p": read_server_freezing(client, runtime_id)}
    )

    # Wait until completely thawed, then re-freeze 145t and test AIR acceleration while frozen!
    client.wait_ticks(55)
    freeze_samples.append({"phase": "thawed_clear", "p": read_server_freezing(client, runtime_id)})
    heal_clean(client)
    prep_tp(client, BASE_X + 26.5, float(BASE_Y), BASE_Z + 17.5, yaw=-90.0)
    client.wait_ticks(150)
    frozen_air_state_before = {
        "state": client.state(),
        "p": read_server_freezing(client, runtime_id),
    }
    prep_tp(client, BASE_X + 4.5, float(BASE_Y), BASE_Z + 24.5, yaw=-90.0, ticks=1)
    with RecordSession(client, period=1, watch=WATCH_FREEZE) as rec:
        client.set_input(forward=1.0, jump=True, ticks=12)
        client.wait_ticks(14)
    freeze_air_jump = compact_rows(rec.rows)

    # Wait until thawed, then equip leather boots & enter powder snow for 30 ticks -> p stays absent/0
    client.wait_ticks(75)
    equip_leather_boots(client)
    prep_tp(client, BASE_X + 26.5, float(BASE_Y), BASE_Z + 17.5, yaw=-90.0)
    with RecordSession(client, period=1, watch=WATCH_FREEZE) as rec:
        client.wait_ticks(30)
    freeze_leather_immune = compact_rows(rec.rows)
    freeze_samples.append({"phase": "leather_30t", "p": read_server_freezing(client, runtime_id)})
    cmd_wait(client, "gamerule naturalregeneration true", ticks=2)
    heal_clean(client)

    results["freezing"] = {
        "server_samples": freeze_samples,
        "ramp_summary": {
            "start_tick": freeze_ramp[0]["tick"],
            "end_tick": freeze_ramp[-1]["tick"],
            "end_health": freeze_ramp[-1]["health"],
            "damage_transitions": [
                {
                    "tick": freeze_ramp[i]["tick"],
                    "dt_from_start": freeze_ramp[i]["tick"] - freeze_ramp[0]["tick"],
                    "health": freeze_ramp[i]["health"],
                }
                for i in range(1, len(freeze_ramp))
                if freeze_ramp[i]["health"] != freeze_ramp[i - 1]["health"]
            ],
        },
        "ground_thaw_walk": freeze_ground_thaw,
        "air_before": {
            "health": frozen_air_state_before["state"]["health"],
            "block_feet": frozen_air_state_before["state"]["block_feet"],
            "p": frozen_air_state_before["p"],
        },
        "air_jump_while_frozen": freeze_air_jump,
        "leather_boots_immune": freeze_leather_immune[:5] + freeze_leather_immune[-5:],
    }

    # --- T7: Creative grounded vs Creative flying immunity ---
    clear_armor_and_wait(client)
    cmd_wait(client, "gamemode creative @s", ticks=3)
    prep_tp(client, BASE_X + 22.5, float(BASE_Y), BASE_Z + 17.5, yaw=-90.0)
    assert settle(client, on_ground=True)["ok"]
    with RecordSession(client, period=1, watch=WATCH_SLOW_MPD) as rec:
        client.wait_ticks(4)
    creative_grounded_web = compact_rows(rec.rows)

    # Fly through the 3-high cobweb strip at z=BASE_Z+14, x=BASE_X+22..25 (y around BASE_Y+1.78)
    prep_tp(client, BASE_X + 20.5, float(BASE_Y), BASE_Z + 14.5, yaw=-90.0)
    assert settle(client, on_ground=True)["ok"]
    fly_res = double_space_fly(client)
    with RecordSession(client, period=1, watch=WATCH_SLOW_MPD) as rec:
        client.set_input(forward=1.0, ticks=16)
        client.wait_ticks(7)
        fly_mid_state = client.state()
        client.wait_ticks(11)
    creative_flying_web = compact_rows(rec.rows)

    cmd_wait(client, "gamemode survival @s", ticks=3)
    results["creative_immunity"] = {
        "grounded_in_web": creative_grounded_web,
        "fly_res": fly_res,
        "flying_mid_block_feet": fly_mid_state["block_feet"],
        "flying_in_web": creative_flying_web,
    }

    # --- T8: Soul Sand + +0.1 lifted region + Soul Speed ---
    heal_clean(client)
    # 8a: Soul Sand flush at y=-61 (feet_y=-60.0), walk + release drag
    prep_tp(client, BASE_X + 4.5, float(BASE_Y), BASE_Z + 28.5, yaw=-90.0)
    assert settle(client, input_sprint=False)["ok"]
    with RecordSession(client, period=1, watch=WATCH_SLOW_MPD) as rec:
        client.wait_ticks(2)
        client.set_input(forward=1.0, sprint=False, ticks=18)
        client.wait_ticks(24)
    ss_walk_release = compact_rows(rec.rows)

    # 8b: Soul Sand sprint
    prep_tp(client, BASE_X + 4.5, float(BASE_Y), BASE_Z + 28.5, yaw=-90.0)
    assert settle(client, input_sprint=False)["ok"]
    ss_sprint = walk_strip(client, WATCH_SLOW_MPD, ticks=18, sprint=True)

    # 8c: Soul Sand jump air accel
    prep_tp(client, BASE_X + 4.5, float(BASE_Y), BASE_Z + 28.5, yaw=-90.0)
    assert settle(client, input_sprint=False)["ok"]
    with RecordSession(client, period=1, watch=WATCH_SLOW_MPD) as rec:
        client.set_input(forward=1.0, jump=True, ticks=12)
        client.wait_ticks(14)
    ss_jump = compact_rows(rec.rows)

    # 8d: Carpet (0.0625 < 0.1) on Soul Sand
    prep_tp(client, BASE_X + 4.5, float(BASE_Y) + 0.0625, BASE_Z + 32.5, yaw=-90.0)
    assert settle(client, input_sprint=False)["ok"]
    ss_carpet = walk_strip(client, WATCH_SLOW_MPD, ticks=18, sprint=False)

    # 8e: Bottom slab (0.5 > 0.1) on Soul Sand
    prep_tp(client, BASE_X + 4.5, float(BASE_Y) + 0.5, BASE_Z + 36.5, yaw=-90.0)
    assert settle(client, input_sprint=False)["ok"]
    ss_slab = walk_strip(client, WATCH_SLOW_MPD, ticks=18, sprint=False)

    # 8f: Soul Speed I boots on Soul Sand
    clear_armor_and_wait(client)
    client.set_item(0, "minecraft:iron_boots", count=1)
    client.select_slot(0)
    client.wait_ticks(2)
    cmd_wait(client, "enchant @s soul_speed 1", ticks=3)
    inv_after_enchant = client.inventory()
    client.equip("feet", slot=0)
    client.wait_ticks(6)
    prep_tp(client, BASE_X + 4.5, float(BASE_Y), BASE_Z + 28.5, yaw=-90.0)
    assert settle(client, input_sprint=False)["ok"]
    ss_soul_speed = walk_strip(client, WATCH_SLOW_MPD, ticks=18, sprint=False)

    results["soul_sand"] = {
        "walk_and_release": ss_walk_release,
        "sprint": ss_sprint,
        "jump_air": ss_jump,
        "carpet_above": ss_carpet,
        "slab_above": ss_slab,
        "soul_speed_inv_check": inv_after_enchant,
        "soul_speed_walk": ss_soul_speed,
    }

    return results


def main() -> int:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ModClient(timeout=30.0).connect() as client:
        client.wait_ready(timeout=30.0, require_world=True)
        with LabSession(
            client,
            clear_box=((BASE_X, BASE_Y, BASE_Z), (BASE_X + 45, BASE_Y + 20, BASE_Z + 45)),
            clear_inventory=True,
        ):
            results = run_all(client)
            cmd_wait(
                client,
                f"fill {BASE_X} {BASE_Y - 1} {BASE_Z} {BASE_X + 45} {BASE_Y - 1} {BASE_Z + 45} grass_block",
                ticks=2,
            )
            cmd_wait(client, "gamerule falldamage true", ticks=2)
            cmd_wait(client, "gamerule naturalregeneration true", ticks=2)

    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved live results to {OUT_PATH}")
    print("Setup OK:", results["setup"]["all_ok"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
