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
import contextlib
import itertools
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.harness.hooks import decode_hook_hits  # noqa: E402
from driver.harness.lab import Builder, double_space_fly, settle, swim_drive, swim_stop  # noqa: E402
from driver.harness.memory import fnv1_64, fnv1a_64  # noqa: E402
from driver.harness.record import RecordEntry  # noqa: E402
from driver.harness.spool import read_spool  # noqa: E402
from driver.transport_mod import ModClient, ModPipeError, ModProtocolError, ModTimeout  # noqa: E402

# Fixed swim fixture: 3x3x10 water column inside a stone shell at (900, 900).
SWIM_X, SWIM_Y, SWIM_Z = 900, -55.5, 900


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


def wait_still(client: ModClient, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.state()
        if (
            state.get("on_ground")
            and abs(float(state.get("vx", 0.0))) < 0.01
            and abs(float(state.get("vz", 0.0))) < 0.01
        ):
            time.sleep(0.3)
            return
        time.sleep(0.05)


def wait_screen(client: ModClient, expected: str, timeout: float = 2.0) -> str:
    deadline = time.monotonic() + timeout
    name = ""
    while time.monotonic() < deadline:
        name = str(client.screen().get("name", ""))
        if name == expected:
            return name
        time.sleep(0.05)
    return name


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


def jump_arcs(entries: list[RecordEntry]) -> list[list[float]]:
    """Extract complete, contiguous ground-to-ground arcs relative to takeoff."""
    arcs: list[list[float]] = []
    start: int | None = None
    for index in range(1, len(entries)):
        before, row = entries[index - 1], entries[index]
        if row.tick != before.tick + 1:
            start = None
            continue
        if before.on_ground and not row.on_ground and row.feet_y > before.feet_y + 0.05:
            start = index - 1
        if start is not None and row.on_ground:
            floor = entries[start].feet_y
            arcs.append([entry.feet_y - floor for entry in entries[start : index + 1]])
            start = None
    return arcs


def check_jump_motion(client: ModClient, checks: Checks, inject_enabled: bool) -> None:
    """Compare repeated scripted jumps with Space's HID binding, in place."""
    original = client.state()
    ready = (
        original.get("on_ground")
        and not original.get("flying")
        and not original.get("in_water")
        and not original.get("effects")
        and client.screen().get("name") == "hud_screen"
    )
    if not checks.check("jump_motion_ready", ready, "dry ground, no effects, HUD"):
        return
    pose = [float(original[f"feet_{axis}"]) for axis in "xyz"]
    runs: dict[str, list[list[float]]] = {}
    modes = ["set_hold", "jump_when"]
    if inject_enabled:
        modes.insert(0, "hid_hold")
    try:
        for mode in modes:
            client.reset(*pose, yaw=float(original["yaw"]), pitch=float(original["pitch"]))
            wait_ticks(client, 8)
            wait_still(client)
            client.record_bin_start(period=1)
            wait_ticks(client, 3)
            if mode == "hid_hold":
                client.key_event(32, True, via="hid")
            elif mode == "set_hold":
                client.set_input(jump=True, ticks=48)
            else:
                client.jump_when("now")
            wait_ticks(client, 48)
            if mode == "hid_hold":
                client.key_event(32, False, via="hid")
            client.stop_move()
            wait_ticks(client, 25)
            client.record_bin_stop()
            rows = client.record_dump_all(format="bin")
            arcs = jump_arcs(rows)
            runs[mode] = arcs
            checks.check(
                f"jump_motion_{mode}",
                (len(arcs) == 1 if mode == "jump_when" else len(arcs) >= 3)
                and all(len(arc) > 4 and abs(arc[1] - 0.42) < 1e-4 for arc in arcs),
                f"arcs={len(arcs)} first_dy={[round(arc[1], 5) for arc in arcs]}",
            )
            checks.check(
                f"jump_motion_{mode}_settled",
                len(rows) >= 5
                and all(row.on_ground and abs(row.feet_y - pose[1]) < 1e-4 for row in rows[-5:])
                and all(math.hypot(row.x - pose[0], row.z - pose[2]) < 1e-4 for row in rows),
                "stationary; released jump returns to ground",
            )
        if inject_enabled:
            reference = runs["hid_hold"][0] if runs["hid_hold"] else []
            compared = runs["set_hold"] + runs["jump_when"]
            checks.check(
                "jump_motion_matches_space",
                bool(reference)
                and bool(compared)
                and all(
                    len(arc) == len(reference)
                    and all(
                        abs(actual - expected) < 1e-4
                        for actual, expected in zip(arc, reference, strict=True)
                    )
                    for arc in compared
                ),
                f"complete arcs compared={len(compared)} tolerance=0.0001 blocks",
            )
    finally:
        if inject_enabled:
            client.key_event(32, False, via="hid")
        client.stop_move()
        client.record_bin_stop()
        client.reset(*pose, yaw=float(original["yaw"]), pitch=float(original["pitch"]))
        wait_ticks(client, 8)


def check_dispatch_compatibility(client: ModClient, checks: Checks) -> None:
    """Exercise every extracted domain and the shared error boundary."""
    requests = (
        ("diagnostics", {"cmd": "ping"}, "pong"),
        ("inspection", {"cmd": "mem_read", "addr": "invalid"}, "mem_read"),
        ("input", {"cmd": "mouse"}, "mouse"),
        ("packets", {"cmd": "packet_stats"}, "packet_stats"),
        ("recording", {"cmd": "record_dump", "limit": 1}, "record_dump"),
        ("query", {"cmd": "state"}, "state"),
        ("world", {"cmd": "worlds"}, "worlds"),
        ("tasks", {"cmd": "task_status"}, "task_status"),
    )
    for domain, payload, event in requests:
        reply = client.request(payload, event)
        checks.check(f"dispatch_{domain}", reply.get("event") == event)
        if domain == "inspection":
            checks.check("dispatch_bad_addr", reply.get("reason") == "bad_addr")

    # A handled validation error must not fall through to unknown_cmd. A
    # following ping also exposes a duplicate error left in the response stream.
    unknown = client.request({"cmd": "__selftest_unknown_command__"}, "error")
    checks.check("dispatch_unknown_cmd", unknown.get("reason") == "unknown_cmd")
    bad_type = client.request({"cmd": "mem_read", "addr": "1", "len": []}, "error")
    checks.check("dispatch_bad_command", bad_type.get("reason") == "bad_command")
    checks.check("dispatch_error_recovery", client.ping().get("event") == "pong")


def check_cli_compatibility(client: ModClient, checks: Checks) -> None:
    """Exercise each CLI domain over the actual pipe, without changing the world."""
    commands = (
        ("diagnostics", ["ping"], '"event": "pong"'),
        ("input", ["mouse"], '"event": "mouse"'),
        ("inspection", ["mem-read", "0x10"], '"reason": "bad_addr"'),
        ("packets", ["packet-stats"], '"event": "packet_stats"'),
        ("query", ["state"], '"event": "state"'),
        ("chat_history", ["chat-history", "--limit", "1"], '"event": "chat_history"'),
        ("world", ["worlds"], '"event": "worlds"'),
        ("tasks", ["task-status"], '"event": "task_status"'),
        ("recording", ["record-dump", "--limit", "1"], "entries="),
    )
    # The server accepts one client at a time. Release this connection before
    # starting subprocesses, then reconnect even if a subprocess fails.
    client.close()
    try:
        for domain, argv, expected in commands:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "driver" / "transport_mod.py"),
                    "--pipe",
                    client.pipe_name,
                    *argv,
                ],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            checks.check(
                f"cli_{domain}",
                result.returncode == 0 and expected in result.stdout,
                f"exit={result.returncode}"
                + (f" stderr={result.stderr.strip()}" if result.stderr else ""),
            )
        result = subprocess.run(
            [sys.executable, "-m", "driver.transport_mod", "--pipe", client.pipe_name, "ping"],
            cwd=ROOT,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        checks.check(
            "cli_module_entry", result.returncode == 0 and '"event": "pong"' in result.stdout
        )
    finally:
        client.connect()


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
            checks.note("task_use_server_terminal", "skipped: no empty hotbar slot")
    finally:
        client.task_stop()
        client.select_slot(int(original["selected_slot"]))
        client.reset(*pose, yaw=float(original["yaw"]), pitch=float(original["pitch"]))
        wait_ticks(client, 5)

    for attempt in range(3):
        old_session = client._session
        client.reconnect()
        checks.check(
            f"connection_session_{attempt}",
            client._session is not old_session
            and old_session.stopped.is_set()
            and client.ping().get("event") == "pong",
        )

    fault = client.request({"cmd": "selftest_task_fault"}, ("task_fault_started", "error"))
    if fault.get("event") == "error":
        checks.check("task_fault_gate", fault.get("reason") == "selftest_stress_disabled")
        checks.note("task_fault_isolation", "skipped: allow_selftest_stress is disabled")
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


def check_task_operations(client: ModClient, checks: Checks) -> None:
    """Verify real navigation and place/mine effects on a temporary air cell."""
    original = client.state()
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
    target = None
    for dx, dz in ((2, 0), (-2, 0), (0, 2), (0, -2)):
        cell = [x + dx, y, z + dz]
        rows = client.blocks([cell, [cell[0], y - 1, cell[2]]])["blocks"]
        if rows[0].get("name") == "minecraft:air" and rows[1].get("name") in {
            "minecraft:stone",
            "minecraft:dirt",
            "minecraft:grass_block",
            "minecraft:bedrock",
        }:
            target = cell
            break
    if target is None or slot is None:
        checks.note(
            "task_operations",
            "skipped: needs an empty hotbar slot and nearby air above solid support",
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


def check_lifecycle_stress(
    client: ModClient, checks: Checks, cycles: int, world: str | None = None
) -> None:
    """Opt-in soak: task replacement, pipe generations and world teardown."""
    original = client.state()
    pose = [float(original[f"feet_{axis}"]) for axis in "xyz"]
    previous_id = 0
    completed = False
    try:
        # A discarded oversized fragment belongs only to its connection.
        client.send_raw(b"x" * ((1 << 20) + 4096))
        rejected = client.wait_event("error", 20)
        checks.check("stress_oversize_fragment", rejected.get("reason") == "line_too_long")
        client.reconnect()
        checks.check("stress_fragment_reconnect", client.ping().get("event") == "pong")
        for cycle in range(cycles):
            first = client.request(
                {
                    "cmd": "jump_when",
                    "mode": "tick",
                    "after_ticks": 2000,
                    "max_ticks": 5000,
                    "forward": 0,
                },
                "jump_when_started",
            )
            second = client.request({"cmd": "use", "ticks": 2000}, "use_started")
            first_id, second_id = int(first["task_id"]), int(second["task_id"])
            cancelled = client.wait_event(
                "task_cancelled",
                5,
                predicate=lambda row, task_id=first_id: row.get("task_id") == task_id,
            )
            checks.check(
                f"stress_{cycle}_replacement",
                previous_id < first_id < second_id and cancelled.get("reason") == "replaced",
            )
            previous_id = second_id
            if world and (cycle + 1) % 10 == 0:
                left = client.leave_world()
                client.wait_event("world_left", 30)
                terminal = client.wait_event(
                    "task_cancelled",
                    5,
                    predicate=lambda row, task_id=second_id: row.get("task_id") == task_id,
                )
                checks.check(
                    f"stress_{cycle}_world_cancel",
                    left.get("ok") and terminal.get("reason") == "world_left",
                )
                joined = client.enter_world_ready(world)
                checks.check(f"stress_{cycle}_world_join", joined.get("ok"))
                client.wait_in_world(timeout=60)
                client.reset(*pose, yaw=float(original["yaw"]), pitch=float(original["pitch"]))
                wait_ticks(client, 10)
            else:
                # The server task remains owned while the client reconnects.
                client.reconnect()
                checks.check(
                    f"stress_{cycle}_owner", client.task_status().get("task_id") == second_id
                )
                client.task_stop()
                terminal = client.wait_event(
                    "task_cancelled",
                    5,
                    predicate=lambda row, task_id=second_id: row.get("task_id") == task_id,
                )
                checks.check(f"stress_{cycle}_stopped", terminal.get("reason") == "stopped")
            checks.check(
                f"stress_{cycle}_recovered",
                client.task_status().get("active") is None and client.ping().get("event") == "pong",
            )
        completed = True
    finally:
        # Recovery must never replace the failure that brought us here: if the
        # client crashed, the pipe is gone and reconnecting is the only check
        # that can run. Report it without masking the original exception.
        try:
            if not client.connected:
                client.reconnect()
            if world and not client.state().get("in_world"):
                client.enter_world_ready(world)
                client.wait_in_world(timeout=60)
            client.stop_move()
            client.task_stop()
            client.macro_stop()
            client.watch_clear(all=True)
            client.reset(*pose, yaw=float(original["yaw"]), pitch=float(original["pitch"]))
            wait_ticks(client, 10)
            checks.check(
                "stress_cleanup",
                client.task_status().get("active") is None
                and client.watch_list().get("watches") == []
                and client.state().get("input", {}).get("move_x") == 0.0,
                "tasks/motion/watch cleared",
            )
        except Exception as exc:  # Recovery is best effort, never mask the failure.
            checks.check("stress_cleanup", False, f"recovery failed: {exc}")
            if completed:
                raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entity", default="minecraft:pig")
    parser.add_argument("--keep", action="store_true", help="do not remove the test entity")
    parser.add_argument("--radius", type=float, default=10.0)
    parser.add_argument("--stress-cycles", type=int, default=0, help="opt-in lifecycle soak cycles")
    parser.add_argument(
        "--stress-world", help="leave/re-enter this test world every 10 soak cycles"
    )
    args = parser.parse_args(argv)
    if args.stress_cycles < 0 or (args.stress_world and args.stress_cycles < 10):
        parser.error(
            "--stress-cycles must be nonnegative; --stress-world requires at least 10 cycles"
        )

    checks = Checks()
    try:
        client = ModClient(connect_timeout=60.0, timeout=30.0).connect()
    except ModPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("提示：游戏内是否已加载 probe mod，并且已经进入存档？", file=sys.stderr)
        return 2

    try:
        # Scripts own the session: drop real hardware keys/mouse so a person at
        # the machine cannot perturb the run. Injected events still pass; use
        # `harness input-lock --off` (or input_lock(false)) to release.
        try:
            auto_lock = client.input_lock(True)
            checks.note("input_lock_auto", f"on={auto_lock.get('on')}")
        except (ModPipeError, ModProtocolError) as exc:
            checks.note("input_lock_auto", f"unavailable: {exc}")
        checks.check("ping", client.ping().get("event") == "pong")
        if args.stress_cycles:
            check_lifecycle_stress(client, checks, args.stress_cycles, args.stress_world)
        client.command('tellraw @s {"rawtext":[{"text":"LeviPilot chat snapshot selftest"}]}')
        wait_ticks(client, 3)
        chat = client.chat_history(limit=4)
        checks.check(
            "chat_history_snapshot",
            chat.get("ok")
            and len(chat.get("messages", [])) <= 4
            and all(isinstance(row.get("text"), str) for row in chat.get("messages", []))
            and any(
                "LeviPilot chat snapshot selftest" in row["text"]
                for row in chat.get("messages", [])
            ),
            str(chat),
        )
        check_dispatch_compatibility(client, checks)
        check_task_lifecycle(client, checks)
        check_task_operations(client, checks)
        check_cli_compatibility(client, checks)

        # -- F1: a client that never sends '\n' cannot grow the buffer ------
        client.send_raw(b"A" * (2 << 20) + b"\n")
        too_long = 0
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            try:
                event = client.readline(0.5)
            except ModTimeout:
                break
            if event.get("event") == "error" and event.get("reason") == "line_too_long":
                too_long += 1
        checks.check("line_too_long", too_long >= 1, f"events={too_long}")
        checks.check("line_too_long_alive", client.ping().get("event") == "pong")

        # -- G1/G2: event-level input injection -----------------------------
        inject_enabled = True
        try:
            client.key_state(49)
            checks.check("input_inject_gate", True, "enabled")
        except ModProtocolError as exc:
            inject_enabled = False
            checks.check("input_inject_gate", "input_inject_disabled" in str(exc), str(exc))
        if inject_enabled:
            client.key_event(49, True, via="hid")
            client.key_event(49, False, via="hid")
            wait_ticks(client, 4)
            checks.check(
                "key_event_slot",
                int(client.state().get("selected_slot", -1)) == 0,
                str(client.state().get("selected_slot")),
            )
            before = client.key_state(72)["events"]
            for _ in range(50):
                client.key_event(72, True, via="hid")
                client.key_event(72, False, via="hid")
            wait_ticks(client, 4)
            after = client.key_state(72)
            checks.check(
                "key_event_count",
                int(after["events"]) - int(before) == 100 and after["down"] is False,
                f"delta={int(after['events']) - int(before)} down={after['down']}",
            )

            # -- input lock: hardware keys stop, injected events keep working --
            slot_before = int(client.state().get("selected_slot", -1))
            lock = client.input_lock(True)
            checks.check(
                "input_lock_on",
                bool(lock.get("on")) and client.state().get("input", {}).get("locked") is True,
                f"{lock} state.locked={client.state().get('input', {}).get('locked')}",
            )
            try:
                # ``raw`` is a genuine Hardware key with no injection guard; it
                # is the only way to exercise the real-press branch of the lock
                # (``hardware`` is a scripted press and passes on purpose, which
                # is what makes double-space flight reproducible under the lock).
                client.key_event(50, True, via="raw")  # '2'
                client.key_event(50, False, via="raw")
                wait_ticks(client, 6)
                after_hw = int(client.state().get("selected_slot", -1))
                client.key_event(51, True, via="hid")  # '3'
                client.key_event(51, False, via="hid")
                wait_ticks(client, 6)
                after_sw = int(client.state().get("selected_slot", -1))
                checks.check(
                    "input_lock_blocks_hardware",
                    after_hw == slot_before,
                    f"slot {slot_before} -> {after_hw}",
                )
                checks.check(
                    "input_lock_allows_injection",
                    after_sw != slot_before,
                    f"slot {slot_before} -> {after_sw}",
                )
                injected_before = int(client.mouse()["counts"][1])
                client.mouse_event("left", down=True)
                client.mouse_event("left", down=False)
                wait_ticks(client, 4)
                injected_after = int(client.mouse()["counts"][1])
                checks.check(
                    "input_lock_allows_mouse_injection",
                    injected_after > injected_before,
                    f"{injected_before} -> {injected_after}",
                )
            finally:
                # Restore the run-wide lock (the block's checks needed the
                # toggle) and the hotbar selection.
                client.input_lock(True)
                client.select_slot(slot_before)
                wait_ticks(client, 3)
            client.mouse_clear()
            client.mouse_event("left", down=True)
            client.mouse_event("left", down=False)
            wait_ticks(client, 4)
            mouse = client.mouse()
            checks.check(
                "mouse_event_count",
                int(mouse["counts"][1]) == 2,
                str({"left": mouse["counts"][1], "last": mouse["last_action"]}),
            )
            yaw0 = float(client.state()["yaw"])
            client.mouse_event("move", dx=200, dy=0)
            wait_ticks(client, 4)
            yaw1 = float(client.state()["yaw"])
            checks.check("mouse_event_turn", abs(yaw1 - yaw0) > 1.0, f"{yaw0:.2f}->{yaw1:.2f}")

            screen0 = str(client.screen().get("name"))
            client.key_event(69)
            client.key_event(69, False)
            opened = wait_screen(client, "inventory_screen")
            client.key_event(27)
            client.key_event(27, False)
            closed = wait_screen(client, screen0)
            checks.check(
                "screen_inventory",
                screen0 == "hud_screen" and opened == "inventory_screen" and closed == screen0,
                f"{screen0}->{opened}->{closed}",
            )

        # -- H0: ability availability gates ---------------------------------
        diagnostics = client.diagnose()
        offsets = {
            (field["struct"], field["field"]): int(field["offset"])
            for field in diagnostics.get("structs", {}).get("struct_fields", [])
        }
        vy_offset = offsets.get(("StateVector", "VelY"))
        checks.check("h0_offsets_table", vy_offset == 0x1C, f"VelY={vy_offset}")

        state = client.state()
        runtime_id = int(state.get("runtime_id", 0))
        components = client.ecs_components(runtime_id).get("components", [])
        state_vector = next(
            (item for item in components if item.get("name") == "StateVector"), None
        )
        checks.check(
            "h0_statevector",
            state_vector is not None
            and int(state_vector.get("size", 0)) == 36
            and int(state_vector.get("stride", 0)) in (0, 36),
            str(state_vector),
        )

        if state_vector is not None and vy_offset is not None:
            # H0.1: identify the vy writer with one stationary jump. This is
            # thread forensics; jump physics are checked separately below.
            # A server task must run once so the integrated server thread id is
            # known; the state vector is written there, not on the tick thread.
            client.command("time set day")
            wait_ticks(client, 3)
            baseline = int(client.watch_list().get("head", 0))
            watch = client.watch_set(
                int(state_vector["address"]) + int(vy_offset),
                size=4,
                mode="w",
                tag="h0_vy",
                thread="both",
            )
            checks.check("h0_watch_install", watch.get("event") == "watch_installed", str(watch))
            try:
                client.jump_when("now")
                wait_ticks(client, 25)
                hits = client.watch_drain(baseline, limit=64).get("hits", [])
            finally:
                client.stop_move()
                client.watch_clear(all=True)
            server_tid = int(client.status().get("server_thread_id", 0))
            checks.check(
                "h0_watch_hits",
                len(hits) >= 1 and any(int(hit["tid"]) == server_tid for hit in hits),
                f"hits={len(hits)} server_tid={server_tid}",
            )
            evidence = "no code_hex"
            store_ok = False
            if hits:
                hit = hits[0]
                code_hex = str(hit.get("code_hex", ""))
                try:
                    import capstone

                    code = bytes.fromhex(code_hex)
                    disassembler = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
                    last = None
                    for instruction in disassembler.disasm(code, int(hit["rip"]) - len(code)):
                        last = instruction
                    if last is not None and last.address + last.size == int(hit["rip"]):
                        evidence = f"{last.mnemonic} {last.op_str} @rip-{last.size}"
                        width = 0
                        op = last.op_str.lower()
                        for marker, size in (
                            ("xmmword ptr", 16),
                            ("qword ptr", 8),
                            ("dword ptr", 4),
                            ("word ptr", 2),
                            ("byte ptr", 1),
                        ):
                            if marker in op:
                                width = size
                                break
                        displacement = None
                        if "[" in op and "+" in op:
                            tail = op.split("+")[-1].split("]")[0].strip()
                            if tail.startswith("0x"):
                                displacement = int(tail, 16)
                        store_ok = (
                            ("mov" in last.mnemonic or "vmov" in last.mnemonic)
                            and "[" in op
                            and displacement is not None
                            and width > 0
                            and displacement <= int(vy_offset) < displacement + width
                        )
                except ImportError:
                    evidence = "capstone not installed"
            checks.check("h0_watch_store", store_ok, evidence)

        # H0.2: probe names/hashes agree with blocks, and record block ids.
        position = client.state()
        bx, by, bz = int(position["feet_x"]), int(position["feet_y"]), int(position["feet_z"])
        client.command(f"setblock {bx + 2} {by} {bz} minecraft:water")
        for _ in range(40):
            if client.blocks([[bx + 2, by, bz]])["blocks"][0].get("name") == "minecraft:water":
                break
            time.sleep(0.05)
        samples = [[bx, by - 1, bz], [bx, by, bz], [bx + 2, by, bz]]
        block_ids: dict[str, int] = {}
        for sample in samples:
            info = client.blocks([sample])["blocks"][0]
            probe = client.probe(info["addr"], "block")
            legacy = client.probe(probe["legacy"], "legacy")
            name = str(probe.get("name", ""))
            checks.check(
                f"h0_probe_{name.split(':')[-1] or 'unknown'}",
                name == info.get("name") and int(legacy.get("hash", 0)) == fnv1_64(name),
                f"{name} hash={legacy.get('hash')}",
            )
            block_ids[name] = int(legacy.get("block_id", -1))
        checks.check(
            "h0_block_ids",
            {"minecraft:air", "minecraft:water"} <= set(block_ids) and len(block_ids) >= 3,
            str(block_ids),
        )
        client.command(f"setblock {bx + 2} {by} {bz} minecraft:air")

        # H0.3: the EntityContext captured by a hook resolves to the same
        # StateVector storage the runtime-id path reports.
        if state_vector is not None:
            hook = client.hook_install("MobJump::setJumpPendingScale", tag="h0_ctx")
            client.hook_call("MobJump::setJumpPendingScale", 1.0)
            wait_ticks(client, 3)
            hook_hits = client.hook_drain(0).get("hits", [])
            context = int(hook_hits[-1]["args"][0]) if hook_hits else 0
            client_context = int(client.state().get("context", 0))
            checks.check(
                "h0_hook_context",
                context != 0 and context == client_context,
                f"hook={context:#x} client={client_context:#x}",
            )
            if context:
                # Resolve StateVector twice: once from the hook-captured
                # context and once from state.context. Enumerating the client
                # registry while the game mutates it is not safe, so compare
                # these two lookups and the offset-table size instead.
                from_hook = client.ecs_context(context, int(state_vector["id"]))
                from_state = client.ecs_context(client_context, int(state_vector["id"]))
                checks.check(
                    "h0_hook_ecs",
                    int(from_hook.get("address", 0)) != 0
                    and int(from_hook.get("address", 0)) == int(from_state.get("address", 0))
                    and int(from_hook.get("count", 0)) == int(from_state.get("count", 0))
                    and int(from_hook.get("size", 0) or 0) == 36,
                    str(
                        {
                            "hook_address": hex(int(from_hook.get("address", 0))),
                            "state_address": hex(int(from_state.get("address", 0))),
                            "count": from_hook.get("count"),
                            "size": from_hook.get("size"),
                        }
                    ),
                )
            if hook.get("event") == "hook_installed":
                client.hook_rm(int(hook["id"]))

        # H0.4: spool tick order survives a panic.
        mod_spool = Path(str(client.hello.get("spool", "")))
        client.start(80)
        while True:
            event = client.readline(8.0).get("event")
            if event == "stopped":
                break
        client.panic("all")
        time.sleep(0.2)
        spool_rows, _ = read_spool(mod_spool)
        tick_numbers = [int(row["tick"]) for row in spool_rows if row.get("event") == "tick"]
        # The spool contains every recording run in this process; a decrease
        # marks a new run. Check the last run only.
        runs: list[list[int]] = []
        current: list[int] = []
        for tick in tick_numbers:
            if current and tick < current[-1]:
                runs.append(current)
                current = []
            current.append(tick)
        if current:
            runs.append(current)
        last_run = runs[-1] if runs else []
        monotonic = (
            last_run == list(range(last_run[0], last_run[0] + len(last_run))) if last_run else False
        )
        checks.check(
            "h0_spool_monotonic",
            len(last_run) >= 60 and monotonic,
            f"ticks={len(last_run)} runs={len(runs)} monotonic={monotonic}",
        )
        checks.check(
            "h0_spool_panic",
            any(row.get("event") == "panic_done" for row in spool_rows),
            "panic_done in spool",
        )

        # -- H1 stage A: packet identity tap --------------------------------
        packet_enabled = True
        try:
            started = client.packet_capture(True, sample=1)
            checks.check(
                "packet_capture_on",
                started.get("active") is True and started.get("send_hooked") is True,
                str(started),
            )
        except ModProtocolError as exc:
            packet_enabled = False
            checks.check("packet_capture_gate", "packet_capture_disabled" in str(exc), str(exc))
        if packet_enabled:
            denied = False
            try:
                client.packet_capture(True, ids=[1])
            except ModProtocolError as exc:
                denied = "denied_id" in str(exc)
            checks.check("packet_denylist", denied, "login id 1 rejected")
            # If the denied request left capture off, turn it back on.
            client.packet_capture(True, sample=1)
            base = int(client.packet_stats().get("records", 0))
            client.set_input(forward=1.0, ticks=-1)
            time.sleep(2.0)
            client.stop_move()
            client.jump_when("now")
            time.sleep(0.4)
            client.command("summon minecraft:pig ~2 ~ ~")
            time.sleep(0.4)
            nearby = client.entities(radius=16, limit=3).get("entities", [])
            if nearby:
                client.attack_entity(int(nearby[0]["runtime_id"]))
            time.sleep(0.4)
            drained = client.packet_drain(base, limit=4096)
            hits = drained.get("hits", [])
            ids = {int(hit["id"]) for hit in hits}
            sequences = [int(hit["seq"]) for hit in hits]
            checks.check(
                "packet_records",
                len(hits) > 0 and 144 in ids,
                f"count={len(hits)} ids={sorted(ids)[:12]}",
            )
            checks.check(
                "packet_seq",
                all(b > a for a, b in itertools.pairwise(sequences)),
                f"n={len(sequences)}",
            )
            checks.check(
                "packet_source",
                all(int(hit["tid"]) != 0 and hit["injected"] is False for hit in hits),
                f"tids={sorted({int(hit['tid']) for hit in hits})}",
            )
            stats = client.packet_stats()
            checks.check(
                "packet_no_drop",
                int(stats.get("dropped", 0)) == 0 and stats.get("active") is True,
                str({k: stats.get(k) for k in ("records", "dropped", "peer_sends", "peer_hooked")}),
            )
            rate = float(client.overhead().get("tick_hz", 0))
            checks.check("packet_tick_rate", rate >= 15.0, f"tick_hz={rate:.2f}")
            # B3: serialize only PlayerAuthInput (144) and correlate by seq.
            client.packet_capture(True, serialize_ids=[144], sample=1)
            client.set_input(forward=1.0, ticks=-1)
            wait_ticks(client, 40)
            client.stop_move()
            wait_ticks(client, 3)
            byte_dump = client.packet_bytes(0, limit=32)
            byte_rows = [row for row in byte_dump.get("hits", []) if int(row["id"]) == 144]
            identity = {
                int(row["seq"]): int(row["id"])
                for row in client.packet_drain(0, limit=4096).get("hits", [])
            }
            checks.check(
                "packet_bytes",
                bool(byte_rows)
                and all(
                    int(row["size"]) > 0 and len(str(row["hex"])) == 2 * min(int(row["size"]), 128)
                    for row in byte_rows
                ),
                f"rows={len(byte_rows)} size={byte_rows[0]['size'] if byte_rows else 0}",
            )
            checks.check(
                "packet_bytes_seq",
                bool(byte_rows)
                and all(identity.get(int(row["seq"])) == int(row["id"]) for row in byte_rows),
                f"matched={sum(1 for row in byte_rows if identity.get(int(row['seq'])) == int(row['id']))}",
            )

            # H1-C: recv-side tap. Server -> client traffic goes through the
            # loopback sender; the Packet* helper hooks measured zero here.
            client.command("summon minecraft:pig ~2 ~ ~")
            wait_ticks(client, 10)
            recv_started = client.packet_capture(
                True, directions=["send", "recv"], sample=1, serialize_ids=[111]
            )
            checks.check(
                "packet_recv_on",
                recv_started.get("recv_hooked") is True
                and recv_started.get("send_hooked") is True
                and all(int(code) == 0 for code in recv_started.get("recv_anchors", {}).values()),
                str(recv_started.get("recv_anchors")),
            )
            recv_base = int(client.packet_stats().get("records", 0))
            time.sleep(2.5)
            recv_drained = client.packet_drain(recv_base, limit=8192)
            recv_hits = [hit for hit in recv_drained.get("hits", []) if hit.get("dir") == "recv"]
            recv_ids = {int(hit["id"]) for hit in recv_hits}
            recv_stats = client.packet_stats()
            checks.check(
                "packet_recv_records",
                len(recv_hits) > 0 and all(int(hit["tid"]) != 0 for hit in recv_hits),
                f"count={len(recv_hits)} ids={sorted(recv_ids)[:8]}",
            )
            checks.check(
                "packet_recv_stats",
                int(recv_stats.get("recv_records", 0)) > 0
                and int(recv_stats.get("recv_hooks", 0)) == 9
                and int(
                    recv_stats.get("recv_anchors", {})
                    .get("loopback_sendToClient", {})
                    .get("hits", 0)
                )
                > 0,
                str({k: recv_stats.get(k) for k in ("recv_records", "recv_hooks")}),
            )
            recv_bytes = [
                row
                for row in client.packet_bytes(0, limit=512).get("hits", [])
                if row.get("dir") == "recv"
            ]
            checks.check(
                "packet_recv_bytes",
                bool(recv_bytes) and all(int(row["size"]) > 0 for row in recv_bytes),
                f"rows={len(recv_bytes)} ids={sorted({int(r['id']) for r in recv_bytes})[:6]}",
            )
            checks.check(
                "packet_recv_dir_field",
                all(hit.get("dir") in ("send", "recv") for hit in recv_drained.get("hits", [])),
                "every row carries dir",
            )

            # H2: command packet injection uses a captured 77 payload template.
            inject_enabled = True
            try:
                client.op_self()
                checks.check("packet_inject_gate", True, "op_self ok")
            except ModProtocolError as exc:
                inject_enabled = False
                checks.check(
                    "packet_inject_gate",
                    "packet_inject_disabled" in str(exc),
                    str(exc),
                )
            if inject_enabled:
                client.packet_capture(True, serialize_ids=[77], sample=1)
                client.key_event(191)
                client.key_event(191, False)
                opened = wait_screen(client, "chat_screen")
                checks.check("packet_chat_open", opened == "chat_screen", opened)
                client.text_input("say harness_template")
                # The chat UI consumes HID text asynchronously after the game
                # tick; allow it to apply the text before injecting Enter.
                wait_ticks(client, 10)
                client.key_event(13)
                client.key_event(13, False)
                closed = wait_screen(client, "hud_screen")
                checks.check("packet_chat_closed", closed == "hud_screen", closed)
                if closed == "chat_screen":
                    # A failed chat submission must not block later movement
                    # tests. Keep the packet assertions below as failures.
                    client.key_event(27)
                    client.key_event(27, False)
                    wait_screen(client, "hud_screen")
                template_rows = [
                    row for row in client.packet_bytes(0, limit=64)["hits"] if int(row["id"]) == 77
                ]
                checks.check(
                    "packet_template",
                    bool(template_rows) and int(template_rows[-1]["size"]) > 0,
                    f"rows={len(template_rows)}",
                )
                position = client.state()
                # A previous run left this test's gold block behind, so checking
                # only its final type could pass even when /setblock failed.
                tx = math.floor(float(position["feet_x"]))
                ty = math.floor(float(position["feet_y"]))
                tz = math.floor(float(position["feet_z"]))
                candidates = [
                    [tx + dx, ty, tz + dz] for dx, dz in ((3, 3), (-3, 3), (3, -3), (-3, -3))
                ]
                blocks = client.blocks(candidates)["blocks"]
                target = next(
                    (
                        cell
                        for cell, block in zip(candidates, blocks, strict=True)
                        if block.get("name") == "minecraft:air"
                    ),
                    None,
                )
                checks.check(
                    "packet_inject_setblock_precondition",
                    target is not None,
                    "requires air so an unchanged block cannot pass",
                )
                before_sent = int(client.status().get("chat_sent", 0))
                if target is not None:
                    tx, ty, tz = target
                    try:
                        client.chat_send(f"/setblock {tx} {ty} {tz} minecraft:gold_block")
                        wait_ticks(client, 8)
                        placed = client.blocks([target])["blocks"][0].get("name")
                        checks.check(
                            "packet_inject_setblock", placed == "minecraft:gold_block", str(placed)
                        )
                    finally:
                        if client.blocks([target])["blocks"][0].get("name") != "minecraft:air":
                            client.command(f"setblock {tx} {ty} {tz} air")
                            wait_ticks(client, 5)
                        checks.check(
                            "packet_inject_setblock_restored",
                            client.blocks([target])["blocks"][0].get("name") == "minecraft:air",
                        )
                x0 = float(client.state()["feet_x"])
                client.chat_send("/tp @s ~1 ~ ~")
                wait_ticks(client, 8)
                dx = float(client.state()["feet_x"]) - x0
                checks.check("packet_inject_relative", abs(dx - 1.0) < 0.05, f"dx={dx:.3f}")
                checks.check(
                    "packet_inject_count",
                    int(client.status().get("chat_sent", 0)) - before_sent >= 2,
                    str(client.status().get("chat_sent")),
                )
                # Panic must close the tap even while capture is active.
                client.panic("all")
                wait_ticks(client, 2)
                checks.check(
                    "packet_panic_off",
                    client.packet_stats().get("active") is False,
                    str(client.packet_stats().get("active")),
                )

            client.packet_capture(False)
            quiet = int(client.packet_stats().get("records", 0))
            client.set_input(forward=1.0, ticks=20)
            wait_ticks(client, 25)
            checks.check(
                "packet_off_zero",
                int(client.packet_stats().get("records", 0)) == quiet,
                f"records={quiet}",
            )

        # -- T1: spool, crash isolation -------------------------------------
        checks.check("hello_spool", bool(client.hello.get("spool")), str(client.hello.get("spool")))

        # -- T7: version fingerprint and anchor signatures -----------------
        diagnostics = client.diagnose()
        checks.check(
            "version_ok",
            diagnostics.get("version_ok") is True
            and diagnostics.get("version", {}).get("prefix_ok") is True
            and bool(diagnostics.get("version", {}).get("file")),
            str(diagnostics.get("version")),
        )
        anchors = diagnostics.get("anchors", [])
        checks.check(
            "anchor_signatures",
            len(anchors) >= 3
            and all(
                anchor.get("match") is True and int(anchor.get("signature_bytes", 0)) == 24
                for anchor in anchors
            ),
            f"anchors={len(anchors)}",
        )
        if anchors:
            wrong = client.signature_check(anchors[0]["name"], "00" * 24)
            checks.check(
                "signature_mismatch",
                wrong.get("match") is False and wrong.get("readable") is True,
                str(wrong),
            )

        # -- porting: profiles are data and dumpable from the live build -----
        status = client.status()
        profile = status.get("profile", {})
        checks.check(
            "profile_selected",
            profile.get("profile_found") is True
            and profile.get("match") in ("exact", "prefix", "version")
            and int(profile.get("anchors_configured", 0)) == len(anchors)
            and int(profile.get("anchors_matched", 0)) == len(anchors),
            str(profile),
        )
        capabilities = status.get("capabilities", {})
        checks.check(
            "profile_capabilities",
            status.get("hooks_allowed") is True
            and capabilities.get("hooks") is True
            and capabilities.get("offsets") is True
            and capabilities.get("profile") is True,
            str(capabilities),
        )
        checks.check(
            "compat_default_off",
            status.get("compat") is False and status.get("version_ok") is True,
            f"compat={status.get('compat')} version_ok={status.get('version_ok')}",
        )
        checks.check(
            "hello_profile",
            client.hello.get("hooks_allowed") is True
            and client.hello.get("profile", {}).get("profile_found") is True,
            str(client.hello.get("profile")),
        )
        dump = client.profile_dump()
        saveable = dump.get("profile", {})
        checks.check(
            "profile_dump",
            dump.get("build_id") == status.get("build_id")
            and saveable.get("runtime") is True
            and saveable.get("build_id") == status.get("build_id")
            and len(saveable.get("anchors", {})) == len(anchors)
            and all(
                len(str(entry.get("expected_hex", ""))) == 48
                for entry in saveable.get("anchors", {}).values()
            )
            and "StateVector" in saveable.get("structs", {}),
            f"anchors={len(saveable.get('anchors', {}))}",
        )
        exact = client.profile_probe(str(status.get("build_id", "")))
        near = client.profile_probe("1.26.51.7")
        unknown = client.profile_probe("9.9.9.9")
        checks.check(
            "profile_probe",
            exact.get("match") == "exact"
            and exact.get("profile_found") is True
            and near.get("match") == "prefix"
            and near.get("profile_found") is True
            and unknown.get("profile_found") is False
            and unknown.get("match") == "none",
            f"exact={exact.get('match')} near={near.get('match')} unknown={unknown.get('match')}",
        )

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
            "watch_code_hex",
            bool(hits) and all(len(hit.get("code_hex", "")) == 32 for hit in hits),
            f"hits={len(hits)}",
        )
        checks.check(
            "watch_rip_semantics",
            drained.get("rip_semantics") == "next_instruction",
            str(drained.get("rip_semantics")),
        )

        checks.check(
            "watch_list",
            len(client.watch_list().get("watches", [])) == 1,
            str(client.watch_list().get("watches")),
        )
        # Per-watch stack depth: default 8, configurable up to 32. The default
        # watch above must stay at 8; a second watch asks for 20 and must be
        # echoed and honored.
        checks.check(
            "watch_depth_default",
            bool(hits) and all(len(hit.get("stack", [])) <= 8 for hit in hits),
            f"hits={len(hits)}",
        )
        deep_base = int(client.watch_list().get("head", 0))
        deep = client.watch_set(probe_addr, size=8, mode="w", tag="deep_probe", depth=20)
        checks.check(
            "watch_depth_install",
            deep.get("event") == "watch_installed" and int(deep.get("depth", 0)) == 20,
            str(deep),
        )
        wait_ticks(client, 10)
        deep_hits = client.watch_drain(deep_base).get("hits", [])
        checks.check(
            "watch_depth_stack",
            bool(deep_hits)
            and all(len(hit.get("stack", [])) <= 20 for hit in deep_hits)
            and any(len(hit.get("stack", [])) >= 9 for hit in deep_hits),
            f"hits={len(deep_hits)} depths={[len(h.get('stack', [])) for h in deep_hits][:8]}",
        )
        bad_depth = False
        try:
            client.watch_set(probe_addr, size=8, mode="w", tag="bad_depth", depth=33)
        except ModProtocolError as exc:
            bad_depth = "bad_depth" in str(exc)
        checks.check("watch_depth_bad", bad_depth, "")
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
        paths = overhead_before.get("paths", {})
        checks.check(
            "overhead_paths",
            all(isinstance(paths.get(name), dict) for name in ("record", "watch", "hook")),
            str(paths),
        )
        checks.check(
            "overhead_semantics",
            overhead_before.get("consistent") is True
            and "tick_interval_p99_us" in overhead_before
            and int(overhead_before.get("window_samples", 0)) == 1024
            and float(overhead_before.get("qpc_resolution_ns", 0)) > 0,
            str(
                {
                    k: overhead_before.get(k)
                    for k in ("consistent", "window_samples", "qpc_resolution_ns")
                }
            ),
        )
        # Accept both outcomes: the gate replies error/stress_disabled when
        # allow_selftest_stress is off (dev.py deletes harness.json on run).
        busy = client.request(
            {"cmd": "selftest_busy", "us": 1000, "ticks": 20}, ("busy_test_started", "error")
        )
        if busy.get("reason") == "stress_disabled":
            checks.check("selftest_busy_gated", True, "stress_disabled")
            overhead_after = overhead_before
        else:
            checks.check(
                "selftest_busy",
                busy.get("event") == "busy_test_started",
                str(busy),
            )
            wait_ticks(client, 30)
            overhead_after = client.overhead()
            checks.check(
                "overhead_busy",
                float(overhead_after.get("hook_p99_us", 0)) >= 900
                and float(overhead_after.get("hook_max_us", 0)) >= 900,
                str(
                    {
                        k: overhead_after.get(k)
                        for k in ("hook_p50_us", "hook_p99_us", "hook_max_us")
                    }
                ),
            )
        checks.check("overhead_alive", wait_ticks(client, 3) >= 3)

        # -- parkour jump/route primitives ---------------------------------
        check_jump_motion(client, checks, inject_enabled)

        # Measured-broken takeoff modes must stay rejected (input pipeline
        # only / setJumpPendingScale only); the mod is the authoritative guard
        # for raw protocol sends.
        for bad_mode, reason in (
            (0, "jump_impulse_disabled"),
            (2, "jump_impulse_pending_scale_only"),
        ):
            try:
                client.request({"cmd": "input_cfg", "jump_impulse": bad_mode}, "input_cfg_ok")
                checks.check(
                    f"jump_impulse_guard_{bad_mode}", False, "broken takeoff mode accepted"
                )
            except ModProtocolError as exc:
                checks.check(f"jump_impulse_guard_{bad_mode}", reason in str(exc), str(exc))
        forced = client.request(
            {"cmd": "input_cfg", "jump_impulse": 0, "force": True}, "input_cfg_ok"
        )
        checks.check(
            "jump_impulse_guard_force",
            forced.get("event") == "input_cfg_ok",
            str(forced),
        )
        restored = client.input_cfg(jump_impulse=1)
        checks.check(
            "jump_impulse_guard_restore",
            restored.get("event") == "input_cfg_ok",
            str(restored),
        )

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
            yaw=-90.0,  # +X target; yaw=0 would walk along +Z forever.
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

        # -- record medium bits: feet/head block is a liquid ----------------
        pose_now = client.state()
        fx = math.floor(float(pose_now["feet_x"]))
        fy = math.floor(float(pose_now["feet_y"]))
        fz = math.floor(float(pose_now["feet_z"]))
        client.command(f"setblock {fx} {fy} {fz} water")
        client.command(f"setblock {fx} {fy + 1} {fz} water")
        wait_ticks(client, 3)
        client.record_bin_start(period=1)
        wait_ticks(client, 8)
        client.record_bin_stop()
        fluid_entries = client.record_dump_all(format="jsonl")
        checks.check(
            "record_in_water_flag",
            any(entry.in_water for entry in fluid_entries),
            f"entries={len(fluid_entries)} water={sum(1 for e in fluid_entries if e.in_water)}",
        )
        client.command(f"setblock {fx} {fy} {fz} air")
        client.command(f"setblock {fx} {fy + 1} {fz} air")
        # Command-placed water can wake and spread once the player enters it;
        # sweep the air layers above the ground so later checks stay dry.
        client.command(f"fill {fx - 3} {fy} {fz - 3} {fx + 3} {fy + 1} {fz + 3} air")
        wait_ticks(client, 15)

        # -- swim: SWIMMING is entered with real keys, never state writes ---
        if not inject_enabled:
            checks.note("swim_drive", "skipped: allow_input_inject is disabled")
        else:
            # Absolute fixture: a 3x3, 10-deep water column inside a stone
            # shell at a fixed world position. Nothing here depends on where the
            # player happened to be, so every run starts from the same blocks.
            # Stone shell -64..-54, water interior -63..-54 (surface top -53).
            client.teleport(SWIM_X + 0.5, -58.0, SWIM_Z + 0.5)
            wait_ticks(client, 25)
            builder = Builder(client)
            pool = builder.build(
                [
                    f"fill {SWIM_X - 2} -64 {SWIM_Z - 2} {SWIM_X + 2} -54 {SWIM_Z + 2} stone",
                    f"fill {SWIM_X - 1} -63 {SWIM_Z - 1} {SWIM_X + 1} -54 {SWIM_Z + 1} water",
                ],
                {
                    (SWIM_X, -58, SWIM_Z): "minecraft:water",
                    (SWIM_X, -63, SWIM_Z): "minecraft:water",
                    (SWIM_X - 2, -58, SWIM_Z): "minecraft:stone",
                },
            )
            if not all(check.ok for check in pool):
                checks.note("swim_pool", f"readback mismatch: {pool}")

            def reset_swim_spot() -> dict:
                """Every swim sub-check starts from the identical, verified state.

                reset() clears input/tasks/velocity/effects and drops the player
                at the same point with yaw/pitch 0 inside the fixed pool.
                ``settle`` then requires four consecutive ticks of "no input,
                no horizontal motion, in water" before the step starts, so a
                leftover velocity from the previous sub-check cannot leak in.
                """
                client.command("gamemode survival @s")
                client.reset(SWIM_X + 0.5, SWIM_Y, SWIM_Z + 0.5, yaw=0.0, pitch=0.0)
                wait_ticks(client, 8)
                return settle(
                    client,
                    ticks=4,
                    on_ground=None,
                    still=2e-3,
                    include_vertical=False,
                    in_water=True,
                    sprinting=False,
                )

            swim_start = reset_swim_spot()
            checks.check(
                "swim_start_still",
                bool(swim_start.get("ok")),
                f"ticks={swim_start.get('ticks')}",
            )
            entered = swim_drive(client, timeout=1.5)
            checks.check(
                "swim_drive_entered",
                bool(entered.get("entered")) and bool(entered.get("swimming")),
                str(entered),
            )

            # Dive from the same fixed point; 20 ticks (~5 blocks) stay clear of
            # the floor in a 9-deep pool.
            reset_swim_spot()
            swim_drive(client, timeout=1.5)
            client.look(0.0, 60.0)
            client.record_bin_start(period=1)
            wait_ticks(client, 20)
            client.record_bin_stop()
            dive = client.record_dump_all(format="jsonl")
            wet_free = [entry for entry in dive if entry.in_water and not entry.on_ground]
            checks.check(
                "swim_drive_dive",
                bool(wet_free) and min(entry.vy for entry in wet_free) < -0.15,
                f"free={len(wet_free)} min_vy={min((e.vy for e in wet_free), default=0.0):.4f}",
            )

            # Holding Space lifts the player in water (+0.175 blocks/tick);
            # looking up does not (the swim gate pins vy to 0). Same fixed start.
            reset_swim_spot()
            swim_drive(client, timeout=1.5)
            client.look(0.0, 0.0)
            client.record_bin_start(period=1)
            client.key_event(32, True)
            wait_ticks(client, 16)
            client.record_bin_stop()
            rise = client.record_dump_all(format="jsonl")
            client.key_event(32, False)
            wet_rise = [entry for entry in rise if entry.in_water]
            rise_vy = [entry.vy for entry in wet_rise[-6:]]
            checks.check(
                "swim_hold_space_rises",
                bool(rise_vy) and sum(rise_vy) / len(rise_vy) > 0.05,
                f"water_rows={len(wet_rise)} tail_vy={[round(v, 3) for v in rise_vy]}",
            )
            stopped = swim_stop(client)
            checks.check("swim_stop_clears", stopped.get("swimming") is False, str(stopped))

            # Sprint must not inject a jump. Re-sending the sprint *button*
            # through the queue/handler modes used to produce a 0.42 jump arc
            # on flat ground (measured 2026-10-25), which contaminated every
            # sprint experiment; the direct MoveInput flags are enough.
            client.reset(SWIM_X + 10.5, -60.0, SWIM_Z + 0.5, yaw=0.0, pitch=0.0)
            wait_ticks(client, 8)
            sprint_pre = settle(
                client,
                ticks=5,
                on_ground=True,
                still=1e-3,
                include_vertical=True,
                in_water=False,
                sprinting=False,
            )
            client.record_bin_start(period=1)
            client.set_input(forward=1.0, sprint=True, ticks=6)
            wait_ticks(client, 8)
            client.record_bin_stop()
            client.set_input()
            sprint_rows = client.record_dump_all(format="jsonl")
            sprint_flag = any(entry.sprinting for entry in sprint_rows)
            sprint_feet = [entry.feet_y for entry in sprint_rows]
            no_jump = (
                bool(sprint_feet)
                and max(sprint_feet) - min(sprint_feet) < 0.02
                and max((entry.vy for entry in sprint_rows), default=0.0) < 0.02
            )
            checks.check(
                "sprint_no_jump",
                bool(sprint_pre.get("ok")) and sprint_flag and no_jump,
                f"pre={sprint_pre.get('ok')} sprint={sprint_flag} jump={not no_jump}",
            )

            # -- togglefly: double-space flight gate in any non-spectator mode
            gm_now = int(client.state().get("gamemode", 0))
            client.command("gamemode survival @s")
            wait_ticks(client, 8)
            enabled = client.toggle_fly(True)
            checks.check("togglefly_enable", bool(enabled.get("mayfly")), str(enabled))
            wait_ticks(client, 4)
            flown = double_space_fly(client)
            checks.check(
                "togglefly_double_space",
                bool(flown.get("flying")),
                f"{flown} gamemode=survival",
            )
            disabled = client.toggle_fly(False)
            wait_ticks(client, 8)
            after_fly = client.state()
            checks.check(
                "togglefly_disable",
                disabled.get("mayfly") is False
                and after_fly.get("mayfly") is False
                and not after_fly.get("flying"),
                str({"reply": disabled, "state": after_fly.get("mayfly")}),
            )
            # The in-game command players type in chat: /togglefly flips it.
            on_reply = client.command("togglefly")
            wait_ticks(client, 6)
            checks.check(
                "togglefly_command_on",
                bool(on_reply.get("success")) and client.state().get("mayfly") is True,
                str(on_reply),
            )
            off_reply = client.command("togglefly")
            wait_ticks(client, 6)
            checks.check(
                "togglefly_command_off",
                bool(off_reply.get("success")) and client.state().get("mayfly") is False,
                str(off_reply),
            )
            # Typed by a player: '/' opens chat, text goes through the client's
            # input field, Enter submits it as a command request to the server.
            # Both keys need to span a tick; a zero-length down/up is dropped.
            client.set_input()
            client.key_event(0xBF, True, via="hid")
            wait_ticks(client, 2)
            client.key_event(0xBF, False, via="hid")
            wait_ticks(client, 3)
            screen = client.screen().get("name")
            client.text_input("togglefly")
            wait_ticks(client, 3)
            client.key_event(0x0D, True, via="hid")
            wait_ticks(client, 2)
            client.key_event(0x0D, False, via="hid")
            wait_ticks(client, 8)
            typed_mayfly = client.state().get("mayfly")
            checks.check(
                "togglefly_chat_command",
                screen in ("chat_screen", "chat") and typed_mayfly is True,
                f"screen={screen} mayfly={typed_mayfly}",
            )
            client.command("togglefly")  # leave the gate off for the spectator check
            wait_ticks(client, 6)
            # NotCheat: the command is usable in a world with cheats switched off
            # (the client still sends the request; the server allows this flag).
            try:
                client.commands_enabled(False)
                wait_ticks(client, 6)
                client.set_input()
                client.key_event(0xBF, True, via="hid")
                wait_ticks(client, 2)
                client.key_event(0xBF, False, via="hid")
                wait_ticks(client, 3)
                client.text_input("togglefly")
                wait_ticks(client, 3)
                client.key_event(0x0D, True, via="hid")
                wait_ticks(client, 2)
                client.key_event(0x0D, False, via="hid")
                wait_ticks(client, 8)
                no_cheat_mayfly = client.state().get("mayfly")
                checks.check(
                    "togglefly_not_cheat",
                    no_cheat_mayfly is True,
                    f"commands_enabled=False mayfly={no_cheat_mayfly}",
                )
            finally:
                client.commands_enabled(True)
                wait_ticks(client, 6)
            client.command("togglefly")
            wait_ticks(client, 6)
            client.command("gamemode spectator @s")
            wait_ticks(client, 8)
            try:
                client.toggle_fly()
                checks.check("togglefly_spectator_guard", False, "accepted in spectator")
            except ModProtocolError as exc:
                checks.check(
                    "togglefly_spectator_guard",
                    "spectator_not_supported" in str(exc),
                    str(exc),
                )
            spectator_reply = client.command("togglefly")
            checks.check(
                "togglefly_command_spectator_guard",
                not bool(spectator_reply.get("success")),
                str(spectator_reply),
            )
            client.command(f"gamemode {gm_now} @s")
            wait_ticks(client, 8)
            # The swim pool lives at the fixed off-site fixture (SWIM_X/SWIM_Z)
            # and is intentionally left in place: keeping the same blocks makes
            # the next run start from the identical geometry.
            # The dive's ground contact enqueues a player_landed event; consume
            # it so the later landing check does not read this one.
            drain_deadline = time.monotonic() + 0.5
            while time.monotonic() < drain_deadline:
                try:
                    client.readline(0.1)
                except ModTimeout:
                    break
            client.reset(feet[0], feet[1], feet[2], yaw=0.0)
            wait_ticks(client, 10)

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
        # A plain teleport must leave effects alone: reset() writes the
        # clearEffects flag into the shared request block, and it used to leak
        # into every later teleport (protection vanished mid-experiment).
        client.teleport(feet[0] + 2.0, feet[1], feet[2])
        wait_ticks(client, 8)
        after_tp = client.state().get("effects", [])
        checks.check(
            "effects_survive_teleport",
            any(int(e.get("id", -1)) == 1 for e in after_tp),
            str(after_tp),
        )
        client.teleport(feet[0], feet[1], feet[2])
        wait_ticks(client, 6)
        client.command("effect @s clear")
        wait_ticks(client, 3)
        checks.check("effects_clear_no_tombstones", client.state().get("effects") == [])
        client.reset(feet[0], feet[1], feet[2], yaw=0.0, pitch=0.0)
        wait_ticks(client, 12)
        checks.check("effects_reset_no_tombstones", client.state().get("effects") == [])
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

        # -- equip: an inventory stack moves into a worn slot ---------------
        armor_before = before.get("armor", [])
        client.set_item(0, "minecraft:diamond_boots", count=1)
        client.select_slot(0)
        wait_ticks(client, 8)
        equipped = client.equip("feet")
        wait_ticks(client, 10)
        armor_after = client.inventory().get("armor", [])
        feet_item = next((entry["item"] for entry in armor_after if int(entry["slot"]) == 3), None)
        checks.check(
            "equip_feet",
            equipped.get("event") == "equip_done"
            and isinstance(feet_item, dict)
            and feet_item.get("type") == "minecraft:diamond_boots",
            f"feet={feet_item}",
        )
        checks.check("equip_cleared_source", slot_item(client.inventory(), 0) is None)
        for armor in armor_before:
            armor_slot = int(armor.get("slot", -1))
            if not 0 <= armor_slot < 4:
                continue
            item = armor.get("item")
            if item is None:
                client.clear_item(armor_slot, container="armor")
                continue
            if item:
                client.set_item(
                    armor_slot,
                    item["type"],
                    count=int(item["count"]),
                    aux=int(item.get("aux", 0)),
                    container="armor",
                )
            else:
                client.clear_item(armor_slot, container="armor")
        wait_ticks(client, 5)

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

        # -- G4: event-level macro replay ----------------------------------
        if inject_enabled:
            mod_dir = Path(str(client.hello.get("spool", ""))).parent
            rows = []
            for index in range(20):
                b = lambda value: "true" if value else "false"  # noqa: E731
                rows.append(
                    [
                        b(3 <= index <= 15),
                        "false",
                        "false",
                        "false",
                        "false",
                        "false",
                        "false",
                        "false",
                        b(5 <= index <= 7),
                        "0",
                        "0",
                    ]
                )
            macro_path = mod_dir / "macros" / "selftest_g4.json"
            macro_path.parent.mkdir(parents=True, exist_ok=True)
            macro_path.write_text(json.dumps(rows), encoding="utf-8")
            client.macro_load("selftest_g4")

            def run_macro(event_level: bool) -> tuple[tuple[float, float], int, float]:
                client.reset(0.5, -49.0, 0.5, yaw=0.0, pitch=0.0)
                # reset_done accepts the request before the server/client
                # teleport is settled; do not mistake the old still pose for it.
                wait_ticks(client, 10)
                wait_still(client)
                client.mouse_clear()
                yaw0 = float(client.state()["yaw"])
                client.macro_run(event_level=event_level)
                while True:
                    event = client.readline(5.0)
                    if event.get("event") == "macro_done":
                        break
                wait_ticks(client, 3)
                state = client.state()
                drift = abs(float(state["yaw"]) - yaw0)
                return (
                    (float(state["feet_x"]), float(state["feet_z"])),
                    int(client.mouse()["counts"][1]),
                    drift,
                )

            first, left_events, drift_a = run_macro(True)
            second, _, drift_b = run_macro(True)
            gap = math.hypot(first[0] - second[0], first[1] - second[1])
            external = drift_a > 0.1 or drift_b > 0.1
            # Repeated runs reproduce exactly, but the first tick's phase
            # occasionally shifts one input edge by a sub-tick amount
            # (measured 0.0017 blocks); a lost edge would move >= a tick of
            # motion (~0.1), and real input interference shows as external
            # camera drift, so keep the guard an order of magnitude below that.
            checks.check(
                "macro_event_deterministic",
                gap < 5e-3 or external,
                f"gap={gap:.6f}" + (" (skipped: real mouse moved the camera)" if external else ""),
            )
            checks.check("macro_event_clicks", left_events == 2, f"left={left_events}")
            _, legacy_left, _ = run_macro(False)
            checks.check("macro_legacy_path", legacy_left == 0, f"left={legacy_left}")

        # -- review hardening guards ---------------------------------------
        # Each check pins one fix from the code-review round; see mod/README.md.
        reply = client.request({"cmd": "look", "yaw": 1e30, "relative": False}, "error")
        checks.check(
            "guard_look_out_of_range_rejected",
            reply.get("reason") == "bad_param",
            str(reply.get("reason")),
        )
        try:
            client.ecs_context(0x41410000, 1)
            checks.check("guard_ecs_context_bad_addr", False, "no error raised")
        except ModProtocolError as exc:
            checks.check("guard_ecs_context_bad_addr", "bad_addr" in str(exc), str(exc))
        context_addr = client.state().get("context")
        if context_addr:
            try:
                client.watch_set(int(context_addr), size=1, mode="w", thread="all")
                checks.check("guard_watch_thread_all_rejected", False, "no error raised")
            except ModProtocolError as exc:
                checks.check(
                    "guard_watch_thread_all_rejected",
                    "thread_all_unsupported" in str(exc),
                    str(exc),
                )
        else:
            checks.note("guard_watch_thread_all_rejected", "state has no context address")
        if inject_enabled and macro_path.exists():
            client.macro_run(loop=True)
            loaded = client.macro_load("selftest_g4")
            status = client.macro_status()
            checks.check(
                "guard_macro_load_stops_playback",
                bool(loaded.get("stopped")) and not bool(status.get("playing")),
                f"stopped={loaded.get('stopped')} playing={status.get('playing')}",
            )

        checks.check("macro_status", client.macro_status().get("event") == "macro_status")
        return checks.finish()
    except ModPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        with contextlib.suppress(ModPipeError, ModProtocolError):
            client.input_lock(False)
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
