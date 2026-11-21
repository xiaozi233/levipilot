"""Wire-level details shared by the client and the CLI.

The mod writes UTF-8 JSON lines to ``\\\\.\\pipe\\mcbe-harness``. The pipe is in
byte mode, so a single ``read`` may contain a partial line or several lines;
:class:`LineDecoder` turns that stream back into complete messages.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import NotRequired, TypedDict

PIPE_NAME = r"\\.\pipe\mcbe-harness"

DEFAULT_TIMEOUT = 10.0


class HelloEvent(TypedDict):
    """Handshake reply; ``replay_rows``/``queue_dropped`` report spool backlog."""

    protocol: int
    spool: str
    capabilities: list[str]
    replay_rows: NotRequired[int]
    queue_dropped: NotRequired[int]


class StateEvent(TypedDict):
    """``state`` reply. Everything except ``in_world`` is only sent in a world."""

    in_world: bool
    tick: NotRequired[int]
    x: NotRequired[float]
    y: NotRequired[float]
    z: NotRequired[float]
    vx: NotRequired[float]
    vy: NotRequired[float]
    vz: NotRequired[float]
    pos_delta_x: NotRequired[float]
    pos_delta_y: NotRequired[float]
    pos_delta_z: NotRequired[float]
    feet_x: NotRequired[float]
    feet_y: NotRequired[float]
    feet_z: NotRequired[float]
    yaw: NotRequired[float]
    pitch: NotRequired[float]
    prev_yaw: NotRequired[float]
    prev_pitch: NotRequired[float]
    on_ground: NotRequired[bool]
    health: NotRequired[float]
    context: NotRequired[int]
    effects: NotRequired[list[dict[str, float | str]]]
    ok: NotRequired[bool]
    partial: NotRequired[bool]
    truncated: NotRequired[bool]
    warnings: NotRequired[list[str]]
    sample_us: NotRequired[int]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.PeekNamedPipe.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.PeekNamedPipe.restype = wintypes.BOOL


MAX_LINE_BYTES = 16 * 1024 * 1024


class LineTooLongError(ValueError):
    """Raised when a single JSONL line exceeds ``max_line_bytes``."""


class LineDecoder:
    """Incrementally split a byte stream into newline-terminated lines."""

    __slots__ = ("_buffer", "_max_line_bytes", "_scan_from")

    def __init__(self, max_line_bytes: int = MAX_LINE_BYTES) -> None:
        if max_line_bytes <= 0:
            raise ValueError("max_line_bytes must be positive")
        self._buffer = bytearray()
        self._scan_from = 0
        self._max_line_bytes = int(max_line_bytes)

    def feed(self, chunk: bytes) -> list[str]:
        if not chunk:
            return []
        self._buffer.extend(chunk)
        lines: list[str] = []
        start = 0
        while True:
            # Bytes retained from the previous feed contain no newline.
            index = self._buffer.find(b"\n", self._scan_from)
            if index < 0:
                limit = self._max_line_bytes + (1 if self._buffer.endswith(b"\r") else 0)
                if len(self._buffer) - start > limit:
                    self._buffer.clear()
                    self._scan_from = 0
                    raise LineTooLongError(f"line exceeds {self._max_line_bytes} bytes")
                break
            end = index - 1 if index > start and self._buffer[index - 1] == 13 else index
            if end - start > self._max_line_bytes:
                self._buffer.clear()
                self._scan_from = 0
                raise LineTooLongError(f"line exceeds {self._max_line_bytes} bytes")
            if end > start:
                lines.append(self._buffer[start:end].decode("utf-8", errors="replace"))
            start = self._scan_from = index + 1
        # Compact once per chunk, rather than once per decoded line.
        if start:
            del self._buffer[:start]
        self._scan_from = len(self._buffer)
        return lines

    def pending(self) -> bytes:
        return bytes(self._buffer)


# Backwards-compatible private name used by the old module and its tests.
_LineDecoder = LineDecoder

__all__ = [
    "DEFAULT_TIMEOUT",
    "MAX_LINE_BYTES",
    "PIPE_NAME",
    "HelloEvent",
    "LineDecoder",
    "LineTooLongError",
    "StateEvent",
    "kernel32",
]
