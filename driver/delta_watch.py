"""Passively capture per-axis mPosDelta writes; never move or teleport the player.

    py -3.11 -m driver.delta_watch --ticks 80 --output out/delta-writes.jsonl

Move manually during the window if desired. Requires a live, compatible world
and three free DR slots. Uses the running profile, not hard-coded component IDs
or field offsets. Does not decode instructions or infer system/pass order.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, TextIO

from .harness.facade import Harness


def _number(value: int | str) -> int:
    return int(value, 0) if isinstance(value, str) else int(value)


def capture(h: Harness, output: TextIO, *, ticks: int = 80) -> dict[str, Any]:
    """Persist raw pages, release only owned watches, and preserve the first error.

    No inputs, tasks, pose or world fixtures are changed. A context change aborts
    collection rather than continuing to observe an old component address.
    """
    if not 1 <= ticks <= 1200:
        raise ValueError("ticks must be 1..1200")
    owned: dict[str, int] = {}
    counts: Counter[str] = Counter()
    summary: dict[str, Any] = {"complete": False, "lost": 0, "cleanup_errors": []}
    primary: BaseException | None = None
    cursor = 0

    def emit(kind: str, **values: Any) -> None:
        output.write(json.dumps({"event": kind, **values}, ensure_ascii=False) + "\n")
        output.flush()

    def drain() -> None:
        nonlocal cursor
        target: int | None = None
        for _ in range(64):
            page = h.watch_drain(since=cursor, limit=4096)
            emit("page", reply=page)  # Keep raw rows, including other watches.
            summary["lost"] += int(page["lost"])
            by_id = {ident: axis for axis, ident in owned.items()}
            for hit in page["hits"]:
                axis = by_id.get(hit["watch"])
                if axis is not None:
                    counts[axis] += 1
            next_cursor = int(page["next"])
            if target is None:
                target = int(page["head"])
            if next_cursor < cursor or (next_cursor == cursor and cursor < target):
                raise RuntimeError("watch drain did not advance; capture incomplete")
            cursor = next_cursor
            if cursor >= target:
                return
        raise RuntimeError("watch drain page limit reached; capture incomplete")

    try:
        status = h.client.status()
        if status.get("version_ok") is not True or status.get("compat") is not False:
            raise RuntimeError("a compatible running profile is required")
        profile = h.profile_dump()["profile"]
        state = h.state()
        context = _number(state["context"])
        if not context:
            raise RuntimeError("a client player context is required")
        component = h.client.ecs_context(context, int(profile["components"]["StateVector"]["id"]))
        address = _number(component["address"])
        fields = profile["structs"]["StateVector"]
        # Vel* are the profile's compatibility keys; the fields are mPosDelta.
        offsets = {axis: _number(fields[f"Vel{axis.upper()}"]) for axis in "xyz"}
        if address <= 0 or any((address + value) % 4 for value in offsets.values()):
            raise RuntimeError("invalid/alignment-unsafe StateVector address")
        if len(set(offsets.values())) != 3:
            raise RuntimeError("profile must provide three distinct per-axis fields")
        listing = h.client.watch_list()
        if len(listing["watches"]) > 1:
            raise RuntimeError("three free DR slots are required; existing watches kept")
        cursor = int(listing["head"])  # Before install: retain earliest hits too.
        emit("begin", state=state, component=component, profile=profile, since=cursor)
        for axis, offset in offsets.items():
            reply = h.watch_set(
                address + offset, size=4, mode="w", thread="both", depth=3, tag=f"delta-{axis}"
            )
            owned[axis] = int(reply["id"])
            emit("installed", axis=axis, reply=reply)
        remaining = ticks
        while remaining:
            step = min(5, remaining)
            h.client.wait_ticks(step)
            current = h.state()
            emit("state", reply=current)
            if _number(current["context"]) != context:
                raise RuntimeError("client context changed; capture incomplete")
            drain()
            remaining -= step
        summary["complete"] = summary["lost"] == 0
    except BaseException as exc:
        primary = exc
        summary["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        for axis, ident in owned.items():
            try:
                h.watch_clear(ident)
            except Exception as exc:
                summary["cleanup_errors"].append(f"{axis}/{ident}: {exc}")
        summary["counts"] = {axis: counts[axis] for axis in owned}
        summary["owned"] = owned
        summary["next"] = cursor
        if primary is not None or summary["cleanup_errors"]:
            summary["complete"] = False
        try:
            emit("summary", **summary)
        except Exception as exc:
            if primary is None:
                raise
            primary.add_note(f"report finalization failed: {exc}")
        if summary["cleanup_errors"]:
            message = "watch cleanup failed: " + "; ".join(summary["cleanup_errors"])
            if primary is not None:
                primary.add_note(message)
            else:
                raise RuntimeError(message)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticks", type=int, default=80)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        # Refuse to overwrite earlier evidence; close both pipe and file on error.
        with args.output.open("x", encoding="utf-8") as output, Harness() as h:
            summary = capture(h, output, ticks=args.ticks)
        return 0 if summary["complete"] else 1
    except Exception as exc:
        print(f"delta_watch: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
