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
from collections import deque
from collections.abc import Callable, Iterator
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any, Self, cast

from .errors import ModDisconnected, ModPipeError, ModProtocolError, ModTimeout
from .protocol import DEFAULT_TIMEOUT, PIPE_NAME, HelloEvent, LineDecoder, kernel32

# Win32 errors seen when the pipe cannot be opened, with the action that
# actually fixes them. "Invalid argument" on its own sends people hunting the
# wrong bug (the usual cause is a wedged mod-side pipe thread, not the client).
_PIPE_HINTS: dict[int, str] = {
    2: "游戏没开或 probe mod 没加载（先 dev.py run，确认 mods/probe 已安装）",
    5: "拒绝访问：用同一用户运行游戏与脚本（不要混用管理员/普通权限）",
    87: "管道实例状态异常（多为上一次脚本把 mod 的管道线程卡住）；重启游戏",
    231: "管道忙：另一个客户端或上一次的脚本还连着；关掉它或重启游戏",
    232: "管道正在关闭；等游戏回到主菜单后重试",
    109: "管道已断开；等游戏回到主菜单后重试",
}


def pipe_error_hint(error: OSError | None) -> str:
    """Map the last CreateFile failure to an actionable sentence."""
    if error is None:
        return "连接超时：游戏没有在 connect_timeout 内出现可用的管道"
    winerror = getattr(error, "winerror", None)
    if winerror is None:
        winerror = getattr(error, "errno", None)
    return _PIPE_HINTS.get(int(winerror or 0), "确认游戏在运行且已进入存档")


@dataclass
class _PipeSession:
    """All reader-visible state belongs to one connection generation."""

    handle: Any = None
    lines: queue.Queue[dict[str, Any]] = field(default_factory=lambda: queue.Queue(maxsize=8192))
    stopped: threading.Event = field(default_factory=threading.Event)
    reader: threading.Thread | None = None
    pending: deque[dict[str, Any]] = field(default_factory=deque)
    failure: str = "pipe 已关闭（游戏退出或 mod 被卸载）"


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
        self.hello: HelloEvent = HelloEvent(protocol=0, spool="", capabilities=[])
        self._session = _PipeSession()
        self._exchange_lock = threading.RLock()

    @property
    def connected(self) -> bool:
        session = self._session
        return session.handle is not None and not session.stopped.is_set()

    @contextlib.contextmanager
    def exchange(self) -> Iterator[None]:
        """One consumer owns a complete command/workflow on protocol 4."""
        with self._exchange_lock:
            yield

    def connect(self) -> Self:
        with self.exchange():
            return self._connect()

    def _connect(self) -> Self:
        self.close()
        self.protocol = 0
        self.hello = HelloEvent(protocol=0, spool="", capabilities=[])
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

            session = _PipeSession(handle=handle)
            self._session = session
            session.reader = threading.Thread(
                target=self._read_loop,
                args=(session,),
                name="mod-pipe-reader",
                daemon=True,
            )
            session.reader.start()
            try:
                hello = self._next_line(self.connect_timeout)
            except ModPipeError:
                self.close()
                raise
            if hello.get("event") != "hello":
                self.close()
                raise ModPipeError(f"握手失败：{hello!r}")
            self.protocol = int(hello.get("protocol", 0))
            self.hello = cast(HelloEvent, hello)
            return self
        raise ModPipeError(
            f"无法连接 {self.pipe_name}: {last_error}\n提示：{pipe_error_hint(last_error)}"
        )

    def reconnect(self) -> Self:
        """Drop the current pipe and open a fresh one (game restart, mod reload)."""
        return self.connect()

    def ensure_connected(self) -> Self:
        if not self.connected:
            return self.connect()
        return self

    @staticmethod
    def _read_loop(session: _PipeSession) -> None:
        """Poll for data instead of blocking in ``read``.

        Windows serialises I/O on a synchronous pipe handle: a ``ReadFile``
        that is waiting for data also blocks ``WriteFile`` on the same handle.
        ``PeekNamedPipe`` lets the reader wait without a pending read, so a
        request can always be written while the reader is idle.
        """
        decoder = LineDecoder()
        try:
            handle = session.handle
            if handle is None:
                return
            os_handle = msvcrt.get_osfhandle(handle.fileno())
            while not session.stopped.is_set():
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
                    session.stopped.wait(0.005)
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
                        session.lines.put_nowait(message)
        except queue.Full:
            session.failure = "pipe 接收队列溢出；连接已终止"
        except (OSError, ValueError):
            pass
        finally:
            session.stopped.set()
            with contextlib.suppress(queue.Full):
                session.lines.put_nowait({"event": "__closed__"})

    def _next_line(self, timeout: float | None = None) -> dict[str, Any]:
        wait = self.timeout if timeout is None else timeout
        session = self._session
        if session.stopped.is_set() and session.lines.empty():
            raise ModDisconnected(session.failure)
        try:
            message = session.lines.get(timeout=max(0.0, wait))
        except queue.Empty as exc:
            raise ModTimeout(f"等待 mod 响应超时（{wait:.1f}s）") from exc
        if message.get("event") == "__closed__":
            self._disconnect(session)
            raise ModDisconnected(session.failure)
        return message

    def close(self) -> None:
        """Also safe from another thread to interrupt an outstanding read."""
        self._disconnect(self._session)

    @staticmethod
    def _disconnect(session: _PipeSession) -> None:
        session.stopped.set()
        with contextlib.suppress(queue.Full):
            session.lines.put_nowait({"event": "__closed__"})
        handle, session.handle = session.handle, None
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()
        reader, session.reader = session.reader, None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)

    def send(self, message: dict[str, Any]) -> None:
        if not self.connected:
            raise ModDisconnected("尚未连接")
        raw = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        self._write(raw)

    def send_raw(self, payload: bytes) -> None:
        """Write bytes without appending a newline (used by the line-bound test)."""
        self._write(payload)

    def _write(self, payload: bytes) -> None:
        """Finish short writes and translate all pipe I/O failures consistently."""
        with self.exchange():
            session = self._session
            handle = session.handle
            if handle is None or session.stopped.is_set():
                raise ModDisconnected("尚未连接")
            view = memoryview(payload)
            try:
                while view:
                    written = handle.write(view)
                    if not written:
                        raise OSError("pipe write returned 0")
                    view = view[written:]
                handle.flush()
            except (OSError, ValueError) as exc:
                self._disconnect(session)
                raise ModDisconnected(f"pipe 写入失败：{exc}") from exc

    def _defer(self, message: dict[str, Any]) -> None:
        if len(self._session.pending) >= 4096:
            self.close()
            raise ModProtocolError("未消费事件超过 4096 条；请通过 readline 消费或停止录制")
        self._session.pending.append(message)

    def readline(self, timeout: float | None = None) -> dict[str, Any]:
        """Read deferred notifications first, then the live stream."""
        with self.exchange():
            if self._session.pending:
                return self._session.pending.popleft()
            return self._next_line(timeout)

    def wait_event(
        self,
        events: str | tuple[str, ...],
        timeout: float | None = None,
        *,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        """Wait without discarding unrelated events, including buffered ones."""
        names = (events,) if isinstance(events, str) else events

        def matches(row: dict[str, Any]) -> bool:
            return row.get("event") in names and (predicate is None or predicate(row))

        with self.exchange():
            for index, row in enumerate(self._session.pending):
                if matches(row):
                    del self._session.pending[index]
                    return row
            deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ModTimeout(f"等待 {names} 超时")
                row = self._next_line(remaining)
                if row.get("event") == "error" and "error" not in names:
                    raise ModProtocolError(str(row.get("reason", row)))
                if matches(row):
                    return row
                self._defer(row)

    def request(
        self,
        message: dict[str, Any],
        expect: str | tuple[str, ...] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        with self.exchange():
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
                names = (expect,) if isinstance(expect, str) else expect
                if event == "error" and (names is None or "error" not in names):
                    raise ModProtocolError(f"{message.get('cmd')}: {reply.get('reason', reply)}")
                if names is None or event in names:
                    return reply
                self._defer(reply)

    def __enter__(self) -> Self:
        return self.connect()

    def __exit__(self, *_exc: object) -> None:
        self.close()
