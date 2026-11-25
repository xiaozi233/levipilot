"""Scan the player movement pipeline (seeds + 1-2 call hops) for speed clamps.

The previous version only inspected functions whose *own bytes* contained a
movement component id. That misses callees that receive a component by pointer
(the 16 b/t clamp lives in ``MoveCollisionSystem::fetchCollisionShapes``, which
takes ``MoveRequestComponent&`` and contains no component id).

This script:
  1. seeds = functions containing any movement component id immediate;
  2. expands to direct callees 1-2 hops (from capstone ``call 0x...`` operands);
  3. for 16.0 / 256.0 / 0.003 / 0.005 / 3.92 / 0.42 / 0.98 reports every
     .text/.rdata constant xref in that reachable set;
  4. lists every RIP-constant comparison/clamp instruction
     (``ucomiss|comiss|minss|maxss|minps|maxps``) in the reachable set.

Run from the repo root:

    py -3.11 skills/bedrock-re/findings/1.26.51.01/cases/speed-limit-12651/probes/scan_move_pipeline.py
"""

from __future__ import annotations

import re
import struct
import sys
from collections import deque
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "driver").is_dir())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver import pe_map  # noqa: E402

COMPONENTS = {
    0x1B5D5238: "StateVector",
    0x4C8F7288: "MoveRequest",
    0x1268662E: "MovementSpeed",
    0x9B8F9061: "AirSpeed",
    0x10257C61: "LocalMoveVelocity",
    0xB212EF87: "PlayerMovementSettings",
    0x70CA8F01: "ServerPlayerMovement",
    0xAF78DFA4: "ServerPlayerCurrentMovement",
    0x39CF8D64: "MobTravel",
}

CONSTANTS = [16.0, 256.0, 0.003, 0.005, 3.92, 0.42, 0.98]
CALL = re.compile(r"\bcall (0x[0-9a-f]+)\b")
CLAMP = re.compile(r"\b(minss|maxss|minps|maxps|ucomiss|comiss)\b")


def comps_in(blob: bytes) -> list[str]:
    return [name for comp_id, name in COMPONENTS.items() if struct.pack("<I", comp_id) in blob]


def main() -> int:
    with pe_map.PeImage(pe_map.default_exe()) as image:
        seeds: dict[tuple[int, int], set[str]] = {}
        for comp_id, name in COMPONENTS.items():
            for _va, func in image.find_u32(comp_id, section=".text", limit=4000):
                if func:
                    seeds.setdefault(func, set()).add(name)
        print(f"seeds (component-id immediates): {len(seeds)}")

        dist: dict[tuple[int, int], int] = dict.fromkeys(seeds, 0)
        queue = deque((func, 0) for func in seeds)
        while queue:
            func, depth = queue.popleft()
            if depth >= 2:
                continue
            try:
                lines = image.disassemble_function(func[0])
            except Exception:
                continue
            for line in lines:
                match = CALL.search(line)
                if not match:
                    continue
                target = int(match.group(1), 16)
                callee = image.function_at(target)
                if callee and callee not in dist:
                    dist[callee] = depth + 1
                    queue.append((callee, depth + 1))
        print(f"reachable (seeds + <=2 call hops): {len(dist)}")

        print("\n== constant xrefs inside the reachable set")
        for value in CONSTANTS:
            targets = []
            for section in (".text", ".rdata"):
                targets.extend(
                    va for va, _func in image.find_f32(value, section=section, limit=400)
                )
            if not targets:
                continue
            xrefs = image._scan_rip_xrefs(sorted(set(targets)))
            rows: set[tuple[int, int, int]] = set()
            for _target, hits in xrefs.items():
                for xref in hits:
                    func = xref.function
                    if func and func in dist:
                        rows.add((func[0], func[1], dist[func]))
            if rows:
                print(f"-- f32 {value}: {len(rows)} reachable site(s)")
                for begin, end, depth in sorted(rows):
                    label = ",".join(sorted(seeds.get((begin, end), set()))) or f"callee+{depth}"
                    print(f"   0x{begin:x}..0x{end:x}  depth={depth}  {label}")
            sys.stdout.flush()

        print("\n== RIP-constant comparison/clamp instructions in the reachable set")
        for (begin, end), depth in sorted(dist.items()):
            try:
                lines = image.disassemble_function(begin)
            except Exception:
                continue
            hits = [line for line in lines if CLAMP.search(line) and "rip" in line]
            if not hits:
                continue
            label = ",".join(sorted(seeds.get((begin, end), set()))) or f"callee+{depth}"
            print(f"-- 0x{begin:x}..0x{end:x}  depth={depth}  {label}")
            for line in hits[:10]:
                print(f"   {line.strip()}")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
