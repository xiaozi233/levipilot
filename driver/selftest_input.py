"""Input, identity, teleport, flight and macro-playback live checks."""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

from driver.harness import ModClient, ModPipeError, ModProtocolError, ModTimeout
from driver.harness.lab import settle
from driver.selftest_flight import EffectFixture
from driver.selftest_runtime import require_world, wait_rotation, wait_screen, wait_ticks
from driver.selftest_support import Checks, preserve_failure


def check_input_mode_contract(client: ModClient, checks: Checks) -> None:
    """Keep protocol numbers and the legacy force gate compatible; never move."""
    try:
        for bad_impulse in (1, 3):
            try:
                client.request({"cmd": "input_cfg", "jump_impulse": bad_impulse}, "input_cfg_ok")
                checks.check(f"jump_impulse_guard_{bad_impulse}", False, "legacy impulse accepted")
            except ModProtocolError as exc:
                checks.check(
                    f"jump_impulse_guard_{bad_impulse}",
                    "legacy_jump_impulse_requires_force" in str(exc),
                    str(exc),
                )
        try:
            client.request({"cmd": "input_cfg", "mode": 7}, "input_cfg_ok")
            checks.check("legacy_input_mode_guard", False, "legacy mode accepted")
        except ModProtocolError as exc:
            checks.check(
                "legacy_input_mode_guard",
                "legacy_input_mode_requires_force" in str(exc),
                str(exc),
            )
        forced = client.request(
            {"cmd": "input_cfg", "jump_impulse": 1, "mode": 7, "force": True}, "input_cfg_ok"
        )
        checks.check("legacy_input_force", forced.get("event") == "input_cfg_ok", str(forced))
    finally:
        with preserve_failure(checks, "input_mode"):
            restored = client.input_cfg(jump_impulse=0, mode=0)
            checks.check(
                "real_input_restore", restored.get("event") == "input_cfg_ok", str(restored)
            )


def check_controls(client: ModClient, checks: Checks) -> None:
    """Exercise real control calls, uncertain edges, failure propagation and retry."""
    original = client.state()
    if not checks.check("controls_in_world", original.get("in_world") is True):
        return
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    client.input_lock(True)
    client.input_cfg(mode=0, jump_impulse=0)
    macro_name = f"selftest_controls_{time.monotonic_ns()}"
    macro_path = directory / "macros" / f"{macro_name}.json"
    client.stop_move()
    settle(client, on_ground=True)

    def arm(operation: str, mode: str) -> None:
        client.request(
            {"cmd": "selftest_controls", "operation": operation, "mode": mode},
            "control_test_armed",
        )

    def recovery() -> dict[str, Any]:
        deadline = time.monotonic() + 4
        while True:
            state: dict[str, Any] = client.status().get("controls", {})
            if (
                state.get("held_mask") == 0
                and state.get("cleanup_pending") is False
                and state.get("injecting_input") is False
                and state.get("injecting_look") is False
            ) or time.monotonic() >= deadline:
                return state
            time.sleep(0.01)

    def diagnostic(operation: str, mode: str, prefix: str = "controls") -> None:
        row = client.wait_event(
            "control_error", 4, predicate=lambda row: row.get("operation") == operation
        )
        key = (
            "faults"
            if mode.startswith("seh")
            else ("failures" if mode.startswith("cpp") else "unavailable")
        )
        checks.check(f"{prefix}_{operation}_{mode}_reported", int(row.get(key, 0)) == 1, str(row))
        state = recovery()
        checks.check(
            f"{prefix}_{operation}_{mode}_releases",
            state.get("held_mask") == 0
            and state.get("cleanup_pending") is False
            and state.get("injecting_input") is False
            and state.get("injecting_look") is False,
            str(state),
        )
        wait_ticks(client, 2)
        current = client.state()
        checks.check(
            f"{prefix}_{operation}_{mode}_game_released",
            current["input"].get("w") is False
            and current["input"].get("jump") is False
            and client.key_state(int(current["bindings"]["forward"])).get("down") is False,
        )

    try:
        for operation in ("key_down", "key_up", "mouse_down", "mouse_up", "turn", "look_read"):
            for mode in ("seh_before", "cpp_before", "seh_after", "cpp_after", "unavailable"):
                client.stop_move()
                client.look(0, relative=True)
                recovery()
                # Rebase the previous after-call fault's local turn through
                # the authoritative server before testing the next sequence.
                # A late camera correction must not look like replay.
                if operation in ("turn", "look_read"):
                    client.teleport(
                        *(float(original[f"feet_{axis}"]) for axis in "xyz"),
                        yaw=float(original["yaw"]),
                        pitch=float(original["pitch"]),
                    )
                    wait_ticks(client, 4)
                if operation in ("key_up", "mouse_up"):
                    client.set_input(
                        forward=1 if operation == "key_up" else 0,
                        lmb=operation == "mouse_up",
                        ticks=-1,
                    )
                    wait_ticks(client, 2)
                arm(operation, mode)
                if operation in ("turn", "look_read"):
                    client.look(6, relative=True, ticks=-1)
                elif operation in ("key_up", "mouse_up"):
                    client.stop_move()
                else:
                    client.set_input(
                        forward=1 if operation == "key_down" else 0,
                        lmb=operation == "mouse_down",
                        ticks=-1,
                    )
                diagnostic(operation, mode)
                if operation in ("turn", "look_read"):
                    yaw = float(client.state()["yaw"])
                    wait_ticks(client, 4)
                    after_yaw = float(client.state()["yaw"])
                    checks.check(
                        f"controls_{operation}_{mode}_not_replayed",
                        abs((after_yaw - yaw + 180) % 360 - 180) < 0.05,
                        f"before={yaw:.6f}, after={after_yaw:.6f}",
                    )
        for operation in ("bindings", "actions"):
            arm(operation, "seh_before")
            diagnostic(operation, "seh_before")
        for operation in ("key_down", "turn"):
            settle(client, on_ground=True)
            arm(operation, "seh_after")
            result = client.jump_when(
                mode="now",
                forward=0.25,
                yaw=float(client.state()["yaw"]) + 15 if operation == "turn" else None,
            )
            checks.check(
                f"controls_task_{operation}_fails",
                result.get("ok") is False and result.get("reason") == "input_failed",
                str(result),
            )
            diagnostic(operation, "seh_after", "controls_task")
        for event_level in (False, True):
            client.macro_save(macro_name, [["true", *["false"] * 8, "5", "0"]] * 4)
            client.macro_load(macro_name)
            arm("turn", "seh_after")
            started = client.macro_run(event_level=event_level)
            done = client.wait_event(
                "macro_done",
                4,
                predicate=lambda row, run_id=started["run_id"]: row.get("run_id") == run_id,
            )
            checks.check(
                f"controls_macro_{event_level}_turn_fails",
                done.get("ok") is False and done.get("reason") == "playback_fault",
                str(done),
            )
            diagnostic("turn", "seh_after", f"controls_macro_{event_level}")
        settle(client, on_ground=True)
        fresh = client.jump_when(mode="now")
        checks.check("controls_fresh_task_recovers", fresh.get("ok") is True, str(fresh))
        settle(client, on_ground=True)
        checks.check(
            "controls_ping_alive", bool(client.ping()) and client.state()["in_world"] is True
        )
    finally:
        with preserve_failure(checks, "controls"):
            arm("", "off")
            client.macro_stop()
            macro_path.unlink(missing_ok=True)
            client.stop_move()
            client.look(0, relative=True)
            recovery()
            client.teleport(
                *(float(original[f"feet_{axis}"]) for axis in "xyz"),
                yaw=float(original["yaw"]),
                pitch=float(original["pitch"]),
            )
            settle(client, on_ground=True)
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("controls_handled_fault_no_dump", after == before)


def check_player_identity(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Verify UUID-only local resolution, failed samples and world retirement."""
    original = client.state()
    if not checks.check("identity_in_world", original.get("in_world") is True):
        return
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    client.input_lock(True)

    def identity_ready(valid: bool) -> dict[str, Any]:
        deadline = time.monotonic() + 3
        while True:
            row: dict[str, Any] = client.status().get("player_identity", {})
            if row.get("valid") is valid or time.monotonic() >= deadline:
                return row
            time.sleep(0.01)

    def arm(mode: str) -> None:
        client.request({"cmd": "selftest_player_identity", "mode": mode}, "identity_test_armed")

    def inventory_items(row: dict[str, Any]) -> dict[str, Any]:
        return {key: row.get(key) for key in ("slots", "armor", "offhand", "selected_slot")}

    try:
        first = identity_ready(True)
        checks.check(
            "identity_snapshot_valid",
            first.get("valid") is True
            and bool(first.get("uuid_a") or first.get("uuid_b"))
            and int(first.get("sample_tick", -1)) >= 0,
            str(first),
        )
        checks.check("identity_local_origin", client.command("testfor @s").get("success") is True)
        checks.check(
            "identity_explicit_origin",
            client.command("testfor @s", player=str(original["name"])).get("success") is True,
        )
        missing = client.request(
            {
                "cmd": "command",
                "command": "say identity must not use console",
                "player": "__no_player__",
            },
            "error",
        )
        checks.check(
            "identity_missing_player_no_console",
            missing.get("reason") == "command_player_not_found",
        )
        for mode, counter in (("seh", "faults"), ("cpp", "failures"), ("zero", "empty")):
            old = client.status()["player_identity"]
            inventory = inventory_items(client.inventory())
            arm(mode)
            invalid = identity_ready(False)
            checks.check(
                f"identity_{mode}_invalidates",
                invalid.get("valid") is False
                and int(invalid.get(counter, 0)) > int(old.get(counter, 0)),
                str(invalid),
            )
            refused = client.request({"cmd": "command", "command": "testfor @s"}, "error")
            checks.check(
                f"identity_{mode}_command_rejected",
                refused.get("reason") == "command_player_unknown",
            )
            try:
                client.fly(False)
                checks.check(f"identity_{mode}_flight_rejected", False, "no error raised")
            except ModProtocolError as exc:
                checks.check(
                    f"identity_{mode}_flight_rejected",
                    "flight_player_unknown" in str(exc),
                    str(exc),
                )
            for command in (
                {"cmd": "give", "item": "minecraft:stick"},
                {"cmd": "set_item", "slot": 8, "item": "minecraft:stone"},
                {"cmd": "equip", "target": "offhand", "slot": 8},
                {"cmd": "clear_inventory"},
            ):
                refused = client.request(command, "error")
                checks.check(
                    f"identity_{mode}_{command['cmd']}_rejected",
                    refused.get("reason") == "inventory_player_unknown",
                )
            checks.check(
                f"identity_{mode}_inventory_intact",
                inventory_items(client.inventory()) == inventory,
            )
            checks.check(
                f"identity_{mode}_tick_alive",
                bool(client.ping()) and client.state().get("in_world") is True,
            )
            arm("off")
            recovered = identity_ready(True)
            checks.check(
                f"identity_{mode}_recovery",
                recovered.get("valid") is True
                and client.command("testfor @s").get("success") is True,
            )

        remembered = client.request(
            {"cmd": "selftest_player_identity", "mode": "remember"}, "identity_remembered"
        )
        current = client.request(
            {"cmd": "selftest_player_identity", "mode": "resolve"}, "identity_resolved"
        )
        checks.check("identity_current_server_resolves", current.get("ok") is True)
        client.close()
        client.connect()
        client.input_lock(True)
        checks.check(
            "identity_reconnect_keeps_current",
            identity_ready(True).get("epoch") == remembered["epoch"]
            and client.command("testfor @s").get("success") is True,
        )
        if world:
            try:
                client.leave_world()
                client.wait_event("world_left", 45)
                absent = client.status()["player_identity"]
                checks.check("identity_menu_invalid", absent.get("valid") is False, str(absent))
                checks.check("identity_menu_new_epoch", absent.get("epoch") != remembered["epoch"])
            finally:
                client.enter_world_ready(world)
                client.wait_in_world(timeout=60)
            recovered = identity_ready(True)
            checks.check(
                "identity_new_world_recovers",
                recovered.get("valid") is True and recovered.get("epoch") != remembered["epoch"],
            )
            obsolete = client.request(
                {"cmd": "selftest_player_identity", "mode": "resolve"}, "identity_resolved"
            )
            checks.check("identity_old_world_rejected", obsolete.get("ok") is False, str(obsolete))
            client.teleport(
                *(float(original[f"feet_{axis}"]) for axis in "xyz"),
                yaw=float(original["yaw"]),
                pitch=float(original["pitch"]),
            )
            checks.check("identity_world_restores_ground", settle(client, on_ground=True)["ok"])
            checks.check(
                "identity_new_world_local_origin",
                client.command("testfor @s").get("success") is True,
            )
    finally:
        if client.status().get("in_world"):
            arm("off")
            identity_ready(True)
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("identity_handled_fault_no_dump", after == before)


def check_teleport(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Verify server teleport ownership, partial failures and session isolation."""
    original = client.state()
    if not checks.check(
        "teleport_ready",
        original.get("in_world") is True and not original.get("effects"),
        "requires a world and no existing potion effects",
    ):
        return
    client.input_lock(True)
    client.set_input()
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    position = {axis: float(original[f"feet_{axis}"]) for axis in "xyz"}
    yaw, pitch = float(original["yaw"]), float(original["pitch"])
    checkpoint = f"selftest_teleport_{time.monotonic_ns()}"

    def complete(teleport_id: int) -> dict[str, Any]:
        return client.wait_event(
            ("teleported", "error"),
            4,
            predicate=lambda row: row.get("teleport_id") == teleport_id,
        )

    def unique(teleport_id: int) -> bool:
        try:
            client.wait_event(
                ("teleported", "error"),
                0.12,
                predicate=lambda row: row.get("teleport_id") == teleport_id,
            )
        except ModTimeout:
            return True
        return False

    def marker() -> None:
        client.command("effect @s speed 6000 0 true")
        wait_ticks(client, 2)
        if not any(int(effect.get("id", -1)) == 1 for effect in client.state().get("effects", [])):
            raise ValueError("teleport fixture failed to apply speed effect")

    def has_marker() -> bool:
        return any(int(effect.get("id", -1)) == 1 for effect in client.state().get("effects", []))

    def inject(mode: str, *, reset: bool = False, timeout: int = 250) -> dict[str, Any]:
        return {
            "cmd": "selftest_teleport",
            **position,
            "yaw": yaw + 35,
            "pitch": 0,
            "mode": mode,
            "reset": reset,
            "timeout_ms": timeout,
        }

    def restore_pose() -> dict[str, Any]:
        reply = client.teleport(**position, yaw=yaw, pitch=pitch)
        wait_ticks(client, 4)
        return reply

    try:
        marker()
        reply = restore_pose()
        checks.check(
            "teleport_server_result",
            reply.get("source") == "server" and reply.get("ok") is True,
            str(reply),
        )
        checks.check("teleport_preserves_effects", has_marker())
        client.teleport(**position, yaw=yaw + 25, pitch=15)
        wait_rotation(client, yaw + 25, 15)
        checks.check("teleport_rotation_settled", True)
        client.teleport(**position)  # Neither angle keeps both current values.
        wait_ticks(client, 4)
        state = client.state()
        checks.check(
            "teleport_keeps_rotation",
            abs((float(state["yaw"]) - yaw - 25 + 180) % 360 - 180) < 0.1
            and abs(float(state["pitch"]) - 15) < 0.1,
            f"yaw={float(state['yaw']):.3f} want={yaw + 25:.3f} "
            f"pitch={float(state['pitch']):.3f} want=15.000",
        )
        client.teleport(**position, yaw=yaw)
        wait_ticks(client, 4)
        checks.check("teleport_partial_rotation_zero", abs(float(client.state()["pitch"])) < 0.1)
        client.checkpoint_save(checkpoint)
        accepted = client.checkpoint_load(checkpoint)
        loaded = complete(int(accepted["teleport_id"]))
        checks.check("teleport_checkpoint_complete", loaded.get("ok") is True and has_marker())
        for mode, reason, reset in (
            ("seh", "teleport_fault", False),
            ("cpp", "teleport_failed", False),
            ("timeout", "teleport_timeout", False),
            ("queue", "server_task_queue_failed", False),
            ("stale_epoch", "world_left", False),
            ("after_effects", "teleport_fault", True),
            ("after_teleport", "teleport_fault", False),
        ):
            restore_pose()
            marker()
            accepted = client.request(inject(mode, reset=reset), ("teleport_queued", "reset_done"))
            teleport_id = int(accepted["teleport_id"])
            result = complete(teleport_id)
            checks.check(
                f"teleport_{mode}",
                result.get("phase") == "teleport"
                and result.get("reason") == reason
                and result.get("partial") is True,
                str(result),
            )
            checks.check(f"teleport_{mode}_unique", unique(teleport_id))
            wait_ticks(client, 4)
            state = client.state()
            expected_yaw = yaw + 35 if mode == "after_teleport" else yaw
            checks.check(
                f"teleport_{mode}_side_effects",
                has_marker() == (mode != "after_effects")
                and abs((float(state["yaw"]) - expected_yaw + 180) % 360 - 180) < 0.1,
            )
            checks.check(f"teleport_{mode}_recovery", restore_pose().get("ok") is True)

        marker()
        accepted = client.request(inject("timeout", timeout=1500), "teleport_queued")
        rejected = client.request({"cmd": "reset", **position}, "error")
        checks.check(
            "teleport_busy_reset", rejected.get("reason") == "teleport_busy" and has_marker()
        )
        rejected = client.request({"cmd": "checkpoint_load", "name": checkpoint}, "error")
        checks.check("teleport_busy_checkpoint", rejected.get("reason") == "teleport_busy")
        rejected = client.request({"cmd": "teleport", **position, "x": "invalid"}, "error")
        checks.check("teleport_bad_param_preserves", rejected.get("reason") == "bad_param")
        result = complete(int(accepted["teleport_id"]))
        checks.check("teleport_busy_original", result.get("reason") == "teleport_timeout")
        accepted = client.reset(**position, yaw=yaw, pitch=pitch, wait_teleport=False)
        result = complete(int(accepted["teleport_id"]))
        wait_ticks(client, 4)
        checks.check(
            "teleport_reset_clears_effects",
            result.get("effects_cleared") is True and not client.state().get("effects"),
        )
        accepted = client.request(inject("timeout", timeout=10000), "teleport_queued")
        client.panic("tasks")
        result = complete(int(accepted["teleport_id"]))
        checks.check("teleport_panic_cancels", result.get("reason") == "panic")
        checks.check("teleport_panic_recovers", restore_pose().get("ok") is True)
        if world:
            accepted = client.request(inject("timeout", timeout=10000), "teleport_queued")
            client.close()
            client.connect()
            client.input_lock(True)
            checks.check("teleport_reconnect_fresh", restore_pose().get("ok") is True)
            checks.check("teleport_reconnect_no_stale", unique(int(accepted["teleport_id"])))
            accepted = client.request(inject("timeout", timeout=10000), "teleport_queued")
            try:
                client.leave_world()
                client.wait_event("world_left", 45)
                result = complete(int(accepted["teleport_id"]))
                checks.check("teleport_world_cancel", result.get("reason") == "world_left")
                rejected = client.request({"cmd": "teleport", **position}, "error")
                checks.check("teleport_menu_rejects", rejected.get("reason") == "not_in_world")
            finally:
                with preserve_failure(checks, "teleport"):
                    client.enter_world_ready(world)
                    client.wait_in_world(timeout=60)
            checks.check("teleport_world_recovers", restore_pose().get("ok") is True)
    finally:
        with preserve_failure(checks, "teleport"):
            client.command("effect @s clear")
            client.checkpoint_delete(checkpoint)
            restore_pose()
    restored = client.state()
    checks.check(
        "teleport_pose_restored",
        all(abs(float(restored[f"feet_{axis}"]) - position[axis]) < 0.1 for axis in "xyz")
        and abs((float(restored["yaw"]) - yaw + 180) % 360 - 180) < 0.1
        and abs(float(restored["pitch"]) - pitch) < 0.1,
    )
    checks.check("teleport_alive", bool(client.ping()) and restored.get("in_world") is True)
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("teleport_handled_fault_no_dump", after == before)


def check_flight(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Verify staged abilities, bounded tick assertion, failures and retirement."""
    original = client.state()
    if not checks.check("flight_in_world", original.get("in_world") is True):
        return
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    client.input_lock(True)
    # Both natural disconnects happened inside this check. When a violation dump
    # is requested, also serialize SetActorData(39): the first bytes separate a
    # serialization-mode mismatch from a payload that was written short.
    bytes_dump = os.environ.get("LEVIPILOT_VIOLATION_DUMP") or None
    bytes_armed = False
    effect_fixture = EffectFixture(original)
    if bytes_dump:
        try:
            # Observed violations were inbound bodies that ran short: 39
            # (SetActorData), 111 (MoveDeltaActor), 188 (UpdateAdventureSettings).
            # 187 (UpdateAbilities) is what the flight checks provoke, and a
            # mis-framed body there would desync every following packet -- so
            # keep the whole identity ring and serialize all four suspects.
            client.packet_capture(
                True,
                serialize_ids=[39, 111, 187, 188],
                directions=["send", "recv"],
            )
            bytes_armed = True
        except (ModPipeError, ModProtocolError) as exc:
            checks.note("flight_bytes_capture", f"unavailable: {exc}")

    def inject(stage: str, mode: str, timeout: int = 250) -> dict[str, Any]:
        return {
            "cmd": "selftest_flight",
            "stage": stage,
            "mode": mode,
            "on": False,
            "timeout_ms": timeout,
        }

    def unique(flight_id: int) -> bool:
        try:
            client.wait_event(
                ("error", "fly_ok", "togglefly_ok", "flight_assertion_failed"),
                0.15,
                predicate=lambda row: row.get("flight_id") == flight_id,
            )
        except ModTimeout:
            return True
        return False

    def off() -> bool:
        reply = client.fly(False)
        wait_ticks(client, 2)
        state = client.state()
        return (
            reply.get("server_applied") is True
            and not state.get("mayfly")
            and not state.get("flying")
        )

    try:
        effect_fixture.install(client, checks, wait_ticks)
        client.command("gamemode survival @s")
        wait_ticks(client, 8)
        checks.check("flight_disable_applies_both", off())
        client.teleport(
            float(original["feet_x"]),
            float(original["feet_y"]) + 6,
            float(original["feet_z"]),
        )
        air_ready = settle(
            client, on_ground=False, still=None, ticks=1, timeout=3, clear_input=False
        )
        airborne = air_ready["state"]
        checks.check(
            "flight_air_fixture",
            air_ready["ok"] and float(airborne["feet_y"]) > float(original["feet_y"]) + 3,
            f"on_ground={airborne.get('on_ground')}, y={airborne.get('feet_y')}",
        )
        enabled = client.fly(True)
        wait_ticks(client, 3)
        flying = client.state()
        checks.check(
            "flight_enable_applies_both",
            enabled.get("client_applied") is True
            and enabled.get("server_applied") is True
            and flying.get("flying") is True
            and flying.get("mayfly") is True
            and flying.get("on_ground") is False,
            str(enabled),
        )
        checks.check(
            "flight_server_single_writer",
            enabled.get("application") == "server_with_client_readback"
            and enabled.get("client_observed") is True
            and enabled.get("assertion") == "readback",
            str(enabled),
        )
        checks.check("flight_enable_off_recovers", off())
        client.teleport(*(float(original[f"feet_{axis}"]) for axis in "xyz"))
        landed = settle(client, on_ground=True)
        checks.check("flight_air_returns_ground", landed["ok"], str(landed))
        enabled = client.toggle_fly(True)
        checks.check(
            "flight_toggle_server",
            enabled.get("server_applied") is True and enabled.get("mayfly") is True,
        )
        wait_ticks(client, 12)
        checks.check("flight_eight_tick_persists", client.state().get("mayfly") is True)
        checks.check("flight_invert_off", client.toggle_fly().get("mayfly") is False)
        checks.check("flight_invert_on", client.toggle_fly().get("mayfly") is True)
        checks.check("flight_toggle_stop", off())
        for stage in ("client", "server"):
            for mode, reason in (
                ("seh", "flight_fault"),
                ("cpp", "capture_failed"),
                ("timeout", "capture_timeout"),
            ):
                failed = client.request(inject(stage, mode), "error", timeout=4)
                checks.check(
                    f"flight_{stage}_{mode}",
                    failed.get("phase") == "flight"
                    and failed.get("reason") == reason
                    and failed.get("partial") is True,
                    str(failed),
                )
                checks.check(f"flight_{stage}_{mode}_unique", unique(int(failed["flight_id"])))
                checks.check(f"flight_{stage}_{mode}_recovery", off())
        for mode, reason in (("seh", "flight_assert_fault"), ("cpp", "flight_assert_failed")):
            queued = client.request(inject("assert", mode, 2000), "flight_queued")
            flight_id = int(queued["flight_id"])
            result = client.wait_event(
                ("error", "togglefly_ok", "flight_assertion_failed"),
                4,
                predicate=lambda row, wanted=flight_id: row.get("flight_id") == wanted,
            )
            if result.get("event") == "togglefly_ok":
                result = client.wait_event(
                    "flight_assertion_failed",
                    3,
                    predicate=lambda row, wanted=flight_id: row.get("flight_id") == wanted,
                )
            checks.check(f"flight_assert_{mode}", result.get("reason") == reason, str(result))
            checks.check(f"flight_assert_{mode}_unique", unique(flight_id))
            checks.check(f"flight_assert_{mode}_recovery", off())

        queued = client.request(inject("server", "timeout", 1000), "flight_queued")
        busy = client.request({"cmd": "fly", "enabled": True}, "error")
        checks.check("flight_busy_preserves_request", busy.get("reason") == "flight_busy")
        malformed = client.request({"cmd": "togglefly", "on": []}, "error")
        checks.check("flight_bad_param_preserves_request", malformed.get("reason") == "bad_command")
        failed = client.wait_event("error", 3, predicate=lambda row: row.get("phase") == "flight")
        checks.check(
            "flight_original_timeout",
            failed.get("flight_id") == queued["flight_id"]
            and failed.get("reason") == "capture_timeout",
        )
        checks.check("flight_busy_recovers", off())
        client.command("gamemode spectator @s")
        wait_ticks(client, 8)
        rejected = client.request({"cmd": "togglefly"}, "error")
        checks.check(
            "flight_spectator_rejects", rejected.get("reason") == "spectator_not_supported"
        )
        client.command("gamemode survival @s")
        wait_ticks(client, 8)
        checks.check("flight_spectator_recovers", off())

        if world:
            queued = client.request(inject("client", "timeout", 10000), "flight_queued")
            client.close()
            client.connect()
            client.input_lock(True)
            checks.check("flight_reconnect_fresh", off())
            checks.check("flight_reconnect_no_stale", unique(int(queued["flight_id"])))
            queued = client.request(inject("server", "timeout", 10000), "flight_queued")
            wait_ticks(client, 3)
            try:
                client.leave_world()
                client.wait_event("world_left", 45)
                cancelled = client.wait_event(
                    "error", 4, predicate=lambda row: row.get("phase") == "flight"
                )
                checks.check(
                    "flight_world_cancel",
                    cancelled.get("reason") == "world_left"
                    and cancelled.get("flight_id") == queued["flight_id"],
                    str(cancelled),
                )
                for command in ("fly", "togglefly"):
                    rejected = client.request({"cmd": command}, "error")
                    checks.check(f"flight_menu_{command}", rejected.get("reason") == "not_in_world")
            finally:
                client.enter_world_ready(world)
                client.wait_in_world(timeout=60)
            checks.check("flight_world_recovers", off())
    finally:
        if bytes_armed:
            # Diagnostic export only: never let it replace the first failure.
            try:
                rows = client.packet_bytes(0, 256)
                Path(f"{bytes_dump}.bytes.json").write_text(
                    json.dumps(rows, ensure_ascii=False), encoding="utf-8"
                )
                # seq -> tick/id mapping, so a violation tick can be matched to
                # the exact packet the sender serialized.
                # The game's working directory differs from the driver's. Wait
                # for this receipt so an export error cannot poison packet_drain.
                inbound_path = Path(f"{bytes_dump}.inbound.json").resolve()
                client.request({"cmd": "inbound_dump", "path": str(inbound_path)}, "inbound_dump")
                index = client.packet_drain(0, 1024)
                Path(f"{bytes_dump}.drain.json").write_text(
                    json.dumps(index, ensure_ascii=False), encoding="utf-8"
                )
                checks.note(
                    "flight_bytes_dump",
                    f"{rows.get('count', 0)} rows, "
                    f"{index.get('count', 0)} index -> {bytes_dump}.bytes.json",
                )
            except (ModPipeError, ModProtocolError, OSError) as exc:
                checks.note("flight_bytes_dump", f"failed: {exc}")
            with contextlib.suppress(ModPipeError, ModProtocolError):
                client.packet_capture(False)
        with preserve_failure(checks, "flight"):
            client.command(f"gamemode {original['gamemode']} @s")
            wait_ticks(client, 8)
            client.fly(bool(original.get("flying")))
            if int(original["gamemode"]) != 3:
                client.toggle_fly(bool(original.get("mayfly")))
            client.teleport(
                *(float(original[f"feet_{axis}"]) for axis in "xyz"),
                yaw=float(original["yaw"]),
                pitch=float(original["pitch"]),
            )
            wait_ticks(client, 10)
            require_world(client, "flight cleanup")
            effect_fixture.verify(client, checks, wait_ticks)
    restored = client.state()
    checks.check(
        "flight_restores_abilities",
        restored.get("mayfly") == original.get("mayfly")
        and restored.get("flying") == original.get("flying"),
    )
    effect_fixture.verify_restored(restored, checks)
    checks.check("flight_tick_alive", bool(client.ping()) and restored.get("in_world") is True)
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("flight_handled_fault_no_dump", after == before)


def check_input_events(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Exercise production event failures, text ordering, and UI thread queries."""
    if not checks.check("input_events_in_world", client.state().get("in_world") is True):
        return
    client.input_lock(True)
    if not checks.check(
        "input_events_locked", client.state().get("input", {}).get("locked") is True
    ):
        return
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}

    def inject(target: str, mode: str) -> dict[str, Any]:
        return {"cmd": "selftest_input_events", "target": target, "mode": mode}

    def event_done(input_id: int) -> dict[str, Any]:
        return client.wait_event(
            "input_event_done", 4, predicate=lambda row: row.get("input_id") == input_id
        )

    def text_done(input_id: int) -> dict[str, Any]:
        return client.wait_event(
            "text_input_done", 4, predicate=lambda row: row.get("input_id") == input_id
        )

    def unique(event: str, input_id: int) -> bool:
        try:
            client.wait_event(event, 0.12, predicate=lambda row: row.get("input_id") == input_id)
        except ModTimeout:
            return True
        return False

    checks.check("input_ui_screen", client.screen().get("name") == "hud_screen")
    for mode, reason in (("seh", "input_fault"), ("cpp", "input_failed")):
        queued = client.request(inject("key", mode), "key_event_queued")
        done = event_done(int(queued["input_id"]))
        checks.check(f"input_key_{mode}", done.get("ok") is False and done.get("reason") == reason)
        checks.check(
            f"input_key_{mode}_unique", unique("input_event_done", int(queued["input_id"]))
        )
        good = client.key_event(72, False, via="hardware")
        checks.check(
            f"input_key_{mode}_recovery", event_done(int(good["input_id"])).get("ok") is True
        )
        before_raw = client.key_state(72)
        raw = client.key_event(72, True, via="raw")
        event_done(int(raw["input_id"]))
        after_raw = client.key_state(72)
        controls = client.status().get("controls", {})
        # LL observers can run before a restored lock hook and count an event
        # that the game never receives. Verify the game state and our guard,
        # retaining the observer counters only as diagnostic evidence.
        checks.check(
            f"input_key_{mode}_guard_restored",
            before_raw.get("down") is False
            and after_raw.get("down") is False
            and isinstance(controls, dict)
            and controls.get("injecting_input") is False,
            str({"before": before_raw, "after": after_raw, "controls": controls}),
        )
        client.key_event(72, False, via="raw")

    for mode, reason in (
        ("seh", "ui_query_fault"),
        ("cpp", "capture_failed"),
        ("timeout", "capture_timeout"),
    ):
        client.send(inject("screen", mode))
        if mode == "timeout":
            client.ping()  # Dispatch fence: query is occupied before submitting again.
            busy = client.request({"cmd": "screen"}, "error")
            checks.check("input_ui_busy", busy.get("reason") == "ui_query_busy")
        failed = client.wait_event("error", 4, predicate=lambda row: row.get("phase") == "ui_query")
        checks.check(f"input_ui_{mode}", failed.get("reason") == reason, str(failed))
        checks.check(f"input_ui_{mode}_recovery", client.screen().get("name") == "hud_screen")

    for mode, reason in (
        ("seh", "text_input_fault"),
        ("cpp", "capture_failed"),
        ("timeout", "capture_timeout"),
    ):
        queued = client.request(inject("text", mode), "text_input_queued")
        if mode == "timeout":
            busy = client.request({"cmd": "text_input", "text": "must_not_replace"}, "error")
            checks.check(
                "input_text_busy_preserves_request", busy.get("reason") == "text_input_busy"
            )
            following = client.key_event(72, False)
            try:
                client.wait_event(
                    "input_event_done",
                    0.12,
                    predicate=lambda row, input_id=following["input_id"]: (
                        row.get("input_id") == input_id
                    ),
                )
                blocked = False
            except ModTimeout:
                blocked = True
            checks.check("input_text_blocks_following_keys", blocked)
            checks.check("input_text_ui_independent", client.screen().get("name") == "hud_screen")
        failed = text_done(int(queued["input_id"]))
        checks.check(
            f"input_text_{mode}",
            failed.get("ok") is False and failed.get("reason") == reason,
            str(failed),
        )
        checks.check(
            f"input_text_{mode}_unique", unique("text_input_done", int(queued["input_id"]))
        )
        if mode == "timeout":
            cancelled = event_done(int(following["input_id"]))
            checks.check(
                "input_text_failure_cancels_following",
                cancelled.get("ok") is False and cancelled.get("reason") == "input_cancelled",
                str(cancelled),
            )
        good = client.key_event(72, False)
        checks.check(
            f"input_text_{mode}_recovery", event_done(int(good["input_id"])).get("ok") is True
        )

    # One deferred text owns a slot. Another 127 events fit, and the next one
    # must reject explicitly instead of losing an old event or its result.
    queued = client.request(inject("text", "timeout"), "text_input_queued")
    wire = (json.dumps({"cmd": "key_event", "code": 72, "down": False}) + "\n") * 128
    client.send_raw(wire.encode())
    accepted: list[int] = []
    rejected = 0
    for _ in range(128):
        row = client.wait_event(("key_event_queued", "error"), 4)
        if row["event"] == "key_event_queued":
            accepted.append(int(row["input_id"]))
        elif row.get("reason") == "inject_queue_full":
            rejected += 1
    checks.check(
        "input_event_bounded_capacity",
        len(accepted) == 127 and rejected == 1,
        f"accepted={len(accepted)} rejected={rejected}",
    )
    checks.check(
        "input_event_full_text_timeout",
        text_done(int(queued["input_id"])).get("reason") == "capture_timeout",
    )
    checks.check(
        "input_event_full_cancellation",
        all(event_done(input_id).get("reason") == "input_cancelled" for input_id in accepted),
    )

    # No sleep between text and Enter: verify actual command execution through
    # the server's chat reply, rather than only checking that chat closed.
    token = f"input_order_{time.monotonic_ns()}"
    try:
        client.key_event(191)
        client.key_event(191, False)
        checks.check("input_order_chat_open", wait_screen(client, "chat_screen") == "chat_screen")
        failed_text = client.request(inject("text", "timeout"), "text_input_queued")
        cancelled_enter = client.key_event(13)
        cancelled_up = client.key_event(13, False)
        checks.check(
            "input_failed_text_terminal",
            text_done(int(failed_text["input_id"])).get("reason") == "capture_timeout",
        )
        checks.check(
            "input_failed_text_cancels_enter",
            event_done(int(cancelled_enter["input_id"])).get("reason") == "input_cancelled"
            and event_done(int(cancelled_up["input_id"])).get("reason") == "input_cancelled",
        )
        checks.check(
            "input_failed_text_keeps_chat_open", client.screen().get("name") == "chat_screen"
        )
        queued = client.text_input(f"say {token}")
        enter = client.key_event(13)
        client.key_event(13, False)
        checks.check("input_order_text_done", text_done(int(queued["input_id"])).get("ok") is True)
        checks.check("input_order_enter_done", event_done(int(enter["input_id"])).get("ok") is True)
        checks.check("input_order_chat_closed", wait_screen(client, "hud_screen") == "hud_screen")
        history = client.chat_history(limit=64)
        checks.check(
            "input_order_command_executed", token in json.dumps(history, ensure_ascii=False)
        )
    finally:
        if client.screen().get("name") == "chat_screen":
            client.key_event(27)
            client.key_event(27, False)
            wait_screen(client, "hud_screen")

    if world:
        client.key_event(72, True, via="hardware")
        wait_ticks(client, 2)
        client.close()
        client.connect()
        client.input_lock(True)
        wait_ticks(client, 3)
        checks.check("input_reconnect_releases_held", client.key_state(72).get("down") is False)
        queued = client.request(inject("text", "timeout"), "text_input_queued")
        client.key_event(72, True)
        try:
            client.leave_world()
            client.wait_event("world_left", 45)
            done = text_done(int(queued["input_id"]))
            checks.check("input_world_cancels_text", done.get("reason") == "world_left", str(done))
            checks.check("input_menu_screen_responsive", bool(client.screen().get("name")))
            rejected = client.request({"cmd": "key_state", "code": 72}, "error")
            checks.check("input_menu_key_rejected", rejected.get("reason") == "not_in_world")
        finally:
            client.enter_world_ready(world)
            client.wait_in_world(timeout=60)
            checks.check("input_world_still", settle(client, on_ground=True)["ok"])
        checks.check("input_world_no_stale_key", client.key_state(72).get("down") is False)
        checks.check("input_world_screen_recovery", client.screen().get("name") == "hud_screen")

    checks.check("input_tick_alive", bool(client.ping()) and client.state().get("in_world") is True)
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("input_handled_fault_no_dump", after == before)


def check_macro_playback(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Exercise real replay faults, release/restart, and cold frame replacement."""
    original = client.state()
    if not checks.check("playback_in_world", original.get("in_world") is True):
        return
    name = f"selftest_playback_{time.monotonic_ns()}"
    mod_dir = Path(str(client.status()["spool"])).parent
    path = mod_dir / "macros" / f"{name}.json"
    frame = ["true", *["false"] * 8]
    empty = ["false"] * 9

    def done(started: dict[str, Any]) -> dict[str, Any]:
        return client.wait_event(
            "macro_done", 4, predicate=lambda row: row.get("run_id") == started["run_id"]
        )

    def no_duplicate(started: dict[str, Any]) -> bool:
        try:
            client.wait_event(
                "macro_done", 0.2, predicate=lambda row: row.get("run_id") == started["run_id"]
            )
        except ModTimeout:
            return True
        return False

    def released() -> bool:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not client.macro_status().get("release_pending"):
                inputs = client.state()["input"]
                if not any(inputs.get(key) for key in ("w", "a", "s", "d", "jump", "sneak")):
                    return True
            time.sleep(0.02)
        return False

    try:
        client.stop_move()
        client.task_stop()
        client.macro_stop()
        client.macro_save(name, [empty] * 5)
        client.macro_load(name)
        rejected = client.request({"cmd": "selftest_macro_playback", "mode": "seh"}, "error")
        if not checks.check(
            "playback_fault_gate", rejected.get("reason") == "not_playing", str(rejected)
        ):
            return
        started = client.macro_run(start=1, end=4, event_level=True)
        finished = done(started)
        checks.check(
            "playback_range_terminal",
            finished.get("ok") is True
            and finished.get("reason") == "end"
            and finished.get("index") == 4
            and finished.get("name") == name,
            str(finished),
        )
        checks.check("playback_range_unique", no_duplicate(started))
        for events in (False, True):
            for mode, reason in (("seh", "playback_fault"), ("cpp", "playback_failed")):
                client.macro_save(name, [frame] * 4)
                client.macro_load(name)
                started = client.macro_run(loop=True, event_level=events)
                wait_ticks(client, 3)
                checks.check(
                    f"playback_{events}_{mode}_held", client.state()["input"].get("w") is True
                )
                client.request(
                    {"cmd": "selftest_macro_playback", "mode": mode}, "macro_playback_test_armed"
                )
                finished = done(started)
                checks.check(
                    f"playback_{events}_{mode}_terminal",
                    finished.get("ok") is False and finished.get("reason") == reason,
                    str(finished),
                )
                checks.check(f"playback_{events}_{mode}_released", released())
                checks.check(f"playback_{events}_{mode}_unique", no_duplicate(started))
                again = client.macro_run(event_level=events)
                checks.check(f"playback_{events}_{mode}_recovery", done(again).get("ok") is True)

        for mode in ("seh", "cpp"):
            started = client.macro_run(loop=True, event_level=True)
            wait_ticks(client, 3)
            client.request(
                {"cmd": "selftest_macro_playback", "target": "release", "mode": mode},
                "macro_playback_test_armed",
            )
            client.macro_stop()
            diagnostic = client.wait_event("macro_release_diagnostic", 3)
            checks.check(
                f"playback_release_{mode}_visible",
                int(diagnostic.get("faults" if mode == "seh" else "exceptions", 0)) >= 1,
                str(diagnostic),
            )
            checks.check(f"playback_release_{mode}_retry", released())
            again = client.macro_run(event_level=True)
            checks.check(f"playback_release_{mode}_recovery", done(again).get("ok") is True)

        started = client.macro_run(loop=True, event_level=True)
        wait_ticks(client, 3)
        rejected = client.request({"cmd": "macro_run", "loop": True}, "error")
        checks.check(
            "playback_busy_preserves_run",
            rejected.get("reason") == "macro_already_playing"
            and client.macro_status().get("run_id") == started["run_id"],
        )
        rejected = client.request({"cmd": "macro_run", "start": "bad"}, "error")
        checks.check(
            "playback_bad_param_preserves_run",
            rejected.get("event") == "error" and client.macro_status().get("playing") is True,
        )
        client.macro_save(name, [empty])
        loaded = client.macro_load(name)
        checks.check(
            "playback_load_shorter_stops",
            loaded.get("stopped") is True
            and loaded.get("ticks") == 1
            and not client.macro_status().get("playing"),
        )
        checks.check("playback_load_releases", released())
        for index in range(3):
            started = client.macro_run(event_level=True)
            checks.check(f"playback_short_restart_{index}", done(started).get("index") == 1)

        client.macro_save(name, [frame] * 4)
        client.macro_load(name)
        started = client.macro_run(loop=True, event_level=True)
        wait_ticks(client, 3)
        position = client.state()
        task = client.navigate(float(position["feet_x"]), float(position["feet_z"]), tolerance=1)
        checks.check(
            "playback_task_takes_ownership",
            task.get("ok") is True and client.macro_status().get("playing") is False,
        )
        checks.check("playback_task_releases", released())

        # Direct clicks retain their reply fields after formatting moved off tick.
        client.look(yaw=0, pitch=-80, relative=False)
        wait_ticks(client, 2)
        clicked = client.click("left")
        checks.check(
            "playback_click_cold_receipt",
            clicked.get("ok") is True
            and clicked.get("button") == "left"
            and clicked.get("game_mode") is True
            and all(key in clicked for key in ("hit_type", "block", "face", "hit_pos")),
            str(clicked),
        )
        for mode, reason in (("seh", "click_fault"), ("cpp", "click_failed")):
            client.request(
                {"cmd": "selftest_click_capture", "mode": mode}, "click_capture_test_armed"
            )
            failed = client.click("left")
            checks.check(
                f"playback_click_{mode}_isolated",
                failed.get("ok") is False and failed.get("reason") == reason,
                str(failed),
            )
            checks.check(f"playback_click_{mode}_recovery", client.click("left").get("ok") is True)
        click_frame = [*empty[:-1], "true"]
        client.macro_save(name, [click_frame, empty, empty])
        client.macro_load(name)
        started = client.macro_run(event_level=False)
        clicked = client.wait_event("click_result", 3)
        checks.check(
            "playback_legacy_click_receipt",
            clicked.get("ok") is True
            and clicked.get("button") == "left"
            and done(started).get("ok") is True,
        )

        for mode, reason in (("seh", "release_fault"), ("cpp", "release_failed")):
            client.macro_save(name, [frame] * 20)
            client.macro_load(name)
            started = client.macro_run(event_level=True)
            wait_ticks(client, 3)
            client.request(
                {"cmd": "selftest_macro_playback", "target": "release", "mode": mode},
                "macro_playback_test_armed",
            )
            finished = done(started)
            checks.check(
                f"playback_end_release_{mode}_terminal",
                finished.get("ok") is False and finished.get("reason") == reason,
                str(finished),
            )
            client.wait_event("macro_release_diagnostic", 3)
            checks.check(f"playback_end_release_{mode}_retry", released())
            checks.check(f"playback_end_release_{mode}_unique", no_duplicate(started))

        if world:
            started = client.macro_run(loop=True, event_level=True)
            try:
                wait_ticks(client, 3)
                client.leave_world()
                client.wait_event("world_left", 45)
                finished = done(started)
                checks.check(
                    "playback_world_cancel",
                    finished.get("ok") is False and finished.get("reason") == "world_left",
                    str(finished),
                )
                checks.check("playback_world_unique", no_duplicate(started))
                deadline = time.monotonic() + 45
                while not client.worlds().get("ready"):
                    if time.monotonic() >= deadline:
                        raise ModTimeout("macro playback: menu did not become ready")
                    time.sleep(0.1)
                rejected = client.request({"cmd": "macro_run"}, "error")
                checks.check("playback_menu_rejects", rejected.get("reason") == "not_in_world")
            finally:
                client.enter_world_ready(world)
                client.wait_in_world(timeout=60)
            again = client.macro_run(event_level=True)
            checks.check("playback_world_recovery", done(again).get("ok") is True)
    finally:
        with preserve_failure(checks, "macro_playback"):
            client.macro_stop()
            client.stop_move()
            client.teleport(
                *(float(original[f"feet_{axis}"]) for axis in "xyz"),
                yaw=float(original["yaw"]),
                pitch=float(original["pitch"]),
            )
            wait_ticks(client, 5)
            checks.check("playback_final_settle", settle(client, on_ground=True)["ok"])
            path.unlink(missing_ok=True)
