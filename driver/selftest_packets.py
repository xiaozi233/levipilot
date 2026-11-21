"""Owned command-packet template and injection acceptance checks."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from driver.harness import ModClient, ModProtocolError, ModTimeout
from driver.selftest_runtime import wait_screen, wait_ticks
from driver.selftest_support import Checks


def check_packet_injection(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Exercise the production template hook and owned send pipeline with readback."""
    if not client.status().get("packet_inject"):
        checks.skip("packet_owned_inject", "packet injection disabled")
        return
    try:
        client.packet_capture(True, ids=[77])
    except ModProtocolError as exc:
        checks.check("packet_owned_capture_gate", "packet_capture_disabled" in str(exc), str(exc))
        checks.skip("packet_owned_capture", str(exc))
        return
    original = client.state()
    client.input_lock(True)
    client.set_input()
    client.op_self()
    client.commands_enabled(True)
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    x, y, z = (math.floor(float(original[f"feet_{axis}"])) for axis in "xyz")
    candidates = [[x + dx, y, z + dz] for dx, dz in ((4, 4), (-4, 4), (4, -4), (-4, -4))]
    blocks = client.blocks(candidates)["blocks"]
    target = next(
        (
            cell
            for cell, block in zip(candidates, blocks, strict=True)
            if block.get("name") == "minecraft:air"
        ),
        None,
    )
    if not checks.check("packet_owned_air_fixture", target is not None):
        client.packet_capture(False)
        return
    assert target is not None
    tx, ty, tz = target
    command = f"/setblock {tx} {ty} {tz} minecraft:gold_block"

    def block() -> str:
        return str(client.blocks([target])["blocks"][0].get("name"))

    def clear() -> None:
        client.command(f"setblock {tx} {ty} {tz} air")
        wait_ticks(client, 4)
        if block() != "minecraft:air":
            raise ValueError("packet fixture failed to restore air")

    def complete(packet_id: int) -> dict[str, Any]:
        return client.wait_event(
            ("chat_sent", "error"), 4, predicate=lambda row: row.get("packet_id") == packet_id
        )

    def recovered() -> None:
        deadline = time.monotonic() + 4
        while client.status()["tick_recovery"]["active"]:
            if time.monotonic() >= deadline:
                raise ModTimeout("packet selftest: tick recovery did not finish")
            time.sleep(0.01)

    def unique(packet_id: int) -> bool:
        try:
            client.wait_event(
                "chat_sent", 0.12, predicate=lambda row: row.get("packet_id") == packet_id
            )
        except ModTimeout:
            return True
        return False

    def inject(mode: str, timeout: int = 250) -> dict[str, Any]:
        return client.request(
            {
                "cmd": "selftest_packet_injection",
                "mode": mode,
                "text": command,
                "timeout_ms": timeout,
            },
            "chat_send_queued",
        )

    def seed(mode: str = "off") -> None:
        client.packet_capture(True, ids=[77])
        client.key_event(191)
        client.key_event(191, False)
        if wait_screen(client, "chat_screen") != "chat_screen":
            raise ValueError("packet fixture failed to open chat")
        try:
            client.text_input("say harness_owned_template", wait=True)
            client.request(
                {"cmd": "selftest_packet_template", "mode": mode}, "packet_template_test_armed"
            )
            count = int(client.status()["command_template_attempts"])
            client.key_event(13)
            client.key_event(13, False)
            if wait_screen(client, "hud_screen") != "hud_screen":
                raise ValueError("packet fixture failed to submit chat")
            status = client.status()
            if int(status["command_template_attempts"]) <= count:
                raise ValueError("packet fixture did not capture the real command send")
            template = status.get("command_template", {})
            reason = {
                "off": "",
                "seh": "command_template_fault",
                "cpp": "command_template_fault",
                "long": "command_template_too_long",
            }[mode]
            checks.check(
                f"packet_template_status_{mode}",
                template.get("ready") is (mode == "off") and template.get("reason") == reason,
                str(template),
            )
        finally:
            if client.screen().get("name") == "chat_screen":
                client.key_event(27)
                client.key_event(27, False)
                wait_screen(client, "hud_screen")

    def no_template(name: str) -> None:
        queued = client.chat_send(command)
        row = complete(int(queued["packet_id"]))
        checks.check(
            name,
            row.get("ok") is False
            and row.get("reason") in ("no_command_template", "command_template_stale"),
            str(row),
        )
        checks.check(name + "_no_effect", block() == "minecraft:air")

    try:
        client.panic("tasks")
        no_template("packet_owned_no_template")
        seed()
        sent_before = int(client.status()["chat_sent"])
        done = client.chat_send(command, wait=True)
        wait_ticks(client, 4)
        checks.check(
            "packet_owned_success", done.get("ok") is True and block() == "minecraft:gold_block"
        )
        checks.check("packet_owned_success_unique", unique(int(done["packet_id"])))
        checks.check("packet_owned_count", int(client.status()["chat_sent"]) == sent_before + 1)
        clear()
        for mode, reason in (
            ("seh", "packet_inject_fault"),
            ("cpp", "packet_inject_failed"),
            ("timeout", "packet_inject_timeout"),
            ("queue", "packet_inject_queue_failed"),
            ("stale_epoch", "world_left"),
            ("wrong_owner", "command_template_owner_mismatch"),
            ("after_send", "packet_inject_fault"),
        ):
            accepted = inject(mode)
            packet_id = int(accepted["packet_id"])
            result = complete(packet_id)
            checks.check(
                f"packet_owned_{mode}",
                result.get("ok") is False
                and result.get("reason") == reason
                and result.get("partial") is True,
                str(result),
            )
            checks.check(f"packet_owned_{mode}_unique", unique(packet_id))
            wait_ticks(client, 4)
            expected = "minecraft:gold_block" if mode == "after_send" else "minecraft:air"
            checks.check(f"packet_owned_{mode}_effect", block() == expected, block())
            clear()
        for mode, reason in (
            ("seh", "command_template_fault"),
            ("cpp", "command_template_fault"),
            ("long", "command_template_too_long"),
        ):
            seed(mode)
            queued = client.chat_send(command)
            result = complete(int(queued["packet_id"]))
            checks.check(
                f"packet_template_{mode}",
                result.get("ok") is False and result.get("reason") == reason,
                str(result),
            )
            checks.check(f"packet_template_{mode}_no_fallback", block() == "minecraft:air")
            seed()
            done = client.chat_send(command, wait=True)
            wait_ticks(client, 4)
            checks.check(
                f"packet_template_{mode}_recovers",
                done.get("ok") is True and block() == "minecraft:gold_block",
            )
            clear()
        accepted = inject("timeout", 10000)
        busy = client.request({"cmd": "chat_send", "text": "/say must_not_replace"}, "error")
        checks.check("packet_owned_busy", busy.get("reason") == "packet_inject_busy")
        client.panic("tasks")
        result = complete(int(accepted["packet_id"]))
        checks.check(
            "packet_owned_panic", result.get("reason") == "panic" and result.get("text") == command
        )
        checks.check("packet_owned_panic_unique", unique(int(accepted["packet_id"])))
        no_template("packet_owned_panic_template_invalid")
        seed()
        accepted = inject("timeout", 10000)
        client.request(
            {"cmd": "selftest_tick_error", "mode": "std", "phase": "pre"}, "tick_error_test_armed"
        )
        client.wait_event(
            "error",
            4,
            predicate=lambda row: (
                row.get("reason") == "internal_error" and row.get("phase") == "tick_pre"
            ),
        )
        result = complete(int(accepted["packet_id"]))
        checks.check("packet_owned_tick_cancel", result.get("reason") == "internal_error")
        recovered()
        no_template("packet_owned_tick_template_invalid")
        if world:
            seed()
            accepted = inject("timeout", 10000)
            client.close()
            client.connect()
            client.input_lock(True)
            no_template("packet_owned_reconnect_template_invalid")
            checks.check("packet_owned_reconnect_no_stale", unique(int(accepted["packet_id"])))
            seed()
            accepted = inject("timeout", 10000)
            try:
                client.leave_world()
                client.wait_event("world_left", 45)
                result = complete(int(accepted["packet_id"]))
                checks.check("packet_owned_world_cancel", result.get("reason") == "world_left")
                row = client.request({"cmd": "chat_send", "text": command}, "error")
                checks.check("packet_owned_menu_rejects", row.get("reason") == "not_in_world")
            finally:
                client.enter_world_ready(world)
                client.wait_in_world(timeout=60)
            no_template("packet_owned_world_template_invalid")
        seed()
        done = client.chat_send(command, wait=True)
        wait_ticks(client, 4)
        checks.check(
            "packet_owned_recovers", done.get("ok") is True and block() == "minecraft:gold_block"
        )
    finally:
        recovered()
        client.request(
            {"cmd": "selftest_packet_template", "mode": "off"}, "packet_template_test_armed"
        )
        clear()
        client.packet_capture(False)
    checks.check("packet_owned_fixture_restored", block() == "minecraft:air")
    checks.check(
        "packet_owned_alive", bool(client.ping()) and client.state().get("in_world") is True
    )
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("packet_owned_handled_fault_no_dump", before == after)
