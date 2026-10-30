"""Runtime enter detours (T5)."""

from __future__ import annotations

from typing import Any

from ..connection import PipeConnection


def remember_ring_head(client: Any, kind: str, ident: int) -> None:
    """Best-effort: remember the ring head right after an install.

    Both rings live in the mod process, so draining from 0 replays rows left by
    earlier hooks — including hooks from previous experiments in the same
    process, with their own ``mem`` shapes. Remembering the head at install time
    lets ``hook_drain()`` / ``watch_drain()`` default to "what this one saw".
    """
    try:
        listing = client.hook_list() if kind == "hook" else client.watch_list()
    except Exception:  # the head is a convenience; never fail an install over it
        return
    head = listing.get("head")
    if isinstance(head, int):
        heads = client.__dict__.setdefault("_ring_heads", {})
        heads[(kind, int(ident))] = head
        client.__dict__["_last_ring"] = (kind, int(ident))


def ring_head(client: Any, kind: str, ident: int | None) -> int:
    """Head recorded for ``ident``, or for the last install of that kind."""
    heads = client.__dict__.get("_ring_heads") or {}
    key = (kind, ident) if ident is not None else client.__dict__.get("_last_ring")
    if isinstance(key, tuple) and key[0] == kind:
        head = heads.get(key)
        if isinstance(head, int):
            return head
    return 0


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
        mem_deref: bool = False,
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
            if int(mem_size) % 4 or not 4 <= int(mem_size) <= 128:
                raise ValueError("mem_size must be a multiple of 4 between 4 and 128")
            if not -4096 <= int(mem_offset) <= 4096:
                raise ValueError("mem_offset must be within -4096..4096")
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
            if mem_deref:
                message["mem_deref"] = True
        if symbol is not None:
            message["symbol"] = symbol
        else:
            message["va"] = va
        reply = self.request(message, ("hook_installed", "hook_dry_run"), timeout)
        if reply.get("event") == "hook_installed" and reply.get("id") is not None:
            remember_ring_head(self, "hook", int(reply["id"]))
        return reply

    def hook_rm(self, hook_id: int) -> dict[str, Any]:
        return self.request({"cmd": "hook_rm", "id": int(hook_id)}, "hook_removed")

    def hook_list(self) -> dict[str, Any]:
        return self.request({"cmd": "hook_list"}, "hook_list")

    def hook_drain(
        self,
        since: int | None = None,
        *,
        hook_id: int | None = None,
        limit: int = 4096,
    ) -> dict[str, Any]:
        """Drain hook hits; by default starting at the last install's ring head.

        ``since=None`` starts at that hook's install-time ring head, so the raw
        ring (which is process-global) does not leak rows from earlier hooks.
        Pass an explicit ``since`` to page through the ring yourself; pass
        ``hook_id`` to also filter the returned rows to one hook.
        """
        resolved = ring_head(self, "hook", hook_id) if since is None else int(since)
        reply = self.request(
            {"cmd": "hook_drain", "since": resolved, "limit": int(limit)},
            "hook_hits",
        )
        if hook_id is not None:
            reply["hits"] = [hit for hit in reply.get("hits", []) if hit.get("hook") == hook_id]
        return reply

    def hook_call(
        self, symbol: str, scale: float = 1.0, *, timeout: float | None = None
    ) -> dict[str, Any]:
        return self.request(
            {"cmd": "hook_call", "symbol": symbol, "scale": float(scale)},
            "hook_call_done",
            timeout=timeout,
        )
