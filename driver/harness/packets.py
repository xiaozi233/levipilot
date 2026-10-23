"""Packet identity/byte decoders (H4).

The tap only stores ``id``/``size`` and (for whitelisted ids) the first 128
serialized bytes. Those bytes do **not** include the packet-id varint: the game
serializes the payload after the id, so each decoder below starts at the first
payload field.

Layouts are marked with their source:

- ``77`` CommandRequest: verified against a live 1.26.51.01 capture.
- ``36`` PlayerAction / ``19`` MovePlayer: standard Bedrock payload layout,
  synthetic tests only (this client uses PlayerAuthInput for movement).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

# Common ids from MinecraftPacketIds. Only ids useful for experiments are named;
# unknown ids fall back to "id_<n>" and raw hex.
PACKET_NAMES: dict[int, str] = {
    9: "Text",
    19: "MovePlayer",
    30: "InventoryTransaction",
    33: "Interact",
    36: "PlayerAction",
    38: "HurtArmor",
    44: "Animate",
    57: "PlayerInputDeprecated",
    68: "MapInfoRequest",
    69: "RequestChunkRadius",
    76: "AvailableCommands",
    77: "CommandRequest",
    93: "PlayerSkin",
    113: "SetLocalPlayerAsInit",
    144: "PlayerAuthInput",
    175: "SubChunkRequest",
}

ORIGIN_TYPES: dict[int, str] = {
    0: "Player",
    1: "CommandBlock",
    2: "MinecartCommandBlock",
    3: "DevConsole",
    4: "Test",
    5: "AutomationPlayer",
    6: "ClientAutomation",
    7: "DedicatedServer",
    8: "Entity",
    9: "Virtual",
    10: "GameArgument",
    11: "EntityServer",
    12: "Precompiled",
    13: "GameDirectorEntityServer",
    14: "Scripting",
    15: "ExecuteContext",
}


class PacketDecodeError(ValueError):
    """Raised when the payload ends before a declared field."""


@dataclass
class Reader:
    data: bytes
    pos: int = 0

    def _take(self, size: int) -> bytes:
        if self.pos + size > len(self.data):
            raise PacketDecodeError(f"need {size} bytes at {self.pos}, have {len(self.data)}")
        raw = self.data[self.pos : self.pos + size]
        self.pos += size
        return raw

    def u8(self) -> int:
        return self._take(1)[0]

    def i8(self) -> int:
        return struct.unpack("<b", self._take(1))[0]

    def u16(self) -> int:
        return struct.unpack("<H", self._take(2))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self._take(8))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self._take(4))[0]

    def varint(self) -> int:
        value = 0
        shift = 0
        while True:
            byte = self.u8()
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7
            if shift > 35:
                raise PacketDecodeError("varint too long")

    def string(self) -> str:
        length = self.varint()
        if length > 4096:
            raise PacketDecodeError(f"string too long: {length}")
        return self._take(length).decode("utf-8", errors="replace")

    def uuid(self) -> str:
        raw = self._take(16)
        hexed = raw.hex()
        return f"{hexed[0:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:32]}"

    def rest(self) -> bytes:
        raw = self.data[self.pos :]
        self.pos = len(self.data)
        return raw


def decode_command_request(reader: Reader) -> dict[str, Any]:
    command = reader.string()
    origin_type = reader.u8()
    origin_name = reader.string()
    origin_uuid = reader.uuid()
    request_id = reader.string()
    player_id = reader.i64()
    version = reader.string()
    return {
        "command": command,
        "origin_type": origin_type,
        "origin_name": origin_name,
        "origin_uuid": origin_uuid,
        "request_id": request_id,
        "player_id": player_id,
        "version": version,
    }


def decode_player_action(reader: Reader) -> dict[str, Any]:
    runtime_id = reader.varint()
    action = reader.varint()
    x = reader.f32()
    y = reader.f32()
    z = reader.f32()
    face = reader.varint()
    return {
        "runtime_id": runtime_id,
        "action": action,
        "x": x,
        "y": y,
        "z": z,
        "face": face,
    }


def decode_move_player(reader: Reader) -> dict[str, Any]:
    runtime_id = reader.varint()
    x = reader.f32()
    y = reader.f32()
    z = reader.f32()
    pitch = reader.f32()
    yaw = reader.f32()
    head_yaw = reader.f32()
    mode = reader.u8()
    on_ground = reader.u8()
    ridden_runtime_id = reader.varint()
    tick = reader.varint()
    return {
        "runtime_id": runtime_id,
        "x": x,
        "y": y,
        "z": z,
        "pitch": pitch,
        "yaw": yaw,
        "head_yaw": head_yaw,
        "mode": mode,
        "on_ground": bool(on_ground),
        "ridden_runtime_id": ridden_runtime_id,
        "tick": tick,
    }


DECODERS = {
    77: decode_command_request,
    36: decode_player_action,
    19: decode_move_player,
}


def decode_packet(packet_id: int, data: bytes) -> dict[str, Any]:
    """Decode one payload; unknown ids keep the raw bytes and never raise."""
    result: dict[str, Any] = {
        "id": int(packet_id),
        "name": PACKET_NAMES.get(int(packet_id), f"id_{int(packet_id)}"),
        "size": len(data),
        "known": int(packet_id) in PACKET_NAMES,
        "hex": data.hex(),
    }
    decoder = DECODERS.get(int(packet_id))
    if decoder is None:
        return result
    try:
        result["fields"] = decoder(Reader(data))
    except (PacketDecodeError, struct.error, UnicodeDecodeError) as exc:
        result["decode_error"] = str(exc)
    return result


def decode_row(row: dict[str, Any]) -> dict[str, Any]:
    """Decode a row returned by ``packet_bytes`` (``id`` + ``hex``)."""
    return decode_packet(int(row["id"]), bytes.fromhex(str(row["hex"])))
