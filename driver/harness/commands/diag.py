"""Diagnostics, version profiles and stress/fault entry points (T7/T9/T1)."""

from __future__ import annotations

from typing import Any

from ..connection import PipeConnection


class DiagnosticsCommands(PipeConnection):
    def overhead(self) -> dict[str, Any]:
        """Tick/hook timing percentiles (T9)."""
        return self.request({"cmd": "overhead"}, "overhead")

    def diagnose(self) -> dict[str, Any]:
        """Executable version, anchor addresses and signature matches (T7)."""
        return self.request({"cmd": "diagnose"}, "diagnose")

    def diag(self) -> dict[str, Any]:
        """Report which server-side objects the mod can see (teleport diagnostics)."""
        return self.request({"cmd": "diag"}, "diag")

    def profile_dump(self) -> dict[str, Any]:
        """Version profile read from the live build; ``profile`` is saveable JSON."""
        return self.request({"cmd": "profile_dump"}, "profile_dump")

    def profile_probe(self, build_id: str) -> dict[str, Any]:
        """Report which profile a given build id would select (porting check)."""
        return self.request({"cmd": "profile_probe", "build_id": build_id}, "profile_probe")

    def signature_check(self, name: str, expected_hex: str) -> dict[str, Any]:
        return self.request(
            {"cmd": "signature_check", "name": name, "expected_hex": expected_hex},
            "signature_check",
        )

    def symbol_resolve(self, name: str) -> dict[str, Any]:
        return self.request({"cmd": "symbol_resolve", "name": name}, "symbol_resolve")

    def selftest_busy(self, us: int = 1000, ticks: int = 20) -> dict[str, Any]:
        """Burn ``us`` microseconds in the tick for ``ticks`` ticks (metric proof)."""
        return self.request(
            {"cmd": "selftest_busy", "us": int(us), "ticks": int(ticks)},
            "busy_test_started",
        )

    def selftest_av(self, timeout: float | None = None) -> dict[str, Any]:
        """Raise an access violation inside the mod; expects an ``internal_error``.

        This is the isolation check from T1: the game must keep ticking.
        """
        return self.request({"cmd": "selftest_av"}, ("error", "selftest_fault_done"), timeout)

    def panic(self, level: str = "all", timeout: float | None = None) -> dict[str, Any]:
        """Remove hooks/watches and cancel tasks without killing the game."""
        if level not in {"hooks", "tasks", "all"}:
            raise ValueError("level must be hooks, tasks or all")
        return self.request({"cmd": "panic", "level": level}, "panic_done", timeout=timeout)

    def spool_flush(self, timeout: float | None = None) -> dict[str, Any]:
        return self.request({"cmd": "spool_flush"}, "spool_flushed", timeout=timeout)
