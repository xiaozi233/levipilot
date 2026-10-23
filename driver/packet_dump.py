"""Capture packet identity + whitelisted bytes into out/packets-<ts>.jsonl.

    py -3.11 driver/packet_dump.py --seconds 5 --walk
    py -3.11 driver/packet_dump.py --seconds 10 --ids 144,77,36
    py -3.11 driver/packet_dump.py --directions send,recv --serialize-ids 144,111

Rows look like::

    {"seq": 12, "tick": 345, "id": 144, "name": "PlayerAuthInput",
     "dir": "send", "size": 92, "hex": "0000...", "fields": {...}}

Identity-only ids (not in --serialize-ids) are written without size/hex.
Exits non-zero when fewer than --min rows are captured.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.harness import ModClient  # noqa: E402
from driver.harness.packets import decode_row  # noqa: E402


def parse_ids(text: str | None) -> list[int] | None:
    if not text:
        return None
    return [int(part) for part in text.split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--ids", help="comma-separated allow-list")
    parser.add_argument(
        "--serialize-ids",
        default="144,77",
        help="ids to serialize (<=128 bytes each); default 144,77",
    )
    parser.add_argument("--sample", type=int, default=1)
    parser.add_argument(
        "--directions",
        default="send",
        help="comma-separated tap directions: send, recv, or send,recv (default send)",
    )
    parser.add_argument("--walk", action="store_true", help="walk forward while capturing")
    parser.add_argument("--min", type=int, default=20)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    out_path = args.out or (ROOT / "out" / f"packets-{datetime.now():%Y%m%d-%H%M%S}.jsonl")
    serialize_ids = parse_ids(args.serialize_ids) or []

    client = ModClient(connect_timeout=30.0, timeout=20.0).connect()
    rows: list[dict[str, Any]] = []
    try:
        client.packet_capture(
            True,
            ids=parse_ids(args.ids),
            sample=args.sample,
            serialize_ids=serialize_ids,
            directions=[part.strip() for part in args.directions.split(",") if part.strip()],
        )
        if args.walk:
            client.set_input(forward=1.0, ticks=-1)
        time.sleep(args.seconds)
        if args.walk:
            client.stop_move()
        time.sleep(0.2)
        identity = {
            int(row["seq"]): row for row in client.packet_drain(0, limit=8192).get("hits", [])
        }
        for row in client.packet_bytes(0, limit=4096).get("hits", []):
            decoded = decode_row(row)
            known = identity.get(int(row["seq"]), {})
            rows.append(
                {
                    "seq": int(row["seq"]),
                    "tick": int(known.get("tick", 0)),
                    "id": int(row["id"]),
                    "name": decoded["name"],
                    "dir": str(row.get("dir", known.get("dir", "send"))),
                    "size": int(row["size"]),
                    "hex": row["hex"],
                    **({"fields": decoded["fields"]} if "fields" in decoded else {}),
                    **(
                        {"decode_error": decoded["decode_error"]}
                        if "decode_error" in decoded
                        else {}
                    ),
                }
            )
        client.packet_capture(False)
    finally:
        client.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"{len(rows)} rows -> {out_path}")
    if len(rows) < args.min:
        print(f"FAILED: fewer than {args.min} rows", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
