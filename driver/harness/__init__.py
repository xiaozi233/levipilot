"""Public API for the Minecraft Bedrock harness client.

The stable surface is :class:`ModClient` (raw protocol), :class:`Harness`
(script-friendly helpers), and the error hierarchy. Wire details live in
:mod:`driver.harness.protocol`; the CLI lives in :mod:`driver.harness.cli`.
"""

from __future__ import annotations

from .client import Harness, ModClient
from .errors import ModDisconnected, ModPipeError, ModProtocolError, ModTimeout
from .hooks import HookHit, decode_hook_hits, float_from_bits
from .memory import fnv1_64, fnv1a_64, format_address, parse_hex_bytes, to_hex
from .packets import PACKET_NAMES, decode_packet, decode_row
from .protocol import DEFAULT_TIMEOUT, PIPE_NAME, LineDecoder
from .record import RecordEntry, block_hash, decode_dump, decode_hex
from .spool import SpoolReader, SpoolStats, read_spool
from .watch import HitCursor, WatchHit, decode_hits

__all__ = [
    "DEFAULT_TIMEOUT",
    "PACKET_NAMES",
    "PIPE_NAME",
    "Harness",
    "HitCursor",
    "HookHit",
    "LineDecoder",
    "ModClient",
    "ModDisconnected",
    "ModPipeError",
    "ModProtocolError",
    "ModTimeout",
    "RecordEntry",
    "SpoolReader",
    "SpoolStats",
    "WatchHit",
    "block_hash",
    "decode_dump",
    "decode_hex",
    "decode_hits",
    "decode_hook_hits",
    "decode_packet",
    "decode_row",
    "float_from_bits",
    "fnv1_64",
    "fnv1a_64",
    "format_address",
    "parse_hex_bytes",
    "read_spool",
    "to_hex",
]
