"""Named-pipe connection, JSONL framing, request deadlines and I/O failures.

This layer knows no game commands. Reads stay on a polling thread so Windows
synchronous pipe reads cannot block writes on the same handle.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import msvcrt
import queue
import threading
import time
from ctypes import wintypes
from typing import Any, Self

from .errors import ModDisconnected, ModPipeError, ModProtocolError, ModTimeout
from .protocol import DEFAULT_TIMEOUT, PIPE_NAME, LineDecoder, kernel32


class PipeConnection:
    """Own one pipe connection and its ordered stream of responses/events."""

    def __init__(
        self,
        pipe_name: str = PIPE_NAME,
        connect_timeout: float = 10.0,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.pipe_name = pipe_name
        self.connect_timeout = connect_timeout
        self.timeout = timeout
        self.protocol = 0
        self.hello: dict[str, Any] = {}
        self._file: Any = None
        self._reader: threading.Thread | None = None
        self._lines: queue.Queue[dict[str, Any]] = queue.Queue()
        self._closed = threading.Event()

    def connect(self) -> Self:
        self.close()
        deadline = time.monotonic() + self.connect_timeout
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            try:
                # Named-pipe paths are not filesystem paths, and the handle must
                # outlive this block (the reader thread owns it), so neither
                # Path.open nor a context manager applies.
                handle = open(self.pipe_name, "r+b", buffering=0)  # noqa: PTH123, SIM115
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)
                continue

            self._file = handle
            self._lines = queue.Queue()
            self._closed.clear()
            self._reader = threading.Thread(
                target=self._read_loop,
                args=(handle,),
                name="mod-pipe-reader",
                daemon=True,
            )
            self._reader.start()
            try:
                hello = self._next_line(self.connect_timeout)
            except ModPipeError:
                self.close()
                raise
            if hello.get("event") != "hello":
                self.close()
                raise ModPipeError(f"握手失败：{hello!r}")
            self.protocol = int(hello.get("protocol", 0))
            self.hello = hello
            return self
        raise ModPipeError(f"无法连接 {self.pipe_name}: {last_error}")

    def reconnect(self) -> Self:
        """Drop the current pipe and open a fresh one (game restart, mod reload)."""
        return self.connect()

    def ensure_connected(self) -> Self:
        if self._file is None:
            return self.connect()
        return self

    def _read_loop(self, handle: Any) -> None:
        """Poll for data instead of blocking in ``read``.

        Windows serialises I/O on a synchronous pipe handle: a ``ReadFile``
        that is waiting for data also blocks ``WriteFile`` on the same handle.
        ``PeekNamedPipe`` lets the reader wait without a pending read, so a
        request can always be written while the reader is idle.
        """
        decoder = LineDecoder()
        os_handle = msvcrt.get_osfhandle(handle.fileno())
        try:
            while not self._closed.is_set():
                available = wintypes.DWORD(0)
                ok = kernel32.PeekNamedPipe(
                    os_handle,
                    None,
                    0,
                    None,
                    ctypes.byref(available),
                    None,
                )
                if not ok:
                    break
                if available.value == 0:
                    time.sleep(0.005)
                    continue
                chunk = handle.read(available.value)
                if not chunk:
                    break
                for raw in decoder.feed(chunk):
                    try:
                        message = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(message, dict):
                        self._lines.put(message)
        except (OSError, ValueError):
            pass
        finally:
            self._lines.put({"event": "__closed__"})

    def _next_line(self, timeout: float | None = None) -> dict[str, Any]:
        wait = self.timeout if timeout is None else timeout
        try:
            message = self._lines.get(timeout=max(0.0, wait))
        except queue.Empty as exc:
            raise ModTimeout(f"等待 mod 响应超时（{wait:.1f}s）") from exc
        if message.get("event") == "__closed__":
            self._file = None
            raise ModDisconnected("pipe 已关闭（游戏退出或 mod 被卸载）")
        return message

    def close(self) -> None:
        self._closed.set()
        handle = self._file
        self._file = None
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()
        reader = self._reader
        self._reader = None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)

    def send(self, message: dict[str, Any]) -> None:
        if self._file is None:
            raise ModDisconnected("尚未连接")
        raw = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        self._write(raw)

    def send_raw(self, payload: bytes) -> None:
        """Write bytes without appending a newline (used by the line-bound test)."""
        self._write(payload)

    def _write(self, payload: bytes) -> None:
        """Finish short writes and translate all pipe I/O failures consistently."""
        if self._file is None:
            raise ModDisconnected("尚未连接")
        view = memoryview(payload)
        try:
            while view:
                written = self._file.write(view)
                if not written:
                    raise OSError("pipe write returned 0")
                view = view[written:]
            self._file.flush()
        except (OSError, ValueError) as exc:
            self.close()
            raise ModDisconnected(f"pipe 写入失败：{exc}") from exc

    def readline(self, timeout: float | None = None) -> dict[str, Any]:
        return self._next_line(timeout)

    def request(
        self,
        message: dict[str, Any],
        expect: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        wait = self.timeout if timeout is None else timeout
        self.send(message)
        deadline = time.monotonic() + wait
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close()
                raise ModTimeout(f"{message.get('cmd')} 等待 {expect or '响应'} 超时")
            try:
                reply = self._next_line(remaining)
            except ModTimeout:
                # A half-consumed stream is worse than no connection.
                self.close()
                raise
            event = reply.get("event")
            if event == "error" and expect != "error":
                raise ModProtocolError(f"{message.get('cmd')}: {reply.get('reason', reply)}")
            if expect is None or event == expect:
                return reply

    def __enter__(self) -> Self:
        return self.connect()

    def __exit__(self, *_exc: object) -> None:
        self.close()
