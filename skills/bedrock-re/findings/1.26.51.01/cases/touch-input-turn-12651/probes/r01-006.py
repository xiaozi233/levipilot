"""Live verification for the look-input dispatch chain and the touch x-factor (1.26.51.01).

Covers, on 1.26.51.01, through runtime VA hooks (``allow_hook_va=1``):

* S1 the mouse-look chain for one injected ``mouse_event``: slot 15
  (``0x1404B91F0``) scales it by ``k`` into ``[this+0x84/0x88]`` -> ``0x144761610``
  degrees. Slot 15 is snapshotted across ``+0x34..+0x8B`` so one hit shows the
  normalised event being consumed and the accumulator it feeds (slot 27,
  ``0x1404C02B0``, is the writer of ``+0x34/+0x38`` but cannot be hooked: the
  generic detour drops its xmm2/xmm3 arguments);
* S2 slot 14 (``0x1404BF490``) runs on the mouse path with ``edx == r8d``;
* S3 the horizontal factor: ``dx`` vs ``dy`` degrees-per-pixel (``1.0`` by
  default, ``0.7`` when ``ctrl_hotbarOnlyTouch=1``).

Raw rows are written to ``out/touch-input-turn-1.26.51.01.json``.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "driver").is_dir())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.harness.client import ModClient  # noqa: E402
from driver.harness.lab import LabSession, settle  # noqa: E402

OUT = ROOT / "out" / "touch-input-turn-1.26.51.01.json"
IMAGE_BASE = 0x140000000
VA_SLOT15 = 0x1404B91F0
VA_SLOT14 = 0x1404BF490
VA_APPLY = 0x144761610
SPAWN = (244.5, 240.5)
GROUND = -60.0
RESULTS: dict[str, Any] = {}


def predicted_k(sensitivity: float) -> tuple[float, float]:
    """``v = 0.81*(1.1*S)^0.6125`` and ``k = 9600*(0.15+0.6v)^3`` (no 0.3 yet)."""

    def r(value: float) -> float:
        return struct.unpack("<f", struct.pack("<f", value))[0]

    scaled = r(r(1.1) * r(sensitivity))
    curved = r(scaled ** r(0.6125))
    v = r(r(0.81) * curved)
    inner = r(r(r(0.6) * v) + r(0.15))
    return v, r(r(9600.0) * r(inner * inner * inner))


def _window(client: ModClient) -> dict[str, Any] | None:
    """Physical client size of the game window; restores a minimized window first."""
    try:
        import ctypes
        import ctypes.wintypes as wintypes
        import re
        import subprocess
        import time

        user32 = ctypes.windll.user32
        with __import__("contextlib").suppress(Exception):
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        listing = subprocess.run(
            ["wmic", "process", "where", "name='Minecraft.Windows.exe'", "get", "ProcessId"],
            capture_output=True,
            text=True,
            errors="ignore",
        ).stdout
        pids = {int(value) for value in re.findall(r"\d+", listing)}
        found: dict[str, int] = {}

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _visit(hwnd: int, _param: int) -> bool:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                cls = ctypes.create_unicode_buffer(100)
                user32.GetClassNameW(hwnd, cls, 100)
                if cls.value == "Bedrock":
                    found["hwnd"] = int(hwnd)
            return True

        user32.EnumWindows(_visit, 0)
        hwnd = found.get("hwnd")
        if not hwnd:
            return None
        with __import__("contextlib").suppress(Exception):
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.4)
        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        return {"width": int(rect.right), "height": int(rect.bottom)}
    except Exception:
        return None


def _mem_f32(hit: dict[str, Any]) -> list[float]:
    return list((hit.get("mem") or {}).get("f32") or [])


def _install(client: ModClient, base: int, tag: str, va: int, **mem: Any) -> dict[str, Any]:
    message: dict[str, Any] = {"va": hex(va - IMAGE_BASE + base), "tag": tag}
    if mem:
        message.update(mem)
    return client.hook_install(**message)


def stage_s1_chain(client: ModClient, base: int) -> None:
    for hook in client.hook_list().get("hooks", []):
        client.hook_rm(int(hook.get("id")))
    # Only four hook slots exist in the mod, so the chain and slot 14 run in two
    # passes. Slot 15 is snapshotted from +0x34 to +0x8B in one go: that range
    # holds the normalised event it is about to consume (+0x34/+0x38) and the
    # accumulator it has built so far (+0x84/+0x88). Axis layout (measured):
    # +0x34 holds -dy/W (pitch axis, sign already flipped upstream), +0x38 holds
    # dx/W (yaw axis); slot 15 scales them by k into +0x84 / +0x88 and the camera
    # apply receives (0.3*+0x84, 0.3*+0x88) = (-d_pitch, d_yaw) in degrees.
    span = 0x58
    idx_event = 0
    idx_accum = (0x84 - 0x34) // 4
    # slot 27 itself must NOT be hooked: the generic VA detour only preserves
    # rcx/rdx/r8/r9 and slot 27 takes its two floats in xmm2/xmm3, so hooking it
    # zeroes the accumulate (measured: d_yaw 0.0 with that hook installed).
    ids = {
        "slot15": _install(
            client, base, "slot15", VA_SLOT15, mem_arg=0, mem_offset=0x34, mem_size=span
        ),
        "apply": _install(client, base, "apply", VA_APPLY, mem_arg=1, mem_offset=0, mem_size=8),
    }
    client.wait_ticks(3)
    client.hook_drain(since=0, limit=65536)
    window = _window(client) or {}
    client.look(0.0, 0.0)
    client.wait_ticks(3)
    head = client.hook_list().get("head")
    dx, dy = 200, 100
    yaw0, pitch0 = float(client.state()["yaw"]), float(client.state()["pitch"])
    client.mouse_event("move", dx=dx, dy=dy)
    client.wait_ticks(4)
    state = client.state()
    d_yaw = ((float(state["yaw"]) - yaw0 + 180.0) % 360.0) - 180.0
    d_pitch = float(state["pitch"]) - pitch0
    drain = client.hook_drain(since=head, limit=65536)
    hits = drain.get("hits", [])
    for hook in ids.values():
        client.hook_rm(int(hook["id"]))
    by_tag: dict[str, list[dict[str, Any]]] = {}
    for hit in hits:
        by_tag.setdefault(str(hit.get("tag")), []).append(hit)

    consuming = [h for h in by_tag.get("slot15", []) if any(_mem_f32(h)[idx_event : idx_event + 2])]
    apply = [h for h in by_tag.get("apply", []) if any(_mem_f32(h))]

    # Second pass: slot 14 on its own, same input, to read its edx/r8d gate.
    slot14_hook = _install(
        client, base, "slot14", VA_SLOT14, mem_arg=0, mem_offset=0x84, mem_size=8
    )
    client.wait_ticks(2)
    client.hook_drain(since=0, limit=65536)
    head = client.hook_list().get("head")
    client.look(0.0, 0.0)
    client.wait_ticks(2)
    yaw14, _ = float(client.state()["yaw"]), 0.0
    client.mouse_event("move", dx=dx, dy=dy)
    client.wait_ticks(4)
    slot14 = client.hook_drain(since=head, limit=65536).get("hits", [])
    client.hook_rm(int(slot14_hook["id"]))
    d_yaw_slot14_pass = ((float(client.state()["yaw"]) - yaw14 + 180.0) % 360.0) - 180.0

    _, k = predicted_k(0.5)
    RESULTS["s1_chain"] = {
        "window": window,
        "dx_px": dx,
        "dy_px": dy,
        "d_yaw": d_yaw,
        "d_pitch": d_pitch,
        "k_predicted": k,
        "event_dx_over_w": _mem_f32(consuming[0])[idx_event + 1] if consuming else None,
        "event_dy_over_w": -_mem_f32(consuming[0])[idx_event] if consuming else None,
        "slot15_consumed": [
            [round(v, 6) for v in _mem_f32(h)[idx_event : idx_event + 2]] for h in consuming[:4]
        ],
        "slot15_accum": [
            [round(v, 6) for v in _mem_f32(h)[idx_accum : idx_accum + 2]] for h in consuming[:6]
        ],
        "slot14_hits": len(slot14),
        "d_yaw_slot14_pass": d_yaw_slot14_pass,
        "slot14_edx_eq_r8d": all(
            (int(h["args"][1]) & 0xFFFFFFFF) == (int(h["args"][2]) & 0xFFFFFFFF) for h in slot14
        )
        if slot14
        else None,
        "apply_args": [[round(v, 6) for v in _mem_f32(h)] for h in apply[:4]],
        "lost": drain.get("lost", 0),
    }


def stage_s2_factor(client: ModClient, label: str) -> None:
    client.look(0.0, 0.0)
    client.wait_ticks(3)
    rows: list[dict[str, Any]] = []
    for dx, dy in ((100, 0), (0, 100), (200, 0), (0, 200)):
        client.look(0.0, 0.0)
        client.wait_ticks(2)
        yaw0, pitch0 = float(client.state()["yaw"]), float(client.state()["pitch"])
        client.mouse_event("move", dx=dx, dy=dy)
        client.wait_ticks(3)
        state = client.state()
        rows.append(
            {
                "dx": dx,
                "dy": dy,
                "d_yaw": ((float(state["yaw"]) - yaw0 + 180.0) % 360.0) - 180.0,
                "d_pitch": float(state["pitch"]) - pitch0,
            }
        )
    per_px_x = (abs(rows[0]["d_yaw"]) / 100 + abs(rows[2]["d_yaw"]) / 200) / 2
    per_px_y = (abs(rows[1]["d_pitch"]) / 100 + abs(rows[3]["d_pitch"]) / 200) / 2
    _record(
        "s2_xfactor",
        {
            "label": label,
            "window": _window(client),
            "samples": rows,
            "deg_per_px_x": per_px_x,
            "deg_per_px_y": per_px_y,
            "ratio_x_over_y": per_px_x / per_px_y if per_px_y else None,
        },
    )


def _record(stage: str, row: dict[str, Any]) -> None:
    kept = [r for r in RESULTS.get(stage, []) if r.get("label") != row["label"]]
    kept.append(row)
    RESULTS[stage] = kept


def verify() -> None:
    chain = RESULTS["s1_chain"]
    width = float((chain.get("window") or {}).get("width") or 0)
    assert width > 0, ("window not measured", chain.get("window"))
    # The event already carries px / renderWidth: this is where the /W lives.
    assert abs(chain["event_dx_over_w"] - chain["dx_px"] / width) <= 2e-6, (
        chain["event_dx_over_w"],
        chain["dx_px"] / width,
    )
    assert abs(chain["event_dy_over_w"] - chain["dy_px"] / width) <= 2e-6, chain["event_dy_over_w"]
    # Slot 15 scales the accumulated event by k and hands 0.3*k*(px/W) degrees to the camera.
    expected_x = chain["k_predicted"] * chain["event_dx_over_w"]
    expected_y = -chain["k_predicted"] * chain["event_dy_over_w"]
    got = max(chain["slot15_accum"], key=lambda v: abs(v[0]) + abs(v[1]))
    assert abs(got[1] - expected_x) <= 2e-3 * max(1.0, abs(expected_x)), (got, expected_x)
    assert abs(got[0] - expected_y) <= 2e-3 * max(1.0, abs(expected_y)), (got, expected_y)
    apply = max(chain["apply_args"], key=lambda v: abs(v[0]) + abs(v[1]))
    assert abs(apply[1] - expected_x * 0.3) <= 2e-3 * max(1.0, abs(expected_x)), (apply, expected_x)
    assert abs(apply[0] - expected_y * 0.3) <= 2e-3 * max(1.0, abs(expected_y)), (apply, expected_y)
    assert abs(chain["d_yaw"] - apply[1]) <= 5e-3, (chain["d_yaw"], apply)
    assert abs(chain["d_pitch"] + apply[0]) <= 5e-3, (chain["d_pitch"], apply)
    assert chain["slot14_hits"] >= 1 and chain["slot14_edx_eq_r8d"] is True, chain["slot14_hits"]
    # The slot 14 detour must not disturb the turn (it takes no XMM arguments).
    assert abs(chain["d_yaw_slot14_pass"] - chain["d_yaw"]) <= 5e-3, chain["d_yaw_slot14_pass"]
    assert chain["lost"] == 0, chain["lost"]

    for row in RESULTS["s2_xfactor"]:
        want = 0.7 if str(row["label"]).endswith("on") else 1.0
        assert abs(float(row["ratio_x_over_y"]) - want) <= 5e-3, (
            row["label"],
            row["ratio_x_over_y"],
        )


def main() -> None:
    label = next(
        (a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--label=")), "hotbar_off"
    )
    stages = [a for a in sys.argv[1:] if not a.startswith("--")] or ["s1", "s2"]
    if OUT.exists():
        RESULTS.update(json.loads(OUT.read_text(encoding="utf-8")))
    with ModClient() as client:
        client.wait_in_world(90.0)
        diag = client.diag()
        base = int(diag["module_base"])
        with LabSession(client, lock_input=True):
            settle(client, ticks=3, on_ground=True, still=5e-3, input_sprint=False)
            client.teleport(SPAWN[0], GROUND, SPAWN[1], yaw=0.0, pitch=0.0)
            client.wait_ticks(6)
            settle(client, ticks=3, on_ground=True, still=5e-3, input_sprint=False)
            RESULTS["meta"] = {"module_base_rel": hex(base - IMAGE_BASE), "label": label}
            if "s1" in stages:
                stage_s1_chain(client, base)
            if "s2" in stages:
                stage_s2_factor(client, label)
    verify()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(RESULTS, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
