"""Static-model self-check plus live cross-check for the touch/D-pad/mouse ``/W`` findings (1.26.51.01).

Without arguments the script orchestrates the whole experiment::

    py -3.11 verify_touch_joystick_dpad.py            # needs LEVIPILOT_VERSION_DIR in the environment

1. ``static_model`` re-evaluates the formulas read from the disassembly (joystick transfer,
   classic D-pad cells, glyph-button press rule, Y-axis inversion factor) and asserts the
   properties the report states. It checks the *write-up*, not the game.
2. Two live sessions against ``dev.py run --exec`` (world entered, window focused):

   * ``invert_off``: ``ctrl_invertmouse_mouse:0`` (stock value)
   * ``invert_on``:  ``ctrl_invertmouse_mouse:1`` (option file edited, game restarted)

   ``options.txt`` is restored byte-for-byte afterwards.

Each live session hooks ``MouseMapper::$tick`` (``0x140362490``, integer arguments only) to read
``mScreenWidth``/``mYAxisInversionFactor`` and the look handler's slot 15 to read the event it
consumes, then injects one ``mouse_event``. Raw rows go to
``out/touch-joystick-dpad-1.26.51.01.json`` (written before the assertions run).
"""

from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "driver").is_dir())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "out" / "touch-joystick-dpad-1.26.51.01.json"
IMAGE_BASE = 0x140000000
VA_MAPPER_TICK = 0x140362490  # MouseMapper::$tick (vtable 0x14E759000 slot 5)
VA_SLOT15 = 0x1404B91F0  # look handler vtable 0x14E760FA0 slot 15
VA_ADAPTER = 0x140889540  # adapter feeding slot 15 (rcx = ctx, rdx = handler)
VA_APPLY = 0x144761610  # LocalPlayer::turn
WORLD = "超平坦测试"
OPTION_KEY = "ctrl_invertmouse_mouse"
SESSIONS = (("invert_off", "0", -1), ("invert_on", "1", 1))
SPAWN = (244.5, 240.5)
GROUND = -60.0
DEADZONE = 25.0
DASH_INSET = 1.01 / 22.0
RESULTS: dict[str, Any] = {}

Rect = tuple[float, float, float, float]  # RectangleArea {_x0, _x1, _y0, _y1}


# --------------------------------------------------------------------------- static model


def joystick_magnitude(dist: float, radius: float) -> float:
    """0x140709000..0x140709075: dead zone 25, then linear up to the radius, clamped there."""

    if dist <= DEADZONE or radius <= DEADZONE:
        return 0.0
    return (min(dist, radius) - DEADZONE) / (radius - DEADZONE)


def joystick_outside_max(dist: float, radius: float) -> bool:
    """0x140709079..0x140709088: ``dist > 1.05 * radius``."""

    return dist > 1.05 * radius


def dpad_cells(box: Rect) -> dict[str, Rect]:
    """The eight direction cells of the classic D-pad.

    ``0x14434E660`` yields the square ``box``; every lambda below uses ``S = (x1 - x0) / 3``:
    corners are whole cells (``0x14436E490/E570/E650/E730``), the side cells are the middle
    cells inset by ``S / 22 * 1.01`` towards the centre (``0x14436D110/D230/D360/D580``).
    """

    x0, x1, y0, y1 = box
    side = x1 - x0
    cell = side / 3.0
    inset = cell * DASH_INSET
    mid_x = x0 + cell * 1.5
    mid_y = y0 + cell * 1.5
    return {
        "up_left": (x0, x0 + cell, y0, y0 + cell),
        "up_right": (x1 - cell, x1, y0, y0 + cell),
        "down_left": (x0, x0 + cell, y1 - cell, y1),
        "down_right": (x1 - cell, x1, y1 - cell, y1),
        "left": (x0 + inset, x0 + cell + inset, mid_y - cell / 2, mid_y + cell / 2),
        "right": (x1 - cell - inset, x1 - inset, mid_y - cell / 2, mid_y + cell / 2),
        "up": (mid_x - cell / 2, mid_x + cell / 2, y0 + inset, y0 + cell + inset),
        "down": (mid_x - cell / 2, mid_x + cell / 2, y1 - cell - inset, y1 - inset),
    }


def overlap_area(a: Rect, b: Rect) -> float:
    width = min(a[1], b[1]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[2], b[2])
    return max(0.0, width) * max(0.0, height)


def glyph_press(inside: bool, fresh: bool, was_pressed: bool, state_ok: bool) -> bool:
    """0x1406ECCE0: promiscuous D-pad buttons press when inside and the point is fresh,
    or when they were already pressed (``byte20 == 1 || (mPromiscuous && wasPressed)``)."""

    return inside and state_ok and (fresh or was_pressed)


def inversion_factor(invert: bool) -> int:
    """0x140362270..0x14036227C: ``(invertYAxis - 1) | 1``."""

    return (int(invert) - 1) | 1


def static_model() -> dict[str, Any]:
    radius = 306.0 / 3.0  # (moveArea.x1 - moveArea.x0) / 3
    mags = {d: joystick_magnitude(d, radius) for d in (0.0, 10.0, 25.0, 25.5, 63.5, 102.0, 140.0)}
    assert mags[0.0] == 0.0 and mags[10.0] == 0.0 and mags[25.0] == 0.0, mags
    assert abs(mags[25.5] - 0.5 / 77.0) < 1e-12, mags
    assert abs(mags[63.5] - 0.5) < 1e-12 and mags[102.0] == 1.0 and mags[140.0] == 1.0, mags
    assert not joystick_outside_max(102.0, radius) and joystick_outside_max(107.2, radius)
    assert joystick_outside_max(102.0 * 1.05 + 1e-3, radius)

    cells = dpad_cells((0.0, 306.0, 0.0, 306.0))
    cell = 102.0
    inset = cell * DASH_INSET
    assert cells["up_left"] == (0.0, cell, 0.0, cell) and cells["down_right"] == (
        204.0,
        306.0,
        204.0,
        306.0,
    )
    assert abs(cells["left"][0] - inset) < 1e-9 and abs(cells["left"][2] - cell) < 1e-9
    assert abs(cells["up"][3] - (cell + inset)) < 1e-9
    corners = ("up_left", "up_right", "down_left", "down_right")
    sides = ("left", "right", "up", "down")
    zero = [(a, b) for a in corners for b in sides if overlap_area(cells[a], cells[b]) > 0.0]
    assert not zero, zero  # corners and side cells share edges only
    assert all(overlap_area(cells[a], cells[b]) == 0.0 for a in corners for b in corners if a != b)
    # Every adjacent pair of side cells overlaps in one ``inset * inset`` square at the
    # junction where the two insets meet; opposite side cells stay disjoint.
    adjacent = (("up", "left"), ("up", "right"), ("down", "left"), ("down", "right"))
    junction = {f"{a}/{b}": overlap_area(cells[a], cells[b]) for a, b in adjacent}
    assert all(abs(area - inset * inset) < 1e-9 for area in junction.values()), junction
    assert overlap_area(cells["up"], cells["down"]) == 0.0
    assert overlap_area(cells["left"], cells["right"]) == 0.0

    assert inversion_factor(False) == -1 and inversion_factor(True) == 1
    assert glyph_press(True, True, False, True)
    assert not glyph_press(True, False, False, True)  # consumed by an earlier control
    assert glyph_press(True, False, True, True)  # promiscuous: keeps an existing press
    assert not glyph_press(False, True, True, True) and not glyph_press(True, True, True, False)
    return {
        "joystick_radius_for_306_wide_area": radius,
        "joystick_magnitude_samples": mags,
        "sprint_zone_radius_for_306_wide_area": 1.05 * radius,
        "dpad_cells_for_306_square": {k: list(v) for k, v in cells.items()},
        "dpad_side_inset": inset,
        "dpad_side_cell_junction_overlaps": junction,
        "inversion_factor_off_on": [inversion_factor(False), inversion_factor(True)],
    }


# --------------------------------------------------------------------------- live helpers


def predicted_k(sensitivity: float) -> tuple[float, float]:
    """``v = 0.81*(1.1*S)^0.6125`` and ``k = 9600*(0.15+0.6v)^3`` (no 0.3 yet)."""

    def r(value: float) -> float:
        return struct.unpack("<f", struct.pack("<f", value))[0]

    scaled = r(r(1.1) * r(sensitivity))
    curved = r(scaled ** r(0.6125))
    v = r(r(0.81) * curved)
    inner = r(r(r(0.6) * v) + r(0.15))
    return v, r(r(9600.0) * r(inner * inner * inner))


def _game_pids() -> set[int]:
    listing = [
        ["wmic", "process", "where", "name='Minecraft.Windows.exe'", "get", "ProcessId"],
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "(Get-Process Minecraft.Windows -ErrorAction SilentlyContinue).Id",
        ],
    ]
    for cmd in listing:
        try:
            out = subprocess.run(
                cmd, capture_output=True, text=True, errors="ignore", timeout=20
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        pids = {int(value) for value in re.findall(r"\d+", out)}
        if pids:
            return pids
    return set()


def _window() -> dict[str, int]:
    """Physical client size of the game window (restores a minimized window first)."""

    import ctypes
    import ctypes.wintypes as wintypes
    import time

    user32 = ctypes.windll.user32
    with __import__("contextlib").suppress(Exception):
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    pids = _game_pids()
    found: dict[str, int] = {}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _visit(hwnd: int, _param: int) -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            cls = ctypes.create_unicode_buffer(100)
            user32.GetClassNameW(hwnd, cls, 100)
            if cls.value == "Bedrock" and user32.IsWindowVisible(hwnd):
                found["hwnd"] = int(hwnd)
        return True

    user32.EnumWindows(_visit, 0)
    hwnd = found.get("hwnd")
    if not hwnd:
        return {}
    with __import__("contextlib").suppress(Exception):
        user32.ShowWindow(hwnd, 9)
        time.sleep(0.4)
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    return {"width": int(rect.right), "height": int(rect.bottom)}


def _install(client: Any, base: int, tag: str, va: int, **mem: Any) -> dict[str, Any]:
    message: dict[str, Any] = {"va": hex(va - IMAGE_BASE + base), "tag": tag}
    if mem:
        message.update(mem)
    return client.hook_install(**message)


def _f32(hit: dict[str, Any]) -> list[float]:
    return list((hit.get("mem") or {}).get("f32") or [])


def _i32_at(hit: dict[str, Any], byte_offset: int) -> int | None:
    """Little-endian int32 at ``byte_offset`` of the snapshot (the ``f32`` view turns
    pointer bytes into NaN, which JSON carries as ``null``; decode the raw hex instead)."""

    raw = (hit.get("mem") or {}).get("hex") or ""
    try:
        return struct.unpack_from("<i", bytes.fromhex(raw), byte_offset)[0]
    except (ValueError, struct.error):
        return None


def _u64(hex_text: str) -> int:
    return int.from_bytes(bytes.fromhex(hex_text[:16]), "little")


def _read_float(client: Any, addr: int) -> float | None:
    reply = client.mem_read(addr, 4)
    if not reply.get("ok", True) or not reply.get("hex") or reply.get("bad_addr"):
        return None
    return struct.unpack("<f", bytes.fromhex(reply["hex"]))[0]


def _turn(client: Any, dx: int, dy: int) -> dict[str, float]:
    client.look(0.0, 0.0)
    client.wait_ticks(3)
    before = client.state()
    yaw0, pitch0 = float(before["yaw"]), float(before["pitch"])
    client.mouse_event("move", dx=dx, dy=dy)
    client.wait_ticks(4)
    after = client.state()
    return {
        "d_yaw": ((float(after["yaw"]) - yaw0 + 180.0) % 360.0) - 180.0,
        "d_pitch": float(after["pitch"]) - pitch0,
    }


def _alpha_chase(client: Any, adapter_hits: list[dict[str, Any]]) -> dict[str, Any]:
    """``c`` of slot 15's touch branch is the adapter's ``*(*(ctx + 0) + 0x1268) + 8`` float."""

    out: dict[str, Any] = {"adapter_hits": len(adapter_hits)}
    pointers = {_u64(h["mem"]["hex"]) for h in adapter_hits if (h.get("mem") or {}).get("ok")}
    out["ctx_object"] = [hex(p) for p in sorted(pointers)]
    if len(pointers) != 1:
        out["error"] = "ctx pointer not unique"
        return out
    obj = pointers.pop()
    reply = client.mem_read(obj + 0x1268, 8)
    if not reply.get("ok", True) or not reply.get("hex") or len(reply["hex"]) < 16:
        out["error"] = f"mem_read(obj+0x1268) failed: {reply}"
        return out
    holder = _u64(reply["hex"])
    out["holder"] = hex(holder)
    samples = []
    for _ in range(8):
        value = _read_float(client, holder + 8)
        samples.append(None if value is None else round(value, 6))
        client.wait_ticks(2)
    out["alpha_samples"] = samples
    out["alpha_neighbourhood"] = [
        _read_float(client, holder + offset) for offset in (0, 4, 8, 12, 16, 20)
    ]
    return out


def stage_live(client: Any, base: int, label: str, expected_factor: int) -> dict[str, Any]:
    span = 0x58
    idx_event = 0
    idx_accum = (0x84 - 0x34) // 4
    hooks = {
        "mapper_tick": _install(
            client, base, "mapper_tick", VA_MAPPER_TICK, mem_arg=0, mem_offset=8, mem_size=80
        ),
        "slot15": _install(
            client, base, "slot15", VA_SLOT15, mem_arg=0, mem_offset=0x34, mem_size=span
        ),
        "adapter": _install(
            client, base, "adapter", VA_ADAPTER, mem_arg=0, mem_offset=0, mem_size=8
        ),
        "apply": _install(client, base, "apply", VA_APPLY, mem_arg=1, mem_offset=0, mem_size=8),
    }
    client.wait_ticks(3)
    client.hook_drain(since=0, limit=65536)
    window = _window()
    head = client.hook_list().get("head")
    dx, dy = 200, 100
    turn = _turn(client, dx, dy)
    drain = client.hook_drain(since=head, limit=65536)
    for hook in hooks.values():
        client.hook_rm(int(hook["id"]))
    hits = drain.get("hits", [])
    by_tag: dict[str, list[dict[str, Any]]] = {}
    for hit in hits:
        by_tag.setdefault(str(hit.get("tag")), []).append(hit)

    ticks = [h for h in by_tag.get("mapper_tick", []) if (h.get("mem") or {}).get("ok")]
    # Snapshot starts at this+0x08: mScreenWidth @+0x08, mScreenHeight @+0x0C,
    # mYAxisInversionFactor @+0x50.
    widths = sorted({v for v in (_i32_at(h, 0) for h in ticks) if v is not None})
    heights = sorted({v for v in (_i32_at(h, 4) for h in ticks) if v is not None})
    factors = sorted({v for v in (_i32_at(h, 0x50 - 8) for h in ticks) if v is not None})

    slot15 = by_tag.get("slot15", [])
    consuming = [h for h in slot15 if any(_f32(h)[idx_event : idx_event + 2])]
    accumulated = [h for h in slot15 if any(_f32(h)[idx_accum : idx_accum + 2])]
    apply = [h for h in by_tag.get("apply", []) if any(_f32(h))]
    order = [str(h.get("tag")) for h in hits[:12]]

    # Degrees per pixel on both axes (factor 1.0 on the mouse path: key == InputMode::Mouse).
    turn_x = _turn(client, 100, 0)
    turn_y = _turn(client, 0, 100)
    per_px_x = (abs(turn["d_yaw"]) / dx + abs(turn_x["d_yaw"]) / 100.0) / 2
    per_px_y = (abs(turn["d_pitch"]) / dy + abs(turn_y["d_pitch"]) / 100.0) / 2

    try:
        alpha = _alpha_chase(client, by_tag.get("adapter", []))
    except Exception as exc:  # the alpha identity is informational, never fatal
        alpha = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "window": window,
        "mapper_tick_hits": len(ticks),
        "mapper_screen_width": widths,
        "mapper_screen_height": heights,
        "mapper_y_axis_inversion_factor": factors,
        "event_dx_over_w": _f32(consuming[0])[idx_event + 1] if consuming else None,
        "event_dy_raw_over_w": _f32(consuming[0])[idx_event] if consuming else None,
        "slot15_consumed": [
            [round(v, 6) for v in _f32(h)[idx_event : idx_event + 2]] for h in consuming[:4]
        ],
        "slot15_accum": [
            [round(v, 6) for v in _f32(h)[idx_accum : idx_accum + 2]] for h in accumulated[:4]
        ],
        "apply_args": [[round(v, 6) for v in _f32(h)] for h in apply[:4]],
        "hit_order_head": order,
        "d_yaw": turn["d_yaw"],
        "d_pitch": turn["d_pitch"],
        "pitch_sign_for_dy_+100": turn_y["d_pitch"],
        "deg_per_px_x": per_px_x,
        "deg_per_px_y": per_px_y,
        "ratio_x_over_y": per_px_x / per_px_y if per_px_y else None,
        "alpha": alpha,
        "lost": drain.get("lost", 0),
        "expected_factor": expected_factor,
        "k_predicted": predicted_k(0.5)[1],
    }


def verify_live(label: str, row: dict[str, Any]) -> None:
    width = float((row.get("window") or {}).get("width") or 0)
    assert width > 0, ("window not measured", row.get("window"))
    assert row["mapper_tick_hits"] >= 1, row["mapper_tick_hits"]
    # The producer's divisor is its own mScreenWidth, and that is the physical client width.
    assert row["mapper_screen_width"] == [int(width)], (row["mapper_screen_width"], width)
    assert row["mapper_screen_height"] == [int(row["window"]["height"])], row[
        "mapper_screen_height"
    ]
    factor = row["expected_factor"]
    assert row["mapper_y_axis_inversion_factor"] == [factor], row["mapper_y_axis_inversion_factor"]
    event_x, event_y = row["event_dx_over_w"], row["event_dy_raw_over_w"]
    assert event_x is not None and event_y is not None, row["slot15_consumed"]
    assert abs(event_x - 200.0 / width) <= 2e-6, (event_x, 200.0 / width)
    assert abs(event_y - 100.0 * factor / width) <= 2e-6, (event_y, 100.0 * factor / width)
    k = row["k_predicted"]
    got = max(row["slot15_accum"], key=lambda v: abs(v[0]) + abs(v[1]))
    assert abs(got[1] - k * event_x) <= 2e-3 * max(1.0, abs(k * event_x)), (got, k * event_x)
    assert abs(got[0] - k * event_y) <= 2e-3 * max(1.0, abs(k * event_y)), (got, k * event_y)
    apply = max(row["apply_args"], key=lambda v: abs(v[0]) + abs(v[1]))
    assert abs(apply[1] - k * event_x * 0.3) <= 2e-3 * max(1.0, abs(apply[1]))
    assert abs(apply[0] - k * event_y * 0.3) <= 2e-3 * max(1.0, abs(apply[0]))
    assert abs(row["d_yaw"] - apply[1]) <= 5e-3, (row["d_yaw"], apply)
    assert abs(row["d_pitch"] + apply[0]) <= 5e-3, (row["d_pitch"], apply)
    assert abs(row["ratio_x_over_y"] - 1.0) <= 5e-3, row["ratio_x_over_y"]
    assert row["lost"] == 0, row["lost"]


def verify() -> None:
    dump()  # pick up the rows the two live sessions saved
    off, on = RESULTS["live"]["invert_off"], RESULTS["live"]["invert_on"]
    verify_live("invert_off", off)
    verify_live("invert_on", on)
    # Inverted Y flips the pitch sign of a downward mouse move and nothing else.
    assert off["d_pitch"] > 0 and on["d_pitch"] < 0, (off["d_pitch"], on["d_pitch"])
    assert abs(off["d_yaw"] - on["d_yaw"]) <= 5e-3 * max(1.0, abs(off["d_yaw"]))
    assert abs(off["event_dy_raw_over_w"] + on["event_dy_raw_over_w"]) <= 2e-6


# --------------------------------------------------------------------------- orchestration


def options_path() -> Path:
    version_dir = os.environ.get("LEVIPILOT_VERSION_DIR")
    if not version_dir:
        raise SystemExit("set LEVIPILOT_VERSION_DIR to the game instance directory")
    hits = sorted(
        Path(version_dir).glob("Minecraft Bedrock/Users/*/games/com.mojang/minecraftpe/options.txt")
    )
    if len(hits) != 1:
        raise SystemExit(f"expected exactly one options.txt under the instance, found {len(hits)}")
    return hits[0]


def with_option(original: bytes, value: str) -> bytes:
    pattern = rb"(" + OPTION_KEY.encode() + rb"):[^\r\n]*"
    if not re.search(pattern, original):
        raise SystemExit(f"{OPTION_KEY} is not present in options.txt; refusing to add it")
    return re.sub(pattern, lambda m: m.group(1) + b":" + value.encode(), original, count=1)


def run_session(label: str) -> int:
    script = Path(__file__).resolve()
    exec_cmd = f'"{sys.executable}" "{script}" --stage=live --label={label}'
    cmd = [
        sys.executable,
        str(ROOT / "driver" / "dev.py"),
        "run",
        "--world",
        WORLD,
        "--no-build",
        "--exec",
        exec_cmd,
        "--stop-after",
    ]
    print(f"[session {label}] dev.py run --exec ...", flush=True)
    env = dict(os.environ, TJD_PROBE_CHILD="1")
    done = subprocess.run(cmd, cwd=str(ROOT), check=False, env=env)
    print(f"[session {label}] exit {done.returncode}", flush=True)
    return done.returncode


def _arg(args: list[str], name: str) -> str | None:
    """``--name=value`` or ``--name value``."""

    for index, item in enumerate(args):
        if item == f"--{name}" and index + 1 < len(args):
            return args[index + 1]
        if item.startswith(f"--{name}="):
            return item.split("=", 1)[1]
    return None


def dump() -> None:
    """Merge ``RESULTS`` into the file on disk.

    The live sessions and the orchestrator are different processes writing the same
    file: a wholesale rewrite from one process would drop the rows the others saved.
    """

    OUT.parent.mkdir(parents=True, exist_ok=True)
    merged: dict[str, Any] = {}
    if OUT.exists():
        with __import__("contextlib").suppress(OSError, json.JSONDecodeError):
            merged = json.loads(OUT.read_text(encoding="utf-8"))
    for key, value in RESULTS.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    OUT.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    RESULTS.clear()
    RESULTS.update(merged)


def main() -> None:
    args = sys.argv[1:]
    stage = _arg(args, "stage") or "all"
    label = _arg(args, "label") or ""
    if stage not in ("all", "live"):
        raise SystemExit(f"unknown --stage {stage!r}")
    if stage == "live" and label not in {name for name, _value, _factor in SESSIONS}:
        raise SystemExit("--stage live needs --label invert_off|invert_on")
    if OUT.exists():
        RESULTS.update(json.loads(OUT.read_text(encoding="utf-8")))

    if stage == "live":
        expected = {name: factor for name, _value, factor in SESSIONS}[label]
        from driver.harness.client import ModClient
        from driver.harness.lab import LabSession, settle

        with ModClient() as client:
            client.wait_in_world(90.0)
            base = int(client.diag()["module_base"])
            with LabSession(client, lock_input=True):
                settle(client, ticks=3, on_ground=True, still=5e-3, input_sprint=False)
                client.teleport(SPAWN[0], GROUND, SPAWN[1], yaw=0.0, pitch=0.0)
                client.wait_ticks(6)
                settle(client, ticks=3, on_ground=True, still=5e-3, input_sprint=False)
                RESULTS.setdefault("live", {})[label] = {}
                try:
                    RESULTS["live"][label] = stage_live(client, base, label, expected)
                    RESULTS.setdefault("meta", {})["module_base_rel"] = hex(base - IMAGE_BASE)
                finally:
                    dump()
        verify_live(label, RESULTS["live"][label])
        print(f"live {label}: ok")
        return

    # Recursion guard: the exec child of ``dev.py run`` must never orchestrate again
    # (a stage parsed as "all" inside a session would relaunch the game in a loop).
    if os.environ.get("TJD_PROBE_CHILD"):
        raise SystemExit("refusing to orchestrate from inside a dev.py --exec child")
    RESULTS["static_model"] = static_model()
    path = options_path()
    original = path.read_bytes()
    current = re.search(rb"" + OPTION_KEY.encode() + rb":([^\r\n]*)", original)
    RESULTS["options"] = {
        "key": OPTION_KEY,
        "original": (current.group(1).decode() if current else None),
    }
    codes: dict[str, int] = {}
    try:
        for name, value, _factor in SESSIONS:
            path.write_bytes(with_option(original, value))
            codes[name] = run_session(name)
    finally:
        path.write_bytes(original)
        restored = re.search(rb"" + OPTION_KEY.encode() + rb":([^\r\n]*)", path.read_bytes())
        RESULTS["options"]["restored"] = restored.group(1).decode() if restored else None
        RESULTS["session_exit_codes"] = codes
        dump()
    verify()
    print(
        "summary: "
        + json.dumps(
            {
                name: {
                    "W": row["mapper_screen_width"],
                    "factor": row["mapper_y_axis_inversion_factor"],
                    "event": row["slot15_consumed"][:1],
                    "d_yaw": round(row["d_yaw"], 6),
                    "d_pitch": round(row["d_pitch"], 6),
                    "alpha": row["alpha"].get("alpha_samples"),
                }
                for name, row in RESULTS["live"].items()
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
