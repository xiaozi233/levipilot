"""World-state queries: state, chat, entities, blocks, scan, inventory, slots."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..connection import PipeConnection
from ..errors import ModProtocolError

# Names that can appear for water/lava blocks (source and flowing variants).
LIQUID_BLOCKS = frozenset(
    {"minecraft:water", "minecraft:flowing_water", "minecraft:lava", "minecraft:flowing_lava"}
)


class QueryCommands(PipeConnection):
    def chat_history(self, limit: int = 32) -> dict[str, Any]:
        """Read the client's retained chat messages, oldest first (at most 64).

        This is a snapshot, not a durable log. Each text is capped at 2047 UTF-8
        bytes and reports truncation; the game controls history retention.
        A menu request returns ok=False/reason=no_player; the mod ends an
        unserviced request after 2 seconds with reason=chat_history_timeout.
        """
        return self.request({"cmd": "chat_history", "limit": int(limit)}, "chat_history")

    def entities(self, radius: float = 16.0, limit: int = 64) -> dict[str, Any]:
        return self.request(
            {"cmd": "entities", "radius": float(radius), "limit": int(limit)}, "entities"
        )

    def blocks(self, positions: Iterable[Iterable[float]]) -> dict[str, Any]:
        return self.request({"cmd": "blocks", "positions": [list(p) for p in positions]}, "blocks")

    def _read_state_word(self, addr: int, length: int = 4) -> int:
        reply = self.request(
            {"cmd": "probe", "addr": int(addr) + 288, "kind": "raw", "len": int(length)},
            "probe",
        )
        if not reply.get("ok"):
            raise ModProtocolError(f"block_state_word: {reply.get('reason', reply)}")
        return int.from_bytes(bytes(reply["vals"]["u8"][:length]), "little")

    def block_state_word(self, pos: Iterable[float], *, length: int = 4) -> int:
        """Read the block's packed state word (`Block+288`) as a little-endian int.

        Use this instead of `/testforblock ... ["liquid_depth"=N]` to inspect a
        liquid level: the command matches `liquid_depth` unreliably (a whole
        pool reported `=0`), while `+288` carries the real value in its low
        byte. `length` is capped at 8 by ``probe``.
        """
        row = self.blocks([pos])["blocks"][0]
        return self._read_state_word(int(row["addr"]), length)

    def liquid_depth(self, pos: Iterable[float]) -> int | None:
        """Return the liquid level (`0` source .. `7` thinnest) or ``None`` for non-liquids."""
        row = self.blocks([pos])["blocks"][0]
        if row.get("name") not in LIQUID_BLOCKS:
            return None
        return self._read_state_word(int(row["addr"]), 2) & 0xFF

    def scan(
        self,
        start: Iterable[float],
        end: Iterable[float],
        *,
        include_air: bool = False,
        limit: int = 4096,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Scan up to 32768 cells in batches of at most 512 per game tick.

        The result includes the first/last sampling tick; a moving world can
        change during the scan. The mod ends an unfinished scan after 10 s.
        """
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
        """Inventory snapshot: slots, armor, offhand and selected slot.

        Up to 256 inventory slots; ``truncated`` reports text/slot limits.
        The mod ends unserviced requests after 2 s and rejects a busy slot.
        """
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
