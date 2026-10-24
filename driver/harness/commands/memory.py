"""Memory primitives (T2)."""

from __future__ import annotations

from typing import Any

from ..connection import PipeConnection
from ..memory import parse_hex_bytes, to_hex


class MemoryCommands(PipeConnection):
    def mem_read(
        self, addr: int | str, length: int = 16, *, timeout: float | None = None
    ) -> dict[str, Any]:
        """Read up to 4096 bytes; a bad address returns ``ok=false/bad_addr``."""
        return self.request(
            {"cmd": "mem_read", "addr": addr, "len": int(length)},
            "mem_read",
            timeout=timeout,
        )

    def mem_write(
        self,
        addr: int | str,
        data: bytes | bytearray | str,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Write bytes (requires ``allow_write=true`` in harness.json).

        ``data`` may be raw bytes or a hex string. The mod rejects the request
        with ``write_disabled`` when writes are not enabled.
        """
        hex_text = data if isinstance(data, str) else to_hex(data)
        normalized = parse_hex_bytes(hex_text).hex()
        return self.request(
            {"cmd": "mem_write", "addr": addr, "hex": normalized},
            "mem_write",
            timeout=timeout,
        )

    def probe(
        self,
        addr: int | str,
        kind: str = "raw",
        *,
        length: int = 16,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Structured read: ``raw`` | ``block`` | ``legacy``."""
        return self.request(
            {"cmd": "probe", "addr": addr, "kind": kind, "len": int(length)},
            "probe",
            timeout=timeout,
        )
