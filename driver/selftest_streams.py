"""Recording, tick-stream, motion and failure-recovery live checks."""

from __future__ import annotations

import contextlib
import itertools
import math
import time
from pathlib import Path
from typing import Any

from driver.harness.lab import record_session, settle
from driver.harness.record import decode_dump
from driver.parkour import read_until_run_end
from driver.selftest_runtime import (
    SWIM_X,
    SWIM_Z,
    jump_arcs,
    prepare_swim_fixture,
    require_world,
    wait_still,
    wait_ticks,
)
from driver.selftest_support import Checks, preserve_failure
from driver.transport_mod import ModClient, ModPipeError, ModProtocolError, ModTimeout


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
            client.teleport(*pose, yaw=float(original["yaw"]), pitch=float(original["pitch"]))
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
        with preserve_failure(checks, "jump_motion"):
            if inject_enabled:
                client.key_event(32, False, via="hid")
            client.stop_move()
            client.record_bin_stop()
            client.teleport(*pose, yaw=float(original["yaw"]), pitch=float(original["pitch"]))
            wait_ticks(client, 8)


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
        for phase in ("pre", "post"):
            rebase()
            row = fault(phase, "client_read")
            recovered = ready()
            checks.check(
                f"tick_recovery_{phase}_client_read_isolated",
                row.get("message") == "client_player_read_fault"
                and recovered["tick_recovery"]["active"] is False
                and bool(client.ping()),
                ascii(row),
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
            with preserve_failure(checks, "binary_capture"):
                joined = client.enter_world_ready(world)
                checks.check("binary_world_rejoin", joined.get("ok") is True, str(joined))
                client.wait_in_world(timeout=60)
                client.teleport(
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
