"""Transport and protocol errors.

All errors raised by the harness inherit from :class:`ModPipeError`, so callers
can keep a single ``except`` for "the game/mod is not reachable" while still
distinguishing timeouts from a closed pipe.
"""

from __future__ import annotations


class ModPipeError(RuntimeError):
    """Base class for transport and protocol errors."""


class ModTimeout(ModPipeError):
    """The mod did not answer within the requested time."""


class ModDisconnected(ModPipeError):
    """The pipe was closed, usually because the game exited."""


class ModProtocolError(ModPipeError):
    """The mod answered with an ``error`` event."""


__all__ = ["ModDisconnected", "ModPipeError", "ModProtocolError", "ModTimeout"]
