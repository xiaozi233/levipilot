"""Probe smoke tests. The default live suite requires the game in a world.

    py -3.11 driver/selftest.py
    py -3.11 driver/selftest.py --keep          # keep the test entity
    py -3.11 driver/selftest.py --entity minecraft:cow
    py -3.11 driver/selftest.py --client-io-only  # offline; never connects to the game
    py -3.11 driver/selftest.py --mod-core-only   # offline native mod checks
    py -3.11 driver/selftest.py --mod-core-live-only  # pipe/recording; no RE commands
    py -3.11 driver/selftest.py --use-lifecycle-only  # bounded server failure/recovery checks
    py -3.11 driver/selftest.py --query-capture-only  # snapshot faults and scan budget
    py -3.11 driver/selftest.py --macro-limit-only  # bounded recording and retained rows
    py -3.11 driver/selftest.py --macro-capture-only  # sampling faults, partial save and retry
    py -3.11 driver/selftest.py --macro-capture-world "超平坦测试"  # also leave/rejoin recording lifecycle
    py -3.11 driver/selftest.py --tick-safety-only  # guarded hook calls and coherent checkpoint poses
    py -3.11 driver/selftest.py --tick-safety-world "超平坦测试"  # plus menu cancellation/pose invalidation
    py -3.11 driver/selftest.py --binary-capture-only  # binary recording failures and retained rows
    py -3.11 driver/selftest.py --binary-capture-world "超平坦测试"  # plus recording across leave/rejoin
    py -3.11 driver/selftest.py --teleport-only  # owned server teleport faults and side effects
    py -3.11 driver/selftest.py --packet-injection-only  # owned command sends and template faults
    py -3.11 driver/selftest.py --packet-tap-only  # isolate packet observation faults, no injected packets
    py -3.11 driver/selftest.py --packet-injection-world "超平坦测试"  # plus reconnect/leave/rejoin
    py -3.11 driver/selftest.py --tick-recovery-only  # tick errors, fail-stop and fresh requests
    py -3.11 driver/selftest.py --tick-recovery-world "超平坦测试"  # plus reconnect/leave/rejoin
    py -3.11 driver/selftest.py --teleport-world "超平坦测试"  # plus reconnect/leave/rejoin
    py -3.11 driver/selftest.py --ecs-capture-only  # bounded ECS snapshots and stage failures
    py -3.11 driver/selftest.py --ecs-capture-world "超平坦测试"  # plus reconnect and leave/rejoin
    py -3.11 driver/selftest.py --macro-playback-only  # guarded replay, release and frame ownership
    py -3.11 driver/selftest.py --macro-playback-world "超平坦测试"  # plus cancellation on world exit
    py -3.11 driver/selftest.py --query-lifecycle-world "超平坦测试"  # reconnect/leave/rejoin
    py -3.11 driver/selftest.py --entry-lifecycle-world "超平坦测试"  # startup watchdog/recovery
    py -3.11 driver/selftest.py --overhead-only  # read concurrently across a full sample-ring wrap
    py -3.11 driver/selftest.py --tick-stream-only  # JSON recording order/faults and parkour events
    py -3.11 driver/selftest.py --tick-stream-world "超平坦测试"  # plus leave/rejoin recording lifecycle

Covers: pipe, state, inventory read/write/give/clear, teleport, server command,
entity query, look-at, attack and interact. The original slot-0 item and the
selected slot are restored at the end.

The default entity is a pig: hostile mobs despawn immediately if the test world
is on Peaceful difficulty.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver import selftest_diagnostics  # noqa: E402
from driver.harness.lab import Builder, capture_disconnect_report, settle  # noqa: E402,F401
from driver.selftest_callbacks import check_callback_drain  # noqa: E402
from driver.selftest_core import (  # noqa: E402,F401
    check_block_identity,
    check_cli_compatibility,
    check_dispatch_compatibility,
    check_overhead_window,
)
from driver.selftest_default import check_default_suite  # noqa: E402
from driver.selftest_flight import (  # noqa: E402,F401
    EffectFixture,
    choose_sentinel_effect,
    required_effect_ids,
)
from driver.selftest_input import (  # noqa: E402
    check_controls,
    check_flight,
    check_input_events,
    check_input_mode_contract,
    check_macro_playback,
    check_player_identity,
    check_teleport,
)
from driver.selftest_lifecycle import check_hot_reload, check_lifecycle_prepare  # noqa: E402
from driver.selftest_macro import (  # noqa: E402
    check_macro_long_recording,
    run_macro_capture,
    run_macro_record_limits,
)
from driver.selftest_packets import check_packet_injection  # noqa: E402
from driver.selftest_queries import (  # noqa: E402,F401
    check_ecs_capture,
    check_entity_slot_capture,
    check_query_capture,
    check_query_lifecycle,
)
from driver.selftest_recovery import (  # noqa: E402,F401
    check_chat_lifecycle,
    check_connection_lifecycle,
    check_diagnostic_export,
    check_entry_lifecycle,
    check_lifecycle_stress,
    check_mod_core_backpressure,
    check_mod_core_live,
    check_request_recovery,
)
from driver.selftest_runtime import (  # noqa: E402,F401
    SWIM_X,
    SWIM_Y,
    SWIM_Z,
    WorldLost,
    dump_packet_tap,
    find_item,
    jump_arcs,
    prepare_swim_fixture,
    require_world,
    slot_item,
    wait_rotation,
    wait_screen,
    wait_still,
    wait_ticks,
)
from driver.selftest_sdk import check_sdk_boundaries  # noqa: E402
from driver.selftest_streams import (  # noqa: E402,F401
    check_binary_capture,
    check_jump_motion,
    check_record_medium,
    check_tick_recovery,
    check_tick_safety,
    check_tick_streams,
)
from driver.selftest_support import Checks, ViolationObservation  # noqa: E402
from driver.selftest_tasks import (  # noqa: E402,F401
    check_task_lifecycle,
    check_task_operations,
    check_use_lifecycle,
)
from driver.transport_mod import ModClient, ModPipeError, ModProtocolError, ModTimeout  # noqa: E402,F401


def check_macro_record_limits(client: ModClient, checks: Checks) -> None:
    run_macro_record_limits(client, checks, wait_ticks)


def check_macro_capture(client: ModClient, checks: Checks, world: str | None = None) -> None:
    run_macro_capture(client, checks, world, wait_ticks, settle)


def check_packet_tap(client: ModClient, checks: Checks) -> None:
    selftest_diagnostics.check_packet_tap(client, checks, wait_ticks)


def role_address(role: str) -> tuple[int, int] | None:
    return selftest_diagnostics.role_address(role)


def violation_watch_address(client: ModClient, role: str) -> int | None:
    return selftest_diagnostics.violation_watch_address(client, role)


def check_inbound_slices(client: ModClient, checks: Checks) -> None:
    selftest_diagnostics.check_inbound_slices(client, checks)


def check_violation_watch(client: ModClient, checks: Checks) -> None:
    selftest_diagnostics.check_violation_watch(client, checks, wait_ticks)


def check_send_probe(client: ModClient, checks: Checks) -> None:
    selftest_diagnostics.check_send_probe(client, checks, wait_ticks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-complete", action="store_true", help="fail when required coverage is skipped"
    )
    parser.add_argument("--entity", default="minecraft:pig")
    parser.add_argument("--keep", action="store_true", help="do not remove the test entity")
    parser.add_argument("--radius", type=float, default=10.0)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--sdk-boundaries-only", action="store_true", help="SDK lookup/submit/run fault recovery"
    )
    modes.add_argument(
        "--callback-drain-only",
        action="store_true",
        help="delay client/server/tick callbacks, test timeout rollback and real reloads",
    )
    modes.add_argument(
        "--lifecycle-prepare-only",
        action="store_true",
        help="check active/expired input unload gates and restore hooks without reloading",
    )
    modes.add_argument(
        "--diagnostic-export-only",
        action="store_true",
        help="export existing diagnostic buffers without arming hooks or changing the world",
    )
    modes.add_argument(
        "--client-io-only",
        action="store_true",
        help="run offline decoder/recording/spool regressions without connecting to the game",
    )
    modes.add_argument(
        "--mod-core-only",
        action="store_true",
        help="compile/run native mod transport/recording checks without connecting to the game",
    )
    modes.add_argument(
        "--chat-lifecycle-world",
        metavar="WORLD",
        help="save/leave, check chat cancellation and main menu recovery, then re-enter WORLD",
    )
    modes.add_argument(
        "--query-capture-only",
        action="store_true",
        help="check fixed query snapshots, SEH/timeout recovery and per-tick scan budget",
    )
    modes.add_argument(
        "--query-lifecycle-world",
        metavar="WORLD",
        help="check scan cancellation on reconnect/exit, then re-enter WORLD",
    )
    modes.add_argument(
        "--overhead-only",
        action="store_true",
        help="read overhead concurrently across 1100 real ticks and verify record path counters",
    )
    modes.add_argument(
        "--tick-stream-only",
        action="store_true",
        help="check JSON streams and parkour event safety",
    )
    modes.add_argument(
        "--tick-stream-world", metavar="WORLD", help="also check stream termination on leave/rejoin"
    )
    modes.add_argument(
        "--entry-lifecycle-world",
        metavar="WORLD",
        help="save/leave, check entry timeout/quarantine and missing join recovery, then restore WORLD",
    )
    modes.add_argument(
        "--macro-limit-only",
        action="store_true",
        help="check recording limit normalization, unique termination and buffered data preservation",
    )
    modes.add_argument(
        "--macro-long-recording-only",
        action="store_true",
        help="record, save and reload all 200000 ticks at real speed (about 2h47 at 20 TPS)",
    )
    parser.add_argument(
        "--macro-resume",
        action="store_true",
        help="with --macro-long-recording-only, resume the same active recording after a watcher restart",
    )
    modes.add_argument(
        "--macro-capture-only",
        action="store_true",
        help="check macro sampling faults and retained saves",
    )
    modes.add_argument(
        "--macro-capture-world",
        metavar="WORLD",
        help="also check macro recording across leave/rejoin",
    )
    modes.add_argument(
        "--tick-safety-only",
        action="store_true",
        help="check guarded hook calls and coherent poses",
    )
    modes.add_argument(
        "--tick-safety-world",
        metavar="WORLD",
        help="also check hook cancellation and pose validity on leave/rejoin",
    )
    modes.add_argument(
        "--binary-capture-only", action="store_true", help="check binary recording failure outcomes"
    )
    modes.add_argument(
        "--binary-capture-world", metavar="WORLD", help="also check recording across leave/rejoin"
    )
    modes.add_argument(
        "--teleport-only", action="store_true", help="check owned server teleports and failures"
    )
    modes.add_argument(
        "--packet-tap-only",
        action="store_true",
        help="check isolation of packet observation faults",
    )
    modes.add_argument(
        "--packet-injection-only",
        action="store_true",
        help="check command templates and owned packet sends",
    )
    modes.add_argument(
        "--packet-injection-world", metavar="WORLD", help="also check packet reconnect/leave/rejoin"
    )
    modes.add_argument(
        "--record-medium-only",
        action="store_true",
        help="check recording in an isolated water fixture",
    )
    modes.add_argument(
        "--violation-only",
        action="store_true",
        help="check the read-only violation watch (synthetic row, no real violation)",
    )
    modes.add_argument(
        "--teleport-world", metavar="WORLD", help="also check teleport reconnect/leave/rejoin"
    )
    modes.add_argument(
        "--controls-only", action="store_true", help="check control faults and input release"
    )
    modes.add_argument(
        "--tick-recovery-only", action="store_true", help="check tick errors and fail-stop recovery"
    )
    modes.add_argument(
        "--tick-recovery-world",
        metavar="WORLD",
        help="also check recovery across reconnect/leave/rejoin",
    )
    modes.add_argument(
        "--hot-reload-only",
        action="store_true",
        help="reload probe through chat input and verify the same live process",
    )
    modes.add_argument(
        "--player-identity-only",
        action="store_true",
        help="check local UUID sampling and server resolution",
    )
    modes.add_argument(
        "--player-identity-world",
        metavar="WORLD",
        help="also reject identities retained across worlds",
    )
    modes.add_argument(
        "--flight-only", action="store_true", help="check staged flight and tick assertions"
    )
    modes.add_argument(
        "--flight-world", metavar="WORLD", help="also check flight reconnect/leave/rejoin"
    )
    modes.add_argument(
        "--input-events-only", action="store_true", help="check guarded input and text ordering"
    )
    modes.add_argument(
        "--input-events-world", metavar="WORLD", help="also check input reconnect/leave/rejoin"
    )
    modes.add_argument(
        "--ecs-capture-only", action="store_true", help="check owned ECS snapshots and failures"
    )
    modes.add_argument(
        "--ecs-capture-world", metavar="WORLD", help="also check ECS reconnect/leave/rejoin"
    )
    modes.add_argument(
        "--macro-playback-only", action="store_true", help="check replay faults and frame ownership"
    )
    modes.add_argument(
        "--macro-playback-world", metavar="WORLD", help="also check playback across leave/rejoin"
    )
    modes.add_argument(
        "--use-lifecycle-only",
        action="store_true",
        help="inject missing/failed server work and verify task termination/recovery in a world",
    )
    modes.add_argument(
        "--connection-only",
        action="store_true",
        help="check abandoned clients and process-exit reconnects; no world changes or RE",
    )
    modes.add_argument(
        "--mod-core-live-only",
        action="store_true",
        help="check installed pipe/recording only; no RE, input injection or world edits",
    )
    parser.add_argument("--stress-cycles", type=int, default=0, help="opt-in lifecycle soak cycles")
    parser.add_argument(
        "--stress-world", help="leave/re-enter this test world every 10 soak cycles"
    )
    args = parser.parse_args(argv)
    if args.client_io_only or args.mod_core_only:
        # Reuse the bounded regression fixtures; no game commands, input or
        # live diagnostics are needed to verify these client-side workflows.
        suites = (
            ["tests.test_mod_models"]
            if args.mod_core_only
            else [
                "tests.test_transport.SessionTests",
                "tests.test_transport_stress",
                "tests.test_transport.LineDecoderTests",
                "tests.test_recording_commands",
                "tests.test_mod_agent.RecordingIntegrationTests",
                "tests.test_dev_config",
                "tests.test_dev.GameShutdownTests",
                "tests.test_spool",
            ]
        )
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                *suites,
                "-v",
            ],
            cwd=ROOT,
            check=False,
        ).returncode
    if args.macro_resume and not args.macro_long_recording_only:
        parser.error("--macro-resume requires --macro-long-recording-only")
    if args.stress_cycles < 0 or (args.stress_world and args.stress_cycles < 10):
        parser.error(
            "--stress-cycles must be nonnegative; --stress-world requires at least 10 cycles"
        )

    checks = Checks(require_complete=args.require_complete)
    try:
        client = ModClient(connect_timeout=60.0, timeout=30.0).connect()
    except ModPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("提示：游戏内是否已加载 probe mod，并且已经进入存档？", file=sys.stderr)
        return 2

    observation = ViolationObservation(
        client,
        checks,
        os.environ.get("LEVIPILOT_VIOLATION_DUMP") or None,
        lambda: violation_watch_address(client, "@client_handle_packet_violation"),
    )
    result = 0
    try:
        observation.start()
        result = run_live(client, checks, args, observation)
    except WorldLost as exc:
        checks.check("aborted_world_left", False, str(exc))
        try:
            report = capture_disconnect_report(client, ROOT / "out", tag="disconnect")
            checks.note("disconnect_report", str(report))
        except (ModPipeError, OSError) as report_exc:
            checks.note("disconnect_report", f"failed: {report_exc}")
    finally:
        try:
            observation.finish()
        finally:
            try:
                with contextlib.suppress(ModPipeError):
                    client.input_lock(False)
            finally:
                client.close()
    return result if result else checks.finish()


def run_live(
    client: ModClient, checks: Checks, args: argparse.Namespace, observation: ViolationObservation
) -> int:
    if args.sdk_boundaries_only:
        try:
            check_sdk_boundaries(client, checks)
        except WorldLost:
            raise
        except (ModPipeError, OSError, ValueError, KeyError) as exc:
            checks.check("sdk_boundaries_error", False, str(exc))
        return 0

    if args.diagnostic_export_only:
        try:
            check_diagnostic_export(client, checks)
        except (ModPipeError, OSError, ValueError, KeyError) as exc:
            checks.check("diagnostic_export_error", False, str(exc))
        return 0

    if args.connection_only:
        try:
            check_connection_lifecycle(client, checks)
        except (ModPipeError, OSError, ValueError, subprocess.SubprocessError) as exc:
            checks.check("connection_lifecycle_error", False, str(exc))
        return 0

    if args.overhead_only:
        try:
            check_overhead_window(client, checks, wrap=True)
        except WorldLost:
            raise
        except (ModPipeError, OSError, ValueError) as exc:
            checks.check("overhead_window_error", False, str(exc))
        return 0

    if (
        args.record_medium_only
        or args.packet_tap_only
        or args.violation_only
        or args.packet_injection_only
        or args.packet_injection_world
        or args.teleport_only
        or args.teleport_world
        or args.tick_recovery_only
        or args.tick_recovery_world
        or args.hot_reload_only
        or args.lifecycle_prepare_only
        or args.callback_drain_only
        or args.controls_only
        or args.player_identity_only
        or args.player_identity_world
        or args.flight_only
        or args.flight_world
        or args.input_events_only
        or args.input_events_world
        or args.ecs_capture_only
        or args.ecs_capture_world
        or args.query_capture_only
        or args.query_lifecycle_world
        or args.macro_limit_only
        or args.macro_long_recording_only
        or args.entry_lifecycle_world
        or args.tick_stream_only
        or args.tick_stream_world
        or args.macro_capture_only
        or args.macro_capture_world
        or args.tick_safety_only
        or args.tick_safety_world
        or args.binary_capture_only
        or args.binary_capture_world
        or args.macro_playback_only
        or args.macro_playback_world
    ):
        try:
            client.input_lock(True)
            if args.record_medium_only:
                check_block_identity(client, checks)
                check_record_medium(client, checks)
            elif args.packet_tap_only:
                check_packet_tap(client, checks)
            elif args.violation_only:
                with observation.gap("violation_selftest"):
                    check_violation_watch(client, checks)
                check_send_probe(client, checks)
            elif args.packet_injection_only or args.packet_injection_world:
                check_packet_injection(client, checks, args.packet_injection_world)
            elif args.tick_recovery_only or args.tick_recovery_world:
                check_tick_recovery(client, checks, args.tick_recovery_world)
            elif args.hot_reload_only:
                with observation.reload_gap():
                    check_hot_reload(client, checks, close_client=False)
            elif args.lifecycle_prepare_only:
                check_lifecycle_prepare(client, checks)
            elif args.callback_drain_only:
                with observation.reload_gap():
                    check_callback_drain(client, checks)
            elif args.teleport_only or args.teleport_world:
                check_teleport(client, checks, args.teleport_world)
            elif args.controls_only:
                check_input_mode_contract(client, checks)
                check_controls(client, checks)
            elif args.player_identity_only or args.player_identity_world:
                check_player_identity(client, checks, args.player_identity_world)
            elif args.flight_only or args.flight_world:
                check_flight(client, checks, args.flight_world)
            elif args.input_events_only or args.input_events_world:
                check_input_events(client, checks, args.input_events_world)
            elif args.ecs_capture_only or args.ecs_capture_world:
                check_ecs_capture(client, checks, args.ecs_capture_world)
            elif args.macro_playback_only or args.macro_playback_world:
                check_macro_playback(client, checks, args.macro_playback_world)
            elif args.binary_capture_only or args.binary_capture_world:
                check_binary_capture(client, checks, args.binary_capture_world)
            elif args.tick_safety_only or args.tick_safety_world:
                check_tick_safety(client, checks, args.tick_safety_world)
            elif args.macro_capture_only or args.macro_capture_world:
                check_macro_capture(client, checks, args.macro_capture_world)
            elif args.tick_stream_only or args.tick_stream_world:
                check_tick_streams(client, checks, args.tick_stream_world)
            elif args.entry_lifecycle_world:
                check_entry_lifecycle(client, checks, args.entry_lifecycle_world)
            elif args.macro_limit_only:
                check_macro_record_limits(client, checks)
            elif args.macro_long_recording_only:
                if args.macro_resume:
                    check_macro_long_recording(client, checks, resume=True)
                else:
                    check_macro_long_recording(client, checks)
            elif args.query_lifecycle_world:
                check_query_lifecycle(client, checks, args.query_lifecycle_world)
            else:
                check_query_capture(client, checks)
        except WorldLost:
            raise
        except (ModPipeError, OSError, ValueError) as exc:
            name = (
                "record_medium_error"
                if args.record_medium_only
                else "packet_tap_error"
                if args.packet_tap_only
                else "violation_watch_error"
                if args.violation_only
                else "packet_injection_error"
                if args.packet_injection_only or args.packet_injection_world
                else "tick_recovery_error"
                if args.tick_recovery_only or args.tick_recovery_world
                else "hot_reload_error"
                if args.hot_reload_only
                else "lifecycle_prepare_error"
                if args.lifecycle_prepare_only
                else "callback_drain_error"
                if args.callback_drain_only
                else "teleport_error"
                if args.teleport_only or args.teleport_world
                else "controls_error"
                if args.controls_only
                else "identity_error"
                if args.player_identity_only or args.player_identity_world
                else "flight_error"
                if args.flight_only or args.flight_world
                else "input_events_error"
                if args.input_events_only or args.input_events_world
                else "ecs_capture_error"
                if args.ecs_capture_only or args.ecs_capture_world
                else "macro_playback_error"
                if args.macro_playback_only or args.macro_playback_world
                else "binary_capture_error"
                if args.binary_capture_only or args.binary_capture_world
                else "tick_safety_error"
                if args.tick_safety_only or args.tick_safety_world
                else "macro_capture_error"
                if args.macro_capture_only or args.macro_capture_world
                else "macro_limit_error"
                if args.macro_limit_only
                else "macro_long_recording_error"
                if args.macro_long_recording_only
                else "query_capture_error"
            )
            checks.check(name, False, str(exc))
        finally:
            with contextlib.suppress(ModPipeError):
                client.input_lock(False)
        return 0

    if args.use_lifecycle_only:
        try:
            check_use_lifecycle(client, checks)
        except WorldLost:
            raise
        except (ModPipeError, OSError, ValueError) as exc:
            checks.check("use_lifecycle_error", False, str(exc))
        return 0

    if args.mod_core_live_only:
        try:
            check_mod_core_live(client, checks)
        except WorldLost:
            raise
        except (ModPipeError, OSError, ValueError) as exc:
            checks.check("mod_core_live_error", False, str(exc))
        return 0

    if args.chat_lifecycle_world:
        try:
            check_chat_lifecycle(client, checks, args.chat_lifecycle_world)
        except WorldLost:
            raise
        except (ModPipeError, OSError, ValueError) as exc:
            checks.check("chat_lifecycle_error", False, str(exc))
        return 0

    return check_default_suite(
        client,
        checks,
        args,
        observation,
        require_world=require_world,
        wait_ticks=wait_ticks,
        dump_packet_tap=dump_packet_tap,
    )


if __name__ == "__main__":
    raise SystemExit(main())
