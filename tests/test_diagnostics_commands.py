"""Panic acknowledgements and cancelled workflows can arrive in either order."""

import unittest
from pathlib import Path

from driver.harness import ModProtocolError, ModTimeout
from tests.test_transport import FakeClient

ROOT = Path(__file__).resolve().parents[1]


class PanicCommandsTests(unittest.TestCase):
    def test_cancelled_workflow_is_preserved_in_either_order(self):
        cancelled = {"event": "error", "phase": "teleport", "teleport_id": 4, "reason": "panic"}
        done = {"event": "panic_done", "level": "tasks"}
        for rows in ((cancelled, done), (done, cancelled)):
            with self.subTest(rows=rows):
                client = FakeClient()
                for row in rows:
                    client._session.stream.lines.put(row)
                self.assertEqual(client.panic("tasks"), done)
                self.assertEqual(client.readline(), cancelled)
                self.assertEqual(client.sent, [{"cmd": "panic", "level": "tasks"}])

    def test_command_rejection_is_still_an_error(self):
        client = FakeClient()
        client._session.stream.lines.put({"event": "error", "reason": "bad_panic_level"})
        with self.assertRaisesRegex(ModProtocolError, "bad_panic_level"):
            client.panic()

    def test_unrelated_fault_is_not_suppressed(self):
        client = FakeClient()
        client._session.stream.lines.put(
            {"event": "error", "phase": "tick", "reason": "internal_error"}
        )
        with self.assertRaisesRegex(ModProtocolError, "internal_error"):
            client.panic()

    def test_timeout_closes_the_session_to_exclude_late_receipts(self):
        client = FakeClient()
        with self.assertRaises(ModTimeout):
            client.panic(timeout=0)
        self.assertTrue(client._session.stream.stopped.is_set())


class CapabilityAndDocParityTests(unittest.TestCase):
    def test_hello_and_pong_share_protocol_capabilities_source(self) -> None:
        transport = (ROOT / "mod/probe/src/mod/Transport.cpp").read_text(encoding="utf-8")
        diag = (ROOT / "mod/probe/src/mod/CommandDiagnostics.cpp").read_text(encoding="utf-8")
        self.assertIn("protocolCapabilitiesJson()", transport)
        self.assertIn("protocolCapabilitiesJson()", diag)

    def test_readme_documents_stopmove_and_mouse_clear(self) -> None:
        readme = (ROOT / "mod/README.md").read_text(encoding="utf-8")
        self.assertIn("`stopmove`", readme)
        self.assertIn("`mouse_clear`", readme)
