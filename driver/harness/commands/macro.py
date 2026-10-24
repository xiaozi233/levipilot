"""Macro load/run/record control (G4)."""

from __future__ import annotations

from typing import Any

from ..connection import PipeConnection


class MacroCommands(PipeConnection):
    def macro_load(self, name: str = "default", path: str | None = None) -> dict[str, Any]:
        message: dict[str, Any] = {"cmd": "macro_load", "name": name}
        if path is not None:
            message["path"] = path
        return self.request(message, "macro_loaded")

    def macro_run(
        self,
        *,
        start: int = 0,
        end: int | None = None,
        loop: bool = False,
        event_level: bool = False,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "cmd": "macro_run",
            "start": int(start),
            "loop": bool(loop),
            "event_level": bool(event_level),
        }
        if end is not None:
            message["end"] = int(end)
        return self.request(message, "macro_started")

    def macro_stop(self) -> dict[str, Any]:
        return self.request({"cmd": "macro_stop"}, "macro_stopped")

    def macro_status(self) -> dict[str, Any]:
        return self.request({"cmd": "macro_status"}, "macro_status")

    def macro_list(self) -> dict[str, Any]:
        return self.request({"cmd": "macro_list"}, "macro_list")

    def macro_record_start(self, max_ticks: int = 0) -> dict[str, Any]:
        return self.request(
            {"cmd": "macro_rec_start", "max_ticks": int(max_ticks)}, "macro_rec_started"
        )

    def macro_record_stop(self, name: str = "recorded") -> dict[str, Any]:
        return self.request({"cmd": "macro_rec_stop", "name": name}, "macro_rec_saved")

    def macro_save(self, name: str, ticks: list[list[str]]) -> dict[str, Any]:
        return self.request({"cmd": "macro_save", "name": name, "ticks": ticks}, "macro_saved")
