from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.postmsg import (  # noqa: E402
    VK_BACK,
    VK_OEM_2,
    VK_RETURN,
    VK_T,
    WM_CHAR,
    WM_KEYDOWN,
    WM_KEYUP,
    PostMessageChannel,
)


class Recorder:
    """Collects posted messages instead of touching a real window."""

    def __init__(self) -> None:
        self.messages: list[tuple[int, int, int]] = []

    def __call__(self, message: int, wparam: int, lparam: int):
        self.messages.append((message, wparam, lparam))
        return True

    # -- helpers ---------------------------------------------------------
    def chars(self) -> str:
        return "".join(chr(wparam) for message, wparam, _ in self.messages if message == WM_CHAR)

    def keys(self) -> list[tuple[int, int]]:
        return [
            (message, wparam)
            for message, wparam, _ in self.messages
            if message in (WM_KEYDOWN, WM_KEYUP)
        ]

    def index_of_char(self, char: str) -> int:
        target = ord(char)
        for index, (message, wparam, _) in enumerate(self.messages):
            if message == WM_CHAR and wparam == target:
                return index
        return -1


def channel(**kwargs) -> tuple[PostMessageChannel, Recorder]:
    recorder = Recorder()
    defaults = {"settle": 0.0, "char_delay": 0.0, "poster": recorder}
    defaults.update(kwargs)
    return PostMessageChannel(**defaults), recorder


class SequenceTest(unittest.TestCase):
    def test_backspaces_clear_before_opening(self) -> None:
        chan, rec = channel()
        chan.send_command("setblock 1 2 3 stone")
        backspaces = [i for i, m in enumerate(rec.messages) if m[1] == VK_BACK]
        first_char = rec.index_of_char("s")
        self.assertGreater(len(backspaces), 0)
        self.assertLess(max(backspaces), first_char)

    def test_slash_key_opens_and_prefix_is_not_retyped(self) -> None:
        chan, rec = channel(open_with="slash_key")
        chan.send_command("/setblock 1 2 3 stone")
        self.assertIn((WM_KEYDOWN, VK_OEM_2), rec.keys())
        self.assertIn((WM_KEYUP, VK_OEM_2), rec.keys())
        # '/' opened the chat, so the box already holds it: it must not be typed twice.
        self.assertEqual(rec.chars().count("/"), 0)
        self.assertIn("setblock 1 2 3 stone", rec.chars())

    def test_command_without_slash_still_works(self) -> None:
        chan, rec = channel(open_with="slash_key")
        chan.send_command("setblock 1 2 3 stone")
        self.assertIn("setblock 1 2 3 stone", rec.chars())

    def test_t_char_mode_types_full_command(self) -> None:
        chan, rec = channel(open_with="t_char")
        chan.send_command("/setblock 1 2 3 stone")
        text = rec.chars().lstrip("\b")
        self.assertTrue(text.startswith("t"), f"chat opener missing: {text[:8]!r}")
        self.assertIn("/setblock 1 2 3 stone", text)

    def test_t_key_mode_inserts_leading_slash(self) -> None:
        chan, rec = channel(open_with="t_key")
        chan.send_command("setblock 1 2 3 stone")
        self.assertIn((WM_KEYDOWN, VK_T), rec.keys())
        self.assertIn("/setblock 1 2 3 stone", rec.chars())

    def test_enter_comes_last(self) -> None:
        chan, rec = channel()
        chan.send_command("time set day")
        self.assertEqual(rec.messages[-1][0], WM_KEYUP)
        self.assertEqual(rec.messages[-1][1], VK_RETURN)
        self.assertIn((WM_KEYDOWN, VK_RETURN), rec.keys())

    def test_empty_command_rejected(self) -> None:
        chan, _ = channel()
        with self.assertRaises(RuntimeError):
            chan.send_command("   ")

    def test_result_reports_typed_characters(self) -> None:
        chan, _ = channel(open_with="slash_key")
        result = chan.send_command("/time set day")
        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "postmessage")
        self.assertEqual(result["chars"], len("time set day"))
        self.assertEqual(result["prefix"], "/")


class LParamTest(unittest.TestCase):
    def test_key_messages_carry_a_scan_code(self) -> None:
        chan, rec = channel()
        chan.send_command("time set day")
        key_messages = [
            (message, lparam)
            for message, _, lparam in rec.messages
            if message in (WM_KEYDOWN, WM_KEYUP)
        ]
        self.assertGreater(len(key_messages), 0)
        for message, lparam in key_messages:
            scan = (lparam >> 16) & 0xFF
            self.assertNotEqual(scan, 0, f"missing scan code in lparam {lparam:#x}")
            self.assertTrue(lparam & 1, "repeat count bit must be set")
        keyup = [lparam for message, lparam in key_messages if message == WM_KEYUP]
        for lparam in keyup:
            self.assertTrue(lparam & (1 << 31), "key-up flag must be set")


class AvailabilityTest(unittest.TestCase):
    def test_available_with_injected_poster(self) -> None:
        chan, _ = channel()
        usable, why = chan.available()
        self.assertTrue(usable, why)


if __name__ == "__main__":
    unittest.main()
