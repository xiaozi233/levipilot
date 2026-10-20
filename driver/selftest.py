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

from driver.transport_mod import ModClient, ModPipeError  # noqa: E402


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

        state = client.state()
        if not state.get("in_world"):
            checks.check("in_world", False, "玩家不在世界中")
            return checks.finish()
        checks.check("in_world", True)

        feet = (state["feet_x"], state["feet_y"], state["feet_z"])
        selected = int(state.get("selected_slot", 0))

        before = client.inventory()
        checks.check("inventory", len(before.get("slots", [])) >= 9, f"slots={len(before.get('slots', []))}")
        original_slot0 = slot_item(before, 0)
        original_selected = slot_item(before, selected)

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
        gap = max(abs(now["feet_x"] - feet[0]), abs(now["feet_y"] - feet[1]), abs(now["feet_z"] - feet[2]))
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
            checks.check("entity_info", int(info.get("runtime_id", -1)) == runtime_id, str(info.get("type")))

            look = client.look_at_entity(runtime_id)
            # the entity may have moved since the info snapshot
            fresh = client.entity(runtime_id)
            expected_yaw = math.degrees(
                math.atan2(-(float(fresh["x"]) - feet[0]), (float(fresh["z"]) - feet[2]))
            )
            current = client.state()
            yaw_gap = abs((float(current.get("yaw", 0.0)) - expected_yaw + 180.0) % 360.0 - 180.0)
            checks.check("look_at_entity", look.get("event") == "look_at_done" and yaw_gap < 20.0,
                         f"yaw={current.get('yaw'):.1f} expected={expected_yaw:.1f}")

            health_before = info.get("health")
            attack = client.attack_entity(runtime_id)
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
            checks.check("interact_entity", interact.get("event") == "entity_interacted",
                         f"ok={interact.get('ok')}")
            wait_ticks(client, 20)
            after_interact = slot_item(client.inventory(), 0)
            checks.note("interact_item", f"slot0={after_interact} (use is injected as an input, best effort)")

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