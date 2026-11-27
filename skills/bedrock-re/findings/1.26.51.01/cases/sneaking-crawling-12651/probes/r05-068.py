"""Round 5 tail closure for sneaking-crawling-12651 (1.26.51.01).

Covers the remaining items of the note's §7:

1. ground **first-tick** horizontal acceleration for walk / manual sneak /
   forced sneak / crawl / sneak+SwiftSneakIII / crawl+SwiftSneakIII, checked
   against the ``0.3 + 0.15 x level`` multiplier.  The Swift Sneak bonus is
   gated by the **same-tick scheduling order** (``SneakingSystem`` runs before
   ``SneakTriggerActionSystem`` in group ``0x14681DA00``): a same-tick
   ``W + Sneak`` press keeps the plain ``0.3`` multiplier on the first moving
   tick, while a sneak pose that is already live (pre-roll or forced crawl)
   gets ``0.3 + 0.15 x level`` on the first moving tick;
2. **air control** right after a jump: walk vs sneak at Swift Sneak 0 / I / II /
   III.  The per-tick acceleration is ``a_i = v[i+1] - 0.91 * v[i]``
   (``a_0 = v[0]``), so the measured values land on the exact
   ``0.0196 x {0.3, 0.45, 0.60, 0.75}`` instead of the un-de-dragged deltas;
3. a **45 degree diagonal sneak approach to a platform corner** (edge guard on
   both axes at once), with the stop point checked against the ``0.025`` inset
   and the ``0.05`` back-off quantum.  The resting centre is *past* the edge
   (``edge + 0.275`` within one ``0.05`` quantum);
4. (supporting) the one-axis stop points for the same fixture.

``set_input(..., ticks=N)`` releases the key when the countdown reaches zero,
so every pre-roll request outlives the moving window and the moving request
re-sends ``sneak=True`` to keep the key held without a release/re-press gap.

All scenes use ``LabSession`` (input lock), ``settle`` and hard assertions.
Raw rows are written to ``out/sneaking-crawling-tail-1.26.51.01.json``.
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
from driver.harness.lab import LabSession, RecordSession, settle  # noqa: E402

OUT = ROOT / "out" / "sneaking-crawling-tail-1.26.51.01.json"
AABB_SHAPE = 0xBAC1B3CF
SWIFT_SNEAK = 0xCDD62A2C
PLAT = -41
GROUND = -40.0
WALK_FIRST = 0.098  # 0.1 speed x 0.98, re-measured here
AIR_BASE = 0.0196  # airborne first-tick walk acceleration
AIR_DRAG = 0.91  # horizontal air drag per tick (0x14E76D2D8 = 0.91f)
WATCH_H = [{"component_id": AABB_SHAPE, "offset": 0x1C, "size": 4, "name": "h", "fmt": "f32"}]

RESULTS: dict[str, Any] = {}


def fill_box(
    client: ModClient,
    lo: tuple[int, int, int],
    hi: tuple[int, int, int],
    block: str,
    states: str = "",
) -> None:
    """Fill a box in <=48-block slabs and verify **every** slab corner.

    This build rejects a single ``fill`` once an axis span grows past ~60
    blocks (x=61 and z=79 fail, x=60 and z=63 work), and a rejected command
    only shows up as ``success: false``.  Splitting on every axis and reading
    back each slab is the only way to know the fixture really landed.
    """
    want = block.split(":")[-1]
    suffix = f" {states}" if states else ""
    xs: list[tuple[int, int]] = []
    x = lo[0]
    while x <= hi[0]:
        x2 = min(x + 47, hi[0])
        xs.append((x, x2))
        x = x2 + 1
    zs: list[tuple[int, int]] = []
    z = lo[2]
    while z <= hi[2]:
        z2 = min(z + 47, hi[2])
        zs.append((z, z2))
        z = z2 + 1
    pending = [(sx, sz) for sx in xs for sz in zs]
    rows: list[dict[str, Any]] = []
    for attempt in range(5):
        for sx, sz in pending:
            # ``fill`` needs every chunk in the box to be live; a slab up to 48
            # blocks across is only reliable with the player parked above it.
            client.teleport(
                (sx[0] + sx[1]) / 2 + 0.5,
                max(hi[1] + 6.0, 55.0),
                (sz[0] + sz[1]) / 2 + 0.5,
                yaw=0.0,
                pitch=0.0,
            )
            client.wait_ticks(2)
            client.command(f"fill {sx[0]} {lo[1]} {sz[0]} {sx[1]} {hi[1]} {sz[1]} {block}{suffix}")
            client.wait_ticks(3)
        client.wait_ticks(6 + attempt * 6)
        failed: list[tuple[tuple[int, int], tuple[int, int]]] = []
        for sx, sz in pending:
            ok = True
            for corner in ([sx[0], lo[1], sz[0]], [sx[1], hi[1], sz[1]]):
                rows = client.blocks([corner]).get("blocks", [])
                if not (rows and str(rows[0].get("name", "")).endswith(want)):
                    ok = False
                    break
            if not ok:
                failed.append((sx, sz))
        if not failed:
            return
        pending = failed
    raise AssertionError(f"fill {block}{suffix} {lo}..{hi} left slabs: {pending} ({rows})")


def aabb(client: ModClient) -> dict[str, Any]:
    st = client.state()
    row = client.entity(st["runtime_id"])
    size = row.get("aabb", {}).get("size") or []
    return {
        "feet_y": st.get("feet_y"),
        "head_y": st.get("head_y"),
        "sneaking": bool(st.get("sneaking")),
        "input_sneak": bool((st.get("input") or {}).get("sneak")),
        "height": round(float(size[1]), 6) if len(size) > 1 else None,
        "x": st.get("x"),
        "z": st.get("z"),
    }


def clear_armor(client: ModClient) -> None:
    """Worn armor survives ``clear_inventory``; clear all four slots."""
    for slot in range(4):
        client.set_item(slot, "minecraft:air", count=0, container="armor")
    client.wait_ticks(3)


def warmup(client: ModClient, *, sneak: bool = True, ticks: int = 10) -> None:
    """Move once so the pose/equipment state is fully applied.

    This is **not** a Swift Sneak replication delay: the bonus is already in
    ``SneakingComponent`` once the sneak pose is live.  The warmup only makes
    sure the previous scene's inputs/velocity are gone before the measured
    start (``settle`` in the caller does the hard gate).
    """
    client.set_input(forward=1.0, sneak=sneak, ticks=ticks)
    client.wait_ticks(ticks + 2)
    client.set_input(ticks=2)
    client.wait_ticks(4)


def swift_sneak_level(client: ModClient) -> int | None:
    rid = int(client.state()["runtime_id"])
    try:
        comp = client.ecs_component(rid, SWIFT_SNEAK, length=8)
    except Exception:
        return None
    raw = bytes.fromhex(str(comp.get("hex", "")))
    return int.from_bytes(raw[:4], "little") if len(raw) >= 4 else None


def apply_swift_sneak(client: ModClient, level: int) -> dict[str, Any]:
    """Give leggings, enchant them, equip, and read the level back."""
    client.clear_inventory()
    slot = int(client.state().get("selected_slot", 0) or 0)
    client.set_item(slot, "minecraft:leather_leggings")
    client.wait_ticks(2)
    reply = client.command(f"/enchant @s swift_sneak {level}")
    client.wait_ticks(2)
    client.equip("legs", slot=slot)
    client.wait_ticks(3)
    rid = int(client.state()["runtime_id"])
    comp = client.ecs_component(rid, SWIFT_SNEAK, length=8)
    raw = bytes.fromhex(str(comp.get("hex", "")))
    got = int.from_bytes(raw[:4], "little") if len(raw) >= 4 else None
    assert got == level, (level, got, comp, reply)
    return {"level": got, "hex": comp.get("hex"), "reply": reply}


def first_ticks(
    client: ModClient,
    *,
    label: str,
    x: float,
    z: float,
    y: float = GROUND,
    yaw: float = 0.0,
    sneak: bool = False,
    pre_sneak: int = 0,
    ticks: int = 10,
    expect_before: float,
    expect_during: float,
) -> dict[str, Any]:
    """Standing start; report the first moving ticks, the pose and steady state.

    ``pre_sneak`` holds the sneak key for that many ticks *before* the forward
    key is added.  ``set_input(..., ticks=N)`` releases the key when the
    countdown reaches zero, so the pre-roll request must outlive both the
    pre-roll and the moving window; the moving request re-sends ``sneak=True``
    and keeps the key held without a release/re-press gap.

    ``SneakingSystem`` (0x14B192C50, group slot #3) runs before
    ``SneakTriggerActionSystem`` (0x14B190C50, slot #6) in group 0x14681DA00,
    so a same-tick ``W + Sneak`` press keeps the plain 0.3 multiplier on the
    first moving tick.  A sneak pose that is already live (pre-roll or forced
    crawl) gets ``0.3 + 0.15 x level`` on the first moving tick.
    """
    client.set_input(ticks=2)
    client.teleport(x, y, z, yaw=yaw, pitch=0.0)
    client.wait_ticks(6)
    got = settle(client, on_ground=True, input_sprint=False, sprinting=False)
    assert got["ok"], (label, got)
    before = aabb(client)
    assert before["feet_y"] > GROUND - 0.05, (label, "not on the platform", before)
    assert abs(before["height"] - expect_before) < 0.01, (label, before)
    with RecordSession(client, period=1, watch=WATCH_H) as rec:
        if pre_sneak:
            # Outlive the pre-roll *and* the moving window: a request ending
            # at ``pre_sneak`` would release the key right before the move.
            client.set_input(sneak=True, ticks=pre_sneak + ticks + 12)
            client.wait_ticks(pre_sneak)
            pose = aabb(client)
            assert pose["sneaking"], (label, "pre_sneak did not enter sneaking", pose)
            assert abs(float(pose["height"]) - expect_during) < 0.01, (label, pose)
        client.set_input(forward=1.0, sneak=sneak, ticks=ticks)
        client.wait_ticks(ticks + 4)
        client.set_input(ticks=2)
        client.wait_ticks(3)
    rows = rec.rows
    flags = [bool((r.get("input") or {}).get("w")) for r in rows]
    assert any(flags), (label, "no forward rows", len(rows))
    held = [r for r, f in zip(rows, flags, strict=True) if f]
    assert len(held) >= 6, (label, len(held))
    heights = [round(float(r["h"]), 4) for r in held if r.get("h")]
    assert heights, (label, "no height rows")
    pose = statistics.mode(heights)
    assert abs(pose - expect_during) < 0.01, (label, pose, heights)
    speeds = [(float(r["vx"]) ** 2 + float(r["vz"]) ** 2) ** 0.5 for r in held]
    # The recorded ``input.w`` flag flips one row before the body actually
    # moves, so the first *moving* row is the real first tick.
    moving = [s for s in speeds if s > 1e-6]
    assert moving, (label, "input held but never moved", speeds)
    first = moving[0]
    second = moving[1] if len(moving) > 1 else None
    steady = statistics.median(speeds[-4:])
    return {
        "label": label,
        "pre_sneak": pre_sneak,
        "before": before,
        "pose_during": pose,
        "first_speed": round(first, 9),
        "second_speed": round(second, 9) if second is not None else None,
        "steady_speed": round(steady, 9),
        "first_over_walk": round(first / WALK_FIRST, 6),
        "held_rows": len(held),
        "series": [round(s, 6) for s in speeds[:8]],
    }


def air_control(
    client: ModClient,
    *,
    label: str,
    x: float,
    z: float,
    y: float = GROUND,
    yaw: float = 0.0,
    sneak: bool = False,
    pre_sneak: int = 0,
    expect_before: float,
    expect_during: float = 1.49,
) -> dict[str, Any]:
    """Jump, then hold forward (+sneak) while airborne and read per-tick speed.

    ``pre_sneak`` keeps the sneak key held on the ground **and through the
    jump** (the pre-roll request outlives the air window), so the Swift Sneak
    multiplier is already live when the airborne window opens.  Without it the
    sneak key is first pressed after leaving the ground, which records the
    "new press in air" control.

    Horizontal acceleration is recovered with the air drag:
    ``a_i = v[i+1] - 0.91 * v[i]`` and ``a_0 = v[0]`` (``0.91f`` lives at
    ``0x14E76D2D8``).  Taking raw deltas instead understates every level by
    exactly the drag term.
    """
    client.set_input(ticks=2)
    client.teleport(x, y, z, yaw=yaw, pitch=0.0)
    client.wait_ticks(6)
    got = settle(client, on_ground=True, input_sprint=False, sprinting=False)
    assert got["ok"], (label, got)
    before = aabb(client)
    assert before["feet_y"] > GROUND - 0.05, (label, "not on the platform", before)
    assert abs(before["height"] - expect_before) < 0.01, (label, before)
    if pre_sneak:
        client.set_input(sneak=True, ticks=pre_sneak + 40)
        client.wait_ticks(pre_sneak)
        pose = aabb(client)
        assert pose["sneaking"], (label, "pre_sneak did not enter sneaking", pose)
        assert abs(float(pose["height"]) - expect_during) < 0.01, (label, pose)
    with RecordSession(client, period=1) as rec:
        client.key_event(32, True, via="hardware")
        client.wait_ticks(1)
        client.key_event(32, False, via="hardware")
        client.wait_ticks(2)
        airborne = not bool(client.state().get("on_ground"))
        client.set_input(forward=1.0, sneak=sneak, ticks=8)
        client.wait_ticks(10)
        client.set_input(ticks=2)
        client.wait_ticks(4)
    rows = rec.rows
    air = [r for r in rows if not r.get("on_ground")]
    assert airborne, (label, "jump did not leave the ground")
    assert len(air) >= 5, (label, len(air))
    forward = [r for r in air if bool((r.get("input") or {}).get("w"))]
    assert len(forward) >= 3, (label, len(forward))
    fspeeds = [(float(r["vx"]) ** 2 + float(r["vz"]) ** 2) ** 0.5 for r in forward]
    # The recorded ``input.w`` flag flips one row before the body actually
    # moves; drop the leading zero rows so ``v[0]`` is the first moving tick.
    moving = [s for s in fspeeds if s > 1e-6]
    assert moving, (label, "forward held in air but never moved", fspeeds)
    accels = [moving[0]] + [moving[i + 1] - AIR_DRAG * moving[i] for i in range(len(moving) - 1)]
    stable = accels[1:] if len(accels) > 1 else accels
    return {
        "label": label,
        "pre_sneak": pre_sneak,
        "airborne_at_input": airborne,
        "air_rows": len(air),
        "forward_air_rows": len(forward),
        "first_speed": round(moving[0], 9),
        "series": [round(s, 6) for s in fspeeds],
        "moving_series": [round(s, 6) for s in moving],
        "accels": [round(a, 9) for a in accels],
        "first_accel": round(accels[0], 9),
        "median_accel": round(statistics.median(stable), 9),
        "max_accel": round(max(stable), 9),
        "first_over_air_base": round(moving[0] / AIR_BASE, 6),
        "max_speed": round(max(fspeeds), 9),
    }


def corner_stop(
    client: ModClient,
    *,
    label: str,
    x: float,
    z: float,
    yaw: float,
    sneak: bool,
    ticks: int = 260,
) -> dict[str, Any]:
    """Run into a platform corner and report the resting centre.

    Sneak speed is ~0.046/tick per axis, so a corner 7.5 blocks away needs
    ~170 ticks; anything shorter just reports a mid-walk position.
    """
    client.set_input(ticks=2)
    client.teleport(x, GROUND, z, yaw=yaw, pitch=0.0)
    client.wait_ticks(6)
    got = settle(client, on_ground=True, input_sprint=False, sprinting=False)
    assert got["ok"], (label, got)
    start = aabb(client)
    assert start["feet_y"] > GROUND - 0.05, (label, "not on the island", start)
    client.set_input(forward=1.0, sneak=sneak, ticks=ticks)
    client.wait_ticks(ticks - 6)
    end = aabb(client)
    client.set_input(ticks=3)
    client.wait_ticks(6)
    return {
        "label": label,
        "yaw": yaw,
        "sneak": sneak,
        "start": [round(float(start["x"]), 6), round(float(start["z"]), 6)],
        "x": round(float(end["x"]), 6),
        "z": round(float(end["z"]), 6),
        "feet_y": end["feet_y"],
        "height": end["height"],
        "sneaking": end["sneaking"],
        "overlap_x": round(0.3 - (float(end["x"]) - 208.0 + 0.0), 6),
        "overlap_z": round(0.3 - (float(end["z"]) - 258.0 + 0.0), 6),
    }


def main() -> int:
    with ModClient() as client:
        # Park the player high above the fixture first: the clear box removes
        # the natural floor and a player who fell below the world makes later
        # fills fail.
        client.teleport(214.5, 60.0, 240.5, yaw=0.0, pitch=0.0)
        client.wait_ticks(6)
        with LabSession(client, clear_box=((190, -44, 180), (270, -26, 275))):
            fill_box(client, (196, PLAT, 190), (258, PLAT, 268), "stone")
            fill_box(client, (196, GROUND, 190), (258, -30, 268), "air")
            # Both low lanes are built once and never cleared mid-run: the
            # fixture is cleaned by LabSession on exit, and re-clearing a lane
            # between scenes only races the world's own block updates.
            fill_box(client, (200, -40, 214), (240, -40, 214), "normal_stone_slab")
            fill_box(client, (200, -38, 214), (240, -38, 214), "stone")
            fill_box(client, (200, -39, 220), (240, -39, 220), "stone")
            client.teleport(214.5, GROUND, 240.5, yaw=0.0, pitch=0.0)
            client.wait_ticks(14)
            landed = aabb(client)
            assert landed["feet_y"] > GROUND - 0.05, landed
            RESULTS["fixture"] = {
                "platform": {"x": [196, 258], "z": [190, 268], "top": GROUND},
                "walk_first_ref": WALK_FIRST,
                "air_base_ref": AIR_BASE,
            }

            # ---- 1. ground first-tick acceleration -----------------------
            # Leftover Swift Sneak leggings from an earlier session would
            # silently scale every "plain" row, so clear the worn armor first.
            clear_armor(client)
            client.clear_inventory()
            client.wait_ticks(3)
            assert swift_sneak_level(client) is None, "Swift Sneak leaked into the fixture"
            ground: list[dict[str, Any]] = []

            def add_ground(**kwargs: Any) -> None:
                ground.append(first_ticks(client, **kwargs))

            add_ground(label="walk", x=206.5, z=210.5, expect_before=1.8, expect_during=1.8)
            add_ground(
                label="sneak",
                x=206.5,
                z=210.5,
                sneak=True,
                expect_before=1.8,
                expect_during=1.49,
            )
            # forced sneak: 1.5 gap lane (slab floor + ceiling at -38, built above)
            add_ground(
                label="forced_sneak",
                x=206.5,
                z=214.5,
                y=-39.5,
                expect_before=1.49,
                expect_during=1.49,
            )
            # crawl: 1.0 gap lane (ceiling at -39, built above)
            add_ground(label="crawl", x=206.5, z=220.5, expect_before=0.6, expect_during=0.6)
            # swift sneak III on the open platform and in the crawl corridor
            armor = {"ss3": apply_swift_sneak(client, 3)}
            client.wait_ticks(10)
            warmup(client)
            add_ground(
                label="sneak+ss3",
                x=206.5,
                z=210.5,
                sneak=True,
                expect_before=1.8,
                expect_during=1.49,
            )
            # sneak held for 6 ticks first: the Swift Sneak bonus is live
            add_ground(
                label="sneak+ss3_held",
                x=206.5,
                z=210.5,
                sneak=True,
                pre_sneak=6,
                expect_before=1.8,
                expect_during=1.49,
            )
            add_ground(label="crawl+ss3", x=206.5, z=220.5, expect_before=0.6, expect_during=0.6)
            RESULTS["armor"] = armor
            RESULTS["ground_first_tick"] = ground

            by_label = {row["label"]: row for row in ground}
            walk_first = by_label["walk"]["first_speed"]
            walk_steady = by_label["walk"]["steady_speed"]
            for label, want in (
                ("walk", 1.0),
                ("sneak", 0.3),
                ("forced_sneak", 0.3),
                ("crawl", 0.3),
            ):
                row = by_label[label]
                # first moving tick already carries the full multiplier
                assert abs(row["first_speed"] / walk_first - want) < 0.03, (label, row)
            for label in ("sneak+ss3", "sneak+ss3_held", "crawl+ss3"):
                row = by_label[label]
                ratio = row["first_speed"] / walk_first
                # Same-tick W+Sneak keeps 0.3 (SneakingSystem runs before
                # SneakTriggerActionSystem); a live sneak pose / forced crawl
                # already carries 0.75 on the first moving tick.
                want = 0.3 if label == "sneak+ss3" else 0.75
                assert abs(ratio - want) < 0.03, (label, want, row)
                row["first_bonus_live"] = want == 0.75
            for label, want in (
                ("walk", 1.0),
                ("sneak", 0.3),
                ("forced_sneak", 0.3),
                ("crawl", 0.3),
                ("sneak+ss3", 0.75),
                ("sneak+ss3_held", 0.75),
                ("crawl+ss3", 0.75),
            ):
                row = by_label[label]
                # steady speed keeps the same ratio (round 3 §3.2 values)
                assert abs(row["steady_speed"] / walk_steady - want) < 0.06, (label, row)

            # ---- 2. air control (walk vs sneak, SS 0..III) ---------------
            clear_armor(client)
            client.clear_inventory()
            client.wait_ticks(4)
            air: list[dict[str, Any]] = [
                air_control(client, label="air_walk", x=206.5, z=250.5, expect_before=1.8),
                air_control(
                    client,
                    label="air_sneak_ss0",
                    x=206.5,
                    z=250.5,
                    sneak=True,
                    expect_before=1.8,
                ),
            ]
            for level in (1, 2, 3):
                apply_swift_sneak(client, level)
                client.wait_ticks(10)
                warmup(client)
                # held: sneak pose live before the jump; the first air tick
                # already carries 0.3 + 0.15 x level.
                air.append(
                    air_control(
                        client,
                        label=f"air_sneak_ss{level}_held",
                        x=206.5,
                        z=250.5,
                        sneak=True,
                        pre_sneak=8,
                        expect_before=1.8,
                    )
                )
                # new: sneak pressed only after leaving the ground; the first
                # air tick still sees the previous 0.3 multiplier, then the
                # same scheduling order catches up.
                air.append(
                    air_control(
                        client,
                        label=f"air_sneak_ss{level}_new",
                        x=206.5,
                        z=250.5,
                        sneak=True,
                        expect_before=1.8,
                    )
                )
            clear_armor(client)
            client.clear_inventory()
            RESULTS["air_control"] = air
            air_by = {row["label"]: row for row in air}
            walk_accel = air_by["air_walk"]["max_accel"]
            for label, want in (
                ("air_walk", 1.0),
                ("air_sneak_ss0", 0.3),
                ("air_sneak_ss1_held", 0.45),
                ("air_sneak_ss2_held", 0.6),
                ("air_sneak_ss3_held", 0.75),
                ("air_sneak_ss1_new", 0.45),
                ("air_sneak_ss2_new", 0.6),
                ("air_sneak_ss3_new", 0.75),
            ):
                row = air_by[label]
                row["accel_over_walk"] = round(row["max_accel"] / walk_accel, 6)
                row["first_accel_over_walk"] = round(row["first_accel"] / walk_accel, 6)
                # the same sneak multiplier scales the airborne acceleration;
                # with the 0.91 drag correction the tolerance is 0.02.
                assert abs(row["accel_over_walk"] - want) < 0.02, (label, row)
                assert row["air_rows"] >= 5, row
            # held vs new-press contrast: a live sneak pose is already at the
            # full multiplier on the first air tick; a new press starts at 0.3.
            for level in (1, 2, 3):
                held = air_by[f"air_sneak_ss{level}_held"]
                new = air_by[f"air_sneak_ss{level}_new"]
                want = 0.3 + 0.15 * level
                assert abs(held["first_accel_over_walk"] - want) < 0.02, held
                assert abs(new["first_accel_over_walk"] - 0.3) < 0.02, new

            # ---- 3. 45 degree corner (both axes clamped at once) ---------
            # island 12x12 with its +x/+z corner at (208.0, 258.0)
            fill_box(client, (196, GROUND, 246), (258, -30, 268), "air")
            # carve the platform layer so the island's +x/+z edges are real
            fill_box(client, (196, PLAT, 246), (258, PLAT, 268), "air")
            fill_box(client, (196, PLAT, 246), (207, PLAT, 257), "stone")
            corner_x, corner_z = 208.0, 258.0
            # Half width 0.3 minus the 0.025 probe inset: the resting centre
            # may reach *past* the edge by 0.275 (the box still overlaps the
            # platform by 0.025..0.05 after the 0.05 back-off quantum).
            inset = 0.3 - 0.025  # centre may reach edge + 0.275
            diag = corner_stop(
                client, label="diag45_sneak", x=200.5, z=250.5, yaw=-45.0, sneak=True
            )
            axis_x = corner_stop(
                client, label="axis_x_sneak", x=200.5, z=252.5, yaw=-90.0, sneak=True
            )
            axis_z = corner_stop(
                client, label="axis_z_sneak", x=200.5, z=250.5, yaw=0.0, sneak=True
            )
            walk_off = corner_stop(
                client,
                label="diag45_walk",
                x=200.5,
                z=250.5,
                yaw=-45.0,
                sneak=False,
                ticks=120,
            )
            RESULTS["corner"] = {
                "corner": [corner_x, corner_z],
                "inset_limit": round(corner_x + inset, 6),
                "inset_limit_z": round(corner_z + inset, 6),
                "diag": diag,
                "axis_x": axis_x,
                "axis_z": axis_z,
                "walk_off": walk_off,
            }
            assert diag["feet_y"] > GROUND - 0.05, diag  # sneak diagonal holds
            assert axis_x["feet_y"] > GROUND - 0.05, axis_x
            assert axis_z["feet_y"] > GROUND - 0.05, axis_z
            assert walk_off["feet_y"] < GROUND - 1.0, walk_off  # control falls
            # The stop centre sits *past* the edge: half width 0.3 minus the
            # 0.025 inset, minus up to one 0.05 back-off quantum.
            lo, hi = corner_x + inset - 0.06, corner_x + inset + 0.005
            lo_z, hi_z = corner_z + inset - 0.06, corner_z + inset + 0.005
            assert lo <= diag["x"] <= hi, diag
            assert lo <= axis_x["x"] <= hi, axis_x
            assert lo_z <= diag["z"] <= hi_z, diag
            assert lo_z <= axis_z["z"] <= hi_z, axis_z
            # the unclamped axis keeps its approach coordinate
            assert abs(axis_z["x"] - 200.5) < 0.01, axis_z
            assert abs(axis_x["z"] - 252.5) < 0.01, axis_x

            OUT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
            print(
                json.dumps(
                    {
                        "ground": {
                            r["label"]: [
                                r["first_over_walk"],
                                round(r["steady_speed"] / walk_steady, 6),
                            ]
                            for r in ground
                        },
                        "air": {
                            r["label"]: [
                                r["first_accel_over_walk"],
                                r["accel_over_walk"],
                            ]
                            for r in air
                        },
                        "corner": {
                            "inset_limit": RESULTS["corner"]["inset_limit"],
                            "inset_limit_z": RESULTS["corner"]["inset_limit_z"],
                            "diag": [diag["x"], diag["z"]],
                            "axis_x": [axis_x["x"], axis_x["z"]],
                            "axis_z": [axis_z["x"], axis_z["z"]],
                        },
                    },
                    ensure_ascii=False,
                    indent=1,
                )
            )
            print(f"Saved: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
