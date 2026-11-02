"""Live diagnostics checks kept out of the monolithic selftest entry point."""

from __future__ import annotations

import itertools
import json
import time
from collections.abc import Callable
from pathlib import Path

from driver.harness.errors import ModPipeError, ModProtocolError, ModTimeout
from driver.harness.lab import inbound_slices
from driver.selftest_fixtures import CrashSnapshot
from driver.selftest_support import Checks, preserve_failure
from driver.transport_mod import ModClient

ROOT = Path(__file__).resolve().parents[1]


def role_address(role: str) -> tuple[int, int] | None:
    """Return a documented static role VA and image base, never a guessed address."""
    path = ROOT / "docs" / "roles" / "1.26.51.01.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entry = data.get("roles", {}).get(role, {})
    address = entry.get("address")
    base = data.get("image_base")
    if entry.get("status") != "resolved" or not address or not base:
        return None
    return int(str(address), 16), int(str(base), 16)


def violation_watch_address(client: ModClient, role: str) -> int | None:
    role_info = role_address(role)
    if role_info is None:
        return None
    role_va, image_base = role_info
    module_base = int(client.diag().get("module_base", 0))
    if module_base == 0 or role_va < image_base:
        return None
    return module_base + (role_va - image_base)


def check_inbound_slices(client: ModClient, checks: Checks) -> None:
    """Verify ids, actual sample boundaries, completeness and sequence."""
    rows = inbound_slices(client, since=0, limit=256)
    decoded = [row for row in rows if row.get("decoded")]
    if not checks.check(
        "inbound_slice_rows", len(decoded) >= 4, f"rows={len(rows)} decoded={len(decoded)}"
    ):
        return
    checks.check(
        "inbound_slice_packet_id",
        all(0 <= row["decoded"]["packet_id"] < 1000 for row in decoded),
        str(sorted({row["decoded"]["packet_id"] for row in decoded})[:12]),
    )
    ids = {row["decoded"]["packet_id"] for row in decoded}
    checks.check(
        "inbound_slice_known_ids", bool(ids & {39, 111, 144, 187, 188}), f"ids={sorted(ids)[:12]}"
    )
    checks.check(
        "inbound_slice_boundaries",
        all(
            row["decoded"]["captured_bytes"] * 2 == len(row["slice_hex"])
            and row["decoded"]["captured_bytes"] <= row["size"]
            and row["decoded"]["sample_complete"]
            == (not row["decoded"]["truncated"] and row["decoded"]["captured_bytes"] == row["size"])
            for row in decoded
        ),
        f"complete={sum(row['decoded']['sample_complete'] is True for row in decoded)}/{len(decoded)}",
    )
    checks.check(
        "inbound_slice_complete_samples",
        any(row["decoded"]["sample_complete"] is True for row in decoded),
        "at least one ordinary packet must be captured in full",
    )
    checks.check(
        "inbound_slice_sequence",
        all(
            isinstance(row["seq"], int)
            and isinstance(row["index"], int)
            and row["seq"] > 0
            and row["index"] > 0
            and int(row["tid"] or 0) > 0
            for row in decoded
        )
        and all(
            (right["seq"], right["index"]) > (left["seq"], left["index"])
            for left, right in itertools.pairwise(decoded)
        ),
        f"first={decoded[0].get('seq')}/{decoded[0].get('index')} last={decoded[-1].get('seq')}/{decoded[-1].get('index')}",
    )
    checks.check(
        "inbound_slice_no_framing_assumption",
        all(
            row["decoded"]["declared"] is None
            and row["decoded"]["framing_applicable"] is False
            and row["verdict"] == "unknown"
            for row in decoded
        ),
        "deep-copy path has no length varint; adjacent heap bytes remain undecoded",
    )


def check_violation_watch(
    client: ModClient, checks: Checks, wait_ticks: Callable[..., int]
) -> None:
    """Exercise synthetic records; this never reproduces a real BadPacket."""
    if not checks.check("violation_watch_in_world", client.state().get("in_world") is True):
        return
    directory = Path(str(client.status()["spool"])).parent
    before = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    addr = violation_watch_address(client, "@client_handle_packet_violation")
    if not checks.check(
        "violation_watch_role", addr is not None, f"runtime address={hex(addr) if addr else None}"
    ):
        return
    assert addr is not None
    was_active = client.violation_stats().get("active") is True
    since = int(client.violation_drain(0, 1).get("head", 0))
    try:
        started = client.violation_watch(True, addr=addr)
        if started.get("event") == "error":
            checks.check(
                "violation_watch_gate",
                started.get("reason") == "violation_watch_disabled",
                str(started),
            )
            checks.skip("violation_watch_observation", "allow_hook_va is disabled")
            return
        checks.check(
            "violation_watch_start",
            started.get("active") is True and bool(started.get("address")),
            str(started),
        )
        checks.check(
            "violation_watch_screen_hooked", started.get("screen_hooked") is True, str(started)
        )
        stats = client.violation_stats()
        checks.check(
            "inbound_freeze_clear",
            client.violation_stats().get("inbound_frozen") is False,
            "a healthy session must not be frozen",
        )
        checks.check(
            "violation_watch_stats_active",
            stats.get("active") is True
            and int(stats.get("capacity", 0)) > 0
            and stats.get("screen_hooked") is True,
            str(stats),
        )
        route = client.disconnect_route(90)
        checks.check(
            "disconnect_route_shape",
            "reason=90" in str(route.get("route", ""))
            and "codeword=Block" in str(route.get("route", "")),
            str(route),
        )
        synthetic = client.violation_watch(mode="synthetic")
        checks.check("violation_synthetic_ok", synthetic.get("ok") is True, str(synthetic))
        checks.check(
            "inbound_freeze_ignores_synthetic",
            client.violation_stats().get("inbound_frozen") is False,
            "the synthetic row never goes through the callback hook",
        )
        long_details = "page " + "x" * 600
        client.request(
            {
                "cmd": "disconnect_route",
                "reason": 90,
                "codeword": "Block",
                "details": long_details,
            },
            "disconnect_route",
        )
        texts = client.violation_text(0, 8).get("hits", [])
        callback_text = next((row for row in texts if row.get("kind") == "callback"), None)
        screen_text = next(
            (
                row
                for row in texts
                if row.get("kind") == "screen" and row.get("text") == long_details
            ),
            None,
        )
        checks.check(
            "violation_text_full_length",
            screen_text is not None
            and screen_text.get("len") == len(long_details)
            and screen_text.get("truncated") is False,
            str(screen_text)[:120],
        )
        checks.check(
            "violation_text_callback",
            callback_text is not None and callback_text.get("text") == "synthetic violation record",
            str(callback_text)[:120],
        )
        drained = client.violation_drain(since, 64)
        rows = drained.get("hits", [])
        row = next(
            (item for item in rows if item.get("synthetic") and item.get("source") == "callback"),
            None,
        )
        checks.check(
            "violation_drain_row",
            row is not None
            and row.get("response") == 3
            and row.get("verdict") == "terminate_connection"
            and row.get("packet_id") == -1,
            str(row),
        )
        checks.check(
            "violation_drain_context",
            row is not None
            and row.get("context") == "synthetic violation record"
            and row.get("context_truncated") is False
            and int(row.get("tick", -1)) >= 0,
            str(row),
        )
        rows_screen = client.violation_drain(since, 64).get("hits", [])
        screen_row = next((item for item in rows_screen if item.get("source") == "screen"), None)
        checks.check(
            "violation_drain_screen_row",
            screen_row is not None
            and screen_row.get("response") == 90
            and screen_row.get("context") == "Block"
            and screen_row.get("synthetic") is True
            and screen_row.get("disconnect_reason") == 90
            and screen_row.get("verdict") == "disconnect",
            str(screen_row),
        )
        checks.check(
            "violation_drain_cursor",
            int(drained.get("next", -1)) >= int(drained.get("count", 0))
            and client.violation_drain(int(drained.get("next", 0)), 64).get("count") == 0,
            str({k: drained.get(k) for k in ("next", "count", "lost")}),
        )
        checks.check("violation_watch_ping", client.ping().get("event") == "pong")
        client.disconnect_route(0)
        reason_zero = client.violation_drain(int(drained["next"]), 64).get("hits", [])
        checks.check(
            "violation_screen_reason_not_verdict",
            any(
                item.get("source") == "screen"
                and item.get("response") == 0
                and item.get("verdict") == "disconnect"
                and item.get("synthetic") is True
                for item in reason_zero
            ),
            str(reason_zero),
        )
        client.command("time set day")
        wait_ticks(client, 2)
        try:
            check_inbound_slices(client, checks)
        except (ModPipeError, ModTimeout) as exc:
            checks.check("inbound_slice_rows", False, f"inbound drain failed: {exc}")
    finally:
        with preserve_failure(checks, "violation_watch"):
            try:
                stopped = client.violation_watch(False)
                checks.check("violation_watch_stop", stopped.get("active") is False, str(stopped))
                checks.check(
                    "violation_watch_stats_stopped", client.violation_stats().get("active") is False
                )
                checks.check(
                    "inbound_freeze_reset_on_stop",
                    client.violation_stats().get("inbound_frozen") is False,
                )
                checks.check(
                    "violation_watch_state_alive",
                    bool(client.state().get("in_world")) and client.ping().get("event") == "pong",
                )
            finally:
                if was_active:
                    restored = client.violation_watch(True, addr=addr)
                    checks.check(
                        "violation_watch_restored",
                        restored.get("active") is True and restored.get("screen_hooked") is True,
                        str(restored),
                    )
    after = {(p.name, p.stat().st_mtime_ns) for p in directory.glob("crash-*")}
    checks.check("violation_watch_no_dump", after == before)


def check_send_probe(client: ModClient, checks: Checks, wait_ticks: Callable[..., int]) -> None:
    """Install the read-only send probe, observe ordinary traffic and stop."""
    if not checks.check("send_probe_in_world", client.state().get("in_world") is True):
        return
    enqueue = violation_watch_address(client, "@client_packet_enqueue")
    serializer = violation_watch_address(client, "@client_packet_send_serialize")
    checks.check(
        "send_probe_roles",
        enqueue is not None and serializer is not None,
        f"enqueue={hex(enqueue) if enqueue else None} serialize={hex(serializer) if serializer else None}",
    )
    if enqueue is None or serializer is None:
        return
    started = client.send_probe(True, addr=enqueue, serialize_addr=serializer)
    if started.get("event") == "error":
        checks.check("send_probe_gate", started.get("reason") == "hook_va_disabled", str(started))
        checks.skip("send_probe_observation", "allow_hook_va is disabled")
        return
    try:
        checks.check(
            "send_probe_start",
            started.get("active") is True and started.get("serialize_hooked") is True,
            str({k: started.get(k) for k in ("active", "serialize_hooked")}),
        )
        checks.check(
            "send_probe_path",
            str(started.get("path", "")).endswith(".jsonl"),
            str(started.get("path")),
        )
        wait_ticks(client, 12)
        stopped = client.send_probe(False)
        checks.check("send_probe_hits", int(stopped.get("hits", 0)) > 0, str(stopped))
        checks.check(
            "send_probe_no_dumps",
            int(stopped.get("dumps", -1)) == 0,
            "healthy traffic must not produce suspect frames",
        )
        checks.check("send_probe_stopped", stopped.get("active") is False, str(stopped))
        checks.check(
            "send_probe_state_alive",
            bool(client.state().get("in_world")) and client.ping().get("event") == "pong",
        )
    finally:
        with preserve_failure(checks, "send_probe"):
            client.send_probe(False)


def check_packet_tap(client: ModClient, checks: Checks, wait_ticks: Callable[..., int]) -> None:
    """Fail observations of real, unmodified traffic; delivery must continue."""
    try:
        client.packet_capture(True, ids=[144], directions=["send"])
        client.request({"cmd": "selftest_packet_tap", "mode": "off"}, "packet_tap_test_armed")
    except ModProtocolError as exc:
        if "packet_capture_disabled" not in str(exc) and "selftest_stress_disabled" not in str(exc):
            raise
        checks.skip("packet_tap_fault_coverage", str(exc))
        client.packet_capture(False)
        return
    crashes = CrashSnapshot(client)

    def traffic(direction: str) -> None:
        if direction == "recv":
            client.command('tellraw @s {"rawtext":[{"text":"LeviPilot packet observation check"}]}')
        wait_ticks(client, 4)

    try:
        for direction, packet_id in (("send", 144), ("recv", 9)):
            started = client.packet_capture(
                True, ids=[packet_id], serialize_ids=[packet_id], directions=[direction]
            )
            checks.check(
                f"packet_tap_{direction}_diagnostic_contract",
                started.get("serialization") == "sdk_hot_path_diagnostic"
                and started.get("diagnostic") is True,
                str(started),
            )
            traffic(direction)
            rows = client.packet_bytes(0, limit=4096)["hits"]
            checks.check(
                f"packet_tap_{direction}_baseline",
                bool(rows)
                and all(
                    row["serialized"]
                    and int(row["size"]) > 0
                    and row.get("source") == "sdk_reserialized"
                    and int(row["captured_size"]) * 2 == len(row["hex"])
                    and bool(row["truncated"]) == (int(row["size"]) > int(row["captured_size"]))
                    for row in rows
                ),
                f"rows={len(rows)}",
            )
            for mode in ("id_seh", "id_cpp", "bytes_seh", "bytes_cpp"):
                before = client.packet_stats()
                cursor = int(client.packet_bytes(0, limit=4096).get("next", 0))
                client.request(
                    {"cmd": "selftest_packet_tap", "mode": mode}, "packet_tap_test_armed"
                )
                traffic(direction)
                deadline = time.monotonic() + 4
                while True:
                    stats = client.packet_stats()
                    if not stats["fault_pending"]:
                        break
                    if time.monotonic() >= deadline:
                        raise ModTimeout(f"packet tap fault was not observed: {direction}/{mode}")
                    time.sleep(0.01)
                byte_fault = mode.startswith("bytes")
                name = f"packet_tap_{direction}_{mode}"
                checks.check(
                    name + "_isolated",
                    int(stats["id_faults"]) == int(before["id_faults"]) + int(not byte_fault)
                    and int(stats["byte_faults"]) == int(before["byte_faults"]) + int(byte_fault),
                    f"id={stats['id_faults']} bytes={stats['byte_faults']}",
                )
                rows = client.packet_bytes(cursor, limit=4096)["hits"]
                failures = [row for row in rows if row["serialized"] is False]
                expected_reason = (
                    "packet_serialize_fault" if mode == "bytes_seh" else "packet_serialize_failed"
                )
                identity = {
                    (int(row["seq"]), int(row["id"]), row["dir"])
                    for row in client.packet_drain(0, limit=8192)["hits"]
                }
                checks.check(
                    name + "_diagnostic",
                    len(failures) == int(byte_fault)
                    and all(
                        row.get("reason") == expected_reason
                        and int(row["size"]) == 0
                        and row["hex"] == ""
                        and (int(row["seq"]), int(row["id"]), row["dir"]) in identity
                        for row in failures
                    ),
                    str(failures),
                )
                cursor = int(client.packet_bytes(0, limit=4096).get("next", 0))
                traffic(direction)
                rows = client.packet_bytes(cursor, limit=4096)["hits"]
                checks.check(
                    name + "_recovers",
                    bool(rows) and all(row["serialized"] and int(row["size"]) > 0 for row in rows),
                    f"rows={len(rows)}",
                )
                client.packet_capture(False)
                checks.check(name + "_stops", client.packet_stats()["active"] is False)
                client.packet_capture(
                    True, ids=[packet_id], serialize_ids=[packet_id], directions=[direction]
                )
            stats = client.packet_stats()
            checks.check(
                f"packet_tap_{direction}_reset",
                stats["id_faults"] == stats["byte_faults"] == 0 and not stats["fault_pending"],
            )
        client.packet_capture(False)
        rejected = client.request({"cmd": "selftest_packet_tap", "mode": "id_seh"}, "error")
        checks.check(
            "packet_tap_inactive_rejects", rejected.get("reason") == "packet_capture_inactive"
        )
        checks.check("packet_tap_alive", bool(client.ping()) and client.state()["in_world"] is True)
    finally:
        try:
            client.request({"cmd": "selftest_packet_tap", "mode": "off"}, "packet_tap_test_armed")
        finally:
            client.packet_capture(False)
    checks.check("packet_tap_handled_fault_no_dump", crashes.unchanged())
