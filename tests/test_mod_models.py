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
    def _find_compiler(self) -> str:
        llvm_root = os.environ.get("LEVIPILOT_LLVM_ROOT")
        if llvm_root:
            rooted = Path(llvm_root) / "bin" / "clang++.exe"
            if rooted.is_file():
                return str(rooted)
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
        return compiler

    def _compile_and_run(self, source: Path, executable: Path) -> None:
        compiler = self._find_compiler()
        use_asan = os.environ.get("LEVIPILOT_NATIVE_ASAN", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        cmd = [
            compiler,
            "-std=c++20",
            "-O2",
            "-fuse-ld=lld",
            "-pthread",
        ]
        if use_asan:
            cmd.extend(["-fsanitize=address", "-fno-omit-frame-pointer", "-g"])
        cmd.extend(["-I", str(ROOT / "mod/probe/src"), str(source)])
        if sys.platform == "win32":
            cmd.append("-ladvapi32")
        cmd.extend(["-o", str(executable)])
        build = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        run_env = dict(os.environ)
        if use_asan and sys.platform == "win32":
            clang_dir = Path(compiler).resolve().parent.parent / "lib" / "clang"
            extra_paths = [str(p) for p in clang_dir.glob("*/lib/windows") if p.is_dir()]
            if extra_paths:
                run_env["PATH"] = os.pathsep.join([*extra_paths, run_env.get("PATH", "")])
        result = subprocess.run(
            [str(executable)],
            capture_output=True,
            text=True,
            timeout=20,
            env=run_env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def run_native(self, name: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._compile_and_run(
                ROOT / f"tests/native/{name}.cpp",
                Path(tmp) / f"{name}.exe",
            )

    def test_query_and_ecs_model_extensions(self) -> None:
        source = """
#include "mod/EcsModel.h"
#include "mod/QueryModel.h"

#include <atomic>
#include <cassert>
#include <cstdlib>
#include <memory>
#include <new>
#include <utility>
#include <vector>

std::atomic<unsigned> allocations{0};
void* operator new(std::size_t size) {
    ++allocations;
    if (auto* value = std::malloc(size)) return value;
    throw std::bad_alloc();
}
void operator delete(void* value) noexcept { std::free(value); }
void operator delete(void* value, std::size_t) noexcept { std::free(value); }

using namespace probe::detail;

int main() {
    auto stateCapture = std::make_unique<StateCapture>();
    auto const stateBaseline = allocations.load();
    assert(stateCapture->begin({}, 0, 2000));
    stateCapture->advance([](auto const&, StateSnapshot& out) {
        assert(out.posDelta.x == 0.0f && out.posDelta.y == 0.0f && out.posDelta.z == 0.0f);
        assert(out.prevYaw == 0.0f && out.prevPitch == 0.0f);
        assert(out.item.damage == 0 && out.item.maxDamage == 0);
        out.posDelta = {0.25f, -0.08f, 0.5f};
        out.prevYaw = 45.0f;
        out.prevPitch = -12.5f;
        out.item.present = true;
        out.item.type.assign("minecraft:elytra");
        out.item.damage = 430;
        out.item.maxDamage = 432;
        return true;
    });
    {
        auto done = stateCapture->take(1);
        assert(done && !done.error);
        auto const& s = done.snapshot();
        assert(s.posDelta.x == 0.25f && s.posDelta.y == -0.08f && s.posDelta.z == 0.5f);
        assert(s.prevYaw == 45.0f && s.prevPitch == -12.5f);
        assert(s.item.present && s.item.damage == 430 && s.item.maxDamage == 432);
    }
    assert(allocations.load() == stateBaseline);

    EcsCapture ecsCapture;
    EcsQuery query;
    query.operation = EcsOperation::Registries;
    assert(query.scope == EcsScope::Client);
    query.scope = EcsScope::All;
    auto req = ecsCapture.begin(query, 0, 2000);
    assert(req && req->dispatch() == EcsStage::Client);
    req->run(EcsStage::Client, 1, [](EcsSnapshot& out) {
        assert(!out.clientRegistry.present && !out.serverRegistry.present);
        out.playerRuntimeId = 77;
        out.clientRegistry = {0x1000, 11, 42, true};
        out.priorVisited = 42;
        return EcsStage::Server;
    });
    assert(req->dispatch() == EcsStage::Server);
    req->run(EcsStage::Server, 2, [](EcsSnapshot& out) {
        assert(out.playerRuntimeId == 77);
        assert(out.clientRegistry.present && out.clientRegistry.registry == 0x1000);
        out.serverRegistry = {0x2000, 12, 84, true};
        out.visited = out.priorVisited + 84;
        return EcsStage::Complete;
    });
    {
        auto done = ecsCapture.take(3);
        assert(done.request && !done.error);
        auto const& snap = done.request->snapshot;
        assert(snap.clientRegistry.present && snap.clientRegistry.components == 42);
        assert(snap.serverRegistry.present && snap.serverRegistry.components == 84);
        assert(snap.visited == 126);
    }

    std::vector<std::pair<std::uint32_t, unsigned>> storages{{10u, 0u}, {20u, 1u}};
    auto ecsOut = std::make_unique<EcsSnapshot>();
    auto const ecsBaseline = allocations.load();
    collectEcsRows(EcsOperation::Components, *ecsOut, storages, [](unsigned tagFlag, EcsSnapshot::Row& row, bool& present) {
        present = true;
        row.address = tagFlag == 0u ? 0u : 0xDEADu;
        row.count = 1;
        return true;
    });
    assert(allocations.load() == ecsBaseline);
    assert(ecsOut->count == 2 && ecsOut->rows[0].address == 0 && ecsOut->rows[1].address == 0xDEADu);
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            cpp_path = Path(tmp) / "query_ecs_ext.cpp"
            cpp_path.write_text(source, encoding="utf-8")
            self._compile_and_run(cpp_path, Path(tmp) / "query_ecs_ext.exe")

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
