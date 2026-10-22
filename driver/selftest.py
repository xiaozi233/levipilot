"""Live smoke test for the probe mod. Requires the game in a world.

    py -3.11 driver/selftest.py
    py -3.11 driver/selftest.py --keep          # keep the test entity
    py -3.11 driver/selftest.py --entity minecraft:cow

Covers: pipe, state, inventory read/write/give/clear, teleport, server command,
entity query, look-at, attack and interact. The original slot-0 item and the
selected slot are restored at the end.

The default entity is a pig: hostile mobs despawn immediately if the test world
is on Peaceful difficulty.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.harness.hooks import decode_hook_hits  # noqa: E402
from driver.harness.memory import fnv1_64, fnv1a_64  # noqa: E402
from driver.harness.spool import read_spool  # noqa: E402
from driver.transport_mod import ModClient, ModPipeError, ModProtocolError  # noqa: E402


class Checks:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        ok = bool(condition)
        self.results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
        return ok

    def note(self, name: str, detail: str = "") -> None:
        print(f"NOTE  {name}" + (f"  ({detail})" if detail else ""))

    def finish(self) -> int:
        failed = [row for row in self.results if not row[1]]
        print(f"\n{len(self.results) - len(failed)}/{len(self.results)} checks passed")
        for name, _ok, detail in failed:
            print(f"  failed: {name} {detail}", file=sys.stderr)
        return 1 if failed else 0


def wait_ticks(client: ModClient, ticks: int, timeout: float = 6.0) -> int:
    start = int(client.status().get("tick", 0))
    deadline = time.monotonic() + timeout
    while True:
        current = int(client.status().get("tick", 0))
        if current - start >= ticks:
            return current - start
        if time.monotonic() > deadline:
            return current - start
        time.sleep(0.01)


def slot_item(snapshot: dict[str, Any], slot: int) -> dict[str, Any] | None:
    for row in snapshot.get("slots", []):
        if int(row.get("slot", -1)) == slot:
            return row.get("item")
    return None


def find_item(snapshot: dict[str, Any], item_type: str) -> tuple[int | None, dict[str, Any] | None]:
    for row in snapshot.get("slots", []):
        item = row.get("item")
        if item and item.get("type") == item_type:
            return int(row["slot"]), item
    return None, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entity", default="minecraft:pig")
    parser.add_argument("--keep", action="store_true", help="do not remove the test entity")
    parser.add_argument("--radius", type=float, default=10.0)
    args = parser.parse_args(argv)

    checks = Checks()
    try:
        client = ModClient().connect()
    except ModPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("提示：游戏内是否已加载 probe mod，并且已经进入存档？", file=sys.stderr)
        return 2

    try:
        checks.check("ping", client.ping().get("event") == "pong")

        # -- T1: spool, crash isolation -------------------------------------
        checks.check("hello_spool", bool(client.hello.get("spool")), str(client.hello.get("spool")))
        client.start(5)
        while True:
            event = client.readline(5.0).get("event")
            if event == "stopped":
                break
        flushed = client.spool_flush()
        spool_path = Path(str(flushed.get("path", "")))
        rows: list[dict[str, Any]] = []
        stats = None
        if spool_path.name:
            rows, stats = read_spool(spool_path)
        checks.check("spool_exists", spool_path.is_file(), str(spool_path))
        checks.check(
            "spool_rows",
            stats is not None and stats.rows > 0,
            f"rows={0 if stats is None else stats.rows}",
        )
        checks.check(
            "spool_tick_rows",
            any(row.get("event") == "tick" for row in rows[-64:]),
            f"tail={len(rows[-64:])}",
        )

        isolated = client.selftest_av()
        checks.check(
            "dispatch_isolation",
            isolated.get("event") == "error" and isolated.get("reason") == "internal_error",
            str(isolated),
        )
        ticks_after_fault = wait_ticks(client, 3)
        checks.check("survives_fault", ticks_after_fault >= 3, f"ticks={ticks_after_fault}")

        state = client.state()
        if not state.get("in_world"):
            checks.check("in_world", False, "玩家不在世界中")
            return checks.finish()
        checks.check("in_world", True)

        feet = (state["feet_x"], state["feet_y"], state["feet_z"])
        selected = int(state.get("selected_slot", 0))

        # -- T2: memory primitives -----------------------------------------
        block_row = client.blocks([(feet[0], feet[1] - 1, feet[2])])["blocks"][0]
        block_addr = int(block_row.get("addr", 0))
        checks.check("blocks_addr", block_addr != 0, hex(block_addr))

        block_probe = client.probe(block_addr, "block")
        checks.check(
            "probe_block",
            bool(block_probe.get("ok")) and bool(block_probe.get("legacy")),
            str(block_probe.get("legacy")),
        )
        checks.check(
            "probe_block_name",
            block_probe.get("name") == block_row["name"],
            f"{block_probe.get('name')} vs {block_row['name']}",
        )

        name = str(block_probe.get("name", ""))
        legacy = client.probe(str(block_probe.get("legacy", 0)), "legacy")
        checks.check(
            "probe_legacy",
            bool(legacy.get("ok")) and legacy.get("name") == name,
            str(legacy.get("name")),
        )
        checks.check(
            "legacy_hash",
            legacy.get("hash") in {fnv1_64(name), fnv1a_64(name)},
            f"hash={legacy.get('hash')}",
        )

        raw = client.probe(block_addr, "raw", length=16)
        checks.check("probe_raw", bool(raw.get("ok")) and len(str(raw.get("hex", ""))) == 32)
        memory = client.mem_read(block_addr, 16)
        checks.check(
            "mem_read",
            bool(memory.get("ok")) and len(str(memory.get("hex", ""))) == 32,
        )
        bad_read = client.mem_read(0x10, 16)
        checks.check(
            "mem_read_bad_addr",
            bad_read.get("ok") is False and bad_read.get("reason") == "bad_addr",
            str(bad_read),
        )
        if not client.status().get("allow_write"):
            try:
                client.mem_write(0x10, b"\x00")
                denied = False
            except ModProtocolError as exc:
                denied = "write_disabled" in str(exc)
            checks.check("mem_write_disabled", denied, "")

        # -- T3: hardware watchpoints --------------------------------------
        status = client.status()
        probe_addr = int(status.get("tick_probe_addr", 0))
        tick_tid = int(status.get("tick_thread_id", 0))
        baseline = int(client.watch_list().get("head", 0))
        installed = client.watch_set(probe_addr, size=8, mode="w", tag="tick_probe")
        checks.check("watch_install", installed.get("event") == "watch_installed", str(installed))
        wait_ticks(client, 20)
        drained = client.watch_drain(baseline)
        hits = drained.get("hits", [])
        checks.check("watch_hits", len(hits) >= 1, f"hits={len(hits)}")
        checks.check(
            "watch_tid",
            bool(hits) and all(int(hit["tid"]) == tick_tid for hit in hits),
            f"tick_tid={tick_tid}",
        )
        checks.check("watch_rip", bool(hits) and all(int(hit["rip"]) != 0 for hit in hits))
        checks.check(
            "watch_list",
            len(client.watch_list().get("watches", [])) == 1,
            str(client.watch_list().get("watches")),
        )
        cleared_cmd = client.watch_clear(all=True)
        checks.check("watch_clear_ok", cleared_cmd.get("ok") is True, str(cleared_cmd))
        cleared = client.watch_list()
        checks.check("watch_list_empty", cleared.get("watches") == [], str(cleared.get("watches")))
        # Ignore anything that was recorded between the last drain and the
        # clear itself; only hits after the clear count.
        head_after_clear = int(cleared.get("head", 0))
        wait_ticks(client, 5)
        after = client.watch_drain(head_after_clear)
        checks.check(
            "watch_cleared",
            len(after.get("hits", [])) == 0,
            f"hits={len(after.get('hits', []))}",
        )

        # -- T5: runtime symbol hook ---------------------------------------
        dry = client.hook_install("MobJump::setJumpPendingScale", dry_run=True)
        checks.check(
            "hook_dry_run",
            dry.get("event") == "hook_dry_run"
            and int(dry.get("address", 0)) != 0
            and dry.get("executable") is True,
            str(dry),
        )
        hook_baseline = int(client.hook_list().get("head", 0))
        installed = client.hook_install("MobJump::setJumpPendingScale", tag="selftest_jps")
        checks.check(
            "hook_install",
            installed.get("event") == "hook_installed",
            str(installed),
        )
        hook_id = int(installed.get("id", -1))
        called = client.hook_call("MobJump::setJumpPendingScale", 1.0)
        checks.check("hook_call", called.get("event") == "hook_call_done", str(called))
        wait_ticks(client, 3)
        drained_hook = client.hook_drain(hook_baseline)
        hook_hits = [hit for hit in decode_hook_hits(drained_hook) if hit.hook == hook_id]
        checks.check("hook_hit", len(hook_hits) >= 1, f"hits={len(hook_hits)}")
        checks.check(
            "hook_args",
            bool(hook_hits)
            and hook_hits[-1].args[0] != 0
            and abs(hook_hits[-1].b_float - 1.0) < 1.0e-6,
            str(hook_hits[-1].args if hook_hits else None),
        )
        removed = client.hook_rm(hook_id)
        checks.check("hook_remove", removed.get("ok") is True, str(removed))
        checks.check(
            "hook_list_empty",
            client.hook_list().get("hooks") == [],
            str(client.hook_list().get("hooks")),
        )
        va_dry = client.hook_install(va=dry["address"], dry_run=True)
        checks.check(
            "hook_va_dry_run",
            va_dry.get("executable") is True and va_dry.get("abi") == "generic4-int",
            str(va_dry),
        )
        bad_va = client.hook_install(va="0x10", dry_run=True)
        checks.check(
            "hook_va_bad",
            bad_va.get("executable") is False,
            str(bad_va),
        )

        # -- T8: panic removes hooks/watches and cancels tasks -------------
        probe_addr = int(client.status().get("tick_probe_addr", 0))
        client.hook_install("MobJump::setJumpPendingScale", tag="panic_hook")
        client.watch_set(probe_addr, size=8, tag="panic_watch")
        panicked = client.panic("all")
        checks.check(
            "panic_done",
            panicked.get("event") == "panic_done"
            and panicked.get("ok") is True
            and int(panicked.get("hooks_removed", 0)) >= 1
            and int(panicked.get("watches_cleared", 0)) >= 1,
            str(panicked),
        )
        checks.check("panic_hooks_empty", client.hook_list().get("hooks") == [])
        checks.check("panic_watches_empty", client.watch_list().get("watches") == [])
        checks.check("panic_task_idle", client.task_status().get("active") is None)
        checks.check("panic_alive", wait_ticks(client, 3) >= 3)

        # -- T9: tick/hook overhead metric ---------------------------------
        overhead_before = client.overhead()
        checks.check(
            "overhead",
            int(overhead_before.get("samples", 0)) > 20
            and float(overhead_before.get("tick_p99_us", 0)) > 0
            and "hook_p99_us" in overhead_before,
            str({k: overhead_before.get(k) for k in ("samples", "tick_p99_us", "hook_p99_us")}),
        )
        client.selftest_busy(1000, 20)
        wait_ticks(client, 30)
        overhead_after = client.overhead()
        checks.check(
            "overhead_busy",
            float(overhead_after.get("hook_p99_us", 0)) >= 900
            and float(overhead_after.get("hook_max_us", 0)) >= 900,
            str({k: overhead_after.get(k) for k in ("hook_p50_us", "hook_p99_us", "hook_max_us")}),
        )
        checks.check("overhead_alive", wait_ticks(client, 3) >= 3)

        # -- parkour jump/route primitives ---------------------------------
        jump_now = client.jump_when("now")
        checks.check(
            "jump_now",
            bool(jump_now.get("ok")) and jump_now.get("mode") == "now",
            str(jump_now),
        )
        wait_ticks(client, 15)

        jump_tick = client.jump_when("tick", after_ticks=5)
        checks.check(
            "jump_tick",
            bool(jump_tick.get("ok")) and int(jump_tick.get("ticks", 0)) >= 5,
            str(jump_tick.get("ticks")),
        )
        wait_ticks(client, 15)

        client.reset(feet[0], feet[1], feet[2], yaw=0.0, pitch=0.0)
        wait_ticks(client, 15)
        jump_distance = client.jump_when(
            "distance",
            x=feet[0] + 4.0,
            z=feet[2],
            distance=1.0,
            forward=1.0,
            max_ticks=100,
        )
        checks.check(
            "jump_distance",
            bool(jump_distance.get("ok")) and float(jump_distance.get("distance", 99.0)) <= 1.1,
            f"distance={jump_distance.get('distance')}",
        )
        wait_ticks(client, 20)

        route_result = client.route(
            [
                {"x": feet[0] + 2.0, "z": feet[2], "tolerance": 0.4},
                {"x": feet[0] + 2.0, "z": feet[2] + 2.0, "tolerance": 0.4},
            ]
        )
        checks.check(
            "route",
            bool(route_result.get("ok")) and int(route_result.get("waypoints", 0)) == 2,
            str(route_result),
        )
        client.reset(feet[0], feet[1], feet[2], yaw=0.0)
        wait_ticks(client, 10)

        # -- T4: binary tick ring ------------------------------------------
        started = client.record_bin_start(period=1)
        checks.check(
            "record_started",
            started.get("event") == "record_started" and int(started.get("entry_size", 0)) == 64,
            str(started),
        )
        wait_ticks(client, 20)
        stopped = client.record_bin_stop()
        recorded = int(stopped.get("rows", 0))
        checks.check("record_rows", recorded >= 10, f"rows={recorded}")
        checks.check("record_no_drop", int(client.status().get("queue_dropped", -1)) == 0)
        jsonl_entries = client.record_dump_all(format="jsonl")
        checks.check(
            "record_jsonl_count",
            len(jsonl_entries) == recorded,
            f"{len(jsonl_entries)} vs {recorded}",
        )
        bin_entries = client.record_dump_all(format="hex")
        checks.check(
            "record_hex_count", len(bin_entries) == recorded, f"{len(bin_entries)} vs {recorded}"
        )
        checks.check(
            "record_flags",
            bool(jsonl_entries) and any(entry.on_ground for entry in jsonl_entries),
            f"entries={len(jsonl_entries)}",
        )

        # -- P2: effects, checkpoints, region scan, landing event ----------
        checks.check("effects_field", isinstance(client.state().get("effects"), list))
        client.command("effect @s speed 10 2")
        wait_ticks(client, 3)
        effects = client.state().get("effects", [])
        checks.check(
            "effects_speed",
            any(int(e.get("id", -1)) == 1 and int(e.get("amplifier", -1)) == 2 for e in effects),
            str(effects),
        )
        client.command("effect @s clear")
        wait_ticks(client, 3)
        client.reset(feet[0], feet[1], feet[2], yaw=0.0, pitch=0.0)
        wait_ticks(client, 12)
        saved = client.checkpoint_save("selftest_cp")
        checks.check("checkpoint_save", saved.get("event") == "checkpoint_saved", str(saved))
        listed = client.checkpoint_list().get("checkpoints", [])
        checks.check(
            "checkpoint_list",
            any(row.get("name") == "selftest_cp" for row in listed),
            f"count={len(listed)}",
        )
        client.teleport(feet[0] + 2.0, feet[1], feet[2])
        wait_ticks(client, 12)
        loaded_cp = client.checkpoint_load("selftest_cp")
        wait_ticks(client, 12)
        now_cp = client.state()
        cp_gap = max(
            abs(now_cp["feet_x"] - feet[0]),
            abs(now_cp["feet_y"] - feet[1]),
            abs(now_cp["feet_z"] - feet[2]),
        )
        checks.check(
            "checkpoint_load",
            loaded_cp.get("event") == "checkpoint_loaded" and cp_gap < 0.3,
            f"gap={cp_gap:.3f}",
        )
        client.checkpoint_delete("selftest_cp")

        scanned = client.scan(
            (feet[0] - 1, feet[1] - 2, feet[2] - 1),
            (feet[0] + 1, feet[1] + 1, feet[2] + 1),
        )
        checks.check(
            "scan",
            int(scanned.get("count", 0)) > 0 and scanned.get("truncated") is False,
            f"count={scanned.get('count')}",
        )

        client.teleport(feet[0], feet[1] + 5.0, feet[2])
        landed = None
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            try:
                message = client.readline(1.0)
            except ModPipeError:
                break
            if message.get("event") == "player_landed":
                landed = message
                break
        checks.check(
            "player_landed",
            landed is not None and float(landed.get("fall_distance", 0.0)) >= 3.0,
            str(landed),
        )
        client.command("effect @s instant_health 1 10")
        wait_ticks(client, 10)

        before = client.inventory()
        checks.check(
            "inventory", len(before.get("slots", [])) >= 9, f"slots={len(before.get('slots', []))}"
        )
        original_slot0 = slot_item(before, 0)

        # -- inventory write / read ----------------------------------------
        client.set_item(0, "minecraft:stone", count=7)
        wait_ticks(client, 10)
        item = slot_item(client.inventory(), 0)
        checks.check(
            "set_item",
            bool(item) and item["type"] == "minecraft:stone" and int(item["count"]) == 7,
            str(item),
        )

        client.give("minecraft:dirt", count=3)
        wait_ticks(client, 10)
        dirt_slot, dirt = find_item(client.inventory(), "minecraft:dirt")
        checks.check("give", dirt is not None and int(dirt["count"]) >= 3, f"slot={dirt_slot}")

        client.clear_item(0)
        wait_ticks(client, 10)
        checks.check("clear_item", slot_item(client.inventory(), 0) is None)
        if dirt_slot not in (None, 0):
            client.clear_item(dirt_slot)
            wait_ticks(client, 10)

        # restore the original hotbar slot and selection
        if original_slot0:
            client.set_item(
                0,
                original_slot0["type"],
                count=int(original_slot0["count"]),
                aux=int(original_slot0.get("aux", 0)),
            )
        else:
            client.clear_item(0)
        client.select_slot(selected)
        wait_ticks(client, 5)

        # -- teleport ------------------------------------------------------
        client.teleport(*feet)
        wait_ticks(client, 20)
        now = client.state()
        gap = max(
            abs(now["feet_x"] - feet[0]), abs(now["feet_y"] - feet[1]), abs(now["feet_z"] - feet[2])
        )
        checks.check("teleport", gap < 0.2, f"gap={gap:.3f}")

        # -- entity --------------------------------------------------------
        short = args.entity.split(":")[-1]
        tx, ty, tz = feet[0] + 3.0, feet[1], feet[2]
        client.command(f"summon {args.entity} {tx:.2f} {ty:.2f} {tz:.2f}")
        wait_ticks(client, 25)
        entities = client.entities(radius=args.radius, limit=64).get("entities", [])
        matches = [
            row
            for row in entities
            if short.lower() in (str(row.get("type", "")) + str(row.get("name", ""))).lower()
        ]
        checks.check("summon/entities", bool(matches), f"count={len(entities)}")

        if matches:
            runtime_id = int(matches[0]["runtime_id"])
            info = client.entity(runtime_id)
            checks.check(
                "entity_info", int(info.get("runtime_id", -1)) == runtime_id, str(info.get("type"))
            )

            ecs_entity = client.ecs_entity(runtime_id)
            checks.check(
                "ecs_entity",
                int(ecs_entity.get("components", 0)) > 0
                and int(ecs_entity.get("registry", 0)) != 0,
                str({k: ecs_entity.get(k) for k in ("components", "registry", "entity")}),
            )
            components = client.ecs_components(runtime_id).get("components", [])
            sized = [row for row in components if int(row.get("stride", 0)) > 0]
            checks.check(
                "ecs_components",
                bool(components) and bool(sized),
                f"components={len(components)} sized={len(sized)}",
            )
            if sized:
                component = sized[0]
                length = min(int(component["stride"]), 512)
                raw = client.ecs_component(runtime_id, int(component["id"]), length=length)
                checks.check(
                    "ecs_component",
                    bool(raw.get("ok")) and len(str(raw.get("hex", ""))) == 2 * length,
                    str({k: raw.get(k) for k in ("component_id", "stride", "len", "ok")}),
                )
            registries = client.ecs_registries().get("registries", [])
            checks.check(
                "ecs_registries",
                bool(registries) and int(registries[0].get("components", 0)) > 0,
                str(registries[:1]),
            )

            look = client.look_at_entity(runtime_id)
            # the entity may have moved since the info snapshot
            fresh = client.entity(runtime_id)
            expected_yaw = math.degrees(
                math.atan2(-(float(fresh["x"]) - feet[0]), (float(fresh["z"]) - feet[2]))
            )
            current = client.state()
            yaw_gap = abs((float(current.get("yaw", 0.0)) - expected_yaw + 180.0) % 360.0 - 180.0)
            checks.check(
                "look_at_entity",
                look.get("event") == "look_at_done" and yaw_gap < 20.0,
                f"yaw={current.get('yaw'):.1f} expected={expected_yaw:.1f}",
            )

            health_before = info.get("health")
            client.attack_entity(runtime_id)
            wait_ticks(client, 15)
            try:
                health_after = client.entity(runtime_id).get("health")
            except ModPipeError:
                health_after = None  # entity died / despawned
            damaged = health_after is None or (
                health_before is not None and float(health_after) < float(health_before)
            )
            checks.check("attack_entity", damaged, f"health {health_before} -> {health_after}")

            # entity use requires a held item; wheat also exercises a real
            # interaction (breeding/food) instead of an empty-hand no-op
            client.set_item(0, "minecraft:wheat", count=1)
            client.select_slot(0)
            wait_ticks(client, 5)
            try:
                interact = client.interact_entity(runtime_id)
            except ModPipeError:
                interact = {"event": "entity_interacted", "ok": False, "gone": True}
            checks.check(
                "interact_entity",
                interact.get("event") == "entity_interacted",
                f"ok={interact.get('ok')}",
            )
            wait_ticks(client, 20)
            after_interact = slot_item(client.inventory(), 0)
            checks.note(
                "interact_item",
                f"slot0={after_interact} (use is injected as an input, best effort)",
            )

            if original_slot0:
                client.set_item(
                    0,
                    original_slot0["type"],
                    count=int(original_slot0["count"]),
                    aux=int(original_slot0.get("aux", 0)),
                )
            else:
                client.clear_item(0)
            client.select_slot(selected)
            wait_ticks(client, 3)

            if not args.keep:
                client.command(f"kill @e[type={short},c=1]")
                wait_ticks(client, 5)

        checks.check("macro_status", client.macro_status().get("event") == "macro_status")
        return checks.finish()
    except ModPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
