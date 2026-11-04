"""Local SDK boundaries: synthetic faults, task retirement and real recovery."""

from __future__ import annotations

import contextlib
import time
from typing import Any

from driver.selftest_runtime import wait_ticks
from driver.selftest_support import Checks, preserve_failure
from driver.transport_mod import ModClient, ModTimeout


def _task_retired(client: ModClient, domain: str) -> bool:
    deadline = time.monotonic() + 5
    while True:
        row = client.status()["lifecycle"]["callbacks"]["domains"][domain]
        if row["owned"] == row["running"] == 0:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


def _sdk_request(client: ModClient, target: str, phase: str, mode: str) -> dict[str, Any]:
    return client.request(
        {"cmd": "selftest_sdk", "target": target, "phase": phase, "mode": mode},
        ("sdk_test_result", "error"),
    )


def check_sdk_boundaries(client: ModClient, checks: Checks) -> None:
    """Faults precede SDK calls; this does not simulate corrupted SDK locks."""
    for domain in ("client", "server"):
        for phase in ("lookup", "submit", "run"):
            for mode in ("seh", "cpp"):
                label = f"sdk_{domain}_{phase}_{mode}"
                reply = _sdk_request(client, domain, phase, mode)
                if reply.get("reason") == "selftest_stress_disabled":
                    checks.skip("sdk_boundaries", "enable allow_selftest_stress")
                    return
                reason = f"{domain}_task_{'failed' if phase == 'run' else 'queue_failed'}"
                checks.check(
                    label, reply.get("ok") is False and reply.get("reason") == reason, str(reply)
                )
                duplicate = False
                with contextlib.suppress(ModTimeout):
                    client.wait_event("sdk_test_result", 0.1)
                    duplicate = True
                checks.check(f"{label}_unique", not duplicate)
                checks.check(f"{label}_retired", _task_retired(client, domain))
                recovered = _sdk_request(client, domain, "run", "none")
                checks.check(f"{label}_recovered", recovered.get("ok") is True, str(recovered))
                checks.check(f"{label}_recovery_retired", _task_retired(client, domain))

    for target in ("registration", "command_boundary"):
        for mode in ("seh", "cpp", "none"):
            reply = _sdk_request(client, target, "run", mode)
            checks.check(
                f"sdk_{target}_{mode}",
                reply.get("event") == "sdk_test_result" and reply.get("ok") is (mode == "none"),
                str(reply),
            )

    original = client.state()
    try:
        result = client.command("togglefly")
        wait_ticks(client, 4)
        checks.check(
            "sdk_togglefly_actual",
            result.get("success") is True
            and client.state().get("mayfly") is not original.get("mayfly"),
            str(result),
        )
    finally:
        with preserve_failure(checks, "sdk_togglefly"):
            if client.state().get("mayfly") != original.get("mayfly"):
                client.command("togglefly")
                wait_ticks(client, 4)
            if original.get("flying"):
                client.fly(True)
                wait_ticks(client, 4)
            restored = client.state()
            checks.check(
                "sdk_togglefly_restored",
                restored.get("mayfly") == original.get("mayfly")
                and restored.get("flying") == original.get("flying"),
                str({key: restored.get(key) for key in ("mayfly", "flying")}),
            )
    checks.check("sdk_pipe_alive", client.ping().get("event") == "pong")
