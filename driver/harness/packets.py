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

# Full MinecraftPacketIds table (LeviLamina 26.51.7 / MCBE 1.26.51.01).
PACKET_NAMES: dict[int, str] = {
    0: "KeepAlive",
    1: "Login",
    2: "PlayStatus",
    3: "ServerToClientHandshake",
    4: "ClientToServerHandshake",
    5: "Disconnect",
    6: "ResourcePacksInfo",
    7: "ResourcePackStack",
    8: "ResourcePackClientResponse",
    9: "Text",
    10: "SetTime",
    11: "StartGame",
    12: "AddPlayer",
    13: "AddActor",
    14: "RemoveActor",
    15: "AddItemActor",
    16: "ServerPlayerPostMovePosition",
    17: "TakeItemActor",
    18: "MoveAbsoluteActor",
    19: "MovePlayer",
    20: "PassengerJumpDeprecated",
    21: "UpdateBlock",
    22: "AddPainting",
    23: "TickSyncDeprecated",
    24: "LevelSoundEventV1Deprecated",
    25: "LevelEvent",
    26: "TileEvent",
    27: "ActorEvent",
    28: "MobEffect",
    29: "UpdateAttributes",
    30: "InventoryTransaction",
    31: "PlayerEquipment",
    32: "MobArmorEquipment",
    33: "Interact",
    34: "BlockPickRequest",
    35: "ActorPickRequest",
    36: "PlayerAction",
    37: "ActorFallDeprecated",
    38: "HurtArmor",
    39: "SetActorData",
    40: "SetActorMotion",
    41: "SetActorLink",
    42: "SetHealth",
    43: "SetSpawnPosition",
    44: "Animate",
    45: "Respawn",
    46: "ContainerOpen",
    47: "ContainerClose",
    48: "PlayerHotbar",
    49: "InventoryContent",
    50: "InventorySlot",
    51: "ContainerSetData",
    52: "CraftingData",
    53: "CraftingEventDeprecated",
    54: "GuiDataPickItem",
    55: "AdventureSettingsDeprecated",
    56: "BlockActorData",
    57: "PlayerInputDeprecated",
    58: "FullChunkData",
    59: "SetCommandsEnabled",
    60: "SetDifficulty",
    61: "ChangeDimension",
    62: "SetPlayerGameType",
    63: "PlayerList",
    64: "SimpleEvent",
    65: "LegacyTelemetryEvent",
    66: "SpawnExperienceOrb",
    67: "MapData",
    68: "MapInfoRequest",
    69: "RequestChunkRadius",
    70: "ChunkRadiusUpdated",
    71: "ItemFrameDropItemDeprecated",
    72: "GameRulesChanged",
    73: "Camera",
    74: "BossEvent",
    75: "ShowCredits",
    76: "AvailableCommands",
    77: "CommandRequest",
    78: "CommandBlockUpdate",
    79: "CommandOutput",
    80: "UpdateTrade",
    81: "UpdateEquip",
    82: "ResourcePackDataInfo",
    83: "ResourcePackChunkData",
    84: "ResourcePackChunkRequest",
    85: "Transfer",
    86: "PlaySound",
    87: "StopSound",
    88: "SetTitle",
    89: "AddBehaviorTree",
    90: "StructureBlockUpdate",
    91: "ShowStoreOffer",
    92: "PurchaseReceipt",
    93: "PlayerSkin",
    94: "SubclientLogin",
    95: "AutomationClientConnect",
    96: "SetLastHurtBy",
    97: "BookEdit",
    98: "NPCRequest",
    99: "PhotoTransfer",
    100: "ShowModalForm",
    101: "ModalFormResponse",
    102: "ServerSettingsRequest",
    103: "ServerSettingsResponse",
    104: "ShowProfile",
    105: "SetDefaultGameType",
    106: "RemoveObjective",
    107: "SetDisplayObjective",
    108: "SetScore",
    109: "LabTable",
    110: "UpdateBlockSynced",
    111: "MoveDeltaActor",
    112: "SetScoreboardIdentity",
    113: "SetLocalPlayerAsInit",
    114: "UpdateSoftEnum",
    115: "Ping",
    116: "BlockPaletteDeprecated",
    117: "ScriptCustomEvent",
    118: "SpawnParticleEffect",
    119: "AvailableActorIDList",
    120: "LevelSoundEventV2Deprecated",
    121: "NetworkChunkPublisherUpdate",
    122: "BiomeDefinitionList",
    123: "LevelSoundEvent",
    124: "LevelEventGeneric",
    125: "LecternUpdate",
    126: "VideoStreamConnectDeprecated",
    127: "AddEntityDeprecated",
    128: "RemoveEntityDeprecated",
    129: "ClientCacheStatus",
    130: "OnScreenTextureAnimation",
    131: "MapCreateLockedCopy",
    132: "StructureTemplateDataExportRequest",
    133: "StructureTemplateDataExportResponse",
    134: "UnusedPlsUseMe",
    135: "ClientCacheBlobStatusPacket",
    136: "ClientCacheMissResponsePacket",
    137: "EducationSettingsPacket",
    138: "Emote",
    139: "MultiplayerSettingsPacket",
    140: "SettingsCommandPacket",
    141: "AnvilDamage",
    142: "CompletedUsingItem",
    143: "NetworkSettings",
    144: "PlayerAuthInputPacket",
    145: "CreativeContent",
    146: "PlayerEnchantOptions",
    147: "ItemStackRequest",
    148: "ItemStackResponse",
    149: "PlayerArmorDamage",
    150: "CodeBuilderPacket",
    151: "UpdatePlayerGameType",
    152: "EmoteList",
    153: "PositionTrackingDBServerBroadcast",
    154: "PositionTrackingDBClientRequest",
    155: "DebugInfoPacket",
    156: "PacketViolationWarning",
    157: "MotionPredictionHints",
    158: "TriggerAnimation",
    159: "CameraShake",
    160: "PlayerFogSetting",
    161: "CorrectPlayerMovePredictionPacket",
    162: "ItemRegistryPacket",
    163: "FilterTextPacketDeprecated",
    164: "ClientBoundDebugRendererPacket",
    165: "SyncActorProperty",
    166: "AddVolumeEntityPacket",
    167: "RemoveVolumeEntityPacket",
    168: "SimulationTypePacket",
    169: "NpcDialoguePacket",
    170: "EduUriResourcePacket",
    171: "CreatePhotoPacket",
    172: "UpdateSubChunkBlocks",
    173: "PhotoInfoRequestDeprecated",
    174: "SubChunkPacket",
    175: "SubChunkRequestPacket",
    176: "PlayerStartItemCooldown",
    177: "ScriptMessagePacket",
    178: "CodeBuilderSourcePacket",
    179: "TickingAreasLoadStatus",
    180: "DimensionDataPacket",
    181: "AgentAction",
    182: "ChangeMobProperty",
    183: "LessonProgressPacket",
    184: "RequestAbilityPacket",
    185: "RequestPermissionsPacket",
    186: "ToastRequest",
    187: "UpdateAbilitiesPacket",
    188: "UpdateAdventureSettingsPacket",
    189: "DeathInfo",
    190: "EditorNetworkPacket",
    191: "FeatureRegistryPacket",
    192: "ServerStats",
    193: "RequestNetworkSettings",
    194: "GameTestRequestPacket",
    195: "GameTestResultsPacket",
    196: "PlayerClientInputPermissions",
    197: "ClientCheatAbilityPacketDeprecated",
    198: "CameraPresets",
    199: "UnlockedRecipes",
    200: "TitleSpecificPacketsStart",
    201: "LeviLaminaRuntimePacket",
    299: "TitleSpecificPacketsEnd",
    300: "CameraInstruction",
    301: "CompressedBiomeDefinitionListDeprecated",
    302: "TrimData",
    303: "OpenSign",
    304: "AgentAnimation",
    305: "RefreshEntitlementsPacket",
    306: "PlayerToggleCrafterSlotRequestPacket",
    307: "SetPlayerInventoryOptions",
    308: "SetHudPacket",
    309: "AwardAchievementPacket",
    310: "ClientboundCloseScreen",
    311: "ClientboundLoadingScreenPacketDeprecated",
    312: "ServerboundLoadingScreenPacket",
    313: "JigsawStructureDataPacket",
    314: "CurrentStructureFeaturePacket",
    315: "ServerboundDiagnosticsPacket",
    316: "CameraAimAssist",
    317: "ContainerRegistryCleanup",
    318: "MovementEffect",
    319: "SetMovementAuthorityModeDeprecated",
    320: "CameraAimAssistPresets",
    321: "ClientCameraAimAssist",
    322: "ClientMovementPredictionSyncPacket",
    323: "UpdateClientOptions",
    324: "PlayerVideoCapturePacket",
    325: "PlayerUpdateEntityOverridesPacket",
    326: "PlayerLocation",
    327: "ClientboundControlSchemeSetPacket",
    328: "PrimitiveShapesPacket",
    329: "ServerboundPackSettingChangePacket",
    330: "ClientboundDataStore",
    331: "GraphicsParameterOverridePacket",
    332: "ServerboundDataStore",
    333: "ClientboundDataDrivenUIShowScreen",
    334: "ClientboundDataDrivenUICloseScreen",
    335: "ClientboundDataDrivenUIReload",
    336: "ClientboundTextureShiftPacket",
    337: "VoxelShapesPacket",
    338: "CameraSpline",
    339: "CameraAimAssistActorPriority",
    340: "ResourcePacksReadyForValidation",
    341: "LocatorBarPacket",
    342: "PartyChanged",
    343: "ServerboundDataDrivenScreenClosed",
    344: "SyncWorldClocks",
    345: "ClientboundAttributeLayerSyncPacket",
    346: "ServerStoreInfo",
    347: "ServerPresenceInfo",
    348: "ClientboundUpdateSoundData",
    349: "SendPartyDestinationCookie",
    350: "PartyDestinationCookieResponse",
    351: "SetPlayerFurnaceOptions",
    352: "RecordStarted",
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
