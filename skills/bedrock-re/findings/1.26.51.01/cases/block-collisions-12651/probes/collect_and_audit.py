"""Collect full-registry + in-world block/entity collisions and audit against MCBE_Parkour_wiki."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "driver").is_dir())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.harness import ModClient  # noqa: E402
from driver.harness.lab import settle  # noqa: E402

OUT_DIR = ROOT / "out" / "block-collisions-1.26.51.01"
ENTITY_MODES = ("none", "player", "leather_boots", "sneak_above")


def _wait_ticks(client: ModClient, ticks: int) -> None:
    start = int(client.state()["tick"])
    deadline = time.monotonic() + max(5.0, ticks * 0.25)
    while time.monotonic() < deadline:
        cur = int(client.state()["tick"])
        if cur - start >= ticks:
            return
        time.sleep(0.02)


def _cmd_wait(client: ModClient, command: str, ticks: int = 3) -> dict[str, Any]:
    reply = client.command(command)
    if ticks > 0:
        _wait_ticks(client, ticks)
    return reply


def _setblock_and_verify(
    client: ModClient, x: int, y: int, z: int, block_spec: str, expected_substr: str | None = None
) -> dict[str, Any]:
    client.command(f"setblock {x} {y} {z} {block_spec}")
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        _wait_ticks(client, 1)
        row = client.blocks([[x, y, z]])["blocks"][0]
        if expected_substr is None or expected_substr in str(row.get("name", "")):
            return row
    return client.blocks([[x, y, z]])["blocks"][0]


def _setup_world_cases(client: ModClient) -> list[dict[str, Any]]:
    """Build deterministic neighbor/composite block test rigs and sample them via block_shapes."""
    # Clear an elevated slab/air platform at z=20..36, y=-49..-46, x=16..84 (on top of ground y=-50).
    _cmd_wait(client, "fill 15 -49 18 90 -44 38 air", ticks=4)
    # Also put a stone floor at y=-50 under x=15..90, z=18..38 just in case.
    _cmd_wait(client, "fill 15 -50 18 90 -50 38 stone", ticks=4)

    samples: list[dict[str, Any]] = []

    def record_rig(label: str, setups: list[str], targets: list[tuple[int, int, int]]) -> None:
        for cmd in setups:
            client.command(cmd)
        _wait_ticks(client, 4)
        res = client.block_shapes(targets, entity_mode="none")
        res_player = client.block_shapes(targets, entity_mode="player")
        samples.append(
            {
                "label": label,
                "setups": setups,
                "targets": [list(t) for t in targets],
                "none": res.get("blocks", []),
                "player": res_player.get("blocks", []),
            }
        )

    # 1. Walls: isolated, 1-sided, 2-adj, 2-opp, 3-sided, 4-sided, and walls with wall above.
    record_rig(
        "wall_isolated",
        ["setblock 18 -49 20 cobblestone_wall"],
        [(18, -49, 20)],
    )
    record_rig(
        "wall_1_sided",
        ["setblock 22 -49 20 cobblestone_wall", "setblock 22 -49 21 stone"],
        [(22, -49, 20)],
    )
    record_rig(
        "wall_2_adjacent",
        [
            "setblock 26 -49 20 cobblestone_wall",
            "setblock 26 -49 21 stone",
            "setblock 27 -49 20 stone",
        ],
        [(26, -49, 20)],
    )
    record_rig(
        "wall_2_opposite",
        [
            "setblock 30 -49 20 cobblestone_wall",
            "setblock 30 -49 21 stone",
            "setblock 30 -49 19 stone",
        ],
        [(30, -49, 20)],
    )
    record_rig(
        "wall_2_opposite_with_top",
        [
            "setblock 34 -49 20 cobblestone_wall",
            "setblock 34 -48 20 cobblestone_wall",
            "setblock 34 -49 21 stone",
            "setblock 34 -49 19 stone",
            "setblock 34 -48 21 stone",
            "setblock 34 -48 19 stone",
        ],
        [(34, -49, 20), (34, -48, 20)],
    )
    record_rig(
        "wall_3_sided",
        [
            "setblock 38 -49 20 cobblestone_wall",
            "setblock 38 -49 21 stone",
            "setblock 38 -49 19 stone",
            "setblock 39 -49 20 stone",
        ],
        [(38, -49, 20)],
    )
    record_rig(
        "wall_4_sided",
        [
            "setblock 42 -49 20 cobblestone_wall",
            "setblock 42 -49 21 stone",
            "setblock 42 -49 19 stone",
            "setblock 43 -49 20 stone",
            "setblock 41 -49 20 stone",
        ],
        [(42, -49, 20)],
    )

    # 2. Fences: isolated, 1-sided, 2-adj, 2-opp, 3-sided, 4-sided.
    record_rig("fence_isolated", ["setblock 18 -49 25 oak_fence"], [(18, -49, 25)])
    record_rig(
        "fence_1_sided",
        ["setblock 22 -49 25 oak_fence", "setblock 22 -49 26 stone"],
        [(22, -49, 25)],
    )
    record_rig(
        "fence_2_adjacent",
        ["setblock 26 -49 25 oak_fence", "setblock 26 -49 26 stone", "setblock 27 -49 25 stone"],
        [(26, -49, 25)],
    )
    record_rig(
        "fence_2_opposite",
        ["setblock 30 -49 25 oak_fence", "setblock 30 -49 26 stone", "setblock 30 -49 24 stone"],
        [(30, -49, 25)],
    )
    record_rig(
        "fence_3_sided",
        [
            "setblock 34 -49 25 oak_fence",
            "setblock 34 -49 26 stone",
            "setblock 34 -49 24 stone",
            "setblock 35 -49 25 stone",
        ],
        [(34, -49, 25)],
    )
    record_rig(
        "fence_4_sided",
        [
            "setblock 38 -49 25 oak_fence",
            "setblock 38 -49 26 stone",
            "setblock 38 -49 24 stone",
            "setblock 39 -49 25 stone",
            "setblock 37 -49 25 stone",
        ],
        [(38, -49, 25)],
    )

    # 3. Panes & Iron bars: isolated, 1-sided, 2-adj, 2-opp, 3-sided, 4-sided.
    record_rig("pane_isolated", ["setblock 46 -49 25 glass_pane"], [(46, -49, 25)])
    record_rig(
        "pane_1_sided",
        ["setblock 50 -49 25 glass_pane", "setblock 50 -49 26 stone"],
        [(50, -49, 25)],
    )
    record_rig(
        "pane_2_adjacent",
        ["setblock 54 -49 25 glass_pane", "setblock 54 -49 26 stone", "setblock 55 -49 25 stone"],
        [(54, -49, 25)],
    )
    record_rig(
        "pane_2_opposite",
        ["setblock 58 -49 25 glass_pane", "setblock 58 -49 26 stone", "setblock 58 -49 24 stone"],
        [(58, -49, 25)],
    )
    record_rig(
        "pane_3_sided",
        [
            "setblock 62 -49 25 glass_pane",
            "setblock 62 -49 26 stone",
            "setblock 62 -49 24 stone",
            "setblock 63 -49 25 stone",
        ],
        [(62, -49, 25)],
    )
    record_rig(
        "pane_4_sided",
        [
            "setblock 66 -49 25 glass_pane",
            "setblock 66 -49 26 stone",
            "setblock 66 -49 24 stone",
            "setblock 67 -49 25 stone",
            "setblock 65 -49 25 stone",
        ],
        [(66, -49, 25)],
    )

    # 4. Stairs: normal, outer corner, inner corner (both normal & upside-down).
    record_rig(
        "stairs_normal_outer_inner",
        [
            'setblock 18 -49 30 oak_stairs ["weirdo_direction"=0,"upside_down_bit"=false]',
            # Outer corner: target at (22,-49,30) faces east(0), neighbor in front (+X=23) faces south(2)
            'setblock 22 -49 30 oak_stairs ["weirdo_direction"=0,"upside_down_bit"=false]',
            'setblock 23 -49 30 oak_stairs ["weirdo_direction"=2,"upside_down_bit"=false]',
            # Inner corner: target at (26,-49,30) faces east(0), neighbor behind (-X=25) faces south(2)
            'setblock 26 -49 30 oak_stairs ["weirdo_direction"=0,"upside_down_bit"=false]',
            'setblock 25 -49 30 oak_stairs ["weirdo_direction"=2,"upside_down_bit"=false]',
            # Opposite inner corner orientation check:
            'setblock 30 -49 30 oak_stairs ["weirdo_direction"=0,"upside_down_bit"=false]',
            'setblock 29 -49 30 oak_stairs ["weirdo_direction"=3,"upside_down_bit"=false]',
        ],
        [(18, -49, 30), (22, -49, 30), (26, -49, 30), (30, -49, 30)],
    )

    # 5. Single Chest vs Double Chest (neighbor coupling via BlockActor / BlockSource).
    record_rig(
        "chests_single_and_double",
        [
            'setblock 36 -49 30 chest ["minecraft:cardinal_direction"="north"]',
            'setblock 40 -49 30 chest ["minecraft:cardinal_direction"="north"]',
            'setblock 41 -49 30 chest ["minecraft:cardinal_direction"="north"]',
        ],
        [(36, -49, 30), (40, -49, 30), (41, -49, 30)],
    )

    # 6. Hopper (all directions), Trapdoors, Doors, Brewing Stand, Cauldron, Composter, Piston Head,
    #    Shulker Box, Big Dripleaf, Shelf Mushroom, Pointed Dripstone, Bamboo, Border Block, Candle, Bell.
    record_rig(
        "composite_and_special_blocks_a",
        [
            'setblock 46 -49 30 hopper ["facing_direction"=0]',
            'setblock 48 -49 30 hopper ["facing_direction"=2]',
            'setblock 50 -49 30 hopper ["facing_direction"=5]',
            "setblock 52 -49 30 cauldron",
            'setblock 54 -49 30 composter ["composter_fill_level"=0]',
            'setblock 56 -49 30 composter ["composter_fill_level"=4]',
            'setblock 58 -49 30 composter ["composter_fill_level"=8]',
            "setblock 60 -49 30 brewing_stand",
            'setblock 62 -49 30 oak_trapdoor ["direction"=0,"open_bit"=false,"upside_down_bit"=false]',
            'setblock 64 -49 30 oak_trapdoor ["direction"=0,"open_bit"=true,"upside_down_bit"=false]',
            'setblock 66 -49 30 oak_door ["direction"=0,"open_bit"=false,"upper_block_bit"=false]',
            'setblock 68 -49 30 iron_trapdoor ["direction"=0,"open_bit"=false,"upside_down_bit"=false]',
            "setblock 70 -49 30 slime",
            "setblock 72 -49 30 honey_block",
            "setblock 74 -49 30 bed",
            'setblock 76 -49 30 big_dripleaf ["big_dripleaf_head"=true,"big_dripleaf_tilt"="none"]',
            'setblock 78 -49 30 big_dripleaf ["big_dripleaf_head"=true,"big_dripleaf_tilt"="unstable"]',
            'setblock 80 -49 30 big_dripleaf ["big_dripleaf_head"=true,"big_dripleaf_tilt"="partial_tilt"]',
            'setblock 82 -49 30 big_dripleaf ["big_dripleaf_head"=true,"big_dripleaf_tilt"="full_tilt"]',
        ],
        [
            (46, -49, 30),
            (48, -49, 30),
            (50, -49, 30),
            (52, -49, 30),
            (54, -49, 30),
            (56, -49, 30),
            (58, -49, 30),
            (60, -49, 30),
            (62, -49, 30),
            (64, -49, 30),
            (66, -49, 30),
            (68, -49, 30),
            (70, -49, 30),
            (72, -49, 30),
            (74, -49, 30),
            (76, -49, 30),
            (78, -49, 30),
            (80, -49, 30),
            (82, -49, 30),
        ],
    )

    # 7. Piston extended head + Shulker box (closed) + Pointed dripstone + Bamboo + Border block + Shelf Mushroom + Chorus
    record_rig(
        "composite_and_special_blocks_b",
        [
            'setblock 18 -49 35 piston ["facing_direction"=1]',
            "setblock 18 -48 35 stone",
            "setblock 17 -49 35 redstone_block",
            "setblock 22 -49 35 shulker_box",
            'setblock 24 -49 35 pointed_dripstone ["dripstone_thickness"="base","hanging"=false]',
            "setblock 25 -49 35 pointed_dripstone",
            'setblock 26 -49 35 bamboo ["bamboo_stalk_thickness"="thin"]',
            'setblock 28 -49 35 bamboo ["bamboo_stalk_thickness"="thick"]',
            "setblock 30 -49 35 border_block",
            'setblock 32 -49 35 candle ["candles"=0,"lit"=false]',
            'setblock 34 -49 35 candle ["candles"=1,"lit"=false]',
            'setblock 36 -49 35 candle ["candles"=2,"lit"=false]',
            'setblock 38 -49 35 candle ["candles"=3,"lit"=false]',
            "setblock 40 -49 35 turtle_egg",
            "setblock 42 -49 35 sniffer_egg",
            "setblock 44 -49 35 lectern",
            "setblock 46 -49 35 chorus_plant",
            "setblock 48 -49 35 oak_shelf",
            "setblock 50 -49 35 dried_ghast",
            "setblock 52 -49 35 copper_golem_statue",
            "setblock 54 -49 35 heavy_core",
            "setblock 56 -49 35 powder_snow",
            "setblock 58 -49 35 scaffolding",
        ],
        [
            (18, -49, 35),
            (18, -48, 35),
            (22, -49, 35),
            (24, -49, 35),
            (25, -49, 35),
            (26, -49, 35),
            (28, -49, 35),
            (30, -49, 35),
            (32, -49, 35),
            (34, -49, 35),
            (36, -49, 35),
            (38, -49, 35),
            (40, -49, 35),
            (42, -49, 35),
            (44, -49, 35),
            (46, -49, 35),
            (48, -49, 35),
            (50, -49, 35),
            (52, -49, 35),
            (54, -49, 35),
            (56, -49, 35),
            (58, -49, 35),
        ],
    )

    # Also inspect piston extended arm & pushed stone + sneak_above on scaffolding + leather_boots on powder_snow!
    ext_check = client.block_shapes([(18, -48, 35), (18, -47, 35)], entity_mode="none")
    scaff_sneak = client.block_shapes([(58, -49, 35)], entity_mode="sneak_above")
    snow_boots = client.block_shapes([(56, -49, 35)], entity_mode="leather_boots")
    samples.append({"label": "piston_arm_extra", "none": ext_check.get("blocks", [])})
    samples.append({"label": "scaffolding_sneak_above", "sneak_above": scaff_sneak.get("blocks", [])})
    samples.append({"label": "powder_snow_leather_boots", "leather_boots": snow_boots.get("blocks", [])})

    # Clean up test rigs
    _cmd_wait(client, "fill 15 -49 18 90 -44 38 air", ticks=4)
    return samples


def _collect_entities(client: ModClient) -> dict[str, Any]:
    """Summon boat, shulker, and happy_ghast (near & far) to read exact Actor::getAABB."""
    saved = client.state()
    orig_pos = (float(saved["feet_x"]), float(saved["feet_y"]), float(saved["feet_z"]))
    orig_yaw = float(saved["yaw"])
    orig_pitch = float(saved["pitch"])

    results: dict[str, Any] = {}
    try:
        # Clean any leftover test mobs
        for kind in ("boat", "shulker", "happy_ghast"):
            client.command(f"kill @e[type={kind}]")
        _wait_ticks(client, 4)

        client.teleport(55.5, -49.0, 25.5, yaw=0.0, pitch=0.0)
        _wait_ticks(client, 5)

        client.command("summon oak_boat 55.5 -49 30.5")
        client.command("summon shulker 60.5 -49 30.5")
        client.command("summon happy_ghast 70.5 -49 30.5")
        _wait_ticks(client, 10)
        results["near_all"] = client.entities(radius=64.0, limit=32).get("entities", [])

        # Observe happy_ghast / shulker over multiple ticks (shulker may open/peek, happy_ghast solidified near player)
        client.teleport(70.5, -45.0, 30.5, yaw=0.0, pitch=90.0)
        _wait_ticks(client, 15)
        results["above_happy_ghast"] = client.entities(radius=64.0, limit=32).get("entities", [])

        # Far from happy_ghast (~30 blocks away)
        client.teleport(35.5, -49.0, 30.5, yaw=0.0, pitch=0.0)
        _wait_ticks(client, 15)
        results["far_from_happy_ghast"] = client.entities(radius=64.0, limit=32).get("entities", [])
    finally:
        for kind in ("boat", "shulker", "happy_ghast"):
            client.command(f"kill @e[type={kind}]")
        _wait_ticks(client, 4)
        client.teleport(*orig_pos, yaw=orig_yaw, pitch=orig_pitch)
        _wait_ticks(client, 4)
    return results


def collect() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with ModClient(timeout=30.0) as client:
        client.wait_ready(timeout=30.0, require_world=True)
        settle(client)
        mod_dir = Path(str(client.status()["spool"])).parent

        dumps: dict[str, Any] = {}
        for mode in ENTITY_MODES:
            fname = f"block-collisions-{mode}.jsonl"
            res = client.dump_block_collisions(
                filename=fname,
                pos=(0, 0, 0),
                include_air=(mode == "none"),
                entity_mode=mode,
                timeout=60.0,
            )
            src = mod_dir / fname
            dst = OUT_DIR / fname
            shutil.copy2(src, dst)
            dumps[mode] = res
            print(f"[dump:{mode}] types={res.get('block_types')} perms={res.get('permutations')} faults={res.get('faults')} ms={res.get('elapsed_ms')}")

        world_samples = _setup_world_cases(client)
        entity_samples = _collect_entities(client)

        bundle = {
            "dumps": dumps,
            "world_samples": world_samples,
            "entity_samples": entity_samples,
        }
        (OUT_DIR / "live-samples.json").write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Saved live collection to {OUT_DIR}")
    return 0


def _dims(box: dict[str, Any]) -> tuple[float, float, float, list[float], list[float], bool]:
    mn = box["min"]
    mx = box["max"]
    return (
        mx[0] - mn[0],
        mx[1] - mn[1],
        mx[2] - mn[2],
        mn,
        mx,
        bool(box.get("empty")),
    )


def _fmt_box(box: dict[str, Any]) -> str:
    dx, dy, dz, mn, mx, empty = _dims(box)
    if empty:
        return "EMPTY"
    return (
        f"size=({dx:.7g}, {dy:.7g}, {dz:.7g}) "
        f"min=({mn[0]:.7g}, {mn[1]:.7g}, {mn[2]:.7g}) "
        f"max=({mx[0]:.7g}, {mx[1]:.7g}, {mx[2]:.7g})"
    )


def analyze() -> int:
    none_path = OUT_DIR / "block-collisions-none.jsonl"
    if not none_path.is_file():
        print(f"Missing {none_path}; run --collect first.", file=sys.stderr)
        return 1

    rows_by_mode: dict[str, list[dict[str, Any]]] = {}
    for mode in ENTITY_MODES:
        p = OUT_DIR / f"block-collisions-{mode}.jsonl"
        rows_by_mode[mode] = [
            json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()
        ]

    none_rows = rows_by_mode["none"]
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in none_rows:
        by_name[r["name"]].append(r)

    # 1. Context-dependent blocks across none / player / leather_boots / sneak_above
    def key_fn(r: dict[str, Any]) -> tuple[str, int]:
        return (str(r["name"]), int(r["data"]))
    mode_maps = {m: {key_fn(r): r for r in rows_by_mode[m]} for m in ENTITY_MODES if m != "none"}
    context_diffs: list[dict[str, Any]] = []
    for r in none_rows:
        if r.get("air"):
            continue
        k = key_fn(r)
        base_sig = (r["collision"], r["boxes"])
        diffs: dict[str, Any] = {}
        for m, mp in mode_maps.items():
            other = mp.get(k)
            if other and (other["collision"], other["boxes"]) != base_sig:
                diffs[m] = {"collision": other["collision"], "boxes": other["boxes"]}
        if diffs:
            context_diffs.append(
                {
                    "name": r["name"],
                    "data": r["data"],
                    "states": r["states"],
                    "none": {"collision": r["collision"], "boxes": r["boxes"]},
                    "diffs": diffs,
                }
            )

    # 2. Group unique box geometries across all non-air permutations
    unique_shapes: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    nonfull_blocks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    outline_mismatches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    random_offset_blocks: set[str] = set()
    bouncy_blocks: dict[str, float] = {}

    for r in none_rows:
        if r.get("air") or r.get("fault"):
            continue
        c = r["collision"]
        boxes = r["boxes"]
        off = r["random_offset"]
        if any(abs(v) > 1e-7 for v in off):
            random_offset_blocks.add(r["name"])
        if abs(float(r["bounciness"])) > 1e-7:
            bouncy_blocks[r["name"]] = float(r["bounciness"])

        sig = (
            tuple((round(b["min"][0], 7), round(b["min"][1], 7), round(b["min"][2], 7),
                   round(b["max"][0], 7), round(b["max"][1], 7), round(b["max"][2], 7), b["empty"])
                  for b in boxes),
            (round(c["min"][0], 7), round(c["min"][1], 7), round(c["min"][2], 7),
             round(c["max"][0], 7), round(c["max"][1], 7), round(c["max"][2], 7), c["empty"]),
        )
        unique_shapes[sig].append(r["name"])

        is_full = (
            len(boxes) == 1
            and not boxes[0]["empty"]
            and boxes[0]["min"] == [0.0, 0.0, 0.0]
            and boxes[0]["max"] == [1.0, 1.0, 1.0]
        )
        is_intangible = (len(boxes) == 0) and c["empty"]
        if not is_full and not is_intangible:
            nonfull_blocks[r["name"]].append(r)
            out = r["outline"]
            if len(boxes) > 1 or (len(boxes) == 1 and (out["min"] != boxes[0]["min"] or out["max"] != boxes[0]["max"])):
                outline_mismatches[r["name"]].append(r)

    live_samples = json.loads((OUT_DIR / "live-samples.json").read_text(encoding="utf-8"))
    summary = {
        "total_block_types": len(by_name),
        "total_permutations_none": len(none_rows),
        "unique_shape_signatures": len(unique_shapes),
        "nonfull_solid_block_types": len(nonfull_blocks),
        "context_diff_count": len(context_diffs),
        "context_diff_blocks": sorted({d["name"] for d in context_diffs}),
        "random_offset_blocks": sorted(random_offset_blocks),
        "bouncy_blocks": bouncy_blocks,
        "nonfull_blocks_sample_keys": sorted(nonfull_blocks.keys()),
        "outline_mismatch_blocks": sorted(outline_mismatches.keys()),
    }
    (OUT_DIR / "audit-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # Print key Wiki audit targets cleanly
    audit_targets = [
        "minecraft:oak_trapdoor",
        "minecraft:iron_trapdoor",
        "minecraft:oak_door",
        "minecraft:chest",
        "minecraft:hopper",
        "minecraft:big_dripleaf",
        "minecraft:snow_layer",
        "minecraft:cake",
        "minecraft:waterlily",
        "minecraft:slime",
        "minecraft:honey_block",
        "minecraft:shelf_mushroom",
        "minecraft:red_mushroom_block",
        "minecraft:oak_shelf",
        "minecraft:turtle_egg",
        "minecraft:sniffer_egg",
        "minecraft:lectern",
        "minecraft:soul_sand",
        "minecraft:mud",
        "minecraft:powder_snow",
        "minecraft:scaffolding",
        "minecraft:ladder",
        "minecraft:pointed_dripstone",
        "minecraft:bamboo",
        "minecraft:dried_ghast",
        "minecraft:copper_golem_statue",
        "minecraft:pitcher_crop",
        "minecraft:candle",
        "minecraft:bell",
        "minecraft:brewing_stand",
        "minecraft:cauldron",
        "minecraft:composter",
        "minecraft:piston_arm_collision",
        "minecraft:shulker_box",
        "minecraft:border_block",
        "minecraft:oak_hanging_sign",
    ]
    print("\n=== REGISTRY KEY BLOCKS ===")
    for name in audit_targets:
        rows = by_name.get(name, [])
        if not rows:
            # try substring search
            matches = [k for k in by_name if name.split(":", 1)[-1] in k]
            print(f"[MISS] {name} -> similar: {matches[:8]}")
            continue
        seen = set()
        for r in rows:
            s = (
                _fmt_box(r["collision"]),
                tuple(_fmt_box(b) for b in r["boxes"]),
                _fmt_box(r["outline"]),
                round(float(r["friction"]), 6),
                round(float(r["bounciness"]), 6),
            )
            if s in seen:
                continue
            seen.add(s)
            print(
                f"{name} data={r['data']} states={r['states']} | col={s[0]} | boxes={list(s[1])} | out={s[2]} | fric={s[3]} bounce={s[4]}"
            )
            if len(seen) >= 12:
                print(f"  ... ({len(rows)} total permutations)")
                break

    print("\n=== CONTEXT DIFFS (player / leather_boots / sneak_above) ===")
    by_ctx_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in context_diffs:
        by_ctx_name[d["name"]].append(d)
    for nm, items in sorted(by_ctx_name.items()):
        sample = items[0]
        print(f"{nm} ({len(items)} perms): none={_fmt_box(sample['none']['collision'])} -> diffs={{m: _fmt_box(v['collision']) for m, v in sample['diffs'].items()}}")

    print("\n=== LIVE WORLD SAMPLES ===")
    for entry in live_samples["world_samples"]:
        print(f"-- {entry['label']} --")
        for key in ("none", "player", "sneak_above", "leather_boots"):
            if key not in entry:
                continue
            for blk in entry[key]:
                print(
                    f"  [{key}] {blk.get('name')} pos={blk.get('pos')} states={blk.get('states')} "
                    f"col={_fmt_box(blk['collision'])} boxes={[_fmt_box(b) for b in blk['boxes']]} "
                    f"out={_fmt_box(blk['outline'])}"
                )

    print("\n=== LIVE ENTITY SAMPLES ===")
    for stage, ents in live_samples["entity_samples"].items():
        print(f"-- {stage} --")
        for ent in ents:
            print(f"  {ent.get('type')} pos=({ent.get('x'):.4f},{ent.get('y'):.4f},{ent.get('z'):.4f}) aabb={ent.get('aabb')}")

    return 0


def _collect_supplement(client: ModClient) -> dict[str, Any]:
    """Collect boats, double-chest placement, wall_post_bit, chorus, scaffolding, and portal frames."""
    saved = client.state()
    orig_pos = (float(saved["feet_x"]), float(saved["feet_y"]), float(saved["feet_z"]))
    orig_yaw = float(saved["yaw"])
    orig_pitch = float(saved["pitch"])
    results: dict[str, Any] = {"world": [], "entities": {}}

    try:
        _cmd_wait(client, "fill 15 -49 18 90 -44 38 air", ticks=4)
        _cmd_wait(client, "fill 15 -50 18 90 -50 38 stone", ticks=4)

        def record(label: str, setups: list[str], targets: list[tuple[int, int, int]]) -> None:
            for cmd in setups:
                client.command(cmd)
            _wait_ticks(client, 4)
            results["world"].append(
                {
                    "label": label,
                    "none": client.block_shapes(targets, entity_mode="none").get("blocks", []),
                    "player": client.block_shapes(targets, entity_mode="player").get("blocks", []),
                    "sneak_above": client.block_shapes(targets, entity_mode="sneak_above").get(
                        "blocks", []
                    ),
                }
            )

        record(
            "chorus_isolated_vs_grounded_vs_connected",
            [
                "setblock 20 -48 22 chorus_plant",
                "setblock 24 -49 22 end_stone",
                "setblock 24 -48 22 chorus_plant",
                "setblock 28 -49 22 end_stone",
                "setblock 28 -48 22 chorus_plant",
                "setblock 29 -48 22 chorus_plant",
                "setblock 28 -47 22 chorus_flower",
            ],
            [(20, -48, 22), (24, -48, 22), (28, -48, 22)],
        )
        record(
            "wall_2_opposite_post_bit",
            [
                "setblock 34 -49 22 cobblestone_wall",
                "setblock 34 -49 23 stone",
                "setblock 34 -49 21 stone",
                "setblock 38 -49 22 cobblestone_wall",
                "setblock 38 -49 23 stone",
                "setblock 38 -49 21 stone",
                "setblock 38 -48 22 torch",
            ],
            [(34, -49, 22), (38, -49, 22)],
        )
        record(
            "end_portal_and_frame_eye",
            [
                'setblock 42 -49 22 end_portal_frame ["end_portal_eye_bit"=false]',
                'setblock 44 -49 22 end_portal_frame ["end_portal_eye_bit"=true]',
                "setblock 46 -49 22 end_portal",
            ],
            [(42, -49, 22), (44, -49, 22), (46, -49, 22)],
        )
        record(
            "trapdoor_door_straw_bed_moss_shelf_scaffolding",
            [
                'setblock 50 -49 22 trapdoor ["direction"=0,"open_bit"=false,"upside_down_bit"=false]',
                'setblock 52 -49 22 wooden_door ["direction"=0,"open_bit"=false,"upper_block_bit"=false]',
                "setblock 54 -49 22 straw_bed",
                "setblock 56 -49 22 pale_moss_carpet",
                "setblock 57 -49 22 stone",
                'setblock 58 -49 22 shelf_mushroom ["growth"=0]',
                'setblock 60 -49 22 shelf_mushroom ["growth"=1]',
                "setblock 62 -49 22 scaffolding",
            ],
            [
                (50, -49, 22),
                (52, -49, 22),
                (54, -49, 22),
                (56, -49, 22),
                (58, -49, 22),
                (60, -49, 22),
                (62, -49, 22),
            ],
        )

        # Real player placement for double chest (sets BlockActor pair state)
        client.set_item(0, "minecraft:chest", count=8)
        client.select_slot(0)
        _wait_ticks(client, 3)
        client.teleport(66.5, -49.0, 20.5, yaw=0.0, pitch=45.0)
        _wait_ticks(client, 4)
        client.place_block(66, -49, 22, against=(66, -50, 22), face=1)
        _wait_ticks(client, 4)
        client.teleport(67.5, -49.0, 20.5, yaw=0.0, pitch=40.0)
        _wait_ticks(client, 4)
        client.place_block(67, -49, 22, against=(67, -50, 22), face=1)
        _wait_ticks(client, 6)
        results["world"].append(
            {
                "label": "double_chest_placed",
                "none": client.block_shapes([(66, -49, 22), (67, -49, 22)], entity_mode="none").get(
                    "blocks", []
                ),
            }
        )
        client.clear_inventory()

        # Boat & Chest Boat entities
        for kind in ("boat", "chest_boat"):
            client.command(f"kill @e[type={kind}]")
        _wait_ticks(client, 4)
        client.teleport(55.5, -49.0, 28.0, yaw=0.0, pitch=0.0)
        _wait_ticks(client, 4)
        client.command("summon boat 55.5 -49 32.5")
        client.command("summon chest_boat 59.5 -49 32.5")
        _wait_ticks(client, 8)
        results["entities"]["boats"] = client.entities(radius=32.0, limit=32).get("entities", [])
    finally:
        for kind in ("boat", "chest_boat"):
            client.command(f"kill @e[type={kind}]")
        _cmd_wait(client, "fill 15 -49 18 90 -44 38 air", ticks=4)
        client.teleport(*orig_pos, yaw=orig_yaw, pitch=orig_pitch)
        _wait_ticks(client, 4)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--supplement", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args(argv)
    if args.collect:
        rc = collect()
        if rc != 0:
            return rc
    if args.supplement:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with ModClient(timeout=30.0) as client:
            client.wait_ready(timeout=30.0, require_world=True)
            settle(client)
            supp = _collect_supplement(client)
        (OUT_DIR / "supplement-samples.json").write_text(
            json.dumps(supp, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Saved supplement collection to {OUT_DIR / 'supplement-samples.json'}")
    if args.analyze or (not args.collect and not args.supplement):
        return analyze()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
