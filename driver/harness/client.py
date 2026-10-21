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
from .protocol import DEFAULT_TIMEOUT, PIPE_NAME, LineDecoder, kernel32


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

    def entities(self, radius: float = 16.0, limit: int = 64) -> dict[str, Any]:
        return self.request(
            {"cmd": "entities", "radius": float(radius), "limit": int(limit)}, "entities"
        )

    def blocks(self, positions: Iterable[Iterable[float]]) -> dict[str, Any]:
        return self.request({"cmd": "blocks", "positions": [list(p) for p in positions]}, "blocks")

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
        return self.request({"cmd": "use", "ticks": int(ticks)}, "item_used", timeout=timeout)

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

    def macro_run(
        self, *, start: int = 0, end: int | None = None, loop: bool = False
    ) -> dict[str, Any]:
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


__all__ = ["Harness", "ModClient"]
