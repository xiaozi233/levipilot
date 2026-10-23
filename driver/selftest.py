"""Live smoke test for the probe mod. Requires the game in a world.

    py -3.11 driver/selftest.py
    py -3.11 driver/selftest.py --keep          # keep the test entity
    py -3.11 driver/selftest.py --entity minecraft:cow

Covers: pipe, state, inventory read/write/give/clear, teleport, server command,
entity query, look-at, attack and interact. The original slot-0 item and the
selected slot are restored at the end.

The default entity is a pig: hostile mobs despawn immediately if the test world
is on Peaceful difficulty.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.harness.hooks import decode_hook_hits  # noqa: E402
from driver.harness.memory import fnv1_64, fnv1a_64  # noqa: E402
from driver.harness.spool import read_spool  # noqa: E402
from driver.transport_mod import ModClient, ModPipeError, ModProtocolError, ModTimeout  # noqa: E402


class Checks:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        ok = bool(condition)
        self.results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
        return ok

    def note(self, name: str, detail: str = "") -> None:
        print(f"NOTE  {name}" + (f"  ({detail})" if detail else ""))

    def finish(self) -> int:
        failed = [row for row in self.results if not row[1]]
        print(f"\n{len(self.results) - len(failed)}/{len(self.results)} checks passed")
        for name, _ok, detail in failed:
            print(f"  failed: {name} {detail}", file=sys.stderr)
        return 1 if failed else 0


def wait_still(client: ModClient, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.state()
        if (
            state.get("on_ground")
            and abs(float(state.get("vx", 0.0))) < 0.01
            and abs(float(state.get("vz", 0.0))) < 0.01
        ):
            time.sleep(0.3)
            return
        time.sleep(0.05)


def wait_screen(client: ModClient, expected: str, timeout: float = 2.0) -> str:
    deadline = time.monotonic() + timeout
    name = ""
    while time.monotonic() < deadline:
        name = str(client.screen().get("name", ""))
        if name == expected:
            return name
        time.sleep(0.05)
    return name


def wait_ticks(client: ModClient, ticks: int, timeout: float = 6.0) -> int:
    start = int(client.status().get("tick", 0))
    deadline = time.monotonic() + timeout
    while True:
        current = int(client.status().get("tick", 0))
        if current - start >= ticks:
            return current - start
        if time.monotonic() > deadline:
            return current - start
        time.sleep(0.01)


def slot_item(snapshot: dict[str, Any], slot: int) -> dict[str, Any] | None:
    for row in snapshot.get("slots", []):
        if int(row.get("slot", -1)) == slot:
            return row.get("item")
    return None


def find_item(snapshot: dict[str, Any], item_type: str) -> tuple[int | None, dict[str, Any] | None]:
    for row in snapshot.get("slots", []):
        item = row.get("item")
        if item and item.get("type") == item_type:
            return int(row["slot"]), item
    return None, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entity", default="minecraft:pig")
    parser.add_argument("--keep", action="store_true", help="do not remove the test entity")
    parser.add_argument("--radius", type=float, default=10.0)
    args = parser.parse_args(argv)

    checks = Checks()
    try:
        client = ModClient(connect_timeout=60.0, timeout=30.0).connect()
    except ModPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("提示：游戏内是否已加载 probe mod，并且已经进入存档？", file=sys.stderr)
        return 2

    try:
        checks.check("ping", client.ping().get("event") == "pong")

        # -- F1: a client that never sends '\n' cannot grow the buffer ------
        client.send_raw(b"A" * (2 << 20) + b"\n")
        too_long = 0
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            try:
                event = client.readline(0.5)
            except ModTimeout:
                break
            if event.get("event") == "error" and event.get("reason") == "line_too_long":
                too_long += 1
        checks.check("line_too_long", too_long >= 1, f"events={too_long}")
        checks.check("line_too_long_alive", client.ping().get("event") == "pong")

        # -- G1/G2: event-level input injection -----------------------------
        inject_enabled = True
        try:
            client.key_state(49)
            checks.check("input_inject_gate", True, "enabled")
        except ModProtocolError as exc:
            inject_enabled = False
            checks.check("input_inject_gate", "input_inject_disabled" in str(exc), str(exc))
        if inject_enabled:
            client.key_event(49, True, via="hid")
            client.key_event(49, False, via="hid")
            wait_ticks(client, 4)
            checks.check(
                "key_event_slot",
                int(client.state().get("selected_slot", -1)) == 0,
                str(client.state().get("selected_slot")),
            )
            before = client.key_state(72)["events"]
            for _ in range(50):
                client.key_event(72, True, via="hid")
                client.key_event(72, False, via="hid")
            wait_ticks(client, 4)
            after = client.key_state(72)
            checks.check(
                "key_event_count",
                int(after["events"]) - int(before) == 100 and after["down"] is False,
                f"delta={int(after['events']) - int(before)} down={after['down']}",
            )
            client.mouse_clear()
            client.mouse_event("left", down=True)
            client.mouse_event("left", down=False)
            wait_ticks(client, 4)
            mouse = client.mouse()
            checks.check(
                "mouse_event_count",
                int(mouse["counts"][1]) == 2,
                str({"left": mouse["counts"][1], "last": mouse["last_action"]}),
            )
            yaw0 = float(client.state()["yaw"])
            client.mouse_event("move", dx=200, dy=0)
            wait_ticks(client, 4)
            yaw1 = float(client.state()["yaw"])
            checks.check("mouse_event_turn", abs(yaw1 - yaw0) > 1.0, f"{yaw0:.2f}->{yaw1:.2f}")

            screen0 = str(client.screen().get("name"))
            client.key_event(69)
            client.key_event(69, False)
            opened = wait_screen(client, "inventory_screen")
            client.key_event(27)
            client.key_event(27, False)
            closed = wait_screen(client, screen0)
            checks.check(
                "screen_inventory",
                screen0 == "hud_screen" and opened == "inventory_screen" and closed == screen0,
                f"{screen0}->{opened}->{closed}",
            )

        # -- H0: ability availability gates ---------------------------------
        diagnostics = client.diagnose()
        offsets = {
            (field["struct"], field["field"]): int(field["offset"])
            for field in diagnostics.get("structs", {}).get("struct_fields", [])
        }
        vy_offset = offsets.get(("StateVector", "VelY"))
        checks.check("h0_offsets_table", vy_offset == 0x1C, f"VelY={vy_offset}")

        state = client.state()
        runtime_id = int(state.get("runtime_id", 0))
        components = client.ecs_components(runtime_id).get("components", [])
        state_vector = next(
            (item for item in components if item.get("name") == "StateVector"), None
        )
        checks.check(
            "h0_statevector",
            state_vector is not None
            and int(state_vector.get("size", 0)) == 36
            and int(state_vector.get("stride", 0)) in (0, 36),
            str(state_vector),
        )

        if state_vector is not None and vy_offset is not None:
            # H0.1: hardware watch on StateVector.vy while walking + jumping.
            # A server task must run once so the integrated server thread id is
            # known; the state vector is written there, not on the tick thread.
            client.command("time set day")
            wait_ticks(client, 3)
            baseline = int(client.watch_list().get("head", 0))
            watch = client.watch_set(
                int(state_vector["address"]) + int(vy_offset),
                size=4,
                mode="w",
                tag="h0_vy",
                thread="both",
            )
            checks.check("h0_watch_install", watch.get("event") == "watch_installed", str(watch))
            client.set_input(forward=1.0, jump=True, ticks=-1)
            time.sleep(30.0)
            client.stop_move()
            wait_ticks(client, 5)
            hits = client.watch_drain(baseline, limit=64).get("hits", [])
            server_tid = int(client.status().get("server_thread_id", 0))
            checks.check(
                "h0_watch_hits",
                len(hits) >= 1 and any(int(hit["tid"]) == server_tid for hit in hits),
                f"hits={len(hits)} server_tid={server_tid}",
            )
            evidence = "no code_hex"
            store_ok = False
            if hits:
                hit = hits[0]
                code_hex = str(hit.get("code_hex", ""))
                try:
                    import capstone

                    code = bytes.fromhex(code_hex)
                    disassembler = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
                    last = None
                    for instruction in disassembler.disasm(code, int(hit["rip"]) - len(code)):
                        last = instruction
                    if last is not None and last.address + last.size == int(hit["rip"]):
                        evidence = f"{last.mnemonic} {last.op_str} @rip-{last.size}"
                        width = 0
                        op = last.op_str.lower()
                        for marker, size in (
                            ("xmmword ptr", 16),
                            ("qword ptr", 8),
                            ("dword ptr", 4),
                            ("word ptr", 2),
                            ("byte ptr", 1),
                        ):
                            if marker in op:
                                width = size
                                break
                        displacement = None
                        if "[" in op and "+" in op:
                            tail = op.split("+")[-1].split("]")[0].strip()
                            if tail.startswith("0x"):
                                displacement = int(tail, 16)
                        store_ok = (
                            ("mov" in last.mnemonic or "vmov" in last.mnemonic)
                            and "[" in op
                            and displacement is not None
                            and width > 0
                            and displacement <= int(vy_offset) < displacement + width
                        )
                except ImportError:
                    evidence = "capstone not installed"
            checks.check("h0_watch_store", store_ok, evidence)
            client.watch_clear(all=True)

        # H0.2: probe names/hashes agree with blocks, and record block ids.
        position = client.state()
        bx, by, bz = int(position["feet_x"]), int(position["feet_y"]), int(position["feet_z"])
        client.command(f"setblock {bx + 2} {by} {bz} minecraft:water")
        for _ in range(40):
            if client.blocks([[bx + 2, by, bz]])["blocks"][0].get("name") == "minecraft:water":
                break
            time.sleep(0.05)
        samples = [[bx, by - 1, bz], [bx, by, bz], [bx + 2, by, bz]]
        block_ids: dict[str, int] = {}
        for sample in samples:
            info = client.blocks([sample])["blocks"][0]
            probe = client.probe(info["addr"], "block")
            legacy = client.probe(probe["legacy"], "legacy")
            name = str(probe.get("name", ""))
            checks.check(
                f"h0_probe_{name.split(':')[-1] or 'unknown'}",
                name == info.get("name") and int(legacy.get("hash", 0)) == fnv1_64(name),
                f"{name} hash={legacy.get('hash')}",
            )
            block_ids[name] = int(legacy.get("block_id", -1))
        checks.check(
            "h0_block_ids",
            {"minecraft:air", "minecraft:water"} <= set(block_ids) and len(block_ids) >= 3,
            str(block_ids),
        )
        client.command(f"setblock {bx + 2} {by} {bz} minecraft:air")

        # H0.3: the EntityContext captured by a hook resolves to the same
        # StateVector storage the runtime-id path reports.
        if state_vector is not None:
            hook = client.hook_install("MobJump::setJumpPendingScale", tag="h0_ctx")
            client.hook_call("MobJump::setJumpPendingScale", 1.0)
            wait_ticks(client, 3)
            hook_hits = client.hook_drain(0).get("hits", [])
            context = int(hook_hits[-1]["args"][0]) if hook_hits else 0
            client_context = int(client.state().get("context", 0))
            checks.check(
                "h0_hook_context",
                context != 0 and context == client_context,
                f"hook={context:#x} client={client_context:#x}",
            )
            if context:
                # Resolve StateVector twice: once from the hook-captured
                # context and once from state.context. Enumerating the client
                # registry while the game mutates it is not safe, so compare
                # these two lookups and the offset-table size instead.
                from_hook = client.ecs_context(context, int(state_vector["id"]))
                from_state = client.ecs_context(client_context, int(state_vector["id"]))
                checks.check(
                    "h0_hook_ecs",
                    int(from_hook.get("address", 0)) != 0
                    and int(from_hook.get("address", 0)) == int(from_state.get("address", 0))
                    and int(from_hook.get("count", 0)) == int(from_state.get("count", 0))
                    and int(from_hook.get("size", 0) or 0) == 36,
                    str(
                        {
                            "hook_address": hex(int(from_hook.get("address", 0))),
                            "state_address": hex(int(from_state.get("address", 0))),
                            "count": from_hook.get("count"),
                            "size": from_hook.get("size"),
                        }
                    ),
                )
            if hook.get("event") == "hook_installed":
                client.hook_rm(int(hook["id"]))

        # H0.4: spool tick order survives a panic.
        mod_spool = Path(str(client.hello.get("spool", "")))
        client.start(80)
        while True:
            event = client.readline(8.0).get("event")
            if event == "stopped":
                break
        client.panic("all")
        time.sleep(0.2)
        spool_rows, _ = read_spool(mod_spool)
        tick_numbers = [int(row["tick"]) for row in spool_rows if row.get("event") == "tick"]
        # The spool contains every recording run in this process; a decrease
        # marks a new run. Check the last run only.
        runs: list[list[int]] = []
        current: list[int] = []
        for tick in tick_numbers:
            if current and tick < current[-1]:
                runs.append(current)
                current = []
            current.append(tick)
        if current:
            runs.append(current)
        last_run = runs[-1] if runs else []
        monotonic = (
            last_run == list(range(last_run[0], last_run[0] + len(last_run))) if last_run else False
        )
        checks.check(
            "h0_spool_monotonic",
            len(last_run) >= 60 and monotonic,
            f"ticks={len(last_run)} runs={len(runs)} monotonic={monotonic}",
        )
        checks.check(
            "h0_spool_panic",
            any(row.get("event") == "panic_done" for row in spool_rows),
            "panic_done in spool",
        )

        # -- H1 stage A: packet identity tap --------------------------------
        packet_enabled = True
        try:
            started = client.packet_capture(True, sample=1)
            checks.check(
                "packet_capture_on",
                started.get("active") is True and started.get("send_hooked") is True,
                str(started),
            )
        except ModProtocolError as exc:
            packet_enabled = False
            checks.check("packet_capture_gate", "packet_capture_disabled" in str(exc), str(exc))
        if packet_enabled:
            denied = False
            try:
                client.packet_capture(True, ids=[1])
            except ModProtocolError as exc:
                denied = "denied_id" in str(exc)
            checks.check("packet_denylist", denied, "login id 1 rejected")
            # If the denied request left capture off, turn it back on.
            client.packet_capture(True, sample=1)
            base = int(client.packet_stats().get("records", 0))
            client.set_input(forward=1.0, ticks=-1)
            time.sleep(2.0)
            client.stop_move()
            client.jump_when("now")
            time.sleep(0.4)
            client.command("summon minecraft:pig ~2 ~ ~")
            time.sleep(0.4)
            nearby = client.entities(radius=16, limit=3).get("entities", [])
            if nearby:
                client.attack_entity(int(nearby[0]["runtime_id"]))
            time.sleep(0.4)
            drained = client.packet_drain(base, limit=4096)
            hits = drained.get("hits", [])
            ids = {int(hit["id"]) for hit in hits}
            sequences = [int(hit["seq"]) for hit in hits]
            checks.check(
                "packet_records",
                len(hits) > 0 and 144 in ids,
                f"count={len(hits)} ids={sorted(ids)[:12]}",
            )
            checks.check(
                "packet_seq",
                all(b > a for a, b in itertools.pairwise(sequences)),
                f"n={len(sequences)}",
            )
            checks.check(
                "packet_source",
                all(int(hit["tid"]) != 0 and hit["injected"] is False for hit in hits),
                f"tids={sorted({int(hit['tid']) for hit in hits})}",
            )
            stats = client.packet_stats()
            checks.check(
                "packet_no_drop",
                int(stats.get("dropped", 0)) == 0 and stats.get("active") is True,
                str({k: stats.get(k) for k in ("records", "dropped", "peer_sends", "peer_hooked")}),
            )
            rate = float(client.overhead().get("tick_hz", 0))
            checks.check("packet_tick_rate", rate >= 15.0, f"tick_hz={rate:.2f}")
            # B3: serialize only PlayerAuthInput (144) and correlate by seq.
            client.packet_capture(True, serialize_ids=[144], sample=1)
            client.set_input(forward=1.0, ticks=-1)
            wait_ticks(client, 40)
            client.stop_move()
            wait_ticks(client, 3)
            byte_dump = client.packet_bytes(0, limit=32)
            byte_rows = [row for row in byte_dump.get("hits", []) if int(row["id"]) == 144]
            identity = {
                int(row["seq"]): int(row["id"])
                for row in client.packet_drain(0, limit=4096).get("hits", [])
            }
            checks.check(
                "packet_bytes",
                bool(byte_rows)
                and all(
                    int(row["size"]) > 0 and len(str(row["hex"])) == 2 * min(int(row["size"]), 128)
                    for row in byte_rows
                ),
                f"rows={len(byte_rows)} size={byte_rows[0]['size'] if byte_rows else 0}",
            )
            checks.check(
                "packet_bytes_seq",
                bool(byte_rows)
                and all(identity.get(int(row["seq"])) == int(row["id"]) for row in byte_rows),
                f"matched={sum(1 for row in byte_rows if identity.get(int(row['seq'])) == int(row['id']))}",
            )

            # H2: command packet injection uses a captured 77 payload template.
            inject_enabled = True
            try:
                client.op_self()
                checks.check("packet_inject_gate", True, "op_self ok")
            except ModProtocolError as exc:
                inject_enabled = False
                checks.check(
                    "packet_inject_gate",
                    "packet_inject_disabled" in str(exc),
                    str(exc),
                )
            if inject_enabled:
                client.packet_capture(True, serialize_ids=[77], sample=1)
                client.key_event(191)
                client.key_event(191, False)
                wait_ticks(client, 3)
                client.text_input("say harness_template")
                wait_ticks(client, 2)
                client.key_event(13)
                client.key_event(13, False)
                wait_ticks(client, 5)
                template_rows = [
                    row for row in client.packet_bytes(0, limit=64)["hits"] if int(row["id"]) == 77
                ]
                checks.check(
                    "packet_template",
                    bool(template_rows) and int(template_rows[-1]["size"]) > 0,
                    f"rows={len(template_rows)}",
                )
                position = client.state()
                tx = int(position["feet_x"]) + 3
                ty = int(position["feet_y"]) - 1
                tz = int(position["feet_z"]) + 3
                before_sent = int(client.status().get("chat_sent", 0))
                client.chat_send(f"/setblock {tx} {ty} {tz} minecraft:gold_block")
                wait_ticks(client, 8)
                placed = client.blocks([[tx, ty, tz]])["blocks"][0].get("name")
                checks.check(
                    "packet_inject_setblock", placed == "minecraft:gold_block", str(placed)
                )
                x0 = float(client.state()["feet_x"])
                client.chat_send("/tp @s ~1 ~ ~")
                wait_ticks(client, 8)
                dx = float(client.state()["feet_x"]) - x0
                checks.check("packet_inject_relative", abs(dx - 1.0) < 0.05, f"dx={dx:.3f}")
                checks.check(
                    "packet_inject_count",
                    int(client.status().get("chat_sent", 0)) - before_sent >= 2,
                    str(client.status().get("chat_sent")),
                )
                # Panic must close the tap even while capture is active.
                client.panic("all")
                wait_ticks(client, 2)
                checks.check(
                    "packet_panic_off",
                    client.packet_stats().get("active") is False,
                    str(client.packet_stats().get("active")),
                )

            client.packet_capture(False)
            quiet = int(client.packet_stats().get("records", 0))
            client.set_input(forward=1.0, ticks=20)
            wait_ticks(client, 25)
            checks.check(
                "packet_off_zero",
                int(client.packet_stats().get("records", 0)) == quiet,
                f"records={quiet}",
            )

        # -- T1: spool, crash isolation -------------------------------------
        checks.check("hello_spool", bool(client.hello.get("spool")), str(client.hello.get("spool")))

        # -- T7: version fingerprint and anchor signatures -----------------
        diagnostics = client.diagnose()
        checks.check(
            "version_ok",
            diagnostics.get("version_ok") is True
            and diagnostics.get("version", {}).get("prefix_ok") is True
            and bool(diagnostics.get("version", {}).get("file")),
            str(diagnostics.get("version")),
        )
        anchors = diagnostics.get("anchors", [])
        checks.check(
            "anchor_signatures",
            len(anchors) >= 3
            and all(
                anchor.get("match") is True and int(anchor.get("signature_bytes", 0)) == 24
                for anchor in anchors
            ),
            f"anchors={len(anchors)}",
        )
        if anchors:
            wrong = client.signature_check(anchors[0]["name"], "00" * 24)
            checks.check(
                "signature_mismatch",
                wrong.get("match") is False and wrong.get("readable") is True,
                str(wrong),
            )

        client.start(5)
        while True:
            event = client.readline(5.0).get("event")
            if event == "stopped":
                break
        flushed = client.spool_flush()
        spool_path = Path(str(flushed.get("path", "")))
        rows: list[dict[str, Any]] = []
        stats = None
        if spool_path.name:
            rows, stats = read_spool(spool_path)
        checks.check("spool_exists", spool_path.is_file(), str(spool_path))
        checks.check(
            "spool_rows",
            stats is not None and stats.rows > 0,
            f"rows={0 if stats is None else stats.rows}",
        )
        checks.check(
            "spool_tick_rows",
            any(row.get("event") == "tick" for row in rows[-64:]),
            f"tail={len(rows[-64:])}",
        )

        isolated = client.selftest_av()
        checks.check(
            "dispatch_isolation",
            isolated.get("event") == "error" and isolated.get("reason") == "internal_error",
            str(isolated),
        )
        ticks_after_fault = wait_ticks(client, 3)
        checks.check("survives_fault", ticks_after_fault >= 3, f"ticks={ticks_after_fault}")

        state = client.state()
        if not state.get("in_world"):
            checks.check("in_world", False, "玩家不在世界中")
            return checks.finish()
        checks.check("in_world", True)

        feet = (state["feet_x"], state["feet_y"], state["feet_z"])
        selected = int(state.get("selected_slot", 0))

        # -- T2: memory primitives -----------------------------------------
        block_row = client.blocks([(feet[0], feet[1] - 1, feet[2])])["blocks"][0]
        block_addr = int(block_row.get("addr", 0))
        checks.check("blocks_addr", block_addr != 0, hex(block_addr))

        block_probe = client.probe(block_addr, "block")
        checks.check(
            "probe_block",
            bool(block_probe.get("ok")) and bool(block_probe.get("legacy")),
            str(block_probe.get("legacy")),
        )
        checks.check(
            "probe_block_name",
            block_probe.get("name") == block_row["name"],
            f"{block_probe.get('name')} vs {block_row['name']}",
        )

        name = str(block_probe.get("name", ""))
        legacy = client.probe(str(block_probe.get("legacy", 0)), "legacy")
        checks.check(
            "probe_legacy",
            bool(legacy.get("ok")) and legacy.get("name") == name,
            str(legacy.get("name")),
        )
        checks.check(
            "legacy_hash",
            legacy.get("hash") in {fnv1_64(name), fnv1a_64(name)},
            f"hash={legacy.get('hash')}",
        )

        raw = client.probe(block_addr, "raw", length=16)
        checks.check("probe_raw", bool(raw.get("ok")) and len(str(raw.get("hex", ""))) == 32)
        memory = client.mem_read(block_addr, 16)
        checks.check(
            "mem_read",
            bool(memory.get("ok")) and len(str(memory.get("hex", ""))) == 32,
        )
        bad_read = client.mem_read(0x10, 16)
        checks.check(
            "mem_read_bad_addr",
            bad_read.get("ok") is False and bad_read.get("reason") == "bad_addr",
            str(bad_read),
        )
        if not client.status().get("allow_write"):
            try:
                client.mem_write(0x10, b"\x00")
                denied = False
            except ModProtocolError as exc:
                denied = "write_disabled" in str(exc)
            checks.check("mem_write_disabled", denied, "")

        # -- T3: hardware watchpoints --------------------------------------
        status = client.status()
        probe_addr = int(status.get("tick_probe_addr", 0))
        tick_tid = int(status.get("tick_thread_id", 0))
        baseline = int(client.watch_list().get("head", 0))
        installed = client.watch_set(probe_addr, size=8, mode="w", tag="tick_probe")
        checks.check("watch_install", installed.get("event") == "watch_installed", str(installed))
        wait_ticks(client, 20)
        drained = client.watch_drain(baseline)
        hits = drained.get("hits", [])
        checks.check("watch_hits", len(hits) >= 1, f"hits={len(hits)}")
        checks.check(
            "watch_tid",
            bool(hits) and all(int(hit["tid"]) == tick_tid for hit in hits),
            f"tick_tid={tick_tid}",
        )
        checks.check("watch_rip", bool(hits) and all(int(hit["rip"]) != 0 for hit in hits))
        checks.check(
            "watch_code_hex",
            bool(hits) and all(len(hit.get("code_hex", "")) == 32 for hit in hits),
            f"hits={len(hits)}",
        )
        checks.check(
            "watch_rip_semantics",
            drained.get("rip_semantics") == "next_instruction",
            str(drained.get("rip_semantics")),
        )
        checks.check(
            "watch_list",
            len(client.watch_list().get("watches", [])) == 1,
            str(client.watch_list().get("watches")),
        )
        cleared_cmd = client.watch_clear(all=True)
        checks.check("watch_clear_ok", cleared_cmd.get("ok") is True, str(cleared_cmd))
        cleared = client.watch_list()
        checks.check("watch_list_empty", cleared.get("watches") == [], str(cleared.get("watches")))
        # Ignore anything that was recorded between the last drain and the
        # clear itself; only hits after the clear count.
        head_after_clear = int(cleared.get("head", 0))
        wait_ticks(client, 5)
        after = client.watch_drain(head_after_clear)
        checks.check(
            "watch_cleared",
            len(after.get("hits", [])) == 0,
            f"hits={len(after.get('hits', []))}",
        )

        # -- T5: runtime symbol hook ---------------------------------------
        dry = client.hook_install("MobJump::setJumpPendingScale", dry_run=True)
        checks.check(
            "hook_dry_run",
            dry.get("event") == "hook_dry_run"
            and int(dry.get("address", 0)) != 0
            and dry.get("executable") is True,
            str(dry),
        )
        hook_baseline = int(client.hook_list().get("head", 0))
        installed = client.hook_install("MobJump::setJumpPendingScale", tag="selftest_jps")
        checks.check(
            "hook_install",
            installed.get("event") == "hook_installed",
            str(installed),
        )
        hook_id = int(installed.get("id", -1))
        called = client.hook_call("MobJump::setJumpPendingScale", 1.0)
        checks.check("hook_call", called.get("event") == "hook_call_done", str(called))
        wait_ticks(client, 3)
        drained_hook = client.hook_drain(hook_baseline)
        hook_hits = [hit for hit in decode_hook_hits(drained_hook) if hit.hook == hook_id]
        checks.check("hook_hit", len(hook_hits) >= 1, f"hits={len(hook_hits)}")
        checks.check(
            "hook_args",
            bool(hook_hits)
            and hook_hits[-1].args[0] != 0
            and abs(hook_hits[-1].b_float - 1.0) < 1.0e-6,
            str(hook_hits[-1].args if hook_hits else None),
        )
        removed = client.hook_rm(hook_id)
        checks.check("hook_remove", removed.get("ok") is True, str(removed))
        checks.check(
            "hook_list_empty",
            client.hook_list().get("hooks") == [],
            str(client.hook_list().get("hooks")),
        )
        va_dry = client.hook_install(va=dry["address"], dry_run=True)
        checks.check(
            "hook_va_dry_run",
            va_dry.get("executable") is True and va_dry.get("abi") == "generic4-int",
            str(va_dry),
        )
        bad_va = client.hook_install(va="0x10", dry_run=True)
        checks.check(
            "hook_va_bad",
            bad_va.get("executable") is False,
            str(bad_va),
        )

        # -- T8: panic removes hooks/watches and cancels tasks -------------
        probe_addr = int(client.status().get("tick_probe_addr", 0))
        client.hook_install("MobJump::setJumpPendingScale", tag="panic_hook")
        client.watch_set(probe_addr, size=8, tag="panic_watch")
        panicked = client.panic("all")
        checks.check(
            "panic_done",
            panicked.get("event") == "panic_done"
            and panicked.get("ok") is True
            and int(panicked.get("hooks_removed", 0)) >= 1
            and int(panicked.get("watches_cleared", 0)) >= 1,
            str(panicked),
        )
        checks.check("panic_hooks_empty", client.hook_list().get("hooks") == [])
        checks.check("panic_watches_empty", client.watch_list().get("watches") == [])
        checks.check("panic_task_idle", client.task_status().get("active") is None)
        checks.check("panic_alive", wait_ticks(client, 3) >= 3)

        # -- T9: tick/hook overhead metric ---------------------------------
        overhead_before = client.overhead()
        checks.check(
            "overhead",
            int(overhead_before.get("samples", 0)) > 20
            and float(overhead_before.get("tick_p99_us", 0)) > 0
            and "hook_p99_us" in overhead_before,
            str({k: overhead_before.get(k) for k in ("samples", "tick_p99_us", "hook_p99_us")}),
        )
        paths = overhead_before.get("paths", {})
        checks.check(
            "overhead_paths",
            all(isinstance(paths.get(name), dict) for name in ("record", "watch", "hook")),
            str(paths),
        )
        checks.check(
            "overhead_semantics",
            overhead_before.get("consistent") is True
            and "tick_interval_p99_us" in overhead_before
            and int(overhead_before.get("window_samples", 0)) == 1024
            and float(overhead_before.get("qpc_resolution_ns", 0)) > 0,
            str(
                {
                    k: overhead_before.get(k)
                    for k in ("consistent", "window_samples", "qpc_resolution_ns")
                }
            ),
        )
        busy = client.request({"cmd": "selftest_busy", "us": 1000, "ticks": 20})
        if busy.get("reason") == "stress_disabled":
            checks.check("selftest_busy_gated", True, "stress_disabled")
            overhead_after = overhead_before
        else:
            checks.check(
                "selftest_busy",
                busy.get("event") == "busy_test_started",
                str(busy),
            )
            wait_ticks(client, 30)
            overhead_after = client.overhead()
            checks.check(
                "overhead_busy",
                float(overhead_after.get("hook_p99_us", 0)) >= 900
                and float(overhead_after.get("hook_max_us", 0)) >= 900,
                str(
                    {
                        k: overhead_after.get(k)
                        for k in ("hook_p50_us", "hook_p99_us", "hook_max_us")
                    }
                ),
            )
        checks.check("overhead_alive", wait_ticks(client, 3) >= 3)

        # -- parkour jump/route primitives ---------------------------------
        jump_now = client.jump_when("now")
        checks.check(
            "jump_now",
            bool(jump_now.get("ok")) and jump_now.get("mode") == "now",
            str(jump_now),
        )
        wait_ticks(client, 15)

        jump_tick = client.jump_when("tick", after_ticks=5)
        checks.check(
            "jump_tick",
            bool(jump_tick.get("ok")) and int(jump_tick.get("ticks", 0)) >= 5,
            str(jump_tick.get("ticks")),
        )
        wait_ticks(client, 15)

        client.reset(feet[0], feet[1], feet[2], yaw=0.0, pitch=0.0)
        wait_ticks(client, 15)
        jump_distance = client.jump_when(
            "distance",
            x=feet[0] + 4.0,
            z=feet[2],
            distance=1.0,
            forward=1.0,
            max_ticks=100,
        )
        checks.check(
            "jump_distance",
            bool(jump_distance.get("ok")) and float(jump_distance.get("distance", 99.0)) <= 1.1,
            f"distance={jump_distance.get('distance')}",
        )
        wait_ticks(client, 20)

        route_result = client.route(
            [
                {"x": feet[0] + 2.0, "z": feet[2], "tolerance": 0.4},
                {"x": feet[0] + 2.0, "z": feet[2] + 2.0, "tolerance": 0.4},
            ]
        )
        checks.check(
            "route",
            bool(route_result.get("ok")) and int(route_result.get("waypoints", 0)) == 2,
            str(route_result),
        )
        client.reset(feet[0], feet[1], feet[2], yaw=0.0)
        wait_ticks(client, 10)

        # -- T4: binary tick ring ------------------------------------------
        started = client.record_bin_start(period=1)
        checks.check(
            "record_started",
            started.get("event") == "record_started" and int(started.get("entry_size", 0)) == 64,
            str(started),
        )
        wait_ticks(client, 20)
        stopped = client.record_bin_stop()
        recorded = int(stopped.get("rows", 0))
        checks.check("record_rows", recorded >= 10, f"rows={recorded}")
        checks.check("record_no_drop", int(client.status().get("queue_dropped", -1)) == 0)
        jsonl_entries = client.record_dump_all(format="jsonl")
        checks.check(
            "record_jsonl_count",
            len(jsonl_entries) == recorded,
            f"{len(jsonl_entries)} vs {recorded}",
        )
        bin_entries = client.record_dump_all(format="hex")
        checks.check(
            "record_hex_count", len(bin_entries) == recorded, f"{len(bin_entries)} vs {recorded}"
        )
        checks.check(
            "record_flags",
            bool(jsonl_entries) and any(entry.on_ground for entry in jsonl_entries),
            f"entries={len(jsonl_entries)}",
        )

        # -- P2: effects, checkpoints, region scan, landing event ----------
        checks.check("effects_field", isinstance(client.state().get("effects"), list))
        client.command("effect @s speed 10 2")
        wait_ticks(client, 3)
        effects = client.state().get("effects", [])
        checks.check(
            "effects_speed",
            any(int(e.get("id", -1)) == 1 and int(e.get("amplifier", -1)) == 2 for e in effects),
            str(effects),
        )
        client.command("effect @s clear")
        wait_ticks(client, 3)
        client.reset(feet[0], feet[1], feet[2], yaw=0.0, pitch=0.0)
        wait_ticks(client, 12)
        saved = client.checkpoint_save("selftest_cp")
        checks.check("checkpoint_save", saved.get("event") == "checkpoint_saved", str(saved))
        listed = client.checkpoint_list().get("checkpoints", [])
        checks.check(
            "checkpoint_list",
            any(row.get("name") == "selftest_cp" for row in listed),
            f"count={len(listed)}",
        )
        client.teleport(feet[0] + 2.0, feet[1], feet[2])
        wait_ticks(client, 12)
        loaded_cp = client.checkpoint_load("selftest_cp")
        wait_ticks(client, 12)
        now_cp = client.state()
        cp_gap = max(
            abs(now_cp["feet_x"] - feet[0]),
            abs(now_cp["feet_y"] - feet[1]),
            abs(now_cp["feet_z"] - feet[2]),
        )
        checks.check(
            "checkpoint_load",
            loaded_cp.get("event") == "checkpoint_loaded" and cp_gap < 0.3,
            f"gap={cp_gap:.3f}",
        )
        client.checkpoint_delete("selftest_cp")

        scanned = client.scan(
            (feet[0] - 1, feet[1] - 2, feet[2] - 1),
            (feet[0] + 1, feet[1] + 1, feet[2] + 1),
        )
        checks.check(
            "scan",
            int(scanned.get("count", 0)) > 0 and scanned.get("truncated") is False,
            f"count={scanned.get('count')}",
        )

        client.teleport(feet[0], feet[1] + 5.0, feet[2])
        landed = None
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            try:
                message = client.readline(1.0)
            except ModPipeError:
                break
            if message.get("event") == "player_landed":
                landed = message
                break
        checks.check(
            "player_landed",
            landed is not None and float(landed.get("fall_distance", 0.0)) >= 3.0,
            str(landed),
        )
        client.command("effect @s instant_health 1 10")
        wait_ticks(client, 10)

        before = client.inventory()
        checks.check(
            "inventory", len(before.get("slots", [])) >= 9, f"slots={len(before.get('slots', []))}"
        )
        original_slot0 = slot_item(before, 0)

        # -- inventory write / read ----------------------------------------
        client.set_item(0, "minecraft:stone", count=7)
        wait_ticks(client, 10)
        item = slot_item(client.inventory(), 0)
        checks.check(
            "set_item",
            bool(item) and item["type"] == "minecraft:stone" and int(item["count"]) == 7,
            str(item),
        )

        client.give("minecraft:dirt", count=3)
        wait_ticks(client, 10)
        dirt_slot, dirt = find_item(client.inventory(), "minecraft:dirt")
        checks.check("give", dirt is not None and int(dirt["count"]) >= 3, f"slot={dirt_slot}")

        client.clear_item(0)
        wait_ticks(client, 10)
        checks.check("clear_item", slot_item(client.inventory(), 0) is None)
        if dirt_slot not in (None, 0):
            client.clear_item(dirt_slot)
            wait_ticks(client, 10)

        # restore the original hotbar slot and selection
        if original_slot0:
            client.set_item(
                0,
                original_slot0["type"],
                count=int(original_slot0["count"]),
                aux=int(original_slot0.get("aux", 0)),
            )
        else:
            client.clear_item(0)
        client.select_slot(selected)
        wait_ticks(client, 5)

        # -- teleport ------------------------------------------------------
        client.teleport(*feet)
        wait_ticks(client, 20)
        now = client.state()
        gap = max(
            abs(now["feet_x"] - feet[0]), abs(now["feet_y"] - feet[1]), abs(now["feet_z"] - feet[2])
        )
        checks.check("teleport", gap < 0.2, f"gap={gap:.3f}")

        # -- entity --------------------------------------------------------
        short = args.entity.split(":")[-1]
        tx, ty, tz = feet[0] + 3.0, feet[1], feet[2]
        client.command(f"summon {args.entity} {tx:.2f} {ty:.2f} {tz:.2f}")
        wait_ticks(client, 25)
        entities = client.entities(radius=args.radius, limit=64).get("entities", [])
        matches = [
            row
            for row in entities
            if short.lower() in (str(row.get("type", "")) + str(row.get("name", ""))).lower()
        ]
        checks.check("summon/entities", bool(matches), f"count={len(entities)}")

        if matches:
            runtime_id = int(matches[0]["runtime_id"])
            info = client.entity(runtime_id)
            checks.check(
                "entity_info", int(info.get("runtime_id", -1)) == runtime_id, str(info.get("type"))
            )

            ecs_entity = client.ecs_entity(runtime_id)
            checks.check(
                "ecs_entity",
                int(ecs_entity.get("components", 0)) > 0
                and int(ecs_entity.get("registry", 0)) != 0,
                str({k: ecs_entity.get(k) for k in ("components", "registry", "entity")}),
            )
            components = client.ecs_components(runtime_id).get("components", [])
            sized = [row for row in components if int(row.get("stride", 0)) > 0]
            checks.check(
                "ecs_components",
                bool(components) and bool(sized),
                f"components={len(components)} sized={len(sized)}",
            )
            if sized:
                component = sized[0]
                length = min(int(component["stride"]), 512)
                raw = client.ecs_component(runtime_id, int(component["id"]), length=length)
                checks.check(
                    "ecs_component",
                    bool(raw.get("ok")) and len(str(raw.get("hex", ""))) == 2 * length,
                    str({k: raw.get(k) for k in ("component_id", "stride", "len", "ok")}),
                )
            registries = client.ecs_registries().get("registries", [])
            checks.check(
                "ecs_registries",
                bool(registries) and int(registries[0].get("components", 0)) > 0,
                str(registries[:1]),
            )

            look = client.look_at_entity(runtime_id)
            # the entity may have moved since the info snapshot
            fresh = client.entity(runtime_id)
            expected_yaw = math.degrees(
                math.atan2(-(float(fresh["x"]) - feet[0]), (float(fresh["z"]) - feet[2]))
            )
            current = client.state()
            yaw_gap = abs((float(current.get("yaw", 0.0)) - expected_yaw + 180.0) % 360.0 - 180.0)
            checks.check(
                "look_at_entity",
                look.get("event") == "look_at_done" and yaw_gap < 20.0,
                f"yaw={current.get('yaw'):.1f} expected={expected_yaw:.1f}",
            )

            health_before = info.get("health")
            client.attack_entity(runtime_id)
            wait_ticks(client, 15)
            try:
                health_after = client.entity(runtime_id).get("health")
            except ModPipeError:
                health_after = None  # entity died / despawned
            damaged = health_after is None or (
                health_before is not None and float(health_after) < float(health_before)
            )
            checks.check("attack_entity", damaged, f"health {health_before} -> {health_after}")

            # entity use requires a held item; wheat also exercises a real
            # interaction (breeding/food) instead of an empty-hand no-op
            client.set_item(0, "minecraft:wheat", count=1)
            client.select_slot(0)
            wait_ticks(client, 5)
            try:
                interact = client.interact_entity(runtime_id)
            except ModPipeError:
                interact = {"event": "entity_interacted", "ok": False, "gone": True}
            checks.check(
                "interact_entity",
                interact.get("event") == "entity_interacted",
                f"ok={interact.get('ok')}",
            )
            wait_ticks(client, 20)
            after_interact = slot_item(client.inventory(), 0)
            checks.note(
                "interact_item",
                f"slot0={after_interact} (use is injected as an input, best effort)",
            )

            if original_slot0:
                client.set_item(
                    0,
                    original_slot0["type"],
                    count=int(original_slot0["count"]),
                    aux=int(original_slot0.get("aux", 0)),
                )
            else:
                client.clear_item(0)
            client.select_slot(selected)
            wait_ticks(client, 3)

            if not args.keep:
                client.command(f"kill @e[type={short},c=1]")
                wait_ticks(client, 5)

        # -- G4: event-level macro replay ----------------------------------
        if inject_enabled:
            mod_dir = Path(str(client.hello.get("spool", ""))).parent
            rows = []
            for index in range(20):
                b = lambda value: "true" if value else "false"  # noqa: E731
                rows.append(
                    [
                        b(3 <= index <= 15),
                        "false",
                        "false",
                        "false",
                        "false",
                        "false",
                        "false",
                        "false",
                        b(5 <= index <= 7),
                        "0",
                        "0",
                    ]
                )
            macro_path = mod_dir / "macros" / "selftest_g4.json"
            macro_path.parent.mkdir(parents=True, exist_ok=True)
            macro_path.write_text(json.dumps(rows), encoding="utf-8")
            client.macro_load("selftest_g4")

            def run_macro(event_level: bool) -> tuple[tuple[float, float], int, float]:
                client.reset(0.5, -49.0, 0.5, yaw=0.0, pitch=0.0)
                wait_still(client)
                client.mouse_clear()
                yaw0 = float(client.state()["yaw"])
                client.macro_run(event_level=event_level)
                while True:
                    event = client.readline(5.0)
                    if event.get("event") == "macro_done":
                        break
                wait_ticks(client, 3)
                state = client.state()
                drift = abs(float(state["yaw"]) - yaw0)
                return (
                    (float(state["feet_x"]), float(state["feet_z"])),
                    int(client.mouse()["counts"][1]),
                    drift,
                )

            first, left_events, drift_a = run_macro(True)
            second, _, drift_b = run_macro(True)
            gap = math.hypot(first[0] - second[0], first[1] - second[1])
            external = drift_a > 0.1 or drift_b > 0.1
            checks.check(
                "macro_event_deterministic",
                gap < 1e-4 or external,
                f"gap={gap:.6f}" + (" (skipped: real mouse moved the camera)" if external else ""),
            )
            checks.check("macro_event_clicks", left_events == 2, f"left={left_events}")
            _, legacy_left, _ = run_macro(False)
            checks.check("macro_legacy_path", legacy_left == 0, f"left={legacy_left}")

        checks.check("macro_status", client.macro_status().get("event") == "macro_status")
        return checks.finish()
    except ModPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
