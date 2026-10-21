"""Named-pipe client for the in-game harness/probe mod.

The mod listens on ``\\\\.\\pipe\\mcbe-harness`` and speaks JSON lines.
This module exposes a small, script-friendly API plus a CLI.

Typical use::

    py -3.11 driver/transport_mod.py state
    py -3.11 driver/transport_mod.py set --forward 1 --ticks 40
    py -3.11 driver/transport_mod.py look --yaw 90 --pitch 0 --absolute
    py -3.11 driver/transport_mod.py navigate 10 20
    py -3.11 driver/transport_mod.py mine 3 -49 3
    py -3.11 driver/transport_mod.py place 3 -49 3 --expected stone
    py -3.11 driver/transport_mod.py use --ticks 32
    py -3.11 driver/transport_mod.py macro load --name default
    py -3.11 driver/transport_mod.py macro run
    py -3.11 driver/transport_mod.py macro rec-start
    py -3.11 driver/transport_mod.py macro rec-stop --name my_run
"""

from __future__ import annotations

import argparse
import ctypes
import json
import msvcrt
import queue
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable, Iterable

PIPE_NAME = r"\\.\pipe\mcbe-harness"

DEFAULT_TIMEOUT = 10.0

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.PeekNamedPipe.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.PeekNamedPipe.restype = wintypes.BOOL


class ModPipeError(RuntimeError):
    """Base class for transport and protocol errors."""


class ModTimeout(ModPipeError):
    """The mod did not answer within the requested time."""


class ModDisconnected(ModPipeError):
    """The pipe was closed, usually because the game exited."""


class ModProtocolError(ModPipeError):
    """The mod answered with an ``error`` event."""


class _LineDecoder:
    """Incrementally split a byte stream into newline-terminated lines.

    A byte-mode pipe is allowed to deliver a JSON line in arbitrary chunks, so
    the reader must not assume one ``read`` equals one message.
    """

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
        self._file = None
        self._reader: threading.Thread | None = None
        self._lines: queue.Queue[dict[str, Any]] = queue.Queue()
        self._closed = threading.Event()

    def connect(self) -> "ModClient":
        self.close()
        deadline = time.monotonic() + self.connect_timeout
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            try:
                handle = open(self.pipe_name, "r+b", buffering=0)
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
            return self
        raise ModPipeError(f"无法连接 {self.pipe_name}: {last_error}")

    def reconnect(self) -> "ModClient":
        """Drop the current pipe and open a fresh one (game restart, mod reload)."""
        return self.connect()

    def ensure_connected(self) -> "ModClient":
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
        decoder = _LineDecoder()
        os_handle = msvcrt.get_osfhandle(handle.fileno())
        try:
            while not self._closed.is_set():
                available = wintypes.DWORD(0)
                ok = _kernel32.PeekNamedPipe(
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
            try:
                handle.close()
            except OSError:
                pass
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
                raise ModProtocolError(
                    f"{message.get('cmd')}: {reply.get('reason', reply)}"
                )
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

    def state(self) -> dict[str, Any]:
        return self.request({"cmd": "state"}, "state")

    def start(self, max_ticks: int = 0) -> dict[str, Any]:
        return self.request({"cmd": "start", "max_ticks": int(max_ticks)}, "started")

    def stop(self) -> dict[str, Any]:
        return self.request({"cmd": "stop"}, "stopped")

    def move(self, forward: float = 0.0, strafe: float = 0.0, ticks: int = 1) -> dict[str, Any]:
        return self.request(
            {"cmd": "move", "forward": float(forward), "strafe": float(strafe), "ticks": int(ticks)},
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

    def entities(self, radius: float = 16.0, limit: int = 64) -> dict[str, Any]:
        return self.request({"cmd": "entities", "radius": float(radius), "limit": int(limit)}, "entities")

    def blocks(self, positions: Iterable[Iterable[float]]) -> dict[str, Any]:
        return self.request(
            {"cmd": "blocks", "positions": [list(p) for p in positions]}, "blocks"
        )

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
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Walk to ``(x, z)`` in a closed loop on the client tick.

        The mod turns and re-aims every tick, jumps when it stops making
        progress, and reports ``arrived`` / ``timeout`` once.
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
            },
            "navigated",
            timeout=timeout,
        )

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
        return self.request(
            {"cmd": "use", "ticks": int(ticks)}, "item_used", timeout=timeout
        )

    def task_status(self) -> dict[str, Any]:
        return self.request({"cmd": "task_status"}, "task_status")

    def task_stop(self) -> dict[str, Any]:
        return self.request({"cmd": "task_stop"}, "task_stopped")

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

    def macro_run(self, *, start: int = 0, end: int | None = None, loop: bool = False) -> dict[str, Any]:
        message: dict[str, Any] = {"cmd": "macro_run", "start": int(start), "loop": bool(loop)}
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

    def __enter__(self) -> "ModClient":
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

    def __enter__(self) -> "Harness":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def state(self) -> dict[str, Any]:
        return self.client.state()

    def entities(self, radius: float = 16.0, limit: int = 64) -> list[dict[str, Any]]:
        return list(self.client.entities(radius, limit).get("entities", []))

    def inventory(self) -> dict[str, Any]:
        return self.client.inventory()

    def give(self, item: str, count: int = 1, aux: int = 0) -> dict[str, Any]:
        return self.client.give(item, count=count, aux=aux)

    def entity(self, runtime_id: int) -> dict[str, Any]:
        return self.client.entity(runtime_id)

    def attack(self, runtime_id: int) -> dict[str, Any]:
        return self.client.attack_entity(runtime_id)

    def interact(self, runtime_id: int) -> dict[str, Any]:
        return self.client.interact_entity(runtime_id)

    def look_at_entity(self, runtime_id: int) -> dict[str, Any]:
        return self.client.look_at_entity(runtime_id)

    def walk_to(self, x: float, z: float, **kwargs: Any) -> dict[str, Any]:
        return self.client.navigate(x, z, **kwargs)

    def mine(self, x: int, y: int, z: int, **kwargs: Any) -> dict[str, Any]:
        return self.client.mine_block(x, y, z, **kwargs)

    def place(self, x: int, y: int, z: int, **kwargs: Any) -> dict[str, Any]:
        return self.client.place_block(x, y, z, **kwargs)

    def use_item(self, ticks: int = 32) -> dict[str, Any]:
        return self.client.use_item(ticks)

    def task_status(self) -> dict[str, Any]:
        return self.client.task_status()

    def stop_task(self) -> dict[str, Any]:
        return self.client.task_stop()

    def reconnect(self) -> "Harness":
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

    def play_macro(self, name: str | None = None, *, loop: bool = False) -> dict[str, Any]:
        if name is not None:
            self.client.macro_load(name)
        return self.client.macro_run(loop=loop)

    def stop_macro(self) -> dict[str, Any]:
        return self.client.macro_stop()

    def record_macro(self, name: str, *, max_ticks: int = 0) -> dict[str, Any]:
        self.client.macro_record_start(max_ticks)
        input("recording... press Enter to stop")
        return self.client.macro_record_stop(name)


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def _add_macro_parser(parent: argparse._SubParsersAction) -> None:
    macro = parent.add_parser("macro", help="macro file playback/recording")
    sub = macro.add_subparsers(dest="macro_command", required=True)

    load = sub.add_parser("load")
    load.add_argument("--name", default="default")
    load.add_argument("--path")

    run = sub.add_parser("run")
    run.add_argument("--start", type=int, default=0)
    run.add_argument("--end", type=int)
    run.add_argument("--loop", action="store_true")

    sub.add_parser("stop")
    sub.add_parser("status")
    sub.add_parser("list")

    rec_start = sub.add_parser("rec-start")
    rec_start.add_argument("--max-ticks", type=int, default=0)

    rec_stop = sub.add_parser("rec-stop")
    rec_stop.add_argument("--name", default="recorded")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipe", default=PIPE_NAME)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping")
    sub.add_parser("status")
    sub.add_parser("diag")
    sub.add_parser("state")

    command = sub.add_parser("command", help="run a server command (fill/tp/...)")
    command.add_argument("text")

    sub.add_parser("mouse")
    sub.add_parser("mouse-clear")

    entities = sub.add_parser("entities")
    entities.add_argument("--radius", type=float, default=16.0)
    entities.add_argument("--limit", type=int, default=64)

    blocks = sub.add_parser("blocks")
    blocks.add_argument("positions", nargs="+", type=float, help="x y z [x y z ...]")

    slot = sub.add_parser("slot")
    slot.add_argument("slot", type=int)

    sub.add_parser("inventory")

    give = sub.add_parser("give")
    give.add_argument("item")
    give.add_argument("--count", type=int, default=1)
    give.add_argument("--aux", type=int, default=0)

    set_item = sub.add_parser("set-item")
    set_item.add_argument("slot", type=int)
    set_item.add_argument("item")
    set_item.add_argument("--count", type=int, default=1)
    set_item.add_argument("--aux", type=int, default=0)
    set_item.add_argument("--container", default="inventory", choices=["inventory", "armor", "offhand"])

    clear_item = sub.add_parser("clear-item")
    clear_item.add_argument("slot", type=int)
    clear_item.add_argument("--container", default="inventory", choices=["inventory", "armor", "offhand"])

    sub.add_parser("clear-inventory")

    entity = sub.add_parser("entity")
    entity.add_argument("runtime_id", type=int)

    attack = sub.add_parser("attack")
    attack.add_argument("runtime_id", type=int)

    interact = sub.add_parser("interact")
    interact.add_argument("runtime_id", type=int)

    look_entity = sub.add_parser("look-entity")
    look_entity.add_argument("runtime_id", type=int)

    click = sub.add_parser("click")
    click.add_argument("button", choices=["left", "right"])
    click.add_argument("--reach", type=float, default=5.5)

    navigate = sub.add_parser("navigate", help="closed-loop walk to x,z")
    navigate.add_argument("x", type=float)
    navigate.add_argument("z", type=float)
    navigate.add_argument("--tolerance", type=float, default=0.35)
    navigate.add_argument("--sprint", action="store_true")
    navigate.add_argument("--max-ticks", type=int, default=600)

    mine = sub.add_parser("mine", help="break one block")
    mine.add_argument("x", type=int)
    mine.add_argument("y", type=int)
    mine.add_argument("z", type=int)
    mine.add_argument("--max-ticks", type=int, default=400)

    place = sub.add_parser("place", help="place the held block into x,y,z")
    place.add_argument("x", type=int)
    place.add_argument("y", type=int)
    place.add_argument("z", type=int)
    place.add_argument("--against", nargs=3, type=int, metavar=("AX", "AY", "AZ"))
    place.add_argument("--face", type=int, choices=range(6))
    place.add_argument("--expected")
    place.add_argument("--slot", type=int)
    place.add_argument("--max-ticks", type=int, default=30)

    use = sub.add_parser("use", help="hold and release the held item")
    use.add_argument("--ticks", type=int, default=32)

    sub.add_parser("task-status")
    sub.add_parser("task-stop")

    fly = sub.add_parser("fly")
    fly.add_argument("--off", action="store_true")

    teleport = sub.add_parser("teleport")
    teleport.add_argument("x", type=float)
    teleport.add_argument("y", type=float)
    teleport.add_argument("z", type=float)
    teleport.add_argument("--yaw", type=float)
    teleport.add_argument("--pitch", type=float)

    set_input = sub.add_parser("set")
    set_input.add_argument("--forward", type=float, default=0.0)
    set_input.add_argument("--strafe", type=float, default=0.0)
    set_input.add_argument("--sprint", action="store_true")
    set_input.add_argument("--sneak", action="store_true")
    set_input.add_argument("--jump", action="store_true")
    set_input.add_argument("--rmb", action="store_true")
    set_input.add_argument("--lmb", action="store_true")
    set_input.add_argument("--ticks", type=int, default=1)

    look = sub.add_parser("look")
    look.add_argument("--yaw", type=float, required=True)
    look.add_argument("--pitch", type=float, default=0.0)
    look.add_argument("--absolute", action="store_true", help="treat yaw/pitch as absolute")
    look.add_argument("--ticks", type=int, default=1)

    sub.add_parser("jump")
    sub.add_parser("stopmove")

    record = sub.add_parser("record")
    record.add_argument("--ticks", type=int, default=40)
    record.add_argument("--out", type=Path, default=None)
    record.add_argument("--move-forward", type=float, default=0.0)
    record.add_argument("--move-strafe", type=float, default=0.0)
    record.add_argument("--move-ticks", type=int, default=0)

    _add_macro_parser(sub)

    args = parser.parse_args(argv)

    try:
        with ModClient(args.pipe) as client:
            if args.command == "ping":
                print(json.dumps(client.ping(), ensure_ascii=False))
            elif args.command == "status":
                print(json.dumps(client.status(), ensure_ascii=False))
            elif args.command == "diag":
                print(json.dumps(client.diag(), ensure_ascii=False))
            elif args.command == "command":
                print(json.dumps(client.command(args.text), ensure_ascii=False))
            elif args.command == "mouse":
                print(json.dumps(client.mouse(), ensure_ascii=False))
            elif args.command == "mouse-clear":
                print(json.dumps(client.mouse_clear(), ensure_ascii=False))
            elif args.command == "state":
                print(json.dumps(client.state(), ensure_ascii=False, indent=2))
            elif args.command == "entities":
                print(json.dumps(client.entities(args.radius, args.limit), ensure_ascii=False, indent=2))
            elif args.command == "blocks":
                if len(args.positions) % 3:
                    parser.error("blocks 需要 3 的倍数个坐标")
                positions = [args.positions[i : i + 3] for i in range(0, len(args.positions), 3)]
                print(json.dumps(client.blocks(positions), ensure_ascii=False, indent=2))
            elif args.command == "slot":
                print(json.dumps(client.select_slot(args.slot)))
            elif args.command == "inventory":
                print(json.dumps(client.inventory(), ensure_ascii=False, indent=2))
            elif args.command == "give":
                print(
                    json.dumps(
                        client.give(args.item, count=args.count, aux=args.aux),
                        ensure_ascii=False,
                    )
                )
            elif args.command == "set-item":
                print(
                    json.dumps(
                        client.set_item(
                            args.slot,
                            args.item,
                            count=args.count,
                            aux=args.aux,
                            container=args.container,
                        ),
                        ensure_ascii=False,
                    )
                )
            elif args.command == "clear-item":
                print(
                    json.dumps(
                        client.clear_item(args.slot, container=args.container),
                        ensure_ascii=False,
                    )
                )
            elif args.command == "clear-inventory":
                print(json.dumps(client.clear_inventory(), ensure_ascii=False))
            elif args.command == "entity":
                print(json.dumps(client.entity(args.runtime_id), ensure_ascii=False, indent=2))
            elif args.command == "attack":
                print(json.dumps(client.attack_entity(args.runtime_id), ensure_ascii=False))
            elif args.command == "interact":
                print(json.dumps(client.interact_entity(args.runtime_id), ensure_ascii=False))
            elif args.command == "look-entity":
                print(json.dumps(client.look_at_entity(args.runtime_id), ensure_ascii=False))
            elif args.command == "click":
                print(json.dumps(client.click(args.button, args.reach)))
            elif args.command == "navigate":
                print(
                    json.dumps(
                        client.navigate(
                            args.x,
                            args.z,
                            tolerance=args.tolerance,
                            sprint=args.sprint,
                            max_ticks=args.max_ticks,
                        ),
                        ensure_ascii=False,
                    )
                )
            elif args.command == "mine":
                print(
                    json.dumps(
                        client.mine_block(args.x, args.y, args.z, max_ticks=args.max_ticks),
                        ensure_ascii=False,
                    )
                )
            elif args.command == "place":
                against = tuple(args.against) if args.against is not None else None
                print(
                    json.dumps(
                        client.place_block(
                            args.x,
                            args.y,
                            args.z,
                            against=against,
                            face=args.face,
                            expected=args.expected,
                            slot=args.slot,
                            max_ticks=args.max_ticks,
                        ),
                        ensure_ascii=False,
                    )
                )
            elif args.command == "use":
                print(json.dumps(client.use_item(args.ticks), ensure_ascii=False))
            elif args.command == "task-status":
                print(json.dumps(client.task_status(), ensure_ascii=False, indent=2))
            elif args.command == "task-stop":
                print(json.dumps(client.task_stop(), ensure_ascii=False))
            elif args.command == "fly":
                print(json.dumps(client.fly(not args.off)))
            elif args.command == "teleport":
                print(json.dumps(client.teleport(args.x, args.y, args.z, yaw=args.yaw, pitch=args.pitch)))
            elif args.command == "set":
                print(
                    json.dumps(
                        client.set_input(
                            args.forward,
                            args.strafe,
                            sprint=args.sprint,
                            sneak=args.sneak,
                            jump=args.jump,
                            rmb=args.rmb,
                            lmb=args.lmb,
                            ticks=args.ticks,
                        )
                    )
                )
            elif args.command == "look":
                print(
                    json.dumps(
                        client.look(
                            args.yaw,
                            args.pitch,
                            relative=not args.absolute,
                            ticks=args.ticks,
                        )
                    )
                )
            elif args.command == "jump":
                print(json.dumps(client.jump()))
            elif args.command == "stopmove":
                print(json.dumps(client.stop_move()))
            elif args.command == "record":
                samples = client.record(
                    args.ticks,
                    move_forward=args.move_forward,
                    move_strafe=args.move_strafe,
                    move_ticks=args.move_ticks,
                )
                if args.out is not None:
                    _write_jsonl(args.out, samples)
                print(f"samples={len(samples)}")
                if samples:
                    print(json.dumps(samples[0], ensure_ascii=False))
                    print(json.dumps(samples[-1], ensure_ascii=False))
            elif args.command == "macro":
                if args.macro_command == "load":
                    print(json.dumps(client.macro_load(args.name, args.path), ensure_ascii=False))
                elif args.macro_command == "run":
                    print(json.dumps(client.macro_run(start=args.start, end=args.end, loop=args.loop)))
                elif args.macro_command == "stop":
                    print(json.dumps(client.macro_stop()))
                elif args.macro_command == "status":
                    print(json.dumps(client.macro_status(), ensure_ascii=False, indent=2))
                elif args.macro_command == "list":
                    print(json.dumps(client.macro_list(), ensure_ascii=False, indent=2))
                elif args.macro_command == "rec-start":
                    print(json.dumps(client.macro_record_start(args.max_ticks)))
                elif args.macro_command == "rec-stop":
                    print(json.dumps(client.macro_record_stop(args.name)))
    except ModPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("提示：游戏内是否已加载 probe/harness mod，并且已经进入存档？", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())