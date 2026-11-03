"""Explicit live lifecycle acceptance checks with integrity evidence."""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from driver.harness.errors import ModPipeError, ModProtocolError, ModTimeout
from driver.selftest_support import Checks, preserve_failure
from driver.transport_mod import ModClient


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _crash_snapshot(directory: Path) -> dict[str, dict[str, Any]]:
    return {
        path.name: {
            "size": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
            "sha256": _sha256(path),
        }
        for path in directory.glob("crash-*")
    }


def _reconnect(client: ModClient, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            client.connect()
            return
        except (ModPipeError, OSError) as exc:
            last = exc
            client.close()
            time.sleep(0.5)
    raise ModTimeout(f"hot reload: pipe did not return: {last}")


RELOAD_COMMAND = "ll reload probe"
MISSING_RELOAD_COMMAND = "ll reload __levipilot_missing__"
TEMPLATE_PROBE_COMMAND = "version"


def _wait_screen(client: ModClient, expected: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        actual = client.screen().get("name")
        if actual == expected:
            return
        if time.monotonic() >= deadline:
            raise ModTimeout(f"hot reload: screen={actual}, want={expected}")
        time.sleep(0.05)


def _capture_command_template(client: ModClient, timeout: float) -> dict[str, Any]:
    """Send one harmless command through the chat UI to capture the packet template.

    ``chat_send`` reuses the last real CommandRequestPacket; nothing has been
    typed through the UI yet in a freshly started session, and the template is
    only captured while the send-side packet tap is active. This must happen
    before ``lifecycle_prepare`` drops the tick hook, because every key we
    inject afterwards would never be applied.
    """
    status = client.status()
    before = int(status["command_template_attempts"])
    faults_before = int(status["command_template_faults"])
    capture = client.packet_capture(True, directions=["send"])
    try:
        client.key_event(191, via="hardware")
        client.key_event(191, False, via="hardware")
        _wait_screen(client, "chat_screen", 8)
        client.text_input(TEMPLATE_PROBE_COMMAND, wait=True, timeout=10)
        client.key_event(13, via="hardware")
        client.key_event(13, False, via="hardware")
        deadline = time.monotonic() + timeout
        attempts = before
        faults = faults_before
        while time.monotonic() < deadline:
            status = client.status()
            attempts = int(status["command_template_attempts"])
            faults = int(status["command_template_faults"])
            template = status.get("command_template", {})
            if (attempts > before or faults > faults_before) and (
                not isinstance(template, dict) or template.get("reason") != "command_template_busy"
            ):
                break
            time.sleep(0.05)
        screen = client.screen().get("name")
        if screen == "chat_screen":
            client.key_event(27, via="hardware")
            client.key_event(27, False, via="hardware")
            with contextlib.suppress(ModTimeout):
                _wait_screen(client, "hud_screen", 3)
    finally:
        client.packet_capture(False)
    # Capture can finish before its reserved row is published. Read its actual
    # usability after UI/capture cleanup, while every hook is still installed.
    status = client.status()
    attempts = int(status["command_template_attempts"])
    faults = int(status["command_template_faults"])
    template = status.get("command_template")
    return {
        "command": f"/{TEMPLATE_PROBE_COMMAND}",
        "capture": capture,
        "attempts_before": before,
        "attempts_after": attempts,
        "faults_before": faults_before,
        "faults_after": faults,
        "template": template,
        "screen_after": client.screen().get("name"),
    }


def _hooks_match(reply: dict[str, Any], armed: bool) -> bool:
    hooks = reply.get("hooks")
    if not isinstance(hooks, dict):
        return False
    return all(hooks.get(name) is armed for name in ("key", "mouse", "player", "turn"))


def _lifecycle(status: dict[str, Any]) -> dict[str, Any]:
    value = status.get("lifecycle", {})
    return value if isinstance(value, dict) else {}


def _boot_delta(path: Path, offset: int) -> tuple[str, bool]:
    data = path.read_bytes()
    if len(data) < offset:
        return "", True
    return data[offset:].decode("utf-8", errors="replace"), False


def _expected_lifecycle_records(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    old_generation = int(before.get("generation", 0))
    old_disable = int(before.get("disable_id", 0))
    new_generation = int(after.get("generation", 0))
    return [
        f"disable: begin lifecycle={old_generation} disable_id={old_disable + 1}",
        f"disable: done lifecycle={old_generation} disable_id={old_disable + 1} ok=1",
        f"enable: begin lifecycle={new_generation}",
        f"enable: done lifecycle={new_generation}",
    ]


def _reject_reload(
    client: ModClient,
    checks: Checks,
    directory: Path,
    initial_status: dict[str, Any],
    boot_offset: int,
) -> tuple[bool, dict[str, Any]]:
    reply = client.command(MISSING_RELOAD_COMMAND)
    history = client.chat_history(limit=8)
    after = client.status()
    boot_delta, truncated = _boot_delta(directory / "harness-boot.log", boot_offset)
    before_lifecycle = _lifecycle(initial_status)
    after_lifecycle = _lifecycle(after)
    rejected = reply.get("success") is False and "__levipilot_missing__" in json.dumps(
        history, ensure_ascii=False
    )
    unchanged = (
        after_lifecycle == before_lifecycle
        and not truncated
        and "enable: begin" not in boot_delta
        and "disable: begin" not in boot_delta
    )
    checks.check(
        "hot_reload_rejected_command",
        rejected,
        f"reply={reply} history={history}",
    )
    checks.check(
        "hot_reload_rejected_no_reload",
        unchanged,
        f"lifecycle={after_lifecycle} boot_delta={boot_delta!r}",
    )
    return rejected and unchanged, {
        "reply": reply,
        "history": history,
        "lifecycle": after_lifecycle,
        "boot_delta": boot_delta,
        "boot_truncated": truncated,
    }


def _wait_lifecycle(
    client: ModClient,
    directory: Path,
    before: dict[str, Any],
    boot_offset: int,
    timeout: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while True:
        try:
            status = client.status()
        except (ModPipeError, OSError):
            # The reload tears the old pipe down before the new instance serves
            # it; a gone pipe is expected here, a silent one past the deadline is not.
            if time.monotonic() >= deadline:
                raise
            client.close()
            with contextlib.suppress(ModPipeError, OSError):
                client.connect()
            time.sleep(0.2)
            continue
        after = _lifecycle(status)
        boot_delta, truncated = _boot_delta(directory / "harness-boot.log", boot_offset)
        expected = _expected_lifecycle_records(before, after)
        advanced = int(after.get("generation", 0)) > int(before.get("generation", 0)) and int(
            after.get("disable_id", 0)
        ) > int(before.get("disable_id", 0))
        evidence = {
            "before": before,
            "after": after,
            "boot_delta": boot_delta,
            "boot_truncated": truncated,
            "expected_records": expected,
        }
        if advanced and not truncated and all(marker in boot_delta for marker in expected):
            return status, evidence
        if time.monotonic() >= deadline:
            return status, evidence
        time.sleep(0.05)


def check_hot_reload(
    client: ModClient,
    checks: Checks,
    *,
    report_path: Path | None = None,
    lifecycle_timeout: float = 10.0,
) -> None:
    """Require rejection safety, new lifecycle IDs and complete reload records."""
    started = time.monotonic()
    report_path = report_path or (
        Path(__file__).resolve().parents[1] / "out" / f"hot-reload-{time.monotonic_ns()}.json"
    )
    report: dict[str, Any] = {
        "kind": "hot_reload",
        "method": "CommandRequestPacket after lifecycle_prepare",
        "command": RELOAD_COMMAND,
        "started_unix": time.time(),
    }
    checks.note("hot_reload_report", str(report_path))
    original = client.state()
    initial_status = client.status()
    directory = Path(str(initial_status["spool"])).parent
    expected_spool = Path(str(initial_status["spool"])).stem
    boot_offset = (directory / "harness-boot.log").stat().st_size
    report["boot_offset"] = boot_offset
    before = {
        "crashes": _crash_snapshot(directory),
        "config": _sha256(directory / "harness.json"),
        "dll": _sha256(directory / "probe.dll"),
    }
    report["before"] = before

    def write_report() -> None:
        report["elapsed_seconds"] = time.monotonic() - started
        report["checks"] = [asdict(row) for row in checks.results]
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            checks.note("hot_reload_report_failed", str(exc))

    prepared = False
    try:
        initial_macro = client.macro_status()
        if not checks.check(
            "hot_reload_preflight",
            original.get("in_world") is True
            and initial_macro.get("playing") is False
            and initial_macro.get("recording") is False,
            str(initial_macro),
        ):
            return
        initial_lifecycle = _lifecycle(initial_status)
        if not checks.check(
            "hot_reload_lifecycle_ready",
            int(initial_lifecycle.get("generation", 0)) > 0
            and initial_lifecycle.get("running") is True,
            str(initial_lifecycle),
        ):
            return
        rejected, rejection = _reject_reload(client, checks, directory, initial_status, boot_offset)
        report["rejected"] = rejection
        if not rejected:
            return
        client.input_lock(True)
        template = _capture_command_template(client, timeout=10)
        report["template"] = template
        if not checks.check(
            "hot_reload_template_ready",
            template["attempts_after"] > template["attempts_before"]
            and template["faults_after"] == template["faults_before"]
            and isinstance(template["template"], dict)
            and template["template"].get("ready") is True,
            str(template),
        ):
            return
        prepared = True
        preparation = client.request({"cmd": "lifecycle_prepare", "on": False}, "lifecycle_prepare")
        report["prepare"] = preparation
        if not checks.check(
            "hot_reload_hook_prepared",
            preparation.get("on") is False
            and preparation.get("ok") is True
            and preparation.get("quiescing") is True
            and _hooks_match(preparation, False),
            str(preparation),
        ):
            return
        # The unload contract must actually stop admitting work: a normal
        # request has to be refused while quiesced.
        try:
            client.state()
            blocked_reason = ""
        except ModProtocolError as exc:
            blocked_reason = str(exc)
        checks.check(
            "hot_reload_quiesce_blocks_work",
            "quiescing" in blocked_reason,
            blocked_reason or "state was served while quiesced",
        )
        # The mod's tick hook drives every injected key, so input injection is
        # gone once the hooks are prepared for unload. Submit the same
        # CommandRequestPacket the chat UI sends instead of typing into it.
        try:
            submitted = client.chat_send("/" + RELOAD_COMMAND, wait=True, timeout=10)
        except ModProtocolError as exc:
            # An explicit refusal (no template, injection disabled, fault) is a
            # failed step, not an uncertain delivery.
            submitted = {"rejected": str(exc)}
        except (ModPipeError, ModTimeout) as exc:
            submitted = {"disconnected_after_send": str(exc)}
        report["submission"] = {"command": RELOAD_COMMAND, "chat_send": submitted}
        if not checks.check(
            "hot_reload_command_sent",
            submitted.get("ok") is True or "disconnected_after_send" in submitted,
            str(submitted),
        ):
            return
        client.close()
        _reconnect(client, 45)
        status, lifecycle_evidence = _wait_lifecycle(
            client, directory, initial_lifecycle, boot_offset, lifecycle_timeout
        )
        report["lifecycle"] = lifecycle_evidence
        after_lifecycle = lifecycle_evidence["after"]
        generation_advanced = int(after_lifecycle.get("generation", 0)) > int(
            initial_lifecycle.get("generation", 0)
        ) and int(after_lifecycle.get("disable_id", 0)) > int(
            initial_lifecycle.get("disable_id", 0)
        )
        if prepared and not generation_advanced:
            # Nothing was reloaded, so the mod is still quiesced with its hooks
            # removed. Lift that before probing it further; otherwise every
            # later request fails for the wrong reason.
            prepared = False
            with contextlib.suppress(ModPipeError, ModTimeout, ModProtocolError):
                report["rollback"] = client.request(
                    {"cmd": "lifecycle_prepare", "on": True}, "lifecycle_prepare"
                )
        client.wait_ready(timeout=30, require_world=True)
        reconnected = client.state()
        checks.check(
            "hot_reload_reconnected",
            reconnected.get("in_world") is True
            and status.get("version_ok") is True
            and Path(str(status["spool"])).stem == expected_spool,
            str(status.get("spool")),
        )
        checks.check(
            "hot_reload_lifecycle_changed",
            generation_advanced,
            str(after_lifecycle),
        )
        expected_records = lifecycle_evidence["expected_records"]
        boot_delta = lifecycle_evidence["boot_delta"]
        checks.check(
            "hot_reload_boot_records",
            not lifecycle_evidence["boot_truncated"]
            and all(marker in boot_delta for marker in expected_records),
            boot_delta,
        )
        tick = int(reconnected["tick"])
        deadline = time.monotonic() + 5
        while int(client.state()["tick"]) <= tick:
            if time.monotonic() >= deadline:
                raise ModTimeout("hot reload: tick did not resume")
            time.sleep(0.05)
        advanced = int(client.state()["tick"])
        checks.check("hot_reload_tick_alive", advanced > tick, f"{tick} -> {advanced}")
        final = client.state()
        macro = client.macro_status()
        try:
            report["history"] = client.chat_history(limit=16)
        except (ModPipeError, OSError) as exc:
            report["history_error"] = str(exc)
        client.input_lock(False)
        final = client.state()
        final_input = final.get("input", {})
        checks.check(
            "hot_reload_input_released",
            isinstance(final_input, dict)
            and final_input.get("locked") is False
            and all(not final_input.get(key) for key in ("w", "a", "s", "d", "jump")),
            str(final_input),
        )
        checks.check(
            "hot_reload_macro_idle",
            macro.get("playing") is False
            and macro.get("recording") is False
            and macro.get("release_pending") is False
            and status.get("tick_recovery", {}).get("active") is False,
            str(macro),
        )
        after = {
            "crashes": _crash_snapshot(directory),
            "config": _sha256(directory / "harness.json"),
            "dll": _sha256(directory / "probe.dll"),
        }
        report["after"] = after
        checks.check("hot_reload_config_unchanged", after["config"] == before["config"])
        checks.check("hot_reload_dll_unchanged", after["dll"] == before["dll"])
        checks.check("hot_reload_no_crash_changes", after["crashes"] == before["crashes"])
        report["state"] = final
        report["macro"] = macro
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        # Restore, close and persist are independent: a failing restore must not
        # skip the close or lose the report, and the first error stays first.
        try:
            if prepared:
                with preserve_failure(checks, "hot_reload"):
                    try:
                        restored = client.request(
                            {"cmd": "lifecycle_prepare", "on": True}, "lifecycle_prepare"
                        )
                        report["hook_restored"] = restored
                        checks.check(
                            "hot_reload_hook_restored",
                            restored.get("on") is True
                            and restored.get("ok") is True
                            and _hooks_match(restored, True),
                            str(restored),
                        )
                    except Exception as exc:
                        # Record before preserve_failure suppresses a secondary
                        # exception, so JSON retains both failures as well.
                        report["cleanup_error"] = f"{type(exc).__name__}: {exc}"
                        checks.check("hot_reload_hook_restored", False, str(exc))
                        raise
        finally:
            try:
                client.close()
            except Exception as exc:
                report["close_error"] = f"{type(exc).__name__}: {exc}"
                checks.note("hot_reload_close_failed", str(exc))
            write_report()
