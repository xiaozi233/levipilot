"""Packet capture/decode and the command-packet family (H1/H2)."""

from __future__ import annotations

from typing import Any

from ..connection import PipeConnection


class PacketCommands(PipeConnection):
    def packet_capture(
        self,
        on: bool = True,
        *,
        ids: list[int] | None = None,
        sample: int = 1,
        serialize_ids: list[int] | None = None,
        directions: list[str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"cmd": "packet_capture", "on": bool(on), "sample": int(sample)}
        if ids is not None:
            message["ids"] = [int(value) for value in ids]
        if serialize_ids is not None:
            message["serialize_ids"] = [int(value) for value in serialize_ids]
        if directions is not None:
            message["directions"] = [str(value) for value in directions]
        return self.request(message, "packet_capture", timeout=timeout)

    def packet_drain(self, since: int = 0, limit: int = 1024) -> dict[str, Any]:
        return self.request(
            {"cmd": "packet_drain", "since": int(since), "limit": int(limit)},
            "packet_drain",
        )

    def packet_bytes(self, since: int = 0, limit: int = 256) -> dict[str, Any]:
        return self.request(
            {"cmd": "packet_bytes", "since": int(since), "limit": int(limit)},
            "packet_bytes",
        )

    def packet_stats(self) -> dict[str, Any]:
        return self.request({"cmd": "packet_stats"}, "packet_stats")

    # -- H2: command packets ---------------------------------------------------
    def op_self(self, level: int = 4, timeout: float | None = None) -> dict[str, Any]:
        return self.request({"cmd": "op_self", "level": int(level)}, "op_self", timeout=timeout)

    def commands_enabled(
        self, value: bool | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"cmd": "commands_enabled"}
        if value is not None:
            message["value"] = bool(value)
        return self.request(message, "commands_enabled", timeout=timeout)

    def text_input(self, text: str) -> dict[str, Any]:
        return self.request({"cmd": "text_input", "text": text}, "text_input_queued")

    def chat_send(self, text: str, timeout: float | None = None) -> dict[str, Any]:
        return self.request({"cmd": "chat_send", "text": text}, "chat_send_queued", timeout=timeout)
