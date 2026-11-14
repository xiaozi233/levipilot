"""Independent dynamic-only liquid blip review; no static code inspection.

The JSON preserves full recordings, slow API observations, and read-only RPM
snapshots bracketed by the harness tick counter. Run from the repository root.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from driver import dev
from driver.harness.facade import Harness
from driver.harness.lab import Builder, settle

COMPONENT = 459100728
ROOT = Path("out/liquid-blip-dynamic-review")
X, Z, SURFACE = 354.5, 300.32, -46.0


def decode(reply):
    return list(struct.unpack("<9f", bytes.fromhex(reply["hex"])))


def public_state(s):
    return {k: v for k, v in s.items() if k not in ("name", "uuid", "item")}


class ReadSampler:
    """Only PROCESS_VM_READ; no writes, injection, scanning or disassembly."""

    def __init__(self, pid, tick_address, addresses):
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        self.k32.OpenProcess.restype = ctypes.c_void_p
        self.k32.ReadProcessMemory.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self.k32.ReadProcessMemory.restype = ctypes.c_int
        self.k32.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = self.k32.OpenProcess(0x0010, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.tick_address = tick_address
        self.addresses = addresses
        self.rows = []
        self.errors = []
        self.read_cycles = 0
        self.done = threading.Event()
        self.thread = None

    def read(self, address, size):
        buf = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t()
        if (
            not self.k32.ReadProcessMemory(self.handle, address, buf, size, ctypes.byref(read))
            or read.value != size
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return buf.raw

    def loop(self):
        try:
            previous = None
            saved_ns = 0
            while not self.done.is_set():
                now = time.perf_counter_ns()
                t0 = struct.unpack("<Q", self.read(self.tick_address, 8))[0]
                values = {
                    name: list(struct.unpack(f"<{length // 4}f", self.read(addr, length)))
                    for name, (addr, length) in self.addresses.items()
                }
                t1 = struct.unpack("<Q", self.read(self.tick_address, 8))[0]
                self.read_cycles += 1
                key = (t0, t1, *(tuple(v) for v in values.values()))
                if key != previous or now - saved_ns >= 3_000_000:
                    self.rows.append({"ns": now, "tick_before": t0, "tick_after": t1, **values})
                    previous, saved_ns = key, now
        except BaseException as exc:
            self.errors.append(str(exc))

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.done.set()
        if self.thread:
            self.thread.join(3)
        self.k32.CloseHandle(self.handle)


def build(h):
    b = Builder(h.client)
    h.client.teleport(X, -60, 303.5, yaw=180, pitch=0)
    h.client.wait_ticks(15)
    checks = b.build(
        [
            "fill 349 -49 298 359 -36 308 air",
            "fill 350 -49 299 358 -49 307 stone",
            "fill 350 -48 299 358 -38 299 stone",
            "fill 350 -48 307 358 -47 307 stone",
            "fill 350 -48 299 350 -47 307 stone",
            "fill 358 -48 299 358 -47 307 stone",
            "fill 351 -48 300 357 -47 306 water",
        ],
        {
            (354, -49, 303): "minecraft:stone",
            (354, -48, 303): "minecraft:water",
            (354, -47, 303): "minecraft:water",
            (354, -46, 303): "minecraft:air",
            (354, -38, 299): "minecraft:stone",
        },
    )
    assert all(c.ok for c in checks), checks
    b.poke(357, -48, 306)
    h.command("setblock 357 -48 306 water")
    h.client.wait_ticks(12)
    geometry = h.client.blocks(
        [[354, y, 303] for y in (-49, -48, -47, -46)] + [[354, y, 299] for y in (-48, -47, -38)]
    )
    depths = {str(y): h.client.liquid_depth([354, y, 300]) for y in (-48, -47)}
    print("GEOMETRY", json.dumps({"blocks": geometry, "depths": depths}), flush=True)
    return {"blocks": geometry, "depths": depths}


def run(
    h, pid, meta, label, height, ticks, *, levitation=None, jump=False, forward=1.0, grounded=False, dry=False
):
    h.client.set_input()
    h.command("effect @s levitation 0")
    ground_y, ground_z = (-60, 312.5) if dry else (-48, 303.5)
    h.client.teleport(X, ground_y if grounded else height, ground_z if grounded else Z, yaw=180, pitch=0)
    gate = settle(
        h.client,
        ticks=4,
        timeout=15,
        on_ground=True if grounded else None,
        include_vertical=grounded,
        in_water=not dry,
        input_sprint=False,
    )
    assert gate["ok"], gate
    if not grounded:
        # The gate is at a depth; teleport back to the requested start then
        # let flags/AABB settle for four ticks before starting the recording.
        h.client.teleport(X, height, Z, yaw=180, pitch=0)
        h.client.wait_ticks(4)
    if levitation is not None:
        h.command(f"effect @s levitation 120 {levitation} true")
        h.client.wait_ticks(2)
    start_state = h.state()
    assert not start_state["flying"] and not start_state["mayfly"]
    assert abs(start_state["z"] - (ground_z if grounded else Z)) < 0.05, start_state
    sampler = ReadSampler(
        pid,
        meta["tick_address"],
        {
            "client": (meta["client"]["address"], 36),
            "server": (meta["server"]["address"], 36),
            **{
                name: (meta[name]["address"], length)
                for name, length in (("client_aabb", 24), ("client_travel", 40))
                if name in meta
            },
        },
    )
    watches = []
    watch_rows = []
    watch_baseline = 0
    if meta.get("watch"):
        watch_baseline = h.client.watch_list()["head"]
        for scope in (() if meta.get("entry_only") else ("client", "server")):
            watches.append(
                h.client.watch_set(
                    meta[scope]["address"] + 28,
                    mode="rw",
                    thread="both",
                    depth=1,
                    sample=bool(meta.get("watch_sample")),
                    tag=f"review-{scope}",
                )
            )
        if meta.get("entry_address"):
            watches.append(h.client.watch_set(
                meta["entry_address"], size=1, mode="x", thread="both", depth=1,
                sample=True, tag="review-entry", snapshots=[
                    {"reg": "rdx", "size": 32},
                    {"reg": "r8", "size": 40},
                    {"reg": "r9", "size": 36},
                ],
            ))
            if meta.get("entry_only"):
                # Existing published 0.3 store instruction; no byte decoding.
                for tid in watches[-1]["threads"][:]:
                    watches.append(h.client.watch_set(
                        meta["entry_address"] + 0x146, size=1, mode="x", thread=tid,
                        depth=1, sample=True, tag="review-overwrite-entry",
                    ))
    started = h.record_bin_start(period=1)
    sampler.start()
    api = []
    began = time.perf_counter()
    try:
        h.client.set_input(
            forward=0 if grounded else forward, jump=jump, sprint=False, ticks=ticks + 8
        )
        first_tick = h.state()["tick"]
        while True:
            begin = time.perf_counter_ns()
            a = h.state()
            raw = h.client.ecs_component(start_state["runtime_id"], COMPONENT, length=36)
            client_mem = h.client.mem_read(meta["client"]["address"], 36)
            b = h.state()
            api.append(
                {
                    "ns_begin": begin,
                    "ns_end": time.perf_counter_ns(),
                    "state_before": public_state(a),
                    "server_ecs": raw,
                    "client_mem": client_mem,
                    "state_after": public_state(b),
                }
            )
            if b["tick"] - first_tick >= ticks:
                break
    finally:
        sampler.stop()
        stopped = h.record_bin_stop()
        if watches:
            for watch in watches:
                h.client.watch_clear(watch["id"])
            watch_rows = h.client.watch_drain(since=watch_baseline, limit=16384)
            # Do not inspect instruction bytes: this task only compares hit
            # addresses with an already-published role interval.
            for hit in watch_rows.get("hits", []):
                hit.pop("code_hex", None)
        h.client.set_input()
        h.command("effect @s levitation 0")
    rows = [r.to_dict() for r in h.record_dump_all(record_id=started["record_id"])]
    elapsed = time.perf_counter() - began
    data = {
        "label": label,
        "requested_height": height,
        "levitation_amplifier": levitation,
        "jump": jump,
        "forward": forward,
        "start_state": public_state(start_state),
        "record_start": started,
        "record_stop": stopped,
        "elapsed_seconds": elapsed,
        "api_cycles": len(api),
        "rpm_errors": sampler.errors,
        "rpm_count": len(sampler.rows),
        "rpm_read_cycles": sampler.read_cycles,
        "watches": watches,
        "watch_rows": watch_rows,
        "record": rows,
        "api": api,
        "rpm": sampler.rows,
    }
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / f"{label}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    blips = [r["tick"] for r in rows if abs(r["vy"] - 0.3) < 0.0001]
    # Only show RPM snapshots matching the record's current position exactly.
    table = []
    for r in rows:
        matches = [
            p
            for p in sampler.rows
            if p["tick_before"] == p["tick_after"]
            and p["tick_before"] in (r["tick"] - 1, r["tick"])
            and max(abs(p["client"][i] - r[k]) for i, k in enumerate(("x", "y", "z"))) < 1e-5
        ]
        if not matches:
            continue
        # First and last values disclose intra-tick changes, rather than
        # silently selecting the phase that agrees with a hypothesis.
        p = matches[-1]
        raw_c, raw_s = p["client"][7], p["server"][7]
        table.append(
            {
                "tick": r["tick"],
                "feet_y": r["feet_y"],
                "z": r["z"],
                "actual_vy": r["vy"],
                "raw_client": raw_c,
                "raw_server": raw_s,
                "delta_client": raw_c - r["vy"],
                "ratio_client": raw_c / r["vy"] if abs(r["vy"]) > 1e-7 else None,
                "c_first": matches[0]["client"][7],
                "c_values": sorted({p["client"][7] for p in matches}),
                "observation_ticks": sorted({p["tick_before"] for p in matches}),
                "matched": len(matches),
            }
        )
    data["paired"] = table
    # Write paired results separately so original raw input remains intact.
    (ROOT / f"{label}-paired.json").write_text(json.dumps(table, indent=1), encoding="utf-8")
    print(
        "RUN",
        json.dumps(
            {
                "label": label,
                "record_rows": len(rows),
                "blips": blips,
                "api_hz": len(api) / elapsed,
                "rpm_hz": len(sampler.rows) / elapsed,
                "rpm_read_hz": sampler.read_cycles / elapsed,
                "paired_ticks": len(table),
                "rpm_errors": sampler.errors,
            }
        ),
        flush=True,
    )
    print("tick feet_y actual_vy raw_client raw_server delta ratio first_raw n", flush=True)
    for p in table:
        print(
            p["tick"],
            *[
                f"{p[k]:.7f}" if p[k] is not None else "undefined"
                for k in (
                    "feet_y",
                    "actual_vy",
                    "raw_client",
                    "raw_server",
                    "delta_client",
                    "ratio_client",
                    "c_first",
                )
            ],
            p["matched"],
            flush=True,
        )
    return {"label": label, "blips": blips, "record_rows": len(rows), "paired": table}


def main():
    global ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runs", default="standing,slow-a,slow-b,jump-a,jump-b,sink-a,sink-b,fast-lev"
    )
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--entry-watch", action="store_true")
    parser.add_argument("--entry-only", action="store_true")
    parser.add_argument("--focus", action="store_true", help="explicitly bring the game to the foreground")
    parser.add_argument("--watch-sample", action="store_true")
    parser.add_argument("--geometry-samples", action="store_true")
    args = parser.parse_args()
    if args.entry_only:
        args.entry_watch = True
    if args.entry_watch:
        args.watch = args.watch_sample = True
    if args.output:
        ROOT = Path(args.output)
    ROOT.mkdir(parents=True, exist_ok=True)
    summary = {}
    with Harness() as h:
        original = h.state()
        assert original["in_world"] and original["dimension"] == 0
        assert not original["effects"], "This review requires an unoccupied effects fixture."
        assert not original["flying"] and not original["mayfly"] and original["gamemode"] == 0
        pids = dev.game_pids(dev.DEFAULT_VERSION / "Minecraft.Windows.exe")
        assert len(pids) == 1
        if args.focus:
            assert dev.focus_game(pids[0])
        tick_before = h.state()["tick"]
        time.sleep(0.6)
        tick_after = h.state()["tick"]
        assert tick_after - tick_before >= 5, "World is not advancing; check pause settings or explicitly pass --focus."
        status = h.client.status()
        assert not any(
            status[k] for k in ("macro_recording", "macro_playing", "record_active", "recording")
        )
        meta = {
            "pid": pids[0],
            "tick_address": status["tick_probe_addr"],
            "client": h.client.ecs_context(original["context"], COMPONENT),
            "server": h.client.ecs_component(original["runtime_id"], COMPONENT, length=36),
            "registries": h.client.ecs_registries(),
            "server_entity": h.client.ecs_entity(original["runtime_id"]),
            "original": public_state(original),
        }
        meta["tick_counter_check"] = {
            "status": h.client.status()["tick"],
            "state": h.state()["tick"],
        }
        if args.geometry_samples:
            for name, component_id in (("client_aabb", 0xBAC1B3CF), ("client_travel", 0x39CF8D64)):
                try:
                    meta[name] = h.client.ecs_context(original["context"], component_id)
                except Exception as exc:
                    meta[name + "_unavailable"] = str(exc)
        if args.watch:
            assert h.client.status()["watch_count"] == 0
            meta["watch"] = True
            meta["watch_sample"] = args.watch_sample
            meta["module_base"] = h.client.request({"cmd": "diag"}, "diag")["module_base"]
            if args.entry_watch:
                roles = json.loads(Path("docs/roles/1.26.51.01.json").read_text(encoding="utf-8"))
                role = roles["roles"]["@liquid_exit_blip"]
                assert role["status"] == "resolved"
                meta["entry_address"] = meta["module_base"] + int(role["address"], 16) - int(roles["image_base"], 16)
                meta["entry_only"] = args.entry_only
        (ROOT / "metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        print("IDENTITY", json.dumps(meta), flush=True)
        h.client.input_lock(True)
        try:
            h.command("gamemode creative")
            h.command("gamemode survival")
            h.command("effect @s water_breathing 1200 0 true")
            if not args.no_build:
                summary["geometry"] = build(h)
            choices = {
                "standing": (-48.0, 20, {"grounded": True}),
                "standing-dry": (-60.0, 20, {"grounded": True, "dry": True}),
                "slow-a": (-47.2, 52, {"levitation": 0}),
                "slow-b": (-47.05, 48, {"levitation": 0}),
                "jump-a": (-47.7, 42, {"jump": True}),
                "jump-b": (-47.45, 42, {"jump": True}),
                "sink-a": (-46.85, 22, {}),
                "sink-b": (-47.1, 22, {}),
                "fast-lev": (-47.5, 42, {"levitation": 2}),
            }
            for name in args.runs.split(","):
                height, ticks, kwargs = choices[name]
                summary[name] = run(h, pids[0], meta, name, height, ticks, **kwargs)
        finally:
            # Independent cleanup attempts, always close the client via with.
            errors = []
            if args.watch:
                try:
                    for w in h.client.watch_list()["watches"]:
                        if w.get("tag", "").startswith("review-"):
                            h.client.watch_clear(w["id"])
                except Exception as exc:
                    errors.append(str(exc))
            for action in (
                lambda: h.client.set_input(),
                lambda: h.command("effect @s levitation 0"),
                lambda: h.command("effect @s water_breathing 0"),
                lambda: h.client.teleport(
                    original["feet_x"],
                    original["feet_y"],
                    original["feet_z"],
                    yaw=original["yaw"],
                    pitch=original["pitch"],
                ),
                lambda: h.command("fill 349 -49 298 359 -36 308 air"),
                lambda: h.client.input_lock(original["input"]["locked"]),
            ):
                try:
                    action()
                except Exception as exc:
                    errors.append(str(exc))
            summary["cleanup_errors"] = errors
            summary["final_state"] = public_state(h.state())
            (ROOT / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
            print("RESTORED", json.dumps(summary["final_state"]), "errors", errors, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
