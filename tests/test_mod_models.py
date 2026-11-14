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

    # One data-driven case per native model. Each model is a self-contained
    # C++ program that asserts its own contract; the Python side only builds and
    # runs it, so a method per model duplicated the same nine lines 30 times.
    MODELS = (
        "transport_model",
        "record_model",
        "tick_stream_model",
        "atomic_ring_model",
        "violation_watch_model",
        "request_model",
        "chat_model",
        "query_model",
        "pose_model",
        "player_identity_model",
        "entity_model",
        "ecs_model",
        "input_event_model",
        "input_session_model",
        "movement_model",
        "tick_recovery_model",
        "flight_model",
        "teleport_model",
        "packet_injection_model",
        "packet_tap_model",
        "macro_playback_model",
        "macro_record_model",
        "macro_session_model",
        "world_entry_model",
        "overhead_model",
        "lifecycle_model",
        "callback_lifecycle_model",
        "send_probe_model",
        "input_lifecycle_model",
    )

    def test_native_models(self) -> None:
        for name in self.MODELS:
            with self.subTest(model=name):
                self.run_native(name)

    @unittest.skipUnless(sys.platform == "win32", "Windows named pipes required")
    def test_pipe_cancellation_and_nonreading_client(self) -> None:
        self.run_native("pipe_channel")
