"""Reusable, side-effect-free fixtures for live selftest domains."""

from __future__ import annotations

from pathlib import Path

from driver.harness import ModClient


class CrashSnapshot:
    """Compare crash artifact names and mtimes around a check's real operations."""

    def __init__(self, client: ModClient) -> None:
        self.directory = Path(str(client.status()["spool"])).parent
        self.before = self._read()

    def _read(self) -> set[tuple[str, int]]:
        return {(path.name, path.stat().st_mtime_ns) for path in self.directory.glob("crash-*")}

    def unchanged(self) -> bool:
        return self._read() == self.before
