"""Live verification for creative flight physics (1.26.51.01).

Covers: horizontal acceleration/steady speed (walk/sprint/strafe/diagonal),
release glide decay (0.91 then 0.34125), vertical ascend/descend/hover/lock,
ground-proximity and blue-ice control, water, Speed/Slowness effects, landing,
and the StateVectorComponent::mPosDelta phase against completed displacement.

Every scenario asserts its fixture (flying flag, pool contents, reset state)
and the fitted recursion residual. Raw rows go to
``out/creative-flying-1.26.51.01.json`` (gitignored).
"""

from __future__ import annotations

import itertools
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "driver").is_dir())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.harness.client import ModClient  # noqa: E402
from driver.harness.lab import LabSession, double_space_fly, record_session  # noqa: E402

OUT = ROOT / "out" / "creative-flying-1.26.51.01.json"

FLOOR_Y = -61  # stone layer; its top face is -60.0
START_X = 40.5
START_Z = 40.5
HIGH_Y = -20.0
LOW_Y = -59.5

F_FRICTION = np.float32(0.91)  # 0x3F68F5C3, flying air friction
F_OVERRIDE = np.float32(0.375)  # 0x3EC00000, FrictionModifierOverride written by VerticalFlySpeed
GLIDE = float(F_FRICTION * F_OVERRIDE)  # 0.34125
V_DRAG = np.float32(0.6)  # 1 - 0.4, vertical drag
V_UP = np.float32(0.15)
V_DOWN = np.float32(-0.22)

RESULTS: dict[str, Any] = {}


def f32(value: float) -> float:
    return float(np.float32(value))


def record(
    client: ModClient,
    *,
    forward: float,
    ticks: int,
    sprint: bool = False,
    jump: bool = False,
    sneak: bool = False,
    tail: int = 0,
) -> list[Any]:
    with record_session(client) as session:
        client.set_input(forward=forward, sprint=sprint, jump=jump, sneak=sneak, ticks=ticks)
        client.wait_ticks(ticks + 3)
        if tail:
            client.wait_ticks(tail)
    return list(session.entries)


def reset(client: ModClient, y: float, *, x: float = START_X) -> None:
    client.set_input()
    client.wait_ticks(4)
    client.teleport(x, y, START_Z)
    client.wait_ticks(8)
    state = client.state()
    assert state.get("flying") is True, f"flight dropped before case: {state.get('flying')}"
    assert abs(float(state.get("feet_y", 0.0)) - y) < 0.05, (
        f"teleport missed: {state.get('feet_y')}"
    )


def fit(pairs: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Least squares fit v' = f*v + a; returns (f, a, max residual)."""
    if len(pairs) < 6:
        raise AssertionError(f"not enough pairs: {len(pairs)}")
    x = np.array([p[0] for p in pairs], dtype=np.float64)
    y = np.array([p[1] for p in pairs], dtype=np.float64)
    design = np.stack([x, np.ones_like(x)], axis=1)
    (f, a), *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = float(np.max(np.abs(design @ np.array([f, a]) - y)))
    return float(f), float(a), resid


def ratios(values: list[float]) -> list[float]:
    out = []
    for prev, cur in itertools.pairwise(values):
        out.append(float("nan") if prev == 0.0 else cur / prev)
    return out


def median(values: list[float]) -> float:
    clean = [v for v in values if v == v]
    return float(statistics.median(clean)) if clean else float("nan")


def case_horizontal(client: ModClient) -> None:
    rows = record(client, forward=1.0, ticks=45)
    vx = [r.vx for r in rows if r.vx > 0.0]
    pairs = list(itertools.pairwise(vx))
    f, a, resid = fit(pairs[:35])
    steady = median([r.vx for r in rows[-9:-3]])
    steady_pred = a / (1.0 - f)
    RESULTS["horizontal_forward"] = {
        "friction_fit": round(f, 8),
        "accel_fit": round(a, 8),
        "residual": round(resid, 12),
        "steady_45t": round(steady, 8),
        "steady_pred": round(steady_pred, 8),
        "rows": [
            {
                "tick": r.tick,
                "x": round(r.x, 6),
                "z": round(r.z, 6),
                "vx": round(r.vx, 8),
                "vz": round(r.vz, 8),
                "flags": r.flags,
            }
            for r in rows
        ],
    }
    assert abs(f - float(F_FRICTION)) < 2e-3, f
    assert resid < 2e-5, resid

    # sprint: same fixture, 2x travel speed
    reset(client, HIGH_Y)
    rows = record(client, forward=1.0, sprint=True, ticks=45)
    vx_s = [r.vx for r in rows if r.vx > 0.0]
    f_s, a_s, resid_s = fit(list(itertools.pairwise(vx_s))[:35])
    steady_s = median([r.vx for r in rows[-9:-3]])
    steady_s_pred = a_s / (1.0 - f_s)
    RESULTS["horizontal_sprint"] = {
        "friction_fit": round(f_s, 8),
        "accel_fit": round(a_s, 8),
        "residual": round(resid_s, 12),
        "steady_45t": round(steady_s, 8),
        "steady_pred": round(steady_s_pred, 8),
        "ratio_to_walk_median": round(steady_s / steady, 6),
        "ratio_to_walk_fit": round(steady_s_pred / steady_pred, 6),
    }
    assert abs(f_s - float(F_FRICTION)) < 2e-3, f_s
    assert abs(steady_s_pred / steady_pred - 2.0) < 2e-3, steady_s_pred / steady_pred

    # diagonal W+A: direction only, magnitude unchanged
    reset(client, HIGH_Y)
    with record_session(client) as session:
        client.set_input(forward=1.0, strafe=1.0, ticks=90)
        client.wait_ticks(93)
    rows = list(session.entries)
    tail = rows[-6:]
    vx_d = median([r.vx for r in tail])
    vz_d = median([r.vz for r in tail])
    xs = [r.vx for r in rows if r.vx > 0.0]
    zs = [r.vz for r in rows if r.vz > 0.0]
    fx, ax, _rx = fit(list(itertools.pairwise(xs))[:35])
    fz, az, _rz = fit(list(itertools.pairwise(zs))[:35])
    mag_pred = ((ax / (1.0 - fx)) ** 2 + (az / (1.0 - fz)) ** 2) ** 0.5
    RESULTS["horizontal_diagonal"] = {
        "vx": round(vx_d, 8),
        "vz": round(vz_d, 8),
        "magnitude": round((vx_d**2 + vz_d**2) ** 0.5, 8),
        "friction_fit_x": round(fx, 8),
        "friction_fit_z": round(fz, 8),
        "magnitude_pred": round(mag_pred, 8),
        "ratio_to_walk_fit": round(mag_pred / RESULTS["horizontal_forward"]["steady_pred"], 6),
        "ratio_to_walk_median": round(((vx_d**2 + vz_d**2) ** 0.5) / steady, 6),
    }
    assert abs(mag_pred / RESULTS["horizontal_forward"]["steady_pred"] - 1.0) < 5e-3
    assert abs(fx - float(F_FRICTION)) < 3e-3 and abs(fz - float(F_FRICTION)) < 3e-3


def case_glide(client: ModClient) -> None:
    reset(client, HIGH_Y)
    rows = record(client, forward=1.0, ticks=40, tail=14)
    vx = [r.vx for r in rows]
    # input stops after the 40th tick; find the peak and the decay that follows
    vmax = max(vx)
    peak = max(i for i, value in enumerate(vx) if value >= vmax - 1e-6)
    decay = vx[peak : peak + 8]
    rs = ratios(decay)
    RESULTS["glide_decay"] = {
        "peak_vx": round(decay[0], 8),
        "ratios": [round(r, 8) for r in rs],
        "first_ratio": round(rs[0], 8),
        "sustained_median": round(median(rs[1:6]), 8),
        "rows": [
            {"tick": r.tick, "vx": round(r.vx, 8), "vy": round(r.vy, 8), "flags": r.flags}
            for r in rows[peak - 2 : peak + 8]
        ],
    }
    assert abs(rs[0] - float(F_FRICTION)) < 5e-3, rs[0]
    assert abs(median(rs[1:6]) - GLIDE) < 2e-3, median(rs[1:6])
    # float32 recurrence: first tick keeps only 0.91, then the 0.375 override joins
    pred1 = f32(decay[0] * F_FRICTION)
    pred2 = f32(decay[1] * F_FRICTION * F_OVERRIDE)
    RESULTS["glide_decay"]["float32_pred_1"] = round(pred1, 8)
    RESULTS["glide_decay"]["float32_pred_2"] = round(pred2, 8)
    assert abs(pred1 - decay[1]) < 2e-5, (pred1, decay[1])
    assert abs(pred2 - decay[2]) < 2e-5, (pred2, decay[2])


def case_glide_modifiers(client: ModClient) -> None:
    """Release W while holding jump / sneak: horizontal glide must stay 0.91 then 0.34125."""
    out: dict[str, Any] = {}
    for name, jump, sneak in (
        ("glide_with_jump", True, False),
        ("glide_with_sneak", False, True),
    ):
        reset(client, HIGH_Y)
        with record_session(client) as session:
            client.set_input(forward=1.0, ticks=40)
            client.wait_ticks(42)
            client.set_input(jump=jump, sneak=sneak, ticks=10)
            client.wait_ticks(12)
        rows = list(session.entries)
        vx = [r.vx for r in rows]
        vmax = max(vx)
        peak = max(i for i, value in enumerate(vx) if value >= vmax - 1e-6)
        decay = vx[peak : peak + 7]
        rs = [b / a for a, b in itertools.pairwise(decay)]
        item = {
            "peak_vx": round(decay[0], 8),
            "ratios": [round(r, 8) for r in rs],
            "first_ratio": round(rs[0], 8),
            "sustained_median": round(median(rs[1:5]), 8),
            "vy_after": round(rows[peak + 3].vy, 8),
            "vy_extreme": round(max((r.vy for r in rows[peak:]), key=abs), 8),
            "rows": [
                {"tick": r.tick, "vx": round(r.vx, 8), "vy": round(r.vy, 8), "flags": r.flags}
                for r in rows[peak - 2 : peak + 7]
            ],
        }
        out[name] = item
        assert abs(rs[0] - float(F_FRICTION)) < 5e-3, (name, rs[0])
        assert abs(median(rs[1:5]) - GLIDE) < 2e-3, (name, median(rs[1:5]))
        extreme = item["vy_extreme"]
        if jump:
            assert extreme > 0.1, (name, extreme)
        else:
            assert extreme < -0.1, (name, extreme)
    RESULTS["glide_modifiers"] = out


def case_vertical_release_moving(client: ModClient) -> None:
    """Keep W held, release jump: the horizontal-input gate skips the 0.375 vertical damping."""
    reset(client, HIGH_Y)
    with record_session(client) as session:
        client.set_input(forward=1.0, jump=True, ticks=45)
        client.wait_ticks(25)
        # overlap the windows so W never drops while jump is released
        client.set_input(forward=1.0, ticks=30)
        client.wait_ticks(32)
    rows = list(session.entries)
    vy = [r.vy for r in rows]
    vmax = max(vy)
    peak = max(i for i, value in enumerate(vy) if value >= vmax - 1e-6)
    # W takes one or two ticks to register after the jump window; measure once it is active
    active = [i for i in range(peak + 1, len(rows)) if rows[i].flags & 0x40]
    assert active, "W never registered after the jump window"
    start = active[0]
    decay = vy[start : start + 4]
    rs = [b / a for a, b in itertools.pairwise(decay)]
    RESULTS["vertical_release_moving_h"] = {
        "peak": round(vy[peak], 8),
        "ticks_until_w_active": start - peak,
        "ratios": [round(r, 8) for r in rs],
        "first_ratio": round(rs[0], 8),
        "vx_at_start": round(rows[start].vx, 8),
        "rows": [
            {"tick": r.tick, "vx": round(r.vx, 8), "vy": round(r.vy, 8), "flags": r.flags}
            for r in rows[peak - 2 : start + 5]
        ],
    }
    assert abs(rs[0] - float(V_DRAG)) < 5e-3, rs[0]
    assert rows[start].vx > 0.3, rows[start].vx


def case_vertical(client: ModClient) -> None:
    reset(client, HIGH_Y)
    rows = record(client, forward=0.0, jump=True, ticks=45)
    vy = [r.vy for r in rows if r.vy > 0.0]
    f_up, a_up, resid_up = fit(list(itertools.pairwise(vy))[:30])
    steady_up = median([r.vy for r in rows[-9:-3]])
    RESULTS["vertical_up"] = {
        "drag_fit": round(f_up, 8),
        "accel_fit": round(a_up, 8),
        "residual": round(resid_up, 12),
        "steady": round(steady_up, 8),
        "rows": [{"tick": r.tick, "y": round(r.y, 6), "vy": round(r.vy, 8)} for r in rows],
    }
    assert abs(f_up - float(V_DRAG)) < 3e-3, f_up
    assert abs(a_up - 0.15) < 2e-3, a_up
    assert abs(steady_up - 0.375) < 2e-3, steady_up

    reset(client, HIGH_Y)
    rows = record(client, forward=0.0, sneak=True, ticks=45)
    vy = [r.vy for r in rows if r.vy < 0.0]
    f_dn, a_dn, resid_dn = fit(list(itertools.pairwise(vy))[:30])
    steady_dn = median([r.vy for r in rows[-9:-3]])
    RESULTS["vertical_down"] = {
        "drag_fit": round(f_dn, 8),
        "accel_fit": round(a_dn, 8),
        "residual": round(resid_dn, 12),
        "steady": round(steady_dn, 8),
    }
    assert abs(f_dn - float(V_DRAG)) < 3e-3, f_dn
    assert abs(a_dn + 0.22) < 2e-3, a_dn
    assert abs(steady_dn + 0.55) < 2e-3, steady_dn

    # both keys: hover lock (mPosDelta.y forced to 0)
    reset(client, HIGH_Y)
    rows = record(client, forward=0.0, jump=True, sneak=True, ticks=30)
    ys = [r.y for r in rows[-10:]]
    RESULTS["vertical_lock"] = {
        "vy_max_abs": round(max(abs(r.vy) for r in rows), 8),
        "y_drift": round(max(ys) - min(ys), 8),
    }
    assert max(abs(r.vy) for r in rows) < 1e-6

    # release from steady ascend: 0.375 * 0.6 = 0.225 on the first idle tick
    reset(client, HIGH_Y)
    rows = record(client, forward=0.0, jump=True, ticks=40, tail=6)
    vy = [r.vy for r in rows]
    vmax = max(vy)
    peak = max(i for i, value in enumerate(vy) if value >= vmax - 1e-6)
    rs = ratios(vy[peak : peak + 4])
    RESULTS["vertical_release"] = {
        "peak": round(vy[peak], 8),
        "ratios": [round(r, 8) for r in rs],
    }
    assert abs(rs[0] - 0.225) < 5e-3, rs[0]


def case_ground(client: ModClient) -> None:
    reset(client, LOW_Y)
    rows = record(client, forward=1.0, ticks=40)
    low_steady = median([r.vx for r in rows[-9:-3]])
    low_vx = [r.vx for r in rows if r.vx > 0.0]
    f_low, a_low, r_low = fit(list(itertools.pairwise(low_vx))[:30])
    low_state = client.state()
    RESULTS["ground_proximity"] = {
        "stone_low_vx": round(low_steady, 8),
        "stone_friction_fit": round(f_low, 8),
        "stone_accel_fit": round(a_low, 8),
        "stone_residual": round(r_low, 12),
        "feet_y": round(float(low_state.get("feet_y", 0.0)), 6),
        "on_ground": bool(low_state.get("on_ground")),
    }
    assert low_steady > 0.3, low_steady

    # blue ice directly below the same line
    client.command(f"fill 20 {FLOOR_Y} 20 70 {FLOOR_Y} 70 blue_ice")
    client.wait_ticks(8)
    rows = client.blocks([[40, FLOOR_Y, 40]]).get("blocks", [])
    assert rows and str(rows[0].get("name", "")).endswith("blue_ice"), rows
    reset(client, LOW_Y)
    rows = record(client, forward=1.0, ticks=40)
    ice_steady = median([r.vx for r in rows[-9:-3]])
    ice_vx = [r.vx for r in rows if r.vx > 0.0]
    f_ice, a_ice, r_ice = fit(list(itertools.pairwise(ice_vx))[:30])
    RESULTS["ground_proximity"].update(
        {
            "blue_ice_low_vx": round(ice_steady, 8),
            "blue_ice_friction_fit": round(f_ice, 8),
            "blue_ice_accel_fit": round(a_ice, 8),
            "blue_ice_residual": round(r_ice, 12),
        }
    )
    assert abs(f_ice - f_low) < 2e-3, (f_ice, f_low)
    assert abs(a_ice - a_low) < 2e-3, (a_ice, a_low)

    # restore stone for later cases
    client.command(f"fill 20 {FLOOR_Y} 20 70 {FLOOR_Y} 70 stone")
    client.wait_ticks(8)


def case_water(client: ModClient) -> None:
    x0, x1, z0, z1 = 22, 54, 30, 46
    top, bottom = -33, -39
    client.command(f"fill {x0} -40 {z0} {x1} -40 {z1} stone")
    client.wait_ticks(8)
    client.command(f"fill {x0} {bottom} {z0} {x1} {top} {z0} stone")
    client.command(f"fill {x0} {bottom} {z1} {x1} {top} {z1} stone")
    client.command(f"fill {x0} {bottom} {z0} {x0} {top} {z1} stone")
    client.command(f"fill {x1} {bottom} {z0} {x1} {top} {z1} stone")
    client.wait_ticks(12)
    client.command(f"fill {x0 + 1} {bottom} {z0 + 1} {x1 - 1} {top} {z1 - 1} water")
    client.wait_ticks(30)
    rows = client.blocks([[x0 + 2, -36, (z0 + z1) // 2]]).get("blocks", [])
    assert rows and str(rows[0].get("name", "")).endswith("water"), rows
    client.teleport(x0 + 3.0, -36.0, (z0 + z1) / 2 + 0.5)
    client.wait_ticks(12)
    state = client.state()
    assert state.get("in_water") is True, state.get("in_water")
    assert state.get("flying") is True, state.get("flying")
    with record_session(client) as session:
        client.set_input(forward=1.0, ticks=18)
        client.wait_ticks(21)
    rows = list(session.entries)
    peak = max(range(len(rows)), key=lambda i: rows[i].vx)
    wet = [r.vx for r in rows[: peak + 1] if r.in_water and r.vx > 0.0]
    dry_peak = RESULTS["horizontal_forward"]["steady_45t"]
    f_wet, a_wet, resid_wet = fit(list(itertools.pairwise(wet)))
    RESULTS["water"] = {
        "in_water": True,
        "flying": True,
        "wet_samples": len(wet),
        "friction_fit": round(f_wet, 8),
        "accel_fit": round(a_wet, 8),
        "residual": round(resid_wet, 12),
        "steady_after": round(median(wet[-4:]), 8),
        "air_accel_fit": RESULTS["horizontal_forward"]["accel_fit"],
        "air_reference": dry_peak,
        "rows": [
            {"tick": r.tick, "vx": round(r.vx, 8), "in_water": r.in_water, "flags": r.flags}
            for r in rows[-12:]
        ],
    }
    assert len(wet) >= 8, len(wet)
    assert abs(f_wet - float(F_FRICTION)) < 5e-3, f_wet
    assert abs(a_wet - RESULTS["horizontal_forward"]["accel_fit"]) < 2e-3, a_wet


def case_effects(client: ModClient) -> None:
    RESULTS["horizontal_forward"]["steady_45t"]
    out: dict[str, Any] = {}
    for name, cmd in (
        ("speed2", "effect @s speed 30 1 true"),
        ("slowness2", "effect @s slowness 30 1 true"),
    ):
        client.command(cmd)
        client.wait_ticks(8)
        effects = client.state().get("effects") or []
        assert any(int(e.get("amplifier", -1)) == 1 for e in effects), effects
        reset(client, HIGH_Y)
        rows = record(client, forward=1.0, ticks=40)
        steady = median([r.vx for r in rows[-9:-3]])
        series = [r.vx for r in rows if r.vx > 0.0]
        f_eff, a_eff, r_eff = fit(list(itertools.pairwise(series))[:30])
        out[name] = {
            "vx": round(steady, 8),
            "friction_fit": round(f_eff, 8),
            "accel_fit": round(a_eff, 8),
            "residual": round(r_eff, 12),
            "accel_ratio_to_base": round(a_eff / RESULTS["horizontal_forward"]["accel_fit"], 6),
            "effects": effects,
        }
        assert abs(a_eff / RESULTS["horizontal_forward"]["accel_fit"] - 1.0) < 5e-3, (name, a_eff)
        client.command("effect @s clear")
        client.wait_ticks(8)
    RESULTS["effects"] = out


def case_landing(client: ModClient) -> None:
    reset(client, -30.0)
    client.set_input(sneak=True, ticks=110)
    states = []
    for _ in range(12):
        client.wait_ticks(10)
        st = client.state()
        states.append(
            {
                "tick": st.get("tick"),
                "flying": st.get("flying"),
                "feet_y": round(float(st.get("feet_y", 0.0)), 4),
                "on_ground": st.get("on_ground"),
                "vy": round(float(st.get("vy", 0.0)), 6),
            }
        )
    client.set_input()
    client.wait_ticks(6)
    RESULTS["landing"] = states
    assert any(s["on_ground"] for s in states), states
    landed = [s for s in states if s["on_ground"]]
    assert all(s["flying"] is True for s in landed), landed


def case_phase(client: ModClient) -> None:
    """Phase of StateVector+0x18 (mPosDelta) against completed displacement."""
    watch = [
        {"component_id": 0x1B5D5238, "offset": 0x18, "size": 4, "name": "pdx", "fmt": "f32"},
        {"component_id": 0x1B5D5238, "offset": 0x1C, "size": 4, "name": "pdy", "fmt": "f32"},
        {"component_id": 0x1B5D5238, "offset": 0x20, "size": 4, "name": "pdz", "fmt": "f32"},
    ]
    reset(client, HIGH_Y)
    with record_session(client, watch=watch) as session:
        client.set_input(forward=1.0, ticks=60)
        client.wait_ticks(63)
    rows = session.rows
    valid = [r for r in rows if r.get("watch_mask") == 7]
    peak = max(range(len(valid)), key=lambda i: float(valid[i].get("vx", 0.0)))
    moving = [r for r in valid[: peak + 1] if float(r.get("vx", 0.0)) > 0.05]
    assert len(moving) >= 12, len(moving)
    vx = [float(r["vx"]) for r in moving]
    pdx = [float(r["pdx"]) for r in moving]
    ticks_all = [int(r["tick"]) for r in moving]
    consecutive = [
        (vx[i], vx[i + 1]) for i in range(len(vx) - 1) if ticks_all[i + 1] == ticks_all[i] + 1
    ]
    f_rec, a_rec, resid_rec = fit(consecutive)
    # phase: mPosDelta is the post-friction pending delta of the same tick
    ratio = median([p / v for p, v in zip(pdx, vx, strict=False) if v > 0.05])
    # next completed delta = previous pending delta + the tick's acceleration
    ticks = [int(r["tick"]) for r in moving]
    a_ref = RESULTS["horizontal_forward"]["accel_fit"]
    step_resid = [
        abs(vx[i + 1] - (pdx[i] + a_ref))
        for i in range(len(vx) - 1)
        if ticks[i + 1] == ticks[i] + 1
    ]
    RESULTS["phase"] = {
        "moving_rows": len(moving),
        "friction_fit": round(f_rec, 8),
        "accel_fit": round(a_rec, 8),
        "recursion_residual_consecutive": round(resid_rec, 10),
        "mpdelta_over_velocity_median": round(ratio, 8),
        "max_phase_residual": round(max(step_resid), 10),
        "median_phase_residual": round(median(step_resid), 12),
        "step_accel_median": round(
            median(
                [vx[i + 1] - pdx[i] for i in range(len(vx) - 1) if ticks[i + 1] == ticks[i] + 1]
            ),
            8,
        ),
        "rows": [
            {
                "tick": r.get("tick"),
                "vx": round(float(r.get("vx", 0.0)), 7),
                "pdx": round(float(r.get("pdx", 0.0)), 7),
                "pdy": round(float(r.get("pdy", 0.0)), 7),
                "vy": round(float(r.get("vy", 0.0)), 7),
            }
            for r in moving[-12:]
        ],
    }
    assert abs(ratio - float(F_FRICTION)) < 2e-4, ratio
    assert median(step_resid) < 1e-5, median(step_resid)


def main() -> None:
    with ModClient() as client:
        client.wait_ticks(10)
        with LabSession(client, clear_box=((18, -62, 18), (72, 4, 72))):
            client.command(f"fill 18 {FLOOR_Y} 18 72 {FLOOR_Y} 72 stone")
            client.wait_ticks(10)
            before = client.state()
            if before.get("flying"):
                enter = {"event": "already_flying", "flying": True, "mayfly": before.get("mayfly")}
            else:
                enter = double_space_fly(client)
            RESULTS["enter_flight"] = enter
            assert enter.get("flying") is True, enter
            assert client.state().get("flying") is True
            client.wait_ticks(4)

            reset(client, HIGH_Y)
            case_horizontal(client)
            reset(client, HIGH_Y)
            case_glide(client)
            reset(client, HIGH_Y)
            case_glide_modifiers(client)
            reset(client, HIGH_Y)
            case_vertical(client)
            reset(client, HIGH_Y)
            case_vertical_release_moving(client)
            reset(client, HIGH_Y)
            case_ground(client)
            reset(client, HIGH_Y)
            case_water(client)
            client.set_input()
            client.wait_ticks(6)
            reset(client, HIGH_Y)
            case_effects(client)
            reset(client, HIGH_Y)
            case_landing(client)
            reset(client, HIGH_Y)
            case_phase(client)

            final = client.state()
            RESULTS["final_state"] = {
                k: final.get(k) for k in ("flying", "mayfly", "gamemode", "feet_y", "on_ground")
            }
            assert final.get("flying") is True, final.get("flying")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(RESULTS, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT}")
    for key in (
        "horizontal_forward",
        "horizontal_sprint",
        "horizontal_diagonal",
        "glide_decay",
        "vertical_up",
        "vertical_down",
        "vertical_lock",
        "vertical_release",
        "ground_proximity",
        "water",
        "effects",
        "phase",
    ):
        item = RESULTS.get(key, {})
        brief = {k: v for k, v in item.items() if k != "rows" and k != "samples"}
        print(key, json.dumps(brief, ensure_ascii=False)[:400])


if __name__ == "__main__":
    main()
