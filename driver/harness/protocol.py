"""Wire-level details shared by the client and the CLI.

The mod writes UTF-8 JSON lines to ``\\\\.\\pipe\\mcbe-harness``. The pipe is in
byte mode, so a single ``read`` may contain a partial line or several lines;
:class:`LineDecoder` turns that stream back into complete messages.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

PIPE_NAME = r"\\.\pipe\mcbe-harness"

DEFAULT_TIMEOUT = 10.0

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

__all__ = ["DEFAULT_TIMEOUT", "PIPE_NAME", "LineDecoder", "kernel32"]
