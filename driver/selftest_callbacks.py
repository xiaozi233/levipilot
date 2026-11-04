"""Bounded callback drain races followed by real reload acceptance."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from driver.selftest_lifecycle import (
    RELOAD_COMMAND,
    _capture_command_template,
    _check_reloaded_command,
    _hooks_match,
    _wait_lifecycle,
    check_hot_reload,
)
from driver.selftest_runtime import wait_ticks
from driver.selftest_support import Checks
from driver.transport_mod import ModClient, ModTimeout


def _callbacks(client: ModClient) -> dict[str, Any]:
    return dict(client.status()["lifecycle"]["callbacks"])


def _wait_test(client: ModClient, domain: str, field: str, minimum: int) -> dict[str, Any]:
    deadline = time.monotonic() + 6
    while True:
        snapshot = _callbacks(client)
        if int(snapshot["domains"][domain][field]) >= minimum:
            return snapshot
        if time.monotonic() >= deadline:
            raise ModTimeout(f"callback {domain}: {field} < {minimum}: {snapshot}")
        time.sleep(0.01)


def check_callback_drain(client: ModClient, checks: Checks) -> None:
    """Stress-only: three delays, cold timeout recovery, self refusal and reload."""
    original: dict[str, Any] = dict(client.state())
    if not checks.check("callback_preflight", original.get("in_world") is True):
        return
    try:
        _check_callback_drain(client, checks, original)
    finally:
        _restore_callback_fixture(client, checks, original)


def _restore_callback_fixture(client: ModClient, checks: Checks, original: dict[str, Any]) -> None:
    cause = sys.exc_info()[1]
    errors: list[Exception] = []

    def attempt(name: str, action: Callable[[], Any]) -> None:
        try:
            action()
        except Exception as exc:
            checks.check(name, False, str(exc))
            errors.append(exc)

    def restore_pose() -> None:
        client.stop_move()
        wait_ticks(client, 3)
        client.teleport(
            *(float(original[f"feet_{a}"]) for a in "xyz"),
            yaw=float(original["yaw"]),
            pitch=float(original["pitch"]),
        )
        wait_ticks(client, 3)
        final: dict[str, Any] = dict(client.state())
        checks.check(
            "callback_input_and_pose_restored",
            not final["input"]["w"]
            and all(abs(final[f"feet_{a}"] - original[f"feet_{a}"]) < 0.01 for a in "xyz")
            and final["mayfly"] == original["mayfly"]
            and final["flying"] == original["flying"],
            str(final),
        )

    attempt(
        "callback_delays_cleared",
        lambda: client.request(
            {"cmd": "selftest_callback", "action": "clear"}, "callback_test_cleared"
        ),
    )
    attempt(
        "callback_key_released",
        lambda: client.key_event(int(original["bindings"]["forward"]), False, via="hardware"),
    )
    attempt("callback_pose_cleanup", restore_pose)
    attempt("callback_lock_restored", lambda: client.input_lock(bool(original["input"]["locked"])))
    if errors:
        first = cause if cause is not None else errors[0]
        for error in errors:
            if error is not first:
                first.add_note(f"callback cleanup failed: {error}")
        if cause is None:
            raise first


def _check_callback_drain(client: ModClient, checks: Checks, original: dict[str, Any]) -> None:
    for domain in ("client", "server", "tick"):
        client.input_lock(True)
        before = client.status()
        counters = before["lifecycle"]["callbacks"]["domains"][domain]
        entered = int(counters["test_entered"]) + 1
        client.request(
            {"cmd": "selftest_callback", "domain": domain, "delay_ms": 2000},
            "callback_test_armed",
        )
        active = _wait_test(client, domain, "test_entered", entered)
        row = active["domains"][domain]
        checks.check(
            f"callback_{domain}_executing_counted",
            row["running"] > 0 and (domain == "tick" or row["owned"] > 0),
            str(row),
        )
        timeout = client.request(
            {"cmd": "selftest_callback", "action": "drain", "timeout_ms": 40},
            "callback_drain_test",
            timeout=4,
        )
        checks.check(
            f"callback_{domain}_timeout_reopens",
            timeout.get("ok") is False and timeout.get("reopened") is True,
            str(timeout),
        )
        _wait_test(client, domain, "test_completed", entered)
        wait_ticks(client, 3)
        checks.check(
            f"callback_{domain}_hooks_after_timeout",
            _hooks_match(client.status()["lifecycle"], True),
        )
        # The command API runs inside our tracked server task. LL must reject
        # this attempt instead of blocking waiting for the task containing it.
        reply = client.command(RELOAD_COMMAND)
        after = client.status()["lifecycle"]
        checks.check(
            f"callback_{domain}_self_unload_refused",
            reply.get("success") is False
            and after["generation"] == before["lifecycle"]["generation"]
            and after["callbacks"]["refusal"] == "callback_self"
            and after["callbacks"]["closed"] is False,
            str(reply),
        )
        # A completed delayed callback may leave its std::function retained by
        # the executor. Real disable must also wait for ownership retirement.
        check_hot_reload(client, checks, close_client=False)
        after = client.status()["lifecycle"]
        checks.check(
            f"callback_{domain}_new_generation",
            after["generation"] > before["lifecycle"]["generation"],
            str(after),
        )
        wait_ticks(client, 3)
        snapshot = _callbacks(client)
        checks.check(
            f"callback_{domain}_no_old_delayed_entry",
            all(
                row["test_entered"] == row["test_completed"] == 0
                for row in snapshot["domains"].values()
            ),
            str(snapshot),
        )
    for domain in ("client", "server", "tick"):
        check_unprepared_callback_reload(client, checks, domain)
    # The last successful reload discarded the previous DLL's packet template.
    # Capture once before installing diagnostics or holding any movement key.
    template = _capture_command_template(client, timeout=10)
    if not checks.check("callback_refusal_template", template["template"]["ready"]):
        return
    # Diagnostic detours remain installed on refusal; unloading them is a
    # separate explicit action, not part of a failed transaction.
    client.packet_capture(True, directions=["send"])
    try:
        _check_external_refusal(client, checks, "diagnostic", "diagnostic_sources_active")
    finally:
        client.packet_capture(False)
    # Unprepared reload while an injected key is held must leave the running
    # session and its release path intact. Use the actual current binding.
    key = int(original["bindings"]["forward"])
    client.input_lock(True)
    client.key_event(key, True, via="hardware")
    wait_ticks(client, 2)
    held: dict[str, Any] = dict(client.state())
    if checks.check("callback_held_key_active", held["input"]["w"] is True):
        _check_external_refusal(client, checks, "held", "busy")


def _check_external_refusal(client: ModClient, checks: Checks, name: str, reason: str) -> None:
    """Use the game's packet channel so callback_self cannot mask a busy gate."""
    before = client.status()
    boot = Path(str(before["spool"])).parent / "harness-boot.log"
    offset = boot.stat().st_size
    sent = client.chat_send("/" + RELOAD_COMMAND, wait=True, timeout=10)
    if not checks.check(f"callback_{name}_command_sent", sent.get("ok") is True, str(sent)):
        return
    after, evidence = _wait_lifecycle(client, boot.parent, before["lifecycle"], offset, 4)
    lifecycle = after["lifecycle"]
    checks.check(
        f"callback_{name}_unprepared_refused",
        lifecycle["generation"] == before["lifecycle"]["generation"]
        and lifecycle["disable_id"] == before["lifecycle"]["disable_id"]
        and lifecycle["running"] is True
        and _hooks_match(lifecycle, True)
        and lifecycle["callbacks"]["closed"] is False
        and lifecycle["callbacks"]["refusal"] == reason
        and f"disable: refused reason={reason}" in evidence["boot_delta"],
        str(lifecycle),
    )
    path = Path("out") / f"callback-external-{name}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")


def check_unprepared_callback_reload(client: ModClient, checks: Checks, domain: str) -> None:
    """Send first, then hold/queue the callback while the server runs reload."""
    observed = False
    attempts: list[dict[str, Any]] = []
    for _ in range(3):
        client.input_lock(True)
        template = _capture_command_template(client, timeout=10)
        if not checks.check(f"callback_external_{domain}_template", template["template"]["ready"]):
            return
        before = client.status()
        boot = Path(str(before["spool"])).parent / "harness-boot.log"
        offset = boot.stat().st_size
        client.request(
            {
                "cmd": "selftest_callback",
                "action": "after_send",
                "domain": domain,
                "delay_ms": 1000,
            },
            "callback_test_armed",
        )
        client.chat_send("/" + RELOAD_COMMAND)
        status, evidence = _wait_lifecycle(client, boot.parent, before["lifecycle"], offset, 6)
        snapshots = [
            json.loads(line.split("disable: drain snapshot ", 1)[1])
            for line in evidence["boot_delta"].splitlines()
            if "disable: drain snapshot " in line
        ]
        observed = any(
            row["domains"][domain]["running"] > 0 or row["domains"][domain]["owned"] > 0
            for row in snapshots
        )
        advanced = status["lifecycle"]["generation"] > before["lifecycle"]["generation"]
        attempts.append({"observed": observed, "advanced": advanced, "evidence": evidence})
        checks.check(
            f"callback_external_{domain}_finished_or_refused",
            (
                advanced
                or status["lifecycle"]["callbacks"]["refusal"]
                in ("callback_timeout", "callback_source_timeout", "callback_self")
            )
            and not status["lifecycle"]["callbacks"]["closed"]
            and _hooks_match(status["lifecycle"], True),
            str(status["lifecycle"]),
        )
        if not advanced:
            client.request({"cmd": "selftest_callback", "action": "clear"}, "callback_test_cleared")
            check_hot_reload(client, checks, close_client=False)
        else:
            _check_reloaded_command(client, checks)
        wait_ticks(client, 3)
        snapshot = _callbacks(client)
        checks.check(
            f"callback_external_{domain}_no_old_entry",
            all(
                row["test_entered"] == row["test_completed"] == 0
                for row in snapshot["domains"].values()
            ),
        )
        if observed:
            break
    path = Path("out") / f"callback-external-{domain}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(attempts, indent=2), encoding="utf-8")
    checks.check(f"callback_external_{domain}_observed_at_disable", observed, str(path))
