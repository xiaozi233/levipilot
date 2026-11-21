"""Macro recording fixtures and explicit full-length acceptance checks."""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from driver.harness import ModClient
from driver.harness.errors import ModPipeError, ModTimeout
from driver.selftest_support import Checks

MACRO_RECORD_LIMIT = 200000


def _record_event(record_id: int) -> Callable[[dict[str, Any]], bool]:
    def matches(row: dict[str, Any]) -> bool:
        return row.get("record_id") == record_id

    return matches


def inspect_saved_macro(path: Path, expected_ticks: int) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    rows = json.loads(path.read_text(encoding="utf-8"))
    well_formed = isinstance(rows, list) and all(
        isinstance(row, list) and len(row) == 11 and all(isinstance(value, str) for value in row)
        for row in rows
    )
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
        "ticks": len(rows) if isinstance(rows, list) else 0,
        "first": rows[0] if isinstance(rows, list) and rows else None,
        "last": rows[-1] if isinstance(rows, list) and rows else None,
        "well_formed": well_formed,
        "valid": well_formed and len(rows) == expected_ticks,
    }


def check_macro_long_recording(
    client: ModClient,
    checks: Checks,
    *,
    report_path: Path | None = None,
    timeout: float = MACRO_RECORD_LIMIT / 15 + 300,
    resume: bool = False,
) -> None:
    """Record or resume the production limit, then save and reload the same frames."""
    started_at = time.monotonic()
    report_path = report_path or (
        Path(__file__).resolve().parents[1]
        / "out"
        / f"macro-long-recording-{time.monotonic_ns()}.json"
    )
    report: dict[str, Any] = {
        "kind": "macro_long_recording",
        "expected_ticks": MACRO_RECORD_LIMIT,
        "timeout_seconds": timeout,
        "started_unix": time.time(),
        "samples": [],
    }
    checks.note("macro_long_report", str(report_path))
    original = client.state()
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    name = f"selftest_macro_long_{time.monotonic_ns()}"
    macro_path = directory / "macros" / f"{name}.json"
    record_id = 0
    saved_ok = False

    def write_report() -> None:
        report["elapsed_seconds"] = time.monotonic() - started_at
        report["checks"] = [asdict(row) for row in checks.results]
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            checks.note("macro_long_report_failed", str(exc))

    try:
        if original.get("in_world") is not True:
            original = client.wait_in_world(timeout=30)
        if not checks.check("macro_long_in_world", original.get("in_world") is True):
            return
        initial = client.macro_status()
        resumed_from = int(initial.get("recorded_ticks", 0)) if resume else 0
        can_resume = (
            int(initial.get("record_id", 0)) > 0
            and initial.get("playing") is False
            and initial.get("record_limit") == MACRO_RECORD_LIMIT
            and 0 < resumed_from <= MACRO_RECORD_LIMIT
            and (initial.get("recording") is True or resumed_from == MACRO_RECORD_LIMIT)
        )
        if not checks.check(
            "macro_long_preflight",
            can_resume
            if resume
            else initial.get("recording") is False
            and initial.get("playing") is False
            and initial.get("recorded_ticks") == 0,
            str(initial),
        ):
            return
        started: dict[str, Any]
        if resume:
            record_id = int(initial["record_id"])
            started = {
                "event": "macro_rec_resumed",
                "record_id": record_id,
                "max_ticks": MACRO_RECORD_LIMIT,
                "hard_limit": MACRO_RECORD_LIMIT,
            }
            active = initial
            report["resumed_from_ticks"] = resumed_from
            checks.note("macro_long_resume", f"record_id={record_id}, ticks={resumed_from}")
        else:
            client.stop_move()
            started = client.macro_record_start(MACRO_RECORD_LIMIT)
            record_id = int(started.get("record_id", 0))
            active = client.macro_status()
        report["record_id"] = record_id
        if not checks.check(
            "macro_long_started",
            record_id > 0
            and started.get("max_ticks") == MACRO_RECORD_LIMIT
            and started.get("hard_limit") == MACRO_RECORD_LIMIT
            and (active.get("recording") is True or resumed_from == MACRO_RECORD_LIMIT)
            and active.get("record_limit") == MACRO_RECORD_LIMIT,
            str(started),
        ):
            return

        deadline = time.monotonic() + timeout
        last_count = resumed_from - 1
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModTimeout(
                    f"macro long recording: {client.macro_status().get('recorded_ticks')}"
                    f"/{MACRO_RECORD_LIMIT} ticks after {timeout:.0f}s"
                )
            try:
                done = client.wait_event(
                    "macro_rec_limit",
                    min(30.0, remaining),
                    predicate=lambda row: row.get("record_id") == record_id,
                )
                break
            except ModTimeout:
                current = client.macro_status()
                count = int(current.get("recorded_ticks", 0))
                if count < last_count or count > MACRO_RECORD_LIMIT:
                    raise ModTimeout(
                        f"macro long recording: invalid progress {last_count} -> {count}"
                    ) from None
                last_count = count
                elapsed = time.monotonic() - started_at
                sample = {
                    "elapsed_seconds": elapsed,
                    "recorded_ticks": count,
                    "recording": current.get("recording"),
                    "observed_hz": (count - resumed_from) / elapsed if elapsed > 0 else 0,
                }
                report["samples"].append(sample)
                checks.note(
                    "macro_long_progress",
                    f"{count}/{MACRO_RECORD_LIMIT} ticks, {sample['observed_hz']:.3f} Hz",
                )
                write_report()
        report["terminal"] = done
        retained = client.macro_status()
        if not checks.check(
            "macro_long_limit",
            done.get("record_id") == record_id
            and done.get("ok") is True
            and done.get("reason") == "max_ticks"
            and done.get("ticks") == MACRO_RECORD_LIMIT
            and retained.get("recording") is False
            and retained.get("recorded_ticks") == MACRO_RECORD_LIMIT,
            str(done),
        ):
            return
        saved = client.macro_record_stop(name)
        report["saved"] = saved
        saved_ok = saved.get("ok") is True and saved.get("record_id") == record_id
        checks.check(
            "macro_long_saved",
            saved_ok and saved.get("ticks") == MACRO_RECORD_LIMIT,
            str(saved),
        )
        report["file"] = inspect_saved_macro(macro_path, MACRO_RECORD_LIMIT)
        checks.check(
            "macro_long_file",
            report["file"]["valid"] is True,
            json.dumps({k: v for k, v in report["file"].items() if k not in ("first", "last")}),
        )
        loaded = client.macro_load(name)
        loaded_status = client.macro_status()
        report["reload"] = {"reply": loaded, "status": loaded_status}
        checks.check(
            "macro_long_reload",
            loaded.get("event") == "macro_loaded"
            and loaded_status.get("ticks") == MACRO_RECORD_LIMIT
            and loaded_status.get("playing") is False,
            str(loaded_status),
        )
        client.stop_move()
        restored = client.state()
        restored_input = restored.get("input", {})
        checks.check(
            "macro_long_restored",
            restored.get("in_world") is True
            and isinstance(restored_input, dict)
            and restored_input.get("w") is False
            and restored_input.get("jump") is False,
        )
        after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
        checks.check("macro_long_no_crash_artifacts", after == before)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["record_id"] = record_id
        if record_id and not saved_ok:
            try:
                status = client.macro_status()
                if status.get("recording") or status.get("recorded_ticks"):
                    partial = client.request(
                        {"cmd": "macro_rec_stop", "name": name}, "macro_rec_saved"
                    )
                    report["partial_save"] = partial
                    if macro_path.exists():
                        report["partial_file"] = inspect_saved_macro(
                            macro_path, int(status.get("recorded_ticks", 0))
                        )
            except Exception as cleanup:
                report["cleanup_error"] = f"{type(cleanup).__name__}: {cleanup}"
                checks.note("macro_long_cleanup_failed", str(cleanup))
        if saved_ok and all(row.outcome == "pass" for row in checks.results):
            try:
                macro_path.unlink(missing_ok=True)
            except OSError as cleanup:
                report["cleanup_error"] = f"{type(cleanup).__name__}: {cleanup}"
                checks.note("macro_long_cleanup_failed", str(cleanup))
        write_report()


def run_macro_record_limits(
    client: ModClient,
    checks: Checks,
    wait_ticks: Callable[[ModClient, int], int],
) -> None:
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


def run_macro_capture(
    client: ModClient,
    checks: Checks,
    world: str | None,
    wait_ticks: Callable[[ModClient, int], int],
    settle: Callable[..., dict[str, Any]],
) -> None:
    """Check production sampling ownership, retained partial data and cold saving."""
    original = cast(dict[str, Any], client.state())
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
                predicate=_record_event(int(started["record_id"])),
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
                predicate=_record_event(int(started["record_id"])),
            )
            saved, rows = save_raw(f"{mode}_recovery")
            checks.check(
                f"macro_capture_{mode}_recovery",
                saved.get("ok") is True
                and len(rows) == 3
                and rows[0][9:] == ["0", "0"]
                and bool(client.ping()),
            )

        # A real save failure must preserve the stopped session for retry. The
        # fixture makes the target a directory, so the atomic write fails at the
        # replace step ("cannot replace"); a read-only temp fails earlier
        # ("cannot write"). Both stages must keep the recording for a retry.
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
            reason = str(failed.get("reason"))
            checks.check(
                "macro_capture_save_failure_retains",
                ("cannot replace" in reason or "cannot write" in reason)
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

        atomic_name = name_for("atomic")
        atomic_path = directory / "macros" / f"{atomic_name}.json"
        old_rows = [["false"] * 9 + ["0", "0"]]
        new_rows = [["true"] + ["false"] * 8 + ["1", "2"]]
        client.macro_save(atomic_name, old_rows)
        old_bytes = atomic_path.read_bytes()
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateFileW.restype = ctypes.c_void_p
        handle = kernel32.CreateFileW(str(atomic_path), 0x80000000, 1, None, 3, 0x80, None)
        checks.check(
            "macro_atomic_lock_fixture",
            handle != ctypes.c_void_p(-1).value,
            str(handle),
        )
        try:
            failed = client.request(
                {"cmd": "macro_save", "name": atomic_name, "ticks": new_rows}, "error"
            )
            checks.check(
                "macro_atomic_replace_failure_preserves_old",
                "cannot replace" in str(failed.get("reason"))
                and atomic_path.read_bytes() == old_bytes,
                str(failed),
            )
        finally:
            if handle != ctypes.c_void_p(-1).value:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
        checks.check(
            "macro_atomic_failure_cleans_temp",
            not list(atomic_path.parent.glob(f"{atomic_path.name}.tmp-*")),
        )
        client.macro_save(atomic_name, new_rows)
        checks.check(
            "macro_atomic_save_reload",
            json.loads(atomic_path.read_text(encoding="utf-8")) == new_rows
            and client.macro_load(atomic_name).get("event") == "macro_loaded"
            and client.macro_status().get("ticks") == 1,
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
