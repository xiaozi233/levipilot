"""Backwards-compatible facade for the harness package.

The implementation now lives in :mod:`driver.harness`:

- :mod:`driver.harness.protocol` – pipe name and line framing
- :mod:`driver.harness.client` – ``ModClient`` / ``Harness``
- :mod:`driver.harness.connection` / :mod:`driver.harness.commands` /
  :mod:`driver.harness.facade` – connection, game commands and script helpers
- :mod:`driver.harness.cli` – command-line entry point

``python driver/transport_mod.py ...`` and ``python -m driver.transport_mod``
both keep working, and existing ``from driver.transport_mod import ...`` callers
see the same names.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow ``python driver/transport_mod.py`` (script mode puts driver/ first on
# sys.path, which would hide the driver package).
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from driver.harness import (
    DEFAULT_TIMEOUT,
    PIPE_NAME,
    Harness,
    LineDecoder,
    ModClient,
    ModDisconnected,
    ModPipeError,
    ModProtocolError,
    ModTaskCancelled,
    ModTimeout,
)
from driver.harness.cli import main

__all__ = [
    "DEFAULT_TIMEOUT",
    "PIPE_NAME",
    "Harness",
    "LineDecoder",
    "ModClient",
    "ModDisconnected",
    "ModPipeError",
    "ModProtocolError",
    "ModTaskCancelled",
    "ModTimeout",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
