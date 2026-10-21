"""Public API for the Minecraft Bedrock harness client.

The stable surface is :class:`ModClient` (raw protocol), :class:`Harness`
(script-friendly helpers), and the error hierarchy. Wire details live in
:mod:`driver.harness.protocol`; the CLI lives in :mod:`driver.harness.cli`.
"""

from __future__ import annotations

from .client import Harness, ModClient
from .errors import ModDisconnected, ModPipeError, ModProtocolError, ModTimeout
from .protocol import DEFAULT_TIMEOUT, PIPE_NAME, LineDecoder

__all__ = [
    "DEFAULT_TIMEOUT",
    "PIPE_NAME",
    "Harness",
    "LineDecoder",
    "ModClient",
    "ModDisconnected",
    "ModPipeError",
    "ModProtocolError",
    "ModTimeout",
]
