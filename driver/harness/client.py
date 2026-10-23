"""Stable import path for the public client and script facade.

Implementation dependencies flow from facade to commands to connection. Keep
these re-exports for existing ``driver.harness.client`` callers.
"""

from .commands import ModClient
from .facade import Harness

__all__ = ["Harness", "ModClient"]
