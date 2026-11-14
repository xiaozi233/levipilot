"""Locate the per-tick writers of StateVector.mPosDelta on the client player.

Read-only: two write watchpoints (x/z and y of `mPosDelta`), a short walk and
three jumps with normal HID input, then every reported RIP is mapped back to a
static VA. No memory writes, no scene changes, no velocity injection.

Important: `watch_drain` must be called with `since=<ring head at install>`.
The ring keeps thousands of rows from earlier sessions, so the default `since`
returns the oldest page and silently filters the new hits away.

    py -3.11 out/locate_delta_writers.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "driver"))

from driver.harness.facade import Harness  # noqa: E402
import pe_map  # noqa: E402

STATE_VECTOR = 459100728  # 0x1B5D5238
IMAGE_BASE = 0x140000000


def main() -> int:
    image = pe_map.PeImage(Path(pe_map.default_exe()))
    with Harness() as h:
        diag = h.client.request({"cmd": "diag"}, "diag")
        base = int(diag["module_base"])
        st = h.state()
        sv = int(h.client.ecs_context(int(st["context"]), STATE_VECTOR)["address"])
        head = int(h.client.watch_list()["head"])
        print("module_base", hex(base), "sv", hex(sv), "head", head, "feet", st["feet_y"])

        watches = {
            "mPosDelta.xz": h.watch_set(
                addr=sv + 0x18, size=8, mode="w", thread="both", depth=3, tag="dxz"
            ),
            "mPosDelta.y": h.watch_set(
                addr=sv + 0x1C, size=4, mode="w", thread="both", depth=3, tag="dy"
            ),
        }
        h.client.set_input(1.0, 0.0, ticks=30)
        h.client.wait_ticks(3)
        for _ in range(3):
            h.client.jump()
            h.client.wait_ticks(12)
        h.client.stop_move()
        h.client.wait_ticks(3)

        for label, watch in watches.items():
            hits = h.watch_drain(since=head, watch_id=int(watch["id"]))["hits"]
            per_site = Counter(int(hit["rip"]) for hit in hits)
            print(f"== {label}: {len(hits)} hits, {len(per_site)} sites")
            for rip, n in per_site.most_common(10):
                va = rip - base + IMAGE_BASE
                found = image.function_at(va)
                print(f"   {hex(rip)} -> static {hex(va)} n={n} "
                      f"func={hex(found[0]) if found else '-'} tid={sorted({hit['tid'] for hit in hits if int(hit['rip']) == rip})}")
        h.watch_clear(all=True)
        print("cleared; tick", h.state()["tick"])
    image.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())