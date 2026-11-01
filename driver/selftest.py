"""Probe smoke tests. The default live suite requires the game in a world.

    py -3.11 driver/selftest.py
    py -3.11 driver/selftest.py --keep          # keep the test entity
    py -3.11 driver/selftest.py --entity minecraft:cow
    py -3.11 driver/selftest.py --client-io-only  # offline; never connects to the game
    py -3.11 driver/selftest.py --mod-core-only   # offline native mod checks
    py -3.11 driver/selftest.py --mod-core-live-only  # pipe/recording; no RE commands
    py -3.11 driver/selftest.py --use-lifecycle-only  # bounded server failure/recovery checks
    py -3.11 driver/selftest.py --query-capture-only  # snapshot faults and scan budget
    py -3.11 driver/selftest.py --macro-limit-only  # bounded recording and retained rows
    py -3.11 driver/selftest.py --macro-capture-only  # sampling faults, partial save and retry
    py -3.11 driver/selftest.py --macro-capture-world "超平坦测试"  # also leave/rejoin recording lifecycle
    py -3.11 driver/selftest.py --tick-safety-only  # guarded hook calls and coherent checkpoint poses
    py -3.11 driver/selftest.py --tick-safety-world "超平坦测试"  # plus menu cancellation/pose invalidation
    py -3.11 driver/selftest.py --binary-capture-only  # binary recording failures and retained rows
    py -3.11 driver/selftest.py --binary-capture-world "超平坦测试"  # plus recording across leave/rejoin
    py -3.11 driver/selftest.py --teleport-only  # owned server teleport faults and side effects
    py -3.11 driver/selftest.py --packet-injection-only  # owned command sends and template faults
    py -3.11 driver/selftest.py --packet-tap-only  # isolate packet observation faults, no injected packets
    py -3.11 driver/selftest.py --packet-injection-world "超平坦测试"  # plus reconnect/leave/rejoin
    py -3.11 driver/selftest.py --tick-recovery-only  # tick errors, fail-stop and fresh requests
    py -3.11 driver/selftest.py --tick-recovery-world "超平坦测试"  # plus reconnect/leave/rejoin
    py -3.11 driver/selftest.py --teleport-world "超平坦测试"  # plus reconnect/leave/rejoin
    py -3.11 driver/selftest.py --ecs-capture-only  # bounded ECS snapshots and stage failures
    py -3.11 driver/selftest.py --ecs-capture-world "超平坦测试"  # plus reconnect and leave/rejoin
    py -3.11 driver/selftest.py --macro-playback-only  # guarded replay, release and frame ownership
    py -3.11 driver/selftest.py --macro-playback-world "超平坦测试"  # plus cancellation on world exit
    py -3.11 driver/selftest.py --query-lifecycle-world "超平坦测试"  # reconnect/leave/rejoin
    py -3.11 driver/selftest.py --entry-lifecycle-world "超平坦测试"  # startup watchdog/recovery
    py -3.11 driver/selftest.py --overhead-only  # read concurrently across a full sample-ring wrap
    py -3.11 driver/selftest.py --tick-stream-only  # JSON recording order/faults and parkour events
    py -3.11 driver/selftest.py --tick-stream-world "超平坦测试"  # plus leave/rejoin recording lifecycle

Covers: pipe, state, inventory read/write/give/clear, teleport, server command,
entity query, look-at, attack and interact. The original slot-0 item and the
selected slot are restored at the end.

The default entity is a pig: hostile mobs despawn immediately if the test world
is on Peaceful difficulty.
"""

from __future__ import annotations

import argparse
import contextlib
import gc
import itertools
import json
import math
import os
import subprocess
import sys
import time
import weakref
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.harness.hooks import decode_hook_hits  # noqa: E402
from driver.harness.lab import (  # noqa: E402
    Builder,
    capture_disconnect_report,
    double_space_fly,
    inbound_slices,
    record_session,
    settle,
    swim_drive,
    swim_stop,
    world_state,
)
from driver.harness.memory import fnv1_64, fnv1a_64  # noqa: E402
from driver.harness.record import RecordEntry, decode_dump  # noqa: E402
from driver.harness.spool import read_spool  # noqa: E402
from driver.parkour import read_until_run_end  # noqa: E402
from driver.selftest_support import Checks, ViolationObservation, preserve_failure  # noqa: E402
from driver.transport_mod import ModClient, ModPipeError, ModProtocolError, ModTimeout  # noqa: E402

# Fixed swim fixture: 3x3x10 water column inside a stone shell at (900, 900).
SWIM_X, SWIM_Y, SWIM_Z = 900, -55.5, 900


class WorldLost(ModPipeError):
    """The level disappeared mid-check.

    Everything a check would measure afterwards is derived from that loss, so
    the run stops instead of letting the next check overwrite the first failure
    (historically a ``feet_x`` KeyError hid the original disconnect).
    """


def require_world(client: ModClient, where: str) -> None:
    """Raise :class:`WorldLost` unless the client is still inside the level."""
    kind = world_state(client)
    if kind != "world":
        raise WorldLost(f"{where}: world state is {kind}")


def dump_packet_tap(client: ModClient, path: str | None) -> None:
    """Write the packet-identity ring to ``path`` (diagnostics; never fatal)."""
    if not path:
        return
    try:
        rows = client.packet_drain(0, 8192)
        Path(path).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    except (ModPipeError, OSError) as exc:
        print(f"NOTE  packet_tap_dump  (failed: {exc})")
        return
    print(f"NOTE  packet_tap_dump  ({rows.get('count', 0)} rows -> {path})")


def wait_still(client: ModClient, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.state()
        if state.get("in_world") is not True:
            raise WorldLost("wait_still: player left the world")
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
    status = client.status()
    if status.get("in_world") is not True:
        raise WorldLost("wait_ticks: player is not in a world")
    start = int(status.get("tick", 0))
    deadline = time.monotonic() + timeout
    while True:
        status = client.status()
        if status.get("in_world") is not True:
            raise WorldLost(f"wait_ticks: world lost after tick {start}")
        current = int(status.get("tick", 0))
        if current - start >= ticks:
            return current - start
        if time.monotonic() > deadline:
            return current - start
        time.sleep(0.01)


def wait_rotation(client: ModClient, yaw: float, pitch: float, timeout: float = 3.0) -> None:
    """Require the requested camera pose on three distinct consecutive ticks.

    A server teleport receipt precedes client synchronization. Starting the
    next teleport before this gate can preserve the old server rotation.
    """
    deadline = time.monotonic() + timeout
    last_tick: int | None = None
    streak = 0
    while True:
        state = client.state()
        if state.get("in_world") is not True:
            raise WorldLost("wait_rotation: player left the world")
        tick = int(state["tick"])
        if tick != last_tick:
            matches = (
                abs((float(state["yaw"]) - yaw + 180) % 360 - 180) < 0.1
                and abs(float(state["pitch"]) - pitch) < 0.1
            )
            consecutive = last_tick is not None and tick == last_tick + 1
            streak = (streak + 1 if consecutive else 1) if matches else 0
            last_tick = tick
            if streak >= 3:
                return
        if time.monotonic() >= deadline:
            raise ModTimeout(
                f"rotation did not settle: yaw={state.get('yaw')} want={yaw}, "
                f"pitch={state.get('pitch')} want={pitch}, matching_ticks={streak}/3"
            )
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


def prepare_swim_fixture(client: ModClient, checks: Checks, name: str) -> bool:
    # Load the fixture's chunks before the deferred block commands. The
    # player starts above the shell while it is built, then enters its water.
    client.teleport(SWIM_X + 0.5, -51, SWIM_Z + 0.5)
    wait_ticks(client, 3)
    pool = Builder(client).build(
        [
            f"fill {SWIM_X - 2} -64 {SWIM_Z - 2} {SWIM_X + 2} -54 {SWIM_Z + 2} stone",
            f"fill {SWIM_X - 1} -63 {SWIM_Z - 1} {SWIM_X + 1} -54 {SWIM_Z + 1} water",
        ],
        {
            (SWIM_X, -58, SWIM_Z): "minecraft:water",
            (SWIM_X, -63, SWIM_Z): "minecraft:water",
            (SWIM_X - 2, -58, SWIM_Z): "minecraft:stone",
            (SWIM_X + 2, -58, SWIM_Z): "minecraft:stone",
        },
    )
    return checks.check(name, bool(pool) and all(row.ok for row in pool), str(pool))


def check_block_identity(client: ModClient, checks: Checks) -> None:
    """Sample stone/air/water without creating uncontained sources near the player."""
    original = client.state()
    require_world(client, "block identity")
    try:
        if not prepare_swim_fixture(client, checks, "h0_water_fixture"):
            return
        samples = [
            [SWIM_X - 2, -58, SWIM_Z],
            [SWIM_X, -52, SWIM_Z],
            [SWIM_X, -58, SWIM_Z],
        ]
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
    finally:
        with preserve_failure(checks, "block_identity"):
            client.teleport(
                *(float(original[f"feet_{axis}"]) for axis in "xyz"),
                yaw=float(original["yaw"]),
                pitch=float(original["pitch"]),
            )
            wait_ticks(client, 12)


def check_record_medium(client: ModClient, checks: Checks) -> None:
    """Read medium flags in the bounded swim fixture, away from later checks."""
    original = client.state()
    require_world(client, "record medium")
    position = [float(original[f"feet_{axis}"]) for axis in "xyz"]
    recording = False
    try:
        client.stop_move()
        if not prepare_swim_fixture(client, checks, "record_water_fixture"):
            return
        client.teleport(SWIM_X + 0.5, -58, SWIM_Z + 0.5)
        ready = settle(
            client, on_ground=None, still=None, in_water=True, ticks=2, clear_input=False
        )
        if not checks.check("record_water_ready", ready["ok"], str(ready["state"])):
            return
        client.record_bin_start(period=1)
        recording = True
        wait_ticks(client, 8)
        client.record_bin_stop()
        recording = False
        entries = client.record_dump_all(format="jsonl")
        checks.check(
            "record_in_water_flag",
            any(entry.in_water for entry in entries),
            f"entries={len(entries)} water={sum(entry.in_water for entry in entries)}",
        )
    finally:
        with preserve_failure(checks, "record_medium"):
            try:
                if recording:
                    client.record_bin_stop()
            finally:
                with preserve_failure(checks, "record_medium_pose"):
                    client.teleport(
                        *position, yaw=float(original["yaw"]), pitch=float(original["pitch"])
                    )
                    wait_ticks(client, 12)
                    restored = settle(
                        client,
                        on_ground=bool(original["on_ground"]),
                        in_water=bool(original["in_water"]),
                        clear_input=False,
                    )
                    checks.check("record_medium_restored", restored["ok"], str(restored["state"]))


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
    was_locked = bool(client.state().get("input", {}).get("locked"))
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
        client.input_lock(was_locked)


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


def check_query_capture(client: ModClient, checks: Checks) -> None:
    """Exercise the real fixed snapshot/SEH/consumer path, including bounded scans."""
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    for target in ("state", "buttons", "inventory", "blocks", "scan"):
        for mode, reason in (
            ("seh", f"{target}_fault"),
            ("cpp", "capture_failed"),
            ("timeout", "capture_timeout"),
        ):
            result = client.request(
                {"cmd": "selftest_query_failure", "target": target, "mode": mode},
                (target, "error"),
            )
            if result.get("reason") == "selftest_stress_disabled":
                checks.skip("query_fault_recovery", "allow_selftest_stress is disabled")
                return
            checks.check(
                f"query_{target}_{mode}_terminal",
                result.get("event") == "error"
                and result.get("reason") == reason
                and result.get("phase") == target,
                str(result),
            )
            duplicate = None
            with contextlib.suppress(ModTimeout):
                duplicate = client.wait_event((target, "error"), 0.03)
            checks.check(f"query_{target}_{mode}_unique", duplicate is None, str(duplicate or ""))
            if target == "state":
                recovered = client.state()
            elif target == "buttons":
                recovered = client.request({"cmd": "buttons"}, "buttons")
            elif target == "inventory":
                recovered = client.inventory()
            elif target == "blocks":
                recovered = client.blocks([])
            else:
                recovered = client.scan([0, -60, 0], [0, -60, 0], include_air=True)
            checks.check(
                f"query_{target}_{mode}_recovery",
                recovered.get("event") == target and wait_ticks(client, 2) >= 2,
            )
    partial = client.request(
        {"cmd": "selftest_query_failure", "target": "state", "mode": "partial"}, "state"
    )
    checks.check(
        "query_partial_state",
        partial.get("partial") is True
        and partial.get("warnings") == ["state_blocks_fault"]
        and "block_feet" not in partial
        and "block_below" in partial,
    )
    checks.check("query_partial_recovers", client.state().get("partial") is False)

    # Reserve the inventory slot long enough to verify explicit backpressure.
    with client.exchange():
        client.send({"cmd": "selftest_query_failure", "target": "inventory", "mode": "timeout"})
        client.send({"cmd": "inventory"})
        busy = client.wait_event("error", 3)
        terminal = client.wait_event("error", 3)
    checks.check(
        "query_inventory_busy",
        busy.get("reason") == "inventory_busy"
        and terminal.get("reason") == "capture_timeout"
        and terminal.get("phase") == "inventory"
        and client.inventory().get("event") == "inventory",
    )

    # 32768 air cells with limit=1 still require all 64 bounded batches.
    pose = client.state()
    x, y, z = [math.floor(float(pose[f"feet_{axis}"])) for axis in "xyz"]
    low, high = [x, y + 8, z], [x + 31, y + 39, z + 31]
    scan = client.scan(high, low, include_air=False, limit=1)
    checks.check(
        "query_scan_budget",
        scan.get("visited") == 32768
        and scan.get("batches") == 64
        and scan.get("batch_limit") == 512
        and int(scan.get("last_tick", 0)) - int(scan.get("first_tick", 0)) >= 63
        and scan.get("from") == low
        and scan.get("to") == high,
        f"visited={scan.get('visited')} batches={scan.get('batches')} max_batch_us={scan.get('max_batch_us')}",
    )
    limited = client.scan(low, [x + 2, y + 8, z], include_air=True, limit=2)
    checks.check(
        "query_scan_limit",
        limited.get("count") == 2
        and limited.get("truncated") is True
        and limited.get("truncated_reason") == "result_limit"
        and [row["x"] for row in limited["blocks"]] == [x, x + 1],
    )
    # An occupied snapshot slot must reject a second request explicitly.
    with client.exchange():
        client.send({"cmd": "scan", "from": low, "to": high, "limit": 1})
        client.send({"cmd": "scan", "from": low, "to": low})
        rejected = client.wait_event("error", 3)
        completed = client.wait_event("scan", 10)
    checks.check(
        "query_scan_busy",
        rejected.get("reason") == "scan_busy" and completed.get("visited") == 32768,
    )
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("query_handled_fault_no_dump", after == before)
    check_entity_slot_capture(client, checks)


def check_entity_slot_capture(client: ModClient, checks: Checks) -> None:
    """Exercise all three entity stages and tick-owned slot receipts."""
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    inventory = client.inventory()
    selected = int(inventory["selected_slot"])
    empty_slot = next(
        (int(row["slot"]) for row in inventory["slots"][:9] if row["item"] is None), None
    )
    wrote_item = False
    self_id = int(client.state()["runtime_id"])
    with contextlib.suppress(ModTimeout):
        while True:
            client.wait_event("select_slot_done", 0.02)
    try:
        for target, stage in (
            ("select_slot", "client"),
            ("entities", "client"),
            ("entities", "server"),
            ("entities", "aim"),
        ):
            event = "select_slot_done" if target == "select_slot" else "entities"
            for mode, reason in (
                ("seh", f"{target}_fault"),
                ("cpp", "capture_failed"),
                ("timeout", "capture_timeout"),
            ):
                result = client.request(
                    {
                        "cmd": "selftest_query_failure",
                        "target": target,
                        "stage": stage,
                        "runtime_id": self_id,
                        "mode": mode,
                    },
                    (event, "error"),
                )
                if result.get("reason") == "selftest_stress_disabled":
                    checks.skip("entity_slot_fault_recovery", "allow_selftest_stress is disabled")
                    return
                label = f"query_{target}_{stage}_{mode}"
                checks.check(
                    f"{label}_terminal",
                    result.get("event") == "error"
                    and result.get("reason") == reason
                    and result.get("phase") == target,
                    str(result),
                )
                duplicate = None
                with contextlib.suppress(ModTimeout):
                    duplicate = client.wait_event((event, "error"), 0.05)
                checks.check(f"{label}_unique", duplicate is None, str(duplicate or ""))
                if target == "select_slot":
                    client.select_slot(selected)
                    recovered = client.wait_event("select_slot_done", 3)
                    ok = recovered.get("ok") is True and recovered.get("applied") == selected
                else:
                    recovered = client.entity(self_id)
                    ok = recovered.get("runtime_id") == self_id
                checks.check(f"{label}_recovery", ok and wait_ticks(client, 2) >= 2)

        for target in ("select_slot", "entities"):
            with client.exchange():
                client.send({"cmd": "selftest_query_failure", "target": target, "mode": "timeout"})
                client.send({"cmd": target})
                busy = client.wait_event("error", 3)
                terminal = client.wait_event("error", 3)
            checks.check(
                f"query_{target}_busy",
                busy.get("reason") == f"{target}_busy"
                and terminal.get("reason") == "capture_timeout"
                and terminal.get("phase") == target,
            )

        # Preserve the old ack/completion contract and clamp/readback behavior.
        for requested in (-1, 0, 4, 8, 9):
            expected = min(8, max(0, requested))
            ack = client.select_slot(requested)
            done = client.wait_event("select_slot_done", 3)
            current = client.inventory()
            item = current["slots"][expected]["item"]
            expected_item = None if item is None else {"type": item["type"], "count": item["count"]}
            checks.check(
                f"select_slot_receipt_{requested}",
                ack.get("event") == "select_slot_ok"
                and done.get("slot") == expected
                and done.get("applied") == expected
                and done.get("ok") is True
                and done.get("item") == expected_item
                and current.get("selected_slot") == expected,
                str(done),
            )
        rejected = client.request({"cmd": "entities", "radius": -1}, "error")
        checks.check("entities_reject_radius", rejected.get("reason") == "bad_radius")
        if checks.check("select_slot_item_fixture", empty_slot is not None):
            assert empty_slot is not None
            wrote_item = True
            client.set_item(empty_slot, "minecraft:stone", count=3)
            client.select_slot(empty_slot)
            done = client.wait_event("select_slot_done", 3)
            checks.check(
                "select_slot_item_receipt",
                done.get("ok") is True
                and done.get("item") == {"type": "minecraft:stone", "count": 3},
                str(done),
            )
    finally:
        if wrote_item and empty_slot is not None:
            client.clear_item(empty_slot)
        client.select_slot(selected)
        client.wait_event("select_slot_done", 3)
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("entity_slot_handled_fault_no_dump", after == before)


def check_macro_record_limits(client: ModClient, checks: Checks) -> None:
    """Verify effective bounds and retained data without recording for hours."""
    mod_dir = Path(str(client.status()["spool"])).parent
    prefix = f"selftest_record_limit_{time.monotonic_ns()}"
    paths: set[Path] = set()

    def save(suffix: str) -> dict[str, Any]:
        name = f"{prefix}_{suffix}"
        paths.add(mod_dir / "macros" / f"{name}.json")
        return client.macro_record_stop(name)

    try:
        for label, requested in (
            ("omitted", None),
            ("zero", 0),
            ("boundary", 200000),
            ("above", 200001),
            ("uint64", (1 << 64) - 1),
        ):
            message: dict[str, Any] = {"cmd": "macro_rec_start"}
            if requested is not None:
                message["max_ticks"] = requested
            started = client.request(message, "macro_rec_started")
            status = client.macro_status()
            checks.check(
                f"macro_bound_{label}",
                started.get("max_ticks") == 200000
                and started.get("hard_limit") == 200000
                and status.get("record_limit") == 200000
                and status.get("recording") is True,
            )
            wait_ticks(client, 2)
            saved = save(label)
            checks.check(
                f"macro_bound_{label}_save",
                0 < int(saved.get("ticks", 0)) < 200000
                and client.macro_status().get("recorded_ticks") == 0,
            )

        client.macro_record_start(3)
        client.wait_event("macro_rec_limit", 3)
        wait_ticks(client, 2)
        status = client.macro_status()
        checks.check(
            "macro_bound_terminal",
            status.get("recording") is False
            and status.get("recorded_ticks") == 3
            and status.get("record_limit") == 3,
        )
        duplicate = None
        with contextlib.suppress(ModTimeout):
            duplicate = client.wait_event("macro_rec_limit", 0.1)
        checks.check("macro_bound_unique", duplicate is None)
        for label, invalid in (("negative", -1), ("string", "bad"), ("fraction", 1.5)):
            rejected = client.request({"cmd": "macro_rec_start", "max_ticks": invalid}, "error")
            status = client.macro_status()
            checks.check(
                f"macro_bound_reject_{label}",
                rejected.get("reason") == "bad_param"
                and rejected.get("field") == "max_ticks"
                and status.get("recorded_ticks") == 3
                and status.get("recording") is False,
            )
        saved = save("terminal")
        checks.check(
            "macro_bound_terminal_save",
            saved.get("ticks") == 3 and client.macro_status().get("recorded_ticks") == 0,
        )
        repeated = client.request({"cmd": "macro_rec_stop"}, "error")
        checks.check("macro_bound_repeated_stop", repeated.get("reason") == "no_recorded_ticks")
    finally:
        with contextlib.suppress(ModPipeError):
            if client.macro_status().get("recording"):
                save("cleanup")
        for path in paths:
            path.unlink(missing_ok=True)


def check_macro_capture(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Check production sampling ownership, retained partial data and cold saving."""
    original = client.state()
    if not checks.check("macro_capture_in_world", original.get("in_world") is True):
        return
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    prefix = f"selftest_macro_capture_{time.monotonic_ns()}"
    paths: set[Path] = set()

    def name_for(suffix: str) -> str:
        name = f"{prefix}_{suffix}"
        paths.add(directory / "macros" / f"{name}.json")
        return name

    def save_raw(suffix: str) -> tuple[dict[str, Any], list[Any]]:
        name = name_for(suffix)
        reply = client.request({"cmd": "macro_rec_stop", "name": name}, "macro_rec_saved")
        # The protocol path may be relative to the game's working directory.
        data = json.loads((directory / "macros" / f"{name}.json").read_text(encoding="utf-8"))
        return reply, data

    def unique(record_id: int) -> bool:
        with contextlib.suppress(ModTimeout):
            client.wait_event(
                ("macro_rec_limit", "macro_rec_stopped"),
                0.15,
                predicate=lambda row: row.get("record_id") == record_id,
            )
            return False
        return True

    try:
        for mode, reason in (("seh", "capture_fault"), ("cpp", "capture_failed")):
            started = client.macro_record_start()
            wait_ticks(client, 4)
            armed = client.request(
                {"cmd": "selftest_macro_capture", "mode": mode},
                ("macro_capture_test_armed", "error"),
            )
            if not checks.check(
                "macro_capture_fault_gate", armed.get("event") != "error", str(armed)
            ):
                return
            done = client.wait_event(
                "macro_rec_stopped",
                3,
                predicate=lambda row, record_id=started["record_id"]: (
                    row.get("record_id") == record_id
                ),
            )
            status = client.macro_status()
            checks.check(
                f"macro_capture_{mode}_terminal",
                done.get("ok") is False
                and done.get("reason") == reason
                and int(done.get("ticks", 0)) >= 4
                and status.get("recording") is False
                and status.get("recorded_ticks") == done.get("ticks"),
                str(done),
            )
            checks.check(f"macro_capture_{mode}_unique", unique(started["record_id"]))
            saved, rows = save_raw(mode)
            checks.check(
                f"macro_capture_{mode}_partial_save",
                saved.get("ok") is False
                and saved.get("partial") is True
                and saved.get("reason") == reason
                and len(rows) == done["ticks"]
                and saved.get("record_id") == started["record_id"]
                and len(rows[0]) == 11
                and rows[0][9:] == ["0", "0"],
                str(saved),
            )
            checks.check(
                f"macro_capture_{mode}_clear", client.macro_status().get("recorded_ticks") == 0
            )
            started = client.macro_record_start(3)
            client.wait_event(
                "macro_rec_limit",
                3,
                predicate=lambda row, record_id=started["record_id"]: (
                    row.get("record_id") == record_id
                ),
            )
            saved, rows = save_raw(f"{mode}_recovery")
            checks.check(
                f"macro_capture_{mode}_recovery",
                saved.get("ok") is True
                and len(rows) == 3
                and rows[0][9:] == ["0", "0"]
                and bool(client.ping()),
            )

        # An actual target-open failure must preserve the stopped session for retry.
        started = client.macro_record_start(3)
        client.wait_event(
            "macro_rec_limit", 3, predicate=lambda row: row.get("record_id") == started["record_id"]
        )
        blocked_name = name_for("blocked")
        blocked = directory / "macros" / f"{blocked_name}.json"
        blocked.mkdir()
        try:
            failed = client.request({"cmd": "macro_rec_stop", "name": blocked_name}, "error")
            status = client.macro_status()
            checks.check(
                "macro_capture_save_failure_retains",
                "cannot write" in str(failed.get("reason"))
                and status.get("recorded_ticks") == 3
                and status.get("recording") is False,
                str(failed),
            )
        finally:
            blocked.rmdir()
        saved, rows = save_raw("retry")
        checks.check(
            "macro_capture_save_retry",
            saved.get("ok") is True
            and len(rows) == 3
            and saved.get("record_id") == started["record_id"],
        )

        for index in range(4):
            started = client.macro_record_start()
            wait_ticks(client, 2)
            saved, rows = save_raw(f"rapid_{index}")
            checks.check(
                f"macro_capture_stop_snapshot_{index}",
                saved.get("ok") is True
                and len(rows) >= 2
                and len(rows) == saved["ticks"]
                and rows[0][9:] == ["0", "0"]
                and saved.get("record_id") == started["record_id"],
            )
            wait_ticks(client, 2)
            checks.check(
                f"macro_capture_no_late_frame_{index}",
                client.macro_status().get("recorded_ticks") == 0,
            )

        if world:
            started = client.macro_record_start()
            wait_ticks(client, 4)
            try:
                client.leave_world()
                client.wait_event("world_left", 45)
                done = client.wait_event(
                    "macro_rec_stopped",
                    5,
                    predicate=lambda row: row.get("record_id") == started["record_id"],
                )
                checks.check(
                    "macro_capture_world_cancel",
                    done.get("ok") is False
                    and done.get("reason") == "world_left"
                    and int(done.get("ticks", 0)) >= 4,
                    str(done),
                )
                deadline = time.monotonic() + 45
                while not client.worlds().get("ready"):
                    if time.monotonic() >= deadline:
                        raise ModTimeout("macro capture: menu did not become ready")
                    time.sleep(0.1)
                refused = client.request({"cmd": "macro_rec_start"}, "error")
                checks.check("macro_capture_menu_rejected", refused.get("reason") == "not_in_world")
                saved, rows = save_raw("world_partial")
                checks.check(
                    "macro_capture_menu_save_partial",
                    saved.get("ok") is False
                    and saved.get("reason") == "world_left"
                    and len(rows) == done["ticks"],
                )
            finally:
                client.enter_world_ready(world)
                client.wait_in_world(timeout=60)
                client.teleport(
                    *(float(original[f"feet_{a}"]) for a in "xyz"),
                    yaw=float(original["yaw"]),
                    pitch=float(original["pitch"]),
                )
                checks.check("macro_capture_world_still", settle(client)["ok"])
            started = client.macro_record_start(3)
            client.wait_event(
                "macro_rec_limit",
                3,
                predicate=lambda row: row.get("record_id") == started["record_id"],
            )
            saved, rows = save_raw("world_recovery")
            checks.check("macro_capture_world_recovery", saved.get("ok") is True and len(rows) == 3)
        after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
        checks.check("macro_capture_handled_fault_no_dump", after == before)
    finally:
        with contextlib.suppress(ModPipeError):
            if client.macro_status().get("recording"):
                save_raw("cleanup")
        for path in paths:
            path.unlink(missing_ok=True)


def check_tick_safety(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Exercise the actual hook request and checkpoint pose capture boundaries."""
    original = client.state()
    if not checks.check("tick_safety_in_world", original.get("in_world") is True):
        return
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    name = f"selftest_pose_{time.monotonic_ns()}"
    symbol = "MobJump::setJumpPendingScale"

    def arm_pose(mode: str) -> dict[str, Any]:
        return client.request(
            {"cmd": "selftest_pose_capture", "mode": mode}, ("pose_capture_test_armed", "error")
        )

    def save_pose() -> dict[str, Any]:
        return client.request(
            {"cmd": "checkpoint_save", "name": name}, ("checkpoint_saved", "error")
        )

    def unique_hook() -> bool:
        with contextlib.suppress(ModTimeout):
            client.wait_event(
                ("hook_call_done", "error"),
                0.15,
                predicate=lambda row: (
                    row.get("event") == "hook_call_done" or row.get("phase") == "hook_call"
                ),
            )
            return False
        return True

    try:
        for mode, reason in (
            ("seh", "capture_fault"),
            ("cpp", "capture_failed"),
            ("timeout", "capture_timeout"),
        ):
            result = client.request(
                {"cmd": "selftest_hook_call", "symbol": symbol, "mode": mode},
                ("hook_call_done", "error"),
                timeout=5,
            )
            if result.get("reason") == "selftest_stress_disabled":
                checks.check("tick_safety_fault_gate", False, str(result))
                return
            checks.check(
                f"hook_call_{mode}_terminal",
                result.get("phase") == "hook_call" and result.get("reason") == reason,
                str(result),
            )
            checks.check(f"hook_call_{mode}_unique", unique_hook())
            recovered = client.hook_call(symbol, 0.0)
            checks.check(
                f"hook_call_{mode}_recovery",
                recovered.get("ok") is True
                and recovered.get("scale") == 0.0
                and bool(client.ping()),
            )

        with client.exchange():
            client.send({"cmd": "selftest_hook_call", "symbol": symbol, "mode": "timeout"})
            busy = client.request({"cmd": "hook_call", "symbol": symbol, "scale": 0.5}, "error")
            checks.check(
                "hook_call_overlap_rejected", busy.get("reason") == "hook_call_busy", str(busy)
            )
            checks.check(
                "hook_call_overlap_original_preserved",
                client.wait_event("error", 5).get("reason") == "capture_timeout",
            )
        invalid = client.request({"cmd": "hook_call", "symbol": symbol, "scale": 1e100}, "error")
        checks.check("hook_call_non_finite_rejected", invalid.get("reason") == "bad_param")
        checks.check("hook_call_invalid_recovery", client.hook_call(symbol, 0.0).get("ok") is True)

        wait_ticks(client, 2)
        initial = save_pose()
        checks.check(
            "pose_snapshot_present",
            initial.get("event") == "checkpoint_saved" and int(initial.get("tick", 0)) > 0,
            str(initial),
        )
        for mode in ("seh", "cpp"):
            while True:
                try:
                    client.wait_event("pose_diagnostic", 0)
                except ModTimeout:
                    break
            armed = arm_pose(mode)
            if not checks.check(
                f"pose_{mode}_armed", armed.get("event") == "pose_capture_test_armed", str(armed)
            ):
                return
            try:
                wait_ticks(client, 3)
                rejected = save_pose()
                checks.check(
                    f"pose_{mode}_invalidated", rejected.get("reason") == "no_pose", str(rejected)
                )
                diagnostic = client.wait_event("pose_diagnostic", 3)
                checks.check(
                    f"pose_{mode}_diagnostic",
                    int(diagnostic.get("faults" if mode == "seh" else "exceptions", 0)) > 0,
                    str(diagnostic),
                )
            finally:
                arm_pose("off")
                wait_ticks(client, 3)
            recovered_pose = save_pose()
            checks.check(
                f"pose_{mode}_recovery",
                recovered_pose.get("event") == "checkpoint_saved"
                and int(recovered_pose.get("tick", 0)) > int(initial.get("tick", 0)),
            )

        if world:
            client.send({"cmd": "selftest_hook_call", "symbol": symbol, "mode": "timeout"})
            client.ping()  # Submit before the real disconnect.
            client.reconnect()
            for _ in range(int(client.hello.get("replay_rows", 0))):
                client.readline(timeout=5)
            client.input_lock(True)
            checks.check(
                "hook_call_reconnect_recovery", client.hook_call(symbol, 0.0).get("ok") is True
            )
            checks.check("hook_call_reconnect_no_stale", unique_hook())
            client.send({"cmd": "selftest_hook_call", "symbol": symbol, "mode": "timeout"})
            try:
                client.leave_world()
                client.wait_event("world_left", 45)
                cancelled = client.wait_event(
                    "error", 5, predicate=lambda row: row.get("phase") == "hook_call"
                )
                checks.check(
                    "hook_call_world_cancel",
                    cancelled.get("reason") == "world_left",
                    str(cancelled),
                )
                deadline = time.monotonic() + 45
                while not client.worlds().get("ready"):
                    if time.monotonic() >= deadline:
                        raise ModTimeout("tick safety: menu did not become ready")
                    time.sleep(0.1)
                rejected = client.request({"cmd": "hook_call", "symbol": symbol}, "error")
                checks.check("hook_call_menu_rejected", rejected.get("reason") == "not_in_world")
                checks.check("pose_menu_invalidated", save_pose().get("reason") == "no_pose")
            finally:
                client.enter_world_ready(world)
                client.wait_in_world(timeout=60)
                client.teleport(
                    *(float(original[f"feet_{a}"]) for a in "xyz"),
                    yaw=float(original["yaw"]),
                    pitch=float(original["pitch"]),
                )
                checks.check("tick_safety_world_still", settle(client)["ok"])
            rejoined = save_pose()
            checks.check(
                "pose_new_world_epoch",
                rejoined.get("event") == "checkpoint_saved"
                and int(rejoined.get("pose_epoch", 0)) > int(initial.get("pose_epoch", 0)),
                str(rejoined),
            )
            checks.check(
                "hook_call_world_recovery", client.hook_call(symbol, 0.0).get("ok") is True
            )
        after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
        checks.check("tick_safety_handled_fault_no_dump", after == before)
    finally:
        with contextlib.suppress(ModPipeError):
            arm_pose("off")
            client.checkpoint_delete(name)


def check_query_lifecycle(client: ModClient, checks: Checks, world: str) -> None:
    """Cancel a real batched scan on disconnect/exit, then verify fresh queries."""
    original = client.state()
    if not checks.check("query_lifecycle_in_world", original.get("in_world") is True):
        return
    x, y, z = [math.floor(float(original[f"feet_{axis}"])) for axis in "xyz"]
    low, high = [x, y + 8, z], [x + 31, y + 39, z + 31]
    scan = {"cmd": "scan", "from": low, "to": high, "limit": 1}
    client.send(scan)
    client.send({"cmd": "selftest_query_failure", "target": "entities", "mode": "timeout"})
    client.send({"cmd": "selftest_query_failure", "target": "select_slot", "mode": "timeout"})
    client.ping()  # Ensure submission reached the mod before closing the pipe.
    client.reconnect()
    # Already queued rows are intentionally replayed; they precede new work.
    for _ in range(int(client.hello.get("replay_rows", 0))):
        client.readline(timeout=5)
    client.input_lock(True)
    fresh = client.scan(low, low, include_air=True)
    checks.check(
        "query_lifecycle_reconnect",
        fresh.get("from") == low and fresh.get("visited") == 1 and fresh.get("count") == 1,
        str(fresh),
    )
    late = None
    with contextlib.suppress(ModTimeout):
        late = client.wait_event(("scan", "error"), 0.2)
    checks.check("query_lifecycle_no_stale_scan", late is None, str(late or ""))
    checks.check(
        "query_lifecycle_entity_reconnect",
        client.entities(limit=1).get("event") == "entities",
    )
    client.select_slot(int(original["selected_slot"]))
    checks.check(
        "query_lifecycle_slot_reconnect",
        client.wait_event("select_slot_done", 3).get("ok") is True,
    )

    try:
        with client.exchange():
            client.send(scan)
            client.send(
                {
                    "cmd": "selftest_query_failure",
                    "target": "entities",
                    "mode": "timeout",
                    "timeout_ms": 10000,
                }
            )
            client.send(
                {
                    "cmd": "selftest_query_failure",
                    "target": "select_slot",
                    "mode": "timeout",
                    "timeout_ms": 10000,
                }
            )
            left = client.leave_world()
            checks.check("query_lifecycle_leave", left.get("ok") is True, str(left))
            client.wait_event("world_left", 45)
            cancelled = client.wait_event(("scan", "error"), 5)
            other_cancellations = [client.wait_event("error", 5), client.wait_event("error", 5)]
        cancellations = [cancelled, *other_cancellations]
        scan_cancelled = next((row for row in cancellations if row.get("phase") == "scan"), {})
        checks.check(
            "query_lifecycle_world_cancel",
            scan_cancelled.get("event") == "error" and scan_cancelled.get("reason") == "world_left",
            str(scan_cancelled),
        )
        # Consumption order is unspecified across query domains.
        checks.check(
            "query_lifecycle_entity_slot_cancel",
            {row.get("phase") for row in cancellations} == {"scan", "entities", "select_slot"}
            and all(row.get("reason") == "world_left" for row in cancellations),
            str(cancellations),
        )
        deadline = time.monotonic() + 45
        while not client.worlds().get("ready"):
            if time.monotonic() >= deadline:
                raise ModTimeout("query lifecycle: main menu did not become ready")
            time.sleep(0.1)
        checks.check("query_lifecycle_menu_state", client.state().get("in_world") is False)
        refused = client.request(scan, ("scan", "error"))
        checks.check("query_lifecycle_menu_scan", refused.get("reason") == "not_in_world")
        for command in ("entities", "select_slot"):
            refused = client.request({"cmd": command}, "error")
            checks.check(f"query_lifecycle_menu_{command}", refused.get("reason") == "not_in_world")
    finally:
        joined = client.enter_world_ready(world)
        checks.check("query_lifecycle_rejoin", joined.get("ok") is True, str(joined))
        client.wait_in_world(timeout=60)
        client.reset(
            *(float(original[f"feet_{axis}"]) for axis in "xyz"),
            yaw=float(original["yaw"]),
            pitch=float(original["pitch"]),
        )
        wait_ticks(client, 8)
        recovered = client.scan(low, low, include_air=True)
        checks.check("query_lifecycle_world_recovery", recovered.get("visited") == 1)
        checks.check(
            "query_lifecycle_entity_recovery", client.entities(limit=1).get("event") == "entities"
        )
        client.select_slot(int(original["selected_slot"]))
        checks.check(
            "query_lifecycle_slot_recovery",
            client.wait_event("select_slot_done", 3).get("ok") is True,
        )


def check_tick_streams(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Exercise the real producer/consumer boundary and explicit failure outcomes."""
    original = client.state()
    if not checks.check("tick_stream_in_world", original.get("in_world") is True):
        return

    def collect(started: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows = []
        while True:
            row = client.wait_event(
                ("tick", "stopped"),
                5,
                predicate=lambda row: row.get("record_id") == started["record_id"],
            )
            if row["event"] == "stopped":
                return rows, row
            rows.append(row)

    def no_late(record_id: int) -> bool:
        try:
            client.wait_event(
                ("tick", "stopped"), 0.2, predicate=lambda row: row.get("record_id") == record_id
            )
        except ModTimeout:
            return True
        return False

    started = client.start(8)
    rows, done = collect(started)
    checks.check(
        "tick_stream_exact_max",
        [row["tick"] for row in rows] == list(range(1, 9))
        and done.get("ok") is True
        and done.get("reason") == "max_ticks"
        and done.get("rows") == 8,
        str(done),
    )
    required = {
        "x",
        "y",
        "z",
        "vx",
        "vy",
        "vz",
        "yaw",
        "pitch",
        "on_ground",
        "in_air",
        "jump_edge",
        "block_below",
        "block_feet",
        "block_head",
        "sprint",
        "sneak",
        "jump",
        "w",
        "a",
        "s",
        "d",
        "action_sprint",
        "input_sprint",
        "input_sprint_down",
        "health",
    }
    checks.check(
        "tick_stream_fields",
        all(required <= row.keys() and not row.get("truncated") for row in rows),
    )
    checks.check("tick_stream_unique_terminal", no_late(started["record_id"]))

    started = client.start()
    before = int(client.status()["tick"])
    for value in (-1, 1.5, "bad", None):
        rejected = client.request({"cmd": "start", "max_ticks": value}, "error")
        checks.check(f"tick_stream_invalid_{value}", rejected.get("reason") == "bad_param")
    rejected = client.request({"cmd": "start", "max_ticks": 1}, "error")
    status = client.status()
    checks.check(
        "tick_stream_busy_preserves_session",
        rejected.get("reason") == "recording_busy"
        and status.get("recording") is True
        and int(status["tick"]) >= before,
    )
    wait_ticks(client, 4)
    done = client.stop()
    rows = [
        client.wait_event(
            "tick", 2, predicate=lambda row: row.get("record_id") == started["record_id"]
        )
        for _ in range(int(done["rows"]))
    ]
    checks.check(
        "tick_stream_stop_drains_admitted",
        done.get("ok") is True
        and done.get("reason") == "client"
        and len(rows) >= 4
        and [row["tick"] for row in rows] == list(range(1, len(rows) + 1)),
        str(done),
    )
    checks.check("tick_stream_no_sample_after_stop", no_late(started["record_id"]))
    for i in range(4):
        rows = client.record(3)
        checks.check(
            f"tick_stream_restart_{i}",
            [row["tick"] for row in rows] == [1, 2, 3]
            and len({row["record_id"] for row in rows}) == 1,
        )

    for mode, reason in (
        ("seh", "capture_fault"),
        ("cpp", "capture_failed"),
        ("overflow", "buffer_full"),
    ):
        started = client.request(
            {"cmd": "selftest_tick_stream", "mode": mode}, ("started", "error")
        )
        if started.get("event") == "error":
            checks.check("tick_stream_fault_gate", False, str(started))
            break
        rows, done = collect(started)
        checks.check(
            f"tick_stream_{mode}_terminal",
            done.get("ok") is False
            and done.get("reason") == reason
            and len(rows) == (3 if mode == "overflow" else 0),
            str(done),
        )
        checks.check(f"tick_stream_{mode}_unique", no_late(started["record_id"]))
        acknowledged = client.request({"cmd": "stop"}, "stopped")
        checks.check(f"tick_stream_{mode}_failure_retained", acknowledged == done)
        checks.check(
            f"tick_stream_{mode}_recovery", len(client.record(2)) == 2 and bool(client.ping())
        )

    for mode, reason in (("seh", "capture_fault"), ("cpp", "capture_failed")):
        client.request(
            {"cmd": "selftest_tick_stream", "target": "parkour", "mode": mode},
            "tick_stream_test_armed",
        )
        diagnostic = client.wait_event("parkour_diagnostic", 3)
        checks.check(
            f"parkour_{mode}_isolated",
            diagnostic.get("reason") == reason and diagnostic.get("count") == 1,
            str(diagnostic),
        )
        checks.check(
            f"parkour_{mode}_recovery",
            client.state().get("in_world") is True and wait_ticks(client, 2) >= 2,
        )

    name = f"selftest_tick_stream_{time.monotonic_ns()}"
    path = Path(str(client.status()["spool"])).parent / "macros" / f"{name}.json"
    try:
        client.macro_save(name, [["false"] * 9 for _ in range(4)])
        client.macro_load(name)
        started = client.start(40)
        client.send({"cmd": "macro_run", "loop": False})
        rows = read_until_run_end(client, 40, record_id=started["record_id"])
        checks.check(
            "tick_stream_parkour_consumer",
            4 <= len(rows) < 40
            and [row["tick"] for row in rows] == list(range(1, len(rows) + 1))
            and client.status().get("recording") is False,
            f"rows={len(rows)}",
        )
    finally:
        path.unlink(missing_ok=True)

    # Use a real fall after fault recovery; a pure model cannot prove event delivery.
    safe = original.get("on_ground") and not original.get("in_water") and not original.get("flying")
    if checks.check("parkour_live_fixture", bool(safe), "dry ground, not flying"):
        pos = [float(original[f"feet_{axis}"]) for axis in "xyz"]
        client.stop_move()
        # Discard already delivered landings before creating this fall.
        with contextlib.suppress(ModTimeout):
            while True:
                client.wait_event("player_landed", 0.05)
        try:
            client.teleport(pos[0], pos[1] + 3, pos[2])
            landed = client.wait_event("player_landed", 5)
            final = settle(client, on_ground=True)
            checks.check(
                "parkour_landed_after_fault",
                float(landed.get("fall_distance", 0)) >= 1.5
                and final["ok"]
                and abs(float(final["state"]["feet_y"]) - pos[1]) < 0.1,
                str(landed),
            )
        finally:
            client.teleport(*pos, yaw=float(original["yaw"]), pitch=float(original["pitch"]))
            settle(client, on_ground=True)

    if world:
        started = client.start()
        try:
            wait_ticks(client, 4)
            client.leave_world()
            client.wait_event("world_left", 45)
            rows, done = collect(started)
            checks.check(
                "tick_stream_world_cancel",
                done.get("reason") == "world_left"
                and done.get("ok") is False
                and len(rows) >= 4
                and len(rows) == done.get("rows"),
                str(done),
            )
            checks.check("tick_stream_world_no_late", no_late(started["record_id"]))
            deadline = time.monotonic() + 45
            while not client.worlds().get("ready"):
                if time.monotonic() >= deadline:
                    raise ModTimeout("tick stream: main menu did not become ready")
                time.sleep(0.1)
            refused = client.request({"cmd": "start"}, "error")
            checks.check("tick_stream_menu_rejected", refused.get("reason") == "not_in_world")
        finally:
            client.enter_world_ready(world)
            client.wait_in_world(timeout=60)
            client.teleport(*(float(original[f"feet_{axis}"]) for axis in "xyz"))
            restored = settle(client, on_ground=True)
            checks.check("tick_stream_world_still", restored["ok"], str(restored))
        checks.check("tick_stream_world_recovery", len(client.record(3)) == 3)


def check_tick_recovery(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Exercise the outer C++ catches without raising an unsafe whole-tick SEH."""
    original = client.state()
    if not checks.check("tick_recovery_in_world", original.get("in_world") is True):
        return
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    macro_name = f"selftest_tick_recovery_{time.monotonic_ns()}"
    macro_path = directory / "macros" / f"{macro_name}.json"
    client.input_lock(True)
    client.input_cfg(mode=0, jump_impulse=0)

    def ready() -> dict[str, Any]:
        deadline = time.monotonic() + 5
        while True:
            status = client.status()
            if status["tick_recovery"]["active"] is False or time.monotonic() >= deadline:
                return status
            time.sleep(0.01)

    def rebase() -> None:
        client.stop_move()
        client.look(0, relative=True)
        client.teleport(
            *(float(original[f"feet_{axis}"]) for axis in "xyz"),
            yaw=float(original["yaw"]),
            pitch=float(original["pitch"]),
        )
        wait_ticks(client, 4)
        settle(client, on_ground=True)

    def fault(phase: str, mode: str, hold_ms: int = 0) -> dict[str, Any]:
        previous = int(client.status()["tick_recovery"]["recovery_id"])
        client.request(
            {"cmd": "selftest_tick_error", "phase": phase, "mode": mode, "hold_ms": hold_ms},
            "tick_error_test_armed",
        )
        row = client.wait_event(
            "error", 4, predicate=lambda row: int(row.get("recovery_id", 0)) > previous
        )
        checks.check(
            f"tick_recovery_{phase}_{mode}_reported",
            row.get("reason") == "internal_error" and row.get("phase") == f"tick_{phase}",
            ascii(row),
        )
        return row

    try:
        for phase in ("pre", "post"):
            for mode in ("std", "unknown", "long", "invalid"):
                rebase()
                client.set_input(forward=1, ticks=-1)
                client.look(1, relative=True, ticks=-1)
                wait_ticks(client, 2)
                # Also retain an event-level key and fail its movement key-up
                # once; both paths must finish release before readmission.
                key = int(original["bindings"]["sneak"])
                queued = client.key_event(key, True, via="hardware")
                client.wait_event(
                    "input_event_done",
                    3,
                    predicate=lambda row, input_id=queued["input_id"]: (
                        row.get("input_id") == input_id
                    ),
                )
                client.request(
                    {"cmd": "selftest_controls", "operation": "key_up", "mode": "seh_before"},
                    "control_test_armed",
                )
                row = fault(phase, mode, 1500)
                checks.check(
                    f"tick_recovery_{phase}_{mode}_message",
                    row.get("truncated") is (mode == "long")
                    and 0 < len(str(row.get("message", "")).encode()) <= 255
                    and (mode != "unknown" or row.get("message") == "unknown exception")
                    and (mode != "invalid" or "\ufffd" in str(row.get("message", ""))),
                )
                pending = client.status()
                refused = client.request({"cmd": "set", "forward": 1, "ticks": -1}, "error")
                unknown = client.request({"cmd": "__tick_recovery_unknown"}, "error")
                checks.check(
                    f"tick_recovery_{phase}_{mode}_admission",
                    pending["tick_recovery"]["active"] is True
                    and refused.get("reason") == "tick_recovering"
                    and unknown.get("reason") == "unknown_cmd"
                    and bool(client.ping()),
                    str(pending["tick_recovery"]),
                )
                if phase == "pre" and mode == "std":
                    isolated = client.request({"cmd": "selftest_av"}, "error")
                    checks.check(
                        "tick_recovery_dispatch_unlocks",
                        isolated.get("phase") == "dispatch" and bool(client.ping()),
                    )
                recovered = ready()
                controls = recovered["controls"]
                checks.check(
                    f"tick_recovery_{phase}_{mode}_released",
                    recovered["tick_recovery"]["active"] is False
                    and controls["held_mask"] == 0
                    and controls["cleanup_pending"] is False
                    and controls["injecting_input"] is False
                    and controls["injecting_look"] is False
                    and client.key_state(key).get("down") is False,
                    str(recovered["tick_recovery"]),
                )
                wait_ticks(client, 6)
                state = client.state()
                wait_ticks(client, 4)
                after_state = client.state()
                checks.check(
                    f"tick_recovery_{phase}_{mode}_not_replayed",
                    state["input"]["w"] is False
                    and state["input"]["jump"] is False
                    and abs((float(after_state["yaw"]) - float(state["yaw"]) + 180) % 360 - 180)
                    < 0.05,
                    f"before={state['yaw']}, after={after_state['yaw']}",
                )
        for event_level in (False, True):
            rebase()
            client.macro_save(macro_name, [["true", *["false"] * 8, "1", "0"]] * 4)
            client.macro_load(macro_name)
            started = client.macro_run(loop=True, event_level=event_level)
            wait_ticks(client, 2)
            fault("post", "std")
            done = client.wait_event(
                "macro_done",
                4,
                predicate=lambda row, run_id=started["run_id"]: row.get("run_id") == run_id,
            )
            ready()
            checks.check(
                f"tick_recovery_macro_{event_level}_cancelled",
                done.get("ok") is False
                and done.get("reason") == "internal_error"
                and client.macro_status().get("playing") is False,
                str(done),
            )
        rebase()
        started = client.request({"cmd": "use", "ticks": 2000}, "use_started")
        fault("pre", "unknown")
        cancelled = client.wait_event(
            "task_cancelled", 4, predicate=lambda row: row.get("task_id") == started["task_id"]
        )
        ready()
        checks.check("tick_recovery_task_cancelled", cancelled.get("reason") == "internal_error")
        checks.check("tick_recovery_fresh_task", client.jump_when(mode="now").get("ok") is True)
        settle(client, on_ground=True)
        client.set_input(forward=1, ticks=10)
        wait_ticks(client, 1)
        checks.check("tick_recovery_fresh_movement", client.state()["input"]["w"] is True)
        client.stop_move()
        wait_ticks(client, 4)
        if world:
            client.set_input(forward=1, ticks=-1)
            row = fault("pre", "std", 2000)
            client.reconnect()
            reconnecting = client.status()["tick_recovery"]
            checks.check(
                "tick_recovery_reconnect_keeps_barrier",
                reconnecting.get("active") is True
                and reconnecting.get("recovery_id") == row["recovery_id"],
            )
            left = client.leave_world()
            client.wait_event("world_left", 30)
            checks.check("tick_recovery_leave", left.get("ok") is True)
            refused = client.request({"cmd": "move", "forward": 1}, "error")
            checks.check(
                "tick_recovery_menu_keeps_barrier", refused.get("reason") == "tick_recovering"
            )
            client.enter_world_ready(world)
            client.wait_in_world(timeout=60)
            recovered = ready()
            state = client.state()
            checks.check(
                "tick_recovery_rejoin_clears_barrier",
                recovered["tick_recovery"]["active"] is False and state["in_world"] is True,
            )
            checks.check("tick_recovery_rejoin_no_replay", state["input"]["w"] is False)
    finally:
        client.request({"cmd": "selftest_tick_error", "mode": "off"}, "tick_error_test_armed")
        ready()
        client.request({"cmd": "selftest_controls", "mode": "off"}, "control_test_armed")
        client.macro_stop()
        macro_path.unlink(missing_ok=True)
        rebase()
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("tick_recovery_no_crash_artifacts", after == before)


def check_input_mode_contract(client: ModClient, checks: Checks) -> None:
    """Keep protocol numbers and the legacy force gate compatible; never move."""
    try:
        for bad_impulse in (1, 3):
            try:
                client.request({"cmd": "input_cfg", "jump_impulse": bad_impulse}, "input_cfg_ok")
                checks.check(f"jump_impulse_guard_{bad_impulse}", False, "legacy impulse accepted")
            except ModProtocolError as exc:
                checks.check(
                    f"jump_impulse_guard_{bad_impulse}",
                    "legacy_jump_impulse_requires_force" in str(exc),
                    str(exc),
                )
        try:
            client.request({"cmd": "input_cfg", "mode": 7}, "input_cfg_ok")
            checks.check("legacy_input_mode_guard", False, "legacy mode accepted")
        except ModProtocolError as exc:
            checks.check(
                "legacy_input_mode_guard",
                "legacy_input_mode_requires_force" in str(exc),
                str(exc),
            )
        forced = client.request(
            {"cmd": "input_cfg", "jump_impulse": 1, "mode": 7, "force": True}, "input_cfg_ok"
        )
        checks.check("legacy_input_force", forced.get("event") == "input_cfg_ok", str(forced))
    finally:
        with preserve_failure(checks, "input_mode"):
            restored = client.input_cfg(jump_impulse=0, mode=0)
            checks.check(
                "real_input_restore", restored.get("event") == "input_cfg_ok", str(restored)
            )


def check_controls(client: ModClient, checks: Checks) -> None:
    """Exercise real control calls, uncertain edges, failure propagation and retry."""
    original = client.state()
    if not checks.check("controls_in_world", original.get("in_world") is True):
        return
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    client.input_lock(True)
    client.input_cfg(mode=0, jump_impulse=0)
    macro_name = f"selftest_controls_{time.monotonic_ns()}"
    macro_path = directory / "macros" / f"{macro_name}.json"
    client.stop_move()
    settle(client, on_ground=True)

    def arm(operation: str, mode: str) -> None:
        client.request(
            {"cmd": "selftest_controls", "operation": operation, "mode": mode},
            "control_test_armed",
        )

    def recovery() -> dict[str, Any]:
        deadline = time.monotonic() + 4
        while True:
            state: dict[str, Any] = client.status().get("controls", {})
            if (
                state.get("held_mask") == 0
                and state.get("cleanup_pending") is False
                and state.get("injecting_input") is False
                and state.get("injecting_look") is False
            ) or time.monotonic() >= deadline:
                return state
            time.sleep(0.01)

    def diagnostic(operation: str, mode: str, prefix: str = "controls") -> None:
        row = client.wait_event(
            "control_error", 4, predicate=lambda row: row.get("operation") == operation
        )
        key = (
            "faults"
            if mode.startswith("seh")
            else ("failures" if mode.startswith("cpp") else "unavailable")
        )
        checks.check(f"{prefix}_{operation}_{mode}_reported", int(row.get(key, 0)) == 1, str(row))
        state = recovery()
        checks.check(
            f"{prefix}_{operation}_{mode}_releases",
            state.get("held_mask") == 0
            and state.get("cleanup_pending") is False
            and state.get("injecting_input") is False
            and state.get("injecting_look") is False,
            str(state),
        )
        wait_ticks(client, 2)
        current = client.state()
        checks.check(
            f"{prefix}_{operation}_{mode}_game_released",
            current["input"].get("w") is False
            and current["input"].get("jump") is False
            and client.key_state(int(current["bindings"]["forward"])).get("down") is False,
        )

    try:
        for operation in ("key_down", "key_up", "mouse_down", "mouse_up", "turn", "look_read"):
            for mode in ("seh_before", "cpp_before", "seh_after", "cpp_after", "unavailable"):
                client.stop_move()
                client.look(0, relative=True)
                recovery()
                # Rebase the previous after-call fault's local turn through
                # the authoritative server before testing the next sequence.
                # A late camera correction must not look like replay.
                if operation in ("turn", "look_read"):
                    client.teleport(
                        *(float(original[f"feet_{axis}"]) for axis in "xyz"),
                        yaw=float(original["yaw"]),
                        pitch=float(original["pitch"]),
                    )
                    wait_ticks(client, 4)
                if operation in ("key_up", "mouse_up"):
                    client.set_input(
                        forward=1 if operation == "key_up" else 0,
                        lmb=operation == "mouse_up",
                        ticks=-1,
                    )
                    wait_ticks(client, 2)
                arm(operation, mode)
                if operation in ("turn", "look_read"):
                    client.look(6, relative=True, ticks=-1)
                elif operation in ("key_up", "mouse_up"):
                    client.stop_move()
                else:
                    client.set_input(
                        forward=1 if operation == "key_down" else 0,
                        lmb=operation == "mouse_down",
                        ticks=-1,
                    )
                diagnostic(operation, mode)
                if operation in ("turn", "look_read"):
                    yaw = float(client.state()["yaw"])
                    wait_ticks(client, 4)
                    after_yaw = float(client.state()["yaw"])
                    checks.check(
                        f"controls_{operation}_{mode}_not_replayed",
                        abs((after_yaw - yaw + 180) % 360 - 180) < 0.05,
                        f"before={yaw:.6f}, after={after_yaw:.6f}",
                    )
        for operation in ("bindings", "actions"):
            arm(operation, "seh_before")
            diagnostic(operation, "seh_before")
        for operation in ("key_down", "turn"):
            settle(client, on_ground=True)
            arm(operation, "seh_after")
            result = client.jump_when(
                mode="now",
                forward=0.25,
                yaw=float(client.state()["yaw"]) + 15 if operation == "turn" else None,
            )
            checks.check(
                f"controls_task_{operation}_fails",
                result.get("ok") is False and result.get("reason") == "input_failed",
                str(result),
            )
            diagnostic(operation, "seh_after", "controls_task")
        for event_level in (False, True):
            client.macro_save(macro_name, [["true", *["false"] * 8, "5", "0"]] * 4)
            client.macro_load(macro_name)
            arm("turn", "seh_after")
            started = client.macro_run(event_level=event_level)
            done = client.wait_event(
                "macro_done",
                4,
                predicate=lambda row, run_id=started["run_id"]: row.get("run_id") == run_id,
            )
            checks.check(
                f"controls_macro_{event_level}_turn_fails",
                done.get("ok") is False and done.get("reason") == "playback_fault",
                str(done),
            )
            diagnostic("turn", "seh_after", f"controls_macro_{event_level}")
        settle(client, on_ground=True)
        fresh = client.jump_when(mode="now")
        checks.check("controls_fresh_task_recovers", fresh.get("ok") is True, str(fresh))
        settle(client, on_ground=True)
        checks.check(
            "controls_ping_alive", bool(client.ping()) and client.state()["in_world"] is True
        )
    finally:
        with preserve_failure(checks, "controls"):
            arm("", "off")
            client.macro_stop()
            macro_path.unlink(missing_ok=True)
            client.stop_move()
            client.look(0, relative=True)
            recovery()
            client.teleport(
                *(float(original[f"feet_{axis}"]) for axis in "xyz"),
                yaw=float(original["yaw"]),
                pitch=float(original["pitch"]),
            )
            settle(client, on_ground=True)
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("controls_handled_fault_no_dump", after == before)


def check_player_identity(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Verify UUID-only local resolution, failed samples and world retirement."""
    original = client.state()
    if not checks.check("identity_in_world", original.get("in_world") is True):
        return
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    client.input_lock(True)

    def identity_ready(valid: bool) -> dict[str, Any]:
        deadline = time.monotonic() + 3
        while True:
            row: dict[str, Any] = client.status().get("player_identity", {})
            if row.get("valid") is valid or time.monotonic() >= deadline:
                return row
            time.sleep(0.01)

    def arm(mode: str) -> None:
        client.request({"cmd": "selftest_player_identity", "mode": mode}, "identity_test_armed")

    def inventory_items(row: dict[str, Any]) -> dict[str, Any]:
        return {key: row.get(key) for key in ("slots", "armor", "offhand", "selected_slot")}

    try:
        first = identity_ready(True)
        checks.check(
            "identity_snapshot_valid",
            first.get("valid") is True
            and bool(first.get("uuid_a") or first.get("uuid_b"))
            and int(first.get("sample_tick", -1)) >= 0,
            str(first),
        )
        checks.check("identity_local_origin", client.command("testfor @s").get("success") is True)
        checks.check(
            "identity_explicit_origin",
            client.command("testfor @s", player=str(original["name"])).get("success") is True,
        )
        missing = client.request(
            {
                "cmd": "command",
                "command": "say identity must not use console",
                "player": "__no_player__",
            },
            "error",
        )
        checks.check(
            "identity_missing_player_no_console",
            missing.get("reason") == "command_player_not_found",
        )
        for mode, counter in (("seh", "faults"), ("cpp", "failures"), ("zero", "empty")):
            old = client.status()["player_identity"]
            inventory = inventory_items(client.inventory())
            arm(mode)
            invalid = identity_ready(False)
            checks.check(
                f"identity_{mode}_invalidates",
                invalid.get("valid") is False
                and int(invalid.get(counter, 0)) > int(old.get(counter, 0)),
                str(invalid),
            )
            refused = client.request({"cmd": "command", "command": "testfor @s"}, "error")
            checks.check(
                f"identity_{mode}_command_rejected",
                refused.get("reason") == "command_player_unknown",
            )
            for command in (
                {"cmd": "give", "item": "minecraft:stick"},
                {"cmd": "set_item", "slot": 8, "item": "minecraft:stone"},
                {"cmd": "equip", "target": "offhand", "slot": 8},
                {"cmd": "clear_inventory"},
            ):
                refused = client.request(command, "error")
                checks.check(
                    f"identity_{mode}_{command['cmd']}_rejected",
                    refused.get("reason") == "inventory_player_unknown",
                )
            checks.check(
                f"identity_{mode}_inventory_intact",
                inventory_items(client.inventory()) == inventory,
            )
            checks.check(
                f"identity_{mode}_tick_alive",
                bool(client.ping()) and client.state().get("in_world") is True,
            )
            arm("off")
            recovered = identity_ready(True)
            checks.check(
                f"identity_{mode}_recovery",
                recovered.get("valid") is True
                and client.command("testfor @s").get("success") is True,
            )

        remembered = client.request(
            {"cmd": "selftest_player_identity", "mode": "remember"}, "identity_remembered"
        )
        current = client.request(
            {"cmd": "selftest_player_identity", "mode": "resolve"}, "identity_resolved"
        )
        checks.check("identity_current_server_resolves", current.get("ok") is True)
        client.close()
        client.connect()
        client.input_lock(True)
        checks.check(
            "identity_reconnect_keeps_current",
            identity_ready(True).get("epoch") == remembered["epoch"]
            and client.command("testfor @s").get("success") is True,
        )
        if world:
            try:
                client.leave_world()
                client.wait_event("world_left", 45)
                absent = client.status()["player_identity"]
                checks.check("identity_menu_invalid", absent.get("valid") is False, str(absent))
                checks.check("identity_menu_new_epoch", absent.get("epoch") != remembered["epoch"])
            finally:
                client.enter_world_ready(world)
                client.wait_in_world(timeout=60)
            recovered = identity_ready(True)
            checks.check(
                "identity_new_world_recovers",
                recovered.get("valid") is True and recovered.get("epoch") != remembered["epoch"],
            )
            obsolete = client.request(
                {"cmd": "selftest_player_identity", "mode": "resolve"}, "identity_resolved"
            )
            checks.check("identity_old_world_rejected", obsolete.get("ok") is False, str(obsolete))
            client.teleport(
                *(float(original[f"feet_{axis}"]) for axis in "xyz"),
                yaw=float(original["yaw"]),
                pitch=float(original["pitch"]),
            )
            checks.check("identity_world_restores_ground", settle(client, on_ground=True)["ok"])
            checks.check(
                "identity_new_world_local_origin",
                client.command("testfor @s").get("success") is True,
            )
    finally:
        if client.status().get("in_world"):
            arm("off")
            identity_ready(True)
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("identity_handled_fault_no_dump", after == before)


def check_packet_tap(client: ModClient, checks: Checks) -> None:
    """Fail observations of real, unmodified traffic; normal delivery must continue."""
    try:
        client.packet_capture(True, ids=[144], directions=["send"])
        client.request({"cmd": "selftest_packet_tap", "mode": "off"}, "packet_tap_test_armed")
    except ModProtocolError as exc:
        if "packet_capture_disabled" not in str(exc) and "selftest_stress_disabled" not in str(exc):
            raise
        checks.skip("packet_tap_fault_coverage", str(exc))
        client.packet_capture(False)
        return
    directory = Path(str(client.status()["spool"])).parent
    before_crashes = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}

    def traffic(direction: str) -> None:
        if direction == "recv":
            client.command('tellraw @s {"rawtext":[{"text":"LeviPilot packet observation check"}]}')
        wait_ticks(client, 4)

    try:
        for direction, packet_id in (("send", 144), ("recv", 9)):
            client.packet_capture(
                True, ids=[packet_id], serialize_ids=[packet_id], directions=[direction]
            )
            traffic(direction)
            rows = client.packet_bytes(0, limit=4096)["hits"]
            checks.check(
                f"packet_tap_{direction}_baseline",
                bool(rows) and all(row["serialized"] and int(row["size"]) > 0 for row in rows),
                f"rows={len(rows)}",
            )
            for mode in ("id_seh", "id_cpp", "bytes_seh", "bytes_cpp"):
                before = client.packet_stats()
                cursor = int(client.packet_bytes(0, limit=4096).get("next", 0))
                client.request(
                    {"cmd": "selftest_packet_tap", "mode": mode}, "packet_tap_test_armed"
                )
                traffic(direction)
                deadline = time.monotonic() + 4
                while True:
                    stats = client.packet_stats()
                    if not stats["fault_pending"]:
                        break
                    if time.monotonic() >= deadline:
                        raise ModTimeout(f"packet tap fault was not observed: {direction}/{mode}")
                    time.sleep(0.01)
                byte_fault = mode.startswith("bytes")
                name = f"packet_tap_{direction}_{mode}"
                checks.check(
                    name + "_isolated",
                    int(stats["id_faults"]) == int(before["id_faults"]) + int(not byte_fault)
                    and int(stats["byte_faults"]) == int(before["byte_faults"]) + int(byte_fault),
                    f"id={stats['id_faults']} bytes={stats['byte_faults']}",
                )
                rows = client.packet_bytes(cursor, limit=4096)["hits"]
                failures = [row for row in rows if row["serialized"] is False]
                expected_reason = (
                    "packet_serialize_fault" if mode == "bytes_seh" else "packet_serialize_failed"
                )
                identity = {
                    (int(row["seq"]), int(row["id"]), row["dir"])
                    for row in client.packet_drain(0, limit=8192)["hits"]
                }
                checks.check(
                    name + "_diagnostic",
                    len(failures) == int(byte_fault)
                    and all(
                        row.get("reason") == expected_reason
                        and int(row["size"]) == 0
                        and row["hex"] == ""
                        and (int(row["seq"]), int(row["id"]), row["dir"]) in identity
                        for row in failures
                    ),
                    str(failures),
                )
                # A second real packet must serialize normally after the fault.
                cursor = int(client.packet_bytes(0, limit=4096).get("next", 0))
                traffic(direction)
                rows = client.packet_bytes(cursor, limit=4096)["hits"]
                checks.check(
                    name + "_recovers",
                    bool(rows) and all(row["serialized"] and int(row["size"]) > 0 for row in rows),
                    f"rows={len(rows)}",
                )
                client.packet_capture(False)  # Also proves the callback gate was released.
                checks.check(name + "_stops", client.packet_stats()["active"] is False)
                client.packet_capture(
                    True, ids=[packet_id], serialize_ids=[packet_id], directions=[direction]
                )
            stats = client.packet_stats()
            checks.check(
                f"packet_tap_{direction}_reset",
                stats["id_faults"] == stats["byte_faults"] == 0 and not stats["fault_pending"],
            )
        client.packet_capture(False)
        rejected = client.request({"cmd": "selftest_packet_tap", "mode": "id_seh"}, "error")
        checks.check(
            "packet_tap_inactive_rejects", rejected.get("reason") == "packet_capture_inactive"
        )
        checks.check("packet_tap_alive", bool(client.ping()) and client.state()["in_world"] is True)
    finally:
        try:
            client.request({"cmd": "selftest_packet_tap", "mode": "off"}, "packet_tap_test_armed")
        finally:
            client.packet_capture(False)
    after_crashes = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("packet_tap_handled_fault_no_dump", after_crashes == before_crashes)


def role_address(role: str) -> tuple[int, int] | None:
    """Role VA and its documented image base from docs/roles (never hard-coded).

    Addresses there are static VAs under ``image_base``. The installed game has a
    rewritten preferred base and ASLR, so the watch needs
    ``module_base + (role_va - image_base)``.
    """
    version = "1.26.51.01"
    path = ROOT / "docs" / "roles" / f"{version}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entry = data.get("roles", {}).get(role, {})
    address = entry.get("address")
    base = data.get("image_base")
    if entry.get("status") != "resolved" or not address or not base:
        return None
    return int(str(address), 16), int(str(base), 16)


def violation_watch_address(client: ModClient, role: str) -> int | None:
    """Runtime address of a role: ``module_base + (role_va - image_base)``."""
    role_info = role_address(role)
    if role_info is None:
        return None
    role_va, image_base = role_info
    module_base = int(client.diag().get("module_base", 0))
    if module_base == 0 or role_va < image_base:
        return None
    return module_base + (role_va - image_base)


def check_inbound_slices(client: ModClient, checks: Checks) -> None:
    """Verify the parse-boundary slices decode to (packet id, payload).

    The parse boundary receives the slice starting at the packet id, and the
    frame's length varint is the byte right before it. Both halves are read by
    the same read-only hook, so this check proves the instrumentation that
    decides "framed short" vs "clipped after framing" is actually wired: it
    never asserts a verdict for a live packet.
    """
    rows = inbound_slices(client, since=0, limit=256)
    decoded = [row for row in rows if row.get("decoded")]
    if not checks.check(
        "inbound_slice_rows",
        len(decoded) >= 4,
        f"rows={len(rows)} decoded={len(decoded)}",
    ):
        return
    checks.check(
        "inbound_slice_packet_id",
        all(0 <= row["decoded"]["packet_id"] < 1000 for row in decoded),
        str(sorted({row["decoded"]["packet_id"] for row in decoded})[:12]),
    )
    ids = {row["decoded"]["packet_id"] for row in decoded}
    checks.check(
        "inbound_slice_known_ids",
        bool(ids & {39, 111, 144, 187, 188}),
        f"ids={sorted(ids)[:12]}",
    )
    framed = [row for row in decoded if row["verdict"] == "framed"]
    if len(framed) * 2 < len(decoded):
        # The length prefix may live outside the captured window; that is a
        # finding, not a failure of the hook.
        checks.skip(
            "inbound_slice_framing",
            f"declared length unreadable: framed={len(framed)}/{len(decoded)}",
        )
        return
    checks.check(
        "inbound_slice_framing",
        len(framed) * 10 >= len(decoded) * 9,
        f"framed={len(framed)}/{len(decoded)}",
    )


def check_violation_watch(client: ModClient, checks: Checks) -> None:
    """Verify the read-only violation record plumbing with a synthetic row.

    A synthetic row only proves the record/drain path; it is not a real
    BadPacket reproduction. The watch itself never changes the verdict, the
    error code or the disconnect decision.
    """
    if not checks.check("violation_watch_in_world", client.state().get("in_world") is True):
        return
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    addr = violation_watch_address(client, "@client_handle_packet_violation")
    if not checks.check(
        "violation_watch_role",
        addr is not None,
        f"runtime address={hex(addr) if addr else None}",
    ):
        return
    assert addr is not None
    was_active = client.violation_stats().get("active") is True
    since = int(client.violation_drain(0, 1).get("head", 0))
    try:
        started = client.violation_watch(True, addr=addr)
        if started.get("event") == "error":
            checks.check(
                "violation_watch_gate",
                started.get("reason") == "violation_watch_disabled",
                str(started),
            )
            checks.skip("violation_watch_observation", "allow_hook_va is disabled")
            return
        checks.check(
            "violation_watch_start",
            started.get("active") is True and bool(started.get("address")),
            str(started),
        )
        checks.check(
            "violation_watch_screen_hooked", started.get("screen_hooked") is True, str(started)
        )
        stats = client.violation_stats()
        checks.check(
            "inbound_freeze_clear",
            client.violation_stats().get("inbound_frozen") is False,
            "a healthy session must not be frozen",
        )
        checks.check(
            "violation_watch_stats_active",
            stats.get("active") is True
            and int(stats.get("capacity", 0)) > 0
            and stats.get("screen_hooked") is True,
            str(stats),
        )
        route = client.disconnect_route(90)
        checks.check(
            "disconnect_route_shape",
            "reason=90" in str(route.get("route", ""))
            and "codeword=Block" in str(route.get("route", "")),
            str(route),
        )
        synthetic = client.violation_watch(mode="synthetic")
        checks.check("violation_synthetic_ok", synthetic.get("ok") is True, str(synthetic))
        checks.check(
            "inbound_freeze_ignores_synthetic",
            client.violation_stats().get("inbound_frozen") is False,
            "the synthetic row never goes through the callback hook",
        )
        # The page text is a std::string with no fixed bound; the full-text ring
        # must return it whole even when it is longer than a record row.
        long_details = "page " + "x" * 600
        client.request(
            {
                "cmd": "disconnect_route",
                "reason": 90,
                "codeword": "Block",
                "details": long_details,
            },
            "disconnect_route",
        )
        texts = client.violation_text(0, 8).get("hits", [])
        callback_text = next((row for row in texts if row.get("kind") == "callback"), None)
        screen_text = next(
            (
                row
                for row in texts
                if row.get("kind") == "screen" and row.get("text") == long_details
            ),
            None,
        )
        checks.check(
            "violation_text_full_length",
            screen_text is not None
            and screen_text.get("len") == len(long_details)
            and screen_text.get("truncated") is False,
            str(screen_text)[:120],
        )
        checks.check(
            "violation_text_callback",
            callback_text is not None and callback_text.get("text") == "synthetic violation record",
            str(callback_text)[:120],
        )
        drained = client.violation_drain(since, 64)
        rows = drained.get("hits", [])
        row = next(
            (item for item in rows if item.get("synthetic") and item.get("source") == "callback"),
            None,
        )
        checks.check(
            "violation_drain_row",
            row is not None
            and row.get("response") == 3
            and row.get("verdict") == "terminate_connection"
            and row.get("packet_id") == -1,
            str(row),
        )
        checks.check(
            "violation_drain_context",
            row is not None
            and row.get("context") == "synthetic violation record"
            and row.get("context_truncated") is False
            and int(row.get("tick", -1)) >= 0,
            str(row),
        )
        rows_screen = client.violation_drain(since, 64).get("hits", [])
        screen_row = next((item for item in rows_screen if item.get("source") == "screen"), None)
        checks.check(
            "violation_drain_screen_row",
            screen_row is not None
            and screen_row.get("response") == 90
            and screen_row.get("context") == "Block"
            and screen_row.get("synthetic") is True
            and screen_row.get("disconnect_reason") == 90
            and screen_row.get("verdict") == "disconnect",
            str(screen_row),
        )
        checks.check(
            "violation_drain_cursor",
            int(drained.get("next", -1)) >= int(drained.get("count", 0))
            and client.violation_drain(int(drained.get("next", 0)), 64).get("count") == 0,
            str({k: drained.get(k) for k in ("next", "count", "lost")}),
        )
        # The patch sits on a violation-only path: normal play must stay intact.
        checks.check("violation_watch_ping", client.ping().get("event") == "pong")
        client.disconnect_route(0)
        reason_zero = client.violation_drain(int(drained["next"]), 64).get("hits", [])
        checks.check(
            "violation_screen_reason_not_verdict",
            any(
                item.get("source") == "screen"
                and item.get("response") == 0
                and item.get("verdict") == "disconnect"
                and item.get("synthetic") is True
                for item in reason_zero
            ),
            str(reason_zero),
        )
        client.command("time set day")
        wait_ticks(client, 2)
        try:
            check_inbound_slices(client, checks)
        except (ModPipeError, ModTimeout) as exc:
            checks.check("inbound_slice_rows", False, f"inbound drain failed: {exc}")
    finally:
        with preserve_failure(checks, "violation_watch"):
            try:
                stopped = client.violation_watch(False)
                checks.check("violation_watch_stop", stopped.get("active") is False, str(stopped))
                checks.check(
                    "violation_watch_stats_stopped", client.violation_stats().get("active") is False
                )
                checks.check(
                    "inbound_freeze_reset_on_stop",
                    client.violation_stats().get("inbound_frozen") is False,
                )
                checks.check(
                    "violation_watch_state_alive",
                    bool(client.state().get("in_world")) and client.ping().get("event") == "pong",
                )
            finally:
                if was_active:
                    restored = client.violation_watch(True, addr=addr)
                    checks.check(
                        "violation_watch_restored",
                        restored.get("active") is True and restored.get("screen_hooked") is True,
                        str(restored),
                    )
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("violation_watch_no_dump", after == before)


def check_send_probe(client: ModClient, checks: Checks) -> None:
    """Read-only send-path probe: install, observe traffic without dumps, stop.

    The probe only reads `[netSystem+0x1B0]` and the enqueued string; a healthy
    session must therefore report hits (traffic exists) and zero dumps (no frame
    length outside the healthy set). Nothing here may change a packet.
    """
    if not checks.check("send_probe_in_world", client.state().get("in_world") is True):
        return
    enqueue = violation_watch_address(client, "@client_packet_enqueue")
    serializer = violation_watch_address(client, "@client_packet_send_serialize")
    checks.check(
        "send_probe_roles",
        enqueue is not None and serializer is not None,
        f"enqueue={hex(enqueue) if enqueue else None} serialize={hex(serializer) if serializer else None}",
    )
    if enqueue is None or serializer is None:
        return
    started = client.send_probe(True, addr=enqueue, serialize_addr=serializer)
    if started.get("event") == "error":
        checks.check(
            "send_probe_gate",
            started.get("reason") == "hook_va_disabled",
            str(started),
        )
        checks.skip("send_probe_observation", "allow_hook_va is disabled")
        return
    try:
        checks.check(
            "send_probe_start",
            started.get("active") is True and started.get("serialize_hooked") is True,
            str({k: started.get(k) for k in ("active", "serialize_hooked")}),
        )
        checks.check(
            "send_probe_path",
            str(started.get("path", "")).endswith(".jsonl"),
            str(started.get("path")),
        )
        wait_ticks(client, 12)
        stopped = client.send_probe(False)
        checks.check("send_probe_hits", int(stopped.get("hits", 0)) > 0, str(stopped))
        checks.check(
            "send_probe_no_dumps",
            int(stopped.get("dumps", -1)) == 0,
            "healthy traffic must not produce suspect frames",
        )
        checks.check("send_probe_stopped", stopped.get("active") is False, str(stopped))
        checks.check(
            "send_probe_state_alive",
            bool(client.state().get("in_world")) and client.ping().get("event") == "pong",
        )
    finally:
        with preserve_failure(checks, "send_probe"):
            client.send_probe(False)


def check_packet_injection(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Exercise the production template hook and owned send pipeline with readback."""
    if not client.status().get("packet_inject"):
        checks.skip("packet_owned_inject", "packet injection disabled")
        return
    try:
        client.packet_capture(True, ids=[77])
    except ModProtocolError as exc:
        checks.check("packet_owned_capture_gate", "packet_capture_disabled" in str(exc), str(exc))
        checks.skip("packet_owned_capture", str(exc))
        return
    original = client.state()
    client.input_lock(True)
    client.set_input()
    client.op_self()
    client.commands_enabled(True)
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    x, y, z = (math.floor(float(original[f"feet_{axis}"])) for axis in "xyz")
    candidates = [[x + dx, y, z + dz] for dx, dz in ((4, 4), (-4, 4), (4, -4), (-4, -4))]
    blocks = client.blocks(candidates)["blocks"]
    target = next(
        (
            cell
            for cell, block in zip(candidates, blocks, strict=True)
            if block.get("name") == "minecraft:air"
        ),
        None,
    )
    if not checks.check("packet_owned_air_fixture", target is not None):
        client.packet_capture(False)
        return
    assert target is not None
    tx, ty, tz = target
    command = f"/setblock {tx} {ty} {tz} minecraft:gold_block"

    def block() -> str:
        return str(client.blocks([target])["blocks"][0].get("name"))

    def clear() -> None:
        client.command(f"setblock {tx} {ty} {tz} air")
        wait_ticks(client, 4)
        if block() != "minecraft:air":
            raise ValueError("packet fixture failed to restore air")

    def complete(packet_id: int) -> dict[str, Any]:
        return client.wait_event(
            ("chat_sent", "error"), 4, predicate=lambda row: row.get("packet_id") == packet_id
        )

    def recovered() -> None:
        deadline = time.monotonic() + 4
        while client.status()["tick_recovery"]["active"]:
            if time.monotonic() >= deadline:
                raise ModTimeout("packet selftest: tick recovery did not finish")
            time.sleep(0.01)

    def unique(packet_id: int) -> bool:
        try:
            client.wait_event(
                "chat_sent", 0.12, predicate=lambda row: row.get("packet_id") == packet_id
            )
        except ModTimeout:
            return True
        return False

    def inject(mode: str, timeout: int = 250) -> dict[str, Any]:
        return client.request(
            {
                "cmd": "selftest_packet_injection",
                "mode": mode,
                "text": command,
                "timeout_ms": timeout,
            },
            "chat_send_queued",
        )

    def seed(mode: str = "off") -> None:
        client.packet_capture(True, ids=[77])
        client.key_event(191)
        client.key_event(191, False)
        if wait_screen(client, "chat_screen") != "chat_screen":
            raise ValueError("packet fixture failed to open chat")
        try:
            client.text_input("say harness_owned_template", wait=True)
            client.request(
                {"cmd": "selftest_packet_template", "mode": mode}, "packet_template_test_armed"
            )
            count = int(client.status()["command_template_attempts"])
            client.key_event(13)
            client.key_event(13, False)
            if wait_screen(client, "hud_screen") != "hud_screen":
                raise ValueError("packet fixture failed to submit chat")
            if int(client.status()["command_template_attempts"]) <= count:
                raise ValueError("packet fixture did not capture the real command send")
        finally:
            if client.screen().get("name") == "chat_screen":
                client.key_event(27)
                client.key_event(27, False)
                wait_screen(client, "hud_screen")

    def no_template(name: str) -> None:
        queued = client.chat_send(command)
        row = complete(int(queued["packet_id"]))
        checks.check(
            name,
            row.get("ok") is False
            and row.get("reason") in ("no_command_template", "command_template_stale"),
            str(row),
        )
        checks.check(name + "_no_effect", block() == "minecraft:air")

    try:
        client.panic("tasks")
        no_template("packet_owned_no_template")
        seed()
        sent_before = int(client.status()["chat_sent"])
        done = client.chat_send(command, wait=True)
        wait_ticks(client, 4)
        checks.check(
            "packet_owned_success", done.get("ok") is True and block() == "minecraft:gold_block"
        )
        checks.check("packet_owned_success_unique", unique(int(done["packet_id"])))
        checks.check("packet_owned_count", int(client.status()["chat_sent"]) == sent_before + 1)
        clear()
        for mode, reason in (
            ("seh", "packet_inject_fault"),
            ("cpp", "packet_inject_failed"),
            ("timeout", "packet_inject_timeout"),
            ("queue", "packet_inject_queue_failed"),
            ("stale_epoch", "world_left"),
            ("wrong_owner", "command_template_owner_mismatch"),
            ("after_send", "packet_inject_fault"),
        ):
            accepted = inject(mode)
            packet_id = int(accepted["packet_id"])
            result = complete(packet_id)
            checks.check(
                f"packet_owned_{mode}",
                result.get("ok") is False
                and result.get("reason") == reason
                and result.get("partial") is True,
                str(result),
            )
            checks.check(f"packet_owned_{mode}_unique", unique(packet_id))
            wait_ticks(client, 4)
            expected = "minecraft:gold_block" if mode == "after_send" else "minecraft:air"
            checks.check(f"packet_owned_{mode}_effect", block() == expected, block())
            clear()
        for mode, reason in (
            ("seh", "command_template_fault"),
            ("cpp", "command_template_fault"),
            ("long", "command_template_too_long"),
        ):
            seed(mode)
            queued = client.chat_send(command)
            result = complete(int(queued["packet_id"]))
            checks.check(
                f"packet_template_{mode}",
                result.get("ok") is False and result.get("reason") == reason,
                str(result),
            )
            checks.check(f"packet_template_{mode}_no_fallback", block() == "minecraft:air")
            seed()
            done = client.chat_send(command, wait=True)
            wait_ticks(client, 4)
            checks.check(
                f"packet_template_{mode}_recovers",
                done.get("ok") is True and block() == "minecraft:gold_block",
            )
            clear()
        accepted = inject("timeout", 10000)
        busy = client.request({"cmd": "chat_send", "text": "/say must_not_replace"}, "error")
        checks.check("packet_owned_busy", busy.get("reason") == "packet_inject_busy")
        client.panic("tasks")
        result = complete(int(accepted["packet_id"]))
        checks.check(
            "packet_owned_panic", result.get("reason") == "panic" and result.get("text") == command
        )
        checks.check("packet_owned_panic_unique", unique(int(accepted["packet_id"])))
        no_template("packet_owned_panic_template_invalid")
        seed()
        accepted = inject("timeout", 10000)
        client.request(
            {"cmd": "selftest_tick_error", "mode": "std", "phase": "pre"}, "tick_error_test_armed"
        )
        client.wait_event(
            "error",
            4,
            predicate=lambda row: (
                row.get("reason") == "internal_error" and row.get("phase") == "tick_pre"
            ),
        )
        result = complete(int(accepted["packet_id"]))
        checks.check("packet_owned_tick_cancel", result.get("reason") == "internal_error")
        recovered()
        no_template("packet_owned_tick_template_invalid")
        if world:
            seed()
            accepted = inject("timeout", 10000)
            client.close()
            client.connect()
            client.input_lock(True)
            no_template("packet_owned_reconnect_template_invalid")
            checks.check("packet_owned_reconnect_no_stale", unique(int(accepted["packet_id"])))
            seed()
            accepted = inject("timeout", 10000)
            try:
                client.leave_world()
                client.wait_event("world_left", 45)
                result = complete(int(accepted["packet_id"]))
                checks.check("packet_owned_world_cancel", result.get("reason") == "world_left")
                row = client.request({"cmd": "chat_send", "text": command}, "error")
                checks.check("packet_owned_menu_rejects", row.get("reason") == "not_in_world")
            finally:
                client.enter_world_ready(world)
                client.wait_in_world(timeout=60)
            no_template("packet_owned_world_template_invalid")
        seed()
        done = client.chat_send(command, wait=True)
        wait_ticks(client, 4)
        checks.check(
            "packet_owned_recovers", done.get("ok") is True and block() == "minecraft:gold_block"
        )
    finally:
        recovered()
        client.request(
            {"cmd": "selftest_packet_template", "mode": "off"}, "packet_template_test_armed"
        )
        clear()
        client.packet_capture(False)
    checks.check("packet_owned_fixture_restored", block() == "minecraft:air")
    checks.check(
        "packet_owned_alive", bool(client.ping()) and client.state().get("in_world") is True
    )
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("packet_owned_handled_fault_no_dump", before == after)


def check_teleport(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Verify server teleport ownership, partial failures and session isolation."""
    original = client.state()
    if not checks.check(
        "teleport_ready",
        original.get("in_world") is True and not original.get("effects"),
        "requires a world and no existing potion effects",
    ):
        return
    client.input_lock(True)
    client.set_input()
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    position = {axis: float(original[f"feet_{axis}"]) for axis in "xyz"}
    yaw, pitch = float(original["yaw"]), float(original["pitch"])
    checkpoint = f"selftest_teleport_{time.monotonic_ns()}"

    def complete(teleport_id: int) -> dict[str, Any]:
        return client.wait_event(
            ("teleported", "error"),
            4,
            predicate=lambda row: row.get("teleport_id") == teleport_id,
        )

    def unique(teleport_id: int) -> bool:
        try:
            client.wait_event(
                ("teleported", "error"),
                0.12,
                predicate=lambda row: row.get("teleport_id") == teleport_id,
            )
        except ModTimeout:
            return True
        return False

    def marker() -> None:
        client.command("effect @s speed 6000 0 true")
        wait_ticks(client, 2)
        if not any(int(effect.get("id", -1)) == 1 for effect in client.state().get("effects", [])):
            raise ValueError("teleport fixture failed to apply speed effect")

    def has_marker() -> bool:
        return any(int(effect.get("id", -1)) == 1 for effect in client.state().get("effects", []))

    def inject(mode: str, *, reset: bool = False, timeout: int = 250) -> dict[str, Any]:
        return {
            "cmd": "selftest_teleport",
            **position,
            "yaw": yaw + 35,
            "pitch": 0,
            "mode": mode,
            "reset": reset,
            "timeout_ms": timeout,
        }

    def restore_pose() -> dict[str, Any]:
        reply = client.teleport(**position, yaw=yaw, pitch=pitch)
        wait_ticks(client, 4)
        return reply

    try:
        marker()
        reply = restore_pose()
        checks.check(
            "teleport_server_result",
            reply.get("source") == "server" and reply.get("ok") is True,
            str(reply),
        )
        checks.check("teleport_preserves_effects", has_marker())
        client.teleport(**position, yaw=yaw + 25, pitch=15)
        wait_rotation(client, yaw + 25, 15)
        checks.check("teleport_rotation_settled", True)
        client.teleport(**position)  # Neither angle keeps both current values.
        wait_ticks(client, 4)
        state = client.state()
        checks.check(
            "teleport_keeps_rotation",
            abs((float(state["yaw"]) - yaw - 25 + 180) % 360 - 180) < 0.1
            and abs(float(state["pitch"]) - 15) < 0.1,
            f"yaw={float(state['yaw']):.3f} want={yaw + 25:.3f} "
            f"pitch={float(state['pitch']):.3f} want=15.000",
        )
        client.teleport(**position, yaw=yaw)
        wait_ticks(client, 4)
        checks.check("teleport_partial_rotation_zero", abs(float(client.state()["pitch"])) < 0.1)
        client.checkpoint_save(checkpoint)
        accepted = client.checkpoint_load(checkpoint)
        loaded = complete(int(accepted["teleport_id"]))
        checks.check("teleport_checkpoint_complete", loaded.get("ok") is True and has_marker())
        for mode, reason, reset in (
            ("seh", "teleport_fault", False),
            ("cpp", "teleport_failed", False),
            ("timeout", "teleport_timeout", False),
            ("queue", "server_task_queue_failed", False),
            ("stale_epoch", "world_left", False),
            ("after_effects", "teleport_fault", True),
            ("after_teleport", "teleport_fault", False),
        ):
            restore_pose()
            marker()
            accepted = client.request(inject(mode, reset=reset), ("teleport_queued", "reset_done"))
            teleport_id = int(accepted["teleport_id"])
            result = complete(teleport_id)
            checks.check(
                f"teleport_{mode}",
                result.get("phase") == "teleport"
                and result.get("reason") == reason
                and result.get("partial") is True,
                str(result),
            )
            checks.check(f"teleport_{mode}_unique", unique(teleport_id))
            wait_ticks(client, 4)
            state = client.state()
            expected_yaw = yaw + 35 if mode == "after_teleport" else yaw
            checks.check(
                f"teleport_{mode}_side_effects",
                has_marker() == (mode != "after_effects")
                and abs((float(state["yaw"]) - expected_yaw + 180) % 360 - 180) < 0.1,
            )
            checks.check(f"teleport_{mode}_recovery", restore_pose().get("ok") is True)

        marker()
        accepted = client.request(inject("timeout", timeout=1500), "teleport_queued")
        rejected = client.request({"cmd": "reset", **position}, "error")
        checks.check(
            "teleport_busy_reset", rejected.get("reason") == "teleport_busy" and has_marker()
        )
        rejected = client.request({"cmd": "checkpoint_load", "name": checkpoint}, "error")
        checks.check("teleport_busy_checkpoint", rejected.get("reason") == "teleport_busy")
        rejected = client.request({"cmd": "teleport", **position, "x": "invalid"}, "error")
        checks.check("teleport_bad_param_preserves", rejected.get("reason") == "bad_param")
        result = complete(int(accepted["teleport_id"]))
        checks.check("teleport_busy_original", result.get("reason") == "teleport_timeout")
        accepted = client.reset(**position, yaw=yaw, pitch=pitch)
        result = complete(int(accepted["teleport_id"]))
        wait_ticks(client, 4)
        checks.check(
            "teleport_reset_clears_effects",
            result.get("effects_cleared") is True and not client.state().get("effects"),
        )
        accepted = client.request(inject("timeout", timeout=10000), "teleport_queued")
        client.panic("tasks")
        result = complete(int(accepted["teleport_id"]))
        checks.check("teleport_panic_cancels", result.get("reason") == "panic")
        checks.check("teleport_panic_recovers", restore_pose().get("ok") is True)
        if world:
            accepted = client.request(inject("timeout", timeout=10000), "teleport_queued")
            client.close()
            client.connect()
            client.input_lock(True)
            checks.check("teleport_reconnect_fresh", restore_pose().get("ok") is True)
            checks.check("teleport_reconnect_no_stale", unique(int(accepted["teleport_id"])))
            accepted = client.request(inject("timeout", timeout=10000), "teleport_queued")
            try:
                client.leave_world()
                client.wait_event("world_left", 45)
                result = complete(int(accepted["teleport_id"]))
                checks.check("teleport_world_cancel", result.get("reason") == "world_left")
                rejected = client.request({"cmd": "teleport", **position}, "error")
                checks.check("teleport_menu_rejects", rejected.get("reason") == "not_in_world")
            finally:
                with preserve_failure(checks, "teleport"):
                    client.enter_world_ready(world)
                    client.wait_in_world(timeout=60)
            checks.check("teleport_world_recovers", restore_pose().get("ok") is True)
    finally:
        with preserve_failure(checks, "teleport"):
            client.command("effect @s clear")
            client.checkpoint_delete(checkpoint)
            restore_pose()
    restored = client.state()
    checks.check(
        "teleport_pose_restored",
        all(abs(float(restored[f"feet_{axis}"]) - position[axis]) < 0.1 for axis in "xyz")
        and abs((float(restored["yaw"]) - yaw + 180) % 360 - 180) < 0.1
        and abs(float(restored["pitch"]) - pitch) < 0.1,
    )
    checks.check("teleport_alive", bool(client.ping()) and restored.get("in_world") is True)
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("teleport_handled_fault_no_dump", after == before)


def check_flight(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Verify staged abilities, bounded tick assertion, failures and retirement."""
    original = client.state()
    if not checks.check("flight_in_world", original.get("in_world") is True):
        return
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    client.input_lock(True)
    # Both natural disconnects happened inside this check. When a violation dump
    # is requested, also serialize SetActorData(39): the first bytes separate a
    # serialization-mode mismatch from a payload that was written short.
    bytes_dump = os.environ.get("LEVIPILOT_VIOLATION_DUMP") or None
    bytes_armed = False
    if bytes_dump:
        try:
            # Observed violations were inbound bodies that ran short: 39
            # (SetActorData), 111 (MoveDeltaActor), 188 (UpdateAdventureSettings).
            # 187 (UpdateAbilities) is what the flight checks provoke, and a
            # mis-framed body there would desync every following packet -- so
            # keep the whole identity ring and serialize all four suspects.
            client.packet_capture(
                True,
                serialize_ids=[39, 111, 187, 188],
                directions=["send", "recv"],
            )
            bytes_armed = True
        except (ModPipeError, ModProtocolError) as exc:
            checks.note("flight_bytes_capture", f"unavailable: {exc}")

    def inject(stage: str, mode: str, timeout: int = 250) -> dict[str, Any]:
        return {
            "cmd": "selftest_flight",
            "stage": stage,
            "mode": mode,
            "on": False,
            "timeout_ms": timeout,
        }

    def unique(flight_id: int) -> bool:
        try:
            client.wait_event(
                ("error", "fly_ok", "togglefly_ok", "flight_assertion_failed"),
                0.15,
                predicate=lambda row: row.get("flight_id") == flight_id,
            )
        except ModTimeout:
            return True
        return False

    def off() -> bool:
        reply = client.fly(False)
        wait_ticks(client, 2)
        state = client.state()
        return (
            reply.get("server_applied") is True
            and not state.get("mayfly")
            and not state.get("flying")
        )

    try:
        client.command("gamemode survival @s")
        wait_ticks(client, 8)
        checks.check("flight_disable_applies_both", off())
        client.teleport(
            float(original["feet_x"]),
            float(original["feet_y"]) + 6,
            float(original["feet_z"]),
        )
        air_ready = settle(
            client, on_ground=False, still=None, ticks=1, timeout=3, clear_input=False
        )
        airborne = air_ready["state"]
        checks.check(
            "flight_air_fixture",
            air_ready["ok"] and float(airborne["feet_y"]) > float(original["feet_y"]) + 3,
            f"on_ground={airborne.get('on_ground')}, y={airborne.get('feet_y')}",
        )
        enabled = client.fly(True)
        wait_ticks(client, 3)
        flying = client.state()
        checks.check(
            "flight_enable_applies_both",
            enabled.get("client_applied") is True
            and enabled.get("server_applied") is True
            and flying.get("flying") is True
            and flying.get("mayfly") is True
            and flying.get("on_ground") is False,
            str(enabled),
        )
        checks.check("flight_enable_off_recovers", off())
        client.teleport(*(float(original[f"feet_{axis}"]) for axis in "xyz"))
        landed = settle(client, on_ground=True)
        checks.check("flight_air_returns_ground", landed["ok"], str(landed))
        enabled = client.toggle_fly(True)
        checks.check(
            "flight_toggle_server",
            enabled.get("server_applied") is True and enabled.get("mayfly") is True,
        )
        wait_ticks(client, 12)
        checks.check("flight_eight_tick_persists", client.state().get("mayfly") is True)
        checks.check("flight_invert_off", client.toggle_fly().get("mayfly") is False)
        checks.check("flight_invert_on", client.toggle_fly().get("mayfly") is True)
        checks.check("flight_toggle_stop", off())
        for stage in ("client", "server"):
            for mode, reason in (
                ("seh", "flight_fault"),
                ("cpp", "capture_failed"),
                ("timeout", "capture_timeout"),
            ):
                failed = client.request(inject(stage, mode), "error", timeout=4)
                checks.check(
                    f"flight_{stage}_{mode}",
                    failed.get("phase") == "flight"
                    and failed.get("reason") == reason
                    and failed.get("partial") is True,
                    str(failed),
                )
                checks.check(f"flight_{stage}_{mode}_unique", unique(int(failed["flight_id"])))
                checks.check(f"flight_{stage}_{mode}_recovery", off())
        for mode, reason in (("seh", "flight_assert_fault"), ("cpp", "flight_assert_failed")):
            queued = client.request(inject("assert", mode, 2000), "flight_queued")
            flight_id = int(queued["flight_id"])
            result = client.wait_event(
                ("error", "togglefly_ok", "flight_assertion_failed"),
                4,
                predicate=lambda row, wanted=flight_id: row.get("flight_id") == wanted,
            )
            if result.get("event") == "togglefly_ok":
                result = client.wait_event(
                    "flight_assertion_failed",
                    3,
                    predicate=lambda row, wanted=flight_id: row.get("flight_id") == wanted,
                )
            checks.check(f"flight_assert_{mode}", result.get("reason") == reason, str(result))
            checks.check(f"flight_assert_{mode}_unique", unique(flight_id))
            checks.check(f"flight_assert_{mode}_recovery", off())

        queued = client.request(inject("server", "timeout", 1000), "flight_queued")
        busy = client.request({"cmd": "fly", "enabled": True}, "error")
        checks.check("flight_busy_preserves_request", busy.get("reason") == "flight_busy")
        malformed = client.request({"cmd": "togglefly", "on": []}, "error")
        checks.check("flight_bad_param_preserves_request", malformed.get("reason") == "bad_command")
        failed = client.wait_event("error", 3, predicate=lambda row: row.get("phase") == "flight")
        checks.check(
            "flight_original_timeout",
            failed.get("flight_id") == queued["flight_id"]
            and failed.get("reason") == "capture_timeout",
        )
        checks.check("flight_busy_recovers", off())
        client.command("gamemode spectator @s")
        wait_ticks(client, 8)
        rejected = client.request({"cmd": "togglefly"}, "error")
        checks.check(
            "flight_spectator_rejects", rejected.get("reason") == "spectator_not_supported"
        )
        client.command("gamemode survival @s")
        wait_ticks(client, 8)
        checks.check("flight_spectator_recovers", off())

        if world:
            queued = client.request(inject("client", "timeout", 10000), "flight_queued")
            client.close()
            client.connect()
            client.input_lock(True)
            checks.check("flight_reconnect_fresh", off())
            checks.check("flight_reconnect_no_stale", unique(int(queued["flight_id"])))
            queued = client.request(inject("server", "timeout", 10000), "flight_queued")
            wait_ticks(client, 3)
            try:
                client.leave_world()
                client.wait_event("world_left", 45)
                cancelled = client.wait_event(
                    "error", 4, predicate=lambda row: row.get("phase") == "flight"
                )
                checks.check(
                    "flight_world_cancel",
                    cancelled.get("reason") == "world_left"
                    and cancelled.get("flight_id") == queued["flight_id"],
                    str(cancelled),
                )
                for command in ("fly", "togglefly"):
                    rejected = client.request({"cmd": command}, "error")
                    checks.check(f"flight_menu_{command}", rejected.get("reason") == "not_in_world")
            finally:
                client.enter_world_ready(world)
                client.wait_in_world(timeout=60)
            checks.check("flight_world_recovers", off())
    finally:
        if bytes_armed:
            # Diagnostic export only: never let it replace the first failure.
            try:
                rows = client.packet_bytes(0, 256)
                Path(f"{bytes_dump}.bytes.json").write_text(
                    json.dumps(rows, ensure_ascii=False), encoding="utf-8"
                )
                # seq -> tick/id mapping, so a violation tick can be matched to
                # the exact packet the sender serialized.
                # The game's working directory differs from the driver's. Wait
                # for this receipt so an export error cannot poison packet_drain.
                inbound_path = Path(f"{bytes_dump}.inbound.json").resolve()
                client.request({"cmd": "inbound_dump", "path": str(inbound_path)}, "inbound_dump")
                index = client.packet_drain(0, 1024)
                Path(f"{bytes_dump}.drain.json").write_text(
                    json.dumps(index, ensure_ascii=False), encoding="utf-8"
                )
                checks.note(
                    "flight_bytes_dump",
                    f"{rows.get('count', 0)} rows, "
                    f"{index.get('count', 0)} index -> {bytes_dump}.bytes.json",
                )
            except (ModPipeError, ModProtocolError, OSError) as exc:
                checks.note("flight_bytes_dump", f"failed: {exc}")
            with contextlib.suppress(ModPipeError, ModProtocolError):
                client.packet_capture(False)
        with preserve_failure(checks, "flight"):
            client.command(f"gamemode {original['gamemode']} @s")
            wait_ticks(client, 8)
            client.fly(bool(original.get("flying")))
            if int(original["gamemode"]) != 3:
                client.toggle_fly(bool(original.get("mayfly")))
            client.reset(
                *(float(original[f"feet_{axis}"]) for axis in "xyz"),
                yaw=float(original["yaw"]),
                pitch=float(original["pitch"]),
            )
            wait_ticks(client, 10)
            # The cleanup reset is the last thing this check does; if the level
            # is gone now, stop here so the disconnect keeps its diagnostics.
            require_world(client, "flight cleanup")
    restored = client.state()
    checks.check(
        "flight_restores_abilities",
        restored.get("mayfly") == original.get("mayfly")
        and restored.get("flying") == original.get("flying"),
    )
    checks.check("flight_tick_alive", bool(client.ping()) and restored.get("in_world") is True)
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("flight_handled_fault_no_dump", after == before)


def check_input_events(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Exercise production event failures, text ordering, and UI thread queries."""
    if not checks.check("input_events_in_world", client.state().get("in_world") is True):
        return
    client.input_lock(True)
    if not checks.check(
        "input_events_locked", client.state().get("input", {}).get("locked") is True
    ):
        return
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}

    def inject(target: str, mode: str) -> dict[str, Any]:
        return {"cmd": "selftest_input_events", "target": target, "mode": mode}

    def event_done(input_id: int) -> dict[str, Any]:
        return client.wait_event(
            "input_event_done", 4, predicate=lambda row: row.get("input_id") == input_id
        )

    def text_done(input_id: int) -> dict[str, Any]:
        return client.wait_event(
            "text_input_done", 4, predicate=lambda row: row.get("input_id") == input_id
        )

    def unique(event: str, input_id: int) -> bool:
        try:
            client.wait_event(event, 0.12, predicate=lambda row: row.get("input_id") == input_id)
        except ModTimeout:
            return True
        return False

    checks.check("input_ui_screen", client.screen().get("name") == "hud_screen")
    for mode, reason in (("seh", "input_fault"), ("cpp", "input_failed")):
        queued = client.request(inject("key", mode), "key_event_queued")
        done = event_done(int(queued["input_id"]))
        checks.check(f"input_key_{mode}", done.get("ok") is False and done.get("reason") == reason)
        checks.check(
            f"input_key_{mode}_unique", unique("input_event_done", int(queued["input_id"]))
        )
        good = client.key_event(72, False, via="hardware")
        checks.check(
            f"input_key_{mode}_recovery", event_done(int(good["input_id"])).get("ok") is True
        )
        count = int(client.key_state(72)["events"])
        raw = client.key_event(72, True, via="raw")
        event_done(int(raw["input_id"]))
        checks.check(
            f"input_key_{mode}_guard_restored", int(client.key_state(72)["events"]) == count
        )
        client.key_event(72, False, via="raw")

    for mode, reason in (
        ("seh", "ui_query_fault"),
        ("cpp", "capture_failed"),
        ("timeout", "capture_timeout"),
    ):
        client.send(inject("screen", mode))
        if mode == "timeout":
            client.ping()  # Dispatch fence: query is occupied before submitting again.
            busy = client.request({"cmd": "screen"}, "error")
            checks.check("input_ui_busy", busy.get("reason") == "ui_query_busy")
        failed = client.wait_event("error", 4, predicate=lambda row: row.get("phase") == "ui_query")
        checks.check(f"input_ui_{mode}", failed.get("reason") == reason, str(failed))
        checks.check(f"input_ui_{mode}_recovery", client.screen().get("name") == "hud_screen")

    for mode, reason in (
        ("seh", "text_input_fault"),
        ("cpp", "capture_failed"),
        ("timeout", "capture_timeout"),
    ):
        queued = client.request(inject("text", mode), "text_input_queued")
        if mode == "timeout":
            busy = client.request({"cmd": "text_input", "text": "must_not_replace"}, "error")
            checks.check(
                "input_text_busy_preserves_request", busy.get("reason") == "text_input_busy"
            )
            following = client.key_event(72, False)
            try:
                client.wait_event(
                    "input_event_done",
                    0.12,
                    predicate=lambda row, input_id=following["input_id"]: (
                        row.get("input_id") == input_id
                    ),
                )
                blocked = False
            except ModTimeout:
                blocked = True
            checks.check("input_text_blocks_following_keys", blocked)
            checks.check("input_text_ui_independent", client.screen().get("name") == "hud_screen")
        failed = text_done(int(queued["input_id"]))
        checks.check(
            f"input_text_{mode}",
            failed.get("ok") is False and failed.get("reason") == reason,
            str(failed),
        )
        checks.check(
            f"input_text_{mode}_unique", unique("text_input_done", int(queued["input_id"]))
        )
        if mode == "timeout":
            cancelled = event_done(int(following["input_id"]))
            checks.check(
                "input_text_failure_cancels_following",
                cancelled.get("ok") is False and cancelled.get("reason") == "input_cancelled",
                str(cancelled),
            )
        good = client.key_event(72, False)
        checks.check(
            f"input_text_{mode}_recovery", event_done(int(good["input_id"])).get("ok") is True
        )

    # One deferred text owns a slot. Another 127 events fit, and the next one
    # must reject explicitly instead of losing an old event or its result.
    queued = client.request(inject("text", "timeout"), "text_input_queued")
    wire = (json.dumps({"cmd": "key_event", "code": 72, "down": False}) + "\n") * 128
    client.send_raw(wire.encode())
    accepted: list[int] = []
    rejected = 0
    for _ in range(128):
        row = client.wait_event(("key_event_queued", "error"), 4)
        if row["event"] == "key_event_queued":
            accepted.append(int(row["input_id"]))
        elif row.get("reason") == "inject_queue_full":
            rejected += 1
    checks.check(
        "input_event_bounded_capacity",
        len(accepted) == 127 and rejected == 1,
        f"accepted={len(accepted)} rejected={rejected}",
    )
    checks.check(
        "input_event_full_text_timeout",
        text_done(int(queued["input_id"])).get("reason") == "capture_timeout",
    )
    checks.check(
        "input_event_full_cancellation",
        all(event_done(input_id).get("reason") == "input_cancelled" for input_id in accepted),
    )

    # No sleep between text and Enter: verify actual command execution through
    # the server's chat reply, rather than only checking that chat closed.
    token = f"input_order_{time.monotonic_ns()}"
    try:
        client.key_event(191)
        client.key_event(191, False)
        checks.check("input_order_chat_open", wait_screen(client, "chat_screen") == "chat_screen")
        failed_text = client.request(inject("text", "timeout"), "text_input_queued")
        cancelled_enter = client.key_event(13)
        cancelled_up = client.key_event(13, False)
        checks.check(
            "input_failed_text_terminal",
            text_done(int(failed_text["input_id"])).get("reason") == "capture_timeout",
        )
        checks.check(
            "input_failed_text_cancels_enter",
            event_done(int(cancelled_enter["input_id"])).get("reason") == "input_cancelled"
            and event_done(int(cancelled_up["input_id"])).get("reason") == "input_cancelled",
        )
        checks.check(
            "input_failed_text_keeps_chat_open", client.screen().get("name") == "chat_screen"
        )
        queued = client.text_input(f"say {token}")
        enter = client.key_event(13)
        client.key_event(13, False)
        checks.check("input_order_text_done", text_done(int(queued["input_id"])).get("ok") is True)
        checks.check("input_order_enter_done", event_done(int(enter["input_id"])).get("ok") is True)
        checks.check("input_order_chat_closed", wait_screen(client, "hud_screen") == "hud_screen")
        history = client.chat_history(limit=64)
        checks.check(
            "input_order_command_executed", token in json.dumps(history, ensure_ascii=False)
        )
    finally:
        if client.screen().get("name") == "chat_screen":
            client.key_event(27)
            client.key_event(27, False)
            wait_screen(client, "hud_screen")

    if world:
        client.key_event(72, True, via="hardware")
        wait_ticks(client, 2)
        client.close()
        client.connect()
        client.input_lock(True)
        wait_ticks(client, 3)
        checks.check("input_reconnect_releases_held", client.key_state(72).get("down") is False)
        queued = client.request(inject("text", "timeout"), "text_input_queued")
        client.key_event(72, True)
        try:
            client.leave_world()
            client.wait_event("world_left", 45)
            done = text_done(int(queued["input_id"]))
            checks.check("input_world_cancels_text", done.get("reason") == "world_left", str(done))
            checks.check("input_menu_screen_responsive", bool(client.screen().get("name")))
            rejected = client.request({"cmd": "key_state", "code": 72}, "error")
            checks.check("input_menu_key_rejected", rejected.get("reason") == "not_in_world")
        finally:
            client.enter_world_ready(world)
            client.wait_in_world(timeout=60)
            checks.check("input_world_still", settle(client, on_ground=True)["ok"])
        checks.check("input_world_no_stale_key", client.key_state(72).get("down") is False)
        checks.check("input_world_screen_recovery", client.screen().get("name") == "hud_screen")

    checks.check("input_tick_alive", bool(client.ping()) and client.state().get("in_world") is True)
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("input_handled_fault_no_dump", after == before)


def check_ecs_capture(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Read both registries through owned stages, then fault and retire requests."""
    original = client.state()
    if not checks.check("ecs_capture_in_world", original.get("in_world") is True):
        return
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    runtime_id = int(original["runtime_id"])

    def unique() -> bool:
        with contextlib.suppress(ModTimeout):
            client.wait_event(
                (
                    "error",
                    "ecs_registries",
                    "ecs_entity",
                    "ecs_components",
                    "ecs_component",
                    "ecs_context",
                ),
                0.15,
                predicate=lambda row: row.get("event") != "error" or row.get("phase") == "ecs",
            )
            return False
        return True

    def inject(stage: str, mode: str, timeout_ms: int = 250) -> dict[str, Any]:
        return {
            "cmd": "selftest_query_failure",
            "target": "ecs",
            "stage": stage,
            "mode": mode,
            "timeout_ms": timeout_ms,
            "runtime_id": runtime_id,
        }

    registries = client.ecs_registries()
    checks.check(
        "ecs_client_registry",
        registries.get("scope") == "client"
        and registries.get("truncated") is False
        and int(registries["registries"][0]["components"]) > 0,
    )
    entity = client.ecs_entity(runtime_id)
    checks.check(
        "ecs_server_entity",
        entity.get("scope") == "server" and int(entity.get("components", 0)) > 0,
    )
    listing = client.ecs_components(runtime_id)
    rows = listing["components"]
    checks.check(
        "ecs_bounded_component_rows",
        listing.get("scope") == "server"
        and len(rows) <= 1024
        and int(listing.get("visited", 0)) <= 4096
        and listing.get("truncated") is False,
        f"rows={len(rows)} skipped={listing.get('skipped')} visited={listing.get('visited')}",
    )
    state_vector = next((row for row in rows if row.get("name") == "StateVector"), None)
    if not checks.check("ecs_known_component", state_vector is not None):
        return
    assert state_vector is not None
    component_id = int(state_vector["id"])
    client_view = client.ecs_context(int(original["context"]), component_id)
    server_view = client.ecs_context(int(entity["context"]), component_id)
    checks.check(
        "ecs_client_context_scope",
        client_view.get("scope") == "client"
        and client_view.get("registry") == registries["registries"][0]["registry"],
    )
    checks.check(
        "ecs_server_context_scope",
        server_view.get("scope") == "server"
        and server_view.get("registry") == entity["registry"]
        and server_view.get("address") == state_vector["address"],
    )
    for length, expected in ((0, int(state_vector["size"])), (1, 1), (512, 512), (999, 512)):
        raw = client.ecs_component(runtime_id, component_id, length=length)
        checks.check(
            f"ecs_component_length_{length}",
            raw.get("ok") is True
            and raw.get("len") == expected
            and len(raw.get("hex", "")) == expected * 2,
        )
    for name, request, reason in (
        ("missing_entity", {"cmd": "ecs_entity", "runtime_id": (1 << 63) - 1}, "entity_not_found"),
        (
            "missing_component",
            {
                "cmd": "ecs_component",
                "runtime_id": runtime_id,
                "component_id": next(i for i in range(100) if i not in {r["id"] for r in rows}),
            },
            "component_not_found",
        ),
        (
            "bad_context",
            {"cmd": "ecs_context", "context": "0x41410000", "component_id": component_id},
            "bad_addr",
        ),
    ):
        rejected = client.request(request, "error")
        checks.check(f"ecs_{name}", rejected.get("reason") == reason, str(rejected))

    for stage in ("client", "server"):
        for mode, reason in (
            ("seh", "ecs_fault"),
            ("cpp", "capture_failed"),
            ("timeout", "capture_timeout"),
        ):
            failed = client.request(inject(stage, mode), "error", timeout=5)
            if not checks.check(
                f"ecs_{stage}_{mode}_terminal",
                failed.get("phase") == "ecs" and failed.get("reason") == reason,
                str(failed),
            ):
                return
            checks.check(f"ecs_{stage}_{mode}_unique", unique())
            checks.check(
                f"ecs_{stage}_{mode}_recovery",
                client.ecs_entity(runtime_id).get("scope") == "server",
            )

    partial = client.request(inject("server", "partial"), "ecs_components")
    checks.check(
        "ecs_storage_fault_preserves_rows",
        partial.get("partial") is True
        and len(partial.get("skipped", [])) == 1
        and len(partial["components"]) > 0,
    )
    recovered = client.ecs_components(runtime_id)
    checks.check(
        "ecs_storage_fault_recovers",
        recovered.get("skipped") == listing.get("skipped")
        and component_id in {row["id"] for row in recovered["components"]},
        # Transient ECS tags can disappear between these requests; compare
        # failures and the stable profile component, not every gameplay tag.
        str({"skipped": recovered.get("skipped"), "components": len(recovered["components"])}),
    )
    with client.exchange():
        client.send(inject("client", "timeout", 500))
        rejected = client.request({"cmd": "ecs_registries"}, "error")
        checks.check("ecs_busy_preserves_request", rejected.get("reason") == "ecs_busy")
        malformed = client.request({"cmd": "ecs_component", "len": "bad"}, "error")
        checks.check(
            "ecs_bad_param_preserves_request",
            malformed.get("reason") == "bad_command",
            str(malformed),
        )
        failed = client.wait_event("error", 3, predicate=lambda row: row.get("phase") == "ecs")
        checks.check("ecs_original_timeout", failed.get("reason") == "capture_timeout")
    checks.check("ecs_busy_recovers", bool(client.ecs_registries().get("registries")))

    if world:
        client.send(inject("server", "timeout", 10000))
        client.ping()
        client.close()
        client.connect()
        client.input_lock(True)
        checks.check("ecs_reconnect_fresh", bool(client.ecs_registries().get("registries")))
        checks.check("ecs_reconnect_no_old_result", unique())
        client.send(inject("server", "timeout", 10000))
        client.ping()
        wait_ticks(client, 3)
        try:
            client.leave_world()
            client.wait_event("world_left", 45)
            failed = client.wait_event("error", 4, predicate=lambda row: row.get("phase") == "ecs")
            checks.check("ecs_world_cancel", failed.get("reason") == "world_left", str(failed))
            checks.check("ecs_world_unique", unique())
            for command in (
                "ecs_registries",
                "ecs_entity",
                "ecs_components",
                "ecs_component",
                "ecs_context",
            ):
                rejected = client.request({"cmd": command}, "error")
                checks.check(f"ecs_menu_{command}", rejected.get("reason") == "not_in_world")
        finally:
            client.enter_world_ready(world)
            client.wait_in_world(timeout=60)
            restored = settle(client, on_ground=True)
            checks.check("ecs_world_still", restored["ok"])
        fresh_state = client.state()
        client.ecs_entity(int(fresh_state["runtime_id"]))
        fresh = client.ecs_context(int(fresh_state["context"]), component_id)
        checks.check("ecs_world_fresh_context", fresh.get("scope") == "client")
        stale = client.request(
            {"cmd": "ecs_context", "context": original["context"], "component_id": component_id},
            ("ecs_context", "error"),
        )
        # Addresses may be recycled; a successful lookup must still prove a current registry.
        checks.check(
            "ecs_stale_handle_safe",
            stale.get("event") == "error" or stale.get("scope") in ("client", "server"),
        )

    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("ecs_handled_fault_no_dump", after == before)


def check_macro_playback(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Exercise real replay faults, release/restart, and cold frame replacement."""
    original = client.state()
    if not checks.check("playback_in_world", original.get("in_world") is True):
        return
    name = f"selftest_playback_{time.monotonic_ns()}"
    mod_dir = Path(str(client.status()["spool"])).parent
    path = mod_dir / "macros" / f"{name}.json"
    frame = ["true", *["false"] * 8]
    empty = ["false"] * 9

    def done(started: dict[str, Any]) -> dict[str, Any]:
        return client.wait_event(
            "macro_done", 4, predicate=lambda row: row.get("run_id") == started["run_id"]
        )

    def no_duplicate(started: dict[str, Any]) -> bool:
        try:
            client.wait_event(
                "macro_done", 0.2, predicate=lambda row: row.get("run_id") == started["run_id"]
            )
        except ModTimeout:
            return True
        return False

    def released() -> bool:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not client.macro_status().get("release_pending"):
                inputs = client.state()["input"]
                if not any(inputs.get(key) for key in ("w", "a", "s", "d", "jump", "sneak")):
                    return True
            time.sleep(0.02)
        return False

    try:
        client.stop_move()
        client.task_stop()
        client.macro_stop()
        client.macro_save(name, [empty] * 5)
        client.macro_load(name)
        rejected = client.request({"cmd": "selftest_macro_playback", "mode": "seh"}, "error")
        if not checks.check(
            "playback_fault_gate", rejected.get("reason") == "not_playing", str(rejected)
        ):
            return
        started = client.macro_run(start=1, end=4, event_level=True)
        finished = done(started)
        checks.check(
            "playback_range_terminal",
            finished.get("ok") is True
            and finished.get("reason") == "end"
            and finished.get("index") == 4
            and finished.get("name") == name,
            str(finished),
        )
        checks.check("playback_range_unique", no_duplicate(started))
        for events in (False, True):
            for mode, reason in (("seh", "playback_fault"), ("cpp", "playback_failed")):
                client.macro_save(name, [frame] * 4)
                client.macro_load(name)
                started = client.macro_run(loop=True, event_level=events)
                wait_ticks(client, 3)
                checks.check(
                    f"playback_{events}_{mode}_held", client.state()["input"].get("w") is True
                )
                client.request(
                    {"cmd": "selftest_macro_playback", "mode": mode}, "macro_playback_test_armed"
                )
                finished = done(started)
                checks.check(
                    f"playback_{events}_{mode}_terminal",
                    finished.get("ok") is False and finished.get("reason") == reason,
                    str(finished),
                )
                checks.check(f"playback_{events}_{mode}_released", released())
                checks.check(f"playback_{events}_{mode}_unique", no_duplicate(started))
                again = client.macro_run(event_level=events)
                checks.check(f"playback_{events}_{mode}_recovery", done(again).get("ok") is True)

        for mode in ("seh", "cpp"):
            started = client.macro_run(loop=True, event_level=True)
            wait_ticks(client, 3)
            client.request(
                {"cmd": "selftest_macro_playback", "target": "release", "mode": mode},
                "macro_playback_test_armed",
            )
            client.macro_stop()
            diagnostic = client.wait_event("macro_release_diagnostic", 3)
            checks.check(
                f"playback_release_{mode}_visible",
                int(diagnostic.get("faults" if mode == "seh" else "exceptions", 0)) >= 1,
                str(diagnostic),
            )
            checks.check(f"playback_release_{mode}_retry", released())
            again = client.macro_run(event_level=True)
            checks.check(f"playback_release_{mode}_recovery", done(again).get("ok") is True)

        started = client.macro_run(loop=True, event_level=True)
        wait_ticks(client, 3)
        rejected = client.request({"cmd": "macro_run", "loop": True}, "error")
        checks.check(
            "playback_busy_preserves_run",
            rejected.get("reason") == "macro_already_playing"
            and client.macro_status().get("run_id") == started["run_id"],
        )
        rejected = client.request({"cmd": "macro_run", "start": "bad"}, "error")
        checks.check(
            "playback_bad_param_preserves_run",
            rejected.get("event") == "error" and client.macro_status().get("playing") is True,
        )
        client.macro_save(name, [empty])
        loaded = client.macro_load(name)
        checks.check(
            "playback_load_shorter_stops",
            loaded.get("stopped") is True
            and loaded.get("ticks") == 1
            and not client.macro_status().get("playing"),
        )
        checks.check("playback_load_releases", released())
        for index in range(3):
            started = client.macro_run(event_level=True)
            checks.check(f"playback_short_restart_{index}", done(started).get("index") == 1)

        client.macro_save(name, [frame] * 4)
        client.macro_load(name)
        started = client.macro_run(loop=True, event_level=True)
        wait_ticks(client, 3)
        position = client.state()
        task = client.navigate(float(position["feet_x"]), float(position["feet_z"]), tolerance=1)
        checks.check(
            "playback_task_takes_ownership",
            task.get("ok") is True and client.macro_status().get("playing") is False,
        )
        checks.check("playback_task_releases", released())

        # Direct clicks retain their reply fields after formatting moved off tick.
        client.look(yaw=0, pitch=-80, relative=False)
        wait_ticks(client, 2)
        clicked = client.click("left")
        checks.check(
            "playback_click_cold_receipt",
            clicked.get("ok") is True
            and clicked.get("button") == "left"
            and clicked.get("game_mode") is True
            and all(key in clicked for key in ("hit_type", "block", "face", "hit_pos")),
            str(clicked),
        )
        for mode, reason in (("seh", "click_fault"), ("cpp", "click_failed")):
            client.request(
                {"cmd": "selftest_click_capture", "mode": mode}, "click_capture_test_armed"
            )
            failed = client.click("left")
            checks.check(
                f"playback_click_{mode}_isolated",
                failed.get("ok") is False and failed.get("reason") == reason,
                str(failed),
            )
            checks.check(f"playback_click_{mode}_recovery", client.click("left").get("ok") is True)
        click_frame = [*empty[:-1], "true"]
        client.macro_save(name, [click_frame, empty, empty])
        client.macro_load(name)
        started = client.macro_run(event_level=False)
        clicked = client.wait_event("click_result", 3)
        checks.check(
            "playback_legacy_click_receipt",
            clicked.get("ok") is True
            and clicked.get("button") == "left"
            and done(started).get("ok") is True,
        )

        for mode, reason in (("seh", "release_fault"), ("cpp", "release_failed")):
            client.macro_save(name, [frame] * 20)
            client.macro_load(name)
            started = client.macro_run(event_level=True)
            wait_ticks(client, 3)
            client.request(
                {"cmd": "selftest_macro_playback", "target": "release", "mode": mode},
                "macro_playback_test_armed",
            )
            finished = done(started)
            checks.check(
                f"playback_end_release_{mode}_terminal",
                finished.get("ok") is False and finished.get("reason") == reason,
                str(finished),
            )
            client.wait_event("macro_release_diagnostic", 3)
            checks.check(f"playback_end_release_{mode}_retry", released())
            checks.check(f"playback_end_release_{mode}_unique", no_duplicate(started))

        if world:
            started = client.macro_run(loop=True, event_level=True)
            try:
                wait_ticks(client, 3)
                client.leave_world()
                client.wait_event("world_left", 45)
                finished = done(started)
                checks.check(
                    "playback_world_cancel",
                    finished.get("ok") is False and finished.get("reason") == "world_left",
                    str(finished),
                )
                checks.check("playback_world_unique", no_duplicate(started))
                deadline = time.monotonic() + 45
                while not client.worlds().get("ready"):
                    if time.monotonic() >= deadline:
                        raise ModTimeout("macro playback: menu did not become ready")
                    time.sleep(0.1)
                rejected = client.request({"cmd": "macro_run"}, "error")
                checks.check("playback_menu_rejects", rejected.get("reason") == "not_in_world")
            finally:
                client.enter_world_ready(world)
                client.wait_in_world(timeout=60)
            again = client.macro_run(event_level=True)
            checks.check("playback_world_recovery", done(again).get("ok") is True)
    finally:
        client.macro_stop()
        client.stop_move()
        client.reset(
            *(float(original[f"feet_{axis}"]) for axis in "xyz"),
            yaw=float(original["yaw"]),
            pitch=float(original["pitch"]),
        )
        wait_ticks(client, 5)
        checks.check("playback_final_settle", settle(client, on_ground=True)["ok"])
        path.unlink(missing_ok=True)


def check_binary_capture(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """A failed binary session must stop, retain rows and stay visibly failed."""
    original = client.state()
    if not checks.check("binary_capture_in_world", original.get("in_world") is True):
        return
    if not checks.check("binary_capture_idle", client.status().get("record_active") is False):
        return

    def terminal(record_id: int) -> dict[str, Any]:
        return client.wait_event(
            "record_failed", 3, predicate=lambda row: row.get("record_id") == record_id
        )

    def rejects(operation: str, reason: str) -> bool:
        try:
            getattr(client, operation)()
        except ModProtocolError as error:
            return reason in str(error)
        return False

    def no_duplicate(record_id: int) -> bool:
        try:
            client.wait_event(
                "record_failed", 0.2, predicate=lambda row: row.get("record_id") == record_id
            )
        except ModTimeout:
            return True
        return False

    rejected = client.request({"cmd": "selftest_record_capture", "mode": "seh"}, "error")
    if not checks.check(
        "binary_capture_fault_gate", rejected.get("reason") == "not_recording", str(rejected)
    ):
        return
    for mode, reason in (("seh", "capture_fault"), ("cpp", "capture_failed")):
        started = client.record_bin_start()
        record_id = int(started.get("record_id", 0))
        checks.check(
            f"binary_{mode}_start",
            record_id > 0
            and started.get("ok") is True
            and started.get("active") is True
            and started.get("reason") == "none",
            str(started),
        )
        wait_ticks(client, 4)
        client.request(
            {"cmd": "selftest_record_capture", "mode": mode}, "record_capture_test_armed"
        )
        done = terminal(record_id)
        checks.check(
            f"binary_{mode}_terminal",
            done.get("reason") == reason
            and done.get("ok") is False
            and int(done.get("rows", 0)) >= 4,
            str(done),
        )
        stopped = client.request({"cmd": "record_stop"}, "record_stopped")
        checks.check(
            f"binary_{mode}_stop_preserves_failure",
            stopped.get("record_id") == record_id
            and stopped.get("active") is False
            and stopped.get("ok") is False
            and stopped.get("reason") == reason
            and stopped.get("rows") == done.get("rows") == stopped.get("head"),
            str(stopped),
        )
        checks.check(f"binary_{mode}_checked_stop", rejects("record_bin_stop", reason))
        json_dump = client.record_dump(limit=8192)
        hex_dump = client.record_dump(limit=8192, format="hex")
        rows = decode_dump(json_dump)
        checks.check(
            f"binary_{mode}_partial_formats",
            json_dump.get("record_id") == hex_dump.get("record_id") == record_id
            and json_dump.get("partial") is True
            and hex_dump.get("partial") is True
            and json_dump.get("ok") is False
            and json_dump.get("reason") == reason
            and json_dump.get("lost") == hex_dump.get("lost") == 0
            and len(rows) == done.get("rows")
            and rows == decode_dump(hex_dump)
            and all(after.tick == before.tick + 1 for before, after in itertools.pairwise(rows)),
            f"rows={len(rows)}",
        )
        checks.check(f"binary_{mode}_checked_dump", rejects("record_dump_all", reason))
        checks.check(
            f"binary_{mode}_explicit_partial",
            client.record_dump_all(chunk=2, allow_partial=True, record_id=record_id) == rows,
        )
        wait_ticks(client, 3)
        status = client.status()["record"]
        checks.check(
            f"binary_{mode}_frozen",
            status.get("active") is False
            and status.get("record_id") == record_id
            and status.get("head") == len(rows)
            and status.get("reason") == reason,
            str(status),
        )
        checks.check(f"binary_{mode}_unique", no_duplicate(record_id))
        with record_session(client) as recovered:
            wait_ticks(client, 4)
        checks.check(
            f"binary_{mode}_recovery",
            int(recovered.record_id or 0) > record_id
            and len(recovered.entries) >= 4
            and client.ping().get("event") == "pong",
        )

    failure = ""
    try:
        with record_session(client) as partial:
            wait_ticks(client, 4)
            client.request(
                {"cmd": "selftest_record_capture", "mode": "seh"}, "record_capture_test_armed"
            )
            terminal(int(partial.record_id or 0))
    except ModProtocolError as error:
        failure = str(error)
    checks.check(
        "binary_context_failure_retains_rows",
        "capture_fault" in failure and len(partial.entries) >= 4,
        f"{failure}; rows={len(partial.entries)}",
    )
    notes: list[str] = []
    try:
        with record_session(client) as body_failure:
            wait_ticks(client, 4)
            client.request(
                {"cmd": "selftest_record_capture", "mode": "cpp"}, "record_capture_test_armed"
            )
            terminal(int(body_failure.record_id or 0))
            raise ValueError("binary capture body sentinel")
    except ValueError as error:
        if str(error) != "binary capture body sentinel":
            raise
        notes = getattr(error, "__notes__", [])
    checks.check(
        "binary_context_preserves_body_exception",
        len(body_failure.entries) >= 4 and any("capture_failed" in note for note in notes),
        str(notes),
    )

    if world:
        started = client.record_bin_start()
        record_id = int(started["record_id"])
        try:
            wait_ticks(client, 4)
            client.leave_world()
            client.wait_event("world_left", 45)
            done = terminal(record_id)
            checks.check(
                "binary_world_cancel",
                done.get("ok") is False
                and done.get("reason") == "world_left"
                and int(done.get("rows", 0)) >= 4,
                str(done),
            )
            checks.check(
                "binary_world_partial_retained",
                len(client.record_dump_all(allow_partial=True, record_id=record_id))
                == done.get("rows"),
            )
            checks.check("binary_world_checked_stop", rejects("record_bin_stop", "world_left"))
            checks.check("binary_world_unique", no_duplicate(record_id))
            deadline = time.monotonic() + 45
            while not client.worlds().get("ready"):
                if time.monotonic() >= deadline:
                    raise ModTimeout("binary recording: main menu did not become ready")
                time.sleep(0.1)
            rejected = client.request({"cmd": "record_start"}, "error")
            checks.check("binary_menu_rejected", rejected.get("reason") == "not_in_world")
        finally:
            joined = client.enter_world_ready(world)
            checks.check("binary_world_rejoin", joined.get("ok") is True, str(joined))
            client.wait_in_world(timeout=60)
            client.reset(
                *(float(original[f"feet_{axis}"]) for axis in "xyz"),
                yaw=float(original["yaw"]),
                pitch=float(original["pitch"]),
            )
            checks.check("binary_world_settle", settle(client, on_ground=True)["ok"])
        with record_session(client) as recovered:
            wait_ticks(client, 4)
        checks.check(
            "binary_world_recovery",
            int(recovered.record_id or 0) > record_id and len(recovered.entries) >= 4,
        )


def check_overhead_window(client: ModClient, checks: Checks, *, wrap: bool = False) -> None:
    """Verify published window bounds/legacy metrics while real ticks overwrite it."""
    start = client.overhead()
    first_head = int(start.get("sample_head", 0))
    last_head = first_head
    deadline = time.monotonic() + (80 if wrap else 15)
    valid = aliases = ordered = monotonic = True
    reads = consistent = busy = 0
    while reads < 100 or (wrap and last_head - first_head < 1100):
        if time.monotonic() >= deadline:
            break
        result = client.overhead()
        reads += 1
        if result.get("consistent") is not True:
            busy += 1
            valid &= (
                result.get("reason") == "snapshot_busy"
                and result.get("samples") == 0
                and "tick_p99_us" not in result
            )
            continue
        consistent += 1
        head, count = int(result.get("sample_head", -1)), int(result.get("samples", -1))
        valid &= (
            0 <= count <= 1024
            and count == min(head, 1024)
            and int(result.get("sample_first", -1)) + count == head
        )
        monotonic &= head >= last_head
        last_head = head
        if count:
            aliases &= all(
                result.get(f"tick_{part}_us") == result.get(f"tick_interval_{part}_us")
                for part in ("p50", "p99", "max")
            )
            ordered &= all(
                0
                <= float(result.get(f"{path}_p50_us", -1))
                <= float(result.get(f"{path}_p99_us", -1))
                <= float(result.get(f"{path}_max_us", -1))
                for path in ("tick", "hook")
            )
        time.sleep(0.02)
    detail = str(
        {"reads": reads, "consistent": consistent, "busy": busy, "advance": last_head - first_head}
    )
    checks.check("overhead_window_readback", reads >= 100 and consistent > 0 and valid, detail)
    checks.check("overhead_window_monotonic", monotonic)
    checks.check("overhead_window_legacy_aliases", aliases)
    checks.check("overhead_window_percentiles", ordered)
    if wrap:
        checks.check("overhead_window_wrap", last_head - first_head >= 1100, detail)
    if not checks.check(
        "overhead_record_fixture_idle", client.status().get("record_active") is False
    ):
        return
    before = int(client.overhead()["paths"]["record"]["calls_total"])
    try:
        client.record_bin_start(period=1)
        wait_ticks(client, 8)
    finally:
        client.record_bin_stop()
    after = int(client.overhead()["paths"]["record"]["calls_total"])
    checks.check("overhead_record_counter", after - before >= 8, f"delta={after - before}")


def check_entry_lifecycle(client: ModClient, checks: Checks, world: str) -> None:
    """Exercise the real deadline/probe pipeline, then restore the saved world."""
    original = client.state()
    if not checks.check("entry_lifecycle_in_world", original.get("in_world") is True):
        return

    def menu_ready() -> dict[str, Any]:
        deadline = time.monotonic() + 60
        while True:
            result = client.worlds()
            if result.get("ready"):
                return result
            if time.monotonic() >= deadline:
                raise ModTimeout(f"entry lifecycle: menu not ready: {result.get('entry')}")
            time.sleep(0.1)

    def no_duplicate(entry_id: int) -> bool:
        with contextlib.suppress(ModTimeout):
            client.wait_enter_settled(0.3, entry_id=entry_id)
            return False
        return True

    try:
        left = client.leave_world()
        checks.check("entry_lifecycle_leave", left.get("ok") is True)
        client.wait_event("world_left", 45)
        checks.check("entry_lifecycle_menu", menu_ready().get("ready") is True)
        last_id = 0
        for mode in ("timeout", "probe_fault", "start_fault"):
            accepted = client.request(
                {"cmd": "enter_world", "name": world, "_selftest": mode},
                ("enter_world_result", "error"),
                timeout=40,
            )
            if accepted.get("reason") == "selftest_stress_disabled":
                checks.check("entry_lifecycle_fault_gate", False, "enable allow_selftest_stress")
                return
            entry_id = int(accepted.get("entry_id", 0))
            checks.check(f"entry_{mode}_accepted", accepted.get("ok") and entry_id > last_id)
            last_id = entry_id
            if mode != "start_fault":
                busy = client.enter_world(world)
                checks.check(
                    f"entry_{mode}_single_start",
                    busy.get("reason") == "already_starting" and busy.get("entry_id") == entry_id,
                    str(busy),
                )
            terminal = client.wait_enter_settled(5, entry_id=entry_id)
            checks.check(
                f"entry_{mode}_terminal",
                terminal.get("ok") is False
                and terminal.get("reason")
                == ("start_fault" if mode == "start_fault" else "entry_timeout"),
                str(terminal),
            )
            if mode != "start_fault":
                refused = client.enter_world(world)
                checks.check(
                    f"entry_{mode}_quarantine",
                    refused.get("ok") is False and refused.get("reason") == "entry_recovering",
                    str(refused),
                )
                checks.check(f"entry_{mode}_not_ready", client.worlds().get("ready") is False)
            if mode == "probe_fault":
                status = client.status()["world_entry"]
                checks.check(
                    "entry_probe_fault_diagnosed",
                    int(status.get("probe_errors", 0)) >= 1
                    and status.get("last_probe_error") == "probe_seh",
                    str(status),
                )
            checks.check(f"entry_{mode}_recovers", menu_ready().get("ready") is True)
            checks.check(f"entry_{mode}_unique_terminal", no_duplicate(entry_id))
            checks.check(f"entry_{mode}_pipe_alive", client.ping().get("event") == "pong")

        # Start a real server but suppress its join listener. The watchdog must
        # infer readiness from a local player + HUD and settle this same entry.
        accepted = client.request(
            {"cmd": "enter_world", "name": world, "_selftest": "missing_join"},
            "enter_world_result",
            timeout=40,
        )
        entry_id = int(accepted.get("entry_id", 0))
        checks.check("entry_missing_join_accepted", accepted.get("ok") and entry_id > last_id)
        joined = client.wait_enter_settled(40, entry_id=entry_id)
        checks.check(
            "entry_missing_join_observed",
            joined.get("ok") is True and joined.get("reason") == "player_observed",
            str(joined),
        )
        checks.check("entry_missing_join_unique_terminal", no_duplicate(entry_id))
        checks.check("entry_missing_join_idle", client.status()["world_entry"]["phase"] == "idle")
    finally:
        if not client.state().get("in_world"):
            menu_ready()
            joined = client.enter_world_ready(world)
            checks.check("entry_lifecycle_restore_join", joined.get("ok") is True, str(joined))
        client.wait_in_world(timeout=60)
        client.reset(
            *(float(original[f"feet_{axis}"]) for axis in "xyz"),
            yaw=float(original["yaw"]),
            pitch=float(original["pitch"]),
        )
        wait_ticks(client, 8)
        checks.check("entry_lifecycle_world_recovery", client.state().get("in_world") is True)


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


def check_connection_lifecycle(client: ModClient, checks: Checks) -> None:
    """Read-only connection recovery after GC and normal/abrupt child exits."""
    status = client.status()
    controls = status.get("controls", {})
    idle = (
        not any(
            status.get(key)
            for key in ("recording", "record_active", "macro_playing", "macro_recording")
        )
        and not client.task_status().get("active")
        and not any(
            controls.get(key)
            for key in ("held_mask", "injecting_input", "injecting_look", "cleanup_pending")
        )
    )
    if not checks.check(
        "connection_idle", idle, "reconnecting requires idle controls/tasks/recording"
    ):
        return
    for cycle in (False, True):
        label = "cycle" if cycle else "plain"
        client.close()
        abandoned = ModClient(client.pipe_name, connect_timeout=10, timeout=5).connect()
        stream = abandoned._session.stream
        reader = stream.reader
        try:
            checks.check(f"connection_{label}_ping", abandoned.ping().get("event") == "pong")
            if cycle:
                abandoned.subscribe("tick", lambda _row, owner=abandoned: owner.protocol)
            reference = weakref.ref(abandoned)
            del abandoned
            gc.collect()
            if reader is not None:
                reader.join(2)
            checks.check(
                f"connection_{label}_released",
                reference() is None
                and stream.handle is None
                and stream.stopped.is_set()
                and reader is not None
                and not reader.is_alive(),
            )
        finally:
            stream.close()
        client.connect()
        checks.check(f"connection_{label}_reconnect", client.ping().get("event") == "pong")

    for abrupt in (False, True):
        label = "abrupt_exit" if abrupt else "normal_exit"
        client.close()
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os, sys; from driver.harness import ModClient; "
                "c=ModClient(sys.argv[1], connect_timeout=10, timeout=5).connect(); "
                "assert c.ping()['event']=='pong'; " + ("os._exit(0)" if abrupt else "sys.exit(0)"),
                client.pipe_name,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        checks.check(f"connection_{label}", child.returncode == 0, child.stderr)
        client.connect()
        checks.check(f"connection_{label}_reconnect", client.ping().get("event") == "pong")


def check_diagnostic_export(client: ModClient, checks: Checks) -> None:
    """Export existing buffers without arming hooks or changing the world."""
    report_path = capture_disconnect_report(client, "out", tag="selftest-diagnostic-export")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    dump = report["inbound_dump"]
    checks.check("diagnostic_report_absolute", report_path.is_absolute(), str(report_path))
    inbound_path = Path(dump["path"])
    checks.check(
        "diagnostic_inbound_export",
        dump.get("ok") is True and inbound_path.is_absolute() and inbound_path.is_file(),
        str(dump),
    )
    checks.check("diagnostic_export_receipt_consumed", client.ping().get("event") == "pong")


def check_mod_core_live(client: ModClient, checks: Checks) -> None:
    """Exercise installed transport/recording without input, world edits or RE."""
    status = client.status()
    ready = (
        status.get("in_world")
        and not any(
            status.get(key)
            for key in ("recording", "record_active", "macro_playing", "macro_recording")
        )
        and not client.task_status().get("active")
    )
    if not checks.check("mod_core_idle_world", ready):
        return
    checks.check("mod_core_protocol", client.protocol == 4)
    if not checks.check("mod_core_installed", status.get("pipe_write_timeout_ms") == 5000):
        return
    checks.check("mod_core_state", client.state().get("in_world") is True)
    check_connection_lifecycle(client, checks)
    reply = client.request({"cmd": "__mod_core_unknown__"}, "error")
    checks.check("mod_core_unknown_cmd", reply.get("reason") == "unknown_cmd")

    # One write containing many commands, then CRLF/UTF-8 across writes.
    client.send_raw(b'{"cmd":"ping"}\n' * 64)
    checks.check(
        "mod_core_batched_ping",
        all(client.wait_event("pong").get("event") == "pong" for _ in range(64)),
    )
    payload = json.dumps({"cmd": "ping", "note": "分片"}, ensure_ascii=False).encode() + b"\r\n"
    for byte in payload:
        client.send_raw(bytes([byte]))
    checks.check("mod_core_fragmented_utf8", client.wait_event("pong").get("event") == "pong")
    for fragmented in (False, True):
        payload = b"x" * ((1 << 20) + 1)
        if fragmented:
            client.send_raw(payload[: 1 << 19])
            payload = payload[1 << 19 :]
        client.send_raw(payload + b'\n{"cmd":"ping"}\n')
        errors = []
        while True:
            reply = client.wait_event(("error", "pong"), timeout=20)
            if reply.get("event") == "pong":
                break
            errors.append(reply.get("reason"))
        checks.check(
            f"mod_core_oversize_{'fragmented' if fragmented else 'complete'}",
            errors == ["line_too_long"],
            str(errors),
        )

    try:
        samples = client.record(max_ticks=8)
        checks.check("mod_core_json_record", len(samples) == 8, f"rows={len(samples)}")
        checks.check("mod_core_json_stopped", client.status().get("recording") is False)
        for period in (1, 3):
            started = client.record_bin_start(period)
            checks.check(
                f"mod_core_record_layout_{period}",
                started.get("entry_size") == 64 and started.get("capacity") == 524288,
            )
            wait_ticks(client, 18)
            live = client.record_dump(limit=128)
            checks.check(f"mod_core_record_active_{period}", live.get("active") is True)
            stopped = client.record_bin_stop()
            rows = decode_dump(client.record_dump(limit=128))
            binary = decode_dump(client.record_dump(limit=128, format="hex"))
            checks.check(f"mod_core_encodings_{period}", bool(rows) and rows == binary)
            checks.check(
                f"mod_core_sample_period_{period}",
                len(rows) > 1
                and all(b.tick - a.tick == period for a, b in itertools.pairwise(rows)),
                f"rows={len(rows)}",
            )
            checks.check(
                f"mod_core_pagination_{period}",
                client.record_dump_all(chunk=3, format="hex") == rows,
            )
            wait_ticks(client, 3)
            checks.check(
                f"mod_core_stop_stable_{period}",
                client.status().get("record_head") == stopped.get("head") == len(rows),
            )

        previous_tick = -1
        isolated = True
        for _ in range(12):
            boundary = int(client.status().get("tick", 0))
            client.record_bin_start()
            wait_ticks(client, 3)
            client.record_bin_stop()
            rows = decode_dump(client.record_dump())
            isolated &= bool(rows) and all(row.tick >= boundary for row in rows)
            isolated &= all(row.tick > previous_tick for row in rows)
            if rows:
                previous_tick = rows[-1].tick
        checks.check("mod_core_restart_isolation", isolated, "12 sessions")

        # Enough output to fill the 64 KiB pipe without any reader thread.
        client.record_bin_start()
        wait_ticks(client, 72)
        client.record_bin_stop()
        rows = decode_dump(client.record_dump(limit=128))
        if checks.check("mod_core_backpressure_fixture", len(rows) >= 64, f"rows={len(rows)}"):
            check_mod_core_backpressure(client, checks)
    finally:
        if client.connected:
            client.stop()
            client.record_bin_stop()

    client.request({"cmd": "spool_flush"}, "spool_flushed")
    events, stats = read_spool(client.hello["spool"])
    checks.check("mod_core_spool_json", stats.bad_lines == 0 and bool(events))
    checks.check(
        "mod_core_no_internal_error",
        not any(row.get("reason") == "internal_error" for row in events),
    )
    for _ in range(3):
        client.reconnect()
        checks.check("mod_core_reconnect", client.ping().get("event") == "pong")


def check_mod_core_backpressure(client: ModClient, checks: Checks) -> None:
    """Hold a client open without consuming hello or dump replies."""
    before = client.status()
    client.close()
    # Named-pipe paths are not filesystem paths. A plain unbuffered handle is
    # intentional here: ModClient's polling reader would prevent backpressure.
    deadline = time.monotonic() + 5.0
    while True:
        try:
            raw = open(client.pipe_name, "r+b", buffering=0)  # noqa: PTH123, SIM115
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    with raw:
        command = b'{"cmd":"record_dump","limit":8192,"format":"hex"}\n' * 64
        remaining = memoryview(command)
        while remaining:
            written = raw.write(remaining)
            if not written:
                raise ModPipeError("nonreading fixture could not send commands")
            remaining = remaining[written:]
        # The budget starts when the server begins writing; allow scheduling
        # slack, while keeping this deliberate wait bounded and under 10 s.
        time.sleep(6.5)
        previous_timeout = client.connect_timeout
        try:
            client.connect_timeout = 5.0
            client.connect()
        finally:
            client.connect_timeout = previous_timeout
        after = client.status()
        checks.check(
            "mod_core_nonreader_timeout",
            int(after.get("pipe_write_timeouts", 0))
            == int(before.get("pipe_write_timeouts", 0)) + 1,
        )
        checks.check("mod_core_ticks_survive_timeout", after.get("tick", 0) > before.get("tick", 0))
        checks.check("mod_core_world_survives_timeout", client.state().get("in_world") is True)


def check_request_recovery(client: ModClient, checks: Checks) -> None:
    before = client.state()
    yaw, pitch = float(before["yaw"]), float(before["pitch"])
    for command in (
        {"cmd": "set", "sprint": "invalid"},
        {"cmd": "move", "ticks": "invalid"},
        {"cmd": "look", "relative": "invalid"},
    ):
        reply = client.request(command, "error")
        checks.check(f"request_reject_{command['cmd']}", reply.get("reason") == "bad_command")
    try:
        client.look(yaw + 5, pitch, relative=False)
        wait_ticks(client, 8)
        actual = float(client.state()["yaw"])
        gap = abs((actual - yaw - 5 + 180) % 360 - 180)
        checks.check("request_recovers_after_bad_types", gap < 0.5, f"yaw_gap={gap:.3f}")
    finally:
        client.look(yaw, pitch, relative=False)
        client.stop_move()


def check_chat_lifecycle(client: ModClient, checks: Checks, world: str) -> None:
    """Exercise chat without world ticks and restore the test world afterward."""
    original = client.state()
    if not checks.check("chat_lifecycle_in_world", original.get("in_world") is True):
        return
    probe = client.request({"cmd": "selftest_chat_timeout"}, ("chat_history", "error"))
    if not checks.check(
        "chat_lifecycle_timeout",
        probe.get("event") == "chat_history" and probe.get("reason") == "chat_history_timeout",
        str(probe),
    ):
        return
    try:
        with client.exchange():
            client.send({"cmd": "selftest_chat_timeout"})
            left = client.leave_world()
            checks.check("chat_lifecycle_leave", left.get("ok") is True, str(left))
            client.wait_event("world_left", 45)
            cancelled = client.wait_event("chat_history", 5)
        checks.check(
            "chat_lifecycle_world_cancel",
            cancelled.get("ok") is False and cancelled.get("reason") == "world_left",
            str(cancelled),
        )
        deadline = time.monotonic() + 45
        while not client.worlds().get("ready"):
            if time.monotonic() >= deadline:
                raise ModTimeout("chat lifecycle: main menu did not become ready")
            time.sleep(0.1)
        tick = client.status().get("tick")
        for attempt in range(2):
            result = client.chat_history(limit=1)
            checks.check(
                f"chat_lifecycle_menu_{attempt}",
                result.get("ok") is False and result.get("reason") == "no_player",
                str(result),
            )
        checks.check("chat_lifecycle_no_world_ticks", client.status().get("tick") == tick)
    finally:
        # Both normal completion and a failed menu check must return the game
        # to the caller's test world; recovery errors remain visible.
        joined = client.enter_world_ready(world)
        checks.check("chat_lifecycle_rejoin", joined.get("ok") is True, str(joined))
        client.wait_in_world(timeout=60)
        client.reset(
            *(float(original[f"feet_{axis}"]) for axis in "xyz"),
            yaw=float(original["yaw"]),
            pitch=float(original["pitch"]),
        )
        wait_ticks(client, 8)
        result = client.chat_history(limit=1)
        checks.check("chat_lifecycle_world_recovery", result.get("ok") is True, str(result))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-complete", action="store_true", help="fail when required coverage is skipped"
    )
    parser.add_argument("--entity", default="minecraft:pig")
    parser.add_argument("--keep", action="store_true", help="do not remove the test entity")
    parser.add_argument("--radius", type=float, default=10.0)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--diagnostic-export-only",
        action="store_true",
        help="export existing diagnostic buffers without arming hooks or changing the world",
    )
    modes.add_argument(
        "--client-io-only",
        action="store_true",
        help="run offline decoder/recording/spool regressions without connecting to the game",
    )
    modes.add_argument(
        "--mod-core-only",
        action="store_true",
        help="compile/run native mod transport/recording checks without connecting to the game",
    )
    modes.add_argument(
        "--chat-lifecycle-world",
        metavar="WORLD",
        help="save/leave, check chat cancellation and main menu recovery, then re-enter WORLD",
    )
    modes.add_argument(
        "--query-capture-only",
        action="store_true",
        help="check fixed query snapshots, SEH/timeout recovery and per-tick scan budget",
    )
    modes.add_argument(
        "--query-lifecycle-world",
        metavar="WORLD",
        help="check scan cancellation on reconnect/exit, then re-enter WORLD",
    )
    modes.add_argument(
        "--overhead-only",
        action="store_true",
        help="read overhead concurrently across 1100 real ticks and verify record path counters",
    )
    modes.add_argument(
        "--tick-stream-only",
        action="store_true",
        help="check JSON streams and parkour event safety",
    )
    modes.add_argument(
        "--tick-stream-world", metavar="WORLD", help="also check stream termination on leave/rejoin"
    )
    modes.add_argument(
        "--entry-lifecycle-world",
        metavar="WORLD",
        help="save/leave, check entry timeout/quarantine and missing join recovery, then restore WORLD",
    )
    modes.add_argument(
        "--macro-limit-only",
        action="store_true",
        help="check recording limit normalization, unique termination and buffered data preservation",
    )
    modes.add_argument(
        "--macro-capture-only",
        action="store_true",
        help="check macro sampling faults and retained saves",
    )
    modes.add_argument(
        "--macro-capture-world",
        metavar="WORLD",
        help="also check macro recording across leave/rejoin",
    )
    modes.add_argument(
        "--tick-safety-only",
        action="store_true",
        help="check guarded hook calls and coherent poses",
    )
    modes.add_argument(
        "--tick-safety-world",
        metavar="WORLD",
        help="also check hook cancellation and pose validity on leave/rejoin",
    )
    modes.add_argument(
        "--binary-capture-only", action="store_true", help="check binary recording failure outcomes"
    )
    modes.add_argument(
        "--binary-capture-world", metavar="WORLD", help="also check recording across leave/rejoin"
    )
    modes.add_argument(
        "--teleport-only", action="store_true", help="check owned server teleports and failures"
    )
    modes.add_argument(
        "--packet-tap-only",
        action="store_true",
        help="check isolation of packet observation faults",
    )
    modes.add_argument(
        "--packet-injection-only",
        action="store_true",
        help="check command templates and owned packet sends",
    )
    modes.add_argument(
        "--packet-injection-world", metavar="WORLD", help="also check packet reconnect/leave/rejoin"
    )
    modes.add_argument(
        "--record-medium-only",
        action="store_true",
        help="check recording in an isolated water fixture",
    )
    modes.add_argument(
        "--violation-only",
        action="store_true",
        help="check the read-only violation watch (synthetic row, no real violation)",
    )
    modes.add_argument(
        "--teleport-world", metavar="WORLD", help="also check teleport reconnect/leave/rejoin"
    )
    modes.add_argument(
        "--controls-only", action="store_true", help="check control faults and input release"
    )
    modes.add_argument(
        "--tick-recovery-only", action="store_true", help="check tick errors and fail-stop recovery"
    )
    modes.add_argument(
        "--tick-recovery-world",
        metavar="WORLD",
        help="also check recovery across reconnect/leave/rejoin",
    )
    modes.add_argument(
        "--player-identity-only",
        action="store_true",
        help="check local UUID sampling and server resolution",
    )
    modes.add_argument(
        "--player-identity-world",
        metavar="WORLD",
        help="also reject identities retained across worlds",
    )
    modes.add_argument(
        "--flight-only", action="store_true", help="check staged flight and tick assertions"
    )
    modes.add_argument(
        "--flight-world", metavar="WORLD", help="also check flight reconnect/leave/rejoin"
    )
    modes.add_argument(
        "--input-events-only", action="store_true", help="check guarded input and text ordering"
    )
    modes.add_argument(
        "--input-events-world", metavar="WORLD", help="also check input reconnect/leave/rejoin"
    )
    modes.add_argument(
        "--ecs-capture-only", action="store_true", help="check owned ECS snapshots and failures"
    )
    modes.add_argument(
        "--ecs-capture-world", metavar="WORLD", help="also check ECS reconnect/leave/rejoin"
    )
    modes.add_argument(
        "--macro-playback-only", action="store_true", help="check replay faults and frame ownership"
    )
    modes.add_argument(
        "--macro-playback-world", metavar="WORLD", help="also check playback across leave/rejoin"
    )
    modes.add_argument(
        "--use-lifecycle-only",
        action="store_true",
        help="inject missing/failed server work and verify task termination/recovery in a world",
    )
    modes.add_argument(
        "--connection-only",
        action="store_true",
        help="check abandoned clients and process-exit reconnects; no world changes or RE",
    )
    modes.add_argument(
        "--mod-core-live-only",
        action="store_true",
        help="check installed pipe/recording only; no RE, input injection or world edits",
    )
    parser.add_argument("--stress-cycles", type=int, default=0, help="opt-in lifecycle soak cycles")
    parser.add_argument(
        "--stress-world", help="leave/re-enter this test world every 10 soak cycles"
    )
    args = parser.parse_args(argv)
    if args.client_io_only or args.mod_core_only:
        # Reuse the bounded regression fixtures; no game commands, input or
        # live diagnostics are needed to verify these client-side workflows.
        suites = (
            ["tests.test_mod_models"]
            if args.mod_core_only
            else [
                "tests.test_transport.SessionTests",
                "tests.test_transport_stress",
                "tests.test_transport.LineDecoderTests",
                "tests.test_recording_commands",
                "tests.test_mod_agent.RecordingIntegrationTests",
                "tests.test_dev_config",
                "tests.test_dev.GameShutdownTests",
                "tests.test_spool",
            ]
        )
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                *suites,
                "-v",
            ],
            cwd=ROOT,
            check=False,
        ).returncode
    if args.stress_cycles < 0 or (args.stress_world and args.stress_cycles < 10):
        parser.error(
            "--stress-cycles must be nonnegative; --stress-world requires at least 10 cycles"
        )

    checks = Checks(require_complete=args.require_complete)
    try:
        client = ModClient(connect_timeout=60.0, timeout=30.0).connect()
    except ModPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("提示：游戏内是否已加载 probe mod，并且已经进入存档？", file=sys.stderr)
        return 2

    observation = ViolationObservation(
        client,
        checks,
        os.environ.get("LEVIPILOT_VIOLATION_DUMP") or None,
        lambda: violation_watch_address(client, "@client_handle_packet_violation"),
    )
    result = 0
    try:
        observation.start()
        result = run_live(client, checks, args, observation)
    except WorldLost as exc:
        checks.check("aborted_world_left", False, str(exc))
        try:
            report = capture_disconnect_report(client, ROOT / "out", tag="disconnect")
            checks.note("disconnect_report", str(report))
        except (ModPipeError, OSError) as report_exc:
            checks.note("disconnect_report", f"failed: {report_exc}")
    finally:
        try:
            observation.finish()
        finally:
            try:
                with contextlib.suppress(ModPipeError):
                    client.input_lock(False)
            finally:
                client.close()
    return result if result else checks.finish()


def run_live(
    client: ModClient, checks: Checks, args: argparse.Namespace, observation: ViolationObservation
) -> int:
    if args.diagnostic_export_only:
        try:
            check_diagnostic_export(client, checks)
        except (ModPipeError, OSError, ValueError, KeyError) as exc:
            checks.check("diagnostic_export_error", False, str(exc))
        return 0

    if args.connection_only:
        try:
            check_connection_lifecycle(client, checks)
        except (ModPipeError, OSError, ValueError, subprocess.SubprocessError) as exc:
            checks.check("connection_lifecycle_error", False, str(exc))
        return 0

    if args.overhead_only:
        try:
            check_overhead_window(client, checks, wrap=True)
        except WorldLost:
            raise
        except (ModPipeError, OSError, ValueError) as exc:
            checks.check("overhead_window_error", False, str(exc))
        return 0

    if (
        args.record_medium_only
        or args.packet_tap_only
        or args.violation_only
        or args.packet_injection_only
        or args.packet_injection_world
        or args.teleport_only
        or args.teleport_world
        or args.tick_recovery_only
        or args.tick_recovery_world
        or args.controls_only
        or args.player_identity_only
        or args.player_identity_world
        or args.flight_only
        or args.flight_world
        or args.input_events_only
        or args.input_events_world
        or args.ecs_capture_only
        or args.ecs_capture_world
        or args.query_capture_only
        or args.query_lifecycle_world
        or args.macro_limit_only
        or args.entry_lifecycle_world
        or args.tick_stream_only
        or args.tick_stream_world
        or args.macro_capture_only
        or args.macro_capture_world
        or args.tick_safety_only
        or args.tick_safety_world
        or args.binary_capture_only
        or args.binary_capture_world
        or args.macro_playback_only
        or args.macro_playback_world
    ):
        try:
            client.input_lock(True)
            if args.record_medium_only:
                check_block_identity(client, checks)
                check_record_medium(client, checks)
            elif args.packet_tap_only:
                check_packet_tap(client, checks)
            elif args.violation_only:
                with observation.gap("violation_selftest"):
                    check_violation_watch(client, checks)
                check_send_probe(client, checks)
            elif args.packet_injection_only or args.packet_injection_world:
                check_packet_injection(client, checks, args.packet_injection_world)
            elif args.tick_recovery_only or args.tick_recovery_world:
                check_tick_recovery(client, checks, args.tick_recovery_world)
            elif args.teleport_only or args.teleport_world:
                check_teleport(client, checks, args.teleport_world)
            elif args.controls_only:
                check_input_mode_contract(client, checks)
                check_controls(client, checks)
            elif args.player_identity_only or args.player_identity_world:
                check_player_identity(client, checks, args.player_identity_world)
            elif args.flight_only or args.flight_world:
                check_flight(client, checks, args.flight_world)
            elif args.input_events_only or args.input_events_world:
                check_input_events(client, checks, args.input_events_world)
            elif args.ecs_capture_only or args.ecs_capture_world:
                check_ecs_capture(client, checks, args.ecs_capture_world)
            elif args.macro_playback_only or args.macro_playback_world:
                check_macro_playback(client, checks, args.macro_playback_world)
            elif args.binary_capture_only or args.binary_capture_world:
                check_binary_capture(client, checks, args.binary_capture_world)
            elif args.tick_safety_only or args.tick_safety_world:
                check_tick_safety(client, checks, args.tick_safety_world)
            elif args.macro_capture_only or args.macro_capture_world:
                check_macro_capture(client, checks, args.macro_capture_world)
            elif args.tick_stream_only or args.tick_stream_world:
                check_tick_streams(client, checks, args.tick_stream_world)
            elif args.entry_lifecycle_world:
                check_entry_lifecycle(client, checks, args.entry_lifecycle_world)
            elif args.macro_limit_only:
                check_macro_record_limits(client, checks)
            elif args.query_lifecycle_world:
                check_query_lifecycle(client, checks, args.query_lifecycle_world)
            else:
                check_query_capture(client, checks)
        except WorldLost:
            raise
        except (ModPipeError, OSError, ValueError) as exc:
            name = (
                "record_medium_error"
                if args.record_medium_only
                else "packet_tap_error"
                if args.packet_tap_only
                else "violation_watch_error"
                if args.violation_only
                else "packet_injection_error"
                if args.packet_injection_only or args.packet_injection_world
                else "tick_recovery_error"
                if args.tick_recovery_only or args.tick_recovery_world
                else "teleport_error"
                if args.teleport_only or args.teleport_world
                else "controls_error"
                if args.controls_only
                else "identity_error"
                if args.player_identity_only or args.player_identity_world
                else "flight_error"
                if args.flight_only or args.flight_world
                else "input_events_error"
                if args.input_events_only or args.input_events_world
                else "ecs_capture_error"
                if args.ecs_capture_only or args.ecs_capture_world
                else "macro_playback_error"
                if args.macro_playback_only or args.macro_playback_world
                else "binary_capture_error"
                if args.binary_capture_only or args.binary_capture_world
                else "tick_safety_error"
                if args.tick_safety_only or args.tick_safety_world
                else "macro_capture_error"
                if args.macro_capture_only or args.macro_capture_world
                else "macro_limit_error"
                if args.macro_limit_only
                else "query_capture_error"
            )
            checks.check(name, False, str(exc))
        finally:
            with contextlib.suppress(ModPipeError):
                client.input_lock(False)
        return 0

    if args.use_lifecycle_only:
        try:
            check_use_lifecycle(client, checks)
        except WorldLost:
            raise
        except (ModPipeError, OSError, ValueError) as exc:
            checks.check("use_lifecycle_error", False, str(exc))
        return 0

    if args.mod_core_live_only:
        try:
            check_mod_core_live(client, checks)
        except WorldLost:
            raise
        except (ModPipeError, OSError, ValueError) as exc:
            checks.check("mod_core_live_error", False, str(exc))
        return 0

    if args.chat_lifecycle_world:
        try:
            check_chat_lifecycle(client, checks, args.chat_lifecycle_world)
        except WorldLost:
            raise
        except (ModPipeError, OSError, ValueError) as exc:
            checks.check("chat_lifecycle_error", False, str(exc))
        return 0

    tap_dump = os.environ.get("LEVIPILOT_PACKET_DUMP") or None
    try:
        require_world(client, "selftest preflight")
        # Scripts own the session: drop real hardware keys/mouse so a person at
        # the machine cannot perturb the run. Injected events still pass; use
        # `harness input-lock --off` (or input_lock(false)) to release.
        try:
            auto_lock = client.input_lock(True)
            checks.note("input_lock_auto", f"on={auto_lock.get('on')}")
        except (ModPipeError, ModProtocolError) as exc:
            checks.note("input_lock_auto", f"unavailable: {exc}")
        checks.check("ping", client.ping().get("event") == "pong")
        if tap_dump:
            # Diagnostic mode: keep the packet-identity ring on for the whole
            # run so a disconnect still has the last packets before it.
            try:
                client.packet_capture(True, sample=1, directions=["send", "recv"])
                checks.note("packet_tap_all", f"on -> {tap_dump}")
            except (ModPipeError, ModProtocolError) as exc:
                checks.note("packet_tap_all", f"unavailable: {exc}")
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
        delayed_chat = client.request({"cmd": "selftest_chat_timeout"}, ("chat_history", "error"))
        if delayed_chat.get("event") == "error":
            checks.check(
                "chat_timeout_gate", delayed_chat.get("reason") == "selftest_stress_disabled"
            )
            checks.skip("chat_timeout_recovery", "allow_selftest_stress is disabled")
        else:
            checks.check(
                "chat_timeout_terminal",
                delayed_chat.get("ok") is False
                and delayed_chat.get("reason") == "chat_history_timeout",
                str(delayed_chat),
            )
            recovered_chat = client.chat_history(limit=1)
            checks.check(
                "chat_timeout_recovery", recovered_chat.get("ok") is True, str(recovered_chat)
            )
        check_dispatch_compatibility(client, checks)
        check_request_recovery(client, checks)
        check_task_lifecycle(client, checks)
        check_query_capture(client, checks)
        check_macro_record_limits(client, checks)
        check_macro_capture(client, checks)
        check_tick_safety(client, checks)
        check_tick_streams(client, checks)
        check_binary_capture(client, checks)
        check_macro_playback(client, checks)
        check_ecs_capture(client, checks)
        check_input_events(client, checks)
        check_flight(client, checks)
        check_player_identity(client, checks)
        check_controls(client, checks)
        check_teleport(client, checks)
        check_tick_recovery(client, checks)
        check_packet_injection(client, checks)
        with observation.gap("violation_selftest"):
            check_violation_watch(client, checks)
        check_send_probe(client, checks)
        check_packet_tap(client, checks)
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
            checks.skip("input_injection_coverage", str(exc))
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

        check_block_identity(client, checks)

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

        # H0.5: the in-detour memory snapshot reads a pointer argument while the
        # call is live. Capture 8 bytes at args[0]+0 and compare with mem_read at
        # the address the mod recorded, so a stale/failed copy cannot pass.
        mem_hook = client.hook_install(
            "MobJump::setJumpPendingScale",
            tag="h0_mem",
            mem_arg=0,
            mem_offset=0,
            mem_size=8,
        )
        if mem_hook.get("event") == "hook_installed":
            client.hook_call("MobJump::setJumpPendingScale", 1.0)
            wait_ticks(client, 3)
            mem_hits = [
                hit
                for hit in decode_hook_hits(client.hook_drain(0))
                if hit.hook == int(mem_hook["id"])
            ]
            latest = mem_hits[-1] if mem_hits else None
            detail: dict[str, object] = {"hits": len(mem_hits)}
            ok = False
            if latest is not None and latest.mem_ok and len(latest.mem) == 8:
                direct = client.mem_read(latest.mem_addr, 8)
                detail.update(
                    {
                        "mem_addr": hex(latest.mem_addr),
                        "hook_hex": latest.mem.hex(),
                        "read_hex": direct.get("hex"),
                        "mem_arg": latest.mem_arg,
                    }
                )
                ok = direct.get("hex") == latest.mem.hex()
            checks.check("h0_hook_mem", ok, str(detail))
            client.hook_rm(int(mem_hook["id"]))
        else:
            checks.check("h0_hook_mem", False, str(mem_hook))

        # H0.4: spool tick order survives a panic.
        mod_spool = Path(str(client.hello.get("spool", "")))
        client.start(80)
        while True:
            event = client.readline(8.0).get("event")
            if event == "stopped":
                break
        with observation.gap("spool_panic"):
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
            checks.skip("packet_capture_coverage", str(exc))
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
                checks.skip("packet_injection_coverage", str(exc))
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
                with observation.gap("packet_panic"):
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
            return 0
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
        with observation.gap("hook_task_panic"):
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
        check_overhead_window(client, checks)
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
            checks.skip("selftest_busy", "allow_selftest_stress is disabled")
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

        # -- dynamic key bindings ------------------------------------------
        bindings = client.state().get("bindings") or {}
        checks.check(
            "key_bindings_read",
            all(
                isinstance(bindings.get(key), int) and 0 <= int(bindings[key]) < 256
                for key in ("forward", "back", "left", "right", "sneak", "jump", "sprint")
            ),
            str(bindings),
        )

        # -- parkour jump/route primitives ---------------------------------
        check_jump_motion(client, checks, inject_enabled)

        check_input_mode_contract(client, checks)

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

        check_record_medium(client, checks)

        # -- swim: SWIMMING is entered with real keys, never state writes ---
        if not inject_enabled:
            checks.skip("swim_drive", "allow_input_inject is disabled")
        else:
            # Absolute fixture: a 3x3, 10-deep water column inside a stone
            # shell at a fixed world position. Nothing here depends on where the
            # player happened to be, so every run starts from the same blocks.
            # Stone shell -64..-54, water interior -63..-54 (surface top -53).
            if not prepare_swim_fixture(client, checks, "swim_pool"):
                return 0

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
            # on flat ground (measured 2026-10-25). The default HID path must
            # keep sprint independent of jump.
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
        cp_target = [float(saved[axis]) for axis in "xyz"]
        cp_gap = max(
            abs(float(now_cp[f"feet_{axis}"]) - value)
            for axis, value in zip("xyz", cp_target, strict=True)
        )
        checks.check(
            "checkpoint_load",
            loaded_cp.get("event") == "checkpoint_loaded" and cp_gap < 0.3,
            f"gap={cp_gap:.3f} target={cp_target} actual={[now_cp[f'feet_{axis}'] for axis in 'xyz']}",
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
        checks.check(
            "teleport",
            gap < 0.2,
            f"gap={gap:.3f} target={feet} actual={[now[f'feet_{axis}'] for axis in 'xyz']} in_water={now.get('in_water')}",
        )

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
            # Validate the positions actually used by this request. A later
            # entity query observes a moving target at a different time.
            eye, target = look["eye"], look["target"]
            expected_yaw = math.degrees(
                math.atan2(
                    -(float(target["x"]) - float(eye["x"])),
                    float(target["z"]) - float(eye["z"]),
                )
            )
            current = client.state()
            yaw_gap = abs((float(current.get("yaw", 0.0)) - expected_yaw + 180.0) % 360.0 - 180.0)
            requested_gap = abs((float(look["yaw"]) - expected_yaw + 180.0) % 360.0 - 180.0)
            checks.check(
                "look_at_entity",
                look.get("event") == "look_at_done"
                and look.get("runtime_id") == runtime_id
                and requested_gap < 1e-3
                and yaw_gap < 0.1,
                f"yaw={current.get('yaw'):.3f} expected={expected_yaw:.3f} requested_gap={requested_gap:.6f}",
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
            if external:
                checks.skip(
                    "macro_event_deterministic", f"real mouse moved the camera; gap={gap:.6f}"
                )
            else:
                checks.check("macro_event_deterministic", gap < 5e-3, f"gap={gap:.6f}")
            checks.check("macro_event_clicks", left_events == 2, f"left={left_events}")
            _, legacy_left, _ = run_macro(False)
            checks.check("macro_legacy_path", legacy_left == 0, f"left={legacy_left}")

            # Macro recording lifecycle: the tick-side append and pipe-side
            # stop must share a boundary, and playback must never be recorded
            # concurrently. A short capped session also exercises the
            # automatic limit path before the explicit save.
            lifecycle_name = "selftest_macro_lifecycle"
            lifecycle_path = mod_dir / "macros" / f"{lifecycle_name}.json"
            with contextlib.suppress(OSError):
                lifecycle_path.unlink()
            client.macro_record_start(max_ticks=3)
            wait_ticks(client, 6)
            limited = client.macro_status()
            checks.check(
                "macro_record_limit_stops",
                not bool(limited.get("recording")) and int(limited.get("recorded_ticks", 0)) >= 3,
                f"recording={limited.get('recording')} ticks={limited.get('recorded_ticks')}",
            )
            saved = client.macro_record_stop(lifecycle_name)
            checks.check(
                "macro_record_limit_save",
                int(saved.get("ticks", 0)) == int(limited.get("recorded_ticks", 0)),
                f"saved={saved.get('ticks')} buffered={limited.get('recorded_ticks')}",
            )
            after_save = client.macro_status()
            checks.check(
                "macro_record_stop_clears_session",
                int(after_save.get("recorded_ticks", -1)) == 0,
                str(after_save.get("recorded_ticks")),
            )

            client.macro_record_start()
            duplicate = client.request({"cmd": "macro_rec_start"}, "error")
            checks.check(
                "macro_record_duplicate_rejected",
                duplicate.get("reason") == "macro_already_recording",
                str(duplicate.get("reason")),
            )
            wait_ticks(client, 1)
            client.macro_record_stop("selftest_macro_duplicate")

            client.macro_run(loop=True, event_level=True)
            wait_ticks(client, 6)
            held_input = client.state().get("input", {})
            checks.check("macro_loop_holds_key", bool(held_input.get("w")), str(held_input))
            reply = client.request({"cmd": "macro_rec_start"}, "error")
            checks.check(
                "macro_record_during_playback_rejected",
                reply.get("reason") == "macro_playing",
                str(reply.get("reason")),
            )
            client.macro_stop()
            wait_ticks(client, 4)
            released_input = client.state().get("input", {})
            checks.check(
                "macro_stop_releases_session",
                not bool(client.macro_status().get("playing"))
                and not any(
                    bool(released_input.get(key)) for key in ("w", "a", "s", "d", "jump", "sneak")
                ),
                str(released_input),
            )

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
            checks.skip("guard_watch_thread_all_rejected", "state has no context address")
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
        return 0
    except WorldLost:
        raise
    except ModPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        dump_packet_tap(client, tap_dump)


if __name__ == "__main__":
    raise SystemExit(main())
