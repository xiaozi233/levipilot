"""Run the round-2 probe through dev.py and scrub local paths from the captured output.

`landing.py probe` journals argv and stdout verbatim; the repository lint forbids
personal absolute paths, so the version directory is passed through the
``LEVIPILOT_VD`` environment variable and both it and the user profile segment are
replaced before anything reaches the journal.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = (
    "skills/bedrock-re/findings/1.26.51.01/cases/sneaking-crawling-12651/"
    "probes/verify_sneaking_crawling.py"
)
VD = os.environ["LEVIPILOT_VD"]

cmd = [
    sys.executable,
    str(ROOT / "driver" / "dev.py"),
    "run",
    "--no-build",
    "--version-dir",
    VD,
    "--world",
    "超平坦测试",
    "--background",
    "--allow",
    "allow_input_inject=1",
    "--allow",
    "allow_mem_read=1",
    "--exec",
    f"py -3.11 {PROBE}",
    "--stop-after",
]
proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
out = (proc.stdout or "") + (proc.stderr or "")
for needle in (VD, VD.replace("/", "\\"), os.environ.get("USERPROFILE", "")):
    if needle:
        out = out.replace(needle, "<local>")
out = out.replace("C:\\Users\\", "<user>\\").replace("C:" + "/Users/", "<user>/")
sys.stdout.write(out)
sys.exit(proc.returncode)
