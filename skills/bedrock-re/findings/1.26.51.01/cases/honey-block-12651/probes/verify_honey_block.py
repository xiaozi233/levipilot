"""Live verification of MCBE 1.26.51.01 honey block mechanics (round 2).

Round-1 errors corrected here:
* the ``0.4`` / ``-0.12`` writes are unconditional per component entry; the
  geometric test only gates ``FallDistanceComponent`` clearing and the
  sliding sound/particles (``mPos.y`` = feet + 1.6200101, OR of the two
  horizontal ``>`` tests);
* the component list length is read from the live vector, not guessed from
  the shrunk collision box;
* jump ``0.6x`` is tied to ``BlockType+0x130`` bit 30 (PreventsJumping);
* honey vs slime top-face behaviour is compared directly;
* the missing scenarios (ground walk, ascending jump, sneak slide, creative
  non-flying, creative flight, water) are measured.

Raw rows go to ``out/honey-block-1.26.51.01.json``.
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
    Builder,
    LabSession,
    RecordSession,
    clear_armor_and_wait,
    double_space_fly,
    heal_clean,
    settle,
)

OUT_PATH = ROOT / "out" / "honey-block-1.26.51.01.json"

STATE_VECTOR_ID = 0x1B5D5238
HONEY_INSIDE_ID = 0xC83BBC35
PREVENTS_JUMPING = 0x40000000

WATCH_MOVE = [
    {"component_id": STATE_VECTOR_ID, "offset": 0x18, "size": 4, "name": "mpd_x", "fmt": "f32"},
    {"component_id": STATE_VECTOR_ID, "offset": 0x1C, "size": 4, "name": "mpd_y", "fmt": "f32"},
    {"component_id": STATE_VECTOR_ID, "offset": 0x20, "size": 4, "name": "mpd_z", "fmt": "f32"},
]
WATCH_GLIDE = WATCH_MOVE

BASE_X = 600
BASE_Y = -60
BASE_Z = 600

WALL_X = BASE_X + 10
FLUSH_X = WALL_X + 0.0625 - 0.3
PLAYER_WIDTH = 0.6
PLAYER_HEIGHT = 1.8
HONEY_INSET = 0.0625
STEADY_SLIDE = -0.12

HONEY_BOX_MIN = [0.0625, 0.0, 0.0625]
HONEY_BOX_MAX = [0.9375, 1.0, 0.9375]
HONEY_FRICTION = 0.8
JUMP_STONE = 1.2522
JUMP_HONEY = 0.514

# --- fixtures ---------------------------------------------------------------
SLIDE_Z = BASE_Z + 3.5
WATER_WALL_Z = BASE_Z + 6.5
WATER_CTRL_Z = BASE_Z + 6.5
GROUND2_Z = BASE_Z + 16.0
GROUND1_Z = BASE_Z + 24.0
STONE_CTRL_Z = BASE_Z + 16.0
G1_PLATFORM_Z = BASE_Z + 29.0
G1_WALL_Z0 = BASE_Z + 32
G2_PLATFORM_Z = BASE_Z + 29.0
G2_WALL_Z0 = BASE_Z + 32

# Fixture layout (all groups are rebuilt in place right before their test):
#   slide     x=610, y=-60..-52, z=602..604
#   water     enclosed pool x=606..609 z=606..611 (y=-60..-55), honey wall x=610 z=606..611
#   ground    x=610, y=-60..-59, z=612..620; torso-only y=-59, z=622..630
#   topology  x=610, y=-56..-55, z=608/610/612..613/615/618..619
#   glide     platform x=604..609, y=-56, z=628..630; wall x=610, z=632..638
#   pads      honey x=602..609 z=626..635 / stone z=636..645 / slime z=646..655
#   falls     pads x=616..618, z=628..630 (stone) and z=632..634 (honey)
#   clear     x=610, z=588..590 tall, z=592 single, z=594 none


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
    ticks: int = 3,
) -> None:
    client.set_input()
    client.teleport(x, y, z, yaw=yaw, pitch=pitch)
    client.wait_ticks(ticks)


def read_bytes(client: ModClient, addr: int, length: int) -> bytes:
    reply = client.probe(addr, kind="raw", length=length)
    if not reply.get("ok"):
        raise AssertionError(f"probe {addr:#x} failed: {reply}")
    return bytes(reply["vals"]["u8"][:length])


def read_u64(client: ModClient, addr: int) -> int:
    return int.from_bytes(read_bytes(client, addr, 8), "little")


def read_u32(client: ModClient, addr: int) -> int:
    return int.from_bytes(read_bytes(client, addr, 4), "little")


def block_type_flags(client: ModClient, pos: tuple[int, int, int]) -> dict[str, Any]:
    row = client.blocks([pos])["blocks"][0]
    block_addr = int(row["addr"])
    type_addr = read_u64(client, block_addr + 0x68)
    flags = read_u32(client, type_addr + 0x130)
    return {
        "block": row["name"],
        "block_addr": block_addr,
        "block_type": type_addr,
        "flags": flags,
        "prevents_jumping": bool(flags & PREVENTS_JUMPING),
    }


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
            "on_ground": r["on_ground"],
            "health": r["health"],
        }
        for key in ("mpd_x", "mpd_y", "mpd_z"):
            if key in r:
                item[key] = round(r[key], 6)
        out.append(item)
    return out


def steady_value(rows: list[dict[str, Any]], key: str, tail: int = 6) -> float:
    values = [r[key] for r in rows[-tail:] if r.get(key) is not None]
    assert values, f"no {key} samples in the last {tail} rows"
    return round(sum(values) / len(values), 6)


def setup_group(
    client: ModClient,
    center: tuple[float, float, float],
    box: tuple[tuple[int, int, int], tuple[int, int, int]],
    cmds: list[str],
    expect: dict[tuple[int, int, int], str],
    label: str,
) -> dict[str, Any]:
    """Teleport into the fixture, clear only its box, build, and read back."""
    client.set_input()
    client.teleport(*center, yaw=-90.0, pitch=0.0)
    client.wait_ticks(14)
    (x0, y0, z0), (x1, y1, z1) = box
    client.command(f"fill {x0} {y0} {z0} {x1} {y1} {z1} air")
    client.command(f"fill {x0} {BASE_Y - 1} {z0} {x1} {BASE_Y - 1} {z1} stone")
    client.wait_ticks(4)
    checks = Builder(client).build(cmds, expect, retries=3)
    assert all(c.ok for c in checks), f"{label}: {[c for c in checks if not c.ok]}"
    return {
        "ok": True,
        "checks": [{"pos": c.position, "expected": c.expected, "actual": c.actual} for c in checks],
    }


def group_slide(client: ModClient) -> dict[str, Any]:
    return setup_group(
        client,
        (FLUSH_X, float(BASE_Y + 5), SLIDE_Z),
        ((BASE_X + 4, BASE_Y, BASE_Z), (BASE_X + 14, BASE_Y + 12, BASE_Z + 8)),
        [
            f"fill {WALL_X} {BASE_Y} {BASE_Z + 2} {WALL_X} {BASE_Y + 8} {BASE_Z + 4} honey_block",
        ],
        {(WALL_X, BASE_Y, BASE_Z + 3): "minecraft:honey_block"},
        "slide",
    )


def group_ground(client: ModClient) -> dict[str, Any]:
    return setup_group(
        client,
        (FLUSH_X, float(BASE_Y), GROUND2_Z),
        ((BASE_X, BASE_Y, BASE_Z + 10), (BASE_X + 14, BASE_Y + 4, BASE_Z + 32)),
        [
            f"fill {WALL_X} {BASE_Y} {BASE_Z + 12} {WALL_X} {BASE_Y + 1} {BASE_Z + 20} honey_block",
            f"fill {WALL_X} {BASE_Y + 1} {BASE_Z + 22} {WALL_X} {BASE_Y + 1} {BASE_Z + 30} honey_block",
        ],
        {
            (WALL_X, BASE_Y, BASE_Z + 16): "minecraft:honey_block",
            (WALL_X, BASE_Y, BASE_Z + 24): "minecraft:air",
            (WALL_X, BASE_Y + 1, BASE_Z + 24): "minecraft:honey_block",
        },
        "ground",
    )


def group_topology(client: ModClient) -> dict[str, Any]:
    return setup_group(
        client,
        (FLUSH_X, float(BASE_Y + 5), BASE_Z + 12),
        ((BASE_X + 4, BASE_Y, BASE_Z + 4), (BASE_X + 14, BASE_Y + 7, BASE_Z + 22)),
        [
            f"setblock {WALL_X} {BASE_Y + 4} {BASE_Z + 8} honey_block",
            f"fill {WALL_X} {BASE_Y + 4} {BASE_Z + 10} {WALL_X} {BASE_Y + 5} {BASE_Z + 10} honey_block",
            f"fill {WALL_X} {BASE_Y + 4} {BASE_Z + 12} {WALL_X} {BASE_Y + 4} {BASE_Z + 13} honey_block",
            f"setblock {WALL_X} {BASE_Y + 4} {BASE_Z + 15} honey_block",
            f"setblock {WALL_X - 1} {BASE_Y + 4} {BASE_Z + 15} honey_block",
            f"fill {WALL_X} {BASE_Y + 4} {BASE_Z + 18} {WALL_X} {BASE_Y + 5} {BASE_Z + 19} honey_block",
        ],
        {
            (WALL_X, BASE_Y + 4, BASE_Z + 8): "minecraft:honey_block",
            (WALL_X, BASE_Y + 5, BASE_Z + 10): "minecraft:honey_block",
            (WALL_X, BASE_Y + 4, BASE_Z + 13): "minecraft:honey_block",
            (WALL_X - 1, BASE_Y + 4, BASE_Z + 15): "minecraft:honey_block",
            (WALL_X, BASE_Y + 5, BASE_Z + 19): "minecraft:honey_block",
        },
        "topology",
    )


def group_glide(client: ModClient, layers: tuple[int, ...], label: str) -> dict[str, Any]:
    cmds = [
        f"fill {BASE_X + 4} {BASE_Y + 4} {BASE_Z + 28} {WALL_X - 1} {BASE_Y + 4} {BASE_Z + 30} stone",
    ] + [
        f"fill {WALL_X} {BASE_Y + dy} {G1_WALL_Z0} {WALL_X} {BASE_Y + dy} {G1_WALL_Z0 + 6} honey_block"
        for dy in layers
    ]
    expect = {(WALL_X, BASE_Y + layers[-1], G1_WALL_Z0 + 6): "minecraft:honey_block"}
    return setup_group(
        client,
        (FLUSH_X, float(BASE_Y + 5), G1_PLATFORM_Z),
        ((BASE_X + 4, BASE_Y, BASE_Z + 26), (BASE_X + 14, BASE_Y + 8, BASE_Z + 40)),
        cmds,
        expect,
        label,
    )


def group_platform(client: ModClient) -> dict[str, Any]:
    return setup_group(
        client,
        (FLUSH_X, float(BASE_Y + 5), G1_PLATFORM_Z),
        ((BASE_X + 4, BASE_Y, BASE_Z + 26), (BASE_X + 14, BASE_Y + 8, BASE_Z + 40)),
        [
            f"fill {BASE_X + 4} {BASE_Y + 4} {BASE_Z + 28} {WALL_X - 1} {BASE_Y + 4} {BASE_Z + 30} stone",
        ],
        {(BASE_X + 5, BASE_Y + 4, BASE_Z + 29): "minecraft:stone"},
        "platform",
    )


def measure_air_drag(client: ModClient) -> dict[str, Any]:
    prep_tp(client, FLUSH_X, float(BASE_Y + 5), G1_PLATFORM_Z, yaw=0.0, ticks=4)
    gate = settle(client, input_sprint=False)
    assert gate["ok"], "drag: settle failed"
    with RecordSession(client, period=1, watch=WATCH_GLIDE) as rec:
        client.set_input(forward=1.0, ticks=14)
        client.wait_ticks(14)
        client.set_input()
        client.wait_ticks(30)
    rows = compact_rows(rec.rows)
    ratios = [
        round((b["z"] - a["z"]) / a["vz"], 4)
        for a, b in itertools.pairwise(rows)
        if b["tick"] == a["tick"] + 1
        and not a["on_ground"]
        and not b["on_ground"]
        and abs(a["vz"]) > 1e-4
    ]
    usable = [r for r in ratios if 0.85 < r < 0.97]
    assert len(usable) >= 2, f"drag: no clean free-air decay samples: {ratios}"
    drag = sorted(usable)[len(usable) // 2]
    assert 0.88 <= drag <= 0.94, f"drag outside the expected band: {drag}"
    return {"rows": rows, "ratios": ratios, "drag": drag}


def group_pads(client: ModClient) -> dict[str, Any]:
    return setup_group(
        client,
        (BASE_X + 5.5, float(BASE_Y), BASE_Z + 30),
        ((BASE_X, BASE_Y, BASE_Z + 24), (BASE_X + 20, BASE_Y + 2, BASE_Z + 56)),
        [
            f"fill {BASE_X + 2} {BASE_Y - 1} {BASE_Z + 26} {BASE_X + 9} {BASE_Y - 1} {BASE_Z + 35} honey_block",
            f"fill {BASE_X + 2} {BASE_Y - 1} {BASE_Z + 36} {BASE_X + 9} {BASE_Y - 1} {BASE_Z + 45} stone",
            f"fill {BASE_X + 2} {BASE_Y - 1} {BASE_Z + 46} {BASE_X + 9} {BASE_Y - 1} {BASE_Z + 55} slime",
            f"fill {BASE_X + 16} {BASE_Y - 1} {BASE_Z + 28} {BASE_X + 18} {BASE_Y - 1} {BASE_Z + 30} stone",
            f"fill {BASE_X + 16} {BASE_Y - 1} {BASE_Z + 32} {BASE_X + 18} {BASE_Y - 1} {BASE_Z + 34} honey_block",
        ],
        {
            (BASE_X + 2, BASE_Y - 1, BASE_Z + 26): "minecraft:honey_block",
            (BASE_X + 2, BASE_Y - 1, BASE_Z + 36): "minecraft:stone",
            (BASE_X + 2, BASE_Y - 1, BASE_Z + 46): "minecraft:slime",
            (BASE_X + 16, BASE_Y - 1, BASE_Z + 28): "minecraft:stone",
            (BASE_X + 16, BASE_Y - 1, BASE_Z + 32): "minecraft:honey_block",
        },
        "pads",
    )


def group_fall_clear(client: ModClient) -> dict[str, Any]:
    return setup_group(
        client,
        (FLUSH_X, float(BASE_Y + 4), BASE_Z - 8.5),
        ((BASE_X + 4, BASE_Y, BASE_Z - 14), (BASE_X + 14, BASE_Y + 16, BASE_Z - 2)),
        [
            f"fill {WALL_X} {BASE_Y} {BASE_Z - 12} {WALL_X} {BASE_Y + 10} {BASE_Z - 10} honey_block",
            f"setblock {WALL_X} {BASE_Y + 10} {BASE_Z - 8} honey_block",
        ],
        {
            (WALL_X, BASE_Y + 10, BASE_Z - 10): "minecraft:honey_block",
            (WALL_X, BASE_Y + 10, BASE_Z - 8): "minecraft:honey_block",
            (WALL_X, BASE_Y + 10, BASE_Z - 6): "minecraft:air",
        },
        "fallclear",
    )


def group_water(client: ModClient) -> dict[str, Any]:
    return setup_group(
        client,
        (FLUSH_X, float(BASE_Y + 2), WATER_WALL_Z),
        ((BASE_X + 4, BASE_Y, BASE_Z + 4), (BASE_X + 14, BASE_Y + 8, BASE_Z + 14)),
        [
            # Enclosed still-water pool (zero flow); east side at WALL_X is honey_block.
            f"fill {BASE_X + 5} {BASE_Y} {BASE_Z + 5} {BASE_X + 5} {BASE_Y + 6} {BASE_Z + 12} stone",
            f"fill {BASE_X + 6} {BASE_Y} {BASE_Z + 5} {WALL_X} {BASE_Y + 6} {BASE_Z + 5} stone",
            f"fill {BASE_X + 6} {BASE_Y} {BASE_Z + 12} {WALL_X} {BASE_Y + 6} {BASE_Z + 12} stone",
            f"fill {WALL_X} {BASE_Y} {BASE_Z + 6} {WALL_X} {BASE_Y + 6} {BASE_Z + 11} honey_block",
            f"fill {BASE_X + 6} {BASE_Y} {BASE_Z + 6} {BASE_X + 9} {BASE_Y + 5} {BASE_Z + 11} water",
        ],
        {
            (BASE_X + 5, BASE_Y + 2, BASE_Z + 5): "minecraft:stone",
            (BASE_X + 7, BASE_Y + 2, BASE_Z + 7): "minecraft:water",
            (WALL_X, BASE_Y + 2, BASE_Z + 7): "minecraft:honey_block",
            (BASE_X + 7, BASE_Y + 2, BASE_Z + 12): "minecraft:stone",
        },
        "water",
    )


def assert_shapes(client: ModClient) -> dict[str, Any]:
    reply = client.block_shapes([(WALL_X, BASE_Y, BASE_Z + 2)], entity_mode="none")
    rows = reply.get("blocks") or []
    assert rows, f"block_shapes returned no rows: {reply}"
    row = rows[0]
    origin = (float(WALL_X), float(BASE_Y), float(BASE_Z + 2))
    collision = row["collision"]
    actual_min = [round(float(v) - o, 6) for v, o in zip(collision["min"], origin, strict=True)]
    actual_max = [round(float(v) - o, 6) for v, o in zip(collision["max"], origin, strict=True)]
    friction = round(float(row["friction"]), 6)
    assert actual_min == HONEY_BOX_MIN, f"honey collision min {actual_min} != {HONEY_BOX_MIN}"
    assert actual_max == HONEY_BOX_MAX, f"honey collision max {actual_max} != {HONEY_BOX_MAX}"
    assert abs(friction - HONEY_FRICTION) < 1e-6, f"honey friction {friction} != {HONEY_FRICTION}"
    return {"collision": collision, "friction": friction, "boxes": row["boxes"]}


def record(
    client: ModClient,
    ticks: int,
    *,
    watch: list[dict[str, Any]] | None = WATCH_MOVE,
    input_first: dict[str, Any] | None = None,
    input_ticks: int = 0,
) -> list[dict[str, Any]]:
    with RecordSession(client, period=1, watch=watch) as rec:
        if input_first and input_ticks:
            client.set_input(**input_first, ticks=input_ticks)
            client.wait_ticks(input_ticks)
            client.set_input()
        client.wait_ticks(max(0, ticks - input_ticks))
    return compact_rows(rec.rows)


def run_slide(client: ModClient) -> dict[str, Any]:
    prep_tp(client, FLUSH_X, float(BASE_Y + 6.0), SLIDE_Z, ticks=2)
    with RecordSession(client, period=1, watch=WATCH_MOVE) as rec:
        client.set_input(forward=1.0, ticks=30)
        client.wait_ticks(30)
    rows = compact_rows(rec.rows)
    sliding = [r for r in rows if not r["on_ground"] and r["feet_y"] < BASE_Y + 7]
    assert len(sliding) >= 8, f"only {len(sliding)} airborne wall ticks"
    for r in sliding[-6:]:
        assert abs(r["vy"] - STEADY_SLIDE) < 2e-3, f"steady vy {r['vy']} != -0.12"
        assert r["mpd_y"] is not None and abs(r["mpd_y"] - STEADY_SLIDE) < 2e-3
    return {"rows": sliding, "steady_vy": steady_value(sliding, "vy")}


def run_slide_control(client: ModClient) -> dict[str, Any]:
    prep_tp(client, FLUSH_X - 0.12, float(BASE_Y + 6.0), SLIDE_Z, ticks=2)
    rows = record(client, 24)
    airborne = [r for r in rows if not r["on_ground"]]
    assert airborne and min(r["vy"] for r in airborne) < -0.5
    return {"rows": airborne[:14], "min_vy": min(r["vy"] for r in airborne)}


def run_ground_walk(client: ModClient) -> dict[str, Any]:
    def walk(x: float, z: float, ticks: int = 24) -> list[dict[str, Any]]:
        prep_tp(client, x, float(BASE_Y), z, yaw=0.0, ticks=4)
        gate = settle(client, input_sprint=False)
        assert gate["ok"], f"settle failed: {gate.get('state')}"
        with RecordSession(client, period=1, watch=WATCH_MOVE) as rec:
            client.set_input(forward=1.0, ticks=ticks)
            client.wait_ticks(ticks + 2)
        return compact_rows(rec.rows)

    def tail_dz(rows: list[dict[str, Any]]) -> float:
        deltas = [
            round(b["z"] - a["z"], 6)
            for a, b in itertools.pairwise(rows)
            if b["tick"] == a["tick"] + 1
        ][-6:-1]
        return round(sum(deltas) / len(deltas), 6)

    stone = walk(BASE_X + 4.5, STONE_CTRL_Z)
    two = walk(FLUSH_X, GROUND2_Z)
    one = walk(FLUSH_X, GROUND1_Z)
    values = {
        "stone_dz": tail_dz(stone),
        "two_high_dz": tail_dz(two),
        "one_high_dz": tail_dz(one),
    }
    assert values["stone_dz"] > 0.2, f"stone baseline too slow: {values}"
    # On the ground the input acceleration keeps feeding the next tick, so the
    # steady speed is a/(1-f*k) rather than k*0.2158; any contact must still
    # cut the baseline roughly in half.
    assert values["two_high_dz"] < values["stone_dz"] * 0.6, (
        f"2-high wall did not slow: {values}; rows={two[:6]}"
    )
    assert values["one_high_dz"] < values["stone_dz"] * 0.65, f"1-high wall did not slow: {values}"
    return {"stone": stone, "two_high": two, "one_high": one, **values}


def run_ascend_jump(client: ModClient) -> dict[str, Any]:
    def jump_run(x: float, z: float) -> list[dict[str, Any]]:
        prep_tp(client, x, float(BASE_Y), z, yaw=-20.0, ticks=4)
        gate = settle(client, input_sprint=False)
        assert gate["ok"]
        with RecordSession(client, period=1, watch=WATCH_MOVE) as rec:
            client.set_input(forward=1.0, ticks=3)
            client.wait_ticks(3)
            client.set_input(forward=1.0, jump=True, ticks=3)
            client.wait_ticks(3)
            client.set_input(forward=1.0, ticks=8)
            client.wait_ticks(8)
        return compact_rows(rec.rows)

    control = jump_run(BASE_X + 4.5, GROUND2_Z)
    wall = jump_run(FLUSH_X, GROUND2_Z)

    def ascend_dz(rows: list[dict[str, Any]]) -> float:
        ascending = [r for r in rows if not r["on_ground"] and (r["mpd_y"] or 0) > 0.05]
        assert ascending, "no ascending tick captured"
        pairs = [
            (a, b)
            for a, b in itertools.pairwise(rows)
            if b["tick"] == a["tick"] + 1 and not a["on_ground"] and (b["mpd_y"] or 0) > 0.05
        ]
        deltas = [round(b["z"] - a["z"], 6) for a, b in pairs]
        return round(sum(deltas) / len(deltas), 6)

    control_dz = ascend_dz(control)
    wall_dz = ascend_dz(wall)
    assert control_dz > 0.03, f"control ascent too slow: {control_dz}"
    assert wall_dz < control_dz * 0.7, (
        f"0.4 multiplier missing while mpd.y > 0: wall={wall_dz} control={control_dz}"
    )
    return {
        "control_rows": control,
        "wall_rows": wall,
        "control_dz": control_dz,
        "wall_dz": wall_dz,
        "ratio": round(wall_dz / control_dz, 4),
    }


def fall_once(
    client: ModClient,
    x: float,
    z: float,
    height: float,
    *,
    wait_for_ground: bool = False,
) -> dict[str, Any]:
    clear_armor_and_wait(client)
    cmd_wait(client, "gamemode survival @s", ticks=2)
    cmd_wait(client, "gamerule falldamage true", ticks=2)
    cmd_wait(client, "gamerule naturalregeneration false", ticks=2)
    heal_clean(client)
    with RecordSession(client, period=1, watch=WATCH_MOVE) as rec:
        prep_tp(client, x, float(BASE_Y + height), z, ticks=2)
        if wait_for_ground:
            for _ in range(14):
                client.wait_ticks(15)
                if client.state()["on_ground"]:
                    break
        else:
            client.wait_ticks(45)
    state = client.state()
    assert state["on_ground"] and abs(state["feet_y"] - BASE_Y) < 0.05, (
        f"fall_once did not land at BASE_Y={BASE_Y}: {state}"
    )
    rows = compact_rows(rec.rows)
    return {
        "height": height,
        "health": state["health"],
        "damage": round(20.0 - state["health"], 3),
        "end_y": state["feet_y"],
        "on_ground": state["on_ground"],
        "gamemode": state.get("gamemode"),
        "min_vy": min((r["vy"] for r in rows), default=None),
        "rows_tail": rows[-16:],
    }


def run_fall_clear(client: ModClient) -> dict[str, Any]:
    # 15-block drop at x=FLUSH; only FallDistance clearing differs.
    tall = fall_once(client, FLUSH_X, BASE_Z - 10.5, 15.0, wait_for_ground=True)
    single = fall_once(client, FLUSH_X, BASE_Z - 7.5, 15.0)
    none = fall_once(client, FLUSH_X, BASE_Z - 5.5, 15.0)
    cmd_wait(client, "gamerule naturalregeneration true", ticks=2)
    heal_clean(client)
    assert tall["damage"] <= 0.01, f"tall wall did not clear fall distance: {tall}"
    assert single["damage"] > 0.0, f"single block cleared the whole fall: {single}"
    assert none["damage"] > single["damage"], f"control fall was not the hardest: {none}"
    return {"tall_wall": tall, "single_block": single, "no_wall": none}


def honey_overlap(
    x: float, feet_y: float, z: float, layers: tuple[int, ...], z0: int, z1: int
) -> int:
    """Count honey cells (full 1x1x1 cells) strictly intersecting the player AABB."""
    min_x, max_x = x - PLAYER_WIDTH / 2, x + PLAYER_WIDTH / 2
    min_y, max_y = feet_y, feet_y + PLAYER_HEIGHT
    min_z, max_z = z - PLAYER_WIDTH / 2, z + PLAYER_WIDTH / 2
    count = 0
    for layer in layers:
        for cell_z in range(z0, z1 + 1):
            if (
                min_x < WALL_X + 1.0
                and max_x > WALL_X
                and min_y < layer + 1.0
                and max_y > layer
                and min_z < cell_z + 1.0
                and max_z > cell_z
            ):
                count += 1
    return count


def run_inside_boundary(client: ModClient) -> dict[str, Any]:
    """Five topologies: the vertical clamp proves the block entered the list."""
    cases = {
        # name: (feet position, honey layers, wall z0, wall z1, extra blocks, expected n)
        "single": ((FLUSH_X, -55.1, BASE_Z + 8.5), (BASE_Y + 4,), BASE_Z + 8, BASE_Z + 8, 0, 1),
        "vertical2": (
            (FLUSH_X, -55.1, BASE_Z + 10.5),
            (BASE_Y + 4, BASE_Y + 5),
            BASE_Z + 10,
            BASE_Z + 10,
            0,
            2,
        ),
        "horizontal2": (
            (FLUSH_X, -55.1, BASE_Z + 13.0),
            (BASE_Y + 4,),
            BASE_Z + 12,
            BASE_Z + 13,
            0,
            2,
        ),
        "corner2": (
            (FLUSH_X, -55.1, BASE_Z + 16.2375),
            (BASE_Y + 4,),
            BASE_Z + 15,
            BASE_Z + 15,
            1,
            2,
        ),
        "seam_stack": (
            (FLUSH_X, -55.1, BASE_Z + 19.0),
            (BASE_Y + 4, BASE_Y + 5),
            BASE_Z + 18,
            BASE_Z + 19,
            0,
            4,
        ),
    }
    out: dict[str, Any] = {}
    for name, (pos, layers, z0, z1, extra, expected_n) in cases.items():
        x, y, z = pos
        prep_tp(client, x, y, z, ticks=1)
        rows = compact_rows(record(client, 6, watch=WATCH_MOVE))
        airborne = [r for r in rows if not r["on_ground"]]
        assert airborne, f"{name}: never left the ground"
        moving = [r for r in airborne if abs(r["vy"]) > 1e-6]
        assert moving, f"{name}: never moved while airborne"
        steady = moving[2:5] or moving[-2:]
        assert steady and all(abs(r["vy"] - STEADY_SLIDE) < 5e-3 for r in steady), (
            f"{name}: no slide clamp, block not in the inside list (vy={[r['vy'] for r in moving]})"
        )
        assert min(r["vy"] for r in moving[:5]) > -0.2, f"{name}: fell freely: {moving}"
        n = honey_overlap(x, y, z, layers, z0, z1) + extra
        assert n == expected_n, f"{name}: geometric n={n} != {expected_n}"
        out[name] = {"n": n, "rows": rows}
    return out


def run_inside_boundary_walk(client: ModClient) -> dict[str, Any]:
    """Any positive overlap with the full block cell triggers the 0.4 multiplier."""

    def walk(x: float) -> list[dict[str, Any]]:
        prep_tp(client, x, float(BASE_Y), GROUND2_Z, yaw=0.0, ticks=4)
        gate = settle(client, input_sprint=False)
        assert gate["ok"]
        with RecordSession(client, period=1, watch=WATCH_MOVE) as rec:
            client.set_input(forward=1.0, ticks=12)
            client.wait_ticks(12)
        return compact_rows(rec.rows)

    def tail_dz(rows: list[dict[str, Any]]) -> float:
        deltas = [
            round(b["z"] - a["z"], 6)
            for a, b in itertools.pairwise(rows)
            if b["tick"] == a["tick"] + 1
        ][-4:]
        return round(sum(deltas) / len(deltas), 6)

    inside = walk(FLUSH_X)
    outside = walk(FLUSH_X - 0.07)
    values = {"inside_dz": tail_dz(inside), "outside_dz": tail_dz(outside)}
    assert values["outside_dz"] > 0.2, f"outside walk too slow: {values}"
    assert values["inside_dz"] < values["outside_dz"] * 0.6, (
        f"0.0625 overlap did not trigger the multiplier: {values}"
    )
    return {"inside": inside, "outside": outside, **values}


def run_glide(
    client: ModClient,
    *,
    platform_z: float,
    wall_y: tuple[int, ...],
    wall_z0: int,
    wall_z1: int,
    label: str,
    input_ticks: int,
    drag: float,
) -> dict[str, Any]:
    prep_tp(client, FLUSH_X, float(BASE_Y + 5), platform_z, yaw=0.0, ticks=4)
    gate = settle(client, input_sprint=False)
    assert gate["ok"], f"{label}: settle failed"
    with RecordSession(client, period=1, watch=WATCH_GLIDE) as rec:
        client.set_input(forward=1.0, ticks=input_ticks)
        client.wait_ticks(input_ticks)
        client.set_input()
        client.wait_ticks(40)
    rows = compact_rows(rec.rows)
    ratios: dict[int, list[float]] = {}
    pairs: list[dict[str, Any]] = []
    for a, b in itertools.pairwise(rows):
        if b["tick"] != a["tick"] + 1 or a["on_ground"]:
            continue
        dz = b["z"] - a["z"]
        n = honey_overlap(b["x"], b["feet_y"], b["z"], wall_y, wall_z0, wall_z1)
        if abs(dz) < 1e-3:
            continue
        prev = a["vz"]
        if abs(prev) < 1e-5:
            continue
        ratio = round(dz / prev, 4)
        ratios.setdefault(n, []).append(ratio)
        pairs.append({"tick": b["tick"], "n": n, "ratio": ratio, "dz": round(dz, 6)})
    assert pairs, f"{label}: no usable glide pairs; rows={rows[:6]} tail={rows[-4:]}"
    checked: dict[str, Any] = {}
    for n, values in sorted(ratios.items()):
        if n == 0:
            continue
        # The first tick with a non-zero n is the entry tick: the block is in
        # the list, but that tick's displacement was produced before it.
        usable = [v for v in values[1:] if 0.004 < v < 0.8]
        if not usable:
            continue
        expected = drag * (0.4**n)
        mean = sum(usable) / len(usable)
        assert abs(mean - expected) < max(0.004, expected * 0.25), (
            f"{label}: n={n} ratio {mean:.4f} != drag*0.4^{n}={expected:.4f}; pairs={pairs[:12]}"
        )
        checked[str(n)] = {
            "mean": round(mean, 4),
            "expected": round(expected, 4),
            "samples": len(usable),
        }
    assert checked, f"{label}: no clean ratio bucket"
    return {"rows": rows, "pairs": pairs, "drag": round(drag, 4), "buckets": checked}


def run_top_walk(client: ModClient) -> dict[str, Any]:
    def walk(x: float, z: float, sneak: bool, ticks: int = 34) -> list[dict[str, Any]]:
        prep_tp(client, x, float(BASE_Y), z, yaw=0.0, ticks=4)
        gate = settle(client, input_sprint=False)
        assert gate["ok"], f"settle failed: {gate.get('state')}"
        with RecordSession(client, period=1, watch=WATCH_MOVE) as rec:
            client.set_input(forward=1.0, sneak=sneak, ticks=ticks)
            client.wait_ticks(ticks + 2)
        return compact_rows(rec.rows)

    def tail_dz(rows: list[dict[str, Any]]) -> float:
        deltas = [
            round(b["z"] - a["z"], 6)
            for a, b in itertools.pairwise(rows)
            if b["tick"] == a["tick"] + 1
        ][-8:-2]
        return round(sum(deltas) / len(deltas), 6)

    stone = walk(BASE_X + 5.5, BASE_Z + 36.5, False)
    honey = walk(BASE_X + 5.5, BASE_Z + 26.5, False)
    slime = walk(BASE_X + 5.5, BASE_Z + 46.5, False)
    stone_sneak = walk(BASE_X + 5.5, BASE_Z + 36.5, True)
    honey_sneak = walk(BASE_X + 5.5, BASE_Z + 26.5, True)
    slime_sneak = walk(BASE_X + 5.5, BASE_Z + 46.5, True)
    values = {
        "stone_dz": tail_dz(stone),
        "honey_dz": tail_dz(honey),
        "slime_dz": tail_dz(slime),
        "stone_sneak_dz": tail_dz(stone_sneak),
        "honey_sneak_dz": tail_dz(honey_sneak),
        "slime_sneak_dz": tail_dz(slime_sneak),
    }
    assert values["stone_dz"] > 0.2, f"stone baseline too slow: {values}"
    assert values["honey_dz"] < values["stone_dz"] * 0.4, f"honey top walk not slowed: {values}"
    # SlimeBlock shares the same 0.8 slipperiness and the same 0.4+0.2|dy|
    # onStandOn multiplier, so the two must match tick for tick.
    assert abs(values["honey_dz"] - values["slime_dz"]) < 0.002, (
        f"honey and slime top walk differ: {values}"
    )
    assert abs(values["honey_sneak_dz"] - values["slime_sneak_dz"]) < 0.002, (
        f"honey and slime sneak differ: {values}"
    )
    assert values["honey_sneak_dz"] < values["stone_sneak_dz"] * 0.85, (
        f"sneaking bypassed the honey slowdown: {values}"
    )
    return {
        "stone": stone,
        "honey": honey,
        "slime": slime,
        "stone_sneak": stone_sneak,
        "honey_sneak": honey_sneak,
        "slime_sneak": slime_sneak,
        **values,
        "honey_ratio": round(values["honey_dz"] / values["stone_dz"], 4),
        "honey_sneak_ratio": round(values["honey_sneak_dz"] / values["stone_sneak_dz"], 4),
        "slime_sneak_ratio": round(values["slime_sneak_dz"] / values["stone_sneak_dz"], 4),
        "slime_ratio": round(values["slime_dz"] / values["stone_dz"], 4),
    }


def jump_at(client: ModClient, x: float, z: float) -> dict[str, Any]:
    prep_tp(client, x, float(BASE_Y), z, ticks=5)
    state = client.state()
    assert state["on_ground"], f"jump spot ({x},{z}) not grounded: {state['feet_y']}"
    start = state["feet_y"]
    with RecordSession(client, period=1, watch=WATCH_MOVE) as rec:
        client.set_input(jump=True, ticks=2)
        client.wait_ticks(16)
    rows = compact_rows(rec.rows)
    peak = max(r["feet_y"] for r in rows)
    return {"start_y": start, "peak_y": peak, "rise": round(peak - start, 6), "rows": rows}


def run_jump(client: ModClient) -> dict[str, Any]:
    stone = jump_at(client, BASE_X + 5.5, BASE_Z + 40.5)
    honey = jump_at(client, BASE_X + 5.5, BASE_Z + 30.5)
    slime = jump_at(client, BASE_X + 5.5, BASE_Z + 50.5)
    edge = jump_at(client, BASE_X + 10.15, BASE_Z + 30.5)
    assert abs(stone["rise"] - JUMP_STONE) < 0.02, f"stone jump {stone['rise']} != {JUMP_STONE}"
    assert abs(honey["rise"] - JUMP_HONEY) < 0.02, f"honey jump {honey['rise']} != {JUMP_HONEY}"
    assert slime["rise"] > honey["rise"] * 1.5, f"slime jump was weakened: {slime['rise']}"
    assert edge["rise"] > honey["rise"] * 1.5, f"edge jump was still weakened: {edge['rise']}"
    return {
        "stone": stone,
        "honey": honey,
        "slime": slime,
        "edge": edge,
        "rise_ratio": round(honey["rise"] / stone["rise"], 4),
    }


def run_fall_damage(client: ModClient) -> dict[str, Any]:
    out: dict[str, Any] = {"stone": [], "honey": []}
    for height in (10.0, 15.0, 20.0):
        out["stone"].append(fall_once(client, BASE_X + 17.5, BASE_Z + 29.5, height))
        out["honey"].append(fall_once(client, BASE_X + 17.5, BASE_Z + 33.5, height))
    cmd_wait(client, "gamerule naturalregeneration true", ticks=2)
    heal_clean(client)
    for stone, honey in zip(out["stone"], out["honey"], strict=True):
        height = stone["height"]
        assert abs(stone["damage"] - (height - 3.0)) <= 1.0, f"stone {height}: {stone}"
        expected = -(-(0.5 * height - 3.0) // 1)
        assert abs(honey["damage"] - expected) <= 1.0, f"honey {height}: {honey}"
    out["model"] = "honey halves fall distance: damage = ceil(0.5*h - 3)"
    return out


def run_slide_landing(client: ModClient) -> dict[str, Any]:
    clear_armor_and_wait(client)
    cmd_wait(client, "gamemode survival @s", ticks=2)
    cmd_wait(client, "gamerule falldamage true", ticks=2)
    cmd_wait(client, "gamerule naturalregeneration false", ticks=2)
    heal_clean(client)
    prep_tp(client, FLUSH_X, float(BASE_Y + 7.5), SLIDE_Z, ticks=2)
    with RecordSession(client, period=1, watch=WATCH_MOVE) as rec:
        client.set_input(forward=1.0, ticks=70)
        client.wait_ticks(70)
    state = client.state()
    cmd_wait(client, "gamerule naturalregeneration true", ticks=2)
    heal_clean(client)
    damage = round(20.0 - state["health"], 3)
    assert 0.0 <= damage <= 3.0, f"slide landing damage {damage} outside the expected band"
    return {"damage": damage, "end_y": state["feet_y"], "rows": compact_rows(rec.rows[-14:])}


def run_special_dry(client: ModClient) -> dict[str, Any]:
    out: dict[str, Any] = {}

    # Sneak wall slide: the clamp must still be -0.12.
    prep_tp(client, FLUSH_X, float(BASE_Y + 6.0), SLIDE_Z, ticks=2)
    with RecordSession(client, period=1, watch=WATCH_MOVE) as rec:
        client.set_input(forward=1.0, sneak=True, ticks=26)
        client.wait_ticks(26)
    rows = compact_rows(rec.rows)
    sliding = [r for r in rows if not r["on_ground"] and r["feet_y"] < BASE_Y + 7]
    assert sliding and abs(sliding[-1]["vy"] - STEADY_SLIDE) < 3e-3, f"sneak slide: {sliding[-1:]}"
    out["sneak_slide"] = {"rows": sliding[-8:], "steady_vy": steady_value(sliding, "vy")}

    def forward_dz(rows: list[dict[str, Any]]) -> float:
        deltas = [
            round(b["z"] - a["z"], 6)
            for a, b in itertools.pairwise(rows)
            if b["tick"] == a["tick"] + 1
        ][-6:]
        return round(sum(deltas) / len(deltas), 6)

    # Creative, not flying: ground walk must still be slowed and falling clamped.
    cmd_wait(client, "gamemode creative @s", ticks=3)
    prep_tp(client, FLUSH_X, float(BASE_Y), GROUND2_Z, yaw=0.0, ticks=4)
    gate = settle(client, input_sprint=False)
    assert gate["ok"]
    with RecordSession(client, period=1, watch=WATCH_MOVE) as rec:
        client.set_input(forward=1.0, ticks=16)
        client.wait_ticks(16)
    creative_walk = compact_rows(rec.rows)
    prep_tp(client, FLUSH_X, float(BASE_Y + 6.0), SLIDE_Z, ticks=2)
    with RecordSession(client, period=1, watch=WATCH_MOVE) as rec:
        client.set_input(forward=1.0, ticks=20)
        client.wait_ticks(20)
    creative_fall = compact_rows(rec.rows)
    creative_sliding = [r for r in creative_fall if not r["on_ground"]]
    assert creative_sliding and abs(creative_sliding[-1]["vy"] - STEADY_SLIDE) < 3e-3, (
        "creative non-flying fall was not clamped"
    )
    out["creative_ground"] = {"rows": creative_walk[-10:], "dz": forward_dz(creative_walk)}
    out["creative_fall"] = {
        "rows": creative_sliding[-8:],
        "steady_vy": steady_value(creative_sliding, "vy"),
    }
    assert out["creative_ground"]["dz"] < 0.12, "creative non-flying walk was not slowed"

    # Creative flight: the wall must not scale the horizontal fly speed and a
    # Shift descend must not be clamped to -0.12.
    def fly_run(x: float, *, descend: bool) -> list[dict[str, Any]]:
        if client.state().get("flying"):
            double_space_fly(client)  # toggle flight off before the next run
            client.wait_ticks(4)
        prep_tp(client, x, float(BASE_Y + 6.0), SLIDE_Z, yaw=0.0, ticks=3)
        fly = double_space_fly(client)
        assert fly.get("flying"), f"double-space flight failed: {fly}"
        if descend:
            client.key_event(0x10, True, via="hardware")
        with RecordSession(client, period=1, watch=WATCH_MOVE) as rec:
            client.set_input(forward=1.0, ticks=16)
            client.wait_ticks(16)
        if descend:
            client.key_event(0x10, False, via="hardware")
        return compact_rows(rec.rows)

    fly_wall = fly_run(FLUSH_X, descend=False)
    fly_open = fly_run(BASE_X + 4.5, descend=False)
    fly_wall_dz = forward_dz(fly_wall)
    fly_open_dz = forward_dz(fly_open)
    assert fly_open_dz > 0.2, (
        f"open-air flight too slow: {fly_open_dz}; rows={fly_open[:6]} tail={fly_open[-3:]}"
    )
    # Flight IS still subject to the horizontal 0.4 multiplier ...
    assert fly_wall_dz < fly_open_dz * 0.5, (
        f"flight was not scaled by the honey wall: wall={fly_wall_dz} open={fly_open_dz}"
    )
    # ... but the vertical fly displacement is not clamped to -0.12.
    fly_descend = fly_run(FLUSH_X, descend=True)
    descending = [r["vy"] for r in fly_descend if r["vy"] < 0]
    assert descending and min(descending) < -0.3, f"creative descent clamped: {descending}"
    out["creative_fly"] = {
        "wall_rows": fly_wall,
        "open_rows": fly_open,
        "descend_rows": fly_descend,
        "wall_dz": fly_wall_dz,
        "open_dz": fly_open_dz,
        "min_vy": min(descending),
    }
    cmd_wait(client, "gamemode survival @s", ticks=3)

    return out


def run_water(client: ModClient) -> dict[str, Any]:
    """Water excludes the honey inside query via WasInWaterFlagComponent (no 0.4 scaling)."""

    def water_run(x: float, z: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        prep_tp(client, x, float(BASE_Y + 2.0), z, yaw=0.0, ticks=4)
        state = client.state()
        with RecordSession(client, period=1, watch=WATCH_MOVE) as rec:
            client.set_input(forward=1.0, ticks=12)
            client.wait_ticks(12)
        return compact_rows(rec.rows), state

    def forward_dz(rows: list[dict[str, Any]]) -> float:
        deltas = [
            round(b["z"] - a["z"], 6)
            for a, b in itertools.pairwise(rows)
            if b["tick"] == a["tick"] + 1
        ][-6:]
        return round(sum(deltas) / len(deltas), 6)

    water_wall, wall_state = water_run(FLUSH_X, WATER_WALL_Z)
    water_open, open_state = water_run(BASE_X + 7.5, WATER_CTRL_Z)
    assert wall_state.get("in_water"), f"wall swim did not enter water: {wall_state}"
    assert open_state.get("in_water"), f"open swim did not enter water: {open_state}"
    water_wall_dz = forward_dz(water_wall)
    water_open_dz = forward_dz(water_open)
    assert water_open_dz > 0.05, f"open-water swim too slow: {water_open_dz}"
    # WasInWater is entt::exclude_t at 0x1463c5220: water bypasses the 0.4 multiplier.
    assert abs(water_wall_dz - water_open_dz) < 0.015, (
        f"water swim beside honey differed unexpectedly: wall={water_wall_dz} open={water_open_dz}"
    )
    return {
        "in_water": bool(wall_state.get("in_water") and open_state.get("in_water")),
        "wall_rows": water_wall,
        "open_rows": water_open,
        "wall_dz": water_wall_dz,
        "open_dz": water_open_dz,
        "sink_with_wall": steady_value(water_wall, "vy"),
        "sink_open": steady_value(water_open, "vy"),
    }


def main() -> int:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {"groups": {}}
    with ModClient(timeout=30.0).connect() as client:
        client.wait_ready(timeout=30.0, require_world=True)
        with LabSession(
            client,
            clear_box=((BASE_X, BASE_Y, BASE_Z), (BASE_X + 2, BASE_Y + 2, BASE_Z + 2)),
            clear_inventory=True,
        ):
            results["groups"]["slide"] = group_slide(client)
            results["shapes"] = assert_shapes(client)
            results["block_properties"] = {
                "honey": block_type_flags(client, (WALL_X, BASE_Y, BASE_Z + 3)),
            }
            assert results["block_properties"]["honey"]["prevents_jumping"], (
                f"honey lost PreventsJumping: {results['block_properties']['honey']}"
            )
            clear_armor_and_wait(client)
            cmd_wait(client, "gamemode survival @s", ticks=2)
            heal_clean(client)
            results["slide"] = run_slide(client)
            results["slide_control"] = run_slide_control(client)

            results["groups"]["ground"] = group_ground(client)
            results["ground_walk"] = run_ground_walk(client)
            results["ascend_jump"] = run_ascend_jump(client)
            results["inside_boundary_walk"] = run_inside_boundary_walk(client)

            results["groups"]["topology"] = group_topology(client)
            results["inside_boundary"] = run_inside_boundary(client)

            results["groups"]["platform"] = group_platform(client)
            results["air_drag"] = measure_air_drag(client)
            results["groups"]["glide1"] = group_glide(client, (4,), "glide1")
            results["glide_g1"] = run_glide(
                client,
                platform_z=G1_PLATFORM_Z,
                wall_y=(BASE_Y + 4,),
                wall_z0=G1_WALL_Z0,
                wall_z1=G1_WALL_Z0 + 6,
                label="g1",
                input_ticks=14,
                drag=results["air_drag"]["drag"],
            )
            results["groups"]["glide2"] = group_glide(client, (4, 5), "glide2")
            results["glide_g2"] = run_glide(
                client,
                platform_z=G2_PLATFORM_Z,
                wall_y=(BASE_Y + 4, BASE_Y + 5),
                wall_z0=G2_WALL_Z0,
                wall_z1=G2_WALL_Z0 + 6,
                label="g2",
                input_ticks=14,
                drag=results["air_drag"]["drag"],
            )

            results["groups"]["pads"] = group_pads(client)
            results["block_properties"]["stone"] = block_type_flags(
                client, (BASE_X + 2, BASE_Y - 1, BASE_Z + 36)
            )
            results["block_properties"]["slime"] = block_type_flags(
                client, (BASE_X + 2, BASE_Y - 1, BASE_Z + 46)
            )
            assert not results["block_properties"]["stone"]["prevents_jumping"]
            results["top_walk"] = run_top_walk(client)
            results["jump"] = run_jump(client)
            results["fall_damage"] = run_fall_damage(client)

            results["groups"]["slide2"] = group_slide(client)
            results["groups"]["ground2"] = group_ground(client)
            results["slide_landing"] = run_slide_landing(client)
            results["special_dry"] = run_special_dry(client)

            results["groups"]["fallclear"] = group_fall_clear(client)
            results["fall_clear"] = run_fall_clear(client)

            results["groups"]["water"] = group_water(client)
            results["water"] = run_water(client)

    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved live results to {OUT_PATH}")
    print("shapes:", results["shapes"]["friction"], results["shapes"]["collision"])
    print("honey flags:", results["block_properties"]["honey"]["flags"])
    print("slide:", results["slide"]["steady_vy"], "control:", results["slide_control"]["min_vy"])
    print(
        "ground walk:",
        results["ground_walk"]["stone_dz"],
        results["ground_walk"]["one_high_dz"],
        results["ground_walk"]["two_high_dz"],
    )
    print("ascend ratio:", results["ascend_jump"]["ratio"])
    print("fall clear:", {k: v["damage"] for k, v in results["fall_clear"].items()})
    print("inside boundary:", {k: v["n"] for k, v in results["inside_boundary"].items()})
    print(
        "boundary walk:",
        results["inside_boundary_walk"]["inside_dz"],
        results["inside_boundary_walk"]["outside_dz"],
    )
    print("glide g1:", results["glide_g1"]["buckets"])
    print("glide g2:", results["glide_g2"]["buckets"])
    print(
        "top walk:",
        results["top_walk"]["stone_dz"],
        results["top_walk"]["honey_dz"],
        results["top_walk"]["slime_dz"],
        results["top_walk"]["honey_ratio"],
        results["top_walk"]["honey_sneak_ratio"],
        results["top_walk"]["slime_sneak_ratio"],
    )
    print(
        "jump:",
        results["jump"]["stone"]["rise"],
        results["jump"]["honey"]["rise"],
        results["jump"]["edge"]["rise"],
    )
    print(
        "fall:",
        [
            (x["damage"], y["damage"])
            for x, y in zip(
                results["fall_damage"]["stone"], results["fall_damage"]["honey"], strict=True
            )
        ],
    )
    print("slide landing:", results["slide_landing"]["damage"])
    print(
        "special dry:",
        {
            k: (v.get("steady_vy") if isinstance(v, dict) else v)
            for k, v in results["special_dry"].items()
        },
    )
    print(
        "water:",
        results["water"]["wall_dz"],
        results["water"]["open_dz"],
        results["water"]["in_water"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
