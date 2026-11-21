"""Named-pipe connection, JSONL framing, request deadlines and I/O failures.

This layer knows no game commands. Reads stay on a polling thread so Windows
synchronous pipe reads cannot block writes on the same handle.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import msvcrt
import os
import queue
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any, Self, cast

from .errors import ModDisconnected, ModPipeError, ModProtocolError, ModTimeout
from .protocol import (
    DEFAULT_TIMEOUT,
    PIPE_NAME,
    HelloEvent,
    LineDecoder,
    LineTooLongError,
    kernel32,
)

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
class _PipeStream:
    """Reader-owned I/O, without callbacks that could retain the client."""

    handle: Any = None
    lines: queue.Queue[dict[str, Any]] = field(default_factory=lambda: queue.Queue(maxsize=20000))
    stopped: threading.Event = field(default_factory=threading.Event)
    reader: threading.Thread | None = None
    failure: str = "pipe 已关闭（游戏退出或 mod 被卸载）"
    drop_events: frozenset[str] = field(default_factory=frozenset)
    dropped: int = 0
    _drop_lock: threading.Lock = field(default_factory=threading.Lock)
    _close_lock: threading.Lock = field(default_factory=threading.Lock)

    def close(self) -> None:
        # The reader's finally and an explicit/GC close may race. Transfer the
        # resources once, then close/join outside the lock so the reader can exit.
        with self._close_lock:
            self.stopped.set()
            with contextlib.suppress(queue.Full):
                self.lines.put_nowait({"event": "__closed__"})
            handle, self.handle = self.handle, None
            reader, self.reader = self.reader, None
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()
        if (
            reader is not None
            and reader.ident is not None
            and reader is not threading.current_thread()
        ):
            reader.join(timeout=1.0)


@dataclass
class _PipeSession:
    """Request state and subscriptions belong to one connection generation."""

    stream: _PipeStream = field(default_factory=_PipeStream)
    finalizer: weakref.finalize | None = None
    pending: deque[dict[str, Any]] = field(default_factory=deque)
    consumers: dict[str, dict[object, Callable[[dict[str, Any]], None]]] = field(
        default_factory=dict
    )
    next_req_id: int = 0


class PipeConnection:
    """Own one pipe connection and its ordered stream of responses/events."""

    def __init__(
        self,
        pipe_name: str = PIPE_NAME,
        connect_timeout: float = 10.0,
        timeout: float = DEFAULT_TIMEOUT,
        token: str | None = None,
    ) -> None:
        self.pipe_name = pipe_name
        self.connect_timeout = connect_timeout
        self.timeout = timeout
        self.token = os.environ.get("LEVIPILOT_PIPE_TOKEN") if token is None else token
        self.protocol = 0
        self.hello: HelloEvent = HelloEvent(protocol=0, spool="", capabilities=[])
        self._drop_events: frozenset[str] = frozenset()
        self._session = _PipeSession()
        self._exchange_lock = threading.RLock()

    def set_drop_events(self, events: Iterable[str] | None = ("tick",)) -> None:
        """Opt in to dropping named high-frequency events when queues fill."""
        names = frozenset(str(name) for name in events) if events else frozenset()
        with self.exchange():
            self._drop_events = names
            stream = self._session.stream
            with stream._drop_lock:
                stream.drop_events = names

    def dropped_events(self) -> int:
        stream = self._session.stream
        with stream._drop_lock:
            return stream.dropped

    @property
    def connected(self) -> bool:
        stream = self._session.stream
        return stream.handle is not None and not stream.stopped.is_set()

    def _supports_req_id(self) -> bool:
        caps = self.hello.get("capabilities")
        return isinstance(caps, list) and "req_id" in caps

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

            stream = _PipeStream(handle=handle, drop_events=self._drop_events)
            session = _PipeSession(stream=stream)
            self._session = session
            # The finalizer holds only I/O state. Neither it nor the worker may
            # retain the client through a subscription callback or bound method.
            try:
                session.finalizer = weakref.finalize(self, _PipeStream.close, stream)
                stream.reader = threading.Thread(
                    target=self._read_loop,
                    args=(stream,),
                    name="mod-pipe-reader",
                    daemon=True,
                )
                stream.reader.start()
                hello = self._next_line(self.connect_timeout)
                if hello.get("event") != "hello":
                    raise ModPipeError(f"握手失败：{hello!r}")
                self.protocol = int(hello.get("protocol", 0))
                self.hello = cast(HelloEvent, hello)
                if hello.get("auth_required"):
                    if not self.token:
                        raise ModProtocolError("auth_required: set token or LEVIPILOT_PIPE_TOKEN")
                    auth_reply = self.request(
                        {"cmd": "auth", "token": self.token},
                        ("auth_result", "error"),
                        timeout=self.connect_timeout,
                    )
                    if not auth_reply.get("ok"):
                        raise ModProtocolError(
                            f"管道认证失败：{auth_reply.get('reason', auth_reply)}"
                        )
            except BaseException:
                self._disconnect(session)
                raise
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
    def _read_loop(stream: _PipeStream) -> None:
        """Poll for data instead of blocking in ``read``.

        Windows serialises I/O on a synchronous pipe handle: a ``ReadFile``
        that is waiting for data also blocks ``WriteFile`` on the same handle.
        ``PeekNamedPipe`` lets the reader wait without a pending read, so a
        request can always be written while the reader is idle.
        """
        decoder = LineDecoder()
        try:
            handle = stream.handle
            if handle is None:
                return
            os_handle = msvcrt.get_osfhandle(handle.fileno())
            while not stream.stopped.is_set():
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
                    stream.stopped.wait(0.005)
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
                        with stream._drop_lock:
                            drop_events = stream.drop_events
                        event_name = str(message.get("event", ""))
                        if drop_events and event_name in drop_events and stream.lines.maxsize > 0:
                            reserve = min(64, max(1, stream.lines.maxsize // 4))
                            if stream.lines.qsize() >= max(1, stream.lines.maxsize - reserve):
                                with stream._drop_lock:
                                    stream.dropped += 1
                                continue
                        try:
                            stream.lines.put_nowait(message)
                        except queue.Full:
                            if drop_events and event_name in drop_events:
                                with stream._drop_lock:
                                    stream.dropped += 1
                                continue
                            raise
        except queue.Full:
            stream.failure = "pipe 接收队列溢出；连接已终止"
        except LineTooLongError as exc:
            stream.failure = f"pipe 单行超过上限：{exc}；连接已终止"
        except (OSError, ValueError):
            pass
        finally:
            # Release even when the caller never attempts another read/close.
            stream.close()

    def _next_line(self, timeout: float | None = None) -> dict[str, Any]:
        wait = self.timeout if timeout is None else timeout
        session = self._session
        stream = session.stream
        deadline = time.monotonic() + max(0.0, wait)
        while True:
            if stream.stopped.is_set() and stream.lines.empty():
                raise ModDisconnected(stream.failure)
            try:
                message = stream.lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise ModTimeout(f"等待 mod 响应超时（{wait:.1f}s）") from exc
            if message.get("event") == "__closed__":
                self._disconnect(session)
                raise ModDisconnected(stream.failure)
            consumers = tuple(session.consumers.get(str(message.get("event")), {}).values())
            if not consumers:
                return message
            try:
                for consume in consumers:
                    consume(message)
            except BaseException:
                self._disconnect(session)
                raise
            if time.monotonic() >= deadline:
                raise ModTimeout(f"等待 mod 响应超时（{wait:.1f}s）")

    def subscribe(
        self, event: str, consume: Callable[[dict[str, Any]], None]
    ) -> Callable[[], None]:
        """Consume new notifications during any read/request; return an unsubscribe function.

        Consumers run on the request's thread under the exchange lock and must
        not issue requests themselves. Subscriptions belong to this connection
        generation, so reconnecting cannot deliver stale events to them.
        """
        with self.exchange():
            session = self._session
            token = object()
            session.consumers.setdefault(event, {})[token] = consume

        def unsubscribe() -> None:
            with self.exchange():
                handlers = session.consumers.get(event)
                if handlers is not None:
                    handlers.pop(token, None)
                    if not handlers:
                        session.consumers.pop(event, None)

        return unsubscribe

    def close(self) -> None:
        """Also safe from another thread to interrupt an outstanding read."""
        self._disconnect(self._session)

    @staticmethod
    def _disconnect(session: _PipeSession) -> None:
        if session.finalizer is not None:
            session.finalizer()
        else:
            session.stream.close()

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
            handle = session.stream.handle
            if handle is None or session.stream.stopped.is_set():
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
            stream = self._session.stream
            with stream._drop_lock:
                drop_events = stream.drop_events
            if drop_events:
                event_name = str(message.get("event", ""))
                if event_name in drop_events:
                    with stream._drop_lock:
                        stream.dropped += 1
                    return
                for index, queued in enumerate(self._session.pending):
                    if str(queued.get("event", "")) in drop_events:
                        del self._session.pending[index]
                        self._session.pending.append(message)
                        with stream._drop_lock:
                            stream.dropped += 1
                        return
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
                    if self._supports_req_id() and (
                        row.get("req_id") is not None
                        or row.get("phase")
                        in ("tick_pre", "tick_post", "spool_thread", "record_tick", "macro_record")
                    ):
                        self._defer(row)
                        continue
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
            session = self._session
            req_id: int | None = None
            outgoing = message
            if "req_id" in message:
                raw_req_id = message["req_id"]
                if (
                    isinstance(raw_req_id, int)
                    and not isinstance(raw_req_id, bool)
                    and raw_req_id > 0
                ):
                    req_id = raw_req_id
            elif self._supports_req_id():
                session.next_req_id += 1
                req_id = session.next_req_id
                outgoing = {**message, "req_id": req_id}
            self.send(outgoing)
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
                    if req_id is None:
                        raise ModProtocolError(
                            f"{message.get('cmd')}: {reply.get('reason', reply)}"
                        )
                    reply_req_id = reply.get("req_id")
                    if reply_req_id == req_id or (
                        reply_req_id is None
                        and reply.get("reason") in ("bad_json", "line_too_long")
                    ):
                        raise ModProtocolError(
                            f"{message.get('cmd')}: {reply.get('reason', reply)}"
                        )
                    self._defer(reply)
                    continue
                if names is None or event in names:
                    if req_id is not None and "req_id" in reply and reply.get("req_id") != req_id:
                        self._defer(reply)
                        continue
                    return reply
                self._defer(reply)

    def __enter__(self) -> Self:
        return self.connect()

    def __exit__(self, *_exc: object) -> None:
        self.close()
