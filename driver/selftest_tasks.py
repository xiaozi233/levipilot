"""Closed-loop task lifecycle and world-effect acceptance checks."""

from __future__ import annotations

import contextlib
import math
from pathlib import Path

from driver.harness.lab import settle, world_state
from driver.selftest_runtime import WorldLost, slot_item, wait_ticks
from driver.selftest_support import Checks, preserve_failure
from driver.transport_mod import ModClient, ModTimeout


def check_task_lifecycle(client: ModClient, checks: Checks) -> None:
    """Replacing jump/route with place/use must not leave a hidden task."""
    original = client.state()
    pose = [float(original[f"feet_{axis}"]) for axis in "xyz"]
    source_tasks = (
        (
            "jump_when",
            {
                "cmd": "jump_when",
                "mode": "tick",
                "after_ticks": 2000,
                "max_ticks": 5000,
                "forward": 0,
            },
        ),
        (
            "route",
            {
                "cmd": "route",
                "waypoints": [
                    {"x": pose[0] + 20, "z": pose[2], "jump": "never", "max_ticks": 2000}
                ],
            },
        ),
    )
    try:
        for source, payload in source_tasks:
            for target in ("place", "use"):
                client.reset(*pose, yaw=float(original["yaw"]), pitch=float(original["pitch"]))
                wait_ticks(client, 5)
                first = client.request(payload, f"{source}_started")
                if target == "place":
                    # Occupied support is a safe, deterministic terminal result.
                    command = {
                        "cmd": "place",
                        "x": math.floor(pose[0]),
                        "y": math.floor(pose[1]) - 1,
                        "z": math.floor(pose[2]),
                    }
                else:
                    command = {"cmd": "use", "ticks": 2000}
                second = client.request(command, f"{target}_started")
                first_id, second_id = int(first["task_id"]), int(second["task_id"])
                cancelled = client.wait_event(
                    "task_cancelled",
                    3,
                    predicate=lambda row, task_id=first_id: row.get("task_id") == task_id,
                )
                checks.check(
                    f"task_{source}_to_{target}_cancel",
                    second_id > first_id and cancelled.get("reason") == "replaced",
                )
                if target == "place":
                    result = client.wait_event(
                        "block_placed",
                        3,
                        predicate=lambda row, task_id=second_id: row.get("task_id") == task_id,
                    )
                    checks.check(
                        f"task_{source}_to_place_terminal", result.get("reason") == "occupied"
                    )
                else:
                    status = client.task_status()
                    checks.check(
                        f"task_{source}_to_use_owner",
                        status.get("active") == "use" and status.get("task_id") == second_id,
                    )
                    # Invalid requests must leave the current owner intact.
                    for bad, reason in (
                        ({"cmd": "jump_when", "mode": "typo"}, "invalid_jump_mode"),
                        ({"cmd": "route", "waypoints": [{}] * 65}, "too_many_waypoints"),
                        ({"cmd": "route", "waypoints": [None]}, "invalid_waypoint"),
                        ({"cmd": "place", "expected": "x" * 256}, "expected_too_long"),
                    ):
                        rejected = client.request(bad, "error")
                        checks.check(
                            f"task_validation_{source}_{reason}",
                            rejected.get("reason") == reason
                            and client.task_status().get("task_id") == second_id,
                        )
                    client.task_stop()
                    stopped = client.wait_event(
                        "task_cancelled",
                        3,
                        predicate=lambda row, task_id=second_id: row.get("task_id") == task_id,
                    )
                    checks.check(
                        f"task_{source}_to_use_stopped", stopped.get("reason") == "stopped"
                    )
                wait_ticks(client, 3)
                checks.check(
                    f"task_{source}_to_{target}_idle", client.task_status().get("active") is None
                )

        inventory = client.inventory()
        empty = next(
            (
                row["slot"]
                for row in inventory.get("slots", [])
                if int(row["slot"]) < 9 and not row.get("item")
            ),
            None,
        )
        if empty is not None:
            client.select_slot(int(empty))
            wait_ticks(client, 3)
            result = client.use_item(ticks=1)
            checks.check(
                "task_use_server_terminal",
                result.get("reason") == "empty_hand"
                and int(result.get("task_id", 0)) > 0
                and client.task_status().get("active") is None,
            )
        else:
            checks.skip("task_use_server_terminal", "no empty hotbar slot")
    finally:
        client.task_stop()
        client.select_slot(int(original["selected_slot"]))
        client.reset(*pose, yaw=float(original["yaw"]), pitch=float(original["pitch"]))
        wait_ticks(client, 5)

    for attempt in range(3):
        old_session = client._session
        client.reconnect()
        client.input_lock(bool(original.get("input", {}).get("locked")))
        checks.check(
            f"connection_session_{attempt}",
            client._session is not old_session
            and old_session.stream.stopped.is_set()
            and client.ping().get("event") == "pong",
        )

    mod_dir = Path(str(client.status()["spool"])).parent
    before_fault = {(p.name, p.stat().st_mtime_ns) for p in mod_dir.glob("crash-*")}
    fault = client.request({"cmd": "selftest_task_fault"}, ("task_fault_started", "error"))
    if fault.get("event") == "error":
        checks.check("task_fault_gate", fault.get("reason") == "selftest_stress_disabled")
        checks.skip("task_fault_isolation", "allow_selftest_stress is disabled")
    else:
        fault_id = int(fault["task_id"])
        result = client.wait_event(
            "item_used", 3, predicate=lambda row: row.get("task_id") == fault_id
        )
        start = int(client.status().get("tick", 0))
        wait_ticks(client, 3)
        checks.check(
            "task_fault_isolation",
            result.get("reason") == "task_fault"
            and not result.get("ok")
            and client.task_status().get("active") is None
            and int(client.status().get("tick", 0)) > start,
        )
        checks.check(
            "task_handled_fault_no_dump",
            {(p.name, p.stat().st_mtime_ns) for p in mod_dir.glob("crash-*")} == before_fault,
        )

    check_use_lifecycle(client, checks)


def check_use_lifecycle(client: ModClient, checks: Checks) -> None:
    """Inject bounded failures before any item effect; verify one terminal and recovery."""
    mod_dir = Path(str(client.status()["spool"])).parent

    def crash_artifacts() -> set[tuple[str, int]]:
        return {(path.name, path.stat().st_mtime_ns) for path in mod_dir.glob("crash-*")}

    before = crash_artifacts()
    for mode, reason in (
        ("timeout", "server_timeout"),
        ("queue", "server_task_queue_failed"),
        ("seh", "server_task_failed"),
        ("cpp", "server_task_failed"),
        ("effect_seh", "server_task_fault"),
    ):
        started = client.request(
            {"cmd": "selftest_use_failure", "mode": mode}, ("use_started", "error")
        )
        if started.get("event") == "error":
            checks.check("use_failure_gate", started.get("reason") == "selftest_stress_disabled")
            checks.skip("use_failure_recovery", "allow_selftest_stress is disabled")
            return
        task_id = int(started["task_id"])
        result = client.wait_event(
            "item_used", 3, predicate=lambda row, task_id=task_id: row.get("task_id") == task_id
        )
        checks.check(
            f"use_{mode}_terminal",
            result.get("reason") == reason
            and result.get("ok") is False
            and client.task_status().get("active") is None,
            str(result),
        )
        duplicate = None
        with contextlib.suppress(ModTimeout):
            duplicate = client.wait_event(
                ("item_used", "task_cancelled", "error"),
                0.15,
                predicate=lambda row, task_id=task_id: (
                    row.get("task_id") == task_id or row.get("event") == "error"
                ),
            )
        checks.check(f"use_{mode}_unique", duplicate is None, str(duplicate or ""))
        advanced = wait_ticks(client, 3)
        checks.check(f"use_{mode}_recovery", advanced >= 3 and client.ping().get("event") == "pong")
    checks.check("use_handled_fault_no_dump", crash_artifacts() == before)


def check_task_operations(client: ModClient, checks: Checks) -> None:
    """Verify real navigation and place/mine effects on a temporary air cell."""
    original = client.state()
    if original.get("in_world") is not True:
        raise WorldLost("task_operations: player left the world")
    if any(f"feet_{axis}" not in original for axis in "xyz"):
        # Never let a missing pose surface as a KeyError: the interesting event
        # is that the level is already gone (see WorldLost in main).
        checks.check(
            "task_operations_pose",
            False,
            f"no player pose: in_world={original.get('in_world')} state={world_state(client)}",
        )
        return
    pose = [float(original[f"feet_{axis}"]) for axis in "xyz"]
    snapshot = client.inventory()
    slot = next(
        (
            int(row["slot"])
            for row in snapshot.get("slots", [])
            if 0 <= int(row["slot"]) < 9 and not row.get("item")
        ),
        None,
    )
    x, y, z = math.floor(pose[0]), math.floor(pose[1]), math.floor(pose[2])

    def find_target() -> list[int] | None:
        # A moving actor can make useItemOn reject an otherwise valid air cell.
        # Re-evaluate immediately before placing, after the navigation tests.
        actors = client.entities(radius=8, limit=128).get("entities", [])
        for dx, dz in ((2, 0), (-2, 0), (0, 2), (0, -2)):
            cell = [x + dx, y, z + dz]
            rows = client.blocks([cell, [cell[0], y - 1, cell[2]]])["blocks"]
            if rows[0].get("name") != "minecraft:air" or rows[1].get("name") not in {
                "minecraft:stone",
                "minecraft:dirt",
                "minecraft:grass_block",
                "minecraft:bedrock",
            }:
                continue
            if any(
                abs(float(actor["y"]) - y) < 3
                and math.hypot(float(actor["x"]) - cell[0] - 0.5, float(actor["z"]) - cell[2] - 0.5)
                < 2
                for actor in actors
            ):
                continue
            return cell
        return None

    target = find_target()
    if target is None or slot is None:
        checks.skip(
            "task_operations",
            "needs an empty hotbar slot and nearby air above solid support",
        )
        return
    try:
        # Creative avoids item drops and restores exactly the air cell we own.
        client.command("gamemode creative @s")
        wait_ticks(client, 5)
        checks.check("task_operations_setup", int(client.state().get("gamemode", -1)) == 1)
        client.set_item(slot, "minecraft:dirt", count=2)
        client.select_slot(slot)
        wait_ticks(client, 5)
        # Regress the deterministic orbit at -53.17 degrees, also checking
        # a full turn and aligned starts through both movement entry points.
        for task in ("navigate", "route"):
            for yaw in (-53.17, 0.0, 90.0, 180.0):
                client.reset(*pose, yaw=yaw, pitch=float(original["pitch"]))
                wait_ticks(client, 8)
                if task == "navigate":
                    result = client.navigate(
                        pose[0], pose[2] - 0.7, tolerance=0.15, jump="never", max_ticks=80
                    )
                else:
                    result = client.route(
                        [
                            {
                                "x": pose[0],
                                "z": pose[2] - 0.7,
                                "tolerance": 0.15,
                                "jump": "never",
                                "max_ticks": 80,
                            }
                        ]
                    )
                state = client.state()
                checks.check(
                    f"task_{task}_readback_{yaw:g}",
                    bool(result.get("ok"))
                    and math.hypot(
                        float(state["feet_x"]) - pose[0], float(state["feet_z"]) - (pose[2] - 0.7)
                    )
                    < 0.25,
                    str(result),
                )
        # Navigation returns as soon as it reaches tolerance; release residual
        # movement and restore the fixture pose before testing item placement.
        client.reset(*pose, yaw=float(original["yaw"]), pitch=float(original["pitch"]))
        ready = settle(client, ticks=3, in_water=False)
        fresh_target = find_target()
        if not checks.check(
            "task_place_ready",
            ready.get("ok") and fresh_target is not None,
            str({"settled": ready.get("ok"), "target": fresh_target}),
        ):
            return
        assert fresh_target is not None
        target = fresh_target
        # Aim through the command channel first: the camera is written back
        # every frame, so a task-internal aim can be stomped by the real mouse.
        pose_now = client.state()
        eye = (
            float(pose_now["feet_x"]),
            float(pose_now["feet_y"]) + 1.62,
            float(pose_now["feet_z"]),
        )
        click = (target[0] + 0.5, float(target[1]), target[2] + 0.5)
        ddx, ddy, ddz = click[0] - eye[0], click[1] - eye[1], click[2] - eye[2]
        client.look(
            math.degrees(math.atan2(-ddx, ddz)) % 360.0,
            math.degrees(math.atan2(-ddy, math.hypot(ddx, ddz))),
        )
        wait_ticks(client, 3)
        placed = client.place_block(*target, slot=slot, expected="dirt")
        wait_ticks(client, 5)
        checks.check(
            "task_place_readback",
            bool(placed.get("ok"))
            and client.blocks([target])["blocks"][0].get("name") == "minecraft:dirt",
            str(placed),
        )
        mined = client.mine_block(*target, max_ticks=80)
        wait_ticks(client, 5)
        checks.check(
            "task_mine_readback",
            bool(mined.get("ok"))
            and client.blocks([target])["blocks"][0].get("name") == "minecraft:air",
            str(mined),
        )
        ids = [int(row["task_id"]) for row in (result, placed, mined)]
        checks.check("task_operations_ids", ids[0] < ids[1] < ids[2])
    finally:
        with preserve_failure(checks, "task_operations"):
            client.task_stop()
            # A successful mine already left air; do not issue a redundant command
            # that reports failure when its target already contains air.
            if client.blocks([target])["blocks"][0].get("name") != "minecraft:air":
                client.command(f"setblock {' '.join(map(str, target))} air")
                wait_ticks(client, 5)
            client.clear_item(slot)
            client.select_slot(int(original["selected_slot"]))
            client.command(f"gamemode {int(original['gamemode'])} @s")
            client.reset(*pose, yaw=float(original["yaw"]), pitch=float(original["pitch"]))
            wait_ticks(client, 8)
            checks.check(
                "task_operations_restored",
                int(client.state().get("gamemode", -1)) == int(original["gamemode"])
                and slot_item(client.inventory(), slot) is None
                and client.blocks([target])["blocks"][0].get("name") == "minecraft:air",
            )
