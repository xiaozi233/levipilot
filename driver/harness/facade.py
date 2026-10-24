"""Script-friendly names, unwrapped collections and multi-command helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .commands import ModClient
from .protocol import PIPE_NAME, StateEvent
from .record import RecordEntry


class Harness:
    """Convenience wrapper for LLM/script use.

    Example::

        with Harness() as h:
            print(h.state()["x"], h.state()["z"])
            h.walk(forward=1.0, ticks=40)
            print(h.state()["x"], h.state()["z"])
    """

    def __init__(
        self,
        pipe_name: str = PIPE_NAME,
        *,
        timeout: float | None = None,
        connect_timeout: float | None = None,
    ) -> None:
        client = ModClient(pipe_name)
        if timeout is not None:
            client.timeout = timeout
        if connect_timeout is not None:
            client.connect_timeout = connect_timeout
        self.client = client.connect()

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Harness:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def state(self) -> StateEvent:
        return self.client.state()

    def overhead(self) -> dict[str, Any]:
        return self.client.overhead()

    def diagnose(self) -> dict[str, Any]:
        return self.client.diagnose()

    def profile_dump(self) -> dict[str, Any]:
        return self.client.profile_dump()

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

    def wait_in_world(self, timeout: float = 90.0) -> StateEvent:
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

    def wait_ready(self, timeout: float = 60.0, require_world: bool = True) -> StateEvent:
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

    def macro_record_start(self, max_ticks: int = 0) -> dict[str, Any]:
        return self.client.macro_record_start(max_ticks)

    def macro_record_stop(self, name: str = "recorded") -> dict[str, Any]:
        return self.client.macro_record_stop(name)
