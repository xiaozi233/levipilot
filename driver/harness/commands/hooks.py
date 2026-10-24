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
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Install an enter hook by LL symbol or (gated) VA; dry_run only validates."""
        if symbol is None and va is None:
            raise ValueError("symbol or va is required")
        message: dict[str, Any] = {
            "cmd": "hook_install",
            "tag": tag,
            "sample": int(sample),
            "dry_run": bool(dry_run),
        }
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
