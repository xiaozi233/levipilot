"""Transport, protocol and task workflow errors.

All errors raised by the harness inherit from :class:`ModPipeError`, so callers
can use a single ``except ModPipeError`` or distinguish disconnection, timeout,
protocol rejection and task cancellation. Cancellation keeps the pipe open.
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


class ModTaskCancelled(ModPipeError):
    """A running task was explicitly stopped, replaced or invalidated."""

    def __init__(self, task_id: int, reason: str) -> None:
        self.task_id = task_id
        self.reason = reason
        super().__init__(f"task {task_id} cancelled: {reason}")


__all__ = ["ModDisconnected", "ModPipeError", "ModProtocolError", "ModTaskCancelled", "ModTimeout"]
