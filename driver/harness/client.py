"""High-level pipe client and the script-friendly ``Harness`` wrapper.

``ModClient`` owns the connection: it validates the handshake, reads messages on
a polling thread (a blocking Windows pipe read would also block writes), and
gives every request a timeout. ``Harness`` adds the convenience methods used by
scripts and the scenario DSL.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import msvcrt
import queue
import threading
import time
from collections.abc import Callable, Iterable
from ctypes import wintypes
from typing import Any

from .errors import ModDisconnected, ModPipeError, ModProtocolError, ModTimeout
from .memory import parse_hex_bytes, to_hex
from .protocol import DEFAULT_TIMEOUT, PIPE_NAME, LineDecoder, kernel32
from .record import RecordEntry, decode_dump


class ModClient:
    """JSON-lines client.

    Reads happen on a dedicated thread so every request can carry a timeout.
    Without that, a crashed game leaves a script blocked in ``readline``
    forever. ``connect`` also validates the handshake, and ``reconnect`` can be
    used to survive a game restart.
    """

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

    def connect(self) -> ModClient:
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

    def reconnect(self) -> ModClient:
        """Drop the current pipe and open a fresh one (game restart, mod reload)."""
        return self.connect()

    def ensure_connected(self) -> ModClient:
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
        view = memoryview(raw)
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

    def send_raw(self, payload: bytes) -> None:
        """Write bytes without appending a newline (used by the line-bound test)."""
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

    def wait_ready(self, timeout: float = 60.0, require_world: bool = True) -> dict[str, Any]:
        """Reconnect until the mod answers and (optionally) a world is loaded."""
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.ensure_connected()
                self.ping()
                state = self.state()
                if state.get("in_world") or not require_world:
                    return state
            except ModPipeError as exc:
                last = exc
                self.close()
            time.sleep(0.5)
        raise ModTimeout(f"等待游戏就绪超时（{timeout:.0f}s）：{last}")

    def ping(self) -> dict[str, Any]:
        return self.request({"cmd": "ping"}, "pong")

    def status(self) -> dict[str, Any]:
        return self.request({"cmd": "status"}, "status")

    def overhead(self) -> dict[str, Any]:
        """Tick/hook timing percentiles (T9)."""
        return self.request({"cmd": "overhead"}, "overhead")

    def diagnose(self) -> dict[str, Any]:
        """Executable version, anchor addresses and signature matches (T7)."""
        return self.request({"cmd": "diagnose"}, "diagnose")

    def signature_check(self, name: str, expected_hex: str) -> dict[str, Any]:
        return self.request(
            {"cmd": "signature_check", "name": name, "expected_hex": expected_hex},
            "signature_check",
        )

    def selftest_busy(self, us: int = 1000, ticks: int = 20) -> dict[str, Any]:
        """Burn ``us`` microseconds in the tick for ``ticks`` ticks (metric proof)."""
        return self.request(
            {"cmd": "selftest_busy", "us": int(us), "ticks": int(ticks)},
            "busy_test_started",
        )

    def diag(self) -> dict[str, Any]:
        """Report which server-side objects the mod can see (teleport diagnostics)."""
        return self.request({"cmd": "diag"}, "diag")

    def command(self, command: str, player: str | None = None) -> dict[str, Any]:
        """Run a server command (``/fill``, ``/tp`` ...) on the integrated server."""
        message: dict[str, Any] = {"cmd": "command", "command": command}
        if player:
            message["player"] = player
        return self.request(message, "command_done")

    def mouse(self) -> dict[str, Any]:
        """Mouse input diagnostics (event counts per MouseAction id)."""
        return self.request({"cmd": "mouse"}, "mouse")

    def mouse_clear(self) -> dict[str, Any]:
        return self.request({"cmd": "mouse_clear"}, "mouse_cleared")

    # -- G1/G2: event-level input injection ----------------------------------
    def key_event(
        self,
        code: int,
        down: bool = True,
        *,
        repeat: bool = False,
        repeat_ms: int = 0,
        via: str = "hid",
    ) -> dict[str, Any]:
        return self.request(
            {
                "cmd": "key_event",
                "code": int(code),
                "down": bool(down),
                "repeat": bool(repeat),
                "repeat_ms": int(repeat_ms),
                "via": via,
            },
            "key_event_queued",
        )

    def key_state(self, code: int) -> dict[str, Any]:
        return self.request({"cmd": "key_state", "code": int(code)}, "key_state")

    def screen(self) -> dict[str, Any]:
        return self.request({"cmd": "screen"}, "screen")

    def packet_capture(
        self,
        on: bool = True,
        *,
        ids: list[int] | None = None,
        sample: int = 1,
        serialize_ids: list[int] | None = None,
        directions: list[str] | None = None,
        timeout: float = 20.0,
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

    def op_self(self, level: int = 4) -> dict[str, Any]:
        return self.request({"cmd": "op_self", "level": int(level)}, "op_self", timeout=20.0)

    def commands_enabled(self, value: bool | None = None) -> dict[str, Any]:
        message: dict[str, Any] = {"cmd": "commands_enabled"}
        if value is not None:
            message["value"] = bool(value)
        return self.request(message, "commands_enabled", timeout=20.0)

    def text_input(self, text: str) -> dict[str, Any]:
        return self.request({"cmd": "text_input", "text": text}, "text_input_queued")

    def chat_send(self, text: str) -> dict[str, Any]:
        return self.request({"cmd": "chat_send", "text": text}, "chat_send_queued", timeout=20.0)

    def symbol_resolve(self, name: str) -> dict[str, Any]:
        return self.request({"cmd": "symbol_resolve", "name": name}, "symbol_resolve")

    def mouse_event(
        self,
        action: int | str,
        *,
        data: int | str | None = None,
        down: bool | None = None,
        dx: int = 0,
        dy: int = 0,
        x: int | None = None,
        y: int | None = None,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "cmd": "mouse_event",
            "action": action,
            "dx": int(dx),
            "dy": int(dy),
        }
        if x is not None:
            message["x"] = int(x)
        if y is not None:
            message["y"] = int(y)
        if data is not None:
            message["data"] = data
        if down is not None:
            message["down"] = bool(down)
        return self.request(message, "mouse_event_queued")

    # -- T2: memory primitives ------------------------------------------------
    def mem_read(
        self, addr: int | str, length: int = 16, *, timeout: float | None = None
    ) -> dict[str, Any]:
        """Read up to 4096 bytes; a bad address returns ``ok=false/bad_addr``."""
        return self.request(
            {"cmd": "mem_read", "addr": addr, "len": int(length)},
            "mem_read",
            timeout=timeout,
        )

    def mem_write(
        self,
        addr: int | str,
        data: bytes | bytearray | str,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Write bytes (requires ``allow_write=true`` in harness.json).

        ``data`` may be raw bytes or a hex string. The mod rejects the request
        with ``write_disabled`` when writes are not enabled.
        """
        hex_text = data if isinstance(data, str) else to_hex(data)
        normalized = parse_hex_bytes(hex_text).hex()
        return self.request(
            {"cmd": "mem_write", "addr": addr, "hex": normalized},
            "mem_write",
            timeout=timeout,
        )

    def probe(
        self,
        addr: int | str,
        kind: str = "raw",
        *,
        length: int = 16,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Structured read: ``raw`` | ``block`` | ``legacy``."""
        return self.request(
            {"cmd": "probe", "addr": addr, "kind": kind, "len": int(length)},
            "probe",
            timeout=timeout,
        )

    def hook_install(
        self,
        symbol: str | None = None,
        *,
        va: int | str | None = None,
        tag: str = "",
        sample: int = 1,
        dry_run: bool = False,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        """Install an enter hook by LL symbol or (gated) VA; dry_run only validates."""
        if symbol is None and va is None:
            raise ValueError("symbol or va is required")
        message: dict[str, Any] = {
            "cmd": "hook_install",
            "tag": tag,
            "sample": int(sample),
            "dry_run": bool(dry_run),
        }
        if symbol is not None:
            message["symbol"] = symbol
        else:
            message["va"] = va
        self.send(message)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close()
                raise ModTimeout(f"hook_install 等待结果超时（{timeout:.1f}s）")
            reply = self._next_line(remaining)
            event = reply.get("event")
            if event in {"hook_installed", "hook_dry_run"}:
                return reply
            if event == "error":
                raise ModProtocolError(f"hook_install: {reply.get('reason', reply)}")

    def hook_rm(self, hook_id: int) -> dict[str, Any]:
        return self.request({"cmd": "hook_rm", "id": int(hook_id)}, "hook_removed")

    def hook_list(self) -> dict[str, Any]:
        return self.request({"cmd": "hook_list"}, "hook_list")

    def panic(self, level: str = "all", timeout: float = 10.0) -> dict[str, Any]:
        """Remove hooks/watches and cancel tasks without killing the game."""
        if level not in {"hooks", "tasks", "all"}:
            raise ValueError("level must be hooks, tasks or all")
        return self.request({"cmd": "panic", "level": level}, "panic_done", timeout=timeout)

    def hook_drain(self, since: int = 0, *, limit: int = 4096) -> dict[str, Any]:
        return self.request(
            {"cmd": "hook_drain", "since": int(since), "limit": int(limit)},
            "hook_hits",
        )

    def hook_call(
        self, symbol: str, scale: float = 1.0, *, timeout: float = 10.0
    ) -> dict[str, Any]:
        return self.request(
            {"cmd": "hook_call", "symbol": symbol, "scale": float(scale)},
            "hook_call_done",
            timeout=timeout,
        )

    def spool_flush(self, timeout: float = 10.0) -> dict[str, Any]:
        return self.request({"cmd": "spool_flush"}, "spool_flushed", timeout=timeout)

    def selftest_av(self, timeout: float = 5.0) -> dict[str, Any]:
        """Raise an access violation inside the mod; expects an ``internal_error``.

        This is the isolation check from T1: the game must keep ticking.
        """
        self.send({"cmd": "selftest_av"})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModTimeout("selftest_av 等待错误回复超时")
            message = self._next_line(remaining)
            if message.get("event") in {"error", "selftest_fault_done"}:
                return message

    # -- T3: hardware watchpoints ---------------------------------------------
    def watch_set(
        self,
        addr: int | str,
        *,
        size: int = 4,
        mode: str = "w",
        thread: str | int = "tick",
        tag: str = "",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Install a debug-register watchpoint (``size`` 1/2/4/8, mode w|rw)."""
        return self.request(
            {
                "cmd": "watch_set",
                "addr": addr,
                "size": int(size),
                "mode": mode,
                "thread": thread,
                "tag": tag,
            },
            "watch_installed",
            timeout=timeout,
        )

    def watch_clear(
        self, watch_id: int | None = None, *, all: bool = False, timeout: float | None = None
    ) -> dict[str, Any]:
        if not all and watch_id is None:
            raise ValueError("watch_id or all=True is required")
        message: dict[str, Any] = {"cmd": "watch_clear"}
        if all:
            message["all"] = True
        else:
            message["id"] = int(watch_id)  # type: ignore[arg-type]
        return self.request(message, "watch_cleared", timeout=timeout)

    def watch_list(self) -> dict[str, Any]:
        return self.request({"cmd": "watch_list"}, "watch_list")

    def watch_drain(self, since: int = 0, *, limit: int = 4096) -> dict[str, Any]:
        return self.request(
            {"cmd": "watch_drain", "since": int(since), "limit": int(limit)}, "watch_hits"
        )

    def state(self) -> dict[str, Any]:
        return self.request({"cmd": "state"}, "state")

    def start(self, max_ticks: int = 0) -> dict[str, Any]:
        return self.request({"cmd": "start", "max_ticks": int(max_ticks)}, "started")

    def stop(self) -> dict[str, Any]:
        return self.request({"cmd": "stop"}, "stopped")

    def move(self, forward: float = 0.0, strafe: float = 0.0, ticks: int = 1) -> dict[str, Any]:
        return self.request(
            {
                "cmd": "move",
                "forward": float(forward),
                "strafe": float(strafe),
                "ticks": int(ticks),
            },
            "moving",
        )

    def stop_move(self) -> dict[str, Any]:
        return self.request({"cmd": "stopmove"}, "stopping")

    def set_input(
        self,
        forward: float = 0.0,
        strafe: float = 0.0,
        *,
        sprint: bool = False,
        sneak: bool = False,
        jump: bool = False,
        rmb: bool = False,
        lmb: bool = False,
        ticks: int = 1,
    ) -> dict[str, Any]:
        return self.request(
            {
                "cmd": "set",
                "forward": float(forward),
                "strafe": float(strafe),
                "sprint": bool(sprint),
                "sneak": bool(sneak),
                "jump": bool(jump),
                "rmb": bool(rmb),
                "lmb": bool(lmb),
                "ticks": int(ticks),
            },
            "set_ok",
        )

    def jump(self) -> dict[str, Any]:
        return self.request({"cmd": "jump"}, "jump_ok")

    def look(
        self,
        yaw: float,
        pitch: float = 0.0,
        *,
        relative: bool = False,
        ticks: int = 1,
    ) -> dict[str, Any]:
        return self.request(
            {
                "cmd": "look",
                "yaw": float(yaw),
                "pitch": float(pitch),
                "relative": bool(relative),
                "ticks": int(ticks),
            },
            "look_ok",
        )

    def record_bin_start(self, period: int = 1, timeout: float | None = None) -> dict[str, Any]:
        """Start the fixed-size binary tick ring (T4)."""
        return self.request(
            {"cmd": "record_start", "period": int(period)},
            "record_started",
            timeout=timeout,
        )

    def record_bin_stop(self, timeout: float | None = None) -> dict[str, Any]:
        return self.request({"cmd": "record_stop"}, "record_stopped", timeout=timeout)

    def record_dump(
        self,
        since: int = 0,
        *,
        limit: int = 1024,
        format: str = "jsonl",  # mirrors the wire field name
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self.request(
            {
                "cmd": "record_dump",
                "since": int(since),
                "limit": int(limit),
                "format": format,
            },
            "record_dump",
            timeout=timeout,
        )

    def record_dump_all(
        self,
        *,
        since: int = 0,
        chunk: int = 1024,
        format: str = "jsonl",
        timeout: float | None = None,
    ) -> list[RecordEntry]:
        """Drain the ring up to the head observed on the first reply."""
        entries: list[RecordEntry] = []
        stop_at: int | None = None
        cursor = since
        while True:
            message = self.record_dump(cursor, limit=chunk, format=format, timeout=timeout)
            entries.extend(decode_dump(message))
            cursor = int(message.get("next", cursor))
            if stop_at is None:
                stop_at = int(message.get("head", cursor))
            if cursor >= stop_at or int(message.get("count", 0)) == 0:
                break
        return entries

    def entities(self, radius: float = 16.0, limit: int = 64) -> dict[str, Any]:
        return self.request(
            {"cmd": "entities", "radius": float(radius), "limit": int(limit)}, "entities"
        )

    def blocks(self, positions: Iterable[Iterable[float]]) -> dict[str, Any]:
        return self.request({"cmd": "blocks", "positions": [list(p) for p in positions]}, "blocks")

    def scan(
        self,
        start: Iterable[float],
        end: Iterable[float],
        *,
        include_air: bool = False,
        limit: int = 4096,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Scan an inclusive block region on the next tick (max 32768 cells)."""
        return self.request(
            {
                "cmd": "scan",
                "from": [int(v) for v in start],
                "to": [int(v) for v in end],
                "include_air": bool(include_air),
                "limit": int(limit),
            },
            "scan",
            timeout=timeout,
        )

    def checkpoint_save(self, name: str) -> dict[str, Any]:
        return self.request({"cmd": "checkpoint_save", "name": name}, "checkpoint_saved")

    def checkpoint_load(self, name: str, timeout: float = 20.0) -> dict[str, Any]:
        return self.request(
            {"cmd": "checkpoint_load", "name": name},
            "checkpoint_loaded",
            timeout=timeout,
        )

    def checkpoint_list(self) -> dict[str, Any]:
        return self.request({"cmd": "checkpoint_list"}, "checkpoint_list")

    def checkpoint_delete(self, name: str) -> dict[str, Any]:
        return self.request({"cmd": "checkpoint_delete", "name": name}, "checkpoint_deleted")

    def select_slot(self, slot: int) -> dict[str, Any]:
        return self.request({"cmd": "select_slot", "slot": int(slot)}, "select_slot_ok")

    def inventory(self) -> dict[str, Any]:
        """Full inventory snapshot: slots, armor, offhand, selected slot."""
        return self.request({"cmd": "inventory"}, "inventory")

    def set_item(
        self,
        slot: int,
        item: str,
        *,
        count: int = 1,
        aux: int = 0,
        container: str = "inventory",
    ) -> dict[str, Any]:
        """Set one slot. ``count=0`` clears it. container: inventory/armor/offhand."""
        return self.request(
            {
                "cmd": "set_item",
                "slot": int(slot),
                "item": item,
                "count": int(count),
                "aux": int(aux),
                "container": container,
            },
            "inventory_set",
        )

    def give(self, item: str, *, count: int = 1, aux: int = 0) -> dict[str, Any]:
        return self.request(
            {"cmd": "give", "item": item, "count": int(count), "aux": int(aux)},
            "inventory_given",
        )

    def clear_item(self, slot: int, *, container: str = "inventory") -> dict[str, Any]:
        return self.request(
            {"cmd": "clear_item", "slot": int(slot), "container": container},
            "inventory_set",
        )

    def clear_inventory(self) -> dict[str, Any]:
        return self.request({"cmd": "clear_inventory"}, "inventory_cleared")

    def entity(self, runtime_id: int) -> dict[str, Any]:
        return self.request({"cmd": "entity", "runtime_id": int(runtime_id)}, "entity_info")

    def attack_entity(self, runtime_id: int) -> dict[str, Any]:
        return self.request(
            {"cmd": "attack_entity", "runtime_id": int(runtime_id)}, "entity_attacked"
        )

    def interact_entity(self, runtime_id: int) -> dict[str, Any]:
        return self.request(
            {"cmd": "interact_entity", "runtime_id": int(runtime_id)}, "entity_interacted"
        )

    def look_at_entity(self, runtime_id: int) -> dict[str, Any]:
        return self.request(
            {"cmd": "look_at_entity", "runtime_id": int(runtime_id)}, "look_at_done"
        )

    def ecs_registries(self, timeout: float = 15.0) -> dict[str, Any]:
        return self.request({"cmd": "ecs_registries"}, "ecs_registries", timeout=timeout)

    def ecs_context(
        self, context: int | str, component_id: int, timeout: float = 15.0
    ) -> dict[str, Any]:
        return self.request(
            {"cmd": "ecs_context", "context": context, "component_id": int(component_id)},
            "ecs_context",
            timeout=timeout,
        )

    def ecs_entity(self, runtime_id: int, timeout: float = 15.0) -> dict[str, Any]:
        return self.request(
            {"cmd": "ecs_entity", "runtime_id": int(runtime_id)},
            "ecs_entity",
            timeout=timeout,
        )

    def ecs_components(self, runtime_id: int, timeout: float = 15.0) -> dict[str, Any]:
        return self.request(
            {"cmd": "ecs_components", "runtime_id": int(runtime_id)},
            "ecs_components",
            timeout=timeout,
        )

    def ecs_component(
        self,
        runtime_id: int,
        component_id: int,
        *,
        length: int = 0,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        return self.request(
            {
                "cmd": "ecs_component",
                "runtime_id": int(runtime_id),
                "component_id": int(component_id),
                "len": int(length),
            },
            "ecs_component",
            timeout=timeout,
        )

    def click(self, button: str = "left", reach: float = 5.5) -> dict[str, Any]:
        return self.request(
            {"cmd": "click", "button": button, "reach": float(reach)}, "click_result"
        )

    def navigate(
        self,
        x: float,
        z: float,
        *,
        tolerance: float = 0.35,
        sprint: bool = False,
        max_ticks: int = 600,
        jump: str = "auto",
        jump_distance: float = 1.0,
        edge_lookahead: float = 0.55,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Walk to ``(x, z)`` in a closed loop on the client tick.

        ``jump`` selects the takeoff policy: ``auto`` (when stuck), ``distance``
        (within ``jump_distance`` of the target), ``edge`` (support ahead is
        missing) or ``never``.
        """
        if timeout is None:
            timeout = max_ticks / 20.0 + 15.0
        return self.request(
            {
                "cmd": "navigate",
                "x": float(x),
                "z": float(z),
                "tolerance": float(tolerance),
                "sprint": bool(sprint),
                "max_ticks": int(max_ticks),
                "jump": jump,
                "jump_distance": float(jump_distance),
                "edge_lookahead": float(edge_lookahead),
            },
            "navigated",
            timeout=timeout,
        )

    def jump_when(
        self,
        mode: str = "now",
        *,
        x: float | None = None,
        z: float | None = None,
        distance: float = 0.6,
        forward: float = 0.0,
        sprint: bool = False,
        yaw: float | None = None,
        after_ticks: int = 1,
        max_ticks: int = 60,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Jump at a controlled moment.

        ``mode``: ``now``, ``tick`` (after ``after_ticks``), ``distance``
        (horizontal distance to x,z <= ``distance``) or ``edge`` (the block
        ahead has no support). ``forward``/``sprint`` keep the player moving
        while waiting, so one command can run and jump.
        """
        if mode == "distance" and (x is None or z is None):
            raise ValueError("distance mode requires x and z")
        message: dict[str, Any] = {
            "cmd": "jump_when",
            "mode": mode,
            "distance": float(distance),
            "forward": float(forward),
            "sprint": bool(sprint),
            "after_ticks": int(after_ticks),
            "max_ticks": int(max_ticks),
        }
        if x is not None:
            message["x"] = float(x)
        if z is not None:
            message["z"] = float(z)
        if yaw is not None:
            message["yaw"] = float(yaw)
        if timeout is None:
            timeout = max_ticks / 20.0 + 10.0
        return self.request(message, "jumped", timeout=timeout)

    def route(
        self,
        waypoints: Iterable[dict[str, Any]],
        *,
        sprint: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Run a waypoint route in the mod and return ``route_done``/``route_failed``.

        Each waypoint accepts ``x, z, tolerance, max_ticks, jump, jump_distance``
        where ``jump`` is ``auto`` (default), ``distance``, ``edge`` or ``never``.
        """
        points = [dict(point) for point in waypoints]
        if not points:
            raise ValueError("at least one waypoint is required")
        total_ticks = sum(int(point.get("max_ticks", 600)) for point in points)
        if timeout is None:
            timeout = total_ticks / 20.0 + 20.0
        self.send({"cmd": "route", "waypoints": points, "sprint": bool(sprint)})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close()
                raise ModTimeout(f"route 等待完成超时（{timeout:.1f}s）")
            message = self._next_line(remaining)
            event = message.get("event")
            if event in {"route_done", "route_failed"}:
                return message
            if event == "error":
                raise ModProtocolError(f"route: {message.get('reason', message)}")

    def mine_block(
        self,
        x: int,
        y: int,
        z: int,
        *,
        max_ticks: int = 400,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Break one block; the mod drives start/continueDestroyBlock per tick."""
        if timeout is None:
            timeout = max_ticks / 20.0 + 15.0
        return self.request(
            {"cmd": "mine", "x": int(x), "y": int(y), "z": int(z), "max_ticks": int(max_ticks)},
            "block_mined",
            timeout=timeout,
        )

    def place_block(
        self,
        x: int,
        y: int,
        z: int,
        *,
        against: tuple[int, int, int] | None = None,
        face: int | None = None,
        expected: str | None = None,
        slot: int | None = None,
        max_ticks: int = 30,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Place the held block into ``(x, y, z)`` and verify the result.

        ``against`` is the block that gets clicked; when omitted the mod picks a
        visible neighbour. ``expected`` checks the resulting block name.
        """
        if timeout is None:
            timeout = max_ticks / 20.0 + 10.0
        message: dict[str, Any] = {
            "cmd": "place",
            "x": int(x),
            "y": int(y),
            "z": int(z),
            "max_ticks": int(max_ticks),
        }
        if against is not None:
            message["against"] = [int(v) for v in against]
        if face is not None:
            message["face"] = int(face)
        if expected is not None:
            message["expected"] = expected
        if slot is not None:
            message["slot"] = int(slot)
        return self.request(message, "block_placed", timeout=timeout)

    def use_item(self, ticks: int = 32, *, timeout: float | None = None) -> dict[str, Any]:
        """Hold the main-hand item for ``ticks`` and then release it (eat/bow)."""
        if timeout is None:
            timeout = ticks / 20.0 + 10.0
        return self.request({"cmd": "use", "ticks": int(ticks)}, "item_used", timeout=timeout)

    def task_status(self) -> dict[str, Any]:
        return self.request({"cmd": "task_status"}, "task_status")

    def task_stop(self) -> dict[str, Any]:
        return self.request({"cmd": "task_stop"}, "task_stopped")

    def worlds(self, timeout: float = 15.0) -> dict[str, Any]:
        """List cached worlds (id, name, last_saved, game_type)."""
        return self.request({"cmd": "worlds"}, "worlds", timeout=timeout)

    def signin(self) -> dict[str, Any]:
        """Xbox Live sign-in state from the main-menu model.

        ``known=false`` means the OreUI model is not available (for example
        in-world); call it from the main menu before entering a world.
        """
        return self.request({"cmd": "signin"}, "signin")

    def enter_world(
        self,
        name: str | None = None,
        *,
        world_id: str | None = None,
        intent: int = 0,
        force: bool = False,
        timeout: float = 40.0,
    ) -> dict[str, Any]:
        """Start an existing world without going through the menu UI.

        The mod waits for Xbox Live sign-in first (unless ``force``), replies
        ``enter_world_result``, then emits ``enter_world_settled`` once the join
        succeeds or is cancelled.
        """
        if not name and not world_id:
            raise ValueError("name or world_id is required")
        message: dict[str, Any] = {"cmd": "enter_world", "intent": int(intent)}
        if name:
            message["name"] = name
        if world_id:
            message["id"] = world_id
        if force:
            message["force"] = True
        return self.request(message, "enter_world_result", timeout=timeout)

    def wait_enter_settled(self, timeout: float = 45.0) -> dict[str, Any]:
        """Wait for the join outcome of the most recent ``enter_world``."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModTimeout(f"等待 enter_world_settled 超时（{timeout:.0f}s）")
            message = self._next_line(remaining)
            if message.get("event") == "enter_world_settled":
                return message

    def enter_world_ready(
        self,
        name: str | None = None,
        *,
        world_id: str | None = None,
        intent: int = 0,
        force: bool = False,
        attempts: int = 3,
        retry_delay: float = 4.0,
        settled_timeout: float = 45.0,
    ) -> dict[str, Any]:
        """Enter a world, retrying the async sign-in/join race.

        Returns the ``enter_world_settled`` message on success, or the last
        failure (``ok=false``) after all attempts.
        """
        last: dict[str, Any] = {"ok": False, "reason": "not_attempted"}
        for attempt in range(max(1, attempts)):
            result = self.enter_world(
                name,
                world_id=world_id,
                intent=intent,
                force=force,
                timeout=settled_timeout,
            )
            if not result.get("ok"):
                last = result
            else:
                last = self.wait_enter_settled(timeout=settled_timeout)
                if last.get("ok"):
                    return last
            if attempt + 1 < attempts:
                time.sleep(retry_delay)
        return last

    def leave_world(self, timeout: float = 20.0) -> dict[str, Any]:
        return self.request({"cmd": "leave_world"}, "leave_world_result", timeout=timeout)

    def reset(
        self,
        x: float,
        y: float,
        z: float,
        *,
        yaw: float | None = None,
        pitch: float | None = None,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Clear input/tasks/macro, then teleport to a known feet pose.

        The server teleport lands on the next tick; callers that need a settled
        state should wait a few ticks before starting a run.
        """
        message: dict[str, Any] = {"cmd": "reset", "x": float(x), "y": float(y), "z": float(z)}
        if yaw is not None:
            message["yaw"] = float(yaw)
        if pitch is not None:
            message["pitch"] = float(pitch)
        return self.request(message, "reset_done", timeout=timeout)

    def set_options(
        self,
        values: dict[str, Any] | None = None,
        *,
        text: str | None = None,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Apply option save-tag values (``gfx_gamma``, ``gfx_viewdistance`` ...).

        ``loadOptionsFromString`` uses the same ``key:value`` format as
        ``options.txt``, so values passed here are applied through the game's
        normal option pipeline and then persisted with ``saveIfNeeded``.
        """
        message: dict[str, Any] = {"cmd": "options_set"}
        if values:
            message["values"] = values
        if text:
            message["text"] = text
        if "values" not in message and "text" not in message:
            raise ValueError("values or text is required")
        return self.request(message, "options_set", timeout=timeout)

    def save_options(self, timeout: float = 20.0) -> dict[str, Any]:
        return self.request({"cmd": "options_save"}, "options_saved", timeout=timeout)

    def wait_in_world(
        self, timeout: float = 90.0, poll: float = 0.5, settle: float = 1.5
    ) -> dict[str, Any]:
        """Wait until ``state.in_world`` is true, then let the join settle."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.state()
            if state.get("in_world"):
                if settle > 0:
                    time.sleep(settle)
                    return self.state()
                return state
            time.sleep(poll)
        raise ModTimeout(f"等待进入世界超时（{timeout:.0f}s）")

    def fly(self, enabled: bool = True) -> dict[str, Any]:
        return self.request({"cmd": "fly", "enabled": bool(enabled)}, "fly_ok")

    def teleport(
        self,
        x: float,
        y: float,
        z: float,
        *,
        yaw: float | None = None,
        pitch: float | None = None,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"cmd": "teleport", "x": float(x), "y": float(y), "z": float(z)}
        if yaw is not None:
            message["yaw"] = float(yaw)
        if pitch is not None:
            message["pitch"] = float(pitch)
        return self.request(message, "teleported")

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

    def record(
        self,
        max_ticks: int,
        on_sample: Callable[[dict[str, Any]], None] | None = None,
        move_forward: float = 0.0,
        move_strafe: float = 0.0,
        move_ticks: int = 0,
    ) -> list[dict[str, Any]]:
        self.start(max_ticks)
        if move_ticks:
            self.send(
                {
                    "cmd": "move",
                    "forward": move_forward,
                    "strafe": move_strafe,
                    "ticks": move_ticks,
                }
            )
        samples: list[dict[str, Any]] = []
        while True:
            message = self.readline()
            event = message.get("event")
            if event == "error":
                raise ModPipeError(str(message.get("reason", message)))
            if event in {"moving", "stopping"}:
                continue
            if event == "tick":
                samples.append(message)
                if on_sample is not None:
                    on_sample(message)
            elif event == "stopped":
                break
        return samples

    def __enter__(self) -> ModClient:
        return self.connect()

    def __exit__(self, *_exc: object) -> None:
        self.close()


class Harness:
    """Convenience wrapper for LLM/script use.

    Example::

        with Harness() as h:
            print(h.state()["x"], h.state()["z"])
            h.walk(forward=1.0, ticks=40)
            print(h.state()["x"], h.state()["z"])
    """

    def __init__(self, pipe_name: str = PIPE_NAME) -> None:
        self.client = ModClient(pipe_name).connect()

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Harness:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def state(self) -> dict[str, Any]:
        return self.client.state()

    def overhead(self) -> dict[str, Any]:
        return self.client.overhead()

    def diagnose(self) -> dict[str, Any]:
        return self.client.diagnose()

    def signature_check(self, name: str, expected_hex: str) -> dict[str, Any]:
        return self.client.signature_check(name, expected_hex)

    def selftest_busy(self, us: int = 1000, ticks: int = 20) -> dict[str, Any]:
        return self.client.selftest_busy(us, ticks)

    def mem_read(self, addr: int | str, length: int = 16) -> dict[str, Any]:
        return self.client.mem_read(addr, length)

    def probe(self, addr: int | str, kind: str = "raw", **kwargs: Any) -> dict[str, Any]:
        return self.client.probe(addr, kind, **kwargs)

    def watch_set(self, addr: int | str, **kwargs: Any) -> dict[str, Any]:
        return self.client.watch_set(addr, **kwargs)

    def watch_clear(self, watch_id: int | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.client.watch_clear(watch_id, **kwargs)

    def watch_drain(self, since: int = 0, **kwargs: Any) -> dict[str, Any]:
        return self.client.watch_drain(since, **kwargs)

    def hook_install(self, symbol: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.client.hook_install(symbol, **kwargs)

    def hook_rm(self, hook_id: int) -> dict[str, Any]:
        return self.client.hook_rm(hook_id)

    def hook_list(self) -> dict[str, Any]:
        return self.client.hook_list()

    def hook_drain(self, since: int = 0, **kwargs: Any) -> dict[str, Any]:
        return self.client.hook_drain(since, **kwargs)

    def hook_call(self, symbol: str, scale: float = 1.0) -> dict[str, Any]:
        return self.client.hook_call(symbol, scale)

    def entities(self, radius: float = 16.0, limit: int = 64) -> list[dict[str, Any]]:
        return list(self.client.entities(radius, limit).get("entities", []))

    def inventory(self) -> dict[str, Any]:
        return self.client.inventory()

    def scan(self, start: Iterable[float], end: Iterable[float], **kwargs: Any) -> dict[str, Any]:
        return self.client.scan(start, end, **kwargs)

    def checkpoint_save(self, name: str) -> dict[str, Any]:
        return self.client.checkpoint_save(name)

    def checkpoint_load(self, name: str) -> dict[str, Any]:
        return self.client.checkpoint_load(name)

    def checkpoint_list(self) -> list[dict[str, Any]]:
        return list(self.client.checkpoint_list().get("checkpoints", []))

    def give(self, item: str, count: int = 1, aux: int = 0) -> dict[str, Any]:
        return self.client.give(item, count=count, aux=aux)

    def entity(self, runtime_id: int) -> dict[str, Any]:
        return self.client.entity(runtime_id)

    def ecs_entity(self, runtime_id: int) -> dict[str, Any]:
        return self.client.ecs_entity(runtime_id)

    def ecs_components(self, runtime_id: int) -> list[dict[str, Any]]:
        return list(self.client.ecs_components(runtime_id).get("components", []))

    def ecs_component(self, runtime_id: int, component_id: int, **kwargs: Any) -> dict[str, Any]:
        return self.client.ecs_component(runtime_id, component_id, **kwargs)

    def ecs_registries(self) -> list[dict[str, Any]]:
        return list(self.client.ecs_registries().get("registries", []))

    def attack(self, runtime_id: int) -> dict[str, Any]:
        return self.client.attack_entity(runtime_id)

    def key_event(self, code: int, down: bool = True, **kwargs: Any) -> dict[str, Any]:
        return self.client.key_event(code, down, **kwargs)

    def key_state(self, code: int) -> dict[str, Any]:
        return self.client.key_state(code)

    def screen(self) -> dict[str, Any]:
        return self.client.screen()

    def packet_capture(self, on: bool = True, **kwargs: Any) -> dict[str, Any]:
        return self.client.packet_capture(on, **kwargs)

    def packet_drain(self, since: int = 0, limit: int = 1024) -> dict[str, Any]:
        return self.client.packet_drain(since, limit)

    def packet_stats(self) -> dict[str, Any]:
        return self.client.packet_stats()

    def packet_bytes(self, since: int = 0, limit: int = 256) -> dict[str, Any]:
        return self.client.packet_bytes(since, limit)

    def mouse_event(self, action: int | str, **kwargs: Any) -> dict[str, Any]:
        return self.client.mouse_event(action, **kwargs)

    def interact(self, runtime_id: int) -> dict[str, Any]:
        return self.client.interact_entity(runtime_id)

    def look_at_entity(self, runtime_id: int) -> dict[str, Any]:
        return self.client.look_at_entity(runtime_id)

    def walk_to(self, x: float, z: float, **kwargs: Any) -> dict[str, Any]:
        return self.client.navigate(x, z, **kwargs)

    def jump_when(self, mode: str = "now", **kwargs: Any) -> dict[str, Any]:
        return self.client.jump_when(mode, **kwargs)

    def route(self, waypoints: Iterable[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        return self.client.route(waypoints, **kwargs)

    def mine(self, x: int, y: int, z: int, **kwargs: Any) -> dict[str, Any]:
        return self.client.mine_block(x, y, z, **kwargs)

    def place(self, x: int, y: int, z: int, **kwargs: Any) -> dict[str, Any]:
        return self.client.place_block(x, y, z, **kwargs)

    def use_item(self, ticks: int = 32) -> dict[str, Any]:
        return self.client.use_item(ticks)

    def task_status(self) -> dict[str, Any]:
        return self.client.task_status()

    def worlds(self) -> list[dict[str, Any]]:
        return list(self.client.worlds().get("worlds", []))

    def enter_world(self, name: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.client.enter_world(name, **kwargs)

    def enter_world_ready(self, name: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.client.enter_world_ready(name, **kwargs)

    def signin(self) -> dict[str, Any]:
        return self.client.signin()

    def leave_world(self) -> dict[str, Any]:
        return self.client.leave_world()

    def reset(self, x: float, y: float, z: float, **kwargs: Any) -> dict[str, Any]:
        return self.client.reset(x, y, z, **kwargs)

    def wait_in_world(self, timeout: float = 90.0) -> dict[str, Any]:
        return self.client.wait_in_world(timeout)

    def set_options(self, values: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.client.set_options(values, **kwargs)

    def save_options(self) -> dict[str, Any]:
        return self.client.save_options()

    def stop_task(self) -> dict[str, Any]:
        return self.client.task_stop()

    def panic(self, level: str = "all") -> dict[str, Any]:
        return self.client.panic(level)

    def reconnect(self) -> Harness:
        self.client.reconnect()
        return self

    def wait_ready(self, timeout: float = 60.0, require_world: bool = True) -> dict[str, Any]:
        return self.client.wait_ready(timeout, require_world)

    def command(self, command: str) -> dict[str, Any]:
        return self.client.command(command)

    def walk(
        self,
        forward: float = 1.0,
        strafe: float = 0.0,
        ticks: int = 20,
        *,
        sprint: bool = False,
        sneak: bool = False,
    ) -> dict[str, Any]:
        return self.client.set_input(forward, strafe, sprint=sprint, sneak=sneak, ticks=ticks)

    def stop(self) -> dict[str, Any]:
        return self.client.stop_move()

    def jump(self) -> dict[str, Any]:
        return self.client.jump()

    def look_at(self, yaw: float, pitch: float = 0.0, *, ticks: int = 1) -> dict[str, Any]:
        return self.client.look(yaw, pitch, relative=False, ticks=ticks)

    def turn(self, yaw_delta: float, pitch_delta: float = 0.0, *, ticks: int = 1) -> dict[str, Any]:
        return self.client.look(yaw_delta, pitch_delta, relative=True, ticks=ticks)

    def play_macro(
        self, name: str | None = None, *, loop: bool = False, event_level: bool = False
    ) -> dict[str, Any]:
        if name is not None:
            self.client.macro_load(name)
        return self.client.macro_run(loop=loop, event_level=event_level)

    def stop_macro(self) -> dict[str, Any]:
        return self.client.macro_stop()

    def record_bin_start(self, period: int = 1) -> dict[str, Any]:
        return self.client.record_bin_start(period)

    def record_bin_stop(self) -> dict[str, Any]:
        return self.client.record_bin_stop()

    def record_dump_all(self, **kwargs: Any) -> list[RecordEntry]:
        return self.client.record_dump_all(**kwargs)

    def record_macro(self, name: str, *, max_ticks: int = 0) -> dict[str, Any]:
        self.client.macro_record_start(max_ticks)
        input("recording... press Enter to stop")
        return self.client.macro_record_stop(name)


__all__ = ["Harness", "ModClient"]
