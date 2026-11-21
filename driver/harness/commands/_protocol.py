"""Structural typing protocol and runtime-empty base for command domain mixins."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Protocol


class PipeClientProtocol(Protocol):
    """Transport surface required by command domain mixins."""

    timeout: float

    def exchange(self) -> AbstractContextManager[None]: ...

    def send(self, message: dict[str, Any]) -> None: ...

    def wait_event(
        self,
        events: str | tuple[str, ...],
        timeout: float | None = None,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]: ...

    def request(
        self,
        message: dict[str, Any],
        expect: str | tuple[str, ...] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]: ...

    def close(self) -> None: ...

    def ensure_connected(self) -> Any: ...


class PipeMixinBase:
    """Empty runtime base so command mixins stay decoupled from PipeConnection."""

    if TYPE_CHECKING:
        timeout: float

        @contextlib.contextmanager
        def exchange(self) -> Iterator[None]: ...

        def send(self, message: dict[str, Any]) -> None: ...

        def wait_event(
            self,
            events: str | tuple[str, ...],
            timeout: float | None = None,
            predicate: Callable[[dict[str, Any]], bool] | None = None,
        ) -> dict[str, Any]: ...

        def request(
            self,
            message: dict[str, Any],
            expect: str | tuple[str, ...] | None = None,
            timeout: float | None = None,
        ) -> dict[str, Any]: ...

        def close(self) -> None: ...

        def ensure_connected(self) -> Any: ...
