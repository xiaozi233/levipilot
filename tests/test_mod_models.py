"""Exercise production mod I/O and recording models without the game or SDK."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ModModelTests(unittest.TestCase):
    def run_native(self, name: str) -> None:
        compiler = shutil.which("clang++")
        if compiler is None:
            compiler = next(
                (
                    str(path)
                    for path in (
                        Path("D:/Tools/LLVM22/bin/clang++.exe"),
                        Path("C:/Program Files/LLVM/bin/clang++.exe"),
                    )
                    if path.is_file()
                ),
                None,
            )
        if compiler is None:
            if os.environ.get("CI"):
                self.fail("CI must provide clang++ for the native mod checks")
            self.skipTest("clang++ required for the native mod checks")
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / f"{name}.exe"
            build = subprocess.run(
                [
                    compiler,
                    "-std=c++20",
                    "-O2",
                    "-fuse-ld=lld",
                    "-pthread",
                    "-I",
                    str(ROOT / "mod/probe/src"),
                    str(ROOT / f"tests/native/{name}.cpp"),
                    "-o",
                    str(executable),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run(
                [str(executable)], capture_output=True, text=True, timeout=20, check=False
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_command_framing_boundaries(self) -> None:
        self.run_native("transport_model")

    def test_record_session_and_wraparound(self) -> None:
        self.run_native("record_model")

    def test_tick_stream_order_terminal_races_and_parkour_baselines(self) -> None:
        self.run_native("tick_stream_model")

    def test_atomic_ring_publication_and_overwrite(self) -> None:
        self.run_native("atomic_ring_model")

    def test_request_transactions_and_concurrent_snapshots(self) -> None:
        self.run_native("request_model")

    def test_chat_timeout_cancellation_and_late_completion(self) -> None:
        self.run_native("chat_model")

    def test_query_publication_cancellation_and_scan_budget(self) -> None:
        self.run_native("query_model")

    def test_pose_snapshots_faults_and_concurrent_publication(self) -> None:
        self.run_native("pose_model")

    def test_player_identity_invalidation_epoch_and_concurrent_publication(self) -> None:
        self.run_native("player_identity_model")

    def test_entity_stage_timeout_cancellation_and_slot_snapshot(self) -> None:
        self.run_native("entity_model")

    def test_ecs_bounded_rows_failures_and_queued_lifecycle(self) -> None:
        self.run_native("ecs_model")

    def test_input_order_barriers_failures_cancellation_and_concurrent_wrap(self) -> None:
        self.run_native("input_event_model")

    def test_control_edge_recovery_nested_flags_and_concurrent_diagnostics(self) -> None:
        self.run_native("movement_model")

    def test_flight_assertion_budget_and_staged_lifecycle(self) -> None:
        self.run_native("flight_model")

    def test_teleport_admission_partial_mutations_and_stale_callbacks(self) -> None:
        self.run_native("teleport_model")

    def test_macro_playback_ownership_failures_and_restart(self) -> None:
        self.run_native("macro_playback_model")

    def test_macro_recording_hard_limit_and_unique_stop(self) -> None:
        self.run_native("macro_record_model")

    def test_world_entry_timeout_quarantine_and_obsolete_completion(self) -> None:
        self.run_native("world_entry_model")

    def test_overhead_window_concurrent_wrap_and_allocation_contract(self) -> None:
        self.run_native("overhead_model")

    @unittest.skipUnless(sys.platform == "win32", "Windows named pipes required")
    def test_pipe_cancellation_and_nonreading_client(self) -> None:
        self.run_native("pipe_channel")
