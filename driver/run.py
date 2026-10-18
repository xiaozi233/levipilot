"""Attach to a running Minecraft client and load the frida agent.

Usage:
    py -3.11 driver/run.py            # attach by process name
    py -3.11 driver/run.py <pid>      # attach by pid
"""

from __future__ import annotations

import sys
from pathlib import Path

import frida

AGENT = Path(__file__).resolve().parents[1] / "agent" / "agent.js"


def main() -> int:
    target: int | str = int(sys.argv[1]) if len(sys.argv) > 1 else "Minecraft.Windows.exe"
    session = frida.attach(target)

    script = session.create_script(AGENT.read_text(encoding="utf-8"))
    script.load()

    print(script.exports_sync.ping())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
