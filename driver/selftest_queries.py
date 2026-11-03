"""Bounded query, entity-slot and ECS capture lifecycle checks."""

from __future__ import annotations

import contextlib
import math
import time
from pathlib import Path
from typing import Any

from driver.harness.lab import settle
from driver.selftest_runtime import wait_ticks
from driver.selftest_support import Checks
from driver.transport_mod import ModClient, ModTimeout


def check_query_capture(client: ModClient, checks: Checks) -> None:
    """Exercise the real fixed snapshot/SEH/consumer path, including bounded scans."""
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    for target in ("state", "buttons", "inventory", "blocks", "scan"):
        for mode, reason in (
            ("seh", f"{target}_fault"),
            ("cpp", "capture_failed"),
            ("timeout", "capture_timeout"),
        ):
            result = client.request(
                {"cmd": "selftest_query_failure", "target": target, "mode": mode},
                (target, "error"),
            )
            if result.get("reason") == "selftest_stress_disabled":
                checks.skip("query_fault_recovery", "allow_selftest_stress is disabled")
                return
            checks.check(
                f"query_{target}_{mode}_terminal",
                result.get("event") == "error"
                and result.get("reason") == reason
                and result.get("phase") == target,
                str(result),
            )
            duplicate = None
            with contextlib.suppress(ModTimeout):
                duplicate = client.wait_event((target, "error"), 0.03)
            checks.check(f"query_{target}_{mode}_unique", duplicate is None, str(duplicate or ""))
            if target == "state":
                recovered = client.state()
            elif target == "buttons":
                recovered = client.request({"cmd": "buttons"}, "buttons")
            elif target == "inventory":
                recovered = client.inventory()
            elif target == "blocks":
                recovered = client.blocks([])
            else:
                recovered = client.scan([0, -60, 0], [0, -60, 0], include_air=True)
            checks.check(
                f"query_{target}_{mode}_recovery",
                recovered.get("event") == target and wait_ticks(client, 2) >= 2,
            )
    partial = client.request(
        {"cmd": "selftest_query_failure", "target": "state", "mode": "partial"}, "state"
    )
    checks.check(
        "query_partial_state",
        partial.get("partial") is True
        and partial.get("warnings") == ["state_blocks_fault"]
        and "block_feet" not in partial
        and "block_below" in partial,
    )
    checks.check("query_partial_recovers", client.state().get("partial") is False)

    # Reserve the inventory slot long enough to verify explicit backpressure.
    with client.exchange():
        client.send({"cmd": "selftest_query_failure", "target": "inventory", "mode": "timeout"})
        client.send({"cmd": "inventory"})
        busy = client.wait_event("error", 3)
        terminal = client.wait_event("error", 3)
    checks.check(
        "query_inventory_busy",
        busy.get("reason") == "inventory_busy"
        and terminal.get("reason") == "capture_timeout"
        and terminal.get("phase") == "inventory"
        and client.inventory().get("event") == "inventory",
    )

    # 32768 air cells with limit=1 still require all 64 bounded batches.
    pose = client.state()
    x, y, z = [math.floor(float(pose[f"feet_{axis}"])) for axis in "xyz"]
    low, high = [x, y + 8, z], [x + 31, y + 39, z + 31]
    scan = client.scan(high, low, include_air=False, limit=1)
    checks.check(
        "query_scan_budget",
        scan.get("visited") == 32768
        and scan.get("batches") == 64
        and scan.get("batch_limit") == 512
        and int(scan.get("last_tick", 0)) - int(scan.get("first_tick", 0)) >= 63
        and scan.get("from") == low
        and scan.get("to") == high,
        f"visited={scan.get('visited')} batches={scan.get('batches')} max_batch_us={scan.get('max_batch_us')}",
    )
    limited = client.scan(low, [x + 2, y + 8, z], include_air=True, limit=2)
    checks.check(
        "query_scan_limit",
        limited.get("count") == 2
        and limited.get("truncated") is True
        and limited.get("truncated_reason") == "result_limit"
        and [row["x"] for row in limited["blocks"]] == [x, x + 1],
    )
    # An occupied snapshot slot must reject a second request explicitly.
    with client.exchange():
        client.send({"cmd": "scan", "from": low, "to": high, "limit": 1})
        client.send({"cmd": "scan", "from": low, "to": low})
        rejected = client.wait_event("error", 3)
        completed = client.wait_event("scan", 10)
    checks.check(
        "query_scan_busy",
        rejected.get("reason") == "scan_busy" and completed.get("visited") == 32768,
    )
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("query_handled_fault_no_dump", after == before)
    check_entity_slot_capture(client, checks)


def check_entity_slot_capture(client: ModClient, checks: Checks) -> None:
    """Exercise all three entity stages and tick-owned slot receipts."""
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    inventory = client.inventory()
    selected = int(inventory["selected_slot"])
    empty_slot = next(
        (int(row["slot"]) for row in inventory["slots"][:9] if row["item"] is None), None
    )
    wrote_item = False
    self_id = int(client.state()["runtime_id"])
    with contextlib.suppress(ModTimeout):
        while True:
            client.wait_event("select_slot_done", 0.02)
    try:
        for target, stage in (
            ("select_slot", "client"),
            ("entities", "client"),
            ("entities", "server"),
            ("entities", "aim"),
        ):
            event = "select_slot_done" if target == "select_slot" else "entities"
            for mode, reason in (
                ("seh", f"{target}_fault"),
                ("cpp", "capture_failed"),
                ("timeout", "capture_timeout"),
            ):
                result = client.request(
                    {
                        "cmd": "selftest_query_failure",
                        "target": target,
                        "stage": stage,
                        "runtime_id": self_id,
                        "mode": mode,
                    },
                    (event, "error"),
                )
                if result.get("reason") == "selftest_stress_disabled":
                    checks.skip("entity_slot_fault_recovery", "allow_selftest_stress is disabled")
                    return
                label = f"query_{target}_{stage}_{mode}"
                checks.check(
                    f"{label}_terminal",
                    result.get("event") == "error"
                    and result.get("reason") == reason
                    and result.get("phase") == target,
                    str(result),
                )
                duplicate = None
                with contextlib.suppress(ModTimeout):
                    duplicate = client.wait_event((event, "error"), 0.05)
                checks.check(f"{label}_unique", duplicate is None, str(duplicate or ""))
                if target == "select_slot":
                    client.select_slot(selected)
                    recovered = client.wait_event("select_slot_done", 3)
                    ok = recovered.get("ok") is True and recovered.get("applied") == selected
                else:
                    recovered = client.entity(self_id)
                    ok = recovered.get("runtime_id") == self_id
                checks.check(f"{label}_recovery", ok and wait_ticks(client, 2) >= 2)

        for target in ("select_slot", "entities"):
            with client.exchange():
                client.send({"cmd": "selftest_query_failure", "target": target, "mode": "timeout"})
                client.send({"cmd": target})
                busy = client.wait_event("error", 3)
                terminal = client.wait_event("error", 3)
            checks.check(
                f"query_{target}_busy",
                busy.get("reason") == f"{target}_busy"
                and terminal.get("reason") == "capture_timeout"
                and terminal.get("phase") == target,
            )

        # Preserve the old ack/completion contract and clamp/readback behavior.
        for requested in (-1, 0, 4, 8, 9):
            expected = min(8, max(0, requested))
            ack = client.select_slot(requested)
            done = client.wait_event("select_slot_done", 3)
            current = client.inventory()
            item = current["slots"][expected]["item"]
            expected_item = None if item is None else {"type": item["type"], "count": item["count"]}
            checks.check(
                f"select_slot_receipt_{requested}",
                ack.get("event") == "select_slot_ok"
                and done.get("slot") == expected
                and done.get("applied") == expected
                and done.get("ok") is True
                and done.get("item") == expected_item
                and current.get("selected_slot") == expected,
                str(done),
            )
        rejected = client.request({"cmd": "entities", "radius": -1}, "error")
        checks.check("entities_reject_radius", rejected.get("reason") == "bad_radius")
        if checks.check("select_slot_item_fixture", empty_slot is not None):
            assert empty_slot is not None
            wrote_item = True
            client.set_item(empty_slot, "minecraft:stone", count=3)
            client.select_slot(empty_slot)
            done = client.wait_event("select_slot_done", 3)
            checks.check(
                "select_slot_item_receipt",
                done.get("ok") is True
                and done.get("item") == {"type": "minecraft:stone", "count": 3},
                str(done),
            )
    finally:
        if wrote_item and empty_slot is not None:
            client.clear_item(empty_slot)
        client.select_slot(selected)
        client.wait_event("select_slot_done", 3)
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("entity_slot_handled_fault_no_dump", after == before)


def check_query_lifecycle(client: ModClient, checks: Checks, world: str) -> None:
    """Cancel a real batched scan on disconnect/exit, then verify fresh queries."""
    original = client.state()
    if not checks.check("query_lifecycle_in_world", original.get("in_world") is True):
        return
    x, y, z = [math.floor(float(original[f"feet_{axis}"])) for axis in "xyz"]
    low, high = [x, y + 8, z], [x + 31, y + 39, z + 31]
    scan = {"cmd": "scan", "from": low, "to": high, "limit": 1}
    client.send(scan)
    client.send({"cmd": "selftest_query_failure", "target": "entities", "mode": "timeout"})
    client.send({"cmd": "selftest_query_failure", "target": "select_slot", "mode": "timeout"})
    client.ping()  # Ensure submission reached the mod before closing the pipe.
    client.reconnect()
    # Already queued rows are intentionally replayed; they precede new work.
    for _ in range(int(client.hello.get("replay_rows", 0))):
        client.readline(timeout=5)
    client.input_lock(True)
    fresh = client.scan(low, low, include_air=True)
    checks.check(
        "query_lifecycle_reconnect",
        fresh.get("from") == low and fresh.get("visited") == 1 and fresh.get("count") == 1,
        str(fresh),
    )
    late = None
    with contextlib.suppress(ModTimeout):
        late = client.wait_event(("scan", "error"), 0.2)
    checks.check("query_lifecycle_no_stale_scan", late is None, str(late or ""))
    checks.check(
        "query_lifecycle_entity_reconnect",
        client.entities(limit=1).get("event") == "entities",
    )
    client.select_slot(int(original["selected_slot"]))
    checks.check(
        "query_lifecycle_slot_reconnect",
        client.wait_event("select_slot_done", 3).get("ok") is True,
    )

    try:
        with client.exchange():
            client.send(scan)
            client.send(
                {
                    "cmd": "selftest_query_failure",
                    "target": "entities",
                    "mode": "timeout",
                    "timeout_ms": 10000,
                }
            )
            client.send(
                {
                    "cmd": "selftest_query_failure",
                    "target": "select_slot",
                    "mode": "timeout",
                    "timeout_ms": 10000,
                }
            )
            left = client.leave_world()
            checks.check("query_lifecycle_leave", left.get("ok") is True, str(left))
            client.wait_event("world_left", 45)
            cancelled = client.wait_event(("scan", "error"), 5)
            other_cancellations = [client.wait_event("error", 5), client.wait_event("error", 5)]
        cancellations = [cancelled, *other_cancellations]
        scan_cancelled = next((row for row in cancellations if row.get("phase") == "scan"), {})
        checks.check(
            "query_lifecycle_world_cancel",
            scan_cancelled.get("event") == "error" and scan_cancelled.get("reason") == "world_left",
            str(scan_cancelled),
        )
        # Consumption order is unspecified across query domains.
        checks.check(
            "query_lifecycle_entity_slot_cancel",
            {row.get("phase") for row in cancellations} == {"scan", "entities", "select_slot"}
            and all(row.get("reason") == "world_left" for row in cancellations),
            str(cancellations),
        )
        deadline = time.monotonic() + 45
        while not client.worlds().get("ready"):
            if time.monotonic() >= deadline:
                raise ModTimeout("query lifecycle: main menu did not become ready")
            time.sleep(0.1)
        checks.check("query_lifecycle_menu_state", client.state().get("in_world") is False)
        refused = client.request(scan, ("scan", "error"))
        checks.check("query_lifecycle_menu_scan", refused.get("reason") == "not_in_world")
        for command in ("entities", "select_slot"):
            refused = client.request({"cmd": command}, "error")
            checks.check(f"query_lifecycle_menu_{command}", refused.get("reason") == "not_in_world")
    finally:
        joined = client.enter_world_ready(world)
        checks.check("query_lifecycle_rejoin", joined.get("ok") is True, str(joined))
        client.wait_in_world(timeout=60)
        client.reset(
            *(float(original[f"feet_{axis}"]) for axis in "xyz"),
            yaw=float(original["yaw"]),
            pitch=float(original["pitch"]),
        )
        wait_ticks(client, 8)
        recovered = client.scan(low, low, include_air=True)
        checks.check("query_lifecycle_world_recovery", recovered.get("visited") == 1)
        checks.check(
            "query_lifecycle_entity_recovery", client.entities(limit=1).get("event") == "entities"
        )
        client.select_slot(int(original["selected_slot"]))
        checks.check(
            "query_lifecycle_slot_recovery",
            client.wait_event("select_slot_done", 3).get("ok") is True,
        )


def check_ecs_capture(client: ModClient, checks: Checks, world: str | None = None) -> None:
    """Read both registries through owned stages, then fault and retire requests."""
    original = client.state()
    if not checks.check("ecs_capture_in_world", original.get("in_world") is True):
        return
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    runtime_id = int(original["runtime_id"])

    def unique() -> bool:
        with contextlib.suppress(ModTimeout):
            client.wait_event(
                (
                    "error",
                    "ecs_registries",
                    "ecs_entity",
                    "ecs_components",
                    "ecs_component",
                    "ecs_context",
                ),
                0.15,
                predicate=lambda row: row.get("event") != "error" or row.get("phase") == "ecs",
            )
            return False
        return True

    def inject(stage: str, mode: str, timeout_ms: int = 250) -> dict[str, Any]:
        return {
            "cmd": "selftest_query_failure",
            "target": "ecs",
            "stage": stage,
            "mode": mode,
            "timeout_ms": timeout_ms,
            "runtime_id": runtime_id,
        }

    registries = client.ecs_registries()
    checks.check(
        "ecs_client_registry",
        registries.get("scope") == "client"
        and registries.get("truncated") is False
        and int(registries["registries"][0]["components"]) > 0,
    )
    entity = client.ecs_entity(runtime_id)
    checks.check(
        "ecs_server_entity",
        entity.get("scope") == "server" and int(entity.get("components", 0)) > 0,
    )
    listing = client.ecs_components(runtime_id)
    rows = listing["components"]
    checks.check(
        "ecs_bounded_component_rows",
        listing.get("scope") == "server"
        and len(rows) <= 1024
        and int(listing.get("visited", 0)) <= 4096
        and listing.get("truncated") is False,
        f"rows={len(rows)} skipped={listing.get('skipped')} visited={listing.get('visited')}",
    )
    state_vector = next((row for row in rows if row.get("name") == "StateVector"), None)
    if not checks.check("ecs_known_component", state_vector is not None):
        return
    assert state_vector is not None
    component_id = int(state_vector["id"])
    client_view = client.ecs_context(int(original["context"]), component_id)
    server_view = client.ecs_context(int(entity["context"]), component_id)
    checks.check(
        "ecs_client_context_scope",
        client_view.get("scope") == "client"
        and client_view.get("registry") == registries["registries"][0]["registry"],
    )
    checks.check(
        "ecs_server_context_scope",
        server_view.get("scope") == "server"
        and server_view.get("registry") == entity["registry"]
        and server_view.get("address") == state_vector["address"],
    )
    for length, expected in ((0, int(state_vector["size"])), (1, 1), (512, 512), (999, 512)):
        raw = client.ecs_component(runtime_id, component_id, length=length)
        checks.check(
            f"ecs_component_length_{length}",
            raw.get("ok") is True
            and raw.get("len") == expected
            and len(raw.get("hex", "")) == expected * 2,
        )
    for name, request, reason in (
        ("missing_entity", {"cmd": "ecs_entity", "runtime_id": (1 << 63) - 1}, "entity_not_found"),
        (
            "missing_component",
            {
                "cmd": "ecs_component",
                "runtime_id": runtime_id,
                "component_id": next(i for i in range(100) if i not in {r["id"] for r in rows}),
            },
            "component_not_found",
        ),
        (
            "bad_context",
            {"cmd": "ecs_context", "context": "0x41410000", "component_id": component_id},
            "bad_addr",
        ),
    ):
        rejected = client.request(request, "error")
        checks.check(f"ecs_{name}", rejected.get("reason") == reason, str(rejected))

    for stage in ("client", "server"):
        for mode, reason in (
            ("seh", "ecs_fault"),
            ("cpp", "capture_failed"),
            ("timeout", "capture_timeout"),
        ):
            failed = client.request(inject(stage, mode), "error", timeout=5)
            if not checks.check(
                f"ecs_{stage}_{mode}_terminal",
                failed.get("phase") == "ecs" and failed.get("reason") == reason,
                str(failed),
            ):
                return
            checks.check(f"ecs_{stage}_{mode}_unique", unique())
            checks.check(
                f"ecs_{stage}_{mode}_recovery",
                client.ecs_entity(runtime_id).get("scope") == "server",
            )

    partial = client.request(inject("server", "partial"), "ecs_components")
    checks.check(
        "ecs_storage_fault_preserves_rows",
        partial.get("partial") is True
        and len(partial.get("skipped", [])) == 1
        and len(partial["components"]) > 0,
    )
    recovered = client.ecs_components(runtime_id)
    checks.check(
        "ecs_storage_fault_recovers",
        recovered.get("skipped") == listing.get("skipped")
        and component_id in {row["id"] for row in recovered["components"]},
        # Transient ECS tags can disappear between these requests; compare
        # failures and the stable profile component, not every gameplay tag.
        str({"skipped": recovered.get("skipped"), "components": len(recovered["components"])}),
    )
    with client.exchange():
        client.send(inject("client", "timeout", 500))
        rejected = client.request({"cmd": "ecs_registries"}, "error")
        checks.check("ecs_busy_preserves_request", rejected.get("reason") == "ecs_busy")
        malformed = client.request({"cmd": "ecs_component", "len": "bad"}, "error")
        checks.check(
            "ecs_bad_param_preserves_request",
            malformed.get("reason") == "bad_command",
            str(malformed),
        )
        failed = client.wait_event("error", 3, predicate=lambda row: row.get("phase") == "ecs")
        checks.check("ecs_original_timeout", failed.get("reason") == "capture_timeout")
    checks.check("ecs_busy_recovers", bool(client.ecs_registries().get("registries")))

    if world:
        client.send(inject("server", "timeout", 10000))
        client.ping()
        client.close()
        client.connect()
        client.input_lock(True)
        checks.check("ecs_reconnect_fresh", bool(client.ecs_registries().get("registries")))
        checks.check("ecs_reconnect_no_old_result", unique())
        client.send(inject("server", "timeout", 10000))
        client.ping()
        wait_ticks(client, 3)
        try:
            client.leave_world()
            client.wait_event("world_left", 45)
            failed = client.wait_event("error", 4, predicate=lambda row: row.get("phase") == "ecs")
            checks.check("ecs_world_cancel", failed.get("reason") == "world_left", str(failed))
            checks.check("ecs_world_unique", unique())
            for command in (
                "ecs_registries",
                "ecs_entity",
                "ecs_components",
                "ecs_component",
                "ecs_context",
            ):
                rejected = client.request({"cmd": command}, "error")
                checks.check(f"ecs_menu_{command}", rejected.get("reason") == "not_in_world")
        finally:
            client.enter_world_ready(world)
            client.wait_in_world(timeout=60)
            restored = settle(client, on_ground=True)
            checks.check("ecs_world_still", restored["ok"])
        fresh_state = client.state()
        client.ecs_entity(int(fresh_state["runtime_id"]))
        fresh = client.ecs_context(int(fresh_state["context"]), component_id)
        checks.check("ecs_world_fresh_context", fresh.get("scope") == "client")
        stale = client.request(
            {"cmd": "ecs_context", "context": original["context"], "component_id": component_id},
            ("ecs_context", "error"),
        )
        # Addresses may be recycled; a successful lookup must still prove a current registry.
        checks.check(
            "ecs_stale_handle_safe",
            stale.get("event") == "error" or stale.get("scope") in ("client", "server"),
        )

    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("ecs_handled_fault_no_dump", after == before)
