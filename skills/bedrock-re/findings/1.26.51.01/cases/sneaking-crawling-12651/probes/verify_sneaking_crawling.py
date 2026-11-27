"""Live verification for sneaking / crawling / headroom / edge gates (1.26.51.01).

Covers pose AABBs (with an open-sky swim pool), fine 0.01-inset headroom
gates, sneak/crawl/forced-sneak speeds, forced-sneak release & edge checks,
edge drop ladder (0.5 / 0.5625 / 0.625 / 1.0), trapdoor & swim-out crawl
entries, and crawling auto-step (0.375 daylight detector).

Raw rows are written to ``out/sneaking-crawling-1.26.51.01.json``.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "driver").is_dir())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.harness.client import ModClient  # noqa: E402
from driver.harness.lab import LabSession, record_session, settle  # noqa: E402

OUT = ROOT / "out" / "sneaking-crawling-1.26.51.01.json"
AABB_SHAPE = 0xBAC1B3CF
AABB_HEIGHT_OFF = 0x1C
ADF = 0xC67426F3
SNEAKING = 0x8918332C
SWIFT_SNEAK = 0xCDD62A2C
PLAT = -41  # platform block layer
GROUND = -40.0  # feet height on the platform
EDGE_Z = 247.0  # first empty column past the platform (last block sits at z=246)

WATCH_H = [
    {"component_id": AABB_SHAPE, "offset": AABB_HEIGHT_OFF, "size": 4, "name": "h", "fmt": "f32"}
]

RESULTS: dict[str, Any] = {}


def fill_box(
    client: ModClient,
    lo: tuple[int, int, int],
    hi: tuple[int, int, int],
    block: str,
    states: str = "",
) -> None:
    """``/fill`` a box in slabs and verify corner read-back."""
    nx = hi[0] - lo[0] + 1
    ny = hi[1] - lo[1] + 1
    per_layer = max(1, nx * ny)
    step = max(1, 30000 // per_layer)
    want = block.split(":")[-1]
    suffix = f" {states}" if states else ""
    corners = [[lo[0], lo[1], lo[2]], [hi[0], hi[1], hi[2]]]
    rows: list[dict[str, Any]] = []
    for attempt in range(4):
        z = lo[2]
        while z <= hi[2]:
            z2 = min(z + step - 1, hi[2])
            client.command(f"fill {lo[0]} {lo[1]} {z} {hi[0]} {hi[1]} {z2} {block}{suffix}")
            z = z2 + 1
        client.wait_ticks(8 + attempt * 8)
        rows = client.blocks(corners).get("blocks", [])
        if rows and all(str(r.get("name", "")).endswith(want) for r in rows):
            return
    raise AssertionError(f"fill {block}{suffix} {lo}..{hi} did not land: {rows}")


def setblock_checked(client: ModClient, pos: tuple[int, int, int], block: str) -> None:
    x, y, z = pos
    want = block.split()[0].split(":")[-1]
    rows: list[dict[str, Any]] = []
    for attempt in range(4):
        client.command(f"setblock {x} {y} {z} {block}")
        client.wait_ticks(4 + attempt * 4)
        rows = client.blocks([[x, y, z]]).get("blocks", [])
        if rows and str(rows[0].get("name", "")).endswith(want):
            return
    raise AssertionError(f"setblock {x} {y} {z} {block} did not land: {rows}")


def collision(client: ModClient, pos: tuple[int, int, int]) -> tuple[float, float, str]:
    rows = client.block_shapes([pos], entity_mode="none").get("blocks", [])
    assert rows, f"block_shapes returned no row for {pos}"
    row = rows[0]
    box = row.get("collision") or {}
    lo = float((box.get("min") or [0, 0, 0])[1])
    hi = float((box.get("max") or [0, 0, 0])[1])
    return lo, hi, str(row.get("name"))


def floor_top(client: ModClient, x: int, z: int) -> tuple[float, str]:
    """Top of whatever the player stands on: partial block on GROUND if present, else PLAT."""
    _, hi, name = collision(client, (x, PLAT + 1, z))
    if name != "minecraft:air" and PLAT + 1.0 - 0.01 < hi <= PLAT + 2.0 + 0.01:
        return hi, name
    _, hi, name = collision(client, (x, PLAT, z))
    return hi, name


def aabb(client: ModClient) -> dict[str, Any]:
    st = client.state()
    row = client.entity(st["runtime_id"])
    size = row.get("aabb", {}).get("size") or []
    return {
        "tick": st.get("tick"),
        "feet_y": st.get("feet_y"),
        "head_y": st.get("head_y"),
        "sneaking": bool(st.get("sneaking")),
        "input_sneak": bool((st.get("input") or {}).get("sneak")),
        "swimming": bool(st.get("swimming")),
        "sprinting": bool(st.get("sprinting")),
        "size": [round(float(v), 6) for v in size],
        "height": round(float(size[1]), 6) if len(size) > 1 else None,
        "x": st.get("x"),
        "z": st.get("z"),
        "health": st.get("health"),
    }


def adf_bits(client: ModClient) -> dict[str, Any]:
    """Raw ActorDataFlagComponent words for inspection."""
    st = client.state()
    rid = int(st["runtime_id"])
    try:
        comp = client.ecs_component(rid, ADF, length=24)
    except Exception as err:
        return {"error": str(err)}
    raw = bytes.fromhex(str(comp.get("hex", "")))
    return {
        "hex": raw.hex(),
        "bit1": bool(raw[0] & 2) if raw else None,
        "bit6": bool(raw[0] & 0x40) if raw else None,
    }


def tp(client: ModClient, x: float, y: float, z: float, yaw: float = -90.0) -> None:
    client.teleport(x, y, z, yaw=yaw, pitch=0.0)
    client.wait_ticks(3)


def pose_at(client: ModClient, x: float, y: float, z: float, *, wait: int = 6) -> dict[str, Any]:
    tp(client, x, y, z)
    client.wait_ticks(wait)
    return aabb(client)


def run_speed(
    client: ModClient,
    *,
    label: str,
    x: float,
    z: float,
    yaw: float,
    ticks: int = 40,
    sneak: bool = False,
    sprint: bool = False,
    expect_height: float | None = None,
    y: float = GROUND,
) -> dict[str, Any]:
    tp(client, x, y, z, yaw=yaw)
    got = settle(client, ticks=4, input_sprint=False, sprinting=False)
    assert got["ok"], f"{label}: not settled {got}"
    start = aabb(client)
    with record_session(client, period=1, watch=WATCH_H) as session:
        client.set_input(forward=1.0, sprint=sprint, sneak=sneak, ticks=ticks)
        client.wait_ticks(ticks + 6)
        client.set_input(ticks=2)
    rows = session.rows
    moving = [r for r in rows if abs(r["vz"]) > 1e-4 or abs(r["vx"]) > 1e-4]
    assert len(moving) >= ticks // 3, f"{label}: only {len(moving)} moving rows"
    heights_all = [round(float(r["h"]), 3) for r in moving if r.get("h")]
    modal = statistics.mode(heights_all) if heights_all else None
    same = [r for r in moving if r.get("h") and abs(float(r["h"]) - modal) < 1e-3]
    pool = same or moving
    vals = [max(abs(r["vx"]), abs(r["vz"])) for r in pool]
    windows = [vals[i : i + 8] for i in range(max(1, len(vals) - 7))]
    speed = max(statistics.median(w) for w in windows)
    if expect_height is not None:
        assert modal is not None and abs(modal - expect_height) < 0.01, (label, modal, start)
    return {
        "label": label,
        "speed": round(speed, 9),
        "height": modal,
        "start_height": start["height"],
        "sneak_rows": sum(1 for r in pool[-12:] if r["sneak"]),
        "sprint_rows": sum(1 for r in pool[-12:] if r["sprint"]),
        "sample": [
            {
                "tick": r["tick"],
                "x": round(r["x"], 4),
                "z": round(r["z"], 4),
                "vx": round(r["vx"], 9),
                "vz": round(r["vz"], 9),
                "h": round(float(r.get("h") or 0), 5),
                "sneak": bool(r["sneak"]),
            }
            for r in pool[-12:]
        ],
    }


def main() -> int:
    with ModClient() as client, LabSession(client, clear_box=((196, -42, 190), (254, -28, 262))):
        # Build clean floating platform: top at -40.0 (PLAT=-41), last column z=246 (EDGE_Z=247.0)
        client.teleport(215.5, GROUND, 215.5)
        client.wait_ticks(12)
        client.command("gamerule falldamage false")
        fill_box(client, (198, -43, 190), (252, -43, 262), "stone")
        fill_box(client, (200, PLAT, 192), (250, PLAT, 246), "stone")
        fill_box(client, (196, -40, 190), (254, -33, 262), "air")
        fill_box(client, (196, -42, 247), (254, -41, 262), "air")
        tp(client, 205.5, GROUND, 205.5)
        settle(client)
        probe = aabb(client)
        assert abs(float(probe["feet_y"]) - GROUND) < 0.05, probe

        # -- A. pose sizes -------------------------------------------------
        RESULTS["pose_stand"] = {**pose_at(client, 205.5, GROUND, 205.5), "adf": adf_bits(client)}
        assert abs(RESULTS["pose_stand"]["height"] - 1.8) < 0.01, RESULTS["pose_stand"]

        client.set_input(sneak=True, ticks=30)
        client.wait_ticks(10)
        RESULTS["pose_sneak"] = {**aabb(client), "adf": adf_bits(client)}
        assert abs(RESULTS["pose_sneak"]["height"] - 1.49) < 0.01, RESULTS["pose_sneak"]
        assert RESULTS["pose_sneak"]["sneaking"], RESULTS["pose_sneak"]
        client.set_input(ticks=4)
        client.wait_ticks(8)

        # crawl: 1.0 gap corridor (teleport in)
        fill_box(client, (210, -39, 226), (226, -39, 226), "stone")
        c_lo, _, _ = collision(client, (218, -39, 226))
        f_hi, f_name = floor_top(client, 218, 226)
        RESULTS["pose_crawl"] = {
            **pose_at(client, 218.5, GROUND, 226.5),
            "adf": adf_bits(client),
            "gap": round(c_lo - f_hi, 6),
            "floor_top": f_hi,
            "floor_block": f_name,
            "ceil_bottom": c_lo,
        }
        assert abs(RESULTS["pose_crawl"]["height"] - 0.6) < 0.01, RESULTS["pose_crawl"]

        # swim: OPEN-SKY 3-deep pool (walls end at y=-38, sky at y>=-37 is air!)
        fill_box(client, (230, -42, 220), (238, -38, 228), "stone")
        fill_box(client, (230, -37, 220), (238, -34, 228), "air")
        fill_box(client, (231, -40, 221), (237, -38, 227), "water")
        client.set_input(ticks=6)
        cleared = settle(client, ticks=4, on_ground=None, include_vertical=False)
        assert cleared["ok"], cleared
        assert not bool(client.state().get("sneaking")), "sneak leaked into the swim fixture"
        tp(client, 232.0, -38.5, 224.5, yaw=-90.0)
        client.set_input(forward=1.0, sprint=True, ticks=30)
        client.wait_ticks(12)
        RESULTS["pose_swim"] = aabb(client)
        client.set_input(ticks=4)
        client.wait_ticks(6)
        assert RESULTS["pose_swim"]["swimming"], RESULTS["pose_swim"]
        assert not RESULTS["pose_swim"]["input_sneak"], RESULTS["pose_swim"]
        assert not RESULTS["pose_swim"]["sneaking"], RESULTS["pose_swim"]
        assert abs(RESULTS["pose_swim"]["height"] - 0.6) < 0.01, RESULTS["pose_swim"]
        # restore platform over the temporary pose_swim pool
        fill_box(client, (230, -42, 220), (238, -42, 228), "stone")
        fill_box(client, (230, PLAT, 220), (238, PLAT, 228), "stone")
        fill_box(client, (230, GROUND, 220), (238, -34, 228), "air")

        # -- B. fine headroom ladder (0.01-inset gates: 1.79 / 1.48 / 0.59) --
        ladder = []
        cases = [
            ("g2.000", -38, None, ""),
            ("g1.9375", -38, "white_carpet", ""),
            ("g1.8175", -38, "trapdoor", '["open_bit"=false,"upside_down_bit"=false]'),
            ("g1.750", -38, "snow_layer", '["height"=2]'),
            ("g1.500", -38, "normal_stone_slab", ""),
            ("g1.4375", -38, "stonecutter_block", ""),
            ("g0.625", -39, "daylight_detector", ""),
            ("g0.5625", -39, "campfire", ""),
            ("g0.500", -39, "normal_stone_slab", ""),
        ]
        for i, (tag, ceil_y, block, states) in enumerate(cases):
            z = 194 + i * 2
            fill_box(client, (210, ceil_y, z), (226, ceil_y, z), "stone")
            if block:
                fill_box(client, (210, PLAT + 1, z), (226, PLAT + 1, z), block, states)
            c_lo, _, ceil_name = collision(client, (218, ceil_y, z))
            f_hi, floor_name = floor_top(client, 218, z)
            got = pose_at(client, 218.5, f_hi, z + 0.5)
            ladder.append(
                {
                    "tag": tag,
                    "ceil_y": ceil_y,
                    "floor_block": floor_name,
                    "ceil_block": ceil_name,
                    "gap": round(c_lo - f_hi, 6),
                    "floor_top": round(f_hi, 6),
                    "ceil_bottom": round(c_lo, 6),
                    "height": got["height"],
                    "sneaking": got["sneaking"],
                    "feet_y": got["feet_y"],
                    "z": round(float(got["z"]), 4),
                    "health": got["health"],
                }
            )
            tp(client, 205.5, GROUND, 205.5)
        RESULTS["ladder"] = ladder
        ladder_by_tag = {r["tag"]: r for r in ladder}
        assert abs(ladder_by_tag["g1.8175"]["height"] - 1.8) < 0.01, ladder_by_tag["g1.8175"]
        assert abs(ladder_by_tag["g1.750"]["gap"] - 1.75) < 0.01, ladder_by_tag["g1.750"]
        assert abs(ladder_by_tag["g1.750"]["height"] - 1.49) < 0.01, ladder_by_tag["g1.750"]
        assert abs(ladder_by_tag["g1.500"]["height"] - 1.49) < 0.01, ladder_by_tag["g1.500"]
        assert abs(ladder_by_tag["g1.4375"]["height"] - 0.6) < 0.01, ladder_by_tag["g1.4375"]
        assert abs(ladder_by_tag["g0.625"]["height"] - 0.6) < 0.01, ladder_by_tag["g0.625"]

        # -- C. entry paths ------------------------------------------------
        entry: dict[str, Any] = {"gap": 1.0}
        fill_box(client, (206, GROUND, 235), (242, -37, 237), "air")
        fill_box(client, (214, -39, 236), (240, -39, 236), "stone")
        tp(client, 208.5, GROUND, 236.5)
        client.set_input(forward=1.0, ticks=50)
        client.wait_ticks(46)
        st = client.state()
        entry["walk_in"] = {"x": round(float(st["x"]), 4), "height": aabb(client)["height"]}
        client.set_input(ticks=3)
        client.wait_ticks(4)
        tp(client, 208.5, GROUND, 236.5)
        client.set_input(forward=1.0, sneak=True, ticks=110)
        client.wait_ticks(100)
        st = client.state()
        entry["sneak_in"] = {"x": round(float(st["x"]), 4), "height": aabb(client)["height"]}
        client.set_input(ticks=3)
        client.wait_ticks(4)
        tp(client, 218.5, GROUND, 236.5)
        with record_session(client, period=1, watch=WATCH_H) as session:
            client.wait_ticks(8)
        entry["teleport_in"] = [
            {"tick": r["tick"], "h": round(float(r.get("h") or 0), 5), "x": round(r["x"], 3)}
            for r in session.rows
        ]
        RESULTS["entry"] = entry
        assert entry["walk_in"]["x"] < 214.0, entry["walk_in"]
        assert entry["sneak_in"]["x"] < 214.0, entry["sneak_in"]
        assert entry["teleport_in"], "teleport-in produced no rows"
        assert min(r["h"] for r in entry["teleport_in"]) < 1.0, entry["teleport_in"][:6]
        fill_box(client, (206, GROUND, 235), (242, -37, 237), "air")

        # -- D. speeds: walking, manual sneak, forced sneak, crawl ----------
        fill_box(client, (198, PLAT, 238), (250, PLAT, 242), "stone")
        fill_box(client, (198, GROUND, 238), (250, -35, 242), "air")
        speeds = [
            run_speed(client, label="walk", x=206.5, z=240.5, yaw=-90.0, expect_height=1.8),
            run_speed(
                client,
                label="sneak",
                x=206.5,
                z=240.5,
                yaw=-90.0,
                sneak=True,
                expect_height=1.49,
            ),
        ]
        fill_box(client, (210, -39, 238), (247, -39, 242), "stone")
        speeds.append(
            run_speed(client, label="crawl", x=212.5, z=240.5, yaw=-90.0, expect_height=0.6)
        )
        speeds.append(
            run_speed(
                client,
                label="crawl+sneakkey",
                x=212.5,
                z=240.5,
                yaw=-90.0,
                sneak=True,
                expect_height=0.6,
            )
        )
        speeds.append(
            run_speed(
                client,
                label="crawl+sprintkey",
                x=212.5,
                z=240.5,
                yaw=-90.0,
                sprint=True,
                expect_height=0.6,
            )
        )

        for level in (1, 2, 3):
            client.clear_inventory()
            slot = int(client.state().get("selected_slot", 0) or 0)
            client.set_item(slot, "minecraft:leather_leggings")
            client.wait_ticks(2)
            ench = client.command(f"/enchant @s swift_sneak {level}")
            client.wait_ticks(2)
            client.equip("legs", slot=slot)
            client.wait_ticks(3)
            rid = int(client.state()["runtime_id"])
            try:
                comp = client.ecs_component(rid, SWIFT_SNEAK, length=8)
            except Exception as err:
                comp = {"error": str(err)}
            RESULTS.setdefault("armor", {})[f"ss{level}"] = {
                "enchant_reply": ench,
                "component": comp,
                "armor": client.inventory().get("armor"),
            }
            speeds.append(
                run_speed(
                    client,
                    label=f"sneak+ss{level}",
                    x=206.5,
                    z=240.5,
                    yaw=-90.0,
                    sneak=True,
                    ticks=44,
                    expect_height=1.49,
                )
            )
            speeds.append(
                run_speed(
                    client,
                    label=f"crawl+ss{level}",
                    x=212.5,
                    z=240.5,
                    yaw=-90.0,
                    ticks=44,
                    expect_height=0.6,
                )
            )
        client.clear_inventory()

        # forced sneak: 1.5 gap corridor (ceiling -38, slab floor 0.5 up to x=240; open platform continues to x=250)
        fill_box(client, (206, GROUND, 238), (250, -35, 242), "air")
        fill_box(client, (210, -38, 238), (240, -38, 242), "stone")
        fill_box(client, (210, -40, 238), (240, -40, 242), "normal_stone_slab")
        c_lo, _, _ = collision(client, (230, -38, 240))
        f_hi, f_name = floor_top(client, 230, 240)
        forced_gap = round(c_lo - f_hi, 6)
        assert 1.48 <= forced_gap < 1.79, (forced_gap, f_name)
        client.set_input(ticks=6)
        tp(client, 212.5, f_hi, 240.5)
        client.wait_ticks(6)
        forced_pose = aabb(client)
        assert abs(forced_pose["height"] - 1.49) < 0.01, forced_pose
        speeds.append(
            run_speed(
                client,
                label=f"forced_sneak(gap={forced_gap})",
                x=212.5,
                z=240.5,
                yaw=-90.0,
                ticks=44,
                expect_height=1.49,
                y=f_hi,
            )
        )
        client.clear_inventory()
        slot = int(client.state().get("selected_slot", 0) or 0)
        client.set_item(slot, "minecraft:leather_leggings")
        client.wait_ticks(2)
        client.command("/enchant @s swift_sneak 3")
        client.wait_ticks(2)
        client.equip("legs", slot=slot)
        client.wait_ticks(3)
        forced_ss3 = run_speed(
            client,
            label="forced_sneak+ss3",
            x=212.5,
            z=240.5,
            yaw=-90.0,
            ticks=44,
            expect_height=1.49,
            y=f_hi,
        )
        speeds.append(forced_ss3)
        assert forced_ss3["speed"] > 0.15, forced_ss3
        client.clear_inventory()
        RESULTS["forced_sneak"] = {"gap": forced_gap, "pose": forced_pose}
        RESULTS["speeds"] = speeds
        sprint_run = next(r for r in speeds if r["label"] == "crawl+sprintkey")
        assert sprint_run["sprint_rows"] == 0, sprint_run

        # forced sneak: release the key under the low ceiling, then walk onto open platform x=241..250
        tp(client, 238.5, f_hi, 240.5)
        client.set_input(sneak=True, ticks=20)
        client.wait_ticks(8)
        holding = aabb(client)
        client.set_input(ticks=4)
        client.wait_ticks(10)
        released = aabb(client)
        client.set_input(forward=1.0, ticks=75)
        client.wait_ticks(80)
        client.set_input(ticks=3)
        client.wait_ticks(6)
        outside = aabb(client)
        RESULTS["forced_release"] = {
            "holding": holding,
            "released": released,
            "outside": outside,
        }
        assert holding["sneaking"] and abs(holding["height"] - 1.49) < 0.01, holding
        assert released["sneaking"] and abs(released["height"] - 1.49) < 0.01, released
        assert outside["x"] > 241.0 and abs(outside["feet_y"] - GROUND) < 0.05, outside
        assert not outside["sneaking"] and abs(outside["height"] - 1.8) < 0.01, outside

        # -- E. crawl mobility: jump ---------------------------------------
        fill_box(client, (200, GROUND, 236), (250, -33, 244), "air")
        fill_box(client, (200, PLAT, 236), (250, PLAT, 246), "stone")
        fill_box(client, (206, -39, 238), (220, -39, 242), "stone")
        tp(client, 214.5, GROUND, 240.5)
        client.wait_ticks(4)
        jump = {"before": aabb(client)}
        assert abs(jump["before"]["height"] - 0.6) < 0.01, jump["before"]
        client.set_input(jump=True, ticks=2)
        with record_session(client, period=1) as session:
            client.wait_ticks(16)
        jump["rows"] = [
            {"tick": r["tick"], "vy": round(r["vy"], 6), "feet": round(r["feet_y"], 5)}
            for r in session.rows
        ]
        jump["max_feet"] = max((r["feet_y"] for r in session.rows), default=None)
        RESULTS["crawl_jump"] = jump
        client.set_input(ticks=3)
        client.wait_ticks(6)

        # -- F. edge: walk / sneak / crawl / forced-sneak -------------------
        fill_box(client, (211, GROUND, 224), (217, -33, 252), "air")
        fill_box(client, (211, PLAT, 224), (217, PLAT, 246), "stone")
        fill_box(client, (211, -42, 247), (217, -41, 252), "air")
        edges = []

        def edge_run(
            tag: str,
            *,
            sneak: bool,
            y: float,
            x: float = 214.5,
            z: float = 238.5,
            expect_h: float | None = None,
            key: bool | None = None,
        ) -> dict[str, Any]:
            tp(client, x, y, z, yaw=0.0)
            client.wait_ticks(4)
            got0 = aabb(client)
            if expect_h is not None:
                assert abs(got0["height"] - expect_h) < 0.01, (tag, got0)
            client.set_input(forward=1.0, sneak=sneak if key is None else key, ticks=280)
            client.wait_ticks(250)
            got = aabb(client)
            client.set_input(ticks=3)
            client.wait_ticks(6)
            row = {
                "tag": tag,
                "edge_z": EDGE_Z,
                "z": round(float(got["z"]), 6),
                "overhang": round(float(got["z"]) + 0.3 - EDGE_Z, 6),
                "height": got["height"],
                "feet_y": got["feet_y"],
                "sneaking": got["sneaking"],
                "start_height": got0["height"],
                "start_sneaking": got0["sneaking"],
                "start_input_sneak": got0["input_sneak"],
            }
            edges.append(row)
            tp(client, x, y, z, yaw=0.0)
            return row

        walk_edge = edge_run("walk", sneak=False, y=GROUND)
        sneak_edge = edge_run("sneak", sneak=True, y=GROUND)
        assert walk_edge["feet_y"] < GROUND - 1.0, walk_edge
        assert sneak_edge["feet_y"] > GROUND - 0.05, sneak_edge

        # crawl edge: ceiling flush with the platform edge (z=226..246)
        fill_box(client, (212, -39, 226), (216, -39, 246), "stone")
        crawl_edge = edge_run("crawl", sneak=False, y=GROUND, z=240.5, expect_h=0.6)
        crawl_sneak_edge = edge_run("crawl+sneak", sneak=True, y=GROUND, z=240.5, expect_h=0.6)
        assert crawl_edge["feet_y"] < GROUND - 1.0, crawl_edge
        assert crawl_sneak_edge["feet_y"] > GROUND - 0.05, crawl_sneak_edge

        # forced sneak (1.5 gap, no key) at the edge: should fall (MoveInput+0x00 bit0 == 0)
        fill_box(client, (211, GROUND, 224), (217, -33, 252), "air")
        fill_box(client, (212, -40, 230), (216, -40, 246), "normal_stone_slab")
        fill_box(client, (212, -38, 230), (216, -38, 246), "stone")
        c_lo, _, _ = collision(client, (214, -38, 246))
        f_hi, f_name = floor_top(client, 214, 246)
        forced_edge_gap = round(c_lo - f_hi, 6)
        assert 1.48 <= forced_edge_gap < 1.79, (forced_edge_gap, f_name, f_hi, c_lo)
        forced_edge = edge_run("forced_sneak_no_key", sneak=False, y=f_hi, z=240.5)
        forced_edge_key = edge_run("forced_sneak_with_key", sneak=True, y=f_hi, z=240.5)
        RESULTS["edges"] = edges
        RESULTS["forced_edge"] = {
            "gap": forced_edge_gap,
            "floor_top": f_hi,
            "floor": f_name,
            "no_key": forced_edge,
            "with_key": forced_edge_key,
        }
        assert abs((forced_edge["start_height"] or 0) - 1.49) < 0.01, forced_edge
        assert not forced_edge["start_input_sneak"], forced_edge
        assert forced_edge["feet_y"] < f_hi - 0.5, forced_edge
        assert abs(forced_edge_key["feet_y"] - f_hi) < 0.05, forced_edge_key
        assert EDGE_Z < forced_edge_key["z"] < EDGE_Z + 0.35, forced_edge_key
        fill_box(client, (211, GROUND, 224), (217, -33, 252), "air")

        # -- G. edge drop ladder: 0.5 / 0.5625 / 0.625 / 1.0 ----------------
        drops = [
            ("drop0.500", "normal_stone_slab"),
            ("drop0.5625", "campfire"),
            ("drop0.625", "daylight_detector"),
            ("drop1.000", "air"),
        ]
        fill_box(client, (222, GROUND, 234), (240, -33, 262), "air")
        fill_box(client, (222, PLAT, 234), (240, PLAT, 246), "stone")
        fill_box(client, (222, -42, 247), (240, -41, 262), "air")
        drop_rows = []
        for i, (tag, block) in enumerate(drops):
            x = 224 + i * 3
            fill_box(client, (x, -42, 247), (x + 2, -42, 260), "stone")
            fill_box(client, (x, -41, 247), (x + 2, -41, 260), block)
            _, top, name = collision(client, (x + 1, -41, 249))
            if name == "minecraft:air":
                _, top, name = collision(client, (x + 1, -42, 249))
            drop = round(GROUND - top, 6)
            tp(client, x + 1.5, GROUND, 238.5, yaw=0.0)
            start = aabb(client)
            client.set_input(forward=1.0, sneak=True, ticks=280)
            client.wait_ticks(250)
            got = aabb(client)
            client.set_input(ticks=3)
            client.wait_ticks(6)
            drop_rows.append(
                {
                    "tag": tag,
                    "block": name,
                    "support_top": round(top, 6),
                    "drop": drop,
                    "z": round(float(got["z"]), 6),
                    "feet_y": round(float(got["feet_y"]), 6),
                    "height": got["height"],
                    "start_height": start["height"],
                }
            )
            tp(client, x + 1.5, GROUND, 238.5, yaw=0.0)
        RESULTS["edge_drops"] = drop_rows
        assert [r["drop"] for r in drop_rows] == [0.5, 0.5625, 0.625, 1.0], drop_rows
        for row in drop_rows:
            if row["drop"] <= 0.5625 + 1e-6:
                assert row["z"] > EDGE_Z + 1.0, row
                assert abs(row["feet_y"] - (GROUND - row["drop"])) < 0.05, row
            else:
                assert EDGE_Z < row["z"] < EDGE_Z + 0.35, row
                assert row["feet_y"] > GROUND - 0.05, row

        # -- H. real crawl entries -----------------------------------------
        entries = {}
        # H1: trapdoor closed above a standing player's head (205, -39, 210)
        fill_box(client, (203, GROUND, 208), (207, -37, 212), "air")
        tp(client, 205.5, GROUND, 210.5)
        settle(client)
        h1_before = aabb(client)
        assert abs(h1_before["height"] - 1.8) < 0.01, h1_before
        with record_session(client, period=1, watch=WATCH_H) as session:
            setblock_checked(
                client,
                (205, -39, 210),
                'trapdoor ["direction"=0,"open_bit"=false,"upside_down_bit"=false]',
            )
            client.wait_ticks(12)
        h1_after = aabb(client)
        entries["head_trapdoor"] = [
            {"tick": r["tick"], "h": round(float(r.get("h") or 0), 5), "x": round(r["x"], 3)}
            for r in session.rows
        ]
        entries["head_trapdoor_before"] = h1_before
        entries["head_trapdoor_after"] = h1_after
        assert abs(h1_after["height"] - 0.6) < 0.01, h1_after
        assert min(r["h"] for r in entries["head_trapdoor"]) < 0.7, entries["head_trapdoor"]
        fill_box(client, (203, GROUND, 208), (207, -37, 212), "air")

        # H2: swim out of an open-sky pool into a dry 1.0-gap tunnel on level floor top=-41.0 (+Z, yaw=0.0)
        fill_box(client, (236, -43, 214), (244, -42, 235), "stone")  # level floor top=-41.0
        fill_box(client, (236, -41, 214), (244, -39, 235), "stone")  # perimeter & tunnel walls
        fill_box(client, (237, -39, 215), (243, -34, 221), "air")  # open sky above pool
        fill_box(client, (237, -41, 223), (243, -41, 235), "air")  # carve dry tunnel first
        fill_box(client, (237, -41, 222), (243, -41, 222), "fence_gate", '["open_bit"=true]')
        # tunnel ceiling sits at y=-40, z=222..235 (bottom=-40.0, gap=1.0)
        fill_box(client, (237, -41, 215), (243, -40, 221), "water")  # pour water last
        tp(client, 240.5, -40.5, 217.5, yaw=0.0)
        client.set_input(forward=1.0, sprint=True, ticks=80)
        with record_session(client, period=1, watch=WATCH_H) as session:
            client.wait_ticks(75)
        h2_after = aabb(client)
        client.set_input(ticks=3)
        client.wait_ticks(6)
        entries["swim_out"] = [
            {
                "tick": r["tick"],
                "h": round(float(r.get("h") or 0), 5),
                "z": round(r["z"], 3),
                "water": r["in_water"],
            }
            for r in session.rows
        ]
        entries["swim_out_after"] = h2_after
        assert not h2_after["swimming"], h2_after
        assert h2_after["z"] > 224.0, h2_after
        assert abs(h2_after["height"] - 0.6) < 0.01, h2_after
        RESULTS["crawl_entries"] = entries

        # -- I. crawl auto-step (0.375 daylight detector inside 1.0 tunnel) -
        fill_box(client, (202, PLAT, 231), (232, PLAT, 233), "stone")
        fill_box(client, (202, GROUND, 231), (232, -37, 233), "air")
        fill_box(client, (204, -39, 231), (228, -39, 233), "stone")
        fill_box(client, (212, -40, 231), (226, -40, 233), "daylight_detector")
        c_lo, _, _ = collision(client, (215, -39, 232))
        f_hi, f_name = floor_top(client, 206, 232)
        step_top, step_name = floor_top(client, 215, 232)
        assert f_name == "minecraft:stone", (f_hi, f_name)
        assert step_name != "minecraft:air", step_name
        assert 0.30 < step_top - f_hi < 0.45, (step_top, f_hi, step_name)
        assert c_lo - step_top >= 0.59, (c_lo, step_top)
        tp(client, 206.5, GROUND, 232.5, yaw=-90.0)
        client.wait_ticks(4)
        pre = aabb(client)
        assert abs(pre["height"] - 0.6) < 0.01, pre
        with record_session(client, period=1, watch=WATCH_H) as session:
            client.set_input(forward=1.0, ticks=150)
            client.wait_ticks(145)
            client.set_input(ticks=3)
            client.wait_ticks(4)
        step_rows = session.rows
        after = aabb(client)
        assert after["x"] > 213.0, after
        assert abs(after["feet_y"] - step_top) < 0.05, (after, step_top)
        assert abs(after["height"] - 0.6) < 0.01, after
        RESULTS["crawl_step"] = {
            "ceil_bottom": c_lo,
            "base_floor": f_hi,
            "step_top": step_top,
            "step_block": step_name,
            "step_height": round(step_top - f_hi, 6),
            "gap_above_step": round(c_lo - step_top, 6),
            "before": pre,
            "after": after,
            "max_feet": max((r["feet_y"] for r in step_rows), default=None),
        }

    OUT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    print("WROTE", OUT)
    print(
        "POSES",
        json.dumps(
            {
                k: RESULTS[k].get("size")
                for k in ("pose_stand", "pose_sneak", "pose_crawl", "pose_swim")
            }
        ),
    )
    print("LADDER", json.dumps([(r["gap"], r["height"], r["sneaking"]) for r in RESULTS["ladder"]]))
    print("SPEEDS", json.dumps([(r["label"], r["speed"], r["height"]) for r in RESULTS["speeds"]]))
    print("EDGES", json.dumps(RESULTS["edges"]))
    print("DROPS", json.dumps(RESULTS["edge_drops"]))
    print("FORCED", json.dumps(RESULTS["forced_edge"]))
    print("STEP", json.dumps(RESULTS["crawl_step"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
