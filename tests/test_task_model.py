"""Compile and run the production task controller without the game/SDK."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TaskModelTests(unittest.TestCase):
    def test_native_lifecycle_and_allocation_contract(self) -> None:
        compiler = shutil.which("clang++")
        # Also support local LLVM installations without a PATH entry.
        candidates = (
            Path("C:/Program Files/LLVM/bin/clang++.exe"),
            Path("D:/Tools/LLVM22/bin/clang++.exe"),
        )
        if compiler is None:
            compiler = next((str(path) for path in candidates if path.is_file()), None)
        if compiler is None:
            if os.environ.get("CI"):
                self.fail("CI must provide clang++ for the native task controller check")
            self.skipTest("clang++ required for the native task controller check")
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "task_model.exe"
            result = subprocess.run(
                [
                    compiler,
                    "-std=c++20",
                    "-fuse-ld=lld",
                    "-pthread",
                    "-I",
                    str(ROOT / "mod/probe/src"),
                    str(ROOT / "tests/native/task_model.cpp"),
                    "-o",
                    str(executable),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run(
                [str(executable)], capture_output=True, text=True, timeout=15, check=False
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
