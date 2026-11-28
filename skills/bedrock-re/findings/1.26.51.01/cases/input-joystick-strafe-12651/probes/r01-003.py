"""Live verification for move-input synthesis, analog scaling and look sensitivity.

Covers, on 1.26.51.01:

* S1 keyboard multi-key synthesis: every single/double/triple/quad combination of
  W/A/S/D observed through ``MoveInputComponent`` (``+0x00`` flags,
  ``+0x04``/``+0x08`` analog, ``+0x24``/``+0x28`` ``mMove``) plus the horizontal
  displacement direction;
* S2 analog move vector: sub-unit pass-through versus the ``> 1.0`` unit clamp
  (legacy ``_updateClientMoveVector`` path, ``input_cfg mode=8 force``);
* S3 mouse look: degrees per pixel, the x/y asymmetry, the sensitivity curve,
  and the pitch clamp.

Raw rows are written to ``out/input-joystick-strafe-1.26.51.01.json``.
"""

from __future__ import annotations

import contextlib
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "driver").is_dir())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.harness.client import ModClient  # noqa: E402
from driver.harness.lab import LabSession, record_session, settle  # noqa: E402

OUT = ROOT / "out" / "input-joystick-strafe-1.26.51.01.json"
MOVE_INPUT = 0x018B1887
ITEM_IN_USE = 0x05B90AEF
KEYS = {"W": 0x57, "A": 0x41, "S": 0x53, "D": 0x44}
GROUND = -60.0
SPAWN = (244.5, 240.5)
YAW0 = 0.0

MOVE_STATE_BITS = {
    "up_left": 9,
    "up_right": 10,
    "down_left": 11,
    "down_right": 12,
    "up": 13,
    "down": 14,
    "left": 15,
    "right": 16,
    "sneak": 0,
    "jump": 7,
    "sprint": 8,
}

# RecordWatchMaxSlots is 4 in the mod, so mMove is read as one packed u64.
WATCH_MOVE = [
    {
        "component_id": MOVE_INPUT,
        "offset": 0x00,
        "size": 4,
        "name": "flags",
        "fmt": "u32",
        "bits": MOVE_STATE_BITS,
    },
    {"component_id": MOVE_INPUT, "offset": 0x04, "size": 4, "name": "ax", "fmt": "f32"},
    {"component_id": MOVE_INPUT, "offset": 0x08, "size": 4, "name": "ay", "fmt": "f32"},
    {"component_id": MOVE_INPUT, "offset": 0x24, "size": 8, "name": "move_packed", "fmt": "u64"},
]


def split_vec2(packed: int | None) -> tuple[float, float]:
    if packed is None:
        return (0.0, 0.0)
    return struct.unpack("<ff", struct.pack("<Q", packed & 0xFFFFFFFFFFFFFFFF))


RESULTS: dict[str, Any] = {}


# -- helpers -----------------------------------------------------------------


def read_move(client: ModClient, runtime_id: int) -> dict[str, Any]:
    reply = client.ecs_component(runtime_id, MOVE_INPUT, length=0x64)
    raw = bytes.fromhex(str(reply["hex"]))
    flags = struct.unpack_from("<I", raw, 0x00)[0]
    ax, ay = struct.unpack_from("<ff", raw, 0x04)
    mx, my = struct.unpack_from("<ff", raw, 0x24)
    cflags = struct.unpack_from("<H", raw, 0x60)[0]
    return {
        "flags": flags,
        "analog_x": ax,
        "analog_y": ay,
        "move_x": mx,
        "move_y": my,
        "component_flags": cflags,
    }


def reset(client: ModClient, *, ticks: int = 8) -> dict[str, Any]:
    client.set_input()
    client.teleport(SPAWN[0], GROUND, SPAWN[1], yaw=YAW0, pitch=0.0)
    client.look(YAW0, 0.0)
    gate = settle(client, ticks=3, on_ground=True, still=5e-3, input_sprint=False)
    client.wait_ticks(ticks)
    return gate


def press(client: ModClient, keys: list[str], down: bool) -> None:
    for name in keys:
        client.key_event(KEYS[name], down, via="hardware")


def hold_window(
    client: ModClient,
    keys: list[str],
    *,
    runtime_id: int,
    warmup: int = 3,
    ticks: int = 6,
) -> dict[str, Any]:
    """Hold ``keys`` across a recorded window and return steady-state rows."""
    press(client, keys, True)
    client.wait_ticks(warmup)
    with record_session(client, watch=WATCH_MOVE) as session:
        client.wait_ticks(ticks)
    press(client, keys, False)
    rows = session.rows
    watches = session.decoded_watch
    # Keep only rows whose flags match the last row: the injection edges take a
    # tick or two to land and the record window may still catch them.
    last = watches[-1] if watches else {}
    steady = [w for w in watches if w and w.get("flags") == last.get("flags")]
    vx = [float(r["vx"]) for r in rows[-max(1, len(rows) // 2) :]]
    vz = [float(r["vz"]) for r in rows[-max(1, len(rows) // 2) :]]
    mx, my = split_vec2(last.get("move_packed"))
    return {
        "flags": last.get("flags"),
        "analog_x": last.get("ax"),
        "analog_y": last.get("ay"),
        "move_x": mx,
        "move_y": my,
        "bits": {k: last.get(k) for k in MOVE_STATE_BITS},
        "steady_watch_rows": len(steady),
        "mean_vx": statistics_mean(vx),
        "mean_vz": statistics_mean(vz),
        "rows": len(rows),
    }


def statistics_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def wrap180(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0


def angle_deg(x: float, z: float) -> float:
    """World heading of a horizontal vector, degrees, atan2(x, z)."""
    return math.degrees(math.atan2(x, z))


# -- S1: keyboard multi-key synthesis ----------------------------------------


S1_COMBOS: list[tuple[str, list[str]]] = [
    ("W", ["W"]),
    ("A", ["A"]),
    ("S", ["S"]),
    ("D", ["D"]),
    ("W+A", ["W", "A"]),
    ("W+D", ["W", "D"]),
    ("S+A", ["S", "A"]),
    ("S+D", ["S", "D"]),
    ("A+D", ["A", "D"]),
    ("W+S", ["W", "S"]),
    ("W+A+D", ["W", "A", "D"]),
    ("W+S+A", ["W", "S", "A"]),
    ("W+S+D", ["W", "S", "D"]),
    ("W+S+A+D", ["W", "S", "A", "D"]),
]


def stage_s1(client: ModClient, runtime_id: int) -> None:
    rows: list[dict[str, Any]] = []
    for label, keys in S1_COMBOS:
        reset(client)
        observed = hold_window(client, keys, runtime_id=runtime_id)
        client.wait_ticks(4)
        moved = client.state()
        observed["combo"] = label
        observed["keys"] = keys
        observed["end_feet_x"] = float(moved["feet_x"])
        observed["end_feet_z"] = float(moved["feet_z"])
        rows.append(observed)
    RESULTS["s1_digital"] = rows


# -- S2: analog move vector ---------------------------------------------------


S2_CASES: list[tuple[float, float]] = [
    (0.0, 0.0),
    (0.5, 0.0),
    (1.0, 0.0),
    (0.5, 0.5),
    (0.7, 0.7),
    (0.9999, 0.0),
    (1.0, 1.0),
    (0.8, 0.8),
    (0.6, 0.8),
    (0.0, 0.9),
]


def stage_s2(client: ModClient, runtime_id: int) -> None:
    rows: list[dict[str, Any]] = []
    client.input_cfg(mode=8, jump_impulse=0, force=True)
    try:
        for forward, strafe in S2_CASES:
            reset(client)
            client.set_input(forward=forward, strafe=strafe, ticks=14)
            client.wait_ticks(10)
            observed = read_move(client, runtime_id)
            state = client.state()
            observed["forward"] = forward
            observed["strafe"] = strafe
            observed["state_input"] = dict(state.get("input") or {})
            observed["mean_vx"] = float(state.get("vx", 0.0))
            observed["mean_vz"] = float(state.get("vz", 0.0))
            rows.append(observed)
            client.set_input()
            client.wait_ticks(6)
    finally:
        client.input_cfg(mode=0, jump_impulse=0, force=True)
    RESULTS["s2_analog"] = rows


# -- S3: mouse look -----------------------------------------------------------


def look_sample(client: ModClient) -> tuple[float, float]:
    state = client.state()
    return float(state.get("yaw", 0.0)), float(state.get("pitch", 0.0))


def mouse_turn(client: ModClient, *, dx: int, dy: int, settle_ticks: int = 3) -> dict[str, Any]:
    yaw0, pitch0 = look_sample(client)
    client.mouse_event("move", dx=dx, dy=dy)
    client.wait_ticks(settle_ticks)
    yaw1, pitch1 = look_sample(client)
    return {
        "dx": dx,
        "dy": dy,
        "yaw0": yaw0,
        "yaw1": yaw1,
        "pitch0": pitch0,
        "pitch1": pitch1,
        "d_yaw": wrap180(yaw1 - yaw0),
        "d_pitch": wrap180(pitch1 - pitch0),
    }


def stage_s3(client: ModClient) -> None:
    client.look(YAW0, 0.0)
    client.wait_ticks(3)
    samples: list[dict[str, Any]] = []
    for dx, dy in ((50, 0), (100, 0), (200, 0), (0, 50), (0, 100), (50, 50), (-100, 0), (0, -100)):
        client.look(YAW0, 0.0)
        client.wait_ticks(2)
        samples.append(mouse_turn(client, dx=dx, dy=dy))
    RESULTS["s3_mouse"] = {"spawn_window": dict(client.screen() or {}), "samples": samples}

    # Pitch clamp: push far past the limit in both directions.
    clamp_rows: list[dict[str, Any]] = []
    for dy in (4000, 4000, -8000, -8000):
        row = mouse_turn(client, dx=0, dy=dy, settle_ticks=4)
        clamp_rows.append(row)
    RESULTS["s4_pitch_clamp"] = clamp_rows


def _record(stage: str, row: dict[str, Any]) -> None:
    """Append ``row`` unless a row with the same label already exists."""
    kept = [
        existing for existing in RESULTS.get(stage, []) if existing.get("label") != row["label"]
    ]
    kept.append(row)
    RESULTS[stage] = kept


def stage_s3_sensitivity(client: ModClient, label: str) -> None:
    """Degrees-per-pixel at whatever sensitivity ``options.txt`` currently holds.

    The settings-UI apply path is not reached through ``options_set`` /
    ``loadOptionsFromString`` (measured: changing ``ctrl_sensitivity2_mouse``
    live leaves the slope bit-identical), so every option value needs its own
    game start and gets its own ``label``.
    """
    client.look(YAW0, 0.0)
    client.wait_ticks(3)
    rows = [mouse_turn(client, dx=100, dy=0), mouse_turn(client, dx=50, dy=0)]
    _record(
        "s5_sensitivity",
        {
            "label": label,
            "window": client_window(),
            "samples": rows,
            "deg_per_px": rows[0]["d_yaw"] / 100.0,
        },
    )


def client_window() -> dict[str, Any] | None:
    """Client area of the game window in physical pixels (DPI aware)."""
    try:
        import ctypes
        import ctypes.wintypes as wintypes
        import re
        import subprocess

        user32 = ctypes.windll.user32
        with contextlib.suppress(Exception):
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
        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        return {
            "width": int(rect.right),
            "height": int(rect.bottom),
            "minimized": bool(user32.IsIconic(hwnd)),
        }
    except Exception:
        return None


def stage_s6_spyglass(client: ModClient, runtime_id: int, label: str) -> None:
    """Degrees-per-pixel with and without an active spyglass."""
    client.command("gamemode survival @s")
    client.wait_ticks(3)
    client.set_item(0, "minecraft:spyglass", container="inventory")
    client.command("slot 0 @s")
    client.wait_ticks(4)
    settle(client, ticks=3, on_ground=True, still=5e-3)
    base = mouse_turn(client, dx=100, dy=0)
    client.set_input(rmb=True, ticks=40)
    client.wait_ticks(12)
    try:
        in_use: Any = client.ecs_component(runtime_id, ITEM_IN_USE, length=8).get("hex")
    except Exception as error:  # the component only exists while an item is in use
        in_use = f"absent:{error}"
    zoomed = mouse_turn(client, dx=100, dy=0)
    client.set_input()
    client.wait_ticks(10)
    after = mouse_turn(client, dx=100, dy=0)
    client.command("gamemode creative @s")
    client.wait_ticks(3)
    _record(
        "s6_spyglass",
        {
            "label": label,
            "item_in_use_hex": in_use,
            "base": base,
            "zoomed": zoomed,
            "after": after,
            "ratio": (zoomed["d_yaw"] / base["d_yaw"]) if base["d_yaw"] else None,
        },
    )


def stage_s7_window(client: ModClient, label: str) -> None:
    """Slope x width across two window sizes: proves the 1/renderWidth divisor."""
    import ctypes
    import ctypes.wintypes as wintypes
    import re
    import subprocess
    import time

    user32 = ctypes.windll.user32
    with contextlib.suppress(Exception):
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
    hwnd = found["hwnd"]

    def client_size() -> tuple[int, int]:
        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        return (int(rect.right), int(rect.bottom))

    def resize(width: int, height: int) -> tuple[int, int]:
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.4)
        outer = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(outer))
        current = client_size()
        border = ((outer.right - outer.left) - current[0], (outer.bottom - outer.top) - current[1])
        user32.SetWindowPos(hwnd, 0, 20, 20, width + border[0], height + border[1], 0x0004 | 0x0040)
        user32.SetForegroundWindow(hwnd)
        time.sleep(1.0)
        return client_size()

    rows: list[dict[str, Any]] = []
    for width, height in ((2559, 1433), (1706, 1433), (2559, 900)):
        size = resize(width, height)
        sample = mouse_turn(client, dx=100, dy=0)
        rows.append(
            {
                "requested": [width, height],
                "client": list(size),
                "deg_per_px": sample["d_yaw"] / 100.0,
                "slope_x_width": (sample["d_yaw"] / 100.0) * size[0],
            }
        )
    resize(2559, 1433)
    _record("s7_window", {"label": label, "rows": rows})


def predicted_k(sensitivity: float) -> tuple[float, float]:
    """Static model: v = 0.81*(1.1*S)^0.6125, k = 9600*(0.15+0.6v)^3*0.3."""

    def f32(value: float) -> float:
        return struct.unpack("<f", struct.pack("<f", value))[0]

    scaled = f32(f32(1.1) * f32(sensitivity))
    curved = f32(scaled ** f32(0.6125))
    v = f32(f32(0.81) * curved)
    inner = f32(f32(f32(0.6) * v) + f32(0.15))
    return v, f32(f32(f32(9600.0) * f32(inner * inner * inner)) * f32(0.3))


S1_EXPECT: dict[str, tuple[float, float]] = {
    "W": (0.0, 1.0),
    "A": (1.0, 0.0),
    "S": (0.0, -1.0),
    "D": (-1.0, 0.0),
    "W+A": (0.707107, 0.707107),
    "W+D": (-0.707107, 0.707107),
    "S+A": (0.707107, -0.707107),
    "S+D": (-0.707107, -0.707107),
    "A+D": (0.0, 0.0),
    "W+S": (0.0, 0.0),
    "W+A+D": (0.0, 1.0),
    "W+S+A": (1.0, 0.0),
    "W+S+D": (-1.0, 0.0),
    "W+S+A+D": (0.0, 0.0),
}

S1_EXPECT_BITS: dict[str, set[str]] = {
    "W": {"up"},
    "A": {"left"},
    "S": {"down"},
    "D": {"right"},
    "W+A": {"up", "left"},
    "W+D": {"up", "right"},
    "S+A": {"down", "left"},
    "S+D": {"down", "right"},
    "A+D": {"left", "right"},
    "W+S": {"up", "down"},
    "W+A+D": {"up", "left", "right"},
    "W+S+A": {"up", "down", "left"},
    "W+S+D": {"up", "down", "right"},
    "W+S+A+D": {"up", "down", "left", "right"},
}

S2_EXPECT: dict[tuple[float, float], tuple[float, float]] = {
    (0.0, 0.0): (0.0, 0.0),
    (0.5, 0.0): (0.0, 0.5),
    (1.0, 0.0): (0.0, 1.0),
    (0.5, 0.5): (0.5, 0.5),
    (0.7, 0.7): (0.7, 0.7),
    (0.9999, 0.0): (0.0, 0.9999),
    (1.0, 1.0): (0.707107, 0.707107),
    (0.8, 0.8): (0.707107, 0.707107),
    (0.6, 0.8): (0.8, 0.6),
    (0.0, 0.9): (0.9, 0.0),
}


def verify() -> None:
    """Hard gates on the numbers the report quotes."""
    for row in RESULTS["s1_digital"]:
        want = S1_EXPECT[row["combo"]]
        got = (row["move_x"], row["move_y"])
        assert abs(got[0] - want[0]) <= 2e-6, (row["combo"], got, want)
        assert abs(got[1] - want[1]) <= 2e-6, (row["combo"], got, want)
        assert math.hypot(*got) <= 1.0000001, (row["combo"], got)
        assert row["analog_x"] == 0.0 and row["analog_y"] == 0.0, (row["combo"], row)
        pressed = {name for name, on in row["bits"].items() if on}
        assert S1_EXPECT_BITS[row["combo"]] <= pressed, (row["combo"], pressed)

    for row in RESULTS["s2_analog"]:
        want = S2_EXPECT[(row["forward"], row["strafe"])]
        got = (row["move_x"], row["move_y"])
        assert abs(got[0] - want[0]) <= 2e-6, (row["forward"], row["strafe"], got, want)
        assert abs(got[1] - want[1]) <= 2e-6, (row["forward"], row["strafe"], got, want)
        assert math.hypot(*got) <= 1.0000001, (row,)

    samples = {row["dx"]: row for row in RESULTS["s3_mouse"]["samples"] if row["dy"] == 0}
    d50, d100, d200 = samples[50]["d_yaw"], samples[100]["d_yaw"], samples[200]["d_yaw"]
    assert abs(d100 - 2 * d50) <= 2e-3, (d50, d100)
    assert abs(d200 - 4 * d50) <= 1e-2, (d50, d200)
    vertical = {row["dy"]: row for row in RESULTS["s3_mouse"]["samples"] if row["dx"] == 0}
    assert abs(vertical[100]["d_pitch"] - d100) <= 2e-3, (vertical[100], d100)

    clamp = [row["pitch1"] for row in RESULTS["s4_pitch_clamp"]]
    assert clamp[0] == 89.89999389648438, clamp[0]
    assert clamp[1] == -89.9000015258789, clamp[1]

    for row in RESULTS["s5_sensitivity"]:
        window = row.get("window") or {}
        width = float(window.get("width") or 0)
        if width <= 0:
            continue
        _, expected = predicted_k(float(str(row["label"]).removeprefix("sens")))
        residual = row["deg_per_px"] * width / expected - 1.0
        assert abs(residual) <= 5e-3, (row["label"], residual)

    for row in RESULTS["s6_spyglass"]:
        assert abs(float(row["ratio"]) - 0.1) <= 1e-4, row

    for group in RESULTS.get("s7_window", []):
        products = [item["slope_x_width"] for item in group["rows"] if item["client"][0] > 0]
        if len(products) > 1:
            assert max(products) / min(products) - 1.0 <= 5e-3, products


def main() -> None:
    stages = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    label = next(
        (arg.split("=", 1)[1] for arg in sys.argv[1:] if arg.startswith("--label=")), "unset"
    )
    if not stages:
        stages = ["s1", "s2", "s3", "s3b", "s6", "s7"]
    if OUT.exists():
        RESULTS.update(json.loads(OUT.read_text(encoding="utf-8")))
    with ModClient() as client:
        client.wait_in_world(90.0)
        runtime_id = int(client.state()["runtime_id"])
        with LabSession(client, lock_input=True) as lab:
            del lab
            settle(client, ticks=4, on_ground=True, still=5e-3, input_sprint=False)
            RESULTS["meta"] = {
                "runtime_id": runtime_id,
                "label": label,
                "sections": {"move_input": MOVE_INPUT, "item_in_use": ITEM_IN_USE},
                "spawn": {"x": SPAWN[0], "y": GROUND, "z": SPAWN[1], "yaw": YAW0},
                "window": client_window(),
                "bindings": dict(client.state().get("bindings") or {}),
            }
            if "s1" in stages:
                stage_s1(client, runtime_id)
            if "s2" in stages:
                stage_s2(client, runtime_id)
            if "s3" in stages:
                stage_s3(client)
            if "s3b" in stages:
                stage_s3_sensitivity(client, label)
            if "s6" in stages:
                stage_s6_spyglass(client, runtime_id, label)
            if "s7" in stages:
                stage_s7_window(client, label)
    verify()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(RESULTS, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
