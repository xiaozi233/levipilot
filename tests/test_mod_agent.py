from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.mod_agent import ModAgent, _tick_row  # noqa: E402
from driver.run_mod import _probe_scenario  # noqa: E402


class TickRowTest(unittest.TestCase):
    def test_maps_position_velocity_and_input(self) -> None:
        row = _tick_row(
            {
                "event": "tick",
                "tick": 12,
                "x": 1.5,
                "y": 64.0,
                "z": -3.25,
                "vx": 0.1,
                "vy": 0.0,
                "vz": -0.2,
                "on_ground": True,
                "yaw": 90.0,
                "pitch": 15.0,
                "w": True,
                "s": False,
                "a": False,
                "d": True,
                "sprint": False,
                "sneak": False,
                "jump": False,
                "health": 20,
            }
        )
        self.assertEqual(row["t"], 12)
        self.assertEqual(row["pos"], [1.5, 64.0, -3.25])
        self.assertEqual(row["vel"], [0.1, 0.0, -0.2])
        self.assertEqual(row["flags_lo"], 1 | 8)
        self.assertEqual(row["input"]["forward"], 1.0)
        self.assertEqual(row["input"]["strafe"], 1.0)
        self.assertTrue(row["ok"])

    def test_missing_flags_default_false(self) -> None:
        row = _tick_row({"event": "tick", "tick": 1, "x": 0, "y": 0, "z": 0})
        self.assertFalse(row["on_ground"])
        self.assertEqual(row["flags_lo"], 0)
        self.assertEqual(row["input"]["forward"], 0.0)


class FakeClient:
    """Records the calls ModAgent makes; no pipe involved."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.state_reply = {
            "in_world": True,
            "x": 0.5,
            "y": 70.0,
            "z": 0.5,
            "feet_x": 0.5,
            "feet_y": 68.38,
            "feet_z": 0.5,
            "vx": 0.0,
            "vy": 0.0,
            "vz": 0.0,
            "yaw": 0.0,
            "pitch": 0.0,
        }
        self.block_reply = {"blocks": [{"name": "minecraft:stone", "x": 0, "y": 0, "z": 0}]}
        self.command_reply = {"success": True, "code": 0}

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    def teleport(self, *args, **kwargs):
        self._record("teleport", *args, **kwargs)
        return {"event": "teleported"}

    def look(self, *args, **kwargs):
        self._record("look", *args, **kwargs)
        return {"event": "look_ok"}

    def set_input(self, *args, **kwargs):
        self._record("set_input", *args, **kwargs)
        return {"event": "set_ok"}

    def stop_move(self):
        self._record("stop_move")
        return {"event": "stopping"}

    def jump(self):
        self._record("jump")
        return {"event": "jump_ok"}

    def state(self):
        return dict(self.state_reply)

    def status(self):
        return {"tick": 100}

    def blocks(self, positions):
        self._record("blocks", positions)
        return dict(self.block_reply)

    def command(self, text):
        self._record("command", text)
        return dict(self.command_reply)


def make_agent(client: FakeClient) -> ModAgent:
    agent = object.__new__(ModAgent)
    agent.client = client
    agent.harness = None  # not used by the methods under test
    agent._samples = []
    agent._recording = False
    agent._move_flags = "none"
    return agent


class ModAgentTest(unittest.TestCase):
    def test_state_exposes_feet_position_for_tp(self) -> None:
        agent = make_agent(FakeClient())
        state = agent.get_state()
        self.assertTrue(state["ok"])
        self.assertEqual(state["pos"], [0.5, 68.38, 0.5])

    def test_state_out_of_world(self) -> None:
        client = FakeClient()
        client.state_reply = {"in_world": False}
        state = make_agent(client).get_state()
        self.assertFalse(state["ok"])

    def test_tp_uses_server_transport(self) -> None:
        client = FakeClient()
        result = make_agent(client).tp(1.0, 2.0, 3.0, "server")
        self.assertTrue(result["ok"])
        self.assertEqual(client.calls[0][0], "teleport")
        self.assertEqual(client.calls[0][1], (1.0, 2.0, 3.0))

    def test_set_input_holds_until_stopped(self) -> None:
        client = FakeClient()
        agent = make_agent(client)
        agent.set_input({"forward": 1.0, "strafe": 0.0, "sprint": True})
        name, args, kwargs = client.calls[-1]
        self.assertEqual(name, "set_input")
        self.assertEqual(args[:2], (1.0, 0.0))
        self.assertTrue(kwargs["sprint"])
        self.assertEqual(kwargs["ticks"], -1)

    def test_sneak_pre_scale_is_undone(self) -> None:
        client = FakeClient()
        agent = make_agent(client)
        agent.move_flags("sneak")
        # The DSL runner already multiplies the world vector by 0.3.
        agent.move_world(0.0, 0.3)
        name, args, kwargs = client.calls[-1]
        self.assertEqual(name, "set_input")
        self.assertAlmostEqual(args[0], 1.0, places=6)  # forward
        self.assertAlmostEqual(args[1], 0.0, places=6)  # strafe
        self.assertTrue(kwargs["sneak"])

    def test_move_world_rotates_with_yaw(self) -> None:
        client = FakeClient()
        agent = make_agent(client)
        client.state_reply["yaw"] = 90.0
        agent.move_world(0.0, 1.0)  # +Z world, facing -X
        _name, args, _kwargs = client.calls[-1]
        self.assertAlmostEqual(args[0], 0.0, places=6)   # forward
        self.assertAlmostEqual(args[1], -1.0, places=6)  # strafe

    def test_block_at_distinguishes_air(self) -> None:
        client = FakeClient()
        agent = make_agent(client)
        stone = agent.block_at(0, 0, 0)
        self.assertTrue(stone["ok"])
        self.assertFalse(stone["air"])
        client.block_reply = {"blocks": [{"name": "minecraft:air"}]}
        air = agent.block_at(0, 0, 0)
        self.assertTrue(air["ok"])
        self.assertTrue(air["air"])

    def test_command_submission_is_accepted_even_when_deferred(self) -> None:
        client = FakeClient()
        agent = make_agent(client)
        ok = agent.command("fill 0 0 0 1 0 1 stone")
        self.assertTrue(ok["ok"])
        self.assertTrue(ok["reported_success"])
        # Deferred block commands reply success=false before running; the
        # submission still counts as accepted and verify/block reads decide.
        client.command_reply = {"success": False, "code": 0}
        deferred = agent.command("fill nope")
        self.assertTrue(deferred["ok"])
        self.assertFalse(deferred["reported_success"])

    def test_probe_scenario_is_valid(self) -> None:
        scenario = _probe_scenario(20)
        self.assertEqual(scenario.name, "probe-forward")
        self.assertEqual(scenario.steps[-1].type, "stop")


if __name__ == "__main__":
    unittest.main()