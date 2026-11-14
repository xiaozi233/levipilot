"""Read-only final integrity check; no movement or game changes."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from driver import dev
from driver.harness.facade import Harness


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    root = Path("out/liquid-blip-dynamic-review")
    meta = json.loads((root / "r14-control/metadata.json").read_text(encoding="utf-8"))
    original = meta["original"]
    with Harness() as h:
        status = h.client.status()
        state = h.state()
        for key in ("name", "uuid", "item"):
            state.pop(key, None)
        watches = h.client.watch_list()
        positions = [(x, y, z) for x in range(349, 360) for y in range(-49, -35) for z in range(298, 309)]
        remaining = []
        for offset in range(0, len(positions), 64):
            rows = h.client.blocks(positions[offset:offset + 64])["blocks"]
            remaining.extend(row for row in rows if row["name"] != "minecraft:air")
        checks = {
            "world": state["in_world"],
            "survival": state["gamemode"] == original["gamemode"] == 0,
            "flight_off": not state["flying"] and not state["mayfly"],
            "effects": state["effects"] == original["effects"] == [],
            "pose": all(abs(state[k] - original[k]) < 0.001 for k in ("feet_x", "feet_y", "feet_z", "yaw", "pitch")),
            "health": state["health"] == original["health"],
            "stationary": all(abs(state[k]) < 1e-6 for k in ("vx", "vy", "vz")),
            "no_watches": not watches["watches"] and status["watch_count"] == 0,
            "no_recording": not any(status[k] for k in ("record_active", "recording", "macro_recording", "macro_playing")),
            "input_unlocked": not state["input"]["locked"],
            "input_released": not any(state["input"][k] for k in ("w", "a", "s", "d", "jump", "sneak", "sprint")),
            "test_pool_removed": not remaining,
        }
        installed_dir = dev.DEFAULT_VERSION / "mods/probe"
        built = digest(dev.MOD_BUILD / "bin/probe/probe.dll")
        installed = digest(installed_dir / "probe.dll")
        checks["dll_matches"] = built == installed
        crash_files = list(installed_dir.glob("crash*"))
        report = {
            "checks": checks,
            "passed": sum(checks.values()),
            "total": len(checks),
            "state": state,
            "original": original,
            "watches": watches,
            "status": {k: v for k, v in status.items() if k.startswith(("record", "macro", "watch", "lifecycle"))},
            "pid": dev.game_pids(dev.DEFAULT_VERSION / "Minecraft.Windows.exe"),
            "built_dll_sha256": built,
            "installed_dll_sha256": installed,
            "current_config_sha256": digest(installed_dir / "harness.json"),
            "configuration_comparison": "No initial hash snapshot; current hash only, no unchanged claim",
            "crash_current_inventory": [p.name for p in crash_files],
            "crash_comparison": "No initial inventory snapshot; current count only, no no-new-crash claim",
            "pool_checked_positions": len(positions),
            "pool_remaining": remaining,
            "natural_hunger_exhaustion": "Not restored or claimed identical",
        }
    (root / "final-integrity.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("checks", "passed", "total", "pid", "built_dll_sha256")}, ensure_ascii=False))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
