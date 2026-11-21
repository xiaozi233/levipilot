"""Connection, world-entry and transport recovery acceptance checks."""

from __future__ import annotations

import contextlib
import gc
import itertools
import json
import subprocess
import sys
import time
import weakref
from pathlib import Path
from typing import Any

from driver.harness import ModClient, ModPipeError, ModTimeout
from driver.harness.lab import capture_disconnect_report
from driver.harness.record import decode_dump
from driver.harness.spool import read_spool
from driver.selftest_runtime import ROOT, wait_ticks
from driver.selftest_support import Checks, preserve_failure


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
                raise ModTimeout(
                    "entry lifecycle: menu not ready: "
                    f"entry={result.get('entry')} menu={result.get('menu')} "
                    f"leave={result.get('leave_completed')}/{result.get('leave_request')}"
                )
            time.sleep(0.1)

    def no_duplicate(entry_id: int) -> bool:
        with contextlib.suppress(ModTimeout):
            client.wait_enter_settled(0.3, entry_id=entry_id)
            return False
        return True

    try:
        left = client.leave_world()
        leave_id = int(left.get("leave_id", 0))
        checks.check("entry_lifecycle_leave", left.get("ok") is True)
        left_event = client.wait_event("world_left", 45)
        checks.check(
            "entry_lifecycle_leave_correlation",
            leave_id > 0 and left_event.get("leave_id") == leave_id,
            f"result={left} event={left_event}",
        )
        ready_menu = menu_ready()
        checks.check("entry_lifecycle_menu", ready_menu.get("ready") is True)
        checks.check(
            "entry_lifecycle_menu_diagnostics",
            ready_menu.get("leave_completed") == leave_id
            and ready_menu.get("menu", {}).get("server_instance") is False
            and ready_menu.get("menu", {}).get("world_list_available") is True,
            str(ready_menu.get("menu")),
        )
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
        with preserve_failure(checks, "entry_lifecycle"):
            if not client.state().get("in_world"):
                menu_ready()
                joined = client.enter_world_ready(world)
                checks.check("entry_lifecycle_restore_join", joined.get("ok") is True, str(joined))
            client.wait_in_world(timeout=60)
            client.teleport(
                *(float(original[f"feet_{axis}"]) for axis in "xyz"),
                yaw=float(original["yaw"]),
                pitch=float(original["pitch"]),
            )
            wait_ticks(client, 8)
            checks.check("entry_lifecycle_world_recovery", client.state().get("in_world") is True)


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
                client.teleport(*pose, yaw=float(original["yaw"]), pitch=float(original["pitch"]))
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
            client.teleport(*pose, yaw=float(original["yaw"]), pitch=float(original["pitch"]))
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
    for command, expected_reason in (
        ({"cmd": "set", "sprint": "invalid"}, "bad_command"),
        ({"cmd": "move", "ticks": "invalid"}, "bad_param"),
        ({"cmd": "look", "relative": "invalid"}, "bad_command"),
    ):
        reply = client.request(command, "error")
        checks.check(
            f"request_reject_{command['cmd']}",
            reply.get("reason") == expected_reason,
            str(reply.get("reason")),
        )
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
        with preserve_failure(checks, "chat_lifecycle"):
            joined = client.enter_world_ready(world)
            checks.check("chat_lifecycle_rejoin", joined.get("ok") is True, str(joined))
            client.wait_in_world(timeout=60)
            client.teleport(
                *(float(original[f"feet_{axis}"]) for axis in "xyz"),
                yaw=float(original["yaw"]),
                pitch=float(original["pitch"]),
            )
            wait_ticks(client, 8)
            result = client.chat_history(limit=1)
            checks.check("chat_lifecycle_world_recovery", result.get("ok") is True, str(result))
