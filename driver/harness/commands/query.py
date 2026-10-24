"""World-state queries: state, chat, entities, blocks, scan, inventory, slots."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..connection import PipeConnection


class QueryCommands(PipeConnection):
    def chat_history(self, limit: int = 32) -> dict[str, Any]:
        """Read the client's retained chat messages, oldest first (at most 64).

        This is a snapshot, not a durable log. Each text is capped at 2047 UTF-8
        bytes and reports truncation; the game controls history retention.
        """
        return self.request({"cmd": "chat_history", "limit": int(limit)}, "chat_history")

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

    def equip(self, target: str, *, slot: int = -1) -> dict[str, Any]:
        """Move an inventory stack into a worn slot (armor/offhand).

        ``target``: head/torso/legs/feet/offhand; ``slot`` defaults to the
        selected hotbar slot. The stack is copied, so enchantments applied
        while the item was held (``/enchant``) survive the transfer, and the
        source slot is cleared.
        """
        return self.request(
            {"cmd": "equip", "target": target, "slot": int(slot)},
            "equip_done",
        )

    def clear_item(self, slot: int, *, container: str = "inventory") -> dict[str, Any]:
        return self.request(
            {"cmd": "clear_item", "slot": int(slot), "container": container},
            "inventory_set",
        )

    def clear_inventory(self) -> dict[str, Any]:
        return self.request({"cmd": "clear_inventory"}, "inventory_cleared")

    def checkpoint_save(self, name: str) -> dict[str, Any]:
        return self.request({"cmd": "checkpoint_save", "name": name}, "checkpoint_saved")

    def checkpoint_load(self, name: str, timeout: float | None = None) -> dict[str, Any]:
        return self.request(
            {"cmd": "checkpoint_load", "name": name},
            "checkpoint_loaded",
            timeout=timeout,
        )

    def checkpoint_list(self) -> dict[str, Any]:
        return self.request({"cmd": "checkpoint_list"}, "checkpoint_list")

    def checkpoint_delete(self, name: str) -> dict[str, Any]:
        return self.request({"cmd": "checkpoint_delete", "name": name}, "checkpoint_deleted")
