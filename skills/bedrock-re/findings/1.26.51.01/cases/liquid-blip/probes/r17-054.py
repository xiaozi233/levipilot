"""Validate execution watch admission/cleanup against the published entry role."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from driver.harness.facade import Harness
from driver.harness.errors import ModProtocolError


def main():
    roles = json.loads(Path("docs/roles/1.26.51.01.json").read_text(encoding="utf-8"))
    checks = {}
    with Harness() as h:
        assert h.client.status()["watch_count"] == 0
        module = h.client.request({"cmd": "diag"}, "diag")["module_base"]
        address = module + int(roles["roles"]["@liquid_exit_blip"]["address"], 16) - int(roles["image_base"], 16)
        base = {"size": 1, "mode": "x", "sample": True}
        for name, overrides, reason in (
            ("size", {"size": 4}, "execute_size_one"),
            ("snapshot_count", {"snapshots": [{"reg": "r8", "size": 4}] * 4}, "bad_snapshots"),
            ("register", {"snapshots": [{"reg": "rax", "size": 4}]}, "bad_snapshots"),
            ("too_large", {"snapshots": [{"reg": "r8", "size": 65}]}, "bad_snapshots"),
            ("negative", {"snapshots": [{"reg": "r8", "size": -1}]}, "bad_snapshots"),
            ("sample_required", {"sample": False, "snapshots": [{"reg": "r8", "size": 4}]}, "bad_snapshots"),
        ):
            options = {**base, **overrides}
            try:
                unexpected = h.client.watch_set(address, **options)
            except ModProtocolError as exc:
                checks[name] = reason in str(exc)
            else:
                h.client.watch_clear(unexpected["id"])
                checks[name] = False
        watch = h.client.watch_set(address, size=1, mode="x", sample=True, thread="both", snapshots=[{"reg": "rdx", "size": 32}, {"reg": "r8", "size": 40}, {"reg": "r9", "size": 36}])
        try:
            checks["mode"] = watch["mode"] == "x"
            checks["snapshots"] = len(watch["snapshots"]) == 3
            before = h.state()["tick"]
            h.client.wait_ticks(4)
            checks["tick_progress"] = h.state()["tick"] >= before + 4
        finally:
            checks["clear"] = h.client.watch_clear(watch["id"]).get("ok", False)
        checks["no_watches"] = h.client.status()["watch_count"] == 0
    print(json.dumps({"passed": sum(checks.values()), "total": len(checks), "checks": checks}))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
