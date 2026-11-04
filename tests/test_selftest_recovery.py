"""World recovery restores pose without clearing effects or replacing first errors."""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from driver.selftest_recovery import check_chat_lifecycle, check_entry_lifecycle
from driver.selftest_support import Checks
from driver.transport_mod import ModTimeout


class EntryCleanupTests(unittest.TestCase):
    def test_chat_recovery_preserves_effects_and_first_error(self) -> None:
        client = Mock()
        client.state.return_value = {
            "in_world": True,
            "feet_x": 0.5,
            "feet_y": -49,
            "feet_z": 4,
            "yaw": 10,
            "pitch": 0,
            "effects": [{"id": 16, "duration": -1}],
        }
        client.request.return_value = {"event": "chat_history", "reason": "chat_history_timeout"}
        client.exchange.return_value = contextlib.nullcontext()
        first = ModTimeout("leave failed")
        client.leave_world.side_effect = first
        with (
            contextlib.redirect_stdout(io.StringIO()),
            patch("driver.selftest_recovery.wait_ticks"),
            self.assertRaises(ModTimeout) as raised,
        ):
            check_chat_lifecycle(client, Checks(), "world")
        self.assertIs(raised.exception, first)
        client.teleport.assert_called_once_with(0.5, -49.0, 4.0, yaw=10.0, pitch=0.0)
        client.reset.assert_not_called()

    def test_chat_cleanup_failure_preserves_original_error(self) -> None:
        client = Mock()
        client.state.return_value = {"in_world": True}
        client.request.return_value = {"event": "chat_history", "reason": "chat_history_timeout"}
        client.exchange.return_value = contextlib.nullcontext()
        first = ModTimeout("leave failed")
        client.leave_world.side_effect = first
        client.enter_world_ready.side_effect = ModTimeout("join failed")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ModTimeout) as raised:
            check_chat_lifecycle(client, Checks(), "world")
        self.assertIs(raised.exception, first)
        self.assertIn("join failed", first.__notes__[0])

    def test_restore_preserves_effects_and_first_error(self) -> None:
        client = Mock()
        client.state.return_value = {
            "in_world": True,
            "feet_x": 0.5,
            "feet_y": -49,
            "feet_z": 4,
            "yaw": 10,
            "pitch": 0,
            "effects": [{"id": 16, "duration": -1}],
        }
        first = ModTimeout("leave failed")
        client.leave_world.side_effect = first
        checks = Checks()
        with (
            contextlib.redirect_stdout(io.StringIO()),
            patch("driver.selftest_recovery.wait_ticks"),
            self.assertRaises(ModTimeout) as raised,
        ):
            check_entry_lifecycle(client, checks, "world")
        self.assertIs(raised.exception, first)
        client.teleport.assert_called_once_with(0.5, -49.0, 4.0, yaw=10.0, pitch=0.0)
        client.reset.assert_not_called()

    def test_cleanup_failure_keeps_original_exception(self) -> None:
        client = Mock()
        client.state.return_value = {
            "in_world": True,
            "feet_x": 0.5,
            "feet_y": -49,
            "feet_z": 4,
            "yaw": 10,
            "pitch": 0,
        }
        first = ModTimeout("leave failed")
        client.leave_world.side_effect = first
        client.teleport.side_effect = ModTimeout("teleport failed")
        with (
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(ModTimeout) as raised,
        ):
            check_entry_lifecycle(client, Checks(), "world")
        self.assertIs(raised.exception, first)
        self.assertIn("teleport failed", first.__notes__[0])
