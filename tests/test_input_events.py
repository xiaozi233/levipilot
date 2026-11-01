"""Text completion must retain compatibility and match the accepted request."""

import unittest

from driver.harness.errors import ModProtocolError
from tests.test_transport import FakeClient


class TextInputTests(unittest.TestCase):
    def test_default_preserves_acceptance_response(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "text_input_queued", "input_id": 4})
        self.assertEqual(client.text_input("abc")["event"], "text_input_queued")

    def test_wait_matches_id_and_preserves_other_completions(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "text_input_queued", "input_id": 4})
        client._session.stream.lines.put({"event": "text_input_done", "input_id": 3, "ok": False})
        client._session.stream.lines.put({"event": "text_input_done", "input_id": 4, "ok": True})
        self.assertTrue(client.text_input("abc", wait=True)["ok"])
        self.assertEqual(client.wait_event("text_input_done")["input_id"], 3)

    def test_wait_propagates_failure(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "text_input_queued", "input_id": 4})
        client._session.stream.lines.put(
            {"event": "text_input_done", "input_id": 4, "ok": False, "reason": "capture_timeout"}
        )
        with self.assertRaisesRegex(ModProtocolError, "capture_timeout"):
            client.text_input("abc", wait=True)

    def test_old_mod_acceptance_has_no_completion(self) -> None:
        client = FakeClient()
        client._session.stream.lines.put({"event": "text_input_queued"})
        self.assertEqual(client.text_input("abc", wait=True), {"event": "text_input_queued"})
