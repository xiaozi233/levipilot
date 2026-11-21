"""Script-friendly names, unwrapped collections and multi-command helpers."""

from __future__ import annotations

from typing import Any

from .commands import ModClient
from .protocol import PIPE_NAME, StateEvent
from .record import RecordEntry


def _item_options(damage: int | None, flight: int | None) -> dict[str, int]:
    extra: dict[str, int] = {}
    if damage is not None:
        extra["damage"] = damage
    if flight is not None:
        extra["flight"] = flight
    return extra


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

    def __getattr__(self, name: str) -> Any:
        client = self.__dict__.get("client")
        if client is None:
            raise AttributeError(name)
        return getattr(client, name)

    def __dir__(self) -> list[str]:
        names = set(super().__dir__())
        client = self.__dict__.get("client")
        if client is not None:
            names.update(dir(client))
        return sorted(names)

    def state(self) -> StateEvent:
        return self.client.state()

    def entities(self, radius: float = 16.0, limit: int = 64) -> list[dict[str, Any]]:
        return list(self.client.entities(radius, limit).get("entities", []))

    def checkpoint_list(self) -> list[dict[str, Any]]:
        return list(self.client.checkpoint_list().get("checkpoints", []))

    def give(
        self,
        item: str,
        count: int = 1,
        aux: int = 0,
        *,
        damage: int | None = None,
        flight: int | None = None,
    ) -> dict[str, Any]:
        return self.client.give(item, count=count, aux=aux, **_item_options(damage, flight))

    def set_item(
        self,
        slot: int,
        item: str,
        *,
        count: int = 1,
        aux: int = 0,
        container: str = "inventory",
        damage: int | None = None,
        flight: int | None = None,
    ) -> dict[str, Any]:
        return self.client.set_item(
            slot,
            item,
            count=count,
            aux=aux,
            container=container,
            **_item_options(damage, flight),
        )

    def ecs_components(self, runtime_id: int) -> list[dict[str, Any]]:
        return list(self.client.ecs_components(runtime_id).get("components", []))

    def ecs_registries(self, *, scope: str | None = None) -> list[dict[str, Any]]:
        extra: dict[str, Any] = {"scope": scope} if scope is not None else {}
        return list(self.client.ecs_registries(**extra).get("registries", []))

    def attack(self, runtime_id: int) -> dict[str, Any]:
        return self.client.attack_entity(runtime_id)

    def interact(self, runtime_id: int) -> dict[str, Any]:
        return self.client.interact_entity(runtime_id)

    def walk_to(self, x: float, z: float, **kwargs: Any) -> dict[str, Any]:
        return self.client.navigate(x, z, **kwargs)

    def mine(self, x: int, y: int, z: int, **kwargs: Any) -> dict[str, Any]:
        return self.client.mine_block(x, y, z, **kwargs)

    def place(self, x: int, y: int, z: int, **kwargs: Any) -> dict[str, Any]:
        return self.client.place_block(x, y, z, **kwargs)

    def worlds(self) -> list[dict[str, Any]]:
        return list(self.client.worlds().get("worlds", []))

    def wait_in_world(self, timeout: float = 90.0) -> StateEvent:
        return self.client.wait_in_world(timeout)

    def stop_task(self) -> dict[str, Any]:
        return self.client.task_stop()

    def reconnect(self) -> Harness:
        self.client.reconnect()
        return self

    def wait_ready(self, timeout: float = 60.0, require_world: bool = True) -> StateEvent:
        return self.client.wait_ready(timeout, require_world)

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

    def record_dump_all(self, **kwargs: Any) -> list[RecordEntry]:
        return self.client.record_dump_all(**kwargs)
