"""Block identity, protocol compatibility and overhead live checks."""

from __future__ import annotations

import subprocess
import sys
import time

from driver.harness import ModClient
from driver.harness.memory import fnv1_64
from driver.selftest_runtime import (
    ROOT,
    SWIM_X,
    SWIM_Z,
    prepare_swim_fixture,
    require_world,
    wait_ticks,
)
from driver.selftest_support import Checks, preserve_failure


def check_block_identity(client: ModClient, checks: Checks) -> None:
    """Sample stone/air/water without creating uncontained sources near the player."""
    original = client.state()
    require_world(client, "block identity")
    try:
        if not prepare_swim_fixture(client, checks, "h0_water_fixture"):
            return
        samples = [
            [SWIM_X - 2, -58, SWIM_Z],
            [SWIM_X, -52, SWIM_Z],
            [SWIM_X, -58, SWIM_Z],
        ]
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
    finally:
        with preserve_failure(checks, "block_identity"):
            client.teleport(
                *(float(original[f"feet_{axis}"]) for axis in "xyz"),
                yaw=float(original["yaw"]),
                pitch=float(original["pitch"]),
            )
            wait_ticks(client, 12)


def check_dispatch_compatibility(client: ModClient, checks: Checks) -> None:
    """Exercise every extracted domain and the shared error boundary."""
    requests = (
        ("diagnostics", {"cmd": "ping"}, "pong"),
        ("inspection", {"cmd": "mem_read", "addr": "invalid"}, "mem_read"),
        ("input", {"cmd": "mouse"}, "mouse"),
        ("packets", {"cmd": "packet_stats"}, "packet_stats"),
        ("recording", {"cmd": "record_dump", "limit": 1}, "record_dump"),
        ("query", {"cmd": "state"}, "state"),
        ("world", {"cmd": "worlds"}, "worlds"),
        ("tasks", {"cmd": "task_status"}, "task_status"),
    )
    for domain, payload, event in requests:
        reply = client.request(payload, event)
        checks.check(f"dispatch_{domain}", reply.get("event") == event)
        if domain == "inspection":
            checks.check("dispatch_bad_addr", reply.get("reason") == "bad_addr")

    # A handled validation error must not fall through to unknown_cmd. A
    # following ping also exposes a duplicate error left in the response stream.
    unknown = client.request({"cmd": "__selftest_unknown_command__"}, "error")
    checks.check("dispatch_unknown_cmd", unknown.get("reason") == "unknown_cmd")
    bad_type = client.request({"cmd": "mem_read", "addr": "1", "len": []}, "error")
    checks.check("dispatch_bad_command", bad_type.get("reason") == "bad_command")
    checks.check("dispatch_error_recovery", client.ping().get("event") == "pong")


def check_cli_compatibility(client: ModClient, checks: Checks) -> None:
    """Exercise each CLI domain over the actual pipe, without changing the world."""
    was_locked = bool(client.state().get("input", {}).get("locked"))
    commands = (
        ("diagnostics", ["ping"], '"event": "pong"', 0),
        ("input", ["mouse"], '"event": "mouse"', 0),
        ("inspection", ["mem-read", "0x10"], '"reason": "bad_addr"', 1),
        ("packets", ["packet-stats"], '"event": "packet_stats"', 0),
        ("query", ["state"], '"event": "state"', 0),
        ("chat_history", ["chat-history", "--limit", "1"], '"event": "chat_history"', 0),
        ("world", ["worlds"], '"event": "worlds"', 0),
        ("tasks", ["task-status"], '"event": "task_status"', 0),
        ("recording", ["record-dump", "--limit", "1"], "entries=", 0),
    )
    # The server accepts one client at a time. Release this connection before
    # starting subprocesses, then reconnect even if a subprocess fails.
    client.close()
    try:
        for domain, argv, expected, expected_code in commands:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "driver" / "transport_mod.py"),
                    "--pipe",
                    client.pipe_name,
                    *argv,
                ],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            checks.check(
                f"cli_{domain}",
                result.returncode == expected_code and expected in result.stdout,
                f"exit={result.returncode} expected={expected_code}"
                + (f" stderr={result.stderr.strip()}" if result.stderr else ""),
            )
        result = subprocess.run(
            [sys.executable, "-m", "driver.transport_mod", "--pipe", client.pipe_name, "ping"],
            cwd=ROOT,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        checks.check(
            "cli_module_entry", result.returncode == 0 and '"event": "pong"' in result.stdout
        )
    finally:
        client.connect()
        client.input_lock(was_locked)


def check_overhead_window(client: ModClient, checks: Checks, *, wrap: bool = False) -> None:
    """Verify published window bounds/legacy metrics while real ticks overwrite it."""
    start = client.overhead()
    first_head = int(start.get("sample_head", 0))
    last_head = first_head
    deadline = time.monotonic() + (80 if wrap else 15)
    valid = aliases = ordered = monotonic = True
    reads = consistent = busy = 0
    while reads < 100 or (wrap and last_head - first_head < 1100):
        if time.monotonic() >= deadline:
            break
        result = client.overhead()
        reads += 1
        if result.get("consistent") is not True:
            busy += 1
            valid &= (
                result.get("reason") == "snapshot_busy"
                and result.get("samples") == 0
                and "tick_p99_us" not in result
            )
            continue
        consistent += 1
        head, count = int(result.get("sample_head", -1)), int(result.get("samples", -1))
        valid &= (
            0 <= count <= 1024
            and count == min(head, 1024)
            and int(result.get("sample_first", -1)) + count == head
        )
        monotonic &= head >= last_head
        last_head = head
        if count:
            aliases &= all(
                result.get(f"tick_{part}_us") == result.get(f"tick_interval_{part}_us")
                for part in ("p50", "p99", "max")
            )
            ordered &= all(
                0
                <= float(result.get(f"{path}_p50_us", -1))
                <= float(result.get(f"{path}_p99_us", -1))
                <= float(result.get(f"{path}_max_us", -1))
                for path in ("tick", "hook")
            )
        time.sleep(0.02)
    detail = str(
        {"reads": reads, "consistent": consistent, "busy": busy, "advance": last_head - first_head}
    )
    checks.check("overhead_window_readback", reads >= 100 and consistent > 0 and valid, detail)
    checks.check("overhead_window_monotonic", monotonic)
    checks.check("overhead_window_legacy_aliases", aliases)
    checks.check("overhead_window_percentiles", ordered)
    if wrap:
        checks.check("overhead_window_wrap", last_head - first_head >= 1100, detail)
    if not checks.check(
        "overhead_record_fixture_idle", client.status().get("record_active") is False
    ):
        return
    before = int(client.overhead()["paths"]["record"]["calls_total"])
    try:
        client.record_bin_start(period=1)
        wait_ticks(client, 8)
    finally:
        client.record_bin_stop()
    after = int(client.overhead()["paths"]["record"]["calls_total"])
    checks.check("overhead_record_counter", after - before >= 8, f"delta={after - before}")
