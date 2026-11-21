"""Probe smoke tests. The default live suite requires the game in a world.

    py -3.11 driver/selftest.py
    py -3.11 driver/selftest.py --list          # list modes and default-suite sections
    py -3.11 driver/selftest.py --only flight --world "超平坦测试"
    py -3.11 driver/selftest.py --only default:parkour,swim
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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver import selftest_diagnostics  # noqa: E402
from driver.harness import ModClient, ModPipeError, ModProtocolError, ModTimeout  # noqa: E402,F401
from driver.harness.lab import Builder, capture_disconnect_report, settle  # noqa: E402,F401
from driver.selftest_callbacks import check_callback_drain  # noqa: E402
from driver.selftest_core import (  # noqa: E402,F401
    check_block_identity,
    check_cli_compatibility,
    check_dispatch_compatibility,
    check_overhead_window,
)
from driver.selftest_default import DEFAULT_SECTIONS, check_default_suite  # noqa: E402
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
from driver.selftest_security import check_security  # noqa: E402
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


def _flag_dest(flag: str) -> str:
    return flag.lstrip("-").replace("-", "_")


def _require_world_arg(world: str | None) -> str:
    if not world:
        raise ValueError("world name is required")
    return world


@dataclass(frozen=True)
class ModeSpec:
    """Registered selftest mode mapped to legacy flags and ``--only``."""

    name: str
    runner: Callable[
        [ModClient, Checks, argparse.Namespace, ViolationObservation, str | None], None
    ]
    error_name: str
    help: str
    only_flag: str | None = None
    world_flag: str | None = None
    world_help: str = ""
    accepts_world: bool = False
    requires_world: bool = False
    lock_input: bool = True
    propagate_world_lost: bool = True
    catch: tuple[type[BaseException], ...] = (ModPipeError, OSError, ValueError)
    offline_suites: tuple[str, ...] = ()


def _noop_runner(
    _client: ModClient,
    _checks: Checks,
    _args: argparse.Namespace,
    _observation: ViolationObservation,
    _world: str | None,
) -> None:
    return None


def _run_violation(
    client: ModClient,
    checks: Checks,
    _args: argparse.Namespace,
    observation: ViolationObservation,
    _world: str | None,
) -> None:
    with observation.gap("violation_selftest"):
        check_violation_watch(client, checks)
    check_send_probe(client, checks)


def _run_hot_reload(
    client: ModClient,
    checks: Checks,
    _args: argparse.Namespace,
    observation: ViolationObservation,
    _world: str | None,
) -> None:
    with observation.reload_gap():
        check_hot_reload(client, checks, close_client=False)


def _run_callback_drain(
    client: ModClient,
    checks: Checks,
    _args: argparse.Namespace,
    observation: ViolationObservation,
    _world: str | None,
) -> None:
    with observation.reload_gap():
        check_callback_drain(client, checks)


def _run_security(
    client: ModClient,
    checks: Checks,
    _args: argparse.Namespace,
    observation: ViolationObservation,
    _world: str | None,
) -> None:
    with observation.reload_gap():
        check_security(client, checks)


def _run_macro_long(
    client: ModClient,
    checks: Checks,
    args: argparse.Namespace,
    _observation: ViolationObservation,
    _world: str | None,
) -> None:
    if args.macro_resume:
        check_macro_long_recording(client, checks, resume=True)
    else:
        check_macro_long_recording(client, checks)


def _run_record_medium(
    client: ModClient,
    checks: Checks,
    _args: argparse.Namespace,
    _observation: ViolationObservation,
    _world: str | None,
) -> None:
    check_block_identity(client, checks)
    check_record_medium(client, checks)


def _run_controls(
    client: ModClient,
    checks: Checks,
    _args: argparse.Namespace,
    _observation: ViolationObservation,
    _world: str | None,
) -> None:
    check_input_mode_contract(client, checks)
    check_controls(client, checks)


_CLIENT_IO_SUITES = (
    "tests.test_transport.SessionTests",
    "tests.test_transport_stress",
    "tests.test_transport.LineDecoderTests",
    "tests.test_recording_commands",
    "tests.test_mod_agent.RecordingIntegrationTests",
    "tests.test_dev_config",
    "tests.test_dev.GameShutdownTests",
    "tests.test_spool",
)


MODE_REGISTRY: tuple[ModeSpec, ...] = (
    ModeSpec(
        name="client-io",
        runner=_noop_runner,
        error_name="client_io_error",
        help="run offline decoder/recording/spool regressions without connecting to the game",
        only_flag="--client-io-only",
        lock_input=False,
        offline_suites=_CLIENT_IO_SUITES,
    ),
    ModeSpec(
        name="mod-core",
        runner=_noop_runner,
        error_name="mod_core_error",
        help="compile/run native mod transport/recording checks without connecting to the game",
        only_flag="--mod-core-only",
        lock_input=False,
        offline_suites=("tests.test_mod_models",),
    ),
    ModeSpec(
        name="sdk-boundaries",
        runner=lambda c, k, _a, _o, _w: check_sdk_boundaries(c, k),
        error_name="sdk_boundaries_error",
        help="SDK lookup/submit/run fault recovery",
        only_flag="--sdk-boundaries-only",
        lock_input=False,
        catch=(ModPipeError, OSError, ValueError, KeyError),
    ),
    ModeSpec(
        name="diagnostic-export",
        runner=lambda c, k, _a, _o, _w: check_diagnostic_export(c, k),
        error_name="diagnostic_export_error",
        help="export existing diagnostic buffers without arming hooks or changing the world",
        only_flag="--diagnostic-export-only",
        lock_input=False,
        propagate_world_lost=False,
        catch=(ModPipeError, OSError, ValueError, KeyError),
    ),
    ModeSpec(
        name="connection",
        runner=lambda c, k, _a, _o, _w: check_connection_lifecycle(c, k),
        error_name="connection_lifecycle_error",
        help="check abandoned clients and process-exit reconnects; no world changes or RE",
        only_flag="--connection-only",
        lock_input=False,
        propagate_world_lost=False,
        catch=(ModPipeError, OSError, ValueError, subprocess.SubprocessError),
    ),
    ModeSpec(
        name="overhead",
        runner=lambda c, k, _a, _o, _w: check_overhead_window(c, k, wrap=True),
        error_name="overhead_window_error",
        help="read overhead concurrently across 1100 real ticks and verify record path counters",
        only_flag="--overhead-only",
        lock_input=False,
    ),
    ModeSpec(
        name="use-lifecycle",
        runner=lambda c, k, _a, _o, _w: check_use_lifecycle(c, k),
        error_name="use_lifecycle_error",
        help="inject missing/failed server work and verify task termination/recovery in a world",
        only_flag="--use-lifecycle-only",
        lock_input=False,
    ),
    ModeSpec(
        name="mod-core-live",
        runner=lambda c, k, _a, _o, _w: check_mod_core_live(c, k),
        error_name="mod_core_live_error",
        help="check installed pipe/recording only; no RE, input injection or world edits",
        only_flag="--mod-core-live-only",
        lock_input=False,
    ),
    ModeSpec(
        name="chat-lifecycle",
        runner=lambda c, k, _a, _o, w: check_chat_lifecycle(c, k, _require_world_arg(w)),
        error_name="chat_lifecycle_error",
        help="save/leave, check chat cancellation and main menu recovery, then re-enter WORLD",
        world_flag="--chat-lifecycle-world",
        world_help="save/leave, check chat cancellation and main menu recovery, then re-enter WORLD",
        accepts_world=True,
        requires_world=True,
        lock_input=False,
    ),
    ModeSpec(
        name="record-medium",
        runner=_run_record_medium,
        error_name="record_medium_error",
        help="check recording in an isolated water fixture",
        only_flag="--record-medium-only",
    ),
    ModeSpec(
        name="packet-tap",
        runner=lambda c, k, _a, _o, _w: check_packet_tap(c, k),
        error_name="packet_tap_error",
        help="check isolation of packet observation faults",
        only_flag="--packet-tap-only",
    ),
    ModeSpec(
        name="violation",
        runner=_run_violation,
        error_name="violation_watch_error",
        help="check the read-only violation watch (synthetic row, no real violation)",
        only_flag="--violation-only",
    ),
    ModeSpec(
        name="packet-injection",
        runner=lambda c, k, _a, _o, w: check_packet_injection(c, k, w),
        error_name="packet_injection_error",
        help="check command templates and owned packet sends",
        only_flag="--packet-injection-only",
        world_flag="--packet-injection-world",
        world_help="also check packet reconnect/leave/rejoin",
        accepts_world=True,
    ),
    ModeSpec(
        name="tick-recovery",
        runner=lambda c, k, _a, _o, w: check_tick_recovery(c, k, w),
        error_name="tick_recovery_error",
        help="check tick errors and fail-stop recovery",
        only_flag="--tick-recovery-only",
        world_flag="--tick-recovery-world",
        world_help="also check recovery across reconnect/leave/rejoin",
        accepts_world=True,
    ),
    ModeSpec(
        name="hot-reload",
        runner=_run_hot_reload,
        error_name="hot_reload_error",
        help="reload probe through chat input and verify the same live process",
        only_flag="--hot-reload-only",
    ),
    ModeSpec(
        name="lifecycle-prepare",
        runner=lambda c, k, _a, _o, _w: check_lifecycle_prepare(c, k),
        error_name="lifecycle_prepare_error",
        help="check active/expired input unload gates and restore hooks without reloading",
        only_flag="--lifecycle-prepare-only",
    ),
    ModeSpec(
        name="callback-drain",
        runner=_run_callback_drain,
        error_name="callback_drain_error",
        help="delay client/server/tick callbacks, test timeout rollback and real reloads",
        only_flag="--callback-drain-only",
    ),
    ModeSpec(
        name="security",
        runner=_run_security,
        error_name="security_error",
        help="verify live pipe DACL, pipe_token auth, allow_mem_read and allow_togglefly across reload",
        only_flag="--security-only",
    ),
    ModeSpec(
        name="teleport",
        runner=lambda c, k, _a, _o, w: check_teleport(c, k, w),
        error_name="teleport_error",
        help="check owned server teleports and failures",
        only_flag="--teleport-only",
        world_flag="--teleport-world",
        world_help="also check teleport reconnect/leave/rejoin",
        accepts_world=True,
    ),
    ModeSpec(
        name="controls",
        runner=_run_controls,
        error_name="controls_error",
        help="check control faults and input release",
        only_flag="--controls-only",
    ),
    ModeSpec(
        name="player-identity",
        runner=lambda c, k, _a, _o, w: check_player_identity(c, k, w),
        error_name="identity_error",
        help="check local UUID sampling and server resolution",
        only_flag="--player-identity-only",
        world_flag="--player-identity-world",
        world_help="also reject identities retained across worlds",
        accepts_world=True,
    ),
    ModeSpec(
        name="flight",
        runner=lambda c, k, _a, _o, w: check_flight(c, k, w),
        error_name="flight_error",
        help="check staged flight and tick assertions",
        only_flag="--flight-only",
        world_flag="--flight-world",
        world_help="also check flight reconnect/leave/rejoin",
        accepts_world=True,
    ),
    ModeSpec(
        name="input-events",
        runner=lambda c, k, _a, _o, w: check_input_events(c, k, w),
        error_name="input_events_error",
        help="check guarded input and text ordering",
        only_flag="--input-events-only",
        world_flag="--input-events-world",
        world_help="also check input reconnect/leave/rejoin",
        accepts_world=True,
    ),
    ModeSpec(
        name="ecs-capture",
        runner=lambda c, k, _a, _o, w: check_ecs_capture(c, k, w),
        error_name="ecs_capture_error",
        help="check owned ECS snapshots and failures",
        only_flag="--ecs-capture-only",
        world_flag="--ecs-capture-world",
        world_help="also check ECS reconnect/leave/rejoin",
        accepts_world=True,
    ),
    ModeSpec(
        name="macro-playback",
        runner=lambda c, k, _a, _o, w: check_macro_playback(c, k, w),
        error_name="macro_playback_error",
        help="check replay faults and frame ownership",
        only_flag="--macro-playback-only",
        world_flag="--macro-playback-world",
        world_help="also check playback across leave/rejoin",
        accepts_world=True,
    ),
    ModeSpec(
        name="binary-capture",
        runner=lambda c, k, _a, _o, w: check_binary_capture(c, k, w),
        error_name="binary_capture_error",
        help="check binary recording failure outcomes",
        only_flag="--binary-capture-only",
        world_flag="--binary-capture-world",
        world_help="also check recording across leave/rejoin",
        accepts_world=True,
    ),
    ModeSpec(
        name="tick-safety",
        runner=lambda c, k, _a, _o, w: check_tick_safety(c, k, w),
        error_name="tick_safety_error",
        help="check guarded hook calls and coherent poses",
        only_flag="--tick-safety-only",
        world_flag="--tick-safety-world",
        world_help="also check hook cancellation and pose validity on leave/rejoin",
        accepts_world=True,
    ),
    ModeSpec(
        name="macro-capture",
        runner=lambda c, k, _a, _o, w: check_macro_capture(c, k, w),
        error_name="macro_capture_error",
        help="check macro sampling faults and retained saves",
        only_flag="--macro-capture-only",
        world_flag="--macro-capture-world",
        world_help="also check macro recording across leave/rejoin",
        accepts_world=True,
    ),
    ModeSpec(
        name="tick-stream",
        runner=lambda c, k, _a, _o, w: check_tick_streams(c, k, w),
        error_name="tick_stream_error",
        help="check JSON streams and parkour event safety",
        only_flag="--tick-stream-only",
        world_flag="--tick-stream-world",
        world_help="also check stream termination on leave/rejoin",
        accepts_world=True,
    ),
    ModeSpec(
        name="entry-lifecycle",
        runner=lambda c, k, _a, _o, w: check_entry_lifecycle(c, k, _require_world_arg(w)),
        error_name="entry_lifecycle_error",
        help="save/leave, check entry timeout/quarantine and missing join recovery, then restore WORLD",
        world_flag="--entry-lifecycle-world",
        world_help="save/leave, check entry timeout/quarantine and missing join recovery, then restore WORLD",
        accepts_world=True,
        requires_world=True,
    ),
    ModeSpec(
        name="macro-limit",
        runner=lambda c, k, _a, _o, _w: check_macro_record_limits(c, k),
        error_name="macro_limit_error",
        help="check recording limit normalization, unique termination and buffered data preservation",
        only_flag="--macro-limit-only",
    ),
    ModeSpec(
        name="macro-long-recording",
        runner=_run_macro_long,
        error_name="macro_long_recording_error",
        help="record, save and reload all 200000 ticks at real speed (about 2h47 at 20 TPS)",
        only_flag="--macro-long-recording-only",
    ),
    ModeSpec(
        name="query-lifecycle",
        runner=lambda c, k, _a, _o, w: check_query_lifecycle(c, k, _require_world_arg(w)),
        error_name="query_lifecycle_error",
        help="check scan cancellation on reconnect/exit, then re-enter WORLD",
        world_flag="--query-lifecycle-world",
        world_help="check scan cancellation on reconnect/exit, then re-enter WORLD",
        accepts_world=True,
        requires_world=True,
    ),
    ModeSpec(
        name="query-capture",
        runner=lambda c, k, _a, _o, _w: check_query_capture(c, k),
        error_name="query_capture_error",
        help="check fixed query snapshots, SEH/timeout recovery and per-tick scan budget",
        only_flag="--query-capture-only",
    ),
)

_MODE_MAP: dict[str, ModeSpec] = {spec.name: spec for spec in MODE_REGISTRY}
_DEFAULT_SECTION_NAMES: frozenset[str] = frozenset(name for name, _ in DEFAULT_SECTIONS)


def _format_mode_list() -> str:
    offline = [spec for spec in MODE_REGISTRY if spec.offline_suites]
    live = [spec for spec in MODE_REGISTRY if not spec.offline_suites]
    lines = ["Offline modes:"]
    for spec in offline:
        lines.append(f"  {spec.name}  - {spec.help}")
    lines.append("")
    lines.append("Live modes:")
    for spec in live:
        note = " (supports --world)" if spec.accepts_world else ""
        req = " [requires --world]" if spec.requires_world else ""
        lines.append(f"  {spec.name}{note}{req}  - {spec.help}")
    lines.append("")
    lines.append("Default suite sections (--only default:<sec1,sec2> or --only <section>):")
    for name, _ in DEFAULT_SECTIONS:
        lines.append(f"  {name}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-complete", action="store_true", help="fail when required coverage is skipped"
    )
    parser.add_argument("--entity", default="minecraft:pig")
    parser.add_argument("--keep", action="store_true", help="do not remove the test entity")
    parser.add_argument("--radius", type=float, default=10.0)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--list",
        action="store_true",
        help="list registered offline/live modes and default-suite sections, then exit",
    )
    modes.add_argument(
        "--only",
        metavar="NAME",
        help="run one registered mode or default-suite section(s) (default:<sec1,sec2>)",
    )
    for spec in MODE_REGISTRY:
        if spec.only_flag:
            modes.add_argument(spec.only_flag, action="store_true", help=spec.help)
        if spec.world_flag:
            modes.add_argument(spec.world_flag, metavar="WORLD", help=spec.world_help or spec.help)
    parser.add_argument(
        "--world",
        metavar="WORLD",
        help="target world when running --only <mode> for world-lifecycle checks",
    )
    parser.add_argument(
        "--macro-resume",
        action="store_true",
        help="with --macro-long-recording-only, resume the same active recording after a watcher restart",
    )
    parser.add_argument("--stress-cycles", type=int, default=0, help="opt-in lifecycle soak cycles")
    parser.add_argument(
        "--stress-world", help="leave/re-enter this test world every 10 soak cycles"
    )
    return parser


def _parse_default_sections(raw: str) -> tuple[str, ...] | None:
    if raw == "default":
        return ()
    body = raw[len("default:") :] if raw.startswith("default:") else raw
    parts = [part.strip() for part in body.split(",") if part.strip()]
    if not parts:
        return None
    if all(part in _DEFAULT_SECTION_NAMES for part in parts):
        return tuple(parts)
    return None


def _resolve_selection(
    args: argparse.Namespace, parser: argparse.ArgumentParser | None
) -> tuple[ModeSpec | None, str | None, tuple[str, ...] | None]:
    def fail(message: str) -> None:
        if parser is not None:
            parser.error(message)
        raise ValueError(message)

    world_opt = getattr(args, "world", None)
    only_opt = getattr(args, "only", None)

    if args.macro_resume and not (
        args.macro_long_recording_only or only_opt == "macro-long-recording"
    ):
        fail("--macro-resume requires --macro-long-recording-only or --only macro-long-recording")
    if world_opt is not None and not only_opt:
        fail("--world requires --only <mode>")

    for spec in MODE_REGISTRY:
        if spec.only_flag and getattr(args, _flag_dest(spec.only_flag), False):
            return spec, None, None
        if spec.world_flag:
            world_value = getattr(args, _flag_dest(spec.world_flag), None)
            if world_value:
                return spec, world_value, None

    if only_opt:
        if only_opt in _MODE_MAP:
            spec = _MODE_MAP[only_opt]
            if spec.requires_world and not world_opt:
                fail(f"--only {spec.name} requires --world WORLD")
            if world_opt is not None and not spec.accepts_world:
                fail(f"--only {spec.name} does not accept --world")
            return spec, world_opt, None
        sections = _parse_default_sections(only_opt)
        if sections is not None:
            if world_opt is not None:
                fail("--only default does not accept --world")
            return None, None, sections or None
        fail(f"unknown mode or default section: {only_opt!r} (use --list)")

    return None, None, None


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list:
        print(_format_mode_list())
        return 0
    if args.stress_cycles < 0 or (args.stress_world and args.stress_cycles < 10):
        parser.error(
            "--stress-cycles must be nonnegative; --stress-world requires at least 10 cycles"
        )

    spec, world, default_sections = _resolve_selection(args, parser)
    if spec is not None and spec.offline_suites:
        return subprocess.run(
            [sys.executable, "-m", "unittest", *spec.offline_suites, "-v"],
            cwd=ROOT,
            check=False,
        ).returncode

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
        result = run_live(
            client,
            checks,
            args,
            observation,
            mode=spec,
            world=world,
            default_sections=default_sections,
        )
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
    client: ModClient,
    checks: Checks,
    args: argparse.Namespace,
    observation: ViolationObservation,
    *,
    mode: ModeSpec | None = None,
    world: str | None = None,
    default_sections: tuple[str, ...] | None = None,
) -> int:
    if mode is None and default_sections is None:
        mode, world, default_sections = _resolve_selection(args, None)

    if mode is not None:
        try:
            if mode.lock_input:
                client.input_lock(True)
            mode.runner(client, checks, args, observation, world)
        except WorldLost:
            if mode.propagate_world_lost:
                raise
        except mode.catch as exc:
            checks.check(mode.error_name, False, str(exc))
        finally:
            if mode.lock_input:
                with contextlib.suppress(ModPipeError):
                    client.input_lock(False)
        return 0

    return check_default_suite(
        client,
        checks,
        args,
        observation,
        require_world=require_world,
        wait_ticks=wait_ticks,
        dump_packet_tap=dump_packet_tap,
        sections=default_sections,
    )


if __name__ == "__main__":
    raise SystemExit(main())
