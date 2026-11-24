"""Liquid level reads and query/world command payload unit tests."""

import unittest
from typing import NotRequired, get_origin, get_type_hints

from driver.harness import cli
from driver.harness.cli_parser import build_parser
from driver.harness.errors import ModProtocolError, ModTimeout
from driver.harness.facade import Harness
from driver.harness.protocol import StateEvent
from tests.cli_support import invoke_cli
from tests.test_transport import FakeClient


class LiquidDepthTests(unittest.TestCase):
    def test_water_returns_low_byte_of_state_word(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put(
            {"event": "blocks", "blocks": [{"addr": 1000, "name": "minecraft:water"}]}
        )
        client._session.stream.lines.put(
            {"event": "probe", "ok": True, "vals": {"u8": [7, 0, 1, 0, 0, 0, 0, 0]}}
        )
        self.assertEqual(client.liquid_depth((1, 2, 3)), 7)
        self.assertEqual(client.sent[0]["cmd"], "blocks")
        self.assertEqual(client.sent[1]["cmd"], "probe")
        self.assertEqual(client.sent[1]["addr"], 1000 + 288)
        self.assertEqual(client.sent[1]["len"], 2)

    def test_non_liquid_does_not_probe(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put(
            {"event": "blocks", "blocks": [{"addr": 1, "name": "minecraft:air"}]}
        )
        self.assertIsNone(client.liquid_depth((0, 0, 0)))
        self.assertEqual([m["cmd"] for m in client.sent], ["blocks"])

    def test_probe_failure_is_an_error(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put(
            {"event": "blocks", "blocks": [{"addr": 5, "name": "minecraft:lava"}]}
        )
        client._session.stream.lines.put({"event": "probe", "ok": False, "reason": "bad_addr"})
        with self.assertRaisesRegex(Exception, "bad_addr"):
            client.block_state_word((0, 0, 0))


class ResetWaitTeleportTests(unittest.TestCase):
    def test_reset_waits_for_matching_teleported_by_default(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "reset_done", "teleport_id": 7, "ok": True})
        client._session.stream.lines.put({"event": "teleported", "teleport_id": 6, "ok": True})
        client._session.stream.lines.put(
            {"event": "teleported", "teleport_id": 7, "ok": True, "effects_cleared": True}
        )

        reply = client.reset(1.0, 64.0, -2.0, yaw=90.0)
        self.assertEqual(client.sent[0]["cmd"], "reset")
        self.assertEqual(reply["event"], "reset_done")
        self.assertEqual(reply["teleported"]["teleport_id"], 7)
        self.assertTrue(reply["teleported"]["effects_cleared"])
        # Stale unrelated teleport completion remains buffered.
        self.assertEqual(client.readline()["teleport_id"], 6)

    def test_reset_wait_teleport_false_skips_wait(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "reset_done", "teleport_id": 7, "ok": True})

        reply = client.reset(1.0, 64.0, -2.0, wait_teleport=False)
        self.assertEqual(reply, {"event": "reset_done", "teleport_id": 7, "ok": True})
        self.assertNotIn("teleported", reply)

    def test_reset_without_teleport_id_returns_immediately(self) -> None:
        for extra in ({}, {"teleport_id": 0}):
            with self.subTest(extra=extra):
                client = FakeClient()
                expected = {"event": "reset_done", "ok": True, **extra}
                client._session.stream.lines.put(dict(expected))
                reply = client.reset(0.0, 64.0, 0.0)
                self.assertEqual(reply, expected)
                self.assertNotIn("teleported", reply)

    def test_reset_missing_teleported_times_out(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "reset_done", "teleport_id": 9, "ok": True})
        with self.assertRaises(ModTimeout):
            client.reset(0.0, 64.0, 0.0, timeout=0.02)

    def test_reset_teleport_error_raises_protocol_error(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "reset_done", "teleport_id": 10, "ok": True})
        client._session.stream.lines.put(
            {"event": "error", "phase": "teleport", "teleport_id": 10, "reason": "teleport_failed"}
        )
        with self.assertRaisesRegex(ModProtocolError, "teleport_failed"):
            client.reset(0.0, 64.0, 0.0)


class InventoryAndEcsPayloadTests(unittest.TestCase):
    def test_set_item_optional_damage_and_flight(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "inventory_set", "ok": True})
        client.set_item(0, "minecraft:stone", count=3, aux=1, container="inventory")
        self.assertEqual(
            client.sent[0],
            {
                "cmd": "set_item",
                "slot": 0,
                "item": "minecraft:stone",
                "count": 3,
                "aux": 1,
                "container": "inventory",
            },
        )

        client._session.stream.lines.put({"event": "inventory_set", "ok": True})
        client.set_item(2, "minecraft:elytra", damage=100, flight=2)
        self.assertEqual(
            client.sent[1],
            {
                "cmd": "set_item",
                "slot": 2,
                "item": "minecraft:elytra",
                "count": 1,
                "aux": 0,
                "container": "inventory",
                "damage": 100,
                "flight": 2,
            },
        )

    def test_give_optional_damage_and_flight(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "inventory_given", "ok": True})
        client.give("minecraft:stone")
        self.assertEqual(
            client.sent[0],
            {"cmd": "give", "item": "minecraft:stone", "count": 1, "aux": 0},
        )

        client._session.stream.lines.put({"event": "inventory_given", "ok": True})
        client.give("minecraft:firework_rocket", count=8, damage=0, flight=3)
        self.assertEqual(
            client.sent[1],
            {
                "cmd": "give",
                "item": "minecraft:firework_rocket",
                "count": 8,
                "aux": 0,
                "damage": 0,
                "flight": 3,
            },
        )

    def test_ecs_registries_optional_scope(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "ecs_registries", "registries": []})
        client.ecs_registries()
        self.assertEqual(client.sent[0], {"cmd": "ecs_registries"})

        client._session.stream.lines.put({"event": "ecs_registries", "registries": []})
        client.ecs_registries(scope="server")
        self.assertEqual(client.sent[1], {"cmd": "ecs_registries", "scope": "server"})

    def test_harness_forwards_extended_options(self) -> None:
        client = FakeClient()
        harness = Harness.__new__(Harness)
        harness.client = client

        client._session.stream.lines.put({"event": "inventory_given", "ok": True})
        harness.give("minecraft:firework_rocket", count=4, flight=2)
        self.assertEqual(
            client.sent[0],
            {"cmd": "give", "item": "minecraft:firework_rocket", "count": 4, "aux": 0, "flight": 2},
        )

        client._session.stream.lines.put({"event": "inventory_set", "ok": True})
        harness.set_item(1, "minecraft:elytra", damage=50)
        self.assertEqual(
            client.sent[1],
            {
                "cmd": "set_item",
                "slot": 1,
                "item": "minecraft:elytra",
                "count": 1,
                "aux": 0,
                "container": "inventory",
                "damage": 50,
            },
        )

        client._session.stream.lines.put(
            {"event": "ecs_registries", "registries": [{"scope": "server"}]}
        )
        self.assertEqual(harness.ecs_registries(scope="server"), [{"scope": "server"}])
        self.assertEqual(client.sent[2], {"cmd": "ecs_registries", "scope": "server"})

        client._session.stream.lines.put({"event": "reset_done", "teleport_id": 12, "ok": True})
        reply = harness.reset(0.0, 64.0, 0.0, wait_teleport=False)
        self.assertEqual(reply["teleport_id"], 12)

    def test_state_event_exposes_prev_rotation_and_pos_delta_keys(self) -> None:
        hints = get_type_hints(StateEvent, include_extras=True)
        for key in ("prev_yaw", "prev_pitch", "pos_delta_x", "pos_delta_y", "pos_delta_z"):
            self.assertIn(key, hints)
            self.assertIs(get_origin(hints[key]), NotRequired)


class BlockShapesPayloadTests(unittest.TestCase):
    def test_block_shapes_request_and_cli_parsing(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put(
            {"event": "block_shapes", "ok": True, "entity_mode": "player", "blocks": []}
        )
        reply = client.block_shapes([(1, -50, 2), (3, -50, 4)], entity_mode="player")
        self.assertTrue(reply["ok"])
        self.assertEqual(
            client.sent[0],
            {
                "cmd": "block_shapes",
                "positions": [[1, -50, 2], [3, -50, 4]],
                "entity_mode": "player",
            },
        )

        parser = build_parser()
        parsed = parser.parse_args(
            ["block-shapes", "1", "-50", "2", "--entity-mode", "leather_boots"]
        )
        self.assertEqual(parsed.command, "block-shapes")
        self.assertEqual(parsed.positions, [1.0, -50.0, 2.0])
        self.assertEqual(parsed.entity_mode, "leather_boots")

        res = invoke_cli(
            cli,
            ["dump-block-collisions", "--filename", "out.jsonl", "--pos", "0", "0", "0"],
        )
        self.assertEqual(res["code"], 0)
        self.assertEqual(
            res["calls"][1],
            [
                "dump_block_collisions",
                [],
                {
                    "filename": "out.jsonl",
                    "pos": [0.0, 0.0, 0.0],
                    "include_air": False,
                    "entity_mode": "none",
                },
            ],
        )

    def test_dump_block_collisions_payload(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put(
            {
                "event": "block_collisions_dumped",
                "ok": True,
                "path": "block-collisions.jsonl",
                "permutations": 100,
            }
        )
        reply = client.dump_block_collisions(
            "custom.jsonl", pos=(0, 0, 0), include_air=True, entity_mode="sneak_above"
        )
        self.assertTrue(reply["ok"])
        self.assertEqual(
            client.sent[0],
            {
                "cmd": "dump_block_collisions",
                "filename": "custom.jsonl",
                "include_air": True,
                "entity_mode": "sneak_above",
                "pos": [0.0, 0.0, 0.0],
            },
        )


if __name__ == "__main__":
    unittest.main()
