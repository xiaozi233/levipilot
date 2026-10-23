"""Game command payloads and workflows on top of the pipe connection.

ModClient remains the public client type; all methods retain their wire payloads,
response events and individual timeout policies.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

from .connection import PipeConnection
from .errors import ModPipeError, ModProtocolError, ModTimeout
from .memory import parse_hex_bytes, to_hex
from .record import RecordEntry, decode_dump


class ModClient(PipeConnection):
    """Typed game commands with connection/reconnection support."""

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

    def profile_dump(self) -> dict[str, Any]:
        """Version profile read from the live build; ``profile`` is saveable JSON."""
        return self.request({"cmd": "profile_dump"}, "profile_dump")

    def profile_probe(self, build_id: str) -> dict[str, Any]:
        """Report which profile a given build id would select (porting check)."""
        return self.request({"cmd": "profile_probe", "build_id": build_id}, "profile_probe")

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
