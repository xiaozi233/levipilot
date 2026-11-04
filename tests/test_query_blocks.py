"""Liquid level reads use the block state word, not /testforblock."""

import unittest

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


if __name__ == "__main__":
    unittest.main()
