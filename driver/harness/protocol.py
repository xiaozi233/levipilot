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
    feet_x: NotRequired[float]
    feet_y: NotRequired[float]
    feet_z: NotRequired[float]
    yaw: NotRequired[float]
    pitch: NotRequired[float]
    on_ground: NotRequired[bool]
    health: NotRequired[float]
    context: NotRequired[int]
    effects: NotRequired[list[dict[str, float | str]]]


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


class LineDecoder:
    """Incrementally split a byte stream into newline-terminated lines."""

    __slots__ = ("_buffer",)

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[str]:
        if not chunk:
            return []
        self._buffer.extend(chunk)
        lines: list[str] = []
        while True:
            index = self._buffer.find(b"\n")
            if index < 0:
                break
            raw = bytes(self._buffer[:index])
            del self._buffer[: index + 1]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            if raw:
                lines.append(raw.decode("utf-8", errors="replace"))
        return lines

    def pending(self) -> bytes:
        return bytes(self._buffer)


# Backwards-compatible private name used by the old module and its tests.
_LineDecoder = LineDecoder

__all__ = [
    "DEFAULT_TIMEOUT",
    "PIPE_NAME",
    "HelloEvent",
    "LineDecoder",
    "StateEvent",
    "kernel32",
]
