from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.runner import RunError, attribute_step, run_scenario  # noqa: E402
from driver.scenario import normalize  # noqa: E402


class FakeAgent:
    def __init__(self) -> None:
        self.tick = 100
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail_on: set[str] = set()
        self.warnings: list[str] = []

    def call(self, name: str, *args: Any) -> Any:
        self.calls.append((name, args))
        if name in self.fail_on:
            return {"ok": False, "error": f"{name} failed"}
        if name == "get_tick_count":
            return self.tick
        if name == "get_state":
            return {
                "ok": True,
                "tick": self.tick,
                "pos": [0.0, 0.0, 0.0],
                "vel": [0.0, 0.0, 0.0],
                "on_ground": True,
                "yaw": 0.0,
                "pitch": 0.0,
            }
        if name == "start_recording":
            return {"ok": True, "start_tick": self.tick, "max": args[0]}
        if name == "stop_recording":
            return {
                "ok": True,
                "start_tick": 100,
                "end_tick": self.tick,
                "samples": [],
                "dropped": 0,
            }
        if name == "set_input":
            return {"ok": True, "applied": args[0], "warnings": list(self.warnings)}
        return {"ok": True}

    def wait_ticks(self, ticks: int, *, timeout: float | None = None) -> int:
        self.tick += ticks
        return self.tick


class RunnerTest(unittest.TestCase):
    def test_step_order_and_marks(self) -> None:
        agent = FakeAgent()
        scen = normalize(
            {
                "name": "t",
                "steps": [
                    {"type": "tp", "pos": [1, -60, 2]},
                    {"type": "look", "yaw": 90, "pitch": 0},
                    {"type": "input", "forward": 1, "ticks": 5},
                    {"type": "wait", "ticks": 2},
                ],
            }
        )
        result = run_scenario(agent, scen)

        names = [name for name, _ in agent.calls]
        self.assertIn("start_recording", names)
        self.assertIn("stop_input", names)
        self.assertEqual(names[-1], "stop_recording")

        self.assertEqual([m.type for m in result.marks], ["tp", "look", "input", "wait"])
        self.assertEqual(result.marks[0].start_tick, 100)
        self.assertEqual(result.marks[0].end_tick, 105)
        self.assertEqual(result.marks[2].end_tick, 100 + 5 + 1 + 5)
        self.assertEqual(result.end_state["ok"], True)

        tp_call = next(args for name, args in agent.calls if name == "tp")
        self.assertEqual(tp_call, (1.0, -60.0, 2.0, "server"))
        look_call = next(args for name, args in agent.calls if name == "set_yaw_pitch")
        self.assertEqual(look_call, (90.0, 0.0))

    def test_failure_raises_and_releases_input(self) -> None:
        agent = FakeAgent()
        agent.fail_on.add("set_vel")
        scen = normalize({"steps": [{"type": "vel", "v": [1, 2, 3]}]})
        with self.assertRaises(RunError):
            run_scenario(agent, scen)
        self.assertEqual(agent.calls[-1][0], "stop_input")

    def test_warnings_are_collected(self) -> None:
        agent = FakeAgent()
        agent.warnings = ["sneak is not injected"]
        scen = normalize(
            {"steps": [{"type": "input", "forward": 0, "sneak": True, "ticks": 1}]}
        )
        result = run_scenario(agent, scen)
        self.assertEqual(result.warnings, ["step 0 (input): sneak is not injected"])

    def test_jump_step_uses_single_request(self) -> None:
        agent = FakeAgent()
        scen = normalize({"steps": [{"type": "jump", "ticks": 2}]})
        result = run_scenario(agent, scen)
        names = [name for name, _ in agent.calls]
        self.assertIn("jump", names)
        self.assertNotIn("set_input", names)
        self.assertEqual(result.marks[0].end_tick, agent.tick)

    def test_setup_and_tp_use_command_sender(self) -> None:
        agent = FakeAgent()
        sent: list[str] = []
        scen = normalize(
            {
                "setup": {"commands": ["tp @s 0 -104 0"]},
                "steps": [{"type": "tp", "pos": [1, 2, 3]}],
            }
        )
        result = run_scenario(agent, scen, command_sender=sent.append)

        self.assertEqual(sent, ["tp @s 0 -104 0"])
        self.assertIn(("tp", (1.0, 2.0, 3.0, "server")), agent.calls)
        self.assertTrue(scen.setup["commands_executed"])
        self.assertEqual(result.marks[0].index, 0)

    def test_tp_falls_back_to_agent_with_warning(self) -> None:
        agent = FakeAgent()
        scen = normalize({"steps": [{"type": "tp", "pos": [1, 2, 3], "mode": "direct"}]})
        result = run_scenario(agent, scen)
        self.assertIn(("tp", (1.0, 2.0, 3.0, "direct")), agent.calls)
        self.assertTrue(
            any("客户端写位置" in warning for warning in result.warnings),
            result.warnings,
        )

    def test_world_input_calls_move_world(self) -> None:
        agent = FakeAgent()
        scen = normalize(
            {"steps": [{"type": "input", "world": [0, 1], "ticks": 2}]}
        )
        run_scenario(agent, scen)
        names = [name for name, _ in agent.calls]
        self.assertIn("move_world", names)
        self.assertNotIn("set_input", names)

    def test_zero_tick_wait(self) -> None:
        agent = FakeAgent()
        scen = normalize({"steps": [{"type": "wait", "ticks": 0}]})
        result = run_scenario(agent, scen)
        self.assertEqual(result.marks[0].start_tick, result.marks[0].end_tick)


class AttributeStepTest(unittest.TestCase):
    def test_ranges(self) -> None:
        from driver.runner import Mark

        marks = [Mark(0, "wait", 0, 10), Mark(1, "input", 10, 20)]
        self.assertEqual(attribute_step(0, marks), 0)
        self.assertEqual(attribute_step(9, marks), 0)
        self.assertEqual(attribute_step(10, marks), 1)
        self.assertEqual(attribute_step(50, marks), 1)
        self.assertIsNone(attribute_step(-1, []))


class SetupVerifyTest(unittest.TestCase):
    def _scenario(self, **setup: Any):
        return normalize({"steps": [{"type": "wait", "ticks": 0}], "setup": setup})

    def test_verify_passes(self) -> None:
        agent = FakeAgent()
        scen = self._scenario(verify=[{"pos": [1, -60, 2], "block": "minecraft:stone"}])
        reader = lambda x, y, z: {"ok": True, "air": False, "name": "minecraft:stone"}  # noqa: E731
        result = run_scenario(agent, scen, block_reader=reader)
        self.assertTrue(scen.setup["verified"])
        self.assertEqual(result.warnings, [])

    def test_expected_air(self) -> None:
        agent = FakeAgent()
        scen = self._scenario(verify=[{"pos": [1, -60, 2], "block": "air"}])
        reader = lambda x, y, z: {"ok": True, "air": True, "name": "minecraft:air"}  # noqa: E731
        run_scenario(agent, scen, block_reader=reader)
        self.assertTrue(scen.setup["verified"])

    def test_verify_mismatch_warns(self) -> None:
        agent = FakeAgent()
        scen = self._scenario(verify=[{"pos": [1, -60, 2], "block": "minecraft:stone"}])
        reader = lambda x, y, z: {"ok": True, "air": True, "name": "minecraft:air"}  # noqa: E731
        result = run_scenario(agent, scen, block_reader=reader)
        self.assertFalse(scen.setup["verified"])
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("期望 minecraft:stone", result.warnings[0])
        self.assertIn("实际 air", result.warnings[0])

    def test_verify_mismatch_strict_raises(self) -> None:
        agent = FakeAgent()
        scen = self._scenario(
            strict=True,
            verify=[{"pos": [1, -60, 2], "block": "minecraft:stone"}],
        )
        reader = lambda x, y, z: {"ok": True, "air": True, "name": "minecraft:air"}  # noqa: E731
        with self.assertRaises(RunError):
            run_scenario(agent, scen, block_reader=reader)

    def test_verify_without_reader_warns(self) -> None:
        agent = FakeAgent()
        scen = self._scenario(verify=[{"pos": [1, -60, 2], "block": "air"}])
        result = run_scenario(agent, scen)
        self.assertFalse(scen.setup["verified"])
        self.assertTrue(result.warnings)

    def test_verify_read_error_counts_as_failure(self) -> None:
        agent = FakeAgent()
        scen = self._scenario(verify=[{"pos": [1, -60, 2], "block": "air"}])

        def reader(x, y, z):
            raise RuntimeError("boom")

        result = run_scenario(agent, scen, block_reader=reader)
        self.assertFalse(scen.setup["verified"])
        self.assertIn("读取失败", result.warnings[0])


class SetupCommandsTest(unittest.TestCase):
    def _scenario(self, **setup: Any):
        return normalize(
            {
                "steps": [{"type": "wait", "ticks": 0}],
                "setup": {"commands": ["time set day"], **setup},
            }
        )

    def test_commands_reach_the_sender(self) -> None:
        agent = FakeAgent()
        scen = self._scenario()
        sent: list[str] = []

        def sender(command: str):
            sent.append(command)
            return {"ok": True}

        run_scenario(agent, scen, command_sender=sender)
        self.assertEqual(sent, ["time set day"])
        self.assertTrue(scen.setup["commands_executed"])

    def test_failed_command_raises(self) -> None:
        agent = FakeAgent()
        scen = self._scenario()
        with self.assertRaises(RunError):
            run_scenario(agent, scen, command_sender=lambda c: {"ok": False, "error": "nope"})

    def test_missing_sender_warns(self) -> None:
        agent = FakeAgent()
        scen = self._scenario()
        result = run_scenario(agent, scen)
        self.assertFalse(scen.setup["commands_executed"])
        self.assertTrue(result.warnings)

    def test_missing_sender_strict_raises(self) -> None:
        agent = FakeAgent()
        scen = self._scenario(strict=True)
        with self.assertRaises(RunError):
            run_scenario(agent, scen)


class LookTest(unittest.TestCase):
    def test_command_mode_uses_chat(self) -> None:
        agent = FakeAgent()
        scen = normalize({"steps": [{"type": "look", "yaw": 90, "pitch": 10, "ticks": 1}]})
        sent: list[str] = []

        def sender(command: str):
            sent.append(command)
            return {"ok": True}

        result = run_scenario(agent, scen, command_sender=sender)
        self.assertEqual(sent, ["tp @s ~ ~ ~ 90.0 10.0"])
        self.assertIn(("clear_look", ()), agent.calls)
        self.assertNotIn("set_yaw_pitch", [name for name, _ in agent.calls])
        self.assertEqual(result.marks[0].result["via"], "chat")

    def test_memory_mode_warns(self) -> None:
        agent = FakeAgent()
        scen = normalize({"steps": [{"type": "look", "yaw": 0, "pitch": 0, "mode": "memory"}]})
        result = run_scenario(agent, scen)
        self.assertIn(("set_yaw_pitch", (0.0, 0.0)), agent.calls)
        self.assertTrue(any("服务端仍按真实朝向" in w for w in result.warnings), result.warnings)

    def test_memory_mode_without_channel_does_not_warn_about_commands(self) -> None:
        agent = FakeAgent()
        scen = normalize({"steps": [{"type": "look", "yaw": 0, "pitch": 0, "mode": "memory"}]})
        result = run_scenario(agent, scen)
        self.assertEqual(len(result.warnings), 1)


class TeardownTest(unittest.TestCase):
    def _scenario(self):
        return normalize(
            {
                "steps": [{"type": "wait", "ticks": 0}],
                "setup": {
                    "commands": ["fill 0 -60 0 1 -60 1 stone"],
                    "teardown": ["fill 0 -60 0 1 -60 1 air"],
                },
            }
        )

    def test_teardown_runs_after_steps(self) -> None:
        agent = FakeAgent()
        scen = self._scenario()
        sent: list[str] = []
        run_scenario(agent, scen, command_sender=lambda c: sent.append(c) or {"ok": True})
        self.assertEqual(sent, ["fill 0 -60 0 1 -60 1 stone", "fill 0 -60 0 1 -60 1 air"])
        self.assertTrue(scen.setup["teardown_done"])

    def test_teardown_runs_even_if_a_step_fails(self) -> None:
        agent = FakeAgent()
        agent.fail_on = {"move_world"}
        scen = normalize(
            {
                "steps": [{"type": "input", "world": [0, 1], "ticks": 1}],
                "setup": {"teardown": ["fill 0 -60 0 1 -60 1 air"]},
            }
        )
        sent: list[str] = []
        with self.assertRaises(RunError):
            run_scenario(agent, scen, command_sender=lambda c: sent.append(c) or {"ok": True})
        self.assertEqual(sent, ["fill 0 -60 0 1 -60 1 air"])

    def test_teardown_without_channel_warns(self) -> None:
        agent = FakeAgent()
        scen = self._scenario()
        result = run_scenario(agent, scen)
        self.assertFalse(scen.setup["teardown_done"])
        self.assertTrue(any("teardown" in w for w in result.warnings), result.warnings)


if __name__ == "__main__":
    unittest.main()
