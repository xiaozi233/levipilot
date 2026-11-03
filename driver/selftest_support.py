"""Reporting and diagnostic lifetimes shared by the live selftest entry points."""

from __future__ import annotations

import contextlib
import json
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from driver.harness.errors import ModPipeError
from driver.transport_mod import ModClient


@dataclass(frozen=True)
class CheckResult:
    name: str
    outcome: Literal["pass", "fail", "skip"]
    detail: str
    required: bool = True


class Checks:
    def __init__(self, *, require_complete: bool = False) -> None:
        self.results: list[CheckResult] = []
        self.require_complete = require_complete

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        ok = bool(condition)
        self.results.append(CheckResult(name, "pass" if ok else "fail", detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
        return ok

    def skip(self, name: str, detail: str, *, required: bool = True) -> None:
        self.results.append(CheckResult(name, "skip", detail, required))
        print(f"SKIP  {name}  ({detail})")

    def note(self, name: str, detail: str = "") -> None:
        print(f"NOTE  {name}" + (f"  ({detail})" if detail else ""))

    def finish(self) -> int:
        failed = [row for row in self.results if row.outcome == "fail"]
        skipped = [row for row in self.results if row.outcome == "skip"]
        required_skips = [row for row in skipped if row.required]
        passed = len(self.results) - len(failed) - len(skipped)
        print(
            f"\n{passed}/{len(self.results)} checks passed "
            f"({len(failed)} failed, {len(skipped)} skipped, "
            f"{len(required_skips)} required skips)"
        )
        for row in failed:
            print(f"  failed: {row.name} {row.detail}", file=sys.stderr)
        if self.require_complete:
            for row in required_skips:
                print(f"  missing required coverage: {row.name} {row.detail}", file=sys.stderr)
        return 1 if failed or (self.require_complete and required_skips) else 0


@contextlib.contextmanager
def preserve_failure(checks: Checks, where: str) -> Iterator[None]:
    """Run cleanup while keeping the exception already unwinding as the first error."""
    cause = sys.exc_info()[1]
    try:
        yield
    except Exception as cleanup:
        if cause is None:
            raise
        cause.add_note(f"{where} cleanup failed: {cleanup}")
        checks.note(f"{where}_cleanup_failed", str(cleanup))


class ViolationObservation:
    """Optional watch around every live mode; export before closing the pipe.

    Explicit gaps preserve panic's removal semantics. A gap starts before the
    destructive check and ends only after both hooks have been confirmed again.
    The report describes observed intervals, not a claim of uninterrupted cover.
    """

    def __init__(
        self,
        client: ModClient,
        checks: Checks,
        path: str | None,
        address: Callable[[], int | None],
    ) -> None:
        self.client = client
        self.checks = checks
        self.path = Path(path) if path else None
        self.address = address
        self.since = 0
        self.was_active: bool | None = None
        self.events: list[dict[str, Any]] = []

    def event(self, kind: str, **fields: Any) -> None:
        self.events.append({"kind": kind, "monotonic_ns": time.monotonic_ns(), **fields})

    def start(self) -> None:
        if self.path is None:
            return
        try:
            stats = self.client.violation_stats()
            self.was_active = stats.get("active") is True
            self.since = int(self.client.violation_drain(0, 1).get("head", 0))
            self.event("start", stats=stats, since=self.since)
            self.resume("start")
        except (ModPipeError, OSError, ValueError) as exc:
            self.event("error", where="start", error=str(exc))
            self.checks.check("violation_session_start", False, str(exc))

    def resume(self, where: str) -> bool:
        if self.path is None:
            return True
        try:
            address = self.address()
            if address is None:
                raise ValueError("violation role/image base unavailable")
            reply = self.client.violation_watch(True, addr=address)
            ok = reply.get("active") is True and reply.get("screen_hooked") is True
            self.event("armed" if ok else "arm_failed", where=where, reply=reply)
            self.checks.check("violation_session_armed", ok, where)
            return ok
        except (ModPipeError, OSError, ValueError) as exc:
            self.event("error", where=where, error=str(exc))
            self.checks.check("violation_session_armed", False, f"{where}: {exc}")
            return False

    @contextlib.contextmanager
    def gap(self, where: str) -> Iterator[None]:
        if self.path is not None:
            self.event("gap_begin", where=where)
        try:
            yield
        finally:
            if self.path is not None:
                if self.resume(where):
                    self.event("gap_end", where=where)
                else:
                    self.event("gap_uncovered", where=where)

    @contextlib.contextmanager
    def reload_gap(self) -> Iterator[None]:
        """Preserve the old DLL's ring, then observe the new ring from zero."""
        if self.path is None:
            yield
            return
        generation: int | None = None
        try:
            generation = int(self.client.status()["lifecycle"]["generation"])
            rows = self.client.violation_drain(self.since, 256)
            texts = self.client.violation_text(0, 8)
            self.event(
                "checkpoint",
                where="hot_reload",
                generation=generation,
                records=rows,
                texts=texts if isinstance(texts, dict) else None,
            )
        except (ModPipeError, OSError, ValueError, KeyError) as exc:
            self.event("error", where="reload_checkpoint", error=str(exc))
            self.checks.check("violation_session_checkpoint", False, str(exc))
        self.event("gap_begin", where="hot_reload")
        try:
            yield
        finally:
            try:
                current = int(self.client.status()["lifecycle"]["generation"])
                if generation is not None and current != generation:
                    previous_since, self.since = self.since, 0
                    self.event(
                        "ring_reset",
                        previous_generation=generation,
                        generation=current,
                        previous_since=previous_since,
                    )
                if self.resume("hot_reload"):
                    self.event("gap_end", where="hot_reload")
                else:
                    self.event("gap_uncovered", where="hot_reload")
            except (ModPipeError, OSError, ValueError, KeyError) as exc:
                self.event("gap_uncovered", where="hot_reload", error=str(exc))
                self.checks.check("violation_session_reloaded", False, str(exc))

    def finish(self) -> None:
        if self.path is None:
            return
        report: dict[str, Any] = {
            "event": "violation_drain",
            "since": self.since,
            "hits": [],
            "count": 0,
            "observation": {"events": self.events, "initially_active": self.was_active},
        }
        try:
            report["observation"]["final_stats"] = self.client.violation_stats()
            report.update(self.client.violation_drain(self.since, 256))
            # The rows keep a bounded prefix; this is the whole string, which is
            # where the numbers in `read() overflow ...` survive.
            try:
                reply = self.client.violation_text(0, 8)
                # A protocol stub answers with a Mock; only a real reply has hits.
                texts = reply.get("hits") if isinstance(reply, dict) else None
                if isinstance(texts, list):
                    report["texts"] = texts
            except (AttributeError, ModPipeError, OSError, ValueError) as text_exc:
                report["texts_error"] = str(text_exc)
            report["real_count"] = sum(not row.get("synthetic", False) for row in report["hits"])
            report["synthetic_count"] = report["count"] - report["real_count"]
        except (ModPipeError, OSError, ValueError) as exc:
            self.event("error", where="drain", error=str(exc))
            self.checks.check("violation_session_drain", False, str(exc))
        finally:
            if self.was_active is False:
                try:
                    stopped = self.client.violation_watch(False)
                    self.event("stop", reply=stopped)
                    self.checks.check("violation_session_stopped", stopped.get("active") is False)
                except (ModPipeError, OSError) as exc:
                    self.event("error", where="stop", error=str(exc))
                    self.checks.check("violation_session_stopped", False, str(exc))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            self.checks.note("violation_dump", f"{report['count']} rows -> {self.path}")
        except OSError as exc:
            self.checks.check("violation_session_dump", False, str(exc))
