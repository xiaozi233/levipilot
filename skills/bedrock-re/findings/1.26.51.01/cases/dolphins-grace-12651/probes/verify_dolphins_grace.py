"""Live verification of Dolphin's Grace (DolphinBoostSystem) in MCBE 1.26.51.01."""

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
from driver.harness.lab import (  # noqa: E402
    RecordSession,
    fit_recursion,
    settle,
    swim_drive,
    swim_stop,
    swimming_state,
)
from driver.harness.record import RecordEntry  # noqa: E402

OUT_DIR = ROOT / "out" / "dolphins-grace-1.26.51.01"

SWIM_SPEED_MULT_ID = 0x60FC5A9A
MOVEMENT_EFFECTS_ID = 0x5C92321D
SCAN_DOLPHIN_TIMER_ID = 0xB8B9C219

# Arena coordinates (isolated superflat region)
AX0 = 400.5
Y_FLOOR = -58
Y_WATER_BOT = -57
Y_WATER_TOP = -50
Y_SWIM = -54.0


def read_comp_bytes(client: ModClient, ctx: int, comp_id: int, size: int) -> bytes | None:
    reply = client.ecs_context(ctx, comp_id)
    addr = int(reply.get("address", 0) or 0)
    if not addr:
        return None
    hex_str = str(client.mem_read(addr, size).get("hex") or "")
    if len(hex_str) < size * 2:
        return None
    return bytes.fromhex(hex_str)


def read_dolphin_ecs(client: ModClient) -> dict[str, Any]:
    st = client.state()
    ctx = int(st.get("context", 0) or 0)
    if not ctx:
        return {}
    out: dict[str, Any] = {"tick": st.get("tick"), "swimming": swimming_state(client)}
    raw_mult = read_comp_bytes(client, ctx, SWIM_SPEED_MULT_ID, 4)
    if raw_mult is not None:
        out["swim_speed_multiplier"] = struct.unpack("<f", raw_mult)[0]
    raw_timer = read_comp_bytes(client, ctx, SCAN_DOLPHIN_TIMER_ID, 4)
    if raw_timer is not None:
        out["scan_dolphin_timer"] = struct.unpack("<i", raw_timer)[0]
    raw_me = read_comp_bytes(client, ctx, MOVEMENT_EFFECTS_ID, 24)
    if raw_me is not None:
        begin, end, _cap = struct.unpack("<QQQ", raw_me)
        n = (end - begin) // 16 if end >= begin and (end - begin) <= 160 else 0
        entries = []
        if n > 0 and begin:
            vec_hex = str(client.mem_read(begin, n * 16).get("hex") or "")
            vec_bytes = bytes.fromhex(vec_hex)
            for i in range(n):
                if (i + 1) * 16 <= len(vec_bytes):
                    start_tick, dur, eff_type = struct.unpack_from("<Qii", vec_bytes, i * 16)
                    entries.append(
                        {"slot": i, "start_tick": start_tick, "duration": dur, "type": eff_type}
                    )
        out["movement_effects"] = entries
    return out


def wait_effect_expired(client: ModClient, max_ticks: int = 75) -> None:
    for _ in range(max_ticks):
        ecs = read_dolphin_ecs(client)
        effs = ecs.get("movement_effects") or []
        active_boost = len(effs) >= 2 and effs[1].get("type", -1) != -1
        mult = float(ecs.get("swim_speed_multiplier") or 1.0)
        if not active_boost and mult <= 1.01:
            return
        client.wait_ticks(2)


def prime_scan_timer_without_dolphins(client: ModClient) -> int:
    """Drain ScanForDolphinTimerComponent to 1..8 (stopping at 6..10 before swim_stop lag)."""
    for _attempt in range(3):
        t_cur = int(read_dolphin_ecs(client).get("scan_dolphin_timer") or 0)
        if 1 <= t_cur <= 8:
            return t_cur
        swim_stop(client, settle=4)
        client.teleport(AX0, Y_SWIM, 396.8, yaw=180.0, pitch=0.0)
        client.wait_ticks(3)
        swim_drive(client, sprint=True, timeout=2.0, hold=True, poll_ticks=2)
        for _ in range(65):
            t = int(read_dolphin_ecs(client).get("scan_dolphin_timer") or 0)
            if 6 <= t <= 10:
                break
            client.wait_ticks(1)
        swim_stop(client, settle=5)
        t_after = int(read_dolphin_ecs(client).get("scan_dolphin_timer") or 0)
        if 1 <= t_after <= 9:
            return t_after
    return int(read_dolphin_ecs(client).get("scan_dolphin_timer") or 0)


def setup_arena(client: ModClient) -> None:
    client.teleport(AX0, -52.0, 415.0, yaw=0.0, pitch=0.0)
    client.wait_ticks(15)
    client.teleport(AX0, -52.0, 455.0, yaw=0.0, pitch=0.0)
    client.wait_ticks(15)

    client.command(f"/fill 392 {Y_FLOOR} 395 406 {Y_WATER_TOP + 2} 485 minecraft:air")
    client.wait_ticks(4)
    client.command(f"/fill 397 {Y_FLOOR} 395 403 {Y_FLOOR} 485 minecraft:stone")
    client.command(f"/fill 397 {Y_WATER_BOT} 395 397 {Y_WATER_TOP} 485 minecraft:stone")
    client.command(f"/fill 403 {Y_WATER_BOT} 395 403 {Y_WATER_TOP} 485 minecraft:stone")
    client.command(f"/fill 397 {Y_WATER_BOT} 395 403 {Y_WATER_TOP} 395 minecraft:stone")
    client.command(f"/fill 397 {Y_WATER_BOT} 395 403 {Y_WATER_TOP} 485 minecraft:stone")
    client.command(f"/fill 398 {Y_WATER_BOT} 396 402 {Y_WATER_TOP} 484 minecraft:water")
    client.wait_ticks(10)


def equip_depth_strider(client: ModClient, level: int) -> dict[str, Any]:
    client.clear_item(3, container="armor")
    client.clear_item(0)
    client.wait_ticks(3)
    if level > 0:
        client.set_item(0, "minecraft:leather_boots", count=1)
        client.select_slot(0)
        client.wait_ticks(3)
        client.command(f"/enchant @s depth_strider {level}")
        client.wait_ticks(3)
        client.equip("feet")
        client.wait_ticks(5)
    armor = client.inventory().get("armor", [])
    return next((e for e in armor if int(e.get("slot", -1)) == 3), {})


def clear_dolphins(client: ModClient) -> None:
    client.command("/tp @e[type=dolphin] 400 -100 400")
    client.command("/kill @e[type=dolphin]")
    client.wait_ticks(2)


def build_side_dolphin_pods(
    client: ModClient, z_positions: list[int], *, dry: bool = False
) -> None:
    clear_dolphins(client)
    for zp in z_positions:
        client.command(f"/fill 395 {Y_FLOOR} {zp - 1} 397 -51 {zp + 1} minecraft:stone")
        fill_block = "minecraft:air" if dry else "minecraft:water"
        client.command(f"/fill 396 -55 {zp} 396 -53 {zp} {fill_block}")
        client.wait_ticks(1)
        client.command(f"/summon dolphin 396.5 -54.5 {zp + 0.5} 0 0")
    client.wait_ticks(5)
    if dry:
        client.command("/effect @e[type=dolphin] resistance 120 4 true")
        client.wait_ticks(2)


def analyze_vz_run(
    entries: list[RecordEntry], *, coast_tick: int | None = None, fit_skip: int = 8
) -> dict[str, Any]:
    vzs = [round(float(e.vz), 6) for e in entries]
    active = [i for i, e in enumerate(entries) if float(e.vz) > 0.005]
    start_idx = active[0] if active else 0
    if coast_tick is not None:
        end_idx = next(
            (i for i, e in enumerate(entries) if int(e.tick) > coast_tick),
            len(entries) - 1,
        )
    else:
        end_idx = len(entries) - 1

    # Fit on steady ramp segment (skip initial sprint/timer activation ticks)
    ramp_pairs: list[tuple[float, float]] = []
    fit_begin = min(end_idx, start_idx + fit_skip)
    for i in range(fit_begin + 1, min(end_idx, fit_begin + 35)):
        if vzs[i - 1] > 0.02 and abs(vzs[i] - vzs[i - 1]) > 1e-4:
            ramp_pairs.append((vzs[i - 1], vzs[i]))
    fit_rho, fit_b, fit_res = (
        fit_recursion(ramp_pairs) if len(ramp_pairs) >= 3 else (0.0, 0.0, 0.0)
    )

    steady_slice = vzs[max(start_idx, end_idx - 10) : end_idx]
    steady_vz = sum(steady_slice) / len(steady_slice) if steady_slice else 0.0

    coast_ratios: list[float] = []
    if coast_tick is not None and end_idx < len(vzs):
        for i in range(end_idx + 1, min(len(vzs), end_idx + 8)):
            if abs(vzs[i - 1]) > 0.01:
                coast_ratios.append(vzs[i] / vzs[i - 1])

    on_ground_count = sum(1 for e in entries[start_idx:end_idx] if e.on_ground)
    return {
        "n_rows": len(entries),
        "start_idx": start_idx,
        "end_idx": end_idx,
        "on_ground_frac": on_ground_count / max(1, end_idx - start_idx),
        "fit_pairs": len(ramp_pairs),
        "fit_rho": fit_rho,
        "fit_b": fit_b,
        "fit_accel_over_098": fit_b / 0.98 if fit_b else 0.0,
        "fit_residual": fit_res,
        "steady_vz": steady_vz,
        "implied_b_at_09": steady_vz * 0.1,
        "implied_a_at_09": (steady_vz * 0.1) / 0.98,
        "coast_ratios": coast_ratios,
        "vzs": vzs,
    }


def run_swim_trial(
    client: ModClient,
    *,
    y_start: float = Y_SWIM,
    z_start: float = 402.0,
    pitch: float = 0.0,
    drive_ticks: int = 60,
    coast_ticks: int = 8,
    fit_skip: int = 8,
) -> dict[str, Any]:
    swim_stop(client, settle=5)
    client.teleport(AX0, y_start, z_start, yaw=0.0, pitch=pitch)
    client.wait_ticks(4)
    client.look(0.0, pitch, ticks=2)
    settle(
        client,
        ticks=4,
        on_ground=None,
        include_vertical=False,
        in_water=True,
        sprinting=False,
        input_sprint=False,
    )
    ecs_before = read_dolphin_ecs(client)
    with RecordSession(client, period=1) as rec:
        drv = swim_drive(client, sprint=True, timeout=2.0, hold=True, poll_ticks=2)
        client.wait_ticks(drive_ticks)
        ecs_mid = read_dolphin_ecs(client)
        coast_tick = int(client.state().get("tick", 0))
        client.set_input(forward=0.0, sprint=True, ticks=-1)
        client.wait_ticks(coast_ticks)
        client.set_input()
    entries = rec.entries
    ecs_after = read_dolphin_ecs(client)
    analysis = analyze_vz_run(entries, coast_tick=coast_tick, fit_skip=fit_skip)
    return {
        "drv": drv,
        "ecs_before": ecs_before,
        "ecs_mid": ecs_mid,
        "ecs_after": ecs_after,
        "analysis": analysis,
    }


def run_all(client: ModClient) -> dict[str, Any]:
    client.command("/effect @s clear")
    client.command("/gamemode c")
    client.fly(False)
    client.wait_ticks(4)

    setup_arena(client)
    results: dict[str, Any] = {"trials": {}}

    pod_zs = [403, 409, 415, 421, 427, 433, 439, 445]

    try:
        # 1. Baseline sprint-swim (no dolphin, DS 0)
        equip_depth_strider(client, 0)
        clear_dolphins(client)
        wait_effect_expired(client)
        results["trials"]["baseline_no_dolphin_ds0"] = run_swim_trial(client, fit_skip=5)

        # 2. Baseline Depth Strider III without dolphin (off-ground vs on-ground)
        equip_depth_strider(client, 3)
        clear_dolphins(client)
        wait_effect_expired(client)
        results["trials"]["no_dolphin_ds3_midwater"] = run_swim_trial(
            client, y_start=-54.0, fit_skip=5
        )
        results["trials"]["no_dolphin_ds3_ground"] = run_swim_trial(
            client, y_start=-57.0, pitch=2.0, fit_skip=5
        )

        # 3. Dolphin's Grace with DS 0, 1, 2, 3 (timer primed to 1..9 before pods spawn)
        for lvl in range(4):
            clear_dolphins(client)
            wait_effect_expired(client)
            prime_scan_timer_without_dolphins(client)
            equip_depth_strider(client, lvl)
            build_side_dolphin_pods(client, pod_zs)
            results["trials"][f"dolphin_ds{lvl}_midwater"] = run_swim_trial(
                client, y_start=-54.0, drive_ticks=65, coast_ticks=6, fit_skip=11
            )

        # 4. Dolphin's Grace + DS 3 on-ground swimming
        clear_dolphins(client)
        wait_effect_expired(client)
        prime_scan_timer_without_dolphins(client)
        equip_depth_strider(client, 3)
        build_side_dolphin_pods(client, pod_zs)
        results["trials"]["dolphin_ds3_ground"] = run_swim_trial(
            client, y_start=-57.0, pitch=2.0, drive_ticks=65, coast_ticks=6, fit_skip=11
        )

        # 5. Speed V (amplifier 4) + DS 3: no dolphin vs with dolphin
        client.command("/effect @s speed 180 4 true")
        client.wait_ticks(4)
        clear_dolphins(client)
        wait_effect_expired(client)
        results["trials"]["speed5_ds3_no_dolphin_midwater"] = run_swim_trial(
            client, y_start=-54.0, drive_ticks=45, coast_ticks=4, fit_skip=5
        )
        results["trials"]["speed5_ds3_no_dolphin_ground"] = run_swim_trial(
            client, y_start=-57.0, pitch=2.0, drive_ticks=45, coast_ticks=4, fit_skip=5
        )
        prime_scan_timer_without_dolphins(client)
        build_side_dolphin_pods(client, pod_zs)
        results["trials"]["speed5_ds3_with_dolphin_midwater"] = run_swim_trial(
            client, y_start=-54.0, drive_ticks=65, coast_ticks=6, fit_skip=11
        )
        client.command("/effect @s clear")
        client.wait_ticks(4)

        # 6. Non-swimming water walk next to dolphin (SWIMMING=false)
        clear_dolphins(client)
        wait_effect_expired(client)
        equip_depth_strider(client, 0)
        build_side_dolphin_pods(client, pod_zs)
        swim_stop(client, settle=5)
        client.teleport(AX0, -57.0, 402.0, yaw=0.0, pitch=0.0)
        client.wait_ticks(4)
        settle(
            client, ticks=4, on_ground=None, include_vertical=False, in_water=True, input_sprint=False
        )
        with RecordSession(client, period=1) as rec_walk:
            client.set_input(forward=1.0, sprint=False, ticks=-1)
            client.wait_ticks(35)
            ecs_walk = read_dolphin_ecs(client)
            client.set_input()
        results["trials"]["walk_no_swim_near_dolphin"] = {
            "ecs": ecs_walk,
            "analysis": analyze_vz_run(rec_walk.entries, fit_skip=4),
        }

        # 7. Dry walled dolphin pod (x=396.5) & distant pod (x=393.5 -> delta X = 7.0)
        clear_dolphins(client)
        wait_effect_expired(client)
        prime_scan_timer_without_dolphins(client)
        build_side_dolphin_pods(client, pod_zs[:6], dry=True)
        results["trials"]["dry_walled_dolphin_ds0"] = run_swim_trial(
            client, y_start=-54.0, drive_ticks=50, coast_ticks=4, fit_skip=11
        )
        clear_dolphins(client)
        wait_effect_expired(client)
        prime_scan_timer_without_dolphins(client)
        for zp in pod_zs[:6]:
            client.command(f"/fill 392 {Y_FLOOR} {zp - 1} 394 -51 {zp + 1} minecraft:stone")
            client.command(f"/fill 393 -55 {zp} 393 -53 {zp} minecraft:water")
            client.wait_ticks(1)
            client.command(f"/summon dolphin 393.5 -54.5 {zp + 0.5} 0 0")
        client.wait_ticks(5)
        results["trials"]["far_dolphin_dist7_ds0"] = run_swim_trial(
            client, y_start=-54.0, drive_ticks=50, coast_ticks=4, fit_skip=11
        )

        # 8a. Continuous 90-tick swim next to dolphins (observe tick 60->61 refresh!)
        clear_dolphins(client)
        wait_effect_expired(client)
        prime_scan_timer_without_dolphins(client)
        build_side_dolphin_pods(client, pod_zs)
        swim_stop(client, settle=5)
        client.teleport(AX0, -54.0, 402.0, yaw=0.0, pitch=0.0)
        client.wait_ticks(4)
        settle(
            client,
            ticks=4,
            on_ground=None,
            include_vertical=False,
            in_water=True,
            sprinting=False,
            input_sprint=False,
        )
        ecs_cont_before = read_dolphin_ecs(client)
        with RecordSession(client, period=1) as rec_c:
            swim_drive(client, sprint=True, timeout=2.0, hold=True, poll_ticks=2)
            client.wait_ticks(85)
            client.set_input()
        results["trials"]["continuous_90t_refresh"] = {
            "ecs_before": ecs_cont_before,
            "vzs": [round(float(e.vz), 6) for e in rec_c.entries],
        }

        # 8b. Trigger once near dolphin, clear dolphins immediately, measure exact expiry tick
        clear_dolphins(client)
        wait_effect_expired(client)
        prime_scan_timer_without_dolphins(client)
        build_side_dolphin_pods(client, pod_zs[:3])
        swim_stop(client, settle=5)
        client.teleport(AX0, -54.0, 402.0, yaw=0.0, pitch=0.0)
        client.wait_ticks(4)
        settle(
            client,
            ticks=4,
            on_ground=None,
            include_vertical=False,
            in_water=True,
            sprinting=False,
            input_sprint=False,
        )
        with RecordSession(client, period=1) as rec_expire:
            swim_drive(client, sprint=True, timeout=2.0, hold=True, poll_ticks=2)
            triggered = False
            for _ in range(25):
                st_ecs = read_dolphin_ecs(client)
                if (st_ecs.get("swim_speed_multiplier") or 0.0) > 1.5:
                    triggered = True
                    break
                client.wait_ticks(1)
            ecs_on_trigger = read_dolphin_ecs(client)
            clear_dolphins(client)
            client.wait_ticks(75)
            client.set_input()
        exp_vzs = [round(float(e.vz), 6) for e in rec_expire.entries]
        boost_start = next((i for i in range(1, len(exp_vzs)) if exp_vzs[i] > 0.025 and i < 25), None)
        drop_idx = next(
            (i for i in range(25, len(exp_vzs)) if exp_vzs[i] < exp_vzs[i - 1] - 0.004),
            None,
        )
        results["trials"]["expire_after_kill"] = {
            "triggered": triggered,
            "ecs_on_trigger": ecs_on_trigger,
            "boost_start": boost_start,
            "drop_idx": drop_idx,
            "vzs": exp_vzs,
        }

    finally:
        swim_stop(client, settle=4)
        clear_dolphins(client)
        client.command("/effect @s clear")
        client.clear_item(3, container="armor")
        client.clear_item(0)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUT_DIR / "live-results.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=== DOLPHIN'S GRACE LIVE SUMMARY ===")
    for k, v in results["trials"].items():
        if isinstance(v, dict) and "analysis" in v:
            a = v["analysis"]
            ecs_mid = v.get("ecs_mid") or v.get("ecs") or {}
            t_b = (v.get("ecs_before") or {}).get("scan_dolphin_timer")
            print(
                f"{k:36s} t0={t_b!s:3s} mult={ecs_mid.get('swim_speed_multiplier')} "
                f"steady_vz={a['steady_vz']:.5f} fit_rho={a['fit_rho']:.5f} "
                f"fit_a={a['fit_accel_over_098']:.5f} imp_a={a['implied_a_at_09']:.5f} "
                f"og={a['on_ground_frac']:.2f}"
            )
    if "expire_after_kill" in results["trials"]:
        ex = results["trials"]["expire_after_kill"]
        di = ex["drop_idx"]
        sl = ex["vzs"][max(0, (di or 10) - 4) : (di or 10) + 6] if di else []
        print(f"expire_after_kill: triggered={ex['triggered']} drop_idx={di} around_drop={sl}")
    if "continuous_90t_refresh" in results["trials"]:
        cv = results["trials"]["continuous_90t_refresh"]["vzs"]
        print(
            f"continuous_90t_refresh(n={len(cv)} t0={results['trials']['continuous_90t_refresh']['ecs_before'].get('scan_dolphin_timer')}): "
            f"steady_min[45:]={min(cv[45:]):.6f} max[45:]={max(cv[45:]):.6f}"
        )
    return results


def main() -> int:
    with ModClient(timeout=30.0).connect() as client:
        run_all(client)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
