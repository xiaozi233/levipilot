"""ECS registry/component introspection (T6/H0.3)."""

from __future__ import annotations

from typing import Any

from ..connection import PipeConnection


class EcsCommands(PipeConnection):
    def ecs_registries(self, timeout: float | None = None) -> dict[str, Any]:
        return self.request({"cmd": "ecs_registries"}, "ecs_registries", timeout=timeout)

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
