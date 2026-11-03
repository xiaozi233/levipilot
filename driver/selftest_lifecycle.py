"""Explicit live lifecycle acceptance checks with integrity evidence."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from driver.harness.errors import ModPipeError, ModTimeout
from driver.selftest_support import Checks
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


def _wait_screen(client: ModClient, expected: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        actual = client.screen().get("name")
        if actual == expected:
            return
        if time.monotonic() >= deadline:
            raise ModTimeout(f"hot reload: screen={actual}, want={expected}")
        time.sleep(0.05)


def check_hot_reload(
    client: ModClient,
    checks: Checks,
    *,
    report_path: Path | None = None,
) -> None:
    """Reload probe through chat input, then verify the same live process."""
    started = time.monotonic()
    report_path = report_path or (
        Path(__file__).resolve().parents[1] / "out" / f"hot-reload-{time.monotonic_ns()}.json"
    )
    report: dict[str, Any] = {
        "kind": "hot_reload",
        "method": "Hardware HID chat input, not probe command dispatch",
        "started_unix": time.time(),
    }
    checks.note("hot_reload_report", str(report_path))
    original = client.state()
    initial_status = client.status()
    directory = Path(str(initial_status["spool"])).parent
    expected_spool = Path(str(initial_status["spool"])).stem
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
        client.input_lock(True)
        client.key_event(191, via="hardware")
        client.key_event(191, False, via="hardware")
        _wait_screen(client, "chat_screen", 8)
        text = client.text_input("ll mod reload probe", wait=True, timeout=10)
        enter = client.key_event(13, via="hardware")
        client.key_event(13, False, via="hardware")
        try:
            applied = client.wait_event(
                "input_event_done",
                5,
                predicate=lambda row: row.get("input_id") == enter["input_id"],
            )
        except ModPipeError as exc:
            applied = {"disconnected_after_enter": str(exc)}
        report["submission"] = {"text": text, "enter": applied}
        checks.check(
            "hot_reload_chat_submitted",
            text.get("ok") is True and applied.get("ok") is not False,
            str(applied),
        )
        client.close()
        _reconnect(client, 45)
        client.wait_ready(timeout=30, require_world=True)
        reconnected = client.state()
        status = client.status()
        checks.check(
            "hot_reload_reconnected",
            reconnected.get("in_world") is True
            and status.get("version_ok") is True
            and Path(str(status["spool"])).stem == expected_spool,
            str(status.get("spool")),
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
        try:
            client.close()
        except Exception as exc:
            report["close_error"] = f"{type(exc).__name__}: {exc}"
            checks.note("hot_reload_close_failed", str(exc))
        write_report()
