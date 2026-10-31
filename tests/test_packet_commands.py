"""Numbered packet sends preserve unrelated completions and legacy receipts."""

import unittest

from driver.harness import ModProtocolError
from tests.test_transport import FakeClient


class PacketCommandsTests(unittest.TestCase):
    def test_wait_matches_id_without_consuming_old_failure(self) -> None:
        client = FakeClient()
        old = {"event": "chat_sent", "packet_id": 3, "ok": False, "reason": "world_left"}
        done = {"event": "chat_sent", "packet_id": 4, "ok": True}
        for row in ({"event": "chat_send_queued", "packet_id": 4}, old, done):
            client._session.lines.put(row)
        self.assertEqual(client.chat_send("/say test", wait=True), done)
        self.assertEqual(client.readline(), old)

    def test_wait_propagates_failure(self) -> None:
        client = FakeClient()
        client._session.lines.put({"event": "chat_send_queued", "packet_id": 4})
        client._session.lines.put(
            {"event": "chat_sent", "packet_id": 4, "ok": False, "reason": "packet_inject_fault"}
        )
        with self.assertRaisesRegex(ModProtocolError, "packet_inject_fault"):
            client.chat_send("/say test", wait=True)

    def test_queued_default_and_legacy_compatibility(self) -> None:
        for reply, wait in (
            ({"event": "chat_send_queued", "packet_id": 1}, False),
            ({"event": "chat_send_queued"}, True),
        ):
            with self.subTest(reply=reply):
                client = FakeClient()
                client._session.lines.put(reply)
                self.assertEqual(client.chat_send("/say test", wait=wait), reply)
