"""Spool forensics for the three shutdown paths (H0.4).

Modes:

  panic       start a tick stream, send ``panic`` (forces a flush), then verify
              the spool is monotonic and ends with ``panic_done``.
  taskkill    start a tick stream, kill the game with ``taskkill /F``, then
              verify the on-disk spool is monotonic (up to 100 ms may be lost).
  verify      verify an existing ``--spool PATH`` file.
  leave_world not automatable yet: the pause menu does not accept injected
              keys, so "Save & Quit" still needs a desktop click. Exits 3 with
              instructions instead of silently passing.

Examples:

    py -3.11 driver/spool_forensics.py panic
    py -3.11 driver/spool_forensics.py taskkill --ticks 120
    py -3.11 driver/spool_forensics.py verify --spool <mods>/probe/spool-1234.jsonl
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.harness import ModClient  # noqa: E402
from driver.harness.spool import read_spool  # noqa: E402


def _runs(ticks: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    current: list[int] = []
    for tick in ticks:
        # A decrease means a new recording run; a forward gap stays in the
        # same run so the contiguity check can flag lost ticks.
        if current and tick < current[-1]:
            runs.append(current)
            current = []
        current.append(tick)
    if current:
        runs.append(current)
    return runs


def verify(path: Path) -> dict[str, Any]:
    rows, stats = read_spool(path)
    ticks = [int(row["tick"]) for row in rows if row.get("event") == "tick"]
    runs = _runs(ticks)
    contiguous = all(run == list(range(run[0], run[0] + len(run))) for run in runs)
    last_event = next(
        (row.get("event") for row in reversed(rows) if row.get("event") != "tick"),
        None,
    )
    result = {
        "path": str(path),
        "rows": len(rows),
        "tick_rows": len(ticks),
        "runs": len(runs),
        "last_run": len(runs[-1]) if runs else 0,
        "first_tick": runs[-1][0] if runs else None,
        "last_tick": runs[-1][-1] if runs else None,
        "contiguous": contiguous,
        "monotonic": contiguous,
        "last_event": last_event,
        "partial_lines": getattr(stats, "partial", 0),
    }
    print(result)
    return result


def run_panic(client: ModClient, ticks: int) -> dict[str, Any]:
    spool = Path(str(client.hello.get("spool", "")))
    client.start(ticks)
    while True:
        if client.readline(10.0).get("event") == "stopped":
            break
    client.panic("all")
    time.sleep(0.2)
    result = verify(spool)
    ok = (
        bool(result["contiguous"])
        and result["last_event"] == "panic_done"
        and int(result["last_run"] or 0) >= ticks - 2
    )
    # The exit code must reflect the full judgement, not just contiguity.
    result["ok"] = ok
    print("panic closure:", "ok" if ok else "FAILED")
    return result


def run_taskkill(client: ModClient, ticks: int) -> dict[str, Any]:
    spool = Path(str(client.hello.get("spool", "")))
    client.start(ticks)
    client.wait_ticks(ticks, timeout=30.0)
    before = verify(spool)
    subprocess.run(
        ["taskkill", "/F", "/IM", "Minecraft.Windows.exe"],
        capture_output=True,
        check=False,
    )
    time.sleep(0.5)
    after = verify(spool)
    lost = 0
    if before["last_tick"] is not None and after["last_tick"] is not None:
        lost = int(before["last_tick"]) - int(after["last_tick"])
    ok = bool(after["contiguous"]) and lost <= 8
    after["ok"] = ok
    print(f"taskkill closure: {'ok' if ok else 'FAILED'} (lost {lost} ticks, <=100 ms)")
    return after


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["panic", "taskkill", "leave_world", "verify"])
    parser.add_argument("--ticks", type=int, default=100)
    parser.add_argument("--spool", type=Path)
    args = parser.parse_args(argv)

    if args.mode == "verify":
        if args.spool is None:
            parser.error("verify requires --spool")
        result = verify(args.spool)
        return 0 if result["contiguous"] else 1
    if args.mode == "leave_world":
        print(
            "leave_world 需要暂停菜单的桌面点击（注入按键无法关闭 pause_screen）。\n"
            "手动流程：record_start -> 走几秒 -> Save & Quit -> taskkill /F，"
            "然后用 verify --spool <文件> 检查。"
        )
        return 3

    client = ModClient(connect_timeout=30.0, timeout=20.0).connect()
    try:
        result = (
            run_panic(client, args.ticks)
            if args.mode == "panic"
            else run_taskkill(client, args.ticks)
        )
    finally:
        client.close()
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
