"""Game command payloads and workflows on top of the pipe connection.

ModClient is composed from one mixin per command domain; task completion is
bound to the accepted task ID and transport owns sessions and event buffering.
Timeout convention: methods take ``timeout: float | None = None`` and fall
back to the connection's ``timeout`` — only world entry keeps explicit long
defaults because the sign-in/join waits legitimately exceed them.
"""

from __future__ import annotations

from ..connection import PipeConnection
from .diag import DiagnosticsCommands
from .ecs import EcsCommands
from .hooks import HookCommands
from .input import InputCommands
from .macro import MacroCommands
from .memory import MemoryCommands
from .packets import PacketCommands
from .query import QueryCommands
from .recording import RecordingCommands
from .session import SessionCommands
from .tasks import TaskCommands
from .watch import WatchCommands
from .world import WorldCommands

__all__ = ["ModClient"]


class ModClient(
    SessionCommands,
    DiagnosticsCommands,
    InputCommands,
    MemoryCommands,
    WatchCommands,
    HookCommands,
    PacketCommands,
    EcsCommands,
    TaskCommands,
    QueryCommands,
    WorldCommands,
    MacroCommands,
    RecordingCommands,
    PipeConnection,
):
    """Typed game commands with connection/reconnection support."""
