"""Diagnostics, version profiles and stress/fault entry points (T7/T9/T1)."""

from __future__ import annotations

from typing import Any

from ..connection import PipeConnection
from ..errors import ModProtocolError, ModTimeout


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

    def violation_watch(
        self,
        on: bool = True,
        *,
        addr: int | str | None = None,
        loop_addr: int | str | None = None,
        mode: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Read-only watch on the client's protocol-violation boundary.

        ``addr`` is the relocated runtime address of the role from
        ``docs/roles/<version>.json`` (``@client_handle_packet_violation``),
        computed as module_base + (role_va - image_base).
        Needs ``allow_hook_va`` and an active world. ``mode="synthetic"``
        publishes one self-test row so the record/drain path can be exercised
        without a real violation.
        """
        message: dict[str, Any] = {"cmd": "violation_watch", "on": bool(on)}
        if addr is not None:
            message["addr"] = hex(addr) if isinstance(addr, int) else addr
        if loop_addr is not None:
            message["loop_addr"] = hex(loop_addr) if isinstance(loop_addr, int) else loop_addr
        if mode:
            message["mode"] = mode
        expected = "violation_synthetic" if mode == "synthetic" else "violation_watch"
        return self.request(message, (expected, "error"), timeout=timeout)

    def disconnect_route(self, reason: int = 90, timeout: float | None = None) -> dict[str, Any]:
        """Render the error page route for ``reason`` through the real symbol.

        Verifies the screen-side record without waiting for a real disconnect;
        the route embeds the reason and codeword the UI would show.
        """
        return self.request(
            {"cmd": "disconnect_route", "reason": int(reason)}, "disconnect_route", timeout=timeout
        )

    def inbound_drain(self, since: int = 0, limit: int = 512) -> dict[str, Any]:
        """Inbound parse-boundary sizes: ``{packet_id, packet_size, error_value}``.

        ``packet_size`` is the slice length the caller handed to
        ``Packet::readNoHeader`` and ``error_value`` the stream read pointer at
        entry; comparing a packet's size here with its sender-side serialized
        length tells a short frame apart from a schema mismatch.
        """
        return self.request(
            {"cmd": "inbound_drain", "since": int(since), "limit": int(limit)},
            "inbound_drain",
        )

    def violation_text(self, since: int = 0, limit: int = 8) -> dict[str, Any]:
        """Full-length violation / disconnect-page strings, unbounded by the row.

        The record rows keep a bounded prefix so they stay fixed size; this ring
        copies the whole game string into a preallocated 4 KB slot, so a long
        error-details block arrives intact instead of truncated.
        """
        return self.request(
            {"cmd": "violation_text", "since": int(since), "limit": int(limit)},
            "violation_text",
        )

    def inbound_sample(self, sample: int = 4, timeout: float | None = None) -> dict[str, Any]:
        """Set the parse-boundary sampling stride (1 = keep every packet).

        Use 1 for a reproduction run: the packet that breaks is the last one
        before the disconnect, and a 1:4 stride can skip exactly that packet.
        """
        return self.request(
            {"cmd": "inbound_sample", "sample": int(sample)}, "inbound_sample", timeout=timeout
        )

    def violation_stats(self) -> dict[str, Any]:
        return self.request({"cmd": "violation_stats"}, "violation_stats")

    def violation_drain(self, since: int = 0, limit: int = 512) -> dict[str, Any]:
        return self.request(
            {"cmd": "violation_drain", "since": int(since), "limit": int(limit)},
            "violation_drain",
        )

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
        """Cancel workflows, preserving their terminal events before/after the receipt."""
        if level not in {"hooks", "tasks", "all"}:
            raise ValueError("level must be hooks, tasks or all")
        with self.exchange():
            self.send({"cmd": "panic", "level": level})
            try:
                reply = self.wait_event(
                    ("panic_done", "error"),
                    timeout,
                    # A cancelled workflow may publish its error before the
                    # panic receipt. It belongs to that workflow's consumer.
                    predicate=lambda row: (
                        not (
                            row.get("event") == "error"
                            and row.get("reason") == "panic"
                            and row.get("phase")
                        )
                    ),
                )
            except ModTimeout:
                self.close()  # A late unnumbered receipt must not match a new panic.
                raise
            if reply.get("event") == "error":
                raise ModProtocolError(f"panic: {reply.get('reason', reply)}")
            return reply

    def spool_flush(self, timeout: float | None = None) -> dict[str, Any]:
        return self.request({"cmd": "spool_flush"}, "spool_flushed", timeout=timeout)
