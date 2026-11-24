"""ECS registry/component introspection (T6/H0.3)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ._protocol import PipeMixinBase


class EcsCommands(PipeMixinBase):
    def ecs_registries(
        self, timeout: float | None = None, *, scope: str | None = None
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"cmd": "ecs_registries"}
        if scope is not None:
            message["scope"] = scope
        return self.request(message, "ecs_registries", timeout=timeout)

    def ecs_context(
        self, context: int | str, component_id: int, timeout: float | None = None
    ) -> dict[str, Any]:
        return self.request(
            {"cmd": "ecs_context", "context": context, "component_id": int(component_id)},
            "ecs_context",
            timeout=timeout,
        )

    def ecs_entity(self, runtime_id: int, timeout: float | None = None) -> dict[str, Any]:
        return self.request(
            {"cmd": "ecs_entity", "runtime_id": int(runtime_id)},
            "ecs_entity",
            timeout=timeout,
        )

    def ecs_components(self, runtime_id: int, timeout: float | None = None) -> dict[str, Any]:
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
        timeout: float | None = None,
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

    def ecs_read_batch(
        self,
        items: Sequence[Mapping[str, Any]],
        *,
        context: int | str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Atomically read up to 8 components (and optional memory slices) in one game tick."""
        payload_items: list[dict[str, int]] = []
        for item in items:
            entry: dict[str, int] = {
                "component_id": int(item["component_id"]),
                "offset": int(item.get("offset", 0)),
                "len": int(item.get("len", 0)),
            }
            payload_items.append(entry)
        message: dict[str, Any] = {"cmd": "ecs_read_batch", "items": payload_items}
        if context is not None:
            message["context"] = context
        return self.request(message, "ecs_read_batch", timeout=timeout)
