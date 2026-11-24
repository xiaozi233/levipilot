"""The default full live suite, separate from CLI parsing and mode dispatch."""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import math
import os
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from driver import selftest_diagnostics
from driver.harness import (
    ModClient,
    ModPipeError,
    ModProtocolError,
    ModTimeout,
)
from driver.harness.hooks import decode_hook_hits
from driver.harness.lab import double_space_fly, settle, swim_drive, swim_stop
from driver.harness.memory import fnv1_64, fnv1a_64
from driver.harness.spool import read_spool
from driver.selftest_core import (
    check_block_identity,
    check_cli_compatibility,
    check_dispatch_compatibility,
    check_overhead_window,
)
from driver.selftest_input import (
    check_controls,
    check_flight,
    check_input_events,
    check_input_mode_contract,
    check_macro_playback,
    check_player_identity,
    check_teleport,
)
from driver.selftest_macro import run_macro_capture, run_macro_record_limits
from driver.selftest_packets import check_packet_injection
from driver.selftest_queries import check_ecs_capture, check_query_capture
from driver.selftest_recovery import check_lifecycle_stress, check_request_recovery
from driver.selftest_runtime import (
    SWIM_X,
    SWIM_Y,
    SWIM_Z,
    WorldLost,
    find_item,
    prepare_swim_fixture,
    slot_item,
    wait_screen,
    wait_still,
)
from driver.selftest_sdk import check_sdk_boundaries
from driver.selftest_streams import (
    check_binary_capture,
    check_jump_motion,
    check_record_medium,
    check_tick_recovery,
    check_tick_safety,
    check_tick_streams,
)
from driver.selftest_support import Checks, ViolationObservation
from driver.selftest_tasks import check_task_lifecycle, check_task_operations


@dataclass
class DefaultSuiteContext:
    """Shared state passed across modular sections of the default live suite."""

    client: ModClient
    checks: Checks
    args: argparse.Namespace
    observation: ViolationObservation
    require_world: Any
    wait_ticks: Any
    dump_packet_tap: Any
    tap_dump: str | None = None
    inject_enabled: bool | None = None
    feet: tuple[float, float, float] | None = None
    selected_slot: int | None = None
    macro_path: Path | None = None
    aborted: bool = False

    def ensure_inject_enabled(self) -> bool:
        if self.inject_enabled is None:
            try:
                self.client.key_state(49)
                self.inject_enabled = True
            except ModProtocolError:
                self.inject_enabled = False
        return self.inject_enabled

    def ensure_pose(self) -> tuple[tuple[float, float, float], int]:
        if self.feet is None or self.selected_slot is None:
            state = self.client.state()
            self.feet = (
                float(state["feet_x"]),
                float(state["feet_y"]),
                float(state["feet_z"]),
            )
            self.selected_slot = int(state.get("selected_slot", 0))
        return self.feet, self.selected_slot


def _section_preflight(ctx: DefaultSuiteContext) -> None:
    client = ctx.client
    checks = ctx.checks
    args = ctx.args
    observation = ctx.observation
    wait_ticks = ctx.wait_ticks
    ctx.require_world(client, "selftest preflight")
    check_sdk_boundaries(client, checks)
    # Scripts own the session: drop real hardware keys/mouse so a person at
    # the machine cannot perturb the run. Injected events still pass; use
    # `harness input-lock --off` (or input_lock(false)) to release.
    try:
        auto_lock = client.input_lock(True)
        checks.note("input_lock_auto", f"on={auto_lock.get('on')}")
    except (ModPipeError, ModProtocolError) as exc:
        checks.note("input_lock_auto", f"unavailable: {exc}")
    checks.check("ping", client.ping().get("event") == "pong")
    if ctx.tap_dump:
        # Diagnostic mode: keep the packet-identity ring on for the whole
        # run so a disconnect still has the last packets before it.
        try:
            client.packet_capture(True, sample=1, directions=["send", "recv"])
            checks.note("packet_tap_all", f"on -> {ctx.tap_dump}")
        except (ModPipeError, ModProtocolError) as exc:
            checks.note("packet_tap_all", f"unavailable: {exc}")
    if args.stress_cycles:
        check_lifecycle_stress(client, checks, args.stress_cycles, args.stress_world)
    client.command('tellraw @s {"rawtext":[{"text":"LeviPilot chat snapshot selftest"}]}')
    wait_ticks(client, 3)
    chat = client.chat_history(limit=4)
    checks.check(
        "chat_history_snapshot",
        chat.get("ok")
        and len(chat.get("messages", [])) <= 4
        and all(isinstance(row.get("text"), str) for row in chat.get("messages", []))
        and any(
            "LeviPilot chat snapshot selftest" in row["text"] for row in chat.get("messages", [])
        ),
        str(chat),
    )
    delayed_chat = client.request({"cmd": "selftest_chat_timeout"}, ("chat_history", "error"))
    if delayed_chat.get("event") == "error":
        checks.check("chat_timeout_gate", delayed_chat.get("reason") == "selftest_stress_disabled")
        checks.skip("chat_timeout_recovery", "allow_selftest_stress is disabled")
    else:
        checks.check(
            "chat_timeout_terminal",
            delayed_chat.get("ok") is False
            and delayed_chat.get("reason") == "chat_history_timeout",
            str(delayed_chat),
        )
        recovered_chat = client.chat_history(limit=1)
        checks.check("chat_timeout_recovery", recovered_chat.get("ok") is True, str(recovered_chat))
    check_dispatch_compatibility(client, checks)
    check_request_recovery(client, checks)
    check_task_lifecycle(client, checks)
    check_query_capture(client, checks)
    run_macro_record_limits(client, checks, wait_ticks)
    run_macro_capture(client, checks, None, wait_ticks, settle)
    check_tick_safety(client, checks)
    check_tick_streams(client, checks)
    check_binary_capture(client, checks)
    check_macro_playback(client, checks)
    check_ecs_capture(client, checks)
    check_input_events(client, checks)
    check_flight(client, checks)
    check_player_identity(client, checks)
    check_controls(client, checks)
    check_teleport(client, checks)
    check_tick_recovery(client, checks)
    check_packet_injection(client, checks)
    with observation.gap("violation_selftest"):
        selftest_diagnostics.check_violation_watch(client, checks, wait_ticks)
    selftest_diagnostics.check_send_probe(client, checks, wait_ticks)
    selftest_diagnostics.check_packet_tap(client, checks, wait_ticks)
    check_task_operations(client, checks)
    check_cli_compatibility(client, checks)


def _section_line_framing(ctx: DefaultSuiteContext) -> None:
    client = ctx.client
    checks = ctx.checks
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


def _section_input_injection(ctx: DefaultSuiteContext) -> None:
    client = ctx.client
    checks = ctx.checks
    wait_ticks = ctx.wait_ticks
    # -- G1/G2: event-level input injection -----------------------------
    inject_enabled = True
    try:
        client.key_state(49)
        checks.check("input_inject_gate", True, "enabled")
    except ModProtocolError as exc:
        inject_enabled = False
        checks.check("input_inject_gate", "input_inject_disabled" in str(exc), str(exc))
        checks.skip("input_injection_coverage", str(exc))
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

        # -- input lock: hardware keys stop, injected events keep working --
        slot_before = int(client.state().get("selected_slot", -1))
        lock = client.input_lock(True)
        checks.check(
            "input_lock_on",
            bool(lock.get("on")) and client.state().get("input", {}).get("locked") is True,
            f"{lock} state.locked={client.state().get('input', {}).get('locked')}",
        )
        try:
            # ``raw`` is a genuine Hardware key with no injection guard; it
            # is the only way to exercise the real-press branch of the lock
            # (``hardware`` is a scripted press and passes on purpose, which
            # is what makes double-space flight reproducible under the lock).
            client.key_event(50, True, via="raw")  # '2'
            client.key_event(50, False, via="raw")
            wait_ticks(client, 6)
            after_hw = int(client.state().get("selected_slot", -1))
            client.key_event(51, True, via="hid")  # '3'
            client.key_event(51, False, via="hid")
            wait_ticks(client, 6)
            after_sw = int(client.state().get("selected_slot", -1))
            checks.check(
                "input_lock_blocks_hardware",
                after_hw == slot_before,
                f"slot {slot_before} -> {after_hw}",
            )
            checks.check(
                "input_lock_allows_injection",
                after_sw != slot_before,
                f"slot {slot_before} -> {after_sw}",
            )
            injected_before = int(client.mouse()["counts"][1])
            client.mouse_event("left", down=True)
            client.mouse_event("left", down=False)
            wait_ticks(client, 4)
            injected_after = int(client.mouse()["counts"][1])
            checks.check(
                "input_lock_allows_mouse_injection",
                injected_after > injected_before,
                f"{injected_before} -> {injected_after}",
            )
        finally:
            # Restore the run-wide lock (the block's checks needed the
            # toggle) and the hotbar selection.
            client.input_lock(True)
            client.select_slot(slot_before)
            wait_ticks(client, 3)
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

    ctx.inject_enabled = inject_enabled


def _section_abilities(ctx: DefaultSuiteContext) -> None:
    client = ctx.client
    checks = ctx.checks
    observation = ctx.observation
    wait_ticks = ctx.wait_ticks
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
    state_vector = next((item for item in components if item.get("name") == "StateVector"), None)
    checks.check(
        "h0_statevector",
        state_vector is not None
        and int(state_vector.get("size", 0)) == 36
        and int(state_vector.get("stride", 0)) in (0, 36),
        str(state_vector),
    )

    if state_vector is not None and vy_offset is not None:
        # H0.1: identify the vy writer with one stationary jump. This is
        # thread forensics; jump physics are checked separately below.
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
        try:
            client.jump_when("now")
            wait_ticks(client, 25)
            hits = client.watch_drain(baseline, limit=64).get("hits", [])
        finally:
            client.stop_move()
            client.watch_clear(all=True)
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

    check_block_identity(client, checks)

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

    # H0.5: the in-detour memory snapshot reads a pointer argument while the
    # call is live. Capture 8 bytes at args[0]+0 and compare with mem_read at
    # the address the mod recorded, so a stale/failed copy cannot pass.
    mem_hook = client.hook_install(
        "MobJump::setJumpPendingScale",
        tag="h0_mem",
        mem_arg=0,
        mem_offset=0,
        mem_size=8,
    )
    if mem_hook.get("event") == "hook_installed":
        client.hook_call("MobJump::setJumpPendingScale", 1.0)
        wait_ticks(client, 3)
        mem_hits = [
            hit for hit in decode_hook_hits(client.hook_drain(0)) if hit.hook == int(mem_hook["id"])
        ]
        latest = mem_hits[-1] if mem_hits else None
        detail: dict[str, object] = {"hits": len(mem_hits)}
        ok = False
        if latest is not None and latest.mem_ok and len(latest.mem) == 8:
            direct = client.mem_read(latest.mem_addr, 8)
            detail.update(
                {
                    "mem_addr": hex(latest.mem_addr),
                    "hook_hex": latest.mem.hex(),
                    "read_hex": direct.get("hex"),
                    "mem_arg": latest.mem_arg,
                }
            )
            ok = direct.get("hex") == latest.mem.hex()
        checks.check("h0_hook_mem", ok, str(detail))
        client.hook_rm(int(mem_hook["id"]))
    else:
        checks.check("h0_hook_mem", False, str(mem_hook))

    # H0.4: spool tick order survives a panic.
    mod_spool = Path(str(client.hello.get("spool", "")))
    client.start(80)
    while True:
        event = client.readline(8.0).get("event")
        if event == "stopped":
            break
    with observation.gap("spool_panic"):
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


def _section_packets(ctx: DefaultSuiteContext) -> None:
    client = ctx.client
    checks = ctx.checks
    observation = ctx.observation
    wait_ticks = ctx.wait_ticks
    # The checks below walk the player to force movement packets. The pose is
    # restored at the end because `memory_watch` captures the run-wide pose
    # from the live state, and later jump arcs require headroom there.
    section_pose = client.state()
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
        checks.skip("packet_capture_coverage", str(exc))
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

        # H1-C: recv-side tap. Server -> client traffic goes through the
        # loopback sender; the Packet* helper hooks measured zero here.
        client.command("summon minecraft:pig ~2 ~ ~")
        wait_ticks(client, 10)
        recv_started = client.packet_capture(
            True, directions=["send", "recv"], sample=1, serialize_ids=[111]
        )
        checks.check(
            "packet_recv_on",
            recv_started.get("recv_hooked") is True
            and recv_started.get("send_hooked") is True
            and all(int(code) == 0 for code in recv_started.get("recv_anchors", {}).values()),
            str(recv_started.get("recv_anchors")),
        )
        recv_base = int(client.packet_stats().get("records", 0))
        time.sleep(2.5)
        recv_drained = client.packet_drain(recv_base, limit=8192)
        recv_hits = [hit for hit in recv_drained.get("hits", []) if hit.get("dir") == "recv"]
        recv_ids = {int(hit["id"]) for hit in recv_hits}
        recv_stats = client.packet_stats()
        checks.check(
            "packet_recv_records",
            len(recv_hits) > 0 and all(int(hit["tid"]) != 0 for hit in recv_hits),
            f"count={len(recv_hits)} ids={sorted(recv_ids)[:8]}",
        )
        checks.check(
            "packet_recv_stats",
            int(recv_stats.get("recv_records", 0)) > 0
            and int(recv_stats.get("recv_hooks", 0)) == 9
            and int(
                recv_stats.get("recv_anchors", {}).get("loopback_sendToClient", {}).get("hits", 0)
            )
            > 0,
            str({k: recv_stats.get(k) for k in ("recv_records", "recv_hooks")}),
        )
        recv_bytes = [
            row
            for row in client.packet_bytes(0, limit=512).get("hits", [])
            if row.get("dir") == "recv"
        ]
        checks.check(
            "packet_recv_bytes",
            bool(recv_bytes) and all(int(row["size"]) > 0 for row in recv_bytes),
            f"rows={len(recv_bytes)} ids={sorted({int(r['id']) for r in recv_bytes})[:6]}",
        )
        checks.check(
            "packet_recv_dir_field",
            all(hit.get("dir") in ("send", "recv") for hit in recv_drained.get("hits", [])),
            "every row carries dir",
        )

        # H2: command packet injection uses a captured 77 payload template.
        packet_inject_enabled = True
        try:
            client.op_self()
            checks.check("packet_inject_gate", True, "op_self ok")
        except ModProtocolError as exc:
            packet_inject_enabled = False
            checks.check(
                "packet_inject_gate",
                "packet_inject_disabled" in str(exc),
                str(exc),
            )
            checks.skip("packet_injection_coverage", str(exc))
        if packet_inject_enabled:
            client.packet_capture(True, serialize_ids=[77], sample=1)
            client.key_event(191)
            client.key_event(191, False)
            opened = wait_screen(client, "chat_screen")
            checks.check("packet_chat_open", opened == "chat_screen", opened)
            client.text_input("say harness_template")
            # The chat UI consumes HID text asynchronously after the game
            # tick; allow it to apply the text before injecting Enter.
            wait_ticks(client, 10)
            client.key_event(13)
            client.key_event(13, False)
            closed = wait_screen(client, "hud_screen")
            checks.check("packet_chat_closed", closed == "hud_screen", closed)
            if closed == "chat_screen":
                # A failed chat submission must not block later movement
                # tests. Keep the packet assertions below as failures.
                client.key_event(27)
                client.key_event(27, False)
                wait_screen(client, "hud_screen")
            template_rows = [
                row for row in client.packet_bytes(0, limit=64)["hits"] if int(row["id"]) == 77
            ]
            checks.check(
                "packet_template",
                bool(template_rows) and int(template_rows[-1]["size"]) > 0,
                f"rows={len(template_rows)}",
            )
            position = client.state()
            # A previous run left this test's gold block behind, so checking
            # only its final type could pass even when /setblock failed.
            tx = math.floor(float(position["feet_x"]))
            ty = math.floor(float(position["feet_y"]))
            tz = math.floor(float(position["feet_z"]))
            candidates = [[tx + dx, ty, tz + dz] for dx, dz in ((3, 3), (-3, 3), (3, -3), (-3, -3))]
            blocks = client.blocks(candidates)["blocks"]
            target = next(
                (
                    cell
                    for cell, block in zip(candidates, blocks, strict=True)
                    if block.get("name") == "minecraft:air"
                ),
                None,
            )
            checks.check(
                "packet_inject_setblock_precondition",
                target is not None,
                "requires air so an unchanged block cannot pass",
            )
            before_sent = int(client.status().get("chat_sent", 0))
            if target is not None:
                tx, ty, tz = target
                try:
                    client.chat_send(f"/setblock {tx} {ty} {tz} minecraft:gold_block")
                    wait_ticks(client, 8)
                    placed = client.blocks([target])["blocks"][0].get("name")
                    checks.check(
                        "packet_inject_setblock", placed == "minecraft:gold_block", str(placed)
                    )
                finally:
                    if client.blocks([target])["blocks"][0].get("name") != "minecraft:air":
                        client.command(f"setblock {tx} {ty} {tz} air")
                        wait_ticks(client, 5)
                    checks.check(
                        "packet_inject_setblock_restored",
                        client.blocks([target])["blocks"][0].get("name") == "minecraft:air",
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
            with observation.gap("packet_panic"):
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

    client.teleport(
        float(section_pose["feet_x"]),
        float(section_pose["feet_y"]),
        float(section_pose["feet_z"]),
        yaw=float(section_pose.get("yaw", 0.0)),
        pitch=float(section_pose.get("pitch", 0.0)),
    )
    wait_ticks(client, 8)


def _section_version_diagnostics(ctx: DefaultSuiteContext) -> None:
    client = ctx.client
    checks = ctx.checks
    wait_ticks = ctx.wait_ticks
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

    # -- porting: profiles are data and dumpable from the live build -----
    status = client.status()
    profile = status.get("profile", {})
    checks.check(
        "profile_selected",
        profile.get("profile_found") is True
        and profile.get("match") in ("exact", "prefix", "version")
        and int(profile.get("anchors_configured", 0)) == len(anchors)
        and int(profile.get("anchors_matched", 0)) == len(anchors),
        str(profile),
    )
    capabilities = status.get("capabilities", {})
    checks.check(
        "profile_capabilities",
        status.get("hooks_allowed") is True
        and capabilities.get("hooks") is True
        and capabilities.get("offsets") is True
        and capabilities.get("profile") is True,
        str(capabilities),
    )
    checks.check(
        "compat_default_off",
        status.get("compat") is False and status.get("version_ok") is True,
        f"compat={status.get('compat')} version_ok={status.get('version_ok')}",
    )
    checks.check(
        "hello_profile",
        client.hello.get("hooks_allowed") is True
        and client.hello.get("profile", {}).get("profile_found") is True,
        str(client.hello.get("profile")),
    )
    dump = client.profile_dump()
    saveable = dump.get("profile", {})
    checks.check(
        "profile_dump",
        dump.get("build_id") == status.get("build_id")
        and saveable.get("runtime") is True
        and saveable.get("build_id") == status.get("build_id")
        and len(saveable.get("anchors", {})) == len(anchors)
        and all(
            len(str(entry.get("expected_hex", ""))) == 48
            for entry in saveable.get("anchors", {}).values()
        )
        and "StateVector" in saveable.get("structs", {}),
        f"anchors={len(saveable.get('anchors', {}))}",
    )
    exact = client.profile_probe(str(status.get("build_id", "")))
    near = client.profile_probe("1.26.51.7")
    unknown = client.profile_probe("9.9.9.9")
    checks.check(
        "profile_probe",
        exact.get("match") == "exact"
        and exact.get("profile_found") is True
        and near.get("match") == "prefix"
        and near.get("profile_found") is True
        and unknown.get("profile_found") is False
        and unknown.get("match") == "none",
        f"exact={exact.get('match')} near={near.get('match')} unknown={unknown.get('match')}",
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
        ctx.aborted = True
        return
    checks.check("in_world", True)

    feet = (state["feet_x"], state["feet_y"], state["feet_z"])
    selected = int(state.get("selected_slot", 0))

    ctx.feet = feet
    ctx.selected_slot = selected


def _section_memory_watch(ctx: DefaultSuiteContext) -> None:
    client = ctx.client
    checks = ctx.checks
    wait_ticks = ctx.wait_ticks
    feet, _ = ctx.ensure_pose()
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
    # Per-watch stack depth: default 8, configurable up to 32. The default
    # watch above must stay at 8; a second watch asks for 20 and must be
    # echoed and honored.
    checks.check(
        "watch_depth_default",
        bool(hits) and all(len(hit.get("stack", [])) <= 8 for hit in hits),
        f"hits={len(hits)}",
    )
    sample_base = int(client.watch_list().get("head", 0))
    sampled = client.watch_set(probe_addr, size=4, mode="w", depth=1, sample=True)
    try:
        wait_ticks(client, 5)
        sample_dump = client.watch_drain(sample_base, watch_id=int(sampled["id"]))
    finally:
        client.watch_clear(int(sampled["id"]))
    sample_hits = sample_dump.get("hits", [])
    checks.check(
        "watch_sample_bytes",
        bool(sample_hits)
        and all(h.get("value_ok") and len(h.get("value_hex", "")) == 8 for h in sample_hits),
    )
    sample_values = [
        int.from_bytes(bytes.fromhex(h["value_hex"]), "little")
        for h in sample_hits
        if h.get("value_ok")
    ]
    checks.check(
        "watch_sample_tick_values",
        len(set(sample_values)) >= 2 and sample_values == sorted(sample_values),
        str(sample_values),
    )
    checks.check(
        "watch_sample_qpc",
        sample_dump.get("qpc_frequency", 0) > 0
        and bool(sample_hits)
        and all(h.get("qpc", 0) > 0 for h in sample_hits),
    )
    deep_base = int(client.watch_list().get("head", 0))
    deep = client.watch_set(probe_addr, size=8, mode="w", tag="deep_probe", depth=20)
    checks.check(
        "watch_depth_install",
        deep.get("event") == "watch_installed" and int(deep.get("depth", 0)) == 20,
        str(deep),
    )
    wait_ticks(client, 10)
    deep_hits = client.watch_drain(deep_base).get("hits", [])
    checks.check(
        "watch_depth_stack",
        bool(deep_hits)
        and all(len(hit.get("stack", [])) <= 20 for hit in deep_hits)
        and any(len(hit.get("stack", [])) >= 9 for hit in deep_hits),
        f"hits={len(deep_hits)} depths={[len(h.get('stack', [])) for h in deep_hits][:8]}",
    )
    bad_depth = False
    try:
        client.watch_set(probe_addr, size=8, mode="w", tag="bad_depth", depth=33)
    except ModProtocolError as exc:
        bad_depth = "bad_depth" in str(exc)
    checks.check("watch_depth_bad", bad_depth, "")
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


def _section_hooks_overhead(ctx: DefaultSuiteContext) -> None:
    client = ctx.client
    checks = ctx.checks
    observation = ctx.observation
    wait_ticks = ctx.wait_ticks
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
    with observation.gap("hook_task_panic"):
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
    check_overhead_window(client, checks)
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
    # Accept both outcomes: the gate replies error/stress_disabled when
    # allow_selftest_stress is off (dev.py deletes harness.json on run).
    busy = client.request(
        {"cmd": "selftest_busy", "us": 1000, "ticks": 20}, ("busy_test_started", "error")
    )
    if busy.get("reason") == "stress_disabled":
        checks.skip("selftest_busy", "allow_selftest_stress is disabled")
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
            str({k: overhead_after.get(k) for k in ("hook_p50_us", "hook_p99_us", "hook_max_us")}),
        )
    checks.check("overhead_alive", wait_ticks(client, 3) >= 3)


def _section_parkour(ctx: DefaultSuiteContext) -> None:
    client = ctx.client
    checks = ctx.checks
    wait_ticks = ctx.wait_ticks
    feet, _ = ctx.ensure_pose()
    inject_enabled = ctx.ensure_inject_enabled()
    # -- dynamic key bindings ------------------------------------------
    bindings = client.state().get("bindings") or {}
    checks.check(
        "key_bindings_read",
        all(
            isinstance(bindings.get(key), int) and 0 <= int(bindings[key]) < 256
            for key in ("forward", "back", "left", "right", "sneak", "jump", "sprint")
        ),
        str(bindings),
    )

    # -- parkour jump/route primitives ---------------------------------
    check_jump_motion(client, checks, inject_enabled)

    check_input_mode_contract(client, checks)

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
        yaw=-90.0,  # +X target; yaw=0 would walk along +Z forever.
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


def _section_binary_record(ctx: DefaultSuiteContext) -> None:
    client = ctx.client
    checks = ctx.checks
    wait_ticks = ctx.wait_ticks
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

    check_record_medium(client, checks)


def _section_swim(ctx: DefaultSuiteContext) -> None:
    client = ctx.client
    checks = ctx.checks
    wait_ticks = ctx.wait_ticks
    feet, _ = ctx.ensure_pose()
    inject_enabled = ctx.ensure_inject_enabled()
    # -- swim: SWIMMING is entered with real keys, never state writes ---
    if not inject_enabled:
        checks.skip("swim_drive", "allow_input_inject is disabled")
    else:
        # Absolute fixture: a 3x3, 10-deep water column inside a stone
        # shell at a fixed world position. Nothing here depends on where the
        # player happened to be, so every run starts from the same blocks.
        # Stone shell -64..-54, water interior -63..-54 (surface top -53).
        if not prepare_swim_fixture(client, checks, "swim_pool"):
            ctx.aborted = True
            return

        def reset_swim_spot() -> dict:
            """Every swim sub-check starts from the identical, verified state.

            reset() clears input/tasks/velocity/effects and drops the player
            at the same point with yaw/pitch 0 inside the fixed pool.
            ``settle`` then requires four consecutive ticks of "no input,
            no horizontal motion, in water" before the step starts, so a
            leftover velocity from the previous sub-check cannot leak in.
            """
            client.command("gamemode survival @s")
            client.reset(SWIM_X + 0.5, SWIM_Y, SWIM_Z + 0.5, yaw=0.0, pitch=0.0)
            wait_ticks(client, 8)
            return settle(
                client,
                ticks=4,
                on_ground=None,
                still=2e-3,
                include_vertical=False,
                in_water=True,
                sprinting=False,
            )

        swim_start = reset_swim_spot()
        checks.check(
            "swim_start_still",
            bool(swim_start.get("ok")),
            f"ticks={swim_start.get('ticks')}",
        )
        entered = swim_drive(client, timeout=1.5)
        checks.check(
            "swim_drive_entered",
            bool(entered.get("entered")) and bool(entered.get("swimming")),
            str(entered),
        )

        # Dive from the same fixed point; 20 ticks (~5 blocks) stay clear of
        # the floor in a 9-deep pool.
        reset_swim_spot()
        swim_drive(client, timeout=1.5)
        client.look(0.0, 60.0)
        client.record_bin_start(period=1)
        wait_ticks(client, 20)
        client.record_bin_stop()
        dive = client.record_dump_all(format="jsonl")
        wet_free = [entry for entry in dive if entry.in_water and not entry.on_ground]
        checks.check(
            "swim_drive_dive",
            bool(wet_free) and min(entry.vy for entry in wet_free) < -0.15,
            f"free={len(wet_free)} min_vy={min((e.vy for e in wet_free), default=0.0):.4f}",
        )

        # Holding Space lifts the player in water (+0.175 blocks/tick);
        # looking up does not (the swim gate pins vy to 0). Same fixed start.
        reset_swim_spot()
        swim_drive(client, timeout=1.5)
        client.look(0.0, 0.0)
        client.record_bin_start(period=1)
        client.key_event(32, True)
        wait_ticks(client, 16)
        client.record_bin_stop()
        rise = client.record_dump_all(format="jsonl")
        client.key_event(32, False)
        wet_rise = [entry for entry in rise if entry.in_water]
        rise_vy = [entry.vy for entry in wet_rise[-6:]]
        checks.check(
            "swim_hold_space_rises",
            bool(rise_vy) and sum(rise_vy) / len(rise_vy) > 0.05,
            f"water_rows={len(wet_rise)} tail_vy={[round(v, 3) for v in rise_vy]}",
        )
        stopped = swim_stop(client)
        checks.check("swim_stop_clears", stopped.get("swimming") is False, str(stopped))

        # Sprint must not inject a jump. Re-sending the sprint *button*
        # through the queue/handler modes used to produce a 0.42 jump arc
        # on flat ground (measured 2026-10-25). The default HID path must
        # keep sprint independent of jump.
        client.reset(SWIM_X + 10.5, -60.0, SWIM_Z + 0.5, yaw=0.0, pitch=0.0)
        wait_ticks(client, 8)
        sprint_pre = settle(
            client,
            ticks=5,
            on_ground=True,
            still=1e-3,
            include_vertical=True,
            in_water=False,
            sprinting=False,
        )
        client.record_bin_start(period=1)
        client.set_input(forward=1.0, sprint=True, ticks=6)
        wait_ticks(client, 8)
        client.record_bin_stop()
        client.set_input()
        sprint_rows = client.record_dump_all(format="jsonl")
        sprint_flag = any(entry.sprinting for entry in sprint_rows)
        sprint_feet = [entry.feet_y for entry in sprint_rows]
        no_jump = (
            bool(sprint_feet)
            and max(sprint_feet) - min(sprint_feet) < 0.02
            and max((entry.vy for entry in sprint_rows), default=0.0) < 0.02
        )
        checks.check(
            "sprint_no_jump",
            bool(sprint_pre.get("ok")) and sprint_flag and no_jump,
            f"pre={sprint_pre.get('ok')} sprint={sprint_flag} jump={not no_jump}",
        )

        # -- togglefly: double-space flight gate in any non-spectator mode
        gm_now = int(client.state().get("gamemode", 0))
        client.command("gamemode survival @s")
        wait_ticks(client, 8)
        enabled = client.toggle_fly(True)
        checks.check("togglefly_enable", bool(enabled.get("mayfly")), str(enabled))
        wait_ticks(client, 4)
        flown = double_space_fly(client)
        checks.check(
            "togglefly_double_space",
            bool(flown.get("flying")),
            f"{flown} gamemode=survival",
        )
        disabled = client.toggle_fly(False)
        wait_ticks(client, 8)
        after_fly = client.state()
        checks.check(
            "togglefly_disable",
            disabled.get("mayfly") is False
            and after_fly.get("mayfly") is False
            and not after_fly.get("flying"),
            str({"reply": disabled, "state": after_fly.get("mayfly")}),
        )
        # The in-game command players type in chat: /togglefly flips it.
        on_reply = client.command("togglefly")
        wait_ticks(client, 6)
        checks.check(
            "togglefly_command_on",
            bool(on_reply.get("success")) and client.state().get("mayfly") is True,
            str(on_reply),
        )
        off_reply = client.command("togglefly")
        wait_ticks(client, 6)
        checks.check(
            "togglefly_command_off",
            bool(off_reply.get("success")) and client.state().get("mayfly") is False,
            str(off_reply),
        )
        # Typed by a player: '/' opens chat, text goes through the client's
        # input field, Enter submits it as a command request to the server.
        # Both keys need to span a tick; a zero-length down/up is dropped.
        client.set_input()
        client.key_event(0xBF, True, via="hid")
        wait_ticks(client, 2)
        client.key_event(0xBF, False, via="hid")
        wait_ticks(client, 3)
        screen = client.screen().get("name")
        client.text_input("togglefly")
        wait_ticks(client, 3)
        client.key_event(0x0D, True, via="hid")
        wait_ticks(client, 2)
        client.key_event(0x0D, False, via="hid")
        wait_ticks(client, 8)
        typed_mayfly = client.state().get("mayfly")
        checks.check(
            "togglefly_chat_command",
            screen in ("chat_screen", "chat") and typed_mayfly is True,
            f"screen={screen} mayfly={typed_mayfly}",
        )
        client.command("togglefly")  # leave the gate off for the spectator check
        wait_ticks(client, 6)
        # NotCheat: the command is usable in a world with cheats switched off
        # (the client still sends the request; the server allows this flag).
        try:
            client.commands_enabled(False)
            wait_ticks(client, 6)
            client.set_input()
            client.key_event(0xBF, True, via="hid")
            wait_ticks(client, 2)
            client.key_event(0xBF, False, via="hid")
            wait_ticks(client, 3)
            client.text_input("togglefly")
            wait_ticks(client, 3)
            client.key_event(0x0D, True, via="hid")
            wait_ticks(client, 2)
            client.key_event(0x0D, False, via="hid")
            wait_ticks(client, 8)
            no_cheat_mayfly = client.state().get("mayfly")
            checks.check(
                "togglefly_not_cheat",
                no_cheat_mayfly is True,
                f"commands_enabled=False mayfly={no_cheat_mayfly}",
            )
        finally:
            client.commands_enabled(True)
            wait_ticks(client, 6)
        client.command("togglefly")
        wait_ticks(client, 6)
        client.command("gamemode spectator @s")
        wait_ticks(client, 8)
        try:
            client.toggle_fly()
            checks.check("togglefly_spectator_guard", False, "accepted in spectator")
        except ModProtocolError as exc:
            checks.check(
                "togglefly_spectator_guard",
                "spectator_not_supported" in str(exc),
                str(exc),
            )
        spectator_reply = client.command("togglefly")
        checks.check(
            "togglefly_command_spectator_guard",
            not bool(spectator_reply.get("success")),
            str(spectator_reply),
        )
        client.command(f"gamemode {gm_now} @s")
        wait_ticks(client, 8)
        # The swim pool lives at the fixed off-site fixture (SWIM_X/SWIM_Z)
        # and is intentionally left in place: keeping the same blocks makes
        # the next run start from the identical geometry.
        # The dive's ground contact enqueues a player_landed event; consume
        # it so the later landing check does not read this one.
        drain_deadline = time.monotonic() + 0.5
        while time.monotonic() < drain_deadline:
            try:
                client.readline(0.1)
            except ModTimeout:
                break
        client.reset(feet[0], feet[1], feet[2], yaw=0.0)
        wait_ticks(client, 10)


def _section_world_queries(ctx: DefaultSuiteContext) -> None:
    client = ctx.client
    checks = ctx.checks
    wait_ticks = ctx.wait_ticks
    feet, _ = ctx.ensure_pose()
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
    # A plain teleport must leave effects alone: reset() writes the
    # clearEffects flag into the shared request block, and it used to leak
    # into every later teleport (protection vanished mid-experiment).
    client.teleport(feet[0] + 2.0, feet[1], feet[2])
    wait_ticks(client, 8)
    after_tp = client.state().get("effects", [])
    checks.check(
        "effects_survive_teleport",
        any(int(e.get("id", -1)) == 1 for e in after_tp),
        str(after_tp),
    )
    client.teleport(feet[0], feet[1], feet[2])
    wait_ticks(client, 6)
    client.command("effect @s clear")
    wait_ticks(client, 3)
    checks.check("effects_clear_no_tombstones", client.state().get("effects") == [])
    client.reset(feet[0], feet[1], feet[2], yaw=0.0, pitch=0.0)
    wait_ticks(client, 12)
    checks.check("effects_reset_no_tombstones", client.state().get("effects") == [])
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
    cp_target = [float(saved[axis]) for axis in "xyz"]
    cp_gap = max(
        abs(float(now_cp[f"feet_{axis}"]) - value)
        for axis, value in zip("xyz", cp_target, strict=True)
    )
    checks.check(
        "checkpoint_load",
        loaded_cp.get("event") == "checkpoint_loaded" and cp_gap < 0.3,
        f"gap={cp_gap:.3f} target={cp_target} actual={[now_cp[f'feet_{axis}'] for axis in 'xyz']}",
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


def _section_inventory_entity(ctx: DefaultSuiteContext) -> None:
    client = ctx.client
    checks = ctx.checks
    wait_ticks = ctx.wait_ticks
    args = ctx.args
    feet, selected = ctx.ensure_pose()
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

    # -- equip: an inventory stack moves into a worn slot ---------------
    armor_before = before.get("armor", [])
    client.set_item(0, "minecraft:diamond_boots", count=1)
    client.select_slot(0)
    wait_ticks(client, 8)
    equipped = client.equip("feet")
    wait_ticks(client, 10)
    armor_after = client.inventory().get("armor", [])
    feet_item = next((entry["item"] for entry in armor_after if int(entry["slot"]) == 3), None)
    checks.check(
        "equip_feet",
        equipped.get("event") == "equip_done"
        and isinstance(feet_item, dict)
        and feet_item.get("type") == "minecraft:diamond_boots",
        f"feet={feet_item}",
    )
    checks.check("equip_cleared_source", slot_item(client.inventory(), 0) is None)
    for armor in armor_before:
        armor_slot = int(armor.get("slot", -1))
        if not 0 <= armor_slot < 4:
            continue
        item = armor.get("item")
        if item is None:
            client.clear_item(armor_slot, container="armor")
            continue
        if item:
            client.set_item(
                armor_slot,
                item["type"],
                count=int(item["count"]),
                aux=int(item.get("aux", 0)),
                container="armor",
            )
        else:
            client.clear_item(armor_slot, container="armor")
    wait_ticks(client, 5)

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
    checks.check(
        "teleport",
        gap < 0.2,
        f"gap={gap:.3f} target={feet} actual={[now[f'feet_{axis}'] for axis in 'xyz']} in_water={now.get('in_water')}",
    )

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
            int(ecs_entity.get("components", 0)) > 0 and int(ecs_entity.get("registry", 0)) != 0,
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
        # Validate the positions actually used by this request. A later
        # entity query observes a moving target at a different time.
        eye, target = look["eye"], look["target"]
        expected_yaw = math.degrees(
            math.atan2(
                -(float(target["x"]) - float(eye["x"])),
                float(target["z"]) - float(eye["z"]),
            )
        )
        current = client.state()
        yaw_gap = abs((float(current.get("yaw", 0.0)) - expected_yaw + 180.0) % 360.0 - 180.0)
        requested_gap = abs((float(look["yaw"]) - expected_yaw + 180.0) % 360.0 - 180.0)
        checks.check(
            "look_at_entity",
            look.get("event") == "look_at_done"
            and look.get("runtime_id") == runtime_id
            and requested_gap < 1e-3
            and yaw_gap < 0.1,
            f"yaw={current.get('yaw'):.3f} expected={expected_yaw:.3f} requested_gap={requested_gap:.6f}",
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


def _section_macro_replay(ctx: DefaultSuiteContext) -> None:
    client = ctx.client
    checks = ctx.checks
    wait_ticks = ctx.wait_ticks
    inject_enabled = ctx.ensure_inject_enabled()
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
            # reset_done accepts the request before the server/client
            # teleport is settled; do not mistake the old still pose for it.
            wait_ticks(client, 10)
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
        # Repeated runs reproduce exactly, but the first tick's phase
        # occasionally shifts one input edge by a sub-tick amount
        # (measured 0.0017 blocks); a lost edge would move >= a tick of
        # motion (~0.1), and real input interference shows as external
        # camera drift, so keep the guard an order of magnitude below that.
        if external:
            checks.skip("macro_event_deterministic", f"real mouse moved the camera; gap={gap:.6f}")
        else:
            checks.check("macro_event_deterministic", gap < 5e-3, f"gap={gap:.6f}")
        checks.check("macro_event_clicks", left_events == 2, f"left={left_events}")
        _, legacy_left, _ = run_macro(False)
        checks.check("macro_legacy_path", legacy_left == 0, f"left={legacy_left}")

        # Macro recording lifecycle: the tick-side append and pipe-side
        # stop must share a boundary, and playback must never be recorded
        # concurrently. A short capped session also exercises the
        # automatic limit path before the explicit save.
        lifecycle_name = "selftest_macro_lifecycle"
        lifecycle_path = mod_dir / "macros" / f"{lifecycle_name}.json"
        with contextlib.suppress(OSError):
            lifecycle_path.unlink()
        client.macro_record_start(max_ticks=3)
        wait_ticks(client, 6)
        limited = client.macro_status()
        checks.check(
            "macro_record_limit_stops",
            not bool(limited.get("recording")) and int(limited.get("recorded_ticks", 0)) >= 3,
            f"recording={limited.get('recording')} ticks={limited.get('recorded_ticks')}",
        )
        saved = client.macro_record_stop(lifecycle_name)
        checks.check(
            "macro_record_limit_save",
            int(saved.get("ticks", 0)) == int(limited.get("recorded_ticks", 0)),
            f"saved={saved.get('ticks')} buffered={limited.get('recorded_ticks')}",
        )
        after_save = client.macro_status()
        checks.check(
            "macro_record_stop_clears_session",
            int(after_save.get("recorded_ticks", -1)) == 0,
            str(after_save.get("recorded_ticks")),
        )

        client.macro_record_start()
        duplicate = client.request({"cmd": "macro_rec_start"}, "error")
        checks.check(
            "macro_record_duplicate_rejected",
            duplicate.get("reason") == "macro_already_recording",
            str(duplicate.get("reason")),
        )
        wait_ticks(client, 1)
        client.macro_record_stop("selftest_macro_duplicate")

        client.macro_run(loop=True, event_level=True)
        wait_ticks(client, 6)
        held_input = client.state().get("input", {})
        checks.check("macro_loop_holds_key", bool(held_input.get("w")), str(held_input))
        reply = client.request({"cmd": "macro_rec_start"}, "error")
        checks.check(
            "macro_record_during_playback_rejected",
            reply.get("reason") == "macro_playing",
            str(reply.get("reason")),
        )
        client.macro_stop()
        wait_ticks(client, 4)
        released_input = client.state().get("input", {})
        checks.check(
            "macro_stop_releases_session",
            not bool(client.macro_status().get("playing"))
            and not any(
                bool(released_input.get(key)) for key in ("w", "a", "s", "d", "jump", "sneak")
            ),
            str(released_input),
        )

    if inject_enabled:
        ctx.macro_path = macro_path


def _section_hardening_guards(ctx: DefaultSuiteContext) -> None:
    client = ctx.client
    checks = ctx.checks
    inject_enabled = ctx.ensure_inject_enabled()
    macro_path = ctx.macro_path
    # -- review hardening guards ---------------------------------------
    # Each check pins one fix from the code-review round; see mod/README.md.
    reply = client.request({"cmd": "look", "yaw": 1e30, "relative": False}, "error")
    checks.check(
        "guard_look_out_of_range_rejected",
        reply.get("reason") == "bad_param",
        str(reply.get("reason")),
    )
    try:
        client.ecs_context(0x41410000, 1)
        checks.check("guard_ecs_context_bad_addr", False, "no error raised")
    except ModProtocolError as exc:
        checks.check("guard_ecs_context_bad_addr", "bad_addr" in str(exc), str(exc))
    context_addr = client.state().get("context")
    if context_addr:
        try:
            client.watch_set(int(context_addr), size=1, mode="w", thread="all")
            checks.check("guard_watch_thread_all_rejected", False, "no error raised")
        except ModProtocolError as exc:
            checks.check(
                "guard_watch_thread_all_rejected",
                "thread_all_unsupported" in str(exc),
                str(exc),
            )
    else:
        checks.skip("guard_watch_thread_all_rejected", "state has no context address")
    if inject_enabled and macro_path is not None and macro_path.exists():
        client.macro_run(loop=True)
        loaded = client.macro_load("selftest_g4")
        status = client.macro_status()
        checks.check(
            "guard_macro_load_stops_playback",
            bool(loaded.get("stopped")) and not bool(status.get("playing")),
            f"stopped={loaded.get('stopped')} playing={status.get('playing')}",
        )

    checks.check("macro_status", client.macro_status().get("event") == "macro_status")


DEFAULT_SECTIONS: tuple[tuple[str, Callable[[DefaultSuiteContext], None]], ...] = (
    ("preflight", _section_preflight),
    ("line_framing", _section_line_framing),
    ("input_injection", _section_input_injection),
    ("abilities", _section_abilities),
    ("packets", _section_packets),
    ("version_diagnostics", _section_version_diagnostics),
    ("memory_watch", _section_memory_watch),
    ("hooks_overhead", _section_hooks_overhead),
    ("parkour", _section_parkour),
    ("binary_record", _section_binary_record),
    ("swim", _section_swim),
    ("world_queries", _section_world_queries),
    ("inventory_entity", _section_inventory_entity),
    ("macro_replay", _section_macro_replay),
    ("hardening_guards", _section_hardening_guards),
)
_DEFAULT_SECTION_MAP = dict(DEFAULT_SECTIONS)


def check_default_suite(
    client: ModClient,
    checks: Checks,
    args: argparse.Namespace,
    observation: ViolationObservation,
    *,
    require_world: Any,
    wait_ticks: Any,
    dump_packet_tap: Any,
    sections: Iterable[str] | None = None,
) -> int:
    selected_names = [name for name, _ in DEFAULT_SECTIONS] if sections is None else list(sections)
    unknown = [name for name in selected_names if name not in _DEFAULT_SECTION_MAP]
    if unknown:
        raise ValueError(f"unknown default suite sections: {', '.join(unknown)}")

    tap_dump = os.environ.get("LEVIPILOT_PACKET_DUMP") or None
    ctx = DefaultSuiteContext(
        client=client,
        checks=checks,
        args=args,
        observation=observation,
        require_world=require_world,
        wait_ticks=wait_ticks,
        dump_packet_tap=dump_packet_tap,
        tap_dump=tap_dump,
    )
    try:
        if sections is not None and "preflight" not in selected_names:
            require_world(client, "selftest preflight")
        for name in selected_names:
            _DEFAULT_SECTION_MAP[name](ctx)
            if ctx.aborted:
                return 0
        return 0
    except WorldLost:
        raise
    except ModPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        dump_packet_tap(client, tap_dump)
