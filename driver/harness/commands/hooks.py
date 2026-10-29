"""Runtime enter detours (T5)."""

from __future__ import annotations

from typing import Any

from ..connection import PipeConnection


class HookCommands(PipeConnection):
    def hook_install(
        self,
        symbol: str | None = None,
        *,
        va: int | str | None = None,
        tag: str = "",
        sample: int = 1,
        dry_run: bool = False,
        mem_arg: int | None = None,
        mem_offset: int = 0,
        mem_size: int = 0,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Install an enter hook by LL symbol or (gated) VA; dry_run only validates.

        ``mem_size`` asks the detour to snapshot ``mem_size`` bytes at
        ``args[mem_arg] + mem_offset`` on every hit. Use it for buffers that are
        dead by the time ``hook_drain`` runs (per-tick vectors, AABBs passed by
        pointer); the copy happens inside the detour, allocation-free.
        """
        if symbol is None and va is None:
            raise ValueError("symbol or va is required")
        if mem_size:
            if mem_arg is None or not 0 <= int(mem_arg) <= 3:
                raise ValueError("mem_arg must be 0..3 when mem_size is set")
            if int(mem_size) % 4 or not 4 <= int(mem_size) <= 64:
                raise ValueError("mem_size must be a multiple of 4 between 4 and 64")
        message: dict[str, Any] = {
            "cmd": "hook_install",
            "tag": tag,
            "sample": int(sample),
            "dry_run": bool(dry_run),
        }
        if mem_size:
            # Omit the keys entirely when unused: the plain install keeps the
            # wire shape captured in tests/fixtures/cli-baseline.jsonl.
            message["mem_arg"] = int(mem_arg or 0)
            message["mem_offset"] = int(mem_offset)
            message["mem_size"] = int(mem_size)
        if symbol is not None:
            message["symbol"] = symbol
        else:
            message["va"] = va
        return self.request(message, ("hook_installed", "hook_dry_run"), timeout)

    def hook_rm(self, hook_id: int) -> dict[str, Any]:
        return self.request({"cmd": "hook_rm", "id": int(hook_id)}, "hook_removed")

    def hook_list(self) -> dict[str, Any]:
        return self.request({"cmd": "hook_list"}, "hook_list")

    def hook_drain(self, since: int = 0, *, limit: int = 4096) -> dict[str, Any]:
        return self.request(
            {"cmd": "hook_drain", "since": int(since), "limit": int(limit)},
            "hook_hits",
        )

    def hook_call(
        self, symbol: str, scale: float = 1.0, *, timeout: float | None = None
    ) -> dict[str, Any]:
        return self.request(
            {"cmd": "hook_call", "symbol": symbol, "scale": float(scale)},
            "hook_call_done",
            timeout=timeout,
        )
